from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from flab import dev_runner as R
from flab.dev_tasks import TaskStore
from flab.timeutil import parse_iso
from test_telegram_bot import project

NOW = parse_iso("2026-11-02T13:01:00Z")


@pytest.fixture
def repo(project):
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / "README.md").write_text("original\n")
    (project / "src").mkdir()
    (project / "src/example.py").write_text("print('example')\n")
    (project / "data").mkdir()
    (project / "data/predictions.jsonl").write_text('{"secret":"prediction"}\n')
    (project / ".gitignore").write_text(".env\ndata/\n.venv/\n")
    return project


def worker(repo, tmp_path):
    return R.DevWorker(repo, "owner", base=tmp_path / "workspaces", now=lambda: NOW)


def test_snapshot_includes_uncommitted_code_and_excludes_private_data(repo, tmp_path):
    job = tmp_path / "job"
    work = R.snapshot(repo, job)
    assert (work / "src/example.py").read_text() == "print('example')\n"
    assert not (work / ".env").exists() and not (work / "data").exists() and not (work / ".git").exists()
    assert (job / "baseline/README.md").read_text() == "original\n"
    assert (repo / "README.md").read_text() == "original\n"


def test_snapshot_refuses_symlinks(repo, tmp_path):
    (repo / "src/link.py").symlink_to(repo / ".env")
    with pytest.raises(R.DevError, match="連結"):
        R.snapshot(repo, tmp_path / "job")


def test_environment_is_allowlisted_and_permissions_are_restricted(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "never_forward")
    monkeypatch.setenv("METACULUS_TOKEN", "never_forward")
    monkeypatch.setenv("OPENAI_API_KEY", "never_forward")
    monkeypatch.setenv("PYTHONPATH", "/host/injection")
    monkeypatch.setenv("CUSTOM_PASSWORD", "never_forward")
    work = tmp_path / "work"
    env = R.clean_env(work)
    assert "never_forward" not in str(env) and "PYTHONPATH" not in env
    args = R.build_argv("codex", repo, work, tmp_path / "run")
    assert "--ignore-user-config" in args and "--ephemeral" in args
    assert "--dangerously-bypass-approvals-and-sandbox" not in args
    assert "-s" not in args and "--sandbox" not in args  # don't override named profile
    policy = R.permission_config(repo, work)
    assert policy["permissions.flabdev.filesystem"][":root"] == "deny"
    assert policy["permissions.flabdev.network.enabled"] is False
    assert policy["approval_policy"] == "never"
    assert "functions.exec" not in R.task_prompt({"requests": ["test"]}, repo)


def test_export_cumulative_diff_is_applicable_and_main_unchanged(repo, tmp_path):
    job = tmp_path / "job"
    work = R.snapshot(repo, job)
    (work / "README.md").write_text("new without newline")
    (work / "src/new.py").write_text("print('new')\n")
    (work / "src/example.py").unlink()
    run_root = job / "run"
    run_root.mkdir()
    changed, patch = R.export_changes(job, run_root)
    assert len(changed) == 3
    subprocess.run(["git", "apply", "--check", patch], cwd=job / "baseline", check=True, capture_output=True)
    assert (repo / "README.md").read_text() == "original\n"
    assert not (repo / "src/new.py").exists()


def fake_cli(tmp_path, monkeypatch, *, needs_input=False, sleep=0, fail=False):
    script = tmp_path / "fake_cli.py"
    script.write_text('''import json, pathlib, sys, time
print(json.dumps({"type":"turn.started"}), flush=True)
print(json.dumps({"type":"item.started","item":{"type":"command_execution","command":"python -m pytest"}}), flush=True)
time.sleep(float(sys.argv[2]))
if sys.argv[4] == "yes": raise SystemExit(1)
pathlib.Path("src/new_feature.py").write_text("NEW_FEATURE = True\\n")
pathlib.Path(sys.argv[1]).write_text(json.dumps({"summary":"已完成新功能", "tests":"合成測試執行器", "needs_input":sys.argv[3] == "yes", "question":"需要哪種圖表？" if sys.argv[3] == "yes" else ""}))
print(json.dumps({"type":"turn.completed","usage":{}}), flush=True)
''')
    monkeypatch.setattr(R, "prepare_cli", lambda project, work, run: [sys.executable, str(script),
        str(run / "last.json"), str(sleep), "yes" if needs_input else "no", "yes" if fail else "no"])


def test_worker_success_keeps_changes_for_review(repo, tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch)
    w = worker(repo, tmp_path)
    task = w.store.submit("owner", "1", "新增功能")
    claimed = w.claim()
    w.execute(claimed)
    result = w.store.tasks()[task["task_id"]]
    assert result["status"] == "review_ready"
    assert "src/new_feature.py" in result["changed"]
    assert Path(result["artifact"]).exists()
    assert not (repo / "src/new_feature.py").exists()
    assert any(r.get("stage") == "test" for r in w.store.events())


def test_worker_waiting_input_then_followup_reuses_work(repo, tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, needs_input=True)
    w = worker(repo, tmp_path)
    task = w.store.submit("owner", "1", "新增功能")
    w.execute(w.claim())
    tid = task["task_id"]
    assert w.store.tasks()[tid]["status"] == "waiting_input"
    work = Path(w.store.tasks()[tid]["workspace"])
    (work / "src/saved.py").write_text("KEPT = True\n")
    w.store.control("owner", "2", "supplement", tid, "長條圖")
    fake_cli(tmp_path, monkeypatch)
    w.execute(w.claim())
    assert w.store.tasks()[tid]["status"] == "review_ready"
    assert (work / "src/saved.py").exists()


def test_worker_cancellation_kills_process_and_retains_workspace(repo, tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, sleep=30)
    w = worker(repo, tmp_path)
    task = w.store.submit("owner", "1", "新增功能")
    w.start()
    deadline = time.monotonic() + 8
    while w.proc is None and time.monotonic() < deadline:
        time.sleep(.02)
    assert w.proc is not None
    pid = w.proc.pid
    w.store.control("owner", "2", "cancel", task["task_id"])
    deadline = time.monotonic() + 8
    while w.store.tasks()[task["task_id"]]["status"] != "cancelled" and time.monotonic() < deadline:
        time.sleep(.02)
    w.stop()
    w.join(5)
    assert w.store.tasks()[task["task_id"]]["status"] == "cancelled"
    assert Path(w.store.tasks()[task["task_id"]]["workspace"]).exists()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_worker_daily_limit_and_unknown_runs_not_replayed(repo, tmp_path):
    w = worker(repo, tmp_path)
    task = w.store.submit("owner", "1", "新增功能")
    claimed = w.claim()
    w.recover()
    assert w.store.tasks()[task["task_id"]]["status"] == "interrupted"
    assert w.claim() is None
    w.store.control("owner", "2", "supplement", task["task_id"], "繼續")
    for n in range(9):
        w.store.record("started", task_id="dummy", owner="other", run_id=str(n), revision=1, workspace="/fake")
    assert w.claim() is None
    assert "等待下一個 UTC 日" in w.store.describe("owner")


def test_worker_failure_is_redacted_and_not_retried(repo, tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, fail=True)
    w = worker(repo, tmp_path)
    task = w.store.submit("owner", "1", "新增功能")
    w.start()
    deadline = time.monotonic() + 8
    while w.store.tasks()[task["task_id"]]["status"] != "failed" and time.monotonic() < deadline:
        time.sleep(.02)
    w.stop()
    w.join(5)
    result = w.store.tasks()[task["task_id"]]
    assert result["status"] == "failed" and result["runs"] == 1
    assert "未自動重試" in result["summary"]


def test_sandbox_probe_fails_closed(repo, tmp_path, monkeypatch):
    work = tmp_path / "work"
    control = tmp_path / "control"
    work.mkdir()
    control.mkdir()
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: type("Result", (), {"returncode": 0, "stdout": "wrong"})())
    with pytest.raises(R.DevError, match="隔離"):
        R.sandbox_probe("codex", repo, work, control)


def test_export_rejects_intermediate_symlink_without_reading_host_file(repo, tmp_path):
    job = tmp_path / "job"
    work = R.snapshot(repo, job)
    external = tmp_path / "external"
    external.mkdir()
    (external / "example.py").write_text("private host data")
    import shutil
    shutil.rmtree(work / "src")
    (work / "src").symlink_to(external, target_is_directory=True)
    run = job / "run"
    run.mkdir()
    with pytest.raises(R.DevError, match="安全讀取"):
        R.export_changes(job, run)
    assert not (run / "changes.patch").exists()


def test_restart_refuses_to_kill_unrelated_recycled_pid(repo, tmp_path, monkeypatch):
    w = worker(repo, tmp_path)
    w.store.submit("owner", "1", "工作")
    task = w.claim()
    w.store.record("spawned", task_id=task["task_id"], owner="owner", pid=123,
        signature="/this/task/unique/last.json")
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: type("Result", (), {"returncode": 0, "stdout": "unrelated"})())
    monkeypatch.setattr(R, "terminate_tree", lambda *a: pytest.fail("must not kill unrelated process"))
    with pytest.raises(R.DevError, match="身分"):
        w.recover()


def test_status_queries_stay_responsive_during_development(repo, tmp_path, monkeypatch):
    from test_telegram_bot import FakeAPI, message
    import telegram_bot as T
    fake_cli(tmp_path, monkeypatch, sleep=30)
    api = FakeAPI()
    bot = T.Service(repo, api, 123, 42, now=lambda: NOW)
    bot.handle(message(1, "開發：測試工作"))
    w = R.DevWorker(repo, bot.dev_owner, base=tmp_path / "workspaces", now=lambda: NOW)
    w.start()
    deadline = time.monotonic() + 8
    while w.proc is None and time.monotonic() < deadline:
        time.sleep(.02)
    assert w.proc is not None
    before = time.monotonic()
    bot.handle(message(2, "狀態"))
    bot.handle(message(3, "開發進度"))
    bot.deliver()
    assert time.monotonic() - before < 2
    assert any("執行中" in text for _, text in api.sent)
    bot.handle(message(4, "停止任務"))
    w.stop()
    w.join(6)
    assert not w.is_alive()
