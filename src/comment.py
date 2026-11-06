"""Deterministic English local comments from the actual adopted inputs (PLAN §5.6).

No model calls and no submission API. Dynamic text is rendered as escaped plain text;
optional sections are removed before shortening individual mandatory explanations.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import aggregate as A
import audit_ledger as L
import numeric as N
from flab.config import PLAYERS, load_agg_config
from flab.ids import new_id
from flab.timeutil import to_iso, utc_now

VERSION = "comment_v1"
MAX_CHARS = 3000
LABELS = {"claude": "Claude", "gemini": "Gemini", "gpt": "GPT"}


class CommentError(ValueError):
    pass


class _Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "object"}:
            self.hidden += 1
        elif not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "object"} and self.hidden:
            self.hidden -= 1
        elif not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def safe_url(value: Any) -> str | None:
    if not isinstance(value, str) or any(c.isspace() or unicodedata.category(c).startswith("C") for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return None
        parsed.port  # reject malformed ports
    except ValueError:
        return None
    return value


def plain(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = unescape(value)
    value = re.sub(r"!\[[^\]]*\]\([^\n]*?\)", "", value)
    value = re.sub(r"\[([^\]]*)\]\(([^\s)]*)\)",
                   lambda m: m[1] + (" " + m[2] if safe_url(m[2]) else ""), value)
    parser = _Plain()
    parser.feed(value)
    value = "".join(parser.parts)
    value = re.sub(r"(?i)\b((?:javascript|vbscript|data|file|mailto|tel|sms|blob|about|ftp)|[a-z][a-z0-9+.-]*(?=://)):[^\s<>]+",
                   lambda m: m[0] if m[1].lower() in ("http", "https") else "", value)
    value = "".join(c for c in value if not unicodedata.category(c).startswith("C") or c in "\n\t")
    return " ".join(value.split())


def escape(value: str) -> str:
    # Angle brackets are escaped too: no HTML or Markdown autolink can survive rendering.
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~<\-])", r"\\\1", value)


def _excerpt(value: str, limit: int | None) -> str:
    if limit is None or len(value) <= limit:
        return value
    sentences = re.split(r"(?<=[.!?])\s+", value)
    retained = ""
    for sentence in sentences:
        candidate = (retained + " " + sentence).strip()
        if len(candidate) + 2 > limit:
            break
        retained = candidate
    if not retained:
        for word in value.split():
            candidate = (retained + " " + word).strip()
            if len(candidate) + 2 > limit:
                break
            retained = candidate
    if not retained:
        raise CommentError("a mandatory explanation cannot fit without cutting a word")
    return retained + " …"


def _probability(row: dict, snapshot: dict) -> str:
    if snapshot["question_type"] in N.TYPES:
        cdf = row["continuous_cdf"]
        mid, lo, hi = (N.quantile_label(cdf, p, snapshot) for p in (.5, .1, .9))
        return f"Median {mid}; 10-90% interval {lo} to {hi} {plain(N.scaling(snapshot).unit)}".rstrip()
    if snapshot["question_type"] == "binary":
        return f"Yes {row['probability']:.2%}"
    return "; ".join(f"{plain(option)} {row['option_probabilities'][option]:.2%}" for option in snapshot["options"])


def build(snapshot: dict, aggregate: dict, records: list[dict], audit_events: list[dict], *, now=None) -> dict:
    graph = L.ImpactGraph(records)
    view = graph.attempt_audit_view(audit_events)
    attempts = [r for r in records if r.get("type") == "forecast_attempt"]
    stage = aggregate.get("stage")
    if stage not in ("tier1", "final"):
        raise CommentError("unknown aggregate stage")
    selected, _ = A.select_inputs(snapshot, attempts, stage=stage, audit_events=view)
    if not selected or [r["attempt_id"] for r in selected] != aggregate.get("input_attempt_ids"):
        raise CommentError("adopted inputs no longer match this aggregate")
    for field, expected in {"question_id": snapshot["question_id"], "question_snapshot_id": snapshot["snapshot_id"],
                            "post_id": snapshot.get("post_id"), "tournament_id": snapshot.get("tournament_id")}.items():
        if aggregate.get(field) != expected:
            raise CommentError(f"aggregate {field} mismatch")
    config = load_agg_config()
    numeric = snapshot["question_type"] in N.TYPES
    version = N.AGG_VERSION if numeric else config["version"]
    if aggregate.get("aggregation_version") != version:
        raise CommentError("unsupported aggregation version")
    combined = A.combine(snapshot, selected, config)
    for field in (("probability", "option_probabilities", "continuous_cdf", "cdf_version", "cdf_sha256")
                  if numeric else ("probability", "option_probabilities")):
        if aggregate.get(field) != combined[field]:
            raise CommentError("aggregate probabilities do not match adopted inputs")
    missing = [LABELS[p] for p in PLAYERS if p not in {r["player"] for r in selected}]
    method = ("Equal-weight mean of log odds; final probability clipped to 2%-98%."
              if snapshot["question_type"] == "binary" else
              "Equal-weight geometric mean; normalized, then 2% uniform smoothing.")
    if numeric:
        method = "Equal-weight arithmetic mean of CDFs; fixed-mass bounded-step projection."
    header = ["Final forecast: " + _probability(aggregate, snapshot), f"Aggregation: {version}. " + method,
              "Missing lines: " + (", ".join(missing) if missing else "none") + "."]
    if snapshot.get('post_type') == 'group_of_questions':
        header.insert(0, f"Question {snapshot['question_id']}: {plain(snapshot['title'])}")
    explanations, details, markets, sources = [], [], [], []
    for r in selected:
        label = LABELS[r["player"]]
        reason, change = plain(r.get("reasoning_summary")), plain(r.get("change_reason"))
        if not reason or (r["tier"] == 2 and not change):
            raise CommentError("adopted answer is missing its required explanation")
        explanations.append((f"{label} (tier {r['tier']}): " + _probability(r, snapshot), reason,
                             change if r["tier"] == 2 else None))
        if factors := [plain(f) for f in r.get("key_factors") or [] if plain(f)]:
            details.append(f"{label} factors: " + "; ".join(factors))
        if base := plain(r.get("base_rate")):
            details.append(f"{label} base rate: " + base)
        for m in r.get("market_refs") or []:
            url = safe_url(m.get("url"))
            markets.append(f"{label} market: {plain(m.get('platform'))}; {plain(m.get('market_title'))}; "
                           f"match={plain(m.get('match'))}; difference={plain(m.get('difference_note')) or 'not stated'}."
                           + (" " + url if url else ""))
        for src in r.get("sources") or []:
            if url := safe_url(src.get("url")):
                entry = "Source: " + plain(src.get("title")) + " " + url
                if entry not in sources:
                    sources.append(entry)
    omitted = {"sources": 0, "markets": 0, "details": 0}

    def render(limit=None):
        mandatory = list(header)
        for heading, reason, change in explanations:
            mandatory += [heading, "Reason: " + _excerpt(reason, limit)]
            if change is not None:
                mandatory.append("Tier-2 judgement: " + _excerpt(change, limit))
        return "\n".join(escape(line) for line in [*mandatory, *details, *markets, *sources])

    text = render()
    # Fixed reduction order; no blind tail slicing of the rendered comment.
    for name, group in (("sources", sources), ("markets", markets), ("details", details)):
        while group and len(text) > MAX_CHARS:
            group.pop()
            omitted[name] += 1
            text = render()
    excerpt_limit = None
    for limit in (480, 320, 200, 120):
        if len(text) <= MAX_CHARS:
            break
        text, excerpt_limit = render(limit), limit
    if len(text) > MAX_CHARS:
        raise CommentError("mandatory probability and explanation sections exceed 3000 characters")
    return {"type": "comment", "record_id": new_id("comment"), "version": VERSION, "mode": "local",
            "created_at_utc": to_iso(now or utc_now()), "question_id": snapshot["question_id"],
            "question_snapshot_id": snapshot["snapshot_id"], "aggregate_id": aggregate["record_id"],
            "input_attempt_ids": aggregate["input_attempt_ids"], "text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "omitted_sections": omitted,
            "explanation_excerpt_limit": excerpt_limit, "submission_eligible": False}
