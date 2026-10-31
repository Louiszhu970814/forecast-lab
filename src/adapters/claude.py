"""Claude stream-json adapter. Parsing never executes the CLI.

Raw files: stdout.jsonl, stderr.txt, cli_version.txt. usage_raw retains the
result's usage, modelUsage and subagent_stats without summing token fields.
"""
from __future__ import annotations

import json
import re
import subprocess
from collections import Counter

from flab import runner
from adapters.base import AdapterRequest, AdapterResult, UrlEvent, model_check_from_ids, url_host

# Reserved for W3: tool name, inherited restrictions and recursion must all be
# verified before populating this tuple. No environment/config opt-in in M1.
TIER2_SUBAGENT_TOOLS: tuple[str, ...] = ()
# Subscription login is handled by the official CLI. No secret environment
# variables need exemptions from runner's scrubber; never read credentials.
KEEP_ENV: tuple[str, ...] = ()
# Fields of the stream-json init event that show what the session actually loaded (PLAN §7.6).
DECLARED_INIT_KEYS: tuple[str, ...] = (
    'tools', 'mcp_servers', 'skills', 'slash_commands', 'plugins', 'agents',
    'model', 'permissionMode', 'apiKeySource', 'cwd',
)


def build_argv(req: AdapterRequest) -> list[str]:
    if req.player != 'claude':
        raise ValueError('claude adapter requires player=claude')
    tools = [] if req.profile == 'format_repair' else ['WebSearch', 'WebFetch']
    if req.profile == 'tier2':
        tools.extend(TIER2_SUBAGENT_TOOLS)
    argv = ['claude', '-p', '--model', req.model, '--effort', req.effort,
            '--tools', ','.join(tools)]
    if tools:
        argv += ['--allowedTools', ','.join(tools)]
    argv += ['--disallowedTools', 'WebFetch(domain:metaculus.com)',
             'WebFetch(domain:*.metaculus.com)', '--restricted',
             '--strict-mcp-config', '--disable-slash-commands',
             '--no-session-persistence', '--output-format', 'stream-json',
             '--verbose', '--json-schema', json.dumps(req.schema, ensure_ascii=False)]
    return argv


def cli_version() -> str | None:
    try:
        result = subprocess.run(['claude', '--version'], capture_output=True,
                                text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.match(r'\s*(\d+\.\d+\.\d+(?:[-+][\w.-]+)?)\b', result.stdout)
    return match.group(1) if result.returncode == 0 and match else None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _text(value):
    return value if isinstance(value, str) else None


def _int(value):
    return value if type(value) is int and value >= 0 else None


def _read(path):
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
        return ''


def _urls(value):
    """URLs in tool data only, never model prose; search hits aren't opened."""
    if isinstance(value, str):
        return re.findall(r'https?://[^\s<>"\\]+', value)
    if isinstance(value, dict):
        return [u for v in value.values() for u in _urls(v)]
    if isinstance(value, list):
        return [u for v in value for u in _urls(v)]
    return []


def parse(req: AdapterRequest, run_result: runner.RunResult | None = None) -> AdapterResult:
    events, malformed = [], False
    for line in _read(req.raw_dir / 'stdout.jsonl').splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            malformed = True
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            malformed = True
    results = [e for e in events if e.get('type') == 'result']
    result = results[-1] if results else {}
    calls, responses, denied_ids = {}, {}, set()
    primary_models = []
    version = _read(req.raw_dir / 'cli_version.txt').strip() or None
    quota = False
    declared = None
    for e in events:
        if e.get('type') == 'system' and e.get('subtype') == 'init':
            version = version or _text(e.get('claude_code_version'))
            declared = {k: e[k] for k in DECLARED_INIT_KEYS if k in e}
        if e.get('type') == 'rate_limit_event':
            # Only top-level rejection, not overageStatus or nested windows.
            quota |= _dict(e.get('rate_limit_info')).get('status') == 'rejected'
        if e.get('type') == 'system' and e.get('subtype') == 'permission_denied':
            if isinstance(e.get('tool_use_id'), str):
                denied_ids.add(e['tool_use_id'])
        message = _dict(e.get('message'))
        if e.get('type') == 'assistant' and not e.get('parent_tool_use_id'):
            model = _text(message.get('model'))
            if model and model not in primary_models:
                primary_models.append(model)
        for c in _list(message.get('content')):
            c = _dict(c)
            if c.get('type') == 'tool_use' and isinstance(c.get('id'), str):
                calls.setdefault(c['id'], c)
            if c.get('type') == 'tool_result' and isinstance(c.get('tool_use_id'), str):
                responses[c['tool_use_id']] = c
    denials = result.get('permission_denials')
    denials = [d for d in denials if isinstance(d, dict)] if isinstance(denials, list) else None
    for d in denials or []:
        if isinstance(d.get('tool_use_id'), str):
            denied_ids.add(d['tool_use_id'])
    urls = []

    def add(url, state, source):
        urls.append(UrlEvent(url, url_host(url), state, source))

    for ident, call in calls.items():
        name = call.get('name')
        if name not in ('WebFetch', 'WebSearch'):
            continue
        data = _dict(call.get('input'))
        targets = [_text(data.get('url'))] if name == 'WebFetch' else (_urls(data) or [None])
        response = responses.get(ident)
        for url in targets:
            add(url, 'attempted', 'tool_use')
            if ident in denied_ids:
                add(url, 'blocked', 'permission_denied')
            elif response is not None:
                state = 'opened' if name == 'WebFetch' and response.get('is_error', False) is False else 'unknown'
                add(url, state, 'tool_result')
            else:
                add(url, 'unknown', 'tool_use')
        if response and name == 'WebSearch':
            for url in dict.fromkeys(_urls(response.get('content'))):
                add(url, 'unknown', 'search_result')
    # Final-only or truncated streams can still contain authoritative denials.
    final_denied_ids = {d.get('tool_use_id') for d in denials or []
                        if isinstance(d.get('tool_use_id'), str)}
    for ident in denied_ids - calls.keys() - final_denied_ids:
        add(None, 'blocked', 'permission_denied')
    for d in denials or []:
        if d.get('tool_use_id') not in calls:
            add(_text(_dict(d.get('tool_input')).get('url')), 'blocked', 'permission_denied')

    models = _dict(result.get('modelUsage'))
    reported = next(iter(models)) if len(models) == 1 else None
    if len(models) > 1 and len(primary_models) == 1 and primary_models[0] in models:
        reported = primary_models[0]
    check = model_check_from_ids(req.model, reported)
    usage = _dict(result.get('usage'))
    completed = bool(results) and result.get('subtype') == 'success' and result.get('is_error') is False
    if malformed or (run_result and (run_result.timed_out or run_result.exit_code != 0)):
        completed = False
    final = _text(result.get('result'))
    structured = result.get('structured_output')
    structured = structured if isinstance(structured, dict) else None
    has_answer = structured is not None or bool(final and final.strip())
    error_type = error = None
    if run_result and run_result.timed_out:
        error_type, error = 'timeout', 'CLI exceeded timeout'
    elif quota and not completed:
        error_type, error = 'quota_exhausted', 'CLI reported rate limit rejection'
    elif run_result and run_result.exit_code != 0:
        error_type, error = 'cli_error', f'CLI exit code {run_result.exit_code}'
    elif check == 'mismatch':
        error_type, error = 'model_mismatch', f'Requested {req.model}; reported {reported}'
    elif (denied_ids or denials) and (not completed or not has_answer):
        error_type, error = 'tool_denied', 'Tools denied without a completed answer'
    elif result.get('is_error') is True:
        error_type, error = 'cli_error', final or str(result.get('errors', result.get('subtype')))
    elif not completed or not has_answer:
        error_type, error = 'invalid_output', 'Missing answer, incomplete or malformed event stream'
    return AdapterResult(
        argv=build_argv(req), run=run_result, cli_version=version, completed=completed,
        final_text=final, structured=structured, reported_model_id=reported, model_check=check,
        usage_raw={k: result[k] for k in ('usage', 'modelUsage', 'subagent_stats', 'total_cost_usd', 'num_turns') if k in result} or None,
        usage={'input_tokens': _int(usage.get('input_tokens')),
               'output_tokens': _int(usage.get('output_tokens')),
               'reasoning_tokens': _int(_dict(usage.get('output_tokens_details')).get('thinking_tokens')),
               'cached_tokens': _int(usage.get('cache_read_input_tokens'))},
        tool_call_counts=dict(Counter(c['name'] for c in calls.values() if isinstance(c.get('name'), str))) if calls or any(e.get('type') == 'system' and e.get('subtype') == 'init' for e in events) else None,
        url_events=urls, denied_actions=denials,
        subagent_count=_int(_dict(result.get('subagent_stats')).get('spawned')),
        error_type=error_type, error=error, declared=declared)


def run(req: AdapterRequest) -> AdapterResult:
    argv = build_argv(req)
    req.raw_dir.mkdir(parents=True, exist_ok=True)
    version = cli_version()
    (req.raw_dir / 'cli_version.txt').write_text(version or '', encoding='utf-8')
    try:
        outcome = runner.run(argv, cwd=req.workdir, stdin_text=req.prompt,
                             timeout_s=req.timeout_s, stdout_path=req.raw_dir / 'stdout.jsonl',
                             stderr_path=req.raw_dir / 'stderr.txt', keep_env=KEEP_ENV)
    except OSError as exc:
        result = parse(req)
        result.completed = False
        result.error_type, result.error = 'cli_error', str(exc)
        return result
    return parse(req, outcome)
