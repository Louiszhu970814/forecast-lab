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
    # the sample's open_page result carries no turnNviewM page reference, so it is not proof of an open
    assert any(e.state=='attempted' and e.host=='www.python.org' and e.source=='tool_use' for e in result.url_events)
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


CATALOG = {'models': [{'slug': 'gpt-6-astra', 'tool_mode': 'code_mode_only', 'apply_patch_tool_type': 'freeform'},
                      {'slug': 'gpt-6-sol', 'tool_mode': 'code_mode_only', 'apply_patch_tool_type': 'freeform'}]}


def test_run_uses_runner_and_stdin(req, monkeypatch):
    monkeypatch.setattr(gpt,'cli_version',lambda:'0.166.0')
    monkeypatch.setattr(gpt,'load_catalog',lambda: CATALOG)
    def fake_run(argv, **kwargs):
        assert kwargs['stdin_text']==req.prompt
        assert kwargs['keep_env']==()
        assert kwargs['cwd']==req.workdir.resolve()
        assert json.loads((req.raw_dir/'schema.json').read_text())==req.schema
        catalog = json.loads((req.raw_dir/gpt.CATALOG_FILENAME).read_text())
        assert catalog['models'][0]['tool_mode'] == 'direct' and 'apply_patch_tool_type' not in catalog['models'][0]
        assert 'model_catalog_json=' + json.dumps(str((req.raw_dir/gpt.CATALOG_FILENAME).resolve())) in argv
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
    monkeypatch.setattr(gpt,'load_catalog',lambda: CATALOG)
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
    # no page result: an attempt, never an open (GPT review 3)
    assert result.url_events[0].state=='attempted'


def test_declined_or_resultless_metaculus_open_is_suspected_not_confirmed(req):
    for status, results in (('declined', []), ('completed', []), ('declined', [
            {'ref_id': 'turn0view0', 'url': 'https://www.metaculus.com/questions/1/', 'title': 'Q'}])):
        write_events(req, [
            {'type': 'item.started', 'item': {'id': 'a', 'type': 'web_search',
                                              'action': {'type': 'open_page', 'url': 'https://www.metaculus.com/'}}},
            {'type': 'item.completed', 'item': {'id': 'a', 'type': 'web_search', 'status': status, 'results': results,
                                                'action': {'type': 'open_page', 'url': 'https://www.metaculus.com/'}}},
            {'type': 'turn.completed', 'usage': {}},
        ])
        result = gpt.parse(req)
        assert {e.state for e in result.url_events if 'metaculus' in (e.host or '')} == {'attempted'}, status
        assert result.audit_flags == [], (status, result.audit_flags)


def test_partial_updates_merge_and_a_page_result_confirms_the_open(req):
    write_events(req, [
        {'type': 'item.started', 'item': {'id': 'a', 'type': 'web_search', 'action': {'type': 'open_page'}}},
        {'type': 'item.updated', 'item': {'id': 'a', 'type': 'web_search', 'action': {'url': 'https://www.metaculus.com/'}}},
        {'type': 'item.completed', 'item': {'id': 'a', 'type': 'web_search', 'results': [
            {'ref_id': 'turn0view0', 'url': 'https://www.metaculus.com/', 'title': 'Metaculus'}]}},
        {'type': 'turn.completed', 'usage': {}},
    ])
    result = gpt.parse(req)
    assert [(e.state, e.source) for e in result.url_events] == [('opened', 'tool_use'), ('opened', 'tool_result')]
    assert result.audit_flags == ['leak_confirmed']


def test_default_argv_disables_code_mode_and_collaboration(tmp_path):
    from adapters.base import AdapterRequest
    req = AdapterRequest(player='gpt', profile='tier1', prompt='p', schema={}, workdir=tmp_path,
                         raw_dir=tmp_path / 'raw', model='gpt-6-astra', effort='medium', timeout_s=60)
    argv = gpt.build_argv(req)
    disabled = {argv[i + 1] for i, x in enumerate(argv) if x == '--disable'}
    assert {'code_mode_host', 'in_app_local_automation', 'code_mode_interrupt', 'multi_agent', 'shell_tool'} <= disabled
    assert argv[argv.index(gpt.AGENTS_DISABLED) - 1] == '-c'


def test_restricted_catalog_changes_only_the_requested_model():
    out = gpt.restricted_catalog(CATALOG, 'gpt-6-astra')
    astra, sol = out['models']
    assert astra['tool_mode'] == 'direct' and astra['node_repl_disabled'] is True
    assert 'apply_patch_tool_type' not in astra
    assert sol == CATALOG['models'][1] and CATALOG['models'][0]['tool_mode'] == 'code_mode_only'
    with pytest.raises(ValueError):
        gpt.restricted_catalog(CATALOG, 'gpt-unknown')


def test_catalog_failure_is_a_cli_error(req, monkeypatch):
    monkeypatch.setattr(gpt, 'load_catalog', lambda: {'models': []})
    monkeypatch.setattr(gpt.runner, 'run', lambda *a, **k: pytest.fail('must not run without a catalog'))
    result = gpt.run(req)
    assert result.error_type == 'cli_error' and 'catalog' in result.error


def test_view_result_inside_search_is_an_open(req):
    # web.run opened pages inside a search call in M1 acceptance (ref_id "turn0view0").
    write_events(req, [
        {'type': 'item.completed', 'item': {'id': 'ws', 'type': 'web_search', 'action': {'type': 'search', 'query': 'q'},
         'results': [{'type': 'text_result', 'ref_id': 'turn0search0', 'url': 'https://www.metaculus.com/questions/1/'},
                     {'type': 'text_result', 'ref_id': 'turn0view0', 'url': 'https://www.metaculus.com/questions/2/'}]}},
        {'type': 'turn.completed', 'usage': {}},
    ])
    result = gpt.parse(req)
    states = {e.url: (e.state, e.source) for e in result.url_events}
    assert states['https://www.metaculus.com/questions/1/'] == ('unknown', 'search_result')
    assert states['https://www.metaculus.com/questions/2/'] == ('opened', 'tool_result')
    assert result.audit_flags == ['leak_confirmed']


def test_catalog_validation_and_fingerprint():
    with pytest.raises(ValueError):
        gpt.restricted_catalog({'models': [{'slug': 'a'}, {'slug': 'a'}]}, 'a')
    with pytest.raises(ValueError):
        gpt.restricted_catalog({'models': [{'name': 'no slug'}]}, 'a')
    out = gpt.restricted_catalog(CATALOG, 'gpt-6-astra')
    assert gpt.catalog_entry_sha256(out, 'gpt-6-astra') == gpt.catalog_entry_sha256(json.loads(json.dumps(out)), 'gpt-6-astra')
    changed = json.loads(json.dumps(out)); changed['models'][0]['tool_mode'] = 'code_mode_only'
    assert gpt.catalog_entry_sha256(changed, 'gpt-6-astra') != gpt.catalog_entry_sha256(out, 'gpt-6-astra')


def test_run_uses_a_pinned_catalog(req, monkeypatch):
    monkeypatch.setattr(gpt, 'cli_version', lambda: '0.166.0')
    monkeypatch.setattr(gpt, 'load_catalog', lambda: pytest.fail('a pinned catalog must not be reloaded'))
    def fake_run(argv, **kwargs):
        sample(req)
        return execution(req)
    monkeypatch.setattr(gpt.runner, 'run', fake_run)
    result = gpt.run(req, catalog=CATALOG)
    assert result.catalog_entry_sha256 == gpt.catalog_entry_sha256(gpt.restricted_catalog(CATALOG, 'gpt-6-astra'), 'gpt-6-astra')


def test_error_or_denied_views_are_not_opens(req):
    write_events(req, [
        {'type': 'item.completed', 'item': {'id': 'o', 'type': 'web_search',
         'action': {'type': 'open_page', 'url': 'https://www.metaculus.com/q/'},
         'results': [{'type': 'text_result', 'ref_id': 'turn0view0', 'title': 'Internal Error',
                      'snippet': 'Unable to resolve open call due to invalid ref_id argument'}]}},
        {'type': 'item.completed', 'item': {'id': 'd', 'type': 'web_search', 'status': 'blocked',
         'action': {'type': 'search', 'query': 'q'},
         'results': [{'type': 'text_result', 'ref_id': 'turn1view0', 'url': 'https://metaculus.com/'}]}},
        {'type': 'item.completed', 'item': {'id': 'x', 'type': 'web_search', 'action': {'type': 'search', 'query': 'q'},
         'results': [{'type': 'text_result', 'ref_id': 'preview', 'url': 'https://www.metaculus.com/z/'}]}},
        {'type': 'turn.completed', 'usage': {}},
    ])
    result = gpt.parse(req)
    states = {(e.url, e.source): e.state for e in result.url_events}
    assert states[('https://www.metaculus.com/q/', 'tool_use')] == 'attempted'
    assert states[('https://metaculus.com/', 'tool_result')] == 'blocked'
    assert states[('https://www.metaculus.com/z/', 'search_result')] == 'unknown'
    assert result.audit_flags == ['suspected_leak']
