"""M5 workflow tests: synthetic adapters, ledgers and HTTP responses only."""
import copy
import json
from datetime import timedelta

import pytest

import aggregate as A
import comment as C
import continuous as CQ
import forecast as F
import numeric as N
import poll_questions as P
import reconcile as R
import submit as S
import submit_transport as T
import validate as V
from flab.config import load_run_config
from flab.ids import hash_obj
from flab.jsonl import append_record, read_records
from flab.timeutil import to_iso
from test_forecast import FakeAdapter, NOW, env
from test_numeric import api_question, enabled, output, record, snapshot, values
from test_reconcile import admit, audit_for, payload
from test_submit import environment, rows
from test_scheduler import environment as scheduler_environment
from test_api_readback import intent, modern, official_row
from test_m3_w9 import HTTP, transport, no_token_in_any_test_ledger_or_console


@pytest.mark.parametrize("kind", N.TYPES)
def test_initial_and_repair_use_numeric_schema_and_provenance(env, enabled, kind):
    snap = snapshot(kind, n=20)
    broken = output(snap)
    broken.pop("key_factors")
    result, attempts, adapter = env(
        [{"structured": broken}, {"structured": output(snap)}], snap=snap)
    assert result["questions"][0]["outcome"] == "selected"
    assert [r["prompt_version"] for r in attempts] == [N.PROMPT_VERSION, "repair_numeric_v1"]
    assert [r["validation_status"] for r in attempts] == [V.SCHEMA_FAILED, V.VALID]
    for req in adapter.requests:
        assert req.schema == V.question_schema(snap)
    row = attempts[-1]
    assert row["cdf_version"] == N.CDF_VERSION
    assert row["cdf_sha256"] == hash_obj(row["continuous_cdf"])
    assert row["percentiles"] == values(snap)
    assert A.input_error(row, snap) is None
    assert row["probability"] is None and row["option_probabilities"] is None


@pytest.mark.parametrize("kind", N.TYPES)
def test_semantic_failure_has_no_repair_or_rerun_even_with_cli_error(env, enabled, kind):
    snap = snapshot(kind, n=20)
    bad = output(snap)
    bad["percentiles"]["p50"] = bad["percentiles"]["p25"]
    result, attempts, adapter = env([{"structured": bad, "error_type": "cli_error"}], snap=snap)
    assert result["questions"][0]["selected_attempt_id"] is None
    assert len(attempts) == len(adapter.requests) == 1
    assert attempts[0]["validation_status"] == V.SEMANTIC_FAILED
    assert attempts[0]["repair_basis"] is None and attempts[0]["continuous_cdf"] is None


def test_numeric_repair_preserves_values_and_stops_on_changed_number(env, enabled):
    snap = snapshot()
    broken, repaired = output(snap), output(snap)
    broken.pop("key_factors")
    repaired["percentiles"]["p50"] += 1
    result, attempts, adapter = env(
        [{"structured": broken}, {"structured": repaired}], snap=snap)
    assert len(adapter.requests) == 2
    assert result["questions"][0]["outcome"] == V.SEMANTIC_FAILED
    assert "repair changed percentiles" in attempts[-1]["validation_errors"]
    assert attempts[-1]["continuous_cdf"] is None


def test_final_cdf_validation_failure_is_invalid_output_without_retry(env, enabled, monkeypatch):
    snap = snapshot()
    obj = output(snap)
    def invalid(*_):
        raise N.InvalidCDF("synthetic server step validation failure")
    monkeypatch.setattr(N, "to_cdf", invalid)
    result, attempts, adapter = env([{"structured": obj}], snap=snap)
    assert len(attempts) == len(adapter.requests) == 1
    assert attempts[0]["validation_status"] == V.SEMANTIC_FAILED
    assert attempts[0]["error_type"] == result["questions"][0]["outcome"] == "invalid_output"
    assert attempts[0]["repair_basis"] is None
    assert attempts[0]["continuous_cdf"] is None


def test_late_numeric_values_are_kept_separately(env, enabled):
    snap = snapshot(scheduled_close_time=to_iso(NOW + timedelta(hours=1)))
    result, attempts, _ = env([{"structured": output(snap)}], snap=snap,
                             after=NOW + timedelta(minutes=50))
    row = attempts[0]
    assert result["questions"][0]["outcome"] == V.LATE
    assert row["continuous_cdf"] is None and row["percentiles"] is None
    assert row["late_values"]["percentiles"] == values(snap)
    assert row["late_values"]["continuous_cdf"] == N.to_cdf(values(snap), snap)


@pytest.mark.parametrize("kind", N.TYPES)
@pytest.mark.parametrize("changed", [False, True])
def test_tier2_anonymity_comparison_and_comment(tmp_path, monkeypatch, enabled, kind, changed):
    monkeypatch.setattr(R, "load_submit_mode", lambda: {"mode": "local"})
    snap = snapshot(kind, n=20)
    initial = [record(snap, p, audit_event_id=f"aud_{p}_1", attempt_kind="initial",
                      finished_at_utc=to_iso(NOW - timedelta(minutes=10)))
               for p in ("claude", "gpt")]
    initial[0]["escalation_flags"] = ["evidence_conflict"]
    audits = [audit_for(r) for r in initial]
    bundle = R.prepare(snap, initial, audits, player="claude", cfg=load_run_config(),
                       now=NOW, shuffle=lambda seq: seq.reverse())
    data = payload(bundle)
    assert bundle["prompt_version"] == N.RECONCILE_VERSION
    assert bundle["tier1_reference"]["percentiles"] == values(snap)
    assert set(data["summaries"][0]) == {"label", *R.SUMMARY_FIELDS, "percentiles"}
    assert "claude" not in bundle["prompt"] and "gpt" not in bundle["prompt"]
    assert "scaling" in data["question"]
    out = output(snap, change_reason="Synthetic evidence supports these quantiles.")
    if changed:
        out["percentiles"]["p50"] += .1
    ctx = F.Context(player="claude", adapter=FakeAdapter([{"structured": out}]),
                    cfg=load_run_config(), run_id="numeric-test",
                    preflight=F.Preflight("claude", tier=2), now=lambda: NOW,
                    forecasts_log=tmp_path / "forecasts.jsonl", audits_log=tmp_path / "audits.jsonl",
                    quota_log=tmp_path / "quota.jsonl", line_status_log=tmp_path / "line_status.jsonl",
                    raw_root=tmp_path / "raw", make_dir=lambda: tmp_path, remove_dir=lambda _: None)
    for r, aud in zip(initial, audits):
        append_record(ctx.forecasts_log, r)
        append_record(ctx.audits_log, aud)
    result = R.run(ctx, snap, admit=admit)
    assert result["outcome"] == "selected"
    attempts = read_records(ctx.forecasts_log)
    tier2 = attempts[-1]
    assert tier2["changed_from_tier1"] is changed
    assert tier2["comparison_method"] == "percentile_equality_v1"
    assert tier2["prompt_version"] == N.RECONCILE_VERSION
    agg = A.build_aggregate(snap, attempts, stage="final")
    comment = C.build(snap, agg, [*attempts, agg], read_records(ctx.audits_log))
    assert "Median" in comment["text"] and "widgets" in comment["text"]
    assert "arithmetic mean of CDFs" in comment["text"] and len(comment["text"]) <= C.MAX_CHARS
    assert R.run(ctx, snap, admit=admit)["outcome"] == "already_selected"
    assert len(ctx.adapter.requests) == 1


def test_comment_quantiles_come_from_aggregate_cdf_and_sanitize_units():
    snap = snapshot(open_lower_bound=True, open_upper_bound=True,
                    unit="<script>unsafe()</script>widgets")
    a = record(snap, us=[-.4, -.2, .2, .5, .8, 1.2, 1.4])
    b = record(snap, "gpt", us=[-.3, -.1, .1, .4, .7, 1.1, 1.3])
    agg = A.build_aggregate(snap, [a, b])
    text = C.build(snap, agg, [a, b, agg], [])["text"]
    assert "below lower bound" in text and "above upper bound" in text
    assert "unsafe" not in text and "widgets" in text
    assert C.escape(N.quantile_label(agg["continuous_cdf"], .5, snap)) in text
    bad = {**agg, "continuous_cdf": a["continuous_cdf"]}
    with pytest.raises(C.CommentError, match="do not match"):
        C.build(snap, bad, [a, b, bad], [])
    log = snapshot(range_min=1., range_max=100., zero_point=0.)
    assert N.quantile_label([i / 200 for i in range(201)], .5, log) == "10"


def seed_numeric(e, kind="numeric"):
    fields = snapshot(kind, n=20)
    question = e["post"]["question"]
    question.update(api_question({**fields, "question_id": question["id"]}))
    snap = {**P.snapshot_from_post(e["post"], "bot-testing-area"),
            "tournament_id": 32977, "snapshot_id": "s1"}
    (e["config"] / "question_types.json").write_text('{"numeric":true,"discrete":true}')
    row = record(snap)
    agg = A.build_aggregate(snap, [row], stage="final")
    agg.update(submission_eligible=True, scheduler_campaign_id="synthetic",
               completion_status="complete_local")
    comment = C.build(snap, agg, [row, agg], [])
    for name, rec in (("questions", snap), ("forecasts", row), ("aggregates", agg), ("comments", comment)):
        append_record(e["root"] / f"{name}.jsonl", rec)
    return snap, agg


@pytest.mark.parametrize("kind", N.TYPES)
def test_submission_payload_private_comment_and_no_duplicate(environment, kind):
    e = environment
    snap, agg = seed_numeric(e, kind)
    e["make"]().run(verify_accepted=True)
    assert rows(e)[-1]["status"] == "comment_ok"
    writes = e["transport"].writes
    assert writes[0][1] == [{"question": snap["question_id"], "continuous_cdf": agg["continuous_cdf"]}]
    assert writes[1][1]["is_private"] is True and writes[1][1]["included_forecast"] is True
    e["make"]().run()
    assert len(writes) == 2


@pytest.mark.parametrize("field", N.SCALING_FIELDS)
def test_precheck_blocks_every_scaling_revision_without_text_fallback(environment, field):
    e = environment
    seed_numeric(e)
    question = e["post"]["question"]
    source = question if field == "unit" else question["scaling"]
    original = source.get(field)
    source[field] = not original if isinstance(original, bool) else "changed"
    e["make"]().run()
    assert not e["transport"].writes
    assert any("scaling_changed" in str(row) for row in rows(e))


def test_precheck_blocks_even_valid_grid_revision(environment):
    e = environment
    snap, _ = seed_numeric(e)
    e["post"]["question"]["scaling"]["continuous_range"][10] += 1e-12
    revised = P.snapshot_from_post(e["post"], "bot-testing-area")
    assert revised["scaling_status"] == "valid"
    assert revised["content_hash"] != snap["content_hash"]
    assert N.scaling_changed(snap, revised)
    e["make"]().run()
    assert not e["transport"].writes
    assert any("scaling_changed" in str(row) for row in rows(e))


def test_switch_off_blocks_existing_submission_intent(environment):
    e = environment
    seed_numeric(e)
    # Retain a pending intent before any HTTP request.
    (e["config"] / "STOP_SUBMIT").touch()
    e["make"]().run()
    (e["config"] / "question_types.json").write_text('{"numeric":false,"discrete":false}')
    (e["config"] / "STOP_SUBMIT").unlink()
    e["make"]().run()
    assert not e["transport"].writes
    assert "unsupported_type" in str(rows(e))


@pytest.mark.parametrize("kind", N.TYPES)
def test_readback_tolerance_identity_unknown_and_reconcile_gate(monkeypatch, kind):
    from test_submit import NOW as SUBMIT_NOW
    monkeypatch.setattr(T, "utc_now", lambda: SUBMIT_NOW + timedelta(seconds=10))
    http = HTTP()
    snap = snapshot(kind, n=20)
    q = http.post["question"]
    q.update(api_question({**snap, "question_id": q["id"]}))
    cdf = N.to_cdf(values(snap), snap)
    expected = intent()
    expected["forecast_payload"] = [{"question": 7, "continuous_cdf": cdf}]
    modern(http, official_row(forecast_values=cdf))
    client = transport(http)
    assert client.can_reconcile_forecast(http.post)
    assert client.readback("forecast", expected, request_id="test").outcome == "found"
    for delta, found in [(4e-10, True), (6e-10, False)]:
        perturbed = list(cdf)
        perturbed[10] += delta
        modern(http, official_row(forecast_values=perturbed))
        assert client.readback("forecast", expected, request_id="test").outcome == ("found" if found else "unknown")
    for change in ({"author_id": 99}, {"question_id": 99}, {"start_time": SUBMIT_NOW.timestamp()},
                   {"forecast_values": cdf[:-1]}, {"forecast_values": [True, *cdf[1:]]},
                   {"forecast_values": [float("nan"), *cdf[1:]]}):
        modern(http, official_row(**{"forecast_values": cdf, **change}))
        assert client.readback("forecast", expected, request_id="test").outcome == "unknown"
    modern(http, official_row(forecast_values=cdf))
    expected["request_forecast_baseline"] = [T.forecast_identity(q["my_forecasts"]["latest"])]
    assert client.readback("forecast", expected, request_id="test").outcome == "unknown"
    del q["unit"]
    assert not client.can_reconcile_forecast(http.post)


@pytest.mark.parametrize("kind", N.TYPES)
def test_unknown_numeric_http_response_restarts_without_resending(environment, monkeypatch, kind):
    from test_submit import NOW as SUBMIT_NOW
    monkeypatch.setattr(T, "utc_now", lambda: SUBMIT_NOW + timedelta(seconds=10))
    e = environment
    _, agg = seed_numeric(e, kind)
    http = HTTP(e["post"])
    http.post["question"]["my_forecasts"] = {"history": [], "latest": None}
    http.effects = [{}]
    def submitter():
        return S.Submitter(e["root"], config_dir=e["config"], transport=transport(http),
                           now=lambda: SUBMIT_NOW)
    submitter().run()
    submitter().run()
    assert rows(e)[-1]["status"] == "unknown" and len(http.writes()) == 1
    modern(http, official_row(forecast_values=agg["continuous_cdf"]))
    submitter().run()
    assert rows(e)[-1]["status"] == "comment_ok" and len(http.writes()) == 2
    assert len([r for r in http.writes() if r.full_url.endswith(T.FORECAST_PATH)]) == 1


def test_queue_switch_reactivates_old_snapshot_and_scaling_change_is_structural(tmp_path, enabled):
    snap = snapshot(supported=False, ground="bot_testing_area", priority="main")
    append_record(tmp_path / "questions.jsonl", snap)
    queue = CQ.Queue(tmp_path, {}, lambda: {}, now=lambda: NOW)
    enabled.write_text('{"numeric":false,"discrete":false}')
    assert queue.update({snap["question_id"]: snap}) == {}
    enabled.write_text('{"numeric":true,"discrete":true}')
    assert snap["question_id"] in queue.update({snap["question_id"]: snap})
    revised = {**snap, "snapshot_id": "revised", "unit": "new unit"}
    append_record(tmp_path / "questions.jsonl", revised)
    queue.now = lambda: F.decision_deadline(revised, load_run_config()) - timedelta(seconds=1)
    assert queue.update({snap["question_id"]: revised}) == {}
    assert read_records(tmp_path / "question_queue.jsonl")[-1]["action"] == "blocked_revision"
    assert not queue.fallbacks


@pytest.mark.parametrize("kind", N.TYPES)
@pytest.mark.parametrize("invalid", ["missing_range", "missing_grid", "scaling_error", "expired"])
def test_queue_switch_does_not_reactivate_ineligible_old_snapshot(tmp_path, enabled, kind, invalid):
    snap = snapshot(kind, supported=False, ground="bot_testing_area", priority="main")
    if invalid == "missing_range":
        del snap["range_min"]
    elif invalid == "missing_grid":
        del snap["continuous_range"]
    elif invalid == "scaling_error":
        snap["scaling_error"] = "unsupported old scaling"
    else:
        snap["scheduled_close_time"] = to_iso(NOW - timedelta(hours=1))
    append_record(tmp_path / "questions.jsonl", snap)
    queue = CQ.Queue(tmp_path, {}, lambda: {}, now=lambda: NOW)
    enabled.write_text('{"numeric":false,"discrete":false}')
    assert queue.update({snap["question_id"]: snap}) == {}
    enabled.write_text('{"numeric":true,"discrete":true}')
    assert F.eligible(snap, load_run_config(), NOW) is not None
    assert queue.update({snap["question_id"]: snap}) == {}
    events = read_records(tmp_path / "question_queue.jsonl")
    assert all(row["action"] != "queued" for row in events)
    assert events[-1]["bound_snapshot_id"] is None


def test_scheduler_reloads_switch_without_new_snapshot(scheduler_environment, enabled):
    scheduler, snaps, tracker, _ = scheduler_environment(count=1)
    snaps[100].update(snapshot(question_id=100, post_id=100, supported=False))
    enabled.write_text('{"numeric":false,"discrete":false}')
    assert scheduler.run()["jobs_completed"] == 0 and not tracker["calls"]
    enabled.write_text('{"numeric":true,"discrete":true}')
    # A scripted in-memory adapter verifies the real scheduler/schema/aggregate path.
    adapters = {p: FakeAdapter([{"structured": output(snaps[100])}])
                for p in ("claude", "gemini", "gpt")}
    scheduler.loader = adapters.__getitem__
    result = scheduler.run()
    assert result["jobs_completed"] == 3
    assert all(len(a.requests) == 1 for a in adapters.values())
    assert read_records(scheduler.root / "comments.jsonl")


@pytest.mark.parametrize("active", [False, True])
def test_scheduler_ignores_old_numeric_snapshot_without_scaling(scheduler_environment, enabled, active):
    scheduler, snaps, tracker, _ = scheduler_environment(count=1)
    snaps[100].update(question_type="numeric", supported=False)
    enabled.write_text(json.dumps({"numeric": active, "discrete": active}))
    assert scheduler.run()["jobs_completed"] == 0
    assert not tracker["calls"]
    assert not (scheduler.root / "aggregates.jsonl").exists()
