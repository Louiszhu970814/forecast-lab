"""Current official API schema, synthetic fixtures only (no account/network calls)."""
import copy
import json
from datetime import timedelta
import urllib.parse

import pytest

import submit as S
import submit_transport as T
from flab.timeutil import to_iso
from test_m3_w9 import HTTP, transport, intent as legacy_intent, TOKEN, no_token_in_any_test_ledger_or_console
from test_m3_w9 import Response
from test_submit import environment, rows, NOW
from poll_questions import snapshot_from_post


@pytest.fixture(autouse=True)
def fixed_observation_time(monkeypatch):
    monkeypatch.setattr(T, 'utc_now', lambda: NOW + timedelta(seconds=10))


def official_row(**kw):
    return {'question_id':7, 'author_id':42, 'start_time':(NOW + timedelta(seconds=1)).timestamp(),
        'end_time':None, 'forecast_values':[.7,.3], 'secret':TOKEN, **kw}


def intent():
    return {**legacy_intent(), 'request_forecast_baseline':[]}


def modern(http, row=None):
    row = official_row() if row is None else row
    http.post['question']['my_forecasts'] = {'history':[copy.deepcopy(row)],'latest':row,'score_data':{}}
    return row


def test_empty_current_container_is_absence_only_for_initial_precheck():
    http = HTTP()
    http.post['question']['my_forecasts'] = {'history':[],'latest':None,'score_data':{}}
    assert S.existing_predictions(http.post) == []
    assert transport(http).readback('forecast', intent(), request_id='req').outcome == 'unknown'


@pytest.mark.parametrize('value', [None, {}, {'history':[]}, {'latest':None},
    {'history':None,'latest':None}, {'history':[None],'latest':None},
    {'history':[{}],'latest':None}, {'history':[],'latest':{}}, {'history':[],'latest':[]}])
def test_incomplete_container_never_looks_like_empty_history(value):
    http=HTTP();http.post['question']['my_forecasts']=value
    with pytest.raises(S.PrecheckError): S.existing_predictions(http.post)
    assert transport(http).readback('forecast', intent(), request_id='req').outcome=='unknown'


def test_withdrawn_or_latest_only_forecast_still_counts_as_existing():
    http=HTTP(); modern(http)
    http.post['question']['my_forecasts']['latest']=None
    assert S.existing_predictions(http.post)
    http.post['question']['my_forecasts']={'history':[],'latest':official_row()}
    assert S.existing_predictions(http.post)


def test_official_identity_is_stable_tuple_not_a_fabricated_database_id():
    http=HTTP();modern(http)
    result=transport(http).readback('forecast',intent(),request_id='r')
    assert result.outcome=='found'
    assert result.record['id_kind']=='question_author_start_time'
    assert result.record['id'].startswith('forecast:7:42:')
    assert result.record['payload']==intent()['forecast_payload']
    assert 'option_order_verified' not in result.record
    assert TOKEN not in repr(result)
    assert transport(http).readback('forecast',intent(),request_id='r').record==result.record


@pytest.mark.parametrize('change', [
    {'question_id':8}, {'question_id':True}, {'author_id':43}, {'author_id':True},
    {'start_time':True}, {'start_time':float('nan')}, {'start_time':float('inf')},
    {'start_time':to_iso(NOW+timedelta(seconds=1))}, {'start_time':NOW.timestamp()},
    {'start_time':(NOW-timedelta(seconds=1)).timestamp()}, {'start_time':(NOW+timedelta(seconds=20)).timestamp()},
    {'forecast_values':[.6,.4]}, {'forecast_values':[.7,True]}, {'forecast_values':[.7,float('nan')]},
    {'forecast_values':[.3]}, {'forecast_values':None}, {'forecast_values':[.71,.3]}])
def test_official_readback_rejects_wrong_identity_time_or_distribution(change):
    http=HTTP();modern(http,official_row(**change))
    assert transport(http).readback('forecast',intent(),request_id='r').outcome=='unknown'


def test_two_distinct_matching_records_are_ambiguous():
    http=HTTP();modern(http)
    http.post['question']['my_forecasts']['history'].append(official_row(start_time=(NOW+timedelta(seconds=2)).timestamp()))
    assert transport(http).readback('forecast',intent(),request_id='r').outcome=='unknown'


def test_category_mapping_uses_all_options_ever_including_inactive_null():
    http=HTTP();modern(http,official_row(forecast_values=[.2,None,.8]))
    q=http.post['question'];q.update(type='multiple_choice',options=['B','A'],all_options_ever=['A','inactive','B'],
        options_order=['B','A'], options_history=[['2026-01-01T00:00:00Z',['A','inactive','B']]])
    data=intent();data['forecast_payload']=[{'question':7,'probability_yes_per_category':{'B':.8,'A':.2}}]
    result=transport(http).readback('forecast',data,request_id='r')
    assert result.outcome=='found' and result.record['option_order_verified'] is True
    q['my_forecasts']['latest']['forecast_values']=[.8,None,.2]
    q['my_forecasts']['history']=[]
    assert transport(http).readback('forecast',data,request_id='r').outcome=='unknown'


@pytest.mark.parametrize('values', [[.2,.8], [.8,.2], [None,.2,None,.8,None], [.8,None,.2]])
def test_null_history_remains_unknown_even_if_value_multiset_matches(values):
    http=HTTP();modern(http,official_row(forecast_values=values))
    http.post['question'].update(type='multiple_choice',options=['A','B'],all_options_ever=None)
    data=intent();data['forecast_payload']=[{'question':7,'probability_yes_per_category':{'A':.2,'B':.8}}]
    assert transport(http).readback('forecast',data,request_id='r').outcome=='unknown'
    assert not transport(http).can_reconcile_forecast(http.post)


@pytest.mark.parametrize('order', [[], ['A','A'], ['B'], ['A',3], 'AB'])
def test_present_but_unusable_history_never_falls_back_to_multiset(order):
    http=HTTP();modern(http,official_row(forecast_values=[.2,.8]))
    http.post['question'].update(type='multiple_choice',options=['A','B'],all_options_ever=order)
    data=intent();data['forecast_payload']=[{'question':7,'probability_yes_per_category':{'A':.2,'B':.8}}]
    assert transport(http).readback('forecast',data,request_id='r').outcome=='unknown'


@pytest.mark.parametrize('question_change,probabilities,row_change', [
    ({}, {'A':.2,'B':.8}, {'forecast_values':[.3,.7]}),
    ({}, {'A':.2,'B':.8}, {'forecast_values':[.2,.8000000000000002]}),
    ({}, {'A':.2,'B':.8}, {'forecast_values':[.2,.8,.8]}),
    ({}, {'A':.2,'B':.8}, {'forecast_values':[None,.8]}),
    ({}, {'A':.2,'B':.8}, {'forecast_values':None}),
    ({}, {'A':.2,'B':.8}, {'forecast_values':[]}),
    ({}, {'A':.2,'B':.8}, {'forecast_values':[None,None]}),
    ({}, {'A':.2}, {}),
    ({}, {'A':.2,'C':.8}, {}),
    ({}, {'A':.2,'B':.8,'C':0}, {}),
    ({}, [.2,.8], {}),
    ({'options':[]}, {}, {}),
    ({'options':['A','A']}, {'A':.2}, {}),
    ({'options':['A',3]}, {'A':.2,3:.8}, {}),
    ({'options':['A',[]]}, {'A':.2}, {}),
    ({'options':None}, {'A':.2,'B':.8}, {}),
    ({'options':'AB'}, {'A':.2,'B':.8}, {}),
    ({'type':'numeric'}, {'A':.2,'B':.8}, {}),
    ({}, {'A':.2,'B':.8}, {'question_id':8}),
    ({}, {'A':.2,'B':.8}, {'author_id':43}),
    ({}, {'A':.2,'B':.8}, {'start_time':NOW.timestamp()}),
    ({}, {'A':.2,'B':.8}, {'start_time':(NOW-timedelta(seconds=1)).timestamp()}),
    ({}, {'A':.2,'B':.8}, {'start_time':(NOW+timedelta(seconds=11)).timestamp()}),
])
def test_label_readback_rejects_invalid_distribution_identity_or_time(question_change, probabilities, row_change):
    http=HTTP()
    row=modern(http,official_row(**{'forecast_values':[.2,.8], **row_change}))
    question=http.post['question']
    question.update(type='multiple_choice',options=['A','B'],all_options_ever=['A','B'])
    question.update(question_change)
    data=intent();data['forecast_payload']=[{'question':7,'probability_yes_per_category':probabilities}]
    assert T.serialized_forecast_receipt(question,row,data,data['forecast_payload'],NOW) is None
    assert transport(http).readback('forecast',data,request_id='r').outcome=='unknown'


@pytest.mark.parametrize('bad', [True, False, float('nan'), float('inf'), -float('inf'), -.1, 1.1, '0.8', [], {}])
@pytest.mark.parametrize('field', ['payload', 'forecast_values'])
def test_label_readback_rejects_invalid_numbers(bad, field):
    http=HTTP()
    row=modern(http,official_row(forecast_values=[0,1]))
    question=http.post['question']
    question.update(type='multiple_choice',options=['A','B'],all_options_ever=['A','B'])
    data=intent();data['forecast_payload']=[{'question':7,'probability_yes_per_category':{'A':0,'B':1}}]
    if field=='payload':
        data['forecast_payload'][0]['probability_yes_per_category']['B']=bad
    else:
        row['forecast_values'][1]=bad
        question['my_forecasts']['history']=[]
    assert T.serialized_forecast_receipt(question,row,data,data['forecast_payload'],NOW) is None
    assert transport(http).readback('forecast',data,request_id='r').outcome=='unknown'


@pytest.mark.parametrize('values,found,verified', [
    ([.5,.25,.25], False, None), ([.25,.25,.5], True, True),
    ([.5,.5,.25], False, None),
])
def test_label_readback_rejects_probability_permutation_even_with_same_multiset(values, found, verified):
    http=HTTP();modern(http,official_row(forecast_values=values))
    http.post['question'].update(type='multiple_choice',options=['A','B','C'],all_options_ever=['A','B','C'])
    data=intent()
    data['forecast_payload']=[{'question':7,'probability_yes_per_category':{'A':.25,'B':.25,'C':.5}}]
    result=transport(http).readback('forecast',data,request_id='r')
    assert result.outcome==('found' if found else 'unknown')
    if found:
        assert result.record['option_order_verified'] is verified


def test_label_readback_accepts_inclusive_probability_and_time_bounds():
    http=HTTP()
    modern(http,official_row(forecast_values=[1.0,None,0],start_time=(NOW+timedelta(seconds=10)).timestamp()))
    http.post['question'].update(type='multiple_choice',options=['A','B'],all_options_ever=['B','inactive','A'])
    data=intent();data['forecast_payload']=[{'question':7,'probability_yes_per_category':{'A':0.0,'B':1}}]
    result=transport(http).readback('forecast',data,request_id='r')
    assert result.outcome=='found' and result.record['option_order_verified'] is True


@pytest.mark.parametrize('options,allowed', [
    (['A','B'], False), (['A'], False), (None, False), ([], False),
    (['A','A'], False), (['A',3], False), (['A',[]], False), ('AB', False), ({'A':1}, False),
])
def test_can_reconcile_multiple_choice_without_history(options, allowed):
    http=HTTP()
    http.post['question'].update(type='multiple_choice',options=options,all_options_ever=None)
    assert transport(http).can_reconcile_forecast(http.post) is allowed


@pytest.mark.parametrize('history,allowed', [(None, False), (['A','B'], True), (['B','x','A'], True),
    ([], False), (['B'], False), (['A','A'], False), (['A',3], False), ('AB', False)])
def test_pre_post_gate_matches_readback_rule_for_present_history(history, allowed):
    http=HTTP()
    http.post['question'].update(type='multiple_choice',options=['A','B'],all_options_ever=history)
    assert transport(http).can_reconcile_forecast(http.post) is allowed


@pytest.mark.parametrize('kind,allowed', [('binary', True), ('numeric', False), ('discrete', False), (None, False)])
def test_can_reconcile_other_question_types_unchanged(kind, allowed):
    http=HTTP();http.post['question'].update(type=kind)
    assert transport(http).can_reconcile_forecast(http.post) is allowed


def test_empty_201_then_restart_reconciles_without_second_forecast(environment):
    e=environment;e['seed']();http=HTTP(e['post'])
    http.post['question']['my_forecasts']={'history':[],'latest':None,'score_data':{}}
    http.effects=[{}]  # official create endpoint returns HTTP 201 with {}
    obj=S.Submitter(e['root'],config_dir=e['config'],transport=transport(http),now=lambda:NOW)
    obj.run()
    assert rows(e)[-1]['status']=='unknown' and len(http.writes())==1
    modern(http)
    obj=S.Submitter(e['root'],config_dir=e['config'],transport=transport(http),now=lambda:NOW+timedelta(seconds=5))
    obj.run()
    assert rows(e)[-1]['status']=='comment_ok'
    assert len(http.writes())==2
    confirmed=[r for r in rows(e) if r['status']=='forecast_ok']
    assert len(confirmed)==1 and confirmed[0]['response']['id_kind']=='question_author_start_time'


def test_comment_query_uses_official_post_filter():
    http=HTTP();transport(http).readback('comment',intent(),request_id='r')
    request=next(r for r in http.calls if urllib.parse.urlsplit(r.full_url).path=='/api/comments/')
    query=urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
    assert query['post']==['17'] and query['author']==['42'] and 'post_id' not in query


@pytest.mark.parametrize('lose_mapping', [False, True])
def test_mc_restart_exact_mapping_or_unknown_never_resends(environment, lose_mapping):
    e=environment
    e['post']['question'].update(type='multiple_choice',options=['A','B'],all_options_ever=['A','inactive','B'],
        my_forecasts={'history':[],'latest':None})
    snap={**snapshot_from_post(e['post'],'bot-testing-area'),'snapshot_id':'s1','tournament_id':32977}
    e['seed'](snap,p={'A':.3,'B':.7})
    http=HTTP(e['post']);http.effects=[{}]
    obj=S.Submitter(e['root'],config_dir=e['config'],transport=transport(http),now=lambda:NOW)
    obj.run();obj.run()
    assert rows(e)[-1]['status']=='unknown' and len(http.writes())==1
    modern(http,official_row(forecast_values=[.7,None,.3]))
    obj.run()
    assert rows(e)[-1]['status']=='unknown' and len(http.writes())==1
    probabilities=json.loads(http.writes()[0].data)[0]['probability_yes_per_category']
    modern(http,official_row(forecast_values=[probabilities['A'],None,probabilities['B']]))
    if lose_mapping:
        http.post['question']['all_options_ever']=None
    obj=S.Submitter(e['root'],config_dir=e['config'],transport=transport(http),now=lambda:NOW+timedelta(seconds=5))
    obj.run();obj.run();obj.run(reconcile_only=True)
    if lose_mapping:
        assert rows(e)[-1]['status']=='unknown' and len(http.writes())==1
    else:
        assert rows(e)[-1]['status']=='comment_ok' and len(http.writes())==2
        confirmed=[r for r in rows(e) if r['status']=='forecast_ok']
        assert len(confirmed)==1 and confirmed[0]['response']['option_order_verified'] is True
    assert len([r for r in http.writes() if r.full_url.endswith(T.FORECAST_PATH)])==1


def test_missing_mc_mapping_blocks_first_post(environment):
    e=environment
    e['post']['question'].update(type='multiple_choice', options=['A','B'], all_options_ever=None,
        my_forecasts={'history': [], 'latest': None})
    snap={**snapshot_from_post(e['post'],'bot-testing-area'),'snapshot_id':'s1','tournament_id':32977}
    e['seed'](snap,p={'A':.3,'B':.7})
    http=HTTP(e['post'])
    obj=S.Submitter(e['root'],config_dir=e['config'],transport=transport(http),now=lambda:NOW)
    obj.run();obj.run()
    assert not http.writes()


@pytest.mark.parametrize('p',[.7,.123457,.999])
def test_binary_serializer_complement_matches_official_python_calculation(p):
    http=HTTP();modern(http,official_row(forecast_values=[1-p,p]))
    data=intent();data['forecast_payload']=[{'question':7,'probability_yes':p}]
    assert transport(http).readback('forecast',data,request_id='r').outcome=='found'


def test_observed_prior_record_is_never_attributed_to_new_request_with_clock_skew():
    http=HTTP();row=modern(http)
    data=intent();data['request_forecast_baseline']=[T.forecast_identity(row)]
    assert transport(http).readback('forecast',data,request_id='r').outcome=='unknown'


def test_tuple_readback_without_prewrite_baseline_fails_closed():
    http=HTTP();modern(http)
    assert transport(http).readback('forecast',legacy_intent(),request_id='r').outcome=='unknown'


def test_prewrite_baseline_and_precise_time_survive_restart(environment):
    e=environment;e['seed']();http=HTTP(e['post']);http.post['question']['my_forecasts']={'history':[],'latest':None}
    http.effects=[{}]
    when=NOW+timedelta(microseconds=789)
    S.Submitter(e['root'],config_dir=e['config'],transport=transport(http),now=lambda:when).run()
    started=next(r for r in rows(e) if r['status']=='request_started')
    assert started['request_started_at_precise'].endswith('.000789Z')
    assert started['forecast_readback_baseline']==[]
    # Earlier receipt in the same millisecond cannot match the precise request.
    modern(http,official_row(start_time=(NOW+timedelta(microseconds=500)).timestamp()))
    S.Submitter(e['root'],config_dir=e['config'],transport=transport(http),now=lambda:NOW+timedelta(seconds=5)).run()
    assert rows(e)[-1]['status']=='unknown' and len(http.writes())==1


def test_int_float_timestamp_representation_cannot_evade_prior_baseline():
    stamp=int((NOW+timedelta(seconds=1)).timestamp())
    prior=official_row(start_time=stamp)
    http=HTTP();modern(http,official_row(start_time=float(stamp)))
    data=intent();data['request_forecast_baseline']=[T.forecast_identity(prior)]
    assert transport(http).readback('forecast',data,request_id='r').outcome=='unknown'


@pytest.mark.parametrize('later',['duplicate','different','failed'])
def test_comment_readback_requires_one_match_over_complete_pagination(later):
    http=HTTP();row={'id':9,'author':42,'on_post':17,'text':'Synthetic comment',
        'created_at':to_iso(NOW+timedelta(seconds=1))}
    def opener(req,timeout):
        if '/comments/' not in req.full_url:return http(req,timeout)
        if req.full_url.endswith('offset=100'):
            if later=='failed':raise TimeoutError('Synthetic failure')
            return Response({'results':[{**row,'id':9 if later=='duplicate' else 10}],'next':None})
        return Response({'results':[row],'next':'https://www.metaculus.com/api/comments/?offset=100'})
    result=T.APITransport(TOKEN,opener=opener).readback('comment',intent(),request_id='r')
    assert result.outcome==('found' if later=='duplicate' else 'unknown')


def _server_like_comments(rows):
    """Backend semantics (D102): private rows only with is_private=true; countless pagination
    always returns a next link, including after the last row."""
    calls = []
    def opener(req, timeout):
        parts = urllib.parse.urlsplit(req.full_url)
        if parts.path != '/api/comments/':
            return HTTP()(req, timeout)
        query = urllib.parse.parse_qs(parts.query)
        calls.append(query)
        private = query.get('is_private') == ['true']
        visible = [r for r in rows if r['is_private'] is private]
        offset, limit = int(query.get('offset', ['0'])[0]), int(query.get('limit', ['100'])[0])
        nxt = 'https://www.metaculus.com/api/comments/?' + urllib.parse.urlencode(
            {**{k: v[0] for k, v in query.items()}, 'offset': offset + limit})
        return Response({'results': visible[offset:offset + limit], 'next': nxt, 'previous': None})
    return opener, calls


def _comment_row(**kw):
    return {'id': 9, 'author': {'id': 42}, 'on_post': 17, 'text': 'Synthetic comment', 'is_private': True,
            'created_at': to_iso(NOW + timedelta(seconds=1)), **kw}


def test_private_comment_readback_requests_private_listing_and_stops_on_empty_page():
    opener, calls = _server_like_comments([_comment_row()])
    result = T.APITransport(TOKEN, opener=opener).readback('comment', legacy_intent(), request_id='r')
    assert result.outcome == 'found' and result.record['id'] == 9
    assert [q.get('is_private') for q in calls] == [['true'], ['true']]
    assert [q.get('offset', ['0']) for q in calls] == [['0'], ['100']]


def test_private_comment_hidden_without_flag_is_not_assumed_found():
    opener, calls = _server_like_comments([_comment_row()])
    data = legacy_intent()
    data['comment_payload'] = {**data['comment_payload'], 'is_private': False}
    result = T.APITransport(TOKEN, opener=opener).readback('comment', data, request_id='r')
    assert result.outcome == 'unknown'
    assert len(calls) == 1 and 'is_private' not in calls[0]


@pytest.mark.parametrize('value', [None, 'true', 1])
def test_comment_readback_requires_explicit_privacy_flag(value):
    opener, calls = _server_like_comments([_comment_row()])
    data = legacy_intent()
    data['comment_payload'] = {**data['comment_payload'], 'is_private': value}
    result = T.APITransport(TOKEN, opener=opener).readback('comment', data, request_id='r')
    assert result.outcome == 'unknown' and not calls


def test_empty_first_comment_page_ends_with_one_request():
    opener, calls = _server_like_comments([])
    result = T.APITransport(TOKEN, opener=opener).readback('comment', legacy_intent(), request_id='r')
    assert result.outcome == 'unknown' and len(calls) == 1


def test_public_comment_readback_found_without_private_flag_across_trailing_page():
    opener, calls = _server_like_comments([_comment_row(id=7, is_private=True), _comment_row(id=9, is_private=False)])
    data = legacy_intent()
    data['comment_payload'] = {**data['comment_payload'], 'is_private': False}
    result = T.APITransport(TOKEN, opener=opener).readback('comment', data, request_id='r')
    assert result.outcome == 'found' and result.record['id'] == 9
    assert len(calls) == 2 and all('is_private' not in q for q in calls)


@pytest.mark.parametrize('row_value', [False, None, 'true'])
def test_private_readback_rejects_row_whose_privacy_differs(row_value):
    def opener(req, timeout):
        if urllib.parse.urlsplit(req.full_url).path != '/api/comments/':
            return HTTP()(req, timeout)
        return Response({'results': [_comment_row(is_private=row_value)], 'next': None})
    result = T.APITransport(TOKEN, opener=opener).readback('comment', legacy_intent(), request_id='r')
    assert result.outcome == 'unknown'
