"""Poll whitelisted venues and record question snapshots (PLAN §3.1, §3.2, D46, D52, D53).

Only whitelisted fields are stored; community prediction and other aggregates are dropped
before anything is written. A question gets a new snapshot when it is first seen and again
whenever its content hash or close time changes.

Usage:
    python src/poll_questions.py --venue bot-testing-area
    python src/poll_questions.py --venue fall-futureeval-2026 --metadata-only
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flab.config import load_tournaments  # noqa: E402
from flab.envfile import get_secret  # noqa: E402
from flab.ids import hash_obj, new_id, sha256_hex  # noqa: E402
from flab.jsonl import append_record, read_records  # noqa: E402
from flab.timeutil import to_iso, utc_now  # noqa: E402
from flab.workdir import PROJECT_ROOT  # noqa: E402
from metaculus import MetaculusClient  # noqa: E402

QUESTIONS_LOG = PROJECT_ROOT / "data" / "questions.jsonl"
POLL_LOG = PROJECT_ROOT / "data" / "poll_log.jsonl"
SUPPORTED_TYPES = ("binary", "multiple_choice")       # numeric and discrete come in M5 (D39, D49)
CONTENT_FIELDS = ("type", "title", "description", "resolution_criteria", "fine_print", "options")
TIME_FIELDS = ("open_time", "scheduled_close_time", "scheduled_resolve_time")


def _project_ids(post: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for value in (post.get("projects") or {}).values():
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict) and isinstance(item.get("id"), int):
                ids.add(item["id"])
    return ids


def snapshot_from_post(post: dict[str, Any], venue: str) -> dict[str, Any] | None:
    """Whitelisted snapshot of a single-question post; None for group or conditional posts."""
    q = post.get("question")
    if not isinstance(q, dict) or not isinstance(post.get("id"), int) or not isinstance(q.get("id"), int):
        return None
    options = q.get("options")
    content = {
        "type": q.get("type"),
        "title": post.get("title") or q.get("title"),
        "description": q.get("description"),
        "resolution_criteria": q.get("resolution_criteria"),
        "fine_print": q.get("fine_print"),
        "options": list(options) if isinstance(options, list) else None,
    }
    times = {k: q.get(k) or post.get(k) for k in TIME_FIELDS}
    return {
        "type": "question_snapshot",
        "venue": venue,
        "post_id": post["id"],
        "question_id": q["id"],
        "project_ids": sorted(_project_ids(post)),
        "status": q.get("status"),
        "supported": content["type"] in SUPPORTED_TYPES,
        "question_type": content["type"],
        **{k: v for k, v in content.items() if k != "type"},
        **times,
        "content_hash": hash_obj(content),
    }


def latest_snapshots(log: Path = QUESTIONS_LOG) -> dict[int, dict[str, Any]]:
    latest: dict[int, dict[str, Any]] = {}
    if log.exists():
        for rec in read_records(log):
            if rec.get("type") == "question_snapshot":
                latest[rec["question_id"]] = rec
    return latest


def title_key(title: str | None) -> str | None:
    """Normalised title hash used to match suspected copies without storing titles twice."""
    return sha256_hex(" ".join(title.lower().split())) if title else None


@dataclass
class TournamentIndex:
    """Open and upcoming tournament questions a trial venue must not overlap with (D53)."""
    project_ids: set[int] = field(default_factory=set)
    question_ids: set[int] = field(default_factory=set)
    post_ids: set[int] = field(default_factory=set)
    title_keys: set[str] = field(default_factory=set)

    @classmethod
    def from_polls(cls, polls: Iterable[dict[str, Any]]) -> "TournamentIndex":
        idx = cls()
        for rec in polls:
            if isinstance(rec.get("tournament_id"), int):
                idx.project_ids.add(rec["tournament_id"])
            for t in rec.get("timing") or []:
                idx.question_ids.add(t["question_id"])
                if isinstance(t.get("post_id"), int):
                    idx.post_ids.add(t["post_id"])
                if t.get("title_key"):
                    idx.title_keys.add(t["title_key"])
        return idx


def is_synced_copy(snap: dict[str, Any], index: TournamentIndex) -> str | None:
    """Reason a trial-venue question must be excluded as a copy of a tournament question (D53), else None."""
    if snap["question_id"] in index.question_ids or snap["post_id"] in index.post_ids:
        return "same question or post ID as a tournament question"
    if index.project_ids & set(snap.get("project_ids") or []):
        return "post belongs to a whitelisted tournament"
    if title_key(snap.get("title")) in index.title_keys:
        return "title matches a tournament question (suspected copy)"
    return None


def record_snapshots(snaps: Iterable[dict[str, Any]], *, log: Path = QUESTIONS_LOG,
                     now: str | None = None) -> dict[str, list[int]]:
    """Append new and revised snapshots; return question IDs by outcome."""
    now = now or to_iso(utc_now())
    latest = latest_snapshots(log)
    outcome: dict[str, list[int]] = {"new": [], "revised": [], "unchanged": []}
    for snap in snaps:
        prev = latest.get(snap["question_id"])
        if prev is None:
            rec = {**snap, "snapshot_id": new_id("qs"), "seen_at": now, "t0": now, "revision_of": None}
            outcome["new"].append(snap["question_id"])
        elif prev["content_hash"] != snap["content_hash"] or prev.get("scheduled_close_time") != snap.get("scheduled_close_time") \
                or any(prev.get(k) != snap.get(k) for k in ("post_id", "tournament_id")):
            # Type, options or close-time changes block a fallback submission; text-only edits do not (D53).
            rec = {**snap, "snapshot_id": new_id("qs"), "seen_at": now, "t0": prev["t0"],
                   "revision_of": prev["snapshot_id"],
                   "revision": {"content_changed": prev["content_hash"] != snap["content_hash"],
                                "close_time_changed": prev.get("scheduled_close_time") != snap.get("scheduled_close_time"),
                                "type_changed": prev.get("question_type") != snap.get("question_type"),
                                "options_changed": prev.get("options") != snap.get("options"),
                                "mapping_changed": any(prev.get(k) != snap.get(k) for k in ("post_id", "tournament_id"))}}
            outcome["revised"].append(snap["question_id"])
        else:
            outcome["unchanged"].append(snap["question_id"])
            continue
        append_record(log, rec)
        latest[snap["question_id"]] = rec
    return outcome


def poll(client: MetaculusClient, venue: str, *, metadata_only: bool = False,
         questions_log: Path = QUESTIONS_LOG, poll_log: Path = POLL_LOG,
         exclude: TournamentIndex | None = None, tournaments: dict[str, Any] | None = None) -> dict[str, Any]:
    tournament_id = resolve_venue(venue, tournaments)
    polled_at = to_iso(utc_now())
    # Tournament metadata also covers upcoming questions so trial venues can exclude their copies (D53).
    statuses = ["open", "upcoming"] if metadata_only else ["open"]
    snaps, skipped, timing = [], [], []
    for post in client.list_posts(tournament_id, statuses=statuses):
        snap = snapshot_from_post(post, venue)
        if snap is None:
            skipped.append({"post_id": post.get("id"), "reason": "not a single-question post"})
            continue
        snap["tournament_id"] = tournament_id
        timing.append({"question_id": snap["question_id"], "post_id": snap["post_id"],
                       "question_type": snap["question_type"], "status": snap["status"],
                       "title_key": title_key(snap.get("title")), **{k: snap.get(k) for k in TIME_FIELDS}})
        reason = is_synced_copy(snap, exclude) if exclude else None
        if reason:
            skipped.append({"post_id": snap["post_id"], "question_id": snap["question_id"], "reason": reason})
            continue
        snaps.append(snap)
    outcome = {"new": [], "revised": [], "unchanged": []} if metadata_only else record_snapshots(snaps, log=questions_log, now=polled_at)
    rec = {"type": "poll", "record_id": new_id("poll"), "polled_at": polled_at, "venue": venue,
           "tournament_id": tournament_id, "metadata_only": metadata_only, "statuses": statuses,
           "n_questions": len(timing), "timing": timing, "skipped": skipped, **outcome}
    append_record(poll_log, rec)
    return rec


def resolve_venue(venue: str | int, tournaments: dict[str, Any] | None = None) -> int:
    """Numeric project ID for a venue slug or ID; the API ignores unknown slugs and returns every post."""
    if isinstance(venue, int) and not isinstance(venue, bool):
        return venue
    if isinstance(venue, str) and venue.isdigit():
        return int(venue)
    cfg = tournaments if tournaments is not None else load_tournaments()
    for entry in (cfg.get("tournaments") or {}).values():
        if entry.get("slug") == venue and isinstance(entry.get("id"), int):
            return entry["id"]
    raise ValueError(f"unknown venue {venue!r}: add its numeric ID to config/tournaments.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venue", required=True, help="tournament slug or ID, e.g. bot-testing-area")
    ap.add_argument("--metadata-only", action="store_true", help="record timing only (tournaments during trial, D45)")
    args = ap.parse_args(argv)
    token = get_secret("METACULUS_TOKEN", PROJECT_ROOT / ".env")
    try:
        rec = poll(MetaculusClient(token), args.venue, metadata_only=args.metadata_only)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(f"{args.venue}: {rec['n_questions']} questions, new={len(rec['new'])} revised={len(rec['revised'])} "
          f"skipped={len(rec['skipped'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
