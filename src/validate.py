"""Per-question output schema, JSON extraction and validation (PLAN §5.4, §7.5).

Status order follows §5.5: an output that is not a JSON object is ``parse_failed``; one whose
question ID, type, options or probabilities are wrong is ``semantic_failed`` (these are never
repaired, because a repair may not change numbers); one that only breaks the schema is
``schema_failed``. ``late`` and ``incomplete`` are decided by the caller.
"""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from flab import schema as S
from flab.ids import hash_obj
from flab.timeutil import parse_iso
from flab.workdir import PROJECT_ROOT

SCHEMA_VERSION = "output_schema_v2"
TEMPLATE_PATH = PROJECT_ROOT / "prompts" / "output_schema_v2.json"
PROB_MIN, PROB_MAX = 0.001, 0.999
SUM_TOLERANCE = 0.01

VALID, PARSE_FAILED, SCHEMA_FAILED, SEMANTIC_FAILED = "valid", "parse_failed", "schema_failed", "semantic_failed"
INCOMPLETE, LATE = "incomplete", "late"
REPAIRABLE = (PARSE_FAILED, SCHEMA_FAILED)
METADATA_KEYS = ("$schema", "$id", "title", "description")


def load_template(path: Path = TEMPLATE_PATH) -> dict[str, Any]:
    template = json.loads(path.read_text(encoding="utf-8"))
    S.check_schema(template)
    return template


def question_schema(snapshot: dict[str, Any], tier: int = 1, template: dict[str, Any] | None = None) -> dict[str, Any]:
    """The template with this question's ID, type and options fixed (every key required, none extra)."""
    qtype = snapshot["question_type"]
    if qtype not in ("binary", "multiple_choice"):
        raise ValueError(f"unsupported question type {qtype!r}")
    sch = copy.deepcopy(template or load_template())
    # Metadata stays in the template file only: claude 2.1.316 rejects a schema whose $schema names
    # draft 2020-12 ("no schema with key or ref"), and the CLIs need nothing but the constraints.
    for key in METADATA_KEYS:
        sch.pop(key, None)
    props = sch["properties"]
    props["question_id"] = {"type": "integer", "const": snapshot["question_id"]}
    props["question_type"] = {"type": "string", "const": qtype}
    market = props["market_refs"]["items"]["properties"]
    if qtype == "binary":
        props["probability"] = {"type": "number", "minimum": PROB_MIN, "maximum": PROB_MAX}
        props["option_probabilities"] = {"type": "null"}
        market["option_probabilities"] = {"type": "null"}
        market["complete"] = {"type": "null"}
    else:
        options = list(snapshot.get("options") or [])
        if len(options) < 2 or len(set(options)) != len(options):
            raise ValueError("a multiple-choice question needs at least two distinct options")
        props["probability"] = {"type": "null"}
        props["option_probabilities"] = {
            "type": "object", "additionalProperties": False, "required": options,
            "properties": {o: {"type": "number", "minimum": PROB_MIN, "maximum": PROB_MAX} for o in options}}
        market["probability"] = {"type": "null"}
        market["option_probabilities"] = {
            "type": "object", "additionalProperties": False, "required": options,
            "properties": {o: {"type": ["number", "null"], "minimum": 0, "maximum": 1} for o in options}}
        market["complete"] = {"type": "boolean"}
    if tier == 1:
        props["changed_from_tier1"] = {"type": "null"}
        props["change_reason"] = {"type": "null"}
    S.check_schema(sch)
    return sch


def schema_sha256(sch: dict[str, Any]) -> str:
    return hash_obj(sch)


# ---------------------------------------------------------------- extraction

def json_objects(text: str) -> list[tuple[dict[str, Any], int, int]]:
    """Every top-level JSON object in ``text`` with its span, fenced or not, each found once."""
    found: list[tuple[dict[str, Any], int, int]] = []
    decoder = json.JSONDecoder()
    i = 0
    while (start := text.find("{", i)) != -1:
        try:
            obj, end = decoder.raw_decode(text, start)
        except ValueError:
            i = start + 1
            continue
        if isinstance(obj, dict):
            found.append((obj, start, end))
        i = end
    return found


def extract_output(structured: Any, final_text: str | None) -> tuple[Any, str]:
    """(object, where it came from): the CLI's structured output, else the only JSON object in the text."""
    if isinstance(structured, dict):
        return structured, "structured_output"
    text = (final_text or "").strip()
    if not text:
        return None, "empty"
    try:
        whole = json.loads(text)
    except ValueError:
        whole = None
    if isinstance(whole, dict):
        return whole, "final_text"
    objs = json_objects(text)
    if len(objs) == 1:
        return objs[0][0], "final_text_embedded"
    return None, "no_object" if not objs else "several_objects"


# ---------------------------------------------------------------- validation

@dataclass
class Outcome:
    status: str
    errors: list[str] = field(default_factory=list)
    probability: float | None = None
    option_probabilities: dict[str, float] | None = None       # normalised
    option_probabilities_raw: dict[str, float] | None = None


def _number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(v: Any) -> bool:
    return _number(v) and math.isfinite(v)


def _bad_probability(v: Any) -> bool:
    """A number that is not finite or outside the range; a non-number is a schema error instead."""
    return _number(v) and not (math.isfinite(v) and PROB_MIN <= v <= PROB_MAX)


def semantic_errors(obj: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    errs = []
    qtype = snapshot["question_type"]
    # A missing field is a schema error (repairable); a present but different one is semantic.
    if "question_id" in obj and obj["question_id"] != snapshot["question_id"]:
        errs.append(f"question_id {obj['question_id']!r} != {snapshot['question_id']}")
    if "question_type" in obj and obj["question_type"] != qtype:
        errs.append(f"question_type {obj['question_type']!r} != {qtype!r}")
    p, ops = obj.get("probability"), obj.get("option_probabilities")
    if qtype == "binary":
        if _bad_probability(p):
            errs.append(f"probability {p!r} is not a finite number in [{PROB_MIN}, {PROB_MAX}]")
        if ops is not None:
            errs.append("option_probabilities must be null for a binary question")
    else:
        if p is not None:
            errs.append("probability must be null for a multiple-choice question")
        if isinstance(ops, dict):
            options = list(snapshot.get("options") or [])
            if set(ops) != set(options):
                errs.append(f"option keys {sorted(ops)} != {sorted(options)}")
            bad = [k for k, v in ops.items() if _bad_probability(v)]
            if bad:
                errs.append(f"option probabilities out of range or not finite: {bad}")
            elif set(ops) == set(options) and all(_finite(v) for v in ops.values()) \
                    and abs(sum(ops.values()) - 1) > SUM_TOLERANCE:
                errs.append(f"option probabilities sum to {sum(ops.values()):.4f}")
    return errs


def validate_output(obj: Any, snapshot: dict[str, Any], sch: dict[str, Any]) -> Outcome:
    if not isinstance(obj, dict):
        return Outcome(PARSE_FAILED, ["no JSON object"])
    sem = semantic_errors(obj, snapshot)
    if sem:
        return Outcome(SEMANTIC_FAILED, sem)
    errs = S.validate(obj, sch)
    if errs:
        return Outcome(SCHEMA_FAILED, errs)
    if snapshot["question_type"] == "binary":
        return Outcome(VALID, probability=float(obj["probability"]))
    raw = {k: float(v) for k, v in obj["option_probabilities"].items()}
    total = sum(raw.values())
    return Outcome(VALID, option_probabilities={k: v / total for k, v in raw.items()}, option_probabilities_raw=raw)


def submit_deadline(snapshot: dict[str, Any], deadlines: dict[str, Any]) -> datetime | None:
    """Close time minus the safety margin (§3.5); None when the snapshot has no close time."""
    close = snapshot.get("scheduled_close_time")
    if not close:
        return None
    return parse_iso(close) - timedelta(minutes=deadlines["safety_margin_minutes"])


# ---------------------------------------------------------------- format repair (§7.5)

_PCT = re.compile(r"^(\d{1,2}(?:\.\d+)?)\s*%$")
_TEXT_PROB = re.compile(
    r"\bprobability\b[^.\n:=]{0,40}?(?:\bis\b|:|=)\s*(0?\.\d+|1\.0+|\d{1,2}(?:\.\d+)?\s*%)", re.I)
_URL = re.compile(r"https?://[^\s\"'<>()\[\]\\]+")


def _as_fraction(token: str) -> float | None:
    token = token.strip()
    m = _PCT.match(token)
    try:
        v = float(m.group(1)) / 100 if m else float(token)
    except ValueError:
        return None
    return v if math.isfinite(v) and 0 <= v <= 1 else None


def urls_in(text: str) -> set[str]:
    """Complete URLs in ``text``; trailing sentence punctuation is not part of a URL."""
    return {u.rstrip(".,;:!?") for u in _URL.findall(text)}


def source_urls(obj: Any) -> set[str]:
    """Source declarations in every JSON candidate, including a nested wrapper or malformed list."""
    urls: set[str] = set()
    if isinstance(obj, dict):
        sources = obj.get("sources")
        for src in sources if isinstance(sources, list) else [sources]:
            value = src.get("url") if isinstance(src, dict) else src
            if isinstance(value, str):
                urls.update(urls_in(value))
        for value in obj.values():
            urls.update(source_urls(value))
    elif isinstance(obj, list):
        for value in obj:
            urls.update(source_urls(value))
    return urls


def _estimate_values(obj: Any) -> list[Any]:
    """Every nested dictionary/string, including lists of lists; exclude the market_refs subtree."""
    if isinstance(obj, dict):
        return [obj, *(nested for key, value in obj.items() if key != "market_refs"
                       for nested in _estimate_values(value))]
    if isinstance(obj, list):
        return [nested for value in obj for nested in _estimate_values(value)]
    return [obj] if isinstance(obj, str) else []


def _candidate(v: Any) -> tuple[bool, float | None]:
    """(usable, value) for a probability field: a number, a numeric or percent string, or null."""
    if v is None:
        return True, None
    if _finite(v):
        return True, float(v)
    if isinstance(v, str) and (f := _as_fraction(v)) is not None:
        return True, f
    return False, None


def _market_basis(objs: list[tuple[dict[str, Any], int, int]]) -> dict[str, Any] | None:
    """URL -> the market's probability and option probabilities in the raw output's JSON, or None
    when the same URL carries different values (then no market can be verified)."""
    markets: dict[str, Any] = {}
    for o, _, _ in objs:
        for m in o.get("market_refs") or [] if isinstance(o.get("market_refs"), list) else []:
            if not isinstance(m, dict) or not isinstance(m.get("url"), str):
                continue
            value = {"probability": m.get("probability"), "option_probabilities": m.get("option_probabilities")}
            if markets.setdefault(m["url"], value) != value:
                return None
    return markets


def repair_basis(raw_text: str, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """The unique set of probabilities in a failed output, or None (then the attempt is invalid_output).

    Every candidate counts: the top-level ``probability`` (numbers and numeric or percent strings) or
    ``option_probabilities`` of every JSON object in the text, and, for a binary question, every
    "probability is/: X" statement outside those objects. A repair needs them all to agree; several
    different estimates, or one that cannot be read, are never chosen between. The markets found in
    the raw output's JSON are kept so that a repair can be checked market by market.
    """
    qtype = snapshot["question_type"]
    objs = json_objects(raw_text)
    markets = _market_basis(objs)
    if markets is None:
        return None  # conflicting market estimates cannot be removed to make a repair pass
    if qtype == "binary":
        values: set[float] = set()
        for o in (nested for root, _, _ in objs for nested in _estimate_values(root)):
            if isinstance(o, str):
                values.update(round(v, 9) for t in _TEXT_PROB.findall(o) if (v := _as_fraction(t)) is not None)
                continue
            if "probability" in o:
                usable, v = _candidate(o["probability"])
                if not usable:
                    return None
                if v is not None:
                    values.add(round(v, 9))
        outside, last = [], 0
        for _, start, end in objs:
            outside.append(raw_text[last:start])
            last = end
        outside.append(raw_text[last:])
        values |= {round(v, 9) for t in _TEXT_PROB.findall("".join(outside)) if (v := _as_fraction(t)) is not None}
        return {"probability": values.pop(), "markets": markets} if len(values) == 1 else None
    options = set(snapshot.get("options") or [])
    sets = set()
    for o in (nested for root, _, _ in objs for nested in _estimate_values(root) if isinstance(nested, dict)):
        ops = o.get("option_probabilities")
        if ops is None:
            continue
        if not isinstance(ops, dict) or set(ops) != options or not all(_finite(v) for v in ops.values()):
            return None  # an incomplete or malformed estimate is still a competing one
        sets.add(json.dumps({k: float(v) for k, v in sorted(ops.items())}))
    return {"option_probabilities": json.loads(sets.pop()), "markets": markets} if len(sets) == 1 else None


def repair_errors(repaired: dict[str, Any], basis: dict[str, Any], raw_text: str) -> list[str]:
    """Ways a repaired output departs from the original: changed probabilities, source URLs the raw
    output does not contain (whole URLs, not substrings), or markets that differ from the raw
    output's JSON market by market. A market that cannot be matched that way is rejected."""
    errs = []
    if "probability" in basis:
        p = repaired.get("probability")
        if not (_finite(p) and abs(p - basis["probability"]) < 1e-9):
            errs.append(f"repair changed the probability ({basis['probability']} -> {p!r})")
    else:
        ops = repaired.get("option_probabilities") or {}
        changed = [k for k, v in basis["option_probabilities"].items()
                   if not (_finite(ops.get(k)) and abs(ops[k] - v) < 1e-9)]
        if changed:
            errs.append(f"repair changed option probabilities {changed}")
    raw_urls = urls_in(raw_text)
    for src in repaired.get("sources") or []:
        url = src.get("url") if isinstance(src, dict) else None
        if not isinstance(url, str) or url not in raw_urls:
            errs.append(f"repair added a source URL not in the raw output: {url!r}")
    original_sources = {url for obj, _, _ in json_objects(raw_text) for url in source_urls(obj)}
    missing = original_sources - source_urls(repaired)
    if missing:
        errs.append(f"repair removed original source URLs: {sorted(missing)}")
    markets = basis.get("markets")
    if isinstance(markets, dict):
        repaired_urls = {m.get("url") for m in repaired.get("market_refs") or [] if isinstance(m, dict)}
        if set(markets) - repaired_urls:
            errs.append(f"repair removed original markets: {sorted(set(markets) - repaired_urls)}")
    for m in repaired.get("market_refs") or []:
        url = m.get("url") if isinstance(m, dict) else None
        if markets is None or not isinstance(url, str) or url not in markets:
            errs.append(f"repair has a market that cannot be matched to the raw output: {url!r}")
            continue
        want = markets[url]
        got = {"probability": m.get("probability"), "option_probabilities": m.get("option_probabilities")}
        if not _same_values(got, want):
            errs.append(f"repair changed the market {url}: {want} -> {got}")
    return errs


def _same_values(a: Any, b: Any) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same_values(a[k], b[k]) for k in a)
    if _finite(a) and _finite(b):
        return abs(a - b) < 1e-9
    return S._equal(a, b)
