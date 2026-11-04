"""D128 selector tests use metadata only, never live forecasts or outcomes."""
import copy
import json
from pathlib import Path

import pytest

from flab.ids import hash_obj
from flab.itt_cohort import BOT_ID, CAMPAIGN_ID, CAMPAIGN_SHA256, END, START, select_cohort

T1 = "2026-11-03T05:00:00Z"
DECISION = "2026-11-03T06:00:00Z"
LATER = "2026-11-03T07:00:00Z"
AS_OF = "2026-11-12T00:00:00Z"


def fixture():
    campaign = json.loads((Path(__file__).parents[1] / 'docs/tasks/D118-MINIBENCH-EARLY.json').read_text())
    assert hash_obj(campaign) == CAMPAIGN_SHA256
    return dict(
        questions=[dict(type='question_snapshot', snapshot_id='s1', question_id=1, tournament_id=33135,
                        ground='minibench', seen_at=T1)],
        forecasts=[dict(type='forecast_attempt', attempt_id='a1', question_id=1, question_snapshot_id='s1',
                        tier=1, run_id='run1', started_at_utc=T1, finished_at_utc=T1)],
        aggregates=[dict(type='aggregate', record_id='root1', stage='tier1', mode='local',
                         aggregation_version='v1', aggregation_config_sha256='test_config',
                         question_id=1, question_snapshot_id='s1', input_attempt_ids=['a1'],
                         created_at_utc=DECISION, escalation={'candidate': True, 'evaluated_at_utc': DECISION})],
        scheduler_runs=[dict(type='scheduler_run', run_id='run1', campaign_id=CAMPAIGN_ID, mode='live',
                             continuous=True, started_at=T1, finished_at=LATER)],
        reconciliation_runs=[dict(type='reconciliation_run', record_id='rr1', run_id='run1',
                                   tier1_aggregate_id='root1', question_id=1, question_snapshot_id='s1',
                                   started_at_utc=DECISION, finished_at_utc=LATER)],
        campaigns=[dict(type='continuous_campaign_registered', at=START, campaign=campaign,
                       sha256=hash_obj(campaign))],
        live_authorizations=[dict(type='live_authorization', recorded_at=START, campaign=campaign,
                                  bot_id=BOT_ID, campaign_sha256=hash_obj(campaign))],
        budget_events=[dict(type='budget_reserved', at=T1, attempt_id='a1', question_id=1,
                            run_id='run1', campaign_id=CAMPAIGN_ID)], as_of=AS_OF,
    )


def select(d):
    return select_cohort(**d)


def test_live_unstarted_unsubmitted_included_and_metadata_only():
    d = fixture(); before = copy.deepcopy(d)
    got = select(d)
    assert d == before
    assert got['counts'] == dict(included=1, excluded=0, unknown=0)
    assert got['decision_capture_complete'] is False
    assert got['capture_limitations'] == ['first_candidate_decisions_not_durably_logged']
    assert not got['complete_membership']
    assert got['observed_membership_complete']
    e = got['episodes'][0]
    assert e['tier2_attempt_ids'] == []
    assert e['root_aggregate_id'] == 'root1'
    assert e['tier1_aggregate_ids'] == ['root1']
    assert e['needs_complete_capture']
    assert e['decision_time_basis'] == 'first_recorded_candidate_aggregate'
    # Full rows cannot leak prediction contents into evidence hashes/output.
    d['forecasts'][0].update(probability=.99, reasoning_summary='DO NOT EXPOSE')
    d['questions'][0]['resolution'] = 'yes'
    assert select(d) == got


@pytest.mark.parametrize('decision,status,reason', [
    (START, 'included', None),
    ('2026-11-03T04:12:32.155Z', 'excluded', 'before_live_start'),
    (END, 'excluded', 'after_cohort_end'),
])
def test_half_open_interval(decision,status,reason):
    d=fixture()
    for f in d['forecasts']: f['started_at_utc']=f['finished_at_utc']=min(START,decision)
    d['questions'][0]['seen_at']='2026-11-03T04:00:00Z'
    d['scheduler_runs'][0].update(started_at='2026-11-03T04:00:00Z',finished_at=END)
    d['reconciliation_runs'][0].update(started_at_utc='2026-11-03T04:00:00Z',finished_at_utc=END)
    d['aggregates'][0].update(created_at_utc=decision,escalation={'candidate':True,'evaluated_at_utc':decision})
    e=select(d)['episodes'][0]
    assert e['status']==status
    if reason: assert reason in e['reasons']


def test_late_trial_retry_stays_with_old_root_even_with_live_run():
    d=fixture(); old='2026-11-02T16:00:00Z'
    d['forecasts'][0].update(started_at_utc=old,finished_at_utc=old)
    d['questions'][0]['seen_at']=old
    d['aggregates'][0].update(created_at_utc=old,escalation={'candidate':True})
    d['forecasts'].extend([
        dict(type='forecast_attempt',attempt_id='t2old',question_id=1,question_snapshot_id='s1',tier=2,
             input_attempt_ids=['a1'],started_at_utc=old,finished_at_utc=old),
        dict(type='forecast_attempt',attempt_id='t2retry',question_id=1,question_snapshot_id='s1',tier=2,
             prev_attempt_id='t2old',input_attempt_ids=['a1'],run_id='run1',started_at_utc=LATER,finished_at_utc=LATER),
    ])
    duplicate=copy.deepcopy(d['aggregates'][0]);duplicate.update(record_id='root2',created_at_utc=DECISION)
    d['aggregates'].append(duplicate)
    got=select(d);assert got['counts']==dict(included=0,excluded=1,unknown=0)
    e=got['episodes'][0];assert e['tier2_attempt_ids']==['t2old','t2retry']
    assert e['tier1_aggregate_ids']==['root1','root2']


@pytest.mark.parametrize('change,reason', [
    ('no_auth','missing_or_conflicting_bot_authorization'),
    ('wrong_bot','missing_or_conflicting_bot_authorization'),
    ('no_run','missing_live_decision_run'),
    ('wrong_campaign','conflicting_campaign_or_mode'),
    ('no_time','invalid_decision_time'),
    ('conflicting_budget','conflicting_budget_campaign'),
    ('invalid_registration','missing_or_conflicting_campaign_registration'),
    ('missing_t1','missing_or_conflicting_attempt_ancestry'),
])
def test_missing_or_conflicting_evidence_never_silently_excluded(change,reason):
    d=fixture()
    if change=='no_auth':d['live_authorizations']=[]
    elif change=='wrong_bot':d['live_authorizations'][0]['bot_id']=999
    elif change=='no_run':d['scheduler_runs']=[]
    elif change=='wrong_campaign':d['scheduler_runs'][0]['campaign_id']='different'
    elif change=='no_time':d['aggregates'][0].pop('created_at_utc')
    elif change=='conflicting_budget':d['budget_events'][0]['campaign_id']='different'
    elif change=='invalid_registration':d['campaigns'][0]['sha256']='bad'
    elif change=='missing_t1':d['forecasts']=[]
    got=select(d);assert got['counts']['unknown']==1
    assert reason in got['episodes'][0]['reasons']
    assert not got['complete_membership']


def test_post_window_completion_keeps_original_cohort_and_final_binding():
    d=fixture()
    d['forecasts'].append(dict(type='forecast_attempt',attempt_id='t2',question_id=1,question_snapshot_id='s1',
                              tier=2,input_attempt_ids=['a1'],started_at_utc=DECISION,
                              finished_at_utc='2026-11-11T00:00:00Z'))
    d['aggregates'].append(dict(type='aggregate',record_id='final',stage='final',question_id=1,
                               question_snapshot_id='s1',input_attempt_ids=['t2'],created_at_utc='2026-11-11T00:01:00Z'))
    e=select(d)['episodes'][0]
    assert e['status']=='included'
    assert e['tier2_attempt_ids']==['t2']
    assert 'final' in e['aggregate_ids']


def test_distinct_same_question_roots_and_ambiguous_final_never_bound():
    d=fixture()
    second=copy.deepcopy(d['forecasts'][0]);second['attempt_id']='a2';d['forecasts'].append(second)
    root=copy.deepcopy(d['aggregates'][0]);root.update(record_id='root2',input_attempt_ids=['a2'])
    d['aggregates'].append(root)
    d['aggregates'].append(dict(type='aggregate',record_id='mixed',stage='final',question_id=1,
                               question_snapshot_id='s1',input_attempt_ids=['a1','a2'],created_at_utc=LATER))
    got=select(d);assert len(got['episodes'])==2
    assert all('mixed' not in e['aggregate_ids'] for e in got['episodes'])


def test_orphan_tier2_and_missing_time_survive_as_unknown():
    d=fixture();d['aggregates']=[]
    d['reconciliation_runs'][0]['reconciliation_id']='bundle1'
    d['forecasts'].append(dict(type='forecast_attempt',attempt_id='orphan',question_id=1,
                              question_snapshot_id='s1',tier=2,input_attempt_ids=['a1'],reconciliation_id='bundle1'))
    got=select(d)
    assert got['counts']['unknown']==1
    assert got['episodes'][0]['tier2_attempt_ids']==['orphan']


def test_future_rows_ignored_but_unknown_timestamps_retained():
    d=fixture();d['as_of']='2026-11-03T08:00:00Z'
    fut=copy.deepcopy(d['aggregates'][0]);fut.update(record_id='future',created_at_utc='2026-11-05T00:00:00Z',escalation={'candidate':True})
    d['aggregates'].append(fut)
    assert select(d)['episodes'][0]['tier1_aggregate_ids']==['root1']
    fut['created_at_utc']='malformed'
    assert select(d)['episodes'][0]['status']=='unknown'


def test_scheduler_tier2_preflight_without_root_is_visible_unknown_and_deduplicated():
    d=fixture();d['aggregates']=[];d['reconciliation_runs']=[]
    d['scheduler_jobs']=[dict(type='scheduler_job_finished',record_id=f'job{i}',run_id='run1',
                             question_id=1,question_snapshot_id='s1',tier=2,campaign_id=CAMPAIGN_ID,
                             started_at=DECISION,finished_at=LATER) for i in range(3)]
    got=select(d)
    assert got['counts']==dict(included=0,excluded=0,unknown=1)
    e=got['episodes'][0]
    assert e['root_aggregate_id'] is None
    assert e['scheduler_job_ids']==['job0','job1','job2']
    assert 'scheduler_tier2_job_without_root' in e['reasons']


def test_job_explicit_root_can_prove_live_decision_without_reconciliation_summary():
    d=fixture();d['reconciliation_runs']=[]
    d['scheduler_jobs']=[dict(type='scheduler_job_finished',record_id='job1',run_id='run1',
                             tier1_aggregate_id='root1',question_id=1,question_snapshot_id='s1',tier=2,
                             campaign_id=CAMPAIGN_ID,started_at=DECISION,finished_at=LATER)]
    assert select(d)['counts']==dict(included=1,excluded=0,unknown=0)
    d['scheduler_jobs'][0].pop('tier1_aggregate_id')
    assert select(d)['counts']==dict(included=0,excluded=0,unknown=2)


def test_live_t1_run_alone_does_not_prove_candidate_was_evaluated_live():
    d=fixture();d['reconciliation_runs']=[]
    e=select(d)['episodes'][0]
    assert e['status']=='unknown'
    assert 'missing_live_decision_run' in e['reasons']


def test_missing_live_root_link_timestamp_is_unknown():
    d=fixture();d['reconciliation_runs'][0].pop('started_at_utc')
    e=select(d)['episodes'][0]
    assert e['status']=='unknown'
    assert 'invalid_live_decision_linkage' in e['reasons']


def test_repair_aliases_are_not_necessarily_equivalent_baselines():
    d=fixture()
    d['forecasts'].append(dict(type='forecast_attempt',attempt_id='a2',prev_attempt_id='a1',
                              question_id=1,question_snapshot_id='s1',tier=1,
                              started_at_utc=DECISION,finished_at_utc=DECISION))
    later=copy.deepcopy(d['aggregates'][0])
    later.update(record_id='root2',input_attempt_ids=['a2'],created_at_utc=LATER,
                 escalation={'candidate':True,'evaluated_at_utc':LATER})
    d['aggregates'].append(later)
    e=select(d)['episodes'][0]
    assert e['tier1_aggregate_ids']==['root1']
    assert e['related_tier1_aggregate_ids']==['root1','root2']


@pytest.mark.parametrize('bad', [7,['a1',7],['a1',{}]])
def test_malformed_candidate_inputs_are_unknown_not_crashes(bad):
    d=fixture();d['aggregates'][0]['input_attempt_ids']=bad
    assert select(d)['counts']['unknown']==1


def test_contradictory_project_membership_is_unknown():
    d=fixture();d['questions'][0]['project_ids']=[100]
    assert 'conflicting_tournament_membership' in select(d)['episodes'][0]['reasons']


@pytest.mark.parametrize('fault,reason', [
    ('missing','unresolved_evidence_run'),
    ('conflicting','conflicting_run_metadata'),
    ('undated','undated_evidence_run'),
    ('reversed','undated_evidence_run'),
])
def test_all_linked_evidence_runs_fail_closed_despite_valid_live_decision(fault,reason):
    d=fixture();d['forecasts'][0]['run_id']='origin_run'
    run=copy.deepcopy(d['scheduler_runs'][0]);run['run_id']='origin_run'
    if fault!='missing':d['scheduler_runs'].append(run)
    if fault=='conflicting':
        conflicting=copy.deepcopy(run);conflicting['campaign_id']='trial'
        d['scheduler_runs'].append(conflicting)
    elif fault=='undated':run.pop('started_at')
    elif fault=='reversed':run.update(started_at=LATER,finished_at=T1)
    e=select(d)['episodes'][0]
    assert e['status']=='unknown'
    assert reason in e['reasons']


def test_live_recorded_root_with_pre_live_ancestry_is_unknown():
    d=fixture();d['forecasts'][0]['started_at_utc']='2026-11-03T04:00:00Z'
    e=select(d)['episodes'][0]
    assert e['status']=='unknown'
    assert 'ancestry_predates_live_start' in e['reasons']


def test_historical_job_campaign_does_not_override_proven_exclusion():
    d=fixture();old='2026-11-02T16:00:00Z'
    d['questions'][0]['seen_at']=old
    d['forecasts'][0].update(started_at_utc=old,finished_at_utc=old)
    d['aggregates'][0].update(created_at_utc=old,escalation={'candidate':True})
    d['scheduler_jobs']=[dict(type='scheduler_job_finished',record_id='job-old',tier=2,
                             tier1_aggregate_id='root1',question_id=1,question_snapshot_id='s1',
                             campaign_id='old_trial',run_id='old_run',started_at=old,finished_at=old)]
    e=select(d)['episodes'][0]
    assert e['status']=='excluded'
    assert e['reasons']==['before_live_start']


def test_orphan_reconciliation_without_forecast_or_job_is_unknown():
    d=fixture();d['aggregates']=[]
    e=select(d)['episodes'][0]
    assert e['status']=='unknown'
    assert e['reconciliation_run_ids']==['rr1']
    assert 'reconciliation_run_without_root' in e['reasons']


def test_orphan_job_reconciliation_and_attempt_share_one_provenance_diagnostic():
    d=fixture();d['aggregates']=[]
    d['reconciliation_runs'][0].update(reconciliation_id='bundle1',selected_attempt_id='t2')
    d['forecasts'].append(dict(type='forecast_attempt',attempt_id='t2',question_id=1,
                              question_snapshot_id='s1',tier=2,input_attempt_ids=['a1'],
                              reconciliation_id='bundle1',started_at_utc=DECISION,finished_at_utc=LATER))
    d['scheduler_jobs']=[dict(type='scheduler_job_finished',record_id='job1',tier=2,
                             tier1_aggregate_id='root1',reconciliation_id='bundle1',
                             question_id=1,question_snapshot_id='s1',campaign_id=CAMPAIGN_ID,
                             run_id='run1',started_at=DECISION,finished_at=LATER)]
    got=select(d);assert got['counts']==dict(included=0,excluded=0,unknown=1)
    e=got['episodes'][0]
    assert e['tier2_attempt_ids']==['t2']
    assert e['scheduler_job_ids']==['job1']
    assert e['reconciliation_run_ids']==['rr1']
    assert {'missing_or_ambiguous_root','scheduler_tier2_job_without_root',
            'reconciliation_run_without_root'} <= set(e['reasons'])


def test_ambiguous_jobs_and_reconciliation_mark_every_matching_root():
    d=fixture()
    other=copy.deepcopy(d['forecasts'][0]);other['attempt_id']='a2';d['forecasts'].append(other)
    other_root=copy.deepcopy(d['aggregates'][0]);other_root['input_attempt_ids']=['a2']
    d['aggregates'].append(other_root)
    d['scheduler_jobs']=[dict(type='scheduler_job_finished',record_id='job1',tier=2,
                             tier1_aggregate_id='root1',question_id=1,question_snapshot_id='s1',
                             campaign_id=CAMPAIGN_ID,run_id='run1',started_at=DECISION,finished_at=LATER)]
    got=select(d)
    roots=[e for e in got['episodes'] if e['root_aggregate_id']]
    assert len(roots)==2
    assert all(e['status']=='unknown' for e in roots)
    assert all({'ambiguous_scheduler_job_root','ambiguous_reconciliation_root'} <= set(e['reasons']) for e in roots)


def test_conflicting_live_reconciliation_campaign_is_unknown():
    d=fixture();d['reconciliation_runs'][0]['campaign_id']='wrong'
    e=select(d)['episodes'][0]
    assert e['status']=='unknown'
    assert 'conflicting_reconciliation_campaign' in e['reasons']


def test_t0_snapshot_and_utc_scheduler_time_aliases_are_supported():
    d=fixture();d['questions'][0]['t0']=d['questions'][0].pop('seen_at')
    run=d['scheduler_runs'][0]
    run['started_at_utc']=run.pop('started_at');run['finished_at_utc']=run.pop('finished_at')
    assert select(d)['counts']==dict(included=1,excluded=0,unknown=0)
