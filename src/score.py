"""Compute local Log Score, clamped Log Score, Brier/CRPS Score, and 4-way pairwise bootstrap comparisons (PLAN.md §4.1–§4.4).

Key guarantees (score_v2 / cdf_score_v1):
- D128 live_itt is an independent preregistered episode view; historical summaries remain intact.
  Missing decision capture evidence blocks full-ITT conclusions even when observed pairs are complete.
- Strictly read-only on `data/` and `config/`; validates `--report-path` to reject any destination
  inside protected directories (`data/`, `config/`, `src/`, `prompts/`, `tests/`, `.git/`).
- Uses only records and the latest `resolution_event` per question with timestamps `<= as_of_utc`.
- Scores ONLY unambiguously resolved questions (`resolution_status == "resolved"`); `ambiguous`,
  `annulled`, and `unresolved` questions are strictly excluded and reported in `excluded_counts`.
- Evaluates only submitted questions (`forecast_ok` or `comment_ok` in `data/submissions.jsonl`,
  or `dry_run` when `include_dry_run=True` for local dry-run verification such as `qid=46289`).
- Binds all comparison series (`tier1_aggregate`, `player_tier1:*`, `player_tier2:*`) and configuration
  segment metadata strictly through the submitted `final_aggregate`'s provenance (`input_attempt_ids`
  and Tier 2 parent `input_attempt_ids`). Never substitutes an unsubmitted aggregate or unrelated run.
- Preregistered M5 continuous/discrete CDF scoring formula (`cdf_score_v1`):
  - `discrete`: exact bin probability mass `p_k = cdf[k + 1] - cdf[k]` (or open-bound tail mass),
    raw `log_score = ln(p_k)` (validated CDFs guarantee strictly positive step/tail mass),
    `log_score_clamped = ln(clamp(p_k, 0.01, 0.99))`, and boundary RPS with exact
    tail indicators (`H_i = 1` for `below_lower_bound`, `H_i = 0` for `above_upper_bound`).
  - `numeric`: unit-scale density `f(u) = (cdf[b + 1] - cdf[b]) * N` (uniform = 0.0 nats) or open-bound
    tail mass `cdf[0]` / `1 - cdf[-1]`, raw `log_score = ln(f(u))` / `ln(mass)`,
    `log_score_clamped = ln(max(0.01, f(u)))` / `ln(clamp(mass, 0.01, 0.99))`, and exact
    piecewise-linear unit-scale CRPS integral (`1/12` for uniform `F(u)=u` at `u*=0.5`, exact `H=0`
    for `above_upper_bound` and `H=1` for `below_lower_bound`).
- Performs 1,000-iteration synchronized paired bootstrap (question-level and cluster-level) across:
  (1) `final_aggregate`, (2) `tier1_aggregate`, (3) `player_tier1:<player>`, and
  (4) `player_tier2:<player>` (restricted to escalated questions only, per PLAN §4.3).
- Segments results by configuration version and enforces PLAN §4.3 conclusion discipline (<30, 30–99, >=100)
  including per-comparison paired sample size and 95% CI zero-exclusion checks.
- `final_aggregate` is scored from the intent's frozen `forecast_payload` (what was actually sent, PLAN §4.1),
  which must equal submit.forecast_payload() re-derived from the provenance-linked final aggregate.
- Tier 2 effects (`final vs tier1_aggregate`, `player_tier2:*`) are compared only within escalated rows.
- D52: escalated episodes without a submission are permanent ITT gaps (never imputed); any ITT gap blocks
  superiority claims; missing Tier 2 budget costs make budget totals None (never defaulted).
- Configuration segments come only from provenance; records without it are unsegmentable, never guessed.
- Official rankings come only from verified `ranking_observation` records, latest per tournament.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

_SRC_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SRC_DIR.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from aggregate import EXCLUDED, effective_flags  # noqa: E402
from flab.ids import hash_obj  # noqa: E402
from flab.ledger import read_complete  # noqa: E402
from flab.submission_identity import submission_identity, is_snapshotless_probe, submission_status  # noqa: E402
from flab.itt_cohort import select_cohort  # noqa: E402
from flab.itt_report import build_live_itt  # noqa: E402
from flab.timeutil import parse_iso, to_iso, utc_now  # noqa: E402
import numeric as N  # noqa: E402

VERSION = "score_v2"
CDF_SCORE_VERSION = "cdf_score_v1"
DEFAULT_DATA_DIR = _REPO_ROOT / "data"
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"
DEFAULT_REPORT_PATH = DEFAULT_REPORTS_DIR / "scoreboard.md"
PLAYERS = ("claude", "gemini", "gpt")
CLAMP_MIN = 0.01
CLAMP_MAX = 0.99
BOOTSTRAP_ITERATIONS = 1000
SCORING_EXCEPTIONS = (ValueError, TypeError, KeyError, N.UnsupportedScaling, N.InvalidCDF)


def _parse_iso_safe(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return parse_iso(value.strip())
    except Exception:
        return None


def _coerce_now(now: Any) -> datetime:
    if now is None:
        return utc_now()
    if isinstance(now, datetime):
        return (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    dt = _parse_iso_safe(now)
    if dt is None:
        raise ValueError(f"invalid timestamp: {now!r}")
    return dt


def _status(row: Dict[str, Any]) -> str:
    return submission_status(row)


def _clamp(p: float, lo: float = CLAMP_MIN, hi: float = CLAMP_MAX) -> float:
    return min(hi, max(lo, p))


def validate_report_path(
    report_path: Path,
    data_dir: Path = DEFAULT_DATA_DIR,
    *,
    reject_existing: bool = True,
) -> Path:
    """Ensure report output path cannot overwrite protected ledgers, configs, source files, or existing reports."""
    resolved = Path(report_path).expanduser().resolve(strict=False)
    if resolved.suffix.lower() != ".md":
        raise ValueError(f"report path must end with .md: {report_path}")
    protected_roots = [
        (_REPO_ROOT / "data").resolve(strict=False),
        Path(data_dir).expanduser().resolve(strict=False),
        (_REPO_ROOT / "config").resolve(strict=False),
        (_REPO_ROOT / "src").resolve(strict=False),
        (_REPO_ROOT / "prompts").resolve(strict=False),
        (_REPO_ROOT / "tests").resolve(strict=False),
        (_REPO_ROOT / "launchd").resolve(strict=False),
        (_REPO_ROOT / ".git").resolve(strict=False),
    ]
    for root in protected_roots:
        if resolved == root or root in resolved.parents:
            raise ValueError(f"refusing to write report inside protected directory {root}: {report_path}")
    protected_files = {
        (_REPO_ROOT / name).resolve(strict=False)
        for name in ("PLAN.md", "AGENTS.md", "CLAUDE.md", "README.md")
    }
    if resolved in protected_files:
        raise ValueError(f"refusing to overwrite protected file: {report_path}")
    if reject_existing and resolved.exists():
        raise FileExistsError(f"refusing to overwrite existing report (PLAN §4.4 requires a new report): {resolved}")
    return resolved


def write_report_exclusive(
    report_path: Optional[Path],
    board: Dict[str, Any],
    content: str,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> Path:
    """Atomically create a new report file with O_CREAT | O_EXCL, retrying generated filenames on collision."""
    payload = content.encode("utf-8")
    if report_path is not None:
        dest = validate_report_path(report_path, data_dir=data_dir, reject_existing=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            raise FileExistsError(f"refusing to overwrite existing report (PLAN §4.4 requires a new report): {dest}")
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        return dest

    as_of_compact = (
        str(board.get("as_of_utc") or "")
        .replace("-", "")
        .replace(":", "")
        .replace(".", "")
    ) or "snapshot"
    board_fp = hash_obj({
        "as_of_utc": board.get("as_of_utc"),
        "analysis_cutoff_utc": board.get("analysis_cutoff_utc"),
        "include_dry_run": bool(board.get("include_dry_run")),
        "questions": board.get("questions"),
    })[:8]
    dry_tag = "_dryrun" if board.get("include_dry_run") else ""
    base_name = f"scoreboard_{as_of_compact}{dry_tag}_{board_fp}"
    idx = 1
    while True:
        suffix = "" if idx == 1 else f"_{idx}"
        candidate = validate_report_path(
            DEFAULT_REPORTS_DIR / f"{base_name}{suffix}.md",
            data_dir=data_dir,
            reject_existing=False,
        )
        candidate.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(candidate), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            idx += 1
            continue
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        return candidate


# --------------------------------------------------------------------------- core scoring formulas
def score_binary(probability: float, outcome: int) -> Dict[str, float]:
    if type(outcome) is not int or outcome not in (0, 1):
        raise ValueError(f"binary outcome must be 0 or 1, got {outcome!r}")
    if not N.finite(probability) or not 0.0 < float(probability) < 1.0:
        raise ValueError(f"binary probability must be in (0, 1), got {probability!r}")
    p = float(probability)
    p_true = p if outcome == 1 else (1.0 - p)
    pc = _clamp(p)
    pc_true = pc if outcome == 1 else (1.0 - pc)
    return {
        "log_score": math.log(p_true),
        "log_score_clamped": math.log(pc_true),
        "brier_score": (p - float(outcome)) ** 2,
    }


def score_multiple_choice(
    option_probabilities: Dict[str, float],
    options: List[str],
    outcome: str,
) -> Dict[str, float]:
    if (
        not isinstance(options, list)
        or len(options) < 2
        or not all(isinstance(o, str) and o for o in options)
        or len(set(options)) != len(options)
    ):
        raise ValueError(f"options must be at least 2 distinct non-empty strings, got {options!r}")
    if outcome not in options:
        raise ValueError(f"outcome {outcome!r} not in options {options!r}")
    if not isinstance(option_probabilities, dict) or set(option_probabilities.keys()) != set(options):
        raise ValueError("option_probabilities keys must match options exactly")
    probs: Dict[str, float] = {}
    for opt in options:
        v = option_probabilities.get(opt)
        if not N.finite(v) or not 0.0 < float(v) < 1.0:
            raise ValueError(f"invalid probability for option {opt!r}: {v!r}")
        probs[opt] = float(v)
    total = math.fsum(probs.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"option_probabilities must sum to 1.0 (got {total})")
    p_win = probs[outcome]
    pc_win = _clamp(p_win)
    brier = math.fsum((probs[opt] - (1.0 if opt == outcome else 0.0)) ** 2 for opt in options)
    return {
        "log_score": math.log(p_win),
        "log_score_clamped": math.log(pc_win),
        "brier_score": brier,
    }


def _quad_segment_integral(du: float, err_a: float, err_b: float) -> float:
    """Exact integral of linear error squared on an interval of width `du`."""
    if du <= 0.0:
        return 0.0
    return (du / 3.0) * (err_a * err_a + err_a * err_b + err_b * err_b)


def _continuous_unit_crps(validated: List[float], n: int, u_star: Optional[float], tail: Optional[str]) -> float:
    """Exact unit-scale CRPS for piecewise-linear CDF on [0, 1]."""
    du = 1.0 / float(n)
    if tail == "below_lower_bound":
        return math.fsum(
            _quad_segment_integral(du, validated[i] - 1.0, validated[i + 1] - 1.0)
            for i in range(n)
        )
    if tail == "above_upper_bound":
        return math.fsum(
            _quad_segment_integral(du, validated[i], validated[i + 1])
            for i in range(n)
        )
    assert u_star is not None
    parts: List[float] = []
    for i in range(n):
        u0 = i / float(n)
        u1 = (i + 1) / float(n)
        f0 = validated[i]
        f1 = validated[i + 1]
        if u1 <= u_star:
            parts.append(_quad_segment_integral(du, f0, f1))
        elif u0 >= u_star:
            parts.append(_quad_segment_integral(du, f0 - 1.0, f1 - 1.0))
        else:
            frac = (u_star - u0) / du
            f_star = f0 + frac * (f1 - f0)
            parts.append(_quad_segment_integral(u_star - u0, f0, f_star))
            parts.append(_quad_segment_integral(u1 - u_star, f_star - 1.0, f1 - 1.0))
    return math.fsum(parts)


def score_cdf(cdf: List[float], snapshot: Dict[str, Any], outcome: Any) -> Dict[str, float]:
    """Preregistered local CDF scoring formula (cdf_score_v1) for numeric and discrete questions."""
    s = N.scaling(snapshot)
    validated = N.validate_cdf(cdf, snapshot)
    qtype = snapshot.get("question_type")
    n = s.n

    tail: Optional[str] = None
    u_star: Optional[float] = None
    if isinstance(outcome, str):
        norm = outcome.strip().lower()
        if norm not in ("below_lower_bound", "above_upper_bound"):
            raise ValueError(f"unrecognized string outcome for {qtype}: {outcome!r}")
        tail = norm
    elif N.finite(outcome):
        x = float(outcome)
        if x < s.lower:
            if not s.open_lower:
                raise ValueError(f"outcome {x} below closed lower bound {s.lower}")
            tail = "below_lower_bound"
        elif x > s.upper:
            if not s.open_upper:
                raise ValueError(f"outcome {x} above closed upper bound {s.upper}")
            tail = "above_upper_bound"
        else:
            u_star = min(1.0, max(0.0, s.to_u(x)))
    else:
        raise ValueError(f"invalid outcome for {qtype}: {outcome!r}")

    if tail == "below_lower_bound" and not s.open_lower:
        raise ValueError("below_lower_bound outcome on closed lower bound")
    if tail == "above_upper_bound" and not s.open_upper:
        raise ValueError("above_upper_bound outcome on closed upper bound")

    if qtype == "discrete":
        if tail == "below_lower_bound":
            mass = validated[0]
            rps = math.fsum((validated[i] - 1.0) ** 2 for i in range(n + 1)) / float(n + 1)
            return {
                "log_score": math.log(mass),
                "log_score_clamped": math.log(_clamp(mass)),
                "brier_score": rps,
            }
        if tail == "above_upper_bound":
            mass = 1.0 - validated[-1]
            rps = math.fsum((validated[i] - 0.0) ** 2 for i in range(n + 1)) / float(n + 1)
            return {
                "log_score": math.log(mass),
                "log_score_clamped": math.log(_clamp(mass)),
                "brier_score": rps,
            }
        # N.scaling(snapshot) already validates finite nominal_min/nominal_max and half-step alignment with s.lower/s.upper
        a = float(snapshot["nominal_min"])
        b = float(snapshot["nominal_max"])
        step = (b - a) / float(n - 1) if n > 1 else 1.0
        k_float = (float(outcome) - a) / step if step > 0 else 0.0
        k = int(round(k_float))
        if not (0 <= k < n) or abs(k_float - k) > 1e-4:
            raise ValueError(f"discrete outcome {outcome} does not align with grid (k={k_float})")
        mass = validated[k + 1] - validated[k]
        # Outcome is bin k: for boundary i <= k, outcome > boundary (H_i = 0); for i >= k + 1, H_i = 1
        rps = math.fsum(
            (validated[i] - (1.0 if i >= k + 1 else 0.0)) ** 2
            for i in range(n + 1)
        ) / float(n + 1)
        return {
            "log_score": math.log(mass),
            "log_score_clamped": math.log(_clamp(mass)),
            "brier_score": rps,
        }

    # Continuous numeric
    crps = _continuous_unit_crps(validated, n, u_star, tail)
    if tail == "below_lower_bound":
        mass = validated[0]
        return {
            "log_score": math.log(mass),
            "log_score_clamped": math.log(_clamp(mass)),
            "brier_score": crps,
        }
    if tail == "above_upper_bound":
        mass = 1.0 - validated[-1]
        return {
            "log_score": math.log(mass),
            "log_score_clamped": math.log(_clamp(mass)),
            "brier_score": crps,
        }
    assert u_star is not None
    b_idx = min(n - 1, max(0, int(math.floor(u_star * n))))
    step_mass = validated[b_idx + 1] - validated[b_idx]
    density = step_mass * float(n)
    return {
        "log_score": math.log(density),
        "log_score_clamped": math.log(max(CLAMP_MIN, density)),
        "brier_score": crps,
    }


def score_prediction(record: Dict[str, Any], snapshot: Dict[str, Any], outcome: Any) -> Dict[str, float]:
    """Score either an aggregate record or a forecast attempt record against canonical outcome."""
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    qtype = snapshot.get("question_type") or record.get("question_type") or "binary"
    if qtype == "binary":
        return score_binary(record.get("probability"), outcome)
    if qtype == "multiple_choice":
        options = snapshot.get("options") or record.get("options") or []
        return score_multiple_choice(record.get("option_probabilities"), options, outcome)
    if qtype in N.TYPES:
        rec_cdf_ver = record.get("cdf_version")
        if rec_cdf_ver is not None and rec_cdf_ver != N.CDF_VERSION:
            raise ValueError(f"unsupported cdf_version: {rec_cdf_ver!r} (expected {N.CDF_VERSION!r})")
        cdf = record.get("continuous_cdf")
        if cdf is None and isinstance(record.get("percentiles"), dict):
            cdf = N.to_cdf(record["percentiles"], snapshot)
        if isinstance(cdf, list) and record.get("cdf_sha256") is not None:
            if hash_obj(cdf) != record.get("cdf_sha256"):
                raise ValueError("cdf_sha256 mismatch against stored continuous_cdf/percentiles")
        return score_cdf(cdf, snapshot, outcome)
    raise ValueError(f"unsupported question_type: {qtype!r}")


# --------------------------------------------------------------------------- synchronized bootstrap & CI
SynchronizedDraw = Tuple[List[int], List[List[int]]]


def _build_synchronized_draws(
    n_items: int,
    clusters: List[Any],
    *,
    n_boot: int,
    seed_prefix: str,
) -> Tuple[List[SynchronizedDraw], List[SynchronizedDraw]]:
    """Build synchronized question-level and cluster-level draws (primary + fallback unit order)."""
    if n_items <= 0:
        return [], []
    rng_q = random.Random(_seed_from(f"{seed_prefix}:sync_q"))
    q_draws: List[SynchronizedDraw] = []
    for _ in range(n_boot):
        sampled_q = [rng_q.randrange(n_items) for _ in range(n_items)]
        fallback_q = [[idx] for idx in rng_q.sample(range(n_items), n_items)]
        q_draws.append((sampled_q, fallback_q))

    by_cluster: Dict[Any, List[int]] = {}
    for idx, cid in enumerate(clusters):
        by_cluster.setdefault(cid, []).append(idx)
    cluster_keys = list(by_cluster.keys())
    m_k = len(cluster_keys)
    rng_c = random.Random(_seed_from(f"{seed_prefix}:sync_c"))
    c_draws: List[SynchronizedDraw] = []
    for _ in range(n_boot):
        picked: List[int] = []
        for _ in range(m_k):
            picked.extend(by_cluster[cluster_keys[rng_c.randrange(m_k)]])
        fallback_c = [by_cluster[k] for k in rng_c.sample(cluster_keys, m_k)]
        c_draws.append((picked, fallback_c))
    return q_draws, c_draws


def _ci_from_synchronized_draws(
    values_by_row: Dict[int, float],
    draws: List[SynchronizedDraw],
    *,
    distinct_units: int,
) -> Optional[Tuple[float, float]]:
    if not values_by_row:
        return None
    if len(values_by_row) < 2 or distinct_units < 2:
        return None
    n_boot = len(draws)
    if n_boot == 0:
        return None
    means: List[float] = []
    for sampled, fallback_units in draws:
        sample = [values_by_row[i] for i in sampled if i in values_by_row]
        if not sample:
            # Synchronized unit-preserving fallback: pick the first available unit (question or whole cluster)
            for unit_rows in fallback_units:
                unit_vals = [values_by_row[i] for i in unit_rows if i in values_by_row]
                if unit_vals:
                    sample = unit_vals
                    break
        means.append(math.fsum(sample) / float(len(sample)))
    means.sort()
    lo_idx = max(0, min(n_boot - 1, int(math.floor(0.025 * n_boot))))
    hi_idx = max(0, min(n_boot - 1, int(math.ceil(0.975 * n_boot)) - 1))
    return (means[lo_idx], means[hi_idx])


def conclusion_discipline(n_resolved: int) -> Dict[str, Any]:
    """Enforce PLAN.md §4.3 sample-size conclusion discipline table."""
    if n_resolved < 30:
        return {
            "resolved_count": n_resolved,
            "tier": "descriptive_only",
            "superiority_assessment_eligible": False,
            "can_claim_superiority": False,
            "rule": "少於 30 題：只做描述，不下結論（PLAN §4.3）",
        }
    if n_resolved < 100:
        return {
            "resolved_count": n_resolved,
            "tier": "preliminary_with_ci",
            "superiority_assessment_eligible": False,
            "can_claim_superiority": False,
            "rule": "30 到 100 題：初步觀察，必須附信賴區間（PLAN §4.3）",
        }
    return {
        "resolved_count": n_resolved,
        "tier": "effect_size_with_ci",
        "superiority_assessment_eligible": True,
        "can_claim_superiority": False,
        "rule": "100 題以上：報告差值的效應量與信賴區間；信賴區間涵蓋 0 時，不宣稱誰比較好（PLAN §4.3）",
    }


def pairwise_conclusion_discipline(
    paired_count: int,
    ci95_question: Optional[List[float]],
    ci95_cluster: Optional[List[float]],
    *,
    higher_is_better: bool,
) -> Dict[str, Any]:
    """Evaluate per-comparison conclusion discipline using paired count and 95% CIs (PLAN §4.3)."""
    base = conclusion_discipline(paired_count)
    if (
        paired_count < 100
        or not ci95_question
        or len(ci95_question) != 2
        or not ci95_cluster
        or len(ci95_cluster) != 2
    ):
        return {
            "paired_count": paired_count,
            "tier": base["tier"],
            "superiority_assessment_eligible": False,
            "can_claim_superiority": False,
            "superiority_claim": "none_insufficient_sample" if paired_count < 100 else "none_missing_ci",
        }

    q_lo, q_hi = float(ci95_question[0]), float(ci95_question[1])
    c_lo, c_hi = float(ci95_cluster[0]), float(ci95_cluster[1])

    # Both question-level and cluster-level CIs must exclude 0 in the same direction
    pos_excl = q_lo > 0.0 and c_lo > 0.0
    neg_excl = q_hi < 0.0 and c_hi < 0.0

    if higher_is_better:
        if pos_excl:
            return {
                "paired_count": paired_count,
                "tier": "effect_size_with_ci",
                "superiority_assessment_eligible": True,
                "can_claim_superiority": True,
                "superiority_claim": "final_aggregate_better",
            }
        if neg_excl:
            return {
                "paired_count": paired_count,
                "tier": "effect_size_with_ci",
                "superiority_assessment_eligible": True,
                "can_claim_superiority": True,
                "superiority_claim": "comparator_better",
            }
    else:
        # Lower loss (Brier / CRPS) is better: diff = final - comparator < 0 means final is better
        if neg_excl:
            return {
                "paired_count": paired_count,
                "tier": "effect_size_with_ci",
                "superiority_assessment_eligible": True,
                "can_claim_superiority": True,
                "superiority_claim": "final_aggregate_better",
            }
        if pos_excl:
            return {
                "paired_count": paired_count,
                "tier": "effect_size_with_ci",
                "superiority_assessment_eligible": True,
                "can_claim_superiority": True,
                "superiority_claim": "comparator_better",
            }

    return {
        "paired_count": paired_count,
        "tier": "effect_size_with_ci",
        "superiority_assessment_eligible": True,
        "can_claim_superiority": False,
        "superiority_claim": "inconclusive_ci_covers_zero",
    }


# --------------------------------------------------------------------------- evaluation & provenance binding
def _record_before_as_of(row: Dict[str, Any], as_of_dt: datetime, *time_keys: str) -> bool:
    """Require at least one valid timestamp among `time_keys`, and all present timestamps `<= as_of_dt`."""
    found_valid = False
    for k in time_keys:
        val = row.get(k)
        if val is not None:
            if not isinstance(val, str) or not val.strip():
                return False
            dt = _parse_iso_safe(val)
            if dt is None or dt > as_of_dt:
                return False
            found_valid = True
    return found_valid


def _extract_aggregate_input_ids(agg: Dict[str, Any]) -> Optional[List[str]]:
    """Extract and validate a consistent, non-empty list of input attempt IDs from an aggregate."""
    raw_list = agg.get("input_attempt_ids")
    raw_inputs = agg.get("inputs")
    ids_from_list: Optional[List[str]] = None
    ids_from_inputs: Optional[List[str]] = None

    if raw_list is not None:
        if not isinstance(raw_list, list) or not raw_list:
            return None
        if not all(isinstance(x, str) and x.strip() for x in raw_list):
            return None
        if len(set(raw_list)) != len(raw_list):
            return None
        ids_from_list = list(raw_list)

    if raw_inputs is not None:
        if not isinstance(raw_inputs, list) or not raw_inputs:
            return None
        extracted: List[str] = []
        for inp in raw_inputs:
            if not isinstance(inp, dict):
                return None
            aid = inp.get("attempt_id")
            if not isinstance(aid, str) or not aid.strip():
                return None
            extracted.append(aid)
        if len(set(extracted)) != len(extracted):
            return None
        ids_from_inputs = extracted

    if ids_from_list is not None and ids_from_inputs is not None:
        if set(ids_from_list) != set(ids_from_inputs) or len(ids_from_list) != len(ids_from_inputs):
            return None
        return ids_from_list
    return ids_from_list if ids_from_list is not None else ids_from_inputs


def _extract_event_excluded_flags(ev: Dict[str, Any]) -> Set[str]:
    fl = set(ev.get("audit_flags") or [])
    if isinstance(ev.get("flag"), str):
        fl.add(ev["flag"])
    if ev.get("status") == "leak_confirmed":
        fl.add("leak_confirmed")
    if ev.get("effect") == "exclude_from_future_use" and not (fl & EXCLUDED):
        fl.add("leak_confirmed")
    return fl & EXCLUDED


def _compute_tainted_attempts(
    forecasts: List[Dict[str, Any]],
    audits: List[Dict[str, Any]],
    subagents: Iterable[Dict[str, Any]] = (),
) -> Set[str]:
    """Compute attempts tainted by EXCLUDED flags (direct or inherited across dependencies)."""
    attempt_rows = [
        f for f in forecasts
        if f.get("type") == "forecast_attempt" and isinstance(f.get("attempt_id"), str)
    ]
    valid_ids = {f["attempt_id"] for f in attempt_rows}
    filtered_audits: List[Dict[str, Any]] = []
    tainted_subagents: Set[str] = set()

    for s in subagents:
        sid = s.get("subagent_id")
        if isinstance(sid, str):
            fl = set(s.get("audit_flags") or [])
            if s.get("model_check") == "mismatch" or s.get("inherited_model_mismatch") is True:
                fl.add("model_mismatch")
            if s.get("tool_violations") or s.get("error_type") == "tool_scope_violation":
                fl.add("tool_scope_violation")
            if fl & EXCLUDED:
                tainted_subagents.add(sid)

    for ev in audits:
        target = ev.get("target")
        ex_fl = _extract_event_excluded_flags(ev)
        if isinstance(target, dict) and target.get("kind") == "subagent" and isinstance(target.get("id"), str):
            if ex_fl:
                tainted_subagents.add(target["id"])
            continue
        aid = ev.get("attempt_id") if target is None else (
            target.get("id") if isinstance(target, dict) and target.get("kind") == "attempt" else None
        )
        if isinstance(aid, str) and aid in valid_ids:
            norm_ev = dict(ev)
            if ex_fl:
                norm_ev["audit_flags"] = sorted(set(norm_ev.get("audit_flags") or []) | ex_fl)
            filtered_audits.append(norm_ev)

    tainted: Set[str] = set()
    try:
        if not tainted_subagents:
            flag_map = effective_flags(attempt_rows, filtered_audits)
            tainted.update(aid for aid, fl in flag_map.items() if fl & EXCLUDED)
        else:
            raise ValueError("use transitive fixed-point with subagents")
    except ValueError:
        for f in attempt_rows:
            aid = f["attempt_id"]
            fl = set(f.get("audit_flags") or []) | set(f.get("own_audit_flags") or [])
            if f.get("model_check") == "mismatch" or f.get("inherited_model_mismatch") is True:
                fl.add("model_mismatch")
            if f.get("tool_violations") or f.get("error_type") == "tool_scope_violation":
                fl.add("tool_scope_violation")
            if fl & EXCLUDED:
                tainted.add(aid)
        for ev in filtered_audits:
            aid = ev.get("attempt_id") or (ev.get("target") or {}).get("id")
            if isinstance(aid, str) and _extract_event_excluded_flags(ev):
                tainted.add(aid)

    # Fixed-point transitive propagation across subagents, input_attempt_ids, and prev_attempt_id
    sub_rows = [s for s in subagents if isinstance(s.get("subagent_id"), str)]
    changed = True
    while changed:
        changed = False
        for s in sub_rows:
            sid = s["subagent_id"]
            parent_aid = s.get("parent_attempt_id")
            in_aids = [a for a in (s.get("input_attempt_ids") or []) if isinstance(a, str)]
            in_sids = [x for x in (s.get("input_subagent_ids") or []) if isinstance(x, str)]
            if sid not in tainted_subagents and (
                (isinstance(parent_aid, str) and parent_aid in tainted)
                or any(a in tainted for a in in_aids)
                or any(x in tainted_subagents for x in in_sids)
            ):
                tainted_subagents.add(sid)
                changed = True
            if sid in tainted_subagents and s.get("result_delivered") is True and isinstance(parent_aid, str):
                if parent_aid in valid_ids and parent_aid not in tainted:
                    tainted.add(parent_aid)
                    changed = True
        for f in attempt_rows:
            aid = f["attempt_id"]
            if aid in tainted:
                continue
            parents = [a for a in (f.get("input_attempt_ids") or []) if isinstance(a, str)]
            if isinstance(f.get("prev_attempt_id"), str):
                parents.append(f["prev_attempt_id"])
            in_sids = [x for x in (f.get("input_subagent_ids") or []) if isinstance(x, str)]
            if any(p in tainted for p in parents) or any(x in tainted_subagents for x in in_sids):
                tainted.add(aid)
                changed = True

    return tainted


def _is_usable_forecast(row: Dict[str, Any], tainted_ids: Set[str]) -> bool:
    if row.get("type") != "forecast_attempt" or row.get("validation_status") != "valid":
        return False
    aid = row.get("attempt_id")
    if not isinstance(aid, str) or aid in tainted_ids:
        return False
    if row.get("model_check") == "mismatch" or row.get("inherited_model_mismatch") is True:
        return False
    flags = set(row.get("audit_flags") or []) | set(row.get("own_audit_flags") or [])
    if flags & EXCLUDED:
        return False
    return True


def _seed_from(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)


def _prediction_signature(agg: Dict[str, Any]) -> str:
    return hash_obj({
        "aggregation_version": agg.get("aggregation_version"),
        "probability": agg.get("probability"),
        "option_probabilities": agg.get("option_probabilities"),
        "continuous_cdf": agg.get("continuous_cdf"),
        "cdf": agg.get("cdf"),
        "percentiles": agg.get("percentiles"),
        "cdf_sha256": agg.get("cdf_sha256"),
        "cdf_version": agg.get("cdf_version"),
        "escalated": bool(
            (agg.get("escalation") or {}).get("escalate")
            or (agg.get("escalation") or {}).get("candidate")
        ),
    })


def submitted_prediction_record(
    sub: Dict[str, Any], snap: Dict[str, Any], final_agg: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return the prediction actually sent to Metaculus (PLAN §4.1), from the intent's frozen forecast_payload.

    The payload is re-derived from the provenance-linked final aggregate with submit.forecast_payload()
    (the same conversion submit.py used, e.g. multiple-choice 6-decimal rounding with the last option
    as remainder) and must match exactly; any absence or mismatch fails closed.
    """
    import submit as S  # lazy: only the pure payload conversion is used

    payload = sub.get("forecast_payload")
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        return None, "missing_submitted_forecast_payload"
    frozen_snap = sub.get("snapshot")
    if frozen_snap is not None and frozen_snap != snap:
        return None, "submitted_snapshot_mismatch"
    try:
        expected = S.forecast_payload(snap, final_agg)
    except Exception as exc:  # PrecheckError or malformed aggregate
        return None, f"submitted_payload_recompute_failed:{type(exc).__name__}"
    if expected != payload:
        return None, "submitted_payload_mismatch"
    item = payload[0]
    if item.get("question") != snap.get("question_id"):
        return None, "submitted_payload_question_mismatch"
    qtype = snap.get("question_type")
    if qtype == "binary":
        return {"probability": item["probability_yes"]}, None
    if qtype == "multiple_choice":
        return {"option_probabilities": dict(item["probability_yes_per_category"])}, None
    if qtype in N.TYPES:
        return {"continuous_cdf": list(item["continuous_cdf"])}, None
    return None, f"unsupported_submitted_question_type:{qtype}"


def evaluate_scoreboard(
    data_dir: Path = DEFAULT_DATA_DIR,
    *,
    as_of: Any = None,
    analysis_at: Any = None,
    include_dry_run: bool = False,
    n_boot: int = BOOTSTRAP_ITERATIONS,
) -> Dict[str, Any]:
    """Evaluate local scores, official scores, and 4-way pairwise comparisons as of `as_of`."""
    data_dir = Path(data_dir)
    if type(n_boot) is not int or n_boot < 1:
        raise ValueError("n_boot must be an integer >= 1")
    as_of_dt = _coerce_now(as_of)
    as_of_iso = to_iso(as_of_dt)
    analysis_dt = _coerce_now(analysis_at) if analysis_at is not None else as_of_dt
    if analysis_at is not None and analysis_dt < as_of_dt:
        raise ValueError(f"analysis_at ({to_iso(analysis_dt)}) cannot be earlier than as_of ({as_of_iso})")
    analysis_iso = to_iso(analysis_dt)

    raw_questions = [
        r for r in (read_complete(data_dir / "questions.jsonl") if (data_dir / "questions.jsonl").exists() else [])
        if r.get("type") == "question_snapshot"
    ]
    raw_forecasts = read_complete(data_dir / "forecasts.jsonl") if (data_dir / "forecasts.jsonl").exists() else []
    raw_aggregates = read_complete(data_dir / "aggregates.jsonl") if (data_dir / "aggregates.jsonl").exists() else []
    raw_submissions = read_complete(data_dir / "submissions.jsonl") if (data_dir / "submissions.jsonl").exists() else []
    resolutions = read_complete(data_dir / "resolutions.jsonl") if (data_dir / "resolutions.jsonl").exists() else []
    raw_cycles = read_complete(data_dir / "cycles.jsonl") if (data_dir / "cycles.jsonl").exists() else []
    raw_subagents = read_complete(data_dir / "subagents.jsonl") if (data_dir / "subagents.jsonl").exists() else []
    raw_audits = (
        (read_complete(data_dir / "audits.jsonl") if (data_dir / "audits.jsonl").exists() else [])
        + (read_complete(data_dir / "audit_impacts.jsonl") if (data_dir / "audit_impacts.jsonl").exists() else [])
    )

    questions = [r for r in raw_questions if _record_before_as_of(r, as_of_dt, "seen_at", "t0", "created_at_utc")]
    forecasts = [
        r for r in raw_forecasts
        if _record_before_as_of(r, as_of_dt, "finished_at_utc", "started_at_utc", "created_at_utc")
    ]
    aggregates = [r for r in raw_aggregates if _record_before_as_of(r, as_of_dt, "created_at_utc", "at")]
    submissions = [r for r in raw_submissions if _record_before_as_of(r, as_of_dt, "at", "created_at_utc")]
    cycles = [
        r for r in raw_cycles
        if _record_before_as_of(r, as_of_dt, "finished_at_utc", "started_at_utc", "finished_at", "started_at", "at")
    ]

    # Consistently enforce analysis_dt on both retrospective audits and subagent records
    # using _record_before_as_of (all present timestamps must be valid and <= analysis_dt, and at least one present).
    audit_ts_keys = ("audited_at", "audited_at_utc", "recorded_at_utc", "created_at_utc", "at", "observed_at_utc")
    subagent_ts_keys = ("finished_at_utc", "started_at_utc", "created_at_utc", "at", "observed_at_utc")
    audits = [r for r in raw_audits if _record_before_as_of(r, analysis_dt, *audit_ts_keys)]
    subagents = [r for r in raw_subagents if _record_before_as_of(r, analysis_dt, *subagent_ts_keys)]

    def _has_missing_or_malformed_ts(row: Dict[str, Any], *time_keys: str) -> bool:
        found_valid = False
        for k in time_keys:
            val = row.get(k)
            if val is not None:
                if not isinstance(val, str) or not val.strip() or _parse_iso_safe(val) is None:
                    return True
                found_valid = True
        return not found_valid

    audits_undated_or_malformed = sum(1 for r in raw_audits if _has_missing_or_malformed_ts(r, *audit_ts_keys))
    subagents_undated_or_malformed = sum(1 for r in raw_subagents if _has_missing_or_malformed_ts(r, *subagent_ts_keys))

    rejected_cutoff_counts = {
        "questions": len(raw_questions) - len(questions),
        "forecasts": len(raw_forecasts) - len(forecasts),
        "aggregates": len(raw_aggregates) - len(aggregates),
        "submissions": len(raw_submissions) - len(submissions),
        "cycles": len(raw_cycles) - len(cycles),
        "audits": len(raw_audits) - len(audits),
        "audits_undated": audits_undated_or_malformed,
        "audits_undated_or_malformed": audits_undated_or_malformed,
        "subagents": len(raw_subagents) - len(subagents),
        "subagents_undated": subagents_undated_or_malformed,
        "subagents_undated_or_malformed": subagents_undated_or_malformed,
    }

    tainted_ids = _compute_tainted_attempts(forecasts, audits, subagents=subagents)
    tainted_agg_ids: Set[str] = set()
    tainted_sub_ids: Set[str] = set()
    for ev in audits:
        target = ev.get("target")
        if isinstance(target, dict) and _extract_event_excluded_flags(ev):
            tkind = target.get("kind")
            tid = target.get("id")
            if tkind == "aggregate" and isinstance(tid, str):
                tainted_agg_ids.add(tid)
            elif tkind == "submission" and isinstance(tid, str):
                tainted_sub_ids.add(tid)

    attempt_by_id: Dict[str, Dict[str, Any]] = {
        f["attempt_id"]: f for f in forecasts if isinstance(f.get("attempt_id"), str)
    }

    snapshots_by_id: Dict[str, Dict[str, Any]] = {}
    latest_snap_by_qid: Dict[int, Dict[str, Any]] = {}
    for s in questions:
        sid = s.get("snapshot_id")
        if isinstance(sid, str):
            snapshots_by_id[sid] = s
        sqid = s.get("question_id")
        if type(sqid) is int:
            latest_snap_by_qid[sqid] = s

    agg_by_id: Dict[str, Dict[str, Any]] = {
        r["record_id"]: r for r in aggregates if isinstance(r.get("record_id"), str)
    }

    # Find submitted intents per question (latest completed submission <= as_of_dt)
    allowed_sub_statuses = {"forecast_ok", "comment_ok"}
    if include_dry_run:
        allowed_sub_statuses.add("dry_run")

    intents_by_id: Dict[str, Dict[str, Any]] = {}
    for row in submissions:
        iid = row.get("intent_id")
        if _status(row) == "intent" and isinstance(iid, str):
            intents_by_id[iid] = row

    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    submitted_intent_by_qid: Dict[int, Dict[str, Any]] = {}
    submitted_sort_by_qid: Dict[int, Tuple[int, datetime, int]] = {}
    for idx, row in enumerate(submissions):
        iid = row.get("intent_id")
        st = _status(row)
        if st in allowed_sub_statuses:
            base = intents_by_id.get(iid, row) if isinstance(iid, str) else row
            qid = row.get("question_id") if type(row.get("question_id")) is int else base.get("question_id")
            if type(qid) is int:
                row_dt = _parse_iso_safe(row.get("at") or row.get("created_at_utc") or base.get("at")) or min_dt
                # Live submissions (forecast_ok / comment_ok -> 1) always take precedence over dry_run (0)
                live_rank = 0 if st == "dry_run" else 1
                sort_k = (live_rank, row_dt, idx)
                if qid not in submitted_sort_by_qid or sort_k >= submitted_sort_by_qid[qid]:
                    submitted_sort_by_qid[qid] = sort_k
                    cand_sub_ids = tuple(sorted({
                        str(val)
                        for src in (base, row)
                        for k in ("record_id", "intent_id", "submission_id")
                        for val in (src.get(k),)
                        if isinstance(val, str) and val.strip()
                    }))
                    submitted_intent_by_qid[qid] = {
                        **base,
                        **row,
                        "aggregate_id": base.get("aggregate_id") or row.get("aggregate_id"),
                        **submission_identity(row, base),
                        "_all_sub_ids": cand_sub_ids,
                        "_merged_audit_flags": sorted(
                            set(base.get("audit_flags") or []) | set(row.get("audit_flags") or [])
                        ),
                    }

    # Latest resolution_event per question with observed_at_utc <= as_of_dt (ordered by observed_at_utc, then file order)
    latest_res_by_qid: Dict[int, Dict[str, Any]] = {}
    latest_res_sort_by_qid: Dict[int, Tuple[datetime, int]] = {}
    rejected_res_count = 0
    for idx, row in enumerate(resolutions):
        if row.get("type") != "resolution_event":
            continue
        obs_dt = _parse_iso_safe(row.get("observed_at_utc"))
        if obs_dt is None or obs_dt > as_of_dt:
            rejected_res_count += 1
            continue
        qid = row.get("question_id")
        if type(qid) is int:
            sort_k = (obs_dt, idx)
            if qid not in latest_res_sort_by_qid or sort_k >= latest_res_sort_by_qid[qid]:
                latest_res_sort_by_qid[qid] = sort_k
                latest_res_by_qid[qid] = row
    rejected_cutoff_counts["resolutions"] = rejected_res_count

    excluded_counts = {"ambiguous": 0, "annulled": 0, "unresolved": 0, "no_resolution_event": 0}
    rejected_submission_counts: Dict[str, int] = {}
    question_rejections: Dict[str, str] = {}
    scorable_questions: List[Dict[str, Any]] = []

    def _record_rejection(qid: int, reason_code: str, detail: Optional[str] = None) -> None:
        rejected_submission_counts[reason_code] = rejected_submission_counts.get(reason_code, 0) + 1
        question_rejections[str(qid)] = detail or reason_code

    def _validate_attempt_ancestors(
        root_att: Dict[str, Any],
        *,
        qid: int,
        canonical_snap_id: str,
    ) -> Tuple[bool, Optional[str], Set[str]]:
        """Validate all transitive prev_attempt_id and input_attempt_ids predecessors of `root_att`.

        Uses 3-color DFS (`WHITE=0`, `GRAY=1`, `BLACK=2`) so shared DAG nodes (such as a Tier 2
        attempt whose `prev_attempt_id` is also in `input_attempt_ids`, or diamond ancestry) are
        accepted, while true cycles along the active recursion path (`GRAY`) fail closed.
        """
        root_id = str(root_att.get("attempt_id") or "")
        root_tier = root_att.get("tier")
        direct_t1_inputs: Set[str] = set()
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {}

        def _visit(cur_att: Dict[str, Any], is_root: bool) -> Optional[str]:
            cur_id = str(cur_att.get("attempt_id") or "")
            c_state = color.get(cur_id, WHITE)
            if c_state == GRAY:
                return f"cyclic_attempt_dependency:{cur_id}"
            if c_state == BLACK:
                return None
            color[cur_id] = GRAY

            cur_player = cur_att.get("player")
            cur_tier = cur_att.get("tier")
            raw_in_ids = cur_att.get("input_attempt_ids")
            if raw_in_ids is not None:
                if not isinstance(raw_in_ids, list) or not all(isinstance(x, str) and x.strip() for x in raw_in_ids):
                    return f"malformed_input_attempt_ids:{cur_id}"
                for parent_id in raw_in_ids:
                    if color.get(parent_id, WHITE) == GRAY:
                        return f"cyclic_attempt_dependency:{parent_id}"
                    p_att = attempt_by_id.get(parent_id)
                    if (
                        p_att is None
                        or p_att.get("question_id") != qid
                        or p_att.get("question_snapshot_id") != canonical_snap_id
                        or p_att.get("player") not in PLAYERS
                        or p_att.get("tier") not in (1, 2)
                        or (isinstance(cur_tier, int) and p_att.get("tier") > cur_tier)
                        or not _is_usable_forecast(p_att, tainted_ids)
                    ):
                        return f"invalid_or_missing_dependency_attempt:{parent_id}"
                    if is_root and root_tier == 2 and p_att.get("tier") == 1:
                        direct_t1_inputs.add(parent_id)
                    err = _visit(p_att, False)
                    if err is not None:
                        return err

            prev_id = cur_att.get("prev_attempt_id")
            if prev_id is not None:
                if not isinstance(prev_id, str) or not prev_id.strip() or prev_id == cur_id:
                    return f"malformed_prev_attempt_id:{cur_id}"
                if color.get(prev_id, WHITE) == GRAY:
                    return f"cyclic_prev_attempt_id:{prev_id}"
                prev_att = attempt_by_id.get(prev_id)
                if (
                    prev_att is None
                    or prev_att.get("question_id") != qid
                    or prev_att.get("question_snapshot_id") != canonical_snap_id
                    or prev_att.get("player") != cur_player
                    or prev_att.get("tier") not in (1, 2)
                    or (isinstance(cur_tier, int) and prev_att.get("tier") > cur_tier)
                    or not _is_usable_forecast(prev_att, tainted_ids)
                ):
                    return f"invalid_or_unusable_prev_attempt:{prev_id}"
                err = _visit(prev_att, False)
                if err is not None:
                    return err

            color[cur_id] = BLACK
            return None

        err_msg = _visit(root_att, True)
        if err_msg is not None:
            return False, err_msg, set()
        if root_tier == 2 and not direct_t1_inputs:
            return False, f"missing_tier2_parent_provenance:{root_id}", set()
        return True, None, direct_t1_inputs

    for qid, sub in sorted(submitted_intent_by_qid.items()):
        res = latest_res_by_qid.get(qid)
        if res is None and is_snapshotless_probe(sub):
            # Mirrors resolve.py: non-live snapshot-less submissions (e.g. D87 V15 synthetic probes) are never polled.
            excluded_counts["submitted_without_snapshot_id"] = excluded_counts.get("submitted_without_snapshot_id", 0) + 1
            continue
        if res is None:
            excluded_counts["no_resolution_event"] += 1
            continue
        r_status = str(res.get("resolution_status") or "unresolved")
        if r_status != "resolved":
            excluded_counts[r_status if r_status in excluded_counts else "unresolved"] += 1
            continue

        snap_id = sub.get("question_snapshot_id")
        snap = snapshots_by_id.get(snap_id) if isinstance(snap_id, str) else None
        if snap is None or snap.get("question_id") != qid:
            excluded_counts["missing_submitted_snapshot"] = excluded_counts.get("missing_submitted_snapshot", 0) + 1
            _record_rejection(qid, "missing_submitted_snapshot", f"missing_submitted_snapshot:{snap_id}")
            continue
        canonical_snap_id = snap["snapshot_id"]

        outcome = res.get("canonical_outcome")
        scoring_errors: Dict[str, str] = {}

        # Strict provenance binding through the submitted aggregate_id (complete chain validation before acceptance)
        agg_id = sub.get("aggregate_id")
        sub_all_ids = sub.get("_all_sub_ids") or tuple(
            str(sub.get(k)) for k in ("record_id", "intent_id", "submission_id") if isinstance(sub.get(k), str)
        )
        raw_final_agg = agg_by_id.get(agg_id) if isinstance(agg_id, str) else None
        final_agg: Optional[Dict[str, Any]] = None
        p_t1: Dict[str, Dict[str, Any]] = {}
        p_t2: Dict[str, Dict[str, Any]] = {}
        tier1_agg: Optional[Dict[str, Any]] = None
        escalated = False

        if raw_final_agg is None:
            scoring_errors["final_aggregate"] = "missing_submitted_aggregate"
            _record_rejection(qid, "missing_submitted_aggregate")
        elif (
            raw_final_agg.get("question_id") != qid
            or raw_final_agg.get("question_snapshot_id") != canonical_snap_id
            or raw_final_agg.get("stage") not in (None, "final")
            or raw_final_agg.get("status") not in (None, "ready")
        ):
            scoring_errors["final_aggregate"] = "invalid_or_unready_submitted_aggregate"
            _record_rejection(qid, "invalid_or_unready_submitted_aggregate")
        else:
            direct_ids = _extract_aggregate_input_ids(raw_final_agg)
            agg_flags = (
                set(raw_final_agg.get("audit_flags") or [])
                | set(sub.get("_merged_audit_flags") or sub.get("audit_flags") or [])
            )
            if not direct_ids:
                scoring_errors["final_aggregate"] = "missing_or_inconsistent_aggregate_input_provenance"
                _record_rejection(qid, "missing_or_inconsistent_aggregate_input_provenance")
            elif (
                (agg_flags & EXCLUDED)
                or (isinstance(agg_id, str) and agg_id in tainted_agg_ids)
                or any(sid in tainted_sub_ids for sid in sub_all_ids)
                or any(aid in tainted_ids for aid in direct_ids)
            ):
                scoring_errors["final_aggregate"] = "tainted_submitted_aggregate"
                _record_rejection(qid, "tainted_submitted_aggregate")
            else:
                # Validate complete dependency chain of direct_ids (both Tier 1 and Tier 2) before accepting final_agg
                chain_ok = True
                cand_t1: Dict[str, Dict[str, Any]] = {}
                cand_t2: Dict[str, Dict[str, Any]] = {}
                tier1_lineage_ids: Set[str] = set()
                seen_direct_players: Set[str] = set()

                for aid in direct_ids:
                    att = attempt_by_id.get(aid)
                    if att is None:
                        scoring_errors["final_aggregate"] = f"missing_input_attempt:{aid}"
                        _record_rejection(qid, "missing_input_attempt", scoring_errors["final_aggregate"])
                        chain_ok = False
                        break
                    if att.get("question_id") != qid or att.get("question_snapshot_id") != canonical_snap_id:
                        scoring_errors["final_aggregate"] = f"mismatched_attempt_snapshot:{aid}"
                        _record_rejection(qid, "mismatched_attempt_snapshot", scoring_errors["final_aggregate"])
                        chain_ok = False
                        break
                    p = att.get("player")
                    t = att.get("tier")
                    if p not in PLAYERS or t not in (1, 2) or p in seen_direct_players or not _is_usable_forecast(att, tainted_ids):
                        scoring_errors["final_aggregate"] = f"invalid_or_tainted_input_attempt:{aid}"
                        _record_rejection(qid, "invalid_or_tainted_input_attempt", scoring_errors["final_aggregate"])
                        chain_ok = False
                        break
                    seen_direct_players.add(p)

                    anc_ok, anc_err, t1_ancs = _validate_attempt_ancestors(
                        att, qid=qid, canonical_snap_id=canonical_snap_id
                    )
                    if not anc_ok:
                        scoring_errors["final_aggregate"] = anc_err or f"invalid_dependency:{aid}"
                        _record_rejection(qid, (anc_err or "invalid_dependency").split(":")[0], scoring_errors["final_aggregate"])
                        chain_ok = False
                        break

                    if t == 2:
                        cand_t2[p] = att
                        tier1_lineage_ids.update(t1_ancs)
                    else:
                        cand_t1[p] = att
                        tier1_lineage_ids.add(aid)

                if chain_ok:
                    # Validate all Tier 1 lineage attempts referenced by Tier 1 / Tier 2 dependencies
                    seen_t1_by_player: Dict[str, str] = {p: a["attempt_id"] for p, a in cand_t1.items()}
                    for t1_id in sorted(tier1_lineage_ids):
                        t1_att = attempt_by_id.get(t1_id)
                        if (
                            t1_att is None
                            or t1_att.get("question_id") != qid
                            or t1_att.get("question_snapshot_id") != canonical_snap_id
                            or t1_att.get("tier") != 1
                            or t1_att.get("player") not in PLAYERS
                            or not _is_usable_forecast(t1_att, tainted_ids)
                        ):
                            scoring_errors["final_aggregate"] = f"invalid_or_missing_tier1_lineage_attempt:{t1_id}"
                            _record_rejection(
                                qid, "invalid_or_missing_tier1_lineage_attempt", scoring_errors["final_aggregate"]
                            )
                            chain_ok = False
                            break
                        tp = t1_att["player"]
                        if tp in seen_t1_by_player and seen_t1_by_player[tp] != t1_id:
                            scoring_errors["final_aggregate"] = f"conflicting_tier1_lineage_for_player:{tp}"
                            _record_rejection(
                                qid, "conflicting_tier1_lineage_for_player", scoring_errors["final_aggregate"]
                            )
                            chain_ok = False
                            break
                        seen_t1_by_player[tp] = t1_id
                        cand_t1[tp] = t1_att

                if chain_ok:
                    final_agg = raw_final_agg
                    p_t1 = cand_t1
                    p_t2 = cand_t2
                    escalated = bool(p_t2)

                    # Resolve provenance-linked tier1_aggregate with exact input set equality & matching agg version
                    final_created_dt = _parse_iso_safe(final_agg.get("created_at_utc") or final_agg.get("at"))
                    explicit_t1_agg_id = final_agg.get("tier1_aggregate_id") or final_agg.get("parent_aggregate_id")
                    candidate_t1_aggs: List[Dict[str, Any]] = []
                    for a in aggregates:
                        a_id = a.get("record_id")
                        if explicit_t1_agg_id is not None and a_id != explicit_t1_agg_id:
                            continue
                        if (
                            a.get("question_id") != qid
                            or a.get("question_snapshot_id") != canonical_snap_id
                            or a.get("stage") != "tier1"
                            or a.get("status") not in (None, "ready")
                            or a.get("aggregation_version") != final_agg.get("aggregation_version")
                        ):
                            continue
                        a_dt = _parse_iso_safe(a.get("created_at_utc") or a.get("at"))
                        if final_created_dt is not None and a_dt is not None and a_dt > final_created_dt:
                            continue
                        a_input_list = _extract_aggregate_input_ids(a)
                        if not a_input_list:
                            continue
                        a_inputs = set(a_input_list)
                        if (
                            (set(a.get("audit_flags") or []) & EXCLUDED)
                            or (isinstance(a_id, str) and a_id in tainted_agg_ids)
                            or any(aid in tainted_ids for aid in a_inputs)
                        ):
                            continue
                        if not tier1_lineage_ids or a_inputs != tier1_lineage_ids:
                            continue
                        candidate_t1_aggs.append(a)

                    if len(candidate_t1_aggs) == 1:
                        tier1_agg = candidate_t1_aggs[0]
                    elif len(candidate_t1_aggs) > 1:
                        sigs = {_prediction_signature(a) for a in candidate_t1_aggs}
                        if len(sigs) == 1:
                            tier1_agg = candidate_t1_aggs[-1]
                        else:
                            scoring_errors["tier1_aggregate"] = "ambiguous_tier1_aggregate_candidates"
                    elif explicit_t1_agg_id is not None:
                        scoring_errors["tier1_aggregate"] = "missing_explicit_tier1_aggregate"
                    elif not escalated:
                        tier1_agg = final_agg
                    else:
                        scoring_errors["tier1_aggregate"] = "missing_provenance_tier1_aggregate"

                    if tier1_agg is not None:
                        esc_info = tier1_agg.get("escalation")
                        if isinstance(esc_info, dict) and (
                            esc_info.get("escalate") is True or esc_info.get("candidate") is True
                        ):
                            escalated = True

        # Extract complete configuration identity from provenance-linked records (preserving provenance even when raw_final_agg is rejected/tainted)
        prov_atts: List[Dict[str, Any]] = [*p_t1.values(), *p_t2.values()]
        if not prov_atts and raw_final_agg is not None:
            seed_ids = list(_extract_aggregate_input_ids(raw_final_agg) or [])
            seen_prov: Set[str] = set()
            queue = list(seed_ids)
            while queue:
                cid = queue.pop(0)
                if not isinstance(cid, str) or cid in seen_prov:
                    continue
                seen_prov.add(cid)
                c_att = attempt_by_id.get(cid)
                if (
                    c_att is None
                    or c_att.get("question_id") != qid
                    or c_att.get("question_snapshot_id") != canonical_snap_id
                ):
                    continue
                prov_atts.append(c_att)
                for pid in (c_att.get("input_attempt_ids") or []):
                    if isinstance(pid, str) and pid not in seen_prov:
                        queue.append(pid)
                prev_aid = c_att.get("prev_attempt_id")
                if isinstance(prev_aid, str) and prev_aid not in seen_prov:
                    queue.append(prev_aid)
            if any(att.get("tier") == 2 for att in prov_atts):
                escalated = True

        run_cfg_set = sorted({
            str(att.get("run_config_version"))
            for att in prov_atts
            if att.get("run_config_version")
        })
        if not run_cfg_set and tier1_agg is not None:
            esc_cfg = (tier1_agg.get("escalation") or {}).get("run_config_version")
            if esc_cfg:
                run_cfg_set = [str(esc_cfg)]
        agg_ver_set = sorted({
            str(x.get("aggregation_version"))
            for x in (tier1_agg, final_agg, raw_final_agg)
            if x is not None and x.get("aggregation_version")
        })
        t1_prompt_set = sorted({
            str(att.get("prompt_version"))
            for att in prov_atts
            if att.get("tier") == 1 and att.get("prompt_version")
        })
        t2_prompt_set = sorted({
            str(att.get("prompt_version"))
            for att in prov_atts
            if att.get("tier") == 2 and att.get("prompt_version")
        })
        if not t2_prompt_set:
            for agg_cand in (final_agg, raw_final_agg):
                if agg_cand is not None and agg_cand.get("reconcile_prompt_version"):
                    t2_prompt_set = [str(agg_cand["reconcile_prompt_version"])]
                    break

        run_cfg_ver = "+".join(run_cfg_set) if run_cfg_set else "unknown"
        agg_ver = "+".join(agg_ver_set) if agg_ver_set else "unknown"
        t1_prompt_ver = "+".join(t1_prompt_set) if t1_prompt_set else "unknown"
        t2_prompt_ver = "+".join(t2_prompt_set) if t2_prompt_set else ""

        series_scores: Dict[str, Optional[Dict[str, float]]] = {}
        submitted_pred: Optional[Dict[str, Any]] = None
        if final_agg is not None:
            submitted_pred, payload_err = submitted_prediction_record(sub, snap, final_agg)
            if payload_err is not None:
                scoring_errors["final_aggregate"] = payload_err
                _record_rejection(qid, payload_err.split(":")[0], payload_err)
        if submitted_pred is not None:
            try:
                series_scores["final_aggregate"] = score_prediction(submitted_pred, snap, outcome)
            except SCORING_EXCEPTIONS as exc:
                series_scores["final_aggregate"] = None
                scoring_errors["final_aggregate"] = f"{type(exc).__name__}: {exc}"
        else:
            series_scores["final_aggregate"] = None

        if tier1_agg is not None:
            try:
                series_scores["tier1_aggregate"] = score_prediction(tier1_agg, snap, outcome)
            except SCORING_EXCEPTIONS as exc:
                series_scores["tier1_aggregate"] = None
                scoring_errors["tier1_aggregate"] = f"{type(exc).__name__}: {exc}"
        else:
            series_scores["tier1_aggregate"] = None

        for p in PLAYERS:
            s_t1 = f"player_tier1:{p}"
            if p in p_t1:
                try:
                    series_scores[s_t1] = score_prediction(p_t1[p], snap, outcome)
                except SCORING_EXCEPTIONS as exc:
                    series_scores[s_t1] = None
                    scoring_errors[s_t1] = f"{type(exc).__name__}: {exc}"
            else:
                series_scores[s_t1] = None

            s_t2 = f"player_tier2:{p}"
            if escalated and p in p_t2:
                try:
                    series_scores[s_t2] = score_prediction(p_t2[p], snap, outcome)
                except SCORING_EXCEPTIONS as exc:
                    series_scores[s_t2] = None
                    scoring_errors[s_t2] = f"{type(exc).__name__}: {exc}"
            else:
                series_scores[s_t2] = None

        scorable_questions.append({
            "question_id": qid,
            "post_id": snap.get("post_id") or qid,
            "event_cluster_id": snap.get("event_cluster_id") or snap.get("post_id") or qid,
            "tournament_id": snap.get("tournament_id"),
            "venue": snap.get("venue"),
            "question_type": snap.get("question_type"),
            "snapshot_id": canonical_snap_id,
            "submitted_aggregate_id": agg_id,
            "tier1_aggregate_id": tier1_agg.get("record_id") if tier1_agg is not None else None,
            "submission_status": _status(sub),
            "_base_config_tuple": (run_cfg_ver, agg_ver, t1_prompt_ver),
            "_t2_prompt_ver": t2_prompt_ver,
            "config_segment": "",
            "escalated": escalated,
            "resolution_event_id": res.get("resolution_event_id"),
            "canonical_outcome": outcome,
            "official_score_data": res.get("official_score_data") or {},
            "scores": series_scores,
            "scoring_errors": scoring_errors,
        })

    # Ledger-wide escalation & resource accounting (PLAN §4.3 D52)
    escalated_qids_all: Set[int] = set()
    for a in aggregates:
        qid = a.get("question_id")
        esc_info = a.get("escalation")
        if type(qid) is int and a.get("stage") == "tier1" and isinstance(esc_info, dict):
            if esc_info.get("escalate") is True or esc_info.get("candidate") is True:
                escalated_qids_all.add(qid)
    for f in forecasts:
        qid = f.get("question_id")
        if type(qid) is int and f.get("tier") == 2:
            escalated_qids_all.add(qid)
    for q in scorable_questions:
        if q["escalated"]:
            escalated_qids_all.add(q["question_id"])

    all_t2_attempts = [f for f in forecasts if f.get("tier") == 2 and type(f.get("question_id")) is int]
    t2_attempts_by_qid: Dict[int, List[Dict[str, Any]]] = {}
    for f in all_t2_attempts:
        t2_attempts_by_qid.setdefault(f["question_id"], []).append(f)

    def _target_snap_id_for_qid(qid: int) -> Optional[str]:
        if qid in scorable_by_qid and isinstance(scorable_by_qid[qid].get("snapshot_id"), str):
            return scorable_by_qid[qid]["snapshot_id"]
        sub_q = submitted_intent_by_qid.get(qid)
        if sub_q is not None and isinstance(sub_q.get("question_snapshot_id"), str):
            return sub_q["question_snapshot_id"]
        snap_q = latest_snap_by_qid.get(qid)
        if snap_q is not None and isinstance(snap_q.get("snapshot_id"), str):
            return snap_q["snapshot_id"]
        return None

    def _t2_provenance_ids(f: Dict[str, Any]) -> Set[str]:
        out_ids: Set[str] = set()
        for aid in (f.get("input_attempt_ids") or []):
            if isinstance(aid, str) and aid.strip():
                out_ids.add(aid)
        prev_aid = f.get("prev_attempt_id")
        if isinstance(prev_aid, str) and prev_aid.strip():
            out_ids.add(prev_aid)
        return out_ids

    def _derive_escalation_config_tuples(qid: int) -> List[Tuple[Tuple[str, str, str], str]]:
        snap_id = _target_snap_id_for_qid(qid)
        q_aggs = [
            a for a in aggregates
            if a.get("question_id") == qid and (snap_id is None or a.get("question_snapshot_id") == snap_id)
        ]
        q_atts = [
            f for f in forecasts
            if f.get("question_id") == qid and (snap_id is None or f.get("question_snapshot_id") == snap_id)
        ]

        all_q_t1_ids: Set[str] = {
            f["attempt_id"]
            for f in q_atts
            if f.get("tier") == 1 and isinstance(f.get("attempt_id"), str)
        }
        esc_aggs = [
            a for a in q_aggs
            if a.get("stage") == "tier1"
            and isinstance(a.get("escalation"), dict)
            and (a["escalation"].get("escalate") is True or a["escalation"].get("candidate") is True)
        ]
        tuples_out: List[Tuple[Tuple[str, str, str], str]] = []
        covered_t2_obj_ids: Set[int] = set()
        has_unlinked_t2 = any(
            f.get("tier") == 2 and not (_t2_provenance_ids(f) & all_q_t1_ids) for f in q_atts
        )

        for a in esc_aggs:
            a_t1_atts: List[Dict[str, Any]] = []
            a_t1_ids: Set[str] = set()
            for aid in (_extract_aggregate_input_ids(a) or []):
                ref = attempt_by_id.get(aid)
                if (
                    ref is not None
                    and ref.get("question_id") == qid
                    and (snap_id is None or ref.get("question_snapshot_id") == snap_id)
                    and ref not in a_t1_atts
                ):
                    a_t1_atts.append(ref)
                    if isinstance(ref.get("attempt_id"), str):
                        a_t1_ids.add(ref["attempt_id"])

            rc_s = sorted({str(f.get("run_config_version")) for f in a_t1_atts if f.get("run_config_version")})
            if not rc_s and isinstance(a.get("escalation"), dict) and (a.get("escalation") or {}).get("run_config_version"):
                rc_s = [str(a["escalation"]["run_config_version"])]

            ag_s = [str(a["aggregation_version"])] if a.get("aggregation_version") else []
            t1_s = sorted({str(f.get("prompt_version")) for f in a_t1_atts if f.get("tier") == 1 and f.get("prompt_version")})

            # Bind escalation episodes strictly through attempt provenance first;
            # never accept a Tier 2 attempt whose provenance links to a different Tier 1 episode.
            prov_t2_atts = [
                f for f in q_atts
                if f.get("tier") == 2
                and bool(a_t1_ids and (_t2_provenance_ids(f) & a_t1_ids))
                and (
                    not a.get("reconcile_prompt_version")
                    or not f.get("prompt_version")
                    or str(f.get("prompt_version")) == str(a.get("reconcile_prompt_version"))
                )
            ]
            # B5: Tier 2 attempts are bound to an episode only through attempt provenance; unlinked
            # Tier 2 attempts are never attributed by configuration similarity or file order.
            matching_t2_atts = prov_t2_atts

            for f in matching_t2_atts:
                covered_t2_obj_ids.add(id(f))

            bt = (
                "+".join(rc_s) if rc_s else "unknown",
                "+".join(ag_s) if ag_s else "unknown",
                "+".join(t1_s) if t1_s else "unknown",
            )
            # Never merge distinct Tier 2 reconciliation prompt versions into a synthetic "r1+r2" segment:
            # emit a separate episode tuple per distinct reconciliation prompt version.
            distinct_t2_pvs = sorted({str(f.get("prompt_version")) for f in matching_t2_atts if f.get("prompt_version")})
            if not distinct_t2_pvs and a.get("reconcile_prompt_version"):
                distinct_t2_pvs = [str(a["reconcile_prompt_version"])]
            if not distinct_t2_pvs and has_unlinked_t2:
                # B5: Tier 2 attempts exist on this question without provenance; we cannot tell whether one
                # of them belonged to this episode, so its reconciliation version is unknown (unsegmentable).
                distinct_t2_pvs = ["unknown"]
            if distinct_t2_pvs:
                for t2_v in distinct_t2_pvs:
                    if (bt, t2_v) not in tuples_out:
                        tuples_out.append((bt, t2_v))
            else:
                if (bt, "") not in tuples_out:
                    tuples_out.append((bt, ""))

        uncovered_t2 = [f for f in q_atts if f.get("tier") == 2 and id(f) not in covered_t2_obj_ids]
        t2_groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for f in uncovered_t2:
            g_key = (str(f.get("run_config_version") or ""), str(f.get("prompt_version") or ""))
            t2_groups.setdefault(g_key, []).append(f)

        for (rc_val, pv_val), group_t2 in t2_groups.items():
            linked_t1_atts: List[Dict[str, Any]] = []
            linked_t1_ids: Set[str] = set()
            for f in group_t2:
                for aid in _t2_provenance_ids(f):
                    ref = attempt_by_id.get(aid)
                    if (
                        ref is not None
                        and ref.get("question_id") == qid
                        and (snap_id is None or ref.get("question_snapshot_id") == snap_id)
                        and ref.get("tier") == 1
                        and ref not in linked_t1_atts
                    ):
                        linked_t1_atts.append(ref)
                        linked_t1_ids.add(aid)

            group_t2_ids = {f["attempt_id"] for f in group_t2 if isinstance(f.get("attempt_id"), str)}
            linked_aggs = [
                a for a in q_aggs
                if bool(set(_extract_aggregate_input_ids(a) or []) & (linked_t1_ids | group_t2_ids))
            ]

            rc_s = [rc_val] if rc_val else sorted({
                str(f.get("run_config_version")) for f in linked_t1_atts if f.get("run_config_version")
            })
            ag_s = sorted({str(a.get("aggregation_version")) for a in linked_aggs if a.get("aggregation_version")})
            t1_s = sorted({str(f.get("prompt_version")) for f in linked_t1_atts if f.get("tier") == 1 and f.get("prompt_version")})
            t2_pvs = [pv_val] if pv_val else sorted({
                str(a.get("reconcile_prompt_version")) for a in linked_aggs if a.get("reconcile_prompt_version")
            })
            bt = (
                "+".join(rc_s) if rc_s else "unknown",
                "+".join(ag_s) if ag_s else "unknown",
                "+".join(t1_s) if t1_s else "unknown",
            )
            if t2_pvs:
                for t2_v in t2_pvs:
                    if (bt, t2_v) not in tuples_out:
                        tuples_out.append((bt, t2_v))
            else:
                if (bt, "") not in tuples_out:
                    tuples_out.append((bt, ""))

        # B5: no provenance-linked episode -> no tuple; the caller records the question as unsegmentable.
        return tuples_out

    # Derive all configuration tuples per escalated question (including multi-config runs on the same question)
    scorable_by_qid: Dict[int, Dict[str, Any]] = {q["question_id"]: q for q in scorable_questions}
    esc_tuples_by_qid: Dict[int, List[Tuple[Tuple[str, str, str], str]]] = {}
    for eqid in sorted(escalated_qids_all):
        derived_list = _derive_escalation_config_tuples(eqid)
        if eqid in scorable_by_qid and scorable_by_qid[eqid]["escalated"]:
            sc_pair = (scorable_by_qid[eqid]["_base_config_tuple"], scorable_by_qid[eqid]["_t2_prompt_ver"])
            if "unknown" not in sc_pair[0] and sc_pair not in derived_list:
                derived_list.insert(0, sc_pair)
        esc_tuples_by_qid[eqid] = derived_list


    # Finalize complete config_segment without merging distinct Tier 2 prompt versions
    t2_by_base: Dict[Tuple[str, str, str], Set[str]] = {}
    for q in scorable_questions:
        bt = q["_base_config_tuple"]
        if "unknown" in bt and q["scores"].get("final_aggregate") is None:
            continue
        t2_by_base.setdefault(bt, set())
        if q["_t2_prompt_ver"]:
            t2_by_base[bt].add(q["_t2_prompt_ver"])
    for tuple_list in esc_tuples_by_qid.values():
        for bt, t2_v in tuple_list:
            if "unknown" in bt:
                continue
            t2_by_base.setdefault(bt, set())
            if t2_v and t2_v != "unknown":
                t2_by_base[bt].add(t2_v)

    def _format_segment_key(bt: Tuple[str, str, str], t2_ver: str) -> str:
        run_cfg_ver, agg_ver, t1_prompt_ver = bt
        if t2_ver == "unknown":
            return f"{run_cfg_ver}|{agg_ver}|{t1_prompt_ver}|unknown"
        distinct_t2 = sorted(t2_by_base.get(bt, set()))
        if len(distinct_t2) > 1:
            return f"{run_cfg_ver}|{agg_ver}|{t1_prompt_ver}|{t2_ver or 'no_tier2'}"
        if len(distinct_t2) == 1:
            return f"{run_cfg_ver}|{agg_ver}|{t1_prompt_ver}|{distinct_t2[0]}"
        if t2_ver:
            return f"{run_cfg_ver}|{agg_ver}|{t1_prompt_ver}|{t2_ver}"
        return f"{run_cfg_ver}|{agg_ver}|{t1_prompt_ver}"

    segment_by_qid: Dict[int, str] = {}
    esc_segments_by_qid: Dict[int, Set[str]] = {}
    unsegmentable_esc_qids: Set[int] = set()
    unscored_pending_match: List[Tuple[Dict[str, Any], Tuple[str, str, str], str]] = []
    for q in scorable_questions:
        bt = q.pop("_base_config_tuple")
        t2_ver = q.pop("_t2_prompt_ver")
        seg_key = _format_segment_key(bt, t2_ver)
        q["config_segment"] = seg_key
        if "unknown" not in bt or any(v is not None for v in q["scores"].values()):
            segment_by_qid[q["question_id"]] = seg_key
        else:
            unscored_pending_match.append((q, bt, t2_ver))

    # B5/E2: rows without complete config provenance are unsegmentable (handled below);
    # they are never wildcard-matched into a known segment nor pooled as a literal "unknown" segment.

    for eqid, tuple_list in esc_tuples_by_qid.items():
        for bt, t2_v in tuple_list:
            cand_seg = _format_segment_key(bt, t2_v)
            if "unknown" not in cand_seg.split("|"):
                esc_segments_by_qid.setdefault(eqid, set()).add(cand_seg)
                if eqid not in segment_by_qid:
                    segment_by_qid[eqid] = cand_seg
        if eqid in scorable_by_qid and scorable_by_qid[eqid]["escalated"]:
            sc_seg = scorable_by_qid[eqid]["config_segment"]
            if "unknown" not in sc_seg.split("|"):
                esc_segments_by_qid.setdefault(eqid, set()).add(sc_seg)
        if not esc_segments_by_qid.get(eqid):
            unsegmentable_esc_qids.add(eqid)

    def _attempt_matches_segment(
        att: Dict[str, Any], seg_key: Optional[str], snap_id: Optional[str] = None
    ) -> bool:
        if seg_key is None:
            return True
        if snap_id is not None and att.get("question_snapshot_id") is not None and att.get("question_snapshot_id") != snap_id:
            return False
        parts = seg_key.split("|")
        seg_rc = parts[0] if len(parts) > 0 else "unknown"
        seg_t1 = parts[2] if len(parts) > 2 else "unknown"
        seg_t2 = parts[3] if len(parts) > 3 else ""
        att_rc = str(att.get("run_config_version") or "")
        if (att_rc or "unknown") not in seg_rc.split("+"):
            return False
        att_pv = str(att.get("prompt_version") or "")
        if att.get("tier") == 1:
            if (att_pv or "unknown") not in seg_t1.split("+"):
                return False
        elif att.get("tier") == 2:
            if seg_t2 == "no_tier2":
                return False
            if seg_t2 and (att_pv or "unknown") not in seg_t2.split("+"):
                return False
            # Also verify provenance-linked Tier 1 attempts match the segment's Tier 1 config.
            # B5: a Tier 2 attempt without any same-question Tier 1 parent is never attributed to a segment.
            linked_parent = False
            for p_aid in _t2_provenance_ids(att):
                p_ref = attempt_by_id.get(p_aid)
                if (
                    p_ref is not None
                    and p_ref.get("question_id") == att.get("question_id")
                    and (snap_id is None or p_ref.get("question_snapshot_id") == snap_id)
                    and p_ref.get("tier") == 1
                ):
                    linked_parent = True
                    if not _attempt_matches_segment(p_ref, seg_key, snap_id):
                        return False
            if not linked_parent:
                return False
        return True

    def _agg_matches_segment(
        agg: Dict[str, Any], seg_key: Optional[str], snap_id: Optional[str] = None
    ) -> bool:
        if seg_key is None:
            return True
        if snap_id is not None and agg.get("question_snapshot_id") is not None and agg.get("question_snapshot_id") != snap_id:
            return False
        parts = seg_key.split("|")
        seg_rc = parts[0] if len(parts) > 0 else "unknown"
        seg_ag = parts[1] if len(parts) > 1 else "unknown"
        seg_t2 = parts[3] if len(parts) > 3 else ""
        agg_ver_val = str(agg.get("aggregation_version") or "")
        if (agg_ver_val or "unknown") not in seg_ag.split("+"):
            return False
        rec_pv = str(agg.get("reconcile_prompt_version") or "")
        if rec_pv and seg_t2 and seg_t2 != "no_tier2" and rec_pv not in seg_t2.split("+"):
            return False
        esc_obj = agg.get("escalation")
        if isinstance(esc_obj, dict) and esc_obj.get("run_config_version"):
            esc_rc = str(esc_obj["run_config_version"])
            if seg_rc != "unknown" and esc_rc not in seg_rc.split("+"):
                return False
        same_q_atts = [
            attempt_by_id[aid]
            for aid in (_extract_aggregate_input_ids(agg) or [])
            if aid in attempt_by_id
            and attempt_by_id[aid].get("question_id") == agg.get("question_id")
            and (snap_id is None or attempt_by_id[aid].get("question_snapshot_id") == snap_id)
        ]
        if same_q_atts and not all(_attempt_matches_segment(att, seg_key, snap_id) for att in same_q_atts):
            return False
        return True

    def _question_escalated_in_segment(qid: int, seg_key: Optional[str]) -> bool:
        if qid not in escalated_qids_all:
            return False
        if seg_key is None:
            return True
        if seg_key in esc_segments_by_qid.get(qid, set()):
            return True
        if qid in scorable_by_qid and scorable_by_qid[qid].get("config_segment") == seg_key and scorable_by_qid[qid]["escalated"]:
            return True
        snap_id = _target_snap_id_for_qid(qid)
        for a in aggregates:
            esc_info = a.get("escalation")
            if (
                a.get("question_id") == qid
                and a.get("stage") == "tier1"
                and isinstance(esc_info, dict)
                and (esc_info.get("escalate") is True or esc_info.get("candidate") is True)
                and _agg_matches_segment(a, seg_key, snap_id)
            ):
                return True
        for f in t2_attempts_by_qid.get(qid, []):
            if _attempt_matches_segment(f, seg_key, snap_id):
                return True
        return False

    def _question_submitted_in_segment(qid: int, seg_key: Optional[str]) -> bool:
        if qid not in submitted_intent_by_qid:
            return False
        if seg_key is None:
            return True
        if qid in scorable_by_qid:
            return scorable_by_qid[qid].get("config_segment") == seg_key
        sub_agg_id = submitted_intent_by_qid[qid].get("aggregate_id")
        sub_agg = agg_by_id.get(sub_agg_id) if isinstance(sub_agg_id, str) else None
        if sub_agg is not None:
            return _agg_matches_segment(sub_agg, seg_key, _target_snap_id_for_qid(qid))
        return segment_by_qid.get(qid) == seg_key

    # B5: row-level q["escalated"] comes only from the submitted series' provenance (p_t2 or the
    # provenance-linked tier1 aggregate); an unadopted escalation on the same question never flips it.

    def _extract_obj_skips(obj: Dict[str, Any]) -> Tuple[int, Set[int]]:
        cnt = 0
        qids: Set[int] = set()
        sb = obj.get("skipped_budget")
        if type(sb) is int and sb > 0:
            cnt = max(cnt, sb)
        qsb = obj.get("questions_skipped_budget")
        if type(qsb) is int and qsb > 0:
            cnt = max(cnt, qsb)
        elif isinstance(qsb, list):
            qids.update(x for x in qsb if type(x) is int)
            cnt = max(cnt, len(qids))
        return cnt, qids

    # Attribute cycle budget skips using BOTH the cycle's configuration and the question's episode memberships,
    # preserving ambiguous attribution explicitly in unattributed_cycle_budget_skips.
    budget_skipped_in_cycles_total = 0
    top_level_budget_skips_total = 0
    tier2_rec_budget_skips_total = 0
    budget_skipped_by_segment: Dict[str, int] = {}
    unattributed_cycle_budget_skips = 0
    all_segment_keys = sorted(
        set(segment_by_qid.values()) | {s for seg_set in esc_segments_by_qid.values() for s in seg_set}
    )

    def _question_candidate_segments(qid: int) -> Set[str]:
        segs = set(esc_segments_by_qid.get(qid, set()))
        if qid in segment_by_qid:
            segs.add(segment_by_qid[qid])
        return {s for s in segs if s in all_segment_keys}

    def _cycle_config_parts(c_obj: Dict[str, Any], t2_obj: Dict[str, Any]) -> Tuple[str, str, str, str]:
        c_rc = str(c_obj.get("run_config_version") or t2_obj.get("run_config_version") or "")
        c_ag = str(c_obj.get("aggregation_version") or t2_obj.get("aggregation_version") or "")
        c_t1 = str(c_obj.get("prompt_version") or c_obj.get("tier1_prompt_version") or t2_obj.get("tier1_prompt_version") or "")
        c_t2 = str(t2_obj.get("prompt_version") or t2_obj.get("reconcile_prompt_version") or c_obj.get("reconcile_prompt_version") or "")
        return c_rc, c_ag, c_t1, c_t2

    def _cycle_has_explicit_config(c_obj: Dict[str, Any], t2_obj: Dict[str, Any]) -> bool:
        return any(bool(x) for x in _cycle_config_parts(c_obj, t2_obj))

    def _cycle_matches_segment(c_obj: Dict[str, Any], t2_obj: Dict[str, Any], seg_key: str) -> bool:
        c_rc, c_ag, c_t1, c_t2 = _cycle_config_parts(c_obj, t2_obj)
        parts = seg_key.split("|")
        seg_rc = parts[0] if len(parts) > 0 else "unknown"
        seg_ag = parts[1] if len(parts) > 1 else "unknown"
        seg_t1 = parts[2] if len(parts) > 2 else "unknown"
        seg_t2 = parts[3] if len(parts) > 3 else ""
        if c_rc and seg_rc != "unknown" and c_rc not in seg_rc.split("+"):
            return False
        if c_ag and seg_ag != "unknown" and c_ag not in seg_ag.split("+"):
            return False
        if c_t1 and seg_t1 != "unknown" and c_t1 not in seg_t1.split("+"):
            return False
        if c_t2:
            if not seg_t2 or seg_t2 == "no_tier2" or c_t2 not in seg_t2.split("+"):
                return False
        return True

    for c in cycles:
        t2_rec = c.get("tier2_reconciliation") if isinstance(c.get("tier2_reconciliation"), dict) else {}
        top_cnt, top_qids = _extract_obj_skips(c)
        sub_cnt, sub_qids = _extract_obj_skips(t2_rec)
        top_level_budget_skips_total += top_cnt
        tier2_rec_budget_skips_total += sub_cnt
        explicit_skip_qids = sorted(top_qids | sub_qids)
        c_skips = max(len(explicit_skip_qids), top_cnt, sub_cnt)
        if c_skips <= 0:
            continue
        budget_skipped_in_cycles_total += c_skips

        c_qids: Set[int] = set()
        for src_obj in (c, t2_rec):
            for k_list in ("question_ids", "new_question_ids", "revised_question_ids", "questions_skipped_budget"):
                vals = src_obj.get(k_list)
                if isinstance(vals, list):
                    c_qids.update(x for x in vals if type(x) is int)

        rem_skips = c_skips - len(explicit_skip_qids)
        for sqid in explicit_skip_qids:
            sqid_segs = _question_candidate_segments(sqid)
            matched_sqid_segs = [s for s in sorted(sqid_segs) if _cycle_matches_segment(c, t2_rec, s)]
            if len(matched_sqid_segs) == 1:
                s_key = matched_sqid_segs[0]
                budget_skipped_by_segment[s_key] = budget_skipped_by_segment.get(s_key, 0) + 1
            elif len(matched_sqid_segs) > 1:
                other_qids = c_qids - {sqid}
                other_segs = {
                    s for x in other_qids for s in _question_candidate_segments(x) if _cycle_matches_segment(c, t2_rec, s)
                }
                narrowed = [s for s in matched_sqid_segs if s in other_segs]
                if len(narrowed) == 1:
                    s_key = narrowed[0]
                    budget_skipped_by_segment[s_key] = budget_skipped_by_segment.get(s_key, 0) + 1
                else:
                    unattributed_cycle_budget_skips += 1
            else:
                rem_skips += 1

        if rem_skips <= 0:
            continue
        c_segs = {
            s for x in c_qids for s in _question_candidate_segments(x) if _cycle_matches_segment(c, t2_rec, s)
        }
        if not c_segs and _cycle_has_explicit_config(c, t2_rec):
            cfg_match = [s for s in all_segment_keys if _cycle_matches_segment(c, t2_rec, s)]
            if len(cfg_match) == 1:
                c_segs = {cfg_match[0]}
        if len(c_segs) == 1:
            only_s = next(iter(c_segs))
            budget_skipped_by_segment[only_s] = budget_skipped_by_segment.get(only_s, 0) + rem_skips
        else:
            unattributed_cycle_budget_skips += rem_skips

    def _extract_attempt_budget_cost(f: Dict[str, Any]) -> Optional[float]:
        # PLAN §4.5: a missing cost is never treated as 0 or 1; the total is marked incomplete instead.
        for k in ("budget_cost", "quota_weight"):
            val = f.get(k)
            if type(val) in (int, float) and math.isfinite(float(val)) and float(val) >= 0.0:
                return float(val)
        return None

    def _sum_budget_costs(rows_in: List[Dict[str, Any]]) -> Tuple[Optional[float], int]:
        costs = [_extract_attempt_budget_cost(f) for f in rows_in]
        missing_n = sum(1 for c in costs if c is None)
        if missing_n:
            return None, missing_n
        return math.fsum(c for c in costs if c is not None), 0

    def _no_claim(disc: Dict[str, Any], claim: str, reasons: Dict[str, int]) -> Dict[str, Any]:
        return {
            **disc,
            "superiority_assessment_eligible": False,
            "can_claim_superiority": False,
            "superiority_claim": claim,
            "tier": "descriptive_only",
            "itt_incomplete_reasons": dict(reasons),
        }

    def _gate_tier2_pairwise(pairwise_in: Dict[str, Any], reasons: Dict[str, int]) -> Dict[str, Any]:
        """D52: Tier 2 effect comparisons only see complete cases; forbid claims (returns a gated deep copy)."""
        out = copy.deepcopy(pairwise_in)
        for comp_key, comp_val in out.items():
            if comp_key == "final_vs_tier1_aggregate" or comp_key.startswith("final_vs_player_tier2:"):
                for m_key in ("log_score_diff", "log_score_clamped_diff", "brier_score_diff"):
                    if isinstance(comp_val.get(m_key), dict):
                        comp_val[m_key]["conclusion_discipline"] = _no_claim(
                            comp_val[m_key]["conclusion_discipline"], "none_incomplete_itt_outcomes", reasons
                        )
                comp_val["conclusion_discipline"] = _no_claim(
                    comp_val["conclusion_discipline"], "none_incomplete_itt_outcomes", reasons
                )
        return out

    def _build_d52_escalation_accounting(
        target_qids: Set[int],
        scored_rows: List[Dict[str, Any]],
        q_draws: List[SynchronizedDraw],
        c_draws: List[SynchronizedDraw],
        *,
        segment_key: Optional[str] = None,
        pooling_prohibited: bool = False,
        seed_prefix: str = "d52",
    ) -> Dict[str, Any]:
        esc_qids = sorted(
            q for q in escalated_qids_all
            if q in target_qids and _question_escalated_in_segment(q, segment_key)
        )
        # Build (question_id, episode_segment) pairs so multi-config runs on the same question
        # are tracked separately by (question_id, config_segment) rather than collapsed.
        episodes: List[Tuple[int, Optional[str]]] = []
        if segment_key is not None:
            episodes = [(q, segment_key) for q in esc_qids]
        else:
            for q in esc_qids:
                q_segs = sorted(s for s in esc_segments_by_qid.get(q, set()) if s in all_segment_keys)
                if q_segs:
                    for s in q_segs:
                        episodes.append((q, s))
                else:
                    episodes.append((q, None))

        multi_ep_qids = {
            q for q in esc_qids if sum(1 for q2, _ in episodes if q2 == q) > 1
        }
        submitted_episodes = [
            (q, ep_seg) for q, ep_seg in episodes if _question_submitted_in_segment(q, ep_seg)
        ]
        unsubmitted_episodes = [
            (q, ep_seg) for q, ep_seg in episodes if not _question_submitted_in_segment(q, ep_seg)
        ]

        consumed_t2_rows = [
            f
            for q in esc_qids
            for f in t2_attempts_by_qid.get(q, [])
            if _attempt_matches_segment(f, segment_key, _target_snap_id_for_qid(q))
        ]
        out_of_segment_t2_count = sum(
            1
            for q in esc_qids
            for f in t2_attempts_by_qid.get(q, [])
            if not _attempt_matches_segment(f, segment_key, _target_snap_id_for_qid(q))
            and any(_attempt_matches_segment(f, s, _target_snap_id_for_qid(q)) for s in all_segment_keys)
        )
        unattributed_t2_count = sum(
            1
            for q in esc_qids
            for f in t2_attempts_by_qid.get(q, [])
            if not any(_attempt_matches_segment(f, s, _target_snap_id_for_qid(q)) for s in all_segment_keys)
        )
        total_t2_attempts = len(consumed_t2_rows)
        failed_or_tainted_t2 = sum(1 for f in consumed_t2_rows if not _is_usable_forecast(f, tainted_ids))
        total_t2_budget_units, t2_budget_cost_missing = _sum_budget_costs(consumed_t2_rows)

        scored_by_qid = {r["question_id"]: r for r in scored_rows}
        incomplete_episodes: List[Tuple[int, Optional[str]]] = []
        missing_outcome_reasons: Dict[str, str] = {}
        missing_outcome_qids_set: Set[int] = set()
        not_yet_evaluable_reasons: Dict[str, str] = {}
        not_yet_evaluable_qids_set: Set[int] = set()
        excluded_by_plan_reasons: Dict[str, str] = {}
        excluded_by_plan_qids_set: Set[int] = set()
        itt_unit_diffs: List[Tuple[int, Optional[str], Any, float]] = []  # (qid, ep_seg, cluster_id, diff)
        fallback_zero_qids: List[int] = []

        def _classify_non_evaluatable(q_id: int, r_key: str, reason: str) -> None:
            if reason in ("no_resolution_event", "resolution_status_unresolved"):
                not_yet_evaluable_qids_set.add(q_id)
                not_yet_evaluable_reasons[r_key] = reason
            elif reason in ("resolution_status_ambiguous", "resolution_status_annulled"):
                excluded_by_plan_qids_set.add(q_id)
                excluded_by_plan_reasons[r_key] = reason
            else:
                missing_outcome_qids_set.add(q_id)
                missing_outcome_reasons[r_key] = reason

        for q, ep_seg in episodes:
            q_snap_id = _target_snap_id_for_qid(q)
            q_seg_t2_rows = [
                f for f in t2_attempts_by_qid.get(q, [])
                if _attempt_matches_segment(f, ep_seg, q_snap_id)
            ]
            sub_in_ep = _question_submitted_in_segment(q, ep_seg)
            ep_in_scored = (
                q in scored_by_qid
                and (ep_seg is None or scored_by_qid[q].get("config_segment") == ep_seg)
            )
            is_incomplete = False
            if not sub_in_ep:
                is_incomplete = True
            elif ep_in_scored:
                r = scored_by_qid[q]
                if r["scores"].get("final_aggregate") is None or any(
                    r["scores"].get(f"player_tier2:{p}") is None for p in PLAYERS
                ):
                    is_incomplete = True
            else:
                usable_players = {
                    f.get("player")
                    for f in q_seg_t2_rows
                    if f.get("player") in PLAYERS and _is_usable_forecast(f, tainted_ids)
                }
                if len(usable_players) < len(PLAYERS):
                    is_incomplete = True
            if is_incomplete:
                incomplete_episodes.append((q, ep_seg))

            reason_key = f"{q}@{ep_seg}" if (q in multi_ep_qids and ep_seg is not None) else str(q)

            # Classify (q, ep_seg) for Observed-Pair vs Intent-To-Treat (ITT)
            if (
                ep_in_scored
                and scored_by_qid[q]["scores"].get("final_aggregate") is not None
                and scored_by_qid[q]["scores"].get("tier1_aggregate") is not None
            ):
                r = scored_by_qid[q]
                diff = float(r["scores"]["final_aggregate"]["log_score"]) - float(
                    r["scores"]["tier1_aggregate"]["log_score"]
                )
                itt_unit_diffs.append((q, ep_seg, r["event_cluster_id"], diff))
            elif sub_in_ep:
                # Submitted question without a valid (final_aggregate, tier1_aggregate) pair MUST NOT be imputed 0.0
                if ep_in_scored:
                    r = scored_by_qid[q]
                    err_f = r["scoring_errors"].get("final_aggregate")
                    err_t1 = r["scoring_errors"].get("tier1_aggregate")
                    _classify_non_evaluatable(q, reason_key, f"submitted_unscorable_pair:{err_f or err_t1 or 'missing_score'}")
                else:
                    res_q = latest_res_by_qid.get(q)
                    if res_q is None:
                        _classify_non_evaluatable(q, reason_key, "no_resolution_event")
                    elif str(res_q.get("resolution_status") or "unresolved") != "resolved":
                        _classify_non_evaluatable(
                            q, reason_key, f"resolution_status_{res_q.get('resolution_status') or 'unresolved'}"
                        )
                    else:
                        _classify_non_evaluatable(q, reason_key, "submitted_question_not_scorable")
            else:
                # PLAN §4.3 D52: an escalated episode that produced no submission has no outcome source
                # (resolve.py only polls submitted questions) and no pre-registered imputation rule.
                # It is a permanent ITT gap: never "not yet evaluable" and never imputed as 0.0.
                res_q = latest_res_by_qid.get(q)
                res_q_status = str(res_q.get("resolution_status") or "unresolved") if res_q is not None else None
                if res_q_status in ("ambiguous", "annulled"):
                    _classify_non_evaluatable(q, reason_key, f"resolution_status_{res_q_status}")
                else:
                    missing_outcome_qids_set.add(q)
                    missing_outcome_reasons[reason_key] = "unsubmitted_escalation_no_outcome_source"

        # Match ITT efficiency denominator (attempts and budget units) to the ITT-evaluatable episodes
        itt_t2_rows: List[Dict[str, Any]] = []
        seen_itt_t2_ids: Set[int] = set()
        for q_itt, ep_seg_itt, _, _ in itt_unit_diffs:
            q_snap_itt = _target_snap_id_for_qid(q_itt)
            for f in t2_attempts_by_qid.get(q_itt, []):
                if id(f) not in seen_itt_t2_ids and _attempt_matches_segment(f, ep_seg_itt, q_snap_itt):
                    seen_itt_t2_ids.add(id(f))
                    itt_t2_rows.append(f)
        itt_t2_attempts = len(itt_t2_rows)
        itt_t2_budget_units, itt_budget_cost_missing = _sum_budget_costs(itt_t2_rows)

        esc_indices = [i for i, r in enumerate(scored_rows) if r["escalated"]]
        esc_paired = [
            i for i in esc_indices
            if scored_rows[i]["scores"].get("final_aggregate") is not None
            and scored_rows[i]["scores"].get("tier1_aggregate") is not None
        ]

        if segment_key is not None:
            seg_skips = budget_skipped_by_segment.get(segment_key, 0)
            opp_cost: Dict[str, Any] = {
                "foregone_tier1_question_equivalents": (
                    total_t2_budget_units / float(len(PLAYERS)) if total_t2_budget_units is not None else None
                ),
                "budget_skipped_questions_in_cycles": seg_skips,
                "unattributed_cycle_budget_skips": unattributed_cycle_budget_skips,
                "out_of_segment_tier2_attempts": out_of_segment_t2_count,
                "unattributed_tier2_attempts": unattributed_t2_count,
            }
        else:
            opp_cost = {
                "foregone_tier1_question_equivalents": (
                    total_t2_budget_units / float(len(PLAYERS)) if total_t2_budget_units is not None else None
                ),
                "budget_skipped_questions_in_cycles": budget_skipped_in_cycles_total,
                "top_level_budget_skips_in_cycles": top_level_budget_skips_total,
                "tier2_reconciliation_budget_skips_in_cycles": tier2_rec_budget_skips_total,
                "unattributed_cycle_budget_skips": unattributed_cycle_budget_skips,
                "out_of_segment_tier2_attempts": 0,
                "unattributed_tier2_attempts": unattributed_t2_count,
            }

        # B1-R (PLAN §4.3 D52): every efficiency / total-gain figure must cover ALL escalated episodes.
        # Any gap means only a complete-case subset is evaluatable, which overstates Tier 2 benefit.
        # D129: unlogged first candidate decisions affect the historical denominator too.
        # Preserve observed descriptive values, but never call this complete ITT.
        itt_gaps = {
            "decision_capture_incomplete": 1,
            "missing_outcome": len(missing_outcome_reasons),
            "not_yet_evaluable": len(not_yet_evaluable_reasons),
            "escalated_unsubmitted": len(unsubmitted_episodes),
            "fallback_zero": len(fallback_zero_qids),
        }
        itt_gap_reasons = {k: v for k, v in itt_gaps.items() if v}
        itt_complete = bool(episodes) and not itt_gap_reasons
        efficiency_status = (
            "complete_itt" if itt_complete else ("no_escalated_episodes" if not episodes else "incomplete_itt")
        )

        if pooling_prohibited:
            return {
                "pooling_prohibited": True,
                "tier2_efficiency_status": "pooling_prohibited",
                "itt_gap_reasons": itt_gap_reasons,
                "evaluatable_subset_log_score_gain_nats": None,
                "escalated_questions_total": len(esc_qids),
                "escalated_episodes_total": len(episodes),
                "escalated_submitted_count": len(submitted_episodes),
                "escalated_unsubmitted_count": len(unsubmitted_episodes),
                "escalated_scorable_resolved_count": len(esc_indices),
                "paired_count": len(esc_paired),
                "itt_evaluatable_count": len(itt_unit_diffs),
                "itt_fallback_zero_count": len(fallback_zero_qids),
                "itt_tier2_attempts": itt_t2_attempts,
                "itt_tier2_budget_units": itt_t2_budget_units,
                "missing_outcome_count": len(missing_outcome_reasons),
                "missing_outcome_question_ids": sorted(missing_outcome_qids_set),
                "missing_outcome_reasons": missing_outcome_reasons,
                "not_yet_evaluable_count": len(not_yet_evaluable_reasons),
                "not_yet_evaluable_question_ids": sorted(not_yet_evaluable_qids_set),
                "not_yet_evaluable_reasons": not_yet_evaluable_reasons,
                "excluded_by_plan_count": len(excluded_by_plan_reasons),
                "excluded_by_plan_question_ids": sorted(excluded_by_plan_qids_set),
                "excluded_by_plan_reasons": excluded_by_plan_reasons,
                "incomplete_questions": len(incomplete_episodes),
                "incomplete_rate": (len(incomplete_episodes) / float(len(episodes))) if episodes else None,
                "extra_tier2_attempts_used": total_t2_attempts,
                "tier2_failed_or_tainted_attempts": failed_or_tainted_t2,
                "tier2_budget_units_consumed": total_t2_budget_units,
                "tier2_budget_cost_missing_count": t2_budget_cost_missing,
                "itt_tier2_budget_cost_missing_count": itt_budget_cost_missing,
                "total_log_score_gain_nats": None,
                "observed_total_log_score_gain_nats": None,
                "log_score_gain_per_tier2_attempt": None,
                "log_score_gain_per_budget_unit": None,
                "missed_question_opportunity_cost": opp_cost,
                "observed_pair_log_score_diff": None,
                "intent_to_treat_log_score_diff": None,
            }

        observed_pair_diff: Optional[Dict[str, Any]] = None
        observed_total_gain: Optional[float] = None
        if esc_paired:
            esc_clusters = len({scored_rows[i]["event_cluster_id"] for i in esc_paired})
            esc_diffs = {
                i: float(scored_rows[i]["scores"]["final_aggregate"]["log_score"])
                - float(scored_rows[i]["scores"]["tier1_aggregate"]["log_score"])
                for i in esc_paired
            }
            obs_mean = math.fsum(esc_diffs.values()) / float(len(esc_diffs))
            observed_total_gain = math.fsum(esc_diffs.values())
            ci_q = _ci_from_synchronized_draws(esc_diffs, q_draws, distinct_units=len(esc_paired))
            ci_c = _ci_from_synchronized_draws(esc_diffs, c_draws, distinct_units=esc_clusters)
            ci_q_l = list(ci_q) if ci_q else None
            ci_c_l = list(ci_c) if ci_c else None
            observed_pair_diff = {
                "paired_count": len(esc_paired),
                "mean": obs_mean,
                "ci95_question": ci_q_l,
                "ci95_cluster": ci_c_l,
                "conclusion_discipline": pairwise_conclusion_discipline(
                    len(esc_paired), ci_q_l, ci_c_l, higher_is_better=True
                ),
            }
            if not itt_complete:
                observed_pair_diff["conclusion_discipline"] = _no_claim(
                    observed_pair_diff["conclusion_discipline"], "none_observed_pairs_only_incomplete_itt", itt_gap_reasons
                )

        itt_log_diff: Optional[Dict[str, Any]] = None
        total_itt_log_gain: Optional[float] = None
        gain_per_attempt: Optional[float] = None
        gain_per_budget_unit: Optional[float] = None
        if itt_unit_diffs:
            itt_n = len(itt_unit_diffs)
            itt_clusters_list = [cid for _, _, cid, _ in itt_unit_diffs]
            itt_distinct_clusters = len(set(itt_clusters_list))
            itt_vals_by_idx = {idx: val for idx, (_, _, _, val) in enumerate(itt_unit_diffs)}
            itt_q_draws, itt_c_draws = _build_synchronized_draws(
                itt_n, itt_clusters_list, n_boot=n_boot, seed_prefix=f"{seed_prefix}:itt"
            )
            itt_mean = math.fsum(itt_vals_by_idx.values()) / float(itt_n)
            total_itt_log_gain = math.fsum(itt_vals_by_idx.values())
            itt_ci_q = _ci_from_synchronized_draws(itt_vals_by_idx, itt_q_draws, distinct_units=itt_n)
            itt_ci_c = _ci_from_synchronized_draws(itt_vals_by_idx, itt_c_draws, distinct_units=itt_distinct_clusters)
            itt_ci_q_l = list(itt_ci_q) if itt_ci_q else None
            itt_ci_c_l = list(itt_ci_c) if itt_ci_c else None
            itt_disc = pairwise_conclusion_discipline(itt_n, itt_ci_q_l, itt_ci_c_l, higher_is_better=True)
            if not itt_complete:
                itt_disc = _no_claim(itt_disc, "none_incomplete_itt_outcomes", itt_gap_reasons)
            itt_log_diff = {
                "evaluatable_count": itt_n,
                "observed_pair_count": len(esc_paired),
                "fallback_zero_count": len(fallback_zero_qids),
                "missing_outcome_count": len(missing_outcome_reasons),
                "missing_outcome_question_ids": sorted(missing_outcome_qids_set),
                "not_yet_evaluable_count": len(not_yet_evaluable_reasons),
                "excluded_by_plan_count": len(excluded_by_plan_reasons),
                "complete_itt_coverage": itt_complete,
                "all_escalated_episodes_resolved": (
                    len(not_yet_evaluable_reasons) == 0 and len(excluded_by_plan_reasons) == 0
                ),
                "mean": itt_mean,
                "ci95_question": itt_ci_q_l,
                "ci95_cluster": itt_ci_c_l,
                "conclusion_discipline": itt_disc,
            }
            if itt_complete and itt_t2_attempts > 0:
                gain_per_attempt = total_itt_log_gain / float(itt_t2_attempts)
            if itt_complete and itt_t2_budget_units is not None and itt_t2_budget_units > 0.0:
                gain_per_budget_unit = total_itt_log_gain / itt_t2_budget_units

        return {
            "pooling_prohibited": False,
            "tier2_efficiency_status": efficiency_status,
            "itt_gap_reasons": itt_gap_reasons,
            "evaluatable_subset_log_score_gain_nats": total_itt_log_gain,
            "escalated_questions_total": len(esc_qids),
            "escalated_episodes_total": len(episodes),
            "escalated_submitted_count": len(submitted_episodes),
            "escalated_unsubmitted_count": len(unsubmitted_episodes),
            "escalated_scorable_resolved_count": len(esc_indices),
            "paired_count": len(esc_paired),
            "itt_evaluatable_count": len(itt_unit_diffs),
            "itt_fallback_zero_count": len(fallback_zero_qids),
            "itt_tier2_attempts": itt_t2_attempts,
            "itt_tier2_budget_units": itt_t2_budget_units,
            "missing_outcome_count": len(missing_outcome_reasons),
            "missing_outcome_question_ids": sorted(missing_outcome_qids_set),
            "missing_outcome_reasons": missing_outcome_reasons,
            "not_yet_evaluable_count": len(not_yet_evaluable_reasons),
            "not_yet_evaluable_question_ids": sorted(not_yet_evaluable_qids_set),
            "not_yet_evaluable_reasons": not_yet_evaluable_reasons,
            "excluded_by_plan_count": len(excluded_by_plan_reasons),
            "excluded_by_plan_question_ids": sorted(excluded_by_plan_qids_set),
            "excluded_by_plan_reasons": excluded_by_plan_reasons,
            "incomplete_questions": len(incomplete_episodes),
            "incomplete_rate": (len(incomplete_episodes) / float(len(episodes))) if episodes else None,
            "extra_tier2_attempts_used": total_t2_attempts,
            "tier2_failed_or_tainted_attempts": failed_or_tainted_t2,
            "tier2_budget_units_consumed": total_t2_budget_units,
            "tier2_budget_cost_missing_count": t2_budget_cost_missing,
            "itt_tier2_budget_cost_missing_count": itt_budget_cost_missing,
            "total_log_score_gain_nats": total_itt_log_gain if itt_complete else None,
            "observed_total_log_score_gain_nats": observed_total_gain,
            "log_score_gain_per_tier2_attempt": gain_per_attempt,
            "log_score_gain_per_budget_unit": gain_per_budget_unit,
            "missed_question_opportunity_cost": opp_cost,
            "observed_pair_log_score_diff": observed_pair_diff,
            "intent_to_treat_log_score_diff": itt_log_diff,
        }

    all_series_names = [
        "final_aggregate",
        "tier1_aggregate",
        *(f"player_tier1:{p}" for p in PLAYERS),
        *(f"player_tier2:{p}" for p in PLAYERS),
    ]

    def _summarize_subset(
        rows: List[Dict[str, Any]],
        seed_prefix: str,
        target_qids: Set[int],
        *,
        segment_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        n_total = len(rows)
        n_escalated = sum(1 for r in rows if r["escalated"])
        row_clusters = [r["event_cluster_id"] for r in rows]
        q_draws, c_draws = _build_synchronized_draws(n_total, row_clusters, n_boot=n_boot, seed_prefix=seed_prefix)

        series_summary: Dict[str, Any] = {}
        for s_name in all_series_names:
            is_t2 = s_name.startswith("player_tier2:")
            pool_indices = [i for i, r in enumerate(rows) if (r["escalated"] if is_t2 else True)]
            denom = len(pool_indices)
            present_indices = [i for i in pool_indices if rows[i]["scores"].get(s_name) is not None]
            missing = denom - len(present_indices)
            if not present_indices:
                series_summary[s_name] = {
                    "eligible_questions": denom,
                    "scored_count": 0,
                    "missing_count": missing,
                    "log_score": None,
                    "log_score_clamped": None,
                    "brier_score": None,
                }
                continue
            distinct_clusters = len({rows[i]["event_cluster_id"] for i in present_indices})
            metrics_out: Dict[str, Any] = {
                "eligible_questions": denom,
                "scored_count": len(present_indices),
                "missing_count": missing,
            }
            for m_key in ("log_score", "log_score_clamped", "brier_score"):
                vals_by_row = {i: float(rows[i]["scores"][s_name][m_key]) for i in present_indices}
                mean_val = math.fsum(vals_by_row.values()) / float(len(vals_by_row))
                ci_q = _ci_from_synchronized_draws(vals_by_row, q_draws, distinct_units=len(present_indices))
                ci_c = _ci_from_synchronized_draws(vals_by_row, c_draws, distinct_units=distinct_clusters)
                metrics_out[m_key] = {
                    "mean": mean_val,
                    "ci95_question": list(ci_q) if ci_q else None,
                    "ci95_cluster": list(ci_c) if ci_c else None,
                }
            series_summary[s_name] = metrics_out

        # Pairwise comparisons on common questions: final_aggregate minus comparator
        pairwise: Dict[str, Any] = {}
        for comp in all_series_names[1:]:
            # PLAN §4.3: Tier 2 effects (final vs tier1 aggregate, player Tier 2) are compared only within
            # escalated questions; non-escalated rows have final == tier1 and would add structural zeros.
            is_t2 = comp.startswith("player_tier2:") or comp == "tier1_aggregate"
            pool_indices = [i for i, r in enumerate(rows) if (r["escalated"] if is_t2 else True)]
            paired_indices = [
                i for i in pool_indices
                if rows[i]["scores"].get("final_aggregate") is not None and rows[i]["scores"].get(comp) is not None
            ]
            missing_pairs = len(pool_indices) - len(paired_indices)
            if not paired_indices:
                empty_disc = pairwise_conclusion_discipline(0, None, None, higher_is_better=True)
                pairwise[f"final_vs_{comp}"] = {
                    "eligible_questions": len(pool_indices),
                    "paired_count": 0,
                    "missing_count": missing_pairs,
                    "conclusion_discipline": empty_disc,
                    "log_score_diff": None,
                    "log_score_clamped_diff": None,
                    "brier_score_diff": None,
                }
                continue
            distinct_clusters = len({rows[i]["event_cluster_id"] for i in paired_indices})
            escalated_paired_count = sum(1 for i in paired_indices if rows[i]["escalated"])
            effective_paired_count = len(paired_indices)
            p_out: Dict[str, Any] = {
                "eligible_questions": len(pool_indices),
                "paired_count": len(paired_indices),
                "escalated_paired_count": escalated_paired_count,
                "missing_count": missing_pairs,
            }
            for m_key in ("log_score", "log_score_clamped", "brier_score"):
                diffs_by_row = {
                    i: float(rows[i]["scores"]["final_aggregate"][m_key]) - float(rows[i]["scores"][comp][m_key])
                    for i in paired_indices
                }
                mean_diff = math.fsum(diffs_by_row.values()) / float(len(diffs_by_row))
                ci_q = _ci_from_synchronized_draws(diffs_by_row, q_draws, distinct_units=len(paired_indices))
                ci_c = _ci_from_synchronized_draws(diffs_by_row, c_draws, distinct_units=distinct_clusters)
                ci_q_list = list(ci_q) if ci_q else None
                ci_c_list = list(ci_c) if ci_c else None
                higher_better = m_key != "brier_score"
                disc = pairwise_conclusion_discipline(
                    effective_paired_count, ci_q_list, ci_c_list, higher_is_better=higher_better
                )
                p_out[f"{m_key}_diff"] = {
                    "mean": mean_diff,
                    "ci95_question": ci_q_list,
                    "ci95_cluster": ci_c_list,
                    "conclusion_discipline": disc,
                }
            # Primary comparison conclusion discipline is governed by the primary metric (log_score_diff)
            p_out["conclusion_discipline"] = p_out["log_score_diff"]["conclusion_discipline"]
            pairwise[f"final_vs_{comp}"] = p_out

        sub_spot_scores = [
            float(r["official_score_data"]["spot_peer_score"])
            for r in rows
            if isinstance(r["official_score_data"].get("spot_peer_score"), (int, float))
        ]
        sub_peer_scores = [
            float(r["official_score_data"]["peer_score"])
            for r in rows
            if isinstance(r["official_score_data"].get("peer_score"), (int, float))
        ]

        t2_analysis = _build_d52_escalation_accounting(
            target_qids,
            rows,
            q_draws,
            c_draws,
            segment_key=segment_key,
            pooling_prohibited=False,
            seed_prefix=seed_prefix,
        )
        if t2_analysis.get("tier2_efficiency_status") != "complete_itt":
            # Tier 2 effect comparisons only see submitted escalated rows (complete cases); D52 forbids claims then.
            pairwise = _gate_tier2_pairwise(
                pairwise,
                t2_analysis.get("itt_gap_reasons") or {str(t2_analysis.get("tier2_efficiency_status")): 1},
            )

        return {
            "resolved_count": n_total,
            "escalated_count": n_escalated,
            "conclusion_discipline": conclusion_discipline(n_total),
            "official_scores": {
                "spot_peer_score_count": len(sub_spot_scores),
                "spot_peer_score_sum": math.fsum(sub_spot_scores) if sub_spot_scores else None,
                "spot_peer_score_mean": (math.fsum(sub_spot_scores) / len(sub_spot_scores)) if sub_spot_scores else None,
                "peer_score_count": len(sub_peer_scores),
                "peer_score_sum": math.fsum(sub_peer_scores) if sub_peer_scores else None,
                "peer_score_mean": (math.fsum(sub_peer_scores) / len(sub_peer_scores)) if sub_peer_scores else None,
            },
            "tier2_escalation_analysis": t2_analysis,
            "series": series_summary,
            "pairwise": pairwise,
        }

    # Segment by config_segment per PLAN §4.3 (never pool scores/CIs/claims across distinct config segments)
    by_segment: Dict[str, List[Dict[str, Any]]] = {}
    by_segment_qids: Dict[str, Set[int]] = {}
    unsegmentable_question_ids: List[int] = sorted(unsegmentable_esc_qids)
    for q in scorable_questions:
        seg = q["config_segment"]
        if "unknown" in seg.split("|"):  # E2: missing config provenance -> unsegmentable, never pooled
            if q["question_id"] not in unsegmentable_question_ids:
                unsegmentable_question_ids.append(q["question_id"])
            continue
        by_segment.setdefault(seg, []).append(q)
        by_segment_qids.setdefault(seg, set()).add(q["question_id"])
    for qid, seg in segment_by_qid.items():
        if "unknown" in seg.split("|"):
            if qid not in unsegmentable_question_ids:
                unsegmentable_question_ids.append(qid)
            continue
        by_segment.setdefault(seg, [])
        by_segment_qids.setdefault(seg, set()).add(qid)
    for eqid, seg_set in sorted(esc_segments_by_qid.items()):
        for seg in sorted(seg_set):
            if "unknown" in seg.split("|"):
                if eqid not in unsegmentable_question_ids:
                    unsegmentable_question_ids.append(eqid)
                continue
            by_segment.setdefault(seg, [])
            by_segment_qids.setdefault(seg, set()).add(eqid)

    segments_out = {
        seg: _summarize_subset(
            rows,
            seed_prefix=f"{as_of_iso}:{seg}",
            target_qids=by_segment_qids.get(seg, {r["question_id"] for r in rows}),
            segment_key=seg,
        )
        for seg, rows in sorted(by_segment.items())
    }

    all_known_qids = (
        {s["question_id"] for s in questions if type(s.get("question_id")) is int}
        | set(submitted_intent_by_qid.keys())
        | set(escalated_qids_all)
    )
    if len(segments_out) == 1:
        only_seg_key, only_seg_summary = next(iter(segments_out.items()))
        only_seg_unattr_t2 = (
            (only_seg_summary.get("tier2_escalation_analysis") or {})
            .get("missed_question_opportunity_cost", {})
            .get("unattributed_tier2_attempts", 0)
        )
        has_external_unsegmented_t2 = bool(unsegmentable_question_ids) or (only_seg_unattr_t2 > 0)
        overall_q_draws, overall_c_draws = _build_synchronized_draws(
            len(scorable_questions),
            [r["event_cluster_id"] for r in scorable_questions],
            n_boot=n_boot,
            seed_prefix=f"{as_of_iso}:overall",
        )
        overall_summary: Dict[str, Any] = {
            **only_seg_summary,
            "pooled_across_segments": False,
            "segment_count": 1,
            "unsegmentable_question_ids": sorted(unsegmentable_question_ids),
            "tier2_escalation_analysis": _build_d52_escalation_accounting(
                all_known_qids,
                scorable_questions,
                overall_q_draws,
                overall_c_draws,
                segment_key=None if has_external_unsegmented_t2 else only_seg_key,
                pooling_prohibited=has_external_unsegmented_t2,
                seed_prefix=f"{as_of_iso}:overall",
            ),
        }
        if has_external_unsegmented_t2 and isinstance(overall_summary.get("pairwise"), dict):
            # E6: the copied single-segment pairwise omits escalations outside the segment -> no overall claims.
            overall_summary["pairwise"] = _gate_tier2_pairwise(
                overall_summary["pairwise"],
                {"unsegmentable_questions_outside_segment": len(unsegmentable_question_ids),
                 "unattributed_tier2_attempts": int(only_seg_unattr_t2 or 0)},
            )
    elif len(segments_out) == 0:
        empty_summary = _summarize_subset([], seed_prefix=f"{as_of_iso}:empty", target_qids=all_known_qids)
        overall_summary = {
            **empty_summary,
            "pooled_across_segments": False,
            "segment_count": 0,
            "unsegmentable_question_ids": sorted(unsegmentable_question_ids),
        }
    else:
        # Multiple configuration segments present: PLAN §4.3 prohibits pooling scores/CIs/claims across segments
        overall_summary = {
            "resolved_count": len(scorable_questions),
            "escalated_count": sum(1 for r in scorable_questions if r["escalated"]),
            "segment_count": len(segments_out),
            "unsegmentable_question_ids": sorted(unsegmentable_question_ids),
            "pooled_across_segments": False,
            "pooling_prohibited": True,
            "pooling_prohibited_reason": "不同設定版本（run_config、agg、prompt）的紀錄分段分析，不合併計算（PLAN §4.3）",
            "conclusion_discipline": conclusion_discipline(0),
            "tier2_escalation_analysis": _build_d52_escalation_accounting(
                all_known_qids,
                scorable_questions,
                [],
                [],
                segment_key=None,
                pooling_prohibited=True,
                seed_prefix=f"{as_of_iso}:overall_d52",
            ),
            "series": None,
            "pairwise": None,
        }

    spot_scores = [
        float(q["official_score_data"]["spot_peer_score"])
        for q in scorable_questions
        if isinstance(q["official_score_data"].get("spot_peer_score"), (int, float))
    ]
    peer_scores = [
        float(q["official_score_data"]["peer_score"])
        for q in scorable_questions
        if isinstance(q["official_score_data"].get("peer_score"), (int, float))
    ]

    # B3/B4: official tournament rankings come only from verified `ranking_observation` records
    # written by resolve.py, latest per tournament with observed_at_utc <= as_of, and only when the matched
    # user id equals the single bot id on that tournament's submitted rows. There is no global
    # "latest" ranking across tournaments and no question-level ranking.
    bot_ids_by_tour: Dict[int, Set[Any]] = {}
    for row in submissions:
        if _status(row) in ("forecast_ok", "comment_ok") and type(row.get("tournament_id")) is int:
            bot_ids_by_tour.setdefault(row["tournament_id"], set()).add(row.get("bot_id"))
    rankings_by_tour: Dict[str, Tuple[Tuple[datetime, int], Dict[str, Any]]] = {}
    unverified_ranking_observations = 0
    ranking_out_keys = (
        "tournament_id", "rank", "matched_user_id", "leaderboard_id", "leaderboard_name",
        "leaderboard_score_type", "leaderboard_finalized", "calculated_on", "observed_at_utc",
        "ranking_observation_id", "endpoint",
    )
    for idx, row in enumerate(resolutions):
        if row.get("type") != "ranking_observation":
            continue
        obs_dt = _parse_iso_safe(row.get("observed_at_utc"))
        if obs_dt is None or obs_dt > as_of_dt:
            continue
        tid = row.get("tournament_id")
        tour_bots = bot_ids_by_tour.get(tid, set()) if type(tid) is int else set()
        if (
            type(tid) is not int
            or row.get("ranking_verified") is not True
            or type(row.get("rank")) is not int
            or row["rank"] < 1
            or type(row.get("matched_user_id")) is not int
            or tour_bots != {row.get("matched_user_id")}
        ):
            unverified_ranking_observations += 1
            continue
        sort_k = (obs_dt, idx)
        prev_rk = rankings_by_tour.get(str(tid))
        if prev_rk is None or sort_k >= prev_rk[0]:
            rankings_by_tour[str(tid)] = (sort_k, {k: row[k] for k in ranking_out_keys if k in row})
    official_rankings_by_tournament = {k: v[1] for k, v in sorted(rankings_by_tour.items())}

    # D128 is a separate episode-level view. Keep all historical rows for
    # provenance and the existing score/official summaries; never time-slice
    # input forecasts to manufacture a live-only successful subset.
    def cohort_ledger(name):
        return read_complete(data_dir / name) if (data_dir / name).exists() else []

    cohort = select_cohort(
        questions=raw_questions, forecasts=raw_forecasts, aggregates=raw_aggregates,
        budget_events=cohort_ledger("budget_events.jsonl"),
        campaigns=cohort_ledger("continuous_campaigns.jsonl"),
        live_authorizations=cohort_ledger("live_authorizations.jsonl"),
        scheduler_runs=cohort_ledger("scheduler_runs.jsonl"),
        scheduler_jobs=cohort_ledger("scheduler_jobs.jsonl"),
        reconciliation_runs=cohort_ledger("reconciliation_runs.jsonl"),
        as_of=as_of_iso,
    )
    live_itt = build_live_itt(
        cohort, scored_questions=scorable_questions, submissions=raw_submissions,
        forecasts=raw_forecasts, aggregates=raw_aggregates, resolutions=resolutions,
        subagents=raw_subagents, as_of=as_of_iso, n_boot=n_boot,
        build_draws=_build_synchronized_draws, ci_from_draws=_ci_from_synchronized_draws,
        conclusion=pairwise_conclusion_discipline,
    )
    multi_seg_prohibited = len(segments_out) > 1
    return {
        "version": VERSION,
        "cdf_score_version": CDF_SCORE_VERSION,
        "as_of_utc": as_of_iso,
        "analysis_cutoff_utc": analysis_iso,
        "include_dry_run": bool(include_dry_run),
        "bootstrap_iterations": n_boot,
        "submitted_questions_count": len(submitted_intent_by_qid),
        "scorable_resolved_count": len(scorable_questions),
        "excluded_counts": excluded_counts,
        "rejected_cutoff_counts": rejected_cutoff_counts,
        "rejected_submission_counts": rejected_submission_counts,
        "question_rejections": question_rejections,
        "conclusion_discipline": (
            conclusion_discipline(len(scorable_questions))
            if not multi_seg_prohibited
            else conclusion_discipline(0)
        ),
        "official_scores": {
            "pooling_prohibited": multi_seg_prohibited,
            "spot_peer_score_count": len(spot_scores),
            "spot_peer_score_sum": (
                (math.fsum(spot_scores) if spot_scores else None)
                if not multi_seg_prohibited
                else None
            ),
            "spot_peer_score_mean": (
                ((math.fsum(spot_scores) / len(spot_scores)) if spot_scores else None)
                if not multi_seg_prohibited
                else None
            ),
            "peer_score_count": len(peer_scores),
            "peer_score_sum": (
                (math.fsum(peer_scores) if peer_scores else None)
                if not multi_seg_prohibited
                else None
            ),
            "peer_score_mean": (
                ((math.fsum(peer_scores) / len(peer_scores)) if peer_scores else None)
                if not multi_seg_prohibited
                else None
            ),
            "official_rankings_by_tournament": official_rankings_by_tournament,
            "unverified_ranking_observations": unverified_ranking_observations,
        },
        "historical_d52_scope": "all_history_d127",
        "live_itt": live_itt,
        "overall": overall_summary,
        "by_config_segment": segments_out,
        "questions": scorable_questions,
    }


def _fmt_metric(m: Optional[Dict[str, Any]]) -> str:
    if not m or m.get("mean") is None:
        return "—"
    mean = m["mean"]
    ci_q = m.get("ci95_question")
    ci_c = m.get("ci95_cluster")
    out = f"{mean:.4f}"
    if ci_q and len(ci_q) == 2:
        out += f" [Q: {ci_q[0]:.4f}, {ci_q[1]:.4f}]"
    if ci_c and len(ci_c) == 2:
        out += f" (C: {ci_c[0]:.4f}, {ci_c[1]:.4f})"
    return out


def _render_subset_tables(subset: Dict[str, Any]) -> List[str]:
    if subset.get("pooling_prohibited") or subset.get("series") is None or subset.get("pairwise") is None:
        lines = [
            f"> **Non-pooling rule enforced (PLAN §4.3)**: {subset.get('pooling_prohibited_reason')} "
            f"(segments=`{subset.get('segment_count')}`, total_resolved=`{subset.get('resolved_count')}`). "
            "See Section 2 for per-segment tables."
        ]
        t2_info = subset.get("tier2_escalation_analysis")
        if isinstance(t2_info, dict):
            opp = t2_info.get("missed_question_opportunity_cost") or {}
            lines += [
                "",
                f"- **Cross-Segment Tier 2 Escalation Counts Only (No Pooled Effect Estimates)**: "
                f"escalated_total=`{t2_info.get('escalated_questions_total')}`, "
                f"unsubmitted=`{t2_info.get('escalated_unsubmitted_count')}`, "
                f"missing_outcomes=`{t2_info.get('missing_outcome_count')}`, "
                f"tier2_attempts_consumed=`{t2_info.get('extra_tier2_attempts_used')}`, "
                f"budget_units=`{t2_info.get('tier2_budget_units_consumed')}`, "
                f"foregone_tier1_equivalents=`{opp.get('foregone_tier1_question_equivalents')}`",
            ]
        return lines
    lines = [
        "| Series | Eligible | Scored | Missing | Log Score (95% CI Q / C) | Diagnostic Clamped Log Score ([0.01, 0.99] prob / 0.01/N CDF floor) | Brier / CRPS (95% CI Q / C) |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for s_name, s_data in subset["series"].items():
        lines.append(
            f"| `{s_name}` | {s_data['eligible_questions']} | {s_data['scored_count']} | {s_data['missing_count']} | "
            f"{_fmt_metric(s_data.get('log_score'))} | {_fmt_metric(s_data.get('log_score_clamped'))} | "
            f"{_fmt_metric(s_data.get('brier_score'))} |"
        )
    lines += [
        "",
        "| Comparison | Eligible | Paired | Missing | Claim | Δ Log Score (95% CI Q / C) | Δ Clamped Log Score | Δ Brier / CRPS (95% CI Q / C) |",
        "|---|---:|---:|---:|---|---|---|---|",
    ]
    for p_name, p_data in subset["pairwise"].items():
        claim = (p_data.get("conclusion_discipline") or {}).get("superiority_claim", "—")
        lines.append(
            f"| `{p_name}` | {p_data['eligible_questions']} | {p_data['paired_count']} | {p_data['missing_count']} | "
            f"`{claim}` | {_fmt_metric(p_data.get('log_score_diff'))} | {_fmt_metric(p_data.get('log_score_clamped_diff'))} | "
            f"{_fmt_metric(p_data.get('brier_score_diff'))} |"
        )
    t2_info = subset.get("tier2_escalation_analysis")
    if isinstance(t2_info, dict):
        opp = t2_info.get("missed_question_opportunity_cost") or {}
        inc_rate = t2_info.get("incomplete_rate")
        inc_str = f"{inc_rate:.2%}" if isinstance(inc_rate, float) else "—"
        gain_att = t2_info.get("log_score_gain_per_tier2_attempt")
        gain_att_str = f"{gain_att:.4f}" if isinstance(gain_att, float) else "—"
        gain_unit = t2_info.get("log_score_gain_per_budget_unit")
        gain_unit_str = f"{gain_unit:.4f}" if isinstance(gain_unit, float) else "—"
        lines += [
            "",
            f"- **Tier 2 Escalation Accounting (PLAN §4.3 / D52)**: "
            f"escalated_total=`{t2_info.get('escalated_questions_total')}`, "
            f"unsubmitted=`{t2_info.get('escalated_unsubmitted_count')}`, "
            f"missing_outcomes=`{t2_info.get('missing_outcome_count')}` (`{t2_info.get('missing_outcome_question_ids')}`), "
            f"incomplete=`{t2_info.get('incomplete_questions')}` (`{inc_str}`), "
            f"tier2_attempts_consumed=`{t2_info.get('extra_tier2_attempts_used')}` "
            f"(failed/tainted=`{t2_info.get('tier2_failed_or_tainted_attempts')}`), "
            f"budget_units=`{t2_info.get('tier2_budget_units_consumed')}`, "
            f"Observed-Pair ΔLogScore=`{_fmt_metric(t2_info.get('observed_pair_log_score_diff'))}`, "
            f"ITT ΔLogScore=`{_fmt_metric(t2_info.get('intent_to_treat_log_score_diff'))}`, "
            f"gain/attempt=`{gain_att_str}`, gain/unit=`{gain_unit_str}`, "
            f"foregone_tier1_equivalents=`{opp.get('foregone_tier1_question_equivalents')}`, "
            f"cycle_budget_skips=`{opp.get('budget_skipped_questions_in_cycles')}`",
        ]
    return lines


def render_scoreboard_markdown(board: Dict[str, Any]) -> str:
    cd = board["conclusion_discipline"]
    off = board["official_scores"]
    rk_by_tour = off.get("official_rankings_by_tournament") or {}
    lines = [
        f"# Forecast Lab Scoreboard (`{board['version']}` / `{board['cdf_score_version']}`)",
        "",
    ]
    if board.get("include_dry_run"):
        lines += [
            "> **WARNING (DRY-RUN INCLUDED)**: This report was generated with `--include-dry-run` "
            "and may include local dry-run records (never superseding live submissions).",
            "",
        ]
    lines += [
        f"- **Resolution & Forecast Cutoff (`as_of_utc`)**: `{board['as_of_utc']}`",
        f"- **Analysis & Audit Cutoff (`analysis_cutoff_utc`)**: `{board.get('analysis_cutoff_utc')}`",
        f"- **Include dry-run (`include_dry_run`)**: `{bool(board.get('include_dry_run'))}`",
        f"- **Submitted questions**: `{board['submitted_questions_count']}` | "
        f"**Scorable resolved**: `{board['scorable_resolved_count']}` | "
        f"**Excluded**: `{json.dumps(board['excluded_counts'], sort_keys=True)}`",
        f"- **Cutoff Rejections**: `{json.dumps(board.get('rejected_cutoff_counts') or {}, sort_keys=True)}` | "
        f"**Submission Provenance Rejections**: `{json.dumps(board.get('rejected_submission_counts') or {}, sort_keys=True)}`",
        f"- **Per-Question Rejections**: `{json.dumps(board.get('question_rejections') or {}, sort_keys=True)}`",
        f"- **Conclusion discipline (PLAN §4.3)**: `{cd['tier']}` "
        f"(eligible_for_superiority_test=`{cd['superiority_assessment_eligible']}`) — {cd['rule']}",
        f"- **Official Spot Peer Score**: count=`{off['spot_peer_score_count']}`, "
        f"sum=`{off['spot_peer_score_sum'] if off['spot_peer_score_sum'] is not None else '—'}`, "
        f"mean=`{off['spot_peer_score_mean'] if off['spot_peer_score_mean'] is not None else '—'}` | "
        f"**Official Ranking (verified own entry, per tournament)**: "
        f"`{json.dumps(rk_by_tour, sort_keys=True) if rk_by_tour else '—'}` | "
        f"unverified observations ignored=`{off.get('unverified_ranking_observations', 0)}`",
        "- **Local metric note**: numeric/discrete rows put CRPS/RPS (cdf_score_v1) in the `brier_score` "
        "column; it is not an ordinary Brier score, and log scores of different question types are not "
        "on one scale.",
        "",
        "## 1. Historical 4-Way Series & Pairwise Summary (D127)",
        "",
        *_render_subset_tables(board["overall"]),
    ]

    if board.get("by_config_segment"):
        lines += ["", "## 2. Segmented by Configuration Version (PLAN §4.3)"]
        for seg, seg_data in board["by_config_segment"].items():
            seg_cd = seg_data["conclusion_discipline"]
            seg_off = seg_data.get("official_scores") or {}
            lines += [
                "",
                f"### Segment `{seg}` (resolved=`{seg_data['resolved_count']}`, "
                f"escalated=`{seg_data['escalated_count']}`, tier=`{seg_cd['tier']}`, "
                f"spot_peer_mean=`{seg_off.get('spot_peer_score_mean') if seg_off.get('spot_peer_score_mean') is not None else '—'}`)",
                "",
                *_render_subset_tables(seg_data),
            ]

    live = board.get("live_itt")
    if live is not None:
        cohort = live["cohort"]
        window_closed = parse_iso(live["as_of_utc"]) >= parse_iso(cohort["end_utc"])
        lines += [
            "", "## 3. Registered Live ITT (D128)", "",
            f"- Cohort: `{cohort.get('cohort_id')}` / `{cohort.get('version')}`; "
            f"source SHA: `{cohort.get('source_sha256')}`.",
            f"- Report version: `{live['version']}`; cohort as-of: `{cohort.get('as_of_utc')}`; "
            f"analysis cutoff: `{board.get('analysis_cutoff_utc')}`.",
            f"- Decision interval: [`{cohort['start_utc']}`, `{cohort['end_utc']}`); "
            f"window status: {'closed for new episodes' if window_closed else 'interim (window still open)'}.",
            "- Observed-pair subset (descriptive only; NOT full ITT). "
            f"Paired / included questions: {live['paired_question_count']}/{live['included_question_count']}.",
            "- Historical tables above remain D127 all-history diagnostics; they do not determine live cohort membership.",
            f"- Included episodes/questions: {live['included_episode_count']}/{live['included_question_count']}; "
            f"historical/out-of-scope: {live['excluded_episode_count']}; unknown: {live['unknown_episode_count']}.",
            f"- Submitted: {live['submitted_episode_count']}; unsubmitted: {live['unsubmitted_episode_count']}; "
            f"paired questions: {live['paired_question_count']}; complete ITT: `{live['complete_itt_coverage']}`.",
            f"- Gaps (may overlap): `{json.dumps(live['itt_gap_reasons'], sort_keys=True)}`.",
            f"- Decision capture complete: `{cohort.get('decision_capture_complete', False)}`; "
            f"limitations: `{json.dumps(cohort.get('capture_limitations', []))}`.",
            f"- Resources: `{json.dumps(live['resources'], sort_keys=True)}`.",
            "- Effects apply only to registered escalated questions. Missing outcomes are never imputed as zero; "
            "configurations and question types are not pooled. Every unknown/missing episode blocks full-ITT claims.",
        ]
        for group in live["by_config_and_type"]:
            lines += ["", f"### Observed-pair subset: `{group['config_segment']}` / `{group['question_type']}` (descriptive only)", "",
                      f"Paired questions / included episodes in segment: {group['paired_question_count']}/{group['episode_count']}.", "",
                      "| Metric difference (final − tier 1) | Mean / 95% CI question / cluster | Claim |",
                      "|---|---|---|"]
            for metric, result in group["pairwise"].items():
                lines.append(f"| {metric} | {_fmt_metric(result)} | "
                             f"{result['conclusion_discipline']['superiority_claim']} |")
            lines += [f"- Observed-pair subset log gain (descriptive): `{group['evaluatable_subset_log_score_gain_nats']}`.",
                      f"- Full ITT log gain: `{group['total_log_score_gain_nats']}`; "
                      f"per Tier 2 attempt: `{group['log_score_gain_per_tier2_attempt']}`; "
                      f"per budget unit: `{group['log_score_gain_per_budget_unit']}`."]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="score", description=f"{VERSION}: compute local and official forecast scores")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--as-of", default=None, help="ISO-8601 UTC cutoff for resolutions/forecasts")
    ap.add_argument("--analysis-at", default=None, help="ISO-8601 UTC cutoff for analysis and retrospective audits")
    ap.add_argument("--include-dry-run", action="store_true")
    ap.add_argument("--bootstrap-iterations", type=int, default=BOOTSTRAP_ITERATIONS)
    ap.add_argument("--format", choices=("json", "markdown"), default="json")
    ap.add_argument("--write-report", action="store_true", help="write markdown report to a new file under reports/")
    ap.add_argument("--report-path", default=None, help="explicit new report .md path (never overwrites existing files)")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    explicit_report_path = Path(args.report_path) if args.report_path else None
    if explicit_report_path is not None and not args.write_report:
        raise ValueError("--report-path requires --write-report")
    if args.write_report and explicit_report_path is not None:
        validate_report_path(explicit_report_path, data_dir=data_dir, reject_existing=True)

    board = evaluate_scoreboard(
        data_dir,
        as_of=args.as_of,
        analysis_at=args.analysis_at,
        include_dry_run=args.include_dry_run,
        n_boot=args.bootstrap_iterations,
    )
    md = render_scoreboard_markdown(board)
    if args.write_report:
        out_path = write_report_exclusive(explicit_report_path, board, md, data_dir=data_dir)
        board["report_path"] = str(out_path)

    if args.format == "markdown":
        sys.stdout.write(md)
    else:
        sys.stdout.write(json.dumps(board, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
