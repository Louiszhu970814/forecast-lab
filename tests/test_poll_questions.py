from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

import poll_questions as pq
from flab.jsonl import read_records
from metaculus import MetaculusClient, MetaculusError

TOKEN = "test-token-not-real-0123456789"


def _post(pid: int, qid: int, qtype: str, title: str, **q: Any) -> dict[str, Any]:
    question = {"id": qid, "type": qtype, "status": "open", "title": title, "description": "background",
                "resolution_criteria": "resolves yes if ...", "fine_print": "fine print",
                "open_time": "2026-10-31T00:00:00Z", "scheduled_close_time": "2026-10-31T03:00:00Z",
                "scheduled_resolve_time": "2026-12-01T00:00:00Z",
                "aggregations": {"recency_weighted": {"latest": {"centers": [0.42]}}},
                "cp_reveal_time": "2026-10-31T03:00:00Z", **q}
    return {"id": pid, "title": title, "question": question, "nr_forecasters": 12,
            "projects": {"tournament": [{"id": 32977}], "default_project": {"id": 32977}}}


POSTS = [
    _post(1, 11, "binary", "Will X happen?"),
    _post(2, 12, "multiple_choice", "Which option?", options=["A", "B", "C"]),
    _post(3, 13, "numeric", "How many?"),
    {"id": 4, "title": "Group post", "question": None, "group_of_questions": {"questions": []}},
]


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *a: object) -> None:
        self.close()


def _opener(pages: list[dict[str, Any]], seen: list[Any]):
    def opener(req, timeout):
        seen.append(req)
        return FakeResponse(json.dumps(pages[len(seen) - 1]).encode())
    return opener


def test_client_sends_token_and_paginates_on_same_host() -> None:
    seen: list[Any] = []
    pages = [{"results": POSTS[:2], "next": "https://www.metaculus.com/api/posts/?offset=2"},
             {"results": POSTS[2:], "next": None}]
    client = MetaculusClient(TOKEN, opener=_opener(pages, seen))
    assert [p["id"] for p in client.list_posts(32977)] == [1, 2, 3, 4]
    assert seen[0].get_header("Authorization") == f"Token {TOKEN}"
    assert seen[0].get_method() == "GET"
    assert TOKEN not in repr(client)


def test_client_refuses_foreign_next_url() -> None:
    pages = [{"results": POSTS[:1], "next": "https://evil.example/api/posts/"}]
    client = MetaculusClient(TOKEN, opener=_opener(pages, []))
    with pytest.raises(MetaculusError):
        list(client.list_posts(32977))


def test_client_retries_then_raises_without_token_in_message() -> None:
    calls: list[int] = []

    def opener(req, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", None, None)

    client = MetaculusClient(TOKEN, opener=opener, sleep=lambda s: None, max_retries=2)
    with pytest.raises(MetaculusError) as exc:
        client.get_post(1)
    assert len(calls) == 3
    assert TOKEN not in str(exc.value)


def test_snapshot_whitelists_fields_and_drops_aggregates() -> None:
    snap = pq.snapshot_from_post(POSTS[1], "bot-testing-area")
    assert snap is not None
    text = json.dumps(snap)
    assert "aggregations" not in text and "0.42" not in text and "cp_reveal_time" not in text
    assert "nr_forecasters" not in text
    assert snap["post_id"] == 2 and snap["question_id"] == 12
    assert snap["options"] == ["A", "B", "C"] and snap["supported"] is True
    assert pq.snapshot_from_post(POSTS[2], "v")["supported"] is False  # numeric waits for M5
    assert pq.snapshot_from_post(POSTS[3], "v") is None


def test_record_snapshots_new_unchanged_and_revised(tmp_path: Path) -> None:
    log = tmp_path / "questions.jsonl"
    snaps = [pq.snapshot_from_post(p, "v") for p in POSTS[:2]]
    assert pq.record_snapshots(snaps, log=log, now="2026-10-31T00:00:00.000Z")["new"] == [11, 12]
    assert pq.record_snapshots(snaps, log=log)["unchanged"] == [11, 12]
    revised = pq.snapshot_from_post(_post(1, 11, "binary", "Will X happen?", fine_print="changed"), "v")
    later = pq.snapshot_from_post(_post(2, 12, "multiple_choice", "Which option?", options=["A", "B", "C"],
                                        scheduled_close_time="2026-10-31T02:00:00Z"), "v")
    out = pq.record_snapshots([revised, later], log=log)
    assert out["revised"] == [11, 12]
    recs = [r for r in read_records(log) if r["question_id"] == 11]
    assert recs[1]["revision_of"] == recs[0]["snapshot_id"]
    assert recs[1]["t0"] == recs[0]["t0"] == "2026-10-31T00:00:00.000Z"
    assert recs[1]["revision"] == {"content_changed": True, "close_time_changed": False,
                                   "type_changed": False, "options_changed": False}


def test_synced_copy_exclusion() -> None:
    snap = pq.snapshot_from_post(POSTS[0], "main-site")
    assert pq.is_synced_copy(snap, pq.TournamentIndex(question_ids={11})).startswith("same question or post ID")
    assert pq.is_synced_copy(snap, pq.TournamentIndex(post_ids={1})).startswith("same question or post ID")
    assert pq.is_synced_copy(snap, pq.TournamentIndex(project_ids={32977})) == "post belongs to a whitelisted tournament"
    idx = pq.TournamentIndex(project_ids={33121}, title_keys={pq.title_key("  WILL x   happen? ")})
    assert pq.is_synced_copy(snap, idx).startswith("title matches")
    assert pq.is_synced_copy(snap, pq.TournamentIndex(project_ids={33121})) is None


def test_tournament_index_from_metadata_poll(tmp_path: Path) -> None:
    client = MetaculusClient(TOKEN, opener=_opener([{"results": POSTS, "next": None}], []))
    rec = pq.poll(client, "33121", metadata_only=True, questions_log=tmp_path / "q.jsonl", poll_log=tmp_path / "p.jsonl")
    idx = pq.TournamentIndex.from_polls([rec])
    assert idx.question_ids == {11, 12, 13} and idx.post_ids == {1, 2, 3} and idx.project_ids == {33121}
    assert rec["statuses"] == ["open", "upcoming"]
    snap = pq.snapshot_from_post(POSTS[1], "main-site")
    assert pq.is_synced_copy(snap, idx) is not None


def test_poll_writes_snapshots_and_timing(tmp_path: Path) -> None:
    client = MetaculusClient(TOKEN, opener=_opener([{"results": POSTS, "next": None}], []))
    rec = pq.poll(client, "bot-testing-area", questions_log=tmp_path / "q.jsonl", poll_log=tmp_path / "p.jsonl",
                  tournaments={"tournaments": {"t": {"slug": "bot-testing-area", "id": 32977}}})
    assert rec["n_questions"] == 3 and rec["new"] == [11, 12, 13] and rec["tournament_id"] == 32977
    assert all(r["tournament_id"] == 32977 for r in read_records(tmp_path / "q.jsonl"))
    assert rec["skipped"] == [{"post_id": 4, "reason": "not a single-question post"}]
    assert {t["question_id"] for t in rec["timing"]} == {11, 12, 13}


def test_poll_metadata_only_writes_no_snapshots(tmp_path: Path) -> None:
    client = MetaculusClient(TOKEN, opener=_opener([{"results": POSTS, "next": None}], []))
    rec = pq.poll(client, "33121", metadata_only=True,
                  questions_log=tmp_path / "q.jsonl", poll_log=tmp_path / "p.jsonl")
    assert not (tmp_path / "q.jsonl").exists()
    assert rec["metadata_only"] is True and rec["n_questions"] == 3


def test_resolve_venue_requires_numeric_id() -> None:
    cfg = {"tournaments": {"main": {"slug": "fall-futureeval-2026", "id": 33121},
                           "minibench": {"slug": "minibench", "id": None}}}
    assert pq.resolve_venue("fall-futureeval-2026", cfg) == 33121
    assert pq.resolve_venue("32977", cfg) == 32977
    with pytest.raises(ValueError):
        pq.resolve_venue("minibench", cfg)


def test_list_posts_rejects_slug() -> None:
    client = MetaculusClient(TOKEN, opener=_opener([], []))
    with pytest.raises(ValueError):
        list(client.list_posts("bot-testing-area"))  # type: ignore[arg-type]


def test_retry_after_and_max_pages() -> None:
    sleeps: list[float] = []
    calls: list[int] = []

    def opener(req, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {"Retry-After": "7"}, None)
        return FakeResponse(json.dumps({"results": POSTS[:1], "next": "https://www.metaculus.com/api/posts/?offset=1"}).encode())

    client = MetaculusClient(TOKEN, opener=opener, sleep=sleeps.append)
    with pytest.raises(MetaculusError):
        list(client.list_posts(32977, max_pages=3))
    assert sleeps == [7.0] and len(calls) == 4


def test_revision_flags_type_and_options(tmp_path: Path) -> None:
    log = tmp_path / "q.jsonl"
    pq.record_snapshots([pq.snapshot_from_post(POSTS[1], "v")], log=log)
    changed = pq.snapshot_from_post(_post(2, 12, "multiple_choice", "Which option?", options=["A", "B", "D"]), "v")
    pq.record_snapshots([changed], log=log)
    last = list(read_records(log))[-1]
    assert last["revision"]["options_changed"] is True and last["revision"]["type_changed"] is False


def test_client_stops_at_empty_page_even_if_next_is_set() -> None:
    pages = [{"results": POSTS[:1], "next": "https://www.metaculus.com/api/posts/?offset=50"},
             {"results": [], "next": "https://www.metaculus.com/api/posts/?offset=100"}]
    seen: list[Any] = []
    client = MetaculusClient(TOKEN, opener=_opener(pages, seen))
    assert [p["id"] for p in client.list_posts(32977)] == [1]
    assert len(seen) == 2
