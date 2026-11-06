from datetime import timedelta
import copy
import pytest
import discovery as D
from flab.jsonl import append_record
from flab.timeutil import to_iso
from test_forecast import NOW

POLICY={'version':'continuous_v11','campaign_id':'new','discovery':D.SETTING}

def scan(root, *, window=10800, delay=0, prior=None, at=NOW, status='open'):
    row={'question_id':1,'post_id':2,'status':status,'open_time':to_iso(at-timedelta(seconds=delay)),
         'scheduled_close_time':to_iso(at+timedelta(seconds=window-delay))}
    return D.record(root,POLICY,[('main',{'tournament_id':3,'timing':[row]})],now=at,prior=prior)

def test_idle_discovery_30_minutes_persists_across_restarts(tmp_path):
    s=scan(tmp_path)
    assert s['interval_s']==1800
    assert not D.due(D.latest(tmp_path,POLICY),NOW+timedelta(seconds=1799))
    assert D.due(s,NOW+timedelta(seconds=1800))
    assert D.due(s,NOW-timedelta(seconds=1))

def test_known_deadline_work_wakes_before_slow_discovery(tmp_path):
    s=scan(tmp_path)
    assert not D.due(s,NOW+timedelta(seconds=60))
    assert D.needs_cycle(tmp_path,POLICY,NOW+timedelta(seconds=60))
    append_record(tmp_path/'submissions.jsonl',{'question_id':1,'status':'comment_ok'})
    assert not D.needs_cycle(tmp_path,POLICY,NOW+timedelta(seconds=60))
    assert D.needs_cycle(tmp_path,POLICY,NOW+timedelta(seconds=1800))

@pytest.mark.parametrize('window,delay,reason',[(3599,0,'observed_window_below_one_hour'),
    (10800,6000,'first_seen_decision_slack_below_research_bound')])
def test_short_windows_or_late_discovery_shorten_and_stay_short(tmp_path,window,delay,reason):
    s=scan(tmp_path,window=window,delay=delay)
    assert s['interval_s']==300 and reason in s['shortening_reasons']
    s2=scan(tmp_path,prior=s,at=NOW+timedelta(seconds=300))
    assert s2['interval_s']==300

def test_preannounced_open_time_wakes_without_waiting_30_min(tmp_path):
    s=scan(tmp_path,delay=-120,status='upcoming')
    assert not D.due(s,NOW+timedelta(seconds=119))
    assert D.due(s,NOW+timedelta(seconds=120))

def test_policy_cannot_silently_increase_to_60_min():
    p=copy.deepcopy(POLICY);p['discovery']['interval_s']=3600
    with pytest.raises(ValueError):D.validate(p)

@pytest.mark.parametrize('interval,remaining',[(600,8400),(1800,7200),(3600,5400)])
def test_same_three_hour_window_replay(interval,remaining):
    # Synthetic upper-bound discovery delay, excluding real sleep/lock outage.
    assert 10800-1800-interval==remaining


def test_full_scan_only_on_cadence_known_refresh_detects_revisions(tmp_path,monkeypatch):
    import continuous as C
    import poll_questions as P
    from test_poll_questions import _post
    from flab.ids import hash_obj
    cfg={'tournaments':{'main':{'id':33121,'submit_whitelisted':True},
                       'bot_testing_area':{'id':32977},'minibench':{'id':33135,'submit_whitelisted':True}}}
    monkeypatch.setattr(C,'load_tournaments',lambda:cfg)
    monkeypatch.setattr(C,'load_submit_mode',lambda:{'mode':'live'})
    p=_post(17,7,'binary','Synthetic test',open_time=to_iso(NOW),scheduled_close_time=to_iso(NOW+timedelta(hours=3)))
    p['projects']={'tournament':[{'id':33121}]}
    class Client:
        listed=0;fetched=0
        def list_posts(self,*a,**kw):self.listed+=1;return [p]
        def get_post(self,pid):self.fetched+=1;return p
    client=Client();clock=[NOW];policy={**POLICY,'venues':['main']}
    poller=C.Poller(client,tmp_path,policy,now=lambda:clock[0])
    a=poller();assert client.listed==1 and client.fetched==0 and 7 in a
    clock[0]+=timedelta(seconds=60);p['question']['description']='revised rules'
    b=C.Poller(client,tmp_path,policy,now=lambda:clock[0])()
    assert client.listed==1 and client.fetched==1 and a[7]['content_hash']!=b[7]['content_hash']
    clock[0]=NOW+timedelta(seconds=1800);poller()
    assert client.listed==2


def test_failed_discovery_does_not_advance_the_clock(tmp_path,monkeypatch):
    import continuous as C
    policy={**POLICY,'venues':['main']}
    obj=C.Poller(None,tmp_path,policy,now=lambda:NOW)
    def fail():raise OSError('synthetic read failure')
    monkeypatch.setattr(obj,'_poll_all',fail)
    with pytest.raises(OSError):obj()
    assert D.latest(tmp_path,policy) is None and D.needs_cycle(tmp_path,policy,NOW)


def test_runtime_questions_do_not_change_discovery_identity(tmp_path):
    state=scan(tmp_path)
    runtime={**POLICY,'questions':[{'question_id':1}]}
    assert D.latest(tmp_path,runtime)==state
    assert D.policy_hash(runtime)==D.policy_hash(POLICY)


def test_successor_does_not_mislabel_legacy_known_question(tmp_path):
    row={'question_id':1,'post_id':2,'status':'open','open_time':to_iso(NOW-timedelta(hours=2)),
         'scheduled_close_time':to_iso(NOW+timedelta(hours=1))}
    state=D.record(tmp_path,POLICY,[('main',{'tournament_id':3,'timing':[row]})],now=NOW,previously_known={1})
    assert state['interval_s']==1800 and not state['metrics'][0]['first_seen']
    assert state['metrics'][0]['observed_open_delay_s'] is None


def test_discovery_keeps_only_accepted_first_venue_mapping(tmp_path):
    q={'question_id':1,'post_id':2,'status':'open'}
    records=[('main',{'tournament_id':3,'timing':[q,{**q,'question_id':2}],
                      'skipped':[{'question_id':2,'reason':'synced copy'}]}),
             ('market_pulse',{'tournament_id':4,'timing':[q]})]
    state=D.record(tmp_path,POLICY,records,now=NOW)
    assert state['known']==[{**q,'ground':'main','tournament_id':3}]


def test_pending_readback_wakes_after_close_but_retry_wait_does_not(tmp_path):
    scan(tmp_path)
    append_record(tmp_path/'submissions.jsonl',{'question_id':9,'intent_id':'i','status':'intent'})
    append_record(tmp_path/'submissions.jsonl',{'question_id':9,'intent_id':'i','status':'unknown'})
    assert D.needs_cycle(tmp_path,POLICY,NOW+timedelta(seconds=60))
    append_record(tmp_path/'submissions.jsonl',{'question_id':9,'intent_id':'i','status':'comment_ok'})
    append_record(tmp_path/'submissions.jsonl',{'question_id':1,'status':'comment_ok'})
    assert not D.needs_cycle(tmp_path,POLICY,NOW+timedelta(seconds=60))


def waiting_question(root, outcome='budget_wait', retry=3600, qid=1):
    from test_forecast import SNAP
    from flab.config import PLAYERS
    snap={**SNAP,'question_id':qid,'snapshot_id':'s','status':'open',
          'open_time':to_iso(NOW-timedelta(hours=1)), 'scheduled_close_time':to_iso(NOW+timedelta(hours=3))}
    append_record(root/'questions.jsonl',snap)
    for player in PLAYERS:
        append_record(root/'scheduler_jobs.jsonl',{'type':'scheduler_job_finished','record_id':player,
          'question_id':qid,'question_snapshot_id':'s','player':player,'tier':1,'outcome':outcome,
          'finished_at':to_iso(NOW),'reevaluate_after':to_iso(NOW+timedelta(seconds=retry))})
    return snap


@pytest.mark.parametrize('outcome',['budget_wait','quota_5h_wait','quota_weekly_wait','preflight_blocked','line_stopped'])
def test_waiting_questions_do_not_start_worker_until_retry(tmp_path,outcome):
    snap=waiting_question(tmp_path,outcome,retry=300)
    known=[snap]
    if outcome=='line_stopped':
        for player in ('claude','gemini','gpt'):
            append_record(tmp_path/'line_status.jsonl',{'type':'line_stop','player':player,'at':to_iso(NOW),'reason':'synthetic stop'})
    assert not D.actionable_questions(tmp_path,known,NOW+timedelta(seconds=60))
    if outcome=='line_stopped':
        append_record(tmp_path/'line_status.jsonl',{'type':'line_resume','player':'gemini','at':to_iso(NOW+timedelta(seconds=300))})
    assert D.actionable_questions(tmp_path,known,NOW+timedelta(seconds=300))=={1}


def test_decision_deadline_wakes_even_when_budget_waiting(tmp_path):
    import forecast as F
    from flab.config import load_run_config
    snap=waiting_question(tmp_path,retry=99999)
    assert D.actionable_questions(tmp_path,[snap],F.decision_deadline(snap,load_run_config()))=={1}


def test_unsupported_and_failed_final_comment_do_not_wake(tmp_path):
    snap=waiting_question(tmp_path)
    unsupported={**snap,'question_type':'unsupported','snapshot_id':'u'}
    append_record(tmp_path/'questions.jsonl',unsupported)
    assert not D.actionable_questions(tmp_path,[unsupported],NOW)
    append_record(tmp_path/'questions.jsonl',snap)
    append_record(tmp_path/'aggregates.jsonl',{'record_id':'a','question_id':1,'question_snapshot_id':'s',
                'completion_status':'complete_local','status':'ready'})
    append_record(tmp_path/'comments.jsonl',{'type':'comment_failed','aggregate_id':'a'})
    assert not D.actionable_questions(tmp_path,[snap],NOW)


def test_refresh_duplicate_qid_cannot_flip_project_and_upcoming_is_not_fetched(tmp_path,monkeypatch):
    import continuous as C
    import market_policy as MP
    from test_poll_questions import _post
    cfg={'tournaments':{'main':{'id':33121,'submit_whitelisted':True},
        'market_pulse':{'id':33131,'slug':'market-pulse-26q4','submit_whitelisted':True}}}
    monkeypatch.setattr(C,'load_tournaments',lambda:cfg)
    monkeypatch.setattr(C,'load_submit_mode',lambda:{'mode':'live'})
    post=_post(17,7,'binary','Synthetic',open_time=to_iso(NOW),scheduled_close_time=to_iso(NOW+timedelta(hours=3)))
    post['projects']={'tournament':[{'id':33121},{'id':33131}]}
    class Client:
        calls=[]
        def get_post(self,pid):self.calls.append(pid);return post
    client=Client();policy={**POLICY,'venues':['main','market_pulse'],'market_pulse':MP.SETTING}
    known=[{'question_id':7,'post_id':17,'status':'open','ground':role,'tournament_id':pid}
           for role,pid in [('main',33121),('market_pulse',33131)]]
    known.append({'question_id':8,'post_id':18,'status':'upcoming','ground':'main','tournament_id':33121})
    poller=C.Poller(client,tmp_path,policy,now=lambda:NOW)
    selected=poller._refresh_known({'known':known})
    assert client.calls==[17] and selected[7]['ground']=='main' and selected[7]['tournament_id']==33121
    policy['questions']=[{'question_id':7}]
    assert 'questions' not in poller.policy


def test_new_snapshot_is_not_blocked_by_abandoned_old_intent(tmp_path):
    snap=waiting_question(tmp_path,retry=0)
    append_record(tmp_path/'submissions.jsonl',{'question_id':1,'intent_id':'old','status':'intent',
                'snapshot':{'snapshot_id':'old'}})
    append_record(tmp_path/'submissions.jsonl',{'question_id':1,'intent_id':'old','status':'abandoned'})
    assert D.actionable_questions(tmp_path,[snap],NOW)=={1}


def test_heartbeat_noop_starts_no_worker_and_writes_no_schedule_log(tmp_path,monkeypatch):
    import cycle as C
    import live_window as W
    import json
    monkeypatch.setattr(C,'PROJECT_ROOT',tmp_path)
    monkeypatch.setattr(C,'utc_now',lambda:NOW)
    monkeypatch.setattr(C,'load_submit_mode',lambda:{'mode':'live'})
    monkeypatch.setattr(W,'bounds',lambda *a:(NOW-timedelta(hours=1),NOW+timedelta(hours=1)))
    monkeypatch.setattr(D,'needs_cycle',lambda *a:False)
    def forbidden(*a,**k):raise AssertionError('worker started')
    monkeypatch.setattr(C,'supervise',forbidden)
    p=tmp_path/'policy.json';p.write_text(json.dumps(POLICY))
    assert C.main(['--campaign',str(p),'--heartbeat'])==3
    assert not (tmp_path/'data/live_schedule.jsonl').exists()


def test_launch_wrapper_translates_idle_sentinel_without_maintenance(tmp_path):
    from pathlib import Path
    import subprocess
    (tmp_path/'launchd').mkdir();(tmp_path/'src').mkdir();(tmp_path/'.venv/bin').mkdir(parents=True)
    wrapper=tmp_path/'launchd/run.sh'
    wrapper.write_bytes((Path(__file__).parents[1]/'launchd/run.sh').read_bytes())
    (tmp_path/'src/cycle.py').write_text('unused')
    python=tmp_path/'.venv/bin/python'
    python.write_text('#!/bin/sh\ncase "$1" in src/cycle.py) exit 3;; *) touch maintenance_called; exit 99;; esac\n')
    python.chmod(0o755)
    result=subprocess.run(['/bin/sh',str(wrapper)],capture_output=True)
    assert result.returncode==0 and not (tmp_path/'maintenance_called').exists()


def test_nonpersistent_line_yield_keeps_its_retry_wait_without_later_resume(tmp_path):
    snap=waiting_question(tmp_path,'line_stopped',retry=600)
    assert not D.actionable_questions(tmp_path,[snap],NOW+timedelta(seconds=60))
    append_record(tmp_path/'line_status.jsonl',{'type':'line_resume','player':'gemini','at':to_iso(NOW-timedelta(seconds=1))})
    assert not D.actionable_questions(tmp_path,[snap],NOW+timedelta(seconds=60))
    append_record(tmp_path/'line_status.jsonl',{'type':'line_resume','player':'gemini','at':to_iso(NOW+timedelta(seconds=30))})
    assert D.actionable_questions(tmp_path,[snap],NOW+timedelta(seconds=60))=={1}


def test_later_resume_does_not_override_forecast_quota_wait(tmp_path):
    snap=waiting_question(tmp_path,'line_stopped',retry=600)
    append_record(tmp_path/'line_status.jsonl',{'type':'line_resume','player':'gemini','at':to_iso(NOW+timedelta(seconds=30))})
    append_record(tmp_path/'forecasts.jsonl',{'player':'gemini','quota_retry_at_utc':to_iso(NOW+timedelta(hours=1))})
    assert not D.actionable_questions(tmp_path,[snap],NOW+timedelta(seconds=60))
