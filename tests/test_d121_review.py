"""Synthetic regression for Gemini's D121 review; never invoke a model."""
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import hashlib

import pytest

import forecast as F
import scheduler as S
from flab import orphan
from flab.config import load_run_config
from flab.jsonl import append_record, read_records
from flab.timeutil import to_iso
from test_budget import ledger, attempt, reserve
from test_d121_fail_soft import new_deep, recovery_context
from test_forecast import NOW, SNAP, FakeAdapter, _good, _qsnap
from test_scheduler import campaign, environment


def wait_row(reason, tier=2, qid=100):
    return {"type": "scheduler_job_finished", "record_id": f"wait-{qid}",
            "version": S.VERSION, "question_id": qid, "player": "gemini", "tier": tier,
            "outcome": "budget_wait", "budget_reason": reason,
            "finished_at": to_iso(NOW - timedelta(minutes=1)),
            "reevaluate_after": to_iso(NOW + timedelta(minutes=9)),
            "budget_window_end": campaign()["budget_window_end"]}


@pytest.mark.parametrize("cause", [
    "weekly_attempt_capacity_reserved_or_exhausted", "tier2_token_breaker",
    "weekly_tier2_token_breaker", "weekly_tier2_argon_breaker",
    "tier1_token_baseline_unmeasured", "unresolved_or_running_attempt",
])
def test_new_usage_does_not_release_nonquota_waits_or_loop(tmp_path, cause):
    clock = [NOW]
    s = S.Scheduler(tmp_path, campaign(), snapshots=lambda: {}, now=lambda: clock[0],
                    acceptance_log=tmp_path / "acceptance.jsonl",
                    context_extra=lambda _: {"quota_snapshot": lambda **kw: pytest.fail("unneeded usage query")})
    rows = [wait_row(cause, qid=q) for q in (100, 101)]
    if cause in S.BREAKER_WAIT_REASONS:
        append_record(s.budget.log, {"type": "budget_upgrade_stop", "player": "gemini",
                      "reason": cause, "until": to_iso(NOW + timedelta(hours=6))})
    for tick in range(5):
        clock[0] = NOW + timedelta(seconds=tick)
        append_record(tmp_path / "quota.jsonl", {"type": "quota_snapshot",
            **replace(_qsnap(.9), taken_at=to_iso(clock[0])).to_dict()})
        assert not s._reevaluate_waits(rows)
    assert not s.jobs_log.exists()
    if cause != "unresolved_or_running_attempt":
        clock[0] = NOW + timedelta(minutes=20)  # generic ten-minute retry cannot expire these
        assert not s._reevaluate_waits(rows)
    if cause in S.BREAKER_WAIT_REASONS:
        clock[0] = NOW + timedelta(hours=6)
        assert s._reevaluate_waits(rows) == {"wait-100", "wait-101"}
        assert len(read_records(s.jobs_log)) == 2
        assert s._reevaluate_waits(rows + read_records(s.jobs_log)) == {"wait-100", "wait-101"}
        assert len(read_records(s.jobs_log)) == 2


def test_tier2_new_snapshot_subtracts_same_protected_quota_as_budget(tmp_path, monkeypatch):
    s = S.Scheduler(tmp_path, campaign(), snapshots=lambda: {}, now=lambda: NOW,
                    acceptance_log=tmp_path / "acceptance.jsonl")
    s._wait_snapshot_checked = True
    monkeypatch.setattr(s, "_protected", lambda _: 10)
    rows = [wait_row("argon_personal_or_tier1_reserve", qid=q) for q in (100, 101)]
    floor = 1 - s.policy["players"]["gemini"]["weekly_cap"]
    estimate = s.policy["players"]["gemini"]["tier2_per_question_cap"]
    for _ in range(3):
        snap = replace(_qsnap(floor + estimate + .025), taken_at=to_iso(NOW))
        append_record(tmp_path / "quota.jsonl", {"type": "quota_snapshot", **snap.to_dict()})
        assert not s._reevaluate_waits(rows)
    snap = replace(_qsnap(floor + estimate + .06), taken_at=to_iso(NOW))
    append_record(tmp_path / "quota.jsonl", {"type": "quota_snapshot", **snap.to_dict()})
    assert s._reevaluate_waits(rows) == {"wait-100", "wait-101"}


@pytest.mark.parametrize("case,expected", [
    ("before_wait", False), ("at_wait", False), ("after_wait", True),
    ("at_now", True), ("future", False), ("before_window", False),
    ("at_window_start", True), ("at_window_end", False), ("after_window", False),
    ("missing_finish", False), ("other_player", False), ("tier2", False),
    ("repair", False), ("mismatch", False), ("missing_tokens", False),
    ("zero_tokens", False),
])
def test_token_wait_requires_new_measurement_in_current_window(tmp_path, case, expected):
    cfg = campaign()
    clock = NOW
    row = wait_row("tier1_token_baseline_unmeasured")
    finished = NOW - timedelta(seconds=30)
    changes = {}
    if case == "before_wait":
        finished = NOW - timedelta(minutes=2)
    elif case == "at_wait":
        finished = NOW - timedelta(minutes=1)
    elif case == "at_now":
        finished = NOW
    elif case == "future":
        finished = NOW + timedelta(seconds=1)
    elif case in ("before_window", "at_window_start"):
        row["finished_at"] = to_iso(NOW - timedelta(days=2))
        finished = NOW - timedelta(days=1, seconds=int(case == "before_window"))
    elif case in ("at_window_end", "after_window"):
        finished = NOW + timedelta(days=6, seconds=int(case == "after_window"))
        clock = NOW + timedelta(days=7)
    elif case == "other_player":
        changes["player"] = "gpt"
    elif case == "tier2":
        changes["tier"] = 2
    elif case == "repair":
        changes["attempt_kind"] = "format_repair"
    elif case == "mismatch":
        changes["model_check"] = "mismatch"
    elif case in ("missing_tokens", "zero_tokens"):
        changes["usage"] = {} if case == "missing_tokens" else {"input_tokens": 0, "output_tokens": 0}
    measurement = {
        "type": "forecast_attempt", "attempt_id": "synthetic-measurement",
        "player": "gemini", "tier": 1, "attempt_kind": "initial",
        "model_check": "verified", "question_id": 999,
        # A call may start before the wait and finish afterwards.
        "started_at_utc": to_iso(finished - timedelta(minutes=5)),
        "finished_at_utc": to_iso(finished), "usage": {"input_tokens": 20, "output_tokens": 10},
        **changes,
    }
    if case == "missing_finish":
        measurement.pop("finished_at_utc")
    append_record(tmp_path / "forecasts.jsonl", measurement)
    s = S.Scheduler(tmp_path, cfg, snapshots=lambda: {}, now=lambda: clock,
                    acceptance_log=tmp_path / "acceptance.jsonl",
                    context_extra=lambda _: {"quota_snapshot": lambda **kw: pytest.fail("unneeded usage query")})
    assert s._reevaluate_waits([row]) == ({"wait-100"} if expected else set())
    events = read_records(s.jobs_log) if s.jobs_log.exists() else []
    assert len(events) == int(expected)
    if expected:
        assert events[0]["reason"] == "token_baseline_measured"
        assert s._reevaluate_waits([row, *events]) == {"wait-100"}
        assert read_records(s.jobs_log) == events


def test_token_measurement_only_releases_waits_finished_before_it(tmp_path):
    s = S.Scheduler(tmp_path, campaign(), snapshots=lambda: {}, now=lambda: NOW,
                    acceptance_log=tmp_path / "acceptance.jsonl")
    rows = [wait_row("tier1_token_baseline_unmeasured", qid=q) for q in (100, 101)]
    rows[1]["finished_at"] = to_iso(NOW - timedelta(seconds=10))
    attempt(s.budget, player="gemini", started=NOW - timedelta(seconds=90))
    assert s._reevaluate_waits(rows) == {"wait-100"}
    events = read_records(s.jobs_log)
    assert s._reevaluate_waits([*rows, *events]) == {"wait-100"}
    assert read_records(s.jobs_log) == events


def test_scheduler_persists_reason_and_week_boundary_without_repeated_jobs(environment):
    s, snaps, tracker, _ = environment()
    s.campaign["weekly_attempt_caps"]["gemini"] = 1
    # Synthetic prior call exhausts this week's capacity.
    append_record(s.root / "forecast_attempt_starts.jsonl", {
        "player": "gemini", "attempt_id": "used", "started_at_utc": to_iso(NOW)})
    append_record(s.root / "forecasts.jsonl", {
        "player": "gemini", "attempt_id": "used", "question_id": 999, "tier": 1,
        "question_snapshot_id": "used-snapshot",
        "type": "forecast_attempt", "validation_status": "incomplete",
        "started_at_utc": to_iso(NOW), "finished_at_utc": to_iso(NOW)})
    s.run(max_jobs=20)
    s.run(max_jobs=20)  # each invocation yields the line after its first refusal
    waits = [r for r in read_records(s.jobs_log) if r.get("outcome") == "budget_wait"]
    assert len(waits) == 2
    assert all(r["budget_reason"] == "weekly_attempt_capacity_reserved_or_exhausted"
               and r["reevaluate_after"] == s.campaign["budget_window_end"] for r in waits)
    before = len(read_records(s.jobs_log))
    s.run(max_jobs=20)
    assert len(read_records(s.jobs_log)) == before
    assert not [c for c in tracker["calls"] if c[0] == "gemini"]


@pytest.mark.parametrize("expired_at_refresh", [True, False])
def test_refresh_rechecks_deadline_and_reset_before_reserving(tmp_path, expired_at_refresh):
    clock, queries, reserved = [NOW], [], []
    cfg = load_run_config()
    def query(**kw):
        queries.append(clock[0])
        if len(queries) == 2 and expired_at_refresh:
            clock[0] = F.decision_deadline(SNAP, cfg)
        snap = replace(_qsnap(.9), taken_at=to_iso(clock[0]))
        # First read expires by reset in 10s, though its age remains below 90s.
        buckets = F.quota.argon_buckets(snap)
        weekly = replace(buckets.weekly, reset_time=to_iso(clock[0] + timedelta(
            seconds=10 if len(queries) == 1 else 3600)))
        return replace(snap, buckets={**snap.buckets, weekly.id: weekly})
    def make():
        work = tmp_path / "work"
        work.mkdir()
        clock[0] += timedelta(seconds=11)
        return work
    adapter = FakeAdapter([{"structured": _good()}])
    ctx = F.Context("gemini", adapter, cfg, "fake", F.Preflight("gemini"),
        now=lambda: clock[0], quota_snapshot=query, make_dir=make, remove_dir=lambda _: None,
        before_attempt=lambda *args: reserved.append(args), raw_root=tmp_path / "raw",
        forecasts_log=tmp_path / "forecasts.jsonl", audits_log=tmp_path / "audits.jsonl",
        quota_log=tmp_path / "quota.jsonl", line_status_log=tmp_path / "line_status.jsonl")
    if expired_at_refresh:
        with pytest.raises(F.QuestionDone, match="decision deadline"):
            F.run_attempt(ctx, SNAP, kind="initial", prompt="fixture", sch=F.V.question_schema(SNAP), prev=None)
        assert len(queries) == 2 and not reserved and adapter.ran == 0
        assert not F.starts_log(ctx.forecasts_log).exists()
    else:
        F.run_attempt(ctx, SNAP, kind="initial", prompt="fixture", sch=F.V.question_schema(SNAP), prev=None)
        assert len(queries) == 3 and len(reserved) == adapter.ran == 1


@pytest.mark.parametrize("stream_failure", ["missing", "exception", "truncated"])
@pytest.mark.usefixtures("legacy_stops")  # pre-D149 stop rule
def test_recovered_gemini_tools_and_urls_survive_final_stream_failure(tmp_path, monkeypatch, stream_failure):
    import adapters.gemini as gemini
    ctx, raw = recovery_context(tmp_path, player="gemini")
    (raw / "partial.log").write_text("synthetic raw evidence")
    stream = raw / "stdout.jsonl"
    stream.write_text("".join(json.dumps({"step_update": {
        "step_index": i, "step_type": "tool", "state": "DONE",
        "tool_info": {"name": name, "parameters": params}}}) + "\n"
        for i, (name, params) in enumerate([
            ("run_command", {"CommandLine": "true"}),
            ("read_url_content", {"Url": "https://metaculus.com/synthetic"}),
            ("synthetic_unknown_tool", {})])))
    extract = F.J.extract_gemini
    def extract_then_lose_stream(*args):
        evidence = extract(*args)
        # Model the stream becoming unavailable between the two independent reads.
        if stream_failure == "truncated":
            stream.write_text('{"result":')
        else:
            stream.unlink()
        return evidence
    monkeypatch.setattr(F.J, "extract_gemini", extract_then_lose_stream)
    monkeypatch.setattr(orphan, "no_process", lambda _: True)
    if stream_failure == "exception":
        def fail(*args):
            raise OSError("synthetic stream read failure")
        monkeypatch.setattr(gemini, "read_stream", fail)
    assert F.recover_interrupted(ctx) == ["interrupted"]
    rec = read_records(ctx.forecasts_log)[0]
    assert rec["tool_call_counts"]["run_command"] == 1
    assert any(v.get("type") == "unknown_tool_event" for v in rec["tool_violations"])
    assert rec["url_events"][0]["state"] == "opened"
    assert "leak_confirmed" in rec["audit_flags"] and rec["parse_errors"]
    assert not F._may_rerun([rec], rec) and not F.adoptable(rec)
    assert "tool_scope_violation" in F.line_stopped(
        ctx.line_status_log, "gemini", ctx.forecasts_log, now=NOW)["reason"]
    F.recover_interrupted(ctx)
    assert len(read_records(ctx.forecasts_log)) == 1


@pytest.mark.parametrize("raw_text", ['{"event":', '{"step_update":{"step_index":1,"step_type":"tool",'
    '"state":"DONE","tool_info":{"name":"run_command","parameters":{"CommandLine":"true"}}}}\n{"event":'])
@pytest.mark.usefixtures("legacy_stops")  # pre-D149 stop rule
def test_gemini_real_partial_parser_quarantines_without_false_persistent_stop(tmp_path, monkeypatch, raw_text):
    ctx, raw = recovery_context(tmp_path, player="gemini")
    (raw / "stdout.jsonl").write_text(raw_text)
    monkeypatch.setattr(orphan, "no_process", lambda _: True)
    F.recover_interrupted(ctx)
    rec = read_records(ctx.forecasts_log)[0]
    assert rec["parse_errors"] and rec["evidence_status"] == "quarantined"
    assert not F._may_rerun([rec], rec) and not F.adoptable(rec)
    stopped = F.line_stopped(ctx.line_status_log, "gemini", ctx.forecasts_log, now=NOW)
    assert bool(stopped) == ("run_command" in raw_text)
    assert not ctx.line_status_log.exists()
    assert F.forecast_question(ctx, {**SNAP, "question_id": 7})["outcome"] == "attempts_exhausted"


@pytest.mark.parametrize("cfg_arg", [{}, {"cfg": None}])
def test_preflight_without_config_recovers_legacy_orphan_acceptance(tmp_path, monkeypatch, cfg_arg):
    log = tmp_path / "acceptance.jsonl"
    append_record(log, {"type": "acceptance_call_started", "run_id": "old", "player": "gpt",
        "attempt_id": "orphan", "started_at": to_iso(NOW - timedelta(days=1))})
    monkeypatch.setattr(orphan, "no_process", lambda _: True)
    monkeypatch.setattr(F, "utc_now", lambda: NOW)
    F.preflight("gpt", **cfg_arg, acceptance_log=log,
        versions=lambda: {"gpt": "fixture"}, snapshot=lambda _: {"sources": {}, "versions": {}},
        fingerprint={"fixture": "hash"}, players_enabled={"gpt": True}, submit_mode={"mode": "local"})
    assert not F.incomplete_acceptance(log, "gpt")
    result = read_records(log)[-1]
    assert result["error_type"] == "interrupted" and result["line_pass"] is False


def test_delayed_breaker_does_not_restart_expired_cooldown(ledger):
    attempt(ledger)
    new_deep(ledger, "a", 10)
    new_deep(ledger, "b", 11)
    ledger.now = lambda: NOW + timedelta(hours=7)
    assert reserve(ledger, tier=2, aid="after", qid=12)["allowed"]
    stops = [r for r in read_records(ledger.log) if r["type"] == "budget_upgrade_stop"]
    assert stops[0]["until"] == to_iso(NOW - timedelta(minutes=1) + timedelta(hours=6))
    ledger.outcome("after", False)
    assert reserve(ledger, tier=2, aid="again", qid=13)["allowed"]
    assert len([r for r in read_records(ledger.log) if r["type"] == "budget_upgrade_stop"]) == 1


def test_legacy_named_workdirs_do_not_scan_other_lines(tmp_path):
    tmp_path = tmp_path / "legacy"
    tmp_path.mkdir()
    for name in ("flab-gemini-old", "flab-claude-live", "flab-gpt-live", "random-old"):
        (tmp_path / name).mkdir()
    paths = orphan.legacy_workdirs(tmp_path, "gemini")
    assert {p.name for p in paths} == {"flab-gemini-old", "random-old"}
    def processes(argv, **kw):
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0, stderr="", stdout=str(tmp_path / "flab-claude-live"))
    assert orphan.no_process(paths, run=processes)


def compatibility_bytes(kind):
    import poll_questions as P
    import submit
    from test_poll_questions import _post
    options = ["B", "A", "C"] if kind == "multiple_choice" else None
    snap = P.snapshot_from_post(_post(17, 7, kind, "Synthetic compatibility fixture", options=options), "fixture")
    snap.update(snapshot_id="synthetic", tournament_id=27)
    row = {"question_id": 7, "post_id": 17, "tournament_id": 27, "question_snapshot_id": "synthetic",
           "question_type": kind, "options": options, "probability": .31 if options is None else None,
           "option_probabilities": {"A": .3333335, "B": .3333335, "C": .333333} if options else None}
    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    content = {"type": kind, **{key: snap[key] for key in (
        "title", "description", "resolution_criteria", "fine_print", "options")}}
    return {"prompt": F.forecast_prompt(snap, "2026-11-01").encode(),
            "payload": encode(submit.forecast_payload(snap, row)),
            "content": encode(content), "content_hash": snap["content_hash"].encode()}


@pytest.mark.parametrize("kind", ["binary", "multiple_choice"])
def test_prompt_payload_and_snapshot_bytes_unchanged(kind):
    expected = json.loads((Path(__file__).parent / "fixtures/d121_compatibility_hashes.json").read_text())
    assert {name: hashlib.sha256(value).hexdigest()
            for name, value in compatibility_bytes(kind).items()} == expected[kind]
