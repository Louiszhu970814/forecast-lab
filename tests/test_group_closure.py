import copy
import pytest
import resolve as R
from flab.jsonl import append_record
from test_resolve import _snap,_write_jsonl,NOW as RESOLVE_NOW
from test_post_close_audit import sc,default_questions,_assert_no_secrets


def test_submitted_child_resolves_while_sibling_group_remains_open(tmp_path):
    snap=_snap(post_type='group_of_questions',group_id=99,tournament_id=33131)
    _write_jsonl(tmp_path/'questions.jsonl',[snap])
    _write_jsonl(tmp_path/'submissions.jsonl',[{'question_id':101,'question_snapshot_id':'snap_101',
        'intent_id':'i','status':'comment_ok','at':'2026-11-03T10:00:00Z'}])
    child={'id':101,'title':'Selected','type':'binary','status':'resolved','resolution':'yes',
           'actual_resolve_time':'2026-11-06T12:30:00Z'}
    post={'id':9001,'title':'Group','status':'open','resolved':False,
          'group_of_questions':{'id':99,'questions':[child,{'id':102,'title':'Still open','status':'open'}]}}
    result=R.poll_resolutions(tmp_path,fetch_post=lambda pid:post,now=RESOLVE_NOW)
    assert result['appended']==1 and result['events'][0]['question_id']==101
    assert result['events'][0]['resolution_status']=='resolved'
    assert result['events'][0]['canonical_outcome']==1


@pytest.mark.parametrize('state,expected',[('closed',True),('open',False),('wrong_group',False)])
def test_post_close_gate_is_for_selected_child_only(sc,state,expected):
    snapshots=default_questions()
    for row in snapshots:row.update(post_type='group_of_questions',group_id=99)
    sc.write('questions.jsonl',snapshots)
    child=sc.post.pop('question');child['title']='Selected'
    child['status']='closed' if state=='wrong_group' else state
    sc.post.update(title='Group',status='open',resolved=False,
        group_of_questions={'id':100 if state=='wrong_group' else 99,'questions':[
            child,{'id':102,'title':'Sibling','status':'open'}]})
    result=sc.review()
    assert result['review_eligible'] is expected
    if not expected:_assert_no_secrets(str(result))
