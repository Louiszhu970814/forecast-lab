from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import acceptance_judge as J
import forecast as F
import quota
import validate as V
from adapters.base import AdapterResult, UrlEvent
from flab.jsonl import append_record, read_records

NOW = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)
SNAP = {"type": "question_snapshot", "question_id": 43332, "post_id": 43327, "tournament_id": 32977,
        "question_type": "binary", "options": None, "supported": True, "status": "open", "snapshot_id": "qs_1",
        "title": "Will X happen?", "description": "d", "resolution_criteria": "rc", "fine_print": "fp",
        "open_time": "2026-04-27T00:00:00Z", "scheduled_close_time": "2026-12-31T23:59:00Z",
        "scheduled_resolve_time": "2027-01-01T00:00:00Z"}


def _good(**kw: object) -> dict:
    out = {"question_id": 43332, "question_type": "binary", "probability": 0.31, "option_probabilities": None,
           "reasoning_summary": "r", "key_factors": ["k"], "base_rate": "b", "market_refs": [],
           "sources": [{"url": "https://example.com/a", "title": "t"}], "changed_from_tier1": None,
           "change_reason": None, "escalation_flags": [], "injection_notice": None}
    out.update(kw)
    return out


class FakeAdapter:
    """Returns scripted results in order; records each request."""

    def __init__(self, script: list[dict]) -> None:
        self.script, self.requests, self.ran = list(script), [], 0

    def run(self, req, **kwargs):  # type: ignore[no-untyped-def]
        self.requests.append(req)
        self.ran += 1
        step = self.script.pop(0)
        if "raise" in step:
            raise step["raise"]
        (req.raw_dir / "stdout.jsonl").write_text(step.get("raw", "{}"))
        result = AdapterResult(argv=["fake"], run=None, cli_version="1", completed=step.get("completed", True),
                             final_text=step.get("text"), structured=step.get("structured"), reported_model_id="m",
                             model_check=step.get("model_check", "unverified"), usage_raw={"input_tokens": 1},
                             url_events=step.get("url_events", []), error_type=step.get("error_type"),
                             error=step.get("error"), tool_call_counts=step.get("tool_call_counts"),
                             subagent_count=step.get("subagent_count"))
        result.violations = step.get("violations", [])
        return result

    def apply_quota_model_check(self, result, check):  # type: ignore[no-untyped-def]
        result.model_check = check
        if check == "mismatch":
            result.error_type = "model_mismatch"


@pytest.fixture(autouse=True)
def _no_real_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No test may append to the project's data/ logs."""
    for name in ("FORECASTS_LOG", "RUNS_LOG", "LINE_STATUS_LOG", "QUOTA_LOG", "RAW_ROOT", "LOCK_PATH"):
        monkeypatch.setattr(F, name, tmp_path / "guard" / name.lower())
    monkeypatch.setattr(F.audit, "AUDITS_LOG", tmp_path / "guard" / "audits")
    for field in F.Context.__dataclass_fields__.values():
        if field.name.endswith(("_log", "_root")):
            monkeypatch.setattr(F.Context, field.name, tmp_path / "guard" / field.name, raising=False)


@pytest.fixture
def env(tmp_path: Path):
    """go(script) runs one forecast.py run; ``ledger`` names the log set, so a second run with the same
    ledger sees the first run's attempts (as a later scheduled run would)."""
    work = tmp_path / "work"

    def make_dir() -> Path:
        work.mkdir(exist_ok=True)
        return work

    def go(script: list[dict], *, snap: dict = SNAP, now: datetime = NOW, after: datetime | None = None,
           ledger: str = "a", player: str = "claude", **extra):
        base = tmp_path / ledger
        paths = {"forecasts_log": base / "forecasts.jsonl", "audits_log": base / "audits.jsonl",
                 "quota_log": base / "quota.jsonl", "raw_root": base / "raw", "line_status_log": base / "line_status.jsonl"}
        adapter = FakeAdapter(script)
        clock = (lambda: after if (after and adapter.ran) else now)
        summary = F.run(player, [snap["question_id"]], adapter_loader=lambda p: adapter,
                        snapshots={snap["question_id"]: snap}, runs_log=base / "runs.jsonl",
                        lock=F.Lock(tmp_path / "lock"), preflight=F.Preflight(player), now=clock,
                        make_dir=make_dir, remove_dir=lambda d: None, **paths, **extra)
        recs = list(read_records(paths["forecasts_log"])) if paths["forecasts_log"].exists() else []
        return summary, recs, adapter
    return go


def test_valid_initial_attempt_is_selected_and_fully_recorded(env) -> None:
    summary, recs, adapter = env([{"structured": _good()}])
    q = summary["questions"][0]
    assert q["outcome"] == "selected" and q["selected_attempt_id"] == recs[0]["attempt_id"]
    r = recs[0]
    assert (r["attempt_kind"], r["validation_status"], r["probability"], r["prev_attempt_id"]) == ("initial", "valid", 0.31, None)
    assert r["prompt_version"] == "forecast_v2" and r["schema_version"] == "output_schema_v2"
    assert r["schema_sha256"] == V.schema_sha256(V.question_schema(SNAP)) and r["audit_flags"] == []
    assert r["deadline_utc"] == "2026-12-31T23:39:00.000Z" and r["tournament_id"] == 32977
    for key in ("record_id", "run_id", "question_snapshot_id", "requested_model_id", "requested_effort",
                "usage", "url_events", "raw_dir", "audit_event_id", "sources", "market_refs"):
        assert key in r, key
    req = adapter.requests[0]
    assert req.profile == "tier1" and req.schema == V.question_schema(SNAP) and "43332" in req.prompt


def test_unparseable_output_with_a_unique_probability_is_repaired(env) -> None:
    raw_text = 'I estimate the probability is 0.31. Source: https://example.com/a'
    summary, recs, adapter = env([{"text": raw_text}, {"structured": _good()}])
    assert [r["attempt_kind"] for r in recs] == ["initial", "format_repair"]
    assert recs[0]["validation_status"] == "parse_failed" and recs[0]["repair_basis"]["probability"] == 0.31
    assert recs[1]["prev_attempt_id"] == recs[0]["attempt_id"] and recs[1]["prompt_version"] == "repair_v1"
    assert adapter.requests[1].profile == "format_repair" and raw_text in adapter.requests[1].prompt
    assert summary["questions"][0]["selected_attempt_id"] == recs[1]["attempt_id"]


def test_repair_that_changes_the_probability_is_rejected(env) -> None:
    summary, recs, _ = env([{"text": "the probability is 0.31 https://example.com/a"}, {"structured": _good(probability=0.4)}])
    assert recs[1]["validation_status"] == "semantic_failed" and "changed the probability" in recs[1]["validation_errors"][0]
    assert summary["questions"][0]["selected_attempt_id"] is None and len(recs) == 2


def test_no_unique_probability_is_invalid_output_without_repair_or_rerun(env) -> None:
    summary, recs, _ = env([{"text": "The probability is 0.2. On reflection the probability is 0.5."}])
    assert len(recs) == 1 and recs[0]["error_type"] == "invalid_output"
    assert summary["questions"][0]["outcome"] == "invalid_output"


def test_cli_failure_is_rerun_once(env) -> None:
    summary, recs, _ = env([{"completed": False, "error_type": "cli_error", "error": "boom"}, {"structured": _good()}])
    assert [(r["attempt_kind"], r["validation_status"]) for r in recs] == [("initial", "incomplete"), ("rerun", "valid")]
    assert recs[1]["prev_attempt_id"] == recs[0]["attempt_id"]
    summary, recs, _ = env([{"completed": False, "error_type": "timeout"}, {"completed": False, "error_type": "timeout"}],
                           ledger="b")
    assert [r["attempt_kind"] for r in recs] == ["initial", "rerun"] and summary["questions"][0]["outcome"] == "timeout"


def test_an_adapter_exception_is_recorded_as_a_cli_error_and_rerun(env) -> None:
    summary, recs, _ = env([{"raise": OSError("agy not found")}, {"structured": _good()}])
    assert recs[0]["error_type"] == "cli_error" and "agy not found" in recs[0]["error"]
    assert summary["questions"][0]["selected_attempt_id"] == recs[1]["attempt_id"]


def test_confirmed_leak_is_never_selected_or_rerun(env) -> None:
    leak = _good(sources=[{"url": "https://www.metaculus.com/questions/43332/", "title": "q"}])
    summary, recs, _ = env([{"structured": leak}])
    assert recs[0]["validation_status"] == "valid" and recs[0]["audit_flags"] == ["leak_confirmed"]
    assert summary["questions"][0]["selected_attempt_id"] is None and len(recs) == 1


def test_model_mismatch_is_not_selected_and_stops_the_line(env) -> None:
    summary, recs, _ = env([{"structured": _good(), "model_check": "mismatch", "error_type": "model_mismatch"}])
    assert summary["questions"][0]["outcome"] == "line_stopped" and "notify Louis" in summary["line_stopped"]
    assert len(recs) == 1 and not F.adoptable(recs[0])


def test_mismatched_output_is_never_repaired_into_an_answer(env) -> None:
    # GPT W8 review: initial=mismatch/schema_failed -> repair=valid used to be selected.
    summary, recs, adapter = env([{"text": "the probability is 0.31", "model_check": "mismatch"},
                                  {"structured": _good()}])
    assert [r["attempt_kind"] for r in recs] == ["initial"] and recs[0]["repair_basis"] is None
    assert adapter.ran == 1 and summary["line_stopped"]
    assert not F.adoptable({**recs[0], "validation_status": "valid", "model_check": "verified",
                            "inherited_model_mismatch": True})


def test_flags_of_the_previous_attempt_are_inherited(env) -> None:
    suspect = [UrlEvent("https://metaculus.com/x", "metaculus.com", "unknown", "search_result")]
    summary, recs, _ = env([{"completed": False, "error_type": "cli_error", "url_events": suspect}, {"structured": _good()}])
    assert recs[0]["audit_flags"] == recs[1]["audit_flags"] == ["suspected_leak"]
    assert summary["questions"][0]["selected_attempt_id"] == recs[1]["attempt_id"]  # suspected answers are still used


def test_output_finished_after_the_deadline_is_late_and_has_no_probability(env) -> None:
    late = datetime(2026, 12, 31, 23, 50, tzinfo=timezone.utc)
    summary, recs, _ = env([{"structured": _good()}], after=late)
    r = recs[0]
    assert r["validation_status"] == "late" and r["probability"] is None and r["option_probabilities"] is None
    assert r["late_values"] == {"probability": 0.31, "option_probabilities": None}
    assert summary["questions"][0]["selected_attempt_id"] is None


def test_timeout_is_capped_by_the_decision_deadline(env) -> None:
    # close 03:30 -> submit deadline 03:10 -> decision deadline 03:05; now 03:00 leaves 300 s
    snap = {**SNAP, "scheduled_close_time": "2026-11-01T03:30:00Z"}
    summary, recs, adapter = env([{"structured": _good()}], snap=snap)
    assert adapter.requests[0].timeout_s == 300 and recs[0]["timeout_s"] == 300
    # 03:04:30 leaves 30 s: no attempt starts, and the question records why
    summary, recs, adapter = env([], snap=snap, now=datetime(2026, 11, 1, 3, 4, 30, tzinfo=timezone.utc), ledger="b")
    q = summary["questions"][0]
    assert recs == [] and q["outcome"] == "stopped_waiting" and "decision deadline" in q["reason"]


def test_attempt_limits_and_selection_hold_across_runs(env) -> None:
    # GPT W8 review: a second run of the same question must not get new attempts or a new answer.
    first, recs, _ = env([{"structured": _good()}])
    again, recs2, adapter = env([{"structured": _good(probability=0.7)}])
    assert again["questions"][0] == {"question_id": 43332, "attempts": [], "outcome": "already_selected",
                                     "selected_attempt_id": first["questions"][0]["selected_attempt_id"]}
    assert adapter.ran == 0 and len(recs2) == 1
    env([{"completed": False, "error_type": "timeout"}, {"completed": False, "error_type": "timeout"}], ledger="b")
    again, _, adapter = env([{"structured": _good()}], ledger="b")
    assert again["questions"][0]["outcome"] == "attempts_exhausted" and adapter.ran == 0
    env([{"text": "no estimate"}], ledger="c")  # invalid_output: not rerun, then or later
    again, _, adapter = env([{"structured": _good()}], ledger="c")
    assert again["questions"][0]["outcome"] == "attempts_exhausted" and adapter.ran == 0


def test_a_rerun_left_by_an_interrupted_run_is_made_once_later(env, tmp_path: Path) -> None:
    env([{"completed": False, "error_type": "cli_error"}, {"completed": False, "error_type": "cli_error"}], ledger="d")
    recs = list(read_records(tmp_path / "d" / "forecasts.jsonl"))
    # simulate a run that stopped after its initial attempt: keep only that record in a fresh ledger
    (tmp_path / "e").mkdir()
    append_record(tmp_path / "e" / "forecasts.jsonl", recs[0])
    summary, recs2, adapter = env([{"structured": _good()}], ledger="e")
    assert [r["attempt_kind"] for r in recs2] == ["initial", "rerun"]
    assert recs2[1]["prev_attempt_id"] == recs[0]["attempt_id"] and adapter.ran == 1
    assert summary["questions"][0]["selected_attempt_id"] == recs2[1]["attempt_id"]


def test_two_confirmed_leaks_in_a_week_stop_the_line(env, tmp_path: Path) -> None:
    leak = _good(sources=[{"url": "https://www.metaculus.com/questions/1/", "title": "q"}])
    env([{"structured": leak}], ledger="f")
    summary, recs, _ = env([{"structured": leak}], snap={**SNAP, "question_id": 43333}, ledger="f")
    assert "leak_confirmed" in summary.get("line_stopped", "")
    blocked, _, adapter = env([{"structured": _good()}], snap={**SNAP, "question_id": 43334}, ledger="f")
    assert not blocked["preflight"]["ok"] and adapter.ran == 0


@pytest.mark.parametrize("change,reason", [
    ({"supported": False}, "unsupported_type"), ({"status": "closed"}, "status 'closed'"),
    ({"scheduled_close_time": "2026-11-01T03:20:00Z"}, "past the decision deadline")])
def test_ineligible_questions_are_skipped(env, change: dict, reason: str) -> None:
    summary, recs, _ = env([], snap={**SNAP, **change})
    assert summary["questions"] == [{"question_id": 43332, "outcome": "skipped", "reason": reason}] and recs == []


def test_a_second_instance_is_refused(tmp_path: Path) -> None:
    with F.Lock(tmp_path / "lock"):
        with pytest.raises(F.PreflightError):
            with F.Lock(tmp_path / "lock"):
                pass


# ---------------------------------------------------------------- pre-run checks

FP = {"adapters/claude.py": "a", "flab/runner.py": "b", "flab/netguard.py": "c"}
SNAPSHOT = {"sources": {}, "versions": {"claude": "2.1.316", "gemini": "1", "gpt": "1"}, "gemini_credits": 0}


def _accept(log: Path, tmp_path: Path, *, mode: str = "acceptance", line_pass: bool = True, code: dict | None = None,
            judge: str = J.JUDGE_VERSION, cli: str = "2.1.316", tamper: bool = False, **extra: object) -> None:
    ev = tmp_path / f"ev_{mode}_{line_pass}.json"
    ev.write_text(json.dumps({"code_sha256": code if code is not None else FP}))
    sha = hashlib.sha256(ev.read_bytes()).hexdigest()
    append_record(log, {"type": "acceptance_result", "record_id": f"acc_{mode}", "player": "claude", "mode": mode,
                        "line_pass": line_pass, "judge_version": judge, "cli_version": cli,
                        "evidence_path": str(ev), "evidence_sha256": "x" if tamper else sha, **extra})


def _pf(log: Path, **kw: object) -> F.Preflight:
    args = dict(cfg={"players": {"gpt": {"tier1": {"model": "gpt-6-astra"}}}}, acceptance_log=log,
                versions=lambda: dict(SNAPSHOT["versions"]), snapshot=lambda v: SNAPSHOT, fingerprint=FP,
                players_enabled={"claude": True, "gpt": True}, submit_mode={"mode": "local"})
    args.update(kw)
    player = args.pop("player", "claude")
    return F.preflight(player, **args)  # type: ignore[arg-type]


def test_preflight_passes_with_matching_acceptance_and_a_waived_baseline(tmp_path: Path) -> None:
    log = tmp_path / "acc.jsonl"
    _accept(log, tmp_path, mode="production")
    _accept(log, tmp_path)
    pf = _pf(log, allow_unapproved_baseline=True)
    assert pf.ok and pf.acceptance_record_id == "acc_acceptance" and pf.production_record_id == "acc_production"
    assert pf.waivers and "no approved baseline" in pf.waivers[0]
    assert "no approved baseline" in " ".join(_pf(log).problems)


@pytest.mark.parametrize("kw,expected", [
    ({"line_pass": False}, "did not pass"), ({"judge": "acceptance_judge_v1"}, "current judge"),
    ({"cli": "2.0.0"}, "differs from the accepted"), ({"tamper": True}, "sha256 does not match"),
    ({"code": {**FP, "flab/runner.py": "changed"}}, "runtime code changed")])
def test_preflight_rejects_a_stale_or_failed_acceptance(tmp_path: Path, kw: dict, expected: str) -> None:
    log = tmp_path / "acc.jsonl"
    _accept(log, tmp_path, mode="production")
    _accept(log, tmp_path, **kw)
    problems = " ".join(_pf(log, allow_unapproved_baseline=True).problems)
    assert expected in problems, problems


def test_production_acceptance_must_match_the_configured_production_model(tmp_path: Path) -> None:
    log = tmp_path / "acc.jsonl"
    _accept(log, tmp_path, mode="production", model="claude-sonnet-5-5", effort="medium")
    _accept(log, tmp_path)
    cfg = {"players": {"claude": {"tier1": {"model": "claude-opus-5-5", "effort": "medium"}}}}
    assert "production model" in " ".join(_pf(log, cfg=cfg, allow_unapproved_baseline=True).problems)
    _accept(log, tmp_path, mode="production", model="claude-opus-5-5", effort="medium")
    _accept(log, tmp_path)
    assert _pf(log, cfg=cfg, allow_unapproved_baseline=True).ok


def test_preflight_requires_production_acceptance_mode_enabled_line_and_baseline(tmp_path: Path) -> None:
    log = tmp_path / "acc.jsonl"
    _accept(log, tmp_path)
    assert "production model" in " ".join(_pf(log, allow_unapproved_baseline=True).problems)
    _accept(log, tmp_path, mode="production")
    assert "disabled" in " ".join(_pf(log, allow_unapproved_baseline=True, players_enabled={"claude": False}).problems)
    assert "local mode" in " ".join(_pf(log, allow_unapproved_baseline=True, submit_mode={"mode": "trial"}).problems)
    blocked = {**SNAPSHOT, "versions": {**SNAPSHOT["versions"], "claude": None}}
    problems = " ".join(_pf(log, allow_unapproved_baseline=True, snapshot=lambda v: blocked).problems)
    assert "baseline blocker" in problems
    approved = {"type": "baseline_approved", "record_id": "base_1", **SNAPSHOT,
                "versions": {**SNAPSHOT["versions"], "claude": "2.1.300"}}
    append_record(log, approved)
    assert "baseline check says 'reaccept'" in " ".join(_pf(log).problems)
    assert "no acceptance record" in " ".join(_pf(tmp_path / "none.jsonl").problems)


def test_preflight_compares_the_codex_catalog_entry(tmp_path: Path) -> None:
    log = tmp_path / "acc.jsonl"
    for mode in ("production", "acceptance"):
        ev = tmp_path / f"g_{mode}.json"
        ev.write_text(json.dumps({"code_sha256": {"adapters/gpt.py": "a", "flab/runner.py": "b", "flab/netguard.py": "c"}}))
        append_record(log, {"type": "acceptance_result", "record_id": mode, "player": "gpt", "mode": mode, "model": "gpt-6-astra",
                            "line_pass": True, "judge_version": J.JUDGE_VERSION, "cli_version": "1",
                            "evidence_path": str(ev), "evidence_sha256": hashlib.sha256(ev.read_bytes()).hexdigest(),
                            "catalog_entry_sha256": "accepted"})

    class Codex:
        def __init__(self, sha: str) -> None:
            self.sha = sha

        def load_catalog(self):  # type: ignore[no-untyped-def]
            return {"models": []}

        def restricted_catalog(self, catalog, model):  # type: ignore[no-untyped-def]
            return catalog

        def catalog_entry_sha256(self, catalog, model):  # type: ignore[no-untyped-def]
            return self.sha

    fp = {"adapters/gpt.py": "a", "flab/runner.py": "b", "flab/netguard.py": "c"}
    ok = _pf(log, player="gpt", adapter=Codex("accepted"), fingerprint=fp, allow_unapproved_baseline=True)
    assert ok.ok and ok.catalog == {"models": []}
    bad = _pf(log, player="gpt", adapter=Codex("other"), fingerprint=fp, allow_unapproved_baseline=True)
    assert "catalog entry differs" in " ".join(bad.problems)


def test_forecast_prompt_fills_every_placeholder() -> None:
    for snap in (SNAP, {**SNAP, "question_type": "multiple_choice", "options": ["A", "B"]}):
        text = F.forecast_prompt(snap, "2026-11-01")
        assert "$" not in text and "2026-11-01" in text and "metaculus.com" in text
    mc = F.forecast_prompt({**SNAP, "question_type": "multiple_choice", "options": ["A", "B"]}, "2026-11-01")
    assert '- Options: A; B' in mc and '["A", "B"]' in mc


# ---------------------------------------------------------------- gemini quota (§7.7)

def _qsnap(argon: float, credits: float | int | None = 0) -> quota.QuotaSnapshot:
    return quota.QuotaSnapshot(buckets={quota.ARGON_BUCKET_ID: quota.BucketQuota(quota.ARGON_BUCKET_ID, argon)},
                               remaining_credits=credits)


POLICY = {"players": {"gemini": {"weekly_cap": 0.6, "tier2_per_question_cap": 0.05}}}


def _gemini(env, monkeypatch, snaps: list, *, judge: str = "verified", script: list | None = None, ledger: str = "g"):
    seq = iter(snaps)

    def snapshot(**kw):  # type: ignore[no-untyped-def]
        item = next(seq)
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(F.quota, "judge", lambda before, after: judge)
    return env(script if script is not None else [{"structured": _good()}], player="gemini", ledger=ledger,
               quota_snapshot=snapshot, quota_policy=POLICY)


def test_gemini_runs_above_the_reserve_line_and_records_both_snapshots(env, monkeypatch, tmp_path: Path) -> None:
    summary, recs, adapter = _gemini(env, monkeypatch, [_qsnap(0.9), _qsnap(0.88)])
    assert adapter.ran == 1 and recs[0]["model_check"] == "verified" and recs[0]["quota_before"] and recs[0]["quota_after"]
    assert len(list(read_records(tmp_path / "g" / "quota.jsonl"))) == 2


@pytest.mark.parametrize("snaps,needle", [
    ([_qsnap(0.44)], "below the reserve line"),            # 0.44 - 0.05 < 0.40
    ([_qsnap(0.9, credits=5)], "credits"),
    ([_qsnap(0.9, credits=None)], "credits"),
    ([RuntimeError("agy down")], "before the run failed"),
])
def test_gemini_does_not_start_without_quota_admission(env, monkeypatch, snaps, needle: str) -> None:
    summary, recs, adapter = _gemini(env, monkeypatch, snaps)
    assert adapter.ran == 0 and recs == [] and needle in summary["line_stopped"]


def test_gemini_quota_failure_after_the_run_keeps_the_attempt(env, monkeypatch) -> None:
    summary, recs, adapter = _gemini(env, monkeypatch, [_qsnap(0.9), RuntimeError("agy down")])
    assert adapter.ran == 1 and len(recs) == 1 and recs[0]["model_check"] == "undetermined"
    assert "after the run failed" in summary["line_stopped"]


def test_gemini_model_mismatch_from_quota_stops_the_line(env, monkeypatch) -> None:
    summary, recs, _ = _gemini(env, monkeypatch, [_qsnap(0.9), _qsnap(0.9)], judge="mismatch")
    assert recs[0]["model_check"] == "mismatch" and not F.adoptable(recs[0]) and "model_mismatch" in summary["line_stopped"]


def test_argon_estimate_uses_observed_drops(tmp_path: Path) -> None:
    log = tmp_path / "q.jsonl"
    assert F.argon_estimate(log, POLICY, tmp_path / "f.jsonl") == (0.05, 0)
    for i, (b, a) in enumerate([(0.9, 0.89), (0.89, 0.87), (0.87, 0.86)]):
        for when, v in (("before", b), ("after", a)):
            append_record(log, {"type": "quota_snapshot", "attempt_id": f"a{i}", "when": when,
                                "buckets": {quota.ARGON_BUCKET_ID: {"remaining_fraction": v}}})
    for i in range(3):
        append_record(tmp_path / "f.jsonl", {"type": "forecast_attempt", "attempt_id": f"a{i}", "model_check": "verified"})
    est, n = F.argon_estimate(log, POLICY, tmp_path / "f.jsonl")
    assert abs(est - 0.02) < 1e-9 and n == 3


# ---------------------------------------------------------------- Codex takeover regressions

def test_interrupted_cli_start_never_gets_a_fresh_attempt_budget(env, tmp_path: Path) -> None:
    with pytest.raises(KeyboardInterrupt):
        env([{"raise": KeyboardInterrupt()}])
    starts = list(read_records(tmp_path / "a" / "forecast_attempt_starts.jsonl"))
    assert len(starts) == 1 and starts[0]["attempt_kind"] == "initial"
    summary, recs, adapter = env([{"structured": _good()}])
    assert recs == [] and adapter.ran == 0
    assert not summary["preflight"]["ok"] and summary["notify_louis"]
    assert starts[0]["attempt_id"] in " ".join(summary["preflight"]["problems"])
    other, _, adapter = env([], snap={**SNAP, "question_id": 43333})
    assert not other["preflight"]["ok"] and adapter.ran == 0


def test_interrupted_repair_is_not_reissued_after_restart(env, tmp_path: Path) -> None:
    with pytest.raises(KeyboardInterrupt):
        env([{"text": "probability is 0.31 https://example.com/a"}, {"raise": KeyboardInterrupt()}])
    summary, recs, adapter = env([])
    assert len(recs) == 1 and recs[0]["validation_status"] == "parse_failed"
    assert adapter.ran == 0 and not summary["preflight"]["ok"]


@pytest.mark.parametrize("player,evidence", [
    ("gpt", {"violations": [{"type": "command_execution", "status": "completed"}]}),
    ("claude", {"tool_call_counts": {"Bash": 1}}),
    ("claude", {"subagent_count": 1}),
])
def test_tool_scope_violation_is_recorded_and_persistently_stops_the_line(env, player, evidence) -> None:
    summary, recs, adapter = env([{"structured": _good(), **evidence}], player=player)
    assert adapter.ran == 1 and summary["notify_louis"] is True
    assert recs[0]["error_type"] == "tool_scope_violation" and recs[0]["probability"] is None
    assert recs[0]["tool_violations"] and not F.adoptable(recs[0])
    again, _, adapter = env([], player=player)
    assert not again["preflight"]["ok"] and adapter.ran == 0


def test_repair_cannot_search_even_when_it_returns_the_original_estimate(env) -> None:
    summary, recs, adapter = env([{"text": "probability is 0.31 https://example.com/a"},
                                 {"structured": _good(), "tool_call_counts": {"WebSearch": 1}}])
    assert len(recs) == 2 and recs[-1]["error_type"] == "tool_scope_violation"
    assert summary["notify_louis"] and adapter.ran == 2


def test_gemini_adapter_violations_stop_the_line_without_repair(env, monkeypatch) -> None:
    summary, recs, adapter = _gemini(env, monkeypatch, [_qsnap(0.9), _qsnap(0.88)],
        script=[{"structured": _good(), "violations": [{"type": "local_read", "path": "/fake/canary"}]}])
    assert recs[0]["error_type"] == "tool_scope_violation" and summary["notify_louis"]
    assert adapter.ran == 1


def test_gemini_accepted_tools_remain_allowed_in_their_profile() -> None:
    result = AdapterResult(argv=[], run=None, cli_version="1", completed=True, final_text=None,
        structured=None, reported_model_id=None, model_check="unverified", usage_raw=None,
        tool_call_counts={"search_web": 1, "read_url_content": 1, "view_file": 1, "finish": 1, "manage_task": 1})
    assert F.tool_violations("gemini", result, repair=False) == []
    result.tool_call_counts = {"finish": 1, "manage_task": 1}
    assert F.tool_violations("gemini", result, repair=True) == []
    result.tool_call_counts["search_web"] = 1
    assert F.tool_violations("gemini", result, repair=True)


def test_nonfinite_invalid_output_is_still_recorded_as_json(env) -> None:
    summary, recs, adapter = env([{"structured": _good(probability=float("nan"),
                                                       base_rate=float("inf"))}])
    assert len(recs) == 1 and recs[0]["validation_status"] == "semantic_failed"
    assert recs[0]["base_rate"] is None and "$.base_rate" in recs[0]["nonfinite_fields"]
    assert summary["questions"][0]["selected_attempt_id"] is None and adapter.ran == 1


def test_parser_type_error_is_recorded_and_uses_only_one_rerun(env) -> None:
    summary, recs, adapter = env([{"raise": TypeError("unexpected CLI event shape")}, {"structured": _good()}])
    assert len(recs) == 2 and recs[0]["error_type"] == "cli_error"
    assert summary["questions"][0]["selected_attempt_id"] == recs[1]["attempt_id"]
    assert adapter.ran == 2


def test_multiple_json_candidates_cannot_hide_a_confirmed_source(env) -> None:
    raw = (json.dumps(_good(sources=[{"url": "https://metaculus.com/questions/7/", "title": "q"}]))
           + "\n" + json.dumps(_good()))
    summary, recs, adapter = env([{"text": raw}, {"structured": _good()}])
    assert adapter.ran == 1 and len(recs) == 1
    assert recs[0]["audit_flags"] == ["leak_confirmed"] and recs[0]["repair_basis"] is None
    assert summary["questions"][0]["selected_attempt_id"] is None


def test_quota_exhaustion_pauses_across_runs_before_using_the_single_rerun(env) -> None:
    first, recs, adapter = env([{"completed": False, "error_type": "quota_exhausted"}, {"structured": _good()}])
    assert adapter.ran == 1 and "pause line" in first["line_stopped"]
    assert recs[0]["quota_retry_at_utc"] == "2026-11-01T03:30:00.000Z"
    blocked, recs, adapter = env([], now=NOW + timedelta(minutes=29))
    assert not blocked["preflight"]["ok"] and adapter.ran == 0 and len(recs) == 1
    later, recs, adapter = env([{"structured": _good()}], now=NOW + timedelta(minutes=31))
    assert adapter.ran == 1 and [r["attempt_kind"] for r in recs] == ["initial", "rerun"]
    assert later["questions"][0]["selected_attempt_id"] == recs[1]["attempt_id"]


@pytest.mark.parametrize("evidence", [
    {"model_check": "mismatch"}, {"tool_call_counts": {"Bash": 1}},
])
def test_safety_stop_is_recovered_when_the_status_append_was_lost(env, tmp_path, evidence) -> None:
    env([{"structured": _good(), **evidence}])
    (tmp_path / "a" / "line_status.jsonl").unlink()  # simulate a kill before the stop append, in fake data only
    blocked, _, adapter = env([], snap={**SNAP, "question_id": 43333})
    assert not blocked["preflight"]["ok"] and adapter.ran == 0
    assert "recovered from forecast ledger" in " ".join(blocked["preflight"]["problems"])
    append_record(tmp_path / "a" / "line_status.jsonl", {"type": "line_resume", "player": "claude",
                                                        "at": "2026-11-01T03:01:00Z"})
    resumed, _, adapter = env([{"structured": _good(question_id=43333)}], snap={**SNAP, "question_id": 43333},
                              now=NOW + timedelta(minutes=2))
    assert resumed["preflight"]["ok"] and adapter.ran == 1


def test_explicit_resume_starts_a_new_leak_stop_window_without_erasing_history(env, tmp_path):
    source = [{"url": "https://metaculus.com/questions/1/", "title": "q"}]
    env([{"structured": _good(sources=source)}])
    env([{"structured": _good(question_id=43333, sources=source)}], snap={**SNAP, "question_id": 43333})
    log = tmp_path / "a" / "line_status.jsonl"
    log.unlink()  # also verify a repeated-leak stop is recoverable before an explicit resume
    assert F.line_stopped(log, "claude", tmp_path / "a" / "forecasts.jsonl")
    append_record(log, {"type": "line_resume", "player": "claude", "at": "2026-11-01T03:01:00Z"})
    resumed, recs, adapter = env([{"structured": _good(question_id=43334)}], snap={**SNAP, "question_id": 43334},
                               now=NOW + timedelta(minutes=2))
    assert resumed["preflight"]["ok"] and adapter.ran == 1 and len(recs) == 3
