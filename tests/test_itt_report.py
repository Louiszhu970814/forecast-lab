"""Synthetic D128 reporting: denominators, provenance, missingness and claims."""
from copy import deepcopy

import pytest
import score as SC
from flab.itt_report import build_live_itt

TS = "2026-11-04T12:00:00Z"
AS_OF = "2026-11-07T12:00:00Z"


def fixture(n=1):
    cohort = {"cohort_id": "d118_live_itt_v1", "bot_id": 311763, "episodes": [], "decision_capture_complete": True}
    data = dict(scored_questions=[], submissions=[], forecasts=[], aggregates=[], resolutions=[], subagents=[])
    for q in range(1, n + 1):
        sid, root, final, aid = f"s{q}", f"root{q}", f"final{q}", f"a{q}"
        cohort["episodes"].append({"episode_id": f"e{q}", "status": "included", "question_id": q,
            "snapshot_id": sid, "root_aggregate_id": root, "tier1_aggregate_ids": [root, root + "copy"],
            "aggregate_ids": [root, final], "tier2_attempt_ids": [aid]})
        data["submissions"].extend([
            {"status": "intent", "intent_id": f"i{q}", "question_id": q, "question_snapshot_id": sid,
             "aggregate_id": final, "bot_id": 311763, "mode": "live", "at": TS},
            {"status": "comment_ok", "intent_id": f"i{q}", "at": TS},
        ])
        data["scored_questions"].append({"question_id": q, "snapshot_id": sid, "submitted_aggregate_id": final,
            "tier1_aggregate_id": root + "copy", "config_segment": "rc|agg|p|r", "question_type": "binary",
            "event_cluster_id": q, "scores": {
                "final_aggregate": {"log_score": -0.1, "log_score_clamped": -0.1, "brier_score": 0.01},
                "tier1_aggregate": {"log_score": -0.2, "log_score_clamped": -0.2, "brier_score": 0.04},
            }})
        data["forecasts"].append({"attempt_id": aid, "question_id": q, "tier": 2, "finished_at_utc": TS, "budget_cost": 0.5})
        data["resolutions"].append({"type": "resolution_event", "question_id": q, "observed_at_utc": AS_OF,
                                    "resolution_status": "resolved"})
    return cohort, data


def report(cohort, data, **kwargs):
    return build_live_itt(cohort, **data, as_of=AS_OF, n_boot=100,
        build_draws=SC._build_synchronized_draws, ci_from_draws=SC._ci_from_synchronized_draws,
        conclusion=SC.pairwise_conclusion_discipline, **kwargs)


def test_complete_cohort_inherits_intent_fields_and_equivalent_baseline():
    c, d = fixture(100)
    r = report(c, d)
    assert r["complete_itt_coverage"]
    assert r["paired_question_count"] == r["submitted_episode_count"] == 100
    assert r["resources"]["budget_units"] == 50
    s = r["by_config_and_type"][0]
    assert s["total_log_score_gain_nats"] == pytest.approx(10)
    assert s["log_score_gain_per_tier2_attempt"] == pytest.approx(0.1)
    assert s["log_score_gain_per_budget_unit"] == pytest.approx(0.2)
    assert s["pairwise"]["log_score_diff"]["conclusion_discipline"]["can_claim_superiority"]


def test_unknown_and_unsubmitted_never_disappear_from_complete_case_claims():
    c, d = fixture(101)
    d["submissions"] = d["submissions"][:-2]
    c["episodes"].append({"episode_id": "unknown", "status": "unknown"})
    c["episodes"].append({"episode_id": "old", "status": "excluded"})
    r = report(c, d)
    assert r["included_episode_count"] == 101
    assert r["unsubmitted_episode_count"] == 1
    assert r["excluded_episode_count"] == 1 and r["unknown_episode_count"] == 1
    assert r["itt_gap_reasons"]["unsubmitted_escalation"] == 1
    s = r["by_config_and_type"][0]
    assert s["paired_question_count"] == 100
    assert s["total_log_score_gain_nats"] is None
    assert s["log_score_gain_per_tier2_attempt"] is None
    assert not s["pairwise"]["log_score_diff"]["conclusion_discipline"]["can_claim_superiority"]


def test_submitted_unresolved_is_not_mislabelled_unsubmitted():
    c, d = fixture()
    d["resolutions"].clear()
    d["scored_questions"].clear()
    r = report(c, d)
    assert r["submitted_episode_count"] == 1 and r["unsubmitted_episode_count"] == 0
    assert r["itt_gap_reasons"]["not_yet_evaluable"] == 1


@pytest.mark.parametrize("change,expected", [
    (lambda c, d: d["scored_questions"][0].update(tier1_aggregate_id="other-root"), "baseline_root_mismatch"),
    (lambda c, d: d["scored_questions"][0]["scores"].update(final_aggregate=None), "missing_validated_pair"),
    (lambda c, d: d["submissions"][0].update(bot_id=2), "submission_identity_conflict"),
    (lambda c, d: d["submissions"][1].update(bot_id=2), "submission_identity_conflict"),
    (lambda c, d: d["scored_questions"][0].update(config_segment="rc|unknown|p|r"), "unsegmented_episode"),
])
def test_report_fails_closed_for_wrong_root_scores_identity_and_config(change, expected):
    c, d = fixture()
    change(c, d)
    r = report(c, d)
    assert expected in r["itt_gap_reasons"]
    assert not r["complete_itt_coverage"]


def test_duplicate_episodes_never_become_independent_questions():
    c, d = fixture()
    c["episodes"].append({**c["episodes"][0], "episode_id": "another-root"})
    r = report(c, d)
    assert r["included_episode_count"] == 2 and r["included_question_count"] == 1
    assert r["paired_question_count"] == 0
    assert r["resources"]["tier2_attempt_count"] == 1
    assert r["itt_gap_reasons"]["multiple_episode_question"] == 2


def test_types_and_configs_are_separate_and_costs_never_default_to_one():
    c, d = fixture(3)
    d["scored_questions"][1]["question_type"] = "numeric"
    d["scored_questions"][2]["config_segment"] = "rc2|agg|p|r"
    d["forecasts"][0].pop("budget_cost")
    r = report(c, d)
    assert len(r["by_config_and_type"]) == 3
    assert r["pooling_prohibited"]
    assert r["resources"]["budget_units"] is None
    assert r["resources"]["budget_cost_missing_count"] == 1
    target = next(g for g in r["by_config_and_type"] if g["config_segment"] == "rc|agg|p|r" and g["question_type"] == "binary")
    assert target["log_score_gain_per_budget_unit"] is None


def test_subagents_count_once_and_do_not_double_count_parent_cost():
    c, d = fixture()
    child = {"subagent_id": "s1", "parent_attempt_id": "a1", "finished_at_utc": TS, "budget_cost": .2}
    d["subagents"] = [child, deepcopy(child)]
    d["forecasts"].append(deepcopy(d["forecasts"][0]))
    r = report(c, d)
    assert r["resources"]["tier2_attempt_count"] == r["resources"]["tier2_subagent_count"] == 1
    assert r["resources"]["subagent_cost_accounting"] == "not_separable_from_parent"
    assert r["resources"]["budget_units"] is None
    assert r["by_config_and_type"][0]["log_score_gain_per_budget_unit"] is None


def test_cutoffs_and_corrected_resolution_use_latest_at_or_before_cutoff():
    c, d = fixture()
    d["resolutions"].append({"type": "resolution_event", "question_id": 1,
                            "observed_at_utc": AS_OF, "resolution_status": "annulled"})
    d["resolutions"].append({"type": "resolution_event", "question_id": 1,
                            "observed_at_utc": "2026-12-01T00:00:00Z", "resolution_status": "resolved"})
    r = report(c, d)
    assert r["itt_gap_reasons"]["excluded_resolution_annulled"] == 1
    assert r["paired_question_count"] == 0


def test_no_escalations_never_claims_complete_itt():
    c, d = fixture(0)
    r = report(c, d)
    assert not r["complete_itt_coverage"]
    assert r["by_config_and_type"] == []


def test_incomplete_decision_capture_blocks_all_effect_claims():
    c, d = fixture(100)
    c["decision_capture_complete"] = False
    r = report(c, d)
    assert r["paired_question_count"] == 100
    assert not r["complete_itt_coverage"]
    assert r["itt_gap_reasons"]["decision_capture_incomplete"] == 1
    segment = r["by_config_and_type"][0]
    assert segment["total_log_score_gain_nats"] is None
    assert segment["evaluatable_subset_log_score_gain_nats"] == pytest.approx(10)
    assert not segment["pairwise"]["log_score_diff"]["conclusion_discipline"]["can_claim_superiority"]
    assert segment["pairwise"]["log_score_diff"]["conclusion_discipline"]["tier"] == "descriptive_only"
    assert segment["estimate_scope"] == "observed_pair_subset_descriptive_only"


def test_reported_missing_children_make_cost_incomplete():
    c, d = fixture()
    d["forecasts"][0]["subagent_count"] = 2
    r = report(c, d)
    assert r["resources"]["budget_units"] is None
    assert r["resources"]["missing_subagent_record_count"] == 2


@pytest.mark.parametrize("timestamp", [None, "not-a-time"])
@pytest.mark.parametrize("kind", ["submission", "intent", "attempt", "aggregate", "subagent", "resolution"])
def test_invalid_time_evidence_survives_and_blocks_episode_pair(kind, timestamp):
    c, d = fixture()
    reason = "submission" if kind == "intent" else kind
    if kind in {"submission", "intent"}:
        d["submissions"][0 if kind == "intent" else 1]["at"] = timestamp
    elif kind == "attempt":
        d["forecasts"][0]["finished_at_utc"] = timestamp
    elif kind == "aggregate":
        d["aggregates"].append({"record_id": "final1", "created_at_utc": timestamp})
    elif kind == "subagent":
        d["subagents"].append({"subagent_id": "child", "parent_attempt_id": "a1", "finished_at_utc": timestamp})
    else:
        # An undated correction cannot silently leave an older valid result usable.
        d["resolutions"].append({"type": "resolution_event", "question_id": 1,
                                 "observed_at_utc": timestamp, "resolution_status": "annulled"})
    r = report(c, d)
    assert r["itt_gap_reasons"][f"invalid_{reason}_time"] == 1
    assert r["submitted_episode_count"] == 1
    assert r["paired_question_count"] == 0
    assert not r["complete_itt_coverage"]
    if kind in {"submission", "intent"}:
        assert r["submission_time_unknown_episode_count"] == 1
    if kind in {"attempt", "subagent"}:
        assert r["resources"]["invalid_time_record_count"] == 1
        assert not r["resources"]["budget_cost_complete"]
        assert r["resources"]["budget_units"] is None
    if kind == "subagent":
        assert r["resources"]["tier2_subagent_count"] == 1


def test_shared_status_and_snapshot_normalization_for_sparse_receipts():
    c, d = fixture()
    d["submissions"][0].update(status=None, state="  INTENT  ", question_snapshot_id=" s1 ")
    d["submissions"][1].update(status=None, state=" COMMENT_OK ", question_snapshot_id="  ")
    r = report(c, d)
    assert r["complete_itt_coverage"]
    assert r["paired_question_count"] == r["submitted_episode_count"] == 1


def test_snapshot_conflict_is_attributed_instead_of_disappearing():
    c, d = fixture()
    d["submissions"][1]["question_snapshot_id"] = "different"
    r = report(c, d)
    assert r["submitted_episode_count"] == 1
    assert r["itt_gap_reasons"]["submission_identity_conflict"] == 1
    assert "unsubmitted_escalation" not in r["itt_gap_reasons"]


def test_known_snapshotless_probe_is_not_confirmed_live_submission():
    c, d = fixture()
    d["submissions"] = [{"status": "forecast_ok", "question_id": 1, "aggregate_id": "final1",
                          "bot_id": 311763, "mode": "local", "at": TS}]
    r = report(c, d)
    assert r["submitted_episode_count"] == 0
    assert r["itt_gap_reasons"]["unsubmitted_escalation"] == 1


def test_provably_future_evidence_is_omitted_and_observed_at_is_supported():
    c, d = fixture()
    d["subagents"] = [{"subagent_id": "child", "parent_attempt_id": "a1", "observed_at_utc": "2026-12-01T00:00:00Z"}]
    d["submissions"].append({**d["submissions"][1], "at": "2026-12-01T00:00:00Z", "bot_id": 9})
    r = report(c, d)
    assert r["complete_itt_coverage"]
    assert r["resources"]["tier2_subagent_count"] == 0
    d["subagents"][0]["observed_at_utc"] = TS
    r = report(c, d)
    assert r["resources"]["tier2_subagent_count"] == 1
    assert r["resources"]["invalid_time_record_count"] == 0


def test_unknown_resources_and_mixed_cost_bases_are_explicit():
    c, d = fixture(2)
    c["episodes"][1]["status"] = "unknown"
    d["forecasts"][1]["finished_at_utc"] = "bad-time"
    r = report(c, d)
    assert r["resources"]["scope"] == "recorded_linked_attempts_only"
    assert r["unknown_resources"]["tier2_attempt_count"] == 1
    assert r["unknown_resources"]["invalid_time_record_count"] == 1
    assert r["unknown_resources"]["budget_units"] is None
    c["episodes"][1]["status"] = "included"
    d["forecasts"][1].update(finished_at_utc=TS, quota_weight=2)
    d["forecasts"][1].pop("budget_cost")
    r = report(c, d)
    assert r["resources"]["mixed_cost_fields"]
    assert r["resources"]["cost_fields"] == ["budget_cost", "quota_weight"]
    assert r["resources"]["budget_units"] is None
