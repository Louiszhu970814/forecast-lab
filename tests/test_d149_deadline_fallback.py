"""D149: submit before close whenever >=1 model has a complete forecast answer."""
import pytest

import aggregate as A
import comment as C
import numeric as N
import submit as S
from test_aggregate import BIN, MC, answer
from test_comment import row
from test_numeric import snapshot as numeric_snapshot


def fallback(snap, rows):
    empty = A.build_aggregate(snap, rows, stage="final")
    assert empty["status"] == "no_valid_inputs"
    return A.apply_fallback(empty, snap, rows)


@pytest.mark.parametrize("snap", [BIN, MC, numeric_snapshot(), numeric_snapshot(open_lower_bound=True, open_upper_bound=True),
                                  numeric_snapshot("discrete", n=20)])
def test_zero_model_answers_stays_no_valid_inputs(snap):
    snap = {**snap, "snapshot_id": "s1"}
    agg = fallback(snap, [])
    assert agg["status"] == "no_valid_inputs" and "fallback_version" not in agg


@pytest.mark.parametrize("snap", [BIN, MC, numeric_snapshot(), numeric_snapshot("discrete", n=20)])
def test_single_held_back_complete_answer_is_salvaged_and_submittable(snap):
    snap = {**snap, "snapshot_id": "s1"}
    if snap["question_type"] == "binary":
        r = row("claude", .35, snapshot=snap, model_check="mismatch")
    elif snap["question_type"] == "multiple_choice":
        r = row("claude", {"A": .5, "B": .3, "C": .2}, snapshot=snap, model_check="mismatch")
    else:
        pct = {k: 1.0 + i * 2.0 for i, k in enumerate(N.KEYS)}
        r = row("claude", None, snapshot=snap, model_check="mismatch", **N.cdf_fields(pct, snap))
    agg = {**fallback(snap, [r]), "submission_eligible": True, "scheduler_campaign_id": "c"}
    assert agg["status"] == "ready" and agg["fallback"] == "salvaged_inputs" and agg["input_attempt_ids"] == ["claude_1"]
    assert S.forecast_payload(snap, agg)
    comment = C.build(snap, agg, [r, agg], [])
    assert "Deadline fallback" in comment["text"] and comment["fallback"] == "salvaged_inputs"


def test_answers_held_back_by_a_check_are_combined_by_agg_v1():
    t1 = row(tool_violations=["Bash"])
    t2 = row("gemini", .5, aid="gemini_t2", tier=2, input_attempt_ids=["claude_1"],
             model_check="mismatch", change_reason="Updated after reviewing tier-1 evidence.")
    rows = [t1, t2]
    agg = fallback(BIN, rows)
    assert agg["fallback"] == "salvaged_inputs" and agg["input_attempt_ids"] == ["claude_1", "gemini_t2"]
    assert agg["probability"] == A.combine(BIN, rows, A.load_agg_config())["probability"]
    text = C.build(BIN, agg, [*rows, agg], [])["text"]
    assert "Claude" in text and "Gemini" in text and "tool\\_scope\\_violation" in text
    assert "Tier\\-2 judgement: Updated after reviewing tier\\-1 evidence\\." in text
    long_rows = [
        {**row("claude", .4, aid="c_t2", tier=2, input_attempt_ids=["claude_1"], model_check="mismatch", change_reason="step. " * 250),
         "reasoning_summary": "word. " * 250},
        {**row("gemini", .5, aid="g_t2", tier=2, input_attempt_ids=["claude_1"], model_check="mismatch", change_reason="step. " * 250),
         "reasoning_summary": "word. " * 250},
        {**row("gpt", .6, aid="p_t2", tier=2, input_attempt_ids=["claude_1"], model_check="mismatch", change_reason="step. " * 250),
         "reasoning_summary": "word. " * 250},
    ]
    long_agg = fallback(BIN, [t1, *long_rows])
    long_comment = C.build(BIN, long_agg, [t1, *long_rows, long_agg], [])
    assert len(long_comment["text"]) <= C.MAX_CHARS and "Reason:" in long_comment["text"]


def test_malformed_incomplete_or_leaked_answers_are_not_salvaged():
    rows = [
        answer(p=1.5, reasoning_summary="Valid prose."),
        answer("gemini", .4, validation_status="invalid", reasoning_summary="Valid prose."),
        answer("gpt", .4, model_check="mismatch", reasoning_summary="   "),
        row("claude", .4, aid="claude_t2", tier=2, input_attempt_ids=["claude_1"], model_check="mismatch", change_reason=""),
        row("gemini", .4, aid="gemini_leak", audit_flags=["leak_confirmed"]),
    ]
    assert fallback(BIN, rows)["status"] == "no_valid_inputs"


def test_same_model_incomplete_then_complete_answer_is_salvaged_unless_leaked():
    scoped_bin = {**BIN, "forecast_scope": "snapshot"}
    clean_incomplete = answer("claude", .3, aid="claude_clean_1", reasoning_summary="")
    held_back_complete = row("claude", .35, aid="claude_mismatch", model_check="mismatch")
    clean_complete = row("claude", .4, aid="claude_clean_2")
    normal = A.build_aggregate(scoped_bin, [clean_incomplete, held_back_complete, clean_complete], stage="final")
    assert normal["status"] == "ready" and normal["input_attempt_ids"] == ["claude_clean_2"]
    assert "fallback_version" not in normal
    assert A.salvage_inputs(scoped_bin, [clean_incomplete, clean_complete]) == []
    incomplete = answer("claude", .3, aid="claude_1", model_check="mismatch", reasoning_summary="")
    complete = row("claude", .4, aid="claude_2", model_check="mismatch")
    agg = fallback(BIN, [incomplete, complete])
    assert agg["status"] == "ready" and agg["input_attempt_ids"] == ["claude_2"]
    adopted_after_mismatch_incomplete = A.build_aggregate(scoped_bin, [incomplete, clean_complete], stage="final")
    assert adopted_after_mismatch_incomplete["status"] == "ready"
    assert adopted_after_mismatch_incomplete["input_attempt_ids"] == ["claude_clean_2"]
    assert "fallback_version" not in adopted_after_mismatch_incomplete
    leaked_invalid = answer("claude", .3, aid="claude_leak_invalid", validation_status="invalid", audit_flags=["leak_confirmed"])
    agg_after_unlinked_invalid_leak = fallback(BIN, [leaked_invalid, complete])
    assert agg_after_unlinked_invalid_leak["status"] == "ready" and agg_after_unlinked_invalid_leak["input_attempt_ids"] == ["claude_2"]
    leaked_parent = row("claude", .3, aid="claude_leak", audit_flags=["leak_confirmed"])
    linked_child = row("claude", .4, aid="claude_child", prev_attempt_id="claude_leak", model_check="mismatch")
    assert fallback(BIN, [leaked_parent, linked_child])["status"] == "no_valid_inputs"


def test_late_audit_leak_and_corrupted_audit_events_fail_closed():
    r = row("claude", .4, model_check="mismatch")
    leak_event = {"audit_event_id": "aud_1", "attempt_id": "claude_1", "question_id": BIN["question_id"],
                  "question_snapshot_id": BIN["snapshot_id"], "audit_flags": ["leak_confirmed"]}
    assert A.salvage_inputs(BIN, [r], audit_events=[leak_event]) == []
    bad_event = {"audit_event_id": "aud_bad", "attempt_id": "unknown_attempt", "audit_flags": []}
    assert A.salvage_inputs(BIN, [r], audit_events=[bad_event]) == []
    leaked_with_bad = [row("claude", .4, audit_flags=["leak_confirmed"])]
    assert A.salvage_inputs(BIN, leaked_with_bad, audit_events=[bad_event]) == []


def test_preflight_exceptions_become_warnings_and_keep_all_hand_switches(monkeypatch):
    import forecast as F
    monkeypatch.setattr(F, "recover_acceptance", lambda *a, **kw: (_ for _ in ()).throw(F.PreflightError("boom")))
    pf1 = F.preflight("claude", tier=1, players_enabled={"claude": True}, submit_mode={"mode": "local"})
    assert pf1.ok and any("PreflightError" in w for w in pf1.warnings)
    bad_cfg = F.load_run_config()
    bad_cfg["players"]["claude"]["tier2"]["subagents_enabled"] = True
    pf2 = F.preflight("claude", tier=2, cfg=bad_cfg, players_enabled={"claude": True}, submit_mode={"mode": "local"})
    assert not pf2.ok and "tier-2 subagents are not enabled by this pipeline" in pf2.problems
    monkeypatch.setattr(F, "load_run_config", lambda *a, **kw: (_ for _ in ()).throw(ValueError("broken cfg")))
    pf3 = F.preflight("claude", tier=2, players_enabled={"claude": True}, submit_mode={"mode": "local"})
    assert pf3.ok and any("ValueError" in w for w in pf3.warnings)
    pf4 = F.preflight("claude", tier=2, players_enabled={"claude": False}, submit_mode={"mode": "local"})
    assert not pf4.ok and "line is disabled in players_enabled.json" in pf4.problems
    monkeypatch.setattr(F, "load_players_enabled", lambda *a, **kw: (_ for _ in ()).throw(OSError("unreadable")))
    pf5 = F.preflight("claude", tier=1, submit_mode={"mode": "local"})
    assert pf5.ok and any("OSError" in w for w in pf5.warnings)
    pf6 = F.preflight("claude", tier=1, submit_mode={"mode": "live"}, continuous=False)
    assert not pf6.ok and any(p.startswith("submit_mode is") for p in pf6.problems)


def test_straggler_skip_requires_complete_explanation(environment):
    from datetime import timedelta
    from flab.jsonl import append_record
    from flab.timeutil import to_iso
    from test_forecast import NOW
    obj, snaps, _, _ = environment(count=1, continuous=True)
    snap = snaps[100]
    snap["scheduled_close_time"] = to_iso(NOW + timedelta(days=3))
    append_record(obj.root / "forecasts.jsonl", answer("claude", .4, snapshot=snap, aid="claude_no_reason", reasoning_summary=""))
    append_record(obj.jobs_log, {"type": "scheduler_job_finished", "player": "gemini", "question_id": 100,
                                 "question_snapshot_id": snap["snapshot_id"], "tier": 1, "outcome": "quota_weekly_wait",
                                 "quota_retry_at_utc": to_iso(NOW + timedelta(hours=6))})
    assert obj.finalize(snaps) == []
    # Even after the decision deadline expires, an answer without a complete explanation
    # is sealed as no_valid_inputs instead of ready/submitted; once another line provides
    # a complete answer, the empty final is replaced and finalized.
    from flab.jsonl import read_records
    snap["scheduled_close_time"] = to_iso(NOW + timedelta(seconds=1800))
    assert obj.finalize(snaps) == []
    finals = [a for a in read_records(obj.root / "aggregates.jsonl") if a.get("completion_status") == "complete_local"]
    assert finals[-1]["status"] == "no_valid_inputs" and not (obj.root / "comments.jsonl").exists()
    append_record(obj.root / "forecasts.jsonl", row("gemini", .5, snapshot=snap, aid="gemini_complete"))
    assert obj.finalize(snaps) == [100]
    finals = [a for a in read_records(obj.root / "aggregates.jsonl") if a.get("completion_status") == "complete_local"]
    assert finals[-1]["status"] == "ready" and finals[-1]["input_attempt_ids"] == ["gemini_complete"]
    assert "fallback_version" not in finals[-1]  # normal adoptable answer; fallback was not triggered
    comments = read_records(obj.root / "comments.jsonl")
    assert "Deadline fallback" not in comments[-1]["text"]


def test_mixed_complete_and_incomplete_explanation_uses_normal_aggregate_not_fallback(environment):
    from datetime import timedelta
    from flab.jsonl import append_record, read_records
    from flab.timeutil import to_iso
    from test_forecast import NOW
    obj, snaps, _, _ = environment(count=1, continuous=True)
    snap = snaps[100]
    snap["scheduled_close_time"] = to_iso(NOW + timedelta(seconds=1800))
    append_record(obj.root / "forecasts.jsonl", row("claude", .35, snapshot=snap, aid="claude_ok"))
    append_record(obj.root / "forecasts.jsonl", answer("gemini", .8, snapshot=snap, aid="gemini_no_reason", reasoning_summary=""))
    append_record(obj.root / "forecasts.jsonl", row("gpt", .9, snapshot=snap, aid="gpt_mismatch", model_check="mismatch"))
    assert obj.finalize(snaps) == [100]
    final = [a for a in read_records(obj.root / "aggregates.jsonl") if a.get("completion_status") == "complete_local"][-1]
    assert final["status"] == "ready" and "fallback_version" not in final
    assert final["input_attempt_ids"] == ["claude_ok"]
    assert {e["player"]: e["reasons"] for e in final["excluded_inputs"]} == {
        "gemini": ["missing_explanation"], "gpt": ["model_mismatch"]}
    comment = read_records(obj.root / "comments.jsonl")[-1]
    assert comment["type"] == "comment" and "Deadline fallback" not in comment["text"]


def test_discovery_does_not_wake_empty_final_when_only_answer_has_audit_leak(tmp_path):
    from datetime import timedelta
    import discovery as D
    from flab.jsonl import append_record
    from flab.timeutil import to_iso
    from test_forecast import NOW
    snap = {**BIN, "type": "question_snapshot", "snapshot_id": "s1", "status": "open", "supported": True,
            "scheduled_close_time": to_iso(NOW + timedelta(hours=4))}
    append_record(tmp_path / "questions.jsonl", snap)
    r = row("claude", .4, snapshot=snap, model_check="mismatch", finished_at_utc=to_iso(NOW))
    append_record(tmp_path / "forecasts.jsonl", r)
    append_record(tmp_path / "aggregates.jsonl", {
        "type": "aggregate", "record_id": "agg_empty", "question_id": snap["question_id"],
        "question_snapshot_id": snap["snapshot_id"], "status": "no_valid_inputs",
        "completion_status": "complete_local"})
    known = [{"question_id": snap["question_id"], "status": "open", "scheduled_close_time": snap["scheduled_close_time"]}]
    assert D.actionable_questions(tmp_path, known, NOW) == {snap["question_id"]}
    append_record(tmp_path / "audits.jsonl", {
        "type": "audit", "audit_event_id": "aud_leak", "attempt_id": r["attempt_id"],
        "question_id": snap["question_id"], "question_snapshot_id": snap["snapshot_id"],
        "audited_at": to_iso(NOW), "audit_reasons": ["late leak confirmed"],
        "audit_flags": ["leak_confirmed"], "own_audit_flags": ["leak_confirmed"]})
    assert D.actionable_questions(tmp_path, known, NOW) == set()


def test_fallback_comment_rejects_tampered_values():
    r = row(model_check="mismatch")
    agg = {**fallback(BIN, [r]), "probability": .9}
    with pytest.raises(C.CommentError):
        C.build(BIN, agg, [r, agg], [])


def test_normal_aggregate_is_unchanged_when_inputs_exist():
    rows = [row(), row("gemini", .5)]
    agg = A.build_aggregate(BIN, rows, stage="final")
    assert agg["status"] == "ready" and "fallback_version" not in agg
    assert A.apply_fallback(agg, BIN, rows) == agg


# ---------------------------------------------------------------- D149 no indefinite line stops

def test_only_hand_set_switches_block_preflight():
    import forecast as F
    pf = F.Preflight("claude", problems=["line is disabled in players_enabled.json", "baseline check says 'stop' for this line",
                                         "acceptance did not pass"])
    F.soften(pf)
    assert pf.problems == ["line is disabled in players_enabled.json"] and not pf.ok
    assert pf.warnings == ["baseline check says 'stop' for this line", "acceptance did not pass"]


def test_recorded_stop_without_end_no_longer_stops_a_line(tmp_path):
    from datetime import timedelta
    import forecast as F
    from flab.jsonl import append_record
    from flab.timeutil import to_iso
    from test_forecast import NOW
    log = tmp_path / "line_status.jsonl"
    append_record(log, {"type": "line_stop", "player": "claude", "reason": "tool_scope_violation", "at": to_iso(NOW)})
    assert F.line_stopped(log, "claude", now=NOW) is None
    assert F.line_stopped(log, "claude", now=NOW, persistent=True)["reason"] == "tool_scope_violation"
    append_record(log, {"type": "line_stop", "player": "claude", "reason": "tool_scope_violation", "at": to_iso(NOW),
                        "until": to_iso(NOW + timedelta(minutes=5))})
    assert F.line_stopped(log, "claude", now=NOW) is None
    append_record(log, {"type": "line_stop", "player": "claude", "reason": "quota_5h_wait", "at": to_iso(NOW),
                        "until": to_iso(NOW + timedelta(minutes=5))})
    assert F.line_stopped(log, "claude", now=NOW)["reason"] == "quota_5h_wait"
    assert F.line_stopped(log, "claude", now=NOW + timedelta(minutes=6)) is None


def test_old_preflight_blocked_does_not_skip_a_line_whose_preflight_is_now_ok(environment):
    from datetime import timedelta
    import forecast as F
    from flab.jsonl import append_record, read_records
    from flab.timeutil import to_iso
    from test_forecast import NOW
    blocked = {"gemini": False}
    obj, snaps, _, _ = environment(
        count=2, continuous=True,
        preflight=lambda p, t, a: F.Preflight(p, tier=t, problems=["line is disabled in players_enabled.json"] if blocked.get(p) else []),
    )
    snap = snaps[100]
    snap["scheduled_close_time"] = to_iso(NOW + timedelta(days=3))
    for player, p in [("claude", .4), ("gpt", .45)]:
        append_record(obj.root / "forecasts.jsonl", row(player, p, snapshot=snap, aid=player))
    append_record(obj.jobs_log, {"type": "scheduler_job_finished", "run_id": "old_run", "player": "gemini",
                                 "question_id": 100, "question_snapshot_id": snap["snapshot_id"], "tier": 1,
                                 "outcome": "preflight_blocked"})
    append_record(obj.jobs_log, {"type": "scheduler_job_finished", "run_id": obj.run_id, "player": "gemini",
                                 "question_id": 101, "question_snapshot_id": snaps[101]["snapshot_id"], "tier": 1,
                                 "outcome": "preflight_blocked"})
    assert obj.finalize(snaps) == []  # gemini's current preflight is ok, so wait for gemini on q=100
    orig_loader = obj.loader
    obj.loader = lambda p: (_ for _ in ()).throw(RuntimeError("loader boom")) if p == "gemini" else orig_loader(p)
    obj._pf_cache = None
    assert obj.finalize(snaps) == []  # loader exception does not confirm a manual switch block
    obj.loader = orig_loader
    blocked["gemini"] = True
    obj._pf_cache = None
    assert obj.finalize(snaps) == [100]
    final = [a for a in read_records(obj.root / "aggregates.jsonl") if a.get("completion_status") == "complete_local"][-1]
    assert final["lines_not_waited_for"] == ["gemini"]


def test_ready_result_does_not_wait_for_a_line_in_a_long_quota_wait(environment):
    from datetime import timedelta
    from flab.jsonl import append_record, read_records
    from flab.timeutil import to_iso
    from test_forecast import NOW
    obj, snaps, _, _ = environment(count=2, continuous=True)
    snap = snaps[100]
    snap["scheduled_close_time"] = to_iso(NOW + timedelta(days=3))
    for player, p in [("claude", .4), ("gpt", .45)]:  # close enough not to request tier 2
        append_record(obj.root / "forecasts.jsonl", row(player, p, snapshot=snap, aid=player))
    append_record(obj.jobs_log, {"type": "scheduler_job_finished", "player": "gemini", "question_id": 100,
                                 "question_snapshot_id": snap["snapshot_id"], "tier": 1, "outcome": "quota_weekly_wait",
                                 "quota_retry_at_utc": to_iso(NOW + timedelta(seconds=900))})
    assert obj.finalize({100: snap}) == []  # remaining wait == 900s (STRAGGLER_HOLD_S): still waits
    append_record(obj.jobs_log, {"type": "scheduler_job_finished", "player": "gemini", "question_id": 100,
                                 "question_snapshot_id": snap["snapshot_id"], "tier": 1, "outcome": "quota_weekly_wait",
                                 "quota_retry_at_utc": to_iso(NOW + timedelta(seconds=901))})
    assert obj.finalize({100: snap}) == [100]  # remaining wait > 900s: skipped
    final = [a for a in read_records(obj.root / "aggregates.jsonl") if a.get("completion_status") == "complete_local"][-1]
    assert final["lines_not_waited_for"] == ["gemini"] and final["input_attempt_ids"] == ["claude", "gpt"]
    # Also skips when remaining wait <= 900s but wait end >= decision_deadline.
    snap2 = snaps[101]
    snap2["scheduled_close_time"] = to_iso(NOW + timedelta(minutes=35))  # decision_deadline = NOW + 5m (300s)
    for player, p in [("claude", .4), ("gpt", .45)]:
        append_record(obj.root / "forecasts.jsonl", row(player, p, snapshot=snap2, aid=f"{player}_2"))
    append_record(obj.jobs_log, {"type": "scheduler_job_finished", "player": "gemini", "question_id": 101,
                                 "question_snapshot_id": snap2["snapshot_id"], "tier": 1, "outcome": "quota_5h_wait",
                                 "quota_retry_at_utc": to_iso(NOW + timedelta(seconds=300))})
    assert obj.finalize({101: snap2}) == [101]


def test_tier1_budget_wait_straggler_does_not_block_escalated_tier2_finalization(environment):
    from datetime import timedelta
    from flab.jsonl import append_record, read_records
    from flab.timeutil import to_iso
    from test_forecast import NOW
    obj, snaps, _, _ = environment(count=1, continuous=True)
    snap = snaps[100]
    snap["scheduled_close_time"] = to_iso(NOW + timedelta(days=3))
    # Claude (.2) and GPT (.8) diverge enough to trigger tier-2 escalation, and both finish tier 2.
    append_record(obj.root / "forecasts.jsonl", row("claude", .2, snapshot=snap, aid="claude_t1"))
    append_record(obj.root / "forecasts.jsonl", row("gpt", .8, snapshot=snap, aid="gpt_t1"))
    append_record(obj.root / "forecasts.jsonl", row("claude", .45, snapshot=snap, aid="claude_t2", tier=2,
                                                    input_attempt_ids=["claude_t1", "gpt_t1"],
                                                    change_reason="Converged after checking tier-1 sources."))
    append_record(obj.root / "forecasts.jsonl", row("gpt", .55, snapshot=snap, aid="gpt_t2", tier=2,
                                                    input_attempt_ids=["claude_t1", "gpt_t1"],
                                                    change_reason="Converged after checking tier-1 sources."))
    # Gemini is stuck in a 6-hour tier-1 budget_wait and has no tier-2 job record.
    append_record(obj.jobs_log, {"type": "scheduler_job_finished", "player": "gemini", "question_id": 100,
                                 "question_snapshot_id": snap["snapshot_id"], "tier": 1, "outcome": "budget_wait",
                                 "reevaluate_after": to_iso(NOW + timedelta(hours=6))})
    assert obj.finalize(snaps) == [100]
    final = [a for a in read_records(obj.root / "aggregates.jsonl") if a.get("completion_status") == "complete_local"][-1]
    assert final["lines_not_waited_for"] == ["gemini"]
    assert final["input_attempt_ids"] == ["claude_t2", "gpt_t2"]


def test_line_that_recovered_after_earlier_quota_wait_is_not_skipped(environment):
    from datetime import timedelta
    from flab.jsonl import append_record
    from flab.timeutil import to_iso
    from test_forecast import NOW
    obj, snaps, _, _ = environment(count=2, continuous=True)
    snap = snaps[100]
    snap["scheduled_close_time"] = to_iso(NOW + timedelta(days=3))
    for player, p in [("claude", .4), ("gpt", .45)]:
        append_record(obj.root / "forecasts.jsonl", row(player, p, snapshot=snap, aid=player))
    # Gemini had an earlier quota wait on q=101, then succeeded on q=101 and is now ready for q=100.
    append_record(obj.root / "forecasts.jsonl", {
        **row("gemini", .5, snapshot=snaps[101], aid="gemini_old_quota"),
        "validation_status": "invalid", "error_type": "quota_exhausted",
        "finished_at_utc": to_iso(NOW - timedelta(minutes=10)),
        "quota_retry_at_utc": to_iso(NOW + timedelta(hours=6)),
    })
    append_record(obj.jobs_log, {"type": "scheduler_job_finished", "player": "gemini", "question_id": 101,
                                 "question_snapshot_id": snaps[101]["snapshot_id"], "tier": 1,
                                 "outcome": "quota_weekly_wait", "finished_at": to_iso(NOW - timedelta(minutes=10)),
                                 "quota_retry_at_utc": to_iso(NOW + timedelta(hours=6))})
    append_record(obj.root / "forecasts.jsonl", {
        **row("gemini", .5, snapshot=snaps[101], aid="gemini_recovered"),
        "finished_at_utc": to_iso(NOW - timedelta(minutes=1)),
    })
    assert obj.finalize(snaps) == []  # gemini has recovered, so wait for gemini on q=100


def test_salvaged_fallback_end_to_end_finalize_publish_and_submit(tmp_path):
    from datetime import timedelta
    from types import SimpleNamespace
    import cycle as CY
    import forecast as F
    import scheduler as SC
    from flab.jsonl import append_record
    from flab.timeutil import to_iso
    from poll_questions import snapshot_from_post
    from test_forecast import NOW
    from test_poll_questions import _post
    from test_submit import FakeTransport
    post = _post(17, 7, "binary", "Synthetic question", my_forecasts=[],
                 open_time=to_iso(NOW - timedelta(hours=1)), scheduled_close_time=to_iso(NOW + timedelta(seconds=1800)))
    snap = {**snapshot_from_post(post, "bot-testing-area"), "tournament_id": 32977, "snapshot_id": "s1",
            "ground": "bot_testing_area", "priority": "main", "supported": True}
    append_record(tmp_path / "questions.jsonl", snap)
    append_record(tmp_path / "forecasts.jsonl", row("claude", .35, snapshot=snap, aid="claude_salvaged", model_check="mismatch"))
    camp = {"version": "m2_campaign_v1", "campaign_id": "c_e2e", "questions": [
        {"question_id": snap["question_id"], "ground": "bot_testing_area", "priority": "main"}],
        "budget_window_start": to_iso(NOW - timedelta(days=1)), "budget_window_end": to_iso(NOW + timedelta(days=6)),
        "weekly_attempt_caps": dict.fromkeys(("claude", "gemini", "gpt"), 100),
        "expected_new_questions": 0, "repair_reserve": 1, "urgent_slack_s": 120}
    sched = SC.Scheduler(tmp_path, camp, snapshots=lambda: {snap["question_id"]: snap}, continuous=True, now=lambda: NOW)
    assert sched.finalize({snap["question_id"]: snap}) == [snap["question_id"]]
    queue = SimpleNamespace(invalidated=set(), fallbacks={})
    pub = CY.publish(tmp_path, sched, {snap["question_id"]: snap}, queue)
    assert len(pub["aggregate_ids"]) == 1 and not pub["rejected_aggregate_ids"]
    pub_repeat = CY.publish(tmp_path, sched, {snap["question_id"]: snap}, queue)
    assert pub_repeat["aggregate_ids"] == [] and pub_repeat["rejected_aggregate_ids"] == []
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "submit_mode.json").write_text('{"mode": "trial", "approved_bot_id": 42}')
    (cfg_dir / "tournaments.json").write_text(
        '{"tournaments": {"main": {"id": 33121, "slug": "main", "submit_whitelisted": true}, '
        '"minibench": {"id": 33122, "slug": "mini", "submit_whitelisted": true}, '
        '"bot_testing_area": {"id": 32977, "slug": "bot-testing-area", "submit_whitelisted": false}}, '
        '"trial_ground_types": ["bot_testing_area", "metaculus_cup", "main_site_questions"]}'
    )
    (cfg_dir / "run_config_v2.json").write_text((F.PROJECT_ROOT / "config/run_config_v2.json").read_text())
    (cfg_dir / "question_types.json").write_text('{"binary": true, "multiple_choice": true, "numeric": true, "discrete": true}')
    transport = FakeTransport(post)
    res = S.Submitter(tmp_path, config_dir=cfg_dir, transport=transport, now=lambda: NOW, sleep=lambda _: None).run()
    assert res["statuses"].get("forecast_ok") == 1 and res["statuses"].get("comment_ok") == 1
    assert [w[0] for w in transport.writes] == ["forecast", "comment"]
    assert "Deadline fallback" in transport.writes[1][1]["text"]


def test_late_leak_confirmed_after_salvaged_final_and_offer_revokes_and_reseals(tmp_path):
    from datetime import timedelta
    from types import SimpleNamespace
    import cycle as CY
    import scheduler as SC
    from flab.jsonl import append_record, read_records
    from flab.timeutil import to_iso
    from poll_questions import snapshot_from_post
    from test_forecast import NOW
    from test_poll_questions import _post
    post = _post(17, 7, "binary", "Synthetic question", my_forecasts=[],
                 open_time=to_iso(NOW - timedelta(hours=1)), scheduled_close_time=to_iso(NOW + timedelta(days=3)))
    snap = {**snapshot_from_post(post, "bot-testing-area"), "tournament_id": 32977, "snapshot_id": "s1",
            "ground": "bot_testing_area", "priority": "main", "supported": True}
    append_record(tmp_path / "questions.jsonl", snap)
    append_record(tmp_path / "forecasts.jsonl", row("claude", .35, snapshot=snap, aid="claude_salvaged", model_check="mismatch"))
    append_record(tmp_path / "scheduler_jobs.jsonl", {
        "type": "scheduler_job_finished", "player": "claude", "question_id": snap["question_id"],
        "question_snapshot_id": snap["snapshot_id"], "tier": 1, "outcome": "output_excluded"})
    for p in ("gemini", "gpt"):
        append_record(tmp_path / "scheduler_jobs.jsonl", {
            "type": "scheduler_job_finished", "player": p, "question_id": snap["question_id"],
            "question_snapshot_id": snap["snapshot_id"], "tier": 1, "outcome": "quota_weekly_wait",
            "quota_retry_at_utc": to_iso(NOW + timedelta(hours=6))})
    camp = {"version": "m2_campaign_v1", "campaign_id": "c_leak", "questions": [
        {"question_id": snap["question_id"], "ground": "bot_testing_area", "priority": "main"}],
        "budget_window_start": to_iso(NOW - timedelta(days=1)), "budget_window_end": to_iso(NOW + timedelta(days=6)),
        "weekly_attempt_caps": dict.fromkeys(("claude", "gemini", "gpt"), 100),
        "expected_new_questions": 0, "repair_reserve": 1, "urgent_slack_s": 120}
    sched = SC.Scheduler(tmp_path, camp, snapshots=lambda: {snap["question_id"]: snap}, continuous=True, now=lambda: NOW)
    assert sched.finalize({snap["question_id"]: snap}) == [snap["question_id"]]
    queue = SimpleNamespace(invalidated=set(), fallbacks={})
    pub1 = CY.publish(tmp_path, sched, {snap["question_id"]: snap}, queue)
    assert len(pub1["aggregate_ids"]) == 1
    append_record(tmp_path / "audits.jsonl", {
        "type": "audit", "audit_event_id": "aud_late_leak", "attempt_id": "claude_salvaged",
        "question_id": snap["question_id"], "question_snapshot_id": snap["snapshot_id"],
        "audited_at": to_iso(NOW), "audit_reasons": ["late leak confirmed"],
        "audit_flags": ["leak_confirmed"], "own_audit_flags": ["leak_confirmed"]})
    assert sched.finalize({snap["question_id"]: snap}) == []
    finals = [a for a in read_records(tmp_path / "aggregates.jsonl") if a.get("completion_status") == "complete_local"]
    assert finals[-1]["status"] == "no_valid_inputs"
    count_after_revoke = len(read_records(tmp_path / "aggregates.jsonl"))
    assert sched.finalize({snap["question_id"]: snap}) == []
    assert len(read_records(tmp_path / "aggregates.jsonl")) == count_after_revoke
    pub2 = CY.publish(tmp_path, sched, {snap["question_id"]: snap}, queue)
    assert pub2["aggregate_ids"] == [] and pub2["rejected_aggregate_ids"] == pub1["aggregate_ids"]


def test_clean_answer_arriving_after_salvaged_offer_supersedes_it_for_publish_and_submit(tmp_path):
    from datetime import timedelta
    from types import SimpleNamespace
    import cycle as CY
    import forecast as F
    import scheduler as SC
    from flab.jsonl import append_record
    from flab.timeutil import to_iso
    from poll_questions import snapshot_from_post
    from test_forecast import NOW
    from test_poll_questions import _post
    from test_submit import FakeTransport
    post = _post(17, 7, "binary", "Synthetic question", my_forecasts=[],
                 open_time=to_iso(NOW - timedelta(hours=1)), scheduled_close_time=to_iso(NOW + timedelta(seconds=1800)))
    snap = {**snapshot_from_post(post, "bot-testing-area"), "tournament_id": 32977, "snapshot_id": "s1",
            "ground": "bot_testing_area", "priority": "main", "supported": True}
    append_record(tmp_path / "questions.jsonl", snap)
    append_record(tmp_path / "forecasts.jsonl", row("claude", .35, snapshot=snap, aid="claude_salvaged", model_check="mismatch"))
    camp = {"version": "m2_campaign_v1", "campaign_id": "c_supersede", "questions": [
        {"question_id": snap["question_id"], "ground": "bot_testing_area", "priority": "main"}],
        "budget_window_start": to_iso(NOW - timedelta(days=1)), "budget_window_end": to_iso(NOW + timedelta(days=6)),
        "weekly_attempt_caps": dict.fromkeys(("claude", "gemini", "gpt"), 100),
        "expected_new_questions": 0, "repair_reserve": 1, "urgent_slack_s": 120}
    sched = SC.Scheduler(tmp_path, camp, snapshots=lambda: {snap["question_id"]: snap}, continuous=True, now=lambda: NOW)
    assert sched.finalize({snap["question_id"]: snap}) == [snap["question_id"]]
    queue = SimpleNamespace(invalidated=set(), fallbacks={})
    pub1 = CY.publish(tmp_path, sched, {snap["question_id"]: snap}, queue)
    assert len(pub1["aggregate_ids"]) == 1
    append_record(tmp_path / "forecasts.jsonl", row("gemini", .65, snapshot=snap, aid="gemini_clean"))
    assert sched.finalize({snap["question_id"]: snap}) == [snap["question_id"]]
    pub2 = CY.publish(tmp_path, sched, {snap["question_id"]: snap}, queue)
    assert len(pub2["aggregate_ids"]) == 1 and pub2["rejected_aggregate_ids"] == pub1["aggregate_ids"]
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "submit_mode.json").write_text('{"mode": "trial", "approved_bot_id": 42}')
    (cfg_dir / "tournaments.json").write_text(
        '{"tournaments": {"main": {"id": 33121, "slug": "main", "submit_whitelisted": true}, '
        '"minibench": {"id": 33122, "slug": "mini", "submit_whitelisted": true}, '
        '"bot_testing_area": {"id": 32977, "slug": "bot-testing-area", "submit_whitelisted": false}}, '
        '"trial_ground_types": ["bot_testing_area", "metaculus_cup", "main_site_questions"]}'
    )
    (cfg_dir / "run_config_v2.json").write_text((F.PROJECT_ROOT / "config/run_config_v2.json").read_text())
    (cfg_dir / "question_types.json").write_text('{"binary": true, "multiple_choice": true, "numeric": true, "discrete": true}')
    transport = FakeTransport(post)
    res = S.Submitter(tmp_path, config_dir=cfg_dir, transport=transport, now=lambda: NOW, sleep=lambda _: None).run()
    assert res["statuses"].get("intent") == 1
    assert res["statuses"].get("forecast_ok") == 1 and res["statuses"].get("comment_ok") == 1
    assert "Deadline fallback" not in transport.writes[1][1]["text"]
    assert "Gemini" in transport.writes[1][1]["text"]


from test_scheduler import environment  # noqa: E402,F401  (fixture)
