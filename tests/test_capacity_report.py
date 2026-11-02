import json
from datetime import timedelta
import capacity_report as C
from flab.jsonl import append_record
from flab.timeutil import to_iso
from test_forecast import NOW


def test_timing_scope_and_no_private_output(tmp_path):
    cfg={'campaign_id':'c','questions':[{'question_id':1}]}
    r={'type':'forecast_attempt','attempt_id':'a','run_id':'r','question_id':1,'question_snapshot_id':'s',
       'player':'gemini','tier':1,'attempt_kind':'initial','model_check':'undetermined','quota_cost_status':'undetermined',
       'started_at_utc':to_iso(NOW+timedelta(seconds=10)),
       'cli_finished_at_utc':to_iso(NOW+timedelta(seconds=20)),
       'finished_at_utc':to_iso(NOW+timedelta(seconds=25)),
       'probability':.314159,'reasoning':'PRIVATE-REASONING','usage':{'input_tokens':7,'output_tokens':3}}
    append_record(tmp_path/'forecasts.jsonl',r)
    append_record(tmp_path/'scheduler_jobs.jsonl',{'campaign_id':'c','run_id':'r','question_snapshot_id':'s',
        'player':'gemini','tier':1,'started_at':to_iso(NOW),'finished_at':to_iso(NOW+timedelta(seconds=30))})
    append_record(tmp_path/'worker_timings.jsonl',{'campaign_id':'c','player':'gemini','tier':1,
        'started_at':to_iso(NOW-timedelta(seconds=20)),'finished_at':to_iso(NOW+timedelta(seconds=30))})
    out=C.build(tmp_path,cfg); cell=out['cells']['gemini.tier1']
    assert cell['cli_seconds']['p90']==10
    assert cell['attempt_seconds_excludes_pre_quota']['p90']==15
    assert cell['job_seconds_excludes_preflight']['p90']==30
    assert cell['job_non_cli_seconds_excludes_preflight']['p90']==20
    assert cell['full_execute_seconds']['p90']==50
    assert cell['trusted_quota_fraction']['n']==0
    assert cell['tokens']['total']==10
    assert not out['formal_weekly_capacity_established']
    assert 'PRIVATE-REASONING' not in json.dumps(out) and '0.314159' not in json.dumps(out)
    assert C.build(tmp_path,{'campaign_id':'other','questions':[{'question_id':2}]})['cells']['gemini.tier1']['attempts']==0


def test_unknown_starts_and_missing_not_zero(tmp_path):
    append_record(tmp_path/'forecast_attempt_starts.jsonl',{'attempt_id':'x','question_id':1})
    out=C.build(tmp_path,{'campaign_id':'c','questions':[{'question_id':1}]})
    assert out['unknown_attempts']==1
    assert out['cells']['claude.tier2']['cli_seconds']['p90'] is None
    assert out['cells']['claude.tier2']['trusted_quota_fraction']['total'] is None


def test_elapsed_rejects_invalid_timestamps():
    assert C.elapsed({},'a','b') is None
    assert C.elapsed({'a':'wrong','b':'wrong'},'a','b') is None
    assert C.elapsed({'a':to_iso(NOW),'b':to_iso(NOW-timedelta(seconds=1))},'a','b') is None


def test_missing_cli_timestamp_reported_without_crash(tmp_path):
    row={'attempt_id':'a','question_id':1,'run_id':'r','question_snapshot_id':'s','player':'gpt','tier':1,
         'started_at_utc':to_iso(NOW),'cli_finished_at_utc':None}
    append_record(tmp_path/'forecasts.jsonl',row)
    append_record(tmp_path/'scheduler_jobs.jsonl',{'campaign_id':'c','run_id':'r','question_snapshot_id':'s',
        'player':'gpt','tier':1,'started_at':to_iso(NOW),'finished_at':to_iso(NOW+timedelta(seconds=30))})
    out=C.build(tmp_path,{'campaign_id':'c','questions':[{'question_id':1}]})
    assert out['cells']['gpt.tier1']['missing_cli_timing_attempts']==1
    assert out['cells']['gpt.tier1']['job_non_cli_seconds_excludes_preflight']['n']==0


def test_malformed_start_identity_fails_closed(tmp_path):
    import pytest
    append_record(tmp_path/'forecast_attempt_starts.jsonl',{'question_id':1})
    with pytest.raises(ValueError,match='identity'):
        C.build(tmp_path,{'campaign_id':'c','questions':[{'question_id':1}]})


def test_missing_quota_has_explicit_unknown_layout():
    import quota
    assert quota.argon_buckets(None).layout=='unknown'
