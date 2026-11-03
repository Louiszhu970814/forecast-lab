"""Bounded GET-only V11 observation; no forecasts, models or automatic M3 approval.

Register the scope before collecting data. All writes are append-only metadata in
an independent directory. A started run cannot resume or extend its frozen window.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import math
import sys
import time
from datetime import timedelta
from pathlib import Path

from flab.envfile import get_secret
from flab.ids import hash_obj, sha256_hex
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso, utc_now
from flab.workdir import PROJECT_ROOT
from metaculus import MetaculusClient, MetaculusError, _default_opener
from poll_questions import CONTENT_FIELDS, exclusion_metadata, _project_ids

VERSION = "metadata_observer_v1"
STATUSES = ["open", "upcoming", "closed", "resolved"]


class ObservationStop(RuntimeError):
    pass


def implementation():
    source = Path(__file__).resolve().parent
    return {"python": sha256_hex(Path(sys.executable).read_bytes()), **{name: sha256_hex((source / name).read_bytes()) for name in (
        "metadata_observer.py", "metaculus.py", "api_backoff.py", "poll_questions.py",
        "flab/jsonl.py", "flab/ledger.py", "flab/timeutil.py", "flab/ids.py", "flab/envfile.py", "flab/workdir.py")}}


def validate(policy):
    if set(policy) != {"version", "observation_id", "start", "cohort_end", "end", "main_id",
                       "interval_s", "poll_budget_s", "max_requests", "max_requests_per_poll",
                       "max_pages", "max_projects", "listing_order", "inventory_passes", "replay_recipe"} or policy["version"] != VERSION:
        raise ValueError("invalid observation policy")
    if not isinstance(policy["observation_id"], str) or not policy["observation_id"]:
        raise ValueError("observation ID required")
    start, cohort_end, end = (parse_iso(policy[k]) for k in ("start", "cohort_end", "end"))
    if not start < cohort_end < end or (end - start).total_seconds() > 172800:
        raise ValueError("invalid bounded window")
    for field, maximum in (("main_id", 10**9), ("interval_s", 3600), ("poll_budget_s", 480),
                           ("max_requests", 10000), ("max_requests_per_poll", 100),
                           ("max_pages", 30), ("max_projects", 16)):
        if type(policy[field]) is not int or not 1 <= policy[field] <= maximum:
            raise ValueError("invalid bound: " + field)
    if policy["interval_s"] < 60 or policy["poll_budget_s"] >= policy["interval_s"]:
        raise ValueError("poll budget must be shorter than interval")
    if policy["listing_order"] != "published_at" or type(policy["inventory_passes"]) is not int or policy["inventory_passes"] != 2:
        raise ValueError("two complete published_at inventory passes required")
    if not isinstance(policy["replay_recipe"], dict) or not policy["replay_recipe"]:
        raise ValueError("freeze replay assumptions before observation")
    hash_obj(policy)


def register(root, policy, *, now=utc_now):
    validate(policy)
    with transaction(root):
        rows = read_complete(root / "registration.jsonl")
        if rows:
            if len(rows) != 1 or rows[0]["sha256"] != hash_obj(policy) or rows[0]["implementation"] != implementation():
                raise ValueError("observation already frozen")
            return rows[0]
        if parse_iso(policy["start"]) <= now():
            raise ValueError("register before start")
        record = {"type": "observation_registered", "registered_at": to_iso(now()),
                  "policy": copy.deepcopy(policy), "sha256": hash_obj(policy), "implementation": implementation()}
        append_record(root / "registration.jsonl", record)
        return record


def load(root, *, verify_code=False):
    rows = read_complete(root / "registration.jsonl")
    if len(rows) != 1 or hash_obj(rows[0]["policy"]) != rows[0]["sha256"]:
        raise ValueError("missing or changed registration")
    validate(rows[0]["policy"])
    if verify_code and rows[0]["implementation"] != implementation():
        raise ValueError("registered implementation changed")
    return rows[0]["policy"]


def metadata(post):
    """Retain IDs/timing and content hashes, never text/options/probabilities."""
    rows = exclusion_metadata(post)
    if not rows:
        return []
    shape = rows[0]["post_type"]
    if shape == "question":
        questions = [post[shape]]
    elif shape == "group_of_questions":
        questions = post[shape]["questions"]
    else:
        questions = [post[shape][k] for k in ("condition", "condition_child", "question_yes", "question_no")]
    by_id = {q["id"]: q for q in questions}
    for row in rows:
        q = by_id[row["question_id"]]
        row.pop("title_key", None)
        row.pop("post_title_key", None)
        content = {k: q.get(k) for k in CONTENT_FIELDS}
        content["title"] = q.get("title") or post.get("title")
        if shape != "question":
            content["parent"] = {k: post[shape].get(k) for k in CONTENT_FIELDS if k != "options"}
        row["content_fields_present"] = sorted(k for k in CONTENT_FIELDS if q.get(k) is not None)
        row["content_sha256"] = hash_obj(content)
        for key in ("actual_close_time", "cp_reveal_time", "spot_scoring_time"):
            value = q.get(key)
            row[key] = to_iso(parse_iso(value)) if value is not None else None
        row["structure_sha256"] = hash_obj({"type": q.get("type"), "options": q.get("options"),
            "post_id": row["post_id"], "post_type": shape, "scheduled_close_time": row["scheduled_close_time"]})
        row["supported"] = shape == "question" and row["question_type"] in ("binary", "multiple_choice")
    return rows


class JournalClient(MetaculusClient):
    def __init__(self, token, root, policy, *, now=utc_now, **kwargs):
        super().__init__(token, max_retries=0, **kwargs)
        self.root, self.policy, self.now = root, policy, now
        self.total = self.in_poll = 0
        self.deadline = parse_iso(policy["start"])
        self.slot = None
        # The second time check runs after shared pacing/cooldown, immediately
        # before network I/O. No new request may slip beyond the frozen bound.
        self.raw_opener = kwargs.get("opener", _default_opener)
        self._open = lambda req, timeout: self._backoff.open(self._bounded_open, req, timeout)

    def _bounded_open(self, request, timeout):
        remaining = (min(self.deadline, parse_iso(self.policy["end"])) - self.now()).total_seconds()
        if remaining <= 0 or request.method != "GET":
            raise ObservationStop("GET time/method bound reached")
        append_record(self.root / "requests.jsonl", {"type": "network_started", "request_id": self.total,
            "slot": self.slot, "at": to_iso(self.now())})
        return self.raw_opener(request, min(timeout, remaining))

    def _get_url(self, url):
        if self.now() >= min(self.deadline, parse_iso(self.policy["end"])):
            raise ObservationStop("poll time bound reached")
        if self.total >= self.policy["max_requests"] or self.in_poll >= self.policy["max_requests_per_poll"]:
            raise ObservationStop("request bound reached")
        self.total += 1
        self.in_poll += 1
        request_id = self.total
        append_record(self.root / "requests.jsonl", {"type": "get_started", "request_id": request_id,
            "slot": self.slot, "at": to_iso(self.now())})
        try:
            result = super()._get_url(url)
        except Exception as exc:
            append_record(self.root / "requests.jsonl", {"type": "get_finished", "request_id": request_id,
                "slot": self.slot, "at": to_iso(self.now()), "ok": False,
                "error_type": type(exc).__name__, "http_status": getattr(exc, "status", None)})
            raise
        append_record(self.root / "requests.jsonl", {"type": "get_finished", "request_id": request_id,
            "slot": self.slot, "at": to_iso(self.now()), "ok": True})
        return result


def discover(client, policy, tracked):
    rows = client.get("projects/minibenches/")
    if not isinstance(rows, list) or not rows:
        raise ValueError("invalid MiniBench index")
    seen, aliases = set(), 0
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0 \
                or row["id"] in seen or type(row.get("is_ongoing")) is not bool:
            raise ValueError("invalid MiniBench identity")
        seen.add(row["id"])
        aliases += row.get("slug") == "minibench"
        start = parse_iso(row["start_date"]) if row.get("start_date") else None
        if not row["is_ongoing"] and start is None:
            raise ValueError("missing MiniBench start date")
        if row["is_ongoing"] or row.get("slug") == "minibench" or \
                (start is not None and parse_iso(policy["start"]) <= start < parse_iso(policy["end"])):
            tracked.add(row["id"])
    if aliases != 1 or len(tracked) + 1 > policy["max_projects"] or policy["main_id"] in seen:
        raise ValueError("MiniBench discovery incomplete or bound exceeded")
    # Keep polling rounds that end or lose their alias during the observation.
    return [("main", policy["main_id"]), *(("minibench", pid) for pid in sorted(tracked))]


def inventory(client, root, policy, slot, pid, pass_number, *, now=utc_now):
    """One bounded pass; a second matching pass is required before a snapshot."""
    started = now()
    page = client.get("posts/", {"tournaments": pid, "statuses": STATUSES,
        "limit": 100, "offset": 0, "include_description": "true", "order_by": policy["listing_order"]})
    visited, posts_seen, qids, observations, counts = set(), set(), set(), [], []
    for number in range(1, policy["max_pages"] + 1):
        if not isinstance(page, dict) or not isinstance(page.get("results"), list):
            raise ValueError("invalid page")
        count = page.get("count")
        if "count" in page and (type(count) is not int or count < 0):
            raise ValueError("invalid page count")
        counts.append(count)
        for post in page["results"]:
            if not isinstance(post, dict) or post.get("id") in posts_seen:
                raise ValueError("duplicate or invalid post")
            if pid not in _project_ids(post):
                raise ValueError("post project membership mismatch")
            posts_seen.add(post.get("id"))
            for item in metadata(post):
                if item["question_id"] in qids:
                    item["repeated_question_id"] = True
                qids.add(item["question_id"])
                observations.append(item)
        append_record(root / "polls.jsonl", {"type": "page_finished", "slot": slot, "project_id": pid,
            "pass": pass_number, "page": number, "at": to_iso(now()), "post_count": len(page["results"]), "reported_count": count})
        nxt = page.get("next")
        if not nxt or not page["results"]:
            break
        if number == policy["max_pages"] or not isinstance(nxt, str) or nxt in visited:
            raise ValueError("pagination incomplete")
        visited.add(nxt)
        page = client._get_url(nxt)
    if len(set(counts)) != 1 or (counts[0] is not None and counts[0] != len(posts_seen)):
        raise ValueError("pagination count mismatch")
    return {"post_ids": posts_seen, "questions": observations, "pages": number,
            "started_at": to_iso(started), "finished_at": to_iso(now()), "reported_count": counts[0]}


def collect(client, root, policy, slot, tracked, *, now=utc_now):
    started = now()
    client.slot, client.in_poll = slot, 0
    client.deadline = min(started + timedelta(seconds=policy["poll_budget_s"]), parse_iso(policy["end"]))
    append_record(root / "polls.jsonl", {"type": "poll_started", "slot": slot, "at": to_iso(started)})
    try:
        targets = discover(client, policy, tracked)
        append_record(root / "polls.jsonl", {"type": "projects_discovered", "slot": slot, "at": to_iso(now()),
            "targets": [{"venue": v, "project_id": pid} for v, pid in targets]})
        for venue, pid in targets:
            first = inventory(client, root, policy, slot, pid, 1, now=now)
            second = inventory(client, root, policy, slot, pid, 2, now=now)
            mappings = lambda r: {(q["post_id"], q["question_id"], q["post_type"]) for q in r["questions"]}
            if first["post_ids"] != second["post_ids"] or mappings(first) != mappings(second) \
                    or first["reported_count"] != second["reported_count"]:
                raise ValueError("unstable pagination inventory")
            # Retain both observations, but only make them available after the
            # two-pass consistency check. Neither is an atomic server snapshot.
            append_record(root / "snapshots.jsonl", {"type": "project_observed", "slot": slot,
                "venue": venue, "project_id": pid, "started_at": first["started_at"], "finished_at": second["finished_at"],
                "first_pass_finished_at": first["finished_at"], "first_pass_questions": first["questions"],
                "pages": first["pages"] + second["pages"], "post_count": len(second["post_ids"]), "questions": second["questions"],
                "inventory_consistent": True, "server_atomic_snapshot": False})
        append_record(root / "polls.jsonl", {"type": "poll_finished", "slot": slot, "at": to_iso(now()),
            "ok": True, "requests": client.in_poll})
    except Exception as exc:
        append_record(root / "polls.jsonl", {"type": "poll_finished", "slot": slot, "at": to_iso(now()),
            "ok": False, "requests": client.in_poll, "error_type": type(exc).__name__,
            "http_status": getattr(exc, "status", None)})
        raise


def observe(root, client_factory, *, now=utc_now, sleep=time.sleep):
    policy = load(root, verify_code=True)
    with transaction(root):
        if read_complete(root / "lifecycle.jsonl"):
            raise ValueError("started observation cannot be resumed or repeated")
        if now() >= parse_iso(policy["end"]):
            raise ValueError("observation expired")
        append_record(root / "lifecycle.jsonl", {"type": "started", "at": to_iso(now())})
    start, end = parse_iso(policy["start"]), parse_iso(policy["end"])
    total_slots = math.ceil((end - start).total_seconds() / policy["interval_s"])
    tracked, next_slot, status, error = set(), 0, "completed", None
    try:
        client = client_factory(policy)
        while next_slot < total_slots and now() < end:
            due = start + timedelta(seconds=next_slot * policy["interval_s"])
            remaining = (due - now()).total_seconds()
            if remaining > 0:
                sleep(min(30, remaining))
                continue
            current_slot = int((now() - start).total_seconds() // policy["interval_s"])
            # Never run missed polls back-to-back or fabricate observations during sleep.
            while next_slot < min(current_slot, total_slots):
                append_record(root / "polls.jsonl", {"type": "poll_missed", "slot": next_slot,
                    "at": to_iso(now()), "reason": "late_or_asleep"})
                next_slot += 1
            if next_slot >= total_slots:
                break
            collect(client, root, policy, next_slot, tracked, now=now)
            next_slot += 1
        # The final cadence slot ends before the wall-clock bound; the report
        # distinguishes observed horizon from the frozen requested horizon.
    except (Exception, KeyboardInterrupt) as exc:
        status, error = "stopped", type(exc).__name__
    append_record(root / "lifecycle.jsonl", {"type": status, "at": to_iso(now()), "error_type": error})
    return report(root, now=now)


def snapshot_rows(path):
    """Stream large metadata inventories under the writer's shared file lock."""
    if not path.exists():
        return
    with path.open() as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        for line in stream:
            if not line.endswith("\n"):
                raise ValueError("unterminated snapshot ledger")
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("invalid snapshot ledger row")
            yield row


def report(root, *, now=utc_now):
    policy = load(root)
    starts, finishes = parse_iso(policy["start"]), parse_iso(policy["cohort_end"])
    cohorts, unknown_time, hashes, repeated, unknown_type = {}, set(), {}, set(), set()
    path = root / "snapshots.jsonl"
    for snapshot in snapshot_rows(path):
        for q in [*snapshot.get("first_pass_questions", []), *snapshot["questions"]]:
            qid = q["question_id"]
            hashes.setdefault(qid, set()).add(q["content_sha256"])
            if q.get("repeated_question_id"):
                repeated.add(qid)
            if q["question_type"] == "unknown":
                unknown_type.add(qid)
            if not q.get("open_time"):
                unknown_time.add(qid)
            elif starts <= parse_iso(q["open_time"]) < finishes:
                cohort = cohorts.setdefault(qid, {"supported": True, "venues": set()})
                cohort["supported"] &= q["supported"]
                cohort["venues"].add(snapshot["venue"])
    polls = read_complete(root / "polls.jsonl")
    good = [p for p in polls if p["type"] == "poll_finished" and p["ok"]]
    gaps = [(parse_iso(b["at"]) - parse_iso(a["at"])).total_seconds() for a, b in zip(good, good[1:])]
    lifecycle = read_complete(root / "lifecycle.jsonl")
    expected = math.ceil((parse_iso(policy["end"]) - starts).total_seconds() / policy["interval_s"])
    supported = sum(q["supported"] for q in cohorts.values())
    status = lifecycle[-1]["type"] if lifecycle else "registered"
    due = min(expected, max(0, int((now() - starts).total_seconds() // policy["interval_s"]) + 1))
    due = max(due, max((p["slot"] + 1 for p in good), default=0))
    return {"observation_id": policy["observation_id"], "lifecycle": status,
        "process_liveness_verified": False, "started_without_terminal_means": "running_or_interrupted",
        "last_activity_at": max((p["at"] for p in [*polls, *lifecycle]), default=None),
        "terminal_error_type": lifecycle[-1].get("error_type") if lifecycle else None,
        "start": policy["start"], "cohort_end": policy["cohort_end"], "end": policy["end"],
        "expected_polls": expected, "completed_polls": len(good),
        "future_poll_slots": expected - due, "missing_or_failed_polls": due - len({p["slot"] for p in good}),
        "missed_slots": sum(p["type"] == "poll_missed" for p in polls),
        "max_completion_gap_s": max(gaps, default=None), "observed_cohort_all": len(cohorts),
        "observed_cohort_supported": supported, "observed_cohort_unsupported": len(cohorts) - supported,
        "unknown_open_time_count": len(unknown_time), "unknown_type_count": len(unknown_type),
        "repeated_mapping_count": len(repeated),
        "per_venue": {v: {"all": sum(v in q["venues"] for q in cohorts.values()),
            "supported": sum(v in q["venues"] and q["supported"] for q in cohorts.values())} for v in ("main", "minibench")},
        "questions_with_content_changes": sum(len(values) > 1 for values in hashes.values()),
        "content_changes_may_include_gateway_visibility": True,
        "m3_passed": False, "next_step": "audit completeness, then preregister and run observed-timing replay"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("register").add_argument("policy", type=Path)
    sub.add_parser("run")
    sub.add_parser("report")
    args = parser.parse_args(argv)
    if args.command == "register":
        rec = register(args.directory, json.loads(args.policy.read_text()))
        result = {"registered": True, "sha256": rec["sha256"]}
    elif args.command == "report":
        result = report(args.directory)
    else:
        result = observe(args.directory, lambda policy: JournalClient(
            get_secret("METACULUS_TOKEN", PROJECT_ROOT / ".env"), args.directory, policy))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result.get("lifecycle") != "stopped" else 1


if __name__ == "__main__":
    raise SystemExit(main())
