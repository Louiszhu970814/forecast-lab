"""Current official API schema, synthetic fixtures only (no account/network calls)."""
import copy
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
    q=http.post['question'];q.update(type='multiple_choice',options=['B','A'],all_options_ever=['A','inactive','B'])
    data=intent();data['forecast_payload']=[{'question':7,'probability_yes_per_category':{'B':.8,'A':.2}}]
    assert transport(http).readback('forecast',data,request_id='r').outcome=='found'
    q['my_forecasts']['latest']['forecast_values']=[.8,None,.2]
    q['my_forecasts']['history']=[]
    assert transport(http).readback('forecast',data,request_id='r').outcome=='unknown'


@pytest.mark.parametrize('order', [None, [], ['A','A'], ['B'], ['A',3]])
def test_unknown_option_order_never_guesses_a_mapping(order):
    http=HTTP();modern(http,official_row(forecast_values=[.2,.8]))
    http.post['question'].update(type='multiple_choice',options=['A','B'],all_options_ever=order)
    data=intent();data['forecast_payload']=[{'question':7,'probability_yes_per_category':{'A':.2,'B':.8}}]
    assert transport(http).readback('forecast',data,request_id='r').outcome=='unknown'


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


def test_missing_option_order_rejects_before_any_write_or_request_started(environment):
    e=environment
    e['post']['question'].update(type='multiple_choice',options=['A','B'],all_options_ever=None,
        my_forecasts={'history':[],'latest':None})
    snap={**snapshot_from_post(e['post'],'bot-testing-area'),'snapshot_id':'s1','tournament_id':32977}
    e['seed'](snap,p={'A':.3,'B':.7})
    http=HTTP(e['post']);http.effects=[{}]
    obj=S.Submitter(e['root'],config_dir=e['config'],transport=transport(http),now=lambda:NOW)
    obj.run();obj.run()
    assert rows(e)[-1]['status']=='rejected_precheck'
    assert 'mapping unavailable' in rows(e)[-1]['reason']
    assert not http.writes() and not any(r['status']=='request_started' for r in rows(e))


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
