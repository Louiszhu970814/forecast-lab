import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from adapters import gpt
from adapters.base import AdapterRequest, USAGE_KEYS
from flab.runner import RunResult

FIXTURES = Path(__file__).parent / 'fixtures/gpt'


@pytest.fixture
def req(tmp_path):
    work = tmp_path / 'work'
    raw = tmp_path / 'raw'
    work.mkdir(); raw.mkdir()
    return AdapterRequest('gpt', 'tier1', 'private prompt', {}, work, raw, 'gpt-6-astra', 'medium', 30)


def sample(req, events='tier1.jsonl', final='sources.json'):
    (req.raw_dir / 'events.jsonl').write_bytes((FIXTURES / events).read_bytes())
    (req.raw_dir / 'last.json').write_bytes((FIXTURES / final).read_bytes())


def write_events(req, events):
    (req.raw_dir / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
    (req.raw_dir / 'last.json').write_text('{}')


def execution(req, **kw):
    return RunResult(**dict(exit_code=kw.get('exit_code', 0), timed_out=kw.get('timed_out', False),
        killed_signal=None, duration_s=1, started_at='2026-10-31T00:00:00Z',
        finished_at='2026-10-31T00:00:01Z', stdout_path=req.raw_dir/'events.jsonl',
        stderr_path=req.raw_dir/'stderr.txt', scrubbed_env_names=[]))


@pytest.mark.parametrize('profile', ['tier1','tier2','acceptance','format_repair'])
def test_argv(req, profile):
    req = replace(req, profile=profile)
    argv = gpt.build_argv(req)
    assert req.prompt not in argv and argv[-1] == '-'
    assert {argv[i+1] for i,x in enumerate(argv) if x=='--disable'} == set(gpt.DISABLED_FEATURES)
    for flag in ('--output-schema', '--output-last-message'):
        assert Path(argv[argv.index(flag)+1]).parent == req.raw_dir.resolve()
    assert argv[argv.index('-C')+1] == str(req.workdir.resolve())
    assert 'approval_policy="never"' in argv
    assert ('web_search="disabled"' in argv) == (profile=='format_repair')
    assert '--enable' not in argv
    assert list(req.raw_dir.iterdir()) == []


def test_tier2_opt_in(req):
    argv = gpt.build_argv(replace(req, profile='tier2'), enable_subagents=True)
    assert argv[argv.index('--enable')+1] == 'multi_agent'
    assert gpt.AGENTS_DISABLED not in argv
    assert 'multi_agent' not in [argv[i+1] for i,x in enumerate(argv) if x=='--disable']
    with pytest.raises(ValueError): gpt.build_argv(req, enable_subagents=True)


def test_sample_and_leak(req, monkeypatch):
    sample(req)
    monkeypatch.setattr(gpt, 'cli_version', lambda: pytest.fail('parse must not invoke CLI'))
    result = gpt.parse(req)
    assert result.completed and result.error_type is None
    assert result.reported_model_id is None and result.model_check == 'unverified'
    assert result.tool_call_counts == {'web_search':2}
    assert any(e.state=='opened' and e.host=='www.python.org' for e in result.url_events)
    assert all(e.state=='unknown' for e in result.url_events if e.source=='search_result')
    assert not any(e.state=='opened' and e.host=='www.metaculus.com' for e in result.url_events)
    assert result.audit_flags == ['leak_confirmed'] and result.audit_required
    assert result.usage['reasoning_tokens'] == result.usage_raw['reasoning_output_tokens']
    assert result.usage['cached_tokens'] == result.usage_raw['cached_input_tokens']
    assert 'cache_write_input_tokens' not in result.usage
    assert 'violations' in asdict(result)


def test_actual_claim_is_not_open_evidence(req):
    sample(req, final='last.json')
    result = gpt.parse(req)
    assert result.structured['opened_urls'][-1].startswith('https://www.metaculus.com')
    claims = [e for e in result.url_events if e.host=='www.metaculus.com']
    assert claims and all(e.state=='unknown' and e.source=='model_report' for e in claims)
    assert result.audit_flags == ['suspected_leak']
    # Downstream audit scans raw prose too; parser does not assert an unobserved open.


def test_tools_and_find(req):
    sample(req, events='tools.jsonl')
    result = gpt.parse(req)
    assert result.audit_required
    assert {i['type'] for i in result.violations} == {'command_execution','file_change'}
    assert result.denied_actions is None
    assert result.tool_call_counts['file_change'] == 1
    assert any(e.source=='tool_result' and e.state=='unknown' for e in result.url_events)


@pytest.mark.parametrize('line', ['{bad', '[]', '{"type":"item.completed","item":null}', ''])
def test_corrupt_lines_recorded(req, line):
    sample(req)
    with (req.raw_dir/'events.jsonl').open('a') as f: f.write(line+'\n')
    result = gpt.parse(req)
    assert not result.completed and result.parse_errors[0]['line'] > 0
    assert result.audit_required and result.error_type=='cli_error'


@pytest.mark.parametrize('text', ['', '   ', 'bad json', '[]'])
def test_bad_final(req, text):
    sample(req)
    (req.raw_dir/'last.json').write_text(text)
    result = gpt.parse(req)
    assert result.structured is None and result.error_type=='invalid_output'
    assert result.completed == bool(text.strip())


def test_missing_files(req):
    result = gpt.parse(req)
    assert not result.completed and len(result.parse_errors)==2


@pytest.mark.parametrize('event,expected', [
    ({'type':'turn.failed','error':{'message':'oops'}}, 'cli_error'),
    ({'type':'error','message':'usage_limit_reached'}, 'quota_exhausted'),
])
def test_errors_override_completion(req, event, expected):
    write_events(req,[{'type':'turn.completed','usage':{}},event])
    result=gpt.parse(req)
    assert not result.completed and result.error_type==expected


@pytest.mark.parametrize('kwargs,expected', [({'timed_out':True},'timeout'),({'exit_code':1},'cli_error')])
def test_process_failure(req, kwargs, expected):
    sample(req)
    result=gpt.parse(req, execution(req, **kwargs))
    assert not result.completed and result.error_type==expected


def test_multi_turn_no_sum(req):
    write_events(req,[{'type':'turn.completed','usage':{'input_tokens':10}},
                      {'type':'turn.completed','usage':{'input_tokens':20}}])
    result=gpt.parse(req)
    assert result.usage_raw=={'turns':[{'input_tokens':10},{'input_tokens':20}]}
    assert result.usage==dict.fromkeys(USAGE_KEYS)


def test_incomplete_and_denied(req):
    write_events(req,[{'type':'item.started','item':{'id':'a','type':'web_search','action':{'type':'open_page','url':'https://example.com'}}}])
    result=gpt.parse(req)
    assert not result.completed and result.url_events[0].state=='attempted'
    write_events(req,[{'type':'item.completed','item':{'id':'a','type':'web_search','status':'denied','action':{'type':'open_page','url':'https://example.com'}}}])
    result=gpt.parse(req)
    assert result.error_type=='tool_denied' and result.denied_actions
    assert result.url_events[0].state=='blocked'


def test_format_repair_tools_violate(req):
    sample(req)
    assert gpt.parse(replace(req,profile='format_repair')).violations


def test_run_uses_runner_and_stdin(req, monkeypatch):
    monkeypatch.setattr(gpt,'cli_version',lambda:'0.166.0')
    def fake_run(argv, **kwargs):
        assert kwargs['stdin_text']==req.prompt
        assert kwargs['keep_env']==()
        assert kwargs['cwd']==req.workdir.resolve()
        assert json.loads((req.raw_dir/'schema.json').read_text())==req.schema
        sample(req)
        return execution(req)
    monkeypatch.setattr(gpt.runner,'run',fake_run)
    assert gpt.run(req).cli_version=='0.166.0'
    with pytest.raises(FileExistsError): gpt.run(req)


@pytest.mark.parametrize('stdout,code,expected', [('codex-cli 0.166.0\n',0,'0.166.0'),('bad',0,None),('codex-cli 0.166.0',1,None)])
def test_version(monkeypatch, stdout, code, expected):
    monkeypatch.setattr(gpt.subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout=stdout,returncode=code))
    assert gpt.cli_version()==expected


def test_launch_failure(req, monkeypatch):
    monkeypatch.setattr(gpt,'cli_version',lambda:None)
    def fail(*args, **kwargs):
        raise FileNotFoundError('codex unavailable')
    monkeypatch.setattr(gpt.runner,'run',fail)
    result=gpt.run(req)
    assert result.error_type=='cli_error' and not result.completed
    assert result.run is None


def test_partial_item_merge(req):
    write_events(req,[
        {'type':'item.started','item':{'id':'a','type':'web_search','action':{'type':'open_page','url':'https://example.com'}}},
        {'type':'item.completed','item':{'id':'a','type':'web_search','action':{'type':'open_page'}}},
        {'type':'turn.completed','usage':{}},
    ])
    result=gpt.parse(req)
    assert result.tool_call_counts=={'web_search':1}
    assert result.url_events[0].state=='opened'


def test_default_argv_disables_code_mode_and_collaboration(tmp_path):
    from adapters.base import AdapterRequest
    req = AdapterRequest(player='gpt', profile='tier1', prompt='p', schema={}, workdir=tmp_path,
                         raw_dir=tmp_path / 'raw', model='gpt-6-astra', effort='medium', timeout_s=60)
    argv = gpt.build_argv(req)
    disabled = {argv[i + 1] for i, x in enumerate(argv) if x == '--disable'}
    assert {'code_mode_host', 'in_app_local_automation', 'code_mode_interrupt', 'multi_agent', 'shell_tool'} <= disabled
    assert argv[argv.index(gpt.AGENTS_DISABLED) - 1] == '-c'
