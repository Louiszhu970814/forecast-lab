import copy
import io
import json
import urllib.error
import urllib.request
import urllib.parse

import pytest

import poll_questions as P
import submit as S
import m2_prepare as M
from metaculus import MetaculusClient, MetaculusError, NoRedirect
from flab.ledger import read_complete
from test_m3_w9 import HTTP, transport, TOKEN, MAIN, no_token_in_any_test_ledger_or_console
from test_submit import environment, rows, NOW


def group(title='Synthetic group', child_title='Synthetic child', qtype='binary'):
    return {'id':21,'title':title,'question':None,'group_of_questions':{
        'questions':[{'id':31,'type':qtype,'title':child_title}]}}


@pytest.mark.parametrize('kind',['child_id','post_id','child_title','parent_title'])
@pytest.mark.parametrize('qtype',['binary','multiple_choice','numeric'])
def test_group_metadata_blocks_synced_single_question_by_all_keys(environment,kind,qtype):
    e=environment;http=HTTP(e['post']);g=group(qtype=qtype)
    if kind=='child_id': g['group_of_questions']['questions'][0]['id']=http.post['question']['id']
    elif kind=='post_id': g['id']=http.post['id']
    elif kind=='child_title': g['group_of_questions']['questions'][0]['title']=http.post['title']
    else:g['title']=http.post['title']
    http.posts[MAIN]=[g];e['seed']()
    S.Submitter(e['root'],config_dir=e['config'],transport=transport(http),now=lambda:NOW).run()
    assert rows(e)[-1]['status']=='rejected_precheck' and not http.writes()


@pytest.mark.parametrize('key',['condition','condition_child','question_yes','question_no'])
def test_each_conditional_child_is_in_exclusion_index(tmp_path,key):
    children={k:{'id':40+i,'title':'Synthetic '+k,'type':'binary'}
        for i,k in enumerate(('condition','condition_child','question_yes','question_no'))}
    post={'id':21,'title':'Synthetic parent','conditional':children}
    http=HTTP();http.posts[MAIN]=[post]
    index=transport(http).tournament_index({'excluded_project_ids':[MAIN]})
    snap={'question_id':999,'post_id':999,'project_ids':[],'title':children[key]['title']}
    assert P.is_synced_copy(snap,index)
    assert {40,41,42,43}==index.question_ids and 21 in index.post_ids
    assert 'Synthetic' not in (tmp_path/'transport_poll.jsonl').read_text()


@pytest.mark.parametrize('post',[{'id':21,'title':'Synthetic','question':None},
    {'id':21,'title':'Synthetic','group_of_questions':{'questions':[]}},
    {'id':21,'title':'Synthetic','conditional':{}},
    {'id':21,'title':'Synthetic','question':{'id':None}},
    {'id':21,'title':'Synthetic','group_of_questions':{'questions':[{'id':31,'type':'binary'}]}},
    {'id':'Synthetic private ID','title':'Synthetic','notebook':{'id':1}}])
def test_unknown_or_incomplete_structure_never_returns_partial_index(tmp_path,post):
    http=HTTP();http.posts[MAIN]=[post]
    with pytest.raises(ValueError):transport(http).tournament_index({'excluded_project_ids':[MAIN]})
    assert not read_complete(tmp_path/'transport_poll.jsonl')
    assert not http.writes()


def test_known_notebook_is_not_a_question_but_old_group_skip_is_incomplete(tmp_path):
    http=HTTP();http.posts[MAIN]=[{'id':21,'notebook':{'id':11}}]
    index=transport(http).tournament_index({'excluded_project_ids':[MAIN]})
    assert index.project_ids=={MAIN} and not index.question_ids
    with pytest.raises(ValueError,match='incomplete'):
        P.TournamentIndex.from_polls([{'tournament_id':MAIN,'skipped':[{'post_id':21,'reason':'not a single-question post'}]}])


@pytest.mark.parametrize('url',['http://www.metaculus.com/api/posts/',
    'https://www.metaculus.com/accounts/Synthetic','https://www.metaculus.com/api/posts/#Synthetic',
    'https://www.metaculus.com/api/../accounts/Synthetic','https://www.metaculus.com/api/%2e%2e/accounts/Synthetic',
    'https://evil.example/api/posts/'])
def test_pagination_boundary_rejects_before_network(url):
    calls=[]
    c=MetaculusClient(TOKEN,opener=lambda *a:calls.append(a))
    with pytest.raises(MetaculusError) as caught:c._get_url(url)
    assert not calls and 'Synthetic' not in str(caught.value) and TOKEN not in str(caught.value)


def test_default_read_redirect_handler_refuses_token_forwarding():
    req=urllib.request.Request('https://www.metaculus.com/api/posts/',headers={'Authorization':'Token '+TOKEN})
    with pytest.raises(MetaculusError) as caught:
        NoRedirect().redirect_request(req,None,302,'Synthetic',{},'https://evil.example/'+TOKEN)
    assert TOKEN not in str(caught.value) and 'Synthetic' not in str(caught.value)


def test_remote_http_error_text_does_not_leave_read_client():
    def opener(req,timeout):
        raise urllib.error.HTTPError(req.full_url,403,TOKEN+' Synthetic',{},io.BytesIO(TOKEN.encode()))
    c=MetaculusClient(TOKEN,opener=opener)
    with pytest.raises(MetaculusError) as caught:c.get('posts/Synthetic/')
    assert TOKEN not in str(caught.value) and 'Synthetic' not in str(caught.value)


def test_exclusion_http_query_has_no_forecast_type_filter(tmp_path):
    http=HTTP();http.posts[MAIN]=[group()]
    index=transport(http).tournament_index({'excluded_project_ids':[MAIN]})
    assert 31 in index.question_ids
    request=next(r for r in http.calls if urllib.parse.urlsplit(r.full_url).path=='/api/posts/')
    query=urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
    assert 'forecast_type' not in query
    assert set(query['statuses'])=={'open','upcoming'}


def test_trial_poll_keeps_its_normal_type_filter(tmp_path):
    http=HTTP()
    P.poll(transport(http).client,MAIN,questions_log=tmp_path/'q.jsonl',poll_log=tmp_path/'p.jsonl')
    request=next(r for r in http.calls if urllib.parse.urlsplit(r.full_url).path=='/api/posts/')
    query=urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
    assert query['forecast_type']==['binary,multiple_choice,numeric,discrete']


@pytest.mark.parametrize('question',[None, 'Synthetic private field', {'id':'Synthetic private field'}])
def test_trial_poll_does_not_emit_unvalidated_ids(tmp_path,question):
    http=HTTP();http.posts[MAIN]=[{'id':'Synthetic private ID','question':question,'projects':{}}]
    P.poll(transport(http).client,MAIN,trial_bot_project_id=32977,
        questions_log=tmp_path/'q.jsonl',poll_log=tmp_path/'p.jsonl')
    assert 'Synthetic' not in (tmp_path/'p.jsonl').read_text()
