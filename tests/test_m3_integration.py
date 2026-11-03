from __future__ import annotations

import copy
import json
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import continuous as Q
import cycle as C
import scheduler
import submit as S
from flab import config
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import to_iso
from poll_questions import latest_snapshots, record_snapshots, snapshot_from_post
from test_cycle import policy
from test_forecast import NOW
from test_poll_questions import _post
from test_scheduler import environment
from test_submit import FakeTransport


class Transport(FakeTransport):
    def __init__(self, post):
        super().__init__(post)
        self.calls, self.account_effects = [], []

    def account(self):
        self.calls.append("account")
        if self.account_effects and self.account_effects.pop(0) == "error":
            raise OSError("synthetic account read failure")
        return super().account()

    def get_post(self, post_id):
        self.calls.append("get_post")
        assert post_id == self.post["id"]
        return super().get_post(post_id)

    def tournament_index(self, configured):
        self.calls.append("tournament_index")
        return super().tournament_index(configured)

    def write(self, path, payload, *, request_id):
        self.calls.append("write")
        return super().write(path, payload, request_id=request_id)

    def readback(self, operation, intent, *, request_id):
        self.calls.append("readback")
        return super().readback(operation, intent, request_id=request_id)


@pytest.fixture
def integrated(environment, tmp_path, monkeypatch):
    project = tmp_path / "project"
    config_dir = project / "config"
    config_dir.mkdir(parents=True)
    configs = config.load_all_configs()
    configs["submit_mode.json"] = {"mode": "trial", "approved_bot_id": 42}
    configs["players_enabled.json"] = dict(claude=False, gemini=False, gpt=True)
    configs["tournaments.json"] = {"tournaments": {
        "main": {"id": 33121, "slug": "main", "submit_whitelisted": False},
        "minibench": {"id": 33122, "slug": "mini", "submit_whitelisted": False},
        "bot_testing_area": {"id": 32977, "slug": "bot-testing-area", "submit_whitelisted": False}},
        "trial_ground_types": ["bot_testing_area"]}
    for name, value in configs.items():
        (config_dir / name).write_text(json.dumps(value))
    monkeypatch.setattr(config, "DEFAULT_CONFIG_DIR", config_dir)
    monkeypatch.setattr(scheduler, "load_players_enabled", config.load_players_enabled)
    obj, _, tracker, _ = environment(count=1)
    root = project / "data"
    clock = [NOW]
    post = _post(1100, 100, "binary", "Synthetic integration question", my_forecasts=[],
                 open_time=to_iso(NOW - timedelta(hours=1)),
                 scheduled_close_time=to_iso(NOW + timedelta(hours=3)))
    posts = [post]
    transport = Transport(post)
    submit_calls = []
    campaign = policy()

    def poller():
        snapshots = [{**snapshot_from_post(p, "bot-testing-area"), "tournament_id": 32977,
                      "ground": "bot_testing_area", "priority": "main"} for p in posts]
        record_snapshots(snapshots, log=root / "questions.jsonl", now=to_iso(clock[0]))
        return latest_snapshots(root / "questions.jsonl")

    def extra(player):
        return {**obj.extra(player), "make_dir": lambda: Path(tempfile.mkdtemp(dir=tmp_path)),
                "remove_dir": shutil.rmtree}

    def sleep(seconds):
        clock[0] += timedelta(seconds=seconds)

    def submit(project_arg, seconds):
        assert project_arg == project and seconds == C.SUBMIT_TIMEOUT_S
        result = S.Submitter(root, config_dir=config_dir, transport=transport,
                             now=lambda: clock[0], sleep=sleep).run()
        submit_calls.append(result)
        return {"status": "ok", **result}

    def run(**kw):
        result = C.run_cycle(root, campaign, poller, project=project, now=lambda: clock[0],
            adapter_loader=obj.loader, preflight=kw.pop("preflight", obj.pf), context_extra=extra,
            acceptance_log=root / "acceptance.jsonl", submit=kw.pop("submit", submit),
            sleep=kw.pop("sleep", sleep), **kw)
        assert result["status"] == "complete", result
        return result

    return SimpleNamespace(root=root, config=config_dir, post=post, posts=posts, clock=clock,
        transport=transport, tracker=tracker, run=run, submit=submit, submit_calls=submit_calls,
        campaign=campaign)


def rows(e, name="submissions"):
    return read_complete(e.root / (name + ".jsonl"))


def offers(e):
    return [r for r in rows(e, "aggregates") if r.get("submission_eligible") is True]


def history(e, intent):
    return [r for r in rows(e) if r.get("intent_id") == intent["intent_id"]]


def revise(e, change="text"):
    if change == "text":
        e.post["question"]["resolution_criteria"] = "Revised synthetic resolution criteria"
    elif change == "options":
        e.post["question"].update(type="multiple_choice", options=["A", "B"])
    else:
        e.post["question"]["scheduled_close_time"] = to_iso(NOW + timedelta(minutes=59))


def stop_after_publish(e):
    # Set STOP at the callable boundary: cycle has already published its offer.
    def submit(project, seconds):
        (e.config / "STOP_SUBMIT").touch()
        return e.submit(project, seconds)
    e.run(submit=submit)
    assert len(offers(e)) == 1
    assert not e.transport.calls
    assert not any(r["status"] == "intent" for r in rows(e))
    (e.config / "STOP_SUBMIT").unlink()


def test_local_published_dry_runs_and_local_final_rejections_are_once_only(integrated):
    e = integrated
    (e.config / "submit_mode.json").write_text(json.dumps({"mode": "local"}))
    other = copy.deepcopy(e.post)
    other["id"], other["question"]["id"] = 1101, 101
    e.posts.append(other)
    e.run()
    assert len(offers(e)) == 2
    finals = [r for r in rows(e, "aggregates") if r.get("submission_eligible") is False]
    assert len(finals) == 2
    for aggregate in [*offers(e), *finals]:
        events = [r for r in rows(e) if r["aggregate_id"] == aggregate["record_id"]]
        expected = "dry_run" if aggregate["submission_eligible"] else "rejected_precheck"
        assert [r["status"] for r in events] == [expected]
        if expected == "rejected_precheck":
            assert events[0]["reason"] == "aggregate is not submission eligible"
    before = rows(e)
    e.run()
    assert rows(e) == before and len(e.tracker["calls"]) == 2
    assert e.transport.calls == []
    assert Q.committed_questions(e.root) == set()


@pytest.mark.parametrize("policy_version", ["continuous_v2", Q.VERSION])
def test_trial_publishes_submits_and_freezes_next_cycle(integrated, policy_version):
    e = integrated
    e.campaign["version"] = policy_version
    e.run()
    offer, = offers(e)
    intent, = [r for r in rows(e) if r["status"] == "intent"]
    assert intent["aggregate_id"] == offer["record_id"]
    assert intent["question_snapshot_id"] == offer["question_snapshot_id"]
    comment, = [r for r in rows(e, "comments") if r["aggregate_id"] == offer["record_id"]]
    assert intent["comment_hash"] == comment["text_sha256"]
    assert [r["status"] for r in history(e, intent)] == [
        "intent", "request_started", "forecast_ok", "request_started", "comment_ok"]
    writes = e.transport.writes
    assert [w[0] for w in writes] == ["forecast", "comment"]
    assert writes[0][1] == [{"question": 100, "probability_yes": offer["probability"]}]
    assert writes[1][1] == {"on_post": 1100, "text": comment["text"],
                            "is_private": True, "included_forecast": True}
    before = rows(e)
    e.run()
    assert rows(e) == before and len(writes) == 2 and len(e.tracker["calls"]) == 1
    assert rows(e, "question_queue")[-1]["action"] == "already_submitted"
    assert Q.committed_questions(e.root) == {100}


def test_revision_after_publication_before_intent_submits_only_new_snapshot(integrated):
    e = integrated
    stop_after_publish(e)
    old, = offers(e)
    before = (e.root / "aggregates.jsonl").read_bytes()
    revise(e)
    result = e.run()
    assert result["revised_question_ids"] == [100]
    assert result["steps"]["on_finalized"]["calls"][0]["publish"]["stale_aggregate_ids"] == [old["record_id"]]
    new = offers(e)[-1]
    assert new["question_snapshot_id"] != old["question_snapshot_id"]
    invalidated, = [r for r in rows(e, "question_queue") if r["type"] == "snapshot_invalidated"]
    assert (invalidated["old_snapshot_id"], invalidated["new_snapshot_id"]) == (
        old["question_snapshot_id"], new["question_snapshot_id"])
    assert len(e.tracker["calls"]) == 2 and all(c[1] == "tier1" for c in e.tracker["calls"])
    assert (e.root / "aggregates.jsonl").read_bytes().startswith(before)
    assert any(r["aggregate_id"] == old["record_id"] and r["status"] == "rejected_precheck"
               and r["reason"] == "question snapshot invalidated" for r in rows(e))
    intent, = [r for r in rows(e) if r["status"] == "intent"]
    assert intent["aggregate_id"] == new["record_id"]
    assert history(e, intent)[-1]["status"] == "comment_ok"
    e.run()
    assert len(e.transport.writes) == 2 and len(e.tracker["calls"]) == 2


@pytest.mark.parametrize("audited_snapshot", ["old", "new"])
def test_revised_offer_rechecks_late_audit_with_full_history(integrated, audited_snapshot):
    e = integrated
    stop_after_publish(e)
    old, = offers(e)
    revise(e)

    def submit(project, seconds):
        new = offers(e)[-1]
        assert new["question_snapshot_id"] != old["question_snapshot_id"]
        audited = old if audited_snapshot == "old" else new
        if not any(r.get("audit_event_id") == "late" for r in rows(e, "audits")):
            append_record(e.root / "audits.jsonl", {"type": "audit_update", "audit_event_id": "late",
                "attempt_id": audited["input_attempt_ids"][0], "audited_at": to_iso(e.clock[0]),
                "audit_flags": ["leak_confirmed"], "audit_reasons": ["synthetic"]})
        return e.submit(project, seconds)

    e.run(submit=submit)
    new = offers(e)[-1]
    events = [r for r in rows(e) if r["aggregate_id"] == new["record_id"]]
    if audited_snapshot == "old":
        assert events[-1]["status"] == "comment_ok" and len(e.transport.writes) == 2
    else:
        assert [r["status"] for r in events] == ["rejected_precheck"]
        assert events[0]["reason"] == "adopted inputs no longer match this aggregate"
        assert not e.transport.writes


@pytest.mark.parametrize("change", ["text", "close_time"])
def test_revision_after_intent_before_request_abandons_then_requeues(integrated, change):
    e = integrated
    # First identity read allows intent; second fails before request_started.
    e.transport.account_effects = ["ok", "error", "error"]
    e.run()
    old, = [r for r in rows(e) if r["status"] == "intent"]
    assert [r["status"] for r in history(e, old)] == ["intent", "rejected_precheck"]
    assert Q.committed_questions(e.root) == {100} and not e.transport.writes
    revise(e, change)
    e.run()
    assert history(e, old)[-1]["status"] == "abandoned"
    assert Q.committed_questions(e.root) == set()
    assert not e.transport.writes
    # The cycle queues before submit resumes the old intent; recovery is next cycle.
    e.run()
    intents = [r for r in rows(e) if r["status"] == "intent"]
    assert len(intents) == 2
    new = intents[-1]
    assert new["question_snapshot_id"] != old["question_snapshot_id"]
    assert history(e, new)[-1]["status"] == "comment_ok"
    assert Q.committed_questions(e.root) == {100}
    assert len(e.tracker["calls"]) == 2 and all(c[1] == "tier1" for c in e.tracker["calls"])
    assert len(e.transport.writes) == 2
    assert sum(r["status"] == "abandoned" for r in rows(e)) == 1
    invalidated = [r for r in rows(e, "question_queue") if r["type"] == "snapshot_invalidated"
                   and r["old_snapshot_id"] == old["question_snapshot_id"]]
    assert len(invalidated) == 1
    e.run()
    assert len(e.transport.writes) == 2 and len(e.tracker["calls"]) == 2


@pytest.mark.parametrize("read_effect", ["empty", "error"])
@pytest.mark.parametrize("change", ["text", "close_time"])
def test_revision_with_unknown_forecast_only_reconciles_and_stays_frozen(integrated, read_effect, change):
    e = integrated
    e.transport.effects["forecast"] = ["timeout_after"]
    e.transport.read_effects = [read_effect]  # End-of-cycle reconcile is also unavailable.
    e.run()
    intent, = [r for r in rows(e) if r["status"] == "intent"]
    assert history(e, intent)[-1]["status"] == "unknown"
    request_id = e.transport.writes[0][2]
    revise(e, change)
    e.transport.read_effects = [read_effect, read_effect]
    e.run()
    before = rows(e)
    e.run()
    assert rows(e) == before  # Identical unknown results are deduplicated, reads are not.
    assert e.transport.reads == [("forecast", request_id)] * 3
    assert len(e.transport.writes) == 1 and len(e.tracker["calls"]) == 1
    assert Q.committed_questions(e.root) == {100}
    assert not any(r["status"] == "abandoned" for r in rows(e))
    assert not any(r["type"] == "snapshot_invalidated" for r in rows(e, "question_queue"))
    assert rows(e, "question_queue")[-1]["action"] == "already_submitted"
    e.run()  # The original accepted forecast finally becomes visible.
    assert [w[0] for w in e.transport.writes] == ["forecast", "comment"]
    assert history(e, intent)[-1]["status"] == "comment_ok"
    assert len(offers(e)) == 1 and len(e.tracker["calls"]) == 1


def test_text_revision_too_late_preserves_old_binding_and_marks_fallback(integrated):
    e = integrated
    e.post["question"]["scheduled_close_time"] = to_iso(NOW + timedelta(hours=1))
    (e.config / "STOP_SUBMIT").touch()
    e.run()
    old, = rows(e, "questions")
    assert not offers(e)
    e.clock[0] = NOW + timedelta(minutes=34)
    revise(e)
    (e.config / "STOP_SUBMIT").unlink()
    e.run()
    offer, = offers(e)
    assert offer["revision_fallback"] is True
    assert offer["question_snapshot_id"] == old["snapshot_id"]
    intent, = [r for r in rows(e) if r["status"] == "intent"]
    assert intent["question_snapshot_id"] == old["snapshot_id"]
    started, = [r for r in history(e, intent) if r["status"] == "request_started" and r["operation"] == "forecast"]
    assert started["text_revised"] is True
    assert history(e, intent)[-1]["status"] == "comment_ok"
    assert rows(e, "question_queue")[-1]["action"] == "text_fallback"
    assert not any(r["type"] == "snapshot_invalidated" for r in rows(e, "question_queue"))
    e.run()
    assert len(e.tracker["calls"]) == 1 and len(e.transport.writes) == 2


@pytest.mark.parametrize("change", ["options", "close_time"])
def test_structural_revision_too_late_rejects_published_result(integrated, change):
    e = integrated
    e.post["question"]["scheduled_close_time"] = to_iso(NOW + timedelta(hours=1))
    stop_after_publish(e)
    old, = offers(e)
    e.clock[0] = NOW + timedelta(minutes=34)
    revise(e, change)
    e.run()
    assert rows(e, "question_queue")[-1]["action"] == "blocked_revision"
    rejected = [r for r in rows(e) if r["aggregate_id"] == old["record_id"]]
    assert rejected[-1]["status"] == "rejected_precheck"
    assert rejected[-1]["reason"] == "question snapshot invalidated"
    before = rows(e)
    e.run()
    assert rows(e) == before
    assert not e.transport.calls and not any(r["status"] == "intent" for r in rows(e))
    assert len(e.tracker["calls"]) == 1


def test_deadline_publishes_and_submits_while_another_model_is_running(integrated, monkeypatch):
    import threading
    import forecast as F
    from test_scheduler import Adapter

    e = integrated
    enabled = dict(claude=True, gemini=False, gpt=True)
    (e.config / "players_enabled.json").write_text(json.dumps(enabled))
    e.post["question"]["scheduled_close_time"] = to_iso(NOW + timedelta(seconds=2100))
    def preflight(player, tier, adapter):
        return F.Preflight(player, tier=tier, problems=["synthetic paused line"] if player == "claude" else [])
    # Simulate a process interruption during idle waiting, after the first answer.
    # The new scheduler otherwise stays alive and submits this question itself.
    from test_submit import Crash
    def interrupted_sleep(seconds):
        raise Crash()
    with pytest.raises(Crash):
        e.run(preflight=preflight, sleep=interrupted_sleep)
    assert not offers(e)  # B has one valid line, but still waits for the paused line.
    other = copy.deepcopy(e.post)
    other["id"], other["question"]["id"] = 1101, 101
    other["question"]["scheduled_close_time"] = to_iso(NOW + timedelta(hours=3))
    e.posts.append(other)
    e.clock[0] = NOW + timedelta(seconds=250)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_run, original_wait = Adapter.run, scheduler.wait
    wakeups = []

    def model(self, req, **kwargs):
        assert req.schema["properties"]["question_id"]["const"] == 101
        started.set()
        assert release.wait(3)
        e.clock[0] = NOW + timedelta(seconds=850)
        result = original_run(self, req, **kwargs)
        finished.set()
        return result

    def wait(active, *, timeout, return_when):
        assert started.wait(2)
        wakeups.append(timeout)
        if not release.is_set():
            assert timeout == 50
            assert all(not f.done() for f in active)
            e.clock[0] += timedelta(seconds=timeout)
            return set(), set(active)
        return original_wait(active, timeout=2, return_when=return_when)

    def submit(project, seconds):
        if not release.is_set():
            assert not finished.is_set()
            assert e.clock[0] == NOW + timedelta(seconds=300)
            result = e.submit(project, seconds)
            assert [w[0] for w in e.transport.writes] == ["forecast", "comment"]
            assert history(e, next(r for r in rows(e) if r["status"] == "intent"))[-1]["status"] == "comment_ok"
            release.set()
            return result
        return e.submit(project, seconds)

    monkeypatch.setattr(Adapter, "run", model)
    monkeypatch.setattr(scheduler, "wait", wait)
    try:
        result = e.run(preflight=preflight, submit=submit)
    finally:
        release.set()
    assert finished.is_set() and wakeups[0] == 50
    assert result["steps"]["scheduler"]["jobs_completed"] == 1
    call, = result["steps"]["on_finalized"]["calls"]
    assert call["question_ids"] == [100] and call["status"] == "ok"
    assert call["publish"]["aggregate_ids"] == [offers(e)[0]["record_id"]]
    assert offers(e)[0]["completion_reason"] == "decision_deadline"
    assert all(r["at"] < to_iso(NOW + timedelta(seconds=600)) for r in rows(e) if r["status"] == "forecast_ok")
    attempts = rows(e, "forecasts")
    assert len(attempts) == 2 and attempts[-1]["question_id"] == 101
    assert attempts[-1]["validation_status"] == "valid"
    assert e.clock[0] > NOW + timedelta(seconds=600)
