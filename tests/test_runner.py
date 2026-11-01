"""Tests for subprocess runner: process-group timeout termination, env scrubbing, IO, argv validation."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from flab.runner import InvalidArgvError, run


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def test_runner_rejects_invalid_argv(tmp_path: Path) -> None:
    out = tmp_path / "stdout.txt"
    err = tmp_path / "stderr.txt"

    for bad_argv in ("ls -la", [], [""], [123], ("echo", "hi"), None):
        with pytest.raises((InvalidArgvError, TypeError, ValueError)):
            run(
                bad_argv,  # type: ignore[arg-type]
                cwd=tmp_path,
                timeout_s=5,
                stdout_path=out,
                stderr_path=err,
            )


def test_runner_extra_env_is_added_but_never_a_secret(tmp_path: Path) -> None:
    out = tmp_path / "stdout.txt"
    res = run([sys.executable, "-c", "import os; print(os.environ.get('CODEX_HOME'))"], cwd=tmp_path,
              timeout_s=5, stdout_path=out, stderr_path=tmp_path / "e.txt", extra_env={"CODEX_HOME": "/x/mirror"})
    assert res.exit_code == 0 and out.read_text().strip() == "/x/mirror"
    with pytest.raises(ValueError):
        run([sys.executable, "-c", "pass"], cwd=tmp_path, timeout_s=5, stdout_path=out,
            stderr_path=tmp_path / "e.txt", extra_env={"OPENAI_API_KEY": "k"})


def test_runner_writes_stdout_stderr_and_passes_stdin(tmp_path: Path) -> None:
    out = tmp_path / "stdout.txt"
    err = tmp_path / "stderr.txt"
    script = (
        "import sys\n"
        "data = sys.stdin.read()\n"
        "sys.stdout.write('OUT:' + data)\n"
        "sys.stderr.write('ERR:' + data.upper())\n"
    )

    res = run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        stdin_text="hello world\n",
        timeout_s=5,
        stdout_path=out,
        stderr_path=err,
    )

    assert res.exit_code == 0
    assert res.timed_out is False
    assert res.killed_signal is None
    assert out.read_text(encoding="utf-8") == "OUT:hello world\n"
    assert err.read_text(encoding="utf-8") == "ERR:HELLO WORLD\n"
    assert res.started_at.endswith("Z")
    assert res.finished_at.endswith("Z")
    assert res.duration_s >= 0.0


def test_runner_scrubs_and_keeps_env_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "stdout.txt"
    err = tmp_path / "stderr.txt"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-123")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa-123")
    monkeypatch.setenv("CODEX_API_KEY", "codex-123")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-123")
    monkeypatch.setenv("GOOGLE_API_KEY", "goog-123")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "1")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
    monkeypatch.setenv("METACULUS_TOKEN", "meta-tok")
    monkeypatch.setenv("CUSTOM_API_KEY", "custom-key")
    monkeypatch.setenv("SERVICE_ACCESS_TOKEN", "svc-tok")
    monkeypatch.setenv("APP_CLIENT_SECRET", "app-sec")
    monkeypatch.setenv("KEEP_THIS_TOKEN", "kept-val")
    monkeypatch.setenv("SAFE_REGULAR_VAR", "safe-val")

    res = run(
        [sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"],
        cwd=tmp_path,
        timeout_s=5,
        stdout_path=out,
        stderr_path=err,
        keep_env=("KEEP_THIS_TOKEN",),
    )

    assert res.exit_code == 0
    child_env = json.loads(out.read_text(encoding="utf-8"))

    for scrubbed_key in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_CLOUD_PROJECT",
        "METACULUS_TOKEN",
        "CUSTOM_API_KEY",
        "SERVICE_ACCESS_TOKEN",
        "APP_CLIENT_SECRET",
    ):
        assert scrubbed_key not in child_env
        assert scrubbed_key in res.scrubbed_env_names

    assert child_env.get("KEEP_THIS_TOKEN") == "kept-val"
    assert "KEEP_THIS_TOKEN" not in res.scrubbed_env_names
    assert child_env.get("SAFE_REGULAR_VAR") == "safe-val"


def test_runner_timeout_kills_child_and_grandchild_processes(tmp_path: Path) -> None:
    out = tmp_path / "stdout.txt"
    err = tmp_path / "stderr.txt"
    pids_file = tmp_path / "pids.json"

    # Child spawns a grandchild; both ignore SIGTERM so runner must escalate to SIGKILL after grace period
    script = f"""
import json, os, signal, subprocess, sys, time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
grandchild = subprocess.Popen(
    [sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"]
)
with open({str(pids_file)!r}, "w") as f:
    json.dump({{"child": os.getpid(), "grandchild": grandchild.pid, "pgid": os.getpgrp()}}, f)
    f.flush()
time.sleep(60)
"""

    res = run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        timeout_s=0.3,
        stdout_path=out,
        stderr_path=err,
        _sigterm_grace_s=0.3,
    )

    assert res.timed_out is True
    assert res.killed_signal == "SIGKILL"
    assert res.killed_signal == signal.SIGKILL

    pids = json.loads(pids_file.read_text(encoding="utf-8"))
    # Wait briefly for OS process table cleanup if needed, then verify both are gone
    time.sleep(0.05)
    assert not _pid_exists(pids["child"])
    assert not _pid_exists(pids["grandchild"])
    with pytest.raises(ProcessLookupError):
        os.killpg(pids["pgid"], 0)


def test_runner_timeout_sigterm_when_process_exits_within_grace_period(tmp_path: Path) -> None:
    out = tmp_path / "stdout.txt"
    err = tmp_path / "stderr.txt"

    res = run(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=tmp_path,
        timeout_s=0.2,
        stdout_path=out,
        stderr_path=err,
    )

    assert res.timed_out is True
    assert res.killed_signal == "SIGTERM"
    assert res.killed_signal == signal.SIGTERM



def test_runner_terminates_orphans_after_normal_exit(tmp_path: Path) -> None:
    out = tmp_path / "stdout.txt"
    err = tmp_path / "stderr.txt"
    pid_file = tmp_path / "orphan.pid"
    script = (
        "import subprocess, sys\n"
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
    )

    res = run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        timeout_s=10,
        stdout_path=out,
        stderr_path=err,
        _sigterm_grace_s=1.0,
    )

    assert res.exit_code == 0
    assert res.timed_out is False
    assert res.orphans_terminated is True
    orphan_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5.0
    while _pid_exists(orphan_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_exists(orphan_pid)


def test_runner_reports_no_orphans_for_clean_exit(tmp_path: Path) -> None:
    res = run(
        [sys.executable, "-c", "print('done')"],
        cwd=tmp_path,
        timeout_s=10,
        stdout_path=tmp_path / "o.txt",
        stderr_path=tmp_path / "e.txt",
    )
    assert res.exit_code == 0
    assert res.orphans_terminated is False


def test_runner_interrupt_terminates_process_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    from flab import runner

    real_popen = subprocess.Popen
    processes = []

    def interrupted_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        processes.append(proc)
        wait = proc.wait
        first = True

        def interrupted_wait(*a, **kw):
            nonlocal first
            if first:
                first = False
                raise KeyboardInterrupt()
            return wait(*a, **kw)

        monkeypatch.setattr(proc, "wait", interrupted_wait)
        return proc

    monkeypatch.setattr(runner.subprocess, "Popen", interrupted_popen)
    with pytest.raises(KeyboardInterrupt):
        runner.run([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path,
                   timeout_s=5, stdout_path=tmp_path / "out", stderr_path=tmp_path / "err",
                   _sigterm_grace_s=0.1)
    assert len(processes) == 1 and processes[0].poll() is not None
    assert not _pid_exists(processes[0].pid)


def test_runner_interrupt_during_timeout_cleanup_retries_termination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from flab import runner

    terminate = runner._terminate_group
    calls = []

    def interrupted_cleanup(pgid, proc, grace):
        calls.append(pgid)
        if len(calls) == 1:
            raise InterruptedError("fake SIGTERM during timeout cleanup")
        return terminate(pgid, proc, grace)

    monkeypatch.setattr(runner, "_terminate_group", interrupted_cleanup)
    with pytest.raises(InterruptedError):
        runner.run([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path,
                   timeout_s=.1, stdout_path=tmp_path / "out", stderr_path=tmp_path / "err",
                   _sigterm_grace_s=.1)
    assert len(calls) == 2 and calls[0] == calls[1]
    assert not _pid_exists(calls[0])
