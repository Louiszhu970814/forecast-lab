from datetime import timedelta
import json

import pytest

import cycle_monitor as M
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.team_chat import Board
from flab.timeutil import to_iso
from test_forecast import NOW


@pytest.fixture
def env(tmp_path):
    root = tmp_path / 'data'; root.mkdir()
    config = tmp_path / 'config'; config.mkdir()
    campaign = {'campaign_id': 'synthetic', 'budget_window_start': to_iso(NOW-timedelta(minutes=1)),
                'budget_window_end': to_iso(NOW+timedelta(seconds=15)), 'main_site_trial': {'max_questions': 2}}
    path = config / 'cycle_campaign.json'; path.write_text(json.dumps(campaign))
    (config / 'submit_mode.json').write_text('{"mode":"trial"}')
    for q in (1, 2):
        append_record(root / 'main_site_trial_selections.jsonl', {'campaign_id':'synthetic','question_id':q})
    def add(status, q=1, op='forecast', request='r1', age=0):
        append_record(root / 'submissions.jsonl', {'status':status,'question_id':q,'operation':op,
            'request_id':request,'at':to_iso(NOW-timedelta(seconds=age)),
            'probability':'PRIVATE_PROBABILITY','text':'PRIVATE_REASONING'})
    return tmp_path, root, campaign, path, add


def test_projection_excludes_payloads_and_resolves_unknown(env):
    project, root, campaign, _, add = env
    add('request_started'); add('unknown'); add('forecast_ok')
    result = M.inspect(root, campaign, {1,2}, now=NOW)
    assert result['unknown_requests'] == 0 and result['forecast_ok'] == [1]
    assert not result['hazards'] and 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('hazard', ['outside','duplicate','unknowns','failed','orphan'])
def test_hazards_are_detected_without_reading_payloads(env, hazard):
    _, root, campaign, _, add = env
    if hazard == 'outside': add('request_started', q=9)
    if hazard == 'duplicate':
        add('request_started'); add('request_started', request='r2')
    if hazard == 'unknowns':
        add('unknown'); add('unknown', q=2, request='r2')
    if hazard == 'failed': add('confirmed_failed')
    if hazard == 'orphan':
        campaign['budget_window_start'] = to_iso(NOW-timedelta(minutes=5))
        add('request_started', age=181)
    assert M.inspect(root,campaign,{1,2},now=NOW)['hazards']


def test_hazard_stops_without_overwriting_existing_switch(env):
    project, root, _, path, add = env
    add('request_started',q=9)
    result=M.run(project,path,now=lambda:NOW,sleep=lambda _:pytest.fail('should stop immediately'))
    assert result['status']=='stopped_for_hazard'
    switch=project/'config/STOP_SUBMIT'
    original=switch.read_text();M.stop(project);assert switch.read_text()==original
    assert 'PRIVATE' not in (root/'cycle_monitor.jsonl').read_text()


def test_window_ends_and_new_host_progress_is_not_overwritten(env):
    project, root, _, path, _ = env
    clock=[NOW]
    def sleep(seconds):
        clock[0]+=timedelta(seconds=seconds)
        Board(project).publish('new host progress',source='new host')
    result=M.run(project,path,now=lambda:clock[0],sleep=sleep)
    assert result['status']=='window_closed'
    assert not (project/'config/STOP_SUBMIT').exists()
    assert [r for r in Board(project).events() if r['type']=='progress'][-1]['source']=='new host'


def test_write_receipts_do_not_claim_independent_readback_or_m3_pass(env):
    project, root, _, path, add=env
    for q in (1,2):
        for op in ('forecast','comment'):add(op+'_ok',q=q,op=op,request=f'{q}{op}')
    result=M.run(project,path,now=lambda:NOW,sleep=lambda _:pytest.fail('should finish'))
    assert result['status']=='writes_complete_requires_host_readback_check'
    assert not (project/'config/STOP_SUBMIT').exists()
