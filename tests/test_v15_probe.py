"""Component driver exercises the real precheck/readback with synthetic HTTP only."""
import copy
import json
import shutil
import urllib.error
import urllib.parse
from datetime import timedelta
from pathlib import Path

import pytest

import forecast as F
import submit as S
import submit_transport as T
import v15_probe as V
from flab.ids import hash_obj
from flab.ledger import read_complete
from flab.timeutil import to_iso
from test_m3_w9 import HTTP, Response, TOKEN, BOT, MAIN, MINI
from test_submit import NOW


class ProbeHTTP(HTTP):
    def __init__(self):
        super().__init__()
        self.post['question'].update(open_time=to_iso(NOW-timedelta(days=1)),
            scheduled_close_time=to_iso(NOW+timedelta(days=30)), my_forecasts={'history':[], 'latest':None})
        self.post['title'] = self.post['question']['title'] = 'UNIQUE-QUESTION-TEXT'
        self.posts[BOT] = [self.post]
        self.after_write_error = False
        self.hide_readback = False
        self.wrong_user = False
        self.fail_write = False
        self.fail_comment = False
        self.failure_status = 400
        self.mc_values = [.333333, None, .333333, .333334]
        self.comment_values = [.333333, None, .333333, .333334]
        self.comment_changes = {}

    def __call__(self, req, timeout):
        path = urllib.parse.urlsplit(req.full_url).path
        if path == '/api/comments/100/':
            self.calls.append(req)
            return Response(self.comments[0])
        if path == '/api/users/me/':
            self.calls.append(req)
            return Response({'id':42 if self.wrong_user else V.BOT_ID, 'is_bot':True})
        if req.method == 'POST':
            self.calls.append(req)
            if self.fail_write or (self.fail_comment and path.endswith('/comments/create/')):
                raise urllib.error.HTTPError(req.full_url, self.failure_status, TOKEN, {}, None)
            data = json.loads(req.data)
            if path.endswith('/forecast/'):
                row = {'question_id':7,'author_id':V.BOT_ID,'start_time':(NOW+timedelta(seconds=.1)).timestamp(),
                    'forecast_values':(self.mc_values if self.post['question']['type'] == 'multiple_choice' else [.5,.5])}
                self.post['question']['my_forecasts'] = {'history':[row],'latest':row}
            else:
                self.comments.append({'id':100,'author':{'id':V.BOT_ID},'on_post':17,
                    'created_at':to_iso(NOW+timedelta(seconds=.2)), 'text':data['text'], 'is_private':data['is_private'], 'included_forecast':{'probability_yes':.5, 'question_type':'binary'}})
                if self.post['question']['type'] == 'multiple_choice':
                    self.comments[-1]['included_forecast'] = {
                        'question_type': 'multiple_choice',
                        'options': self.post['question']['options'],
                        'probability_yes_per_category': self.comment_values}
                self.comments[-1].update(self.comment_changes)
            if self.after_write_error:
                raise TimeoutError(TOKEN)
            return Response({})
        if req.method == 'GET' and path == '/api/posts/18/':
            self.calls.append(req)
            return Response(next(p for p in self.posts[BOT] if p['id']==18))
        if path == '/api/posts/17/' and self.hide_readback:
            data = copy.deepcopy(self.post)
            data['question']['my_forecasts'] = {'history':[], 'latest':None}
            self.calls.append(req)
            return Response(data)
        return super().__call__(req, timeout)


@pytest.fixture
def probe(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    shutil.copytree(Path(__file__).parents[1]/'config', project/'config')
    (project/'config/submit_mode.json').write_text(json.dumps({'mode':'trial','approved_bot_id':V.BOT_ID}))
    (project/'data').mkdir()
    http = ProbeHTTP()
    t = T.APITransport(TOKEN, opener=http, poll_log=project/'data/poll_log.jsonl')
    t.client._sleep = lambda _: None
    monkeypatch.setattr(T, 'utc_now', lambda: NOW+timedelta(seconds=1))
    obj = V.Probe(project, t, now=lambda: NOW)
    yield obj, http, project
    assert all(TOKEN not in p.read_text() for p in (project/'data').glob('*.jsonl'))


@pytest.fixture
def mc_probe(probe):
    obj, http, project = probe
    http.post['question'].update(type='multiple_choice', options=['a', 'b', 'c'], all_options_ever=['a', 'inactive', 'b', 'c'])
    return probe


def rewrite_plan(obj, change, *, version=None):
    """Synthetic ledger mutation for legacy compatibility and tamper tests only."""
    rows = obj.rows()
    prepared = next(r for r in rows if r['state'] == 'prepared')
    change(prepared['plan'])
    prepared['plan_sha256'] = hash_obj(prepared['plan'])
    if version:
        prepared['version'] = version
    obj.log.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')


@pytest.fixture
def legacy_probe(probe, monkeypatch):
    obj, _, _ = probe
    prepare = obj.prepare
    def legacy_prepare():
        result = prepare()
        rewrite_plan(obj, lambda p: p.pop('max_comment_requests'), version='v15_probe_v2')
        return result
    monkeypatch.setattr(obj, 'prepare', legacy_prepare)
    return probe


def test_full_precheck_write_readback_and_restart_never_duplicate(probe):
    obj,http,project=probe
    plan=obj.prepare()
    assert not http.writes() and plan['writes']==0
    assert obj.run(plan['probe_id'])['status']=='complete'
    assert len(http.writes())==2 and all(json.loads(r.data).get('is_private') is True for r in http.writes() if r.full_url.endswith('/comments/create/'))
    assert obj.run(plan['probe_id'])['status']=='complete' and len(http.writes())==2
    assert not (project/'data/forecasts.jsonl').exists() and not (project/'data/aggregates.jsonl').exists()
    assert 'UNIQUE-QUESTION-TEXT' not in obj.log.read_text()


@pytest.mark.parametrize('change', ['local','live','wrong_bot','stop','membership','copy','closed','revision','wrong_account'])
def test_all_writes_require_real_submission_prechecks(probe,change):
    obj,http,project=probe
    pid=obj.prepare()['probe_id']
    if change in ('local','live','wrong_bot'):
        mode={'mode':change if change!='wrong_bot' else 'trial','approved_bot_id':42 if change=='wrong_bot' else V.BOT_ID}
        (project/'config/submit_mode.json').write_text(json.dumps(mode))
    elif change=='stop': (project/'config/STOP_SUBMIT').touch()
    elif change=='membership': http.post['projects']['tournament'].append({'id':MAIN})
    elif change=='copy': http.posts[MAIN]=[copy.deepcopy(http.post)]
    elif change=='closed': http.post['question']['status']='closed'
    elif change=='revision': http.post['question']['scheduled_close_time']=to_iso(NOW+timedelta(days=40))
    elif change=='wrong_account': http.wrong_user=True
    with pytest.raises(S.PrecheckError):obj.run(pid)
    assert not http.writes()


def test_lost_response_reconciles_once_and_then_only_sends_comment(probe):
    obj,http,_=probe
    pid=obj.prepare()['probe_id'];http.after_write_error=True
    assert obj.run(pid)['status']=='complete'
    assert len(http.writes())==2


def test_unknown_request_and_restart_do_not_resend_forecast(probe):
    obj,http,_=probe
    pid=obj.prepare()['probe_id'];http.hide_readback=True
    assert obj.run(pid)['status']=='unknown' and len(http.writes())==1
    assert obj.run(pid)['status']=='unknown' and len(http.writes())==1
    http.hide_readback=False
    assert obj.run(pid)['status']=='complete' and len(http.writes())==2


def test_confirmed_rejection_never_retries_and_logs_no_remote_error(probe):
    obj,http,_=probe
    pid=obj.prepare()['probe_id'];http.fail_write=True
    assert obj.run(pid)['status']=='confirmed_failed'
    assert obj.run(pid)['status']=='confirmed_failed'
    assert len(http.writes())==1


def test_running_scheduler_and_submitter_block_probe(probe):
    obj,http,project=probe
    pid=obj.prepare()['probe_id']
    for name in ('scheduler.lock','submit.lock','forecast.gemini.lock'):
        with F.Lock(project/'data'/name), pytest.raises(F.PreflightError):obj.run(pid)
    assert not http.writes()


def test_tampered_ledger_refused(probe):
    obj,http,_=probe
    pid=obj.prepare()['probe_id']
    rows=obj.rows(); rows[0]['plan']['forecast_payload'][0]['probability_yes']=.8
    obj.log.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    with pytest.raises(S.PrecheckError):obj.run(pid)
    assert not http.writes()


def test_existing_forecast_or_wrong_venue_is_not_selected(probe):
    obj,http,_=probe
    http.post['question']['my_forecasts']={'history':[{'question_id':7}], 'latest':None}
    with pytest.raises(S.PrecheckError):obj.prepare()
    http.post['question']['my_forecasts']={'history':[], 'latest':None}
    http.post['projects']={'site_main':[{'id':144}]}
    with pytest.raises(S.PrecheckError):obj.prepare()
    assert not http.writes()


@pytest.mark.parametrize('field,value', [('is_private',False),('included_forecast',None),('author',{'id':42}), ('included_forecast',{'probability_yes':.7, 'question_type':'binary'})])
def test_comment_properties_cannot_be_assumed_from_post_success(probe,field,value):
    obj,http,_=probe
    pid=obj.prepare()['probe_id']
    assert obj.run(pid)['status']=='complete'
    http.comments[0][field]=value
    assert obj.run(pid)['status']=='comment_properties_unknown'
    assert len(http.writes())==2


def test_stop_between_journal_and_write_stays_unknown_without_retry(probe,monkeypatch):
    obj,http,project=probe
    pid=obj.prepare()['probe_id'];event=obj.event
    def stopped(probe_id,state,**kw):
        event(probe_id,state,**kw)
        if state=='request_started':(project/'config/STOP_SUBMIT').touch()
    monkeypatch.setattr(obj,'event',stopped)
    assert obj.run(pid)['status']=='unknown'
    (project/'config/STOP_SUBMIT').unlink()
    assert obj.run(pid)['status']=='unknown' and not http.writes()


def test_malformed_question_id_never_selected(probe):
    obj,http,_=probe
    http.post['question']['id']=True
    with pytest.raises(S.PrecheckError):obj.prepare()
    assert not http.writes()


@pytest.mark.parametrize('field', ['question_id','post_id'])
def test_target_payload_must_match_checked_snapshot_even_with_rehashed_plan(probe,field):
    obj,http,_=probe
    pid=obj.prepare()['probe_id']
    rows=obj.rows();p=rows[0]['plan'];p[field]+=1
    if field=='question_id':p['forecast_payload'][0]['question']=p[field]
    else:p['comment_payload']['on_post']=p[field]
    rows[0]['plan_sha256']=hash_obj(p)
    obj.log.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    with pytest.raises(S.PrecheckError):obj.run(pid)
    assert not http.writes()


def test_unknown_probe_reserves_question_against_normal_submitter(probe):
    obj,http,_=probe
    pid=obj.prepare()['probe_id']
    normal={'intent_id':'normal-intent','bot_id':V.BOT_ID,'question_id':7}
    obj.sub._duplicate(normal)  # Preparation alone does not reserve a write.
    http.hide_readback=True
    assert obj.run(pid)['status']=='unknown'
    with pytest.raises(S.PrecheckError,match='reserved by API component'):
        obj.sub._duplicate(normal)
    assert len(http.writes())==1


def test_prepare_skips_previously_attempted_probe_even_after_confirmed_rejection(probe):
    obj,http,_=probe
    pid=obj.prepare()['probe_id'];http.fail_write=True
    assert obj.run(pid)['status']=='confirmed_failed'
    post=copy.deepcopy(http.post);post['id']=18;post['question']['id']=8
    http.posts[BOT].append(post)
    assert obj.prepare()['question_id']==8


def test_orphan_probe_request_blocks_all_normal_writes(probe):
    obj,http,_=probe
    obj.event('missing-plan','request_started',operation='forecast')
    with pytest.raises(S.PrecheckError,match='without frozen plan'):
        obj.sub._duplicate({'intent_id':'normal','bot_id':V.BOT_ID,'question_id':7})
    assert not http.writes()


@pytest.mark.parametrize('boundary', ['after_precheck', 'after_journal'])
def test_deadline_is_rechecked_after_remote_reads_and_immediately_before_write(probe,monkeypatch,boundary):
    obj,http,_=probe
    pid=obj.prepare()['probe_id']
    def expire():obj.now=lambda:NOW+timedelta(days=31)
    if boundary=='after_precheck':
        check=obj._check
        def expired_check(*args):
            result=check(*args);expire();return result
        monkeypatch.setattr(obj,'_check',expired_check)
        with pytest.raises(S.PrecheckError,match='deadline elapsed'):obj.run(pid)
        assert not any(r['state']=='request_started' for r in obj.rows())
    else:
        event=obj.event
        def expired_event(probe_id,state,**kw):
            event(probe_id,state,**kw)
            if state=='request_started':expire()
        monkeypatch.setattr(obj,'event',expired_event)
        assert obj.run(pid)['status']=='unknown'
        assert obj.run(pid)['status']=='unknown'
    assert not http.writes()


def test_configuration_changes_during_remote_precheck_are_rechecked(probe,monkeypatch):
    obj,http,project=probe
    pid=obj.prepare()['probe_id'];check=obj._check
    def changed_check(*args):
        result=check(*args)
        p=project/'config/tournaments.json';cfg=json.loads(p.read_text())
        cfg['tournaments']['main']['id']+=1;p.write_text(json.dumps(cfg))
        return result
    monkeypatch.setattr(obj,'_check',changed_check)
    with pytest.raises(S.PrecheckError,match='identity changed'):obj.run(pid)
    assert not http.writes()


def test_prepare_requires_full_detail_when_listing_omits_own_history(probe):
    obj,http,_=probe
    listed=copy.deepcopy(http.post);listed['question'].pop('my_forecasts')
    http.posts[BOT]=[listed]
    pid=obj.prepare()['probe_id']
    assert obj.plan(pid)['question_id']==7
    assert any(r.full_url.endswith('/posts/17/') for r in http.calls)
    assert not http.writes()


def test_prepare_cannot_infer_empty_history_from_missing_detail(probe):
    obj,http,_=probe
    http.post['question'].pop('my_forecasts')
    with pytest.raises(ValueError,match='own forecast'):obj.prepare()
    assert not obj.rows() and not http.writes()


def test_prepare_rejects_detail_identity_change(probe,monkeypatch):
    obj,http,_=probe
    changed=copy.deepcopy(http.post);changed['question']['id']=88
    monkeypatch.setattr(obj.transport,'get_post',lambda _:changed)
    with pytest.raises(S.PrecheckError,match='identity differs'):obj.prepare()
    assert not obj.rows() and not http.writes()


def test_one_explicit_confirmed_comment_recovery_preserves_forecast_and_payload(legacy_probe):
    obj,http,_=legacy_probe
    pid=obj.prepare()['probe_id'];http.fail_comment=True
    assert obj.run(pid)['status']=='confirmed_failed'
    rejected=next(r for r in obj.rows() if r['state']=='confirmed_failed')
    assert rejected['http_status']==400
    assert obj.run(pid)['status']=='confirmed_failed' and len(http.writes())==2
    http.fail_comment=False
    assert obj.run(pid,retry_confirmed_comment=True)['status']=='complete'
    assert len(http.writes())==3
    assert sum(r.full_url.endswith('/forecast/') for r in http.writes())==1
    assert http.writes()[1].data==http.writes()[2].data
    assert obj.run(pid,retry_confirmed_comment=True)['status']=='complete' and len(http.writes())==3


def test_second_comment_rejection_is_terminal_even_with_recovery_flag(legacy_probe):
    obj,http,_=legacy_probe
    pid=obj.prepare()['probe_id'];http.fail_comment=True
    assert obj.run(pid)['status']=='confirmed_failed'
    assert obj.run(pid,retry_confirmed_comment=True)['status']=='confirmed_failed'
    assert obj.run(pid,retry_confirmed_comment=True)['status']=='confirmed_failed'
    assert len(http.writes())==3


def test_forecast_rejection_never_retried_by_comment_recovery(legacy_probe):
    obj,http,_=legacy_probe
    pid=obj.prepare()['probe_id'];http.fail_write=True
    assert obj.run(pid,retry_confirmed_comment=True)['status']=='confirmed_failed'
    assert obj.run(pid,retry_confirmed_comment=True)['status']=='confirmed_failed'
    assert len(http.writes())==1


def test_unknown_recovery_comment_only_reads_back_even_with_flag(legacy_probe,monkeypatch):
    from submit_contract import Readback
    obj,http,_=legacy_probe
    pid=obj.prepare()['probe_id'];http.fail_comment=True
    assert obj.run(pid)['status']=='confirmed_failed'
    http.fail_comment=False;original=obj.transport.readback
    monkeypatch.setattr(obj.transport,'readback',lambda op,intent,request_id:Readback(request_id=request_id))
    assert obj.run(pid,retry_confirmed_comment=True)['status']=='unknown'
    assert obj.run(pid,retry_confirmed_comment=True)['status']=='unknown'
    assert len(http.writes())==3
    monkeypatch.setattr(obj.transport,'readback',original)
    assert obj.run(pid,retry_confirmed_comment=True)['status']=='complete' and len(http.writes())==3
    comment_rows=[r for r in obj.rows() if r.get('operation')=='comment']
    second=[r for r in comment_rows if r['state']=='request_started'][-1]['request_id']
    assert {r['request_id'] for r in comment_rows if r['state'] in ('unknown','found')}=={second}


@pytest.mark.parametrize('status,version,allowed',[(None,'v15_probe_v1',True),(None,'v15_probe_v2',False),(500,'v15_probe_v2',False),(True,'v15_probe_v2',False)])
def test_recovery_requires_proven_rejection_or_unchanged_legacy_contract(legacy_probe,status,version,allowed):
    obj,http,_=legacy_probe
    pid=obj.prepare()['probe_id'];http.fail_comment=True
    assert obj.run(pid)['status']=='confirmed_failed'
    rows=obj.rows();r=next(r for r in rows if r['state']=='confirmed_failed');r['http_status']=status;r['version']=version
    obj.log.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    http.fail_comment=False
    assert obj.run(pid,retry_confirmed_comment=True)['status']==('complete' if allowed else 'confirmed_failed')
    assert len(http.writes())==(3 if allowed else 2)


def test_first_unknown_comment_is_never_retried_by_recovery_flag(legacy_probe,monkeypatch):
    from submit_contract import Readback
    obj,http,_=legacy_probe
    pid=obj.prepare()['probe_id'];original=obj.transport.readback
    def readback(op,intent,request_id):
        return Readback(request_id=request_id) if op=='comment' else original(op,intent,request_id=request_id)
    monkeypatch.setattr(obj.transport,'readback',readback)
    assert obj.run(pid)['status']=='unknown'
    assert obj.run(pid,retry_confirmed_comment=True)['status']=='unknown'
    assert len(http.writes())==2


def test_orphan_operation_failure_cannot_be_used_to_create_request(legacy_probe):
    obj,http,_=legacy_probe
    pid=obj.prepare()['probe_id']
    obj.event(pid,'confirmed_failed',operation='forecast',request_id='orphan',error_type='ConfirmedFailure',http_status=400)
    with pytest.raises(S.PrecheckError,match='without unique request'):obj.run(pid,retry_confirmed_comment=True)
    assert not http.writes()


@pytest.mark.parametrize('state',['unknown','write_returned','write_unconfirmed'])
def test_mixed_rejection_and_ambiguous_history_cannot_authorize_recovery(legacy_probe,state):
    obj,http,_=legacy_probe
    pid=obj.prepare()['probe_id'];http.fail_comment=True
    assert obj.run(pid)['status']=='confirmed_failed'
    first=next(r for r in obj.rows() if r.get('operation')=='comment' and r['state']=='request_started')
    obj.event(pid,state,operation='comment',request_id=first['request_id'])
    with pytest.raises(S.PrecheckError,match='ambiguous'):obj.run(pid,retry_confirmed_comment=True)
    assert len(http.writes())==2


@pytest.mark.parametrize('broken',['no_recovery_event','invalid_first_rejection'])
def test_resumed_second_request_requires_valid_first_rejection_and_recovery_event(legacy_probe,monkeypatch,broken):
    from submit_contract import Readback
    obj,http,_=legacy_probe
    pid=obj.prepare()['probe_id'];http.fail_comment=True
    assert obj.run(pid)['status']=='confirmed_failed'
    http.fail_comment=False
    monkeypatch.setattr(obj.transport,'readback',lambda op,intent,request_id:Readback(request_id=request_id))
    assert obj.run(pid,retry_confirmed_comment=True)['status']=='unknown'
    rows=obj.rows()
    if broken=='no_recovery_event':rows=[r for r in rows if r['state']!='comment_recovery_started']
    else:next(r for r in rows if r['state']=='confirmed_failed')['http_status']=500
    obj.log.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    with pytest.raises(S.PrecheckError,match='invalid comment recovery'):obj.run(pid,retry_confirmed_comment=True)
    assert len(http.writes())==3


def test_mc_prepare_uses_uniform_submission_rounding_and_bounded_plan(mc_probe):
    obj, http, project = mc_probe
    with pytest.raises(S.PrecheckError, match='no eligible'):
        obj.prepare()
    result = obj.prepare(question_type='multiple_choice')
    plan = obj.plan(result['probe_id'])
    assert result['writes'] == 0 and not http.writes()
    assert plan['forecast_payload'] == [{'question': 7, 'probability_yes_per_category':
        {'a': .333333, 'b': .333333, 'c': .333334}}]
    assert plan['max_comment_requests'] == 1
    assert plan['comment_payload'] == {'on_post': 17, 'is_private': True, 'included_forecast': True,
        'text': f"Forecast Lab API component check {result['probe_id']}. Synthetic uniform test; not a research forecast."}
    queries = [urllib.parse.parse_qs(urllib.parse.urlsplit(r.full_url).query)
        for r in http.calls if urllib.parse.urlsplit(r.full_url).path == '/api/posts/']
    assert queries[-1]['forecast_type'] == ['multiple_choice']
    assert obj.rows()[0]['version'] == V.VERSION
    assert 'UNIQUE-QUESTION-TEXT' not in obj.log.read_text()
    assert not (project/'data/forecasts.jsonl').exists()
    assert not (project/'data/aggregates.jsonl').exists()


def test_mc_known_history_completes_with_exact_label_mapping(mc_probe):
    obj, http, _ = mc_probe
    pid = obj.prepare(question_type='multiple_choice')['probe_id']
    assert obj.run(pid)['status'] == 'complete'
    assert obj.run(pid)['status'] == 'complete'
    assert len(http.writes()) == 2
    forecast = next(r for r in obj.rows() if r['state'] == 'found' and r['operation'] == 'forecast')
    assert forecast['receipt']['option_order_verified'] is True
    assert obj.rows()[-1]['included_forecast_order_verified'] is True


def test_mc_null_history_cannot_start_new_probe(mc_probe):
    obj, http, _ = mc_probe
    http.post['question']['all_options_ever'] = None
    with pytest.raises(S.PrecheckError):
        obj.prepare(question_type='multiple_choice')
    assert not http.writes()


@pytest.mark.parametrize('field,value', [
    ('question_type', 'binary'), ('options', ['a', 'c', 'b']), ('options', ['a', 'b', 'd']),
    ('probability_yes_per_category', {'a': .333333, 'b': .333333, 'c': .333334}),
    ('probability_yes_per_category', None),
    ('probability_yes_per_category', [.333333, .333333, .333333]),
    ('probability_yes_per_category', [.333333, None, .333334]),
    ('probability_yes_per_category', [.333333, .333333, .333334, .333334]),
    ('probability_yes_per_category', [True, .333333, .333334]),
    ('probability_yes_per_category', ['0.333333', .333333, .333334]),
    ('probability_yes_per_category', [float('nan'), .333333, .333334]),
    ('probability_yes_per_category', [float('inf'), .333333, .333334]),
    ('probability_yes_per_category', [-.1, .333333, .333334]),
    ('probability_yes_per_category', [1.1, .333333, .333334]),
    ('probability_yes_per_category', [10**400, .333333, .333334]),
])
def test_mc_invalid_included_forecast_stays_unknown_without_extra_posts(mc_probe, field, value):
    obj, http, _ = mc_probe
    included = {'question_type': 'multiple_choice', 'options': ['a', 'b', 'c'],
        'probability_yes_per_category': http.comment_values}
    included[field] = value
    http.comment_changes = {'included_forecast': included}
    pid = obj.prepare(question_type='multiple_choice')['probe_id']
    for _ in range(2):
        assert obj.run(pid)['status'] == 'comment_properties_unknown'
    assert len(http.writes()) == 2
    assert obj.rows()[-1]['verified'] is False
    assert obj.rows()[-1]['included_forecast_order_verified'] is False


@pytest.mark.parametrize('field,value', [
    ('id', True), ('author', {'id': 42}), ('on_post', True), ('is_private', False),
    ('text', 'changed'), ('included_forecast', None),
])
def test_mc_comment_identity_and_properties_still_required(mc_probe, field, value):
    obj, http, _ = mc_probe
    pid = obj.prepare(question_type='multiple_choice')['probe_id']
    assert obj.run(pid)['status'] == 'complete'
    http.comments[0][field] = value
    assert obj.run(pid)['status'] == 'comment_properties_unknown'
    assert len(http.writes()) == 2


@pytest.mark.parametrize('question_type', ['binary', 'multiple_choice'])
@pytest.mark.parametrize('status', [400, 429])
def test_v3_comment_rejection_never_allows_recovery(probe, question_type, status):
    obj, http, _ = probe
    if question_type == 'multiple_choice':
        http.post['question'].update(type=question_type, options=['a', 'b', 'c'], all_options_ever=['a', 'inactive', 'b', 'c'])
    pid = obj.prepare(question_type=question_type)['probe_id']
    http.fail_comment, http.failure_status = True, status
    assert obj.run(pid)['status'] == 'confirmed_failed'
    assert obj.rows()[-1]['http_status'] == status
    calls = len(http.calls)
    with pytest.raises(S.PrecheckError, match='forbids comment recovery'):
        obj.run(pid, retry_confirmed_comment=True)
    assert obj.run(pid)['status'] == 'confirmed_failed'
    assert len(http.calls) == calls and len(http.writes()) == 2


def test_v3_recovery_flag_refused_before_any_request(probe):
    obj, http, _ = probe
    pid = obj.prepare()['probe_id']
    calls = len(http.calls)
    with pytest.raises(S.PrecheckError, match='forbids comment recovery'):
        obj.run(pid, retry_confirmed_comment=True)
    assert len(http.calls) == calls and not http.writes()
    assert len(obj.rows()) == 1


@pytest.mark.parametrize('failure', ['400', '429', 'unknown'])
def test_mc_forecast_failure_stops_without_comment_or_resend(mc_probe, failure):
    obj, http, _ = mc_probe
    pid = obj.prepare(question_type='multiple_choice')['probe_id']
    if failure == 'unknown':
        http.hide_readback = True
    else:
        http.fail_write, http.failure_status = True, int(failure)
    for _ in range(2):
        assert obj.run(pid)['status'] == ('unknown' if failure == 'unknown' else 'confirmed_failed')
    assert len(http.writes()) == 1
    assert not any(r.get('operation') == 'comment' for r in obj.rows())


def test_mc_unknown_comment_only_reconciles_without_resend(mc_probe, monkeypatch):
    from submit_contract import Readback
    obj, http, _ = mc_probe
    original = obj.transport.readback
    def readback(operation, intent, request_id):
        return Readback(request_id=request_id) if operation == 'comment' else original(operation, intent, request_id=request_id)
    monkeypatch.setattr(obj.transport, 'readback', readback)
    pid = obj.prepare(question_type='multiple_choice')['probe_id']
    for _ in range(2):
        assert obj.run(pid)['status'] == 'unknown'
    assert len(http.writes()) == 2
    monkeypatch.setattr(obj.transport, 'readback', original)
    assert obj.run(pid)['status'] == 'complete' and len(http.writes()) == 2


@pytest.mark.parametrize('change', ['nonuniform', 'options', 'limit', 'bool_limit', 'hash'])
def test_mc_tampered_plan_refused(mc_probe, change):
    obj, http, _ = mc_probe
    pid = obj.prepare(question_type='multiple_choice')['probe_id']
    def mutate(p):
        if change == 'nonuniform':
            p['forecast_payload'][0]['probability_yes_per_category'] = {'a': .2, 'b': .3, 'c': .5}
        elif change == 'options':
            p['snapshot']['options'] = ['a', 'b', 'd']
        else:
            p['max_comment_requests'] = True if change == 'bool_limit' else 2
    if change == 'hash':
        rows = obj.rows()
        rows[0]['plan_sha256'] = 'changed'
        obj.log.write_text(json.dumps(rows[0]) + '\n')
    else:
        rewrite_plan(obj, mutate)
    calls = len(http.calls)
    with pytest.raises(S.PrecheckError):
        obj.run(pid)
    assert len(http.calls) == calls and not http.writes()


@pytest.mark.parametrize('options', [None, [], ['a'], ['a', 'a'], ['a', 1]])
def test_mc_invalid_current_options_not_selected(mc_probe, options):
    obj, http, _ = mc_probe
    http.post['question']['options'] = options
    with pytest.raises(S.PrecheckError):
        obj.prepare(question_type='multiple_choice')
    assert not obj.rows() and not http.writes()


def test_mc_unreconcilable_history_not_selected(mc_probe):
    obj, http, _ = mc_probe
    http.post['question']['all_options_ever'] = ['a', 'b']
    with pytest.raises(S.PrecheckError, match='no eligible'):
        obj.prepare(question_type='multiple_choice')
    assert not obj.rows() and not http.writes()


def test_mc_uniform_payload_enforces_submission_probability_bounds(mc_probe):
    obj, http, _ = mc_probe
    http.post['question']['options'] = [str(i) for i in range(1001)]
    http.post['question']['all_options_ever'] = list(http.post['question']['options'])
    with pytest.raises(S.PrecheckError, match='uniform payload'):
        obj.prepare(question_type='multiple_choice')
    assert not obj.rows() and not http.writes()


def test_mc_previous_probe_reserves_question_even_after_rejection(mc_probe):
    obj, http, _ = mc_probe
    pid = obj.prepare(question_type='multiple_choice')['probe_id']
    http.fail_write = True
    assert obj.run(pid)['status'] == 'confirmed_failed'
    with pytest.raises(S.PrecheckError, match='reserved by API component'):
        obj.sub._duplicate({'intent_id': 'normal', 'bot_id': V.BOT_ID, 'question_id': 7})
    post = copy.deepcopy(http.post)
    post['id'], post['question']['id'] = 18, 8
    http.posts[BOT].append(post)
    assert obj.prepare(question_type='multiple_choice')['question_id'] == 8
    assert len(http.writes()) == 1


@pytest.mark.parametrize('change', ['options', 'history', 'own_forecast', 'closed'])
def test_mc_run_rechecks_current_question_before_write(mc_probe, change):
    obj, http, _ = mc_probe
    pid = obj.prepare(question_type='multiple_choice')['probe_id']
    if change == 'options':
        http.post['question']['options'] = ['a', 'c', 'b']
    elif change == 'history':
        http.post['question']['all_options_ever'] = ['a']
    elif change == 'own_forecast':
        http.post['question']['my_forecasts'] = {'history': [{'question_id': 7}], 'latest': None}
    else:
        http.post['question']['status'] = 'closed'
    with pytest.raises(S.PrecheckError):
        obj.run(pid)
    assert not http.writes()


@pytest.mark.parametrize('args,expected', [([], 'binary'), (['--question-type', 'multiple_choice'], 'multiple_choice')])
def test_cli_passes_question_type_without_real_credentials_or_http(probe, monkeypatch, capsys, args, expected):
    import flab.envfile
    obj, http, _ = probe
    calls = []
    monkeypatch.setattr(flab.envfile, 'get_secret', lambda *a: TOKEN)
    monkeypatch.setattr(V, 'APITransport', lambda token: obj.transport)
    monkeypatch.setattr(V, 'Probe', lambda *a: obj)
    def prepare(question_type, scenario):
        calls.append((question_type, scenario))
        return {'writes': 0}
    monkeypatch.setattr(obj, 'prepare', prepare)
    assert V.main(['prepare', *args]) == 0
    assert calls == [(expected, 'single')] and not http.calls
    assert json.loads(capsys.readouterr().out) == {'writes': 0}


def test_comment_properties_use_documented_own_comment_list(mc_probe):
    obj, http, _ = mc_probe
    pid = obj.prepare(question_type='multiple_choice')['probe_id']
    assert obj.run(pid)['status'] == 'complete'
    paths = [urllib.parse.urlsplit(r.full_url).path for r in http.calls]
    assert '/api/comments/100/' not in paths
    last = [r for r in http.calls if urllib.parse.urlsplit(r.full_url).path == '/api/comments/'][-1]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(last.full_url).query)
    assert query['author'] == [str(V.BOT_ID)] and query['post'] == ['17'] and query['is_private'] == ['true']


@pytest.mark.parametrize('change', ['duplicate', 'missing'])
def test_ambiguous_or_missing_comment_row_stays_unverified(mc_probe, change):
    obj, http, _ = mc_probe
    pid = obj.prepare(question_type='multiple_choice')['probe_id']
    assert obj.run(pid)['status'] == 'complete'
    if change == 'duplicate':
        http.comments.append(copy.deepcopy(http.comments[0]))
    else:
        http.comments.clear()
    assert obj.run(pid)['status'] == 'comment_properties_unknown'
    assert obj.rows()[-1]['verified'] is False and len(http.writes()) == 2


@pytest.mark.parametrize('question_type', ['binary', 'multiple_choice'])
def test_v3_plan_cannot_drop_comment_bound_to_regain_recovery(probe, question_type):
    obj, http, _ = probe
    if question_type == 'multiple_choice':
        http.post['question'].update(type=question_type, options=['a', 'b', 'c'], all_options_ever=['a', 'inactive', 'b', 'c'])
    pid = obj.prepare(question_type=question_type)['probe_id']
    rewrite_plan(obj, lambda p: p.pop('max_comment_requests'))
    calls = len(http.calls)
    with pytest.raises(S.PrecheckError, match='invalid synthetic probe plan'):
        obj.run(pid, retry_confirmed_comment=True)
    with pytest.raises(S.PrecheckError, match='invalid synthetic probe plan'):
        obj.run(pid)
    assert len(http.calls) == calls and not http.writes()


def test_legacy_label_cannot_unbound_multiple_choice_plan(mc_probe):
    obj, http, _ = mc_probe
    pid = obj.prepare(question_type='multiple_choice')['probe_id']
    rewrite_plan(obj, lambda p: p.pop('max_comment_requests'), version='v15_probe_v2')
    with pytest.raises(S.PrecheckError, match='invalid synthetic probe plan'):
        obj.run(pid)
    assert not http.writes()
