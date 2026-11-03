"""Synthetic HTTP and fixture-only mode changes; never invoke operational CLIs."""
import copy
import hashlib
import json
import urllib.parse

import pytest

import forecast as F
import m5_probe_session as M
import numeric as N
import submit as S
import submit_transport as T
import v15_probe as V
from flab.ids import hash_obj, sha256_hex
from flab.ledger import read_complete
from poll_questions import snapshot_from_post
from test_numeric import api_question, snapshot
from test_submit import NOW
from test_v15_probe import ProbeHTTP, probe, rewrite_plan
from test_v15_probe_reuse import reuse, retry_probe, seed_prior


class NumericHTTP(ProbeHTTP):
    def __call__(self, req, timeout):
        forecast = req.method == 'POST' and req.full_url.endswith('/forecast/')
        try:
            return super().__call__(req, timeout)
        finally:
            if forecast and not self.fail_write:
                row = self.post['question']['my_forecasts']['latest']
                row['forecast_values'] = json.loads(req.data)[0]['continuous_cdf']


def set_question(http, kind='numeric', **kw):
    fields = api_question(snapshot(kind, **kw))
    fields['id'] = http.post['question']['id']
    http.post['question'].update(fields)


@pytest.fixture
def numeric_probe(probe):
    obj, _, project = probe
    http = NumericHTTP()
    set_question(http)
    obj.transport._open = http
    return obj, http, project


@pytest.mark.parametrize('kind,n,zero', [
    ('numeric', 200, None), ('numeric', 200, -10.), ('numeric', 200, 110.),
    ('discrete', 2, None), ('discrete', 10, None), ('discrete', 200, None),
])
@pytest.mark.parametrize('bounds', [(False, False), (True, False), (False, True), (True, True)])
def test_cdf_and_formal_submission_readback(numeric_probe, kind, n, zero, bounds):
    obj, http, project = numeric_probe
    set_question(http, kind, n=n, zero_point=zero,
                 open_lower_bound=bounds[0], open_upper_bound=bounds[1])
    pid = obj.prepare(kind)['probe_id']
    plan = obj.plan(pid)
    assert plan['snapshot']['question_type'] == kind
    assert all(k in plan['snapshot'] for k in N.SCALING_FIELDS)
    cdf = plan['forecast_payload'][0]['continuous_cdf']
    assert len(N.validate_cdf(cdf, plan['snapshot'])) == n + 1
    assert plan['comment_payload']['text'] == (
        f'Forecast Lab API component check {pid}. Synthetic CDF test; not a research forecast.')
    for _ in range(2):
        result = obj.run(pid)
        assert result['status'] == 'found'
        assert (result['forecast_requests'], result['comment_requests']) == (1, 1)
    assert len(http.writes()) == 2
    rows = read_complete(project / 'data/submissions.jsonl')
    assert sum(r['status'] == 'request_started' for r in rows) == 2
    assert all(r.get('synthetic') is True for r in rows)
    assert not any(r['status'] == 'intent' for r in rows)
    for operation in ('forecast', 'comment'):
        last = [r for r in rows if r.get('operation') == operation][-1]
        assert last['status'] == operation + '_ok' and last['reconciled'] is True
    assert not (project / 'data/forecasts.jsonl').exists()
    assert not (project / 'data/aggregates.jsonl').exists()


@pytest.mark.parametrize('field', N.SCALING_FIELDS)
@pytest.mark.parametrize('rehash', [False, True])
def test_scaling_tamper_refused_before_io(numeric_probe, field, rehash):
    obj, http, _ = numeric_probe
    pid = obj.prepare('numeric')['probe_id']
    calls = len(http.calls)
    def change(plan):
        plan['snapshot'][field] = 'tampered'
    if rehash:
        rewrite_plan(obj, change)
    else:
        rows = obj.rows()
        change(rows[0]['plan'])
        obj.log.write_text(json.dumps(rows[0]) + '\n')
    with pytest.raises(S.PrecheckError):
        obj.run(pid)
    assert len(http.calls) == calls and not http.writes()


@pytest.mark.parametrize('index,value', [(80, .4), (0, False), (-1, True)])
def test_rehashed_cdf_tamper_refused(numeric_probe, index, value):
    obj, http, _ = numeric_probe
    pid = obj.prepare('numeric')['probe_id']
    rewrite_plan(obj, lambda p: p['forecast_payload'][0]['continuous_cdf'].__setitem__(index, value))
    with pytest.raises(S.PrecheckError):
        obj.run(pid)
    assert not http.writes()


@pytest.mark.parametrize('change', ['unit', 'nominal_min', 'open_lower_bound'])
def test_valid_remote_scaling_revision_blocks_write(numeric_probe, change):
    obj, http, _ = numeric_probe
    pid = obj.prepare('numeric')['probe_id']
    if change == 'unit':
        http.post['question']['unit'] = 'changed'
    else:
        http.post['question']['scaling'][change] = True if change == 'open_lower_bound' else -1
        if change == 'open_lower_bound':
            http.post['question'][change] = True
    with pytest.raises(S.PrecheckError, match='scaling_changed'):
        obj.run(pid)
    assert not http.writes()


@pytest.mark.parametrize('kind', N.TYPES)
@pytest.mark.parametrize('scenario', V.SCENARIOS[1:])
def test_numeric_reuse_not_supported(numeric_probe, kind, scenario):
    obj, http, _ = numeric_probe
    with pytest.raises(S.PrecheckError):
        obj.prepare(kind, scenario)
    assert not http.calls


@pytest.mark.parametrize('mode,venue,override,allowed', [
    ('trial', V.GROUND_ID, N.TYPES, True),
    ('trial', V.GROUND_ID, (), False),
    ('trial', 144, N.TYPES, False),
    ('live', V.GROUND_ID, N.TYPES, False),
    ('local', V.GROUND_ID, N.TYPES, False),
])
@pytest.mark.parametrize('kind', N.TYPES)
def test_gate_scoped_to_trial_bta(numeric_probe, mode, venue, override, allowed, kind):
    obj, _, project = numeric_probe
    # Force fixture flags false even after a future production enablement.
    (project / 'config/question_types.json').write_text('{"numeric":false,"discrete":false}')
    (project / 'config/submit_mode.json').write_text(json.dumps({'mode': mode, 'approved_bot_id': V.BOT_ID}))
    intent = dict(mode=mode, bot_id=V.BOT_ID, tournament_id=venue,
                  snapshot={'question_type': kind}, allow_resubmit=False)
    sub = S.Submitter(obj.root, config_dir=project / 'config', transport=obj.transport,
                      probe_question_types=override)
    if allowed:
        sub._gate(intent, sub._discovery())
    else:
        with pytest.raises(S.PrecheckError, match='unsupported_type'):
            sub._gate(intent)


@pytest.mark.parametrize('failure', ['400', '429', 'unknown', 'lost_response'])
def test_failure_and_recovery_never_repeat_numeric_write(numeric_probe, failure):
    obj, http, _ = numeric_probe
    pid = obj.prepare('numeric')['probe_id']
    if failure.isdigit():
        http.fail_write, http.failure_status = True, int(failure)
    elif failure == 'unknown':
        http.hide_readback = True
    else:
        http.after_write_error = True
    expected = 'rejected' if failure.isdigit() else 'unknown' if failure == 'unknown' else 'found'
    assert obj.run(pid)['status'] == expected
    assert obj.run(pid)['status'] == expected
    assert len(http.writes()) == (2 if failure == 'lost_response' else 1)
    if failure == 'unknown':
        http.hide_readback = False
        assert obj.run(pid)['status'] == 'unknown'  # Reconciliation cannot start a new comment.
        assert len(http.writes()) == 1


def test_unknown_diagnostics_contain_shapes_only(numeric_probe, monkeypatch):
    obj, http, _ = numeric_probe
    pid = obj.prepare('numeric')['probe_id']
    original = obj.transport.readback
    def drift(operation, intent, request_id):
        row = http.post['question']['my_forecasts']['latest']
        row['centers'] = row.pop('forecast_values')
        row['interval_lower_bounds'] = {'secret': 'DO-NOT-LOG-VALUES'}
        return original(operation, intent, request_id=request_id)
    monkeypatch.setattr(obj.transport, 'readback', drift)
    assert obj.run(pid)['status'] == 'unknown'
    event = next(r for r in obj.rows() if r['state'] == 'readback_diagnostics')
    assert 'centers' in event['rows'][0]['keys']
    assert event['rows'][0]['fields']['centers'] == {'type': 'list', 'length': 201}
    assert 'DO-NOT-LOG-VALUES' not in json.dumps(event)
    assert 'secret' not in json.dumps(event)
    assert len(http.writes()) == 1


@pytest.mark.parametrize('failure', ['unknown', '429', 'private'])
def test_comment_readback_failure_never_repeats(numeric_probe, monkeypatch, failure):
    obj, http, _ = numeric_probe
    pid = obj.prepare('numeric')['probe_id']
    if failure == '429':
        http.fail_comment, http.failure_status = True, 429
    elif failure == 'private':
        http.comment_changes['is_private'] = False
    else:
        readback = obj.transport.readback
        monkeypatch.setattr(obj.transport, 'readback', lambda op, intent, request_id:
            S.Readback(request_id=request_id) if op == 'comment'
            else readback(op, intent, request_id=request_id))
    for _ in range(2):
        assert obj.run(pid)['status'] == ('rejected' if failure == '429' else 'unknown')
    assert len(http.writes()) == 2


@pytest.mark.parametrize('kind', N.TYPES)
def test_preference_fallback_and_prepared_reservation(numeric_probe, monkeypatch, kind):
    obj, http, _ = numeric_probe
    set_question(http, kind)
    ids = [9, *reversed(V.PREFERRED[kind])]
    posts = {}
    for index, qid in enumerate(ids):
        post = copy.deepcopy(http.post)
        post['id'], post['question']['id'] = 20 + index, qid
        posts[post['id']] = post
    monkeypatch.setattr(obj.transport.client, 'list_posts', lambda *a, **kw: list(posts.values()))
    monkeypatch.setattr(obj.transport, 'get_post', lambda pid: posts[pid])
    assert [obj.prepare(kind)['question_id'] for _ in ids] == [*V.PREFERRED[kind], 9]
    assert obj.prepare(kind)['status'] == 'no_candidate'
    assert obj._unresolved_questions() == set()
    assert not http.writes()


@pytest.mark.parametrize('change', ['scaling', 'mapping', 'closed', 'own', 'missing_history'])
def test_no_candidate_never_posts(numeric_probe, monkeypatch, change):
    obj, http, _ = numeric_probe
    if change == 'scaling':
        http.post['question']['scaling']['continuous_range'] = []
    elif change == 'mapping':
        monkeypatch.setattr(obj.transport, 'can_reconcile_forecast', lambda _: False)
    elif change == 'closed':
        http.post['question']['status'] = 'closed'
    elif change == 'missing_history':
        http.post['question'].pop('my_forecasts')
    else:
        http.post['question']['my_forecasts'] = {'history': [{'question_id': 7}], 'latest': None}
    assert obj.prepare('numeric')['status'] == 'no_candidate'
    assert obj.rows()[-1]['state'] == 'no_candidate'
    assert not http.writes()


def live_session(numeric_probe, **kwargs):
    obj, _, project = numeric_probe
    # Formatting and an opaque finite live authorization must survive byte-for-byte.
    original = b'{ "approved_bot_id":311763, "mode":"live", "live_window":{"synthetic":true} }\n'
    (project / 'config/submit_mode.json').write_bytes(original)
    kwargs.setdefault('sleep', lambda seconds: None)
    session = M.Session(project, transport_factory=lambda _: obj.transport,
                        now=lambda: NOW, wait_seconds=0, **kwargs)
    return session, original


def test_real_probe_session_restores_and_releases_all_locks(numeric_probe):
    obj, http, project = numeric_probe
    session, original = live_session(numeric_probe)
    result = session.run()
    assert result['status'] == 'complete'
    assert [r['status'] for r in result['results']] == ['found', 'no_candidate']
    assert len(http.writes()) == 2
    assert session.mode_path.read_bytes() == original
    events = read_complete(obj.root / 'mode_changes.jsonl')
    assert [e['mode'] for e in events] == ['trial', 'live']
    assert events[-1]['mode_sha256'] == hashlib.sha256(original).hexdigest()
    assert all(e['authorization'] == M.AUTHORIZATION for e in events)
    assert not session.stop_path.exists()
    for name in M.LOCK_NAMES:
        with F.Lock(obj.root / name):
            pass


@pytest.mark.parametrize('failure', ['probe', 'transport', 'audit', 'interrupt'])
def test_session_exception_restores_bytes(numeric_probe, monkeypatch, failure):
    session, original = live_session(numeric_probe)
    def fail(*args, **kwargs):
        raise KeyboardInterrupt() if failure == 'interrupt' else RuntimeError('synthetic failure')
    if failure == 'transport':
        session.transport_factory = fail
    elif failure in ('probe', 'interrupt'):
        session.probe_factory = fail
    else:
        event = session._event
        def fail_trial(event_type, **kw):
            if kw.get('mode') == 'trial':
                fail()
            event(event_type, **kw)
        monkeypatch.setattr(session, '_event', fail_trial)
    if failure == 'interrupt':
        with pytest.raises(KeyboardInterrupt):
            session.run()
    else:
        assert session.run()['status'] == 'blocked'
    assert session.mode_path.read_bytes() == original
    assert not session.stop_path.exists()


@pytest.mark.parametrize('failure', ['hash', 'write', 'audit'])
def test_restore_failure_establishes_stop_before_unlock(numeric_probe, monkeypatch, failure):
    session, original = live_session(numeric_probe)
    if failure == 'hash':
        monkeypatch.setattr(session, '_digest', lambda: 'incorrect')
    elif failure == 'write':
        write = M.atomic_bytes
        def fail_restore(path, content):
            if path == session.mode_path and content == original:
                raise OSError('synthetic restore failure')
            write(path, content)
        monkeypatch.setattr(M, 'atomic_bytes', fail_restore)
    else:
        event = session._event
        def fail_live(event_type, **kw):
            if kw.get('mode') == 'live':
                raise OSError('synthetic log failure')
            event(event_type, **kw)
        monkeypatch.setattr(session, '_event', fail_live)
    assert session.run()['status'] == 'blocked'
    assert session.stop_path.exists()
    assert session.mode_path.read_bytes() == original or failure == 'write'
    assert read_complete(session.root / 'mode_changes.jsonl')[-1]['type'] == 'mode_restore_failed'


@pytest.mark.parametrize('name', M.LOCK_NAMES)
def test_busy_lock_leaves_mode_and_journals_untouched(numeric_probe, name):
    obj, http, _ = numeric_probe
    session, original = live_session(numeric_probe)
    with F.Lock(obj.root / name):
        assert session.run()['status'] == 'busy'
    assert session.mode_path.read_bytes() == original
    assert not (obj.root / 'mode_changes.jsonl').exists()
    assert not obj.log.exists() and not http.calls


def test_lock_wait_is_bounded_and_retries(numeric_probe):
    obj, _, _ = numeric_probe
    session, original = live_session(numeric_probe)
    ticks = [0.]
    session.wait_seconds = 2.5
    session.monotonic = lambda: ticks[0]
    session.sleep = lambda delay: ticks.__setitem__(0, ticks[0] + delay)
    with F.Lock(obj.root / 'submit.lock'):
        assert session.run()['status'] == 'busy'
    assert ticks == [2.5] and session.mode_path.read_bytes() == original


@pytest.mark.parametrize('failure', ['429', 'unknown'])
def test_session_stops_before_second_type(numeric_probe, failure):
    obj, http, _ = numeric_probe
    session, original = live_session(numeric_probe)
    if failure == '429':
        http.fail_write, http.failure_status = True, 429
    else:
        http.hide_readback = True
    result = session.run()
    assert result['status'] == 'stopped'
    assert result['results'][1]['reason'] == 'not_started'
    assert len(http.writes()) == 1
    assert session.mode_path.read_bytes() == original


@pytest.mark.parametrize('kind', ['binary', 'multiple_choice'])
def test_legacy_single_plan_bytes_unchanged(probe, monkeypatch, kind):
    obj, http, _ = probe
    if kind == 'multiple_choice':
        http.post['question'].update(type=kind, options=['a', 'b'], all_options_ever=['a', 'b'])
    monkeypatch.setattr(V, 'new_id', lambda _: 'v15_fixed')
    snap = snapshot_from_post(http.post, 'bot-testing-area')
    metadata = {k: snap.get(k) for k in ('post_id', 'question_id', 'question_type', 'options',
                'scheduled_close_time', 'open_time', 'content_hash', 'venue')}
    metadata['tournament_id'] = V.GROUND_ID
    distribution = '50%' if kind == 'binary' else 'uniform'
    text = f'Forecast Lab API component check v15_fixed. Synthetic {distribution} test; not a research forecast.'
    expected = dict(intent_id='v15_fixed', mode='trial', bot_id=V.BOT_ID, tournament_id=V.GROUND_ID,
        post_id=17, question_id=7, snapshot=metadata, allow_resubmit=False,
        forecast_payload=[{'question': 7, **({'probability_yes': .5} if kind == 'binary'
            else {'probability_yes_per_category': {'a': .5, 'b': .5}})}],
        max_comment_requests=1,
        comment_payload={'on_post': 17, 'text': text, 'is_private': True, 'included_forecast': True},
        comment_hash=sha256_hex(text))
    pid = obj.prepare(kind)['probe_id']
    assert json.dumps(obj.plan(pid)) == json.dumps(expected)
    for version in ('v15_probe_v3', 'v15_probe_v4'):
        rewrite_plan(obj, lambda p: None, version=version)
        assert hash_obj(obj.plan(pid)) == hash_obj(expected)


@pytest.mark.parametrize('scenario', V.SCENARIOS[1:])
@pytest.mark.parametrize('kind', ['binary', 'multiple_choice'])
def test_v4_reuse_plan_and_injected_rejection_remain_compatible(retry_probe, scenario, kind):
    obj, http, project, _ = retry_probe
    seed_prior((obj, http, project), question_type=kind)
    pid = obj.prepare(kind, scenario)['probe_id']
    original = json.dumps(obj.plan(pid))
    rows = obj.rows()
    for row in rows:
        row['version'] = 'v15_probe_v4'
    obj.log.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    assert json.dumps(obj.plan(pid)) == original
    result = obj.run(pid)
    assert result['status'] == 'complete'
    count = len(http.writes())
    # Include the v4 local-injection rejection in reservation recovery checks.
    rows = obj.rows()
    for row in rows:
        row['version'] = 'v15_probe_v4'
    obj.log.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    assert obj._unresolved_questions() == set()
    assert obj.run(pid) == result and len(http.writes()) == count


@pytest.mark.parametrize('change', ['stop', 'mode', 'deadline'])
def test_formal_network_guard_runs_after_reservation(numeric_probe, monkeypatch, change):
    from datetime import timedelta
    obj, http, project = numeric_probe
    pid = obj.prepare('numeric')['probe_id']
    event = obj.event
    def changed(probe_id, state, **kw):
        event(probe_id, state, **kw)
        if state == 'request_started':
            if change == 'stop':
                (project / 'config/STOP_SUBMIT').touch()
            elif change == 'mode':
                (project / 'config/submit_mode.json').write_text('{"mode":"local"}')
            else:
                # The guard's deadline uses the Submitter clock, captured at construction.
                clock[0] = NOW + timedelta(days=31)
    clock = [NOW]
    obj.now = lambda: clock[0]
    monkeypatch.setattr(obj, 'event', changed)
    assert obj.run(pid)['status'] == 'rejected'
    assert not http.writes()


@pytest.mark.parametrize('invalid', ['local', 'wrong_bot', 'stop'])
def test_session_initial_preconditions_never_change_mode(numeric_probe, invalid):
    _, http, _ = numeric_probe
    session, original = live_session(numeric_probe)
    if invalid == 'local':
        original = b'{"mode":"local"}'
        session.mode_path.write_bytes(original)
    elif invalid == 'wrong_bot':
        original = b'{"mode":"live","approved_bot_id":42}'
        session.mode_path.write_bytes(original)
    else:
        session.stop_path.write_bytes(b'existing stop')
    assert session.run()['status'] == 'blocked'
    assert session.mode_path.read_bytes() == original
    assert not (session.root / 'mode_changes.jsonl').exists()
    assert not http.calls


def test_session_exception_after_reservation_keeps_counts(numeric_probe, monkeypatch):
    session, original = live_session(numeric_probe)
    event = V.Probe.event
    def crash(probe, pid, state, **kw):
        event(probe, pid, state, **kw)
        if state == 'request_started':
            raise RuntimeError('synthetic crash after reservation')
    monkeypatch.setattr(V.Probe, 'event', crash)
    result = session.run()
    assert result['status'] == 'blocked'
    assert result['results'][0]['status'] == 'unknown'
    assert result['results'][0]['forecast_requests'] == 1
    assert result['results'][0]['comment_requests'] == 0
    assert session.mode_path.read_bytes() == original


def test_session_holds_all_locks_through_restore_audit(numeric_probe, monkeypatch):
    session, _ = live_session(numeric_probe)
    event = session._event
    checkpoints = []
    def check(event_type, **kw):
        for name in M.LOCK_NAMES:
            with pytest.raises(F.PreflightError):
                with F.Lock(session.root / name):
                    pass
        checkpoints.append(kw.get('mode'))
        event(event_type, **kw)
    monkeypatch.setattr(session, '_event', check)
    assert session.run()['status'] == 'complete'
    assert checkpoints == ['trial', 'live']


def test_two_type_session_has_exactly_four_bounded_posts(numeric_probe):
    from test_m3_w9 import BOT
    obj, _, _ = numeric_probe
    class DualHTTP(NumericHTTP):
        def __init__(self):
            super().__init__()
            set_question(self)
            first = self.post
            self.post = copy.deepcopy(first)
            self.post['id'], self.post['question']['id'] = 18, 8
            set_question(self, 'discrete', n=2)
            self.by_id = {17: first, 18: self.post}
            self.posts[BOT] = list(self.by_id.values())

        def __call__(self, req, timeout):
            path = urllib.parse.urlsplit(req.full_url).path
            if req.method == 'POST':
                payload = json.loads(req.data)
                pid = (17 if payload[0]['question'] == 7 else 18) if isinstance(payload, list) else payload['on_post']
                self.post = self.by_id[pid]
            elif path in ('/api/posts/17/', '/api/posts/18/'):
                self.post = self.by_id[int(path.split('/')[-2])]
            response = super().__call__(req, timeout)
            if req.method == 'POST':
                if path.endswith('/forecast/'):
                    self.post['question']['my_forecasts']['latest']['question_id'] = self.post['question']['id']
                else:
                    self.comments[-1].update(id=100 + len(self.comments), on_post=self.post['id'])
            return response
    http = DualHTTP()
    obj.transport._open = http
    session, original = live_session((obj, http, obj.project))
    result = session.run()
    assert [r['status'] for r in result['results']] == ['found', 'found']
    assert [(r['forecast_requests'], r['comment_requests']) for r in result['results']] == [(1, 1), (1, 1)]
    assert len(http.writes()) == 4
    assert session.mode_path.read_bytes() == original


def test_sigterm_handler_unwinds_and_is_restored(monkeypatch, capsys):
    import signal
    previous = signal.getsignal(signal.SIGTERM)
    class Interrupted:
        def __init__(self, *args):
            pass
        def run(self):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    monkeypatch.setattr(M, 'Session', Interrupted)
    with pytest.raises(SystemExit) as exc:
        M.main([])
    assert exc.value.code == 128 + signal.SIGTERM
    assert signal.getsignal(signal.SIGTERM) is previous
    assert not capsys.readouterr().out


def test_session_spaces_the_second_probe_after_a_found_first(numeric_probe):
    slept = []
    session, original = live_session(numeric_probe, sleep=slept.append)
    result = session.run()
    assert [r['status'] for r in result['results']] == ['found', 'no_candidate']
    assert M.PROBE_GAP_SECONDS in slept and session.mode_path.read_bytes() == original
