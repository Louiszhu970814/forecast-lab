from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest

import practice as P
import forecast as F
import budget as B
import validate as V
from flab.external_usage import practice_usage, usage
from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.practice_contract import execution_cutoff
from flab.timeutil import to_iso
from poll_questions import TournamentIndex
from test_forecast import NOW, FakeAdapter, _good
from flab.config import load_run_config, load_quota_policy


@pytest.fixture
def post():
    return {"id": 10, "title": "Synthetic closed event", "status": "closed",
        "projects": {"tournament": [{"id": 33121}]}, "question": {
        "id": 20, "type": "binary", "status": "closed", "description": "Synthetic background.",
        "resolution_criteria": "The synthetic event must occur at noon tomorrow.", "fine_print": "",
        "options": None, "my_forecasts": {"history": [], "latest": None, "score_data": None}, "open_time": to_iso(NOW-timedelta(days=2)),
        "scheduled_close_time": to_iso(NOW-timedelta(days=1)),
        "scheduled_resolve_time": to_iso(NOW+timedelta(days=1)), "resolution": None}}


@pytest.mark.parametrize("field,value", [("status", "open"), ("status", "upcoming"),
    ("status", None), ("scheduled_close_time", to_iso(NOW)), ("description", None),
    ("resolution_criteria", ""), ("scheduled_close_time", "bad")])
def test_platform_missing_and_reopen_fail_closed(post, field, value):
    post["question"][field] = value
    with pytest.raises(ValueError): P.closed_snapshot(post, TournamentIndex(), now=NOW)


@pytest.mark.parametrize("overlap", ["post", "question", "project", "title"])
def test_synced_copies_excluded(post, overlap):
    idx = TournamentIndex()
    if overlap == "post": idx.post_ids.add(10)
    if overlap == "question": idx.question_ids.add(20)
    if overlap == "project":
        idx.project_ids.add(30); post["projects"]["tournament"].append({"id": 30})
    if overlap == "title":
        from poll_questions import title_key
        idx.title_keys.add(title_key(post["title"]))
    with pytest.raises(ValueError, match="synced"): P.closed_snapshot(post, idx, now=NOW)


def test_closed_membership_does_not_exclude_itself(post):
    assert P.closed_snapshot(post, TournamentIndex(project_ids={33121}), now=NOW)["question_id"] == 20


def test_resolution_and_community_never_in_prompt_snapshot(post):
    post["question"].update(resolution=1, aggregations={"community": .99}, my_forecasts={"probability": .6})
    snap = P.closed_snapshot(post, TournamentIndex(), now=NOW)
    assert not {"resolution", "aggregations", "my_forecasts"} & snap.keys()
    assert P.timing_classification(post)["group"] == "retrospective_diagnostic"


def test_event_occurred_but_unresolved_is_not_clean_prospective(post):
    post["question"]["scheduled_resolve_time"] = to_iso(NOW+timedelta(days=30))
    classification = P.timing_classification(post)
    assert classification["event_time"] is None
    assert classification["clean_evaluation_eligible"] is False
    assert classification["group"] == "outcome_timing_unknown_diagnostic"


def test_lifecycle_absence_requires_real_ledgers(tmp_path):
    with pytest.raises(ValueError, match="missing"): P.lifecycle_evidence(tmp_path, 20)
    for name in P.LIFECYCLE: (tmp_path/name).touch()
    assert len(P.lifecycle_evidence(tmp_path, 20)) == len(P.LIFECYCLE)
    append_record(tmp_path/"question_queue.jsonl", {"question_id":20, "action":"closed"})
    with pytest.raises(ValueError, match="formal lifecycle"): P.lifecycle_evidence(tmp_path, 20)


@pytest.fixture
def registered(tmp_path, post):
    shared = tmp_path/"data"; root=shared/"practice/batch"; root.mkdir(parents=True)
    snap=P.closed_snapshot(post,TournamentIndex(),now=NOW)
    manifest={"batch":"batch", "selected":[{"question_id":20, "post_id":10,"snapshot":snap,
              "group":"outcome_timing_unknown_diagnostic"}],
              "expires_at":to_iso(NOW+timedelta(days=1))}
    append_record(root/"manifest.jsonl",manifest)
    append_record(shared/"practice_batches.jsonl",{"batch":"batch","manifest_sha256":hash_obj(manifest)})
    return shared,root,snap,manifest


def practice_context(registered, adapter=None):
    shared,root,snap,manifest=registered
    line=root/"20/claude";line.mkdir(parents=True,exist_ok=True)
    ctx=F.Context(player="claude",adapter=adapter, cfg=load_run_config(), run_id="test",preflight=F.Preflight("claude"),
        now=lambda:NOW, forecasts_log=line/"forecasts.jsonl",audits_log=line/"audits.jsonl",
        line_status_log=shared/"line_status.jsonl",quota_log=shared/"quota.jsonl",raw_root=line/"raw",
        before_attempt=lambda kind,aid:None,recheck_inputs=lambda:None,admission_outcome=lambda aid,started:None,
        closed_practice={"batch":"batch","batch_path":str(root),"started_at":to_iso(NOW),"cutoff":to_iso(NOW+timedelta(minutes=10))})
    return ctx


def test_actual_engine_uses_practice_clock_without_changing_close(registered,tmp_path):
    adapter=FakeAdapter([{"structured":_good(question_id=20)}])
    ctx=practice_context(registered,adapter)
    work=tmp_path/"empty";work.mkdir()
    ctx.make_dir=lambda:work;ctx.remove_dir=lambda p:None
    snap=registered[2];before=deepcopy(snap)
    rec=F.run_attempt(ctx,snap,kind="initial",prev=None,prompt=F.forecast_prompt(snap,"2026-11-01"),sch=V.question_schema(snap))
    assert snap == before and rec["validation_status"] == "valid"
    assert rec["purpose"] == "closed_practice" and rec["official_submission_eligible"] is False
    assert rec["deadline_utc"] < rec["started_at_utc"] < rec["decision_deadline_utc"]
    assert adapter.ran == 1
    assert not (registered[0]/"forecasts.jsonl").exists()
    assert F.eligible(snap,ctx.cfg,NOW).startswith("status")


@pytest.mark.parametrize("mutation", ["formal_path","second_tier","repair","no_guard","unregistered","changed_snapshot","overlong_clock"])
def test_practice_clock_cannot_bypass_formal_contract(registered,mutation):
    shared,root,snap,_=registered;ctx=practice_context(registered);kind="initial"
    if mutation == "formal_path":ctx.forecasts_log=shared/"forecasts.jsonl"
    if mutation == "second_tier":ctx.tier=2
    if mutation == "repair":kind="format_repair"
    if mutation == "no_guard":ctx.recheck_inputs=None
    if mutation == "unregistered":(shared/"practice_batches.jsonl").unlink()
    if mutation == "changed_snapshot":snap={**snap,"status":"open"}
    if mutation == "overlong_clock":ctx.closed_practice["cutoff"]=to_iso(NOW+timedelta(hours=1))
    with pytest.raises(ValueError):execution_cutoff(ctx,snap,kind,None)


def budget_for(shared):
    return B.Budget(shared,{"campaign_id":"test","budget_window_start":to_iso(NOW-timedelta(days=1)),
        "budget_window_end":to_iso(NOW+timedelta(days=1)),"weekly_attempt_caps":{"claude":3},
        "expected_new_questions":0,"repair_reserve":0},load_quota_policy(),"run",now=lambda:NOW)


def admission(shared,root,b,manifest,aid="a"):
    request={"player":"claude","tier":1,"question_id":20,"attempt_id":aid,"attempt_kind":"initial"}
    d=b.reserve(request,pending_tier1=0,expected_new=0,repair_reserve=0)
    P.register_admission(shared,b,root/"20/claude",request,manifest,d["decision_id"])
    return d


def test_unknown_practice_blocks_formal_budget_and_is_charged(registered):
    shared,root,_,m=registered;b=budget_for(shared);admission(shared,root,b,m)
    b.run_id="new";b.recover()
    result=b.reserve({"player":"claude","tier":1,"question_id":77,"attempt_id":"b"},pending_tier1=0,expected_new=0,repair_reserve=0)
    assert result["reason"] == "unresolved_or_running_attempt"
    assert result["weekly_attempts_used"] == 1


def test_completed_practice_charged_without_contaminating_formal(registered):
    shared,root,_,m=registered;b=budget_for(shared);admission(shared,root,b,m)
    line=root/"20/claude";line.mkdir(parents=True)
    append_record(line/"forecast_attempt_starts.jsonl",{"attempt_id":"a"})
    append_record(line/"forecasts.jsonl",{"attempt_id":"a","probability":.42})
    P.finish_accounting(shared,line,"claude","a",b)
    result=b.reserve({"player":"claude","tier":1,"question_id":77,"attempt_id":"b"},pending_tier1=0,expected_new=0,repair_reserve=0)
    assert result["allowed"] and result["weekly_attempts_used"]==1
    assert not (shared/"forecasts.jsonl").exists()


def test_no_start_release_never_allows_question_rerun(registered):
    shared,root,_,m=registered;b=budget_for(shared);admission(shared,root,b,m)
    P.finish_accounting(shared,root/"20/claude","claude","a",b)
    assert practice_usage(shared)[1]["a"]["started"] is False
    with pytest.raises(F.QuestionDone,match="duplicate"):
        P.register_admission(shared,b,root/"other",{"player":"claude","tier":1,"question_id":20,"attempt_id":"b","attempt_kind":"initial"},m,"d")


def test_unknown_start_cannot_be_closed_for_free(registered):
    shared,root,_,m=registered;b=budget_for(shared);admission(shared,root,b,m)
    line=root/"20/claude";line.mkdir(parents=True)
    append_record(line/"forecast_attempt_starts.jsonl",{"attempt_id":"a"})
    P.finish_accounting(shared,line,"claude","a",b)
    assert not practice_usage(shared)[1]


def test_guard_propagates_quota_and_confirmed_exposure(registered):
    shared,root,_,_=registered;line=root/"20/claude";line.mkdir(parents=True)
    append_record(line/"forecasts.jsonl",{"attempt_id":"a","player":"claude","question_id":20,
        "finished_at_utc":to_iso(NOW),"pipeline_version":F.PIPELINE_VERSION,"error_type":"quota_exhausted",
        "quota_retry_at_utc":to_iso(NOW+timedelta(minutes=30)),"model_check":"verified","audit_flags":["leak_confirmed"]})
    P.propagate_safety(shared,line,"claude","a")
    P.propagate_safety(shared,line,"claude","a")
    assert len(read_complete(shared/"practice_guards.jsonl"))==1
    assert F.line_stopped(shared/"line_status.jsonl","claude",shared/"forecasts.jsonl",now=NOW)


def test_search_summary_exposure_is_kept_by_real_engine(registered,tmp_path):
    adapter=FakeAdapter([{"structured":_good(question_id=20),"raw":"Search summary at metaculus.com: answer visible"}])
    ctx=practice_context(registered,adapter);work=tmp_path/"empty";work.mkdir();ctx.make_dir=lambda:work;ctx.remove_dir=lambda p:None
    rec=F.run_attempt(ctx,registered[2],kind="initial",prev=None,prompt="test",sch=V.question_schema(registered[2]))
    assert "suspected_leak" in rec["audit_flags"]
    assert not P.timing_classification({"question":{}})["clean_evaluation_eligible"]


def test_corrupt_or_answer_containing_metadata_fails_closed(registered):
    shared,root,_,m=registered;b=budget_for(shared);admission(shared,root,b,m)
    with (shared/"practice_usage.jsonl").open("a") as f:f.write('{"probability":0.99}')
    with pytest.raises(ValueError):usage(shared)


def test_no_network_write_transport():
    import ast
    tree=ast.parse(Path(P.__file__).read_text())
    assert not any(isinstance(n,ast.ImportFrom) and n.module in ("submit", "submit_transport") for n in ast.walk(tree))
    assert not any(isinstance(n,ast.Attribute) and n.attr in ("post", "submit_forecast", "submit_comment") for n in ast.walk(tree))


@pytest.mark.parametrize("status,opens", [("open", -1), ("upcoming", 10), ("unknown", 100)])
def test_formal_work_yields_before_practice(tmp_path, monkeypatch, status, opens):
    class Client:
        def get(self,path):return [{"id":55,"slug":"minibench","is_ongoing":True,"start_date":to_iso(NOW-timedelta(days=1))}]
    def poll(client,pid,**kw):
        return {"tournament_id":pid,"timing":[{"status":status,"open_time":to_iso(NOW+timedelta(minutes=opens))}] if pid==55 else []}
    monkeypatch.setattr(P,"poll",poll)
    with pytest.raises(ValueError):P.fresh_index(Client(),tmp_path,now=NOW)


def test_prepare_masked_population_freezes_zero_calls(tmp_path,post,monkeypatch):
    shared=tmp_path/"data";root=shared/"practice/empty";root.mkdir(parents=True)
    post["question"]["description"]=None
    class Client:
        def list_posts(self,*a,**kw):return [post]
        def get_post(self,*a):raise AssertionError("masked population requires no extra fetch")
    c={"campaign_id":"test","budget_window_end":to_iso(NOW+timedelta(days=1))}
    monkeypatch.setattr(P,"campaign_settings",lambda project:(c,{},{}))
    monkeypatch.setattr(P,"fresh_index",lambda *a,**kw:TournamentIndex())
    monkeypatch.setattr(P,"utc_now",lambda:NOW)
    result=P.prepare(root,tmp_path,client=Client())
    assert result["selected"] == result["model_calls"] == 0
    assert P.report(root,tmp_path)["admitted"] == 0
    with pytest.raises(ValueError,match="already frozen"):P.prepare(root,tmp_path,client=Client())
    with pytest.raises(ValueError,match="selected"):P.run(root,20,tmp_path,client=Client())
    assert not (shared/"forecasts.jsonl").exists()


def test_prepare_freezes_selection_and_time_holdout(tmp_path,post,monkeypatch):
    shared=tmp_path/"data";root=shared/"practice/batch";root.mkdir(parents=True)
    for name in P.LIFECYCLE:(shared/name).touch()
    posts=[]
    for i in range(5):
        p=deepcopy(post);p["id"]+=i;p["question"]["id"]+=i
        p["question"]["scheduled_close_time"]=to_iso(NOW-timedelta(days=5-i));posts.append(p)
    class Client:
        def list_posts(self,*a,**kw):return posts
        def get_post(self,pid):return next(p for p in posts if p["id"]==pid)
    c={"campaign_id":"test","budget_window_end":to_iso(NOW+timedelta(days=1))}
    monkeypatch.setattr(P,"campaign_settings",lambda project:(c,{},{}))
    monkeypatch.setattr(P,"fresh_index",lambda *a,**kw:TournamentIndex())
    monkeypatch.setattr(P,"utc_now",lambda:NOW)
    result=P.prepare(root,tmp_path,client=Client());assert result["selected"]==3 and result["holdout"]==1
    m=P.manifest_for(root,shared)
    assert [r["question_id"] for r in m["selected"]]==[20,21,22]
    assert m["holdout"]==[{"post_id":14,"question_id":24}]
    assert m["remaining_unused"]==[23]


def test_nine_admissions_cannot_expand(registered):
    shared,root,_,m=registered;b=budget_for(shared)
    for i in range(9):
        row={"type":"practice_admitted","version":P.VERSION,"at":to_iso(NOW),"attempt_id":str(i),
             "player":"gpt","question_id":i,"batch":"previous","batch_path":str(root),
             "campaign_id":"test","protected_attempts":22,"decision_id":str(i)}
        append_record(shared/"practice_usage.jsonl",row)
    with pytest.raises(F.QuestionDone,match="contract"):
        P.register_admission(shared,b,root,{"player":"claude","question_id":20,"attempt_id":"last","tier":1,"attempt_kind":"initial"},m,"d")


@pytest.mark.parametrize("expired,absent", [(False,True),(True,False),(True,True)])
def test_recovery_keeps_cost_and_does_not_call_models(registered,tmp_path,monkeypatch,expired,absent):
    from flab import orphan
    shared,root,_,m=registered;b=budget_for(shared);admission(shared,root,b,m)
    (tmp_path/"config").mkdir();(tmp_path/"config/cycle_campaign.json").write_text(__import__('json').dumps(b.campaign))
    line=root/"20/claude";line.mkdir(parents=True)
    append_record(line/"forecast_attempt_starts.jsonl",{"type":"forecast_attempt_started","attempt_id":"a",
        "player":"claude","question_id":20,"tier":1,"attempt_kind":"initial","started_at_utc":to_iso(NOW),
        "run_id":"old","pipeline_version":F.PIPELINE_VERSION})
    monkeypatch.setattr(orphan,"expired",lambda *a:expired)
    monkeypatch.setattr(orphan,"no_process",lambda *a:absent)
    result=P.recover(root,tmp_path)
    assert result["model_calls"]==0
    if expired and absent:
        assert practice_usage(shared)[1]["a"]["started"] is True
        assert read_complete(line/"forecasts.jsonl")[0]["error_type"]=="interrupted"
    else:assert result["pending"]==["a"]


@pytest.mark.parametrize("error", [None,"quota_exhausted","model_mismatch"])
def test_real_practice_line_integrates_engine_budget_and_guards(registered,tmp_path,monkeypatch,error):
    shared,root,snap,m=registered
    cfg,policy=load_run_config(),load_quota_policy();c=budget_for(shared).campaign
    c["weekly_attempt_caps"]["claude"]=130;c["expected_new_questions"]=10;c["repair_reserve"]=2
    adapter=FakeAdapter([{"structured":_good(question_id=20),"error_type":error,
                          "model_check":"mismatch" if error=="model_mismatch" else "verified"}])
    monkeypatch.setattr(P,"utc_now",lambda:NOW)
    monkeypatch.setattr(P.baseline,"ACCEPTANCE_LOG",tmp_path/"empty")
    monkeypatch.setattr(P,"load_players_enabled",lambda:{"claude":True})
    clock={"batch":"batch","batch_path":str(root),"started_at":to_iso(NOW),"cutoff":to_iso(NOW+timedelta(minutes=10))}
    result=P.run_line("claude",m["selected"][0],root,shared,c,cfg,policy,F.Preflight("claude"),adapter,m,lambda:None,clock)
    assert adapter.ran==1
    a,f=practice_usage(shared);assert set(a)==set(f) and len(a)==1
    rec=read_complete(root/"20/claude/forecasts.jsonl")[0]
    reserved=next(r for r in read_complete(shared/"budget_events.jsonl") if r["type"]=="budget_reserved")
    assert reserved["protected_attempts"]==22
    assert rec["admission_decision_id"]==reserved["decision_id"]==next(iter(a.values()))["decision_id"]
    assert not (shared/"forecasts.jsonl").exists()
    if error:assert F.line_stopped(shared/"line_status.jsonl","claude",now=NOW)
    else:assert result["adoptable"]


@pytest.mark.parametrize("resolution,status,count", [("yes","resolved",1),(None,"closed",0),
    ("ambiguous","resolved",0),("annulled","resolved",0),("yes","closed",0)])
def test_local_scoring_requires_official_resolution_and_stays_separate(registered,tmp_path,post,monkeypatch,resolution,status,count):
    ctx=practice_context(registered,FakeAdapter([{"structured":_good(question_id=20)}]))
    work=tmp_path/"empty";work.mkdir();ctx.make_dir=lambda:work;ctx.remove_dir=lambda p:None
    F.run_attempt(ctx,registered[2],kind="initial",prev=None,prompt="test",sch=V.question_schema(registered[2]))
    post["question"]["resolution"]=resolution;post["status"]=post["question"]["status"]=status
    class Client:
        def get_post(self,pid):assert pid==10;return post
    monkeypatch.setattr(P,"fresh_index",lambda *a,**k:TournamentIndex())
    result=P.score(registered[1],tmp_path,client=Client())
    assert result["questions"][0]["scored_outputs"]==count
    rows=read_complete(registered[1]/"scores.jsonl")
    assert rows[0]["official_peer_score"] is None and not rows[0]["clean_evaluation_eligible"]
    assert rows[0]["missing_players"]==["gemini","gpt"]
    assert not (registered[0]/"resolutions.jsonl").exists()


def test_budget_holds_real_capacity_for_practice(registered):
    shared,_,_,_=registered;b=budget_for(shared)
    result=b.reserve({"player":"claude","tier":1,"question_id":20,"attempt_id":"a"},
        pending_tier1=0,expected_new=0,repair_reserve=0,advisory_protected=3)
    assert not result["allowed"] and result["reason"]=="weekly_attempt_capacity_reserved_or_exhausted"

@pytest.mark.parametrize('value', [None, 'missing', {}, {'history':[{}],'latest':None}, {'history':[],'latest':{}}])
def test_remote_history_is_required_and_empty(post,value):
    if value=='missing':post['question'].pop('my_forecasts')
    else:post['question']['my_forecasts']=value
    with pytest.raises(ValueError):P.remote_unforecasted(post)


def test_post_id_and_readback_history_exclude(tmp_path):
    for name in P.LIFECYCLE:(tmp_path/name).touch()
    append_record(tmp_path/'submission_readbacks.jsonl',{'post_id':10})
    with pytest.raises(ValueError,match='formal lifecycle'):P.lifecycle_evidence(tmp_path,20,10)


def test_reservation_without_admission_is_released(registered):
    shared,root,_,m=registered;b=budget_for(shared)
    b.reserve({'player':'claude','tier':1,'question_id':20,'attempt_id':'a'},pending_tier1=0,expected_new=0,repair_reserve=0)
    P.finish_accounting(shared,root/'20/claude','claude','a',b)
    assert b.reserve({'player':'claude','tier':1,'question_id':77,'attempt_id':'b'},pending_tier1=0,expected_new=0,repair_reserve=0)['allowed']


@pytest.mark.usefixtures("legacy_stops")  # pre-D149 stop rule
def test_leak_threshold_is_existing_two_not_one(registered):
    shared,root,_,m=registered;line=root/'20/claude';line.mkdir(parents=True)
    for i in range(2):
        append_record(line/'forecasts.jsonl',{'attempt_id':str(i),'player':'claude','question_id':20,
            'finished_at_utc':to_iso(NOW),'pipeline_version':F.PIPELINE_VERSION,'model_check':'verified',
            'error_type':None,'own_audit_flags':['leak_confirmed']})
        P.propagate_safety(shared,line,'claude',str(i))
        assert bool(F.line_stopped(shared/'line_status.jsonl','claude',shared/'forecasts.jsonl',now=NOW)) == bool(i)
    assert not read_complete(shared/'line_status.jsonl')


@pytest.mark.parametrize('state',['absent','empty','corrupt'])
def test_formal_budget_and_guard_external_regression(tmp_path,state):
    if state!='absent':
        (tmp_path/'practice_usage.jsonl').write_text('{}\n' if state=='corrupt' else '')
        (tmp_path/'practice_guards.jsonl').write_text('{}\n' if state=='corrupt' else '')
    b=budget_for(tmp_path)
    def reserve():return b.reserve({'player':'claude','tier':1,'question_id':77,'attempt_id':'b'},pending_tier1=0,expected_new=0,repair_reserve=0)
    if state=='corrupt':
        with pytest.raises(ValueError):reserve()
        with pytest.raises(ValueError):F.line_stopped(tmp_path/'line_status.jsonl','claude',now=NOW)
    else:
        assert reserve()['allowed']
        assert not F.line_stopped(tmp_path/'line_status.jsonl','claude',now=NOW)


def test_complete_metadata_cannot_contain_answer(registered):
    shared,root,_,m=registered;b=budget_for(shared);admission(shared,root,b,m)
    r=read_complete(shared/'practice_usage.jsonl')[0];r['probability']=.99
    (shared/'practice_usage.jsonl').write_text(__import__('json').dumps(r)+'\n')
    with pytest.raises(ValueError):practice_usage(shared)


def test_score_uses_frozen_id_and_allows_resolution_text_edits(registered,tmp_path,post,monkeypatch):
    shared,root,snap,m=registered;ctx=practice_context(registered,FakeAdapter([{'structured':_good(question_id=20)}]))
    F.run_attempt(ctx,snap,kind='initial',prev=None,prompt='test',sch=V.question_schema(snap))
    agg=P.aggregate.build_aggregate(snap,read_complete(ctx.forecasts_log))
    append_record(root/'aggregates.jsonl',agg)
    post['question'].update(status='resolved',resolution='yes',description='Resolved: explanation added.')
    post['status']='resolved'
    class Client:
        def get_post(self,pid):return post
    def deny(*a,**k):raise AssertionError('scoring must not use formal locks/index')
    monkeypatch.setattr(P,'practice_locks',deny);monkeypatch.setattr(P,'fresh_index',deny)
    P.score(root,tmp_path,client=Client())
    r=read_complete(root/'scores.jsonl')[0]
    assert len(r['scores'])==2 and r['scores'][1]['source_id']==agg['record_id']
    assert r['text_changed_since_prediction']


def setup_full_run(tmp_path,post,monkeypatch):
    shared=tmp_path/'data';root=shared/'practice/full';root.mkdir(parents=True)
    for name in P.LIFECYCLE:(shared/name).touch()
    c=budget_for(shared).campaign
    c['weekly_attempt_caps']={p:130 for p in P.PLAYERS}
    cfg,policy=load_run_config(),load_quota_policy()
    monkeypatch.setattr(P,'campaign_settings',lambda project:(c,cfg,policy))
    monkeypatch.setattr(P,'fresh_index',lambda *a,**kw:TournamentIndex())
    monkeypatch.setattr(P,'utc_now',lambda:NOW)
    monkeypatch.setattr(P.baseline,'ACCEPTANCE_LOG',shared/'baseline_acceptance.jsonl')
    monkeypatch.setattr(P,'load_players_enabled',lambda:{p:True for p in P.PLAYERS})
    class Client:
        def list_posts(self,*a,**k):return [post]
        def get_post(self,pid):return post
    client=Client()
    assert P.prepare(root,tmp_path,client=client)['selected']==1
    adapters={p:FakeAdapter([{'structured':_good(question_id=20)}]) for p in P.PLAYERS}
    original=P.importlib.import_module
    monkeypatch.setattr(P.importlib,'import_module',lambda name:adapters[name.split('.')[1]] if name.startswith('adapters.') else original(name))
    monkeypatch.setattr(F,'preflight',lambda p,**k:F.Preflight(p))
    from test_forecast import _qsnap
    snapshots=iter([_qsnap(.99),_qsnap(.98)])
    context=F.Context
    monkeypatch.setattr(F,'Context',lambda **kw:context(**kw,quota_snapshot=lambda **opts:next(snapshots)))
    return root,shared,client,adapters


def test_complete_run_three_lines_once_private_report(tmp_path,post,monkeypatch):
    root,shared,client,adapters=setup_full_run(tmp_path,post,monkeypatch)
    result=P.run(root,20,tmp_path,client=client)
    assert result['aggregate_status']=='ready'
    assert all(a.ran==1 for a in adapters.values())
    assert len(practice_usage(shared)[0])==3
    with pytest.raises(ValueError,match='already attempted'):P.run(root,20,tmp_path,client=client)
    assert not (shared/'forecasts.jsonl').stat().st_size
    assert not (shared/'aggregates.jsonl').stat().st_size
    report=P.report(root,tmp_path)
    assert report['adoptable']==3 and report['pending']==0
    assert 'probability' not in __import__('json').dumps(report)
    assert post['title'] not in __import__('json').dumps(report)
    assert len(read_complete(root/'lock_events.jsonl'))==6


def test_slow_preflight_exhausts_total_admission_clock(tmp_path,post,monkeypatch):
    root,shared,client,adapters=setup_full_run(tmp_path,post,monkeypatch)
    def slow(p,**kw):
        monkeypatch.setattr(P,'utc_now',lambda:NOW+timedelta(seconds=700))
        return F.Preflight(p)
    monkeypatch.setattr(F,'preflight',slow)
    with pytest.raises(F.QuestionDone,match='lock admission'):P.run(root,20,tmp_path,client=client)
    assert all(a.ran==0 for a in adapters.values())
    assert not practice_usage(shared)[0]


def test_post_preflight_refresh_catches_reopen(tmp_path,post,monkeypatch):
    root,shared,client,adapters=setup_full_run(tmp_path,post,monkeypatch)
    def reopen(p,**kw):post['question']['status']='open';return F.Preflight(p)
    monkeypatch.setattr(F,'preflight',reopen)
    with pytest.raises(ValueError):P.run(root,20,tmp_path,client=client)
    assert all(a.ran==0 for a in adapters.values())
    assert not read_complete(root/'runs.jsonl')

@pytest.mark.parametrize('action',['prepare','run','recover'])
@pytest.mark.parametrize('name',['scheduler','forecast.gpt'])
def test_busy_locks_do_not_mutate(registered,tmp_path,action,name):
    shared,root,_,_=registered
    before={str(p):p.read_bytes() for p in shared.rglob('*.jsonl')}
    with F.Lock(shared/(name+'.lock')):
        with pytest.raises(F.PreflightError):
            P.run(root,20,tmp_path) if action=='run' else getattr(P,action)(root,tmp_path)
    assert before=={str(p):p.read_bytes() for p in shared.rglob('*.jsonl')}


@pytest.mark.usefixtures("legacy_stops")  # pre-D149 stop rule
def test_unknown_practice_visible_in_preflight_before_cli(registered):
    shared,root,_,m=registered;b=budget_for(shared);admission(shared,root,b,m)
    def forbidden():raise AssertionError('no CLI version probe before recovery')
    pf=F.preflight('claude',acceptance_log=shared/'acceptance.jsonl',versions=forbidden)
    assert not pf.ok and any('practice_attempt_unknown' in x for x in pf.problems)


def test_mixed_formal_and_practice_exposures_replay_chronologically(registered):
    shared,root,_,_=registered;line=root/'20/claude';line.mkdir(parents=True)
    common={'type':'forecast_attempt','player':'claude','question_id':20,
            'pipeline_version':F.PIPELINE_VERSION,'model_check':'verified',
            'error_type':None,'own_audit_flags':['leak_confirmed']}
    append_record(shared/'forecasts.jsonl',{**common,'attempt_id':'new','finished_at_utc':to_iso(NOW)})
    append_record(line/'forecasts.jsonl',{**common,'attempt_id':'old','finished_at_utc':to_iso(NOW-timedelta(days=30))})
    P.propagate_safety(shared,line,'claude','old')
    assert not F.line_stopped(shared/'line_status.jsonl','claude',shared/'forecasts.jsonl',now=NOW)


@pytest.mark.parametrize('ledger',['practice_usage.jsonl','practice_guards.jsonl'])
def test_unknown_metadata_versions_fail_closed(registered,ledger):
    shared,root,_,m=registered;b=budget_for(shared);admission(shared,root,b,m)
    if ledger=='practice_usage.jsonl':
        P.finish_accounting(shared,root/'20/claude','claude','a',b)
    else:
        line=root/'20/claude';line.mkdir(parents=True)
        append_record(line/'forecasts.jsonl',{'attempt_id':'a','player':'claude','question_id':20,
            'finished_at_utc':to_iso(NOW),'pipeline_version':F.PIPELINE_VERSION,'model_check':'verified',
            'error_type':None,'own_audit_flags':['leak_confirmed']})
        P.propagate_safety(shared,line,'claude','a')
    rows=read_complete(shared/ledger);rows[-1]['version']='unknown'
    (shared/ledger).write_text(''.join(__import__('json').dumps(r)+'\n' for r in rows))
    with pytest.raises(ValueError):
        practice_usage(shared) if ledger=='practice_usage.jsonl' else P.guards(shared)


def test_budget_denied_sibling_is_not_formal_history(tmp_path,post,monkeypatch):
    root,shared,client,adapters=setup_full_run(tmp_path,post,monkeypatch)
    reserve=B.Budget.reserve
    import threading
    denied=threading.Event()
    def one_denied(self,request,**kw):
        if request['player']=='claude':
            campaign=deepcopy(self.campaign);campaign['weekly_attempt_caps']['claude']=0
            self.campaign=campaign
            result=reserve(self,request,**kw);assert not result['allowed'];denied.set();return result
        return reserve(self,request,**kw)
    # Make the denied decision durable before either other line's final guards.
    original=P.run_line
    def ordered(player,*a,**kw):
        if player!='claude':assert denied.wait(5)
        return original(player,*a,**kw)
    monkeypatch.setattr(B.Budget,'reserve',one_denied);monkeypatch.setattr(P,'run_line',ordered)
    result=P.run(root,20,tmp_path,client=client)
    assert adapters['claude'].ran==0 and adapters['gemini'].ran==adapters['gpt'].ran==1
    assert all('formal lifecycle' not in x.get('reason','') for x in result['players'])
    assert {r['player'] for r in practice_usage(shared)[0].values()}=={'gemini','gpt'}
    # prepare has no exemption for this failed practice budget history.
    with pytest.raises(ValueError,match='formal lifecycle'):P.lifecycle_evidence(shared,20,10,'full')


def test_score_does_not_observe_in_progress_run(registered,tmp_path):
    shared,root,_,_=registered
    append_record(root/'runs.jsonl',{'type':'practice_question_started','question_id':20})
    class Client:
        def get_post(self,pid):raise AssertionError('no resolution read while practice runs')
    assert P.score(root,tmp_path,client=Client())['questions'][0]['resolution_status']=='run_in_progress'


def test_unrelated_metadata_and_other_player_bad_time_do_not_stop_line(tmp_path):
    append_record(tmp_path/'forecasts.jsonl',{'type':'note','player':'claude'})
    append_record(tmp_path/'forecasts.jsonl',{'type':'forecast_attempt','player':'gpt','finished_at_utc':'malformed'})
    assert not F.line_stopped(tmp_path/'line_status.jsonl','claude',tmp_path/'forecasts.jsonl',now=NOW)


@pytest.mark.usefixtures("legacy_stops")  # pre-D149 stop rule
def test_mixed_recent_formal_practice_exposures_still_stop(registered):
    shared,root,_,_=registered;line=root/'20/claude';line.mkdir(parents=True)
    common={'type':'forecast_attempt','player':'claude','question_id':20,
            'pipeline_version':F.PIPELINE_VERSION,'model_check':'verified',
            'error_type':None,'own_audit_flags':['leak_confirmed']}
    append_record(shared/'forecasts.jsonl',{**common,'attempt_id':'new','finished_at_utc':to_iso(NOW)})
    append_record(line/'forecasts.jsonl',{**common,'attempt_id':'old','finished_at_utc':to_iso(NOW-timedelta(days=2))})
    P.propagate_safety(shared,line,'claude','old')
    assert F.line_stopped(shared/'line_status.jsonl','claude',shared/'forecasts.jsonl',now=NOW)
