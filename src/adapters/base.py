"""Shared adapter contract for the claude, gemini (agy) and gpt (codex) lines.

See docs/tasks/M1-adapters.md and PLAN.md §5.3, §5.5, §7.3, §7.5, §7.7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from flab.runner import RunResult

PLAYERS: tuple[str, ...] = ("claude", "gemini", "gpt")
PROFILES: tuple[str, ...] = ("tier1", "tier2", "format_repair", "acceptance")
URL_STATES: tuple[str, ...] = ("attempted", "blocked", "opened", "unknown")
MODEL_CHECKS: tuple[str, ...] = ("verified", "unverified", "mismatch", "undetermined")
ERROR_TYPES: tuple[str, ...] = (
    "quota_exhausted",
    "timeout",
    "invalid_output",
    "tool_denied",
    "cli_error",
    "model_mismatch",
    "budget_skipped",
    "disabled",
)
USAGE_KEYS: tuple[str, ...] = ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens")


@dataclass(frozen=True)
class AdapterRequest:
    player: str
    profile: str
    prompt: str
    schema: dict[str, Any]
    workdir: Path
    raw_dir: Path
    model: str
    effort: str
    timeout_s: int

    def __post_init__(self) -> None:
        if self.player not in PLAYERS:
            raise ValueError(f"unknown player: {self.player!r}")
        if self.profile not in PROFILES:
            raise ValueError(f"unknown profile: {self.profile!r}")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")


@dataclass(frozen=True)
class UrlEvent:
    url: str | None
    host: str | None
    state: str
    source: str

    def __post_init__(self) -> None:
        if self.state not in URL_STATES:
            raise ValueError(f"unknown url state: {self.state!r}")


@dataclass
class AdapterResult:
    argv: list[str]
    run: RunResult | None
    cli_version: str | None
    completed: bool
    final_text: str | None
    structured: dict[str, Any] | None
    reported_model_id: str | None
    model_check: str
    usage_raw: dict[str, Any] | None
    usage: dict[str, int | None] = field(default_factory=lambda: {k: None for k in USAGE_KEYS})
    tool_call_counts: dict[str, int] | None = None
    url_events: list[UrlEvent] = field(default_factory=list)
    denied_actions: list[dict[str, Any]] | None = None
    subagent_count: int | None = None
    error_type: str | None = None
    error: str | None = None
    # What the CLI itself declared to the model (tool list, MCP servers, skills, plugins, agents).
    # Evidence for PLAN §7.6 "tool not provided" and auto-load checks; None when the CLI does not report it.
    declared: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.model_check not in MODEL_CHECKS:
            raise ValueError(f"unknown model_check: {self.model_check!r}")
        if self.error_type is not None and self.error_type not in ERROR_TYPES:
            raise ValueError(f"unknown error_type: {self.error_type!r}")
        missing = set(USAGE_KEYS) - set(self.usage)
        if missing:
            raise ValueError(f"usage is missing keys: {sorted(missing)}")


class Adapter(Protocol):
    """Functions every adapter module exposes."""

    def build_argv(self, req: AdapterRequest) -> list[str]: ...

    def parse(self, req: AdapterRequest, run_result: RunResult | None) -> AdapterResult: ...

    def run(self, req: AdapterRequest) -> AdapterResult: ...

    def cli_version(self) -> str | None: ...


def url_host(url: str | None) -> str | None:
    """Lower-cased hostname of ``url``, or None when it cannot be determined."""
    if not url:
        return None
    try:
        host = urlsplit(url.strip()).hostname
    except ValueError:
        return None
    return host.lower().rstrip(".") if host else None


def is_metaculus_host(host: str | None) -> bool:
    """True for metaculus.com and any of its subdomains (PLAN §3.3)."""
    if not host:
        return False
    host = host.lower().rstrip(".")
    return host == "metaculus.com" or host.endswith(".metaculus.com")


def model_check_from_ids(requested: str, reported: str | None, aliases: dict[str, str] | None = None) -> str:
    """Compare the requested model with the CLI-reported one; aliases map short names (e.g. 'haiku') to IDs."""
    if reported is None:
        return "unverified"
    wanted = (aliases or {}).get(requested, requested)
    return "verified" if reported == wanted else "mismatch"
