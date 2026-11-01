"""OS-level loopback guard for CLIs whose web tool runs on this machine (PLAN §7.3, §7.6 item 2).

M1 acceptance (claude 2.1.316) showed that WebFetch rejects ``localhost`` and dotless hostnames
but still connects to ``127.0.0.1`` and to names that resolve to it (``localtest.me``); only the
TLS upgrade kept it from reading the local server. The guard runs the CLI under a seatbelt
profile that denies every outbound connection to a loopback address, whatever URL, DNS name or
redirect led there.

Evidence of the denial: the kernel logs "deny(1) network-outbound remote:*:<port>", but those
reports are rate-limited and were missing for some ports in M1, so acceptance also runs a control
connection to the same port under the same profile and checks that the local server never saw it
(see ``acceptance._Server.guard_control``).

The seatbelt filter only accepts ``localhost`` or ``*`` as the host, so other private addresses
(the LAN) are not covered; see PLAN §7.4.
"""

from __future__ import annotations

import re
import subprocess
import time
from datetime import datetime
from typing import Any

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
LOOPBACK_DENY_PROFILE = '(version 1)(allow default)(deny network-outbound (remote ip "localhost:*"))'

_DENY_RE = re.compile(r"deny\(\d+\) network-outbound remote:\S*:(\d+)\b")


def wrap(argv: list[str]) -> list[str]:
    """``argv`` prefixed so that the process and its children cannot connect to loopback."""
    return [SANDBOX_EXEC, "-p", LOOPBACK_DENY_PROFILE, *argv]


def is_wrapped(argv: list[str]) -> bool:
    return argv[:3] == [SANDBOX_EXEC, "-p", LOOPBACK_DENY_PROFILE]


def profile_loads(timeout_s: float = 10.0) -> dict[str, Any]:
    """Run /usr/bin/true under the profile; exit 0 shows the profile itself is accepted."""
    return _step(wrap(["/usr/bin/true"]), timeout_s)


def curl_step(host: str, port: int, *, guarded: bool, timeout_s: float = 15.0) -> dict[str, Any]:
    """One curl request to the local test server, ignoring ~/.curlrc (-q) and any proxy."""
    netloc = f"[{host}]" if ":" in host else host
    argv = ["/usr/bin/curl", "-q", "--noproxy", "*", "-s", "-o", "/dev/null", "-m", "5",
            f"http://{netloc}:{port}/__guard_control__"]
    return _step(wrap(argv) if guarded else argv, timeout_s)


CURL_COULDNT_CONNECT = 7


def control_verdict(profile: dict[str, Any], unguarded: dict[str, Any], unguarded_reached: bool,
                    guarded: dict[str, Any], guarded_reached: bool) -> bool:
    """A port counts as denied by the guard only if every control step came out as expected."""
    return (profile.get("exit") == 0 and unguarded.get("exit") == 0 and unguarded_reached
            and guarded.get("exit") == CURL_COULDNT_CONNECT and not guarded_reached)


def _step(argv: list[str], timeout_s: float) -> dict[str, Any]:
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "exit": None, "stderr": type(exc).__name__, "at": time.time()}
    return {"argv": argv, "exit": res.returncode, "stderr": res.stderr[-300:], "at": time.time()}


def denied_ports(log_text: str) -> set[int]:
    """Ports named in kernel sandbox denials of outbound connections."""
    return {int(m.group(1)) for m in _DENY_RE.finditer(log_text)}


def read_denials(start: datetime, end: datetime, timeout_s: float = 120.0) -> str | None:
    """Kernel sandbox network denials logged between ``start`` and ``end`` (local time), or None."""
    fmt = "%Y-%m-%d %H:%M:%S"
    argv = ["/usr/bin/log", "show", "--style", "compact",
            "--start", start.astimezone().strftime(fmt), "--end", end.astimezone().strftime(fmt),
            "--predicate", 'eventMessage CONTAINS "network-outbound"']
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    return "\n".join(line for line in res.stdout.splitlines() if "Sandbox:" in line and "deny(" in line)
