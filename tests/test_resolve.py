"""Unit tests for src/resolve.py (resolve_v2, PLAN.md §4.1, §4.4, §5.1)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from forecast import Lock  # noqa: E402
from flab.jsonl import append_record  # noqa: E402
from flab.ledger import read_complete  # noqa: E402
import resolve as R  # noqa: E402

NOW = "2026-11-06T14:00:00Z"


def _snap(
    qid: int = 101,
    post_id: int = 9001,
    qtype: str = "binary",
    close: str = "2026-11-03T12:00:00Z",
    resolve_at: Any = "2026-11-06T12:00:00Z",
    **extra: Any,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "type": "question_snapshot",
        "snapshot_id": f"snap_{qid}",
        "question_id": qid,
        "post_id": post_id,
        "tournament_id": 33135,
        "venue": "minibench",
        "question_type": qtype,
        "title": f"Question {qid}",
        "status": "open",
        "scheduled_close_time": close,
        "scheduled_resolve_time": resolve_at,
    }
    row.update(extra)
    return row


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for r in rows:
        append_record(path, r)


# --------------------------------------------------------------------------- classify_resolution
@pytest.mark.parametrize(
    "raw,expected_status,expected_outcome",
    [
        ("yes", "resolved", 1),
        ("YES", "resolved", 1),
        ("no", "resolved", 0),
        ("No ", "resolved", 0),
        ("ambiguous", "ambiguous", None),
        ("annulled", "annulled", None),
        ("canceled", "annulled", None),
        (None, "unresolved", None),
        ("", "unresolved", None),
    ],
)
def test_classify_resolution_binary(raw, expected_status, expected_outcome):
    snap = _snap(qtype="binary")
    post = {
        "id": 9001,
        "status": "resolved" if raw else "closed",
        "resolved": bool(raw),
        "question": {
            "id": 101,
            "type": "binary",
            "status": "resolved" if raw else "closed",
            "resolution": raw,
            "actual_resolve_time": "2026-11-06T12:05:00Z",
        },
    }
    status, outcome, got_raw, rtime = R.classify_resolution(post, snap)
    assert status == expected_status
    assert outcome == expected_outcome
    assert got_raw == raw
    assert rtime == "2026-11-06T12:05:00Z"


def test_classify_resolution_contradictory_open_status_rejected():
    snap = _snap(qtype="binary")
    # Post says resolved, but question says open -> must classify as unresolved
    post1 = {
        "id": 9001,
        "status": "resolved",
        "resolved": True,
        "question": {"id": 101, "type": "binary", "status": "open", "resolution": "yes"},
    }
    assert R.classify_resolution(post1, snap)[0] == "unresolved"

    # Question says resolved, but post says open -> must classify as unresolved
    post2 = {
        "id": 9001,
        "status": "open",
        "resolved": True,
        "question": {"id": 101, "type": "binary", "status": "resolved", "resolution": "yes"},
    }
    assert R.classify_resolution(post2, snap)[0] == "unresolved"

    # Neither resolved flag nor resolved status -> must classify as unresolved
    post3 = {
        "id": 9001,
        "status": "closed",
        "resolved": False,
        "question": {"id": 101, "type": "binary", "status": "closed", "resolution": "yes"},
    }
    assert R.classify_resolution(post3, snap)[0] == "unresolved"

    # Explicit resolved=False MUST be rejected even when status="resolved"
    post4 = {
        "id": 9001,
        "status": "resolved",
        "resolved": False,
        "question": {"id": 101, "type": "binary", "status": "resolved", "resolution": "yes"},
    }
    assert R.classify_resolution(post4, snap)[0] == "unresolved"

    # Missing actual_resolve_time MUST NOT fabricate a timestamp from scheduled_resolve_time
    post5 = {
        "id": 9001,
        "status": "resolved",
        "resolved": True,
        "question": {"id": 101, "type": "binary", "status": "resolved", "resolution": "yes"},
    }
    st5, out5, _, rtime5 = R.classify_resolution(post5, snap)
    assert (st5, out5, rtime5) == ("resolved", 1, None)


def test_classify_resolution_multiple_choice():
    snap = _snap(qtype="multiple_choice", options=["Democrats", "Republicans", "Other"])
    post = {
        "id": 9001,
        "status": "resolved",
        "question": {"id": 101, "type": "multiple_choice", "status": "resolved", "resolution": "Democrats"},
    }
    status, outcome, _, _ = R.classify_resolution(post, snap)
    assert (status, outcome) == ("resolved", "Democrats")

    post["question"]["resolution"] = "NonexistentOption"
    status2, outcome2, _, _ = R.classify_resolution(post, snap)
    assert (status2, outcome2) == ("unresolved", None)


@pytest.mark.parametrize(
    "raw,expected_status,expected_outcome",
    [
        ("-8.45", "resolved", -8.45),
        (1.19, "resolved", 1.19),
        ("below_lower_bound", "resolved", "below_lower_bound"),
        ("above_upper_bound", "resolved", "above_upper_bound"),
        ("ambiguous", "ambiguous", None),
        ("annulled", "annulled", None),
    ],
)
def test_classify_resolution_numeric_and_discrete(raw, expected_status, expected_outcome):
    snap = _snap(qtype="numeric", range_min=-20.0, range_max=20.0, open_lower_bound=True, open_upper_bound=True)
    post = {
        "id": 9001,
        "status": "resolved",
        "question": {"id": 101, "type": "numeric", "status": "resolved", "resolution": raw},
    }
    status, outcome, _, _ = R.classify_resolution(post, snap)
    assert status == expected_status
    assert outcome == expected_outcome


def test_classify_resolution_closed_bounds_and_discrete_grid_validation():
    snap_closed = _snap(
        qtype="numeric",
        range_min=0.0,
        range_max=100.0,
        open_lower_bound=False,
        open_upper_bound=False,
    )
    for invalid_raw in ("below_lower_bound", "above_upper_bound", -5.0, 105.0):
        post = {
            "id": 9001,
            "status": "resolved",
            "question": {"id": 101, "type": "numeric", "status": "resolved", "resolution": invalid_raw},
        }
        assert R.classify_resolution(post, snap_closed)[0] == "unresolved"

    snap_disc = _snap(
        qtype="discrete",
        nominal_min=0.0,
        nominal_max=10.0,
        range_min=-0.5,
        range_max=10.5,
        inbound_outcome_count=11,
        open_lower_bound=False,
        open_upper_bound=False,
    )
    post_ok = {
        "id": 9001,
        "status": "resolved",
        "question": {"id": 101, "type": "discrete", "status": "resolved", "resolution": 4.0},
    }
    assert R.classify_resolution(post_ok, snap_disc)[:2] == ("resolved", 4.0)

    post_bad_grid = {
        "id": 9001,
        "status": "resolved",
        "question": {"id": 101, "type": "discrete", "status": "resolved", "resolution": 4.3},
    }
    assert R.classify_resolution(post_bad_grid, snap_disc)[0] == "unresolved"

    # Missing open_lower_bound / open_upper_bound MUST default to False (closed, M5-NUMERIC-SPEC §2)
    snap_missing_flags = _snap(qtype="numeric", range_min=0.0, range_max=100.0)
    post_tail = {
        "id": 9001,
        "status": "resolved",
        "question": {"id": 101, "type": "numeric", "status": "resolved", "resolution": "above_upper_bound"},
    }
    assert R.classify_resolution(post_tail, snap_missing_flags)[0] == "unresolved"

    # Missing range_min / range_max on finite numeric outcome MUST fail closed
    snap_missing_bounds = _snap(qtype="numeric", open_lower_bound=False, open_upper_bound=False)
    post_finite = {
        "id": 9001,
        "status": "resolved",
        "question": {"id": 101, "type": "numeric", "status": "resolved", "resolution": 50.0},
    }
    assert R.classify_resolution(post_finite, snap_missing_bounds)[0] == "unresolved"


def test_extract_official_scores():
    post = {
        "id": 9001,
        "question": {
            "id": 101,
            "my_forecasts": {
                "history": [],
                "latest": None,
                "score_data": {
                    "spot_peer_score": 14.25,
                    "peer_score": 11.8,
                    "baseline_score": 22.0,
                    "coverage": 1.0,
                    "unlisted_field": "ignored",
                },
            },
        },
    }
    scores = R.extract_official_scores(post)
    assert scores == {
        "spot_peer_score": 14.25,
        "peer_score": 11.8,
        "baseline_score": 22.0,
        "coverage": 1.0,
    }


# --------------------------------------------------------------------------- candidate selection
def test_candidate_questions_filters_submitted_and_past_resolve_time(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_jsonl(data_dir / "questions.jsonl", [
        _snap(101, 9001, close="2026-11-03T12:00:00Z", resolve_at="2026-11-06T12:00:00Z"),  # eligible
        _snap(102, 9002, close="2026-11-03T12:00:00Z", resolve_at="2026-11-13T12:00:00Z", status="resolved"),  # future resolve even if status="resolved"
        _snap(103, 9003, close="2026-11-07T12:00:00Z", resolve_at="2026-11-06T12:00:00Z"),  # still open!
        _snap(104, 9004, close="2026-11-03T12:00:00Z", resolve_at="2026-11-06T12:00:00Z"),  # dry_run only
        _snap(105, 9005, close="2026-11-03T12:00:00Z", resolve_at="2026-11-06T12:00:00Z"),  # never submitted
        _snap(106, 9006, close="2026-11-03T12:00:00Z", resolve_at=None),  # missing resolve timestamp
    ])
    _write_jsonl(data_dir / "submissions.jsonl", [
        {"question_id": 101, "question_snapshot_id": "snap_101", "intent_id": "i1", "status": "comment_ok", "at": "2026-11-03T10:00:00Z"},
        {"question_id": 102, "question_snapshot_id": "snap_102", "intent_id": "i2", "status": "comment_ok", "at": "2026-11-03T10:00:00Z"},
        {"question_id": 103, "question_snapshot_id": "snap_103", "intent_id": "i3", "status": "comment_ok", "at": "2026-11-03T10:00:00Z"},
        {"question_id": 104, "question_snapshot_id": "snap_104", "intent_id": "i4", "status": "dry_run", "at": "2026-11-03T10:00:00Z"},
        {"question_id": 106, "question_snapshot_id": "snap_106", "intent_id": "i6", "status": "comment_ok", "at": "2026-11-03T10:00:00Z"},
    ])

    now_dt = R._coerce_now(NOW)
    cands = R.candidate_questions(data_dir, now_dt=now_dt)
    assert [c["question_id"] for c in cands] == [101]

    # Explicit --question-id MUST NOT bypass submission or scheduled_resolve_time gates!
    assert R.candidate_questions(data_dir, now_dt=now_dt, question_ids=[105]) == []
    assert R.candidate_questions(data_dir, now_dt=now_dt, question_ids=[104]) == []
    assert [c["question_id"] for c in R.candidate_questions(
        data_dir, now_dt=now_dt, question_ids=[104], include_dry_run=True
    )] == [104]
    assert R.candidate_questions(data_dir, now_dt=now_dt, question_ids=[102]) == []
    assert R.candidate_questions(data_dir, now_dt=now_dt, question_ids=[106]) == []

    # With allow_before_scheduled_resolve=True, 102 and 106 (closed, submitted) are included, while 103 (open) and 105 (unsubmitted) are not.
    cands_early = R.candidate_questions(data_dir, now_dt=now_dt, allow_before_scheduled_resolve=True)
    assert [c["question_id"] for c in cands_early] == [101, 102, 106]


def test_missing_submitted_snapshot_is_skipped_and_diagnosed(tmp_path: Path):
    data_dir = tmp_path / "data"
    # questions.jsonl has snap_101_v2, but submission references snap_101_v1 (absent from questions.jsonl)
    _write_jsonl(data_dir / "questions.jsonl", [
        _snap(101, 9001, snapshot_id="snap_101_v2"),
    ])
    _write_jsonl(data_dir / "submissions.jsonl", [
        {"question_id": 101, "question_snapshot_id": "snap_101_v1", "intent_id": "i1", "status": "comment_ok", "at": "2026-11-03T10:00:00Z"},
    ])
    now_dt = R._coerce_now(NOW)
    assert R.candidate_questions(data_dir, now_dt=now_dt) == []
    res = R.poll_resolutions(data_dir, fetch_post=lambda _pid: {}, now=NOW)
    assert res["polled"] == 0
    assert res["errors"] == [{
        "question_id": 101,
        "snapshot_id": "snap_101_v1",
        "error_type": "MissingSubmittedSnapshot",
    }]


# --------------------------------------------------------------------------- poll_resolutions & locks
def test_poll_resolutions_defers_when_submit_or_cycle_lock_held(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_jsonl(data_dir / "questions.jsonl", [_snap(101, 9001), _snap(102, 9002)])
    _write_jsonl(data_dir / "submissions.jsonl", [
        {"question_id": 101, "question_snapshot_id": "snap_101", "intent_id": "i1", "status": "comment_ok", "at": "2026-11-03T10:00:00Z"},
        {"question_id": 102, "question_snapshot_id": "snap_102", "intent_id": "i2", "status": "comment_ok", "at": "2026-11-03T10:00:00Z"},
    ])

    calls: List[int] = []
    with Lock(data_dir / "submit.lock"):
        res = R.poll_resolutions(data_dir, fetch_post=lambda pid: calls.append(pid) or {}, now=NOW)
    assert res["status"] == "deferred_busy_lock"
    assert res["busy_lock"] == "submit.lock"
    assert calls == []

    with Lock(data_dir / "cycle.lock"):
        res_cycle = R.poll_resolutions(data_dir, fetch_post=lambda pid: calls.append(pid) or {}, now=NOW)
    assert res_cycle["status"] == "deferred_busy_lock"
    assert res_cycle["busy_lock"] == "cycle.lock"
    assert calls == []


def test_poll_resolutions_yields_between_questions_and_during_pacing(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_jsonl(data_dir / "questions.jsonl", [_snap(101, 9001), _snap(102, 9002)])
    _write_jsonl(data_dir / "submissions.jsonl", [
        {"question_id": 101, "question_snapshot_id": "snap_101", "intent_id": "i1", "status": "comment_ok", "at": "2026-11-03T10:00:00Z"},
        {"question_id": 102, "question_snapshot_id": "snap_102", "intent_id": "i2", "status": "comment_ok", "at": "2026-11-03T10:00:00Z"},
    ])

    # Case 1: submit.lock becomes busy while polling question 1 -> question 2 is deferred immediately
    held_locks: List[Lock] = []
    calls: List[int] = []

    def fetch_then_lock(pid: int) -> Dict[str, Any]:
        calls.append(pid)
        lk = Lock(data_dir / "submit.lock")
        lk.__enter__()
        held_locks.append(lk)
        return {
            "id": pid,
            "status": "resolved",
            "resolved": True,
            "question": {"id": 101, "type": "binary", "status": "resolved", "resolution": "yes"},
        }

    try:
        res = R.poll_resolutions(data_dir, fetch_post=fetch_then_lock, now=NOW)
    finally:
        for lk in held_locks:
            lk.__exit__(None, None, None)

    assert res["status"] == "deferred_busy_lock"
    assert res["busy_lock"] == "submit.lock"
    assert res["polled"] == 1
    assert res["appended"] == 1
    assert calls == [9001]

    # Case 2: cycle.lock becomes busy mid-sleep (on the 2nd 0.1s slice) inside _build_client_fetcher -> interrupts sleep immediately
    opener_calls: List[str] = []
    sleep_slices: List[float] = []
    sleep_locks: List[Lock] = []
    cooldown_lock = data_dir / "api_cooldowns.lock"
    cooldown_lock.write_text("p", encoding="utf-8")

    def fake_sleep(sec: float) -> None:
        sleep_slices.append(sec)
        if len(sleep_slices) >= 2:
            lk = Lock(data_dir / "cycle.lock")
            lk.__enter__()
            sleep_locks.append(lk)

    def fake_opener(req: Any, _timeout: float) -> Any:
        opener_calls.append(req.full_url)
        raise AssertionError("opener should not be reached after cycle.lock becomes active")

    fetcher = R._build_client_fetcher(data_dir, token="test-token", opener=fake_opener, sleep_fn=fake_sleep)
    try:
        res_paced = R.poll_resolutions(data_dir, fetch_post=fetcher, now=NOW)
    finally:
        for lk in sleep_locks:
            lk.__exit__(None, None, None)

    assert res_paced["status"] == "deferred_busy_lock"
    assert res_paced["busy_lock"] == "cycle.lock"
    assert res_paced["polled"] == 0
    assert len(sleep_slices) == 2
    assert opener_calls == []


def test_poll_resolutions_idempotent_append_and_superseding_revisions(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_jsonl(data_dir / "questions.jsonl", [_snap(101, 9001)])
    _write_jsonl(data_dir / "submissions.jsonl", [
        {"question_id": 101, "question_snapshot_id": "snap_101", "intent_id": "i1", "status": "comment_ok", "at": "2026-11-03T10:00:00Z"},
    ])

    api_post: Dict[str, Any] = {
        "id": 9001,
        "status": "resolved",
        "resolved": True,
        "question": {
            "id": 101,
            "type": "binary",
            "status": "resolved",
            "resolution": "yes",
            "actual_resolve_time": "2026-11-06T12:30:00Z",
            "my_forecasts": {"history": [], "latest": None, "score_data": {}},
        },
    }

    # First poll: appends initial resolution_event
    r1 = R.poll_resolutions(data_dir, fetch_post=lambda _pid: api_post, now=NOW)
    assert r1["status"] == "ok"
    assert r1["polled"] == 1
    assert r1["appended"] == 1
    first_id = r1["events"][0]["resolution_event_id"]
    assert r1["events"][0]["canonical_outcome"] == 1
    assert r1["events"][0]["first_observed_at_utc"] == "2026-11-06T14:00:00.000Z"
    assert r1["events"][0]["supersedes_resolution_event_id"] is None

    # Second poll with identical state: no duplicate appended
    r2 = R.poll_resolutions(data_dir, fetch_post=lambda _pid: api_post, now="2026-11-06T15:00:00Z")
    assert r2["appended"] == 0
    assert len(read_complete(data_dir / "resolutions.jsonl")) == 1

    # Third poll after official spot_peer_score and leaderboard rank are published: appends superseding event
    api_post["question"]["my_forecasts"]["score_data"] = {"spot_peer_score": 18.4, "peer_score": 15.2, "rank": 4}
    r3 = R.poll_resolutions(
        data_dir,
        fetch_post=lambda _pid: api_post,
        fetch_ranking=lambda _tid, _uid: {"rank": 4, "matched_user_id": _uid},
        now="2026-11-06T16:00:00Z",
    )
    assert r3["appended"] == 1
    ev2 = r3["events"][0]
    assert ev2["supersedes_resolution_event_id"] == first_id
    assert ev2["first_observed_at_utc"] == "2026-11-06T14:00:00.000Z"
    assert ev2["official_score_data"] == {"spot_peer_score": 18.4, "peer_score": 15.2}
    assert ev2["official_score_first_observed_at_utc"] == "2026-11-06T16:00:00.000Z"
    # B4: rankings never live inside resolution events
    assert "official_ranking_data" not in ev2
    # These fixture rows carry no bot_id, so the bot identity cannot be proven: no ranking is recorded
    assert r3["ranking_observations"] == []
    assert r3["ranking_diagnostics"] == [
        {"tournament_id": 33135, "reason": "bot_identity_unavailable_or_ambiguous"}
    ]

    # Fourth poll: platform revises resolution to annulled -> appends superseding event (with updated first_observed_at_utc!)
    api_post["question"]["resolution"] = "annulled"
    api_post["question"]["my_forecasts"]["score_data"] = {}
    r4 = R.poll_resolutions(data_dir, fetch_post=lambda _pid: api_post, now="2026-11-06T17:00:00Z")
    assert r4["appended"] == 1
    ev3 = r4["events"][0]
    assert ev3["resolution_status"] == "annulled"
    assert ev3["canonical_outcome"] is None
    assert ev3["first_observed_at_utc"] == "2026-11-06T17:00:00.000Z"
    assert ev3["supersedes_resolution_event_id"] == ev2["resolution_event_id"]

    # Fifth poll: platform unresolves question -> appends superseding unresolved event (with updated first_observed_at_utc!)
    api_post["status"] = "closed"
    api_post["resolved"] = False
    api_post["question"]["status"] = "closed"
    api_post["question"]["resolution"] = None
    r5 = R.poll_resolutions(data_dir, fetch_post=lambda _pid: api_post, now="2026-11-06T18:00:00Z")
    assert r5["appended"] == 1
    ev4 = r5["events"][0]
    assert ev4["resolution_status"] == "unresolved"
    assert ev4["first_observed_at_utc"] == "2026-11-06T18:00:00.000Z"
    assert ev4["supersedes_resolution_event_id"] == ev3["resolution_event_id"]
    assert len(read_complete(data_dir / "resolutions.jsonl")) == 4


def test_build_client_ranking_fetcher_and_cli_main_and_unresolved_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    class FakeClient:
        def __init__(self, responses: dict[str, Any]):
            self.responses = responses
            self.calls: list[str] = []

        def get(self, path: str, params: Any = None) -> Any:
            self.calls.append(path)
            val = self.responses.get(path)
            if isinstance(val, Exception):
                raise val
            return val

        def get_post(self, post_id: int) -> dict[str, Any]:
            return self.responses[f"post:{post_id}"]

    # 1. Direct unit test of _build_client_ranking_fetcher (verifies Mapping annotation & leaderboard extraction)
    fc = FakeClient({
        "leaderboards/project/33135/": [{
            "id": 501, "project_id": 33135, "is_primary_leaderboard": True, "name": "MiniBench",
            "score_type": "peer_tournament", "finalized": False,
            "userEntry": {"user": {"id": 77, "username": "bot"}, "rank": 5, "calculated_on": "2026-11-06T12:00:00Z"},
            "entries": [{"user": {"id": 1}, "rank": 1}],
        }],
        "leaderboards/project/99999/": [{
            "project_id": 99999, "is_primary_leaderboard": True,
            "userEntry": {"user": {"id": 77}, "rank": 7},
        }],
    })
    fetcher = R._build_client_ranking_fetcher(tmp_path / "data", client=fc)  # type: ignore[arg-type]
    rk1 = fetcher(33135, 77)
    assert rk1 == {
        "rank": 5, "matched_user_id": 77, "endpoint": "leaderboards/project/33135/?primary_only=true",
        "leaderboard_id": 501, "leaderboard_name": "MiniBench", "leaderboard_score_type": "peer_tournament",
        "leaderboard_finalized": False, "calculated_on": "2026-11-06T12:00:00Z",
    }
    assert "total_participants" not in rk1

    rk2 = fetcher(99999, 77)
    assert rk2["rank"] == 7
    assert "participants_count" not in rk2
    # B3: the authenticated userEntry must be the bot account recorded on our submissions
    assert fetcher(33135, 78) == {}
    assert fetcher(33135, 0) == {}

    # PriorityYieldError inside _build_client_ranking_fetcher must propagate immediately
    fc_yield = FakeClient({
        "leaderboards/project/33135/": R.PriorityYieldError("submit.lock"),
    })
    fetcher_yield = R._build_client_ranking_fetcher(tmp_path / "data", client=fc_yield)  # type: ignore[arg-type]
    with pytest.raises(R.PriorityYieldError):
        fetcher_yield(33135, 77)

    # 2. First-time unresolved question with no score_data must NOT trigger tournament ranking fetch
    data_dir = tmp_path / "data"
    _write_jsonl(data_dir / "questions.jsonl", [
        {
            "type": "question_snapshot",
            "snapshot_id": "s_unres",
            "question_id": 501,
            "post_id": 9501,
            "tournament_id": 33135,
            "venue": "minibench",
            "question_type": "binary",
            "title": "Unresolved Q",
            "status": "closed",
            "scheduled_close_time": "2026-11-02T00:00:00Z",
            "scheduled_resolve_time": "2026-11-03T00:00:00Z",
            "seen_at": "2026-11-01T08:00:00Z",
        }
    ])
    _write_jsonl(data_dir / "submissions.jsonl", [
        {
            "question_id": 501,
            "question_snapshot_id": "s_unres",
            "intent_id": "i501",
            "aggregate_id": "agg501",
            "status": "comment_ok",
            "at": "2026-11-01T09:00:00Z",
        }
    ])
    ranking_calls: list[Any] = []
    unres_post = {
        "id": 9501,
        "status": "closed",
        "resolved": False,
        "question": {
            "id": 501,
            "type": "binary",
            "status": "closed",
            "resolution": None,
            "my_forecasts": {"history": [], "latest": None, "score_data": {}},
        },
    }
    res_unres = R.poll_resolutions(
        data_dir,
        fetch_post=lambda _pid: unres_post,
        fetch_ranking=lambda tid, uid: ranking_calls.append(tid) or {"rank": 1, "matched_user_id": uid},
        now=NOW,
    )
    assert res_unres["polled"] == 1
    assert res_unres["appended"] == 0
    assert ranking_calls == []

    # 3. CLI main() end-to-end with FakeClient wired through MetaculusClient
    resolved_post = {
        "id": 9501,
        "status": "resolved",
        "resolved": True,
        "question": {
            "id": 501,
            "type": "binary",
            "status": "resolved",
            "resolution": "no",
            "actual_resolve_time": "2026-11-06T12:00:00Z",
            "my_forecasts": {"history": [], "latest": None, "score_data": {"spot_peer_score": 10.0, "rank": 2}},
        },
    }
    fc_main = FakeClient({
        "post:9501": resolved_post,
        "leaderboards/project/33135/": [{"project_id": 33135, "is_primary_leaderboard": True,
                                          "userEntry": {"user": {"id": 77}, "rank": 3}}],
    })
    # Production submitted rows carry the approved bot id; it is the only accepted ranking identity.
    _write_jsonl(data_dir / "submissions.jsonl", [
        {"question_id": 501, "question_snapshot_id": "s_unres", "intent_id": "i501", "aggregate_id": "agg501",
         "status": "forecast_ok", "tournament_id": 33135, "bot_id": 77, "at": "2026-11-01T09:00:00Z"},
    ])
    monkeypatch.setattr(R, "_build_client", lambda *_a, **_kw: fc_main)
    rc = R.main(["--data-dir", str(data_dir)])
    assert rc == 0
    rows = read_complete(data_dir / "resolutions.jsonl")
    assert [r["type"] for r in rows] == ["resolution_event", "ranking_observation"]
    assert rows[0]["canonical_outcome"] == 0
    assert "official_ranking_data" not in rows[0]
    assert rows[1]["tournament_id"] == 33135
    assert rows[1]["rank"] == 3
    assert rows[1]["matched_user_id"] == 77
    assert rows[1]["ranking_verified"] is True
    assert rows[1]["supersedes_ranking_observation_id"] is None
    assert "total_participants" not in rows[1]

    # Re-running with an unchanged leaderboard state appends nothing (at most one per tournament per change)
    assert R.main(["--data-dir", str(data_dir)]) == 0
    assert len(read_complete(data_dir / "resolutions.jsonl")) == 2
    # A rank change appends exactly one new ranking_observation and no new resolution_event (B4)
    fc_main.responses["leaderboards/project/33135/"][0]["userEntry"]["rank"] = 2
    assert R.main(["--data-dir", str(data_dir)]) == 0
    rows = read_complete(data_dir / "resolutions.jsonl")
    assert [r["type"] for r in rows] == ["resolution_event", "ranking_observation", "ranking_observation"]
    assert rows[2]["rank"] == 2
    assert rows[2]["supersedes_ranking_observation_id"] == rows[1]["ranking_observation_id"]

    # 4. Strict post_id / question_id / question_type binding checks (MissingPostId, PostQuestionMismatch, QuestionTypeMismatch)
    data_dir_bind = tmp_path / "data_bind"
    _write_jsonl(data_dir_bind / "questions.jsonl", [
        {
            "type": "question_snapshot",
            "snapshot_id": "s_no_pid",
            "question_id": 601,
            "question_type": "binary",
            "scheduled_close_time": "2026-11-02T00:00:00Z",
            "scheduled_resolve_time": "2026-11-03T00:00:00Z",
            "seen_at": "2026-11-01T08:00:00Z",
        },
        {
            "type": "question_snapshot",
            "snapshot_id": "s_bad_qid",
            "question_id": 602,
            "post_id": 9602,
            "question_type": "binary",
            "scheduled_close_time": "2026-11-02T00:00:00Z",
            "scheduled_resolve_time": "2026-11-03T00:00:00Z",
            "seen_at": "2026-11-01T08:00:00Z",
        },
        {
            "type": "question_snapshot",
            "snapshot_id": "s_bad_type",
            "question_id": 603,
            "post_id": 9603,
            "question_type": "binary",
            "scheduled_close_time": "2026-11-02T00:00:00Z",
            "scheduled_resolve_time": "2026-11-03T00:00:00Z",
            "seen_at": "2026-11-01T08:00:00Z",
        },
    ])
    _write_jsonl(data_dir_bind / "submissions.jsonl", [
        {"question_id": 601, "question_snapshot_id": "s_no_pid", "intent_id": "i601", "status": "comment_ok", "at": "2026-11-01T09:00:00Z"},
        {"question_id": 602, "question_snapshot_id": "s_bad_qid", "intent_id": "i602", "status": "comment_ok", "at": "2026-11-01T09:00:00Z"},
        {"question_id": 603, "question_snapshot_id": "s_bad_type", "intent_id": "i603", "status": "comment_ok", "at": "2026-11-01T09:00:00Z"},
    ])
    posts_bind = {
        9602: {"id": 9602, "status": "resolved", "resolved": True, "question": {"id": 999999, "type": "binary", "status": "resolved", "resolution": "yes"}},
        9603: {"id": 9603, "status": "resolved", "resolved": True, "question": {"id": 603, "type": "numeric", "status": "resolved", "resolution": "1.0"}},
    }
    res_bind = R.poll_resolutions(data_dir_bind, fetch_post=lambda pid: posts_bind[pid], now=NOW)
    assert res_bind["appended"] == 0
    err_types = {e["question_id"]: e["error_type"] for e in res_bind["errors"]}
    assert err_types == {
        601: "MissingPostId",
        602: "PostQuestionMismatch",
        603: "QuestionTypeMismatch",
    }



@pytest.mark.parametrize('payload', [
    {'rank': 1},
    [{'project_id': 33135, 'is_primary_leaderboard': True, 'entries': [{'rank': 1}]}],
    [{'project_id': 99999, 'is_primary_leaderboard': True, 'userEntry': {'rank': 1}}],
    [{'project_id': 33135, 'is_primary_leaderboard': False, 'userEntry': {'rank': 1}}],
    [{'project_id': 33135, 'is_primary_leaderboard': True, 'userEntry': {'rank': True}}],
    [{'project_id': 33135, 'is_primary_leaderboard': True, 'userEntry': {'rank': 0}}],
    [{'project_id': 33135, 'is_primary_leaderboard': True, 'userEntry': {'rank': 1}}] * 2,
    # B3: userEntry must name the expected bot account
    [{'project_id': 33135, 'is_primary_leaderboard': True, 'userEntry': {'rank': 1}}],
    [{'project_id': 33135, 'is_primary_leaderboard': True, 'userEntry': {'user': {'id': 78}, 'rank': 1}}],
    [{'project_id': 33135, 'is_primary_leaderboard': True, 'userEntry': {'user': {'id': '77'}, 'rank': 1}}],
    [{'project_id': 33135, 'is_primary_leaderboard': True, 'userEntry': {'user': None, 'rank': 1}}],
])
def test_ranking_does_not_guess_identity_or_primary_board(tmp_path, payload):
    class Client:
        def get(self, path, params=None):
            assert path == 'leaderboards/project/33135/'
            assert params == {'primary_only': 'true'}
            return payload
    assert R._build_client_ranking_fetcher(tmp_path, client=Client())(33135, 77) == {}


def test_priority_probe_observes_other_process_lock(tmp_path):
    import subprocess
    path = tmp_path / 'submit.lock'
    child = subprocess.Popen(
        [sys.executable, '-c',
         'import fcntl,sys;f=open(sys.argv[1],"a");'
         'fcntl.flock(f,fcntl.LOCK_EX);print("ready",flush=True);sys.stdin.read()',
         str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == 'ready'
        assert R._is_lock_busy(path)
        assert R.poll_resolutions(tmp_path)['status'] == 'deferred_busy_lock'
    finally:
        child.communicate('', timeout=5)
    assert not R._is_lock_busy(path)


def test_ranking_observation_whitelists_fetcher_keys_and_treats_missing_leaderboard_as_diagnostic(tmp_path: Path):
    from metaculus import MetaculusError

    data_dir = tmp_path / "data"
    _write_jsonl(data_dir / "questions.jsonl", [_snap(101, 9001)])
    _write_jsonl(data_dir / "submissions.jsonl", [
        {"question_id": 101, "question_snapshot_id": "snap_101", "intent_id": "i1", "status": "forecast_ok",
         "tournament_id": 33135, "bot_id": 77, "at": "2026-11-03T10:00:00Z"},
    ])
    post = {"id": 9001, "status": "resolved", "resolved": True,
            "question": {"id": 101, "type": "binary", "status": "resolved", "resolution": "yes",
                         "actual_resolve_time": "2026-11-06T12:00:00Z",
                         "my_forecasts": {"score_data": {"spot_peer_score": 3.0}}}}

    # N1: a fetcher cannot override fixed observation fields or inject extra keys
    hostile = {"rank": 2, "matched_user_id": 77, "type": "resolution_event", "ranking_verified": False,
               "tournament_id": 1, "total_participants": 999}
    r1 = R.poll_resolutions(data_dir, fetch_post=lambda _p: post, fetch_ranking=lambda _t, _u: dict(hostile),
                            now="2026-11-06T14:00:00Z")
    obs = r1["ranking_observations"][0]
    assert obs["type"] == "ranking_observation"
    assert obs["ranking_verified"] is True
    assert obs["tournament_id"] == 33135
    assert "total_participants" not in obs
    assert r1["errors"] == []

    # N3: no leaderboard yet (HTTP 404) is a diagnostic, not an error that fails main()
    def missing(_t: int, _u: int) -> dict:
        raise MetaculusError(404, "read", "not found")

    data_dir2 = tmp_path / "data2"
    for name in ("questions.jsonl", "submissions.jsonl"):
        _write_jsonl(data_dir2 / name, read_complete(data_dir / name))
    r2 = R.poll_resolutions(data_dir2, fetch_post=lambda _p: post, fetch_ranking=missing, now="2026-11-06T14:00:00Z")
    assert r2["errors"] == []
    assert r2["ranking_diagnostics"] == [{"tournament_id": 33135, "reason": "leaderboard_not_found"}]
    assert set(r2) == set(r1)


def test_mid_loop_yield_returns_complete_key_set(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_jsonl(data_dir / "questions.jsonl", [_snap(101, 9001)])
    _write_jsonl(data_dir / "submissions.jsonl", [
        {"question_id": 101, "question_snapshot_id": "snap_101", "intent_id": "i1", "status": "forecast_ok",
         "tournament_id": 33135, "bot_id": 77, "at": "2026-11-03T10:00:00Z"},
    ])

    def yielding(_pid: int) -> dict:
        raise R.PriorityYieldError("submit.lock")

    res = R.poll_resolutions(data_dir, fetch_post=yielding, now="2026-11-06T14:00:00Z")
    assert res["status"] == "deferred_busy_lock"
    assert res["busy_lock"] == "submit.lock"
    assert res["ranking_observations"] == [] and res["ranking_diagnostics"] == []
    assert isinstance(res["events"], list) and isinstance(res["errors"], list)


def test_snapshotless_probe_submission_is_skipped_not_an_error(tmp_path: Path):
    """D87 V15 synthetic probes are submitted without a question snapshot; they must not fail every run."""
    data_dir = tmp_path / "data"
    _write_jsonl(data_dir / "questions.jsonl", [_snap(101, 9001)])
    _write_jsonl(data_dir / "submissions.jsonl", [
        {"question_id": 46451, "question_snapshot_id": None, "intent_id": "v15_1", "status": "forecast_ok",
         "mode": "trial", "tournament_id": 32977, "bot_id": 77, "at": "2026-11-03T10:00:00Z"},
    ])
    res = R.poll_resolutions(data_dir, fetch_post=lambda _pid: {}, now=NOW)
    assert res["status"] == "ok"
    assert res["errors"] == []
    assert res["skipped"] == [{"question_id": 46451, "skip_reason": "submitted_without_snapshot_id"}]
    assert res["polled"] == 0

    # A LIVE submission without a snapshot id is an integrity problem and stays an error
    live_dir = tmp_path / "live"
    _write_jsonl(live_dir / "questions.jsonl", [_snap(101, 9001)])
    _write_jsonl(live_dir / "submissions.jsonl", [
        {"question_id": 101, "question_snapshot_id": None, "intent_id": "i1", "status": "forecast_ok",
         "mode": "live", "tournament_id": 33135, "bot_id": 77, "at": "2026-11-03T10:00:00Z"},
    ])
    res_live = R.poll_resolutions(live_dir, fetch_post=lambda _pid: {}, now=NOW)
    assert res_live["status"] == "error"
    assert res_live["skipped"] == []
    assert res_live["errors"] == [{"question_id": 101, "snapshot_id": None, "error_type": "MissingSubmittedSnapshot"}]


@pytest.mark.parametrize('mode', [None, '', ' ', 'Live', 'live ', ' trial', 'TRIAL', 'dry_run', 'unknown', 1])
@pytest.mark.parametrize('snapshot_id', [None, '', '   '])
def test_snapshotless_malformed_mode_is_error_not_probe(tmp_path, mode, snapshot_id):
    _write_jsonl(tmp_path / 'submissions.jsonl', [
        {'question_id': 101, 'intent_id': 'i1', 'status': 'forecast_ok', 'mode': mode,
         'question_snapshot_id': snapshot_id, 'at': '2026-11-03T10:00:00Z'},
    ])
    result = R.poll_resolutions(tmp_path, now=NOW)
    assert result['status'] == 'error'
    assert result['skipped'] == []
    assert result['errors'] == [{'question_id': 101, 'snapshot_id': None, 'error_type': 'MissingSubmittedSnapshot'}]


def test_live_receipt_without_mode_inherits_intent_and_cli_fails(tmp_path, monkeypatch, capsys):
    _write_jsonl(tmp_path / 'submissions.jsonl', [
        {'question_id': 101, 'intent_id': 'i1', 'status': 'intent', 'mode': 'live',
         'question_snapshot_id': None, 'at': '2026-11-03T09:00:00Z'},
        {'question_id': 101, 'intent_id': 'i1', 'status': 'forecast_ok',
         'at': '2026-11-03T10:00:00Z'},
    ])
    result = R.poll_resolutions(tmp_path, now=NOW)
    assert result['status'] == 'error' and result['skipped'] == []
    assert result['errors'][0]['error_type'] == 'MissingSubmittedSnapshot'

    class NoNetwork:
        def get_post(self, _post_id):
            raise AssertionError('Invalid identity must not call the API')
        def get(self, *_args, **_kwargs):
            raise AssertionError('Invalid identity must not call the API')
    monkeypatch.setattr(R, '_build_client', lambda *_a, **_kw: NoNetwork())
    assert R.main(['--data-dir', str(tmp_path)]) == 1
    import json
    assert json.loads(capsys.readouterr().out)['status'] == 'error'


def test_conflicting_receipt_mode_cannot_downgrade_live_to_trial(tmp_path):
    _write_jsonl(tmp_path / 'submissions.jsonl', [
        {'question_id': 101, 'intent_id': 'i1', 'status': 'intent', 'mode': 'live',
         'question_snapshot_id': None, 'at': '2026-11-03T09:00:00Z'},
        {'question_id': 101, 'intent_id': 'i1', 'status': 'forecast_ok', 'mode': 'trial',
         'at': '2026-11-03T10:00:00Z'},
    ])
    result = R.poll_resolutions(tmp_path, now=NOW)
    assert result['status'] == 'error' and result['skipped'] == []
    assert result['errors'][0]['error_type'] == 'MissingSubmittedSnapshot'


@pytest.mark.parametrize('mode,skip', [('local', True), ('trial', True), ('live', False),
                                     (None, False), (' trial', False)])
def test_dry_candidates_preserves_diagnostics_without_api_or_writes(tmp_path, monkeypatch, capsys, mode, skip):
    _write_jsonl(tmp_path / 'questions.jsonl', [_snap(102, 9002)])
    _write_jsonl(tmp_path / 'submissions.jsonl', [
        {'question_id': 101, 'intent_id': 'i1', 'status': 'intent', 'mode': mode,
         'question_snapshot_id': None, 'at': '2026-11-03T09:00:00Z'},
        {'question_id': 101, 'intent_id': 'i1', 'status': 'forecast_ok', 'at': '2026-11-03T10:00:00Z'},
        {'question_id': 102, 'intent_id': 'i2', 'status': 'forecast_ok', 'mode': 'live',
         'question_snapshot_id': 'snap_102', 'at': '2026-11-03T10:00:00Z'},
    ])
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    def no_client(*_args, **_kwargs):
        raise AssertionError('Dry candidates must not create an API client')
    monkeypatch.setattr(R, '_build_client', no_client)
    monkeypatch.setattr(R, 'utc_now', lambda: R.parse_iso(NOW))
    assert R.main(['--data-dir', str(tmp_path), '--dry-candidates']) == int(not skip)
    import json
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == ('ok' if skip else 'error')
    assert result['skipped'] == ([{'question_id': 101, 'skip_reason': 'submitted_without_snapshot_id'}] if skip else [])
    assert result['errors'] == ([] if skip else [{'question_id': 101, 'snapshot_id': None, 'error_type': 'MissingSubmittedSnapshot'}])
    assert [c['question_id'] for c in result['candidates']] == [102]
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_dry_candidates_empty_directory_is_success(tmp_path, monkeypatch, capsys):
    def no_client(*_args, **_kwargs):
        raise AssertionError('Dry candidates must not create an API client')
    monkeypatch.setattr(R, '_build_client', no_client)
    assert R.main(['--data-dir', str(tmp_path), '--dry-candidates']) == 0
    import json
    assert json.loads(capsys.readouterr().out) == {
        'version': R.VERSION, 'status': 'ok', 'candidates': [], 'errors': [], 'skipped': [],
    }
    assert list(tmp_path.iterdir()) == []
