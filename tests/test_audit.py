from __future__ import annotations

import json
from pathlib import Path

import audit
from adapters.base import AdapterResult, UrlEvent
from adapters.gpt import GptResult

PROMPT = "Rules: Do not open metaculus.com or any of its subdomains. Use only web search and reading pages."


def _result(cls=AdapterResult, **kw: object) -> AdapterResult:
    base: dict[str, object] = dict(argv=["x"], run=None, cli_version="1", completed=True, final_text="done",
                                   structured=None, reported_model_id=None, model_check="unverified", usage_raw=None)
    base.update(kw)
    return cls(**base)  # type: ignore[arg-type]


def _audit(tmp_path: Path, text: str = "", **kw: object) -> dict:
    raw = tmp_path / "stdout.jsonl"
    raw.write_text(text)
    args = dict(attempt_id="att_1", player="claude", question_id=7, result=_result(), structured=None,
                raw_files=[raw], prompt=PROMPT)
    args.update(kw)
    return audit.audit_attempt(**args)  # type: ignore[arg-type]


def test_clean_attempt(tmp_path: Path) -> None:
    rec = _audit(tmp_path, '{"result": "searched example.com"}')
    assert rec["status"] == "clean" and rec["audit_flags"] == [] and rec["audit_event_id"].startswith("aud_")


def test_sources_or_an_opened_url_confirm_a_leak(tmp_path: Path) -> None:
    rec = _audit(tmp_path, structured={"sources": [{"url": "https://www.metaculus.com/questions/1/", "title": "q"}]})
    assert rec["status"] == "leak_confirmed"
    opened = _result(url_events=[UrlEvent("https://metaculus.com/", "metaculus.com", "opened", "tool_use")])
    assert _audit(tmp_path, result=opened)["status"] == "leak_confirmed"
    assert _audit(tmp_path, result=_result(GptResult, audit_flags=["leak_confirmed"]))["status"] == "leak_confirmed"


def _claude_events(url: str) -> list[dict]:
    return [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "WebFetch",
                                                           "input": {"url": url}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                                                      "content": "WebFetch denied access to domain:metaculus.com"}]}}]


def test_blocked_request_alone_is_a_blocked_attempt(tmp_path: Path) -> None:
    url = "https://www.metaculus.com/questions/"
    blocked = _result(url_events=[UrlEvent(url, "www.metaculus.com", "blocked", "cli_rule")])
    rec = _audit(tmp_path, "\n".join(json.dumps(e) for e in _claude_events(url)), result=blocked)
    assert rec["status"] == "blocked_attempt"
    assert [h["kind"] for h in rec["keyword_hits"]] == ["blocked_request", "blocked_request"]


def test_exemption_is_bound_to_the_denied_request_block(tmp_path: Path) -> None:
    # GPT W8 review 2: one user message carrying t1's CLI denial and t2's search result.
    url = "https://www.metaculus.com/questions/"
    blocked = _result(url_events=[UrlEvent(url, "www.metaculus.com", "blocked", "cli_rule")])
    events = _claude_events(url)
    events[1]["message"]["content"].append({"type": "tool_result", "tool_use_id": "t2",
                                            "content": "https://www.metaculus.com/questions/9/ forecast 40%"})
    rec = _audit(tmp_path, "\n".join(json.dumps(e) for e in events), result=blocked)
    assert [h["kind"] for h in rec["keyword_hits"]] == ["blocked_request", "blocked_request", "exposure"]
    assert rec["status"] == "suspected_leak" and "blocked_attempt" not in rec["audit_flags"]
    # a lone host field has no context to compare with the prompt
    lone = _audit(tmp_path, json.dumps({"type": "search_result", "host": "metaculus.com", "snippet": "Forecast: 40%"}))
    assert lone["keyword_hits"][0]["kind"] == "exposure" and lone["status"] == "suspected_leak"


def test_keywords_outside_the_prompt_are_suspected(tmp_path: Path) -> None:
    rec = _audit(tmp_path, '{"snippet": "The Metaculus community prediction is 40%"}')
    assert rec["status"] == "suspected_leak" and rec["keyword_hits"][0]["kind"] == "exposure"
    echo = _audit(tmp_path, '{"text": "Do not open metaculus.com or any of its subdomains. Use only web search"}')
    assert echo["status"] == "clean" and echo["keyword_hits"][0]["kind"] == "prompt_echo"
    unknown = _result(url_events=[UrlEvent("https://metaculus.com/x", "metaculus.com", "unknown", "search_result")])
    assert _audit(tmp_path, result=unknown)["status"] == "suspected_leak"


def test_flags_are_inherited_and_never_removed(tmp_path: Path) -> None:
    rec = _audit(tmp_path, inherited=["suspected_leak", "not_a_flag"], prev_attempt_id="att_0")
    assert rec["audit_flags"] == ["suspected_leak"] and rec["prev_attempt_id"] == "att_0"
    assert "inherited suspected_leak from att_0" in rec["audit_reasons"]
    both = _audit(tmp_path, structured={"sources": [{"url": "https://metaculus.com/", "title": ""}]},
                  inherited=["blocked_attempt"])
    assert both["audit_flags"] == ["leak_confirmed", "blocked_attempt"] and both["status"] == "leak_confirmed"


def test_new_text_next_to_an_echo_or_a_blocked_url_is_still_exposure(tmp_path: Path) -> None:
    # GPT W8 review: an echoed rule followed by a community prediction, and a search result listing a blocked URL.
    rec = _audit(tmp_path, json.dumps({"text": PROMPT[:60] + " community prediction is 40%"}))
    kinds = {h["match"].lower(): h["kind"] for h in rec["keyword_hits"]}
    assert kinds["community prediction"] == "exposure" and rec["status"] == "suspected_leak"
    url = "https://www.metaculus.com/questions/"
    blocked = _result(url_events=[UrlEvent(url, "www.metaculus.com", "blocked", "cli_rule")])
    lines = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "WebFetch",
                                                              "input": {"url": url}}]}},
             {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                                        "content": f"WebFetch denied access to domain:metaculus.com {url}"}]}},
             {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2",
                                                        "content": f"Search results: {url} community prediction 40%"}]}}]
    rec = _audit(tmp_path, "\n".join(json.dumps(x) for x in lines), result=blocked)
    assert [h["kind"] for h in rec["keyword_hits"]] == ["blocked_request", "blocked_request", "blocked_request",
                                                       "exposure", "exposure"]
    assert rec["status"] == "suspected_leak" and "blocked_attempt" not in rec["audit_flags"]
    only_blocked = _audit(tmp_path, "\n".join(json.dumps(x) for x in lines[:2]), result=blocked)
    assert only_blocked["audit_flags"] == ["blocked_attempt"]
    words_only = _audit(tmp_path, json.dumps({"text": "metaculus.com was blocked by policy"}), result=blocked)
    assert words_only["status"] == "suspected_leak"  # denial words without a CLI denial are not an exemption


def test_audit_survives_malformed_output(tmp_path: Path) -> None:
    for structured in ({"sources": 1}, {"sources": [1, None, {"url": 5}]}, {"sources": "x"}):
        assert _audit(tmp_path, structured=structured)["status"] == "clean"
