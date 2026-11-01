"""Recoverable submission ledger for PLAN §3.6 (M3-W1).

The default transport is deliberately offline until V15 verifies authenticated readback.
Transport implementations must never retry writes themselves. A missing readback is unknown,
not evidence that a request failed. Both commands use submit.lock and short ledger transactions.
"""
from __future__ import annotations

import argparse
import copy
import time
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Protocol

import aggregate as A
import comment as C
from forecast import Lock, PreflightError
from flab.config import load_submit_mode, load_tournaments, load_run_config
from flab.ids import new_id, sha256_hex
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso, utc_now
from flab.workdir import PROJECT_ROOT
from poll_questions import TournamentIndex, is_synced_copy, snapshot_from_post
from validate import submit_deadline

VERSION = "submit_v4"
BUSY_EXIT_CODE = 75
FORECAST_PATH = "/api/questions/forecast/"
COMMENT_PATH = "/api/comments/create/"
# Conservative provisional platform limits; both bounds and option-label mapping await V15.
PROBABILITY_MIN = Decimal("0.001")
PROBABILITY_MAX = Decimal("0.999")
COMMENT_ATTEMPTS = 3
FORECAST_ATTEMPTS = 3


class PrecheckError(ValueError):
    """Temporary blocker: configuration, identity, transport or uncertain evidence.

    Unknown exceptions also remain temporary. Only PermanentPrecheckError can abandon
    an intent, and only before ANY request_started event. Never classify by message text.
    """


class PermanentPrecheckError(PrecheckError):
    """This frozen input is unusable: invalidated/changed data, target or closed window."""


class ConfirmedFailure(Exception):
    """Transport has authoritative evidence the write was NOT accepted (never a timeout).

    Evidence must be a credential-free description suitable for the local ledger.
    """


@dataclass(frozen=True)
class Readback:
    outcome: str = "unknown"  # found, not_accepted, unknown
    record: dict | None = None
    request_id: str | None = None
    evidence: str | None = None


class Transport(Protocol):
    """V15 boundary. Records/receipts are normalized, authenticated and credential-free.

    A success record has id, bot_id, question_id (forecast) or post_id (comment), and
    payload equal to the submitted JSON. readback must identify this *request*, including
    across trial repetitions; matching an older identical forecast is not sufficient.
    not_accepted requires authoritative evidence and the exact local request_id. Empty
    pages, eventual consistency, HTTP errors and read failures must return unknown.
    tournament_index must freshly cover all open/upcoming main and MiniBench rounds,
    including overlapping rounds; raise if completeness or project identity is uncertain.
    """
    def account(self) -> dict: ...
    def get_post(self, post_id: int) -> dict: ...
    def tournament_index(self, configured: dict) -> TournamentIndex: ...
    def write(self, path: str, payload: object, *, request_id: str) -> dict: ...
    def readback(self, operation: str, intent: dict, *, request_id: str) -> Readback: ...


class OfflineTransport:
    def __getattr__(self, name):
        raise PrecheckError("V15 transport unavailable; no network transport is installed")


def existing_predictions(post: dict) -> list[dict]:
    """V15 assumption: authenticated question.my_forecasts is a complete list.

    Missing/unrecognized is unsafe; only an explicit empty list proves no prior forecast
    during initial precheck. It NEVER proves nonacceptance of an outstanding request.
    """
    values = post.get("question", {}).get("my_forecasts")
    if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
        raise PrecheckError("existing prediction field unavailable or unrecognized (V15)")
    return values


def forecast_payload(snapshot: dict, aggregate: dict) -> list[dict]:
    if error := A.input_error(aggregate, snapshot):
        raise PrecheckError(error)
    if aggregate.get("options") != snapshot.get("options"):
        raise PrecheckError("aggregate options differ from snapshot")
    if snapshot["question_type"] == "binary":
        p = Decimal(str(aggregate["probability"]))
        if not PROBABILITY_MIN <= p <= PROBABILITY_MAX:
            raise PrecheckError("probability outside provisional platform range")
        return [{"question": snapshot["question_id"], "probability_yes": float(p)}]
    options = snapshot["options"]
    values = [Decimal(str(aggregate["option_probabilities"][o])) for o in options]
    rounded = [p.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP) for p in values[:-1]]
    rounded.append(Decimal(1) - sum(rounded))
    if any(not PROBABILITY_MIN <= p <= PROBABILITY_MAX for p in [*values, *rounded]):
        raise PrecheckError("option probability outside provisional platform range")
    return [{"question": snapshot["question_id"],
             "probability_yes_per_category": dict(zip(options, map(float, rounded)))}]


def _unique(rows: list[dict], field: str) -> dict:
    result = {}
    for row in rows:
        key = row.get(field)
        if not isinstance(key, str) or not key or key in result:
            raise PrecheckError(f"missing or duplicate {field} in ledger")
        result[key] = row
    return result


class Submitter:
    def __init__(self, root: Path, *, config_dir: Path | None = None,
                 transport: Transport | None = None, allow_resubmit_by_venue: dict[int, bool] | None = None,
                 now=utc_now, sleep=time.sleep):
        self.root = Path(root)
        self.config_dir = config_dir or PROJECT_ROOT / "config"
        self.transport = transport if transport is not None else OfflineTransport()
        self.allow_resubmit = dict(allow_resubmit_by_venue or {})
        self.now, self.sleep = now, sleep
        self.log = self.root / "submissions.jsonl"

    def _rows(self):
        with transaction(self.root):
            return read_complete(self.log)

    def _event(self, intent, status, **extra):
        with transaction(self.root):
            rows = self._rows()  # Always reread under the transaction before appending.
            # Recheck on every run, but persist only changes in the rejection/unknown reason.
            if status == "rejected_precheck":
                last = next((r for r in reversed(rows) if r["status"] == status
                             and r.get("aggregate_id") == intent.get("aggregate_id")), None)
                if last and last.get("reason") == extra.get("reason"):
                    return last
            elif status == "unknown":
                last = self._last_operation(intent, extra["operation"])
                if last and last["status"] == status and last.get("request_id") == extra["request_id"] \
                        and last.get("reason") == extra.get("reason"):
                    return last
            row = {k: intent.get(k) for k in ("intent_id", "mode", "bot_id", "tournament_id", "post_id",
                   "question_id", "question_snapshot_id", "options", "aggregate_id", "comment_hash")}
            if status in ("intent", "dry_run"):
                row.update(copy.deepcopy(intent))
            row.update(type="submission", record_id=new_id("submission"), version=VERSION,
                       at=to_iso(self.now()), status=status, state=status,
                       submission_id=intent.get("intent_id"), **extra)
            append_record(self.log, row)
            return row

    def _policy(self):
        mode = load_submit_mode(self.config_dir)
        return mode, load_tournaments(self.config_dir)

    def _gate(self, intent):
        mode, cfg = self._policy()
        if mode["mode"] not in ("trial", "live") or mode["mode"] != intent["mode"]:
            raise PrecheckError("submission mode disabled or changed")
        if (self.config_dir / "STOP_SUBMIT").exists():
            raise PrecheckError("STOP_SUBMIT exists")
        bot = mode.get("approved_bot_id")
        if type(bot) is not int or bot <= 0 or bot != intent["bot_id"]:
            raise PrecheckError("approved bot ID missing or changed")
        entries = [(name, entry) for name, entry in cfg["tournaments"].items()
                   if entry.get("id") == intent["tournament_id"]]
        if len(entries) != 1:
            raise PrecheckError("venue ID is not uniquely configured")
        name, entry = entries[0]
        if mode["mode"] == "trial":
            if name not in cfg["trial_ground_types"] or name in ("main", "minibench"):
                raise PrecheckError("venue not allowed in trial")
        elif entry.get("submit_whitelisted") is not True:
            raise PrecheckError("venue is not submit whitelisted")
        if intent["allow_resubmit"] and (mode["mode"] != "trial" or
                self.allow_resubmit.get(intent["tournament_id"]) is not True):
            raise PrecheckError("explicit trial repeat policy is no longer enabled")
        return cfg

    def _identity(self, intent):
        account = self.transport.account()
        if type(account.get("id")) is not int or account["id"] != intent["bot_id"]:
            raise PrecheckError("wrong authenticated bot account")

    def _check_snapshot(self, snapshot_id):
        with transaction(self.root):
            # Snapshot IDs are unique in questions.jsonl. Never infer validity from the
            # replacement snapshot, or require it to exist before honoring invalidation.
            if any(r.get("type") == "snapshot_invalidated" and r.get("old_snapshot_id") == snapshot_id
                   for r in read_complete(self.root / "question_queue.jsonl")):
                raise PermanentPrecheckError("question snapshot invalidated")

    def _inputs(self, aggregate, comment, snapshots):
        with transaction(self.root):
            self._check_snapshot(aggregate.get("question_snapshot_id"))
            if aggregate.get("submission_eligible") is not True:
                raise PrecheckError("aggregate is not submission eligible")
            if aggregate.get("stage") != "final" or aggregate.get("status") != "ready" \
                    or not aggregate.get("scheduler_campaign_id"):
                raise PrecheckError("not a ready scheduler final aggregate")
            snap = snapshots.get(aggregate.get("question_snapshot_id"))
            if snap is None:
                raise PrecheckError("snapshot not found in fetched question ledger")
            if any(type(snap.get(k)) is not int or snap[k] <= 0 for k in ("post_id", "question_id", "tournament_id")):
                raise PrecheckError("invalid fetched target IDs")
            payload = forecast_payload(snap, aggregate)
            if not comment or comment.get("version") != C.VERSION or comment.get("aggregate_id") != aggregate["record_id"] \
                    or comment.get("question_id") != snap["question_id"] \
                    or comment.get("question_snapshot_id") != snap["snapshot_id"]:
                raise PrecheckError("comment identity mismatch or missing comment_v1")
            text = comment.get("text")
            if not isinstance(text, str) or sha256_hex(text) != comment.get("text_sha256"):
                raise PrecheckError("comment hash mismatch")
            # Reuse comment_v1's full audit graph and exact adopted-input checks.
            # cycle.publish uses snapshot-scoped selection; fetched snapshots themselves
            # do not carry that runtime flag. Keep the full history for audit propagation.
            comment_snapshot = {**snap, "forecast_scope": "snapshot"} if aggregate.get("cycle_version") else snap
            rebuilt = C.build(comment_snapshot, aggregate, [*read_complete(self.root / "forecasts.jsonl"),
                              *read_complete(self.root / "aggregates.jsonl")],
                              read_complete(self.root / "audits.jsonl"), now=self.now())
            if text != rebuilt["text"] or comment.get("input_attempt_ids") != rebuilt["input_attempt_ids"]:
                raise PrecheckError("comment differs from adopted local aggregate")
            return snap, payload

    def _candidate(self, aggregate, comment, snapshots):
        with transaction(self.root):
            snap, payload = self._inputs(aggregate, comment, snapshots)
            mode = load_submit_mode(self.config_dir)
            return {"intent_id": new_id("intent"), "mode": mode["mode"], "bot_id": mode.get("approved_bot_id"),
                    "aggregate_id": aggregate["record_id"], "comment_id": comment["record_id"],
                    "question_snapshot_id": snap["snapshot_id"], "snapshot": copy.deepcopy(snap),
                    **{k: snap.get(k) for k in ("tournament_id", "post_id", "question_id", "options")},
                    "probability": aggregate.get("probability"), "option_probabilities": aggregate.get("option_probabilities"),
                    "forecast_payload": payload, "comment_text": comment["text"], "comment_hash": comment["text_sha256"],
                    "comment_payload": {"on_post": snap["post_id"], "text": comment["text"],
                                        "is_private": True, "included_forecast": True},
                    "allow_resubmit": mode["mode"] == "trial" and self.allow_resubmit.get(snap["tournament_id"]) is True}

    def _history(self, intent):
        return [r for r in self._rows() if r.get("intent_id") == intent["intent_id"]]

    def _duplicate(self, intent):
        with transaction(self.root):
            rows = self._rows()
            others = [r for r in rows if r["status"] == "intent" and r["intent_id"] != intent["intent_id"]
                      and (r.get("bot_id"), r.get("question_id")) == (intent["bot_id"], intent["question_id"])]
            for other in others:
                history = [r for r in rows if r.get("intent_id") == other["intent_id"]]
                if any(r["status"] == "abandoned" for r in history) \
                        and not any(r["status"] == "request_started" for r in history):
                    continue
                completed = any(r.get("intent_id") == other["intent_id"] and r["status"] == "comment_ok" for r in rows)
                repeat = intent["allow_resubmit"] and other["mode"] == "trial" \
                    and other["tournament_id"] == intent["tournament_id"] and completed
                if not repeat:
                    raise PrecheckError("duplicate bot/question across venues or unresolved prior intent")

    def _precheck(self, intent):
        cfg = self._gate(intent)
        self._identity(intent)
        self._duplicate(intent)
        post = self.transport.get_post(intent["post_id"])
        current = snapshot_from_post(post, intent["snapshot"].get("venue", ""))
        if current is None:
            raise PrecheckError("unrecognized current post")
        snap = intent["snapshot"]
        for key in ("post_id", "question_id", "question_type", "options", "scheduled_close_time"):
            if current.get(key) != snap.get(key):
                raise PermanentPrecheckError(f"current {key} differs from snapshot")
        if intent["tournament_id"] not in current["project_ids"]:
            raise PermanentPrecheckError("question does not belong to target venue")
        if current["status"] != "open":
            raise PermanentPrecheckError("question is not open")
        deadline = submit_deadline(snap, load_run_config(self.config_dir)["deadlines"])
        if deadline is None or self.now() >= deadline:
            raise PermanentPrecheckError("outside local submission window")
        if not current.get("open_time") or self.now() < parse_iso(current["open_time"]):
            raise PrecheckError("outside local submission window")
        trial_ground = any(name in cfg["trial_ground_types"] and entry.get("id") == intent["tournament_id"]
                           for name, entry in cfg["tournaments"].items())
        if intent["mode"] == "trial" or trial_ground:
            index = self.transport.tournament_index(copy.deepcopy(cfg))
            if not isinstance(index, TournamentIndex) or not index.project_ids:
                raise PrecheckError("complete current tournament exclusion index unavailable")
            excluded = [cfg["tournaments"].get(name, {}).get("id") for name in ("main", "minibench")]
            if any(type(pid) is not int or pid <= 0 or pid not in index.project_ids for pid in excluded):
                raise PrecheckError("main and MiniBench identity or exclusion coverage unavailable")
            if reason := is_synced_copy(current, index):
                raise PrecheckError(reason)
        if existing_predictions(post) and not intent["allow_resubmit"]:
            raise PrecheckError("platform already has a forecast for this bot/question")
        return current["content_hash"] != snap.get("content_hash")

    def _receipt_matches(self, operation, intent, record):
        key = "question_id" if operation == "forecast" else "post_id"
        return isinstance(record, dict) and type(record.get("id")) in (int, str) \
            and bool(record["id"]) and type(record.get("bot_id")) is int and record["bot_id"] == intent["bot_id"] \
            and type(record.get(key)) is int and record[key] == intent[key] \
            and record.get("payload") == intent[f"{operation}_payload"]

    def _reconcile_request(self, intent, operation, last):
        request_id = last["request_id"]
        try:
            self._identity(intent)
            result = self.transport.readback(operation, copy.deepcopy(intent), request_id=request_id)
            if result.request_id == request_id and result.outcome == "found" \
                    and self._receipt_matches(operation, intent, result.record):
                self._event(intent, operation + "_ok", operation=operation, request_id=request_id,
                            response=result.record, remote_id=result.record["id"], reconciled=True)
                return
            if result.request_id == request_id and result.outcome == "not_accepted" and result.evidence:
                self._failed(intent, operation, request_id, result.evidence)
                return
            reason = "readback cannot establish this request's outcome"
        except Exception as exc:
            reason = f"readback unavailable: {type(exc).__name__}"
        self._event(intent, "unknown", operation=operation, request_id=request_id, reason=reason)

    def _failed(self, intent, operation, request_id, evidence):
        with transaction(self.root):
            count = sum(r["status"] == "request_started" and r.get("operation") == operation for r in self._history(intent))
            exhausted = count >= (COMMENT_ATTEMPTS if operation == "comment" else FORECAST_ATTEMPTS)
            self._event(intent, operation + "_failed" if exhausted else "confirmed_failed", operation=operation,
                        request_id=request_id, evidence=evidence, notify_louis=exhausted,
                        next_retry_at=to_iso(self.now() + timedelta(seconds=2 ** max(0, count - 1))))

    def _last_operation(self, intent, operation):
        return next((r for r in reversed(self._history(intent)) if r.get("operation") == operation), None)

    def _write(self, intent, operation):
        try:
            if operation == "forecast":
                self._validate_frozen(intent)
                revised = self._precheck(intent)
            else:
                self._identity(intent)
                revised = False
        except Exception as exc:
            abandon = isinstance(exc, PermanentPrecheckError) and not any(
                r["status"] == "request_started" for r in self._history(intent))
            self._event(intent, "abandoned" if abandon else "rejected_precheck",
                        reason=self._reason(exc), blocked_operation=operation)
            return False
        request_id = new_id("request")
        with transaction(self.root):
            try:
                if operation == "forecast":
                    self._validate_frozen(intent)
                    self._duplicate(intent)
                # Recheck after remote reads, backoff and waiting for the local transaction.
                self._gate(intent)
                if operation == "forecast":
                    deadline = submit_deadline(intent["snapshot"], load_run_config(self.config_dir)["deadlines"])
                    if deadline is None or self.now() >= deadline:
                        raise PermanentPrecheckError("local submission deadline elapsed during precheck")
            except Exception as exc:
                abandon = isinstance(exc, PermanentPrecheckError) and not any(
                    r["status"] == "request_started" for r in self._history(intent))
                self._event(intent, "abandoned" if abandon else "rejected_precheck",
                            reason=self._reason(exc), blocked_operation=operation)
                return False
            self._event(intent, "request_started", operation=operation, request_id=request_id, text_revised=revised)
        try:
            path = FORECAST_PATH if operation == "forecast" else COMMENT_PATH
            response = self.transport.write(path, copy.deepcopy(intent[f"{operation}_payload"]), request_id=request_id)
        except ConfirmedFailure as exc:
            self._failed(intent, operation, request_id, str(exc) or "transport confirmed nonacceptance")
            return True
        except Exception as exc:
            self._event(intent, "unknown", operation=operation, request_id=request_id,
                        reason=f"write outcome unavailable: {type(exc).__name__}")
            return False
        if not self._receipt_matches(operation, intent, response):
            self._event(intent, "unknown", operation=operation, request_id=request_id, reason="unrecognized write receipt")
            return False
        self._event(intent, operation + "_ok", operation=operation, request_id=request_id,
                    response=response, remote_id=response["id"], reconciled=False)
        return True

    def _validate_frozen(self, intent):
        with transaction(self.root):
            self._check_snapshot(intent["question_snapshot_id"])
            try:
                aggregates, comments, snapshots = self._load_inputs()
                aggregate = aggregates[intent["aggregate_id"]]
                comment = comments[intent["comment_id"]]
                snap, payload = self._inputs(aggregate, comment, snapshots)
            except (PrecheckError, C.CommentError) as exc:
                raise PermanentPrecheckError(str(exc)) from exc
            except KeyError as exc:
                raise PermanentPrecheckError("frozen submission local records missing") from exc
            if snap != intent["snapshot"] or payload != intent["forecast_payload"] \
                    or comment["text_sha256"] != intent["comment_hash"] or comment["text"] != intent["comment_text"]:
                raise PermanentPrecheckError("frozen submission differs from local records")

    def _resume(self, intent, *, reconcile_only=False):
        if any(r["status"] in ("abandoned", "forecast_failed", "comment_failed") for r in self._history(intent)):
            return
        for operation in ("forecast", "comment"):
            last = self._last_operation(intent, operation)
            if last and last["status"] in ("request_started", "unknown"):
                self._reconcile_request(intent, operation, last)
                last = self._last_operation(intent, operation)
            if last and last["status"] == operation + "_ok":
                continue
            # Also enforce the cap for confirmed failures written by submit_v1.
            if last and last["status"] == "confirmed_failed":
                count = sum(r["status"] == "request_started" and r.get("operation") == operation
                            for r in self._history(intent))
                if count >= (COMMENT_ATTEMPTS if operation == "comment" else FORECAST_ATTEMPTS):
                    self._failed(intent, operation, last["request_id"], last["evidence"])
                    return
            if reconcile_only or (last and last["status"] in ("unknown", "forecast_failed", "comment_failed")):
                return
            while True:
                if last and last["status"] == "confirmed_failed":
                    delay = (parse_iso(last["next_retry_at"]) - self.now()).total_seconds()
                    if delay > 0:
                        self.sleep(delay)
                        if self.now() < parse_iso(last["next_retry_at"]):
                            return
                if not self._write(intent, operation):
                    return
                last = self._last_operation(intent, operation)
                if last["status"] == operation + "_ok":
                    break
                if operation == "forecast" or last["status"] == "comment_failed":
                    return

    def _load_inputs(self):
        with transaction(self.root):
            aggregates = _unique(read_complete(self.root / "aggregates.jsonl"), "record_id")
            comments = _unique([r for r in read_complete(self.root / "comments.jsonl") if r.get("type") == "comment"], "record_id")
            snapshots = _unique([r for r in read_complete(self.root / "questions.jsonl")
                                 if r.get("type") == "question_snapshot"], "snapshot_id")
            return aggregates, comments, snapshots

    @staticmethod
    def _reason(exc):
        # Arbitrary transport exceptions can contain credentials or remote bodies.
        return str(exc) if isinstance(exc, (PrecheckError, C.CommentError)) else f"precheck unavailable: {type(exc).__name__}"

    def run(self, *, reconcile_only=False):
        lock = Lock(self.root / "submit.lock")
        try:
            lock.__enter__()
        except PreflightError:
            return {"status": "busy", "events": 0, "statuses": {}}
        try:
            rows = self._rows()
            intents = [r for r in rows if r["status"] == "intent"]
            for intent in intents:
                self._resume(intent, reconcile_only=reconcile_only)
            if not reconcile_only:
                aggregates, comments, snapshots = self._load_inputs()
                for aggregate in aggregates.values():
                    if aggregate.get("stage") != "final" or any(i["aggregate_id"] == aggregate["record_id"] for i in intents):
                        continue
                    matches = [c for c in comments.values() if c.get("aggregate_id") == aggregate["record_id"]]
                    candidate = {"aggregate_id": aggregate["record_id"], "mode": load_submit_mode(self.config_dir)["mode"]}
                    snap = snapshots.get(aggregate.get("question_snapshot_id"))
                    if snap is not None:
                        candidate.update({k: snap.get(k) for k in ("question_id", "post_id", "tournament_id", "options")})
                        candidate["question_snapshot_id"] = snap["snapshot_id"]
                    try:
                        if len(matches) != 1:
                            raise PrecheckError("expected exactly one comment_v1 for aggregate")
                        candidate = self._candidate(aggregate, matches[0], snapshots)
                        if candidate["mode"] == "local":
                            if not any(r["status"] == "dry_run" and r["aggregate_id"] == candidate["aggregate_id"] for r in rows):
                                self._event(candidate, "dry_run")
                            continue
                        self._precheck(candidate)
                    except Exception as exc:
                        self._event(candidate, "rejected_precheck", reason=self._reason(exc))
                        continue
                    row = self._event(candidate, "intent")
                    intents.append(row)
                    self._resume(row)
            new_rows = self._rows()[len(rows):]
            return {"events": len(new_rows), "statuses": {s: sum(r["status"] == s for r in new_rows)
                    for s in sorted({r["status"] for r in new_rows})}}
        finally:
            lock.__exit__(None, None, None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "reconcile"))
    args = parser.parse_args(argv)
    result = Submitter(PROJECT_ROOT / "data").run(reconcile_only=args.command == "reconcile")
    print(result)  # Counts only: do not expose open-question outputs.
    return BUSY_EXIT_CODE if result.get("status") == "busy" else 0


if __name__ == "__main__":
    raise SystemExit(main())
