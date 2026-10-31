"""Codex JSONL adapter; tool counts are observable items, not queries/page opens.

Extra GPT result fields are serialized by dataclasses.asdict. Consumers must audit
these before using an answer. No local flag can block server-side Metaculus access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from flab import runner
from .base import AdapterRequest, AdapterResult, USAGE_KEYS, UrlEvent, is_metaculus_host, url_host

DISABLED_FEATURES = (
    'shell_tool', 'unified_exec', 'computer_use', 'browser_use',
    'browser_use_external', 'in_app_browser', 'apps', 'plugins',
    'remote_plugin', 'multi_agent', 'hooks', 'image_generation', 'view_image',
    # Found in M1 acceptance (codex-cli 0.166.0): with only the items above, the model still
    # had functions.exec (code mode) and ran a background script. Disabling these makes the
    # router refuse with "code-mode host is disabled" (PLAN §7.3, D59).
    'code_mode_host', 'in_app_local_automation', 'code_mode_interrupt',
)
# multi_agent=false still declares the collaboration.* tools (spawn_agent, wait_agent, ...);
# agents.enabled=false removes them. Re-enabled only for an accepted tier2 run.
AGENTS_DISABLED = 'agents.enabled=false'
# Subscription login is handled by the official CLI. No secret-variable exception
# is needed: runner retains HOME/CODEX_HOME and scrubs API keys and tokens.
KEEP_ENV: tuple[str, ...] = ()


@dataclass
class GptResult(AdapterResult):
    violations: list[dict[str, Any]] = field(default_factory=list)
    parse_errors: list[dict[str, Any]] = field(default_factory=list)
    audit_required: bool = False
    audit_flags: list[str] = field(default_factory=list)


def build_argv(req: AdapterRequest, *, enable_subagents: bool = False) -> list[str]:
    """Enable tier2 delegation only after the caller's isolation acceptance gate."""
    if req.player != 'gpt':
        raise ValueError('gpt adapter requires player=gpt')
    if enable_subagents and req.profile != 'tier2':
        raise ValueError('subagents are allowed only for tier2')
    raw = req.raw_dir.resolve()
    argv = ['codex', 'exec', '--ignore-user-config', '--strict-config', '-s', 'read-only',
            '-c', 'approval_policy="never"',
            '-c', 'web_search="disabled"' if req.profile == 'format_repair' else 'web_search="live"',
            '-c', 'model_reasoning_effort=' + json.dumps(req.effort)]
    for feature in DISABLED_FEATURES:
        if feature != 'multi_agent' or not enable_subagents:
            argv.extend(['--disable', feature])
    if enable_subagents:
        argv.extend(['--enable', 'multi_agent'])
    else:
        argv.extend(['-c', AGENTS_DISABLED])
    return argv + ['-m', req.model, '--skip-git-repo-check', '-C', str(req.workdir.resolve()),
                   '--ephemeral', '--json', '--output-schema', str(raw / 'schema.json'),
                   '--output-last-message', str(raw / 'last.json'), '-']


def cli_version() -> str | None:
    try:
        result = subprocess.run(['codex', '--version'], capture_output=True, text=True,
                                timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.fullmatch(r'codex-cli\s+(\S+)\s*', result.stdout)
    return match[1] if result.returncode == 0 and match else None


def _urls(value: Any):
    """Only explicit URL fields/strings; never turn prose claims into opened events."""
    if isinstance(value, str) and value.strip().lower().startswith(('https://', 'http://')):
        yield value.strip()
    elif isinstance(value, list):
        for child in value:
            yield from _urls(child)
    elif isinstance(value, dict):
        for key, child in value.items():
            if key in ('url', 'urls'):
                yield from _urls(child)


def parse(req: AdapterRequest, run_result: runner.RunResult | None = None,
          *, enable_subagents: bool = False) -> GptResult:
    result = GptResult(argv=build_argv(req, enable_subagents=enable_subagents), run=run_result,
                       cli_version=None, completed=False, final_text=None, structured=None,
                       reported_model_id=None, model_check='unverified', usage_raw=None)
    items: dict[str, dict] = {}
    finished_items: set[str] = set()
    turns = []
    failures = []
    denied = []

    def read(name: str) -> str | None:
        try:
            return (req.raw_dir / name).read_text(encoding='utf-8')
        except (OSError, UnicodeError) as exc:
            result.parse_errors.append({'file': name, 'error': str(exc)})
            return None

    for number, line in enumerate((read('events.jsonl') or '').splitlines(), 1):
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                raise ValueError('expected event object with type')
            kind = event['type']
            if kind in ('item.started', 'item.updated', 'item.completed'):
                item = event.get('item')
                if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not isinstance(item.get('type'), str):
                    raise ValueError('invalid item/id/type')
                key = item['id']
                previous = items.get(key, {})
                merged = {**previous, **item}
                if isinstance(previous.get('action'), dict) and isinstance(item.get('action'), dict):
                    merged['action'] = {**previous['action'], **item['action']}
                if isinstance(previous.get('results'), list) and isinstance(item.get('results'), list):
                    merged['results'] = previous['results'] + [r for r in item['results'] if r not in previous['results']]
                items[key] = merged
                if kind == 'item.completed':
                    finished_items.add(key)
            elif kind == 'turn.completed':
                turns.append(event.get('usage'))
            elif kind in ('turn.failed', 'error'):
                failures.append(event)
            elif kind in ('permission_denied', 'tool_denied'):
                denied.append(event)
        except (ValueError, TypeError) as exc:
            result.parse_errors.append({'file': 'events.jsonl', 'line': number, 'error': str(exc)})

    counts: dict[str, int] = {}
    for key, item in items.items():
        kind = item['type']
        if kind in ('agent_message', 'reasoning', 'todo_list'):
            continue
        counts[kind] = counts.get(kind, 0) + 1
        is_denied = item.get('status') in ('denied', 'blocked')
        if is_denied:
            denied.append(item)
        if kind != 'web_search' or req.profile == 'format_repair':
            # Unrecognized/delegation tools remain auditable until their event
            # schemas and inherited permissions have passed acceptance.
            result.violations.append(item)
        if kind == 'web_search':
            action = item.get('action')
            action = action if isinstance(action, dict) else {}
            action_kind = action.get('type')
            for url in _urls(action):
                state = 'unknown'
                if is_denied:
                    state = 'blocked'
                elif action_kind == 'open_page':
                    state = 'opened' if key in finished_items and not item.get('error') and item.get('status') != 'failed' else 'attempted'
                result.url_events.append(UrlEvent(url, url_host(url), state, 'tool_use'))
            for url in _urls(item.get('results', [])):
                result.url_events.append(UrlEvent(url, url_host(url), 'unknown',
                                                 'search_result' if action_kind == 'search' else 'tool_result'))
    result.tool_call_counts = counts
    result.denied_actions = denied or None
    result.usage_raw = ({'turns': turns} if len(turns) > 1 else turns[0]) if turns else None
    if len(turns) == 1 and isinstance(turns[0], dict):
        mapping = dict(zip(USAGE_KEYS, ('input_tokens', 'output_tokens', 'reasoning_output_tokens', 'cached_input_tokens')))
        result.usage = {key: value if type(value := turns[0].get(source)) is int and value >= 0 else None
                        for key, source in mapping.items()}
    elif result.usage_raw is not None and not isinstance(result.usage_raw, dict):
        result.usage_raw = {'turns': turns}

    result.final_text = read('last.json')
    if result.final_text and result.final_text.strip():
        try:
            structured = json.loads(result.final_text)
            result.structured = structured if isinstance(structured, dict) else None
        except ValueError:
            pass
    for url in _urls((result.structured or {}).get('opened_urls', [])):
        result.url_events.append(UrlEvent(url, url_host(url), 'unknown', 'model_report'))
    for url in _urls((result.structured or {}).get('sources', [])):
        result.url_events.append(UrlEvent(url, url_host(url), 'unknown', 'model_sources'))
    if any(is_metaculus_host(e.host) and (e.state == 'opened' or e.source == 'model_sources') for e in result.url_events):
        result.audit_flags.append('leak_confirmed')
    elif any(is_metaculus_host(e.host) and e.state == 'unknown' for e in result.url_events):
        result.audit_flags.append('suspected_leak')
    result.audit_required = bool(result.violations or result.parse_errors or result.audit_flags)
    process_ok = run_result is None or (run_result.exit_code == 0 and not run_result.timed_out and not run_result.killed_signal)
    result.completed = bool(turns and not failures and result.final_text and result.final_text.strip()
                            and process_ok and not result.parse_errors)
    if run_result and run_result.timed_out:
        result.error_type, result.error = 'timeout', 'Codex exceeded timeout'
    elif failures or not process_ok:
        result.error_type, result.error = 'cli_error', json.dumps(failures) if failures else 'Codex exited unsuccessfully'
        # Only explicit CLI error evidence, never model prose, determines quota.
        if re.search(r'quota_exhausted|usage_limit_reached|insufficient_quota', result.error):
            result.error_type = 'quota_exhausted'
    elif denied and (not result.completed or result.structured is None):
        result.error_type, result.error = 'tool_denied', 'CLI reported denied tool actions'
    elif result.parse_errors:
        result.error_type, result.error = 'cli_error', json.dumps(result.parse_errors)
    elif not result.completed or result.structured is None:
        result.error_type, result.error = 'invalid_output', 'Missing completion or final JSON object'
    return result


def run(req: AdapterRequest, *, enable_subagents: bool = False) -> GptResult:
    argv = build_argv(req, enable_subagents=enable_subagents)
    req.raw_dir.mkdir(parents=True, exist_ok=True)
    # Attempts need fresh raw paths; never silently reuse an earlier final answer.
    for name in ('events.jsonl', 'stderr.txt', 'last.json', 'schema.json'):
        if (req.raw_dir / name).exists():
            raise FileExistsError(req.raw_dir / name)
    with (req.raw_dir / 'schema.json').open('x', encoding='utf-8') as stream:
        json.dump(req.schema, stream)
    version = cli_version()
    try:
        execution = runner.run(argv, cwd=req.workdir.resolve(), stdin_text=req.prompt,
                               timeout_s=req.timeout_s, stdout_path=req.raw_dir / 'events.jsonl',
                               stderr_path=req.raw_dir / 'stderr.txt', keep_env=KEEP_ENV)
    except OSError as exc:
        return GptResult(argv=argv, run=None, cli_version=version, completed=False,
                         final_text=None, structured=None, reported_model_id=None,
                         model_check='unverified', usage_raw=None, error_type='cli_error',
                         error=str(exc))
    result = parse(req, execution, enable_subagents=enable_subagents)
    result.cli_version = version
    return result
