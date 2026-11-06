"""Tests for src/post_close_audit.py (PLAN.md D126 / D45).

Covers the three review gates, strict D45 masking on every rejection path,
full diagnostic disclosure when eligible, and the read-only guarantee for
both the library API and the CLI (including a mocked MetaculusClient).
"""
from __future__ import annotations

import builtins
import copy
import hashlib
import io
import json
import os
import sys
import types
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import post_close_audit as pca  # noqa: E402

# --------------------------------------------------------------------------- constants
QID = 101
POST_ID = 9001
SNAP = "snap-1"
OLD_SNAP = "snap-0"
TITLE = "Will X happen?"
CLOSE = "2026-11-01T12:00:00Z"
NOW = "2026-11-01T13:00:00Z"

SECRET_PROBS = ("0.731", "0.642")
SECRET_TEXT = "secret reasoning"


# --------------------------------------------------------------------------- fixture data
def _fc(player: str, tier: int, p: float, attempt: str, snap: str = SNAP, **extra: Any) -> Dict[str, Any]:
    row = {
        "question_id": QID,
        "snapshot_id": snap,
        "type": "forecast_attempt",
        "attempt_id": attempt,
        "player": player,
        "tier": tier,
        "validation_status": "valid",
        "probability": p,
    }
    row.update(extra)
    return row


def default_questions() -> List[Dict[str, Any]]:
    return [
        {"question_id": QID, "post_id": POST_ID, "snapshot_id": OLD_SNAP, "title": TITLE,
         "scheduled_close_time": CLOSE},
        {"question_id": QID, "post_id": POST_ID, "snapshot_id": SNAP, "title": TITLE,
         "scheduled_close_time": CLOSE},
    ]


def default_forecasts() -> List[Dict[str, Any]]:
    return [
        # Stale forecast bound to an older snapshot: must never surface in diagnostics.
        _fc("alpha", 1, 0.999, "a0", snap=OLD_SNAP, reasoning_summary="stale"),
        _fc("alpha", 1, 0.731, "a1", base_rate=0.4, market_refs=["mkt-1"],
            reasoning_summary="secret reasoning alpha initial", key_factors=["polls"]),
        _fc("beta", 1, 0.642, "b1", base_rate=0.35,
            reasoning_summary="secret reasoning beta initial"),
        _fc("alpha", 2, 0.70, "a2", base_rate=0.4, market_refs=["mkt-1"],
            change_reason="secret reasoning update",
            reasoning_summary="secret reasoning alpha revised",
            key_factors=["polls", "news"], audit_flags=["stale_market"]),
        _fc("beta", 2, 0.66, "b2", reasoning_summary="secret reasoning beta revised"),
        # Invalid row: must be ignored by diagnostics.
        {"question_id": QID, "snapshot_id": SNAP, "type": "forecast_attempt", "attempt_id": "g1",
         "player": "gamma", "tier": 1, "validation_status": "invalid", "probability": 0.5},
    ]


def default_starts() -> List[Dict[str, Any]]:
    return [{"question_id": QID, "attempt_id": a} for a in ("a1", "b1", "a2", "b2")]


def default_aggregates() -> List[Dict[str, Any]]:
    return [{
        "question_id": QID,
        "snapshot_id": SNAP,
        "record_id": "agg-1",
        "stage": "final",
        "completion_status": "complete",
        "probability": 0.68,
        "input_attempt_ids": ["a2", "b2"],
        "expected_players": ["alpha", "beta", "gamma"],
    }]


def default_submissions() -> List[Dict[str, Any]]:
    return [
        {"question_id": QID, "intent_id": "i1", "status": "intent", "aggregate_record_id": "agg-1"},
        {"question_id": QID, "intent_id": "i1", "operation": "forecast", "status": "request_started",
         "request_id": "r1"},
        {"question_id": QID, "intent_id": "i1", "operation": "forecast", "status": "forecast_ok",
         "request_id": "r1"},
        {"question_id": QID, "intent_id": "i1", "operation": "comment", "status": "request_started",
         "request_id": "r2"},
        {"question_id": QID, "intent_id": "i1", "operation": "comment", "status": "comment_ok",
         "request_id": "r2"},
    ]


def submissions_with_unknown(request_id: Any = "r1") -> List[Dict[str, Any]]:
    forecast_unknown = {"question_id": QID, "intent_id": "i1", "operation": "forecast", "status": "unknown"}
    if request_id is not None:
        forecast_unknown["request_id"] = request_id
    return [
        {"question_id": QID, "intent_id": "i1", "status": "intent", "aggregate_record_id": "agg-1"},
        {"question_id": QID, "intent_id": "i1", "operation": "forecast", "status": "request_started",
         "request_id": "r1"},
        forecast_unknown,
        {"question_id": QID, "intent_id": "i1", "operation": "comment", "status": "request_started",
         "request_id": "r2"},
        {"question_id": QID, "intent_id": "i1", "operation": "comment", "status": "comment_ok",
         "request_id": "r2"},
    ]


def default_queue() -> List[Dict[str, Any]]:
    return [
        {"question_id": QID, "type": "queue_state", "action": "queued"},
        {"question_id": QID, "type": "queue_state", "action": "already_submitted"},
    ]


def closed_post() -> Dict[str, Any]:
    return {
        "id": POST_ID,
        "status": "closed",
        "question": {"id": QID, "status": "resolved", "actual_close_time": CLOSE,
                     "scheduled_close_time": CLOSE},
    }


# --------------------------------------------------------------------------- scenario helper
class Scenario:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.data_dir = root / "data"
        self.config_dir = root / "config"
        self.data_dir.mkdir()
        self.config_dir.mkdir()
        (self.config_dir / "settings.json").write_text(json.dumps({"players": ["alpha", "beta"]}) + "\n")
        self.post: Dict[str, Any] = closed_post()
        self.fetch_calls: List[Any] = []
        self.write("questions.jsonl", default_questions())
        self.write("forecasts.jsonl", default_forecasts())
        self.write("aggregates.jsonl", default_aggregates())
        self.write("submissions.jsonl", default_submissions())
        self.write("question_queue.jsonl", default_queue())
        self.write("forecast_attempt_starts.jsonl", default_starts())
        self.write("submission_readbacks.jsonl", [])

    def path(self, name: str) -> Path:
        return self.data_dir / name

    def write(self, name: str, rows: List[Dict[str, Any]]) -> None:
        self.path(name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def append(self, name: str, *rows: Dict[str, Any]) -> None:
        with self.path(name).open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def append_raw(self, name: str, text: str) -> None:
        with self.path(name).open("a", encoding="utf-8") as fh:
            fh.write(text)

    def fetch(self, post_id: Any) -> Dict[str, Any]:
        self.fetch_calls.append(post_id)
        return copy.deepcopy(self.post)

    def review(self, **kw: Any) -> Dict[str, Any]:
        kw.setdefault("data_dir", self.data_dir)
        kw.setdefault("fetch_post", self.fetch)
        kw.setdefault("now", NOW)
        return pca.review_eligible(QID, **kw)

    def report(self, **kw: Any) -> Dict[str, Any]:
        kw.setdefault("data_dir", self.data_dir)
        kw.setdefault("fetch_post", self.fetch)
        kw.setdefault("now", NOW)
        return pca.build_post_close_report(**kw)


def _fake_read_complete(path: Any):
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()], True


@pytest.fixture(autouse=True)
def _deterministic_reader(monkeypatch):
    """Pin the lock-free reader so tests don't depend on flab.ledger being importable."""
    monkeypatch.setattr(pca, "_read_complete", _fake_read_complete)


@pytest.fixture
def sc(tmp_path: Path) -> Scenario:
    return Scenario(tmp_path)


def _dump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def _assert_no_secrets(text: str) -> None:
    for secret in SECRET_PROBS:
        assert secret not in text, f"leaked probability {secret}"
    assert SECRET_TEXT not in text, "leaked reasoning text"


# --------------------------------------------------------------------------- baseline
def test_baseline_scenario_is_review_eligible(sc: Scenario):
    rec = sc.review()
    assert rec["gates"] == {
        "gate1_platform_closed": True,
        "gate2_lifecycle_converged": True,
        "gate3_not_queued": True,
    }
    assert rec["reasons"] == {
        "gate1_platform_closed": [],
        "gate2_lifecycle_converged": [],
        "gate3_not_queued": [],
    }
    assert rec["review_eligible"] is True
    assert rec["masked"] is False
    assert rec["diagnostics"] is not None
    assert rec["snapshot_id"] == SNAP
    assert rec["post_id"] == POST_ID
    assert sc.fetch_calls == [POST_ID]


# --------------------------------------------------------------------------- gate 1
def test_gate1_fails_offline_when_fetch_post_is_none(sc: Scenario):
    rec = sc.review(fetch_post=None)
    assert rec["gates"]["gate1_platform_closed"] is False
    assert rec["reasons"]["gate1_platform_closed"] == ["offline_or_no_post"]
    # Only gate 1 blocks: local lifecycle is otherwise healthy.
    assert rec["gates"]["gate2_lifecycle_converged"] is True
    assert rec["gates"]["gate3_not_queued"] is True
    assert rec["review_eligible"] is False
    assert rec["masked"] is True
    assert rec["diagnostics"] is None


def test_gate1_fails_when_fetch_raises(sc: Scenario):
    def boom(_pid):
        raise RuntimeError("network down")

    rec = sc.review(fetch_post=boom)
    assert rec["gates"]["gate1_platform_closed"] is False
    assert rec["reasons"]["gate1_platform_closed"] == ["fetch_error:RuntimeError"]
    assert rec["diagnostics"] is None


@pytest.mark.parametrize(
    "post_status,question_status",
    [("open", "open"), ("open", None), ("closed", "open"), ("OPEN", "resolved")],
)
def test_gate1_fails_when_api_status_open(sc: Scenario, post_status, question_status):
    sc.post["status"] = post_status
    if question_status is None:
        sc.post["question"].pop("status")
    else:
        sc.post["question"]["status"] = question_status
    rec = sc.review()
    assert rec["gates"]["gate1_platform_closed"] is False
    assert "platform_not_closed" in rec["reasons"]["gate1_platform_closed"]
    assert rec["diagnostics"] is None


def test_gate1_fails_when_no_platform_status(sc: Scenario):
    sc.post.pop("status")
    sc.post["question"].pop("status")
    rec = sc.review()
    assert rec["gates"]["gate1_platform_closed"] is False
    assert "no_platform_status" in rec["reasons"]["gate1_platform_closed"]


@pytest.mark.parametrize("now", ["2026-11-01T12:00:00Z", "2026-11-01T12:05:00Z", "2026-11-01T12:14:59Z"])
def test_gate1_fails_when_closed_less_than_15_minutes_ago(sc: Scenario, now):
    rec = sc.review(now=now)
    assert rec["gates"]["gate1_platform_closed"] is False
    assert rec["reasons"]["gate1_platform_closed"] == ["close_window_not_elapsed"]
    assert rec["diagnostics"] is None


def test_gate1_passes_at_exact_15_minute_boundary(sc: Scenario):
    rec = sc.review(now="2026-11-01T12:15:00Z")
    assert rec["gates"]["gate1_platform_closed"] is True
    assert rec["reasons"]["gate1_platform_closed"] == []
    assert rec["platform"]["effective_close_time"] == "2026-11-01T12:00:00+00:00"
    assert rec["review_eligible"] is True


def test_gate1_custom_min_closed_minutes(sc: Scenario):
    assert sc.review(now=NOW, min_closed_minutes=60)["gates"]["gate1_platform_closed"] is True
    assert sc.review(now=NOW, min_closed_minutes=61)["gates"]["gate1_platform_closed"] is False


def test_negative_min_closed_minutes_rejected(sc: Scenario):
    with pytest.raises(ValueError):
        sc.review(min_closed_minutes=-1)
    with pytest.raises(ValueError):
        sc.report(min_closed_minutes=-1)


@pytest.mark.parametrize("level", ["post", "question"])
@pytest.mark.parametrize("bad_value", [12345, "not-a-date", "2026-13-45T00:00:00Z", ""])
def test_gate1_fails_on_malformed_actual_close_time(sc: Scenario, level, bad_value):
    target = sc.post if level == "post" else sc.post["question"]
    target["actual_close_time"] = bad_value
    rec = sc.review()
    assert rec["gates"]["gate1_platform_closed"] is False
    assert "malformed_close_time" in rec["reasons"]["gate1_platform_closed"]
    assert rec["platform"]["effective_close_time"] is None
    assert rec["diagnostics"] is None


def test_gate1_fails_when_no_close_time_anywhere(sc: Scenario):
    sc.post["question"].pop("actual_close_time")
    sc.post["question"].pop("scheduled_close_time")
    sc.write("questions.jsonl", [{"question_id": QID, "post_id": POST_ID, "snapshot_id": SNAP}])
    rec = sc.review()
    assert rec["gates"]["gate1_platform_closed"] is False
    assert "no_close_time" in rec["reasons"]["gate1_platform_closed"]


@pytest.mark.parametrize("later_level", ["post", "question"])
def test_gate1_conflicting_close_times_use_conservative_max(sc: Scenario, later_level):
    early, late = "2026-11-01T12:00:00Z", "2026-11-01T12:30:00Z"
    sc.post["actual_close_time"] = late if later_level == "post" else early
    sc.post["question"]["actual_close_time"] = late if later_level == "question" else early

    # 40 minutes after the early close but only 10 after the late one -> must fail.
    rec = sc.review(now="2026-11-01T12:40:00Z")
    assert rec["gates"]["gate1_platform_closed"] is False
    assert rec["reasons"]["gate1_platform_closed"] == ["close_window_not_elapsed"]
    assert rec["platform"]["effective_close_time"] == "2026-11-01T12:30:00+00:00"
    assert rec["diagnostics"] is None

    # Exactly 15 minutes after the latest close -> passes.
    rec = sc.review(now="2026-11-01T12:45:00Z")
    assert rec["gates"]["gate1_platform_closed"] is True
    assert rec["platform"]["effective_close_time"] == "2026-11-01T12:30:00+00:00"


def test_gate1_local_snapshot_close_time_also_considered(sc: Scenario):
    rows = default_questions()
    rows[-1]["scheduled_close_time"] = "2026-11-01T12:30:00Z"
    sc.write("questions.jsonl", rows)
    rec = sc.review(now="2026-11-01T12:40:00Z")
    assert rec["gates"]["gate1_platform_closed"] is False
    assert rec["platform"]["effective_close_time"] == "2026-11-01T12:30:00+00:00"


# --------------------------------------------------------------------------- gate 2
@pytest.mark.parametrize(
    "start_row",
    [
        {"question_id": QID, "attempt_id": "a9"},          # started, never finished
        {"question_id": QID, "player": "alpha"},           # missing attempt_id
        {"question_id": QID, "attempt_id": ""},            # empty attempt_id
    ],
    ids=["unfinished", "missing_attempt_id", "empty_attempt_id"],
)
def test_gate2_fails_on_unfinished_or_unidentified_attempt(sc: Scenario, start_row):
    sc.append("forecast_attempt_starts.jsonl", start_row)
    rec = sc.review()
    assert rec["gates"]["gate2_lifecycle_converged"] is False
    assert rec["reasons"]["gate2_lifecycle_converged"] == ["unfinished_attempts"]
    assert rec["diagnostics"] is None


def test_gate2_fails_on_inline_attempt_started_row_in_forecasts(sc: Scenario):
    sc.append("forecasts.jsonl", {"question_id": QID, "type": "forecast_attempt_started", "attempt_id": "a7"})
    rec = sc.review()
    assert rec["gates"]["gate2_lifecycle_converged"] is False
    assert "unfinished_attempts" in rec["reasons"]["gate2_lifecycle_converged"]


@pytest.mark.parametrize(
    "newer_rows",
    [
        [{"question_id": QID, "intent_id": "i2", "status": "intent"}],
        [
            {"question_id": QID, "intent_id": "i2", "status": "intent"},
            {"question_id": QID, "intent_id": "i2", "operation": "forecast", "status": "request_started",
             "request_id": "r5"},
            {"question_id": QID, "intent_id": "i2", "operation": "forecast", "status": "forecast_ok",
             "request_id": "r5"},
        ],
    ],
    ids=["intent", "forecast_ok"],
)
def test_gate2_fails_when_newer_intent_pending_after_completed_one(sc: Scenario, newer_rows):
    sc.append("submissions.jsonl", *newer_rows)
    rec = sc.review()
    assert rec["gates"]["gate2_lifecycle_converged"] is False
    assert rec["reasons"]["gate2_lifecycle_converged"] == ["pending_submission_intents"]
    assert rec["diagnostics"] is None


def test_gate2_fails_when_submission_row_missing_intent_id(sc: Scenario):
    sc.append("submissions.jsonl", {"question_id": QID, "operation": "forecast", "status": "forecast_ok"})
    rec = sc.review()
    assert rec["gates"]["gate2_lifecycle_converged"] is False
    assert "missing_intent_id" in rec["reasons"]["gate2_lifecycle_converged"]


@pytest.mark.parametrize("request_id", ["r1", None], ids=["with_request_id", "no_request_id"])
def test_gate2_fails_on_unknown_operation_without_readback(sc: Scenario, request_id):
    sc.write("submissions.jsonl", submissions_with_unknown(request_id))
    rec = sc.review()
    assert rec["gates"]["gate2_lifecycle_converged"] is False
    assert rec["reasons"]["gate2_lifecycle_converged"] == ["unresolved_or_unknown_requests"]
    assert rec["diagnostics"] is None


@pytest.mark.parametrize(
    "readback",
    [
        {"intent_id": "i1", "request_id": "r1", "operation": "forecast", "outcome": "error"},
        {"intent_id": "i1", "request_id": "r999", "operation": "forecast", "outcome": "found"},
        {"intent_id": "i1", "request_id": "r1", "operation": "comment", "outcome": "found"},
        {"intent_id": "i9", "request_id": "r1", "operation": "forecast", "outcome": "found"},
    ],
    ids=["bad_outcome", "wrong_request", "wrong_operation", "wrong_intent"],
)
def test_gate2_fails_on_unknown_operation_with_non_matching_readback(sc: Scenario, readback):
    sc.write("submissions.jsonl", submissions_with_unknown())
    sc.write("submission_readbacks.jsonl", [readback])
    rec = sc.review()
    assert rec["gates"]["gate2_lifecycle_converged"] is False
    assert "unresolved_or_unknown_requests" in rec["reasons"]["gate2_lifecycle_converged"]


@pytest.mark.parametrize("outcome", ["found", "not_accepted", "FOUND"])
def test_gate2_passes_on_unknown_operation_with_matching_readback(sc: Scenario, outcome):
    sc.write("submissions.jsonl", submissions_with_unknown())
    sc.write("submission_readbacks.jsonl", [
        {"intent_id": "i1", "request_id": "r1", "operation": "forecast", "outcome": outcome},
    ])
    rec = sc.review()
    assert rec["gates"]["gate2_lifecycle_converged"] is True
    assert rec["reasons"]["gate2_lifecycle_converged"] == []
    assert rec["review_eligible"] is True


@pytest.mark.parametrize(
    "ledger,row",
    [
        ("question_queue.jsonl", {"question_id": 999, "type": "queue_state", "action": "skipped"}),
        ("submissions.jsonl", {"question_id": 999, "intent_id": "other", "status": "comment_ok"}),
    ],
)
def test_gate2_fails_on_concurrent_ledger_change_during_fetch(sc: Scenario, ledger, row):
    def racing_fetch(post_id):
        sc.append(ledger, row)  # simulates a scheduler writing during the API call
        return sc.fetch(post_id)

    rec = sc.review(fetch_post=racing_fetch)
    assert sc.fetch_calls == [POST_ID]
    assert rec["gates"]["gate1_platform_closed"] is True
    assert rec["gates"]["gate3_not_queued"] is True
    assert rec["gates"]["gate2_lifecycle_converged"] is False
    assert rec["reasons"]["gate2_lifecycle_converged"] == ["concurrent_ledger_change"]
    assert rec["concurrent_changes"] == [ledger]
    assert rec["review_eligible"] is False
    assert rec["diagnostics"] is None


@pytest.mark.parametrize("ledger", pca.REQUIRED_LEDGERS)
def test_gate2_fails_when_required_ledger_missing(sc: Scenario, ledger):
    sc.path(ledger).unlink()
    rec = sc.review()
    assert rec["ledger_errors"][ledger] == "missing"
    assert rec["gates"]["gate2_lifecycle_converged"] is False
    assert "ledger_incomplete_or_missing" in rec["reasons"]["gate2_lifecycle_converged"]
    assert rec["review_eligible"] is False
    assert rec["diagnostics"] is None


@pytest.mark.parametrize("ledger", pca.REQUIRED_LEDGERS + pca.OPTIONAL_LEDGERS)
def test_gate2_fails_when_ledger_has_truncated_json_line(sc: Scenario, ledger):
    sc.append_raw(ledger, '{"question_id": 101, "validation_status": "va')  # torn write, no newline
    rec = sc.review()
    assert rec["ledger_errors"][ledger].startswith("malformed_line:")
    assert rec["gates"]["gate2_lifecycle_converged"] is False
    assert "ledger_incomplete_or_missing" in rec["reasons"]["gate2_lifecycle_converged"]
    assert rec["diagnostics"] is None


def test_gate2_fails_when_ledger_has_non_object_line(sc: Scenario):
    sc.append_raw("aggregates.jsonl", "[1, 2, 3]\n")
    rec = sc.review()
    assert rec["ledger_errors"]["aggregates.jsonl"].startswith("non_object_line:")
    assert rec["gates"]["gate2_lifecycle_converged"] is False


def test_gate2_fails_when_reader_reports_incomplete(sc: Scenario, monkeypatch):
    def partial_reader(path):
        rows, _ = _fake_read_complete(path)
        return rows, Path(path).name != "aggregates.jsonl"

    monkeypatch.setattr(pca, "_read_complete", partial_reader)
    rec = sc.review()
    assert rec["ledger_errors"] == {"aggregates.jsonl": "incomplete"}
    assert rec["gates"]["gate2_lifecycle_converged"] is False
    assert rec["diagnostics"] is None


def test_gate2_fails_when_awaiting_scheduler_finalization(sc: Scenario):
    agg = default_aggregates()
    agg[0]["completion_status"] = "awaiting_scheduler_finalization"
    sc.write("aggregates.jsonl", agg)
    rec = sc.review()
    assert rec["reasons"]["gate2_lifecycle_converged"] == ["awaiting_scheduler_finalization"]


def test_gate2_passes_when_provisional_aggregate_followed_by_complete_local(sc: Scenario):
    provisional = copy.deepcopy(default_aggregates()[0])
    provisional["record_id"] = "agg-0"
    provisional["status"] = "provisional"
    provisional["completion_status"] = "awaiting_scheduler_finalization"
    final = copy.deepcopy(default_aggregates()[0])
    final["completion_status"] = "complete_local"
    sc.write("aggregates.jsonl", [provisional, final])
    rec = sc.review()
    assert rec["gates"]["gate2_lifecycle_converged"] is True
    assert rec["review_eligible"] is True


# --------------------------------------------------------------------------- gate 3
@pytest.mark.parametrize("action", ["queued", "requeued", "Requeued "])
def test_gate3_fails_when_latest_queue_action_active(sc: Scenario, action):
    sc.append("question_queue.jsonl", {"question_id": QID, "type": "queue_state", "action": action})
    rec = sc.review()
    assert rec["gates"]["gate3_not_queued"] is False
    assert rec["reasons"]["gate3_not_queued"] == [f"queue_active:{action.strip().lower()}"]
    assert rec["diagnostics"] is None


@pytest.mark.parametrize("action", ["pondering", None, 42])
def test_gate3_fails_on_unrecognized_or_missing_action(sc: Scenario, action):
    row: Dict[str, Any] = {"question_id": QID, "type": "queue_state"}
    if action is not None:
        row["action"] = action
    sc.append("question_queue.jsonl", row)
    rec = sc.review()
    assert rec["gates"]["gate3_not_queued"] is False
    assert rec["reasons"]["gate3_not_queued"] == ["queue_action_missing_or_unrecognized"]


def test_gate3_fails_when_latest_snapshot_invalidated(sc: Scenario):
    sc.append("question_queue.jsonl",
              {"question_id": QID, "type": "snapshot_invalidated", "old_snapshot_id": SNAP})
    rec = sc.review()
    assert rec["gates"]["gate3_not_queued"] is False
    assert rec["reasons"]["gate3_not_queued"] == ["snapshot_invalidated"]
    assert rec["diagnostics"] is None


def test_gate3_passes_when_only_older_snapshot_invalidated(sc: Scenario):
    sc.append("question_queue.jsonl",
              {"question_id": QID, "type": "snapshot_invalidated", "old_snapshot_id": OLD_SNAP})
    rec = sc.review()
    assert rec["gates"]["gate3_not_queued"] is True
    assert rec["review_eligible"] is True


def test_gate3_ignores_other_questions_queue_state(sc: Scenario):
    sc.append("question_queue.jsonl", {"question_id": 999, "type": "queue_state", "action": "queued"})
    assert sc.review()["gates"]["gate3_not_queued"] is True


# --------------------------------------------------------------------------- D45 masking
def _m_offline(sc: Scenario):
    return {"fetch_post": None}


def _m_open(sc: Scenario):
    sc.post["status"] = "open"
    sc.post["question"]["status"] = "open"
    return {}


def _m_recent(sc: Scenario):
    return {"now": "2026-11-01T12:05:00Z"}


def _m_malformed_close(sc: Scenario):
    sc.post["question"]["actual_close_time"] = 12345
    return {}


def _m_unfinished(sc: Scenario):
    sc.append("forecast_attempt_starts.jsonl", {"question_id": QID, "attempt_id": "a9"})
    return {}


def _m_pending_intent(sc: Scenario):
    sc.append("submissions.jsonl", {"question_id": QID, "intent_id": "i2", "status": "forecast_ok",
                                    "operation": "forecast", "request_id": "r5"})
    return {}


def _m_unknown(sc: Scenario):
    sc.write("submissions.jsonl", submissions_with_unknown())
    return {}


def _m_concurrent(sc: Scenario):
    def racing_fetch(pid):
        sc.append("submissions.jsonl", {"question_id": 999, "intent_id": "x", "status": "intent"})
        return sc.fetch(pid)
    return {"fetch_post": racing_fetch}


def _m_missing(sc: Scenario):
    sc.path("aggregates.jsonl").unlink()
    return {}


def _m_truncated(sc: Scenario):
    sc.append_raw("forecasts.jsonl", '{"question_id": 101, "prob')
    return {}


def _m_queue(action: str) -> Callable[[Scenario], Dict[str, Any]]:
    def mutate(sc: Scenario):
        sc.append("question_queue.jsonl", {"question_id": QID, "type": "queue_state", "action": action})
        return {}
    return mutate


def _m_invalidated(sc: Scenario):
    sc.append("question_queue.jsonl",
              {"question_id": QID, "type": "snapshot_invalidated", "old_snapshot_id": SNAP})
    return {}


MASKING_CASES = [
    ("g1_offline", _m_offline, "gate1_platform_closed"),
    ("g1_open", _m_open, "gate1_platform_closed"),
    ("g1_recent_close", _m_recent, "gate1_platform_closed"),
    ("g1_malformed_close", _m_malformed_close, "gate1_platform_closed"),
    ("g2_unfinished_attempt", _m_unfinished, "gate2_lifecycle_converged"),
    ("g2_pending_intent", _m_pending_intent, "gate2_lifecycle_converged"),
    ("g2_unknown_no_readback", _m_unknown, "gate2_lifecycle_converged"),
    ("g2_concurrent_change", _m_concurrent, "gate2_lifecycle_converged"),
    ("g2_missing_ledger", _m_missing, "gate2_lifecycle_converged"),
    ("g2_truncated_ledger", _m_truncated, "gate2_lifecycle_converged"),
    ("g3_queued", _m_queue("queued"), "gate3_not_queued"),
    ("g3_requeued", _m_queue("requeued"), "gate3_not_queued"),
    ("g3_unrecognized", _m_queue("pondering"), "gate3_not_queued"),
    ("g3_snapshot_invalidated", _m_invalidated, "gate3_not_queued"),
]


@pytest.mark.parametrize("mutate,failing_gate", [(m, g) for _, m, g in MASKING_CASES],
                         ids=[c[0] for c in MASKING_CASES])
def test_masking_on_every_rejection_path(sc: Scenario, mutate, failing_gate):
    overrides = mutate(sc)
    report = sc.report(**overrides)

    assert report["counts"]["evaluated"] == 1
    assert report["counts"]["eligible"] == 0
    (rec,) = report["questions"]
    assert rec["gates"][failing_gate] is False
    assert rec["reasons"][failing_gate], "a failing gate must carry a reason"
    assert rec["review_eligible"] is False
    assert rec["masked"] is True
    assert rec["diagnostics"] is None

    _assert_no_secrets(_dump(report))
    markdown = pca.render_markdown(report)
    _assert_no_secrets(markdown)
    assert "## Question" not in markdown
    assert "Health warnings" not in markdown


@pytest.mark.parametrize("mutate", [m for _, m, _ in MASKING_CASES], ids=[c[0] for c in MASKING_CASES])
def test_masking_with_only_eligible_drops_record(sc: Scenario, mutate):
    report = sc.report(only_eligible=True, **mutate(sc))
    assert report["questions"] == []
    assert report["counts"]["eligible"] == 0
    assert report["counts"]["masked"] is None
    _assert_no_secrets(_dump(report))
    _assert_no_secrets(pca.render_markdown(report))


@pytest.mark.parametrize("fmt", ["json", "markdown"])
def test_cli_offline_output_is_masked(sc: Scenario, capsys, fmt):
    rc = pca.main(["--data-dir", str(sc.data_dir), "--format", fmt])
    assert rc == 0
    out = capsys.readouterr().out
    _assert_no_secrets(out)
    assert "offline_or_no_post" in out
    if fmt == "json":
        report = json.loads(out)
        assert report["live_api"] is False
        assert all(r["diagnostics"] is None for r in report["questions"])


def test_masking_control_eligible_output_does_contain_secrets(sc: Scenario):
    """Positive control: proves the masking assertions above are meaningful."""
    report = sc.report()
    blob, md = _dump(report), pca.render_markdown(report)
    for secret in SECRET_PROBS:
        assert secret in blob and secret in md
    assert SECRET_TEXT in blob and SECRET_TEXT in md


# --------------------------------------------------------------------------- full disclosure
def test_full_diagnostic_disclosure_when_eligible(sc: Scenario):
    rec = sc.review()
    assert rec["review_eligible"] is True
    d = rec["diagnostics"]
    assert d["snapshot_id"] == SNAP

    # Only valid forecasts bound to the latest snapshot are included.
    assert set(d["players"]) == {"alpha", "beta"}
    assert "0.999" not in _dump(d)

    alpha, beta = d["players"]["alpha"], d["players"]["beta"]
    assert alpha["tier1"]["probability"] == 0.731
    assert alpha["tier1"]["attempt_id"] == "a1"
    assert alpha["tier1"]["reasoning_summary"] == "secret reasoning alpha initial"
    assert alpha["tier1"]["key_factors"] == ["polls"]
    assert alpha["tier2"]["probability"] == 0.70
    assert alpha["tier2"]["attempt_id"] == "a2"
    assert alpha["tier2"]["changed_from_tier1"] is True
    assert alpha["tier2"]["change_reason"] == "secret reasoning update"
    assert alpha["tier2"]["audit_flags"] == ["stale_market"]
    assert alpha["tier2"]["market_refs"] == ["mkt-1"]

    assert beta["tier1"]["probability"] == 0.642
    assert beta["tier2"]["probability"] == 0.66
    assert beta["tier2"]["changed_from_tier1"] is True

    assert d["tier1_spread"] == pytest.approx(0.089)
    assert d["tier2_spread"] == pytest.approx(0.04)

    agg = d["aggregate"]
    assert agg["record_id"] == "agg-1"
    assert agg["stage"] == "final"
    assert agg["status"] == "complete"
    assert agg["probability"] == 0.68
    assert agg["input_attempt_ids"] == ["a2", "b2"]

    ss = d["submission_summary"]
    assert ss["intent_count"] == 1
    assert ss["submitted"] is True
    (intent,) = ss["intents"]
    assert intent["intent_id"] == "i1"
    assert intent["final_status"] == "comment_ok"
    assert intent["statuses"] == ["intent", "request_started", "forecast_ok", "request_started", "comment_ok"]
    assert intent["operations"] == ["comment", "forecast"]
    assert intent["aggregate_record_id"] == "agg-1"

    assert d["health_warnings"] == sorted([
        "audit_flag:stale_market",
        "missing_player:gamma",
        "no_market_or_base_rate:beta",
    ])


def test_full_disclosure_markdown_rendering(sc: Scenario):
    report = sc.report()
    assert report["counts"] == {"evaluated": 1, "eligible": 1, "masked": 0}
    md = pca.render_markdown(report)

    assert md.startswith(f"# Post-close audit ({pca.VERSION})")
    assert "| 101 | True | True | True | True |  |" in md
    assert f"## Question 101 — {TITLE}" in md
    assert f"- Snapshot: `{SNAP}`" in md
    assert "- Aggregate `agg-1` stage=final status=complete p=0.680" in md
    assert "- Tier1 spread: 0.089  |  Tier2 spread: 0.040" in md
    assert "- Submissions: 1 intent(s), submitted=True" in md
    assert "| alpha | 0.731 | 0.700 | True | secret reasoning update | 0.400 |" in md
    assert '["mkt-1"]' in md
    assert '["stale_market"]' in md
    assert "| beta | 0.642 | 0.660 | True |" in md
    assert "- **alpha**: secret reasoning alpha revised" in md
    assert "- **beta**: secret reasoning beta revised" in md
    assert "**Health warnings:**" in md
    for w in ("audit_flag:stale_market", "missing_player:gamma", "no_market_or_base_rate:beta"):
        assert f"`{w}`" in md
    assert md.endswith("\n")


def test_diagnostics_high_dispersion_without_inventing_tier2_request(sc: Scenario):
    sc.write("forecasts.jsonl", [
        _fc("alpha", 1, 0.9, "a1", base_rate=0.5),
        _fc("beta", 1, 0.2, "b1", market_refs=["m"]),
    ])
    sc.write("forecast_attempt_starts.jsonl", [{"question_id": QID, "attempt_id": a} for a in ("a1", "b1")])
    agg = default_aggregates()
    agg[0]["expected_players"] = ["alpha", "beta"]
    sc.write("aggregates.jsonl", agg)
    rec = sc.review()
    assert rec["review_eligible"] is True
    d = rec["diagnostics"]
    assert d["tier1_spread"] == pytest.approx(0.7)
    assert d["tier2_spread"] is None
    assert d["players"]["alpha"]["tier2"] is None
    assert d["health_warnings"] == ["high_dispersion:0.700"]
    assert d["line_coverage"]["alpha"]["2"]["state"] == "not_requested_or_unobserved"


def test_diagnostics_warn_unsubmitted_final_aggregate(sc: Scenario):
    sc.write("submissions.jsonl", [
        {"question_id": QID, "intent_id": "i1", "status": "intent"},
        {"question_id": QID, "intent_id": "i1", "status": "dry_run"},
    ])
    rec = sc.review()
    assert rec["review_eligible"] is True
    d = rec["diagnostics"]
    assert d["submission_summary"]["submitted"] is False
    assert "unsubmitted_final_aggregate" in d["health_warnings"]


def test_report_question_id_filter_normalizes_string_ids(sc: Scenario):
    report = sc.report(question_ids=["101"])
    (rec,) = report["questions"]
    assert rec["question_id"] == QID
    assert rec["review_eligible"] is True


# --------------------------------------------------------------------------- read-only guarantee
def _fingerprint(*roots: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for root in roots:
        out[str(root)] = "<dir>"
        for p in sorted(root.rglob("*")):
            if p.is_dir():
                out[str(p)] = "<dir>"
            else:
                st = p.stat()
                out[str(p)] = (hashlib.sha256(p.read_bytes()).hexdigest(), st.st_size,
                               st.st_mtime_ns, st.st_mode)
    return out


class _WriteGuard:
    def __init__(self) -> None:
        self.protected: List[Path] = []
        self.violations: List[tuple] = []

    def protect(self, *paths: Path) -> None:
        self.protected.extend(Path(p).resolve() for p in paths)

    def is_protected(self, target: Any) -> bool:
        try:
            p = Path(os.fspath(target)).resolve()
        except TypeError:
            return False
        return any(p == r or r in p.parents for r in self.protected)

    def deny(self, op: str, *targets: Any) -> None:
        if any(self.is_protected(t) for t in targets):
            self.violations.append((op,) + tuple(str(t) for t in targets))
            raise PermissionError(f"read-only violation: {op} {targets}")


@pytest.fixture
def write_guard(monkeypatch):
    guard = _WriteGuard()
    real_open, real_os_open = builtins.open, os.open
    real_replace, real_rename = os.replace, os.rename
    real_remove, real_unlink, real_mkdir = os.remove, os.unlink, os.mkdir
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

    def g_open(file, mode="r", *a, **k):
        if any(c in mode for c in "wax+"):
            guard.deny(f"open[{mode}]", file)
        return real_open(file, mode, *a, **k)

    def g_os_open(path, flags, *a, **k):
        if flags & write_flags:
            guard.deny("os.open", path)
        return real_os_open(path, flags, *a, **k)

    def g_replace(src, dst, *a, **k):
        guard.deny("replace", src, dst)
        return real_replace(src, dst, *a, **k)

    def g_rename(src, dst, *a, **k):
        guard.deny("rename", src, dst)
        return real_rename(src, dst, *a, **k)

    def g_remove(path, *a, **k):
        guard.deny("remove", path)
        return real_remove(path, *a, **k)

    def g_unlink(path, *a, **k):
        guard.deny("unlink", path)
        return real_unlink(path, *a, **k)

    def g_mkdir(path, *a, **k):
        guard.deny("mkdir", path)
        return real_mkdir(path, *a, **k)

    monkeypatch.setattr(builtins, "open", g_open)
    monkeypatch.setattr(io, "open", g_open)
    monkeypatch.setattr(os, "open", g_os_open)
    monkeypatch.setattr(os, "replace", g_replace)
    monkeypatch.setattr(os, "rename", g_rename)
    monkeypatch.setattr(os, "remove", g_remove)
    monkeypatch.setattr(os, "unlink", g_unlink)
    monkeypatch.setattr(os, "mkdir", g_mkdir)
    return guard


def test_write_guard_self_check(sc: Scenario, write_guard: _WriteGuard):
    write_guard.protect(sc.data_dir, sc.config_dir)
    with pytest.raises(PermissionError):
        (sc.data_dir / "intruder.jsonl").write_text("x\n")
    with pytest.raises(PermissionError):
        with open(sc.config_dir / "settings.json", "a") as fh:
            fh.write("x")
    assert len(write_guard.violations) == 2
    write_guard.violations.clear()


@pytest.mark.parametrize("live", [True, False], ids=["live_fetch", "offline"])
def test_build_report_and_render_are_read_only(sc: Scenario, write_guard: _WriteGuard, live):
    before = _fingerprint(sc.data_dir, sc.config_dir)
    write_guard.protect(sc.data_dir, sc.config_dir)

    report = sc.report(fetch_post=sc.fetch if live else None)
    pca.render_markdown(report)
    pca.review_eligible(QID, data_dir=sc.data_dir, fetch_post=sc.fetch if live else None, now=NOW)

    assert write_guard.violations == []
    assert _fingerprint(sc.data_dir, sc.config_dir) == before
    assert report["questions"][0]["review_eligible"] is live


def _install_fake_metaculus(monkeypatch, sc: Scenario):
    class FakeMetaculusClient:
        instances: List["FakeMetaculusClient"] = []

        def __init__(self, token, manage_backoff=True):
            assert manage_backoff is False, "audit must never let the client write cooldowns"
            self.token = token
            self.calls: List[Any] = []
            FakeMetaculusClient.instances.append(self)

        def fetch_post(self, post_id):
            self.calls.append(post_id)
            return closed_post()

    fake_mod = types.ModuleType("flab.metaculus")
    fake_mod.MetaculusClient = FakeMetaculusClient
    monkeypatch.setitem(sys.modules, "flab.metaculus", fake_mod)
    monkeypatch.setenv("METACULUS_TOKEN", "test-token")
    return FakeMetaculusClient


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--format", "json"],
        ["--format", "markdown"],
        ["--format", "json", "--question-id", "101", "--only-eligible"],
    ],
    ids=["json", "markdown", "filtered"],
)
def test_main_with_mocked_live_client_is_read_only(sc: Scenario, write_guard: _WriteGuard, monkeypatch,
                                                    capsys, extra_args):
    fake_cls = _install_fake_metaculus(monkeypatch, sc)
    before = _fingerprint(sc.data_dir, sc.config_dir)
    write_guard.protect(sc.data_dir, sc.config_dir)

    rc = pca.main(["--data-dir", str(sc.data_dir), "--live-api", *extra_args])
    out = capsys.readouterr().out

    assert rc == 0
    assert write_guard.violations == []
    assert _fingerprint(sc.data_dir, sc.config_dir) == before
    assert not (sc.data_dir / "api_cooldowns.jsonl").exists()
    assert len(fake_cls.instances) == 1
    assert fake_cls.instances[0].token == "test-token"
    assert fake_cls.instances[0].calls == [POST_ID]
    if "json" in extra_args:
        report = json.loads(out)
        assert report["live_api"] is True
        assert report["version"] == pca.VERSION
    else:
        assert out.startswith(f"# Post-close audit ({pca.VERSION})")


def test_main_offline_is_read_only(sc: Scenario, write_guard: _WriteGuard, capsys):
    before = _fingerprint(sc.data_dir, sc.config_dir)
    write_guard.protect(sc.data_dir, sc.config_dir)
    assert pca.main(["--data-dir", str(sc.data_dir)]) == 0
    capsys.readouterr()
    assert write_guard.violations == []
    assert _fingerprint(sc.data_dir, sc.config_dir) == before


# --------------------------------------------------------------------------- CLI error paths
def test_main_rejects_negative_min_closed_minutes(sc: Scenario, capsys):
    assert pca.main(["--data-dir", str(sc.data_dir), "--min-closed-minutes", "-1"]) == 2
    assert "min-closed-minutes" in capsys.readouterr().err


def test_main_rejects_missing_data_dir(tmp_path: Path, capsys):
    assert pca.main(["--data-dir", str(tmp_path / "nope")]) == 2
    assert "data dir not found" in capsys.readouterr().err


def test_main_live_api_requires_token(sc: Scenario, monkeypatch, capsys):
    monkeypatch.delenv("METACULUS_TOKEN", raising=False)
    assert pca.main(["--data-dir", str(sc.data_dir), "--live-api"]) == 2
    assert "METACULUS_TOKEN" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["cycles.jsonl", "audits.jsonl", "scheduler_jobs.jsonl"])
def test_d135_corrupt_optional_evidence_masks_all_diagnostics(sc, name):
    sc.append_raw(name, '{"broken":')
    rec=sc.review()
    assert not rec['review_eligible'] and rec['diagnostics'] is None
    _assert_no_secrets(_dump(rec))


def test_d135_first_valid_and_frozen_selection(sc):
    sc.append('forecasts.jsonl',_fc('alpha',1,.123,'later',reasoning_summary='later answer'))
    assert sc.review()['diagnostics']['players']['alpha']['tier1']['attempt_id']=='a1'
    aggs=default_aggregates();aggs[0]['input_attempt_ids']=['later']
    sc.write('aggregates.jsonl',aggs)
    assert sc.review()['diagnostics']['players']['alpha']['tier1']['attempt_id']=='later'


def test_d135_cycle_evidence_binds_only_explicit_question(sc):
    sc.write('cycles.jsonl',[
       {'cycle_id':'unrelated','new_question_ids':[999], 'players':{'delta':{'status':'preflight_blocked','preflight':{'ok':False,'tier':1,'problems':['UNRELATED_PRIVATE']}}}},
       {'cycle_id':'related','new_question_ids':[QID], 'players':{'delta':{'status':'preflight_blocked','preflight':{'ok':False,'tier':1,'problems':['baseline stop']}}}}])
    d=sc.review()['diagnostics'];obs=d['line_coverage']['delta']['1']
    assert obs['state']=='no_attempt_with_cycle_block'
    assert [o['cycle_id'] for o in obs['cycle_observations']]==['related']
    assert 'UNRELATED_PRIVATE' not in _dump(d)
    assert 'baseline stop' in pca.render_markdown(sc.report())
    sc.post['status']='open';sc.post['question']['status']='open'
    rec=sc.review();assert not rec['review_eligible'] and rec['diagnostics'] is None
    assert 'baseline stop' not in _dump(rec)


@pytest.mark.parametrize('ids',[[str(QID)],str(QID)])
def test_d135_cycle_ids_are_typed_and_normalized(sc,ids):
    sc.write('cycles.jsonl',[{'cycle_id':'c','new_question_ids':ids,'players':{'delta':{'status':'ready'}}}])
    d=sc.review()['diagnostics']
    assert ('delta' in d['line_coverage']) == isinstance(ids,list)
