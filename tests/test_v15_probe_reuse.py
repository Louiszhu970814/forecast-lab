"""D107: synthetic HTTP only, including unchanged Submitter boundary evidence."""
import json
import urllib.error
import urllib.parse
from datetime import timedelta

import pytest

import submit as S
import submit_transport as T
import v15_probe as V
from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import to_iso
from test_m3_w9 import Response, TOKEN, BOT, MAIN
from test_submit import NOW, environment
from test_v15_probe import ProbeHTTP, probe, rewrite_plan


class ReuseHTTP(ProbeHTTP):
    """Keep real APITransport matching, with distinct identities and retained history."""
    def __init__(self):
        super().__init__()
        self.fail_at = None
        self.truncate_at = None
        self.hide_at = None
        self.stale_at = None
        self.drop_history = False
        self.post_count = 0
        self.now = lambda: NOW

    def __call__(self, req, timeout):
        if req.method != 'POST':
            return super().__call__(req, timeout)
        self.calls.append(req)
        self.post_count += 1
        if self.post_count == self.fail_at:
            raise urllib.error.HTTPError(req.full_url, self.failure_status, TOKEN, {}, None)
        data = json.loads(req.data)
        if req.full_url.endswith('/forecast/'):
            if self.post_count != self.stale_at:
                payload = data[0]
                values = ([1 - payload['probability_yes'], payload['probability_yes']]
                    if 'probability_yes' in payload else list(payload['probability_yes_per_category'].values()))
                row = {'question_id': 7, 'author_id': V.BOT_ID,
                    'start_time': (self.now() + timedelta(seconds=self.post_count / 10)).timestamp(),
                    'forecast_values': values}
                history = self.post['question']['my_forecasts']['history']
                self.post['question']['my_forecasts'] = {
                    'history': ([] if self.drop_history else history) + [row], 'latest': row}
            if self.post_count == self.hide_at:
                self.hide_readback = True
        else:
            if self.post_count != self.hide_at:
                text = data['text'][:1000] if self.post_count == self.truncate_at else data['text']
                self.comments.append({'id': 100 + self.post_count, 'author': {'id': V.BOT_ID},
                    'on_post': 17, 'created_at': to_iso(self.now() + timedelta(seconds=.2)),
                    'is_private': True, 'text': text, 'included_forecast': {'question_type': 'binary',
                    'probability_yes': .5}})
        return Response({})


@pytest.fixture
def reuse(probe):
    obj, _, project = probe
    http = ReuseHTTP()
    obj.transport = T.APITransport(TOKEN, opener=http, poll_log=project / 'data/poll_log.jsonl')
    obj.transport.client._sleep = lambda _: None
    obj.sub.transport = obj.transport
    return obj, http, project


def seed_prior(reuse, *, state='found', question_type='binary'):
    obj, http, _ = reuse
    if question_type == 'multiple_choice':
        http.post['question'].update(type=question_type, options=['a', 'b', 'c'], all_options_ever=None)
    pid = obj.prepare(question_type=question_type)['probe_id']
    obj.event(pid, 'request_started', operation='forecast', request_id='prior-request')
    if state:
        obj.event(pid, state, operation='forecast', request_id='prior-request',
            error_type='ConfirmedFailure', http_status=400)
    row = {'question_id': 7, 'author_id': V.BOT_ID,
        'start_time': (NOW - timedelta(seconds=10)).timestamp(),
        'forecast_values': [.5, .5] if question_type == 'binary' else [.333333, .333333, .333334]}
    http.post['question']['my_forecasts'] = {'history': [row], 'latest': row}
    return pid


@pytest.mark.parametrize('question_type', ['binary', 'multiple_choice'])
def test_duplicate_forecasts_read_back_new_ids_and_history(reuse, question_type):
    obj, http, project = reuse
    seed_prior(reuse, question_type=question_type)
    pid = obj.prepare(question_type, 'duplicate_forecast')['probe_id']
    plan = obj.plan(pid)
    assert plan['max_forecast_requests'] == 2 and plan['max_comment_requests'] == 0
    result = obj.run(pid)
    assert result['status'] == 'complete'
    assert result['platform_behavior'] == 'accepted_with_history'
    assert result['history_counts'] == [1, 2, 3]
    assert len(http.writes()) == 2
    assert all(r.full_url.endswith('/forecast/') for r in http.writes())
    assert json.loads(http.writes()[0].data) != json.loads(http.writes()[1].data)
    rows = [r for r in obj.rows() if r['probe_id'] == pid]
    starts = [r for r in rows if r['state'] == 'request_started']
    found = [r for r in rows if r['state'] == 'found']
    assert found[0]['receipt']['id'] in starts[1]['request_forecast_baseline']
    assert found[1]['receipt']['id'] not in starts[1]['request_forecast_baseline']
    if question_type == 'multiple_choice':
        assert all(r['receipt']['option_order_verified'] for r in found)
        assert plan['second_forecast_payload'][0]['probability_yes_per_category'] == {
            'a': .4, 'b': .333333, 'c': .266667}
    calls = len(http.calls)
    assert obj.run(pid) == result and len(http.calls) == calls
    assert not (project / 'data/submissions.jsonl').exists()
    with pytest.raises(S.PrecheckError, match='reserved'):
        obj.sub._duplicate({'intent_id': 'normal', 'bot_id': V.BOT_ID, 'question_id': 7})


def test_duplicate_can_use_previously_unused_question(reuse):
    obj, http, _ = reuse
    pid = obj.prepare(scenario='duplicate_forecast')['probe_id']
    assert obj.run(pid)['history_counts'] == [0, 1, 2]
    assert len(http.writes()) == 2


@pytest.mark.parametrize('failure', ['400', '429', 'unknown', 'stale'])
@pytest.mark.parametrize('at', [1, 2])
def test_duplicate_stops_at_failed_or_unrecognized_request(reuse, failure, at):
    obj, http, _ = reuse
    seed_prior(reuse)
    pid = obj.prepare(scenario='duplicate_forecast')['probe_id']
    if failure in ('400', '429'):
        http.fail_at, http.failure_status = at, int(failure)
    elif failure == 'unknown':
        http.hide_at = at
    else:
        http.stale_at = at
    result = obj.run(pid)
    assert result['status'] == ('confirmed_failed' if failure in ('400', '429') else 'unknown')
    assert len(http.writes()) == at
    assert obj.run(pid) == result and len(http.writes()) == at
    if failure in ('400', '429'):
        assert result['http_status'] == int(failure)


def test_duplicate_reports_replacement_without_claiming_retained_history(reuse):
    obj, http, _ = reuse
    seed_prior(reuse)
    http.drop_history = True
    result = obj.run(obj.prepare(scenario='duplicate_forecast')['probe_id'])
    assert result['status'] == 'complete'
    assert result['platform_behavior'] == 'accepted_without_full_history'
    assert result['history_counts'] == [1, 1, 1]


@pytest.mark.parametrize('question_type', ['binary', 'multiple_choice'])
@pytest.mark.parametrize('failure,at', [
    (None, None), ('400', 1), ('400', 2), ('400', 3), ('400', 4),
    ('429', 2), ('truncate', 2), ('unknown', 2)])
def test_length_steps_results_and_no_extra_posts(reuse, question_type, failure, at):
    obj, http, project = reuse
    seed_prior(reuse, question_type=question_type)
    pid = obj.prepare(question_type, 'comment_length')['probe_id']
    if failure in ('400', '429'):
        http.fail_at, http.failure_status = at, int(failure)
    elif failure == 'truncate':
        http.truncate_at = at
    elif failure == 'unknown':
        http.hide_at = at
    result = obj.run(pid)
    count = at or 4
    assert len(http.writes()) == count
    assert all(r.full_url.endswith('/comments/create/') for r in http.writes())
    assert [len(json.loads(r.data)['text']) for r in http.writes()] == list(V.LENGTH_STEPS[:count])
    assert all(json.loads(r.data)['text'].isascii() and pid in json.loads(r.data)['text']
        and 'Synthetic length test; not a research forecast' in json.loads(r.data)['text']
        and json.loads(r.data)['included_forecast'] is True
        for r in http.writes())
    accepted = V.LENGTH_STEPS[count - 2] if failure and count > 1 else (0 if failure else 150000)
    assert result['max_accepted_length'] == accepted
    assert result['first_rejected_length'] == (V.LENGTH_STEPS[at - 1] if failure in ('400', '429') else None)
    assert result['truncation_detected'] is (failure == 'truncate')
    expected = {None: 'complete', '400': 'confirmed_failed', '429': 'confirmed_failed',
        'truncate': 'truncated', 'unknown': 'unknown'}
    assert result['status'] == expected[failure]
    calls = len(http.calls)
    assert obj.run(pid) == result and len(http.calls) == calls
    assert not (project / 'data/submissions.jsonl').exists()
    assert 'UNIQUE-QUESTION-TEXT' not in obj.log.read_text()
    with pytest.raises(S.PrecheckError, match='reserved'):
        obj.sub._duplicate({'intent_id': 'normal', 'bot_id': V.BOT_ID, 'question_id': 7})


@pytest.mark.parametrize('scenario', ['comment_length', 'comment_retry', 'duplicate_forecast'])
@pytest.mark.parametrize('state', [None, 'unknown', 'write_unconfirmed'])
def test_reuse_refuses_unresolved_probe_requests(reuse, scenario, state):
    obj, http, _ = reuse
    seed_prior(reuse, state=state)
    with pytest.raises(S.PrecheckError, match='no eligible'):
        obj.prepare(scenario=scenario)
    assert not http.writes()


@pytest.mark.parametrize('state', ['found', 'confirmed_failed'])
def test_resolved_probe_reservation_can_be_reused(reuse, state):
    obj, http, _ = reuse
    seed_prior(reuse, state=state)
    assert obj.prepare(scenario='duplicate_forecast')['question_id'] == 7
    with pytest.raises(S.PrecheckError, match='no eligible'):
        obj.prepare()
    assert not http.writes()


@pytest.mark.parametrize('scenario', ['comment_length', 'comment_retry', 'duplicate_forecast'])
@pytest.mark.parametrize('change', ['main', 'closed', 'future', 'soon', 'invalid_options', 'unreconcilable'])
def test_reuse_keeps_selection_checks(reuse, scenario, change):
    obj, http, _ = reuse
    seed_prior(reuse, question_type='multiple_choice')
    q = http.post['question']
    if change == 'main':
        http.post['projects']['tournament'] = [{'id': MAIN}]
    elif change == 'closed':
        q['status'] = 'closed'
    elif change == 'future':
        q['open_time'] = to_iso(NOW + timedelta(minutes=1))
    elif change == 'soon':
        q['scheduled_close_time'] = to_iso(NOW + timedelta(hours=1))
    elif change == 'invalid_options':
        q['options'] = ['a', 'a']
    else:
        q['all_options_ever'] = ['a']
    with pytest.raises(S.PrecheckError, match='no eligible'):
        obj.prepare('multiple_choice', scenario)
    assert not http.writes()


@pytest.mark.parametrize('missing', ['forecast', 'reservation'])
def test_length_requires_existing_forecast_and_normal_submitter_reservation(reuse, missing):
    obj, http, _ = reuse
    seed_prior(reuse)
    if missing == 'forecast':
        http.post['question']['my_forecasts'] = {'history': [], 'latest': None}
    else:
        obj.log.unlink()  # Synthetic test fixture only.
    with pytest.raises(S.PrecheckError, match='no eligible'):
        obj.prepare(scenario='comment_length')
    assert not http.writes()


@pytest.mark.parametrize('scenario', ['comment_length', 'comment_retry', 'duplicate_forecast'])
@pytest.mark.parametrize('change', ['forecast_cap', 'comment_cap', 'bool_cap', 'authorization',
    'scenario', 'drop_scenario', 'payload', 'venue', 'intent_id', 'allow_resubmit', 'extra_fields'])
def test_rehashed_tampered_reuse_plan_is_rejected_before_io(reuse, scenario, change):
    obj, http, _ = reuse
    seed_prior(reuse)
    pid = obj.prepare(scenario=scenario)['probe_id']
    rows = obj.rows()
    row = next(r for r in rows if r['probe_id'] == pid)
    p = row['plan']
    if change == 'forecast_cap':
        p['max_forecast_requests'] = 3
    elif change == 'comment_cap':
        p['max_comment_requests'] = 5
    elif change == 'bool_cap':
        p['max_forecast_requests'] = False
    elif change == 'authorization':
        p['reuse_authorization'] = 'wrong'
    elif change == 'scenario':
        p['scenario'] = 'single'
    elif change == 'drop_scenario':
        del p['scenario']
    elif change == 'venue':
        p['tournament_id'] = p['snapshot']['tournament_id'] = MAIN
    elif change == 'intent_id':
        p['intent_id'] = 'another-plan'
    elif change == 'allow_resubmit':
        p['allow_resubmit'] = True
    elif change == 'extra_fields':
        p['second_forecast_payload' if scenario == 'comment_length' else 'comment_lengths'] = []
    elif scenario == 'comment_length':
        p['comment_lengths'] = [2000]
    elif scenario == 'comment_retry':
        p['comment_payload']['text'] = 'changed synthetic comment'
        p['comment_hash'] = V.sha256_hex(p['comment_payload']['text'])
    else:
        p['second_forecast_payload'][0]['probability_yes'] = .7
    row['plan_sha256'] = hash_obj(p)
    obj.log.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    calls = len(http.calls)
    with pytest.raises(S.PrecheckError):
        obj.run(pid)
    assert len(http.calls) == calls and not http.writes()


@pytest.mark.parametrize('scenario', ['comment_length', 'comment_retry', 'duplicate_forecast'])
@pytest.mark.parametrize('change', ['stop', 'local', 'account', 'membership', 'revision', 'new_unknown', 'normal_intent'])
def test_reuse_rechecks_gates_and_formal_ledger_without_writing_it(reuse, scenario, change):
    obj, http, project = reuse
    seed_prior(reuse)
    pid = obj.prepare(scenario=scenario)['probe_id']
    if change == 'stop':
        (project / 'config/STOP_SUBMIT').touch()
    elif change == 'local':
        (project / 'config/submit_mode.json').write_text(json.dumps({'mode': 'local', 'approved_bot_id': V.BOT_ID}))
    elif change == 'account':
        http.wrong_user = True
    elif change == 'membership':
        http.post['projects']['tournament'].append({'id': MAIN})
    elif change == 'revision':
        http.post['question']['scheduled_close_time'] = to_iso(NOW + timedelta(days=40))
    elif change == 'new_unknown':
        prior = obj.rows()[0]['probe_id']
        obj.event(prior, 'unknown', operation='forecast', request_id='prior-request')
    else:
        append_record(project / 'data/submissions.jsonl', {'status': 'intent', 'intent_id': 'formal',
            'bot_id': V.BOT_ID, 'question_id': 7, 'mode': 'trial', 'tournament_id': BOT})
    log = project / 'data/submissions.jsonl'
    before = log.read_bytes() if log.exists() else None
    with pytest.raises(S.PrecheckError):
        obj.run(pid)
    assert (log.read_bytes() if log.exists() else None) == before
    assert not http.writes()


@pytest.mark.parametrize('scenario', ['duplicate_forecast', 'comment_retry'])
def test_no_recovery_after_crash_or_explicit_flag(reuse, monkeypatch, scenario):
    obj, http, _ = reuse
    seed_prior(reuse)
    pid = obj.prepare(scenario=scenario)['probe_id']
    calls = len(http.calls)
    with pytest.raises(S.PrecheckError, match='forbids'):
        obj.run(pid, retry_confirmed_comment=True)
    assert len(http.calls) == calls
    original = obj.event
    def crash(probe_id, state, **extra):
        original(probe_id, state, **extra)
        if state == 'request_started':
            raise KeyboardInterrupt()
    monkeypatch.setattr(obj, 'event', crash)
    with pytest.raises(KeyboardInterrupt):
        obj.run(pid)
    calls = len(http.calls)
    with pytest.raises(S.PrecheckError, match='no recovery'):
        obj.run(pid)
    assert len(http.calls) == calls and not http.writes()


def test_v3_plan_still_validates_and_runs_unchanged(probe):
    obj, http, _ = probe
    pid = obj.prepare()['probe_id']
    rewrite_plan(obj, lambda p: None, version='v15_probe_v3')
    assert obj.run(pid)['status'] == 'complete'
    assert len(http.writes()) == 2


@pytest.mark.parametrize('scenario', V.SCENARIOS)
def test_cli_scenario_forwarding_without_credentials_or_network(reuse, monkeypatch, capsys, scenario):
    import flab.envfile
    obj, http, _ = reuse
    calls = []
    monkeypatch.setattr(flab.envfile, 'get_secret', lambda *a: TOKEN)
    monkeypatch.setattr(V, 'APITransport', lambda token: obj.transport)
    monkeypatch.setattr(V, 'Probe', lambda *a: obj)
    def prepare(question_type, scenario):
        calls.append((question_type, scenario))
        return {'writes': 0}
    monkeypatch.setattr(obj, 'prepare', prepare)
    assert V.main(['prepare', '--scenario', scenario]) == 0
    assert calls == [('binary', scenario)]
    assert json.loads(capsys.readouterr().out) == {'writes': 0}
    assert not http.calls


@pytest.mark.parametrize('change', ['same_length_wrong_hash', 'diagnostic_error', 'ambiguous', 'wrong_author'])
def test_length_uncertain_evidence_never_counts_as_accepted(reuse, monkeypatch, change):
    obj, http, _ = reuse
    seed_prior(reuse)
    original = obj.transport.write
    def write(*args, **kwargs):
        receipt = original(*args, **kwargs)
        if change == 'same_length_wrong_hash':
            http.comments[-1]['text'] = http.comments[-1]['text'][:-1] + 'y'
        elif change == 'ambiguous':
            http.comments[-1]['text'] = http.comments[-1]['text'][:1000]
            http.comments.append(dict(http.comments[-1]))
        elif change == 'wrong_author':
            http.comments[-1]['author'] = {'id': 42}
        return receipt
    monkeypatch.setattr(obj.transport, 'write', write)
    if change == 'diagnostic_error':
        def unavailable(*args):
            raise OSError(TOKEN)
        monkeypatch.setattr(obj, '_comment_record', unavailable)
    result = obj.run(obj.prepare(scenario='comment_length')['probe_id'])
    assert result['status'] == 'unknown' and result['max_accepted_length'] == 0
    assert result['truncation_detected'] is False
    assert len(http.writes()) == 1


@pytest.mark.parametrize('scenario', ['comment_length', 'comment_retry', 'duplicate_forecast'])
def test_readback_exception_is_terminal_unknown(reuse, monkeypatch, scenario):
    obj, http, _ = reuse
    seed_prior(reuse)
    def unavailable(*args, **kwargs):
        raise OSError(TOKEN)
    monkeypatch.setattr(obj.transport, 'readback', unavailable)
    pid = obj.prepare(scenario=scenario)['probe_id']
    assert obj.run(pid)['status'] == 'unknown'
    assert obj.run(pid)['status'] == 'unknown'
    assert len(http.writes()) == 1


@pytest.mark.parametrize('state', ['unknown', 'write_unconfirmed'])
@pytest.mark.parametrize('terminal', ['found', 'confirmed_failed'])
def test_prior_ambiguity_resolved_by_later_terminal_evidence(reuse, state, terminal):
    obj, http, _ = reuse
    pid = seed_prior(reuse, state=state)
    obj.event(pid, terminal, operation='forecast', request_id='prior-request',
        error_type='ConfirmedFailure', http_status=400)
    assert obj.prepare(scenario='duplicate_forecast')['question_id'] == 7
    assert not http.writes()


def test_orphan_comment_request_rejects_reuse(reuse):
    obj, http, _ = reuse
    seed_prior(reuse)
    obj.event('no-plan', 'request_started', operation='comment', request_id='orphan')
    with pytest.raises(S.PrecheckError, match='without plan'):
        obj.prepare(scenario='duplicate_forecast')
    assert not http.writes()


def test_existing_resume_supports_injection_and_two_real_rejections(environment):
    """The approved contract uses the unchanged global three-attempt cap."""
    e = environment
    e['seed']()
    transport = e['transport']
    original = transport.write
    injected = []
    def write(path, payload, *, request_id):
        if path == S.COMMENT_PATH and not injected:
            injected.append(request_id)
            raise S.ConfirmedFailure('synthetic local fault; no network request')
        return original(path, payload, request_id=request_id)
    transport.write = write
    transport.effects['comment'] = ['rejected', 'rejected']
    e['make']().run()
    assert len(injected) == 1
    assert [w[0] for w in transport.writes] == ['forecast', 'comment', 'comment']
    assert e['delays'] == [1, 2]
    assert read_complete(e['root'] / 'submissions.jsonl')[-1]['status'] == 'comment_failed'


@pytest.fixture
def retry_probe(reuse, monkeypatch):
    obj, http, project = reuse
    clock, delays = {'now': NOW}, []
    obj.now = http.now = lambda: clock['now']
    monkeypatch.setattr(T, 'utc_now', lambda: clock['now'] + timedelta(seconds=1))
    def sleep(seconds):
        delays.append(seconds)
        clock['now'] += timedelta(seconds=seconds)
    obj.sleep = sleep
    return obj, http, project, delays


@pytest.mark.parametrize('question_type', ['binary', 'multiple_choice'])
@pytest.mark.parametrize('rejections', [0, 1, 2])
def test_retry_uses_unchanged_submitter_and_isolated_synthetic_graph(retry_probe, monkeypatch,
        question_type, rejections):
    obj, http, project, delays = retry_probe
    seed_prior((obj, http, project), question_type=question_type)
    # Keep a completed formal trial intent; allowed repeat must read but never copy/write it.
    formal = project / 'data/submissions.jsonl'
    for status in ('intent', 'comment_ok'):
        append_record(formal, dict(status=status, intent_id='formal', mode='trial',
            bot_id=V.BOT_ID, question_id=7, tournament_id=V.GROUND_ID))
    before = formal.read_bytes()
    pid = obj.prepare(question_type, 'comment_retry')['probe_id']
    plan = obj.plan(pid)
    assert (plan['max_forecast_requests'], plan['max_comment_requests']) == (1, 3)
    original = obj.transport.write
    comments = []
    def write(path, payload, *, request_id):
        if path == S.COMMENT_PATH:
            comments.append(payload)
            if len(comments) <= rejections:
                http.fail_at = http.post_count + 1
        return original(path, payload, request_id=request_id)
    monkeypatch.setattr(obj.transport, 'write', write)
    # Fault injection itself must not delegate any GET, POST or readback.
    wrapper_write = V._RetryTransport.write
    injected_checks = []
    def watched(transport, path, payload, *, request_id):
        count = len(http.calls)
        try:
            return wrapper_write(transport, path, payload, request_id=request_id)
        except S.ConfirmedFailure:
            if request_id in transport.faults:
                injected_checks.append(len(http.calls) == count)
            raise
    monkeypatch.setattr(V._RetryTransport, 'write', watched)
    result = obj.run(pid)
    assert result['status'] == ('comment_failed' if rejections == 2 else 'complete')
    assert injected_checks == [True]
    assert delays == ([1] if rejections == 0 else [1, 2])
    assert sum(r.full_url.endswith('/forecast/') for r in http.writes()) == 1
    assert len(comments) == min(rejections + 1, 2)
    assert all(p == plan['comment_payload'] for p in comments)
    root = project / 'data/v15_probe_submitter' / pid
    for name in ('questions', 'forecasts', 'aggregates', 'comments', 'submissions'):
        records = read_complete(root / f'{name}.jsonl')
        assert records and all(r['synthetic'] is True for r in records)
        assert 'UNIQUE-QUESTION-TEXT' not in (root / f'{name}.jsonl').read_text()
        assert TOKEN not in (root / f'{name}.jsonl').read_text()
    rows = read_complete(root / 'submissions.jsonl')
    assert rows[-1]['status'] == ('comment_failed' if rejections == 2 else 'comment_ok')
    assert rows[-1].get('notify_louis', False) is (rejections == 2)
    injected = [r for r in rows if r.get('injected_fault')]
    assert len(injected) == 1 and injected[0]['network_request_sent'] is False
    journal = [r for r in obj.rows() if r['probe_id'] == pid]
    assert len([r for r in journal if r.get('injected_fault')]) == 1
    assert len([r for r in journal if r['state'] == 'found']) == (1 if rejections == 2 else 2)
    assert formal.read_bytes() == before
    assert not (project / 'data/aggregates.jsonl').exists()
    for method in ('_resume', '_write', '_failed', '_reconcile_request', '_validate_frozen'):
        assert getattr(V._RetrySubmitter, method) is getattr(S.Submitter, method)
    calls = len(http.calls)
    assert obj.run(pid) == result and len(http.calls) == calls
    assert obj.prepare(question_type, 'comment_retry')['question_id'] == 7
    with pytest.raises(S.PrecheckError, match='reserved'):
        obj.sub._duplicate(dict(intent_id='normal', bot_id=V.BOT_ID, question_id=7))


@pytest.mark.parametrize('at', [1, 2, 3])
@pytest.mark.parametrize('failure', ['unknown', 'read_exception', 'timeout', 'wrong_id', 'wrong_payload'])
def test_retry_unknown_never_repeats_even_with_successful_post_receipt(retry_probe, monkeypatch, at, failure):
    obj, http, project, _ = retry_probe
    if at == 3:
        http.fail_at = 2  # First real comment is rejected; observe the second.
    original_write, original_read = obj.transport.write, obj.transport.readback
    def write(path, payload, *, request_id):
        original_write(path, payload, request_id=request_id)
        if http.post_count == at and failure == 'timeout':
            raise TimeoutError(TOKEN)
        return {'id': 999, 'bot_id': V.BOT_ID, 'payload': payload,
            ('question_id' if path == S.FORECAST_PATH else 'post_id'): 7 if path == S.FORECAST_PATH else 17}
    def read(operation, intent, *, request_id):
        if http.post_count == at:
            if failure == 'read_exception':
                raise OSError(TOKEN)
            if failure == 'wrong_id':
                found = original_read(operation, intent, request_id=request_id)
                return S.Readback('found', found.record, 'unrelated')
            if failure == 'wrong_payload':
                return S.Readback('found', {'id': 999, 'bot_id': V.BOT_ID,
                    'question_id': 7, 'post_id': 17, 'payload': {}}, request_id)
            return S.Readback(request_id=request_id)
        return original_read(operation, intent, request_id=request_id)
    monkeypatch.setattr(obj.transport, 'write', write)
    monkeypatch.setattr(obj.transport, 'readback', read)
    pid = obj.prepare(scenario='comment_retry')['probe_id']
    assert obj.run(pid)['status'] == 'unknown'
    assert len(http.writes()) == at
    calls = len(http.calls)
    assert obj.run(pid)['status'] == 'unknown' and len(http.calls) == calls
    with pytest.raises(S.PrecheckError, match='no eligible'):
        obj.prepare(scenario='comment_retry')


@pytest.mark.parametrize('status', [400, 429])
def test_retry_forecast_rejection_is_never_retried(retry_probe, status):
    obj, http, _, delays = retry_probe
    http.fail_at, http.failure_status = 1, status
    pid = obj.prepare(scenario='comment_retry')['probe_id']
    assert obj.run(pid)['status'] == 'confirmed_failed'
    assert obj.run(pid)['status'] == 'confirmed_failed'
    assert len(http.writes()) == 1 and delays == []


@pytest.mark.parametrize('change', ['orphan', 'duplicate', 'wrong_request', 'wrong_operation',
    'unproven_rejection', 'terminal_before_start'])
def test_history_requires_unique_request_and_later_matching_terminal(reuse, change):
    obj, http, _ = reuse
    pid = seed_prior(reuse, state='unknown')
    if change == 'orphan':
        obj.event(pid, 'unknown', operation='comment', request_id='orphan')
    elif change == 'duplicate':
        obj.event(pid, 'request_started', operation='forecast', request_id='prior-request')
    else:
        obj.event(pid, 'confirmed_failed' if change == 'unproven_rejection' else 'found',
            operation='comment' if change == 'wrong_operation' else 'forecast',
            request_id='different' if change == 'wrong_request' else 'prior-request')
        if change == 'terminal_before_start':
            rows = obj.rows()
            rows.insert(1, rows.pop())
            obj.log.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    with pytest.raises(S.PrecheckError):
        obj.prepare(scenario='comment_retry')
    assert not http.writes()


@pytest.mark.parametrize('boundary', ['injected', 'first_real_rejection'])
@pytest.mark.parametrize('change', ['stop', 'local', 'account', 'membership'])
def test_retry_checks_changes_during_existing_backoff(retry_probe, monkeypatch, boundary, change):
    obj, http, project, delays = retry_probe
    if boundary == 'first_real_rejection':
        http.fail_at = 2
    original_sleep = obj.sleep
    def sleep(seconds):
        original_sleep(seconds)
        if len(delays) != (1 if boundary == 'injected' else 2):
            return
        if change == 'stop':
            (project / 'config/STOP_SUBMIT').touch()
        elif change == 'local':
            (project / 'config/submit_mode.json').write_text(json.dumps(
                {'mode': 'local', 'approved_bot_id': V.BOT_ID}))
        elif change == 'account':
            http.wrong_user = True
        else:
            http.post['projects']['tournament'].append({'id': MAIN})
    obj.sleep = sleep
    pid = obj.prepare(scenario='comment_retry')['probe_id']
    assert obj.run(pid)['status'] == 'rejected_precheck'
    assert len(http.writes()) == (1 if boundary == 'injected' else 2)
    calls = len(http.calls)
    assert obj.run(pid)['status'] == 'rejected_precheck' and len(http.calls) == calls


def test_retry_honors_long_retry_after_without_recovery(retry_probe, monkeypatch):
    obj, http, _, delays = retry_probe
    http.fail_at, http.failure_status = 2, 429
    original = obj.transport.write
    def write(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except S.ConfirmedFailure as exc:
            exc.retry_at = to_iso(obj.now() + timedelta(minutes=2))
            raise
    monkeypatch.setattr(obj.transport, 'write', write)
    pid = obj.prepare(scenario='comment_retry')['probe_id']
    assert obj.run(pid)['status'] == 'confirmed_failed'
    assert delays == [1] and len(http.writes()) == 2
    calls = len(http.calls)
    assert obj.run(pid)['status'] == 'confirmed_failed' and len(http.calls) == calls


@pytest.mark.parametrize('operation', ['forecast', 'comment'])
def test_retry_lost_response_can_only_continue_after_found(retry_probe, monkeypatch, operation):
    obj, http, project, _ = retry_probe
    original = obj.transport.write
    def write(path, payload, *, request_id):
        result = original(path, payload, request_id=request_id)
        if path == (S.FORECAST_PATH if operation == 'forecast' else S.COMMENT_PATH):
            raise TimeoutError(TOKEN)
        return result
    monkeypatch.setattr(obj.transport, 'write', write)
    pid = obj.prepare(scenario='comment_retry')['probe_id']
    assert obj.run(pid)['status'] == 'complete'
    assert len(http.writes()) == 2
    own = [r for r in obj.rows() if r['probe_id'] == pid and r.get('operation') == operation]
    assert any(r['state'] == 'write_unconfirmed' for r in own)
    assert own[-1]['state'] == 'found'
    assert obj.prepare(scenario='comment_retry')['question_id'] == 7


def test_retry_rejects_changed_global_attempt_contract_before_io(reuse, monkeypatch):
    obj, http, _ = reuse
    pid = obj.prepare(scenario='comment_retry')['probe_id']
    monkeypatch.setattr(S, 'COMMENT_ATTEMPTS', 4)
    calls = len(http.calls)
    with pytest.raises(S.PrecheckError, match='attempt contract'):
        obj.run(pid)
    assert len(http.calls) == calls


@pytest.mark.parametrize('pid', ['../escape', '/absolute', '..'])
def test_retry_rejects_directory_identity_tampering(reuse, pid):
    obj, http, _ = reuse
    obj.prepare(scenario='comment_retry')
    row = obj.rows()[0]
    row['probe_id'] = row['plan']['intent_id'] = pid
    # Match the synthetic text as well; path validation must remain independent.
    comment = V._retry_inputs(row['plan'], NOW)[-1]
    row['plan']['comment_payload']['text'] = comment['text']
    row['plan']['comment_hash'] = comment['text_sha256']
    row['plan_sha256'] = hash_obj(row['plan'])
    obj.log.write_text(json.dumps(row) + '\n')
    calls = len(http.calls)
    with pytest.raises(S.PrecheckError, match='directory identity'):
        obj.run(pid)
    assert len(http.calls) == calls and not http.writes()
