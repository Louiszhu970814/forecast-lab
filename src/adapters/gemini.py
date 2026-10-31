"""Antigravity CLI (agy) adapter for the gemini line.

See docs/tasks/M1-adapters.md, docs/tasks/M1-W5.md, and PLAN.md §5.3, §5.5, §7.3, §7.5, §7.7.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from adapters.base import (
    MODEL_CHECKS,
    USAGE_KEYS,
    AdapterRequest,
    AdapterResult,
    UrlEvent,
    model_check_from_ids,
    url_host,
)
from flab import runner as flab_runner
from flab.runner import RunResult

# agy authenticates via the OS keyring (keyringAuth) and ~/.gemini/antigravity-cli
# (located via HOME, which flab.runner preserves by default). No API key, _TOKEN,
# or SECRET environment variable should be kept so that execution never falls back
# to API billing (PLAN.md §5.3, D19).
KEEP_ENV: tuple[str, ...] = ()

# Default state for Tier 2 custom agent delegation (disabled in M1 until §7.6
# subagent isolation acceptance passes).
TIER2_SUBAGENTS_ENABLED_DEFAULT: bool = False

TIER1_ALLOW_PERMISSIONS: tuple[str, ...] = ("read_url(*)",)
TIER1_DENY_PERMISSIONS: tuple[str, ...] = (
    "command(*)",
    "write_file(*)",
    "read_file(*)",
    "execute_url(*)",
    "mcp(*)",
    "read_url(metaculus.com)",
)
FORMAT_REPAIR_DENY_PERMISSIONS: tuple[str, ...] = (
    "command(*)",
    "write_file(*)",
    "read_file(*)",
    "read_url(*)",
    "execute_url(*)",
    "mcp(*)",
)

# Mapping from CLI model IDs (from `agy models`) to human-readable backend labels
# logged in `agy_cli.log` via `Propagating selected model override to backend: label="..."`.
MODEL_LABELS: dict[str, str] = {
    "gemini-4-argon-medium": "Gemini 4 Argon (Medium)",
    "gemini-4-argon-high": "Gemini 4 Argon (High)",
    "gemini-4-argon-low": "Gemini 4 Argon (Low)",
    "gemini-3.8-flash-high": "Gemini 3.8 Flash (High)",
    "gemini-3.8-flash-medium": "Gemini 3.8 Flash (Medium)",
    "gemini-3.8-flash-low": "Gemini 3.8 Flash (Low)",
    "claude-opus-5-5-low": "Claude Opus 5.5 (Low)",
    "claude-opus-5-5-medium": "Claude Opus 5.5 (Medium)",
    "claude-opus-5-5-high": "Claude Opus 5.5 (High)",
    "claude-sonnet-5-5-low": "Claude Sonnet 5.5 (Low)",
    "claude-sonnet-5-5-medium": "Claude Sonnet 5.5 (Medium)",
    "claude-sonnet-5-5-high": "Claude Sonnet 5.5 (High)",
    "gpt-oss-120b-medium": "GPT-OSS 120B (Medium)",
}

FORMAT_REPAIR_AGENT_FILENAME = "format_repair_agent.md"
TIER2_MAIN_AGENT_FILENAME = "tier2_main_agent.md"
TIER2_SUBAGENT_FILENAME = "tier2_research_subagent.md"

FORMAT_REPAIR_AGENT_CONTENT = """---
name: flab-format-repair
description: Minimal format-repair agent with all default tools and subagents excluded.
excludeDefaultComponents: true
inheritCustomizations: false
inheritMcp: false
tools: []
agents: []
---
You are a format repair agent. Convert the provided raw text into the required JSON schema without performing any search or tool actions.
"""

TIER2_MAIN_AGENT_CONTENT = """---
name: flab-tier2-main
description: Minimal Tier 2 forecasting agent with web search, URL reading, and one research subagent.
excludeDefaultComponents: true
inheritCustomizations: false
inheritMcp: false
tools:
  - search_web
  - read_url_content
  - invoke_subagent
agents:
  - ./tier2_research_subagent.md
---
You are a forecasting agent. Use only web search, URL reading, and the declared research subagent.
"""

TIER2_SUBAGENT_CONTENT = """---
name: flab-tier2-research
description: Minimal read-only web research subagent for Tier 2 forecasting.
subagent: true
model: inherit
excludeDefaultComponents: true
inheritCustomizations: false
inheritMcp: false
tools:
  - search_web
  - read_url_content
---
You are a research subagent. Use only web search and URL reading to gather evidence.
"""

_LOG_VERSION_RE = re.compile(r"Language server version:\s*(\S+)")
_LOG_MODEL_LABEL_RE = re.compile(
    r'Propagating selected model override to backend:\s*label="([^"]+)"'
)
_LOG_SOFT_DENY_RE = re.compile(
    r'Print mode:\s*soft-denying tool confirmation\s*"([^"]+)"(?:\s*at step\s*(\d+))?'
)
_LOG_DENY_RULE_RE = re.compile(r"Matches user-configured deny rule", re.IGNORECASE)
_HYPOTHETICAL_DENY_RULE_RE = re.compile(
    r"(?:\bif\b|\bwhen\b|\bwhether\b|\bexample\b|\bsuch as\b|\be\.g\.\b)[^.\n]{0,80}"
    r"Matches user-configured deny rule",
    re.IGNORECASE,
)
_NO_BROWSER_TOOL_RE = re.compile(
    r"(?:no\s+browser\s+tool|do\s+not\s+have\s+a\s+browser|no\s+tool\s+available|"
    r"browser\s+tool\s+unavailable|cannot\s+open\s+urls?\s+without\s+a\s+browser|"
    r"lack\s+a\s+browser\s+tool)",
    re.IGNORECASE,
)
_ITEM_DENY_EVIDENCE_RE = re.compile(
    r"(?:Matches user-configured deny rule|permission\s+denied|denied\s+by\s+(?:a\s+)?(?:user-configured\s+)?(?:permission\s+|deny\s+)?rule)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")
_READ_URL_ARG_RE = re.compile(r"read_?url\s*\(\s*([^)\s]+)\s*\)", re.IGNORECASE)
_QUOTA_ERROR_RE = re.compile(
    r"(quota\s+exceeded|resource_exhausted|rate\s+limit\s+exceeded|out\s+of\s+quota|weekly\s+limit)",
    re.IGNORECASE,
)


def build_settings(profile: str) -> dict[str, Any]:
    """Build the ``--settings`` JSON object for the requested execution profile."""
    if profile in ("tier1", "tier2", "acceptance"):
        return {
            "agentMode": "",
            "useG1Credits": False,
            "permissions": {
                "allow": list(TIER1_ALLOW_PERMISSIONS),
                "deny": list(TIER1_DENY_PERMISSIONS),
            },
        }
    if profile == "format_repair":
        return {
            "agentMode": "",
            "useG1Credits": False,
            "permissions": {
                "allow": [],
                "deny": list(FORMAT_REPAIR_DENY_PERMISSIONS),
            },
        }
    raise ValueError(f"unknown profile: {profile!r}")


def write_format_repair_agent_file(workdir: Path) -> Path:
    """Write the tool-less custom agent definition for ``format_repair`` (PLAN §7.5) into ``workdir``."""
    workdir.mkdir(parents=True, exist_ok=True)
    agent_path = workdir / FORMAT_REPAIR_AGENT_FILENAME
    agent_path.write_text(FORMAT_REPAIR_AGENT_CONTENT, encoding="utf-8")
    return agent_path


def write_tier2_agent_files(workdir: Path) -> Path:
    """Write the minimal custom agent and research subagent definitions (PLAN §7.3) into ``workdir``."""
    main_path = workdir / TIER2_MAIN_AGENT_FILENAME
    sub_path = workdir / TIER2_SUBAGENT_FILENAME
    workdir.mkdir(parents=True, exist_ok=True)
    sub_path.write_text(TIER2_SUBAGENT_CONTENT, encoding="utf-8")
    main_path.write_text(TIER2_MAIN_AGENT_CONTENT, encoding="utf-8")
    return main_path


def prepare_agent_files(req: AdapterRequest) -> Path | None:
    """Write custom agent definition files into ``req.workdir`` during ``run()`` preparation."""
    if req.profile == "format_repair":
        return write_format_repair_agent_file(req.workdir)
    if req.profile == "tier2":
        return write_tier2_agent_files(req.workdir)
    return None


def build_argv(
    req: AdapterRequest,
    *,
    subagents_enabled: bool = TIER2_SUBAGENTS_ENABLED_DEFAULT,
) -> list[str]:
    """Assemble the CLI argument list for ``agy`` without executing it or writing any files."""
    if req.player != "gemini":
        raise ValueError(f"gemini adapter cannot build argv for player={req.player!r}")

    settings_json = json.dumps(build_settings(req.profile), separators=(",", ":"))
    schema_path = req.raw_dir / "schema.json"
    log_path = req.raw_dir / "agy_cli.log"

    argv: list[str] = [
        "agy",
        "-p",
        req.prompt,
        "--model",
        req.model,
        "--effort",
        req.effort,
        "--output-format",
        "json",
        "--json-schema",
        str(schema_path),
        "--disable-slash-commands",
        "--print-timeout",
        f"{req.timeout_s}s",
        "--sandbox",
        "--log-file",
        str(log_path),
        "--settings",
        settings_json,
    ]

    if req.profile == "format_repair":
        format_repair_agent_path = req.workdir / FORMAT_REPAIR_AGENT_FILENAME
        argv.extend(["--agent", str(format_repair_agent_path)])
    elif req.profile == "tier2" and subagents_enabled:
        main_agent_path = req.workdir / TIER2_MAIN_AGENT_FILENAME
        argv.extend(["--agent", str(main_agent_path)])

    return argv


def _parse_changelog_version(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            version = stripped.rstrip(":").strip()
            return version or None
    return None


def cli_version(*, timeout_s: float = 10.0) -> str | None:
    """Return the installed ``agy`` version from the first line of ``agy changelog``."""
    try:
        proc = subprocess.run(
            ["agy", "changelog"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    return _parse_changelog_version(proc.stdout)


def _find_stdout_file(raw_dir: Path, run_result: RunResult | None) -> Path | None:
    if run_result is not None and run_result.stdout_path.is_file():
        return run_result.stdout_path
    for name in ("stdout.json", "output.json", "response.json", "agy.json", "stdout.txt", "stdout"):
        candidate = raw_dir / name
        if candidate.is_file():
            return candidate
    if raw_dir.is_dir():
        for path in sorted(raw_dir.glob("*.json")):
            if path.name != "schema.json":
                return path
    return None


def _find_log_file(raw_dir: Path) -> Path | None:
    for name in ("agy_cli.log", "cli.log", "agy.log"):
        candidate = raw_dir / name
        if candidate.is_file():
            return candidate
    if raw_dir.is_dir():
        for path in sorted(raw_dir.glob("*.log")):
            return path
    return None


def _find_stderr_file(raw_dir: Path, run_result: RunResult | None) -> Path | None:
    if run_result is not None and run_result.stderr_path.is_file():
        return run_result.stderr_path
    for name in ("stderr.txt", "agy.stderr", "stderr"):
        candidate = raw_dir / name
        if candidate.is_file():
            return candidate
    if raw_dir.is_dir():
        for path in sorted(raw_dir.glob("*.stderr")):
            return path
    return None


def _int_or_none(val: Any) -> int | None:
    if isinstance(val, int) and not isinstance(val, bool):
        return val
    return None


def _parse_log(log_path: Path | None) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    """Extract (cli_version, model_label, log_denials) from ``agy_cli.log``."""
    if log_path is None or not log_path.is_file():
        return None, None, []

    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None, []

    version: str | None = None
    model_label: str | None = None
    log_denials: list[dict[str, Any]] = []

    for line in content.splitlines():
        if version is None:
            m_ver = _LOG_VERSION_RE.search(line)
            if m_ver:
                version = m_ver.group(1)

        m_model = _LOG_MODEL_LABEL_RE.search(line)
        if m_model:
            model_label = m_model.group(1)

        m_soft = _LOG_SOFT_DENY_RE.search(line)
        if m_soft:
            entry: dict[str, Any] = {
                "display_name": m_soft.group(1),
                "source": "cli_log",
                "evidence_strength": "cli_verified",
                "reason": "soft_denied_in_print_mode",
            }
            if m_soft.group(2) is not None:
                entry["step"] = int(m_soft.group(2))
            log_denials.append(entry)
        elif _LOG_DENY_RULE_RE.search(line):
            log_denials.append(
                {
                    "action": None,
                    "source": "cli_log",
                    "evidence_strength": "cli_verified",
                    "reason": "Matches user-configured deny rule",
                    "log_line": line.strip(),
                }
            )

    return version, model_label, log_denials


def _has_non_hypothetical_deny_rule(text: str) -> bool:
    """Return True if ``text`` reports ``Matches user-configured deny rule`` outside hypothetical quotes."""
    if not _LOG_DENY_RULE_RE.search(text):
        return False
    scrubbed = _HYPOTHETICAL_DENY_RULE_RE.sub("", text)
    return bool(_LOG_DENY_RULE_RE.search(scrubbed))


def _collect_denied_actions(
    data: dict[str, Any] | None,
    final_text: str | None,
    structured: dict[str, Any] | None,
    log_denials: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Combine ask-rule auto-denials (JSON + log) and deny-rule rejections (model output + log)."""
    combined: list[dict[str, Any]] = []

    # 1. Ask-rule auto-denials reported in JSON outer frame (`denied_actions`).
    json_denials_present = False
    if isinstance(data, dict) and isinstance(data.get("denied_actions"), list):
        json_denials_present = True
        for item in data["denied_actions"]:
            if isinstance(item, dict):
                entry = dict(item)
                entry.setdefault("source", "json_denied_actions")
                entry.setdefault("evidence_strength", "cli_verified")
                combined.append(entry)

    # 2. Soft-denials or deny-rule entries recorded in `agy_cli.log`.
    for log_entry in log_denials:
        matched = False
        for existing in combined:
            if (
                existing.get("source") == "json_denied_actions"
                and existing.get("display_name") == log_entry.get("display_name")
                and log_entry.get("display_name") is not None
            ):
                if "step" in log_entry:
                    existing["step"] = log_entry["step"]
                existing["log_confirmed"] = True
                matched = True
                break
        if not matched:
            combined.append(log_entry)

    # 3. Deny-rule blocks surfaced to the model ("Matches user-configured deny rule").
    refused_items: list[str] = []
    if isinstance(structured, dict) and isinstance(structured.get("refused"), list):
        refused_items = [str(x) for x in structured["refused"] if x is not None]

    # Check if any refused item itself carries deny-rule text vs non-permission reasons.
    explicit_deny_items = [
        item
        for item in refused_items
        if _ITEM_DENY_EVIDENCE_RE.search(item) and not _NO_BROWSER_TOOL_RE.search(item)
    ]
    non_contradicted_items = [
        item for item in refused_items if not _NO_BROWSER_TOOL_RE.search(item)
    ]

    context_texts: list[str] = []
    if isinstance(structured, dict):
        for key in ("answer", "error", "note"):
            val = structured.get(key)
            if isinstance(val, str):
                context_texts.append(val)
        if not context_texts and final_text and "refused" not in structured:
            context_texts.append(final_text)
    elif final_text:
        context_texts.append(final_text)

    has_context_deny_rule = any(_has_non_hypothetical_deny_rule(t) for t in context_texts)

    if explicit_deny_items:
        for item_str in explicit_deny_items:
            combined.append(
                {
                    "action": item_str,
                    "reason": "Matches user-configured deny rule",
                    "source": "model_output_deny_rule",
                    "evidence_strength": "model_self_report",
                }
            )
    elif has_context_deny_rule:
        # If structured output is a forecast without a `refused` list and the phrase only
        # appeared in general reasoning text without any refused items, only record when
        # there is no contradictory indicator.
        if refused_items:
            for item_str in non_contradicted_items:
                combined.append(
                    {
                        "action": item_str,
                        "reason": "Matches user-configured deny rule",
                        "source": "model_output_deny_rule",
                        "evidence_strength": "model_self_report",
                    }
                )
        elif not (isinstance(structured, dict) and "refused" in structured):
            combined.append(
                {
                    "action": None,
                    "reason": "Matches user-configured deny rule",
                    "source": "model_output_deny_rule",
                    "evidence_strength": "model_self_report",
                }
            )

    if combined:
        return combined
    if json_denials_present:
        return []
    return None


def _normalize_url_for_match(u: str) -> tuple[str, str, str, str, str] | None:
    # Nothing but surrounding whitespace is stripped: a trailing "." or ";" can be part of the resource.
    cleaned = u.strip()
    try:
        parts = urlsplit(cleaned)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    # Only an empty path and "/" are the same resource; "/a/" and "/a" may differ.
    path = parts.path if parts.path not in ("", "/") else "/"
    return (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, parts.fragment)


def _urls_match(cli_target: str, prose_url: str) -> bool:
    """Exact CLI target versus a URL taken from model output (which may carry sentence punctuation)."""
    # Exact resource match only. Trailing "." or ";" may belong to the URL, so nothing is stripped;
    # a URL quoted with sentence punctuation stays "unknown" rather than being claimed as blocked.
    norm_c = _normalize_url_for_match(cli_target)
    norm_t = _normalize_url_for_match(prose_url)
    if norm_c is None or norm_t is None:
        return cli_target.strip() == prose_url.strip()
    return norm_c == norm_t


def _iter_entry_strings(val: Any) -> list[str]:
    if isinstance(val, str):
        return [val]
    if isinstance(val, dict):
        out: list[str] = []
        for k, v in val.items():
            if k in ("source", "evidence_strength", "reason", "step", "log_confirmed"):
                continue
            out.extend(_iter_entry_strings(v))
        return out
    if isinstance(val, (list, tuple)):
        out = []
        for item in val:
            out.extend(_iter_entry_strings(item))
        return out
    return []


def _cli_entry_matches_url(entry: dict[str, Any], url: str) -> bool:
    """True iff ``entry`` is a CLI-verified denial of a page read whose own target is ``url``.

    Only the read-URL action's argument (``read_url(<target>)``) or an explicit ``url`` field of a
    read-URL entry counts; URLs mentioned elsewhere (e.g. inside a denied shell command) do not.
    """
    if entry.get("source", "cli_log") not in ("json_denied_actions", "cli_log"):
        return False
    if entry.get("evidence_strength", "cli_verified") != "cli_verified":
        return False
    action = entry.get("action") if isinstance(entry.get("action"), str) else ""
    display = entry.get("display_name") if isinstance(entry.get("display_name"), str) else ""
    targets: list[str] = []
    m = _READ_URL_ARG_RE.fullmatch(action.strip())
    if m:
        targets.append(m.group(1).strip("\"'"))
    elif action.strip().lower() in ("read_url", "readurl") or display.strip().lower() in ("readurl", "read_url"):
        if isinstance(entry.get("url"), str):
            targets.append(entry["url"])
    for arg in targets:
        if not arg or arg == "*":
            continue
        if arg.startswith(("http://", "https://")):
            if _urls_match(arg, url):
                return True
        elif "/" in arg:
            if _urls_match(f"https://{arg}", url) or _urls_match(f"http://{arg}", url):
                return True
        else:
            target_h = url_host(url)
            arg_h = arg.lower().strip(".")
            if target_h and arg_h and (target_h == arg_h or target_h.endswith("." + arg_h)):
                return True
    return False


def _has_cli_url_denial(
    url: str,
    log_denials: list[dict[str, Any]],
    denied_actions: list[dict[str, Any]] | None,
) -> bool:
    """Return True if CLI log or JSON denied_actions confirms a URL/read_url denial targeting ``url``."""
    for entry in log_denials:
        if isinstance(entry, dict) and _cli_entry_matches_url(entry, url):
            return True
    if denied_actions:
        for entry in denied_actions:
            if isinstance(entry, dict) and _cli_entry_matches_url(entry, url):
                return True
    return False


def _extract_url_events(
    structured: dict[str, Any] | None,
    final_text: str | None = None,
    log_denials: list[dict[str, Any]] | None = None,
    denied_actions: list[dict[str, Any]] | None = None,
) -> list[UrlEvent]:
    """Extract URL events conservatively from structured output and denial records.

    - ``opened_urls``: recorded as ``state="opened"`` unless contradicted by a missing-browser notice.
    - ``refused``: recorded as ``state="blocked"`` ONLY when backed by a CLI denial targeting that URL
      or explicit deny-rule evidence without a missing-tool explanation; otherwise ``state="unknown"``.
    - ``sources``: citations from search snippets do not prove a page was opened, so URLs
      present only in ``sources`` are recorded as ``state="unknown"`` with source
      ``"structured_output.sources:citation_only"``.
    """
    events: list[UrlEvent] = []
    if not isinstance(structured, dict):
        return events

    log_denials = log_denials or []
    answer_text = str(structured.get("answer") or "")
    combined_context = answer_text if answer_text else (final_text or "")
    context_no_browser = bool(_NO_BROWSER_TOOL_RE.search(combined_context))
    context_has_deny_rule = _has_non_hypothetical_deny_rule(combined_context)

    seen_opened: set[str] = set()

    opened_urls = structured.get("opened_urls")
    if isinstance(opened_urls, list):
        for item in opened_urls:
            if isinstance(item, str):
                for url in _URL_RE.findall(item) or ([item] if url_host(item) else []):
                    if context_no_browser:
                        events.append(
                            UrlEvent(
                                url=url,
                                host=url_host(url),
                                state="unknown",
                                source="structured_output.opened_urls:contradicted_by_no_browser",
                            )
                        )
                    else:
                        seen_opened.add(url)
                        events.append(
                            UrlEvent(
                                url=url,
                                host=url_host(url),
                                state="opened",
                                source="structured_output.opened_urls:model_report",
                            )
                        )

    refused = structured.get("refused")
    if isinstance(refused, list):
        # Check if some items have explicit per-item reasons
        any_item_specific_reason = any(
            isinstance(x, str)
            and bool(_ITEM_DENY_EVIDENCE_RE.search(x) or _NO_BROWSER_TOOL_RE.search(x))
            for x in refused
        )
        for item in refused:
            if not isinstance(item, str):
                continue
            item_no_browser = bool(_NO_BROWSER_TOOL_RE.search(item))
            item_has_deny = bool(_ITEM_DENY_EVIDENCE_RE.search(item))
            for url in _URL_RE.findall(item):
                if item_no_browser:
                    state = "unknown"
                    src = "structured_output.refused:no_browser_tool"
                elif _has_cli_url_denial(url, log_denials, denied_actions):
                    state = "blocked"
                    src = "structured_output.refused:cli_verified"
                elif item_has_deny:
                    state = "blocked"
                    src = "structured_output.refused:deny_rule_model_report"
                elif context_no_browser:
                    state = "unknown"
                    src = "structured_output.refused:no_browser_tool"
                elif context_has_deny_rule and not any_item_specific_reason:
                    state = "blocked"
                    src = "structured_output.refused:deny_rule_model_report"
                else:
                    state = "unknown"
                    src = "structured_output.refused:unverified_reason"

                events.append(
                    UrlEvent(
                        url=url,
                        host=url_host(url),
                        state=state,
                        source=src,
                    )
                )

    sources = structured.get("sources")
    if isinstance(sources, list):
        for src_item in sources:
            url_str: str | None = None
            if isinstance(src_item, str):
                url_str = src_item
            elif isinstance(src_item, dict) and isinstance(src_item.get("url"), str):
                url_str = src_item["url"]
            if url_str and url_str not in seen_opened:
                events.append(
                    UrlEvent(
                        url=url_str,
                        host=url_host(url_str),
                        state="unknown",
                        source="structured_output.sources:citation_only",
                    )
                )

    return events


def apply_quota_model_check(result: AdapterResult, quota_check: str) -> AdapterResult:
    """Update ``result.model_check`` (and ``error_type`` on mismatch) using ``quota.judge`` output.

    Per PLAN §7.7, ``agy`` model verification is determined from ``/usage`` quota diffs rather
    than the CLI log's model override label alone. The main workflow calls ``quota.judge(...)``
    around ``run(req)`` and passes the resulting status here.
    """
    if quota_check not in MODEL_CHECKS:
        raise ValueError(f"unknown quota_check: {quota_check!r}")

    result.model_check = quota_check
    if result.usage_raw is None:
        result.usage_raw = {}
    result.usage_raw["quota_model_check"] = quota_check

    if quota_check == "mismatch":
        result.error_type = "model_mismatch"
        if not result.error:
            result.error = (
                f"quota verification reported model_mismatch "
                f"(reported_model_id={result.reported_model_id!r})"
            )
    return result


def parse(req: AdapterRequest, run_result: RunResult | None) -> AdapterResult:
    """Parse ``agy`` output and log files from ``req.raw_dir`` without invoking the CLI or writing files."""
    argv = build_argv(req)
    stdout_path = _find_stdout_file(req.raw_dir, run_result)
    log_path = _find_log_file(req.raw_dir)
    stderr_path = _find_stderr_file(req.raw_dir, run_result)

    log_version, model_label, log_denials = _parse_log(log_path)

    stderr_text = ""
    if stderr_path is not None and stderr_path.is_file():
        try:
            stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            stderr_text = ""

    raw_stdout_text = ""
    data: dict[str, Any] | None = None
    json_parse_error: str | None = None

    if stdout_path is not None and stdout_path.is_file():
        try:
            raw_stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            json_parse_error = f"failed to read stdout: {exc}"
        else:
            stripped = raw_stdout_text.strip()
            if stripped:
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict):
                        data = parsed
                    else:
                        json_parse_error = "stdout JSON root is not an object"
                except json.JSONDecodeError as exc:
                    json_parse_error = f"invalid stdout JSON: {exc}"
            else:
                json_parse_error = "stdout is empty"
    else:
        json_parse_error = "stdout file not found"

    status: str | None = None
    finished_reason: str | None = None
    outer_error: str | None = None
    final_text: str | None = None
    structured: dict[str, Any] | None = None
    usage_raw: dict[str, Any] | None = None
    usage: dict[str, int | None] = {k: None for k in USAGE_KEYS}

    if data is not None:
        raw_status = data.get("status")
        status = str(raw_status) if raw_status is not None else None

        raw_finished = data.get("finished_reason")
        if raw_finished is not None and str(raw_finished).strip():
            finished_reason = str(raw_finished).strip()

        raw_err = data.get("error")
        if isinstance(raw_err, str) and raw_err.strip():
            outer_error = raw_err.strip()
        elif isinstance(raw_err, dict):
            outer_error = json.dumps(raw_err, ensure_ascii=False)

        raw_resp = data.get("response")
        if isinstance(raw_resp, str) and raw_resp != "":
            final_text = raw_resp

        raw_struct = data.get("structured_output")
        if isinstance(raw_struct, dict):
            structured = raw_struct

        raw_usage = data.get("usage")
        if isinstance(raw_usage, dict):
            usage_raw = dict(raw_usage)
            if "num_turns" in data and isinstance(data["num_turns"], int):
                usage_raw.setdefault("num_turns", data["num_turns"])
            usage = {
                "input_tokens": _int_or_none(raw_usage.get("input_tokens")),
                "output_tokens": _int_or_none(raw_usage.get("output_tokens")),
                "reasoning_tokens": _int_or_none(raw_usage.get("thinking_tokens")),
                "cached_tokens": _int_or_none(raw_usage.get("cache_read_tokens")),
            }

    # Log model label is preserved as auxiliary evidence in `usage_raw` and `reported_model_id`;
    # adapter itself returns `model_check = "unverified"` until `quota.judge` updates it (PLAN §7.7).
    aliases = MODEL_LABELS if model_label != req.model else None
    log_model_check = model_check_from_ids(req.model, model_label, aliases)
    if model_label is not None:
        if usage_raw is None:
            usage_raw = {}
        usage_raw["model_label"] = model_label
        usage_raw["log_model_check"] = log_model_check

    model_check = "unverified"

    denied_actions = _collect_denied_actions(data, final_text, structured, log_denials)
    url_events = _extract_url_events(
        structured,
        final_text=final_text,
        log_denials=log_denials,
        denied_actions=denied_actions,
    )

    timed_out = bool(run_result is not None and run_result.timed_out) or (
        finished_reason == "print_timeout"
    )
    exit_ok = run_result is None or (run_result.exit_code == 0 and not run_result.timed_out)

    completed = bool(
        exit_ok
        and data is not None
        and status == "SUCCESS"
        and not finished_reason
        and final_text is not None
        and final_text.strip() != ""
    )

    error_type: str | None = None
    error_msg: str | None = None

    combined_error_text = " ".join(
        part for part in (outer_error, stderr_text, raw_stdout_text if data is None else "") if part
    )

    if timed_out:
        completed = False
        error_type = "timeout"
        error_msg = outer_error or stderr_text or f"timed out (finished_reason={finished_reason!r})"
    elif not completed:
        if denied_actions:
            error_type = "tool_denied"
            error_msg = outer_error or "tool action denied in non-interactive mode"
        elif _QUOTA_ERROR_RE.search(combined_error_text):
            error_type = "quota_exhausted"
            error_msg = outer_error or stderr_text or "quota exhausted"
        elif data is not None and exit_ok and status == "SUCCESS" and not finished_reason and not (final_text and final_text.strip()):
            error_type = "invalid_output"
            error_msg = "empty response from CLI"
        else:
            error_type = "cli_error"
            error_msg = (
                outer_error
                or stderr_text
                or (f"finished_reason={finished_reason}" if finished_reason else None)
                or (
                    f"non-zero exit code ({run_result.exit_code})"
                    if run_result is not None and run_result.exit_code != 0
                    else None
                )
                or json_parse_error
                or "CLI execution failed"
            )

    return AdapterResult(
        argv=argv,
        run=run_result,
        cli_version=log_version,
        completed=completed,
        final_text=final_text,
        structured=structured,
        reported_model_id=model_label,
        model_check=model_check,
        usage_raw=usage_raw,
        usage=usage,
        tool_call_counts=None,
        url_events=url_events,
        denied_actions=denied_actions,
        subagent_count=None,
        error_type=error_type,
        error=error_msg,
    )


def run(
    req: AdapterRequest,
    *,
    subagents_enabled: bool = TIER2_SUBAGENTS_ENABLED_DEFAULT,
) -> AdapterResult:
    """Write schema/agent files, execute ``agy`` via ``flab.runner.run``, and parse the output."""
    req.raw_dir.mkdir(parents=True, exist_ok=True)
    req.workdir.mkdir(parents=True, exist_ok=True)

    schema_path = req.raw_dir / "schema.json"
    schema_path.write_text(json.dumps(req.schema, ensure_ascii=False), encoding="utf-8")

    prepare_agent_files(req)

    argv = build_argv(req, subagents_enabled=subagents_enabled)
    stdout_path = req.raw_dir / "stdout.json"
    stderr_path = req.raw_dir / "stderr.txt"

    run_result = flab_runner.run(
        argv,
        cwd=req.workdir,
        stdin_text=None,
        timeout_s=req.timeout_s,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        keep_env=KEEP_ENV,
    )

    result = parse(req, run_result)
    result.argv = argv
    if result.cli_version is None:
        result.cli_version = cli_version()
    return result
