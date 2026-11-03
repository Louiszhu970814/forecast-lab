"""Offline maintenance contract: locking, budget, crash evidence and fail-closed recovery."""
import json
import os
from pathlib import Path
import signal
import shutil
from types import SimpleNamespace
from datetime import timedelta

import pytest

import autoload as A
import autoload_maintenance as M
import baseline as B
import forecast as F
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import utc_now, to_iso
from test_autoload import fx, _loaded_executor, _result


@pytest.fixture
def session(fx, monkeypatch):
    project = fx.home.parent/'project'
    shutil.copytree(Path(__file__).resolve().parents[1]/'config',project/'config')
    now = utc_now()
    campaign = {'campaign_id':'maintenance-test','budget_window_start':to_iso(now-timedelta(days=1)),
                'budget_window_end':to_iso(now+timedelta(days=6)),
                'weekly_attempt_caps':dict.fromkeys(B.PLAYERS,10)}
    (project/'config/cycle_campaign.json').write_text(json.dumps(campaign))
    snap = {'sources':{},'versions':dict.fromkeys(B.PLAYERS,'fake'), 'gemini_credits':0}
    data = project/'data'
    append_record(data/'acceptance.jsonl',{'type':'baseline_approved',**snap})
    monkeypatch.setattr(M.W,'require_active',lambda *a: None)
    monkeypatch.setattr(M.orphan,'no_process',lambda paths:True)
    calls = []
    def execute(req):
        calls.append(req.player)
        return _result(req),A.Declaration()
    session = M.Session(project,wait_s=0,snapshot=lambda: snap,
                        preflight=lambda p,t:F.Preflight(p,tier=t),execute=execute,
                        idle=lambda *a,**kw:None)
    monkeypatch.setattr(session,'identities',lambda:{'fake':'fixed'})
    monkeypatch.setattr(session,'protected_hashes',lambda:{'fake':'fixed'})
    return SimpleNamespace(s=session,fx=fx,calls=calls,campaign=campaign,now=now,snap=snap)


@pytest.mark.parametrize('name',M.LOCKS)
def test_every_busy_lock_refuses_and_releases_partial_locks(session,name):
    s=session.s
    with F.Lock(s.root/name):
        with pytest.raises(M.MaintenanceError,match='wait expired'):
            s.run(approved_by='Louis')
        for other in M.LOCKS:
            if other!=name:
                with F.Lock(s.root/other): pass
    assert session.calls==[] and not read_complete(s.log)
    assert not list(session.fx.home.rglob('SKILL.md'))


def test_success_has_three_calls_six_postchecks_and_no_approval(session):
    s=session.s
    result=s.run(approved_by='Louis')
    assert result['status']=='complete' and result['restore_ok']
    assert set(result['preflight'])=={f'{p}:{t}' for p in B.PLAYERS for t in (1,2)}
    assert session.calls==list(B.PLAYERS)
    rows=read_complete(s.root/'acceptance.jsonl')
    assert len([r for r in rows if r['type']=='baseline_approved'])==1
    starts=[r for r in rows if r['type']=='acceptance_call_started']
    assert len(starts)==3 and all(r['source']=='autoload' and r['boot_id'] for r in starts)
    assert len([r for r in rows if r['type']=='acceptance_call_finished'])==3
    assert A.unclosed(s.root/'acceptance.jsonl')==[]
    assert list(session.fx.home.iterdir())==[]
    with pytest.raises(M.MaintenanceError,match='consumed'):
        s.run(approved_by='Louis')


@pytest.mark.parametrize('reason',['pending','cap','deadline','quota','baseline','pin'])
def test_prechecks_stop_before_any_global_mutation(session,monkeypatch,reason):
    s=session.s
    def fail(*a,**kw): raise M.MaintenanceError(reason)
    if reason=='pending': s.idle=fail
    elif reason=='cap':
        append_record(s.root/'acceptance.jsonl',{'type':'acceptance_result','player':'gpt',
            'run_id':'old','model_call_count':10,'finished_at':to_iso(session.now)})
    elif reason=='deadline':
        c=session.campaign|{'budget_window_end':to_iso(session.now+timedelta(minutes=60))}
        (s.project/'config/cycle_campaign.json').write_text(json.dumps(c))
    elif reason=='quota': monkeypatch.setattr(s,'snapshot',fail)
    elif reason=='baseline': session.snap['versions']['gpt']='new'
    else: monkeypatch.setattr(s,'identities',fail)
    with pytest.raises(M.MaintenanceError): s.run(approved_by='Louis')
    assert session.calls==[] and not (s.root/'autoload_backups').exists()


def test_zero_call_failure_allows_exactly_one_retry(session,monkeypatch):
    s=session.s
    def fail(**kw):
        return {'record_id':'failed','restore_ok':True,'passed':False,'errors':[], 'locations':[]}
    monkeypatch.setattr(A,'run',fail)
    assert s.run(approved_by='Louis')['status']=='measurement_failed'
    assert s.run(approved_by='Louis')['status']=='measurement_failed'
    with pytest.raises(M.MaintenanceError,match='consumed'): s.run(approved_by='Louis')


@pytest.mark.parametrize('failure',['restore','scope','config'])
def test_failure_sets_stop_before_unlocking(session,monkeypatch,failure):
    s=session.s
    def execute(req):
        result=_result(req)
        if failure=='scope': result.error_type='tool_scope_violation'
        elif failure=='restore':
            with (session.fx.home/'.claude/CLAUDE.md').open('ab') as stream: stream.write(b'foreign')
        elif failure=='config': monkeypatch.setattr(s,'protected_hashes',lambda:{'fake':'changed'})
        return result,A.Declaration()
    s.execute=execute
    stop=s.stop
    def verify_stop(run_id):
        for name in M.LOCKS:
            with pytest.raises(F.PreflightError):
                with F.Lock(s.root/name): pass
        stop(run_id)
    monkeypatch.setattr(s,'stop',verify_stop)
    with pytest.raises(M.MaintenanceError): s.run(approved_by='Louis')
    assert (s.project/'config/STOP_SUBMIT').is_file()
    for name in M.LOCKS:
        with F.Lock(s.root/name): pass


def test_timeout_restores_and_finishes_call(fx):
    def execute(req):
        os.kill(os.getpid(),signal.SIGALRM)
        pytest.fail('alarm ignored')
    result=A.run(**fx.kwargs,executor=execute,timeout_s=30)
    assert result['restore_ok'] and not result['passed']
    assert result['errors']==[{'error':'TimeoutError'}]
    assert not signal.getitimer(signal.ITIMER_REAL)[0]
    assert not A.unclosed(fx.data/'acceptance.jsonl')


@pytest.mark.parametrize('fault',['missing_result','false_result','missing_manifest','false_closed','trailing','parent_link'])
def test_unclosed_requires_durable_result_and_manifest(tmp_path,fault):
    log=tmp_path/'acceptance.jsonl'; run='autoload_test'
    manifest=tmp_path/'autoload_backups'/run/'manifest.jsonl'
    append_record(log,{'type':'autoload_run_started','run_id':run,'manifest':str(manifest)})
    if fault!='missing_result':
        append_record(log,{'type':'autoload_result','run_id':run,'restore_ok':fault!='false_result'})
    if fault!='missing_manifest':
        append_record(manifest,{'type':'closed','run_id':run,'restore_ok':fault!='false_closed'})
    if fault=='trailing':
        with manifest.open('a') as f:f.write('{')
    if fault=='parent_link':
        moved=tmp_path/'moved'; manifest.parent.rename(moved); manifest.parent.symlink_to(moved)
    assert A.unclosed(log)==[run]


@pytest.mark.parametrize('crash_at',['before_write','after_write','after_truncate'])
def test_recovery_replays_write_intent_and_is_idempotent(fx,monkeypatch,crash_at):
    run='autoload_crash'; backup=fx.data/'autoload_backups'/run
    target=fx.home/'.claude/CLAUDE.md';target.parent.mkdir();target.write_bytes(b'original')
    p=A.Placement(A.locations(run,('claude',),fx.home,fx.base),backup)
    append_record(fx.data/'acceptance.jsonl',{'type':'autoload_run_started','run_id':run,
        'manifest':str(backup/'manifest.jsonl'),'attempt_base':str(fx.base),'players':['claude']})
    p.prepare()
    write=A._write
    def crash(path,content,**kw):
        if path==target:
            if crash_at!='before_write': write(path,content,**kw)
            raise KeyboardInterrupt()
        write(path,content,**kw)
    monkeypatch.setattr(A,'_write',crash)
    with pytest.raises(KeyboardInterrupt):p.place()
    if crash_at=='after_truncate':
        with target.open('r+b') as f:os.ftruncate(f.fileno(),len(b'original'))
    assert A.recover_placement(fx.data,run,home=fx.home)==[]
    assert A.recover_placement(fx.data,run,home=fx.home)==[]
    assert target.read_bytes()==b'original'


def test_retained_weekly_usage_matches_production_budget(tmp_path):
    from test_budget_handoff import campaigns,finished,reserve,NOW
    import continuous as Q
    _,campaign=campaigns(tmp_path,cap=4)
    Q.register(tmp_path,campaign,now=lambda:NOW)
    finished(tmp_path,'old',NOW-timedelta(hours=1))
    finished(tmp_path,'legacy',NOW-timedelta(hours=2),start=False)
    log=tmp_path/'acceptance.jsonl'
    call={'type':'acceptance_call_started','attempt_id':'call','run_id':'r','player':'claude',
          'started_at':to_iso(NOW-timedelta(minutes=2)),'source':'autoload'}
    append_record(log,call);append_record(log,call)
    append_record(log,{'type':'acceptance_result','run_id':'r','player':'claude',
                      'finished_at':to_iso(NOW),'model_call_count':3})
    used=M.weekly_usage(tmp_path,campaign,now=NOW)['claude']
    assert used==3==reserve(tmp_path,campaign,acceptance=log)['weekly_attempts_used']


@pytest.mark.parametrize('state',['model','request','intent','deadline','process'])
def test_idle_gate_detects_work_without_reading_forecast_contents(tmp_path,state):
    now=utc_now()
    if state in ('model','process'):
        row={'attempt_id':'a','workdir':str(tmp_path/'work')}
        append_record(tmp_path/'forecast_attempt_starts.jsonl',row)
        if state=='process':append_record(tmp_path/'forecasts.jsonl',{'attempt_id':'a'})
    elif state=='request':append_record(tmp_path/'submissions.jsonl',{'request_id':'r','status':'unknown'})
    elif state=='intent':append_record(tmp_path/'submissions.jsonl',{'intent_id':'i','status':'intent'})
    else:append_record(tmp_path/'questions.jsonl',{'question_id':7,'supported':True,'status':'open',
        'scheduled_close_time':to_iso(now+timedelta(minutes=40))})
    with pytest.raises(M.MaintenanceError):
        M.require_idle(tmp_path,now=now,horizon_s=900,process_check=lambda paths:False)


def test_authorized_cli_delegates_to_maintenance(fx,monkeypatch):
    seen=[]
    monkeypatch.setattr(M.Session,'run',lambda self,**kw:seen.append(kw) or {'status':'complete'})
    assert A.main(['run','--approved-by','Louis','--i-understand-this-edits-global-files'])==0
    assert seen==[{'approved_by':'Louis'}]


def interrupted(session,monkeypatch,stage='placed'):
    s=session.s;fx=session.fx;run='autoload_recovery'
    target=fx.home/'.claude/CLAUDE.md';target.parent.mkdir();target.write_text('original')
    p=A.Placement(A.locations(run,tuple(M.B.PLAYERS),fx.home,fx.base),s.root/'autoload_backups'/run)
    monkeypatch.setattr(M.orphan,'boot_id',lambda:'test-boot')
    append_record(s.root/'acceptance.jsonl',{'type':'autoload_run_started','run_id':run,
        'manifest':str(p.backup_dir/'manifest.jsonl'),'attempt_base':str(fx.base),
        'players':list(M.B.PLAYERS),'pid':os.getpid(),'boot_id':'test-boot'})
    s.event('maintenance_started',run_id=run,identities=s.identities(),protected_sha256=s.protected_hashes())
    if stage=='managed':append_record(p.backup_dir/'manifest.jsonl',{'type':'managed','run_id':run,'version':A.VERSION})
    elif stage in ('original','placed'):
        p.prepare()
        if stage=='placed':p.place()
    return run,p,target


@pytest.mark.parametrize('stage',['missing','managed','original','placed'])
def test_controller_recovery_is_idempotent_at_every_preparation_stage(session,monkeypatch,stage):
    s=session.s;run,p,target=interrupted(session,monkeypatch,stage)
    s.stop(run)
    record=s.recover(run)
    assert record['restore_ok'] and not record['passed']
    before=(s.root/'acceptance.jsonl').read_bytes()
    assert s.recover(run)==record and (s.root/'acceptance.jsonl').read_bytes()==before
    assert target.read_text()=='original' and not A.unclosed(s.root/'acceptance.jsonl')
    assert not (s.project/'config/STOP_SUBMIT').exists()
    assert len([r for r in read_complete(s.log) if r['type']=='maintenance_recovered'])==1


def test_recovery_cleans_only_registered_workdir_and_closes_call(session,monkeypatch):
    s=session.s;run,p,target=interrupted(session,monkeypatch)
    base=session.fx.base;base.mkdir();work=base/('a'*32);work.mkdir();(work/'temporary').write_text('fake')
    other=base/'unrelated';other.mkdir()
    append_record(s.root/'acceptance.jsonl',{'type':'acceptance_call_started','run_id':run,
        'attempt_id':'probe','player':'claude','source':'autoload','workdir':str(work),'raw_dir':str(s.root/'raw')})
    record=s.recover(run)
    assert record['restore_ok'] and not work.exists() and other.is_dir()
    s.recover(run)
    finished=[r for r in read_complete(s.root/'acceptance.jsonl') if r['type']=='acceptance_call_finished']
    assert len(finished)==1 and finished[0]['source']=='autoload' and finished[0]['error_type']=='interrupted'
    assert not any(r['type']=='acceptance_result' for r in read_complete(s.root/'acceptance.jsonl'))


@pytest.mark.parametrize('obstacle',['live_pid','unknown_boot','child','preflight','foreign_stop'])
def test_recovery_retains_stop_and_preserves_evidence_when_unproven(session,monkeypatch,obstacle):
    s=session.s;run,p,target=interrupted(session,monkeypatch)
    if obstacle=='live_pid':
        monkeypatch.setattr(os,'getpid',lambda:-1)
        monkeypatch.setattr(os,'kill',lambda pid,sig:None)
    elif obstacle=='unknown_boot':monkeypatch.setattr(M.orphan,'boot_id',lambda:None)
    elif obstacle=='child':
        append_record(s.root/'acceptance.jsonl',{'type':'acceptance_call_started','run_id':run,
            'attempt_id':'probe','player':'claude','source':'autoload','workdir':str(session.fx.base/('a'*32))})
        monkeypatch.setattr(M.orphan,'no_process',lambda paths:False)
    elif obstacle=='preflight':s.preflight_fn=lambda p,t:F.Preflight(p,tier=t,problems=['synthetic blocker'])
    else:(s.project/'config/STOP_SUBMIT').write_text('set by another owner\n')
    if obstacle=='foreign_stop':
        s.recover(run)
        assert (s.project/'config/STOP_SUBMIT').read_text()=='set by another owner\n'
    else:
        with pytest.raises(M.MaintenanceError):s.recover(run)
        assert (s.project/'config/STOP_SUBMIT').exists()


def test_host_conflict_resolution_requires_canary_removal(session,monkeypatch):
    s=session.s;run,p,target=interrupted(session,monkeypatch)
    replacement=target.with_suffix('.tmp');replacement.write_bytes(target.read_bytes());replacement.replace(target)
    with pytest.raises(M.MaintenanceError):s.recover(run)
    with pytest.raises(ValueError,match='canary remains'):s.recover(run,resolved_by='Louis')
    target.write_text('original')
    result=s.recover(run,resolved_by='Louis')
    assert result['restore_ok'] and not result['passed'] and result['byte_exact'] is False
    assert result['resolution']=='host_attested' and target.read_text()=='original'
    assert read_complete(p.backup_dir/'manifest.jsonl')[-1]['resolved_by']=='Louis'


def test_recovery_ignores_platform_intents_and_near_deadlines(tmp_path):
    now=utc_now()
    append_record(tmp_path/'submissions.jsonl',{'status':'intent','intent_id':'pending'})
    append_record(tmp_path/'questions.jsonl',{'question_id':1,'supported':True,'status':'open',
        'scheduled_close_time':to_iso(now+timedelta(minutes=1))})
    M.require_idle(tmp_path,now=now,horizon_s=0,recovering='autoload_recovery')
    with pytest.raises(M.MaintenanceError):M.require_idle(tmp_path,now=now,horizon_s=0)


def test_old_unmanaged_autoload_result_does_not_break_preflight(tmp_path):
    append_record(tmp_path/'acceptance.jsonl',{'type':'autoload_result','passed':True})
    assert A.unclosed(tmp_path/'acceptance.jsonl')==[]


def test_deadline_denial_is_before_started_and_allows_zero_call_retry(session,monkeypatch):
    s=session.s;run=A.run
    time=[0.0];s.monotonic=lambda:time[0]
    def elapsed(**kw):
        time[0]+=2000
        return run(**kw)
    monkeypatch.setattr(A,'run',elapsed)
    for _ in range(2):
        result=s.run(approved_by='Louis')
        assert result['status']=='measurement_failed'
    assert session.calls==[]
    assert not any(r['type']=='acceptance_call_started' for r in read_complete(s.root/'acceptance.jsonl'))


def test_real_snapshot_and_identity_methods_validate_pin_and_quota(session,monkeypatch):
    s=session.s;fx=session.fx
    pin=s.root/'cli_pins/claude/2.1.316';pin.parent.mkdir(parents=True);pin.write_bytes(b'official fake pin')
    wrapper=s.project/'launchd/bin/claude';wrapper.parent.mkdir(parents=True);wrapper.write_text('fake wrapper')
    local=fx.home/'.local/bin';local.mkdir(parents=True)
    for p in [wrapper,local/'codex',local/'agy']:
        if not p.exists():p.write_text('fake official entry')
        p.chmod(0o700)
    versions=dict.fromkeys(B.PLAYERS,'1');versions['claude']='2.1.316'
    monkeypatch.setattr(B,'cli_versions',lambda:versions)
    monkeypatch.setattr(B,'take_snapshot',lambda **kw:B.Snapshot(to_iso(utc_now()),{},kw['versions'],kw['gemini_credits']))
    s.snapshot_fn=None
    old_path=os.environ['PATH']
    with s.production_path():
        actual=M.Session.identities(s)
        assert actual['claude']['path']==str(wrapper) and actual['claude']['pinned_sha256']
        assert s.snapshot(admit=True)['versions']['claude']=='2.1.316'
        monkeypatch.setattr(M.quota,'manual_admission',lambda *a,**kw:('denied',{}))
        with pytest.raises(M.MaintenanceError,match='admission'):s.snapshot(admit=True)
    assert os.environ['PATH']==old_path
