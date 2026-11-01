"""Antigravity CLI (agy) adapter for the gemini line.

See docs/tasks/M1-adapters.md, docs/tasks/M1-W5.md, docs/tasks/M1-W2W3.md and PLAN.md §5.3,
§5.5, §7.3, §7.5, §7.7.

Findings that shape this adapter (agy 1.4.12, M1 acceptance):
- The default print-mode agent has no page-reading tool but does have ``schedule`` and
  ``invoke_subagent``. Each run therefore selects a minimal custom agent that the adapter writes
  to ``<workdir>/.agents/agents/`` (agy's project-level agent location, so no global setting
  changes) with ``excludeDefaultComponents: true``.
- ``read_url_content`` fetches on Google's servers and saves the page to
  ``~/.gemini/antigravity-cli/brain/<conversation>/.system_generated/steps/<n>/content.md``; the
  model reads it with ``view_file``. agy lets a conversation read its own brain folder without a
  rule and soft-denies another conversation's. Every other path is denied by explicit rules (see
  ``read_deny_rules``); a soft-denied action makes agy stop the run.
- ``--json-schema`` needs the ``finish`` tool, which ``excludeDefaultComponents`` removes unless
  the agent lists it.
- Only ``--output-format stream-json`` reports tool calls (``step_update`` events with
  ``tool_info``: name, parameters and the CLI's own error text). Its ``init`` event lists every
  registered tool, not the agent's tool set, so it is not a declaration.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from adapters.base import (
    MODEL_CHECKS,
    USAGE_KEYS,
    AdapterRequest,
    AdapterResult,
    UrlEvent,
    is_metaculus_host,
    model_check_from_ids,
    url_host,
)
from flab import netguard
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

STDOUT_FILENAME = "stdout.jsonl"
AGENTS_SUBDIR = Path(".agents") / "agents"

TIER1_AGENT = "flab-forecast"
FORMAT_REPAIR_AGENT = "flab-format-repair"
TIER2_MAIN_AGENT = "flab-tier2-main"
TIER2_SUBAGENT = "flab-tier2-research"

WEB_TOOLS: tuple[str, ...] = ("search_web", "read_url_content", "view_file")
TIER1_TOOLS: tuple[str, ...] = (*WEB_TOOLS, "finish")
FORMAT_REPAIR_TOOLS: tuple[str, ...] = ("finish",)
TIER2_MAIN_TOOLS: tuple[str, ...] = (*TIER1_TOOLS, "invoke_subagent")
TIER2_SUBAGENT_TOOLS: tuple[str, ...] = WEB_TOOLS
# agy adds manage_task to every agent, including one that lists no tools (M1 acceptance). It acts
# on background tasks of the conversation; none can be started without schedule or run_command.
ALWAYS_ADDED_TOOLS: tuple[str, ...] = ("manage_task",)
# manage_task actions that only report on those tasks (M1 acceptance saw "list").
INERT_TASK_ACTIONS: tuple[str, ...] = ("list", "status")

COMMON_DENY: tuple[str, ...] = ("command(*)", "write_file(*)", "execute_url(*)", "mcp(*)")
TIER1_ALLOW_PERMISSIONS: tuple[str, ...] = ("read_url(*)",)
TIER1_DENY_PERMISSIONS: tuple[str, ...] = (*COMMON_DENY, "read_url(metaculus.com)")
FORMAT_REPAIR_DENY_PERMISSIONS: tuple[str, ...] = (*COMMON_DENY, "read_url(*)", "read_file(*)")

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

_LOG_VERSION_RE = re.compile(r"Language server version:\s*(\S+)")
_LOG_MODEL_LABEL_RE = re.compile(
    r'Propagating selected model override to backend:\s*label="([^"]+)"'
)
# The CLI's own error text on a tool step; both name the permission and its target.
DENY_RULE_RE = re.compile(r"Permission denied for (\w+)\((.*?)\)\. Matches user-configured deny rule")
SOFT_DENY_RE = re.compile(r"user denied permission for (\w+)\((.*?)\)(?:\n|$)")
_QUOTA_ERROR_RE = re.compile(
    r"(quota\s+exceeded|resource_exhausted|rate\s+limit\s+exceeded|out\s+of\s+quota|weekly\s+limit)",
    re.IGNORECASE,
)


@dataclass
class GeminiResult(AdapterResult):
    conversation_id: str | None = None
    agent: str | None = None
    agent_sha256: str | None = None
    registry_tools: list[str] | None = None      # init event: every registered tool, not a declaration
    tool_steps: list[dict[str, Any]] = field(default_factory=list)
    violations: list[dict[str, Any]] = field(default_factory=list)
    audit_flags: list[str] = field(default_factory=list)
    parse_errors: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------- agents and settings

def agent_markdown(name: str, description: str, tools: tuple[str, ...], body: str, *,
                   subagent: bool = False, agents: tuple[str, ...] = ()) -> str:
    lines = ["---", f"name: {name}", f"description: {description}"]
    if subagent:
        lines += ["subagent: true", "model: inherit"]
    lines += ["excludeDefaultComponents: true", "inheritCustomizations: false", "inheritMcp: false", "tools:"]
    lines += [f"  - {t}" for t in tools]
    lines += ["agents: []"] if not agents else ["agents:", *[f"  - ./{a}.md" for a in agents]]
    lines += ["---", f"# {name}", body, ""]
    return "\n".join(lines)


AGENT_FILES: dict[str, str] = {
    TIER1_AGENT: agent_markdown(
        TIER1_AGENT, "Forecasting agent limited to web search and reading web pages.", TIER1_TOOLS,
        "You are a forecasting agent. Use only web search and URL reading. view_file is only for the "
        "page files that read_url_content reports."),
    FORMAT_REPAIR_AGENT: agent_markdown(
        FORMAT_REPAIR_AGENT, "Format repair agent without search or file tools.", FORMAT_REPAIR_TOOLS,
        "You are a format repair agent. Convert the provided raw text into the required JSON schema "
        "without performing any search or tool actions."),
    TIER2_MAIN_AGENT: agent_markdown(
        TIER2_MAIN_AGENT, "Tier 2 forecasting agent with one web research subagent.", TIER2_MAIN_TOOLS,
        "You are a forecasting agent. Use only web search, URL reading and the declared research subagent.",
        agents=(TIER2_SUBAGENT,)),
    TIER2_SUBAGENT: agent_markdown(
        TIER2_SUBAGENT, "Read-only web research subagent for Tier 2 forecasting.", TIER2_SUBAGENT_TOOLS,
        "You are a research subagent. Use only web search and URL reading to gather evidence.", subagent=True),
}


def agent_name(profile: str, subagents_enabled: bool = False) -> str:
    if profile == "format_repair":
        return FORMAT_REPAIR_AGENT
    if profile == "tier2" and subagents_enabled:
        return TIER2_MAIN_AGENT
    return TIER1_AGENT


def agent_tools(name: str) -> tuple[str, ...]:
    return {TIER1_AGENT: TIER1_TOOLS, FORMAT_REPAIR_AGENT: FORMAT_REPAIR_TOOLS,
            TIER2_MAIN_AGENT: TIER2_MAIN_TOOLS, TIER2_SUBAGENT: TIER2_SUBAGENT_TOOLS}[name]


def agent_path(workdir: Path, name: str) -> Path:
    return workdir / AGENTS_SUBDIR / f"{name}.md"


def prepare_agent_files(req: AdapterRequest, subagents_enabled: bool = False) -> Path:
    """Write the run's agent definition(s) into ``<workdir>/.agents/agents/``; unchanged files are kept."""
    main = agent_name(req.profile, subagents_enabled)
    names = [main, TIER2_SUBAGENT] if main == TIER2_MAIN_AGENT else [main]
    for name in names:
        path = agent_path(req.workdir, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = AGENT_FILES[name]
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
    return agent_path(req.workdir, main)


def brain_dir() -> Path:
    """Where agy keeps per-conversation files, including pages read_url_content saved."""
    return Path(os.path.realpath(Path.home() / ".gemini" / "antigravity-cli" / "brain"))


def read_deny_rules(keep: Path, listdir: Callable[[Path], list[str]] = lambda p: os.listdir(p)) -> list[str]:
    """``read_file`` deny rules for every entry beside the path from / to ``keep``.

    agy denies every path under a listed directory, matched by path component (a rule for
    ``~/.gemini/antigravity`` does not cover ``~/.gemini/antigravity-cli``), and deny wins over
    allow, so this leaves ``keep`` as the only readable tree. A path created after the run starts
    falls back to print mode's soft-deny, which stops the run. A level that cannot be listed
    raises OSError rather than producing an incomplete rule set.
    """
    keep = Path(os.path.realpath(keep))
    rules: list[str] = []
    cur = Path("/")
    for part in keep.parts[1:]:
        # An unlistable level would leave its entries unruled; refuse to build settings instead.
        names = sorted(listdir(cur))
        rules += [f"read_file({cur / n})" for n in names if n != part]
        cur = cur / part
    return rules


def build_settings(profile: str, *, keep: Path | None = None) -> dict[str, Any]:
    """Build the ``--settings`` JSON object for the requested execution profile."""
    if profile in ("tier1", "tier2", "acceptance"):
        return {
            "agentMode": "",
            "useG1Credits": False,
            "permissions": {
                "allow": list(TIER1_ALLOW_PERMISSIONS),
                "deny": [*TIER1_DENY_PERMISSIONS, *read_deny_rules(keep or brain_dir())],
            },
        }
    if profile == "format_repair":
        return {
            "agentMode": "",
            "useG1Credits": False,
            "permissions": {"allow": [], "deny": list(FORMAT_REPAIR_DENY_PERMISSIONS)},
        }
    raise ValueError(f"unknown profile: {profile!r}")


def build_argv(
    req: AdapterRequest,
    *,
    subagents_enabled: bool = TIER2_SUBAGENTS_ENABLED_DEFAULT,
) -> list[str]:
    """Assemble the CLI argument list for ``agy`` without executing it or writing any files."""
    if req.player != "gemini":
        raise ValueError(f"gemini adapter cannot build argv for player={req.player!r}")

    settings_json = json.dumps(build_settings(req.profile), separators=(",", ":"))
    argv: list[str] = [
        "agy",
        "-p",
        req.prompt,
        "--agent",
        agent_name(req.profile, subagents_enabled),
        "--model",
        req.model,
        "--effort",
        req.effort,
        "--output-format",
        "stream-json",
        "--json-schema",
        str(req.raw_dir / "schema.json"),
        "--disable-slash-commands",
        "--print-timeout",
        f"{req.timeout_s}s",
        "--sandbox",
        "--log-file",
        str(req.raw_dir / "agy_cli.log"),
        "--settings",
        settings_json,
    ]
    # Pages are fetched on Google's servers, but a local fallback cannot be ruled out (PLAN §7.3).
    return netguard.wrap(argv)


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


# ---------------------------------------------------------------- stream parsing

def read_stream(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(events, parse errors) from agy's stream-json stdout."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], [{"file": path.name, "error": "missing"}]
    events, errors = [], []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            errors.append({"file": path.name, "line": n, "error": "invalid JSON"})
            continue
        if isinstance(e, dict):
            events.append(e)
        else:
            errors.append({"file": path.name, "line": n, "error": "not an object"})
    return events, errors


def tool_steps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool steps keyed by step index, merged across updates; ``state`` is the last one seen."""
    steps: dict[int, dict[str, Any]] = {}
    for e in events:
        su = e.get("step_update")
        if not isinstance(su, dict) or not isinstance(su.get("step_index"), int):
            continue
        # finish starts as a "tool" step and completes as step_type "finish" (agy 1.4.12)
        if su.get("step_type") != "tool" and su["step_index"] not in steps:
            continue
        info = su.get("tool_info") if isinstance(su.get("tool_info"), dict) else {}
        step = steps.setdefault(su["step_index"], {"step": su["step_index"]})
        step["tool"] = str(info.get("name") or su.get("tool_name") or step.get("tool") or "")
        step["state"] = su.get("state")
        if isinstance(info.get("parameters"), dict):
            step["parameters"] = info["parameters"]
        err = info.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            step["error"] = err["message"]
        if info.get("output") is not None:
            step["output"] = str(info["output"])
    return [steps[k] for k in sorted(steps)]


def denial_of(message: str | None) -> tuple[str, str, str] | None:
    """(kind, permission, target) when ``message`` is agy's own permission denial."""
    if not message:
        return None
    m = DENY_RULE_RE.search(message)
    if m:
        return "cli_rule", m.group(1), m.group(2)
    m = SOFT_DENY_RE.search(message)
    if m:
        return "cli_soft_deny", m.group(1), m.group(2)
    return None


CONVERSATION_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]{0,127}$")
# The one parameter that names each web tool's target (agy 1.4.12 tool_info).
TARGET_PARAM: dict[str, str] = {"view_file": "AbsolutePath", "read_url_content": "Url", "search_web": "query"}


def own_page_file(path: str | None, conversation_id: str | None) -> bool:
    """True for a file inside this conversation's own brain folder (pages read_url_content saved).

    The conversation ID must be one plain path component, so it cannot point outside brain.
    """
    if not path or not conversation_id or not CONVERSATION_ID_RE.match(conversation_id):
        return False
    brain = brain_dir()
    own = Path(os.path.realpath(brain / conversation_id))
    if own.parent != brain:
        return False
    try:
        return Path(os.path.realpath(path)).is_relative_to(own)
    except (OSError, ValueError):
        return False


def _params_target(tool: str, params: dict[str, Any] | None) -> str | None:
    if not isinstance(params, dict):
        return None
    key = TARGET_PARAM.get(tool)
    if key is not None:
        return params[key] if isinstance(params.get(key), str) else None
    return next((v for v in params.values() if isinstance(v, str)), None)


def _parse_log(log_path: Path) -> tuple[str | None, str | None]:
    """(cli_version, model_label) from ``agy_cli.log``."""
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    version = model_label = None
    for line in content.splitlines():
        if version is None and (m := _LOG_VERSION_RE.search(line)):
            version = m.group(1)
        if m := _LOG_MODEL_LABEL_RE.search(line):
            model_label = m.group(1)
    return version, model_label


def _int_or_none(val: Any) -> int | None:
    if isinstance(val, int) and not isinstance(val, bool):
        return val
    return None


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


def parse(req: AdapterRequest, run_result: RunResult | None, *,
          subagents_enabled: bool = TIER2_SUBAGENTS_ENABLED_DEFAULT) -> GeminiResult:
    """Parse ``agy`` stream-json output and log from ``req.raw_dir`` without invoking the CLI."""
    argv = build_argv(req, subagents_enabled=subagents_enabled)
    stdout_path = run_result.stdout_path if run_result is not None else req.raw_dir / STDOUT_FILENAME
    events, parse_errors = read_stream(stdout_path)
    log_version, model_label = _parse_log(req.raw_dir / "agy_cli.log")
    try:
        stderr_text = (req.raw_dir / "stderr.txt").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        stderr_text = ""

    init_ev = next((e for e in events if e.get("event") == "init" and isinstance(e.get("init"), dict)), {})
    init = {"conversation_id": init_ev.get("conversation_id"), **(init_ev.get("init") or {})}
    result_ev = next((e["result"] for e in reversed(events)
                      if e.get("event") == "result" and isinstance(e.get("result"), dict)), None)
    conversation_id = init.get("conversation_id") or (result_ev or {}).get("conversation_id")
    conversation_id = str(conversation_id) if conversation_id else None
    steps = tool_steps(events)
    name = agent_name(req.profile, subagents_enabled)
    allowed = set(agent_tools(name))

    url_events: list[UrlEvent] = []
    denied: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    flags: list[str] = []
    counts: dict[str, int] = {}
    for s in steps:
        tool, state = s.get("tool", ""), s.get("state")
        target = _params_target(tool, s.get("parameters"))
        counts[tool] = counts.get(tool, 0) + 1
        den = denial_of(s.get("error"))
        if den:
            denied.append({"action": f"{den[1]}({den[2]})", "tool": tool, "step": s["step"], "reason": den[0],
                           "source": "stream_tool_error", "evidence_strength": "cli_verified"})
        if tool == "manage_task" and str((s.get("parameters") or {}).get("Action", "")).lower() in INERT_TASK_ACTIONS:
            flags.append("manage_task_query")
            continue
        if tool not in allowed:
            violations.append({"type": "unexpected_tool", "tool": tool, "step": s["step"], "state": state})
            continue
        if tool == "read_url_content" and target:
            st = "opened" if state == "DONE" else "blocked" if den else "attempted"
            url_events.append(UrlEvent(url=target, host=url_host(target), state=st,
                                       source=f"stream.read_url_content:{den[0] if den else state}"))
            if is_metaculus_host(url_host(target)):
                flags.append("metaculus_opened" if st == "opened" else "metaculus_attempted")
        elif tool == "view_file" and not own_page_file(target, conversation_id):
            if state == "DONE":
                violations.append({"type": "local_read", "path": target, "step": s["step"]})
            else:
                flags.append("local_read_denied" if den else "local_read_failed")
    for d in (result_ev or {}).get("denied_actions") or []:
        if isinstance(d, dict):
            denied.append({**d, "source": "stream_result_denied_actions", "evidence_strength": "cli_verified"})

    structured = (result_ev or {}).get("structured_output")
    structured = structured if isinstance(structured, dict) else None
    response = (result_ev or {}).get("response")
    final_text = response if isinstance(response, str) and response else None
    usage_raw = dict(result_ev["usage"]) if isinstance((result_ev or {}).get("usage"), dict) else None
    usage = {k: None for k in USAGE_KEYS}
    if usage_raw is not None:
        usage_raw["num_turns"] = (result_ev or {}).get("num_turns")
        usage = {"input_tokens": _int_or_none(usage_raw.get("input_tokens")),
                 "output_tokens": _int_or_none(usage_raw.get("output_tokens")),
                 "reasoning_tokens": _int_or_none(usage_raw.get("thinking_tokens")),
                 "cached_tokens": _int_or_none(usage_raw.get("cache_read_tokens"))}
    if model_label is not None:
        usage_raw = usage_raw or {}
        usage_raw["model_label"] = model_label
        aliases = MODEL_LABELS if model_label != req.model else None
        usage_raw["log_model_check"] = model_check_from_ids(req.model, model_label, aliases)

    timed_out = bool(run_result is not None and run_result.timed_out)
    exit_ok = run_result is None or (run_result.exit_code == 0 and not timed_out)
    status = (result_ev or {}).get("status")
    completed = bool(exit_ok and status == "SUCCESS" and structured is not None and not parse_errors)
    error_type = error_msg = None
    if timed_out:
        error_type, error_msg = "timeout", stderr_text or "timed out"
    elif not completed:
        text = " ".join(p for p in (stderr_text, json.dumps(result_ev or {}, ensure_ascii=False)) if p)
        soft = [d for d in denied if d.get("reason") == "cli_soft_deny" or d["source"] == "stream_result_denied_actions"]
        if soft:
            error_type, error_msg = "tool_denied", f"agy stopped the run after a soft-denied action: {soft[0].get('action') or soft[0]}"
        elif _QUOTA_ERROR_RE.search(text):
            error_type, error_msg = "quota_exhausted", stderr_text or "quota exhausted"
        elif exit_ok and status == "SUCCESS":
            error_type, error_msg = "invalid_output", "no structured output in the result event"
        else:
            error_type = "cli_error"
            error_msg = (stderr_text or (f"status={status}" if status else None)
                         or (f"non-zero exit code ({run_result.exit_code})" if run_result and run_result.exit_code else None)
                         or (parse_errors[0]["error"] if parse_errors else None) or "no result event")

    path = agent_path(req.workdir, name)
    return GeminiResult(
        argv=argv,
        run=run_result,
        cli_version=log_version,
        completed=completed,
        final_text=final_text,
        structured=structured,
        reported_model_id=model_label,
        model_check="unverified",  # set by apply_quota_model_check (PLAN §7.7)
        usage_raw=usage_raw,
        usage=usage,
        tool_call_counts=counts,
        url_events=url_events,
        denied_actions=denied,
        subagent_count=None,
        error_type=error_type,
        error=error_msg,
        declared={"source": "agent file (config; agy reports no per-agent tool list)", "agent": name,
                  "tools": list(agent_tools(name)), "always_added": list(ALWAYS_ADDED_TOOLS)},
        conversation_id=conversation_id,
        agent=name,
        agent_sha256=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        registry_tools=[str(t) for t in init.get("tools") or []] or None,
        tool_steps=steps,
        violations=violations,
        audit_flags=sorted(set(flags)),
        parse_errors=parse_errors,
    )


def run(
    req: AdapterRequest,
    *,
    subagents_enabled: bool = TIER2_SUBAGENTS_ENABLED_DEFAULT,
) -> GeminiResult:
    """Write schema/agent files, execute ``agy`` via ``flab.runner.run``, and parse the output."""
    req.raw_dir.mkdir(parents=True, exist_ok=True)
    req.workdir.mkdir(parents=True, exist_ok=True)
    (req.raw_dir / "schema.json").write_text(json.dumps(req.schema, ensure_ascii=False), encoding="utf-8")
    prepare_agent_files(req, subagents_enabled)

    argv = build_argv(req, subagents_enabled=subagents_enabled)
    run_result = flab_runner.run(
        argv,
        cwd=req.workdir,
        stdin_text=None,
        timeout_s=req.timeout_s,
        stdout_path=req.raw_dir / STDOUT_FILENAME,
        stderr_path=req.raw_dir / "stderr.txt",
        keep_env=KEEP_ENV,
    )
    result = parse(req, run_result, subagents_enabled=subagents_enabled)
    result.argv = argv
    if result.cli_version is None:
        result.cli_version = cli_version()
    return result
