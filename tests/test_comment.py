import copy

import pytest

import aggregate as A
import comment as C
from test_aggregate import BIN, MC, answer


def row(player="claude", p=.3, **kw):
    return answer(player, p, reasoning_summary="The evidence supports this forecast.",
                  key_factors=["Timing", "Demand"], base_rate="Comparable cases: 30%.",
                  sources=[{"url": "https://example.org/source", "title": "Evidence"}], **kw)


def make(rows=None, snap=BIN):
    rows = rows or [row(), row("gemini", .5)]
    aggregate = A.build_aggregate(snap, rows, stage="final")
    return C.build(snap, aggregate, [*rows, aggregate], [])


def test_comment_is_english_and_exactly_uses_selected_inputs():
    result = make()
    assert "Claude" in result["text"] and "Gemini" in result["text"]
    assert "Missing lines: GPT" in result["text"] and "log odds" in result["text"]
    assert "Reason:" in result["text"] and "Source:" in result["text"]
    assert result["input_attempt_ids"] == ["claude_1", "gemini_1"] and not result["submission_eligible"]


def test_multiple_choice_reports_all_options_and_smoothing():
    result = make([row(p={"A": .5, "B": .3, "C": .2}, snapshot=MC)], MC)
    assert "uniform smoothing" in result["text"]
    assert all(option in result["text"] for option in MC["options"])


def test_tier2_reason_is_present_and_unused_tier1_text_is_absent():
    first = row()
    first["reasoning_summary"] = "UNUSED_TIER_ONE"
    second = row(p=.6, aid="tier2", tier=2, input_attempt_ids=[first["attempt_id"]],
                 change_reason="The new publication resolves the timing dispute.")
    result = make([first, second])
    assert "Tier\\-2 judgement" in result["text"] and "UNUSED_TIER_ONE" not in result["text"]
    assert result["input_attempt_ids"] == ["tier2"]


@pytest.mark.parametrize("source", ["javascript:alert(1)", "file:///private/data", "data:text/html,hi", "https://u:p@example.org"])
def test_unsafe_source_urls_are_omitted(source):
    r = row()
    r["sources"] = [{"url": source, "title": "BAD_SOURCE_TITLE"}]
    assert "BAD_SOURCE_TITLE" not in make([r])["text"]


def test_html_images_commands_and_markdown_stay_inert_plain_text():
    r = row()
    r["reasoning_summary"] = '<script>HIDDEN_SCRIPT</script><b>Useful</b> ![img](https://bad.org/x) [click](javascript:evil) **bold** <img src="x">'
    text = make([r])["text"]
    assert "HIDDEN_SCRIPT" not in text and "bad.org" not in text and "javascript:" not in text
    assert "<script>" not in text and "<img" not in text and "\\*\\*bold\\*\\*" in text
    assert "Useful" in text


def test_length_reduction_keeps_each_line_and_omits_sources_first():
    rows = [row(p, .3) for p in ("claude", "gemini", "gpt")]
    for r in rows:
        r["reasoning_summary"] = "A relevant complete sentence about the evidence. " * 25
        r["sources"] = [{"url": f"https://example.org/{i}", "title": "Long source title " * 10} for i in range(20)]
    result = make(rows)
    assert len(result["text"]) <= 3000 and result["omitted_sections"]["sources"] > 0
    assert result["text"].count("Reason:") == 3 and "…" in result["text"]


def test_late_audit_does_not_silently_replace_the_inputs_in_a_comment():
    rows = [row(), row("gemini", .5)]
    agg = A.build_aggregate(BIN, rows, stage="final")
    event = {"type": "audit_update", "audit_event_id": "late", "attempt_id": "claude_1",
             "audited_at": "2026-11-01T05:00:00Z", "audit_flags": ["leak_confirmed"], "audit_reasons": ["test"]}
    with pytest.raises(C.CommentError, match="no longer match"):
        C.build(BIN, agg, [*rows, agg], [event])


def test_no_comment_for_invented_probability_or_wrong_question():
    rows = [row()]
    agg = A.build_aggregate(BIN, rows, stage="final")
    for key, val in (("probability", .9), ("question_id", 99)):
        bad = {**agg, key: val}
        with pytest.raises(ValueError):
            C.build(BIN, bad, rows, [])


def test_unfittable_mandatory_options_fail_instead_of_cutting_tail():
    snap = {**MC, "options": ["X" * 2000, "Y" * 2000]}
    r = row(p={snap["options"][0]: .5, snap["options"][1]: .5}, snapshot=snap)
    with pytest.raises(C.CommentError, match="exceed"):
        make([r], snap)


def test_inputs_are_not_mutated():
    rows = [row()]
    before = copy.deepcopy(rows)
    make(rows)
    assert rows == before


def test_colon_in_reason_is_preserved_as_prose():
    r = row()
    r["reasoning_summary"] = "Probability:0.3 given the comparable evidence."
    assert r"Probability:0\.3" in make([r])["text"]
