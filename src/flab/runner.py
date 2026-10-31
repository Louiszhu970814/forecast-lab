"""Subprocess execution helper with environment scrubbing and process-group timeout termination."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from flab.timeutil import to_iso, utc_now

EXPLICIT_SCRUB_ENV_NAMES: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_CLOUD_PROJECT",
        "METACULUS_TOKEN",
    }
)

SCRUB_SUFFIXES: tuple[str, ...] = ("API_KEY", "_TOKEN", "SECRET")

SIGTERM_GRACE_SECONDS: float = 5.0


class InvalidArgvError(TypeError, ValueError):
    """Raised when argv is not a non-empty list of strings."""


class SignalName(str):
    """String representation of a POSIX signal name that also compares equal to its signal number."""

    _num: int

    def __new__(cls, sig: signal.Signals) -> SignalName:
        obj = super().__new__(cls, sig.name)
        obj._num = int(sig.value)
        return obj

    def __eq__(self, other: object) -> bool:
        if isinstance(other, int) and not isinstance(other, bool):
            return self._num == int(other)
        return super().__eq__(other)

    def __hash__(self) -> int:
        return super().__hash__()


@dataclass(frozen=True)
class RunResult:
    """Metadata and outcome of a single subprocess execution."""

    exit_code: int | None
    timed_out: bool
    killed_signal: SignalName | None
    duration_s: float
    started_at: str
    finished_at: str
    stdout_path: Path
    stderr_path: Path
    scrubbed_env_names: list[str]
    orphans_terminated: bool = False


def _should_scrub(name: str, keep_set: set[str]) -> bool:
    if name in keep_set:
        return False
    upper = name.upper()
    if name in EXPLICIT_SCRUB_ENV_NAMES or upper in EXPLICIT_SCRUB_ENV_NAMES:
        return True
    return upper.endswith(SCRUB_SUFFIXES)


def _prepare_env(keep_env: Iterable[str]) -> tuple[dict[str, str], list[str]]:
    keep_set = set(keep_env)
    env: dict[str, str] = {}
    scrubbed: list[str] = []
    for key, val in os.environ.items():
        if _should_scrub(key, keep_set):
            scrubbed.append(key)
        else:
            env[key] = val
    scrubbed.sort()
    return env, scrubbed


def _is_pg_alive(pgid: int, proc: subprocess.Popen[bytes]) -> bool:
    proc.poll()
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _terminate_group(pgid: int, proc: subprocess.Popen[bytes], grace_s: float) -> SignalName | None:
    """Send SIGTERM, then SIGKILL after ``grace_s``, to the process group; return the last signal sent."""
    sent: SignalName | None = None
    try:
        os.killpg(pgid, signal.SIGTERM)
        sent = SignalName(signal.SIGTERM)
    except ProcessLookupError:
        return sent

    grace_deadline = time.monotonic() + grace_s
    while _is_pg_alive(pgid, proc) and time.monotonic() < grace_deadline:
        time.sleep(0.02)

    if _is_pg_alive(pgid, proc):
        try:
            os.killpg(pgid, signal.SIGKILL)
            sent = SignalName(signal.SIGKILL)
        except ProcessLookupError:
            return sent

        kill_deadline = time.monotonic() + 5.0
        while _is_pg_alive(pgid, proc) and time.monotonic() < kill_deadline:
            time.sleep(0.01)

        if _is_pg_alive(pgid, proc):
            raise RuntimeError(f"Process group {pgid} still exists after SIGKILL")
    return sent


def run(
    argv: list[str],
    *,
    cwd: str | Path,
    stdin_text: str | None = None,
    timeout_s: float,
    stdout_path: str | Path,
    stderr_path: str | Path,
    keep_env: Iterable[str] = (),
    _sigterm_grace_s: float = SIGTERM_GRACE_SECONDS,
) -> RunResult:
    """Execute ``argv`` without a shell in a new session, writing stdout/stderr directly to files."""
    if (
        not isinstance(argv, list)
        or len(argv) == 0
        or not all(isinstance(arg, str) for arg in argv)
        or not argv[0]
    ):
        raise InvalidArgvError("argv must be a non-empty list of strings")

    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise ValueError("timeout_s must be a positive number")

    cwd_path = Path(cwd)
    out_path = Path(stdout_path)
    err_path = Path(stderr_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    err_path.parent.mkdir(parents=True, exist_ok=True)

    env, scrubbed_names = _prepare_env(keep_env)

    started_dt = utc_now()
    t0 = time.monotonic()
    timed_out = False
    killed_signal: SignalName | None = None

    stdin_bytes = stdin_text.encode("utf-8") if stdin_text is not None else None

    with open(out_path, "wb") as out_f, open(err_path, "wb") as err_f:
        proc = subprocess.Popen(
            argv,
            cwd=cwd_path,
            env=env,
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=out_f,
            stderr=err_f,
            shell=False,
            start_new_session=True,
        )
        pgid = proc.pid

        try:
            if stdin_bytes is not None:
                proc.communicate(input=stdin_bytes, timeout=timeout_s)
            else:
                proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

            killed_signal = _terminate_group(pgid, proc, _sigterm_grace_s)
            proc.wait()

        # The leader exited normally, but children it spawned (for example background
        # subagent helpers) may still hold the process group (PLAN §7.6 item 5).
        orphans_terminated = False
        if not timed_out and _is_pg_alive(pgid, proc):
            orphans_terminated = True
            _terminate_group(pgid, proc, _sigterm_grace_s)

    duration_s = time.monotonic() - t0
    finished_dt = utc_now()

    exit_code = proc.returncode
    if not timed_out and exit_code is not None and exit_code < 0:
        try:
            killed_signal = SignalName(signal.Signals(-exit_code))
        except ValueError:
            pass

    return RunResult(
        exit_code=exit_code,
        timed_out=timed_out,
        killed_signal=killed_signal,
        duration_s=duration_s,
        started_at=to_iso(started_dt),
        finished_at=to_iso(finished_dt),
        stdout_path=out_path,
        stderr_path=err_path,
        scrubbed_env_names=scrubbed_names,
        orphans_terminated=orphans_terminated,
    )
