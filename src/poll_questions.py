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
from urllib.parse import urlsplit, unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flab.config import load_tournaments  # noqa: E402
from flab.envfile import get_secret  # noqa: E402
from flab.ids import hash_obj, new_id, sha256_hex  # noqa: E402
from flab.jsonl import append_record, read_records  # noqa: E402
from flab.timeutil import to_iso, utc_now, parse_iso  # noqa: E402
from flab.workdir import PROJECT_ROOT  # noqa: E402
from metaculus import API_BASE, MetaculusClient  # noqa: E402
from project_policy import trial_membership_error, VERSION as PROJECT_POLICY_VERSION  # noqa: E402
import numeric as N
from question_types import is_supported, supported_types
from question_mapping import question_ids, select_question

QUESTIONS_LOG = PROJECT_ROOT / "data" / "questions.jsonl"
POLL_LOG = PROJECT_ROOT / "data" / "poll_log.jsonl"
class _SupportedTypes:
    """Compatibility view whose membership and iteration reread the rollout file."""
    def __contains__(self, kind):
        return is_supported(kind)

    def __iter__(self):
        return iter(supported_types())


SUPPORTED_TYPES = _SupportedTypes()
CONTENT_FIELDS = ("type", "title", "description", "resolution_criteria", "fine_print", "options")
TIME_FIELDS = ("open_time", "scheduled_close_time", "scheduled_resolve_time")
METADATA_VERSION = "exclusion_metadata_v2"


def main_site_pages(client, *, max_pages=20, validate_next_at_limit=False):
    """Bound main-feed discovery; optionally validate even the unfollowed final URL.

    The default preserves M2's existing pagination behavior. Continuous selection
    additionally rejects unsafe links at the limit without issuing another GET.
    """
    if type(max_pages) is not int or max_pages < 1:
        raise ValueError("positive main-site page limit required")
    page = client.get("posts/", {"main_feed": "true", "statuses": "open", "forecast_type": ",".join(supported_types()),
                                 "limit": 50, "offset": 0, "include_description": "true"})
    visited = set()
    for _ in range(max_pages):
        if not isinstance(page, dict) or not isinstance(page.get("results"), list):
            raise SystemExit("Unrecognized main-site page; no campaign file written")
        yield page["results"]
        nxt = page.get("next")
        if not page["results"] or not nxt:
            return
        if not isinstance(nxt, str) or nxt in visited:
            raise SystemExit("Invalid main-site pagination; no campaign file written")
        visited.add(nxt)
        if len(visited) < max_pages:
            page = client._get_url(nxt)
        elif validate_next_at_limit:
            try:
                parts = urlsplit(nxt)
                base = urlsplit(getattr(client, "_base", API_BASE))
                if parts.scheme != "https" or parts.netloc != base.netloc or parts.fragment \
                        or not parts.path.startswith(base.path.rstrip("/") + "/") \
                        or ".." in unquote(parts.path).split("/"):
                    raise ValueError("unsafe next URL")
            except ValueError:
                raise SystemExit("Invalid main-site pagination; no campaign file written") from None


def safe_remote_id(value):
    return value if type(value) is int and value > 0 else None


def _project_ids(post: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for value in (post.get("projects") or {}).values():
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict) and isinstance(item.get("id"), int):
                ids.add(item["id"])
    return ids


def _scaling_error_value(value):
    """Keep malformed numeric evidence JSON-safe so rejection can be recorded."""
    if isinstance(value, float) and not N.finite(value):
        return {"nonfinite_float": repr(value)}
    if isinstance(value, list):
        return [_scaling_error_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _scaling_error_value(v) for k, v in value.items()}
    return value


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
    scaling_error = None
    if content["type"] in N.TYPES:
        try:
            content.update(N.read_scaling_fields(q))
            N.scaling({**content, "question_type": content["type"]})
        except N.UnsupportedScaling as exc:
            scaling_error = str(exc)
            # Retain conflicting allowlisted values for revision detection without
            # trusting one of the conflicting API locations.
            content["scaling_error"] = scaling_error
            content["scaling_conflicts"] = {
                k: [q.get(k), (q.get("scaling") or {}).get(k)]
                for k in N.SCALING_FIELDS
            } if isinstance(q.get("scaling"), dict) else None
            for key in (*N.SCALING_FIELDS, "scaling_conflicts"):
                if key in content:
                    content[key] = _scaling_error_value(content[key])
    times = {k: q.get(k) or post.get(k) for k in TIME_FIELDS}
    return {
        "type": "question_snapshot",
        "venue": venue,
        "post_id": post["id"],
        "question_id": q["id"],
        **post.get("_group_mapping", {}),
        "project_ids": sorted(_project_ids(post)),
        "status": q.get("status"),
        "supported": content["type"] in SUPPORTED_TYPES,
        "question_type": content["type"],
        **{k: v for k, v in content.items() if k != "type"},
        **times,
        "content_hash": hash_obj(content),
        **({"scaling_status": "unsupported_scaling" if scaling_error else "valid"} if content["type"] in N.TYPES else {}),
    }


def snapshots_from_post(post, venue, *, allow_groups=False):
    if post.get('group_of_questions') is not None and allow_groups:
        return [snapshot_from_post(select_question(post, qid, allow_groups=True), venue)
                for qid in question_ids(post, allow_groups=True)]
    snap = snapshot_from_post(post, venue)
    return [snap] if snap else []


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
            if any(s.get("reason") != "notebook (no forecast question)" for s in rec.get("skipped", [])):
                raise ValueError("incomplete exclusion metadata")
            if isinstance(rec.get("tournament_id"), int):
                idx.project_ids.add(rec["tournament_id"])
            for t in rec.get("timing") or []:
                idx.question_ids.add(t["question_id"])
                if isinstance(t.get("post_id"), int):
                    idx.post_ids.add(t["post_id"])
                if t.get("title_key"):
                    idx.title_keys.add(t["title_key"])
                if t.get("post_title_key"):
                    idx.title_keys.add(t["post_title_key"])
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
                or any(prev.get(k) != snap.get(k) for k in ("post_id", "tournament_id", "group_id")):
            # Type, options or close-time changes block a fallback submission; text-only edits do not (D53).
            rec = {**snap, "snapshot_id": new_id("qs"), "seen_at": now, "t0": prev["t0"],
                   "revision_of": prev["snapshot_id"],
                   "revision": {"content_changed": prev["content_hash"] != snap["content_hash"],
                                "close_time_changed": prev.get("scheduled_close_time") != snap.get("scheduled_close_time"),
                                "type_changed": prev.get("question_type") != snap.get("question_type"),
                                "options_changed": prev.get("options") != snap.get("options"),
                                "mapping_changed": any(prev.get(k) != snap.get(k) for k in ("post_id", "tournament_id", "group_id"))}}
            outcome["revised"].append(snap["question_id"])
            if snap.get("question_type") in N.TYPES or prev.get("question_type") in N.TYPES:
                rec["revision"]["scaling_changed"] = N.scaling_changed(prev, snap)
        else:
            outcome["unchanged"].append(snap["question_id"])
            continue
        append_record(log, rec)
        latest[snap["question_id"]] = rec
    return outcome


def exclusion_metadata(post: dict) -> list[dict]:
    """Index all group/conditional children without retaining their content.

    A known notebook is not a forecast. Every other unrecognized structure fails
    closed instead of silently claiming complete exclusion coverage.
    """
    valid_id = lambda value: type(value) is int and value > 0
    if not isinstance(post, dict) or not valid_id(post.get("id")):
        raise ValueError("invalid exclusion post identity")
    shapes = [k for k in ("question", "group_of_questions", "conditional", "notebook") if post.get(k) is not None]
    if len(shapes) != 1 or not isinstance(post[shapes[0]], dict):
        raise ValueError("unrecognized exclusion post structure")
    shape = shapes[0]
    if shape == "notebook":
        if not valid_id(post[shape].get("id")):
            raise ValueError("unrecognized exclusion notebook")
        return []
    if not isinstance(post.get("title"), str) or not post["title"].strip():
        raise ValueError("exclusion post title unavailable")
    if shape == "question":
        questions = [post[shape]]
    elif shape == "group_of_questions":
        questions = post[shape].get("questions")
    else:
        questions = [post[shape].get(k) for k in ("condition", "condition_child", "question_yes", "question_no")]
    if not isinstance(questions, list) or not questions:
        raise ValueError("exclusion children unavailable")
    timing = []
    for q in questions:
        if not isinstance(q, dict) or not valid_id(q.get("id")):
            raise ValueError("invalid exclusion child identity")
        title = (q.get("title") or post["title"]) if shape == "question" else q.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("exclusion child title unavailable")
        times = {}
        for k in TIME_FIELDS:
            value = q.get(k) or post.get(k)
            try:
                times[k] = to_iso(parse_iso(value)) if value is not None else None
            except (TypeError, ValueError):
                raise ValueError("invalid exclusion timing") from None
        timing.append({"question_id": q["id"], "post_id": post["id"],
            "question_type": q.get("type") if q.get("type") in ("binary", "multiple_choice", "numeric", "discrete", "date") else "unknown",
            "status": q.get("status") if q.get("status") in ("open", "upcoming", "closed", "resolved", "pending") else "unknown",
            "title_key": title_key(title), "post_title_key": title_key(post["title"]), "post_type": shape, **times})
    return timing


def poll(client: MetaculusClient, venue: str, *, metadata_only: bool = False,
         questions_log: Path = QUESTIONS_LOG, poll_log: Path = POLL_LOG,
         exclude: TournamentIndex | None = None, tournaments: dict[str, Any] | None = None,
         seen_question_ids: set[int] | None = None, trial_bot_project_id: int | None = None,
         statuses: list[str] | None = None, allow_groups: bool = False) -> dict[str, Any]:
    tournament_id = resolve_venue(venue, tournaments)
    polled_at = to_iso(utc_now())
    # Tournament metadata also covers upcoming questions so trial venues can exclude their copies (D53).
    statuses = statuses or (["open", "upcoming"] if metadata_only else ["open"])
    snaps, skipped, timing = [], [], []
    listing = {"statuses": statuses}
    if metadata_only or allow_groups:
        listing["forecast_type"] = None  # Omit the parameter; include every post structure.
    for post in client.list_posts(tournament_id, **listing):
        if not isinstance(post, dict):
            raise ValueError("unrecognized polled post")
        if metadata_only:
            entries = exclusion_metadata(post)
            timing.extend(entries)
            if not entries:
                skipped.append({"post_id": post["id"], "reason": "notebook (no forecast question)"})
            continue
        membership_error = trial_membership_error(post, trial_bot_project_id) if trial_bot_project_id is not None else None
        if membership_error:
            question = post.get("question")
            skipped.append({"post_id": safe_remote_id(post.get("id")),
                            "question_id": safe_remote_id(question.get("id")) if isinstance(question, dict) else None,
                            "reason": membership_error, "retryable": True})
            continue
        candidates = snapshots_from_post(post, venue, allow_groups=allow_groups)
        if not candidates:
            skipped.append({"post_id": safe_remote_id(post.get("id")), "reason": "not a single-question post"})
            continue
        for snap in candidates:
            snap["tournament_id"] = tournament_id
            timing.append({"question_id": snap["question_id"], "post_id": snap["post_id"],
                           "question_type": snap["question_type"], "status": snap["status"],
                           "title_key": title_key(snap.get("title")), **{k: snap.get(k) for k in TIME_FIELDS}})
            reason = is_synced_copy(snap, exclude) if exclude else None
            if seen_question_ids is not None and snap["question_id"] in seen_question_ids:
                reason = reason or "already polled through another allowed venue"
            if reason:
                skipped.append({"post_id": snap["post_id"], "question_id": snap["question_id"], "reason": reason})
                continue
            snaps.append(snap)
    outcome = {"new": [], "revised": [], "unchanged": []} if metadata_only else record_snapshots(snaps, log=questions_log, now=polled_at)
    rec = {"type": "poll", "record_id": new_id("poll"), "polled_at": polled_at, "venue": venue,
           "tournament_id": tournament_id, "metadata_only": metadata_only, "statuses": statuses,
           "n_questions": len(timing), "timing": timing, "skipped": skipped, **outcome}
    if metadata_only:
        rec["metadata_version"] = METADATA_VERSION
    if trial_bot_project_id is not None:
        rec["project_policy_version"] = PROJECT_POLICY_VERSION
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
