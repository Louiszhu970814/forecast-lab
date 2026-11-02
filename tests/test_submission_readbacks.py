"""Acknowledgement verification cannot authorize retries of accepted writes."""
import copy
import json
import threading

import pytest

import submit as S
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from test_submit import environment, rows, set_mode


def checks(e):
    return read_complete(e['root'] / 'submission_readbacks.jsonl')


def test_cli_success_gets_both_operations_and_restart_deduplicates(environment):
    e = environment
    e['seed']()
    e['make']().run(verify_accepted=True)
    assert [(r['operation'], r['outcome']) for r in checks(e)] == [('forecast', 'found'), ('comment', 'found')]
    assert [r['status'] for r in rows(e)] == ['intent', 'request_started', 'forecast_ok', 'request_started', 'comment_ok']
    before = copy.deepcopy(rows(e))
    reads = list(e['transport'].reads)
    e['make']().run(verify_accepted=True)
    e['make']().run(reconcile_only=True, verify_accepted=True)
    assert e['transport'].reads == reads and len(e['transport'].writes) == 2
    assert rows(e) == before and len(checks(e)) == 2


def test_legacy_acknowledgements_receive_readback_without_changing_write_history(environment):
    e = environment
    e['seed']()
    e['make']().run()
    before = copy.deepcopy(rows(e))
    e['make']().run(verify_accepted=True)
    assert len(checks(e)) == 2 and all(r['outcome'] == 'found' for r in checks(e))
    assert rows(e) == before and len(e['transport'].writes) == 2


def test_delayed_visibility_retries_only_gets_and_keeps_accepted_state(environment):
    e = environment
    e['seed']()
    e['transport'].read_effects = ['empty', 'empty']
    e['make']().run(verify_accepted=True)
    before = copy.deepcopy(rows(e))
    assert [r['outcome'] for r in checks(e)] == ['unknown', 'unknown']
    e['make']().run(verify_accepted=True)
    assert [r['outcome'] for r in checks(e)] == ['unknown', 'unknown', 'found', 'found']
    assert rows(e) == before and len(e['transport'].writes) == 2


def test_contradictory_negative_readback_never_authorizes_resend(environment, monkeypatch):
    e = environment
    e['seed']()
    monkeypatch.setattr(e['transport'], 'readback', lambda op, intent, request_id:
        S.Readback('not_accepted', request_id=request_id, evidence='synthetic contradictory response'))
    for _ in range(3):
        e['make']().run(verify_accepted=True)
    assert len(e['transport'].writes) == 2
    assert len(checks(e)) == 6 and all(r['outcome'] == 'unknown' for r in checks(e))
    assert {r['status'] for r in rows(e)} == {'intent', 'request_started', 'forecast_ok', 'comment_ok'}


@pytest.mark.parametrize('mismatch', ['request', 'bot', 'target', 'payload'])
def test_mismatched_readback_is_unknown_without_leaking_payload(environment, monkeypatch, mismatch):
    e = environment
    e['seed']()
    original = e['transport'].readback
    def wrong(operation, intent, *, request_id):
        result = original(operation, intent, request_id=request_id)
        record = copy.deepcopy(result.record)
        rid = request_id
        if mismatch == 'request':
            rid = 'wrong-request'
        elif mismatch == 'bot':
            record['bot_id'] = 999
        elif mismatch == 'target':
            record['question_id' if operation == 'forecast' else 'post_id'] = 999
        else:
            record['payload'] = 'sensitive-synthetic-content'
        return S.Readback('found', record, rid)
    monkeypatch.setattr(e['transport'], 'readback', wrong)
    e['make']().run(verify_accepted=True)
    assert all(r['outcome'] == 'unknown' for r in checks(e))
    text = json.dumps(checks(e))
    assert 'sensitive-synthetic-content' not in text and 'probability' not in text and 'comment_text' not in text
    assert len(e['transport'].writes) == 2


def test_error_text_is_not_logged_and_known_receipt_stays_accepted(environment):
    e = environment
    e['seed']()
    e['transport'].read_effects = ['error', 'error']
    e['make']().run(verify_accepted=True)
    assert all(r['error_type'] == 'OSError' and r['outcome'] == 'unknown' for r in checks(e))
    assert 'secret' not in json.dumps(checks(e))
    assert rows(e)[-1]['status'] == 'comment_ok' and len(e['transport'].writes) == 2


def test_wrong_authenticated_account_cannot_produce_found(environment):
    e = environment
    e['seed']()
    e['make']().run()
    e['transport'].bot = 999
    e['make']().run(verify_accepted=True)
    assert all(r['outcome'] == 'unknown' and r['error_type'] == 'PrecheckError' for r in checks(e))
    assert not e['transport'].reads and len(e['transport'].writes) == 2


def test_local_mode_never_verifies_previous_trial_acknowledgements(environment):
    e = environment
    e['seed']()
    e['make']().run()
    set_mode(e, mode='local', approved_bot_id=None)
    e['make']().run(verify_accepted=True)
    assert not checks(e) and not e['transport'].reads and len(e['transport'].writes) == 2


def test_reconciled_unknown_is_not_resent_or_reverified(environment):
    e = environment
    e['seed']()
    e['transport'].effects['forecast'] = ['timeout_after']
    e['make']().run(verify_accepted=True)
    assert len(e['transport'].writes) == 1 and not checks(e)
    e['make']().run(reconcile_only=True, verify_accepted=True)
    assert len(e['transport'].writes) == 1 and not checks(e)
    assert rows(e)[-1]['status'] == 'forecast_ok' and rows(e)[-1]['reconciled'] is True
    e['make']().run(verify_accepted=True)
    assert [(r['operation'], r['outcome']) for r in checks(e)] == [('comment', 'found')]
    assert len(e['transport'].writes) == 2


def test_successful_record_for_wrong_lookup_cannot_suppress_read(environment):
    e = environment
    e['seed']()
    e['make']().run(verify_accepted=True)
    old = checks(e)[0]
    # Change the frozen lookup in a synthetic direct call to check the cache key.
    intent = copy.deepcopy(rows(e)[0])
    intent['forecast_payload'][0]['probability_yes'] = .4
    e['make']()._verify_accepted(intent)
    assert len(e['transport'].reads) == 3
    assert checks(e)[-1]['operation'] == 'forecast' and checks(e)[-1]['outcome'] == 'unknown'
    assert checks(e)[-1]['lookup_sha256'] != old['lookup_sha256']


def test_verification_does_not_hold_ledger_transaction_during_network(environment, monkeypatch):
    e = environment
    e['seed']()
    e['make']().run()
    original = e['transport'].readback
    workers, finished = [], []
    def read(operation, intent, *, request_id):
        done = threading.Event()
        def append():
            with transaction(e['root']):
                append_record(e['root'] / 'synthetic_events.jsonl', {'status': 'ok'})
            done.set()
        worker = threading.Thread(target=append)
        workers.append(worker)
        worker.start()
        finished.append(done.wait(1))
        return original(operation, intent, request_id=request_id)
    monkeypatch.setattr(e['transport'], 'readback', read)
    e['make']().run(verify_accepted=True)
    for worker in workers:
        worker.join(2)
    assert finished == [True, True]


@pytest.mark.parametrize('command', ['run', 'reconcile'])
def test_official_cli_enables_accepted_write_verification(tmp_path, monkeypatch, capsys, command):
    seen = []
    monkeypatch.setattr(S, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(S, 'cli_transport', lambda root: S.OfflineTransport())
    def run(self, **kwargs):
        seen.append(kwargs)
        return {'events': 0, 'statuses': {}}
    monkeypatch.setattr(S.Submitter, 'run', run)
    assert S.main([command]) == 0
    assert seen == [{'reconcile_only': command == 'reconcile', 'verify_accepted': True}]
    capsys.readouterr()
