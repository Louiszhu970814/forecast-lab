from __future__ import annotations

import copy
import itertools
import json
import math
from datetime import datetime, timezone

import pytest

import aggregate as A
import escalate as E
from flab.config import load_agg_config, load_run_config
from flab.jsonl import read_records

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
BIN = {"question_id": 7, "post_id": 17, "tournament_id": 27, "snapshot_id": "s1", "question_type": "binary"}
MC = {**BIN, "question_type": "multiple_choice", "options": ["A", "B", "C"]}


def answer(player="claude", p=.3, *, snapshot=BIN, aid=None, **kw):
    rec = {"type": "forecast_attempt", "attempt_id": aid or player + "_1", "player": player, "tier": 1,
           "question_id": snapshot["question_id"], "post_id": snapshot["post_id"],
           "tournament_id": snapshot["tournament_id"], "question_snapshot_id": snapshot["snapshot_id"],
           "question_type": snapshot["question_type"], "validation_status": "valid", "model_check": "verified",
           "probability": p if snapshot["question_type"] == "binary" else None,
           "option_probabilities": p if snapshot["question_type"] == "multiple_choice" else None,
           "audit_flags": [], "market_refs": [], "escalation_flags": []}
    rec.update(kw)
    return rec


def market(p=.3, **kw):
    return {"url": "https://example.org/market", "probability": p, "match": "exact",
            "retrieved_at_utc": "2026-10-31T23:00:00Z", **kw}


def test_binary_log_odds_mean_is_not_arithmetic_mean():
    # Odds 1 and 9: geometric mean odds 3 -> probability .75, arithmetic mean would be .70.
    rec = A.build_aggregate(BIN, [answer(p=.5), answer("gemini", .9)], now=NOW)
    assert rec["probability"] == pytest.approx(.75)
    assert rec["missing_players"] == ["gpt"]
    assert rec["input_attempt_ids"] == ["claude_1", "gemini_1"]
    assert rec["created_at_utc"] == "2026-11-01T00:00:00.000Z"


@pytest.mark.parametrize("p,expected", [(.001, .02), (.999, .98), (.4, .4)])
def test_binary_clipping_is_applied_after_averaging(p, expected):
    rec = A.build_aggregate(BIN, [answer(p=p)], now=NOW)
    assert rec["probability"] == pytest.approx(expected)
    assert rec["before_clipping_or_smoothing"] == pytest.approx(p)


def test_binary_extreme_inputs_are_not_clipped_before_averaging():
    rec = A.build_aggregate(BIN, [answer(p=.001), answer("gpt", .5)])
    want = math.sqrt(.001 / .999) / (1 + math.sqrt(.001 / .999))
    assert rec["probability"] == pytest.approx(want)


def test_multiple_choice_geometric_mean_and_uniform_smoothing():
    rows = [answer(p={"A": .8, "B": .1, "C": .1}, snapshot=MC),
            answer("gpt", {"A": .1, "B": .8, "C": .1}, snapshot=MC)]
    rec = A.build_aggregate(MC, rows)
    weights = [math.sqrt(.08), math.sqrt(.08), .1]
    expected = {o: .98 * v / sum(weights) + .02 / 3 for o, v in zip(MC["options"], weights)}
    assert rec["option_probabilities"] == pytest.approx(expected)
    assert math.fsum(rec["option_probabilities"].values()) == pytest.approx(1)
    assert min(rec["option_probabilities"].values()) >= .02 / 3


def test_order_invariance_and_inputs_are_not_mutated():
    rows = [answer(p=.2), answer("gpt", .7), answer("gemini", .4)]
    before = copy.deepcopy(rows)
    values = [A.build_aggregate(BIN, list(p))["probability"] for p in itertools.permutations(rows)]
    assert max(values) == min(values)
    assert rows == before


def test_first_valid_is_fixed_across_later_answers_and_later_audits():
    rows = [answer(p=.2), answer(p=.9, aid="claude_2")]
    assert A.build_aggregate(BIN, rows)["input_attempt_ids"] == ["claude_1"]
    later = [{"audit_event_id": "aud_late", "attempt_id": "claude_1", "audit_flags": ["leak_confirmed"]}]
    rec = A.build_aggregate(BIN, rows, audit_events=later)
    assert rec["status"] == "no_valid_inputs" and rec["probability"] is None
    assert rec["audit_event_ids"] == ["aud_late"]  # never replace the now-excluded .2 by .9


@pytest.mark.parametrize("kw,reason", [
    ({"model_check": "mismatch"}, "model_mismatch"),
    ({"audit_flags": ["leak_confirmed"]}, "leak_confirmed"),
    ({"tool_violations": [{"tool": "Bash"}]}, "tool_scope_violation"),
    ({"question_snapshot_id": "old"}, "mismatched_snapshot"),
    ({"post_id": 999}, "mismatched_post_id"),
    ({"tournament_id": 999}, "mismatched_tournament_id"),
])
def test_ineligible_answers_are_excluded_with_a_reason(kw, reason):
    rec = A.build_aggregate(BIN, [answer(**kw)])
    assert rec["status"] == "no_valid_inputs"
    assert reason in rec["excluded_inputs"][0]["reasons"]


@pytest.mark.parametrize("p", [True, 0, 1, -1, math.nan, math.inf, "0.3"])
def test_stored_valid_label_cannot_override_invalid_probabilities(p):
    rec = A.build_aggregate(BIN, [answer(p=p)])
    assert rec["status"] == "no_valid_inputs"


def test_final_prefers_tier2_and_falls_back_per_line():
    rows = [answer(p=.2), answer("gpt", .8),
            answer(p=.4, aid="c2", tier=2, input_attempt_ids=["claude_1"]),
            answer("gpt", .9, aid="g2", tier=2, validation_status="late")]
    rec = A.build_aggregate(BIN, rows, stage="final")
    assert rec["inputs"] == [{"player": "claude", "tier": 2, "attempt_id": "c2"},
                              {"player": "gpt", "tier": 1, "attempt_id": "gpt_1"}]
    assert A.build_aggregate(BIN, rows)["input_attempt_ids"] == ["claude_1", "gpt_1"]


def test_late_audit_propagates_through_repair_and_cross_line_inputs():
    rows = [answer(aid="a", validation_status="schema_failed"),
            answer(aid="b", prev_attempt_id="a"),
            answer("gpt", aid="c", tier=2, input_attempt_ids=["b"])]
    events = [{"attempt_id": "a", "audit_flags": ["leak_confirmed"]}]
    flags = A.effective_flags(rows, events)
    assert all("leak_confirmed" in flags[aid] for aid in ("a", "b", "c"))
    rec = A.build_aggregate(BIN, rows, stage="final", audit_events=events)
    assert rec["status"] == "no_valid_inputs"


def test_suspected_leaks_stay_flagged_but_usable():
    rec = A.build_aggregate(BIN, [answer(audit_flags=["suspected_leak"])])
    assert rec["status"] == "ready" and rec["audit_flags"] == ["suspected_leak"]


def test_unknown_dependencies_fail_closed_and_cycles_or_duplicate_ids_raise():
    rec = A.build_aggregate(BIN, [answer(prev_attempt_id="missing")])
    assert "missing_dependency" in rec["excluded_inputs"][0]["reasons"]
    with pytest.raises(ValueError, match="cycle"):
        A.build_aggregate(BIN, [answer(aid="a", prev_attempt_id="b"), answer(aid="b", prev_attempt_id="a")])
    with pytest.raises(ValueError, match="unique"):
        A.build_aggregate(BIN, [answer(), answer()])
    with pytest.raises(ValueError, match="unknown attempt"):
        A.build_aggregate(BIN, [], audit_events=[{"attempt_id": "ghost"}])


def test_invalid_or_cross_question_input_is_not_used():
    rows = [answer(aid="a", validation_status="incomplete"),
            answer("gpt", aid="b", input_attempt_ids=["a"])]
    assert A.build_aggregate(BIN, rows)["status"] == "no_valid_inputs"
    rows[0].update(question_id=123, validation_status="valid")
    assert A.build_aggregate(BIN, rows)["status"] == "no_valid_inputs"


def test_tier2_requires_traceable_inputs_from_the_same_snapshot():
    rec = A.build_aggregate(BIN, [answer(tier=2)], stage="final")
    assert "missing_tier2_inputs" in rec["excluded_inputs"][0]["reasons"]
    rows = [answer(aid="old", question_snapshot_id="old"),
            answer("gpt", tier=2, input_attempt_ids=["old"])]
    rec = A.build_aggregate(BIN, rows, stage="final")
    assert rec["status"] == "no_valid_inputs"
    assert "invalid_dependency" in rec["excluded_inputs"][1]["reasons"]


def test_multichoice_keys_normalization_and_raw_consistency_are_checked():
    for ops in ({"A": .5, "B": .5}, {"A": .5, "B": .4, "C": .2}):
        assert A.build_aggregate(MC, [answer(p=ops, snapshot=MC)])["status"] == "no_valid_inputs"
    row = answer(p={"A": .5, "B": .3, "C": .2}, snapshot=MC,
                 option_probabilities_raw={"A": .6, "B": .2, "C": .2})
    assert A.build_aggregate(MC, [row])["status"] == "no_valid_inputs"


def test_unknown_rules_are_not_silently_applied_and_logs_are_append_only(tmp_path):
    cfg = load_agg_config()
    cfg["weights"]["claude"] = 2
    with pytest.raises(ValueError, match="unsupported"):
        A.build_aggregate(BIN, [answer()], config=cfg)
    path = tmp_path / "aggregates.jsonl"
    one, two = A.build_aggregate(BIN, [answer()]), A.build_aggregate(BIN, [])
    A.append_local_aggregate(path, one)
    A.append_local_aggregate(path, two)
    assert list(read_records(path)) == [one, two]
    with pytest.raises(ValueError, match="local"):
        A.append_local_aggregate(path, {**one, "mode": "live"})


def test_escalation_uses_clipped_binary_values_and_is_only_a_candidate():
    rec = E.evaluate(BIN, [answer(p=.001), answer("gpt", .019)], now=NOW)
    assert not rec["escalation"]["candidate"]  # both clip to .02; raw log-odds would disagree
    rec = E.evaluate(BIN, [answer(p=.1), answer("gpt", .9)], now=NOW)
    esc = rec["escalation"]
    assert esc["candidate"] and esc["admission_status"] == "pending_budget_and_deadline"
    assert esc["triggers"][0]["strength"] == pytest.approx(2 * math.log(9))


def test_multiple_choice_disagreement_uses_raw_not_normalised_values():
    raw_a, raw_b = {"A": .5, "B": .3, "C": .205}, {"A": .25, "B": .45, "C": .3}
    rows = [answer(p={k: v / sum(raw_a.values()) for k, v in raw_a.items()}, snapshot=MC,
                   option_probabilities_raw=raw_a), answer("gpt", raw_b, snapshot=MC, option_probabilities_raw=raw_b)]
    trigger = E.evaluate(MC, rows, now=NOW)["escalation"]["triggers"][0]
    assert trigger["type"] == "model_disagreement" and trigger["strength"] == .25 and trigger["option"] == "A"


@pytest.mark.parametrize("change,reason", [
    ({"match": "close"}, "not_exact"), ({"retrieved_at_utc": "yesterday"}, "invalid_timestamp_or_url"),
    ({"retrieved_at_utc": "2027-01-01T00:00:00Z"}, "future_timestamp"),
    ({"probability": math.nan}, "invalid_market_probability"), ({"url": "javascript:bad"}, "invalid_url"),
])
def test_ineligible_markets_cannot_trigger_escalation(change, reason):
    rec = E.evaluate(BIN, [answer(p=.8, market_refs=[market(.1, **change)])], now=NOW)
    assert not rec["escalation"]["candidate"]
    assert rec["escalation"]["rejected_markets"][0]["reason"] == reason


@pytest.mark.parametrize("price,used", [(0, .005), (1, .995), (.001, .001)])
def test_market_endpoints_are_replaced_but_original_values_retained(price, used):
    rec = E.evaluate(BIN, [answer(p=.5, market_refs=[market(price)])], now=NOW)
    trigger = rec["escalation"]["triggers"][0]
    assert trigger["market_prices_raw"] == {"Yes": price}
    assert trigger["market_prices_used"] == {"Yes": used}
    assert trigger["strength"] == pytest.approx(abs(A.logit(used)))


def test_multiple_choice_market_needs_a_complete_mapping():
    m = market(None, complete=True, option_probabilities={"A": .05, "B": .9, "C": .05})
    row = answer(p={"A": .8, "B": .1, "C": .1}, snapshot=MC, market_refs=[m])
    rec = E.evaluate(MC, [row], now=NOW)
    assert any(t["type"] == "market_disagreement" for t in rec["escalation"]["triggers"])
    row["market_refs"] = [{**m, "complete": False}]
    assert not E.evaluate(MC, [row], now=NOW)["escalation"]["candidate"]
    row["market_refs"] = [{**m, "option_probabilities": {"A": .2, "B": .8}}]
    assert not E.evaluate(MC, [row], now=NOW)["escalation"]["candidate"]


def test_flags_trigger_but_excluded_lines_cannot_influence_escalation():
    row = answer(escalation_flags=["resolution_ambiguity"])
    rec = E.evaluate(BIN, [row], now=NOW)
    assert rec["escalation"]["triggers"][0]["type"] == "resolution_ambiguity"
    row["audit_flags"] = ["leak_confirmed"]
    rec = E.evaluate(BIN, [row], now=NOW)
    assert rec["status"] == "no_valid_inputs" and not rec["escalation"]["candidate"]
    assert json.loads(json.dumps(rec, allow_nan=False)) == rec


def test_escalation_refuses_unversioned_threshold_changes():
    cfg = load_run_config()
    cfg["escalation"]["binary_log_odds_diff"] = .1
    with pytest.raises(ValueError, match="unsupported"):
        E.evaluate(BIN, [answer()], run_config=cfg)
