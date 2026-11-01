import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from adapters import claude
from adapters.base import AdapterRequest
from flab import netguard
from flab.runner import RunResult

FIXTURES = Path(__file__).parent / 'fixtures' / 'claude'


@pytest.fixture
def req(tmp_path):
    return AdapterRequest('claude', 'tier1', 'private prompt', {'type': 'object'},
                          tmp_path, tmp_path, 'claude-haiku-5-5', 'medium', 60)


def fixture(req, name):
    shutil.copyfile(FIXTURES / f'{name}.jsonl', req.raw_dir / 'stdout.jsonl')
    return claude.parse(req)


def write(req, events):
    (req.raw_dir / 'stdout.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))


def success(**kwargs):
    return dict(type='result', subtype='success', is_error=False, result='answer', **kwargs)


def outcome(req, **kwargs):
    return RunResult(**(dict(exit_code=0, timed_out=False, killed_signal=None, duration_s=1,
        started_at='2026-10-31T00:00:00Z', finished_at='2026-10-31T00:00:01Z',
        stdout_path=req.raw_dir/'stdout.jsonl', stderr_path=req.raw_dir/'stderr.txt',
        scrubbed_env_names=[]) | kwargs))


@pytest.mark.parametrize('profile', ['tier1', 'tier2', 'acceptance', 'format_repair'])
def test_argv(req, profile):
    argv = claude.build_argv(replace(req, profile=profile))
    tools = '' if profile == 'format_repair' else 'WebSearch,WebFetch'
    assert argv[argv.index('--tools')+1] == tools
    if tools:
        assert argv[argv.index('--allowedTools')+1] == tools
    else:
        assert '--allowedTools' not in argv
    # --restricted ignores user/project/local settings and removes unlisted
    # executable tools; subscription login still requires W3 live acceptance.
    for flag in ['--restricted', '--strict-mcp-config', '--disable-slash-commands',
                 '--no-session-persistence', '--verbose']:
        assert flag in argv
    assert argv[argv.index('--disallowedTools')+1:argv.index('--restricted')] == [
        'WebFetch(domain:metaculus.com)', 'WebFetch(domain:*.metaculus.com)']
    assert argv[argv.index('--output-format')+1] == 'stream-json'
    assert json.loads(argv[-1]) == req.schema
    assert req.prompt not in argv
    assert netguard.is_wrapped(argv) and argv[3] == 'claude'
    assert '--bare' not in argv


def test_allowed(req):
    r = fixture(req, 'allowed_stream')
    assert r.completed and r.error_type is None
    assert r.model_check == 'verified'
    assert r.reported_model_id == req.model
    assert r.cli_version == '2.1.316'
    assert r.denied_actions == [] and r.subagent_count == 0
    assert r.tool_call_counts['WebFetch'] == 3
    assert r.tool_call_counts['WebSearch'] == 1
    assert any(e.host == 'www.python.org' and e.state == 'opened' for e in r.url_events)
    assert any(e.host == 'blog.metaculus.com' and e.state == 'unknown' for e in r.url_events)
    assert any(e.source == 'search_result' and e.state == 'unknown' for e in r.url_events)
    # Task mapping is based on is_error; HTTP 403 without it is conservative opened.
    assert any(e.host == 'www.metaculus.com' and e.state == 'opened' for e in r.url_events)
    u = r.usage_raw['usage']
    assert r.usage == dict(input_tokens=u['input_tokens'], output_tokens=u['output_tokens'],
        reasoning_tokens=u['output_tokens_details']['thinking_tokens'], cached_tokens=u['cache_read_input_tokens'])
    assert 'cache_creation_input_tokens' in u
    assert 'cache_creation_input_tokens' not in r.usage


@pytest.mark.parametrize('name', ['search_stream', 'deny_variants_stream'])
def test_denied_with_answer(req, name):
    r = fixture(req, name)
    assert r.completed and r.error_type is None
    assert r.denied_actions
    assert any(e.state == 'blocked' for e in r.url_events)
    assert not any(e.state == 'opened' for e in r.url_events)
    assert r.usage_raw['usage']['server_tool_use']['web_fetch_requests'] == 0
    assert r.tool_call_counts['WebFetch'] > 0


def test_dedup_and_truncation(req):
    fixture(req, 'search_stream')
    path = req.raw_dir/'stdout.jsonl'
    lines = path.read_text().splitlines()
    path.write_text('\n'.join(lines+lines)+'\n')
    assert claude.parse(req).tool_call_counts['WebSearch'] == 1
    path.write_text('\n'.join(lines[:-1])+'\n{"type":')
    r = claude.parse(req)
    assert not r.completed and r.error_type == 'tool_denied'


@pytest.mark.parametrize('changes,error', [({'timed_out': True}, 'timeout'), ({'exit_code': 1}, 'cli_error')])
def test_process_failure_overrides_success(req, changes, error):
    write(req, [success()])
    r = claude.parse(req, outcome(req, **changes))
    assert not r.completed and r.error_type == error


def test_unknown_and_missing(req):
    r = claude.parse(req)
    assert not r.completed and r.error_type == 'invalid_output'
    assert r.model_check == 'unverified' and r.usage_raw is None
    assert all(v is None for v in r.usage.values())
    assert r.denied_actions is None and r.subagent_count is None
    write(req, [success(), {'type':'new_event'}])
    assert claude.parse(req).completed


def test_models(req):
    write(req, [success(modelUsage={'other':{}})])
    assert claude.parse(req).error_type == 'model_mismatch'
    multi = success(modelUsage={'child':{}, req.model:{}})
    write(req, [multi])
    assert claude.parse(req).reported_model_id is None
    write(req, [{'type':'assistant', 'message':{'model':req.model}},
                {'type':'assistant','parent_tool_use_id':'x','message':{'model':'child'}}, multi])
    r = claude.parse(req)
    assert r.model_check == 'verified' and 'child' in r.usage_raw['modelUsage']


@pytest.mark.parametrize('event,error', [
    ({'type':'result','subtype':'error_during_execution','is_error':True}, 'cli_error'),
    ({'type':'rate_limit_event','rate_limit_info':{'status':'rejected'}}, 'quota_exhausted'),
    ({'type':'rate_limit_event','rate_limit_info':{'status':'allowed','overageStatus':'rejected'}}, 'invalid_output'),
])
def test_errors(req, event, error):
    write(req, [event])
    assert claude.parse(req).error_type == error


def test_final_only_denial(req):
    write(req, [dict(type='result', subtype='success', is_error=False, permission_denials=[
        {'tool_use_id':'x','tool_input':{'url':'https://metaculus.com'}}])])
    r = claude.parse(req)
    assert r.error_type == 'tool_denied'
    assert r.url_events[0].state == 'blocked'


def test_malformed_shapes(req):
    write(req, [{'type':'assistant','message':{'content':[None, {'type':'tool_use','id':[]}]}},
                success(usage={'output_tokens_details':None}, modelUsage=[], permission_denials=[None])])
    assert claude.parse(req).completed


def test_run(req, monkeypatch):
    monkeypatch.setattr(claude, 'cli_version', lambda: '2.1.316')
    def fake_run(argv, **kwargs):
        assert argv == claude.build_argv(req)
        assert kwargs['stdin_text'] == req.prompt and kwargs['cwd'] == req.workdir
        assert kwargs['keep_env'] == () and kwargs['timeout_s'] == 60
        write(req, [success()])
        return outcome(req)
    monkeypatch.setattr(claude.runner, 'run', fake_run)
    assert claude.run(req).cli_version == '2.1.316'
    def fail(*args, **kwargs):
        raise FileNotFoundError('claude unavailable')
    monkeypatch.setattr(claude.runner, 'run', fail)
    assert claude.run(req).error_type == 'cli_error'


@pytest.mark.parametrize('stdout,code,expected', [('2.1.316 (Claude Code)\n',0,'2.1.316'), ('bad',0,None), ('2.1.316',1,None)])
def test_version(monkeypatch, stdout, code, expected):
    def fake(argv, **kwargs):
        assert argv == ['claude','--version']
        return SimpleNamespace(stdout=stdout,returncode=code)
    monkeypatch.setattr(claude.subprocess,'run',fake)
    assert claude.cli_version() == expected


def test_parse_is_offline_and_orphan_denial(req, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('parse must never execute subprocesses')
    monkeypatch.setattr(claude.subprocess, 'run', forbidden)
    monkeypatch.setattr(claude.runner, 'run', forbidden)
    write(req, [{'type':'system','subtype':'permission_denied','tool_use_id':'missing'}])
    r = claude.parse(req)
    assert r.url_events[0].state == 'blocked' and r.url_events[0].url is None
    assert r.tool_call_counts is None
    write(req, [success()])
    assert claude.parse(req).tool_call_counts is None


def test_declared_init_fields(req):
    r = fixture(req, 'allowed_stream')
    assert r.declared is not None
    assert r.declared['tools'] == ['StructuredOutput', 'WebFetch', 'WebSearch']
    assert r.declared['mcp_servers'] == [] and r.declared['skills'] == []
    assert 'Agent' not in r.declared['tools'] and 'Task' not in r.declared['tools']
    assert r.declared['apiKeySource'] == 'none'
