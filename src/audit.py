"""metaculus.com audit of one attempt (PLAN §3.3, D22, D48).

Flags, most severe first:
- ``leak_confirmed``: the model's ``sources`` contain metaculus.com, a metaculus.com URL was
  opened, or the adapter confirmed an open (codex);
- ``suspected_leak``: metaculus.com or "community prediction" appears in the raw output or tool
  events outside the prompt's own text and outside blocked requests, a metaculus.com URL has an
  ``attempted`` or ``unknown`` state, or the adapter suspects one;
- ``blocked_attempt``: every metaculus.com request was blocked by the CLI and nothing else points
  to exposure.
Flags of the previous attempt (``prev_attempt_id``) are inherited and never removed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from adapters.base import AdapterResult, is_metaculus_host, url_host
from flab.ids import new_id
from flab.timeutil import to_iso, utc_now
from flab.workdir import PROJECT_ROOT
import validate as V

AUDITS_LOG = PROJECT_ROOT / "data" / "audits.jsonl"
SEVERITY = ("leak_confirmed", "suspected_leak", "blocked_attempt")
KEYWORDS = re.compile(r"metaculus\.com|community prediction", re.I)
ECHO_CONTEXT = 30     # characters on each side of a hit that must also appear in the prompt
CONTEXT = 60          # characters kept on each side of a hit in the record
# The CLIs' own words for a denied request (claude 2.1.316, agy 1.4.12, codex 0.166.0 item status).
DENIAL_TEXT = re.compile(r"denied access to domain|Matches user-configured deny rule|user denied permission for read_url"
                         r"|\"status\":\s*\"(?:denied|blocked)\"")


def status_of(flags: Iterable[str]) -> str:
    present = set(flags)
    return next((f for f in SEVERITY if f in present), "clean")


def reported_source_urls(result: AdapterResult, structured: Any) -> set[str]:
    urls = V.source_urls(structured) | V.source_urls(result.structured)
    for obj, _, _ in V.json_objects(result.final_text or ""):
        urls.update(V.source_urls(obj))
    return urls


def _echo(text: str, start: int, end: int, prompt: str) -> bool:
    """The hit and at least ECHO_CONTEXT characters around it appear verbatim in the prompt.

    A short string (for example a lone "metaculus.com" host field) has no context to compare and is
    never an echo."""
    window = text[max(0, start - ECHO_CONTEXT):end + ECHO_CONTEXT]
    return len(window) - (end - start) >= ECHO_CONTEXT and window in prompt


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [x for v in value.values() for x in _strings(v)]
    if isinstance(value, list):
        return [x for v in value for x in _strings(v)]
    return []


def _requested_urls(events: list[Any]) -> dict[str, str]:
    """claude tool_use id -> requested URL, over all events of the run."""
    out: dict[str, str] = {}
    for e in events:
        msg = e.get("message") if isinstance(e, dict) and isinstance(e.get("message"), dict) else {}
        for b in msg.get("content") if isinstance(msg.get("content"), list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and isinstance(b.get("input"), dict):
                url = b["input"].get("url")
                if isinstance(b.get("id"), str) and isinstance(url, str):
                    out[b["id"]] = url
    return out


def _units(event: Any, blocked: set[str], requested: dict[str, str]) -> list[tuple[bool, Any]]:
    """(exempt, part) for each separately attributable part of one event.

    A part is exempt only when it is a request for a blocked URL, or the CLI's denial of exactly
    that request (bound by tool_use_id for claude, by the step for agy, by the item for codex).
    """
    if not isinstance(event, dict):
        return [(False, event)]
    msg = event.get("message") if isinstance(event.get("message"), dict) else None
    if msg is not None and isinstance(msg.get("content"), list):  # claude: one unit per content block
        units: list[tuple[bool, Any]] = [(False, {k: v for k, v in event.items() if k != "message"})]
        for b in msg["content"]:
            exempt = False
            if isinstance(b, dict) and b.get("type") == "tool_use":
                exempt = isinstance(b.get("input"), dict) and b["input"].get("url") in blocked
            elif isinstance(b, dict) and b.get("type") == "tool_result":
                exempt = requested.get(b.get("tool_use_id")) in blocked and bool(DENIAL_TEXT.search(json.dumps(b)))
            units.append((exempt, b))
        return units
    if event.get("type") == "system" and event.get("subtype") == "permission_denied":
        return [(requested.get(event.get("tool_use_id")) in blocked, event)]
    su = event.get("step_update")
    if isinstance(su, dict) and isinstance(su.get("tool_info"), dict):  # agy: one tool step
        info = su["tool_info"]
        url = (info.get("parameters") or {}).get("Url") if isinstance(info.get("parameters"), dict) else None
        err = info.get("error") if isinstance(info.get("error"), dict) else {}
        denied = bool(DENIAL_TEXT.search(str(err.get("message") or "")))
        exempt = info.get("name") == "read_url_content" and url in blocked and info.get("output") is None \
            and (denied or su.get("state") == "ACTIVE")
        return [(exempt, event)]
    item = event.get("item")
    if isinstance(item, dict) and item.get("type") == "web_search":  # codex: one item
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        urls = {u for u in [action.get("url"), *(action.get("urls") or [])] if u}
        exempt = bool(urls) and urls <= blocked and item.get("status") in ("denied", "blocked") and not item.get("results")
        return [(exempt, event)]
    return [(False, event)]


def keyword_hits(files: Iterable[Path], prompt: str, blocked_urls: set[str]) -> list[dict[str, Any]]:
    """Every keyword hit, classified per attributable part of each event:

    - ``prompt_echo``: the hit with its surrounding text appears verbatim in the prompt;
    - ``blocked_request``: a metaculus.com mention inside a request for a blocked URL, or inside the
      CLI's denial of that same request;
    - ``exposure``: everything else, including other results in the same message and search results
      that list the same URL.
    """
    hits = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        events = []
        for line in lines:
            try:
                events.append(json.loads(line))
            except ValueError:
                events.append(line)
        requested = _requested_urls(events)
        for n, event in enumerate(events):
            for exempt, part in _units(event, blocked_urls, requested):
                for chunk in _strings(part):
                    for m in KEYWORDS.finditer(chunk):
                        if _echo(chunk, m.start(), m.end(), prompt):
                            kind = "prompt_echo"
                        elif exempt and m.group(0).lower() == "metaculus.com":
                            kind = "blocked_request"
                        else:
                            kind = "exposure"
                        lo, hi = max(0, m.start() - CONTEXT), min(len(chunk), m.end() + CONTEXT)
                        hits.append({"file": path.name, "line": n + 1, "match": m.group(0),
                                     "context": chunk[lo:hi], "kind": kind})
    return hits


def audit_attempt(*, attempt_id: str, player: str, question_id: int, result: AdapterResult,
                  structured: dict[str, Any] | None, raw_files: Iterable[Path], prompt: str,
                  inherited: Iterable[str] = (), prev_attempt_id: str | None = None) -> dict[str, Any]:
    flags: set[str] = set()
    reasons: list[str] = []
    for url in sorted(reported_source_urls(result, structured)):
        if is_metaculus_host(url_host(url)):
            flags.add("leak_confirmed")
            reasons.append(f"sources contain {url}")
    blocked_urls: set[str] = set()
    other_states = False
    for e in result.url_events:
        if not is_metaculus_host(e.host or url_host(e.url)):
            continue
        if e.state == "opened":
            flags.add("leak_confirmed")
            other_states = True
            reasons.append(f"opened {e.url} ({e.source})")
        elif e.state == "blocked":
            blocked_urls.add(e.url or "")
            reasons.append(f"blocked {e.url} ({e.source})")
        else:
            flags.add("suspected_leak")
            other_states = True
            reasons.append(f"{e.state} {e.url} ({e.source})")
    for f in getattr(result, "audit_flags", None) or []:
        if f in ("leak_confirmed", "suspected_leak"):
            flags.add(f)
            reasons.append(f"adapter flag {f}")
    hits = keyword_hits(raw_files, prompt, {u for u in blocked_urls if u})
    exposure = [h for h in hits if h["kind"] == "exposure"]
    if exposure:
        flags.add("suspected_leak")
        reasons.append(f"{len(exposure)} keyword hit(s) outside the prompt and blocked requests")
    # Only when every metaculus.com request was blocked and nothing else points to exposure.
    if blocked_urls and not other_states and not exposure and not flags & {"leak_confirmed", "suspected_leak"}:
        flags.add("blocked_attempt")
    own = [f for f in SEVERITY if f in flags]  # this attempt's own findings, before inheritance
    for f in inherited:
        if f in SEVERITY and f not in flags:
            flags.add(f)
            reasons.append(f"inherited {f} from {prev_attempt_id}")
    ordered = [f for f in SEVERITY if f in flags]
    return {"type": "audit", "audit_event_id": new_id("aud"), "attempt_id": attempt_id, "prev_attempt_id": prev_attempt_id,
            "player": player, "question_id": question_id, "audit_flags": ordered, "own_audit_flags": own,
            "status": status_of(ordered),
            "audit_reasons": reasons, "keyword_hits": hits, "audited_at": to_iso(utc_now())}
