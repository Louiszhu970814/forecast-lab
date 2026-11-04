"""Synthetic end-to-end D128 provenance, scoring and denominator integration."""
import json
import math
from pathlib import Path

import pytest

import score
import submit
from flab.ids import hash_obj
from flab.itt_cohort import BOT_ID, CAMPAIGN_ID, START
from flab.jsonl import append_record

T1 = "2026-11-03T05:00:00Z"
DECISION = "2026-11-03T06:00:00Z"
FINAL = "2026-11-03T07:00:00Z"
RESOLVED = "2026-11-11T12:00:00Z"
AS_OF = "2026-11-12T00:00:00Z"


def records(qid=1, *, submitted=True, started=True, historical=False):
    """Complete records constructed independently from all scorer test helpers."""
    when = "2026-11-02T06:00:00Z" if historical else DECISION
    first = "2026-11-02T05:00:00Z" if historical else T1
    sid, root, final_id, run_id = f"s{qid}", f"root{qid}", f"final{qid}", f"run{qid}"
    snapshot = dict(type="question_snapshot", snapshot_id=sid, question_id=qid,
                    post_id=9000 + qid, tournament_id=33135, ground="minibench",
                    venue="minibench", question_type="binary", options=None, seen_at=first)
    identity = dict(question_id=qid, question_snapshot_id=sid, post_id=9000 + qid,
                    tournament_id=33135, question_type="binary")
    t1 = [dict(type="forecast_attempt", attempt_id=f"{qid}_{player}1", **identity,
               tier=1, player=player, probability=.6, validation_status="valid",
               run_id=run_id, run_config_version="rc1", prompt_version="p1",
               started_at_utc=first, finished_at_utc=first)
          for player in ("claude", "gemini", "gpt")]
    t1_ids = [r["attempt_id"] for r in t1]
    baseline = dict(type="aggregate", record_id=root, **identity, stage="tier1", mode="local",
                    status="ready", probability=.6, options=None, aggregation_version="agg_v1",
                    input_attempt_ids=t1_ids, created_at_utc=when,
                    escalation={"candidate": True, "evaluated_at_utc": when})
    t2 = [dict(type="forecast_attempt", attempt_id=f"{qid}_{player}2", **identity,
               tier=2, player=player, probability=.8, validation_status="valid", budget_cost=1,
               input_attempt_ids=t1_ids, run_id=run_id, run_config_version="rc1", prompt_version="r1",
               started_at_utc=when, finished_at_utc=FINAL)
          for player in ("claude", "gemini", "gpt")] if started else []
    final = dict(type="aggregate", record_id=final_id, **identity, stage="final", mode="local",
                 status="ready", probability=.8, options=None, aggregation_version="agg_v1",
                 input_attempt_ids=[r["attempt_id"] for r in t2], tier1_aggregate_id=root,
                 created_at_utc=FINAL)
    submissions = [
        dict(type="submission", status="intent", intent_id=f"i{qid}", aggregate_id=final_id,
             question_id=qid, question_snapshot_id=sid, tournament_id=33135, bot_id=BOT_ID,
             mode="live", snapshot=snapshot, forecast_payload=submit.forecast_payload(snapshot, final), at=FINAL),
        # Completion deliberately inherits immutable identity from the intent.
        dict(type="submission", status="forecast_ok", intent_id=f"i{qid}", at=FINAL),
        dict(type="submission", status="comment_ok", intent_id=f"i{qid}", at=FINAL),
    ] if submitted else []
    campaign = json.loads((Path(__file__).parents[1] / "docs/tasks/D118-MINIBENCH-EARLY.json").read_text())
    return {
        "questions.jsonl": [snapshot], "forecasts.jsonl": t1 + t2,
        "aggregates.jsonl": [baseline] + ([final] if started else []),
        "submissions.jsonl": submissions,
        "resolutions.jsonl": [dict(type="resolution_event", resolution_event_id=f"r{qid}",
                                    question_id=qid, observed_at_utc=RESOLVED,
                                    resolution_status="resolved", canonical_outcome=1)] if submitted else [],
        "scheduler_runs.jsonl": [dict(type="scheduler_run", run_id=run_id, campaign_id=CAMPAIGN_ID,
                                       mode="live", continuous=True, started_at=first, finished_at=FINAL)],
        "reconciliation_runs.jsonl": [dict(type="reconciliation_run", record_id=f"rr{qid}", run_id=run_id,
                                            tier1_aggregate_id=root, question_id=qid, question_snapshot_id=sid,
                                            started_at_utc=when, finished_at_utc=FINAL)],
        "continuous_campaigns.jsonl": [dict(type="continuous_campaign_registered", at=START,
                                             campaign=campaign, sha256=hash_obj(campaign))],
        "live_authorizations.jsonl": [dict(type="live_authorization", recorded_at=START,
                                            campaign=campaign, bot_id=BOT_ID,
                                            campaign_sha256=hash_obj(campaign))],
        "budget_events.jsonl": [dict(type="budget_reserved", at=first, attempt_id=r["attempt_id"],
                                      question_id=qid, run_id=run_id, campaign_id=CAMPAIGN_ID) for r in t1],
    }


def write_records(path, *parts):
    path.mkdir(parents=True, exist_ok=True)
    for part in parts:
        for filename, rows in part.items():
            for row in rows:
                append_record(path / filename, row)


def test_live_score_reuses_validated_pair_and_retains_capture_gap(tmp_path):
    write_records(tmp_path, records())
    board = score.evaluate_scoreboard(tmp_path, as_of=AS_OF, n_boot=20)
    live = board["live_itt"]
    assert live["included_episode_count"] == live["submitted_episode_count"] == live["paired_question_count"] == 1
    assert live["unsubmitted_episode_count"] == 0
    assert live["itt_gap_reasons"] == {"decision_capture_incomplete": 1}
    assert live["cohort"]["decision_capture_complete"] is False
    assert not live["complete_itt_coverage"]
    assert live["resources"]["tier2_attempt_count"] == 3
    assert live["resources"]["budget_units"] == 3
    segment = live["by_config_and_type"][0]
    assert segment["config_segment"] == "rc1|agg_v1|p1|r1"
    assert segment["question_type"] == "binary"
    assert segment["evaluatable_subset_log_score_gain_nats"] == pytest.approx(math.log(.8 / .6))
    assert segment["total_log_score_gain_nats"] is None
    assert segment["log_score_gain_per_budget_unit"] is None
    assert not segment["pairwise"]["log_score_diff"]["conclusion_discipline"]["can_claim_superiority"]
    row = board["questions"][0]
    assert row["submitted_aggregate_id"] == "final1"
    assert row["tier1_aggregate_id"] == "root1"
    assert row["scores"]["final_aggregate"]["log_score"] == pytest.approx(math.log(.8))
    markdown = score.render_scoreboard_markdown(board)
    assert "D128" in markdown
    assert "decision_capture_incomplete" in markdown


def test_live_not_started_and_historical_roots_preserve_denominator(tmp_path):
    write_records(tmp_path, records(), records(2, submitted=False, started=False), records(3, historical=True))
    board = score.evaluate_scoreboard(tmp_path, as_of=AS_OF, n_boot=20)
    live = board["live_itt"]
    assert live["included_episode_count"] == 2
    assert live["excluded_episode_count"] == 1
    assert live["unknown_episode_count"] == 0
    assert live["submitted_episode_count"] == live["unsubmitted_episode_count"] == 1
    assert live["paired_question_count"] == 1
    assert live["itt_gap_reasons"]["unsubmitted_escalation"] == 1
    excluded = next(e for e in live["cohort"]["episodes"] if e["question_id"] == 3)
    assert excluded["status"] == "excluded"
    assert "before_live_start" in excluded["reasons"]
    # The legacy diagnostic history still contains the historical submission.
    assert {r["question_id"] for r in board["questions"]} == {1, 3}


def test_live_submission_before_resolution_is_not_unsubmitted(tmp_path):
    write_records(tmp_path, records())
    live = score.evaluate_scoreboard(tmp_path, as_of="2026-11-04T00:00:00Z", n_boot=20)["live_itt"]
    assert live["included_episode_count"] == live["submitted_episode_count"] == 1
    assert live["unsubmitted_episode_count"] == live["paired_question_count"] == 0
    assert live["itt_gap_reasons"]["not_yet_evaluable"] == 1
    assert "unsubmitted_escalation" not in live["itt_gap_reasons"]


@pytest.mark.parametrize("kind", ["submission", "intent", "subagent", "resolution"])
def test_raw_invalid_time_evidence_reaches_live_report(tmp_path, kind):
    data = records()
    if kind in {"submission", "intent"}:
        # Both receipt stages are undated; the immutable intent is still linked.
        indexes = [0] if kind == "intent" else [1, 2]
        for index in indexes:
            data["submissions.jsonl"][index]["at"] = "broken"
    elif kind == "subagent":
        # No parent subagent_count hint: only the raw row exposes this resource.
        data["subagents.jsonl"] = [dict(subagent_id="undated-child", parent_attempt_id="1_claude2")]
    else:
        data["resolutions.jsonl"].append(dict(type="resolution_event", question_id=1,
                                               resolution_status="annulled", observed_at_utc="broken"))
    write_records(tmp_path, data)
    live = score.evaluate_scoreboard(tmp_path, as_of=AS_OF, n_boot=20)["live_itt"]
    reason = "submission" if kind == "intent" else kind
    assert live["included_episode_count"] == live["submitted_episode_count"] == 1
    assert live["itt_gap_reasons"][f"invalid_{reason}_time"] == 1
    assert live["paired_question_count"] == 0
    assert not live["complete_itt_coverage"]
    if kind == "subagent":
        assert live["resources"]["tier2_subagent_count"] == 1
        assert live["resources"]["invalid_time_record_count"] == 1
        assert live["resources"]["budget_units"] is None


def test_shared_blank_receipt_identity_matches_legacy_and_live(tmp_path):
    data = records()
    for receipt in data["submissions.jsonl"][1:]:
        receipt["state"] = " " + receipt.pop("status").upper() + " "
        receipt["status"] = None
        receipt["question_snapshot_id"] = "  "
    write_records(tmp_path, data)
    board = score.evaluate_scoreboard(tmp_path, as_of=AS_OF, n_boot=20)
    assert board["questions"][0]["submitted_aggregate_id"] == "final1"
    assert board["live_itt"]["paired_question_count"] == 1
    assert board["live_itt"]["submitted_episode_count"] == 1
