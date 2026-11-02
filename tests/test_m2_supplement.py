from datetime import timedelta
import copy
import json

import pytest
import forecast as F
import m2_supplement as M
import scheduler as S
from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import to_iso
from test_forecast import NOW
from test_scheduler import campaign, environment


def registered(tmp_path, monkeypatch):
    monkeypatch.setattr(M, 'utc_now', lambda: NOW)
    parent = campaign(20)
    parent['campaign_id'] = 'parent'
    S.register(tmp_path, parent, now=NOW)
    child = campaign(3)
    child['campaign_id'] = 'child'
    child['questions'] = [{**q, 'question_id': q['question_id'] + 50} for q in child['questions']]
    S.register(tmp_path, child, now=NOW)
    append_record(tmp_path / 'm2_supplements.jsonl', {'type': 'supplement_registered', 'version': M.VERSION,
        'supplement_id': 'child', 'parent_id': 'parent', 'campaign_sha256': hash_obj(child),
        'max_calls': 6, 'max_seconds': 3600, 'runtime_fingerprint': F.runtime_fingerprint('gemini')})
    return parent, child


def test_registration_matches_parent_and_original_window(tmp_path, monkeypatch):
    parent, child = registered(tmp_path, monkeypatch)
    rec, _ = M.registration(tmp_path, child)
    assert rec['parent_id'] == parent['campaign_id']
    child['weekly_attempt_caps']['gemini'] = 130
    with pytest.raises(ValueError, match='drifted'):
        M.registration(tmp_path, child)


@pytest.mark.parametrize('field,value', [('max_calls', 7), ('max_seconds', 3601), ('version', 'v2')])
def test_contract_drift_fails(tmp_path, monkeypatch, field, value):
    _, child = registered(tmp_path, monkeypatch)
    rows = read_complete(tmp_path / 'm2_supplements.jsonl'); rows[0][field] = value
    (tmp_path / 'm2_supplements.jsonl').write_text(json.dumps(rows[0])+'\n')
    with pytest.raises(ValueError, match='contract changed'):
        M.registration(tmp_path, child)


def test_cannot_prepare_second_supplement(tmp_path, monkeypatch):
    parent, _ = registered(tmp_path, monkeypatch)
    monkeypatch.setattr(M.m2_prepare, 'prepare', lambda *a, **k: pytest.fail('must not query/select'))
    with pytest.raises(ValueError, match='already registered'):
        M.prepare(tmp_path, parent)


@pytest.mark.parametrize('bad', ['old_window', 'cap', 'config', 'unregistered'])
def test_parent_guard(tmp_path, monkeypatch, bad):
    parent, _ = registered(tmp_path, monkeypatch)
    if bad == 'old_window':
        monkeypatch.setattr(M, 'utc_now', lambda: NOW + timedelta(days=8))
    elif bad == 'cap': parent['weekly_attempt_caps']['gemini'] = 131
    elif bad == 'config': parent['run_config_sha256'] = 'wrong'
    else: parent['campaign_id'] = 'other'
    with pytest.raises(ValueError): M.parent_check(tmp_path, parent)


def test_actual_calls_cap_survives_restart(tmp_path, monkeypatch):
    cfg = campaign(3)
    for i in range(6):
        append_record(tmp_path / 'forecast_attempt_starts.jsonl', {'attempt_id': str(i), 'question_id': 100 + i % 3})
    monkeypatch.setattr(M.B.Budget, 'reserve', lambda *a, **k: pytest.fail('base admission reached'))
    budget = M.BoundedBudget(tmp_path, cfg, {}, 'r')
    with pytest.raises(F.QuestionDone, match='actual-call cap'):
        budget.reserve({'player': 'gemini', 'tier': 1, 'question_id': 102}, pending_tier1=0, expected_new=0, repair_reserve=2)


@pytest.mark.parametrize('player,tier,qid', [('gpt',1,100),('gemini',2,100),('gemini',1,999)])
def test_scope_cannot_broaden(tmp_path, player, tier, qid):
    with pytest.raises(F.QuestionDone, match='scope'):
        M.BoundedBudget(tmp_path, campaign(), {}, 'r').reserve({'player': player, 'tier': tier, 'question_id': qid})


def test_concurrent_agy_aborts_without_budget_or_call(tmp_path, monkeypatch):
    monkeypatch.setattr(M.quota, 'detect_agy_activity', lambda: ({'interactive':1}, None))
    with pytest.raises(F.QuestionDone, match='concurrent'):
        M.BoundedBudget(tmp_path, campaign(), {}, 'r').reserve({'player':'gemini','tier':1,'question_id':100})
    assert not (tmp_path / 'budget_events.jsonl').exists()


def test_real_scheduler_keeps_safety_and_only_gemini_tier1(environment, monkeypatch):
    engine, snaps, tracker, _ = environment(3)
    engine.__class__ = M.SupplementScheduler
    engine.budget.__class__ = M.BoundedBudget
    monkeypatch.setattr(M.quota, 'detect_agy_activity', lambda: ({}, None))
    engine.run(max_jobs=3, paused_players=('gpt', 'claude'))
    assert len(tracker['calls']) == 3
    assert all(p == 'gemini' and profile == 'tier1' for p, profile, _ in tracker['calls'])
    assert not read_complete(engine.root / 'aggregates.jsonl')
    assert len(read_complete(engine.root / 'worker_timings.jsonl')) == 3
    decisions = read_complete(engine.root / 'budget_events.jsonl')
    assert len([r for r in decisions if r['type'] == 'budget_reserved']) == 3


def test_untrusted_sample_stops_further_jobs(environment, monkeypatch):
    engine, snaps, tracker, _ = environment(3)
    engine.__class__ = M.SupplementScheduler
    monkeypatch.setattr(M.quota, 'attempt_drop', lambda *a, **kw: None)
    engine.run(max_jobs=3, paused_players=('gpt', 'claude'))
    assert len(tracker['calls']) == 1


def test_existing_weekly_cap_not_reset(environment, monkeypatch):
    engine, snaps, tracker, _ = environment(1)
    engine.__class__ = M.SupplementScheduler
    engine.budget.__class__ = M.BoundedBudget
    monkeypatch.setattr(M.quota, 'detect_agy_activity', lambda: ({}, None))
    engine.campaign['weekly_attempt_caps']['gemini'] = 1
    append_record(engine.root / 'forecasts.jsonl', {'type':'forecast_attempt','record_id':'fc-old','question_snapshot_id':'snap-old','attempt_id':'old', 'player':'gemini','question_id':999,
        'tier':1,'started_at_utc': to_iso(NOW), 'finished_at_utc':to_iso(NOW), 'usage':{}})
    engine.run(max_jobs=3, paused_players=('gpt', 'claude'))
    assert not tracker['calls']


def test_started_supplement_never_resumes(tmp_path, monkeypatch):
    _, child = registered(tmp_path, monkeypatch)
    append_record(tmp_path / 'm2_supplements.jsonl', {'type':'supplement_started','supplement_id':'child'})
    monkeypatch.setattr(M.quota, 'detect_agy_activity', lambda: pytest.fail('reached execution'))
    with pytest.raises(ValueError, match='one-shot'):
        M.worker(tmp_path, child)


def test_supervisor_reuses_descendant_cleanup_and_reserved_time(tmp_path, monkeypatch):
    cfg=campaign(); path=tmp_path/'c.json';path.write_text(json.dumps(cfg))
    monkeypatch.setattr(F,'DATA',tmp_path)
    calls=[]
    monkeypatch.setattr(M,'supervise',lambda argv,**kw: (calls.append((argv,kw)) or (124,True)))
    assert M.main(['run','--campaign',str(path)])==124
    assert calls[0][1]['timeout_s']==3600-M.CLEANUP_RESERVE_S
    row=read_complete(tmp_path/'m2_supplements.jsonl')[-1]
    assert row['timed_out'] and not row['automatic_retry']
