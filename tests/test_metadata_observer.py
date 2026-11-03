import copy
import io
import json
from datetime import timedelta
from urllib.error import HTTPError

import pytest

import metadata_observer as M
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import parse_iso, to_iso

NOW = parse_iso("2026-11-03T00:00:00Z")


def at(seconds):
    return to_iso(NOW + timedelta(seconds=seconds))


def policy():
    return {"version": M.VERSION, "observation_id": "synthetic", "start": at(0),
        "cohort_end": at(1200), "end": at(2400), "main_id": 1,
        "interval_s": 600, "poll_budget_s": 480, "max_requests": 50,
        "max_requests_per_poll": 20, "max_pages": 2, "max_projects": 4,
        "listing_order": "published_at", "inventory_passes": 2,
        "replay_recipe": {"trigger": "after_completion", "minimum_coverage": .9}}


def post(qid=10, kind="binary", opening=100):
    return {"id": qid + 100, "title": "PRIVATE TITLE", "projects": {"tournament": [{"id": 1}, {"id": 2}]}, "question": {"id": qid,
        "title": "PRIVATE TITLE", "type": kind, "status": "open", "open_time": at(opening),
        "scheduled_close_time": at(10000), "description": "SECRET BODY",
        "options": ["SECRET OPTION"], "my_forecasts": {"latest": "SECRET FORECAST"},
        "aggregations": {"recency_weighted": "SECRET CP"}}}


def registered(tmp_path, cfg=None):
    cfg = cfg or policy()
    M.register(tmp_path, cfg, now=lambda: NOW - timedelta(seconds=1))
    return cfg


class Fake:
    def __init__(self, pages=None):
        self.pages = pages or [{"results": [post()], "next": None}]
        self.index = [{"id": 2, "slug": "minibench", "is_ongoing": True, "start_date": at(-86400)}]
        self.calls = []
        self.in_poll = 0

    def get(self, path, params=None):
        self.calls.append((path, params))
        self.in_poll += 1
        if path == "projects/minibenches/":
            return copy.deepcopy(self.index)
        return copy.deepcopy(self.pages[0])

    def _get_url(self, url):
        self.calls.append((url, None))
        self.in_poll += 1
        return copy.deepcopy(self.pages[-1])


def test_frozen_registration_and_no_backdating(tmp_path):
    cfg = registered(tmp_path)
    M.register(tmp_path, cfg, now=lambda: NOW)
    cfg["end"] = at(2500)
    with pytest.raises(ValueError, match="frozen"):
        M.register(tmp_path, cfg, now=lambda: NOW)
    with pytest.raises(ValueError, match="before start"):
        M.register(tmp_path / "late", policy(), now=lambda: NOW)


def test_snapshot_cannot_persist_prediction_text_or_options(tmp_path):
    cfg, client = registered(tmp_path), Fake()
    M.collect(client, tmp_path, cfg, 0, set(), now=lambda: NOW)
    saved = (tmp_path / "snapshots.jsonl").read_text()
    assert "PRIVATE" not in saved and "SECRET" not in saved
    assert "my_forecasts" not in saved and "aggregations" not in saved
    assert "content_sha256" in saved and "structure_sha256" in saved
    for path, params in client.calls:
        if path == "posts/":
            assert params["statuses"] == M.STATUSES
            assert "forecast_type" not in params


def test_unsupported_and_groups_remain_in_denominator(tmp_path):
    cfg = registered(tmp_path)
    numeric = post(20, "numeric")
    group = {"id": 130, "title": "PRIVATE GROUP", "projects": {"tournament": [{"id": 1}, {"id": 2}]},
             "group_of_questions": {"questions": [post(30)["question"]]}}
    M.collect(Fake([{"results": [post(), numeric, group], "next": None}]), tmp_path, cfg, 0, set(), now=lambda: NOW)
    r = M.report(tmp_path)
    assert r["observed_cohort_all"] == 3
    assert r["observed_cohort_supported"] == 1
    assert r["m3_passed"] is False


def test_all_states_keep_previously_unseen_closed_questions(tmp_path):
    cfg = registered(tmp_path)
    closed = post()
    closed["question"]["status"] = "closed"
    M.collect(Fake([{"results": [closed], "next": None}]), tmp_path, cfg, 0, set(), now=lambda: NOW)
    assert M.report(tmp_path)["observed_cohort_all"] == 1


def test_completed_rounds_remain_tracked(tmp_path):
    cfg, client, tracked = registered(tmp_path), Fake(), set()
    assert M.discover(client, cfg, tracked) == [("main", 1), ("minibench", 2)]
    client.index = [{"id": 3, "slug": "minibench", "is_ongoing": True, "start_date": at(100)},
                    {"id": 2, "is_ongoing": False, "start_date": at(-86400)}]
    assert M.discover(client, cfg, tracked) == [("main", 1), ("minibench", 2), ("minibench", 3)]


@pytest.mark.parametrize("pages", [
    [{"results": [post()], "next": "https://www.metaculus.com/api/posts/?offset=100"}],
    [{"results": "unexpected", "next": None}],
    [{"results": [post(), post()], "next": None}],
    [{"results": [{"id": 1}], "next": None}],
])
def test_incomplete_pages_stop_without_claiming_complete_poll(tmp_path, pages):
    cfg = registered(tmp_path)
    with pytest.raises(ValueError):
        M.collect(Fake(pages), tmp_path, cfg, 0, set(), now=lambda: NOW)
    assert M.report(tmp_path)["completed_polls"] == 0
    assert read_complete(tmp_path / "polls.jsonl")[-1]["ok"] is False


def test_http_rejection_no_retry_no_error_body_or_token(tmp_path):
    cfg = registered(tmp_path)
    calls = []

    def rejected(req, timeout):
        calls.append(req)
        raise HTTPError(req.full_url, 429, "SECRET REMOTE ERROR", {}, io.BytesIO(b"SECRET BODY"))

    client = M.JournalClient("SECRET TOKEN", tmp_path, cfg, now=lambda: NOW, opener=rejected)
    with pytest.raises(M.MetaculusError):
        M.collect(client, tmp_path, cfg, 0, set(), now=lambda: NOW)
    assert len(calls) == 1 and calls[0].method == "GET"
    assert "SECRET" not in (tmp_path / "requests.jsonl").read_text()
    assert "SECRET" not in (tmp_path / "polls.jsonl").read_text()


def test_request_limit_blocks_next_network_call(tmp_path):
    cfg = registered(tmp_path)
    calls = []

    def opened(req, timeout):
        calls.append(req)
        return io.BytesIO(b"{}")

    client = M.JournalClient("secret", tmp_path, cfg, now=lambda: NOW, opener=opened)
    client.deadline = NOW + timedelta(seconds=480)
    client.total = cfg["max_requests"]
    with pytest.raises(M.ObservationStop):
        client.get("posts/")
    assert calls == []


def test_crash_or_restart_never_replays_started_observation(tmp_path):
    registered(tmp_path)
    append_record(tmp_path / "lifecycle.jsonl", {"type": "started", "at": at(0)})
    with pytest.raises(ValueError, match="cannot be resumed"):
        M.observe(tmp_path, lambda _: pytest.fail("must not construct client"))


def test_sleep_gaps_are_recorded_and_no_catchup_burst(tmp_path):
    cfg = registered(tmp_path)
    current = [NOW + timedelta(seconds=1250)]
    client = Fake()
    result = M.observe(tmp_path, lambda _: client, now=lambda: current[0],
        sleep=lambda seconds: current.__setitem__(0, current[0] + timedelta(seconds=seconds)))
    assert result["lifecycle"] == "completed"
    assert result["completed_polls"] == 2
    assert result["missing_or_failed_polls"] == 2
    missed = [r["slot"] for r in read_complete(tmp_path / "polls.jsonl") if r["type"] == "poll_missed"]
    assert missed == [0, 1]


def test_revision_moves_outside_window_cannot_shrink_denominator(tmp_path):
    cfg, client = registered(tmp_path), Fake()
    M.collect(client, tmp_path, cfg, 0, set(), now=lambda: NOW)
    client.pages[0]["results"][0]["question"]["open_time"] = at(-1000)
    client.pages[0]["results"][0]["question"]["description"] = "REVISED PRIVATE CONTENT"
    M.collect(client, tmp_path, cfg, 1, set(), now=lambda: NOW + timedelta(seconds=600))
    r = M.report(tmp_path)
    assert r["observed_cohort_all"] == 1
    assert r["questions_with_content_changes"] == 1


def test_empty_window_is_not_coverage_pass(tmp_path):
    cfg = registered(tmp_path)
    M.collect(Fake([{"results": [], "next": None}]), tmp_path, cfg, 0, set(), now=lambda: NOW)
    result = M.report(tmp_path)
    assert result["observed_cohort_all"] == 0 and result["m3_passed"] is False


def test_bad_membership_is_not_silently_counted(tmp_path):
    cfg = registered(tmp_path)
    wrong = post()
    wrong["projects"] = {"tournament": [{"id": 999}]}
    with pytest.raises(ValueError, match="membership"):
        M.collect(Fake([{"results": [wrong], "next": None}]), tmp_path, cfg, 0, set(), now=lambda: NOW)


def test_network_check_after_pacing_cannot_cross_end(tmp_path):
    cfg = registered(tmp_path)
    current, calls = [NOW], []
    client = M.JournalClient("secret", tmp_path, cfg, now=lambda: current[0],
                             opener=lambda req, timeout: calls.append(req))
    client.deadline = NOW + timedelta(seconds=1)
    def delayed_open(opener, request, timeout):
        current[0] += timedelta(seconds=2)
        return opener(request, timeout)
    client._backoff.open = delayed_open
    with pytest.raises(M.ObservationStop):
        client.get("posts/")
    assert calls == []


def test_changed_implementation_blocks_run_not_report(tmp_path, monkeypatch):
    registered(tmp_path)
    monkeypatch.setattr(M, "implementation", lambda: {"changed": "hash"})
    with pytest.raises(ValueError, match="implementation"):
        M.observe(tmp_path, lambda _: pytest.fail("no client"))
    assert M.report(tmp_path)["lifecycle"] == "registered"


class Paged(Fake):
    def __init__(self, shift=False, bad_count=False):
        super().__init__()
        self.pass_number = 0
        self.shift, self.bad_count = shift, bad_count

    def get(self, path, params=None):
        if path == "projects/minibenches/":
            return super().get(path, params)
        self.pass_number += 1
        assert params["order_by"] == "published_at"
        self.in_poll += 1
        return {"results": [post()], "count": 3 if self.bad_count else 2,
                "next": "https://www.metaculus.com/api/posts/?offset=100"}

    def _get_url(self, url):
        self.in_poll += 1
        qid = 30 if self.shift and self.pass_number == 2 else 20
        return {"results": [post(qid)], "count": 3 if self.bad_count else 2, "next": None}


def test_two_complete_multipage_passes_record_consistent_inventory(tmp_path):
    cfg = registered(tmp_path)
    M.collect(Paged(), tmp_path, cfg, 0, set(), now=lambda: NOW)
    snapshots = read_complete(tmp_path / "snapshots.jsonl")
    assert len(snapshots) == 2
    assert all(s["inventory_consistent"] and s["pages"] == 4 for s in snapshots)
    assert all(s["server_atomic_snapshot"] is False for s in snapshots)
    assert M.report(tmp_path)["observed_cohort_all"] == 2


@pytest.mark.parametrize('kwargs', [{'shift': True}, {'bad_count': True}])
def test_shift_or_count_mismatch_cannot_publish_complete_snapshot(tmp_path, kwargs):
    cfg = registered(tmp_path)
    with pytest.raises(ValueError):
        M.collect(Paged(**kwargs), tmp_path, cfg, 0, set(), now=lambda: NOW)
    assert not (tmp_path / "snapshots.jsonl").exists()


def test_page_bound_and_discovery_alias_are_fail_closed(tmp_path):
    cfg = policy(); cfg["max_pages"] = 1; registered(tmp_path, cfg)
    with pytest.raises(ValueError, match="pagination incomplete"):
        M.collect(Paged(), tmp_path, cfg, 0, set(), now=lambda: NOW)
    client = Fake(); client.index[0]["slug"] = "old-round"
    with pytest.raises(ValueError, match="discovery"):
        M.discover(client, cfg, set())


def test_production_observer_uses_shared_paced_guard(tmp_path):
    from api_backoff import DEFAULT_PATH
    client = M.JournalClient("synthetic", tmp_path, policy())
    assert client._backoff.path == DEFAULT_PATH and client._backoff.pace
    assert client._max_retries == 0


def test_future_slots_are_not_reported_as_missed_and_start_is_not_liveness(tmp_path):
    registered(tmp_path)
    result = M.report(tmp_path, now=lambda: NOW - timedelta(seconds=1))
    assert result["future_poll_slots"] == 4 and result["missing_or_failed_polls"] == 0
    append_record(tmp_path / "lifecycle.jsonl", {"type": "started", "at": at(0)})
    result = M.report(tmp_path, now=lambda: NOW)
    assert not result["process_liveness_verified"]


def test_truncated_snapshot_ledger_cannot_be_reported_as_complete(tmp_path):
    registered(tmp_path)
    (tmp_path / 'snapshots.jsonl').write_text('{}')
    with pytest.raises(ValueError, match='unterminated'):
        M.report(tmp_path)
