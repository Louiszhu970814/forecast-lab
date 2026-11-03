"""Claude integration review fixes for D121 batch 1 (synthetic data only)."""
from datetime import timedelta

import pytest

import baseline
import forecast as F
from flab.jsonl import append_record
from flab.timeutil import to_iso
from test_forecast import NOW


def _mismatch(path, aid, version, at):
    append_record(path, {"type": "forecast_attempt", "attempt_id": aid, "player": "gpt",
        "question_id": 1, "model_check": "mismatch", "pipeline_version": version,
        "finished_at_utc": to_iso(at)})


@pytest.mark.parametrize("future", ["forecast_pipeline_v8", "forecast_pipeline_v12"])
def test_soft_mismatch_survives_a_future_pipeline_bump(tmp_path, monkeypatch, future):
    log, forecasts = tmp_path / "line.jsonl", tmp_path / "forecasts.jsonl"
    _mismatch(forecasts, "a", "forecast_pipeline_v7", NOW)
    monkeypatch.setattr(F, "PIPELINE_VERSION", future)
    # The single v7 event is still a 30-minute backoff, never a ledger-recovered stop.
    assert F.line_stopped(log, "gpt", forecasts, now=NOW + timedelta(minutes=10))["reason"] == "model_mismatch_backoff"
    assert F.line_stopped(log, "gpt", forecasts, now=NOW + timedelta(days=2)) is None
    base = {"error_type": "model_mismatch", "question_type": "binary"}
    chain = [{"model_check": "mismatch", "pipeline_version": "forecast_pipeline_v7"}]
    assert F._may_rerun(chain, base)


def test_legacy_and_unversioned_mismatch_keep_hard_stop(tmp_path):
    log, forecasts = tmp_path / "line.jsonl", tmp_path / "forecasts.jsonl"
    for aid, version in (("v6", "forecast_pipeline_v6"), ("none", None)):
        path = tmp_path / f"{aid}.jsonl"
        _mismatch(path, aid, version, NOW)
        assert "recovered" in F.line_stopped(log, "gpt", path, now=NOW + timedelta(days=30))["reason"]
        assert not F._may_rerun([{"model_check": "mismatch", "pipeline_version": version}],
                                {"error_type": "model_mismatch", "question_type": "binary"})


def _src(player, h, category=baseline.CONTENT, kind="dir", **extra):
    return {"path": "/x", "player": player, "kind": kind, "category": category,
            "general_category": None, "exists": True, "hash": h, **extra}


def _approved_v1(log):
    append_record(log, {"type": "autoload_result", "record_id": "auto_1", "passed": True})
    append_record(log, {"type": "baseline_approved", "record_id": "base_old", "reviewed_by": "Louis",
        "autoload_record": "auto_1", "approved_at": to_iso(NOW), "taken_at": to_iso(NOW),
        "sources": {"claude.skills": _src("claude", "old-skills"),
                    "ancestor:/p/CLAUDE.md": _src("common", "anc"),
                    "ancestor:/p/AGENTS.md": _src("common", "agents"),
                    "codex.skills": _src("gpt", "gpt-old"),
                    "codex.config": _src("gpt", "cfg-old", baseline.INFORMATIONAL)},
        "versions": {"claude": "2.1.316", "gemini": "1.4.12", "gpt": "0.166.0"}, "gemini_credits": 0})


def _current(**changes):
    v2 = {"baseline_version": baseline.VERSION}
    sources = {"claude.skills": _src("claude", "new-skills", **v2),
               "ancestor:/p/CLAUDE.md": _src("claude", "anc", **v2),     # re-attributed, same content
               "ancestor:/p/AGENTS.md": _src("common", "agents", **v2),
               "codex.skills": _src("gpt", "gpt-old", **v2),
               "codex.config": _src("gpt", "cfg-new", baseline.INFORMATIONAL, **v2)}
    sources.update(changes)
    return baseline.Snapshot(taken_at=to_iso(NOW), sources=sources,
                             versions={"claude": "2.1.316", "gemini": None, "gpt": "0.166.0"},
                             gemini_credits=None)


def test_scoped_approval_works_against_a_v1_baseline(tmp_path):
    log = tmp_path / "acceptance.jsonl"
    _approved_v1(log)
    rec = baseline.approve(_current(), "Louis", "auto_1", log=log, player="claude")
    assert rec["scope"] == "claude" and rec["replaces_baseline"] == "base_old"
    assert rec["sources"]["claude.skills"]["hash"] == "new-skills"
    # Other lines keep their approved records and versions; unknown credits are not re-attested.
    assert rec["sources"]["codex.config"]["hash"] == "cfg-old"
    assert rec["versions"] == {"claude": "2.1.316", "gemini": "1.4.12", "gpt": "0.166.0"}
    assert rec["gemini_credits"] == 0
    decisions = baseline.compare(baseline.latest_approved(log), _current().to_record() | {"gemini_credits": 0,
        "versions": rec["versions"]})["decisions"]
    assert decisions["claude"] == "ok" and decisions["gpt"] == "ok"


def test_scoped_approval_never_approves_common_or_other_line_content(tmp_path):
    log = tmp_path / "acceptance.jsonl"
    _approved_v1(log)
    with pytest.raises(ValueError, match="common source"):
        baseline.approve(_current(**{"ancestor:/p/AGENTS.md": _src("common", "agents-new")}),
                         "Louis", "auto_1", log=log, player="claude")
    changed_gpt = _current(**{"codex.skills": _src("gpt", "gpt-new")})
    rec = baseline.approve(changed_gpt, "Louis", "auto_1", log=log, player="claude")
    assert rec["sources"]["codex.skills"]["hash"] == "gpt-old"
    after = changed_gpt.to_record() | {"gemini_credits": 0, "versions": rec["versions"]}
    assert baseline.compare(baseline.latest_approved(log), after)["decisions"]["gpt"] == "stop"
