"""M3-W9: synthetic HTTP only. No credentials, real APIs or model CLIs."""
import copy
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

import pytest

import continuous as Q
import forecast as F
import m2_prepare as M
import poll_questions as PQ
import submit as S
import submit_transport as T
from flab.ids import sha256_hex
from flab.ledger import read_complete
from flab.timeutil import to_iso
from project_policy import trial_membership_error
from test_poll_questions import _post
from test_submit import environment, rows, set_mode, update_cfg, NOW

TOKEN = 'synthetic-private-token-73491'
BOT, MAIN, MINI, SITE = 32977, 33121, 33122, 144


@pytest.fixture(autouse=True)
def no_token_in_any_test_ledger_or_console(tmp_path, capsys):
    yield
    for path in tmp_path.rglob('*.jsonl'):
        assert TOKEN not in path.read_text()
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err


class Response(io.BytesIO):
    def __init__(self, value, *, status=200):
        super().__init__(value if isinstance(value, bytes) else json.dumps(value).encode())
        self.status = status


class HTTP:
    def __init__(self, post=None):
        self.post = copy.deepcopy(post or _post(17, 7, 'binary', 'Synthetic', my_forecasts=[]))
        self.calls, self.comments, self.effects = [], [], []
        self.rounds = [{'id': MINI, 'slug': 'minibench', 'is_ongoing': True},
                       {'id': MINI + 1, 'slug': 'previous', 'is_ongoing': True},
                       {'id': MINI + 2, 'slug': 'next', 'is_ongoing': False,
                        'start_date': to_iso(NOW + timedelta(days=1000))}]
        self.alias = MINI
        self.posts = {}

    def __call__(self, req, timeout):
        self.calls.append(req)
        assert req.get_header('Authorization') == 'Token ' + TOKEN
        url = urllib.parse.urlsplit(req.full_url)
        assert url.scheme == 'https' and url.netloc == 'www.metaculus.com'
        if req.method == 'POST':
            if self.effects:
                effect = self.effects.pop(0)
                if isinstance(effect, BaseException):
                    raise effect
                return Response(effect)
            return Response([{'id': 99, 'debug': TOKEN}] if req.full_url.endswith('forecast/') else {'id': 100, 'debug': TOKEN})
        data = {
            '/api/users/me/': {'id': 42, 'is_bot': True, 'secret': TOKEN},
            '/api/posts/17/': self.post,
            '/api/comments/': {'results': self.comments, 'next': None},
            '/api/projects/tournaments/minibench/': {'id': self.alias, 'slug': 'minibench'},
            '/api/projects/tournaments/': [{'id': 33108, 'name': 'Metaculus Cup Synthetic',
                'is_ongoing': True, 'start_date': to_iso(NOW)}],
            '/api/projects/site_main/': {'id': SITE, 'type': 'site_main'},
            '/api/projects/minibenches/': self.rounds,
        }
        if url.path == '/api/posts/':
            pid = int(urllib.parse.parse_qs(url.query)['tournaments'][0])
            return Response({'results': self.posts.get(pid, []), 'next': None})
        return Response(data[url.path])

    def writes(self):
        return [r for r in self.calls if r.method == 'POST']


def transport(http):
    obj = T.APITransport(TOKEN, opener=http)
    obj.client._sleep = lambda delay: None
    return obj


def memberships(kind):
    site = {'site_main': [{'id': SITE}], 'default_project': {'id': SITE}}
    if kind in ('cup', 'market', 'bot_and_cup'):
        site['tournament'] = [{'id': 33108 if kind == 'cup' else 555}]
        if kind == 'bot_and_cup':
            site['tournament'].append({'id': BOT})
    elif kind == 'bot':
        return {'tournament': [{'id': BOT}], 'default_project': {'id': BOT}}
    elif kind == 'missing':
        return None
    elif kind == 'unknown':
        site['mystery'] = [{'id': 777}]
    elif kind == 'unknown_type':
        site['default_project']['type'] = 'mystery'
    elif kind == 'untyped_default':
        return {'default_project': {'id': SITE}}
    elif kind == 'malformed':
        return ['invalid']
    return site


@pytest.mark.parametrize('kind', ['cup', 'market', 'site', 'bot', 'missing', 'unknown', 'unknown_type',
                                  'untyped_default', 'malformed', 'bot_and_cup'])
def test_trial_memberships_at_fresh_submission(environment, kind):
    e = environment
    target = BOT if kind == 'bot' else SITE
    e['cfg']['tournaments']['main_site_questions'] = {'id': SITE, 'submit_whitelisted': False}
    update_cfg(e)
    # Stored snapshot is safe; the current API response must override that old evidence.
    e['post']['projects'] = memberships('bot' if kind == 'bot' else 'site')
    snap = {**PQ.snapshot_from_post(e['post'], 'synthetic'), 'snapshot_id': 's1', 'tournament_id': target}
    e['seed'](snap)
    e['post']['projects'] = memberships(kind)
    e['make']().run()
    allowed = kind in ('site', 'bot')
    assert bool(e['transport'].writes) is allowed
    assert rows(e)[-1]['status'] == ('comment_ok' if allowed else 'rejected_precheck')
    if not allowed:
        assert not any(r['status'] == 'abandoned' for r in rows(e))


@pytest.mark.parametrize('kind', ['cup', 'market', 'site', 'bot', 'missing', 'unknown', 'unknown_type'])
def test_trial_memberships_in_poller(tmp_path, monkeypatch, kind):
    http = HTTP()
    post = copy.deepcopy(http.post)
    post['projects'] = memberships(kind)
    http.posts[SITE] = [post]
    cfg = {'tournaments': {'main': {'id': MAIN}, 'minibench': {'id': None},
                          'bot_testing_area': {'id': BOT}, 'main_site_questions': {'id': SITE}},
           'trial_ground_types': ['bot_testing_area', 'main_site_questions']}
    # Bot testing example must also belong to the selected feed.
    venue = 'bot_testing_area' if kind == 'bot' else 'main_site_questions'
    if kind == 'bot':
        http.posts[BOT] = [post]
    monkeypatch.setattr(Q, 'load_tournaments', lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(Q, 'load_submit_mode', lambda: {'mode': 'trial'})
    poller = Q.Poller(transport(http).client, tmp_path, {'venues': [venue]}, now=lambda: NOW)
    if kind in ('missing', 'unknown', 'unknown_type'):
        with pytest.raises(F.PreflightError, match='membership'):
            poller()
    else:
        result = poller()
        assert bool(result) is (kind in ('site', 'bot'))
    stored = read_complete(tmp_path / 'questions.jsonl')
    assert bool(stored) is (kind in ('site', 'bot'))
    if kind not in ('site', 'bot'):
        assert 'Synthetic' not in json.dumps(stored)


@pytest.mark.parametrize('kind', ['cup', 'market', 'site', 'missing', 'unknown'])
def test_prepare_new_campaign_excludes_competitions(tmp_path, monkeypatch, kind):
    cfg = {'tournaments': {'main': {'id': MAIN}, 'minibench': {'id': MINI},
                          'bot_testing_area': {'id': BOT}, 'main_site_questions': {'id': SITE}},
           'excluded_project_ids': [MAIN, MINI]}
    post = _post(17, 7, 'binary', 'Synthetic', open_time=to_iso(NOW - timedelta(hours=1)),
                 scheduled_close_time=to_iso(NOW + timedelta(hours=3)))
    post['projects'] = memberships(kind)
    class Client:
        def get(self, path, params):
            assert path == 'posts/' and params['main_feed'] == 'true'
            return {'results': [post]}
    root = tmp_path / 'data'
    root.mkdir()
    monkeypatch.setattr(M, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(M, 'get_secret', lambda *a: TOKEN)
    monkeypatch.setattr(M, 'MetaculusClient', lambda token: Client())
    monkeypatch.setattr(M, 'current_tournaments', lambda *a, **k: cfg)
    monkeypatch.setattr(M, 'poll', lambda client, i, **k: {'tournament_id': i})
    monkeypatch.setattr(M, 'utc_now', lambda: NOW)
    monkeypatch.setattr(M, 'record_snapshots', lambda snaps: PQ.record_snapshots(snaps, log=root / 'questions.jsonl'))
    monkeypatch.setattr(M, 'latest_snapshots', lambda: PQ.latest_snapshots(root / 'questions.jsonl'))
    with pytest.raises(SystemExit, match='Fewer than 20'):
        M.prepare(64)
    assert bool(read_complete(root / 'questions.jsonl')) is (kind == 'site')


def live_environment(e, http, target=MINI):
    set_mode(e, mode='live')
    e['cfg']['tournaments']['minibench'].update(id=None, submit_whitelisted=True)
    update_cfg(e)
    http.post['projects'] = {'tournament': [{'id': target}]}
    snap = {**PQ.snapshot_from_post(http.post, 'synthetic'), 'snapshot_id': 's1', 'tournament_id': target}
    e['seed'](snap)
    return S.Submitter(e['root'], config_dir=e['config'], transport=transport(http), now=lambda: NOW)


@pytest.mark.parametrize('target', [MAIN, MINI, MINI + 1, MINI + 2, 999])
def test_live_static_main_and_dynamic_ongoing_rounds(environment, target):
    http = HTTP(environment['post'])
    obj = live_environment(environment, http, target)
    obj.run()
    allowed = target in (MAIN, MINI, MINI + 1)
    assert len(http.writes()) == (2 if allowed else 0)
    if allowed:
        assert all(r['tournament_id'] == target for r in rows(environment))
    if target == MAIN:
        assert not any('/projects/' in req.full_url for req in http.calls)


@pytest.mark.parametrize('fault', ['unapproved', 'failure', 'mismatch', 'ended', 'future_ongoing', 'duplicate_alias'])
def test_live_minibench_fails_closed(environment, fault):
    http = HTTP(environment['post'])
    obj = live_environment(environment, http)
    if fault == 'unapproved':
        environment['cfg']['tournaments']['minibench']['submit_whitelisted'] = False
        update_cfg(environment)
    elif fault == 'failure':
        http.rounds = {'error': TOKEN}
    elif fault == 'mismatch':
        http.alias = 123
    elif fault == 'ended':
        http.rounds[0].update(is_ongoing=False, start_date=to_iso(NOW - timedelta(days=20)))
    elif fault == 'future_ongoing':
        http.rounds[0]['start_date'] = to_iso(NOW + timedelta(days=1000))
    else:
        http.rounds[1]['slug'] = 'minibench'
    obj.run()
    assert not http.writes() and rows(environment)[-1]['status'] == 'rejected_precheck'


def test_live_poller_two_rounds_and_rollover(tmp_path, monkeypatch):
    http = HTTP()
    cfg = {'tournaments': {'main': {'id': MAIN}, 'minibench': {'id': None, 'submit_whitelisted': True},
                          'bot_testing_area': {'id': BOT}}, 'trial_ground_types': ['bot_testing_area']}
    for pid in (MINI, MINI + 1, MINI + 2):
        post = _post(pid, pid, 'binary', f'Synthetic {pid}')
        post['projects'] = {'tournament': [{'id': pid}]}
        http.posts[pid] = [post]
    monkeypatch.setattr(Q, 'load_tournaments', lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(Q, 'load_submit_mode', lambda: {'mode': 'live'})
    poller = Q.Poller(transport(http).client, tmp_path, {'venues': ['minibench']}, now=lambda: NOW)
    assert set(poller()) == {MINI, MINI + 1}
    http.rounds[0].update(slug='old', is_ongoing=False, start_date=to_iso(NOW - timedelta(days=20)))
    http.rounds[2].update(slug='minibench', is_ongoing=True, start_date=to_iso(NOW))
    http.alias = MINI + 2
    assert set(poller()) == {MINI + 1, MINI + 2}
    assert cfg['tournaments']['minibench']['id'] is None


def intent():
    return {'bot_id': 42, 'post_id': 17, 'question_id': 7, 'request_started_at': to_iso(NOW),
            'forecast_payload': [{'question': 7, 'probability_yes': .3}],
            'comment_payload': {'on_post': 17, 'text': 'Synthetic comment', 'is_private': True, 'included_forecast': True},
            'comment_hash': sha256_hex('Synthetic comment')}


@pytest.mark.parametrize('operation', ['forecast', 'comment'])
@pytest.mark.parametrize('case', ['found', 'empty', 'older', 'equal_time', 'different', 'missing_time', 'missing_id',
                                 'missing_fields', 'wrong_target', 'wrong_account'])
def test_readback_requires_fresh_matching_record(operation, case):
    http = HTTP()
    obj, data = transport(http), intent()
    timestamp = to_iso(NOW + timedelta(seconds=1))
    if case == 'older':
        timestamp = to_iso(NOW - timedelta(seconds=1))
    if case == 'equal_time':
        timestamp = to_iso(NOW)
    if operation == 'forecast':
        row = {'id': 12, 'start_time': timestamp, 'probability_yes': .3}
        http.post['question']['my_forecasts'] = [] if case == 'empty' else [row]
        if case == 'different':
            row['probability_yes'] = .4
        if case == 'missing_time':
            row.pop('start_time')
        if case == 'missing_fields':
            http.post['question'].pop('my_forecasts')
        if case == 'wrong_target':
            http.post['question']['id'] = 8
    else:
        row = {'id': 13, 'created_at': timestamp, 'author': {'id': 42}, 'on_post': 17, 'text': 'Synthetic comment'}
        http.comments = [] if case == 'empty' else [row]
        if case == 'different':
            row['text'] += ' different'
        if case == 'missing_time':
            row.pop('created_at')
        if case == 'missing_fields':
            row.pop('author')
        if case == 'wrong_target':
            row['on_post'] = 18
    if case == 'missing_id':
        row.pop('id')
    if case == 'wrong_account':
        data['bot_id'] = 43
    result = obj.readback(operation, data, request_id='r1')
    assert result.outcome == ('found' if case == 'found' else 'unknown')
    assert result.request_id == 'r1'
    assert TOKEN not in repr(result)


def test_multiple_choice_readback():
    http = HTTP()
    data = intent()
    data['forecast_payload'] = [{'question': 7, 'probability_yes_per_category': {'A': .123456, 'B': .876544}}]
    http.post['question']['my_forecasts'] = [{'id': 23, 'start_time': to_iso(NOW + timedelta(seconds=1)),
                                            'probability_yes_per_category': {'A': .123456, 'B': .876544}}]
    assert transport(http).readback('forecast', data, request_id='r').outcome == 'found'
    http.post['question']['my_forecasts'][0]['probability_yes_per_category']['A'] = .123457
    assert transport(http).readback('forecast', data, request_id='r').outcome == 'unknown'


@pytest.mark.parametrize('status', [301, 302, 307, 308, 400, 401, 403, 429, 500, 503])
def test_http_errors_never_retry_post_and_are_sanitized(status):
    http = HTTP()
    obj = transport(http)
    obj.account()
    http.effects = [urllib.error.HTTPError('https://www.metaculus.com/api/questions/forecast/', status,
        TOKEN, {'Location': 'https://evil.example/' + TOKEN}, io.BytesIO(TOKEN.encode()))]
    expected = S.ConfirmedFailure if 400 <= status < 500 else T.TransportError
    with pytest.raises(expected) as caught:
        obj.write(S.FORECAST_PATH, intent()['forecast_payload'], request_id='r')
    assert len(http.writes()) == 1
    assert TOKEN not in str(caught.value)
    assert caught.value.status == status


@pytest.mark.parametrize('effect', [TimeoutError(TOKEN), ConnectionResetError(TOKEN), b'not JSON ' + TOKEN.encode()])
def test_unknown_write_and_restart_readback_use_original_request_time(environment, effect, capsys):
    e = environment
    e['seed']()
    http = HTTP(e['post'])
    http.effects = [effect]
    obj = S.Submitter(e['root'], config_dir=e['config'], transport=transport(http), now=lambda: NOW)
    obj.run()
    assert rows(e)[-1]['status'] == 'unknown' and len(http.writes()) == 1
    # Matching older requests must not resolve the outstanding request.
    http.post['question']['my_forecasts'] = [{'id': 90, 'start_time': to_iso(NOW - timedelta(seconds=1)),
                                            'probability_yes': .3}]
    obj.run(reconcile_only=True)
    assert rows(e)[-1]['status'] == 'unknown'
    http.post['question']['my_forecasts'][0]['start_time'] = to_iso(NOW + timedelta(seconds=1))
    restarted = S.Submitter(e['root'], config_dir=e['config'], transport=transport(http), now=lambda: NOW + timedelta(minutes=2))
    restarted.run()
    assert rows(e)[-1]['status'] == 'comment_ok'
    assert len(http.writes()) == 2  # one forecast, one comment; no forecast retry
    for path in e['root'].rglob('*.jsonl'):
        assert TOKEN not in path.read_text()
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err


@pytest.mark.parametrize('url', ['http://www.metaculus.com/api/questions/forecast/',
    'https://evil.example/api/questions/forecast/', 'https://www.metaculus.com.evil.example/api/comments/create/',
    'https://user@www.metaculus.com/api/comments/create/', 'https://www.metaculus.com/not-api/'])
def test_non_api_urls_never_reach_opener(url):
    http = HTTP()
    obj = transport(http)
    with pytest.raises(T.TransportError):
        obj._safe_open(urllib.request.Request(url), 1)
    with pytest.raises(T.TransportError):
        obj.write(url, {}, request_id='r')
    assert not http.calls


def test_redirect_handler_never_creates_followup_request():
    req = urllib.request.Request('https://www.metaculus.com/api/comments/create/', method='POST')
    with pytest.raises(T.TransportError):
        T.NoRedirect().redirect_request(req, None, 302, TOKEN, {}, 'https://evil.example/' + TOKEN)


def test_local_cli_never_constructs_write_transport_or_reads_secret(environment, monkeypatch):
    import flab.envfile
    e = environment
    e['seed']()
    set_mode(e, mode='local')
    monkeypatch.setattr(S, 'PROJECT_ROOT', e['root'].parent)
    def forbidden(*args, **kwargs):
        pytest.fail('local mode accessed a credential or constructed the network transport')
    monkeypatch.setattr(T, 'APITransport', forbidden)
    monkeypatch.setattr(flab.envfile, 'get_secret', forbidden)
    assert isinstance(S.cli_transport(e['root'].parent), S.OfflineTransport)
    assert S.main(['run']) == 0
    assert rows(e)[-1]['status'] == 'dry_run'


@pytest.mark.parametrize('status', [400, 401, 403, 429, 500, 503])
def test_http_error_ledger_and_console_never_contain_token(environment, status, capsys):
    e = environment
    e['seed']()
    http = HTTP(e['post'])
    http.effects = [urllib.error.HTTPError('https://www.metaculus.com/api/questions/forecast/', status,
                                          TOKEN, {}, io.BytesIO(TOKEN.encode()))]
    obj = S.Submitter(e['root'], config_dir=e['config'], transport=transport(http), now=lambda: NOW)
    obj.run()
    assert len(http.writes()) == 1
    assert rows(e)[-1]['status'] == ('confirmed_failed' if status < 500 else 'unknown')
    if status < 500:
        assert f'HTTP {status}:' in rows(e)[-1]['evidence']
    for path in e['root'].rglob('*.jsonl'):
        assert TOKEN not in path.read_text()
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err


def test_comment_timeout_restart_only_reconciles_comment(environment):
    e = environment
    _, comment = e['seed']()
    http = HTTP(e['post'])
    http.effects = [[{'id': 99}], TimeoutError(TOKEN)]
    S.Submitter(e['root'], config_dir=e['config'], transport=transport(http), now=lambda: NOW).run()
    assert rows(e)[-1]['status'] == 'unknown' and rows(e)[-1]['operation'] == 'comment'
    http.comments = [{'id': 100, 'author': 42, 'on_post': 17, 'text': comment['text'],
                      'created_at': to_iso(NOW + timedelta(seconds=1))}]
    S.Submitter(e['root'], config_dir=e['config'], transport=transport(http),
                now=lambda: NOW + timedelta(minutes=5)).run()
    assert rows(e)[-1]['status'] == 'comment_ok' and rows(e)[-1]['reconciled']
    assert len(http.writes()) == 2


@pytest.mark.parametrize('fault', ['wrong_author', 'read_timeout', 'foreign_next', 'missing_page', 'same_host_next'])
def test_comment_readback_pagination_and_uncertainty(fault):
    http = HTTP()
    record = {'id': 9, 'author': 41 if fault == 'wrong_author' else 42, 'on_post': 17,
              'text': 'Synthetic comment', 'created_at': to_iso(NOW + timedelta(seconds=1))}
    def opener(req, timeout):
        if '/comments/' not in req.full_url:
            return http(req, timeout)
        if fault == 'read_timeout':
            raise TimeoutError(TOKEN)
        if fault == 'missing_page':
            return Response({'unexpected': TOKEN})
        if fault == 'wrong_author' or req.full_url.endswith('offset=100'):
            return Response({'results': [record], 'next': None})
        return Response({'results': [], 'next': ('https://evil.example/' + TOKEN if fault == 'foreign_next'
                         else 'https://www.metaculus.com/api/comments/?offset=100')})
    obj = T.APITransport(TOKEN, opener=opener)
    result = obj.readback('comment', intent(), request_id='r')
    assert result.outcome == ('found' if fault == 'same_host_next' else 'unknown')
    assert TOKEN not in repr(result)


@pytest.mark.parametrize('identity', [{'id': 42}, {'id': 42, 'is_bot': False}, {'id': True, 'is_bot': True}])
def test_transport_requires_explicit_bot_identity(identity):
    obj = T.APITransport(TOKEN, opener=lambda req, timeout: Response(identity))
    with pytest.raises(T.TransportError, match='identity'):
        obj.account()
    with pytest.raises(T.TransportError, match='identity'):
        obj.write(S.FORECAST_PATH, intent()['forecast_payload'], request_id='r')


@pytest.mark.parametrize('when', ['before_forecast', 'before_comment'])
def test_round_ending_blocks_each_write(environment, when):
    e = environment
    http = HTTP(e['post'])
    obj = live_environment(e, http)
    original = obj.transport.current_tournaments
    checks = []
    def discover(cfg):
        checks.append(True)
        if (when == 'before_forecast' and len(checks) == 3) or (when == 'before_comment' and http.writes()):
            http.rounds[0].update(is_ongoing=False, start_date=to_iso(NOW - timedelta(days=20)))
        return original(cfg)
    obj.transport.current_tournaments = discover
    obj.run()
    assert len(http.writes()) == (0 if when == 'before_forecast' else 1)
    assert rows(e)[-1]['status'] == 'rejected_precheck'


def test_trial_project_change_before_comment_is_rejected(environment):
    e = environment
    e['seed']()
    e['transport'].hook = lambda operation: e['post']['projects'].update(tournament=[{'id': BOT}, {'id': 555}])
    e['make']().run()
    assert len(e['transport'].writes) == 1
    assert rows(e)[-1]['status'] == 'rejected_precheck'


@pytest.mark.parametrize('changed', [False, True])
def test_new_m2_campaign_rechecks_membership_without_changing_frozen_campaigns(tmp_path, monkeypatch, changed):
    root = tmp_path / 'data'
    root.mkdir()
    posts = [_post(100 + i, 100 + i, 'binary', f'Synthetic {i}',
                   open_time=to_iso(NOW - timedelta(minutes=10)),
                   scheduled_close_time=to_iso(NOW + timedelta(hours=3))) for i in range(20)]
    for post in posts:
        post['projects'] = memberships('site')
    cfg = {'tournaments': {'main': {'id': MAIN}, 'minibench': {'id': MINI},
                          'bot_testing_area': {'id': BOT}, 'main_site_questions': {'id': SITE}},
           'excluded_project_ids': [MAIN, MINI]}
    class Client:
        def get(self, path, params):
            return {'results': copy.deepcopy(posts)}
        def get_post(self, post_id):
            result = copy.deepcopy(posts[post_id - 100])
            if changed:
                result['projects'] = memberships('cup')
            return result
    monkeypatch.setattr(M, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(M, 'get_secret', lambda *a: TOKEN)
    monkeypatch.setattr(M, 'MetaculusClient', lambda token: Client())
    monkeypatch.setattr(M, 'current_tournaments', lambda *a, **k: cfg)
    monkeypatch.setattr(M, 'poll', lambda client, i, **k: {'tournament_id': i})
    monkeypatch.setattr(M, 'utc_now', lambda: NOW)
    monkeypatch.setattr(M, 'record_snapshots', lambda snaps: PQ.record_snapshots(snaps, log=root / 'questions.jsonl'))
    monkeypatch.setattr(M, 'latest_snapshots', lambda: PQ.latest_snapshots(root / 'questions.jsonl'))
    monkeypatch.setattr(M, 'SafeSnapshots', lambda *a, **kw: lambda: M.latest_snapshots())
    if changed:
        with pytest.raises(SystemExit, match='membership changed'):
            M.prepare(64)
        assert not (root / 'campaigns').exists()
    else:
        path = M.prepare(64)
        campaign = json.loads(path.read_text())
        assert campaign['preparation_version'] == 'm2_prepare_v2'
        assert campaign['version'] == 'm2_campaign_v1' and len(campaign['questions']) == 20
        assert all(q['ground'] == 'main_site_questions' for q in campaign['questions'])
