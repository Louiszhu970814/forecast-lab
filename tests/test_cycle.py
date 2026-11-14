from __future__ import annotations

import copy
import plistlib
import signal
import subprocess
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest

import continuous as Q
import cycle as C
import forecast as F
import scheduler as S
from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import to_iso
from poll_questions import record_snapshots, latest_snapshots
from test_forecast import NOW, SNAP
from test_scheduler import environment, campaign


def policy():
    return {k: v for k, v in {**campaign(), "version": "continuous_v10",
                            "venues": ["bot_testing_area"]}.items() if k != "questions"}


def snapshot(qid=100, **kw):
    snap = {**SNAP, "question_id": qid, "post_id": qid + 1000,
            "ground": "bot_testing_area", "priority": "main", **kw}
    snap["content_hash"] = hash_obj({k: snap.get(k) for k in ("question_type", "title", "options", "resolution_criteria")})
    return snap


@pytest.fixture
def setup_cycle(environment, tmp_path, monkeypatch):
    obj, _, tracker, _ = environment(count=1)
    # Only the fake cycle adapter uses these disposable directories. Keep the
    # existing scheduler/workdir isolation tests unchanged.
    def extra(player):
        return {**obj.extra(player), "make_dir": lambda: Path(tempfile.mkdtemp(dir=tmp_path)),
                "remove_dir": shutil.rmtree}
    enabled = dict(claude=False, gemini=False, gpt=True)
    monkeypatch.setattr(C, "load_players_enabled", lambda: enabled)
    monkeypatch.setattr(S, "load_players_enabled", lambda: enabled)
    project = tmp_path / "project"
    (project / "config").mkdir(parents=True)
    clock = [NOW]
    current = [snapshot()]
    calls = []
    def poller():
        calls.append("poll")
        record_snapshots(current, log=tmp_path / "questions.jsonl", now=to_iso(clock[0]))
        latest = latest_snapshots(tmp_path / "questions.jsonl")
        return {s["question_id"]: latest[s["question_id"]] for s in current}
    def submit(project, seconds):
        assert seconds == C.SUBMIT_TIMEOUT_S
        # Submission owns a separate lock, including during scheduler.run().
        with F.Lock(tmp_path / "submit.lock"):
            calls.append("submit")
        return {"status": "ok"}
    def run(**kw):
        return C.run_cycle(tmp_path, policy(), poller, project=project, now=lambda: clock[0],
            adapter_loader=kw.pop("adapter_loader", obj.loader), preflight=kw.pop("preflight", obj.pf), context_extra=extra,
            acceptance_log=tmp_path / "acceptance.jsonl", submit=kw.pop("submit", submit),
            sleep=kw.pop("sleep", lambda seconds: clock.__setitem__(0, clock[0] + timedelta(seconds=seconds))), **kw)
    return run, current, clock, enabled, calls, tracker, project


def test_overlap_is_immediate_and_logged(setup_cycle, tmp_path):
    run, _, _, _, calls, tracker, _ = setup_cycle
    with F.Lock(tmp_path / "cycle.lock"):
        result = run()
    assert result["status"] == "overlap_skipped"
    assert not calls and not tracker["calls"]
    assert read_complete(tmp_path / "cycles.jsonl") == [result]


def test_scheduler_overlap_is_also_skipped(setup_cycle, tmp_path):
    run, *_ = setup_cycle
    with F.Lock(tmp_path / "scheduler.lock"):
        assert run()["status"] == "overlap_skipped"
    with F.Lock(tmp_path / "cycle.lock"):
        pass


def test_new_questions_append_resume_and_publish(setup_cycle, tmp_path):
    run, current, _, _, calls, tracker, _ = setup_cycle
    first = run()
    assert first["status"] == "complete", first
    assert first["new_question_ids"] == [100]
    assert first["players"]["claude"]["status"] == "disabled"
    assert len(tracker["calls"]) == 1
    finals = [a for a in read_complete(tmp_path / "aggregates.jsonl") if a.get("submission_eligible")]
    assert len(finals) == 1
    assert run()["new_question_ids"] == []
    assert len(tracker["calls"]) == 1
    current.append(snapshot(101))
    assert run()["new_question_ids"] == [101]
    assert len(tracker["calls"]) == 2 and calls.count("submit") == 5
    logs = read_complete(tmp_path / "cycles.jsonl")
    assert len(logs) == 3
    assert all(r["started_at"].endswith("Z") and r["finished_at"].endswith("Z") for r in logs)


def test_stop_submit_does_not_stop_research(setup_cycle, tmp_path):
    run, _, _, _, calls, tracker, project = setup_cycle
    (project / "config" / "STOP_SUBMIT").touch()
    result = run()
    assert result["steps"]["submit"]["status"] == "stopped"
    assert len(tracker["calls"]) == 1 and "submit" not in calls
    assert not any(a.get("submission_eligible") for a in read_complete(tmp_path / "aggregates.jsonl"))


def test_preflight_blocks_only_one_line(setup_cycle):
    run, _, _, enabled, _, tracker, _ = setup_cycle
    enabled["claude"] = True
    def pf(p, t, a):
        return F.Preflight(p, tier=t, problems=["CLI version and baseline changed"] if p == "claude" else [])
    result = run(preflight=pf)
    assert result["players"]["claude"]["status"] == "preflight_blocked"
    assert "baseline" in str(result["players"]["claude"])
    assert [p for p, _, _ in tracker["calls"]] == ["gpt"]


def test_preflight_exception_is_line_local(setup_cycle):
    run, _, _, enabled, _, tracker, _ = setup_cycle
    enabled["claude"] = True
    def pf(p, t, a):
        if p == "claude":
            raise RuntimeError("secret must not be logged")
        return F.Preflight(p, tier=t)
    result = run(preflight=pf)
    assert result["status"] == "complete"
    assert result["players"]["claude"]["reason"] == "RuntimeError"
    assert "secret" not in str(result)
    assert len(tracker["calls"]) == 1


def test_revision_invalidates_and_restarts_tier1_without_rewriting(setup_cycle, tmp_path):
    run, current, _, _, _, tracker, project = setup_cycle
    (project / "config" / "STOP_SUBMIT").touch()
    run()
    before = (tmp_path / "forecasts.jsonl").read_bytes()
    current[0]["title"] = "Revised title"
    current[0]["content_hash"] = "new-content"
    result = run()
    assert result["revised_question_ids"] == [100]
    assert len(tracker["calls"]) == 2
    assert (tmp_path / "forecasts.jsonl").read_bytes().startswith(before)
    attempts = read_complete(tmp_path / "forecasts.jsonl")
    assert len({r["question_snapshot_id"] for r in attempts}) == 2
    finals = [r for r in read_complete(tmp_path / "aggregates.jsonl") if r.get("completion_status") == "complete_local"]
    assert len(finals) == 2 and finals[-1]["status"] == "ready"
    assert finals[-1]["input_attempt_ids"] != finals[0]["input_attempt_ids"]
    assert any(r["type"] == "snapshot_invalidated" for r in read_complete(tmp_path / "question_queue.jsonl"))


def test_text_fallback_keeps_original_snapshot(setup_cycle, tmp_path):
    run, current, clock, _, _, tracker, project = setup_cycle
    (project / "config" / "STOP_SUBMIT").touch()
    current[0] = snapshot(scheduled_close_time=to_iso(NOW + timedelta(hours=1)))
    run()
    old = latest_snapshots(tmp_path / "questions.jsonl")[100]["snapshot_id"]
    clock[0] = NOW + timedelta(minutes=34)
    current[0].update(title="Text revision", content_hash="new")
    (project / "config" / "STOP_SUBMIT").unlink()
    result = run()
    assert result["status"] == "complete", result
    assert len(tracker["calls"]) == 1
    offer = next(r for r in read_complete(tmp_path / "aggregates.jsonl") if r.get("submission_eligible"))
    assert offer["question_snapshot_id"] == old and offer["revision_fallback"]
    assert read_complete(tmp_path / "question_queue.jsonl")[-1]["action"] == "text_fallback"
    run()
    assert len(tracker["calls"]) == 1


@pytest.mark.parametrize("change", [dict(question_type="multiple_choice", options=["A", "B"]),
                                    dict(options=["new"]),
                                    dict(scheduled_close_time=to_iso(NOW + timedelta(minutes=59)))])
def test_structural_revision_too_late_blocks(setup_cycle, tmp_path, change):
    run, current, clock, _, _, tracker, project = setup_cycle
    (project / "config" / "STOP_SUBMIT").touch()
    current[0] = snapshot(scheduled_close_time=to_iso(NOW + timedelta(hours=1)))
    run()
    clock[0] = NOW + timedelta(minutes=34)
    current[0].update(change, content_hash="structural")
    result = run()
    assert result["revised_question_ids"] == [100]
    assert read_complete(tmp_path / "question_queue.jsonl")[-1]["action"] == "blocked_revision"
    assert len(tracker["calls"]) == 1
    run()
    assert len(tracker["calls"]) == 1


@pytest.mark.parametrize("state", ["forecast_ok", "intent", "unknown", "future_unknown_state"])
def test_submitted_or_unknown_revision_only_records(setup_cycle, tmp_path, state):
    run, current, _, _, _, tracker, _ = setup_cycle
    run()
    append_record(tmp_path / "submissions.jsonl", {"status": "intent", "intent_id": "i1", "question_id": 100})
    append_record(tmp_path / "submissions.jsonl", {"status": state, "intent_id": "i1", "question_id": 100})
    current[0].update(title="revised", content_hash="rev")
    result = run()
    assert result["revised_question_ids"] == [100]
    assert read_complete(tmp_path / "question_queue.jsonl")[-1]["action"] == "already_submitted"
    assert len(tracker["calls"]) == 1


def test_published_stale_result_allows_new_snapshot_and_other_questions(setup_cycle, tmp_path):
    run, current, _, _, calls, tracker, _ = setup_cycle
    run()
    old = next(a for a in read_complete(tmp_path / "aggregates.jsonl") if a.get("submission_eligible"))
    current[0].update(title="revised", content_hash="rev")
    current.append(snapshot(101))
    result = run()
    assert len(tracker["calls"]) == 3
    assert result["steps"]["submit"]["status"] == "ok"
    assert calls.count("submit") == 5
    assert result["steps"]["publish"]["stale_aggregate_ids"] == [old["record_id"]]
    offers = [a for a in read_complete(tmp_path / "aggregates.jsonl") if a.get("submission_eligible")]
    assert len(offers) == 3
    assert offers[1]["question_id"] == 100 and offers[1]["question_snapshot_id"] != old["question_snapshot_id"]
    assert offers[2]["question_id"] == 101
    assert run()["steps"]["submit"]["status"] == "ok"
    assert len([a for a in read_complete(tmp_path / "aggregates.jsonl") if a.get("submission_eligible")]) == 3


def test_cycle_budget_defers_work_and_next_cycle_resumes(setup_cycle):
    run, _, clock, _, _, tracker, _ = setup_cycle
    result = run(deadline=clock[0])
    assert result["steps"]["scheduler"]["jobs_completed"] == 0
    assert not tracker["calls"]
    assert run()["steps"]["scheduler"]["jobs_completed"] == 1


def test_expired_decision_does_not_call_model(setup_cycle, tmp_path):
    run, current, _, _, _, tracker, _ = setup_cycle
    current[0] = snapshot(scheduled_close_time=to_iso(NOW + timedelta(minutes=24)))
    assert run()["status"] == "complete"
    assert not tracker["calls"]
    assert read_complete(tmp_path / "aggregates.jsonl")[-1]["completion_reason"] == "decision_deadline"


def test_window_and_caps_are_frozen(tmp_path):
    cfg = policy()
    Q.register(tmp_path, cfg, now=lambda: NOW)
    Q.register(tmp_path, cfg, now=lambda: NOW)
    changed = copy.deepcopy(cfg)
    changed["weekly_attempt_caps"]["gpt"] += 1
    with pytest.raises(ValueError, match="cannot change"):
        Q.register(tmp_path, changed, now=lambda: NOW)
    changed["campaign_id"] = "renamed"
    with pytest.raises(ValueError, match="overlap"):
        Q.register(tmp_path, changed, now=lambda: NOW)
    with pytest.raises(ValueError, match="active"):
        Q.register(tmp_path, cfg, now=lambda: NOW + timedelta(days=8))


def test_registered_caps_respect_quota_policy_ceilings(tmp_path):
    ceilings = {"claude": 130, "gpt": 130, "gemini": 200}
    for player in ceilings:
        cfg = policy()
        cfg["weekly_attempt_caps"] = {**ceilings, player: ceilings[player] + 1}
        with pytest.raises(ValueError, match="exceeds quota policy"):
            Q.register(tmp_path, cfg, now=lambda: NOW)
    assert not (tmp_path / "continuous_campaigns.jsonl").exists()
    cfg = policy()
    cfg["weekly_attempt_caps"] = dict(ceilings)
    Q.register(tmp_path, cfg, now=lambda: NOW)
    assert len(read_complete(tmp_path / "continuous_campaigns.jsonl")) == 1


def test_missing_submit_is_recorded(tmp_path):
    assert C.submit_cli(tmp_path, 1)["status"] == "unavailable"


def test_submit_cli_contract(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "submit.py").touch()
    calls = []
    def run(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, stdout='{"events": 0, "statuses": {}}')
    monkeypatch.setattr(C.subprocess, "run", run)
    assert C.submit_cli(tmp_path, 15)["status"] == "ok"
    argv, kw = calls[0]
    assert argv == [str(tmp_path / ".venv/bin/python"), str(tmp_path / "src/submit.py"), "run"]
    assert kw["timeout"] == 15


def test_supervisor_timeout_kills_worker_tree(monkeypatch):
    calls = []
    class Process:
        pid = 12345
        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("fake", timeout)
            return -9
    monkeypatch.setattr(C, "kill_worker_tree", calls.append)
    def popen(cmd, **kw):
        assert kw == {"start_new_session": True}
        return Process()
    assert C.supervise(["fake"], timeout_s=570, popen=popen) == (124, True)
    assert calls == [12345]


def test_tree_cleanup_includes_detached_children(monkeypatch):
    signals = []
    monkeypatch.setattr(C.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(C.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(C.subprocess, "check_output", lambda *a, **kw: "100 1\n200 100\n300 200\n999 1\n")
    C.kill_worker_tree(100)
    assert {p for p, sig in signals if sig == signal.SIGKILL} == {100, 200, 300}


def test_launchd_templates():
    root = Path(__file__).resolve().parents[1] / "launchd"
    configs = {p.stem.rsplit(".", 1)[-1]: plistlib.loads(p.read_bytes()) for p in root.glob("*.plist")}
    assert set(configs) == {"cycle", "telegram"}
    assert configs["cycle"]["StartInterval"] == 60
    # Forecast cycle uses run.sh/caffeinate; Telegram has its own resident service.
    assert configs["cycle"]["ProgramArguments"][:3] == ["/usr/bin/caffeinate", "-i", "/bin/sh"]


@pytest.fixture
def fake_api(tmp_path, monkeypatch):
    from test_poll_questions import _post
    cfg = {"tournaments": {"main": {"id": 1, "submit_whitelisted": False},
        "minibench": {"id": 2, "submit_whitelisted": False},
        "bot_testing_area": {"id": 32977, "submit_whitelisted": False}},
        "trial_ground_types": ["bot_testing_area"]}
    monkeypatch.setattr(Q, "load_tournaments", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(Q, "discover_tournaments", lambda client, configured: configured)
    monkeypatch.setattr(Q, "load_submit_mode", lambda: {"mode": "trial"})
    class Client:
        def __init__(self):
            self.calls = []
            self.posts = {1: [_post(10, 20, "binary", "Tournament private title")], 2: [], 3: [],
                32977: [_post(10, 20, "binary", "Copy with different title"),
                        _post(11, 21, "binary", "Allowed"), _post(12, 22, "numeric", "Unsupported")]}
        def get(self, path):
            assert path == "projects/minibenches/"
            return [{"id": 2, "slug": "minibench", "is_ongoing": False, "start_date": to_iso(NOW + timedelta(days=1))},
                    {"id": 3, "slug": "previous", "is_ongoing": True}]
        def list_posts(self, project, **kwargs):
            self.calls.append((project, kwargs))
            return self.posts[project]
    client = Client()
    return client, cfg


def test_poller_uses_metadata_exclusions_and_saves_only_trial_questions(fake_api, tmp_path, question_type_flags):
    question_type_flags(False)  # Exercise unsupported types independently of production rollout.
    client, _ = fake_api
    poller = Q.Poller(client, tmp_path, policy(), now=lambda: NOW)
    snapshots = poller()
    assert set(snapshots) == {21, 22}
    assert all(kw["statuses"] == ["open", "upcoming"] for project, kw in client.calls if project != 32977)
    assert {project for project, _ in client.calls} == {1, 2, 3, 32977}
    assert "Tournament private title" not in (tmp_path / "questions.jsonl").read_text()
    assert "aggregations" not in (tmp_path / "questions.jsonl").read_text()
    queue = Q.Queue(tmp_path, {**policy(), "questions": []}, poller, now=lambda: NOW)
    assert set(queue(force=True)) == {21}
    assert read_complete(tmp_path / "question_queue.jsonl")[-1]["action"] == "unsupported_type"


def test_poller_rejects_unverified_project_membership(fake_api, tmp_path):
    client, _ = fake_api
    client.posts[32977][1]["projects"] = {}
    with pytest.raises(F.PreflightError, match="membership"):
        Q.Poller(client, tmp_path, policy(), now=lambda: NOW)()


def test_trial_refuses_competition_content(fake_api, tmp_path):
    client, _ = fake_api
    with pytest.raises(F.PreflightError, match="trial grounds"):
        Q.Poller(client, tmp_path, {**policy(), "venues": ["main"]})()
    assert not client.calls


def test_live_requires_whitelist(fake_api, tmp_path, monkeypatch):
    client, cfg = fake_api
    monkeypatch.setattr(Q, "load_submit_mode", lambda: {"mode": "live"})
    with pytest.raises(F.PreflightError, match="whitelisted"):
        Q.Poller(client, tmp_path, policy())()
    cfg["tournaments"]["bot_testing_area"]["submit_whitelisted"] = True
    assert Q.Poller(client, tmp_path, policy())()


def test_poll_error_is_logged_and_does_not_submit(setup_cycle, tmp_path, monkeypatch):
    run, _, _, _, calls, tracker, _ = setup_cycle
    def broken(self, *, force=False):
        raise RuntimeError("secret")
    monkeypatch.setattr(Q.Queue, "__call__", broken)
    result = run()
    assert result["status"] == "error"
    assert result["steps"]["poll"]["status"] == "error"
    assert result["steps"]["submit"]["status"] == "skipped"
    assert result["errors"] == [{"type": "RuntimeError", "step": "poll"}]
    assert not tracker["calls"] and "submit" not in calls
    assert read_complete(tmp_path / "cycles.jsonl") == [result]


def test_started_attempt_keeps_full_timeout_and_submits_after_admission_close(legacy_forecast_timeouts, setup_cycle, tmp_path, monkeypatch):
    run, _, clock, _, calls, tracker, _ = setup_cycle
    original = S.Scheduler.execute
    deadlines = []
    def execute(self, job):
        loader = self.loader
        def load(p):
            adapter = loader(p)
            original_run = adapter.run
            def invoke(req, **kw):
                deadlines.append(req.timeout_s)
                assert req.timeout_s == 600
                clock[0] += timedelta(seconds=600)
                # The active cycle keeps its lock even across the next scheduled start.
                assert run()["status"] == "overlap_skipped"
                return original_run(req, **kw)
            adapter.run = invoke
            return adapter
        self.loader = load
        return original(self, job)
    monkeypatch.setattr(S.Scheduler, "execute", execute)
    result = run(deadline=clock[0] + timedelta(seconds=1))
    assert result["status"] == "complete", result
    assert deadlines and len(tracker["calls"]) == 1
    assert result["steps"]["on_finalized"]["calls"][0]["publish"]["aggregate_ids"] and calls.count("submit") == 2
    attempt = read_complete(tmp_path / "forecasts.jsonl")[0]
    assert attempt["timeout_s"] == 600 and attempt["validation_status"] == "valid"
    assert attempt["decision_deadline_utc"] != result["admission_deadline"]


def test_revision_scopes_tier2_bundles_and_all_three_lines(setup_cycle, tmp_path, monkeypatch):
    run, current, _, enabled, _, tracker, project = setup_cycle
    enabled.update(claude=True, gemini=True)
    (project / "config" / "STOP_SUBMIT").touch()
    assert run()["status"] == "complete"
    assert len(tracker["calls"]) == 6
    current[0].update(title="revised", content_hash="revision")
    result = run()
    assert result["status"] == "complete", result
    assert len(tracker["calls"]) == 12
    bundles = read_complete(tmp_path / "reconciliation_inputs.jsonl")
    assert len(bundles) == 6
    assert len({b["question_snapshot_id"] for b in bundles}) == 2
    finals = [r for r in read_complete(tmp_path / "aggregates.jsonl") if r.get("completion_status") == "complete_local"]
    assert len(finals) == 2 and all(f["status"] == "ready" for f in finals)
    assert set(finals[0]["input_attempt_ids"]).isdisjoint(finals[1]["input_attempt_ids"])


def test_text_fallback_finalizes_partial_unsealed_answers(setup_cycle, tmp_path):
    run, current, clock, enabled, _, tracker, project = setup_cycle
    enabled["claude"] = True
    current[0] = snapshot(scheduled_close_time=to_iso(NOW + timedelta(hours=1)))
    def pf(p, t, a):
        return F.Preflight(p, tier=t, problems=["baseline changed"] if p == "claude" else [])
    run(preflight=pf)
    assert not read_complete(tmp_path / "aggregates.jsonl")
    clock[0] = NOW + timedelta(minutes=34)
    current[0].update(title="text revision", content_hash="revision")
    result = run(preflight=pf)
    assert result["status"] == "complete", result
    offers = [r for r in read_complete(tmp_path / "aggregates.jsonl") if r.get("submission_eligible")]
    assert len(offers) == 1 and offers[0]["revision_fallback"]
    assert len(tracker["calls"]) == 1


def test_deadline_reached_during_work_leaves_new_question_pending(setup_cycle, monkeypatch):
    run, current, clock, _, _, tracker, _ = setup_cycle
    current.append(snapshot(101))
    execute = S.Scheduler.execute
    def finish_at_limit(self, job):
        result = execute(self, job)
        clock[0] = NOW + timedelta(seconds=510)
        return result
    monkeypatch.setattr(S.Scheduler, "execute", finish_at_limit)
    assert run()["steps"]["scheduler"]["jobs_completed"] == 1
    assert [qid for _, _, qid in tracker["calls"]] == [100]
    monkeypatch.setattr(S.Scheduler, "execute", execute)
    assert run()["steps"]["scheduler"]["jobs_completed"] == 1
    assert [qid for _, _, qid in tracker["calls"]] == [100, 101]


def test_supervisor_recovers_progress_into_one_cycle_summary(legacy_forecast_timeouts, tmp_path, monkeypatch):
    monkeypatch.setattr(C, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(C, "utc_now", lambda: NOW)
    def supervise(command, **kw):
        cycle_id = command[command.index("--cycle-id") + 1]
        append_record(tmp_path / "data/cycle_progress.jsonl", {"type": "cycle_progress", "cycle_id": cycle_id,
            "active_step": "scheduler", "summary": {"type": "cycle", "cycle_id": cycle_id,
            "started_at": to_iso(NOW), "steps": {"poll": {"status": "ok"}},
            "new_question_ids": [100], "revised_question_ids": [], "errors": []}})
        assert kw["timeout_s"] == 5100
        return 124, True
    monkeypatch.setattr(C, "supervise", supervise)
    assert C.main(["--campaign", str(tmp_path / "fake.json")]) == 124
    rows = read_complete(tmp_path / "data/cycles.jsonl")
    assert len(rows) == 1 and rows[0]["status"] == "timeout"
    assert rows[0]["new_question_ids"] == [100]
    assert rows[0]["steps"]["scheduler"]["status"] == "interrupted"
    assert rows[0]["timing_limits"]["supervisor_timeout_s"] == 5110


def test_tree_cleanup_failure_still_kills_stopped_worker(monkeypatch):
    signals = []
    monkeypatch.setattr(C.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(C.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    def failed(*args, **kwargs):
        raise OSError("ps unavailable")
    monkeypatch.setattr(C.subprocess, "check_output", failed)
    with pytest.raises(OSError):
        C.kill_worker_tree(100)
    assert (100, signal.SIGKILL) in signals


def test_multiple_trial_venues_do_not_create_false_revisions(fake_api, tmp_path, monkeypatch):
    client, cfg = fake_api
    cfg["trial_ground_types"].append("main_site_questions")
    cfg["tournaments"]["main_site_questions"] = {"id": 40000, "submit_whitelisted": False}
    client.posts[40000] = copy.deepcopy(client.posts[32977][1:])
    for post in client.posts[32977][1:] + client.posts[40000]:
        post["projects"]["site_main"] = [{"id": 40000}]
    original_get = client.get
    monkeypatch.setattr(client, "get", lambda path, params=None:
                        {"results": client.posts[40000], "next": None} if path == "posts/" else original_get(path))
    poller = Q.Poller(client, tmp_path, {**policy(), "venues": ["main_site_questions", "bot_testing_area"],
        "main_site_trial": {"version": Q.MAIN_SITE_VERSION, "max_questions": 4, "max_pages": 1}}, now=lambda: NOW)
    first, second = poller(), poller()
    assert set(first) == set(second) == {21, 22}
    assert all(first[q]["snapshot_id"] == second[q]["snapshot_id"] for q in first)
    assert len(read_complete(tmp_path / "questions.jsonl")) == 2


def test_unmeasured_tier2_can_run_longer_than_cycle_interval(legacy_forecast_timeouts, setup_cycle, tmp_path, monkeypatch):
    from test_scheduler import Adapter
    run, _, clock, _, calls, tracker, _ = setup_cycle
    original = Adapter.run
    def invoke(self, req, **kw):
        result = original(self, req, **kw)
        if req.profile == 'tier1':
            result.structured['escalation_flags'] = ['evidence_conflict']
        else:
            assert req.profile == 'tier2' and req.timeout_s == 1200
            clock[0] += timedelta(seconds=700)
        return result
    monkeypatch.setattr(Adapter, 'run', invoke)
    result = run()
    assert result['status'] == 'complete', result
    assert [tier for _, tier, _ in tracker['calls']] == ['tier1', 'tier2']
    jobs = read_complete(tmp_path / 'scheduler_jobs.jsonl')
    assert jobs[-1]['tier'] == 2 and jobs[-1]['estimated_completion_s'] == 1260
    assert result['steps']['on_finalized']['calls'][0]['publish']['aggregate_ids'] and calls.count('submit') == 2
    assert result['timing_limits'] == C.timing_limits(F.load_run_config())


@pytest.mark.parametrize('tier', [1, 2])
def test_admission_close_defers_rerun_without_consuming_it(setup_cycle, tmp_path, monkeypatch, tier):
    from test_scheduler import Adapter
    run, _, clock, _, _, tracker, _ = setup_cycle
    original = Adapter.run
    failed = []
    def invoke(self, req, **kw):
        result = original(self, req, **kw)
        if tier == 2 and req.profile == 'tier1':
            result.structured['escalation_flags'] = ['evidence_conflict']
        if req.profile == f'tier{tier}' and not failed:
            failed.append(True)
            clock[0] += timedelta(seconds=req.timeout_s)
            result.completed, result.error_type, result.structured = False, 'timeout', None
        return result
    monkeypatch.setattr(Adapter, 'run', invoke)
    first = run()
    assert first['status'] == 'complete'
    rows = [r for r in read_complete(tmp_path / 'forecasts.jsonl') if r['tier'] == tier]
    assert [r['attempt_kind'] for r in rows] == ['initial']
    job = read_complete(tmp_path / 'scheduler_jobs.jsonl')[-1]
    assert job['outcome'] == 'stopped_waiting' and job['reason'] == 'cycle time limit'
    assert run()['status'] == 'complete'
    rows = [r for r in read_complete(tmp_path / 'forecasts.jsonl') if r['tier'] == tier]
    assert [r['attempt_kind'] for r in rows] == ['initial', 'rerun']
    assert rows[-1]['validation_status'] == 'valid'


@pytest.mark.parametrize('timeouts', [dict(tier1=600, tier2=1200, repair=300),
                                      dict(tier1=1800, tier2=900, repair=80)])
def test_supervisor_limit_uses_configured_full_chain(timeouts):
    cfg = {**F.load_run_config(), 'timeouts_s': timeouts}
    limits = C.timing_limits(cfg)
    assert limits['worst_job_s'] == 2 * (max(timeouts['tier1'], timeouts['tier2']) + timeouts['repair']) + 8 * F.QUOTA_QUERY_S
    assert limits['handoff_s'] >= 510 + limits['worst_job_s'] + 600
    assert (limits['handoff_s'] + 30) % C.INTERVAL_S == 0
    assert limits['supervisor_timeout_s'] == limits['handoff_s'] + 30 + 300 + 10
    assert limits['worker_timeout_s'] == limits['supervisor_timeout_s'] - 10


@pytest.mark.parametrize('status', ['dry_run', 'rejected_precheck', 'forecast_ok', 'unknown',
                                   'future_unknown_state', 'abandoned'])
def test_submission_status_without_intent_does_not_commit(tmp_path, status):
    append_record(tmp_path / 'submissions.jsonl', {'question_id': 100, 'intent_id': 'i1', 'status': status})
    assert Q.committed_questions(tmp_path) == set()


def test_commit_is_scoped_to_each_non_abandoned_intent(tmp_path):
    log = tmp_path / 'submissions.jsonl'
    for iid in ('i1', 'i2'):
        append_record(log, {'question_id': 100, 'intent_id': iid, 'status': 'intent'})
    append_record(log, {'intent_id': 'i1', 'status': 'abandoned'})
    assert Q.committed_questions(tmp_path) == {100}
    append_record(log, {'intent_id': 'i2', 'status': 'abandoned'})
    assert Q.committed_questions(tmp_path) == set()
    append_record(log, {'question_id': 100, 'intent_id': 'i3', 'status': 'intent'})
    append_record(log, {'intent_id': 'i3', 'status': 'forecast_ok'})
    assert Q.committed_questions(tmp_path) == {100}


@pytest.mark.parametrize('observe_before_abandon', [False, True])
def test_abandoned_intent_requeues_new_snapshot(setup_cycle, tmp_path, observe_before_abandon):
    run, current, _, _, calls, tracker, _ = setup_cycle
    run()
    log = tmp_path / 'submissions.jsonl'
    append_record(log, {'question_id': 100, 'intent_id': 'i1', 'status': 'intent'})
    current[0].update(title='revised', content_hash='new')
    if observe_before_abandon:
        run()
        assert len(tracker['calls']) == 1
        assert read_complete(tmp_path / 'question_queue.jsonl')[-1]['action'] == 'already_submitted'
    append_record(log, {'intent_id': 'i1', 'status': 'abandoned'})
    result = run()
    assert result['status'] == 'complete' and result['steps']['on_finalized']['calls'][0]['publish']['aggregate_ids']
    assert len(tracker['calls']) == 2
    assert read_complete(tmp_path / 'question_queue.jsonl')[-1]['action'] == 'requeued'
    offers = [r for r in read_complete(tmp_path / 'aggregates.jsonl') if r.get('submission_eligible')]
    assert len({r['question_snapshot_id'] for r in offers}) == 2
    invalidated = {r['old_snapshot_id'] for r in read_complete(tmp_path / 'question_queue.jsonl')
                   if r['type'] == 'snapshot_invalidated'}
    assert offers[0]['question_snapshot_id'] in invalidated
    assert result['steps']['publish']['stale_aggregate_ids'] == [offers[0]['record_id']]


def test_publish_never_offers_invalidated_snapshot(setup_cycle, tmp_path, monkeypatch):
    run, _, _, _, _, _, project = setup_cycle
    (project / 'config' / 'STOP_SUBMIT').touch()
    run()
    original = C.publish
    def publish(root, scheduler, snapshots, queue):
        queue.invalidated.update(s['snapshot_id'] for s in snapshots.values())
        return original(root, scheduler, snapshots, queue)
    monkeypatch.setattr(C, 'publish', publish)
    (project / 'config' / 'STOP_SUBMIT').unlink()
    result = run()
    assert result['steps']['publish']['aggregate_ids'] == []
    assert not any(r.get('submission_eligible') for r in read_complete(tmp_path / 'aggregates.jsonl'))


@pytest.mark.parametrize('tier', [1, 2])
@pytest.mark.parametrize('after_rerun', [False, True])
def test_pending_format_repair_resumes_once_in_next_cycle(setup_cycle, tmp_path, monkeypatch, tier, after_rerun):
    from test_scheduler import Adapter
    run, _, clock, _, _, _, _ = setup_cycle
    original = Adapter.run
    base_calls = []
    def invoke(self, req, **kw):
        result = original(self, req, **kw)
        if tier == 2 and req.profile == 'tier1':
            result.structured['escalation_flags'] = ['evidence_conflict']
        if req.profile == f'tier{tier}':
            base_calls.append(req)
            if after_rerun and len(base_calls) == 1:
                result.completed, result.error_type, result.structured = False, 'timeout', None
            else:
                result.structured.pop('key_factors')
                clock[0] += timedelta(seconds=req.timeout_s)
        if req.profile == 'format_repair' and tier == 2:
            result.structured['change_reason'] = 'The verified evidence resolves the dispute.'
        return result
    monkeypatch.setattr(Adapter, 'run', invoke)
    assert run()['status'] == 'complete'
    rows = [r for r in read_complete(tmp_path / 'forecasts.jsonl') if r['tier'] == tier]
    kinds = ['initial', 'rerun'] if after_rerun else ['initial']
    assert [r['attempt_kind'] for r in rows] == kinds
    assert rows[-1]['repair_basis'] is not None
    assert run()['status'] == 'complete'
    rows = [r for r in read_complete(tmp_path / 'forecasts.jsonl') if r['tier'] == tier]
    assert [r['attempt_kind'] for r in rows] == kinds + ['format_repair']
    assert rows[-1]['validation_status'] == 'valid'
    assert rows[-1]['prev_attempt_id'] == rows[-2]['attempt_id']
    assert run()['steps']['scheduler']['jobs_completed'] == 0


def test_abandoned_unchanged_intent_does_not_invalidate_its_own_snapshot(setup_cycle, tmp_path):
    run, _, _, _, _, tracker, _ = setup_cycle
    run()
    log = tmp_path / 'submissions.jsonl'
    append_record(log, {'question_id': 100, 'intent_id': 'i1', 'status': 'intent'})
    run()
    append_record(log, {'intent_id': 'i1', 'status': 'abandoned'})
    result = run()
    assert result['steps']['submit']['status'] == 'ok'
    assert not result['steps']['publish']['stale_aggregate_ids']
    assert len(tracker['calls']) == 1
    assert not any(r['type'] == 'snapshot_invalidated' for r in read_complete(tmp_path / 'question_queue.jsonl'))


@pytest.mark.parametrize("outcome", ["busy", "timeout"])
def test_finalized_submit_failure_is_recorded_and_cycle_continues(setup_cycle, tmp_path, outcome):
    run, _, clock, _, _, tracker, _ = setup_cycle
    calls = []
    def submit(project, seconds):
        calls.append(seconds)
        if len(calls) == 1:
            clock[0] += timedelta(seconds=2)
            if outcome == "timeout":
                raise subprocess.TimeoutExpired("synthetic", seconds)
            return {"status": "busy", "returncode": C.BUSY_EXIT_CODE}
        return {"status": "ok"}
    result = run(submit=submit)
    assert result["status"] == "complete" and len(tracker["calls"]) == 1
    assert calls == [300, 300]
    hook = result["steps"]["on_finalized"]
    assert hook["count"] == 1
    call, = hook["calls"]
    assert call["status"] == ("error" if outcome == "timeout" else "busy")
    assert call["elapsed_s"] == 2 and call["publish"]["aggregate_ids"]
    assert read_complete(tmp_path / "cycles.jsonl") == [result]
    progress = read_complete(tmp_path / "cycle_progress.jsonl")
    assert any(r["summary"]["steps"]["on_finalized"]["calls"] and
               r["summary"]["steps"]["on_finalized"]["calls"][0].get("elapsed_s") == 2 for r in progress)


def test_stop_submit_records_hook_without_invoking_submit(setup_cycle):
    run, _, _, _, calls, _, project = setup_cycle
    (project / "config" / "STOP_SUBMIT").touch()
    result = run()
    hook = result["steps"]["on_finalized"]
    assert hook["count"] == 1
    assert hook["calls"][0]["submit"] == {"status": "stopped", "reason": "STOP_SUBMIT"}
    assert "submit" not in calls


def test_submit_cli_reports_busy_exit_code(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/submit.py").touch()
    monkeypatch.setattr(C.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, C.BUSY_EXIT_CODE))
    assert C.submit_cli(tmp_path, 120) == {"status": "busy", "returncode": C.BUSY_EXIT_CODE}


@pytest.mark.parametrize('status', sorted(C.SUBMISSION_STATES))
def test_submit_counts_surface_attention(tmp_path, monkeypatch, status):
    import json
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/submit.py').touch()
    counts = {'events': 2, 'statuses': {status: 2}}
    monkeypatch.setattr(C.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(counts)))
    result = C.submit_cli(tmp_path, 120)
    assert result['status'] == ('attention' if status in C.ATTENTION_STATES else 'ok')
    assert result['statuses'] == counts['statuses'] and result['events'] == 2


@pytest.mark.parametrize('stdout', ["{'events': 0}", '{}', '[]', 'null',
    '{"events": 1, "statuses": {}}', '{"events": 1, "statuses": {"secret": 1}}',
    '{"events": 1, "statuses": {"unknown": true}}', '{"events": -1, "statuses": {"unknown": -1}}'])
def test_submit_invalid_counts_are_error_without_echoing_stdout(tmp_path, monkeypatch, stdout):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/submit.py').touch()
    monkeypatch.setattr(C.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout))
    result = C.submit_cli(tmp_path, 120)
    assert result['status'] == 'error' and 'secret' not in str(result)


def test_callback_attention_survives_clean_final_submit(setup_cycle):
    run, *_ = setup_cycle
    results = iter([{'status': 'attention', 'events': 1, 'statuses': {'unknown': 1}}, {'status': 'ok'}])
    rec = run(submit=lambda *a: next(results))
    assert rec['status'] == 'attention'
    assert rec['steps']['on_finalized']['calls'][0]['submit']['statuses'] == {'unknown': 1}


def test_ops_report_recognizes_attention(tmp_path):
    import json
    from flab.ops_report import status_text
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/submit_mode.json').write_text(json.dumps({'mode': 'trial'}))
    (tmp_path / 'config/players_enabled.json').write_text(json.dumps(dict.fromkeys(C.PLAYERS, True)))
    append_record(tmp_path / 'data/cycles.jsonl', {'status': 'attention', 'finished_at': to_iso(NOW), 'secret': 'DO NOT DISPLAY'})
    text = status_text(tmp_path, now=NOW)
    assert 'attention' in text and 'DO NOT DISPLAY' not in text
