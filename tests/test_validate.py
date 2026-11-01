from __future__ import annotations

import json
import math

import pytest

import validate as V
from flab import schema as S

BIN = {"question_id": 7, "question_type": "binary", "options": None, "scheduled_close_time": "2026-12-01T00:00:00Z"}
MC = {"question_id": 8, "question_type": "multiple_choice", "options": ["A", "B", "Other"],
      "scheduled_close_time": "2026-12-01T00:00:00Z"}


def _out(snap: dict, **kw: object) -> dict:
    base = {"question_id": snap["question_id"], "question_type": snap["question_type"],
            "probability": 0.3 if snap["question_type"] == "binary" else None,
            "option_probabilities": None if snap["question_type"] == "binary" else {"A": 0.5, "B": 0.3, "Other": 0.2},
            "reasoning_summary": "r", "key_factors": ["k"], "base_rate": "b", "market_refs": [],
            "sources": [{"url": "https://example.com/a", "title": "t"}], "changed_from_tier1": None,
            "change_reason": None, "escalation_flags": [], "injection_notice": None}
    base.update(kw)
    return base


# ---------------------------------------------------------------- schema validator

def test_validator_reports_each_keyword_and_rejects_unsupported_ones() -> None:
    sch = {"type": "object", "additionalProperties": False, "required": ["a", "b"],
           "properties": {"a": {"type": "integer", "const": 1}, "b": {"type": "array", "minItems": 1,
                                                                       "items": {"type": "string", "enum": ["x"]}},
                          "c": {"type": ["number", "null"], "minimum": 0, "maximum": 1}}}
    S.check_schema(sch)
    assert S.validate({"a": 1, "b": ["x"], "c": None}, sch) == []
    errs = S.validate({"a": 2, "b": [], "c": 2, "d": 0}, sch)
    assert any("must equal 1" in e for e in errs) and any("fewer than 1" in e for e in errs)
    assert any("above maximum" in e for e in errs) and any("unexpected 'd'" in e for e in errs)
    assert S.validate({"a": True, "b": ["x"]}, sch) == ["$.a: expected integer, got bool"]
    assert S.validate({"a": 1, "b": ["x"], "c": math.nan}, sch)[0].startswith("$.c: expected")
    with pytest.raises(S.SchemaError):
        S.check_schema({"type": "object", "patternProperties": {}})


def test_question_schema_fixes_id_type_and_options() -> None:
    b, m = V.question_schema(BIN), V.question_schema(MC)
    assert b["properties"]["question_id"] == {"type": "integer", "const": 7}
    assert b["properties"]["option_probabilities"] == {"type": "null"}
    assert m["properties"]["option_probabilities"]["required"] == ["A", "B", "Other"]
    assert m["properties"]["probability"] == {"type": "null"}
    assert m["properties"]["market_refs"]["items"]["properties"]["complete"] == {"type": "boolean"}
    assert set(b["required"]) == set(b["properties"]) and b["additionalProperties"] is False
    assert not set(V.METADATA_KEYS) & set(b) and "$schema" in V.load_template()
    assert V.schema_sha256(b) != V.schema_sha256(V.question_schema({**BIN, "question_id": 9}))
    with pytest.raises(ValueError):
        V.question_schema({**MC, "options": ["A", "A"]})
    with pytest.raises(ValueError):
        V.question_schema({**BIN, "question_type": "numeric"})


# ---------------------------------------------------------------- extraction and status

def test_extraction_prefers_structured_output_then_the_only_object_in_the_text() -> None:
    assert V.extract_output({"a": 1}, "ignored") == ({"a": 1}, "structured_output")
    assert V.extract_output(None, '{"a": 1}') == ({"a": 1}, "final_text")
    assert V.extract_output(None, 'Here:\n```json\n{"a": 2}\n```') == ({"a": 2}, "final_text_embedded")
    assert V.extract_output(None, 'x {"a": 1} y {"b": 2}') == (None, "several_objects")
    assert V.extract_output(None, "no json") == (None, "no_object")
    assert V.extract_output(None, "") == (None, "empty")


@pytest.mark.parametrize("snap,kw,status", [
    (BIN, {}, V.VALID),
    (BIN, {"question_id": 99}, V.SEMANTIC_FAILED),
    (BIN, {"question_type": "multiple_choice"}, V.SEMANTIC_FAILED),
    (BIN, {"probability": 1.0}, V.SEMANTIC_FAILED),
    (BIN, {"probability": float("inf")}, V.SEMANTIC_FAILED),
    (BIN, {"option_probabilities": {"A": 1}}, V.SEMANTIC_FAILED),
    (BIN, {"sources": []}, V.SCHEMA_FAILED),
    (BIN, {"extra": 1}, V.SCHEMA_FAILED),
    (BIN, {"probability": "0.3"}, V.SCHEMA_FAILED),
    (BIN, {"changed_from_tier1": True}, V.SCHEMA_FAILED),
    (MC, {}, V.VALID),
    (MC, {"option_probabilities": {"A": 0.5, "B": 0.5}}, V.SEMANTIC_FAILED),
    (MC, {"option_probabilities": {"A": 0.5, "B": 0.3, "Other": 0.3}}, V.SEMANTIC_FAILED),
    (MC, {"option_probabilities": {"A": 0.5, "B": 0.3, "Other": 0.0}}, V.SEMANTIC_FAILED),
    (MC, {"probability": 0.4}, V.SEMANTIC_FAILED),
])
def test_validation_status(snap: dict, kw: dict, status: str) -> None:
    assert V.validate_output(_out(snap, **kw), snap, V.question_schema(snap)).status == status


def test_multiple_choice_is_normalised_and_raw_values_kept() -> None:
    ops = {"A": 0.5, "B": 0.3, "Other": 0.205}
    out = V.validate_output(_out(MC, option_probabilities=ops), MC, V.question_schema(MC))
    assert out.status == V.VALID and out.option_probabilities_raw == ops
    assert abs(sum(out.option_probabilities.values()) - 1) < 1e-12
    assert V.validate_output("text", MC, V.question_schema(MC)).status == V.PARSE_FAILED


def test_submit_deadline_is_close_minus_margin() -> None:
    d = V.submit_deadline(BIN, {"safety_margin_minutes": 20})
    assert d.isoformat() == "2026-11-30T23:40:00+00:00"
    assert V.submit_deadline({**BIN, "scheduled_close_time": None}, {"safety_margin_minutes": 20}) is None


# ---------------------------------------------------------------- format repair (§7.5)

def test_repair_basis_needs_one_unique_probability() -> None:
    assert V.repair_basis('{"question_id": 7, "probability": 0.31, "sources": []}', BIN)["probability"] == 0.31
    assert V.repair_basis("My final probability is 23%.", BIN)["probability"] == 0.23
    assert V.repair_basis("Probability: 0.2. Later the probability is 0.4.", BIN) is None
    assert V.repair_basis("no numbers here", BIN) is None
    ops = {"A": 0.5, "B": 0.3, "Other": 0.2}
    assert V.repair_basis(json.dumps({"option_probabilities": ops}), MC)["option_probabilities"] == ops
    assert V.repair_basis(json.dumps({"option_probabilities": {"A": 0.5, "B": 0.5}}), MC) is None


def test_repair_may_not_change_probabilities_or_add_sources() -> None:
    raw = 'probability is 0.31 according to https://example.com/a'
    good = _out(BIN, probability=0.31)
    assert V.repair_errors(good, {"probability": 0.31}, raw) == []
    assert V.repair_errors(_out(BIN, probability=0.35), {"probability": 0.31}, raw)
    added = _out(BIN, probability=0.31, sources=[{"url": "https://new.example/", "title": "x"}])
    assert "added a source" in V.repair_errors(added, {"probability": 0.31}, raw)[0]
    ops = {"A": 0.5, "B": 0.3, "Other": 0.2}
    raw_mc = json.dumps({"option_probabilities": ops, "u": "https://example.com/a"})
    assert V.repair_errors(_out(MC), {"option_probabilities": ops}, raw_mc) == []
    assert V.repair_errors(_out(MC, option_probabilities={**ops, "A": 0.4}), {"option_probabilities": ops}, raw_mc)


# ---------------------------------------------------------------- GPT W8 review counterexamples

def test_competing_estimates_are_never_chosen_between() -> None:
    fenced_and_outside = 'Draft:\n```json\n{"probability": 0.3}\n```\nFinal: {"probability": 0.8}'
    assert V.extract_output(None, fenced_and_outside) == (None, "several_objects")
    assert V.repair_basis(fenced_and_outside, BIN) is None
    assert V.repair_basis('{"probability": 0.3} Final probability is 0.8', BIN) is None
    assert V.repair_basis('{"probability": 0.3} and again the probability is 30%', BIN)["probability"] == 0.3
    # market prices inside the object are not competing estimates
    one = json.dumps({"probability": 0.3, "market_refs": [{"probability": 0.41}]})
    assert V.repair_basis(one, BIN)["probability"] == 0.3
    two_mc = json.dumps({"option_probabilities": {"A": 0.5, "B": 0.3, "Other": 0.2}}) + \
        json.dumps({"option_probabilities": {"A": 0.6, "B": 0.2, "Other": 0.2}})
    assert V.repair_basis(two_mc, MC) is None


def test_repair_check_covers_market_urls_and_prices_and_whole_urls() -> None:
    market = {"platform": "Polymarket", "url": "https://polymarket.com/e/x", "market_title": "x", "probability": 0.41,
              "option_probabilities": None, "complete": None, "retrieved_at_utc": "t", "match": "close", "difference_note": None}
    raw = ("Draft: " + json.dumps({"probability": 0.31, "market_refs": [market]}) +
           " see https://example.com/article-long.")
    basis = V.repair_basis(raw, BIN)
    assert basis == {"probability": 0.31, "markets": {"https://polymarket.com/e/x": {"probability": 0.41,
                                                                                    "option_probabilities": None}}}
    ok = _out(BIN, probability=0.31, sources=[{"url": "https://example.com/article-long", "title": "a"}], market_refs=[market])
    assert V.repair_errors(ok, basis, raw) == []
    prefix = _out(BIN, probability=0.31, sources=[{"url": "https://example.com/article", "title": "a"}])
    assert "source URL" in V.repair_errors(prefix, basis, raw)[0]
    new_market = _out(BIN, probability=0.31, sources=ok["sources"], market_refs=[{**market, "url": "https://kalshi.com/m"}])
    assert any("cannot be matched" in error for error in V.repair_errors(new_market, basis, raw))
    # GPT W8 review 2: a price that appears elsewhere in the text (here the top-level 0.31) is still a change
    moved = _out(BIN, probability=0.31, sources=ok["sources"], market_refs=[{**market, "probability": 0.31}])
    assert "changed the market" in V.repair_errors(moved, basis, raw)[0]
    prose = "probability is 0.31; Polymarket https://polymarket.com/e/x at 41%"
    assert "cannot be matched" in V.repair_errors(_out(BIN, probability=0.31, sources=[], market_refs=[market]),
                                                  V.repair_basis(prose, BIN), prose)[0]


def test_multiple_choice_market_options_cannot_be_swapped() -> None:
    m = {"url": "https://kalshi.com/m", "probability": None, "option_probabilities": {"A": 0.6, "B": 0.3, "Other": None}}
    raw = json.dumps({"option_probabilities": {"A": 0.5, "B": 0.3, "Other": 0.2}, "market_refs": [m]})
    basis = V.repair_basis(raw, MC)
    swapped = {**m, "option_probabilities": {"A": 0.3, "B": 0.6, "Other": None}}
    assert "changed the market" in V.repair_errors(_out(MC, sources=[], market_refs=[swapped]), basis, raw)[0]


def test_string_estimates_compete_with_numbers() -> None:
    # GPT W8 review 2: {"probability": 0.3} followed by {"probability": "0.8"} is not a unique estimate.
    assert V.repair_basis('{"probability": 0.3} {"probability": "0.8"}', BIN) is None
    assert V.repair_basis('{"probability": 0.3} {"probability": "30%"}', BIN)["probability"] == 0.3
    assert V.repair_basis('{"probability": 0.3} {"probability": "high"}', BIN) is None


def test_missing_identity_fields_are_schema_errors_not_semantic() -> None:
    out = _out(BIN)
    del out["question_id"]
    assert V.validate_output(out, BIN, V.question_schema(BIN)).status == V.SCHEMA_FAILED


def test_schema_equality_follows_json_not_python() -> None:
    assert S.validate(True, {"const": 1}) and S.validate(1, {"enum": [True]})
    assert S.validate(1.0, {"type": "integer", "const": 1}) == []
    assert S.validate(1.5, {"type": "integer"})


def test_repair_cannot_discard_market_estimates_to_pass_validation() -> None:
    market = {"url": "https://example.com/market", "probability": 0.4, "option_probabilities": None}
    raw = json.dumps({"probability": 0.3, "market_refs": [market]})
    basis = V.repair_basis(raw, BIN)
    errors = V.repair_errors(_out(BIN, sources=[], market_refs=[]), basis, raw)
    assert any("removed original markets" in e for e in errors)
    conflicting = raw + json.dumps({"probability": 0.3, "market_refs": [{**market, "probability": 0.6}]})
    assert V.repair_basis(conflicting, BIN) is None


def test_repair_preserves_all_original_source_urls():
    raw = json.dumps(_out(BIN, sources=[{"url": "https://example.com/a", "title": "a"},
                                       {"url": "https://example.org/b", "title": "b"}]))
    errors = V.repair_errors(_out(BIN), V.repair_basis(raw, BIN), raw)
    assert any("removed original source URLs" in e for e in errors)


def test_wrapped_estimates_and_embedded_text_compete_but_market_prices_do_not():
    assert V.repair_basis('My probability is 0.3. {"forecast":{"probability":0.8}}', BIN) is None
    assert V.repair_basis('{"probability":0.3,"reasoning_summary":"My final probability is 0.8"}', BIN) is None
    assert V.repair_basis('{"probability":0.3,"market_refs":[{"probability":0.8}]}', BIN)["probability"] == .3


@pytest.mark.parametrize("refs", [1, True, "bad", None])
def test_malformed_market_refs_do_not_crash_repair_basis(refs):
    # Independent review suspected an operator-precedence bug here; this verifies the actual semantics.
    assert V.repair_basis(json.dumps({"probability": .3, "market_refs": refs}), BIN)["probability"] == .3


@pytest.mark.parametrize("extra", [{"notes": ["my probability is 30%"]}, {"s": [[{"probability": .3}]]}])
def test_competing_estimates_inside_nested_lists_are_rejected(extra):
    assert V.repair_basis(json.dumps({"probability": .6, **extra}), BIN) is None
