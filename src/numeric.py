"""M5 numeric_cdf_v2: pure scaling, percentile conversion and bounded CDF projection.

API layout follows M5 specification section 14's recorded GET field contract.
Missing or conflicting fields fail closed; no endpoint is inferred for discrete.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass
from itertools import combinations

from flab.ids import hash_obj

TYPES = ("numeric", "discrete")
CDF_VERSION = "numeric_cdf_v2"
AGG_VERSION = "agg_numeric_v1"
SCHEMA_VERSION = "output_schema_numeric_v1"
PROMPT_VERSION = "forecast_numeric_v1"
RECONCILE_VERSION = "reconcile_numeric_v1"
ESCALATION_VERSION = "escalation_numeric_v1"
KEYS = ("p05", "p10", "p25", "p50", "p75", "p90", "p95")
LEVELS = (.05, .10, .25, .50, .75, .90, .95)
SCALING_FIELDS = ("range_min", "range_max", "zero_point", "open_lower_bound",
                  "open_upper_bound", "inbound_outcome_count", "unit",
                  "nominal_min", "nominal_max", "continuous_range")
REQUIRED_FIELDS = (*SCALING_FIELDS[:7], "continuous_range")
MIRRORED_FIELDS = ("open_lower_bound", "open_upper_bound", "inbound_outcome_count")
GRID_REL_TOLERANCE, GRID_ABS_TOLERANCE = 1e-12, 1e-10
MIN_N, MAX_N, DEFAULT_N = 2, 1000, 200
MIN_GAP_U = 1e-8
CLOSED_TOLERANCE_U = .02
OPEN_MIN_U, OPEN_MAX_U = -1., 2.
MIN_STEP_FACTOR, MAX_STEP_FACTOR = 1.1, .95
MASS_MARGIN = 1e-8
READBACK_TOLERANCE = 5e-10
ESCALATION_FLOOR_U, IQR_MULTIPLIER = .02, 1.


class UnsupportedScaling(ValueError):
    pass


class InvalidCDF(ValueError):
    """Conversion failed the final server-compatible validator; never repair it."""


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        # JSON integers are unbounded; reject values outside float arithmetic
        # through the normal semantic/scaling validation path.
        return False


def read_scaling_fields(question):
    """Read scaling exclusively from its API object, except top-level unit.

    Only the three mirrored fields can conflict, and top-level nulls are absent
    mirrors. Top-level range/nominal/grid fields and nested unit are ignored.
    """
    nested = question.get("scaling")
    if not isinstance(nested, dict):
        raise UnsupportedScaling("scaling is not an object")
    result = {}
    for key in SCALING_FIELDS:
        if key in MIRRORED_FIELDS and key in nested and question.get(key) is not None and (
                type(nested[key]) is not type(question[key]) or nested[key] != question[key]):
            raise UnsupportedScaling(f"conflicting scaling field: {key}")
        source = question if key == "unit" else nested
        if key in source:
            result[key] = deepcopy(source[key])
    return result


@dataclass(frozen=True)
class Scaling:
    lower: float
    upper: float
    zero: float | None
    n: int
    open_lower: bool
    open_upper: bool
    unit: str
    log_ratio: float | None

    def to_u(self, x):
        if not finite(x):
            raise ValueError("percentile is not finite")
        if self.log_ratio is None:
            return (x - self.lower) / (self.upper - self.lower)
        ratio = (x - self.zero) / (self.lower - self.zero)
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError("percentile outside logarithmic domain")
        return math.log(ratio) / self.log_ratio

    def from_u(self, u):
        if self.log_ratio is None:
            return self.lower + (self.upper - self.lower) * u
        return self.lower + (self.lower - self.zero) * math.expm1(self.log_ratio * u)


def scaling(snapshot):
    if snapshot.get("scaling_error"):
        raise UnsupportedScaling(snapshot["scaling_error"])
    missing = [k for k in REQUIRED_FIELDS if k not in snapshot]
    if missing:
        raise UnsupportedScaling("missing scaling fields: " + ", ".join(missing))
    lo, hi, zero = (snapshot[k] for k in ("range_min", "range_max", "zero_point"))
    if not finite(lo) or not finite(hi) or lo >= hi or not finite(hi - lo):
        raise UnsupportedScaling("invalid range")
    n = snapshot["inbound_outcome_count"]
    n = DEFAULT_N if n is None else n
    if type(n) is not int or not MIN_N <= n <= MAX_N:
        raise UnsupportedScaling("invalid inbound_outcome_count")
    bounds = [snapshot[k] for k in ("open_lower_bound", "open_upper_bound")]
    if any(v is not None and type(v) is not bool for v in bounds):
        raise UnsupportedScaling("invalid bound flags")
    unit = snapshot["unit"]
    if unit is not None and not isinstance(unit, str):
        raise UnsupportedScaling("invalid unit")
    log_r = None
    if zero is not None:
        if not finite(zero) or zero == lo:
            raise UnsupportedScaling("invalid zero_point")
        r = (hi - zero) / (lo - zero)
        if not math.isfinite(r) or r <= 0 or r == 1:
            raise UnsupportedScaling("invalid logarithmic ratio")
        log_r = math.log(r)
    if snapshot.get("question_type") == "discrete":
        a, b = (snapshot.get(k) for k in ("nominal_min", "nominal_max"))
        if not finite(a) or not finite(b) or a >= b or not finite(b - a) or zero is not None:
            raise UnsupportedScaling("discrete requires linear nominal endpoints")
        step = (b - a) / (n - 1)
        # A large coordinate offset must not widen the tolerance by whole bins.
        if step <= 0 or not math.isclose(lo, a - step / 2, rel_tol=0, abs_tol=step * 1e-8) or \
                not math.isclose(hi, b + step / 2, rel_tol=0, abs_tol=step * 1e-8):
            raise UnsupportedScaling("unconfirmed discrete half-bin geometry")
    result = Scaling(lo, hi, zero, n, bool(bounds[0]), bool(bounds[1]), unit or "", log_r)
    grid = snapshot["continuous_range"]
    if not isinstance(grid, list) or len(grid) != n + 1 or any(not finite(x) for x in grid):
        raise UnsupportedScaling("invalid continuous_range")
    try:
        expected = [result.from_u(i / n) for i in range(n + 1)]
    except (OverflowError, ValueError) as exc:
        raise UnsupportedScaling("unrepresentable continuous_range") from exc
    if any(not finite(y) or not math.isclose(x, y, rel_tol=GRID_REL_TOLERANCE,
                                             abs_tol=GRID_ABS_TOLERANCE)
           for x, y in zip(grid, expected)):
        raise UnsupportedScaling("continuous_range does not match scaling")
    return result


def scaling_changed(old, new):
    return (old.get("question_type") in TYPES or new.get("question_type") in TYPES) and any(
        (k in old, old.get(k)) != (k in new, new.get(k))
        for k in (*SCALING_FIELDS, "scaling_error", "scaling_conflicts"))


def percentiles(values, snapshot):
    s = scaling(snapshot)
    if not isinstance(values, dict) or set(values) != set(KEYS):
        raise ValueError("percentiles must contain exactly seven required keys")
    us = [s.to_u(values[k]) for k in KEYS]
    if any(not math.isfinite(u) for u in us):
        raise ValueError("percentile coordinate is not finite")
    if any(b - a < MIN_GAP_U for a, b in zip(us, us[1:])):
        raise ValueError("percentiles must be strictly increasing and separated")
    lo = OPEN_MIN_U if s.open_lower else -CLOSED_TOLERANCE_U
    hi = OPEN_MAX_U if s.open_upper else 1 + CLOSED_TOLERANCE_U
    if any(not lo <= u <= hi for u in us):
        raise ValueError("percentile exceeds permitted tail or closed-bound tolerance")
    original_us = us
    us = [max(MIN_GAP_U, u) if not s.open_lower else u for u in us]
    us = [min(1 - MIN_GAP_U, u) if not s.open_upper else u for u in us]
    if any(b - a < MIN_GAP_U for a, b in zip(us, us[1:])):
        raise ValueError("clamping collapses percentiles")
    normalized = {k: float(values[k]) if u == original else s.from_u(u)
                  for k, u, original in zip(KEYS, us, original_us)}
    # Large offsets can round a clamped value back onto a closed endpoint, or
    # collapse distinct JSON integers when storing floats. Reject that loss of
    # separation instead of silently dropping a percentile during interpolation.
    actual = [s.to_u(normalized[k]) for k in KEYS]
    tolerance = 1e-15  # round-trip arithmetic only, far below MIN_GAP_U
    if (not s.open_lower and actual[0] < MIN_GAP_U - tolerance) or \
            (not s.open_upper and actual[-1] > 1 - MIN_GAP_U + tolerance) or \
            any(b - a < MIN_GAP_U - tolerance for a, b in zip(actual, actual[1:])):
        raise ValueError("percentile separation cannot be represented at this scale")
    return normalized


def validate_cdf(cdf, snapshot):
    """Mirror server round(10), then round(delta, 9), plus local finite/range checks."""
    s = scaling(snapshot)
    if not isinstance(cdf, list) or len(cdf) != s.n + 1:
        raise InvalidCDF("invalid CDF length")
    if any(not finite(v) or not 0 <= v <= 1 for v in cdf):
        raise InvalidCDF("invalid CDF value")
    rounded = [round(v, 10) for v in cdf]
    if (rounded[0] < .001 if s.open_lower else rounded[0] != 0) or \
            (rounded[-1] > .999 if s.open_upper else rounded[-1] != 1):
        raise InvalidCDF("invalid CDF endpoints")
    minimum, maximum = round(.01 / s.n, 9), .2 * 200 / s.n
    for a, b in zip(rounded, rounded[1:]):
        if b <= a or not minimum <= round(b - a, 9) <= maximum:
            raise InvalidCDF("invalid CDF step")
    return rounded


def project_cdf(cdf, snapshot):
    """Euclidean projection of steps onto a fixed-mass bounded simplex; idempotent."""
    s = scaling(snapshot)
    if len(cdf) != s.n + 1 or any(not finite(v) for v in cdf):
        raise ValueError("invalid projection input")
    low, high = MIN_STEP_FACTOR * .01 / s.n, MAX_STEP_FACTOR * .2 * 200 / s.n
    left = min(.999, max(.001, cdf[0])) if s.open_lower else 0.
    right = min(.999, max(.001, cdf[-1])) if s.open_upper else 1.
    minimum_mass = s.n * low + MASS_MARGIN
    if right - left < minimum_mass:
        if s.open_lower and s.open_upper:
            left = min(.999 - minimum_mass, max(.001, (left + right - minimum_mass) / 2))
            right = left + minimum_mass
        elif s.open_lower:
            left = right - minimum_mass
        else:
            right = left + minimum_mass
    mass = right - left
    steps = [b - a for a, b in zip(cdf, cdf[1:])]
    # Roundoff after the first projection is not a reason to smooth a second time.
    if left == cdf[0] and right == cdf[-1] and all(low - 1e-10 <= d <= high + 1e-10 for d in steps):
        return validate_cdf(cdf, snapshot)
    lower, upper = min(steps) - high, max(steps) - low
    for _ in range(90):
        offset = (lower + upper) / 2
        total = math.fsum(min(high, max(low, d - offset)) for d in steps)
        if total > mass:
            lower = offset
        else:
            upper = offset
    bounded = [min(high, max(low, d - (lower + upper) / 2)) for d in steps]
    out = [round(left, 10)]
    for i in range(1, s.n):
        out.append(round(left + math.fsum(bounded[:i]), 10))
    out.append(round(right, 10))  # repair accumulated last-place error at fixed endpoint
    return validate_cdf(out, snapshot)


def interpolate(x, xs, ys):
    i = max(0, min(len(xs) - 2, bisect_right(xs, x) - 1))
    return ys[i] + (ys[i + 1] - ys[i]) * (x - xs[i]) / (xs[i + 1] - xs[i])


def to_cdf(values, snapshot):
    s = scaling(snapshot)
    values = percentiles(values, snapshot)
    us = [s.to_u(values[k]) for k in KEYS]
    left = (min(.025, max(.001, interpolate(0, us[:2], LEVELS[:2]))) if us[0] > 0
            else interpolate(0, us, LEVELS)) if s.open_lower else 0.
    right = (min(.999, max(.975, interpolate(1, us[-2:], LEVELS[-2:]))) if us[-1] < 1
             else interpolate(1, us, LEVELS)) if s.open_upper else 1.
    left, right = max(0., min(1., left)), max(0., min(1., right))
    # If all quantiles lie beyond the same open boundary, the opposite tail's
    # capped extrapolation must not reverse the boundary CDF values.
    if us[-1] <= 0:
        right = max(left, right)
    if us[0] >= 1:
        left = min(left, right)
    points = [(0., left),
              *[(u, p) for u, p in zip(us, LEVELS) if 0 < u < 1],
              (1., right)]
    xs, ys = zip(*points)
    return project_cdf([interpolate(i / s.n, xs, ys) for i in range(s.n + 1)], snapshot)


def cdf_fields(values, snapshot):
    cdf = to_cdf(values, snapshot)
    return {"percentiles": percentiles(values, snapshot), "continuous_cdf": cdf,
            "cdf_version": CDF_VERSION, "cdf_sha256": hash_obj(cdf)}


def quantile_label(cdf, p, snapshot):
    s = scaling(snapshot)
    cdf = validate_cdf(cdf, snapshot)
    if p < cdf[0]:
        return "below lower bound"
    if p > cdf[-1]:
        return "above upper bound"
    u = interpolate(p, cdf, [i / s.n for i in range(s.n + 1)])
    return f"{s.from_u(u):.4g}"


def prompt_scaling(snapshot):
    s = scaling(snapshot)
    if snapshot["question_type"] == "discrete":
        a, b = snapshot["nominal_min"], snapshot["nominal_max"]
        step = (b - a) / (s.n - 1)
        bounds = f"Nominal outcomes: {a:g} to {b:g}; step {step:g}; possible values: {a:g} + k * {step:g}, k=0,...,{s.n - 1}."
    else:
        bounds = f"Range: {s.lower:g} to {s.upper:g}."
    return (f"{bounds} Unit: {s.unit or '(unspecified)'}.\n"
            f"Lower bound: {'open; outcomes below it remain possible' if s.open_lower else 'closed; outcomes below it are impossible'}.\n"
            f"Upper bound: {'open; outcomes above it remain possible' if s.open_upper else 'closed; outcomes above it are impossible'}.\n"
            + (f"Logarithmic scale with zero_point {s.zero:g}; report actual values, not logs."
               if s.zero is not None else "Linear scale; report actual values."))


def escalation_triggers(selected, snapshot):
    s = scaling(snapshot)
    triggers = []
    for a, b in combinations(selected, 2):
        aa, bb = ([s.to_u(r["percentiles"][k]) for k in ("p25", "p50", "p75")] for r in (a, b))
        threshold = max(IQR_MULTIPLIER * ((aa[2] - aa[0]) + (bb[2] - bb[0])) / 2, ESCALATION_FLOOR_U)
        gap = max(aa[0] - bb[2], bb[0] - aa[2])
        ids = [a["attempt_id"], b["attempt_id"]]
        if abs(aa[1] - bb[1]) > threshold:
            triggers.append({"type": "model_disagreement", "strength": abs(aa[1] - bb[1]),
                             "threshold": threshold, "attempt_ids": ids})
        if gap >= ESCALATION_FLOOR_U:
            triggers.append({"type": "nonoverlapping_iqr", "strength": gap,
                             "threshold": ESCALATION_FLOOR_U, "attempt_ids": ids})
    return triggers
