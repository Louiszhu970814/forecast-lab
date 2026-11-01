from __future__ import annotations

import json
from datetime import timedelta
import os
from pathlib import Path
import signal
from types import SimpleNamespace

import pytest

import autoload as auto
import baseline
import budget
import quota
from adapters.base import AdapterRequest, AdapterResult
from flab.config import load_quota_policy, load_run_config
from flab.ids import sha256_hex
from flab.jsonl import read_records
from flab import workdir
from flab.timeutil import parse_iso, to_iso, utc_now


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    base = tmp_path / "scratch" / "flab-runs"
    base.parent.mkdir()
    data = tmp_path / "data"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(auto, "default_base_dir", lambda: base)
    monkeypatch.setattr(workdir, "PROJECT_ROOT", tmp_path / "project")
    monkeypatch.setattr(auto, "PROJECT_ROOT", tmp_path / "project")
    monkeypatch.setattr(quota, "snapshot", lambda **kw: quota.QuotaSnapshot(
        buckets={quota.ARGON_BUCKET_ID: quota.BucketQuota(
            id=quota.ARGON_BUCKET_ID, remaining_fraction=.9,
            reset_time=to_iso(utc_now() + timedelta(days=3)))}, remaining_credits=0))
    return SimpleNamespace(home=home, base=base, data=data,
                           kwargs={"approved_by": "Louis", "confirmed": True, "home": home,
                                   "attempt_base": base, "data_dir": data})


def _result(req: AdapterRequest, **kw) -> AdapterResult:
    args = dict(argv=["fake"], run=None, cli_version="fake-1", completed=True,
                final_text="NONE", structured={"text": "NONE"}, reported_model_id=req.model,
                model_check="verified", usage_raw=None)
    return AdapterResult(**(args | kw))


def _targets(fx) -> list[auto.Location]:
    manifests = list(fx.data.glob("autoload_backups/*/manifest.jsonl"))
    tag = manifests[0].parent.name
    return auto.locations(tag, baseline.PLAYERS, fx.home, fx.base, env={})


def _loaded_executor(fx, seen=None):
    def execute(req):
        if seen is not None:
            seen.append(req)
        targets = _targets(fx)
        relevant = [loc for loc in targets if loc.player in (req.player, "common")]
        assert all(loc.canary.encode() in loc.path.read_bytes() for loc in relevant)
        assert all(loc.canary not in req.prompt for loc in targets)
        assert req.workdir.parent == fx.base and req.workdir.stat().st_mode & 0o777 == 0o700
        return _result(req, final_text="\n".join(loc.canary for loc in relevant)), auto.Declaration()
    return execute


def test_inventory_minimum_paths_canaries_and_codex_home(fx):
    targets = auto.locations("tag", baseline.PLAYERS, fx.home, fx.base, env={})
    assert len(targets) == 12 and len({t.canary for t in targets}) == 12
    assert {str(t.path.relative_to(fx.home)) for t in targets if t.player != "common"} == {
        ".claude/CLAUDE.md", ".claude/skills/flab-autoload-tag/SKILL.md",
        ".codex/AGENTS.md", ".codex/skills/flab-autoload-tag/SKILL.md",
        ".gemini/GEMINI.md", ".gemini/AGENTS.md", ".gemini/config/rules/flab-autoload-tag.md",
        ".gemini/config/skills/flab-autoload-tag/SKILL.md"}
    assert {str(t.path.relative_to(fx.base.parent)) for t in targets if t.player == "common"} == {
        "AGENTS.md", "CLAUDE.md", "GEMINI.md", ".agents/rules/flab-autoload-tag.md"}
    override = fx.home / "custom-codex"
    targets = auto.locations("tag", ("gpt",), fx.home, fx.base, env={"CODEX_HOME": str(override)})
    assert all(t.path.is_relative_to(override) for t in targets if t.player == "gpt")


@pytest.mark.parametrize("args,code", [(["plan"], 0), (["run"], 2),
    (["run", "--approved-by", "Louis"], 2), (["run", "--i-understand-this-edits-global-files"], 2),
    (["run", "--approved-by", "  ", "--i-understand-this-edits-global-files"], 2)])
def test_plan_and_missing_confirmations_never_write_or_read_contents(fx, monkeypatch, args, code):
    path = fx.home / ".claude" / "CLAUDE.md"
    path.parent.mkdir()
    path.write_text("private fake instructions")
    before = sorted(str(p) for p in fx.home.parent.rglob("*"))
    monkeypatch.setattr(auto, "_read", lambda p: pytest.fail("plan opened file contents"))
    monkeypatch.setattr(auto, "run", lambda **kw: pytest.fail("plan ran a model"))
    assert auto.main(args) == code
    assert sorted(str(p) for p in fx.home.parent.rglob("*")) == before
    assert path.read_text() == "private fake instructions"


@pytest.mark.parametrize("overrides", [{"approved_by": ""}, {"confirmed": False}])
def test_api_also_requires_confirmations(fx, overrides):
    with pytest.raises(ValueError, match="both"):
        auto.run(**(fx.kwargs | overrides), executor=lambda req: pytest.fail("model called"))
    assert not fx.data.exists()


@pytest.mark.parametrize("kind", ["symlink", "broken_link", "directory", "fifo", "parent_link", "hardlink"])
def test_preflight_refuses_entire_test_on_unsafe_target(fx, kind):
    path = fx.home / ".gemini" / "GEMINI.md"
    path.parent.mkdir()
    if kind == "parent_link":
        path.parent.rmdir()
        path.parent.symlink_to(fx.base.parent, target_is_directory=True)
    elif kind in ("symlink", "broken_link"):
        other = fx.home / "other"
        if kind == "symlink":
            other.write_text("untouched")
        path.symlink_to(other)
    elif kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        other = fx.home / "other"
        other.write_text("untouched")
        os.link(other, path)
    rec = auto.run(**fx.kwargs, executor=lambda req: pytest.fail("model called"))
    assert not rec["passed"] and rec["restore_ok"]
    assert not (fx.home / ".claude").exists()
    assert not (fx.data / "autoload_backups").exists()
    assert rec["errors"] and read_records(fx.data / "acceptance.jsonl") == [rec]


def test_existing_bytes_appended_then_restored_and_new_directories_removed(fx):
    path = fx.home / ".claude" / "CLAUDE.md"
    path.parent.mkdir()
    original = b"fake private content\r\n\xff\x00no final newline"
    path.write_bytes(original)
    path.chmod(0o640)
    before = path.stat()
    seen = []
    execute = _loaded_executor(fx, seen)

    def check(req):
        assert path.read_bytes().startswith(original + b"\n\n")
        return execute(req)

    rec = auto.run(**fx.kwargs, executor=check)
    assert rec["passed"] and rec["restore_ok"] and len(rec["locations"]) == 20
    assert len(rec["must_review"]) == 20
    assert path.read_bytes() == original and path.stat().st_mode & 0o777 == 0o640
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert sorted(p.relative_to(fx.home).as_posix() for p in fx.home.rglob("*")) == [".claude", ".claude/CLAUDE.md"]
    assert list(fx.base.parent.iterdir()) == []
    backup = next(Path(rec["backup_dir"]).glob("*.bin"))
    assert backup.read_bytes() == original and backup.stat().st_mode & 0o777 == 0o600
    manifest = read_records(Path(rec["backup_dir"]) / "manifest.jsonl")
    assert next(r for r in manifest if r.get("path") == str(path))["sha256"] == sha256_hex(original)
    assert "fake private content" not in (fx.data / "acceptance.jsonl").read_text()
    config = load_run_config()
    assert [r.player for r in seen] == list(baseline.PLAYERS)
    for req in seen:
        assert req.profile == "tier1" and req.model == config["players"][req.player]["tier1"]["model"]
        assert req.effort == config["players"][req.player]["tier1"]["effort"] and req.timeout_s == 600
    snap = baseline.Snapshot(rec["finished_at"], {}, rec["cli_versions"], gemini_credits=0)
    approved = baseline.approve(snap, "Louis", rec["record_id"], log=fx.data / "acceptance.jsonl")
    assert approved["autoload_record"] == rec["record_id"]


@pytest.mark.parametrize("exc", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_execution_exception_still_restores_and_records(fx, exc):
    original = fx.home / ".codex" / "AGENTS.md"
    original.parent.mkdir()
    original.write_text("original fake")

    def fail(req):
        assert "FLABAUTO-" in original.read_text()
        raise exc("sensitive error text must not be copied into acceptance log")

    rec = auto.run(**fx.kwargs, executor=fail)
    assert not rec["passed"] and rec["restore_ok"]
    assert original.read_text() == "original fake"
    assert list(fx.base.parent.iterdir()) == []
    assert rec["errors"] == [{"error": exc.__name__}]
    rows = read_records(fx.data / "acceptance.jsonl")
    assert rows[-1] == rec
    assert [r["player"] for r in rows if r["type"] == "acceptance_call_started"] == ["claude"]


def test_placement_failure_rolls_back_partial_writes(fx, monkeypatch):
    write = auto._write
    count = 0

    def fail(path, content, **kw):
        nonlocal count
        if b"FLABAUTO-" in content:
            count += 1
            if count == 2:
                write(path, content[:10], **kw)
                raise OSError("partial placement")
        return write(path, content, **kw)

    monkeypatch.setattr(auto, "_write", fail)
    rec = auto.run(**fx.kwargs, executor=lambda req: pytest.fail("model called"))
    assert rec["restore_ok"] and not rec["passed"]
    assert list(fx.home.iterdir()) == [] and list(fx.base.parent.iterdir()) == []


@pytest.mark.parametrize("corrupt", ["write", "hash", "delete", "directory", "symlink"])
def test_restore_failure_records_recovery_and_cli_exit(fx, monkeypatch, capsys, corrupt):
    original = fx.home / ".claude" / "CLAUDE.md"
    original.parent.mkdir()
    original.write_text("original fake")
    real_write = auto._write
    real_unlink = Path.unlink
    loaded = _loaded_executor(fx)

    def execute(req):
        result = loaded(req)
        if req.player == "gpt":
            if corrupt in ("write", "hash"):
                def damaged(path, content, **kw):
                    if path == original and not kw.get("append"):
                        if corrupt == "write":
                            raise OSError("restore write failed")
                        content = b"bad hash"
                    real_write(path, content, **kw)
                monkeypatch.setattr(auto, "_write", damaged)
            elif corrupt == "delete":
                monkeypatch.setattr(Path, "unlink", lambda p, **kw: None if p == fx.base.parent / "AGENTS.md"
                                    else real_unlink(p, **kw))
            elif corrupt == "directory":
                (fx.home / ".gemini" / "new-file").write_text("concurrent fake file")
            else:
                original.unlink()
                original.symlink_to(fx.home / "untouched")
        return result

    run = auto.run
    monkeypatch.setattr(auto, "run", lambda **kw: run(**(fx.kwargs | kw), executor=execute))
    assert auto.main(["run", "--approved-by", "Louis", "--i-understand-this-edits-global-files"]) == 1
    rec = read_records(fx.data / "acceptance.jsonl")[-1]
    assert not rec["restore_ok"] and not rec["passed"] and rec["restore_failures"]
    assert "MANUAL RESTORE REQUIRED" in capsys.readouterr().err
    assert all("path" in f and "backup" in f for f in rec["restore_failures"])
    assert Path(rec["backup_dir"]).is_dir()
    if corrupt == "symlink":
        assert not (fx.home / "untouched").exists()


@pytest.mark.parametrize("channel", ["model", "raw", "declaration", "path", "skill", "excluded", "unknown"])
def test_classification_uses_evidence_not_silence(tmp_path, channel):
    loc = auto.Location("claude", tmp_path / "flab-autoload-tag" / "SKILL.md", "skill", "FLABAUTO-tag-01")
    declaration = auto.Declaration(source="fake CLI init")
    output = "NONE"
    if channel in ("model", "raw"):
        output = loc.canary
    elif channel == "declaration":
        declaration.text = loc.canary
    elif channel == "path":
        declaration.text = str(loc.path)
    elif channel == "skill":
        declaration.skills = [loc.path.parent.name]
    elif channel == "excluded":
        declaration.skills = []
    outcome, reason = auto.classify(loc, output, declaration)
    assert outcome == ("not_loaded" if channel == "excluded" else "unknown" if channel == "unknown" else "loaded")
    assert reason
    if channel == "excluded":
        instruction = auto.Location("claude", tmp_path / "CLAUDE.md", "instructions", loc.canary)
        assert auto.classify(instruction, "NONE", declaration)[0] == "unknown"


@pytest.mark.parametrize("skills,expected", [([], []), (["a"], ["a"]), ([{}], None), (None, None)])
def test_claude_reads_actual_init_not_model_claims(tmp_path, skills, expected):
    events = [{"type": "system", "subtype": "init", "skills": skills,
               "plugins": [{"path": "test-plugin"}], "mcp_servers": []},
              {"type": "assistant", "skills": ["invented"], "text": "nothing loaded"}]
    (tmp_path / "stdout.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    declared = auto.claude_declaration(tmp_path)
    assert declared.skills == expected and "test-plugin" in declared.text
    assert "invented" not in declared.text
    (tmp_path / "stdout.jsonl").write_text("bad json")
    assert auto.claude_declaration(tmp_path).skills is None


def test_codex_probe_uses_same_overrides_catalog_and_config_free_mirror(fx, monkeypatch):
    from adapters import gpt
    raw = fx.data / "raw"
    raw.mkdir(parents=True)
    real = fx.home / ".codex"
    real.mkdir()
    (real / "AGENTS.md").write_text("FLABAUTO-test-01")
    (real / "config.toml").write_text("fake config must be excluded")
    (real / "auth.json").write_text("fake credential must be excluded")
    req = AdapterRequest("gpt", "tier1", auto.PROMPT, auto.SCHEMA, fx.base.parent, raw, "gpt-6-astra", "medium", 600)

    mirrors = []

    def fake_run(argv, **kw):
        assert argv[:3] == ["codex", "debug", "prompt-input"]
        expected = auto.codex_probe_argv(gpt.build_argv(req))
        assert argv[:-1] == expected[:-1] and argv[-1] == auto.PROMPT
        assert kw["cwd"] == req.workdir
        mirror = Path(kw["extra_env"]["CODEX_HOME"])
        mirrors.append(mirror)
        assert not mirror.is_relative_to(auto.PROJECT_ROOT) and not mirror.is_relative_to(fx.data)
        assert mirror.parent.name.startswith("flab-codex-home-")
        assert not (mirror / "auth.json").exists() and not (mirror / "config.toml").exists()
        assert (mirror / "AGENTS.md").read_text() == "FLABAUTO-test-01"
        kw["stdout_path"].write_text(json.dumps([{"type": "message", "role": "developer",
            "content": [{"type": "input_text", "text": f"FLABAUTO-test-01 {mirror}/AGENTS.md"}]}]))
        return SimpleNamespace(exit_code=0, timed_out=False, killed_signal=None)

    monkeypatch.setattr(auto.runner, "run", fake_run)
    declaration = auto.codex_declaration(req, gpt)
    assert "FLABAUTO-test-01" in declaration.text and str(real / "AGENTS.md") in declaration.text
    assert declaration.skills is None
    assert not (raw / "codex_probe" / "home").exists()
    assert not mirrors[0].parent.exists()
    assert (raw / "codex_probe" / "prompt_input.json").is_file()


@pytest.mark.parametrize("mode", ["cheap", "subset", "incomplete", "no_version", "error"])
def test_ineligible_runs_never_authorize_baseline(fx, mode):
    seen = []
    loaded = _loaded_executor(fx, seen)

    def execute(req):
        result, declaration = loaded(req)
        if mode == "incomplete":
            result.completed = False
        elif mode == "no_version":
            result.cli_version = None
        elif mode == "error":
            result.error_type = "fake_error"
        return result, declaration

    rec = auto.run(**fx.kwargs, cheap=mode == "cheap",
                   players=("claude",) if mode == "subset" else baseline.PLAYERS, executor=execute)
    assert not rec["passed"] and rec["restore_ok"]
    if mode == "cheap":
        assert rec["mode"] == "cheap_dry_run"
        config = load_run_config()
        assert all(r.model == config["acceptance"][r.player]["model"] and
                   r.effort == config["acceptance"][r.player]["effort"] and r.profile == "tier1" for r in seen)
    snap = baseline.Snapshot(rec["finished_at"], {}, {p: "1" for p in baseline.PLAYERS}, gemini_credits=0)
    with pytest.raises(ValueError, match="no passing"):
        baseline.approve(snap, "Louis", rec["record_id"], log=fx.data / "acceptance.jsonl")


def test_raw_output_only_scans_cli_outputs(fx):
    fx.data.mkdir()
    (fx.data / "schema.json").write_text("FLABAUTO-not-evidence")
    (fx.data / "events.jsonl").write_text("FLABAUTO-evidence")
    assert auto.raw_output(fx.data) == "FLABAUTO-evidence"


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_signal_restores_and_handlers_are_put_back(fx, sig):
    before = signal.getsignal(sig)

    def interrupt(req):
        os.kill(os.getpid(), sig)
        pytest.fail("signal did not interrupt")

    rec = auto.run(**fx.kwargs, executor=interrupt)
    assert not rec["passed"] and rec["restore_ok"]
    assert rec["errors"] == [{"error": "InterruptedError"}]
    assert signal.getsignal(sig) == before
    assert list(fx.home.iterdir()) == [] and list(fx.base.parent.iterdir()) == []


def test_existing_attempt_base_and_unrelated_files_survive(fx):
    fx.base.mkdir(mode=0o755)
    sentinel = fx.base / "unrelated"
    sentinel.write_text("fake untouched file")
    rec = auto.run(**fx.kwargs, executor=_loaded_executor(fx))
    assert rec["passed"] and rec["restore_ok"]
    assert fx.base.stat().st_mode & 0o777 == 0o755
    assert list(fx.base.iterdir()) == [sentinel] and sentinel.read_text() == "fake untouched file"


def test_raw_canary_is_still_recorded_when_adapter_raises(fx):
    def fail(req):
        loc = next(t for t in _targets(fx) if t.player == req.player)
        req.raw_dir.mkdir(parents=True)
        (req.raw_dir / "stderr.txt").write_text(loc.canary)
        raise RuntimeError("fake crash")

    rec = auto.run(**fx.kwargs, executor=fail)
    assert not rec["passed"] and rec["restore_ok"]
    assert rec["locations"][0]["outcome"] == "loaded"
    assert len(rec["must_review"]) == 20
    assert sum(r["outcome"] == "loaded" for r in rec["must_review"]) == 1
    assert sum(r["outcome"] == "unknown" for r in rec["must_review"]) == 19


def test_recovery_instructions_survive_result_log_failure(fx, monkeypatch, capsys):
    loaded = _loaded_executor(fx)
    append = auto.append_record

    def execute(req):
        result = loaded(req)
        if req.player == "gpt":
            # Leave an unexpected file in a new directory; cleanup must preserve it and report failure.
            (fx.home / ".gemini" / "concurrent").write_text("fake data")
        return result

    def fail_log(path, record):
        if record["type"] == "autoload_result":
            raise OSError("disk full")
        append(path, record)

    monkeypatch.setattr(auto, "append_record", fail_log)
    with pytest.raises(OSError):
        auto.run(**fx.kwargs, executor=execute)
    err = capsys.readouterr().err
    assert "MANUAL RESTORE REQUIRED" in err and "manifest.jsonl" in err
    assert str(fx.home / ".gemini") in err


def test_invalid_attempt_base_rejected_before_global_edits(fx):
    fx.base.write_text("not a directory")
    rec = auto.run(**fx.kwargs, executor=lambda req: pytest.fail("model called"))
    assert rec["restore_ok"] and not rec["passed"]
    assert not (fx.data / "autoload_backups").exists() and list(fx.home.iterdir()) == []


def test_execute_uses_real_adapter_entrypoints_with_fake_calls(fx, monkeypatch):
    from adapters import claude, gemini, gpt

    seen = []
    for player, adapter in (("claude", claude), ("gemini", gemini), ("gpt", gpt)):
        raw = fx.data / player
        raw.mkdir(parents=True)
        req = AdapterRequest(player, "tier1", auto.PROMPT, auto.SCHEMA, fx.base.parent, raw,
                             "gemini-4-argon-medium" if player == "gemini" else "fake-model", "medium", 600)

        def fake_run(request):
            seen.append(request)
            return _result(request)

        monkeypatch.setattr(adapter, "run", fake_run)
        monkeypatch.setattr(auto, "codex_declaration", lambda r, a: auto.Declaration(source="fake codex probe"))
        result, declaration = auto.execute(req)
        assert result.completed and seen[-1] is req
        if player == "gemini":
            assert "agy" in declaration.source
    assert len(seen) == 3


@pytest.mark.parametrize("mixed", [False, True])
def test_unknown_sources_pass_but_require_review_and_only_excluded_sources_are_omitted(fx, mixed):
    def execute(req):
        if mixed and req.player == "claude":
            loc = next(t for t in _targets(fx) if t.player == "claude")
            return _result(req, final_text=loc.canary), auto.Declaration(skills=[])
        return _result(req), auto.Declaration()

    rec = auto.run(**fx.kwargs, executor=execute)
    assert rec["passed"] and rec["restore_ok"]
    assert len(rec["locations"]) == 20
    assert rec["must_review"] == [{k: loc[k] for k in ("player", "path", "outcome")}
                                  for loc in rec["locations"] if loc["outcome"] != "not_loaded"]
    assert sum(loc["outcome"] == "unknown" for loc in rec["must_review"]) == (18 if mixed else 20)
    assert len(rec["must_review"]) == (19 if mixed else 20)
    snap = baseline.Snapshot(rec["finished_at"], {}, rec["cli_versions"], gemini_credits=0)
    assert baseline.approve(snap, "Louis", rec["record_id"], log=fx.data / "acceptance.jsonl")


@pytest.mark.parametrize("cheap", [False, True])
def test_calls_journaled_before_real_executor_and_counted_by_budget(fx, monkeypatch, cheap):
    from adapters import claude, gemini, gpt

    log = fx.data / "acceptance.jsonl"
    seen, probes = [], []

    def fake_run(req):
        rows = read_records(log)
        call = rows[-1]
        assert call["type"] == "acceptance_call_started"
        assert call["player"] == req.player and call["source"] == "autoload"
        assert call["tier"] == 1 and call["profile"] == req.profile
        assert call["model"] == req.model and call["effort"] == req.effort
        assert parse_iso(call["started_at"]) <= utc_now()
        if req.player == "gemini":
            snapshot = rows[-2]
            assert snapshot["type"] == "acceptance_quota_snapshot" and snapshot["when"] == "before"
            assert snapshot["attempt_id"] == call["attempt_id"] and snapshot["remaining_credits"] == 0
        seen.append(call)
        return _result(req)

    def fake_probe(req, adapter):
        probes.append(req.player)
        return auto.Declaration()

    for adapter in (claude, gemini, gpt):
        monkeypatch.setattr(adapter, "run", fake_run)
    monkeypatch.setattr(auto, "codex_declaration", fake_probe)
    rec = auto.run(**fx.kwargs, cheap=cheap)
    assert rec["passed"] is (not cheap) and probes == ["gpt"]
    assert len(seen) == 3 and len({r["attempt_id"] for r in seen}) == 3
    assert {r["run_id"] for r in seen} == {rec["run_id"]}
    now = utc_now()
    campaign = {"campaign_id": "autoload-budget-test", "budget_window_start": to_iso(now - timedelta(days=1)),
                "budget_window_end": to_iso(now + timedelta(days=6)),
                "weekly_attempt_caps": dict.fromkeys(baseline.PLAYERS, 1)}
    ledger = budget.Budget(fx.data, campaign, load_quota_policy(), "budget-test",
                           now=lambda: now, acceptance_log=log)
    for player in baseline.PLAYERS:
        decision = ledger.reserve({"player": player, "tier": 1, "question_id": 1,
                                   "attempt_id": f"next-{player}"},
                                  pending_tier1=0, expected_new=0, repair_reserve=0)
        assert decision["weekly_attempts_used"] == 1
        assert not decision["allowed"]
    assert len([r for r in read_records(log) if r["type"] == "acceptance_call_started"]) == 3


@pytest.mark.parametrize("problem", ["credits", "stale", "concurrent", "low", "missing", "nan"])
def test_gemini_gate_pauses_without_counting_or_calling_model(fx, monkeypatch, problem):
    snapshot = quota.QuotaSnapshot(
        buckets={} if problem == "missing" else {quota.ARGON_BUCKET_ID: quota.BucketQuota(
            id=quota.ARGON_BUCKET_ID, remaining_fraction=.44 if problem == "low" else
            float("nan") if problem == "nan" else .9)},
        remaining_credits=1 if problem == "credits" else 0,
        stale=problem == "stale", concurrent_agy_detected=problem == "concurrent")
    monkeypatch.setattr(quota, "snapshot", lambda **kw: snapshot)
    rec = auto.run(**fx.kwargs, players=("gemini",), executor=lambda req: pytest.fail("model called"))
    rows = read_records(fx.data / "acceptance.jsonl")
    assert [r["type"] for r in rows] == ["acceptance_paused", "autoload_result"]
    paused = rows[0]
    assert paused["run_id"] == rec["run_id"] and paused["player"] == "gemini"
    assert paused["tier"] == 1 and paused["profile"] == "tier1" and paused["source"] == "autoload"
    assert "quota_snapshot" in paused and "45%" in paused["reason"]
    if problem == "nan":
        assert paused["quota_snapshot"]["buckets"][quota.ARGON_BUCKET_ID]["remaining_fraction"] is None
    assert not rec["passed"] and rec["restore_ok"]
    assert list(fx.home.iterdir()) == [] and list(fx.base.parent.iterdir()) == []


@pytest.mark.parametrize("record_type", ["acceptance_call_started", "acceptance_quota_snapshot"])
def test_journal_failure_prevents_call_and_restores(fx, monkeypatch, record_type):
    append = auto.append_record

    def fail(path, record):
        if record["type"] == record_type:
            raise OSError("fake disk full")
        append(path, record)

    monkeypatch.setattr(auto, "append_record", fail)
    rec = auto.run(**fx.kwargs, players=("gemini",), executor=lambda req: pytest.fail("model called"))
    assert not rec["passed"] and rec["restore_ok"]
    assert not any(r["type"] == "acceptance_call_started" for r in read_records(fx.data / "acceptance.jsonl"))
    assert list(fx.home.iterdir()) == [] and list(fx.base.parent.iterdir()) == []


@pytest.mark.parametrize("failure", ["mirror", "probe", "interrupt", "inside_project"])
def test_codex_mirror_cleanup_on_failure(fx, monkeypatch, failure):
    from adapters import gpt

    raw = fx.data / "raw"
    raw.mkdir(parents=True)
    real = fx.home / ".codex"
    real.mkdir()
    (real / "AGENTS.md").write_text("fake instructions")
    temp = fx.home.parent / "flab-codex-home-fake"
    temp.mkdir()
    monkeypatch.setattr(auto.tempfile, "mkdtemp", lambda **kw: str(temp))
    if failure == "inside_project":
        monkeypatch.setattr(auto, "PROJECT_ROOT", fx.home.parent)
    req = AdapterRequest("gpt", "tier1", auto.PROMPT, auto.SCHEMA, fx.base.parent, raw,
                         "gpt-6-astra", "medium", 600)
    mirror_home = auto.codex_home_mirror

    def mirror(dest, source):
        mirror_home(dest, source)
        if failure == "mirror":
            raise OSError("fake partial mirror failure")

    def fail_run(*args, **kw):
        raise KeyboardInterrupt() if failure == "interrupt" else OSError("fake probe failure")

    monkeypatch.setattr(auto, "codex_home_mirror", mirror)
    monkeypatch.setattr(auto.runner, "run", fail_run)
    expected = KeyboardInterrupt if failure == "interrupt" else ValueError if failure == "inside_project" else OSError
    with pytest.raises(expected):
        auto.codex_declaration(req, gpt)
    assert not temp.exists() and not (raw / "codex_probe" / "home").exists()
    assert (real / "AGENTS.md").read_text() == "fake instructions"


def test_attempt_base_is_resolved_and_persisted_for_default_location(fx, monkeypatch):
    monkeypatch.chdir(fx.home.parent)
    monkeypatch.setattr(auto, "default_base_dir", lambda: Path("scratch/flab-runs"))
    kwargs = {k: v for k, v in fx.kwargs.items() if k != "attempt_base"}
    rec = auto.run(**kwargs, executor=_loaded_executor(fx))
    assert rec["attempt_base"] == str(fx.base.resolve())
    assert read_records(fx.data / "acceptance.jsonl")[-1]["attempt_base"] == rec["attempt_base"]
    assert any(Path(loc["path"]).parent == Path(rec["attempt_base"]).parent for loc in rec["locations"])
