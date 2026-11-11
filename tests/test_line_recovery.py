import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

# Line recovery lifts pre-D149 persistent stops; exercise it under those rules.
pytestmark = pytest.mark.usefixtures("legacy_stops")
import forecast as F
import line_recovery as R
from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import to_iso

NOW=datetime(2026,11,5,11,tzinfo=timezone.utc)

@pytest.fixture
def fixture(tmp_path,monkeypatch):
    project=tmp_path/'project';root=project/'data';root.mkdir(parents=True)
    brain=tmp_path/'brain';monkeypatch.setattr(R.G,'brain_dir',lambda:brain)
    target=str(brain/'conversation-1/.system_generated/steps/3/content.md')
    params={'AbsolutePath':target,'StartLine':1,'EndLine':5}
    events=[{'event':'init','conversation_id':'conversation-1','init':{}},
        *[{'event':'step_update','step_update':{'step_index':3,'step_type':'tool','state':state,
             'tool_info':{'name':'read_url_content','parameters':{'Url':'https://example.com'}}}} for state in ('ACTIVE','DONE')],
        *[{'event':'step_update','step_update':{'step_index':4,'step_type':'tool','state':state,
             'tool_info':{'name':' view_file','parameters':params,**extra}}}
          for state,extra in [('ACTIVE',{}),('ERROR',{'error':{'message':R.REJECTION}})]],
        {'event':'result','result':{'status':'SUCCESS','structured_output':{'never_read':'forecast contents'}}}]
    row={'type':'forecast_attempt','attempt_id':'att_one','player':'gemini','tier':2,'question_id':123,
        'question_snapshot_id':'qs_one','run_id':'run_one','attempt_kind':'initial','prev_attempt_id':None,
        'requested_model_id':'gemini-4-argon-high','requested_effort':'high','cli_version':'1.4.12','validation_status':'incomplete','error_type':'tool_scope_violation',
        'model_check':'verified','audit_flags':[],'own_audit_flags':[],
        'tool_violations':[{'type':'unexpected_tool','tool':' view_file','step':4,'state':'ERROR'},
                           {'type':'unexpected_tool','tool':' view_file','count':1}],
        'tool_call_counts':{'read_url_content':1,' view_file':1},
        'raw_dir':'data/raw/gemini/123_att_one','finished_at_utc':to_iso(NOW-timedelta(seconds=5)),
        'started_at_utc':to_iso(NOW-timedelta(seconds=60)),'run_config_version':'run_config_v3',
        'decision_deadline_utc':to_iso(NOW+timedelta(hours=4)),
        'reasoning_summary':'never inspect','probability':0.876}
    stop={'type':'line_stop','player':'gemini','run_id':'run_one','at':to_iso(NOW),
        'reason':'tool_scope_violation on attempt att_one: stop the line and notify Louis; re-acceptance required (§7.3, D60, D62)'}
    raw=project/row['raw_dir']/'stdout.jsonl';raw.parent.mkdir(parents=True)
    def save_events():raw.write_text(''.join(json.dumps(e)+'\n' for e in events))
    def save_ledgers():
        for name,rows in [('forecasts.jsonl',[row]),('line_status.jsonl',[stop])]:
            (root/name).write_text(''.join(json.dumps(r)+'\n' for r in rows))
    save_events();save_ledgers()
    wrapper=project/'launchd/bin/agy';wrapper.parent.mkdir(parents=True,exist_ok=True);wrapper.write_text('wrapper')
    pin=root/'cli_pins/agy/1.4.12';pin.parent.mkdir(parents=True,exist_ok=True);pin.write_text('pin')
    stamp=to_iso(NOW-timedelta(hours=1))
    append_record(root/'acceptance.jsonl',{'type':'baseline_approved','record_id':'base','approved_at':stamp})
    from flab.config import load_run_config
    cfg=load_run_config()
    for record_id in ('accepted','prod1','prod2'):
        append_record(root/'acceptance.jsonl',{'type':'acceptance_result','record_id':record_id,
            'player':'gemini','finished_at':stamp,'line_pass':True,
            **({'mode':'production','tier':int(record_id[-1]),**cfg['players']['gemini'][f'tier{record_id[-1]}']} if record_id.startswith('prod') else {})})
    from flab.config import load_run_config
    binding={'baseline_record_id':'base','runtime':F.runtime_fingerprint('gemini'),
        'run_config_sha256':hash_obj(load_run_config()),'wrapper_sha256':R.sha(wrapper),'pin_sha256':R.sha(pin),
        'judge_version':F.J.JUDGE_VERSION,'cli_version':'1.4.12','sources':{'synthetic':['absent',None]},
        'proofs':[{'tier':tier,'acceptance_record_id':'accepted','production_record_id':f'prod{tier}'} for tier in (1,2)]}
    return SimpleNamespace(project=project,root=root,row=row,stop=stop,events=events,raw=raw,
                           save_events=save_events,save_ledgers=save_ledgers,binding=binding,capture=lambda *_:copy.deepcopy(binding))


def test_exact_rejection_has_no_prediction_content(fixture):
    f=fixture
    proof=R.classify(f.row,f.project)
    assert proof['diagnosis']=='unexecuted_page_tool_typo'
    assert '0.876' not in json.dumps(proof) and 'never inspect' not in json.dumps(proof)
    candidate,status=R.candidate(f.root,NOW)
    assert status=='eligible' and candidate['row']==R.projection(f.row)


@pytest.mark.parametrize('key,value',[
    ('player','gpt'),('cli_version','1.5.0'),('model_check','unverified'),('attempt_kind','rerun'),
    ('prev_attempt_id','old'),('audit_flags',['suspected_leak']),('own_audit_flags',['leak_confirmed']),
    ('subagent_count',1),('denied_actions',[{'tool':'write_file'}]),('preflight_waivers',['x']),
    ('safety_stop_reason','nonzero credits'),('quota_after_error','missing'),('recovery_no_rerun',True),
    ('validation_status','valid'),('inherited_model_mismatch',True),('tool_call_counts',{}),
])
def test_metadata_disqualifies(fixture,key,value):
    f=fixture;f.row[key]=value
    assert R.classify(f.row,f.project) is None


@pytest.mark.parametrize('change', ['done_before_error','output','wrong_error','unicode_space',
                                  'outside_file','other_conversation','denied','unknown_tool','duplicate_final',
                                  'metaculus','bad_init','truncated'])
def test_evidence_cannot_be_normalized_or_waived(fixture,change):
    f=fixture;e=f.events
    if change=='done_before_error':e[3]['step_update']['state']='DONE'
    elif change=='output':e[3]['step_update']['tool_info']['output']='private bytes'
    elif change=='wrong_error':e[4]['step_update']['tool_info']['error']['message']='something else'
    elif change=='unicode_space':
        for x in e[3:5]:x['step_update']['tool_info']['name']='\u00a0view_file'
    elif change=='outside_file':e[3]['step_update']['tool_info']['parameters']['AbsolutePath']='/etc/passwd'
    elif change=='other_conversation':e[0]['conversation_id']='other'
    elif change=='denied':e[-1]['result']['denied_actions']=[{'action':'write'}]
    elif change=='unknown_tool':e[2]['step_update']['tool_info']['name']='run_command'
    elif change=='duplicate_final':e.append(copy.deepcopy(e[-1]))
    elif change=='metaculus':e[2]['step_update']['tool_info']['parameters']['Url']='https://www.metaculus.com'
    elif change=='bad_init':e[0]['conversation_id']='../outside'
    f.save_events()
    if change=='truncated':f.raw.write_text(f.raw.read_text()+'{bad\n')
    assert R.classify(f.row,f.project) is None


def test_other_hazards_and_unknown_starts_block(fixture):
    f=fixture
    append_record(f.root/'forecasts.jsonl',{**f.row,'attempt_id':'hazard','error_type':None,
        'tool_violations':[],'own_audit_flags':['leak_confirmed']})
    assert R.candidate(f.root,NOW)[1]=='safety_review_required'
    append_record(f.root/'forecast_attempt_starts.jsonl',{'player':'gemini','attempt_id':'unknown'})
    with pytest.raises(ValueError,match='unresolved_start'):R.candidate(f.root,NOW)


def test_historical_suspected_hits_retained_but_do_not_become_hard_stops(fixture):
    f=fixture
    append_record(f.root/'forecasts.jsonl',{**f.row,'attempt_id':'old','error_type':None,
        'tool_violations':[],'audit_flags':['suspected_leak'],'own_audit_flags':['suspected_leak']})
    assert R.candidate(f.root,NOW)[1]=='eligible'
    assert read_complete(f.root/'forecasts.jsonl')[-1]['own_audit_flags']==['suspected_leak']


def enable(f,monkeypatch):
    config=f.project/'config';config.mkdir()
    settings={'version':R.VERSION,'enabled':True,'authorization':R.AUTHORIZATION,'interval_s':300}
    (config/'line_recovery.json').write_text(json.dumps(settings))
    from flab.config import load_run_config
    cfg=load_run_config()
    monkeypatch.setattr(R,'load_run_config',lambda *_:cfg)
    monkeypatch.setattr(R,'load_submit_mode',lambda *_:{'mode':'live'})
    monkeypatch.setattr(R,'load_players_enabled',lambda *_:{'gemini':True})
    monkeypatch.setattr(R.W,'campaign_from',lambda *_:{'campaign':'fixed'})
    monkeypatch.setattr(R.W,'require_active',lambda *_:True)


def test_resume_then_original_single_rerun_and_idempotence(fixture,monkeypatch):
    f=fixture;enable(f,monkeypatch)
    old=f.raw.read_bytes(),(f.root/'forecasts.jsonl').read_bytes()
    result=R.recover(f.root,now=lambda:NOW,capture_fn=f.capture)
    assert result['status']=='resumed' and result['model_calls']==0
    assert F.line_stopped(f.root/'line_status.jsonl','gemini',f.root/'forecasts.jsonl',now=NOW) is None
    assert R.retry_grant(f.root,[f.row],f.row,now=NOW)
    assert F._may_rerun([f.row],f.row,recovery_root=f.root,now=NOW)
    assert not F._may_rerun([f.row],f.row)
    assert old==(f.raw.read_bytes(),(f.root/'forecasts.jsonl').read_bytes())
    assert R.recover(f.root,now=lambda:NOW,capture_fn=lambda *_:pytest.fail('not again'))['status']=='healthy'
    append_record(f.root/'forecast_attempt_starts.jsonl',{'player':'gemini','question_id':123,'tier':2,
        'attempt_kind':'rerun','attempt_id':'new','started_at_utc':to_iso(NOW)})
    assert R.retry_grant(f.root,[f.row],f.row,now=NOW) is None


def test_changed_environment_never_resumes_or_retries_same_stop(fixture,monkeypatch):
    f=fixture;enable(f,monkeypatch);values=iter([{**f.binding,'hash':'a'},{**f.binding,'hash':'b'}])
    with pytest.raises(ValueError,match='environment_changed'):
        R.recover(f.root,now=lambda:NOW,capture_fn=lambda *_:next(values))
    assert F.line_stopped(f.root/'line_status.jsonl','gemini',f.root/'forecasts.jsonl',now=NOW)
    assert R.recover(f.root,now=lambda:NOW,capture_fn=lambda *_:pytest.fail('no retry'))['status']=='already_attempted'


def test_deadline_expired_resumes_future_work_without_grant(fixture,monkeypatch):
    f=fixture;enable(f,monkeypatch);f.row['decision_deadline_utc']=to_iso(NOW);f.save_ledgers()
    result=R.recover(f.root,now=lambda:NOW,capture_fn=f.capture)
    assert result['status']=='resumed' and result['retry_granted'] is False


def test_superseding_stop_blocks_resume(fixture,monkeypatch):
    f=fixture;enable(f,monkeypatch);calls=[]
    def capture(*_):
        calls.append(1)
        if len(calls)==2:append_record(f.root/'line_status.jsonl',{**f.stop,'reason':'new safety issue'})
        return copy.deepcopy(f.binding)
    with pytest.raises(ValueError,match='superseded|other_uncovered_stop'):R.recover(f.root,now=lambda:NOW,capture_fn=capture)
    assert all(r['type']!='line_resume' for r in read_complete(f.root/'line_status.jsonl'))


def test_global_stop_and_inactive_window_never_override(fixture,monkeypatch):
    f=fixture;enable(f,monkeypatch);(f.project/'config/STOP_SUBMIT').write_text('stop')
    with pytest.raises(ValueError,match='global_stop'):
        R.recover(f.root,now=lambda:NOW,capture_fn=lambda *_:pytest.fail('no capture'))


def test_daily_limit_and_busy_do_not_call_models(fixture,monkeypatch):
    f=fixture;enable(f,monkeypatch)
    for i in range(3):append_record(f.root/'line_recovery.jsonl',{'type':'recovery_started','at':to_iso(NOW),'stop_sha256':str(i)})
    assert R.recover(f.root,now=lambda:NOW,capture_fn=lambda *_:pytest.fail('no capture'))['status']=='daily_limit'
    with F.Lock(f.root/'forecast.gemini.lock'),pytest.raises(F.PreflightError):R.recover(f.root,now=lambda:NOW)


def test_late_audit_or_changed_raw_cannot_use_grant(fixture,monkeypatch):
    f=fixture;enable(f,monkeypatch)
    R.recover(f.root,now=lambda:NOW,capture_fn=f.capture)
    append_record(f.root/'audits.jsonl',{'attempt_id':f.row['attempt_id'],'audit_flags':['leak_confirmed']})
    assert R.retry_grant(f.root,[f.row],f.row,now=NOW) is None
    (f.root/'audits.jsonl').unlink()
    f.raw.write_text(f.raw.read_text()+'\n')
    assert R.retry_grant(f.root,[f.row],f.row,now=NOW) is None


def test_crash_after_start_never_retries(fixture,monkeypatch):
    f=fixture;enable(f,monkeypatch);calls=[]
    def capture(*_):
        calls.append(1)
        if len(calls)>1:raise KeyboardInterrupt()
        return copy.deepcopy(f.binding)
    with pytest.raises(KeyboardInterrupt):R.recover(f.root,now=lambda:NOW,capture_fn=capture)
    assert R.recover(f.root,now=lambda:NOW,capture_fn=lambda *_:pytest.fail('no retry'))['status']=='already_attempted'
    assert all(r['type']!='line_resume' for r in read_complete(f.root/'line_status.jsonl'))


def test_recovery_retry_reaches_real_start_journal_once(fixture,monkeypatch):
    from dataclasses import replace
    from test_forecast import FakeAdapter, SNAP, _good, _qsnap, POLICY
    from flab.config import load_run_config
    f=fixture;cfg=load_run_config();enable(f,monkeypatch)
    f.row.update(tier=1,question_type='binary',requested_model_id='gemini-4-argon-medium',requested_effort='medium');f.save_ledgers()
    result=R.recover(f.root,now=lambda:NOW,capture_fn=f.capture)
    assert result['retry_granted']
    snapshots=iter([replace(_qsnap(.9),taken_at=to_iso(NOW)),replace(_qsnap(.89),taken_at=to_iso(NOW))])
    monkeypatch.setattr(F.quota,'judge',lambda *_:'verified')
    adapter=FakeAdapter([{'structured':_good(question_id=123)}])
    work=f.project.parent/'empty';work.mkdir()
    admissions=[]
    ctx=F.Context(player='gemini',adapter=adapter,cfg=cfg,run_id='rerun_test',preflight=F.Preflight('gemini'),
        now=lambda:NOW,forecasts_log=f.root/'forecasts.jsonl',audits_log=f.root/'audits.jsonl',
        line_status_log=f.root/'line_status.jsonl',quota_log=f.root/'quota.jsonl',raw_root=f.root/'raw',
        make_dir=lambda:work,remove_dir=lambda _:None,quota_snapshot=lambda **_:next(snapshots),
        quota_policy=POLICY,before_attempt=lambda kind,aid:admissions.append((kind,aid)),continuous=True)
    snap={**SNAP,'question_id':123,'snapshot_id':'qs_one'}
    outcome=F.forecast_question(ctx,snap)
    assert outcome['outcome']=='selected' and adapter.ran==1 and admissions[0][0]=='rerun'
    start=read_complete(f.root/'forecast_attempt_starts.jsonl')[-1]
    assert start['recovery_grant_id']==result['recovery_id'] and start['prev_attempt_id']=='att_one'
    assert F.forecast_question(ctx,snap)['outcome']=='already_selected' and adapter.ran==1
    assert read_complete(f.root/'forecasts.jsonl')[0]['validation_status']=='incomplete'


@pytest.mark.parametrize('fault',['none','pin','wrapper','credits','stale','baseline','tier1','tier2','waiver'])
def test_capture_requires_unchanged_identity_zero_credits_and_both_proofs(fixture,monkeypatch,fault):
    f=fixture;wrapper=f.project/'launchd/bin/agy';wrapper.parent.mkdir(parents=True,exist_ok=True);wrapper.write_text('wrapper')
    pin=f.root/'cli_pins/agy/1.4.12';pin.parent.mkdir(parents=True,exist_ok=True);pin.write_text('pin')
    monkeypatch.setattr(R,'PIN_SHA',R.sha(pin));monkeypatch.setattr(R,'WRAPPER_SHA',R.sha(wrapper))
    monkeypatch.setattr(R.shutil,'which',lambda _:str(wrapper))
    if fault=='pin':pin.write_text('changed')
    if fault=='wrapper':wrapper.write_text('changed')
    monkeypatch.setattr(R.quota,'snapshot',lambda **_:SimpleNamespace(remaining_credits=1 if fault=='credits' else 0))
    monkeypatch.setattr(R.quota,'observation_fresh',lambda *_:fault!='stale')
    monkeypatch.setattr(R.B,'cli_versions',lambda:{'gemini':'1.4.12'})
    monkeypatch.setattr(R.B,'take_snapshot',lambda **_:SimpleNamespace(to_record=lambda:{'sources':{}}))
    monkeypatch.setattr(R.B,'latest_approved',lambda *_:{'record_id':'approved'})
    monkeypatch.setattr(R.B,'compare',lambda *_:{'decisions':{'gemini':'stop' if fault=='baseline' else 'ok'}})
    seen=[]
    def pf(player,**kw):
        seen.append(kw['tier']);tier=kw['tier']
        return SimpleNamespace(ok=fault!=f'tier{tier}',waivers=['waiver'] if fault=='waiver' else [],
                               acceptance_record_id='a',production_record_id=f'p{tier}')
    monkeypatch.setattr(R.F,'preflight',pf)
    if fault!='none':
        with pytest.raises(ValueError):R.capture(f.project,{})
    else:
        binding=R.capture(f.project,{})
        assert seen==[1,2] and [p['production_record_id'] for p in binding['proofs']]==['p1','p2']


def test_new_late_safety_evidence_blocks_recovery_even_if_stop_unchanged(fixture):
    f=fixture
    append_record(f.root/'audits.jsonl',{'attempt_id':f.row['attempt_id'],'audit_flags':['leak_confirmed']})
    with pytest.raises(ValueError,match='confirmed_audit_exposure'):R.candidate(f.root,NOW)


def test_resume_only_covers_its_exact_episode_not_old_or_late_hazards(fixture,monkeypatch):
    f=fixture;enable(f,monkeypatch)
    R.recover(f.root,now=lambda:NOW,capture_fn=f.capture)
    assert F.latest_resume(f.root/'line_status.jsonl','gemini') is None
    # A late-arriving, backdated hazard cannot be hidden behind auto-resume time.
    append_record(f.root/'forecasts.jsonl',{**f.row,'attempt_id':'late_old','safety_stop_reason':'unsafe',
        'finished_at_utc':to_iso(NOW-timedelta(seconds=20))})
    assert F.line_stopped(f.root/'line_status.jsonl','gemini',f.root/'forecasts.jsonl',now=NOW)


def test_older_uncovered_explicit_stop_prevents_recovery(fixture):
    f=fixture
    (f.root/'line_status.jsonl').write_text(json.dumps({**f.stop,'reason':'earlier distinct stop'})+'\n'+json.dumps(f.stop)+'\n')
    with pytest.raises(ValueError,match='other_uncovered_stop'):R.candidate(f.root,NOW)


def test_page_must_come_from_successful_read_in_same_attempt(fixture):
    f=fixture
    f.events[2]['step_update']['tool_info']['name']='search_web';f.save_events()
    assert R.classify(f.row,f.project) is None
    f.events[2]['step_update']['tool_info']['name']='read_url_content'
    f.events[2]['step_update']['state']='ERROR';f.save_events()
    assert R.classify(f.row,f.project) is None


@pytest.mark.parametrize('kind',['empty_output','duplicate_key','unknown_event','unknown_field'])
def test_strict_runtime_event_schema(fixture,kind):
    f=fixture
    if kind=='empty_output':f.events[4]['step_update']['tool_info']['output']=''
    elif kind=='unknown_event':f.events.insert(-1,{'event':'hidden_action'})
    elif kind=='unknown_field':f.events[4]['step_update']['execute']='x'
    f.save_events()
    if kind=='duplicate_key':f.raw.write_text(f.raw.read_text().replace('"state": "ERROR"','"state":"DONE","state":"ERROR"'))
    assert R.classify(f.row,f.project) is None



def test_same_attempt_id_cannot_cover_new_tool_hazard(fixture,monkeypatch):
    f=fixture;enable(f,monkeypatch)
    R.recover(f.root,now=lambda:NOW,capture_fn=f.capture)
    append_record(f.root/'forecasts.jsonl',{**f.row,'tool_violations':[{'type':'unexpected_tool','tool':'run_command','count':1}]})
    assert F.line_stopped(f.root/'line_status.jsonl','gemini',f.root/'forecasts.jsonl',now=NOW)
    with pytest.raises(ValueError,match='duplicate_attempt_id'):R.candidate(f.root,NOW)



@pytest.mark.parametrize('where',['init','step_update','result'])
def test_conflicting_conversation_identity_is_not_recoverable(fixture,where):
    f=fixture
    if where=='init':f.events[0]['init']['conversation_id']='other-conversation'
    elif where=='step_update':f.events[3]['step_update']['conversation_id']='other-conversation'
    else:f.events[-1]['result']['conversation_id']='other-conversation'
    f.save_events()
    assert R.classify(f.row,f.project) is None



def test_earlier_tool_identity_cannot_be_overwritten_by_page_fetch(fixture):
    f=fixture
    hidden=copy.deepcopy(f.events[1:3])
    for e in hidden:
        e['step_update']['tool_info']={'name':'view_file','parameters':{'AbsolutePath':'/outside/secret'}}
    hidden[-1]['step_update']['tool_info']['output']='synthetic prohibited output'
    f.events[1:1]=hidden;f.save_events()
    assert R.classify(f.row,f.project) is None


@pytest.mark.parametrize('field',['name','parameters','state','step_type'])
def test_every_tool_step_identity_and_transition_are_checked(fixture,field):
    f=fixture
    if field=='name':f.events[1]['step_update']['tool_info']['name']='view_file'
    elif field=='parameters':f.events[1]['step_update']['tool_info']['parameters']={'Url':'https://different.example'}
    elif field=='state':f.events[1]['step_update']['state']='DONE'
    else:f.events[1]['step_update']['step_type']='agent_response'
    f.save_events()
    assert R.classify(f.row,f.project) is None


@pytest.mark.parametrize('change',['string_index','bool_index','non_tool_identity','nan'])
def test_no_unparsed_tool_evidence_or_nonfinite_json(fixture,change):
    f=fixture
    if change=='string_index':f.events[1]['step_update']['step_index']='3'
    elif change=='bool_index':f.events[1]['step_update']['step_index']=True
    elif change=='non_tool_identity':
        f.events.insert(-1,{'event':'step_update','step_update':{'step_index':9,'step_type':'agent_response',
                           'state':'DONE','tool_name':'view_file'}})
    else:f.events[-1]['result']['structured_output']={'x':float('nan')}
    f.save_events()
    assert R.classify(f.row,f.project) is None


@pytest.mark.parametrize('kind',['baseline_approved','acceptance_call_started','acceptance_result','config'])
def test_incident_cannot_reuse_later_environment_approval(fixture,monkeypatch,kind):
    f=fixture;enable(f,monkeypatch)
    if kind=='config':
        f.row['run_config_version']='changed';f.save_ledgers()
    else:
        time_field={'baseline_approved':'approved_at','acceptance_call_started':'started_at','acceptance_result':'finished_at'}[kind]
        append_record(f.root/'acceptance.jsonl',{'type':kind,'player':'gemini',time_field:to_iso(NOW)})
    with pytest.raises(ValueError,match='incident|after_incident'):
        R.recover(f.root,now=lambda:NOW,capture_fn=lambda *_:pytest.fail('cannot use later approval'))


@pytest.mark.parametrize('fault',['completion_log','after_commit_signal'])
def test_authoritative_resume_never_relabelled_held(fixture,monkeypatch,fault):
    f=fixture;enable(f,monkeypatch);append=R.append_record
    def failing(path,row):
        if fault=='completion_log' and row.get('type')=='recovery_finished':raise OSError('synthetic disk error')
        append(path,row)
        if fault=='after_commit_signal' and row.get('type')=='line_resume':raise R.RecoveryDeadline()
    monkeypatch.setattr(R,'append_record',failing)
    result=R.recover(f.root,now=lambda:NOW,capture_fn=f.capture)
    assert result['status']=='resumed' and result['completion_journal']=='pending'
    assert F.line_stopped(f.root/'line_status.jsonl','gemini',f.root/'forecasts.jsonl',now=NOW) is None
    assert not any(r.get('outcome')=='held' for r in read_complete(f.root/'line_recovery.jsonl'))


def test_healthy_line_never_acquires_recovery_or_forecast_lock(fixture,monkeypatch):
    f=fixture;enable(f,monkeypatch)
    append_record(f.root/'line_status.jsonl',{'type':'line_resume','player':'gemini','by':'Louis','at':to_iso(NOW)})
    with F.Lock(f.root/'forecast.gemini.lock'),F.Lock(f.root/'line_recovery.lock'):
        assert R.recover(f.root,now=lambda:NOW,capture_fn=lambda *_:pytest.fail('healthy'))['status']=='healthy'


def test_recovery_does_not_lock_other_lines_or_submit(fixture,monkeypatch):
    from contextlib import ExitStack
    f=fixture;enable(f,monkeypatch)
    def capture(*_):
        with ExitStack() as stack:
            for name in ('forecast.claude.lock','forecast.gpt.lock','cycle.lock','scheduler.lock','submit.lock'):
                stack.enter_context(F.Lock(f.root/name))
        return copy.deepcopy(f.binding)
    assert R.recover(f.root,now=lambda:NOW,capture_fn=capture)['status']=='resumed'


def test_recovery_grant_is_selected_and_budgeted_by_real_scheduler(fixture, monkeypatch):
    from dataclasses import replace
    import scheduler as S
    from test_scheduler import campaign
    from test_forecast import FakeAdapter, SNAP, _good, _qsnap
    f=fixture;enable(f,monkeypatch)
    f.row.update(tier=1,question_type='binary',requested_model_id='gemini-4-argon-medium',requested_effort='medium');f.save_ledgers()
    result=R.recover(f.root,now=lambda:NOW,capture_fn=f.capture)
    cfg=campaign(1);cfg['questions']=[{'question_id':123,'ground':'bot_testing_area','priority':'main'}]
    cfg['budget_window_end']=to_iso(NOW+timedelta(days=1))
    snap={**SNAP,'question_id':123,'snapshot_id':'qs_one'}
    adapter=FakeAdapter([{'structured':_good(question_id=123)}])
    monkeypatch.setattr(S,'load_players_enabled',lambda:{'claude':False,'gemini':True,'gpt':False})
    monkeypatch.setattr(F.quota,'judge',lambda *_:'verified')
    quotas=iter([replace(_qsnap(.9),taken_at=to_iso(NOW)),replace(_qsnap(.89),taken_at=to_iso(NOW))])
    obj=S.Scheduler(f.root,cfg,snapshots=lambda:{123:snap},adapter_loader=lambda _:adapter,
        preflight=lambda p,t,a:F.Preflight(p,tier=t),now=lambda:NOW,continuous=True,
        acceptance_log=f.root/'acceptance.jsonl',
        context_extra=lambda p:{'quota_snapshot':lambda **_:next(quotas)})
    jobs=obj.jobs({123:snap},paused=set(),active=set())
    assert len(jobs)==1 and jobs[0].player=='gemini' and jobs[0].tier==1
    outcome=obj.execute(jobs[0])
    assert outcome['outcome']=='selected' and adapter.ran==1
    start=read_complete(f.root/'forecast_attempt_starts.jsonl')[-1]
    assert start['recovery_grant_id']==result['recovery_id']
    assert start['admission_decision_id']
    assert read_complete(f.root/'forecasts.jsonl')[0]['validation_status']=='incomplete'


@pytest.mark.parametrize('change',['autoload_result','unknown','missing_time','production'])
def test_postincident_or_unknown_acceptance_evidence_is_never_reused(fixture,monkeypatch,change):
    f=fixture;enable(f,monkeypatch)
    if change=='unknown':row={'type':'unknown_approval','at':to_iso(NOW)}
    elif change=='missing_time':row={'type':'acceptance_result','player':'gemini'}
    else:row={'type':'acceptance_result','player':'gemini','source':'autoload' if change=='autoload_result' else 'manual',
              'mode':'production','finished_at':to_iso(NOW),'record_id':'postincident'}
    append_record(f.root/'acceptance.jsonl',row)
    with pytest.raises(ValueError,match='incident'):
        R.recover(f.root,now=lambda:NOW,capture_fn=lambda *_:pytest.fail('no capture'))
    assert not any(r['type']=='line_resume' for r in read_complete(f.root/'line_status.jsonl'))


@pytest.mark.parametrize('change',['missing','duplicate','late','wrong_player','not_passed'])
def test_selected_proof_ids_are_explicitly_bound_to_unique_prior_records(fixture,monkeypatch,change):
    f=fixture;enable(f,monkeypatch);rows=read_complete(f.root/'acceptance.jsonl')
    if change=='missing':rows=[r for r in rows if r.get('record_id')!='prod2']
    elif change=='duplicate':rows.append(copy.deepcopy(rows[-1]))
    elif change=='late':rows[-1]['finished_at']=to_iso(NOW)
    elif change=='wrong_player':rows[-1]['player']='gpt'
    else:rows[-1]['line_pass']=False
    (f.root/'acceptance.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    with pytest.raises(ValueError,match='incident_evidence'):R.incident_binding(f.root,f.row,f.binding)


@pytest.mark.parametrize('key',['baseline_record_id','runtime','run_config_sha256','wrapper_sha256',
    'pin_sha256','judge_version','cli_version','sources','proofs'])
def test_incomplete_capture_is_held_without_resuming(fixture,monkeypatch,key):
    f=fixture;enable(f,monkeypatch);binding=copy.deepcopy(f.binding);binding.pop(key)
    with pytest.raises(ValueError,match='binding'):
        R.recover(f.root,now=lambda:NOW,capture_fn=lambda *_:binding)
    assert not any(r['type']=='line_resume' for r in read_complete(f.root/'line_status.jsonl'))
    assert read_complete(f.root/'line_recovery.jsonl')[-1]['outcome']=='held'


@pytest.mark.parametrize('key',['baseline_record_id','runtime','run_config_sha256','wrapper_sha256',
    'pin_sha256','judge_version','cli_version','sources','proofs','incident_evidence'])
def test_incomplete_resume_binding_never_grants_retry(fixture,monkeypatch,key):
    f=fixture;enable(f,monkeypatch);R.recover(f.root,now=lambda:NOW,capture_fn=f.capture)
    rows=read_complete(f.root/'line_status.jsonl');rows[-1]['binding'].pop(key)
    (f.root/'line_status.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    assert R.retry_grant(f.root,[f.row],f.row,now=NOW) is None


@pytest.mark.parametrize('change',['runtime','config','baseline','wrapper','pin','proof'])
def test_environment_drift_after_admission_releases_reservation_without_start(fixture,monkeypatch,change):
    from dataclasses import replace
    from test_forecast import FakeAdapter,SNAP,_qsnap,POLICY
    from flab.config import load_run_config
    f=fixture;enable(f,monkeypatch);cfg=load_run_config()
    f.row.update(tier=1,question_type='binary',requested_model_id='gemini-4-argon-medium',requested_effort='medium');f.save_ledgers()
    R.recover(f.root,now=lambda:NOW,capture_fn=f.capture)
    adapter=FakeAdapter([]);work=f.project.parent/'empty';work.mkdir();admissions=[];released=[]
    def reserve(kind,aid):
        admissions.append((kind,aid))
        if change=='runtime':monkeypatch.setattr(F,'runtime_fingerprint',lambda *_:{'changed':'hash'})
        elif change=='config':monkeypatch.setattr(R,'load_run_config',lambda *_:{**cfg,'version':'changed'})
        elif change=='baseline':append_record(f.root/'acceptance.jsonl',{'type':'baseline_approved',
             'record_id':'replacement','approved_at':to_iso(NOW-timedelta(minutes=5))})
        elif change in ('wrapper','pin'):
            path=f.project/'launchd/bin/agy' if change=='wrapper' else f.root/'cli_pins/agy/1.4.12'
            path.write_text('changed')
        else:
            rows=read_complete(f.root/'acceptance.jsonl');rows[-1]['evidence_sha256']='changed'
            (f.root/'acceptance.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    ctx=F.Context(player='gemini',adapter=adapter,cfg=cfg,run_id='drift_test',preflight=F.Preflight('gemini'),
        now=lambda:NOW,forecasts_log=f.root/'forecasts.jsonl',audits_log=f.root/'audits.jsonl',
        line_status_log=f.root/'line_status.jsonl',quota_log=f.root/'quota.jsonl',raw_root=f.root/'raw',
        make_dir=lambda:work,remove_dir=lambda _:None,
        quota_snapshot=lambda **_:replace(_qsnap(.9),taken_at=to_iso(NOW)),quota_policy=POLICY,
        before_attempt=reserve,admission_outcome=lambda aid,started:released.append((aid,started)),continuous=True)
    snap={**SNAP,'question_id':123,'snapshot_id':'qs_one'}
    with F.Lock(f.root/'forecast.gemini.lock'),pytest.raises(F.QuestionDone,match='recovery grant'):
        F.run_attempt(ctx,snap,kind='rerun',prompt='synthetic',sch={},prev=f.row)
    assert len(admissions)==1 and released==[(admissions[0][1],False)]
    assert adapter.ran==0 and not read_complete(f.root/'forecast_attempt_starts.jsonl')
    assert read_complete(f.root/'forecasts.jsonl')[0]['validation_status']=='incomplete'


@pytest.mark.parametrize('field',['mode','tier','model','effort'])
def test_selected_production_identity_must_match_current_config(fixture,monkeypatch,field):
    f=fixture;enable(f,monkeypatch);rows=read_complete(f.root/'acceptance.jsonl')
    rows[-1][field]='changed'
    (f.root/'acceptance.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    with pytest.raises(ValueError,match='incident_production_identity'):
        R.incident_binding(f.root,f.row,f.binding)
