"""Single-line tier-1 pipeline for local mode (PLAN §3.4 steps 1-3, §3.5, §5.5, §7.5; M1-W8).

For each requested question: latest snapshot -> pre-run checks -> tier-1 attempt -> validation
-> audit -> record, with at most one format repair per initial or rerun attempt and at most one
rerun. Nothing is submitted: this module refuses to run unless submit_mode is ``local``.

Usage:
    python src/forecast.py run --player claude --questions 43332,46450,43331
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib
import json
import math
import string
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

import acceptance_judge as J  # noqa: E402
import audit  # noqa: E402
import baseline  # noqa: E402
import quota  # noqa: E402
import validate as V  # noqa: E402
from adapters.base import AdapterRequest, AdapterResult, is_metaculus_host, url_host  # noqa: E402
from flab.config import load_players_enabled, load_quota_policy, load_run_config, load_submit_mode  # noqa: E402
from flab.ids import new_id  # noqa: E402
from flab.jsonl import append_record, read_records  # noqa: E402
from flab.ledger import transaction  # noqa: E402
from flab.timeutil import parse_iso, to_iso, utc_now  # noqa: E402
from flab.workdir import PROJECT_ROOT, make_attempt_dir, remove_attempt_dir  # noqa: E402
from poll_questions import latest_snapshots  # noqa: E402

DATA = PROJECT_ROOT / "data"
FORECASTS_LOG = DATA / "forecasts.jsonl"
RUNS_LOG = DATA / "forecast_runs.jsonl"
LINE_STATUS_LOG = DATA / "line_status.jsonl"
QUOTA_LOG = DATA / "quota.jsonl"
RAW_ROOT = DATA / "raw"
LOCK_PATH = DATA / "forecast.lock"
PROMPTS = PROJECT_ROOT / "prompts"
PROMPT_VERSION = "forecast_v2"
REPAIR_PROMPT_VERSION = "repair_v1"
PIPELINE_VERSION = "forecast_pipeline_v6"
QUOTA_BACKOFF_S = 1800  # V5 reset-time formats are not yet measured; use the §7.5 fixed fallback.
RERUN_ERRORS = ("quota_exhausted", "timeout", "tool_denied", "cli_error")
EXCLUDED_FLAGS = ("leak_confirmed",)
# Code that runs the CLI: acceptance must have been run with exactly this code (§7.2). This is the
# adapter, the subprocess runner and the loopback guard only; shared modules such as adapters/base.py,
# flab/workdir.py and the config files are not covered, so changing them needs a manual re-acceptance.
RUNTIME_FILES = ("flab/runner.py", "flab/netguard.py")


class PreflightError(RuntimeError):
    pass


# ---------------------------------------------------------------- prompts

def _template(name: str) -> string.Template:
    return string.Template((PROMPTS / name).read_text(encoding="utf-8"))


def forecast_prompt(snapshot: dict[str, Any], today: str) -> str:
    qtype = snapshot["question_type"]
    options = list(snapshot.get("options") or [])
    if qtype == "binary":
        label, options_block = "binary (Yes/No)", ""
        prob_rule = ("- `probability`: your probability that the question resolves Yes, between 0.001 and 0.999; "
                     "`option_probabilities`: null.")
        market_prob = " (the market's probability for the event, or null if it cannot be read as one number)"
        market_opt, market_complete = " (null)", " (null)"
    else:
        label = "multiple choice"
        options_block = "- Options: " + "; ".join(options) + "\n"
        prob_rule = ("- `probability`: null; `option_probabilities`: an object whose keys are exactly the options "
                     f"({json.dumps(options, ensure_ascii=False)}), each value between 0.001 and 0.999, summing to 1.")
        market_prob = " (null; use option_probabilities)"
        market_opt = " (the market's probability for each option, null for an option it does not cover)"
        market_complete = " (true if the market covers every option)"
    return _template("forecast_v2.md").substitute(
        today_utc=today, question_id=snapshot["question_id"], question_type=qtype, question_type_label=label,
        title=snapshot.get("title") or "", open_time=snapshot.get("open_time") or "unknown",
        close_time=snapshot.get("scheduled_close_time") or "unknown",
        resolve_time=snapshot.get("scheduled_resolve_time") or "unknown", options_block=options_block,
        description=snapshot.get("description") or "(none)",
        resolution_criteria=snapshot.get("resolution_criteria") or "(none)",
        fine_print=snapshot.get("fine_print") or "(none)", output_probability_rule=prob_rule,
        market_probability_rule=market_prob, market_option_rule=market_opt, market_complete_rule=market_complete)


def repair_prompt(snapshot: dict[str, Any], raw_text: str, basis: dict[str, Any], *, tier: int = 1) -> str:
    if "probability" in basis:
        hint = f" (the probability is {basis['probability']})"
    else:
        hint = f" (the option probabilities are {json.dumps(basis['option_probabilities'], ensure_ascii=False)})"
    # A random delimiter: the raw output (which may quote web pages) cannot close the data block.
    return _template("repair_v2.md" if tier == 2 else "repair_v1.md").substitute(question_id=snapshot["question_id"], raw_output=raw_text,
                                                repair_probability_hint=hint, delimiter=new_id("raw"))


def raw_output_text(result: AdapterResult) -> str:
    parts = [result.final_text or ""]
    if isinstance(result.structured, dict):
        parts.append(json.dumps(result.structured, ensure_ascii=False))
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------- pre-run checks (§7.2, §7.6)

def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_fingerprint(player: str, src: Path = PROJECT_ROOT / "src") -> dict[str, str]:
    return {name: _sha_file(src / name) for name in (f"adapters/{player}.py", *RUNTIME_FILES)}


@dataclass
class Preflight:
    player: str
    problems: list[str] = field(default_factory=list)
    waivers: list[str] = field(default_factory=list)
    acceptance_record_id: str | None = None
    production_record_id: str | None = None
    cli_version: str | None = None
    catalog: dict[str, Any] | None = None
    tier: int = 1

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> dict[str, Any]:
        return {"player": self.player, "tier": self.tier, "ok": self.ok, "problems": self.problems, "waivers": self.waivers,
                "acceptance_record_id": self.acceptance_record_id, "production_record_id": self.production_record_id,
                "cli_version": self.cli_version}


def _acceptance_records(log: Path, player: str) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [r for r in read_records(log) if r.get("type") == "acceptance_result" and r.get("player") == player]


def incomplete_acceptance(log: Path, player: str) -> bool:
    """A newly started but unfinished acceptance cannot leave an older pass in force.

    The autoload test journals its model calls as ``acceptance_call_started`` with
    ``source = "autoload"`` so the budget counts them; it is not an isolation acceptance
    and never writes ``acceptance_result``, so those rows must not revoke a pass."""
    with transaction(log.parent):
        rows = list(read_records(log)) if log.exists() else []
    mine = [r for r in rows if r.get("player") == player and r.get("source") != "autoload"]
    latest_result = max((i for i, r in enumerate(mine) if r.get("type") == "acceptance_result"), default=-1)
    return any(r.get("type") == "acceptance_call_started" for r in mine[latest_result + 1:])


def check_acceptance(pf: Preflight, records: list[dict[str, Any]], fingerprint: dict[str, str],
                     project_root: Path = PROJECT_ROOT, production: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The latest acceptance must have passed with this judge, CLI version and runtime code, and one
    acceptance must have passed with the production model and effort configured for this tier."""
    if not records:
        pf.problems.append("no acceptance record for this line")
        return None
    latest = records[-1]
    pf.acceptance_record_id = latest.get("record_id")
    if not latest.get("line_pass"):
        pf.problems.append(f"latest acceptance {latest.get('record_id')} did not pass")
    if latest.get("judge_version") != J.JUDGE_VERSION:
        pf.problems.append(f"latest acceptance used {latest.get('judge_version')}, current judge is {J.JUDGE_VERSION}")
    if latest.get("cli_version") != pf.cli_version:
        pf.problems.append(f"CLI version {pf.cli_version!r} differs from the accepted {latest.get('cli_version')!r}")
    ev_path = project_root / str(latest.get("evidence_path") or "")
    try:
        ok_hash = _sha_file(ev_path) == latest.get("evidence_sha256")
        evidence = json.loads(ev_path.read_text(encoding="utf-8")) if ok_hash else None
    except (OSError, ValueError):
        ok_hash, evidence = False, None
    if not ok_hash or not isinstance(evidence, dict):
        pf.problems.append("acceptance evidence missing or its sha256 does not match the record")
    else:
        if pf.tier == 2 and evidence.get("tier2_equivalent_to_tier1") is not True:
            pf.problems.append("no accepted tier-2 tool-profile equivalence evidence")
        accepted = evidence.get("code_sha256") or {}
        changed = [n for n, h in fingerprint.items() if accepted.get(n) != h]
        if changed:
            pf.problems.append(f"runtime code changed since acceptance: {changed}")
    production = [r for r in records if r.get("mode") == "production" and r.get("line_pass")
                  and (production is None or (r.get("model"), r.get("effort")) == (production.get("model"), production.get("effort")))]
    if pf.tier == 2:
        current = []
        for record in production:
            if record.get("cli_version") != pf.cli_version or record.get("judge_version") != J.JUDGE_VERSION:
                continue
            try:
                path = project_root / str(record.get("evidence_path") or "")
                proof = json.loads(path.read_text()) if _sha_file(path) == record.get("evidence_sha256") else {}
            except (OSError, ValueError):
                continue
            if isinstance(proof, dict) and proof.get("tier2_equivalent_to_tier1") is True and all(
                    (proof.get("code_sha256") or {}).get(name) == sha for name, sha in fingerprint.items()):
                current.append(record)
        production = current
    if production:
        pf.production_record_id = production[-1].get("record_id")
    else:
        pf.problems.append("no passing acceptance with the production model and effort (§7.6)")
    return latest


def check_baseline(pf: Preflight, approved: dict[str, Any] | None, current: dict[str, Any], *,
                   allow_unapproved: bool) -> None:
    mine = [b for b in baseline.blockers(current) if b["player"] in (pf.player, "common")]
    for b in mine:
        pf.problems.append(f"baseline blocker {b['source']}: {b['what']}")
    if approved is None:
        if allow_unapproved:
            pf.waivers.append("no approved baseline (local mode, --allow-unapproved-baseline)")
        else:
            pf.problems.append("no approved baseline (§7.2); run the human review first")
        return
    decision = baseline.compare(approved, current)["decisions"].get(pf.player)
    if decision != "ok":
        pf.problems.append(f"baseline check says {decision!r} for this line")


def preflight(player: str, *, cfg: dict[str, Any], allow_unapproved_baseline: bool = False,
              tier: int = 1,
              acceptance_log: Path = baseline.ACCEPTANCE_LOG,
              versions: Callable[[], dict[str, str | None]] = baseline.cli_versions,
              snapshot: Callable[[dict[str, str | None]], dict[str, Any]] | None = None,
              adapter: Any = None, fingerprint: dict[str, str] | None = None,
              players_enabled: dict[str, Any] | None = None, submit_mode: dict[str, Any] | None = None,
              continuous: bool = False) -> Preflight:
    if tier not in (1, 2):
        raise ValueError("tier must be 1 or 2")
    pf = Preflight(player, tier=tier)
    if tier == 2 and cfg["players"][player]["tier2"].get("subagents_enabled") is not False:
        pf.problems.append("tier-2 subagents are not enabled by this pipeline")
    enabled = players_enabled if players_enabled is not None else load_players_enabled()
    mode = (submit_mode if submit_mode is not None else load_submit_mode()).get("mode")
    if not enabled.get(player):
        pf.problems.append("line is disabled in players_enabled.json")
    if mode != "local" and not (continuous and mode in ("trial", "live")):
        pf.problems.append(f"submit_mode is {mode!r}; this pipeline runs only in local mode (M1-W8)")
    if incomplete_acceptance(acceptance_log, player):
        pf.problems.append("latest isolation acceptance run is incomplete; an older pass cannot authorize forecasting")
    vers = versions()
    pf.cli_version = vers.get(player)
    latest = check_acceptance(pf, _acceptance_records(acceptance_log, player), fingerprint or runtime_fingerprint(player),
                              production=(cfg.get("players") or {}).get(player, {}).get(f"tier{tier}"))
    if snapshot is not None:
        snap = snapshot(vers)
    else:
        credits = None
        if player == "gemini":
            observed = quota.snapshot(timeout_s=30)
            if not observed.stale and not observed.concurrent_agy_detected:
                credits = observed.remaining_credits
        snap = baseline.take_snapshot(versions=vers, gemini_credits=credits).to_record()
    check_baseline(pf, baseline.latest_approved(acceptance_log), snap, allow_unapproved=allow_unapproved_baseline)
    if player == "gpt" and adapter is not None and latest is not None:
        model = cfg["players"]["gpt"][f"tier{tier}"]["model"]
        try:
            pf.catalog = adapter.load_catalog()
            sha = adapter.catalog_entry_sha256(adapter.restricted_catalog(pf.catalog, model), model)
        except (OSError, ValueError, RuntimeError) as exc:
            pf.problems.append(f"cannot load the codex model catalog: {exc}")
        else:
            if sha != latest.get("catalog_entry_sha256"):
                pf.problems.append("codex model catalog entry differs from the accepted one (D60)")
    return pf


# ---------------------------------------------------------------- attempts

class LineStop(RuntimeError):
    """Stop this line. With ``notify`` the stop is recorded in data/line_status.jsonl and lasts until
    Louis records a resume (``forecast.py resume``); otherwise it ends this run only (§7.5, §7.7)."""

    def __init__(self, reason: str, *, notify: bool = False) -> None:
        super().__init__(reason)
        self.notify = notify


def line_stopped(log: Path, player: str, forecasts_log: Path | None = None) -> dict[str, Any] | None:
    """The stop record in force for this line, or None if there is none or a later resume."""
    state = None
    resumed_at = None
    for r in read_records(log) if log.exists() else []:
        if r.get("player") == player and r.get("type") in ("line_stop", "line_resume"):
            state = r if r["type"] == "line_stop" else None
            if r["type"] == "line_resume":
                resumed_at = parse_iso(r["at"])
    if state:
        return state
    records = read_records(forecasts_log) if forecasts_log and forecasts_log.exists() else []
    completed = {r.get("attempt_id") for r in records if r.get("player") == player}
    journal = starts_log(forecasts_log) if forecasts_log else None
    for started in read_records(journal) if journal and journal.exists() else []:
        if started.get("player") == player and started["attempt_id"] not in completed \
                and (resumed_at is None or parse_iso(started["started_at_utc"]) > resumed_at):
            return {"type": "line_stop", "player": player, "at": started["started_at_utc"],
                    "reason": f"unresolved CLI start {started['attempt_id']}: result and safety checks unknown"}
    # Recover the stop if the process died between appending the forecast and its line_stop.
    leaks: list[datetime] = []
    for r in records:
        if r.get("type") != "forecast_attempt" or r.get("player") != player:
            continue
        at = parse_iso(r["finished_at_utc"])
        if resumed_at is not None and at <= resumed_at:
            continue
        reason = None
        if r.get("model_check") == "mismatch" or r.get("inherited_model_mismatch"):
            reason = "model_mismatch"
        elif r.get("tool_violations") or r.get("error_type") == "tool_scope_violation":
            reason = "tool_scope_violation"
        if "leak_confirmed" in (r.get("own_audit_flags", r.get("audit_flags")) or []):
            leaks.append(at)
            if sum(t >= at - timedelta(days=LEAK_WINDOW_DAYS) for t in leaks) >= LEAK_STOP_COUNT:
                reason = "repeated leak_confirmed"
        if reason:
            return {"type": "line_stop", "player": player, "at": r["finished_at_utc"],
                    "reason": f"{reason} recovered from forecast ledger at attempt {r['attempt_id']}"}
    return state


def latest_resume(log: Path, player: str) -> datetime | None:
    rows = [r for r in read_records(log) if r.get("player") == player and r.get("type") == "line_resume"] if log.exists() else []
    return parse_iso(rows[-1]["at"]) if rows else None


def _peek_flags(result: AdapterResult, obj: Any) -> set[str]:
    """Whether this attempt's own output already shows a confirmed leak (checked again by the audit)."""
    flags: set[str] = set()
    for url in audit.reported_source_urls(result, obj):
        if is_metaculus_host(url_host(url)):
            flags.add("leak_confirmed")
    if any(e.state == "opened" and is_metaculus_host(e.host or url_host(e.url)) for e in result.url_events):
        flags.add("leak_confirmed")
    if "leak_confirmed" in (getattr(result, "audit_flags", None) or []):
        flags.add("leak_confirmed")
    return flags


@dataclass
class Context:
    player: str
    adapter: Any
    cfg: dict[str, Any]
    run_id: str
    preflight: Preflight
    now: Callable[[], datetime] = utc_now
    quota_snapshot: Callable[..., Any] = quota.snapshot   # called with timeout_s=...
    quota_policy: dict[str, Any] | None = None
    line_status_log: Path = LINE_STATUS_LOG
    forecasts_log: Path = FORECASTS_LOG
    audits_log: Path = audit.AUDITS_LOG
    quota_log: Path = QUOTA_LOG
    raw_root: Path = RAW_ROOT
    make_dir: Callable[[], Path] = make_attempt_dir
    remove_dir: Callable[[Path], None] = remove_attempt_dir
    tier: int = 1
    reconciliation: dict[str, Any] | None = None
    before_attempt: Callable[[str, str], None] | None = None
    recheck_inputs: Callable[[], None] | None = None
    admission_outcome: Callable[[str, bool], None] | None = None
    continuous: bool = False


def _structured_fields(obj: Any) -> dict[str, Any]:
    keys = ("reasoning_summary", "key_factors", "base_rate", "market_refs", "sources", "changed_from_tier1",
            "change_reason", "escalation_flags", "injection_notice")
    return {k: (obj.get(k) if isinstance(obj, dict) else None) for k in keys}


def tool_violations(player: str, result: AdapterResult, *, repair: bool) -> list[Any]:
    """Use adapter evidence; also enforce the tier-1/no-tools repair boundary on observed calls."""
    violations = list(getattr(result, "violations", None) or [])
    from adapters.gemini import ALWAYS_ADDED_TOOLS, FORMAT_REPAIR_TOOLS, TIER1_TOOLS

    # Claude delivers --json-schema output through its built-in StructuredOutput tool; keep the
    # same sets the acceptance judge accepted, so a passing profile is never stopped here.
    allowed = {"claude": set(J.CLAUDE_FORMAT_REPAIR_TOOLS if repair else J.CLAUDE_SAFE_TOOLS),
               "gpt": set() if repair else {"web_search"},
               "gemini": set(FORMAT_REPAIR_TOOLS if repair else TIER1_TOOLS) | set(ALWAYS_ADDED_TOOLS)}
    # Gemini's adapter checks manage_task actions (only list/status are harmless, D62).
    for tool, count in (result.tool_call_counts or {}).items():
        if count and tool not in allowed[player]:
            violations.append({"type": "unexpected_tool", "tool": tool, "count": count})
    if result.subagent_count:
        violations.append({"type": "unexpected_subagents", "count": result.subagent_count})
    return violations


def json_safe(value: Any, path: str = "$") -> tuple[Any, list[str]]:
    """Invalid model numbers must not prevent the append-only ledger from recording a failure.

    Replace non-finite floats only in the ledger copy; raw CLI output remains untouched.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None, [path]
    if isinstance(value, (dict, list)):
        items = value.items() if isinstance(value, dict) else enumerate(value)
        converted, paths = {}, []
        for key, item in items:
            converted[key], bad = json_safe(item, f"{path}.{key}")
            paths.extend(bad)
        return (converted if isinstance(value, dict) else list(converted.values())), paths
    return value, []


def starts_log(forecasts_log: Path) -> Path:
    return forecasts_log.with_name("forecast_attempt_starts.jsonl")


def unresolved_starts(ctx: Context, question_id: int, past: list[dict[str, Any]]) -> list[str]:
    """A process can die after invoking a CLI but before saving its outcome. Never silently rerun it."""
    log = starts_log(ctx.forecasts_log)
    completed = {r["attempt_id"] for r in past}
    return [r["attempt_id"] for r in read_records(log) if r.get("player") == ctx.player
            and r.get("question_id") == question_id and r.get("tier") == ctx.tier
            and r["attempt_id"] not in completed] if log.exists() else []


def decision_deadline(snap: dict[str, Any], cfg: dict[str, Any]) -> datetime | None:
    """Submit deadline minus the time a submission needs (§3.5): all waiting stops here."""
    d = V.submit_deadline(snap, cfg["deadlines"])
    return d - timedelta(minutes=cfg["deadlines"]["submit_lead_minutes"]) if d else None


QUOTA_QUERY_S = 30   # upper bound for one /usage + /credits query


def _fraction(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 1 else None


def argon_estimate(quota_log: Path, policy: dict[str, Any], forecasts_log: Path, *, tier: int = 1) -> tuple[float, int]:
    """(expected Argon use of one attempt at this tier, number of samples).

    Samples are before/after drops of attempts whose model check is ``verified``, from snapshots that
    are neither stale nor taken during other agy use, with finite fractions and a real decrease; the
    estimate is their 90th percentile. Without such samples the per-question tier-2 cap is used, so an
    unknown consumption is never taken as zero (§7.7, §9).
    """
    fallback = float(policy["players"]["gemini"]["tier2_per_question_cap"])
    verified = set()
    if forecasts_log.exists():
        verified = {r.get("attempt_id") for r in read_records(forecasts_log)
                    if r.get("type") == "forecast_attempt" and r.get("model_check") == "verified"
                    and r.get("tier", 1) == tier}
    pairs: dict[str, dict[str, float]] = {}
    if quota_log.exists():
        for r in read_records(quota_log):
            if r.get("type") != "quota_snapshot" or r.get("stale") or r.get("concurrent_agy_detected"):
                continue
            frac = _fraction(((r.get("buckets") or {}).get(quota.ARGON_BUCKET_ID) or {}).get("remaining_fraction"))
            if frac is not None and r.get("attempt_id") in verified:
                pairs.setdefault(r["attempt_id"], {})[r.get("when")] = frac
    drops = sorted(d for p in pairs.values() if {"before", "after"} <= set(p) and (d := p["before"] - p["after"]) > 0)
    if not drops:
        return fallback, 0
    return drops[min(len(drops) - 1, int(0.9 * len(drops)))], len(drops)


def gemini_admission(ctx: Context, before: Any) -> tuple[str | None, dict[str, Any]]:
    """(reason not to start or None, the numbers used): credits must be 0, the snapshot trustworthy,
    and Argon must stay above the reserve line after the estimated use."""
    if not quota.credits_ok(before):
        return "agy AI credits are not confirmed as 0 (§7.7, D54)", {}
    if getattr(before, "stale", False) or getattr(before, "concurrent_agy_detected", False):
        return "quota_exhausted: the /usage snapshot is stale or other agy use was detected", {}
    remaining = _fraction(before.remaining_fraction(quota.ARGON_BUCKET_ID)) if hasattr(before, "remaining_fraction") else None
    if remaining is None:
        return "quota_exhausted: Argon remaining fraction is not a number in [0, 1]", {}
    policy = ctx.quota_policy or load_quota_policy()
    reserve = 1 - float(policy["players"]["gemini"]["weekly_cap"])
    estimate, samples = argon_estimate(ctx.quota_log, policy, ctx.forecasts_log, tier=ctx.tier)
    used = {"argon_remaining": remaining, "estimate": estimate, "estimate_samples": samples, "reserve": reserve}
    if remaining - estimate < reserve:
        return (f"quota_exhausted: Argon {remaining:.4f} - estimate {estimate:.4f} is below the reserve line "
                f"{reserve:.2f}"), used
    return None, used


def run_attempt(ctx: Context, snap: dict[str, Any], *, kind: str, prompt: str, sch: dict[str, Any],
                prev: dict[str, Any] | None, basis: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run, validate and audit one attempt; append its audit and forecast records; return the record.

    Raises LineStop before running when the attempt may not start (time, quota), and after recording
    when the attempt shows the line must stop (model mismatch, quota check failed after the run).
    """
    if ctx.recheck_inputs is not None:
        ctx.recheck_inputs()
    player, qid = ctx.player, snap["question_id"]
    inputs = ctx.reconciliation or {}
    if ctx.tier == 2 and (not inputs.get("input_attempt_ids") or
                         ctx.cfg["players"][player]["tier2"].get("subagents_enabled") is not False):
        raise PreflightError("tier 2 requires audited inputs and disabled subagents")
    repair = kind == "format_repair"
    model_cfg = ctx.cfg["players"][player][f"tier{ctx.tier}"]
    limit = int(ctx.cfg["timeouts_s"]["repair" if repair else f"tier{ctx.tier}"])
    deadline = V.submit_deadline(snap, ctx.cfg["deadlines"])
    decide_by = decision_deadline(snap, ctx.cfg)
    gemini = player == "gemini"
    post_quota_s = QUOTA_QUERY_S if gemini else 0

    def seconds_left() -> float:
        return (decide_by - ctx.now()).total_seconds() if decide_by is not None else float("inf")

    def require_time(what: str) -> float:
        left = seconds_left() - post_quota_s
        if left < MIN_ATTEMPT_S:
            raise QuestionDone(f"no time left before the decision deadline {to_iso(decide_by)} for {what}")
        return left

    require_time(f"a {kind} attempt")
    quota_before = quota_after = None
    stop_after = None
    admission: dict[str, Any] = {}
    attempt_id = new_id("att")
    if gemini:
        try:
            quota_before = ctx.quota_snapshot(timeout_s=min(QUOTA_QUERY_S, max(1.0, seconds_left())))
        except Exception as exc:  # noqa: BLE001 - any failure means the run may not start
            raise LineStop(f"agy /usage before the run failed: {type(exc).__name__}: {exc}") from exc
        with transaction(ctx.quota_log.parent):
            append_record(ctx.quota_log, {"type": "quota_snapshot", "attempt_id": attempt_id, "when": "before",
                                          **quota_before.to_dict()})
        reason, admission = gemini_admission(ctx, quota_before)
        if reason:
            raise LineStop(reason)
    # recomputed after the quota query, and leaving time for the one after the run (§3.5)
    timeout = int(min(limit, require_time(f"a {kind} attempt after the quota check")))
    # Recheck after an external quota query as well: late audits must precede every exposure.
    if ctx.recheck_inputs is not None:
        ctx.recheck_inputs()
        timeout = int(min(limit, require_time(f"a {kind} attempt after input revalidation")))
    started = ctx.now()
    raw_dir = ctx.raw_root / player / f"{qid}_{attempt_id}"
    raw_dir.mkdir(parents=True)
    workdir = ctx.make_dir()
    admitted, start_written = False, False
    try:
        req = AdapterRequest(player=player, profile="format_repair" if repair else f"tier{ctx.tier}", prompt=prompt, schema=sch,
                             workdir=workdir, raw_dir=raw_dir, model=model_cfg["model"], effort=model_cfg["effort"],
                             timeout_s=timeout)
        # Reserve only after quota checks and filesystem setup; persist an outcome even if a later
        # check prevents starting. Unknown process death remains distinguishable by the start log.
        if ctx.before_attempt is not None:
            ctx.before_attempt(kind, attempt_id)
            admitted = True
        if ctx.recheck_inputs is not None:
            ctx.recheck_inputs()
        timeout = int(min(limit, require_time("the admitted CLI start")))
        req = replace(req, timeout_s=timeout)
        started = ctx.now()
        # fsync the start before the external call. A missing outcome after restart is ambiguous,
        # so that question stays unresolved instead of obtaining a fresh attempt budget.
        start_record = {
            "type": "forecast_attempt_started", "attempt_id": attempt_id, "run_id": ctx.run_id,
            "player": player, "question_id": qid, "question_snapshot_id": snap.get("snapshot_id"),
            "tier": ctx.tier, "attempt_kind": kind, "prev_attempt_id": (prev or {}).get("attempt_id"),
            "reconciliation_id": inputs.get("record_id"), "input_attempt_ids": inputs.get("input_attempt_ids"),
            "input_order": inputs.get("input_order"),
            "admission_decision_id": inputs.get("admission_decision_id"),
            "started_at_utc": to_iso(started), "pipeline_version": PIPELINE_VERSION}
        with transaction(ctx.forecasts_log.parent):
            append_record(starts_log(ctx.forecasts_log), start_record)
        start_written = True
        try:
            result = ctx.adapter.run(req, catalog=ctx.preflight.catalog) if player == "gpt" else ctx.adapter.run(req)
        except Exception as exc:  # noqa: BLE001 - preserve ordinary adapter/parser failures; not process interrupts
            # The attempt still gets a record, so a crash cannot hide a run that may have reached the CLI.
            result = AdapterResult(argv=[], run=None, cli_version=None, completed=False, final_text=None,
                                   structured=None, reported_model_id=None, model_check="unverified", usage_raw=None,
                                   error_type="cli_error", error=f"adapter raised {type(exc).__name__}: {exc}")
        cli_finished = ctx.now()
        if gemini:
            try:
                quota_after = ctx.quota_snapshot(timeout_s=min(QUOTA_QUERY_S, max(1.0, seconds_left())))
            except Exception as exc:  # noqa: BLE001 - record the attempt, then stop the line
                stop_after = f"agy /usage after the run failed: {type(exc).__name__}: {exc}"
                ctx.adapter.apply_quota_model_check(result, "undetermined")
            else:
                with transaction(ctx.quota_log.parent):
                    append_record(ctx.quota_log, {"type": "quota_snapshot", "attempt_id": attempt_id, "when": "after",
                                                  **quota_after.to_dict()})
                ctx.adapter.apply_quota_model_check(result, quota.judge(quota_before, quota_after))
    finally:
        try:
            if admitted and ctx.admission_outcome is not None:
                ctx.admission_outcome(attempt_id, start_written)
        finally:
            ctx.remove_dir(workdir)
    finished = ctx.now()

    obj, source = V.extract_output(result.structured, result.final_text)
    outcome = V.validate_output(obj, snap, sch) if result.completed else V.Outcome(V.INCOMPLETE, [result.error or "CLI did not complete"])
    violations = tool_violations(player, result, repair=repair)
    if violations:
        outcome = V.Outcome(V.INCOMPLETE, ["observed tools exceeded the accepted profile"])
    if repair and outcome.status == V.VALID and basis is not None:
        errs = V.repair_errors(obj, basis, prev.get("_raw_text", "") if prev else "")
        if errs:
            outcome = V.Outcome(V.SEMANTIC_FAILED, errs)
    late_values = None
    # Usable only if checked before waiting stopped (the decision deadline, §3.5); §5.5: probabilities are
    # null unless the attempt is valid, and what it produced is kept apart.
    cutoff = decide_by or deadline
    if cutoff is not None and finished > cutoff and outcome.status != V.INCOMPLETE:
        late_values = {"probability": outcome.probability, "option_probabilities": outcome.option_probabilities}
        outcome = V.Outcome(V.LATE, [*outcome.errors, f"finished after the decision deadline {to_iso(cutoff)}"])
    raw_text = raw_output_text(result)
    error_type, error = result.error_type, result.error
    if violations:
        error_type, error = "tool_scope_violation", "observed tools exceeded the accepted profile; re-acceptance required"
    inherited_mismatch = bool(prev and (prev.get("model_check") == "mismatch" or prev.get("inherited_model_mismatch")))
    next_basis = None
    if outcome.status in V.REPAIRABLE and not repair and result.model_check != "mismatch" \
            and "leak_confirmed" not in (aud_flags := _peek_flags(result, obj)):
        next_basis = V.repair_basis(raw_text, snap, tier=ctx.tier)
        if next_basis is None and error_type is None:
            error_type, error = "invalid_output", "no unique repair basis in the raw output; not repaired (§7.5)"

    aud = audit.audit_attempt(attempt_id=attempt_id, player=player, question_id=qid, result=result,
                              structured=obj if isinstance(obj, dict) else None,
                              raw_files=sorted(p for p in raw_dir.rglob("*") if p.is_file()), prompt=prompt,
                              inherited=set((prev or {}).get("audit_flags") or ()) | set(inputs.get("audit_flags") or ()),
                              prev_attempt_id=(prev or {}).get("attempt_id"))
    usage = {"raw": result.usage_raw, **result.usage}
    rec = {
        "type": "forecast_attempt", "record_id": new_id("fc"), "run_id": ctx.run_id, "mode": "local",
        "pipeline_version": PIPELINE_VERSION,
        "attempt_id": attempt_id, "prev_attempt_id": (prev or {}).get("attempt_id"), "tier": ctx.tier, "attempt_kind": kind,
        "tournament_id": snap.get("tournament_id"), "post_id": snap.get("post_id"), "question_id": qid,
        "question_type": snap["question_type"], "question_snapshot_id": snap.get("snapshot_id"),
        "input_attempt_ids": inputs.get("input_attempt_ids"), "input_order": inputs.get("input_order"),
        "reconciliation_id": inputs.get("record_id"), "input_audit_event_ids": inputs.get("audit_event_ids"),
        "admission_decision_id": inputs.get("admission_decision_id"),
        "audit_flags": aud["audit_flags"], "audit_event_id": aud["audit_event_id"],
        "player": player, "requested_model_id": model_cfg["model"], "requested_effort": model_cfg["effort"],
        "reported_model_id": result.reported_model_id, "model_check": result.model_check,
        "inherited_model_mismatch": inherited_mismatch,
        "cli_version": result.cli_version, "prompt_version": ("repair_v2" if ctx.tier == 2 else REPAIR_PROMPT_VERSION) if repair else
            (inputs.get("prompt_version") if ctx.tier == 2 else PROMPT_VERSION),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "schema_version": V.SCHEMA_VERSION, "schema_sha256": V.schema_sha256(sch),
        "run_config_version": ctx.cfg.get("version"),
        "started_at_utc": to_iso(started), "cli_finished_at_utc": to_iso(cli_finished), "finished_at_utc": to_iso(finished),
        "deadline_utc": to_iso(deadline) if deadline else None,
        "decision_deadline_utc": to_iso(decide_by) if decide_by else None, "timeout_s": timeout,
        "probability": outcome.probability, "option_probabilities": outcome.option_probabilities,
        "option_probabilities_raw": outcome.option_probabilities_raw, "late_values": late_values,
        "validation_status": outcome.status, "validation_errors": outcome.errors[:20], "output_source": source,
        **_structured_fields(obj),
        "usage": usage, "quota_before": getattr(quota_before, "snapshot_id", None),
        "quota_after": getattr(quota_after, "snapshot_id", None),
        "tool_call_counts": result.tool_call_counts, "subagent_count": result.subagent_count,
        "tool_violations": violations,
        "url_events": [{"url": e.url, "host": e.host, "state": e.state, "source": e.source} for e in result.url_events],
        "denied_actions": result.denied_actions, "raw_dir": str(raw_dir.relative_to(PROJECT_ROOT)) if raw_dir.is_relative_to(PROJECT_ROOT) else str(raw_dir),
        "own_audit_flags": aud["own_audit_flags"], "quota_admission": admission or None,
        "error_type": error_type, "error": error, "repair_basis": next_basis,
        **({"repair_raw_text": raw_text} if ctx.continuous and next_basis is not None else {}),
        "quota_retry_at_utc": to_iso(finished + timedelta(seconds=QUOTA_BACKOFF_S))
            if error_type == "quota_exhausted" else None,
        "preflight_waivers": ctx.preflight.waivers,
    }
    if ctx.tier == 2:
        reference = inputs.get("tier1_reference")
        changed = None
        if reference is not None and outcome.status == V.VALID:
            old = reference["option_probabilities"] if snap["question_type"] == "multiple_choice" else {"Yes": reference["probability"]}
            new = outcome.option_probabilities if snap["question_type"] == "multiple_choice" else {"Yes": outcome.probability}
            changed = any(not math.isclose(old[o], new[o], rel_tol=0, abs_tol=1e-12) for o in old)
        rec.update(changed_from_tier1=changed, comparison_attempt_id=(reference or {}).get("attempt_id"),
                   comparison_method="normalized_probability_equality_v1")
    rec, nonfinite = json_safe(rec)
    rec["nonfinite_fields"] = nonfinite
    with transaction(ctx.forecasts_log.parent):
        append_record(ctx.audits_log, aud)
        append_record(ctx.forecasts_log, rec)
    if violations:
        raise LineStop(f"tool_scope_violation on attempt {attempt_id}: stop the line and notify Louis; "
                       "re-acceptance required (§7.3, D60, D62)", notify=True)
    if result.model_check == "mismatch":
        raise LineStop(f"model_mismatch on attempt {attempt_id}: stop the line and notify Louis (§7.5, §7.7)",
                       notify=True)
    if stop_after:
        raise LineStop(stop_after)
    if leak_count(ctx.forecasts_log, player, ctx.now(), resumed_at=latest_resume(ctx.line_status_log, player)) >= LEAK_STOP_COUNT:
        raise LineStop(f"{LEAK_STOP_COUNT} or more leak_confirmed answers within {LEAK_WINDOW_DAYS} days: "
                       "stop the line and notify Louis (§7.5)", notify=True)
    if error_type == "quota_exhausted":
        raise LineStop(f"quota_exhausted: pause line until {rec['quota_retry_at_utc']} (fixed backoff, §7.5)")
    return {**rec, "_raw_text": raw_text, "_basis": next_basis}


class QuestionDone(RuntimeError):
    """No further attempt for this question in this run (for example, no time left)."""


MIN_ATTEMPT_S = 60
LEAK_STOP_COUNT, LEAK_WINDOW_DAYS = 2, 7


def leak_count(log: Path, player: str, now: datetime, *, resumed_at: datetime | None = None) -> int:
    if not log.exists():
        return 0
    since = now - timedelta(days=LEAK_WINDOW_DAYS)
    # Own findings only: an inherited flag is the same exposure, not a new one.
    return sum(1 for r in read_records(log) if r.get("type") == "forecast_attempt" and r.get("player") == player
               and "leak_confirmed" in (r.get("own_audit_flags", r.get("audit_flags")) or []) and r.get("finished_at_utc")
               and parse_iso(r["finished_at_utc"]) >= since
               and (resumed_at is None or parse_iso(r["finished_at_utc"]) > resumed_at))


def adoptable(rec: dict[str, Any]) -> bool:
    return (rec["validation_status"] == V.VALID and rec.get("model_check") != "mismatch"
            and not rec.get("inherited_model_mismatch")
            and not rec.get("tool_violations")
            and not set(rec.get("audit_flags") or ()) & set(EXCLUDED_FLAGS))


def history(log: Path, player: str, question_id: int, tier: int = 1) -> list[dict[str, Any]]:
    """Every earlier attempt of this line and tier on this question, in order (the ledger, §5.5)."""
    if not log.exists():
        return []
    return [r for r in read_records(log) if r.get("type") == "forecast_attempt" and r.get("player") == player
            and r.get("question_id") == question_id and r.get("tier") == tier]


def _may_rerun(chain: list[dict[str, Any]], base: dict[str, Any]) -> bool:
    """§7.5: only these failures, once, never after a confirmed leak or a model mismatch."""
    return (base["error_type"] in RERUN_ERRORS
            and not any("leak_confirmed" in (r.get("audit_flags") or ()) for r in chain)
            and not any(r.get("model_check") == "mismatch" or r.get("inherited_model_mismatch") for r in chain))


def forecast_question(ctx: Context, snap: dict[str, Any]) -> dict[str, Any]:
    """initial -> (format_repair) -> (rerun -> (format_repair)), counted over the whole ledger.

    The first adoptable attempt ever recorded is the line's answer; later runs never pick another
    one, and the single rerun is never granted again (§7.5). Continuous mode applies this
    rule per snapshot after an automatic revision decision; unresolved starts still block the question.
    """
    qid = snap["question_id"]
    all_past = history(ctx.forecasts_log, ctx.player, qid, tier=ctx.tier)
    past = [r for r in all_past if not ctx.continuous or r.get("question_snapshot_id") == snap["snapshot_id"]]
    chosen = next((r for r in past if adoptable(r)), None)
    if chosen:
        return {"question_id": qid, "attempts": [], "selected_attempt_id": chosen["attempt_id"],
                "outcome": "already_selected"}
    unresolved = unresolved_starts(ctx, qid, all_past)
    if unresolved:
        return {"question_id": qid, "attempts": [], "selected_attempt_id": None,
                "outcome": "interrupted_attempt_unresolved", "unresolved_attempt_ids": unresolved,
                "reason": "CLI start was recorded without an outcome; no automatic repeat or fresh attempt budget"}
    bases = [r for r in past if r["attempt_kind"] in ("initial", "rerun")]
    # Admission may close between a base call and its format repair. Preserve that
    # one pending repair across cycles without granting another base attempt.
    pending_repair = (past[-1] if ctx.continuous and past and past[-1]["attempt_kind"] in ("initial", "rerun")
                      and past[-1].get("repair_basis") is not None
                      and isinstance(past[-1].get("repair_raw_text"), str) else None)
    if pending_repair is None and any(r["attempt_kind"] == "rerun" for r in bases):
        return {"question_id": qid, "attempts": [], "selected_attempt_id": None, "outcome": "attempts_exhausted"}
    if pending_repair is None and bases and not _may_rerun(past, bases[0]):
        return {"question_id": qid, "attempts": [], "selected_attempt_id": None,
                "outcome": "attempts_exhausted", "reason": f"initial failed with {bases[0]['error_type'] or bases[0]['validation_status']}"}
    kinds = ["rerun"] if bases else ["initial", "rerun"]
    prev = past[-1] if past else None
    sch = V.question_schema(snap, tier=ctx.tier)
    prompt = ctx.reconciliation["prompt"] if ctx.tier == 2 else forecast_prompt(snap, ctx.now().date().isoformat())
    chain: list[dict[str, Any]] = []
    reason = None
    try:
        if pending_repair is not None:
            raw = pending_repair["repair_raw_text"]
            prev = {**pending_repair, "_raw_text": raw}
            last = run_attempt(ctx, snap, kind="format_repair", sch=sch, prev=prev,
                               basis=pending_repair["repair_basis"],
                               prompt=repair_prompt(snap, raw, pending_repair["repair_basis"], tier=ctx.tier))
            chain.append(last)
            if adoptable(last) or pending_repair["attempt_kind"] == "rerun" or not _may_rerun(past + chain, bases[0]):
                kinds = []
            prev = last
        for kind in kinds:
            base = last = run_attempt(ctx, snap, kind=kind, prompt=prompt, sch=sch, prev=prev)
            chain.append(base)
            if not adoptable(base) and base["_basis"] is not None:
                last = run_attempt(ctx, snap, kind="format_repair", sch=sch, prev=base, basis=base["_basis"],
                                   prompt=repair_prompt(snap, base["_raw_text"], base["_basis"], tier=ctx.tier))
                chain.append(last)
            if adoptable(last) or kind == "rerun" or not _may_rerun(past + chain, bases[0] if bases else base):
                break
            prev = last
    except QuestionDone as exc:
        reason = str(exc)
    selected = next((r for r in chain if adoptable(r)), None)
    outcome = "selected" if selected else ("stopped_waiting" if reason else
                                          (chain[-1]["error_type"] or chain[-1]["validation_status"]) if chain else "not_started")
    out = {"question_id": qid, "attempts": [r["attempt_id"] for r in chain],
           "selected_attempt_id": selected["attempt_id"] if selected else None, "outcome": outcome}
    if reason:
        out["reason"] = reason
    return out


def eligible(snap: dict[str, Any] | None, cfg: dict[str, Any], now: datetime) -> str | None:
    """Reason a question is not forecast now, or None."""
    if snap is None:
        return "no snapshot (run poll_questions.py first)"
    if not snap.get("supported"):
        return "unsupported_type"
    if snap.get("status") != "open":
        return f"status {snap.get('status')!r}"
    decide_by = decision_deadline(snap, cfg)
    if decide_by is not None and now >= decide_by:
        return "past the decision deadline"
    return None


@dataclass
class Lock:
    path: Path = LOCK_PATH

    def __enter__(self) -> "Lock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._fh.close()
            raise PreflightError("another forecast.py is running")
        return self

    def __exit__(self, *exc: object) -> None:
        fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()


def run(player: str, question_ids: list[int], *, allow_unapproved_baseline: bool = False,
        adapter_loader: Callable[[str], Any] = lambda p: importlib.import_module(f"adapters.{p}"),
        snapshots: dict[int, dict[str, Any]] | None = None, runs_log: Path = RUNS_LOG,
        lock: Lock | None = None, **ctx_overrides: Any) -> dict[str, Any]:
    cfg = load_run_config()
    run_id = new_id("run")
    with (lock or Lock(DATA / f"forecast.{player}.lock")):
        adapter = adapter_loader(player)
        pf = ctx_overrides.pop("preflight", None) or preflight(
            player, cfg=cfg, allow_unapproved_baseline=allow_unapproved_baseline, adapter=adapter)
        ctx = Context(player=player, adapter=adapter, cfg=cfg, run_id=run_id, preflight=pf, **ctx_overrides)
        if ctx.tier != 1:
            raise PreflightError("forecast.run is tier 1 only; use the gated reconciliation API for tier 2")
        stop = line_stopped(ctx.line_status_log, player, ctx.forecasts_log)
        if stop:
            pf.problems.append(f"line stopped since {stop['at']}: {stop['reason']} (resume with forecast.py resume)")
        for r in read_records(ctx.forecasts_log) if ctx.forecasts_log.exists() else []:
            if r.get("player") == player and r.get("error_type") == "quota_exhausted":
                retry_at = r.get("quota_retry_at_utc") or to_iso(parse_iso(r["finished_at_utc"]) + timedelta(seconds=QUOTA_BACKOFF_S))
                if ctx.now() < parse_iso(retry_at):
                    pf.problems.append(f"quota_exhausted: line paused until {retry_at}")
        if pf.ok and leak_count(ctx.forecasts_log, player, ctx.now(), resumed_at=latest_resume(ctx.line_status_log, player)) >= LEAK_STOP_COUNT:
            pf.problems.append(f"{LEAK_STOP_COUNT} or more leak_confirmed answers within {LEAK_WINDOW_DAYS} days (§7.5)")
        summary: dict[str, Any] = {"type": "forecast_run", "run_id": run_id, "player": player, "mode": "local",
                                   "pipeline_version": PIPELINE_VERSION,
                                   "started_at": to_iso(utc_now()), "preflight": pf.summary(), "questions": []}
        if stop:
            summary["notify_louis"] = True
        if pf.ok:
            snaps = snapshots if snapshots is not None else latest_snapshots()
            for qid in question_ids:
                snap = snaps.get(qid)
                reason = eligible(snap, cfg, ctx.now())
                if reason:
                    summary["questions"].append({"question_id": qid, "outcome": "skipped", "reason": reason})
                    continue
                try:
                    summary["questions"].append(forecast_question(ctx, snap))
                except LineStop as exc:
                    summary["questions"].append({"question_id": qid, "outcome": "line_stopped", "reason": str(exc)})
                    summary["line_stopped"] = str(exc)
                    if exc.notify:
                        append_record(ctx.line_status_log, {"type": "line_stop", "player": player, "run_id": run_id,
                                                            "question_id": qid, "reason": str(exc),
                                                            "at": to_iso(utc_now())})
                        summary["notify_louis"] = True
                    break
        summary["finished_at"] = to_iso(utc_now())
        append_record(runs_log, summary)
        return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--player", required=True, choices=("claude", "gemini", "gpt"))
    r.add_argument("--questions", required=True, help="comma-separated question IDs")
    r.add_argument("--allow-unapproved-baseline", action="store_true",
                   help="local mode only: run before the first baseline approval; recorded as a waiver")
    res = sub.add_parser("resume", help="record Louis's decision to lift a line stop")
    res.add_argument("--player", required=True, choices=("claude", "gemini", "gpt"))
    res.add_argument("--by", required=True, help="who decided (Louis)")
    res.add_argument("--reason", required=True, help="why the stop is lifted, e.g. the re-acceptance record ID")
    args = ap.parse_args(argv)
    if args.cmd == "resume":
        stop = line_stopped(LINE_STATUS_LOG, args.player, FORECASTS_LOG)
        if stop is None:
            print(f"{args.player} is not stopped", file=sys.stderr)
            return 1
        append_record(LINE_STATUS_LOG, {"type": "line_resume", "player": args.player, "by": args.by,
                                        "reason": args.reason, "lifts": stop, "at": to_iso(utc_now())})
        return 0
    try:
        summary = run(args.player, [int(x) for x in args.questions.split(",") if x.strip()],
                      allow_unapproved_baseline=args.allow_unapproved_baseline)
    except PreflightError as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 2
    pf = summary["preflight"]
    if not pf["ok"]:
        print("pre-run checks failed:\n  " + "\n  ".join(pf["problems"]), file=sys.stderr)
        return 1
    for q in summary["questions"]:
        print(json.dumps(q, ensure_ascii=False))
    if summary.get("line_stopped"):
        print(f"line stopped: {summary['line_stopped']}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
