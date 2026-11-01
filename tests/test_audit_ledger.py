from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import aggregate as A
import audit_ledger as L
from flab.ids import hash_obj
from flab.jsonl import append_record, read_records

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
IDENTITY = {"question_id": 7, "question_snapshot_id": "s1", "post_id": 17, "tournament_id": 27}


def attempt(aid, **kw):
    return {"type": "forecast_attempt", "attempt_id": aid, **IDENTITY,
            "player": "claude", "tier": 1, "question_type": "binary", "probability": .3,
            "option_probabilities": None, "validation_status": "valid", "audit_flags": [], **kw}


def event(eid="aud_late", aid="a", flags=None, **kw):
    return {"type": "audit_update", "audit_event_id": eid, "attempt_id": aid,
            "audited_at": "2026-11-01T00:00:00Z", "audit_flags": flags if flags is not None else ["leak_confirmed"],
            "audit_reasons": ["Synthetic post-hoc evidence confirms the source was opened."], **kw}


def records():
    return [attempt("a", validation_status="schema_failed", audit_flags=["suspected_leak"]),
            attempt("repair", prev_attempt_id="a"), attempt("rerun", prev_attempt_id="repair"),
            {"type": "subagent_result", "subagent_id": "child", **IDENTITY, "parent_attempt_id": "b",
             "result_delivered": True, "input_attempt_ids": ["repair"]},
            attempt("b", player="gemini", tier=2, input_attempt_ids=["repair"], input_subagent_ids=["child"]),
            attempt("c", player="gpt", tier=2, input_attempt_ids=["b"]),
            {"type": "aggregate", "record_id": "agg", **IDENTITY, "input_attempt_ids": ["repair", "b", "c"],
             "probability": .4, "status": "ready"},
            {"type": "submission", "record_id": "intent", **IDENTITY, "aggregate_id": "agg", "state": "intent",
             "submission_id": "send_1", "probability": .4},
            {"type": "submission", "record_id": "receipt", **IDENTITY, "aggregate_id": "agg", "state": "forecast_ok",
             "submission_id": "send_1", "probability": .4},
            attempt("unrelated", question_id=8, question_snapshot_id="s2")]


def impacts(rows):
    return [r for r in rows if r["type"] == "audit_impact"]


def test_late_audit_reaches_repairs_reruns_other_lines_children_aggregates_and_submissions(tmp_path):
    source = records()
    original = copy.deepcopy(source)
    rows = L.append_impacts(tmp_path / "impacts.jsonl", L.ImpactGraph(source), [event()], now=NOW)
    affected = impacts(rows)
    targets = {(r["target"]["kind"], r["target"]["id"]) for r in affected}
    assert targets == {("attempt", a) for a in ("a", "repair", "rerun", "b", "c")} | {
        ("subagent", "child"), ("aggregate", "agg"), ("submission", "intent"), ("submission", "receipt")}
    assert all(r["effect"] == "exclude_from_future_use" and r["audit_event_id"] == "aud_late" for r in affected)
    assert {r["submission_state"] for r in affected if r["target"]["kind"] == "submission"} == {"intent", "forecast_ok"}
    assert source == original  # probabilities, selection and remote receipts are never rewritten


def test_replay_is_idempotent_and_new_descendants_receive_old_events(tmp_path):
    path = tmp_path / "impacts.jsonl"
    first = records()[:3]
    L.append_impacts(path, L.ImpactGraph(first), [event()], now=NOW)
    prefix = path.read_bytes()
    assert L.append_impacts(path, L.ImpactGraph(first), [event()], now=NOW + timedelta(days=1)) == []
    assert path.read_bytes() == prefix
    added = L.append_impacts(path, L.ImpactGraph(records()), [event()], now=NOW + timedelta(days=2))
    assert path.read_bytes().startswith(prefix)
    assert len(impacts(added)) == 6  # three attempts already covered, six new descendants
    assert L.append_impacts(path, L.ImpactGraph(records()), [event()]) == []


def test_new_event_appends_impacts_without_erasing_suspected_history(tmp_path):
    path = tmp_path / "impact.jsonl"
    suspected = event("aud_suspected", flags=["suspected_leak"])
    graph = L.ImpactGraph(records())
    L.append_impacts(path, graph, [suspected], now=NOW)
    before = path.read_bytes()
    added = L.append_impacts(path, graph, [suspected, event()], now=NOW + timedelta(hours=1))
    assert len(impacts(added)) == 9 and path.read_bytes().startswith(before)
    view = graph.attempt_audit_view([suspected, event(), event("aud_clean", flags=[])])
    row = next(r for r in view if r["attempt_id"] == "b")
    assert row["audit_flags"] == ["leak_confirmed", "suspected_leak"]
    assert row["source_audit_event_ids"] == ["aud_late", "aud_suspected"]


@pytest.mark.parametrize("mutation", ["node", "event", "omit_event", "omit_node"])
def test_history_cannot_be_changed_or_silently_omitted(tmp_path, mutation):
    path = tmp_path / "impact.jsonl"
    rows, audits = records(), [event()]
    L.append_impacts(path, L.ImpactGraph(rows), audits)
    before = path.read_bytes()
    if mutation == "node":
        rows[0]["probability"] = .8
    elif mutation == "event":
        audits[0]["audit_reasons"] = ["changed evidence"]
    elif mutation == "omit_event":
        audits = []
    else:
        rows = rows[:-1]  # even an unrelated previously registered node must be retained
    with pytest.raises(L.LedgerError, match="history differs"):
        L.append_impacts(path, L.ImpactGraph(rows), audits)
    assert path.read_bytes() == before


@pytest.mark.parametrize("bad,needle", [
    ([attempt("a", prev_attempt_id="missing")], "missing dependency"),
    ([attempt("a"), attempt("a")], "duplicate node"),
    ([attempt("a", prev_attempt_id="b"), attempt("b", prev_attempt_id="a")], "cycle"),
    ([attempt("a"), attempt("b", prev_attempt_id="a", question_snapshot_id="other")], "incompatible"),
    ([attempt("a"), attempt("b", prev_attempt_id="a", post_id=19)], "incompatible"),
    ([attempt("a", input_attempt_ids="a")], "must be a list"),
    ([attempt("a", audit_flags=["cleared"])], "known flags"),
])
def test_malformed_graphs_fail_closed(bad, needle):
    with pytest.raises(L.LedgerError, match=needle):
        L.ImpactGraph(bad)


def test_complete_batch_is_validated_before_the_first_append(tmp_path):
    path = tmp_path / "impact.jsonl"
    with pytest.raises(L.LedgerError, match="unknown audit target"):
        L.append_impacts(path, L.ImpactGraph(records()), [event(), event("ghost", aid="missing")])
    assert not path.exists()


@pytest.mark.parametrize("change", [
    {"audit_flags": ["unknown"]}, {"audit_reasons": []}, {"question_id": 999},
    {"target": {"kind": "attempt", "id": "repair"}}, {"audited_at": "yesterday"},
])
def test_invalid_events_are_rejected(change):
    with pytest.raises(ValueError):
        L.proposed_rows(L.ImpactGraph(records()), [event(**change)])


def test_existing_attempt_audit_format_and_empty_clean_audits_work():
    clean = event("clean", flags=[], type="audit", audit_reasons=[])
    graph = L.ImpactGraph(records())
    assert impacts(L.proposed_rows(graph, [clean])) == []
    assert len(impacts(L.proposed_rows(graph, [event(type="audit")]))) == 9
    with pytest.raises(L.LedgerError, match="duplicate audit event"):
        graph.events([clean, clean])


def test_subagent_source_propagates_to_consumer_without_manufacturing_parent_cycle():
    rows = records()
    rows[3]["parent_attempt_id"] = "b"  # launch attribution, not an input from b's eventual answer
    graph = L.ImpactGraph(rows)
    e = event(aid=None, target={"kind": "subagent", "id": "child"})
    targets = {r["target"]["id"] for r in impacts(L.proposed_rows(graph, [e]))}
    assert targets == {"child", "b", "c", "agg", "intent", "receipt"}
    assert "repair" not in targets  # no backward contamination of a source by its consumer


def test_projection_excludes_subagent_contamination_from_aggregation_and_preserves_event_ids():
    rows = [attempt("a", player="gpt"),
            {"type": "subagent_result", "subagent_id": "s", **IDENTITY, "parent_attempt_id": "b",
             "result_delivered": True, "input_attempt_ids": ["a"]},
            attempt("b", tier=2, input_attempt_ids=["a"], input_subagent_ids=["s"])]
    graph = L.ImpactGraph(rows)
    e = event(aid=None, target={"kind": "subagent", "id": "s"})
    projected = graph.attempt_audit_view([e])
    snapshot = {**IDENTITY, "snapshot_id": "s1", "question_type": "binary"}
    result = A.build_aggregate(snapshot, [rows[0], rows[2]], stage="final", audit_events=projected)
    assert result["input_attempt_ids"] == ["a"]
    assert result["audit_event_ids"] == ["aud_late"]
    assert "leak_confirmed" in result["excluded_inputs"][0]["reasons"]


def test_model_mismatch_and_tool_violations_also_flow_through_the_view():
    rows = records()
    rows[0]["model_check"] = "mismatch"
    rows[3]["tool_violations"] = [{"tool": "Bash"}]
    view = L.ImpactGraph(rows).attempt_audit_view([])
    b = next(r for r in view if r["attempt_id"] == "b")
    assert {"model_mismatch", "tool_scope_violation"} <= set(b["audit_flags"])


def test_interrupted_append_resumes_a_valid_prefix(monkeypatch, tmp_path):
    path = tmp_path / "impact.jsonl"
    graph = L.ImpactGraph(records())
    real_append = L.append_record
    calls = 0

    def interrupt(path, row):
        nonlocal calls
        calls += 1
        if calls == 14:
            raise OSError("simulated storage interruption")
        real_append(path, row)

    monkeypatch.setattr(L, "append_record", interrupt)
    with pytest.raises(OSError):
        L.append_impacts(path, graph, [event()], now=NOW)
    prefix = path.read_bytes()
    monkeypatch.setattr(L, "append_record", real_append)
    added = L.append_impacts(path, graph, [event()], now=NOW + timedelta(hours=1))
    assert added and path.read_bytes().startswith(prefix)
    expected = L.proposed_rows(graph, [event()], now=NOW)
    actual = list(read_records(path))
    assert {r["record_id"] for r in actual} == {r["record_id"] for r in expected}
    assert len(actual) == len(expected)


def test_torn_history_is_not_truncated_or_ignored(tmp_path):
    path = tmp_path / "impact.jsonl"
    path.write_bytes(b'{"type":')
    with pytest.raises(ValueError):
        L.append_impacts(path, L.ImpactGraph(records()), [event()])
    assert path.read_bytes() == b'{"type":'


def test_two_processes_cannot_append_duplicate_impacts(tmp_path):
    path, packet = tmp_path / "impact.jsonl", tmp_path / "inputs.json"
    packet.write_text(json.dumps({"records": records(), "events": [event()]}))
    code = ('import json, sys; from pathlib import Path; import audit_ledger as L; '
            'p=json.loads(Path(sys.argv[1]).read_text()); '
            'print(len(L.append_impacts(Path(sys.argv[2]), L.ImpactGraph(p["records"]), p["events"])))')
    env = {**os.environ, "PYTHONPATH": str(Path(L.__file__).parent)}
    processes = [subprocess.Popen([sys.executable, "-c", code, str(packet), str(path)], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    outputs = [p.communicate(timeout=20) for p in processes]
    assert all(p.returncode == 0 for p in processes), outputs
    counts = [int(stdout) for stdout, _ in outputs]
    rows = list(read_records(path))
    assert min(counts) == 0 and sum(counts) == len(rows)
    assert len({r["record_id"] for r in rows}) == len(rows)


def test_long_chains_use_iterative_traversal():
    rows = [attempt("a")]
    for i in range(1100):
        rows.append(attempt(str(i), prev_attempt_id=rows[-1]["attempt_id"]))
    graph = L.ImpactGraph(rows)
    assert len(graph.descendants(("attempt", "a"))) == 1101
    assert graph.attempt_audit_view([event()])[-1]["audit_flags"] == ["leak_confirmed"]


def test_complete_json_without_trailing_newline_is_not_appended_to(tmp_path):
    path = tmp_path / "impact.jsonl"
    L.append_impacts(path, L.ImpactGraph(records()), [event()])
    torn = path.read_bytes()[:-1]
    path.write_bytes(torn)
    with pytest.raises(L.LedgerError, match="unterminated"):
        L.append_impacts(path, L.ImpactGraph(records()), [event()])
    assert path.read_bytes() == torn


def test_cli_replays_explicit_files_without_mutating_them(tmp_path, capsys):
    source, audits, output = [tmp_path / name for name in ("forecasts.jsonl", "audits.jsonl", "impacts.jsonl")]
    for row in records():
        append_record(source, row)
    append_record(audits, event())
    before = (source.read_bytes(), audits.read_bytes())
    argv = ["--records", str(source), "--audits", str(audits), "--output", str(output)]
    assert L.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["impacts"] == 9
    assert L.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["appended"] == 0
    assert before == (source.read_bytes(), audits.read_bytes())
    assert L.main([*argv[:-1], str(source)]) == 1
    assert "alias" in capsys.readouterr().err
    assert before == (source.read_bytes(), audits.read_bytes())


def test_parent_exposure_reaches_child_and_consumers_without_tainting_earlier_inputs():
    graph = L.ImpactGraph(records())
    targets = {r["target"]["id"] for r in impacts(L.proposed_rows(graph, [event(aid="b")]))}
    assert targets == {"b", "child", "c", "agg", "intent", "receipt"}
    view = graph.attempt_audit_view([event(aid="b")])
    assert "leak_confirmed" not in next(r for r in view if r["attempt_id"] == "repair")["audit_flags"]
    assert "leak_confirmed" in next(r for r in view if r["attempt_id"] == "c")["audit_flags"]


def test_unused_child_exposure_does_not_flow_back_to_launcher():
    rows = records()
    rows[4]["input_subagent_ids"] = []  # result was never delivered to the launcher, not merely uncited
    rows[3]["result_delivered"] = False
    affected = impacts(L.proposed_rows(L.ImpactGraph(rows), [event(aid=None, target={"kind": "subagent", "id": "child"})]))
    assert [r["target"]["id"] for r in affected] == ["child"]


@pytest.mark.parametrize("change", [{"parent_attempt_id": None}, {"parent_attempt_id": "missing"}, {"post_id": 77}])
def test_missing_or_incompatible_child_launcher_is_rejected(change):
    rows = records()
    rows[3].update(change)
    with pytest.raises(L.LedgerError):
        L.ImpactGraph(rows)


def test_multiquestion_projection_is_scoped_and_aggregate_lineage_is_relevant_only():
    rows = records()
    graph = L.ImpactGraph(rows)
    audits = [event("aud_q8", aid="unrelated")]
    scoped = graph.attempt_audit_view(audits, question_id=7, question_snapshot_id="s1")
    assert all(r["question_id"] == 7 for r in scoped)
    snapshot = {**IDENTITY, "snapshot_id": "s1", "question_type": "binary"}
    attempts = [r for r in rows if r["type"] == "forecast_attempt"]
    rec = A.build_aggregate(snapshot, [r for r in attempts if r["question_id"] == 7], audit_events=scoped)
    assert rec["status"] == "ready" and "aud_q8" not in rec["audit_event_ids"]
    rec = A.build_aggregate(snapshot, attempts, audit_events=graph.attempt_audit_view(audits))
    assert "aud_q8" not in rec["audit_event_ids"]


def test_creation_flags_keep_audit_and_record_provenance_in_the_view():
    rows = records()
    rows[3].update(tool_violations=[{"tool": "Bash"}], audit_event_id="aud_child")
    view = L.ImpactGraph(rows).attempt_audit_view([])
    b = next(r for r in view if r["attempt_id"] == "b")
    assert "aud_child" in b["source_audit_event_ids"]
    assert {"kind": "subagent", "id": "child"} in b["source_record_refs"]
    # If a derived flag has no separate audit event, retain its actual immutable source record.
    rows[3].pop("audit_event_id")
    b = next(r for r in L.ImpactGraph(rows).attempt_audit_view([]) if r["attempt_id"] == "b")
    assert {"kind": "subagent", "id": "child"} in b["source_record_refs"]


def test_aggregate_retains_audit_events_on_ancestors_of_the_selected_repair():
    rows = [attempt("a", validation_status="schema_failed"), attempt("repair", prev_attempt_id="a")]
    e = event(flags=["suspected_leak"])
    snapshot = {**IDENTITY, "snapshot_id": "s1", "question_type": "binary"}
    rec = A.build_aggregate(snapshot, rows, audit_events=[e])
    assert rec["input_attempt_ids"] == ["repair"] and rec["audit_event_ids"] == ["aud_late"]


@pytest.mark.parametrize("delivered", [None, "true", False])
def test_child_delivery_must_be_explicit_and_consistent(delivered):
    rows = records()
    rows[3]["result_delivered"] = delivered
    with pytest.raises(L.LedgerError):
        L.ImpactGraph(rows)


def test_delivered_child_cannot_be_omitted_from_launcher_inputs():
    rows = records()
    rows[4]["input_subagent_ids"] = []
    with pytest.raises(L.LedgerError, match="delivery must match"):
        L.ImpactGraph(rows)


def test_raw_attempt_target_is_supported_and_child_target_requires_graph_projection():
    snapshot = {**IDENTITY, "snapshot_id": "s1", "question_type": "binary"}
    raw = event(aid=None, target={"kind": "attempt", "id": "a"}, flags=["suspected_leak"])
    result = A.build_aggregate(snapshot, [attempt("a")], audit_events=[raw])
    assert result["audit_event_ids"] == ["aud_late"] and result["audit_flags"] == ["suspected_leak"]
    with pytest.raises(ValueError, match="require ImpactGraph"):
        A.build_aggregate(snapshot, [attempt("a")], audit_events=[event(aid=None, target={"kind": "subagent", "id": "s"})])
    with pytest.raises(ValueError, match="question_id differs"):
        A.build_aggregate(snapshot, [attempt("a")], audit_events=[event(question_id=8)])
