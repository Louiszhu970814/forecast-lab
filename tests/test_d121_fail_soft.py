"""D121 synthetic-only regression: durable recovery, policy replay and protected files."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import baseline
import budget as B
import forecast as F
import quota
import scheduler as S
from flab import fail_soft as FS, orphan
from flab.config import load_quota_policy, load_run_config
from flab.jsonl import append_record, read_records
from flab.team_runner import answer_question
from flab.timeutil import to_iso
from test_budget import attempt, ledger, reserve, snapshot
from test_forecast import NOW, SNAP, _good, _qsnap, env
from test_scheduler import campaign


def test_protected_runtime_and_campaign_hashes():
    root = Path(__file__).resolve().parents[1]
    # D136 advances only AGENTS.md for the reviewed D79 Cup wording correction.
    # Adapter/runner/netguard and campaign/config digests remain unchanged.
    # D137 explicitly authorizes the finite campaign/mode/venue transition only.
    # D149 unpins config/cycle_campaign.json: the live window now renews it weekly;
    # its integrity stays bound by submit_mode.json's live_window campaign_sha256.
    # D151 updates config/run_config_v2.json (run_config_v4) and src/adapters/gemini.py
    # (MODEL_LABELS for gemini-4-carbon-*).
    expected = json.loads((root / "tests/fixtures/d121_protected_hashes.json").read_text())
    assert all(hashlib.sha256((root / name).read_bytes()).hexdigest() == sha for name, sha in expected.items())
    assert F.PROMPT_VERSION == "forecast_v2"


def new_deep(ledger, aid, qid, total=100, error=None):
    # Save the admission baseline at CLI start, not the later reserve's median.
    append_record(ledger.log, {"type": "budget_reserved", "version": B.VERSION,
        "player": "claude", "tier": 2, "question_id": qid, "attempt_id": aid,
        "decision_id": aid, "token_limit": 90, "token_baseline": 30,
        "run_id": "old", "at": to_iso(ledger.now() - timedelta(minutes=2))})
    row = attempt(ledger, aid=aid, qid=qid, tier=2, total=total,
                  started=ledger.now() - timedelta(minutes=2))
    # Fixtures append only once, including explicit completion/error metadata.
    if error:
        rows = read_records(ledger.root / "forecasts.jsonl")
        rows[-1]["error_type"] = error
        (ledger.root / "forecasts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return row


def test_single_breach_is_question_only_and_baseline_frozen(ledger):
    attempt(ledger)
    new_deep(ledger, "deep", 10)
    # Raise median before first post-crash decision: saved 90-token limit still wins.
    for i in range(3):
        attempt(ledger, aid=f"base{i}", qid=100+i, total=1000)
    other = reserve(ledger, tier=2, aid="other", qid=11)
    assert other["allowed"]
    ledger.outcome("other", False)
    assert reserve(ledger, tier=2, qid=10)["reason"] == "question_tier2_token_capacity"
    event = next(r for r in read_records(ledger.log) if r["type"] == "tier2_token_assessed")
    assert event["token_limit"] == 90 and event["status"] == "exceeded"


@pytest.mark.parametrize("error,own_reason", [(None, "tier2_tokens_unknown"),
    ("timeout", None), ("cli_error", None), ("interrupted", None)])
def test_unknown_tokens_distinguish_completed_from_interrupted(ledger, error, own_reason):
    attempt(ledger)
    new_deep(ledger, "deep", 10, total=None, error=error)
    decision = reserve(ledger, tier=2, aid="own", qid=10)
    assert decision["reason"] == own_reason
    if decision["allowed"]:
        ledger.outcome("own", False)
    assert reserve(ledger, tier=2, aid="other", qid=11)["allowed"]


def test_two_distinct_questions_cooldown_expires_without_retrigger(ledger):
    attempt(ledger)
    new_deep(ledger, "a", 10)
    new_deep(ledger, "b", 11)
    decision = reserve(ledger, tier=2, aid="denied", qid=12)
    assert decision["reason"] == "tier2_token_breaker"
    stops = [r for r in read_records(ledger.log) if r["type"] == "budget_upgrade_stop"]
    assert stops[-1]["until"] == to_iso(NOW - timedelta(minutes=1) + timedelta(hours=6))
    ledger.now = lambda: NOW + timedelta(hours=6)
    # Cost estimate can still deny a question, but no new line stop is written.
    reserve(ledger, tier=2, aid="later", qid=13)
    assert len([r for r in read_records(ledger.log) if r["type"] == "budget_upgrade_stop"]) == 1
    ledger.outcome("later", False)
    ledger.now = lambda: NOW + timedelta(hours=7)
    new_deep(ledger, "c", 14)
    new_deep(ledger, "d", 15)
    assert reserve(ledger, tier=2, aid="again", qid=16)["reason"] == "tier2_token_breaker"
    assert len([r for r in read_records(ledger.log) if r["type"] == "budget_upgrade_stop"]) == 2


def test_old_stops_and_lifts_replay_without_new_judgments(ledger):
    attempt(ledger)
    attempt(ledger, aid="old", tier=2, total=None)
    append_record(ledger.log, {"type": "budget_upgrade_stop", "version": "budget_v6", "player": "claude",
        "reason": "weekly_tier2_token_breaker", "at": to_iso(NOW - timedelta(hours=1)),
        "until": to_iso(NOW + timedelta(days=3))})
    assert reserve(ledger, tier=2, qid=9)["reason"] == "weekly_tier2_token_breaker"
    B.lift_tier2_stops(ledger.root, ["claude"], "PLAN D121", now=lambda: NOW)
    assert reserve(ledger, tier=2, qid=9)["allowed"]
    assert not any(r["type"] == "tier2_token_assessed" for r in read_records(ledger.log))


def test_r5_protected_quota_cap_and_concurrent_admission(ledger):
    attempt(ledger, player="gemini")
    snapshot(ledger, remaining=.2, concurrent_agy_detected=True, concurrent_agy_reason="ps_failed")
    result = reserve(ledger, tier=2, player="gemini", pending=15, qid=8)
    assert result["allowed"] and result["protected_quota"] == .05


def test_mismatch_yields_then_clean_rerun_is_adoptable(env):
    first, records, adapter = env([{"structured": _good(), "model_check": "mismatch"}])
    assert adapter.ran == 1 and not F.adoptable(records[0]) and not first.get("notify_louis")
    second, _, adapter = env([], now=NOW + timedelta(minutes=10))
    assert not second["preflight"]["ok"] and adapter.ran == 0
    third, records, adapter = env([{"structured": _good()}], now=NOW + timedelta(minutes=31))
    assert adapter.ran == 1 and third["questions"][0]["outcome"] == "selected"
    assert records[-1]["attempt_kind"] == "rerun" and not records[-1]["inherited_model_mismatch"]


@pytest.mark.usefixtures("legacy_stops")  # pre-D149 stop rule
def test_mismatch_window_replay_and_expiry(tmp_path):
    log, forecasts = tmp_path / "line.jsonl", tmp_path / "forecasts.jsonl"
    for i in range(2):
        append_record(forecasts, {"type": "forecast_attempt", "attempt_id": str(i), "player": "gpt",
            "model_check": "mismatch", "pipeline_version": F.PIPELINE_VERSION,
            "finished_at_utc": to_iso(NOW + timedelta(minutes=i*31))})
    assert F.line_stopped(log, "gpt", forecasts, now=NOW+timedelta(hours=1))["reason"] == "model_mismatch_cooldown"
    assert F.line_stopped(log, "gpt", forecasts, now=NOW+timedelta(hours=7)) is None
    append_record(forecasts, {"type": "forecast_attempt", "attempt_id": "legacy", "player": "gpt",
        "model_check": "mismatch", "pipeline_version": "forecast_pipeline_v6", "finished_at_utc": to_iso(NOW)})
    assert "recovered" in F.line_stopped(log, "gpt", forecasts, now=NOW+timedelta(days=10))["reason"]


def recovery_context(tmp_path, *, kind="initial", player="claude"):
    work = tmp_path / "scratch"
    work.mkdir()
    ctx = F.Context(player, None, load_run_config(), "recover", F.Preflight(player),
        now=lambda: NOW, forecasts_log=tmp_path/"forecasts.jsonl", audits_log=tmp_path/"audits.jsonl",
        raw_root=tmp_path/"raw", line_status_log=tmp_path/"line_status.jsonl",
        quota_log=tmp_path/"quota.jsonl", remove_dir=lambda p: None)
    raw = ctx.raw_root/player/"7_interrupted"
    raw.mkdir(parents=True)
    row = {"type": "forecast_attempt_started", "attempt_id": "interrupted", "player": player,
        "tier": 1, "question_id": 7, "question_snapshot_id": "synthetic", "attempt_kind": kind,
        "started_at_utc": to_iso(NOW-timedelta(hours=1)), "workdir": str(work), "raw_dir": str(raw),
        "timeout_s": 600, "hard_deadline_utc": to_iso(NOW-timedelta(minutes=50)),
        "pipeline_version": F.PIPELINE_VERSION}
    append_record(F.starts_log(ctx.forecasts_log), row)
    append_record(tmp_path/"budget_events.jsonl", {"type": "budget_reserved", "attempt_id": "interrupted",
        "player": player, "decision_id": "decision"})
    return ctx, raw


@pytest.mark.parametrize("alive", [True, False])
def test_recovery_requires_real_line_lock_and_no_live_cli(tmp_path, monkeypatch, alive):
    ctx, raw = recovery_context(tmp_path)
    observed = []
    def probe(paths):
        assert (tmp_path/"forecast.claude.lock").resolve() in F._HELD_LOCKS.paths
        observed.append(paths)
        return not alive
    monkeypatch.setattr(orphan, "no_process", probe)
    assert F.recover_interrupted(ctx) == ([] if alive else ["interrupted"])
    assert observed
    if not alive:
        F.recover_interrupted(ctx)
        records = read_records(ctx.forecasts_log)
        assert len(records) == 1 and records[0]["error_type"] == "interrupted"
        assert not F.adoptable(records[0])
        assert len(read_records(ctx.audits_log)) == 1
        assert sum(r["type"] == "budget_outcome" for r in read_records(tmp_path/"budget_events.jsonl")) == 1


@pytest.mark.usefixtures("legacy_stops")  # pre-D149 stop rule
def test_recovery_partial_tool_violation_still_stops_and_no_rerun(tmp_path, monkeypatch):
    ctx, raw = recovery_context(tmp_path)
    (raw/"stdout.jsonl").write_text(json.dumps({"type":"assistant", "message":{"content":[
        {"type":"tool_use", "id":"bad", "name":"Bash", "input":{"command":"true"}}]}})+"\n")
    monkeypatch.setattr(orphan, "no_process", lambda _: True)
    F.recover_interrupted(ctx)
    rec = read_records(ctx.forecasts_log)[0]
    assert rec["tool_violations"] and not F._may_rerun([rec], rec)
    assert F.line_stopped(ctx.line_status_log, ctx.player, ctx.forecasts_log, now=NOW)


def test_recovery_crash_between_result_and_budget_outcome_is_reentrant(tmp_path, monkeypatch):
    ctx, _ = recovery_context(tmp_path)
    monkeypatch.setattr(orphan, "no_process", lambda _: True)
    real = F.append_record
    def fail(path, row):
        if row["type"] == "budget_outcome":
            raise OSError("synthetic crash")
        return real(path, row)
    monkeypatch.setattr(F, "append_record", fail)
    with pytest.raises(OSError):
        F.recover_interrupted(ctx)
    monkeypatch.setattr(F, "append_record", real)
    F.recover_interrupted(ctx)
    assert len(read_records(ctx.forecasts_log)) == len(read_records(ctx.audits_log)) == 1
    assert read_records(tmp_path/"budget_events.jsonl")[-1]["status"] == "started"


def test_interrupted_repair_keeps_used_rerun_budget():
    base = {"attempt_kind":"initial", "error_type":"invalid_output"}
    repair = {"attempt_kind":"format_repair", "error_type":"interrupted"}
    assert F._may_rerun([base, repair], base)
    assert "interrupted" in F.RERUN_ERRORS


@pytest.mark.parametrize("source", ["ps", "lsof", "failure", "absent"])
def test_orphan_process_checks_cover_arguments_and_open_files(source):
    def run(argv, **kwargs):
        match = (source == "ps" and "ps" in argv[0]) or (source == "lsof" and "lsof" in argv[0])
        return SimpleNamespace(returncode=1 if source=="failure" else 0, stderr="",
            stdout="123 /synthetic/run/file" if match else "123 unrelated")
    assert orphan.no_process([Path("/synthetic/run")], run=run) == (source=="absent")


def test_orphan_acceptance_closes_once_without_forging_pass(tmp_path, monkeypatch):
    log = tmp_path/"acceptance.jsonl"
    append_record(log, {"type":"acceptance_result", "player":"gpt", "run_id":"old",
        "record_id":"old-proof", "line_pass":True, "finished_at":to_iso(NOW-timedelta(days=1))})
    append_record(log, {"type":"acceptance_call_started", "player":"gpt", "run_id":"fake",
        "attempt_id":"call", "started_at":to_iso(NOW-timedelta(hours=1)), "tier":1})
    monkeypatch.setattr(orphan, "no_process", lambda _: True)
    F.recover_acceptance(log, "gpt", load_run_config(), now=NOW)
    F.recover_acceptance(log, "gpt", load_run_config(), now=NOW)
    assert not F.incomplete_acceptance(log, "gpt")
    results = [r for r in read_records(log) if r["type"]=="acceptance_result" and r["run_id"]=="fake"]
    assert len(results)==1 and results[0]["line_pass"] is False and results[0]["error_type"]=="interrupted"
    assert F._acceptance_records(log, "gpt")[-1]["record_id"]=="old-proof"


def test_quota_retry_reads_credits_during_concurrent_activity(monkeypatch):
    fixture = Path(__file__).parent/"fixtures/gemini"
    calls = []
    def query(argv):
        name = argv[2]
        calls.append(name)
        if calls.count(name) == 1:
            raise RuntimeError("transient")
        return (fixture/("slash_usage.json" if name=="/usage" else "slash_credits.json")).read_text()
    monkeypatch.setattr(quota, "detect_agy_activity", lambda: ({"headless":1}, "ps_failed"))
    snap = quota.snapshot(_run_cmd=query)
    assert snap.remaining_credits == 0 and calls==["/usage", "/usage", "/credits", "/credits"]


def test_private_directories_informational_credentials_still_block(tmp_path, monkeypatch):
    source = tmp_path/"skills"
    (source/"logs").mkdir(parents=True)
    (source/"logs/private.txt").write_text("synthetic private content")
    (source/"skill.md").write_text("synthetic instructions")
    src = baseline.Source("fake", "claude", str(source), "dir")
    original = baseline._hash_file
    monkeypatch.setattr(baseline, "_hash_file", lambda p: (_ for _ in ()).throw(AssertionError("private read"))
                        if p.name=="private.txt" else original(p))
    before = baseline.inspect_source(src, tmp_path)
    (source/"logs/private.txt").write_text("different synthetic content")
    after = baseline.inspect_source(src, tmp_path)
    assert before["hash"] == after["hash"] and not after.get("error")
    (source/"auth.json").write_text("synthetic credentials")
    assert baseline.inspect_source(src, tmp_path).get("error")


def test_baseline_line_scope_and_shared_instructions(tmp_path):
    sources = baseline.ancestor_sources(tmp_path)
    by_name = {Path(s.path).name:s.player for s in sources}
    assert by_name["CLAUDE.md"] == "claude" and by_name["AGENTS.md"] == "common"
    snap = {"sources":{}, "versions":{"claude":"1","gpt":"1","gemini":"1"}, "gemini_credits":None}
    assert not baseline.blockers(snap, player="claude")
    assert baseline.blockers(snap, player="gemini")


def test_tg_answer_does_not_acquire_forecast_locks(tmp_path, monkeypatch):
    import flab.team_runner as team
    import tempfile
    @contextmanager
    def forbidden(*args):
        raise AssertionError("Q&A acquired a forecast lock")
        yield
    monkeypatch.setattr(team, "idle_locks", forbidden)
    monkeypatch.setattr(team, "prepare", lambda *args: ["fake"])
    monkeypatch.setattr(team, "execute", lambda *args, **kw: "{}")
    monkeypatch.setattr(team, "parse_answer", lambda *args: "synthetic")
    # Temp directory and fake project are siblings within the permitted work copy.
    assert answer_question(tmp_path/"project", "claude", "synthetic", {}, None)=="synthetic"


def test_new_budget_wait_releases_with_snapshot_legacy_terminal_is_preserved(tmp_path):
    s = S.Scheduler(tmp_path, campaign(1), snapshots=lambda: {}, now=lambda: NOW,
                    acceptance_log=tmp_path/"acceptance.jsonl")
    row = {"type":"scheduler_job_finished", "record_id":"waiting", "version":S.VERSION,
           "question_id":100, "player":"gemini", "tier":1, "outcome":"quota_weekly_wait",
           "finished_at":to_iso(NOW-timedelta(minutes=10)), "quota_retry_at_utc":to_iso(NOW+timedelta(days=1))}
    snap = replace(_qsnap(.9), taken_at=to_iso(NOW))
    s._wait_snapshot_checked = True
    append_record(s.root/"quota.jsonl", {"type":"quota_snapshot", **snap.to_dict()})
    assert s._reevaluate_waits([row]) == {"waiting"}
    event = read_records(s.jobs_log)[-1]
    assert event["reason"]=="new_valid_snapshot" and event["snapshot_id"]==snap.snapshot_id
    legacy = {**row, "outcome":"budget_skipped", "version":"scheduler_v7"}
    assert s._terminal(100, "gemini", 1, [], [legacy])
    assert not s._terminal(100, "gemini", 1, [], [{**row,"outcome":"budget_wait"}])


@pytest.mark.parametrize("credits", [0, 5])
def test_quota_refresh_occurs_before_budget_transaction_and_rechecks_credits(ledger, monkeypatch, credits):
    from test_forecast import FakeAdapter
    current = [NOW]
    queries = []
    in_budget = [False]
    original = B.transaction
    @contextmanager
    def transaction(root):
        with original(root):
            in_budget[0] = True
            try:
                yield
            finally:
                in_budget[0] = False
    monkeypatch.setattr(B, "transaction", transaction)
    ledger.now = lambda: current[0]
    def query(**kwargs):
        assert not in_budget[0]
        queries.append(current[0])
        return replace(_qsnap(.9, credits=credits if len(queries)>1 else 0), taken_at=to_iso(current[0]))
    work = ledger.root / "scratch"
    def make():
        work.mkdir()
        current[0] += timedelta(seconds=100)
        return work
    def before(kind, aid):
        assert len(queries) == 2
        decision = ledger.reserve({"attempt_id":aid, "player":"gemini", "tier":1,
            "question_id":SNAP["question_id"]}, pending_tier1=0, expected_new=0, repair_reserve=0)
        assert decision["allowed"]
    ctx = F.Context("gemini", FakeAdapter([{"structured":_good()}]), load_run_config(),
        "synthetic", F.Preflight("gemini"), now=lambda:current[0], quota_snapshot=query,
        quota_policy=ledger.policy, forecasts_log=ledger.root/"forecasts.jsonl",
        audits_log=ledger.root/"audits.jsonl", quota_log=ledger.root/"quota.jsonl",
        raw_root=ledger.root/"raw", line_status_log=ledger.root/"line_status.jsonl",
        make_dir=make, remove_dir=lambda _:None, before_attempt=before, admission_outcome=ledger.outcome)
    if credits:
        with pytest.raises(F.LineStop, match="credits"):
            F.run_attempt(ctx, SNAP, kind="initial", prompt="synthetic", sch=F.V.question_schema(SNAP), prev=None)
        assert not ctx.forecasts_log.exists() and not ledger.log.exists()
        return
    result = F.run_attempt(ctx, SNAP, kind="initial", prompt="synthetic", sch=F.V.question_schema(SNAP), prev=None)
    assert len(queries) == 3 and F.adoptable(result)
    assert len([r for r in read_records(ctx.quota_log) if r["when"]=="before"])==2
    started = read_records(F.starts_log(ctx.forecasts_log))[0]
    assert all(k in started for k in ("workdir", "raw_dir", "timeout_s", "hard_deadline_utc", "boot_id"))


def test_interrupted_format_repair_recovers_to_only_one_rerun(env, monkeypatch):
    monkeypatch.setattr(orphan, "no_process", lambda _: True)
    with pytest.raises(KeyboardInterrupt):
        env([{"text":"probability is 0.31 https://example.com/a"}, {"raise":KeyboardInterrupt()}])
    summary, records, adapter = env([{"structured":_good()}], now=NOW+timedelta(hours=1))
    assert summary["questions"][0]["outcome"]=="selected" and adapter.ran==1
    assert [r["attempt_kind"] for r in records]==["initial","format_repair","rerun"]
    assert records[1]["error_type"]=="interrupted"
    summary, _, adapter = env([], now=NOW+timedelta(hours=2))
    assert adapter.ran==0 and summary["questions"][0]["outcome"]=="already_selected"


def test_scoped_candidate_ignores_other_line_blocker_but_does_not_approve_it(tmp_path, monkeypatch):
    import copy
    from test_claude_baseline_candidate import prepared, apply
    from flab.ids import hash_obj
    monkeypatch.setattr(baseline, "ancestor_sources", lambda _: [])
    old, candidate, log, _, _ = prepared(tmp_path)
    live = copy.deepcopy(candidate["snapshot"])
    live["gemini_credits"] = None
    live["sources"]["other"] = {"player":"gpt","kind":"file","category":"content","error":"synthetic blocker"}
    result = apply(candidate, log, live)
    assert result["sources"].keys() == old["sources"].keys()
    assert result["gemini_credits"] == old["gemini_credits"]
    assert baseline.compare(result, live)["decisions"]["claude"] == "ok"


def test_preflight_uses_fresh_credits_even_with_other_headless_agy(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "recover_acceptance", lambda *args, **kwargs: None)
    monkeypatch.setattr(F, "check_acceptance", lambda *args, **kwargs: {})
    monkeypatch.setattr(F, "utc_now", lambda: NOW)
    monkeypatch.setattr(quota, "snapshot", lambda **kw:
                        replace(_qsnap(.9), concurrent_agy_detected=True, concurrent_agy_reason="ps_failed"))
    seen = []
    def snapshot(**kw):
        seen.append(kw["gemini_credits"])
        return baseline.Snapshot(to_iso(NOW), {}, kw["versions"], kw["gemini_credits"])
    monkeypatch.setattr(baseline, "take_snapshot", snapshot)
    pf = F.preflight("gemini", cfg=load_run_config(), acceptance_log=tmp_path/"acceptance.jsonl",
        versions=lambda:dict.fromkeys(("claude","gpt","gemini"),"fake"), fingerprint={"fake":"hash"},
        allow_unapproved_baseline=True, players_enabled={"gemini":True}, submit_mode={"mode":"local"})
    assert pf.ok and seen==[0]


def test_same_question_and_outside_window_do_not_count_as_two_breaches(ledger):
    attempt(ledger)
    new_deep(ledger, "first", 10)
    new_deep(ledger, "repair", 10)
    assert reserve(ledger, tier=2, aid="other", qid=20)["allowed"]
    ledger.outcome("other", False)
    ledger.now = lambda: NOW + timedelta(hours=25)
    new_deep(ledger, "late", 11)
    assert reserve(ledger, tier=2, aid="other2", qid=21)["allowed"]
    assert not any(r["type"]=="budget_upgrade_stop" for r in read_records(ledger.log))


def test_old_lift_authorization_replays_but_cannot_create_new_lifts(ledger):
    attempt(ledger)
    at = to_iso(NOW-timedelta(hours=1))
    append_record(ledger.log, {"type":"budget_upgrade_stop", "player":"claude", "at":at,
        "until":to_iso(NOW+timedelta(days=1)), "reason":"weekly_tier2_token_breaker", "version":"budget_v6"})
    append_record(ledger.log, {"type":"budget_upgrade_stop_lifted", "player":"claude",
        "at":to_iso(NOW-timedelta(minutes=1)), "lifted_stop_ats":[at],
        "authorization":"PLAN D107 A3", "version":"budget_v6"})
    assert reserve(ledger, tier=2)["allowed"]
    with pytest.raises(ValueError, match="PLAN D121"):
        B.lift_tier2_stops(ledger.root, ["claude"], "PLAN D107 A3")


def test_partial_confirmed_leak_is_quarantined_and_never_rerun(tmp_path, monkeypatch):
    ctx, raw = recovery_context(tmp_path)
    (raw/"stdout.jsonl").write_text(json.dumps({"type":"result", "subtype":"success",
        "result":json.dumps(_good(sources=[{"url":"https://metaculus.com/synthetic","title":"fixture"}]))})+"\n")
    monkeypatch.setattr(orphan, "no_process", lambda _: True)
    F.recover_interrupted(ctx)
    rec = read_records(ctx.forecasts_log)[0]
    assert "leak_confirmed" in rec["audit_flags"]
    assert not F._may_rerun([rec], rec) and not F.adoptable(rec)


def test_legacy_start_without_paths_uses_base_scan_without_deleting_unmapped_dir(tmp_path, monkeypatch):
    import flab.workdir as workdir
    ctx, _ = recovery_context(tmp_path)
    base = tmp_path / "legacy"
    base.mkdir()
    (base / "random-unmapped").mkdir()
    monkeypatch.setattr(workdir, "default_base_dir", lambda: base)
    row = read_records(F.starts_log(ctx.forecasts_log))[0]
    for key in ("workdir", "raw_dir", "hard_deadline_utc", "timeout_s"):
        row.pop(key)
    F.starts_log(ctx.forecasts_log).write_text(json.dumps(row)+"\n")
    paths = []
    monkeypatch.setattr(orphan, "no_process", lambda p: paths.extend(p) or True)
    F.recover_interrupted(ctx)
    assert len(paths)==2 and paths[0].name=="random-unmapped"
    assert read_records(tmp_path/"recovery_events.jsonl")[-1]["workdir_cleanup"]=="legacy_path_not_recorded"


def test_formal_acceptance_holds_only_its_line_lock(tmp_path, monkeypatch):
    import acceptance
    monkeypatch.setattr(acceptance, "ACCEPTANCE_LOG", tmp_path/"acceptance.jsonl")
    def inner(player, **kwargs):
        assert F._HELD_LOCKS.paths == {(tmp_path/f"forecast.{player}.lock").resolve()}
        assert not (tmp_path/"scheduler.lock").exists()
        return {"line_pass":False}
    monkeypatch.setattr(acceptance, "_run_line", inner)
    assert not acceptance.run_line("gpt", production=True, run_id="synthetic")["line_pass"]
