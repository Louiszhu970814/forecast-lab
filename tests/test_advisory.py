from datetime import timedelta
from pathlib import Path

import pytest

import advise
import budget as B
from flab.advisory_usage import usage
from flab.config import load_quota_policy
from flab.jsonl import append_record
from flab.timeutil import to_iso
from poll_questions import TournamentIndex
from test_forecast import NOW


@pytest.fixture
def ledger(tmp_path):
    campaign = {"campaign_id": "test", "budget_window_start": to_iso(NOW-timedelta(days=1)),
        "budget_window_end": to_iso(NOW+timedelta(days=1)), "weekly_attempt_caps": {"claude": 2}}
    return B.Budget(tmp_path, campaign, load_quota_policy(), "run", now=lambda: NOW)


def event(ledger, aid="a", *, finished=False, started=True, at=None):
    row = {"type": "advisory_finished" if finished else "advisory_admitted", "at": to_iso(at or NOW),
           "attempt_id": aid, "player": "claude"}
    if finished: row["started"] = started
    append_record(ledger.root/"advisory_usage.jsonl", row)


def reserve(ledger, aid="next"):
    return ledger.reserve({"player":"claude", "tier":1, "question_id":7, "attempt_id":aid},
                          pending_tier1=0, expected_new=0, repair_reserve=0)


def test_advisory_charged_to_bot_cap_without_forecasts(ledger):
    for aid in ("a", "b"):
        event(ledger, aid); event(ledger, aid, finished=True)
    result = reserve(ledger)
    assert result["weekly_attempts_used"] == 2
    assert result["reason"] == "weekly_attempt_capacity_reserved_or_exhausted"
    assert not (ledger.root/"forecasts.jsonl").exists()


def test_unknown_call_stays_charged_and_blocks_after_restart(ledger):
    reserve(ledger, "a"); event(ledger)
    ledger.run_id = "new_run"
    ledger.recover()
    result = reserve(ledger)
    assert result["weekly_attempts_used"] == 1
    assert result["reason"] == "unresolved_or_running_attempt"


def test_finished_external_reservation_does_not_block_bot(ledger):
    reserve(ledger, "a"); event(ledger); ledger.outcome("a", True)
    event(ledger, finished=True)
    result = reserve(ledger)
    assert result["allowed"] and result["weekly_attempts_used"] == 1


def test_proven_no_start_is_not_charged(ledger):
    reserve(ledger, "a"); event(ledger); ledger.outcome("a", False)
    event(ledger, finished=True, started=False)
    result = reserve(ledger)
    assert result["allowed"] and result["weekly_attempts_used"] == 0


def test_window_boundary_but_old_unknown_still_blocks(ledger):
    event(ledger, at=NOW-timedelta(days=2))
    result = reserve(ledger)
    assert result["weekly_attempts_used"] == 0
    assert result["reason"] == "unresolved_or_running_attempt"


@pytest.mark.parametrize("bad", ["duplicate", "no_start", "wrong_player", "backwards", "no_bool", "unknown_type"])
def test_corrupt_accounting_fails_closed(ledger, bad):
    if bad != "no_start": event(ledger)
    row = {"type":"advisory_finished", "at":to_iso(NOW), "attempt_id":"a", "player":"claude", "started":True}
    if bad == "duplicate": row["type"] = "advisory_admitted"
    if bad == "wrong_player": row["player"] = "gpt"
    if bad == "backwards": row["at"] = to_iso(NOW-timedelta(seconds=1))
    if bad == "no_bool": row["started"] = "true"
    if bad == "unknown_type": row["type"] = "ignore_me"
    append_record(ledger.root/"advisory_usage.jsonl", row)
    with pytest.raises(ValueError): reserve(ledger)


def test_partial_advisory_record_is_not_ignored(ledger):
    (ledger.root/"advisory_usage.jsonl").write_text('{"attempt_id":')
    with pytest.raises(ValueError): reserve(ledger)


def test_no_answer_metadata_is_required_for_accounting(ledger):
    event(ledger); event(ledger, finished=True)
    a, f = usage(ledger.root)
    assert set(a) == set(f) == {"a"}


def test_advisory_directory_cannot_be_bot_data_or_external(tmp_path):
    (tmp_path/"data/advisory/test").mkdir(parents=True)
    assert advise.batch_path(tmp_path/"data/advisory/test",tmp_path).name == "test"
    for path in (tmp_path/"data", tmp_path/"data/advisory", tmp_path):
        with pytest.raises(ValueError): advise.batch_path(path,tmp_path)


def test_no_started_output_keeps_advisory_unknown(ledger):
    root=ledger.root/"separate";root.mkdir()
    event(ledger)
    append_record(root/"forecast_attempt_starts.jsonl", {"attempt_id":"a"})
    advise.finish_accounting(ledger.root,root,"claude","a")
    assert usage(ledger.root)[1] == {}


def test_completed_output_unblocks_without_copying_answer(ledger):
    root=ledger.root/"separate";root.mkdir()
    event(ledger)
    append_record(root/"forecast_attempt_starts.jsonl", {"attempt_id":"a"})
    append_record(root/"forecasts.jsonl", {"attempt_id":"a", "probability":0.62})
    advise.finish_accounting(ledger.root,root,"claude","a")
    assert usage(ledger.root)[1]["a"]["started"] is True
    assert not (ledger.root/"forecasts.jsonl").exists()


@pytest.fixture
def post():
    return {"id":46106, "title":"Synthetic COP test", "projects":{"tournament":[{"id":33108}]},
        "question":{"id":46343, "type":"binary", "status":"open", "options":None,
            "open_time":to_iso(NOW-timedelta(days=1)), "scheduled_close_time":to_iso(NOW+timedelta(days=1)),
            "scheduled_resolve_time":to_iso(NOW+timedelta(days=2))}}


@pytest.mark.parametrize("field,value", [("status","closed"),("type","numeric"),("id",123)])
def test_selected_question_and_type_guards(post,field,value):
    post['question'][field]=value
    with pytest.raises(ValueError): advise.safe_snapshot(post,TournamentIndex(),now=NOW)


def test_same_question_bot_overlap_is_rejected(post):
    index=TournamentIndex();index.question_ids.add(46343)
    with pytest.raises(ValueError,match="overlaps"): advise.safe_snapshot(post,index,now=NOW)


def test_whitelisted_snapshot_excludes_community_and_personal_forecasts(post):
    post['question']['aggregations']={'community':{'probability':0.99}}
    post['question']['my_forecasts']={'probability':0.01}
    snap=advise.safe_snapshot(post,TournamentIndex(),now=NOW)
    assert 'aggregations' not in snap and 'my_forecasts' not in snap
    assert snap['tournament_id']==33108


def test_bot_protected_capacity_releases_denied_advisory(ledger, monkeypatch):
    monkeypatch.setattr(advise, 'utc_now', lambda: NOW)
    req={'player':'claude','tier':1,'question_id':46343,'attempt_id':'human'}
    with pytest.raises(Exception, match='budget refused'):
        advise.register_admission(ledger.root,ledger,ledger.root/'human',req,2)
    assert usage(ledger.root)==({}, {})
    assert reserve(ledger)['allowed']


def test_completed_player_question_cannot_be_rerun(ledger, monkeypatch):
    monkeypatch.setattr(advise, 'utc_now', lambda: NOW)
    root=ledger.root/'human';root.mkdir()
    req={'player':'claude','tier':1,'question_id':46343,'attempt_id':'human'}
    advise.register_admission(ledger.root,ledger,root,req,0)
    ledger.outcome('human', True); event(ledger,'human',finished=True)
    with pytest.raises(Exception, match='already admitted'):
        advise.register_admission(ledger.root,ledger,root,{**req,'attempt_id':'rerun'},0)


def test_v7_unassessed_token_breach_is_not_lost_on_v8_upgrade(ledger):
    from test_budget import attempt
    ledger.campaign['weekly_attempt_caps']['claude']=20
    attempt(ledger, aid='first', total=10)
    attempt(ledger, aid='olddeep', tier=2, total=1000)
    append_record(ledger.log, {'type':'budget_reserved','attempt_id':'olddeep','player':'claude',
        'question_id':7,'tier':2,'run_id':'prior','version':'budget_v7','token_limit':30,'at':to_iso(NOW)})
    ledger.reserve({'player':'claude','tier':2,'question_id':8,'attempt_id':'newdeep'},
        pending_tier1=0,expected_new=0,repair_reserve=0)
    from flab.ledger import read_complete
    assert any(r.get('type')=='tier2_token_assessed' and r.get('attempt_id')=='olddeep'
               and r.get('status')=='exceeded' for r in read_complete(ledger.log))


def test_cross_batch_research_is_not_an_extra_attempt(ledger, monkeypatch):
    monkeypatch.setattr(advise, 'utc_now', lambda: NOW)
    req={'player':'claude','tier':1,'question_id':46343,'attempt_id':'human'}
    advise.register_admission(ledger.root,ledger,ledger.root/'first',req,0)
    ledger.outcome('human', False); event(ledger,'human',finished=True,started=False)
    with pytest.raises(Exception,match='already admitted'):
        advise.register_admission(ledger.root,ledger,ledger.root/'second',{**req,'attempt_id':'second'},0)


def test_six_admissions_cannot_be_extended(ledger):
    for i in range(6):
        append_record(ledger.root/'advisory_usage.jsonl', {'type':'advisory_admitted','version':advise.VERSION,
            'at':to_iso(NOW),'attempt_id':str(i),'player':'gpt','question_id':i})
    with pytest.raises(Exception, match='cap'):
        advise.register_admission(ledger.root,ledger,ledger.root/'new',
            {'player':'claude','tier':1,'question_id':46343,'attempt_id':'seventh'},0)


def test_gemini_reserves_future_argon_before_human_call(ledger):
    from test_budget import snapshot
    ledger.campaign['weekly_attempt_caps']['gemini']=200
    snapshot(ledger, remaining=.06)
    result=ledger.reserve({'player':'gemini','tier':1,'question_id':46343,'attempt_id':'new'},
        pending_tier1=0,expected_new=0,repair_reserve=0,advisory_protected=22)
    assert result['reason']=='argon_personal_or_tier1_reserve'
    assert result['protected_quota'] > 0


@pytest.mark.parametrize('fields',[
    {'tool_violations':[{'type':'unexpected_tool'}]},
    {'safety_stop_reason':'synthetic nonzero credits'},
    {'own_audit_flags':['leak_confirmed']},
    {'recovery_no_rerun':True}])
@pytest.mark.usefixtures("legacy_stops")  # pre-D149 stop rule
def test_private_safety_events_stop_shared_bot_line(ledger,fields):
    from flab.ledger import read_complete
    root=ledger.root/'separate';root.mkdir()
    append_record(root/'forecasts.jsonl',{'attempt_id':'a','player':'claude','question_id':46343,
        'run_id':'human','probability':.62,**fields})
    advise.propagate_safety(ledger.root,root,'claude','a')
    advise.propagate_safety(ledger.root,root,'claude','a')
    rows=read_complete(ledger.root/'line_status.jsonl')
    assert len(rows)==1 and 'probability' not in rows[0]
    assert advise.F.line_stopped(ledger.root/'line_status.jsonl','claude')


def test_real_engine_keeps_admission_trace_and_no_bot_answer(tmp_path,monkeypatch):
    from test_forecast import FakeAdapter,_good,SNAP
    from flab.config import load_run_config
    from flab.ledger import read_complete
    from flab.timeutil import utc_now
    cfg,policy=load_run_config(),load_quota_policy()
    now=utc_now();shared=tmp_path/'data';root=shared/'advisory/one';root.mkdir(parents=True)
    campaign={'campaign_id':'test','budget_window_start':to_iso(now-timedelta(days=1)),
        'budget_window_end':to_iso(now+timedelta(days=1)), 'weekly_attempt_caps':{'claude':130},
        'expected_new_questions':10,'repair_reserve':2,'run_config_sha256':advise.V.hash_obj(cfg),
        'quota_policy_sha256':advise.V.hash_obj(policy)}
    snap={**SNAP,'question_id':46343,'post_id':46106,'tournament_id':33108}
    adapter=FakeAdapter([{'structured':_good(question_id=46343)}])
    monkeypatch.setattr(advise,'load_players_enabled',lambda:{'claude':True})
    monkeypatch.setattr(advise.baseline,'ACCEPTANCE_LOG',tmp_path/'empty')
    result=advise.run_line('claude',snap,root,shared,campaign,cfg,policy,advise.F.Preflight('claude'),adapter)
    assert result['adoptable'] and adapter.ran==1
    start=read_complete(root/'forecast_attempt_starts.jsonl')[0]
    out=read_complete(root/'forecasts.jsonl')[0]
    reserved=next(r for r in read_complete(shared/'budget_events.jsonl') if r['type']=='budget_reserved')
    assert start['admission_decision_id']==out['admission_decision_id']==reserved['decision_id']
    assert not (shared/'forecasts.jsonl').exists()
    assert usage(shared)[1][start['attempt_id']]['started'] is True


@pytest.mark.parametrize('expired,absent',[(False,True),(True,False),(True,True)])
def test_recovery_audits_expired_dead_attempts_and_preserves_cost(ledger,monkeypatch,expired,absent):
    from flab.config import load_run_config
    from flab import orphan
    from flab.ledger import read_complete
    root=ledger.root/'separate';root.mkdir()
    req={'player':'claude','tier':1,'question_id':46343,'attempt_id':'a'}
    monkeypatch.setattr(advise,'utc_now',lambda:NOW)
    advise.register_admission(ledger.root,ledger,root,req,0)
    append_record(root/'forecast_attempt_starts.jsonl', {'type':'forecast_attempt_started','attempt_id':'a',
        'player':'claude','question_id':46343,'tier':1,'attempt_kind':'initial','started_at_utc':to_iso(NOW),
        'run_id':'old','pipeline_version':advise.F.PIPELINE_VERSION})
    monkeypatch.setattr(orphan,'expired',lambda *a:expired)
    monkeypatch.setattr(orphan,'no_process',lambda *a:absent)
    advise.recover_accounting(ledger.root,root,ledger.campaign,load_run_config(),ledger.policy)
    if expired and absent:
        assert usage(ledger.root)[1]['a']['started'] is True
        assert read_complete(root/'forecasts.jsonl')[0]['error_type']=='interrupted'
        assert reserve(ledger)['weekly_attempts_used']==1
    else:
        assert not usage(ledger.root)[1]
        assert reserve(ledger)['reason']=='unresolved_or_running_attempt'


@pytest.mark.parametrize('name',['scheduler','forecast.gpt'])
def test_busy_production_lock_prevents_any_advisory_mutation(tmp_path,name):
    from contextlib import ExitStack
    shared=tmp_path/'data';shared.mkdir()
    with advise.F.Lock(shared/(name+'.lock')):
        with pytest.raises(advise.F.PreflightError):
            with ExitStack() as stack: advise.lock_all(shared,stack)
    assert not list(shared.glob('*.jsonl'))


@pytest.mark.parametrize('error',['quota_exhausted','model_mismatch'])
def test_advisory_backoff_is_seen_by_bot_and_expires(ledger,error):
    root=ledger.root/'separate';root.mkdir()
    append_record(root/'forecasts.jsonl',{'attempt_id':'a','player':'claude','question_id':46343,
        'finished_at_utc':to_iso(NOW),'pipeline_version':advise.F.PIPELINE_VERSION,
        'model_check':'mismatch' if error=='model_mismatch' else 'verified',
        'error_type':error,'quota_retry_at_utc':to_iso(NOW+timedelta(minutes=30))})
    advise.propagate_safety(ledger.root,root,'claude','a')
    assert advise.F.line_stopped(ledger.root/'line_status.jsonl','claude',now=NOW)
    assert not advise.F.line_stopped(ledger.root/'line_status.jsonl','claude',now=NOW+timedelta(days=1))


def test_finished_without_start_cannot_free_budget(ledger):
    root=ledger.root/'private';root.mkdir()
    event(ledger)
    append_record(root/'forecasts.jsonl',{'attempt_id':'a'})
    with pytest.raises(ValueError,match='missing its start'):
        advise.finish_accounting(ledger.root,root,'claude','a')
    assert not usage(ledger.root)[1]


def test_accounting_conflict_fails_closed(ledger):
    reserve(ledger,'a');event(ledger);ledger.outcome('a',True)
    event(ledger,finished=True,started=False)
    with pytest.raises(ValueError,match='contradicts'):
        reserve(ledger)


def test_answers_cannot_enter_metadata_ledger(ledger):
    append_record(ledger.root/'advisory_usage.jsonl',{'type':'advisory_admitted','at':to_iso(NOW),
        'attempt_id':'a','player':'claude','probability':.7})
    with pytest.raises(ValueError): usage(ledger.root)
