import copy,json
from datetime import timedelta
import pytest
import human_advice as H
import advise as A
from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.timeutil import to_iso
from poll_questions import TournamentIndex
from test_advisory import ledger, post, event
from test_forecast import NOW
from flab.config import load_run_config


def test_generic_cup_question_supported_without_legacy_fixed_id(post):
    post['id']=50;post['question']['id']=60
    s=H.safe_snapshot(post,60,33108,TournamentIndex(),now=NOW,cfg=load_run_config())
    assert s['question_id']==60 and s['post_id']==50
    with pytest.raises(ValueError):A.safe_snapshot(post,TournamentIndex(),now=NOW)


def test_generic_question_rejects_cross_venue_and_bot_copy(post):
    idx=TournamentIndex();idx.question_ids.add(46343)
    with pytest.raises(ValueError,match='overlaps'):H.safe_snapshot(post,46343,33108,idx,now=NOW,cfg=load_run_config())
    with pytest.raises(ValueError):H.safe_snapshot(post,46343,123,TournamentIndex(),now=NOW,cfg=load_run_config())


def test_generic_admission_rejects_rerun_and_requires_update_reason(ledger,monkeypatch):
    monkeypatch.setattr(H,'utc_now',lambda:NOW)
    spec={'request_id':'req','update_reason':None};snap={'content_hash':'h'}
    req={'player':'claude','tier':1,'question_id':70,'attempt_id':'human'}
    H.register_admission(ledger.root,ledger,ledger.root/'human',req,0,spec=spec,snap=snap)
    ledger.outcome('human',True);event(ledger,'human',finished=True)
    with pytest.raises(Exception,match='consumed'):
        H.register_admission(ledger.root,ledger,ledger.root/'human',{**req,'attempt_id':'2'},0,spec=spec,snap=snap)
    with pytest.raises(Exception,match='update reason'):
        H.register_admission(ledger.root,ledger,ledger.root/'human',{**req,'attempt_id':'3'},0,spec={'request_id':'next'},snap=snap)


def test_main_minibench_market_open_and_imminent_yield(tmp_path):
    for pid in (33121,33135,33131):
        for status in ('open','upcoming'):
            records=[{'tournament_id':pid,'timing':[{'question_id':1,'status':status,'open_time':to_iso(NOW+timedelta(seconds=30))}]}]
            with pytest.raises(ValueError,match='yields'):H.bot_priority(tmp_path,records,now=NOW,horizon_s=900)
    append_record(tmp_path/'submissions.jsonl',{'status':'comment_ok','question_id':1})
    H.bot_priority(tmp_path,records,now=NOW,horizon_s=900)


def test_inventory_counts_posts_children_and_no_model_calls(tmp_path,monkeypatch,post):
    (tmp_path/'config').mkdir();(tmp_path/'config/cycle_campaign.json').write_text('{}')
    monkeypatch.setattr(H,'configuration',lambda p:{'tournaments':[{'id':33108,'name':'Cup'}]})
    monkeypatch.setattr(H,'exclusion_index',lambda *a,**k:(TournamentIndex(),[]))
    group=copy.deepcopy(post);q=group.pop('question');q2={**q,'id':46344,'scheduled_close_time':to_iso(NOW+timedelta(hours=2))}
    q['title']='Later';q2['title']='Urgent'
    group['group_of_questions']={'id':22,'questions':[q,q2],'description':'Shared rules'}
    class Client:
        def list_posts(self,*a,**k):return [group]
    result=H.inventory(Client(),tmp_path/'inventory',project=tmp_path,now=NOW)
    assert (result['open_posts'],result['open_group_posts'],result['open_child_questions'])==(1,1,2)
    assert result['top'][0]['question_id']==46344
    assert result['model_calls']==result['submissions']==0
    assert result['top'][0]['close_taipei'].endswith('+08:00')
    assert result['top'][0]['human_submission']=='unknown'


def test_actual_metadata_poll_includes_market_children_and_wait_does_not_block(tmp_path,monkeypatch,post):
    from test_discovery_cadence import waiting_question
    g=copy.deepcopy(post);q=g.pop('question');q.update(id=1,title='Child')
    g['group_of_questions']={'id':90,'questions':[q]}
    g['projects']={'tournament':[{'id':33131}]}
    monkeypatch.setattr(H.C,'current_tournaments',lambda *a,**k:{'excluded_project_ids':[33131],
                 'tournaments':{'market_pulse':{'id':33131}}})
    class Client:
        def list_posts(self,pid,**kw):
            assert pid==33131 and kw['forecast_type'] is None
            return [g]
    idx,records=H.exclusion_index(Client(),tmp_path,{},now=lambda:NOW)
    assert 1 in idx.question_ids and records[0]['timing'][0]['question_id']==1
    with pytest.raises(ValueError,match='yields'):H.bot_priority(tmp_path,records,now=NOW,horizon_s=900)
    waiting_question(tmp_path)
    H.bot_priority(tmp_path,records,now=NOW,horizon_s=900)


def test_recover_entrypoint_visits_actual_player_subdirectories(tmp_path,monkeypatch):
    from contextlib import nullcontext
    root=tmp_path/'data/advisory/request';root.mkdir(parents=True)
    seen=[]
    monkeypatch.setattr(A,'batch_path',lambda r,p:r)
    monkeypatch.setattr(A,'lock_all',lambda *a:None)
    monkeypatch.setattr(A,'recover_accounting',lambda shared,line,*a,**k:seen.append(line) or {'status':'recovered'})
    (tmp_path/'config').mkdir();(tmp_path/'config/cycle_campaign.json').write_text('{}')
    A.recover(root,project=tmp_path)
    assert seen==[root/p for p in A.PLAYERS]
