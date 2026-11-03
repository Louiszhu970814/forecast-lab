"""Read-only process evidence for D121 recovery. Never signal any process."""
from __future__ import annotations

import subprocess
from datetime import timedelta
from pathlib import Path

from flab.fail_soft import CLEANUP_GRACE_S
from flab.timeutil import parse_iso, to_iso


def boot_id():
    try:
        result = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"],
                                capture_output=True, text=True, timeout=5, check=True)
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def metadata(workdir, raw_dir, timeout_s, started):
    return {"workdir": str(workdir), "raw_dir": str(raw_dir), "timeout_s": timeout_s,
            "hard_deadline_utc": to_iso(started + timedelta(seconds=timeout_s)),
            "boot_id": boot_id()}


def expired(row, cfg, now):
    try:
        profile = "repair" if row.get("attempt_kind", row.get("profile")) == "format_repair" else f"tier{row.get('tier', 1)}"
        deadline = (parse_iso(row["hard_deadline_utc"]) if row.get("hard_deadline_utc") else
                    parse_iso(row.get("started_at_utc") or row["started_at"]) +
                    timedelta(seconds=row.get("timeout_s", cfg["timeouts_s"][profile])))
        return now >= deadline + timedelta(seconds=CLEANUP_GRACE_S)
    except (KeyError, TypeError, ValueError):
        return False


def legacy_workdirs(base: Path, player: str) -> list[Path]:
    """Scope named legacy directories to this line; retain unidentifiable ones.

    Older random names cannot establish ownership, so they still require a
    process check. No directory or session contents are read or deleted.
    """
    try:
        return [p for p in base.iterdir() if
                not p.name.startswith(("flab-claude-", "flab-gpt-", "flab-gemini-"))
                or p.name.startswith(f"flab-{player}-")]
    except FileNotFoundError:
        return []
    except OSError:
        # Missing inventory is not evidence that no old process exists.
        return [base]


def no_process(paths, *, run=subprocess.run):
    """Require both argument and cwd/open-file scans; missing evidence is not absence."""
    targets = {s for p in paths for s in (str(Path(p).absolute()), str(Path(p).resolve()))}
    if not targets:
        return False
    try:
        for argv in (["/bin/ps", "-axo", "pid=,command="],
                     ["/usr/sbin/lsof", "-nP", "-Fpn"]):
            result = run(argv, capture_output=True, text=True, timeout=15, check=False)
            if result.returncode or result.stderr.strip() or not result.stdout.strip():
                return False
            if any(target in result.stdout for target in targets):
                return False
        return True
    except (OSError, subprocess.SubprocessError):
        return False
