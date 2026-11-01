"""Append-only retrospective audit propagation (PLAN §3.3 D48/D53, §5.5; M2-W2).

Explicit input records and destination only. Never rewrites forecasts, chooses another answer,
submits, or calls models. Submission rows below are an offline contract for the future M3 writer.
"""

from __future__ import annotations

import copy
import argparse
import fcntl
import json
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from flab.ids import hash_obj
from flab.jsonl import append_record, read_records
from flab.timeutil import parse_iso, to_iso, utc_now

VERSION = "audit_ledger_v1"
KINDS = {"forecast_attempt": ("attempt", "attempt_id"), "subagent_result": ("subagent", "subagent_id"),
         "aggregate": ("aggregate", "record_id"), "submission": ("submission", "record_id")}
FLAGS = frozenset({"leak_confirmed", "suspected_leak", "blocked_attempt", "model_mismatch", "tool_scope_violation"})
EXCLUDE = frozenset({"leak_confirmed", "model_mismatch", "tool_scope_violation"})
Key = tuple[str, str]


class LedgerError(ValueError):
    """Missing/inconsistent provenance: do not append a partial or falsely clean impact report."""


def _id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LedgerError("IDs must be nonempty strings")
    return value


def _flags(value: Any) -> set[str]:
    if not isinstance(value, list) or not all(isinstance(f, str) and f in FLAGS for f in value):
        raise LedgerError("audit_flags must be a list of known flags")
    return set(value)


def _ref(key: Key) -> dict[str, str]:
    return {"kind": key[0], "id": key[1]}


def _refs(record: dict[str, Any], name: str, kind: str) -> list[Key]:
    values = record.get(name)
    if values is None:
        return []
    if not isinstance(values, list):
        raise LedgerError(f"{name} must be a list")
    return [(kind, _id(v)) for v in values]


class ImpactGraph:
    """Typed DAG of immutable input records. All data dependencies must be explicit.

    A subagent result identifies its launcher with parent_attempt_id and its explicit inputs with
    input_attempt_ids/input_subagent_ids. A forecast identifies consumed child results with
    input_subagent_ids. Launcher -> child is a conservative exposure edge, not a dependency on the
    parent's eventual answer; it may form a propagation cycle, handled by monotone fixed-point union.
    """

    def __init__(self, records: Iterable[dict[str, Any]]) -> None:
        self.records: dict[Key, dict[str, Any]] = {}
        self.parents: dict[Key, set[Key]] = {}
        self.children: dict[Key, set[Key]] = {}
        for original in records:
            r = copy.deepcopy(original)
            if not isinstance(r, dict) or r.get("type") not in KINDS:
                raise LedgerError("unsupported dependency record type")
            kind, id_field = KINDS[r["type"]]
            key = (kind, _id(r.get(id_field)))
            if key in self.records:
                raise LedgerError(f"duplicate node {key}")
            if type(r.get("question_id")) is not int or r["question_id"] <= 0:
                raise LedgerError("question_id must be a positive integer")
            _id(r.get("question_snapshot_id"))
            hash_obj(r)  # reject non-JSON/non-finite source records before any writes
            _flags(r.get("audit_flags", []))
            parents = set(_refs(r, "input_attempt_ids", "attempt") + _refs(r, "input_subagent_ids", "subagent"))
            if kind == "attempt" and r.get("tier") == 2 and not r.get("input_attempt_ids") and not r.get("prev_attempt_id"):
                raise LedgerError("tier-2 attempt requires input provenance")
            if kind == "aggregate" and r.get("status") == "ready" and not r.get("input_attempt_ids"):
                raise LedgerError("ready aggregate requires input provenance")
            if kind == "attempt" and r.get("prev_attempt_id") is not None:
                parents.add(("attempt", _id(r["prev_attempt_id"])))
            if kind == "submission":
                parents.add(("aggregate", _id(r.get("aggregate_id"))))
            self.records[key], self.parents[key], self.children[key] = r, parents, set()
        for key, parents in self.parents.items():
            for parent in parents:
                if parent not in self.records:
                    raise LedgerError(f"missing dependency {parent} for {key}")
                child_record, parent_record = self.records[key], self.records[parent]
                for field in ("question_id", "question_snapshot_id", "post_id", "tournament_id"):
                    if child_record.get(field) != parent_record.get(field):
                        raise LedgerError(f"incompatible {field} on edge {parent} -> {key}")
                self.children[parent].add(key)
        # Kahn's algorithm also handles long chains without recursive stack limits.
        remaining = {key: len(parents) for key, parents in self.parents.items()}
        ready = deque(sorted(key for key, count in remaining.items() if count == 0))
        self.order: list[Key] = []
        while ready:
            key = ready.popleft()
            self.order.append(key)
            for child in sorted(self.children[key]):
                remaining[child] -= 1
                if remaining[child] == 0:
                    ready.append(child)
        if len(self.order) != len(self.records):
            raise LedgerError("cycle in data dependencies")
        self.exposure_children = {key: set(children) for key, children in self.children.items()}
        for key, record in self.records.items():
            if key[0] != "subagent":
                continue
            launcher = ("attempt", _id(record.get("parent_attempt_id")))
            if launcher not in self.records:
                raise LedgerError(f"missing subagent launcher {launcher}")
            for field in ("question_id", "question_snapshot_id", "post_id", "tournament_id"):
                if record.get(field) != self.records[launcher].get(field):
                    raise LedgerError(f"incompatible {field} on subagent launch")
            delivered = record.get("result_delivered")
            if type(delivered) is not bool:
                raise LedgerError("subagent result_delivered must be a harness-recorded boolean")
            if delivered != (key in self.parents[launcher]):
                raise LedgerError("subagent delivery must match the launcher's input_subagent_ids")
            self.exposure_children[launcher].add(key)

    def descendants(self, root: Key) -> set[Key]:
        if root not in self.records:
            raise LedgerError(f"unknown audit target {root}")
        found, work = {root}, [root]
        while work:
            for child in self.exposure_children[work.pop()]:
                if child not in found:
                    found.add(child)
                    work.append(child)
        return found

    def events(self, events: Iterable[dict[str, Any]]) -> list[tuple[dict[str, Any], Key, set[str]]]:
        parsed, seen = [], set()
        for original in events:
            e = copy.deepcopy(original)
            if not isinstance(e, dict) or e.get("type") not in ("audit", "audit_update"):
                raise LedgerError("expected audit or audit_update records")
            eid = _id(e.get("audit_event_id"))
            if eid in seen:
                raise LedgerError(f"duplicate audit event {eid}")
            seen.add(eid)
            target = e.get("target")
            if target is None:
                key = ("attempt", _id(e.get("attempt_id")))
            else:
                if not isinstance(target, dict) or target.get("kind") not in {v[0] for v in KINDS.values()}:
                    raise LedgerError("invalid audit target")
                key = (target["kind"], _id(target.get("id")))
                if e.get("attempt_id") is not None and key != ("attempt", e["attempt_id"]):
                    raise LedgerError("conflicting audit target and attempt_id")
            if key not in self.records:
                raise LedgerError(f"unknown audit target {key}")
            if e.get("question_id") is not None and e["question_id"] != self.records[key]["question_id"]:
                raise LedgerError("audit question_id differs from its target")
            parse_iso(e.get("audited_at"))
            reasons = e.get("audit_reasons")
            if not isinstance(reasons, list) or not reasons or not all(isinstance(x, str) and x.strip() for x in reasons):
                # Existing clean attempt audits may have no reasons; flagged events require evidence context.
                if e.get("audit_flags"):
                    raise LedgerError("flagged audit event needs audit_reasons")
            flags = _flags(e.get("audit_flags"))
            hash_obj(e)
            parsed.append((e, key, flags))
        return parsed

    def attempt_audit_view(self, events: Iterable[dict[str, Any]], *, question_id: int | None = None,
                           question_snapshot_id: str | None = None) -> list[dict[str, Any]]:
        """Ephemeral additive overlays for aggregate.select_inputs, never new source audit events.

        Propagates original flags as well as later events, so child results can affect their consumers.
        Source event IDs remain available for the resulting aggregate's audit lineage.
        """
        flags, origins = {}, {key: set() for key in self.records}
        evidence = {key: set() for key in self.records}
        for key, r in self.records.items():
            flags[key] = _flags(r.get("audit_flags", []))
            if r.get("model_check") == "mismatch" or r.get("inherited_model_mismatch"):
                flags[key].add("model_mismatch")
            if r.get("tool_violations") or r.get("error_type") == "tool_scope_violation":
                flags[key].add("tool_scope_violation")
            if flags[key]:
                evidence[key].add(key)
                if r.get("audit_event_id") is not None:
                    origins[key].add(_id(r["audit_event_id"]))
        for event, key, added in self.events(events):
            flags[key].update(added)
            if added:
                origins[key].add(event["audit_event_id"])
        pending = deque(key for key in self.order if flags[key] or origins[key])
        while pending:
            key = pending.popleft()
            for child in self.exposure_children[key]:
                previous = (len(flags[child]), len(origins[child]), len(evidence[child]))
                flags[child].update(flags[key])
                origins[child].update(origins[key])
                evidence[child].update(evidence[key])
                if previous != (len(flags[child]), len(origins[child]), len(evidence[child])):
                    pending.append(child)
        return [{"type": "audit_projection", "attempt_id": key[1], "audit_flags": sorted(flags[key]),
                 "question_id": self.records[key]["question_id"],
                 "question_snapshot_id": self.records[key]["question_snapshot_id"],
                 "source_audit_event_ids": sorted(origins[key]),
                 "source_record_refs": [_ref(ref) for ref in sorted(evidence[key])]}
                for key in self.order if key[0] == "attempt"
                and (question_id is None or self.records[key]["question_id"] == question_id)
                and (question_snapshot_id is None or self.records[key]["question_snapshot_id"] == question_snapshot_id)]


def _row(kind: str, identity: Any, at: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "record_id": "ail_" + hash_obj([VERSION, kind, identity]),
            "version": VERSION, "recorded_at_utc": at, **fields}


def proposed_rows(graph: ImpactGraph, events: Iterable[dict[str, Any]], *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Registers immutable nodes/events, then one impact per (source audit event, affected node)."""
    parsed = graph.events(events)  # validate the entire batch before planning writes
    at = to_iso(now or utc_now())
    rows = [_row("dependency_registered", key, at, target=_ref(key), source_sha256=hash_obj(r))
            for key, r in sorted(graph.records.items())]
    for event, root, flags in parsed:
        eid = event["audit_event_id"]
        rows.append(_row("audit_registered", eid, at, audit_event_id=eid, target=_ref(root),
                         source_sha256=hash_obj(event)))
        if not flags:
            continue
        for key in sorted(graph.descendants(root)):
            r = graph.records[key]
            rows.append(_row("audit_impact", [eid, key], at, audit_event_id=eid, source_target=_ref(root),
                             target=_ref(key), question_id=r["question_id"], question_snapshot_id=r["question_snapshot_id"],
                             audit_flags=sorted(flags), audit_reasons=event["audit_reasons"],
                             effect="exclude_from_future_use" if flags & EXCLUDE else "retain_with_flag",
                             submission_state=r.get("state") if key[0] == "submission" else None,
                             submission_id=r.get("submission_id") if key[0] == "submission" else None,
                             source_event_sha256=hash_obj(event), target_record_sha256=hash_obj(r)))
    return rows


def _content(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "recorded_at_utc"}


def append_impacts(path: Path, graph: ImpactGraph, events: Iterable[dict[str, Any]], *,
                   now: datetime | None = None) -> list[dict[str, Any]]:
    """Idempotent append with a process lock and per-row fsync.

    Pass the full retained graph/event snapshot each time. Existing source IDs cannot be edited or
    silently dropped. A valid prefix left by interruption resumes safely; a torn/invalid JSONL row
    fails closed and requires storage recovery rather than silently truncating history.
    """
    rows = proposed_rows(graph, events, now=now)
    planned = {r["record_id"]: r for r in rows}
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists() and path.stat().st_size:
            with path.open("rb") as stream:
                stream.seek(-1, 2)
                if stream.read(1) != b"\n":
                    raise LedgerError("unterminated history row; storage recovery required before appending")
        existing: dict[str, dict[str, Any]] = {}
        for row in read_records(path) if path.exists() else []:
            if not isinstance(row, dict):
                raise LedgerError("history row is not an object")
            rid = row.get("record_id")
            if rid in existing or rid not in planned or _content(row) != _content(planned[rid]):
                raise LedgerError("history differs, contains duplicate IDs, or inputs omitted a registered source")
            existing[rid] = row
        missing = [r for r in rows if r["record_id"] not in existing]
        for row in missing:
            append_record(path, row)
    return missing


def replay_files(record_paths: list[Path], audit_paths: list[Path], output: Path) -> list[dict[str, Any]]:
    """Read explicit snapshots of the source logs; never overwrite or alias an input file."""
    paths = [Path(p).resolve() for p in [*record_paths, *audit_paths]]
    output = output.resolve()
    if any(output == p or (output.exists() and p.exists() and output.samefile(p)) for p in paths):
        raise LedgerError("output must not alias a source log")
    records = [r for path in record_paths for r in read_records(path)]
    events = [e for path in audit_paths for e in read_records(path)]
    return append_impacts(output, ImpactGraph(records), events)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append retrospective audit impacts; never send or rerun predictions.")
    parser.add_argument("--records", type=Path, action="append", required=True,
                        help="forecast, subagent, aggregate or submission JSONL; repeat for each source log")
    parser.add_argument("--audits", type=Path, action="append", required=True, help="complete audit JSONL history")
    parser.add_argument("--output", type=Path, required=True, help="dedicated append-only audit impact JSONL")
    args = parser.parse_args(argv)
    try:
        added = replay_files(args.records, args.audits, args.output)
    except (OSError, ValueError) as exc:
        print(f"audit propagation stopped: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"version": VERSION, "appended": len(added),
                      "impacts": sum(r["type"] == "audit_impact" for r in added)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
