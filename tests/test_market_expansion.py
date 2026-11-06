"""D139 synthetic parent/child, finite budget and exact readback regression tests."""
import copy,json
from datetime import timedelta
import pytest

import continuous as C
import budget_handoff as H
import discovery as D
import market_policy as MP
import scheduler as S
import poll_questions as P
import submit as U
import submit_transport as T
from question_mapping import question_ids, select_question
from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import to_iso,parse_iso
from test_forecast import NOW
from test_main_launch_handoff import chain,LATER
from test_budget_handoff import finished,reserve
from test_api_readback import official_row,modern,intent
from test_m3_w9 import HTTP,transport
from test_submit import environment,rows,set_mode,update_cfg
from test_advisory import ledger


def successor(root,cap=40):
    original,mini,main=chain(root,cap=cap);C.register(root,main,now=lambda:LATER)
    policy=copy.deepcopy(main);policy.update(version=C.VERSION,campaign_id='market',venues=['main','minibench','market_pulse'],
            budget_window_start=to_iso(LATER+timedelta(hours=1)),discovery=copy.deepcopy(D.SETTING),market_pulse=copy.deepcopy(MP.SETTING))
    policy['budget_handoff']={'version':H.EXPANSION_VERSION,'source_campaign_id':main['campaign_id'],
            'source_sha256':hash_obj(main),'accounting_start':original['budget_window_start'],'authorization':'PLAN D139'}
    return original,main,policy


def test_v3_preserves_all_root_usage_and_expiry(tmp_path):
    original,main,p=successor(tmp_path,cap=3)
    for n,when in enumerate((NOW-timedelta(hours=1),NOW+timedelta(minutes=1),LATER+timedelta(minutes=1))):
        finished(tmp_path,str(n),when)
    before=(tmp_path/'continuous_campaigns.jsonl').read_bytes()
    now=LATER+timedelta(hours=1);C.register(tmp_path,p,now=lambda:now)
    assert (tmp_path/'continuous_campaigns.jsonl').read_bytes().startswith(before)
    assert len(H.ancestors(p,read_complete(tmp_path/'continuous_campaigns.jsonl')))==3
    assert H.accounting_start(tmp_path,p)==parse_iso(original['budget_window_start'])
    result=reserve(tmp_path,p,now=now)
    assert result['weekly_attempts_used']==3 and not result['allowed']
    with pytest.raises(ValueError,match='superseded'):reserve(tmp_path,main,now=now)


@pytest.mark.parametrize('change',['cap','end','root','source','fork','fourth','no-discovery','policy','authorization','rollover'])
def test_v3_cannot_reset_extend_fork_or_add_fourth(tmp_path,change):
    _,main,p=successor(tmp_path)
    now=LATER+timedelta(hours=1)
    if change in ('fork','fourth'):C.register(tmp_path,p,now=lambda:now)
    q=copy.deepcopy(p)
    if change=='cap':q['weekly_attempt_caps']['gpt']+=1
    if change=='end':q['budget_window_end']=to_iso(parse_iso(q['budget_window_end'])+timedelta(seconds=1))
    if change=='root':q['budget_handoff']['accounting_start']=main['budget_window_start']
    if change=='source':q['budget_handoff']['source_sha256']='wrong'
    if change=='fork':q['campaign_id']='fork'
    if change=='fourth':
        q.update(campaign_id='fourth',budget_window_start=to_iso(now+timedelta(minutes=1)))
        q['budget_handoff'].update(source_campaign_id=p['campaign_id'],source_sha256=hash_obj(p))
        now+=timedelta(minutes=1)
    if change=='no-discovery':q.pop('discovery')
    if change=='policy':q['market_pulse']['updates']=1
    if change=='authorization':q['budget_handoff']['authorization']='PLAN D137'
    if change=='rollover':q['rollover']={'version':'weekly_rollover_v1','windows':2}
    before=(tmp_path/'continuous_campaigns.jsonl').read_bytes()
    with pytest.raises(ValueError):C.register(tmp_path,q,now=lambda:now)
    assert (tmp_path/'continuous_campaigns.jsonl').read_bytes()==before


def group(post):
    result=copy.deepcopy(post);q=result.pop('question');q['title']='Child A'
    sibling=copy.deepcopy(q);sibling.update(id=q['id']+1,title='Child B',scheduled_close_time=to_iso(NOW+timedelta(days=2)))
    result['group_of_questions']={'id':99,'description':'Shared description','resolution_criteria':'Common resolution',
                                 'fine_print':'Common caveat','questions':[q,sibling]}
    return result


def test_group_snapshot_keeps_shared_and_child_rules_but_no_predictions():
    http=HTTP();modern(http);p=group(http.post)
    p['group_of_questions']['questions'][0]['resolution_criteria']='Child-specific resolution'
    ss=P.snapshots_from_post(p,33131,allow_groups=True)
    assert len(ss)==2 and ss[0]['group_id']==99 and ss[0]['post_id']==p['id']
    assert 'Common resolution' in ss[0]['resolution_criteria'] and 'Child-specific' in ss[0]['resolution_criteria']
    assert ss[0]['scheduled_close_time']!=ss[1]['scheduled_close_time']
    assert 'my_forecasts' not in repr(ss) and 'forecast_values' not in repr(ss)
    assert P.snapshots_from_post(p,33131)==[]


@pytest.mark.parametrize('mutation',['duplicate','missing','mixed','parent','group'])
def test_bad_mapping_never_selects_another_child(mutation):
    p=group(HTTP().post)
    if mutation=='duplicate':p['group_of_questions']['questions'][1]['id']=7
    if mutation=='missing':p['group_of_questions']['questions']=p['group_of_questions']['questions'][1:]
    if mutation=='mixed':p['question']={'id':7}
    if mutation=='parent':p['group_of_questions']['questions'][0]['post_id']=999
    if mutation=='group':p['group_of_questions']['id']=True
    with pytest.raises(ValueError):select_question(p,7,allow_groups=True)


def test_group_readback_only_matches_selected_child_and_own_history(monkeypatch):
    monkeypatch.setattr(T,'utc_now',lambda:NOW+timedelta(seconds=10))
    http=HTTP();modern(http);http.post=group(http.post)
    selected=http.post['group_of_questions']['questions'][0]
    sibling=http.post['group_of_questions']['questions'][1]
    selected['my_forecasts']={'history':[],'latest':None}
    sibling['my_forecasts']={'history':[official_row(question_id=8)],'latest':None}
    req={**intent(),'snapshot':{'post_type':'group_of_questions','group_id':99}}
    assert transport(http).readback('forecast',req,request_id='x').outcome=='unknown'
    selected['my_forecasts']['latest']=official_row()
    assert transport(http).readback('forecast',req,request_id='x').outcome=='found'
    req['request_forecast_baseline']=[T.forecast_identity(selected['my_forecasts']['latest'])]
    assert transport(http).readback('forecast',req,request_id='x').outcome=='unknown'


def test_group_submission_precheck_and_once_only_restart(environment):
    e=environment;e['cfg']['tournaments']['market_pulse']={'id':33131,'slug':'market-pulse-26q4','submit_whitelisted':True}
    update_cfg(e);set_mode(e,mode='live')
    p=json.loads((e['config']/'cycle_campaign.json').read_text());p.update(version='continuous_v11',market_pulse=MP.SETTING)
    (e['config']/'cycle_campaign.json').write_text(json.dumps(p))
    e['mode']['live_window']['campaign_sha256']=hash_obj(p);set_mode(e)
    e['post']['projects']={'tournament':[{'id':33131}]}
    g=group(e['post']);e['transport'].post=g
    q=g['group_of_questions']['questions'][0]
    g['group_of_questions']['questions'][1]['my_forecasts']=[{'id':123}]
    snap={**P.snapshots_from_post(g,33131,allow_groups=True)[0],'tournament_id':33131,'snapshot_id':'group-1'}
    e['seed'](snapshot=snap)
    original=e['transport'].write
    def write(path,payload,*,request_id):
        e['transport'].post={**g,'question':q}
        try:return original(path,payload,request_id=request_id)
        finally:e['transport'].post=g
    e['transport'].write=write
    e['make']().run();e['make']().run()
    assert [r['status'] for r in rows(e)]==['intent','request_started','forecast_ok','request_started','comment_ok']
    assert len(e['transport'].writes)==2
    assert rows(e)[0]['forecast_payload'][0]['question']==7
    assert rows(e)[0]['comment_payload']['on_post']==17
    assert 'Question 7:' in rows(e)[0]['comment_text']


def test_main_and_mini_both_tiers_precede_market_first_layer():
    jobs=[S.Job('gpt',tier,q,{},priority,NOW+timedelta(hours=2),100)
          for q,(priority,tier) in enumerate([('market_pulse',1),('main',2),('minibench',2),('minibench',1),('main',1)])]
    assert [(j.priority,j.tier) for j in S.order_jobs(jobs,now=NOW,urgent_slack_s=120)]==[
        ('main',1),('minibench',1),('main',2),('minibench',2),('market_pulse',1)]


def test_low_priority_budget_reserves_bot_capacity(ledger):
    ledger.campaign['weekly_attempt_caps']['claude']=22
    req={'player':'claude','tier':1,'question_id':5,'attempt_id':'market'}
    r=ledger.reserve(req,pending_tier1=0,expected_new=0,repair_reserve=0,low_priority_protected=22)
    assert not r['allowed'] and r['protected_attempts']==22
    r=ledger.reserve({**req,'attempt_id':'main'},pending_tier1=0,expected_new=0,repair_reserve=0)
    assert r['allowed']


def test_jobs_active_high_first_blocks_market_and_then_allows_high_second(tmp_path,monkeypatch):
    from test_scheduler import campaign
    from test_forecast import SNAP
    p=campaign(3);p['questions'][2]['priority']='market_pulse'
    snaps={q['question_id']:{**SNAP,'question_id':q['question_id'],'snapshot_id':str(q['question_id'])} for q in p['questions']}
    scheduler=S.Scheduler(tmp_path,p,snapshots=lambda:snaps,now=lambda:NOW)
    monkeypatch.setattr(S,'load_players_enabled',lambda:dict.fromkeys(('claude','gemini','gpt'),True))
    monkeypatch.setattr(scheduler,'_terminal',lambda q,p,t,a,r: t==1 and (q==101 or (q==100 and p!='claude')))
    monkeypatch.setattr(S.E,'evaluate',lambda *a,**k:{'escalation':{'candidate':True}})
    assert scheduler.jobs(snaps,paused=set(),active={'claude'})==[]
    monkeypatch.setattr(scheduler,'_terminal',lambda q,p,t,a,r:t==1 and q in (100,101))
    jobs=scheduler.jobs(snaps,paused=set(),active={'claude'})
    assert jobs and all(j.priority=='main' and j.tier==2 for j in jobs)
