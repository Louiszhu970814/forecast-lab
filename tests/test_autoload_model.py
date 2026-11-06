"""Codex's real parser has no model ID; acceptance must not fabricate one."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

import acceptance_judge as judge
import autoload as A
import forecast as F
from adapters import gpt
from adapters.base import AdapterRequest
from flab.config import load_run_config
from flab.ids import sha256_hex
from flab.jsonl import append_record, read_records
from flab.timeutil import to_iso, utc_now
from test_autoload import fx, _loaded_executor, _result
from test_autoload_maintenance import session


@pytest.fixture
def accepted(fx):
    model = load_run_config()['players']['gpt']['tier1']
    catalog = gpt.restricted_catalog({'models': [{'slug': model['model']}]}, model['model'])
    proof = {'code_sha256': F.runtime_fingerprint('gpt'), 'tier2_equivalent_to_tier1': True}
    fx.data.mkdir()
    path = fx.data / 'synthetic-acceptance.json'
    path.write_text(json.dumps(proof))
    record = {'type': 'acceptance_result', 'record_id': 'synthetic-acceptance',
              'run_id': 'synthetic-run', 'player': 'gpt', 'tier': 1, 'mode': 'production',
              'line_pass': True, 'cli_version': '0.166.0', 'judge_version': judge.JUDGE_VERSION,
              'model': model['model'], 'effort': model['effort'], 'model_call_count': 3,
              'finished_at': to_iso(utc_now()),
              'catalog_entry_sha256': gpt.catalog_entry_sha256(catalog, model['model']),
              'evidence_path': str(path), 'evidence_sha256': sha256_hex(path.read_bytes())}
    append_record(fx.data / 'acceptance.jsonl', record)
    return SimpleNamespace(fx=fx, catalog=catalog, record=record, proof=proof, path=path, model=model)


def parsed_result(req, catalog, *, tools=False, failed=False):
    """Feed realistic minimal JSONL through the protected, unmodified parser."""
    req.raw_dir.mkdir(parents=True, exist_ok=True)
    events = [{'type': 'thread.started', 'thread_id': 'synthetic'}, {'type': 'turn.started'}]
    if tools:
        events.append({'type': 'item.completed', 'item': {'id': 'call', 'type': 'command_execution'}})
    events.append({'type': 'turn.failed', 'error': {'message': 'synthetic failure'}} if failed else
                  {'type': 'turn.completed', 'usage': {'input_tokens': 1, 'output_tokens': 1}})
    (req.raw_dir / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
    (req.raw_dir / 'last.json').write_text('{"text":"NONE"}')
    (req.raw_dir / gpt.CATALOG_FILENAME).write_text(json.dumps(catalog))
    result = gpt.parse(req)
    result.cli_version = '0.166.0'
    result.catalog_entry_sha256 = gpt.catalog_entry_sha256(catalog, req.model)
    return result


def request(accepted):
    return AdapterRequest('gpt', 'tier1', A.PROMPT, A.SCHEMA, accepted.fx.base / 'work',
                          accepted.fx.data / 'raw', accepted.model['model'], accepted.model['effort'], 600)


def check(accepted, req, result):
    return A.model_evidence(req, result, acceptance_log=accepted.fx.data / 'acceptance.jsonl')


def test_real_parser_unverified_is_accepted_without_relabeling_identity(accepted):
    req = request(accepted)
    result = parsed_result(req, accepted.catalog)
    assert result.completed and result.error_type is None and result.tool_call_counts == {}
    assert result.model_check == 'unverified' and result.reported_model_id is None
    evidence = check(accepted, req, result)
    assert evidence['accepted'] and evidence['basis'] == 'codex_requested_model_and_accepted_profile'
    assert evidence['requested_model'] == accepted.model['model']
    assert evidence['acceptance_record_id'] == evidence['production_record_id'] == accepted.record['record_id']
    assert evidence['model_check'] == result.model_check == 'unverified'
    assert evidence['reported_model_id'] is result.reported_model_id is None


@pytest.mark.parametrize('fault', ['model', 'effort', 'user_config', 'duplicate_model', 'tools', 'raw_path'])
def test_argv_changes_are_rejected(accepted, fault):
    req = request(accepted); result = parsed_result(req, accepted.catalog)
    if fault == 'model': result.argv[result.argv.index('-m') + 1] = 'different-model'
    elif fault == 'effort':
        i = next(i for i, v in enumerate(result.argv) if v.startswith('model_reasoning_effort='))
        result.argv[i] = 'model_reasoning_effort="high"'
    elif fault == 'user_config': result.argv.remove('--ignore-user-config')
    elif fault == 'duplicate_model': result.argv += ['-m', 'different-model']
    elif fault == 'tools': result.argv += ['--enable', 'shell_tool']
    else: result.argv[result.argv.index('--output-last-message') + 1] = '/other/last.json'
    assert check(accepted, req, result)['basis'] == 'codex_argv_differs'


@pytest.mark.parametrize('fault', ['missing', 'malformed', 'unrestricted', 'result_hash', 'accepted_hash', 'entry_changed'])
def test_missing_or_changed_catalog_is_rejected(accepted, fault):
    req = request(accepted); result = parsed_result(req, accepted.catalog)
    path = req.raw_dir / gpt.CATALOG_FILENAME
    if fault == 'missing': path.unlink()
    elif fault == 'malformed': path.write_text('[')
    elif fault == 'unrestricted': path.write_text(json.dumps({'models': [{'slug': req.model}]}))
    elif fault == 'result_hash': result.catalog_entry_sha256 = None
    elif fault == 'accepted_hash':
        append_record(accepted.fx.data / 'acceptance.jsonl', accepted.record | {'catalog_entry_sha256': 'changed'})
    else:
        catalog = json.loads(path.read_text()); catalog['models'][0]['extra'] = 'changed'
        path.write_text(json.dumps(catalog)); result.catalog_entry_sha256 = gpt.catalog_entry_sha256(catalog, req.model)
    assert not check(accepted, req, result)['accepted']


@pytest.mark.parametrize('fault', ['missing', 'failed', 'version', 'judge', 'fingerprint', 'proof_hash',
                                  'effort', 'model', 'pending', 'proof_missing'])
def test_current_acceptance_is_required(accepted, fault):
    req = request(accepted); result = parsed_result(req, accepted.catalog)
    log = accepted.fx.data / 'acceptance.jsonl'; record = accepted.record.copy()
    if fault == 'missing': log.unlink()
    elif fault == 'pending':
        append_record(log, {'type': 'acceptance_call_started', 'player': 'gpt', 'run_id': 'unfinished'})
    elif fault == 'proof_missing': accepted.path.unlink()
    elif fault == 'fingerprint':
        accepted.proof['code_sha256']['adapters/gpt.py'] = 'changed'
        accepted.path.write_text(json.dumps(accepted.proof))
        append_record(log, record | {'evidence_sha256': sha256_hex(accepted.path.read_bytes())})
    elif fault in ('effort', 'model'):
        # No older correct production proof should remain in this synthetic ledger.
        record[fault] = 'different'; log.write_text(json.dumps(record) + '\n')
    else:
        key, value = {'failed': ('line_pass', False), 'version': ('cli_version', 'new'),
                      'judge': ('judge_version', 'different'), 'proof_hash': ('evidence_sha256', 'changed')}[fault]
        append_record(log, record | {key: value})
    # D143 already permits known version changes; other acceptance failures still reject.
    assert check(accepted, req, result)['accepted'] is (fault == 'version')


@pytest.mark.parametrize('state', ['mismatch', 'undetermined'])
def test_codex_other_model_states_remain_rejected(accepted, state):
    req = request(accepted); result = parsed_result(req, accepted.catalog); result.model_check = state
    assert not check(accepted, req, result)['accepted']


def test_codex_unverified_with_reported_id_is_not_the_known_absence_case(accepted):
    req = request(accepted); result = parsed_result(req, accepted.catalog); result.reported_model_id = 'other'
    assert not check(accepted, req, result)['accepted']


@pytest.mark.parametrize('player', ['claude', 'gemini'])
@pytest.mark.parametrize('state', ['unverified', 'undetermined', 'mismatch'])
def test_exception_does_not_apply_to_other_players(accepted, player, state):
    req = replace(request(accepted), player=player)
    assert not check(accepted, req, _result(req, model_check=state))['accepted']


@pytest.mark.parametrize('fault', [None, 'tool', 'failed', 'missing_acceptance', 'mismatch'])
def test_whole_measurement_with_real_codex_parser(accepted, fault):
    fx = accepted.fx; loaded = _loaded_executor(fx)
    before = (fx.data / 'acceptance.jsonl').read_bytes()
    def execute(req):
        if req.player != 'gpt': return loaded(req)
        if fault == 'missing_acceptance': (fx.data / 'acceptance.jsonl').unlink()
        result = parsed_result(req, accepted.catalog, tools=fault == 'tool', failed=fault == 'failed')
        if fault == 'mismatch': result.model_check = 'mismatch'
        return result, A.Declaration()
    rec = A.run(**fx.kwargs, executor=execute)
    assert rec['restore_ok']
    assert rec['passed'] is (fault is None)
    assert ('gpt' in rec['completed_players']) is (fault is None)
    if fault == 'mismatch': assert rec['errors'][-1]['error'] == 'model_mismatch'
    if fault is None:
        assert rec['model_evidence']['gpt']['model_check'] == 'unverified'
        assert rec['model_evidence']['gpt']['reported_model_id'] is None
        assert (fx.data / 'acceptance.jsonl').read_bytes().startswith(before)
        rows = read_records(fx.data / 'acceptance.jsonl')
        assert sum(r['type'] == 'acceptance_result' for r in rows) == 1
        assert not any(r['type'] == 'baseline_approved' for r in rows)
    assert not list(fx.home.rglob('SKILL.md'))


def test_relative_acceptance_evidence_uses_the_run_project(accepted):
    fx = accepted.fx
    append_record(fx.data / 'acceptance.jsonl', accepted.record | {
        'evidence_path': str(accepted.path.relative_to(fx.data.parent))})
    loaded = _loaded_executor(fx)
    def execute(req):
        return (parsed_result(req, accepted.catalog), A.Declaration()) if req.player == 'gpt' else loaded(req)
    rec = A.run(**fx.kwargs, executor=execute)
    assert rec['passed'] and rec['model_evidence']['gpt']['accepted']


@pytest.mark.parametrize('profile', ['tier2', 'format_repair', 'acceptance'])
def test_unverified_codex_exception_is_tier1_only(accepted, profile):
    req = replace(request(accepted), profile=profile)
    assert not check(accepted, req, parsed_result(req, accepted.catalog))['accepted']


@pytest.mark.parametrize('player', ['claude', 'gemini', 'gpt'])
@pytest.mark.parametrize('error', [None, 'invalid_output'])
def test_mismatch_always_stops_before_next_player(accepted, player, error):
    calls = []
    def execute(req):
        calls.append(req.player)
        return _result(req, model_check='mismatch', error_type=error), A.Declaration()
    players = (player,) + tuple(p for p in A.baseline.PLAYERS if p != player)
    rec = A.run(**accepted.fx.kwargs, players=players, executor=execute)
    assert calls == [player] and not rec['passed'] and rec['restore_ok']
    assert rec['errors'] == [{'player': player, 'error': 'model_mismatch'}]


def test_controller_accepts_real_codex_parser_without_stop_or_new_approval(session, accepted):
    s = session.s
    append_record(s.root / 'acceptance.jsonl', accepted.record)
    def execute(req):
        session.calls.append(req.player)
        return (parsed_result(req, accepted.catalog) if req.player == 'gpt' else _result(req)), A.Declaration()
    s.execute = execute
    result = s.run(approved_by='Louis')
    assert result['status'] == 'complete' and result['restore_ok']
    assert session.calls == list(A.baseline.PLAYERS)
    assert not (s.project / 'config/STOP_SUBMIT').exists()
    rows = read_records(s.root / 'acceptance.jsonl')
    assert sum(r['type'] == 'baseline_approved' for r in rows) == 1
    assert sum(r['type'] == 'acceptance_result' for r in rows) == 1


def test_controller_stops_on_mismatch_even_with_other_error(session):
    import autoload_maintenance as M
    def execute(req):
        session.calls.append(req.player)
        return _result(req, model_check='mismatch', error_type='invalid_output'), A.Declaration()
    session.s.execute = execute
    with pytest.raises(M.MaintenanceError, match='forbidden scope or model'):
        session.s.run(approved_by='Louis')
    assert session.calls == ['claude']
    assert (session.s.project / 'config/STOP_SUBMIT').exists()
    assert not A.unclosed(session.s.root / 'acceptance.jsonl')
