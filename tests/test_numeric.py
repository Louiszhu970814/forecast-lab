"""M5 synthetic numerical properties and compatibility; no external data or services."""
import copy
import hashlib
import itertools
import json
import math
import random
from pathlib import Path

import pytest

import aggregate as A
import escalate as E
import forecast as F
import numeric as N
import poll_questions as P
import question_types as Q
import submit as S
import validate as V
from flab.config import load_run_config
from flab.ids import hash_obj
from flab.jsonl import read_records
from test_aggregate import answer
from test_forecast import SNAP, NOW, _good

ROOT = Path(__file__).resolve().parents[1]


def snapshot(kind="numeric", n=200, **kw):
    fields = dict(range_min=0., range_max=100., zero_point=None, inbound_outcome_count=n,
                  open_lower_bound=False, open_upper_bound=False, unit="widgets",
                  nominal_min=0., nominal_max=100.)
    if kind == "discrete":
        fields.update(range_min=-.5, range_max=n - .5, nominal_min=0., nominal_max=n - 1.)
    fields.update(kw)
    lo, hi, zero = (fields[k] for k in ("range_min", "range_max", "zero_point"))
    count = fields["inbound_outcome_count"]
    count = 200 if count is None else count
    try:
        # Independent API-formula fixture; do not use the converter to certify itself.
        grid = [lo + (hi - lo) * i / count if zero is None else
                zero + (lo - zero) * ((hi - zero) / (lo - zero)) ** (i / count)
                for i in range(count + 1)]
    except (TypeError, ValueError, OverflowError, ZeroDivisionError):
        grid = []  # Deliberately invalid scaling cases still reach the validator.
    fields.setdefault("continuous_range", grid)
    return {**SNAP, "question_type": kind, **fields}


def api_question(snap):
    """Synthetic response in the field layout recorded by specification §14."""
    return {
        "id": snap["question_id"], "type": snap["question_type"], "unit": snap["unit"],
        "range_min": None, "range_max": None, "zero_point": None,
        **{k: snap[k] for k in N.MIRRORED_FIELDS},
        "scaling": {k: copy.deepcopy(snap[k]) for k in N.SCALING_FIELDS
                    if k != "unit" and k in snap},
    }


def values(snap, us=(.05, .1, .25, .5, .75, .9, .95)):
    scale = N.scaling(snap)
    return dict(zip(N.KEYS, (scale.from_u(u) for u in us)))


def output(snap, us=(.05, .1, .25, .5, .75, .9, .95), **kw):
    return _good(question_id=snap["question_id"], question_type=snap["question_type"],
                 probability=None, percentiles=values(snap, us), **kw)


def record(snap, player="claude", us=(.05, .1, .25, .5, .75, .9, .95), **kw):
    return {**answer(player, snapshot=snap), **output(snap, us), **N.cdf_fields(values(snap, us), snap), **kw}


@pytest.fixture
def enabled(tmp_path, monkeypatch):
    path = tmp_path / "question_types.json"
    path.write_text('{"numeric":true,"discrete":true}')
    monkeypatch.setattr(Q, "CONFIG_PATH", path)
    return path


@pytest.mark.parametrize("n", [2, 10, 20, 50, 200, 1000])
@pytest.mark.parametrize("bounds", list(itertools.product([False, True], repeat=2)))
@pytest.mark.parametrize("zero", [None, -10., 110.])
def test_randomized_cdf_properties_and_projection_idempotence(n, bounds, zero):
    rng = random.Random(20261103)
    snap = snapshot(n=n, zero_point=zero, open_lower_bound=bounds[0], open_upper_bound=bounds[1])
    for _ in range(20):
        us = sorted(rng.uniform(-.8 if bounds[0] else .0001, 1.8 if bounds[1] else .9999) for _ in N.KEYS)
        cdf = N.to_cdf(values(snap, us), snap)
        assert N.validate_cdf(cdf, snap) == cdf
        assert N.project_cdf(cdf, snap) == cdf
        # Independent assertions of published server limits, not only converter self-checks.
        assert len(cdf) == n + 1
        assert all(round(.01 / n, 9) <= round(b - a, 9) <= 40 / n for a, b in zip(cdf, cdf[1:]))
        assert cdf[0] >= .001 if bounds[0] else cdf[0] == 0
        assert cdf[-1] <= .999 if bounds[1] else cdf[-1] == 1
        assert all(math.isfinite(v) and 0 <= v <= 1 for v in cdf)


@pytest.mark.parametrize("center", [.0001, .5, .9998, -.5, 1.5])
def test_extreme_mass_and_narrow_intervals(center):
    snap = snapshot(open_lower_bound=True, open_upper_bound=True)
    cdf = N.to_cdf(values(snap, [center + i * 1e-6 for i in range(7)]), snap)
    N.validate_cdf(cdf, snap)
    assert cdf[-1] - cdf[0] >= .011


def test_linear_uniform_log_inverse_and_nonfixed_open_anchors():
    snap = snapshot()
    assert N.to_cdf(values(snap), snap) == [round(i / 200, 10) for i in range(201)]
    for zero in [-10., 110.]:
        s = N.scaling(snapshot(zero_point=zero))
        for u in [-1., 0., .3, .8, 1., 2.]:
            assert s.to_u(s.from_u(u)) == pytest.approx(u)
    snap = snapshot(open_lower_bound=True, open_upper_bound=True)
    cdf = N.to_cdf(values(snap, [.01, .1, .25, .5, .75, .9, .99]), snap)
    assert cdf[0] == .025 and cdf[-1] == .975
    cdf = N.to_cdf(values(snap), snap)
    assert cdf[0] == .001 and cdf[-1] == .999
    cdf = N.to_cdf(values(snap, [-.4, -.2, .2, .5, .8, 1.2, 1.4]), snap)
    assert cdf[0] > .1 and cdf[-1] < .9


@pytest.mark.parametrize("change", [
    {"range_min": None}, {"range_max": float("inf")}, {"range_min": 100},
    {"zero_point": 0}, {"zero_point": 50}, {"zero_point": float("nan")},
    {"inbound_outcome_count": 1}, {"inbound_outcome_count": 1001},
    {"inbound_outcome_count": True}, {"inbound_outcome_count": 20.0},
    {"open_lower_bound": 1}, {"unit": []},
])
def test_unsupported_scaling(change):
    with pytest.raises(N.UnsupportedScaling):
        N.scaling(snapshot(**change))


def test_missing_fields_are_not_silent_defaults_but_explicit_nulls_are():
    for key in N.REQUIRED_FIELDS:
        snap = snapshot()
        del snap[key]
        with pytest.raises(N.UnsupportedScaling, match="missing"):
            N.scaling(snap)
    s = N.scaling(snapshot(inbound_outcome_count=None, open_lower_bound=None, open_upper_bound=None, unit=None))
    assert s.n == 200 and not s.open_lower and not s.open_upper


def test_unrepresentable_json_integers_fail_validation_without_crashing():
    huge = 10 ** 400
    for key in ("range_min", "range_max", "zero_point"):
        with pytest.raises(N.UnsupportedScaling):
            N.scaling(snapshot(**{key: huge}))
    snap = snapshot()
    obj = output(snap)
    obj["percentiles"]["p95"] = huge
    assert V.validate_output(obj, snap, V.question_schema(snap)).status == V.SEMANTIC_FAILED
    assert V.repair_basis(json.dumps(obj), snap) is None
    with pytest.raises(ValueError):
        N.validate_cdf([0, huge, *[i / 200 for i in range(2, 201)]], snap)
    with pytest.raises(N.UnsupportedScaling):
        N.scaling(snapshot(range_min=-(10 ** 308), range_max=10 ** 308))


def test_discrete_geometry_tolerance_uses_bin_width_not_absolute_coordinate():
    snap = snapshot("discrete", n=20, range_min=1e12 - .5, range_max=1e12 + 19.5,
                    nominal_min=1e12, nominal_max=1e12 + 19)
    assert N.scaling(snap).n == 20
    # Relative tolerance at this offset must not admit a ten-bin geometry error.
    with pytest.raises(N.UnsupportedScaling):
        N.scaling({**snap, "range_min": snap["range_min"] - 10})


@pytest.mark.parametrize("kind", N.TYPES)
def test_confirmed_api_layout_and_revision_fields(tmp_path, kind):
    base = snapshot(kind, n=20)
    question = api_question(base)
    post = {"id": base["post_id"], "title": "Synthetic scaling", "question": question}
    nested = P.snapshot_from_post(post, "synthetic")
    assert nested["scaling_status"] == "valid"
    assert all(nested[k] == base[k] for k in N.SCALING_FIELDS)
    path = tmp_path / "questions.jsonl"
    P.record_snapshots([nested], log=path)
    for key in N.SCALING_FIELDS:
        changed = copy.deepcopy(nested)
        changed[key] = "synthetic change"
        changed["content_hash"] = hash_obj(changed)
        P.record_snapshots([changed], log=path)
        assert list(read_records(path))[-1]["revision"]["scaling_changed"]
    question["range_min"] = -999
    question["range_max"] = 999
    question["zero_point"] = 0
    question["nominal_min"] = -999
    question["nominal_max"] = 999
    question["continuous_range"] = []
    question["scaling"]["unit"] = "ignored nested unit"
    assert P.snapshot_from_post(post, "synthetic") == nested


@pytest.mark.parametrize("field", [k for k in N.SCALING_FIELDS if k != "unit"])
def test_top_level_values_never_replace_missing_scaling_fields(field):
    question = api_question(snapshot("discrete", n=20))
    question[field] = question["scaling"].pop(field)
    post = {"id": 1, "title": "Synthetic scaling", "question": question}
    assert P.snapshot_from_post(post, "synthetic")["scaling_status"] == "unsupported_scaling"


@pytest.mark.parametrize("nested", [None, {}, [], "invalid"])
def test_flat_scaling_is_rejected(nested):
    snap = snapshot()
    question = {"id": 1, "type": "numeric", **{k: snap[k] for k in N.SCALING_FIELDS},
                "scaling": nested}
    post = {"id": 1, "question": question}
    rejected = P.snapshot_from_post(post, "synthetic")
    assert rejected["scaling_status"] == "unsupported_scaling"
    json.dumps(rejected, allow_nan=False)


@pytest.mark.parametrize("field", N.MIRRORED_FIELDS)
def test_only_nonnull_top_level_mirrors_are_checked(field):
    question = api_question(snapshot())
    expected = N.read_scaling_fields(question)
    question[field] = None
    assert N.read_scaling_fields(question) == expected
    del question[field]
    assert N.read_scaling_fields(question) == expected
    question[field] = 20 if field == "inbound_outcome_count" else True
    post = {"id": 1, "question": question}
    assert P.snapshot_from_post(post, "synthetic")["scaling_status"] == "unsupported_scaling"


def test_unit_must_be_top_level():
    question = api_question(snapshot())
    question["scaling"]["unit"] = question.pop("unit")
    post = {"id": 1, "question": question}
    assert P.snapshot_from_post(post, "synthetic")["scaling_status"] == "unsupported_scaling"


@pytest.mark.parametrize("mutation", ["missing", "null", "object", "short", "long",
                                    "nan", "inf", "bool", "huge", "interior", "endpoint"])
def test_continuous_range_invalid_or_inconsistent_is_unsupported(mutation):
    question = api_question(snapshot())
    fields = question["scaling"]
    grid = fields["continuous_range"]
    if mutation == "missing":
        del fields["continuous_range"]
    elif mutation in ("null", "object"):
        fields["continuous_range"] = None if mutation == "null" else {}
    elif mutation == "short":
        grid.pop()
    elif mutation == "long":
        grid.append(101)
    else:
        index = -1 if mutation == "endpoint" else 100
        grid[index] = {"nan": math.nan, "inf": math.inf, "bool": True, "huge": 10**400,
                       "interior": 50.01, "endpoint": 100.01}[mutation]
    post = {"id": 1, "question": question}
    rejected = P.snapshot_from_post(post, "synthetic")
    assert rejected["scaling_status"] == "unsupported_scaling"
    json.dumps(rejected, allow_nan=False)


def test_grid_tolerates_float_roundoff_but_tracks_even_small_revisions():
    old = snapshot(range_min=-35., range_max=0.)
    new = copy.deepcopy(old)
    new["continuous_range"][100] += 1e-12
    N.scaling(new)
    assert N.scaling_changed(old, new)
    new["continuous_range"][100] += 1e-5
    with pytest.raises(N.UnsupportedScaling, match="continuous_range"):
        N.scaling(new)


REAL_SCALINGS = json.loads((ROOT / "tests/fixtures/m5_real_scaling.json").read_text())


@pytest.mark.parametrize("question", REAL_SCALINGS, ids=lambda q: str(q["id"]))
def test_recorded_scalings_with_synthetic_percentiles_pass_cdf_validator(question):
    # Only supplied numeric metadata is used; percentiles are synthetic u positions.
    snap = P.snapshot_from_post({"id": 1, "question": question}, "synthetic")
    assert snap["scaling_status"] == "valid"
    scale = N.scaling(snap)
    assert snap["continuous_range"] == question["scaling"]["continuous_range"]
    assert [scale.from_u(i / scale.n) for i in range(scale.n + 1)] == pytest.approx(
        snap["continuous_range"], rel=1e-12, abs=1e-10)
    if question["type"] == "discrete":
        step = (snap["nominal_max"] - snap["nominal_min"]) / (scale.n - 1)
        assert snap["continuous_range"][0] == pytest.approx(snap["nominal_min"] - step / 2)
        assert snap["continuous_range"][-1] == pytest.approx(snap["nominal_max"] + step / 2)
        for i, (a, b) in enumerate(zip(snap["continuous_range"], snap["continuous_range"][1:])):
            assert (a + b) / 2 == pytest.approx(snap["nominal_min"] + i * step)
    cdf = N.to_cdf(values(snap), snap)
    assert N.validate_cdf(cdf, snap) == cdf
    assert len(cdf) == scale.n + 1
    assert all(round(.01 / scale.n, 9) <= round(b - a, 9) <= 40 / scale.n
               for a, b in zip(cdf, cdf[1:]))


@pytest.mark.parametrize("us", [
    [-.9, -.8, -.7, -.6, -.5, -.4, -.3],
    [1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9],
    [-.6, -.5, -.4, -.3, -.2, -.1, 0.],
    [1., 1.1, 1.2, 1.3, 1.4, 1.5, 1.6],
])
def test_same_side_open_tail_anchors_are_monotone_before_projection(monkeypatch, us):
    snap = snapshot(open_lower_bound=True, open_upper_bound=True)
    project = N.project_cdf
    def checked(cdf, snap):
        assert all(a <= b for a, b in zip(cdf, cdf[1:]))
        return project(cdf, snap)
    monkeypatch.setattr(N, "project_cdf", checked)
    cdf = N.to_cdf(values(snap, us), snap)
    assert N.validate_cdf(cdf, snap) == cdf
    assert cdf[0] > .9 if us[-1] <= 0 else cdf[-1] < .1


def test_discrete_geometry_prompt_and_date_exclusion():
    snap = snapshot("discrete", n=10)
    prompt = F.forecast_prompt(snap, "2026-11-03")
    assert "Nominal outcomes: 0 to 9; step 1" in prompt
    assert "-0.5" not in prompt and "9.5" not in prompt
    assert len(N.to_cdf(values(snap), snap)) == 11
    for change in ({"nominal_min": None}, {"range_min": 0}, {"zero_point": -10}):
        with pytest.raises(N.UnsupportedScaling):
            N.scaling({**snap, **change})
    assert not Q.is_supported("date")
    with pytest.raises(ValueError):
        V.question_schema(snapshot("date"))


@pytest.mark.parametrize("mutation", ["nan", "bool", "equal", "reversed", "narrow", "closed_tail", "open_tail", "log_domain", "clamped_collision"])
def test_numeric_semantic_errors_are_not_repairable(mutation):
    snap = snapshot()
    obj = output(snap)
    p = obj["percentiles"]
    if mutation == "nan":
        p["p05"] = math.nan
    elif mutation == "bool":
        p["p05"] = True
    elif mutation == "equal":
        p["p05"] = p["p10"]
    elif mutation == "reversed":
        p["p05"] = 99
    elif mutation == "narrow":
        p["p10"] = p["p05"] + 1e-10
    elif mutation == "closed_tail":
        p["p05"] = -2.01
    elif mutation == "open_tail":
        snap["open_lower_bound"] = True
        p["p05"] = -101
    elif mutation == "log_domain":
        snap = snapshot(zero_point=-10)
        p["p05"] = -11
    else:
        p["p05"], p["p10"] = -1., 0.
    outcome = V.validate_output(obj, snap, V.question_schema(snap))
    assert outcome.status == V.SEMANTIC_FAILED and outcome.status not in V.REPAIRABLE
    assert V.repair_basis(json.dumps(obj), snap) is None


def test_closed_tolerance_clamps_with_margin_and_preserves_order():
    snap = snapshot()
    obj = output(snap)
    obj["percentiles"].update(p05=-1., p95=101.)
    out = V.validate_output(obj, snap, V.question_schema(snap))
    assert out.status == V.VALID
    assert 0 < out.percentiles["p05"] < out.percentiles["p10"]
    assert out.percentiles["p90"] < out.percentiles["p95"] < 100


def test_clamping_cannot_round_back_to_a_closed_endpoint():
    snap = snapshot(range_min=1e12, range_max=1e12 + 100)
    obj = output(snap)
    obj["percentiles"]["p05"] = 1e12 - 1
    outcome = V.validate_output(obj, snap, V.question_schema(snap))
    assert outcome.status == V.SEMANTIC_FAILED
    assert V.repair_basis(json.dumps(obj), snap) is None


def test_strict_schema_and_format_repair_cannot_change_numbers_or_sources():
    snap = snapshot()
    schema = V.question_schema(snap)
    percentiles = schema["properties"]["percentiles"]
    assert percentiles["required"] == list(N.KEYS) and percentiles["additionalProperties"] is False
    obj = output(snap)
    broken = {**obj, "extra": "format"}
    assert V.validate_output(broken, snap, schema).status == V.SCHEMA_FAILED
    text = json.dumps(broken)
    basis = V.repair_basis(text, snap)
    assert not V.repair_errors(obj, basis, text)
    changed = copy.deepcopy(obj)
    changed["percentiles"]["p50"] += 1e-10
    assert "repair changed percentiles" in V.repair_errors(changed, basis, text)
    changed = {**obj, "sources": []}
    assert V.repair_errors(changed, basis, text)
    assert V.repair_basis(text + json.dumps({**obj, "percentiles": values(snap, [.1,.2,.3,.4,.5,.6,.7])}), snap) is None
    obj["change_reason"] = "Synthetic evidence."
    basis = V.repair_basis(json.dumps(obj), snap, tier=2)
    assert basis["change_reason"] == obj["change_reason"]
    assert V.repair_errors({**obj, "change_reason": "Invented."}, basis, json.dumps(obj))


def test_validator_checks_rounding_finiteness_endpoints_and_step_limits():
    snap = snapshot()
    cdf = N.to_cdf(values(snap), snap)
    for bad in (cdf[:-1], [math.nan, *cdf[1:]], [True, *cdf[1:]], [.001, *cdf[1:]],
                [*cdf[:-1], .999], [cdf[0], cdf[0], *cdf[2:]], [0, .21, *cdf[2:]]):
        with pytest.raises(ValueError):
            N.validate_cdf(bad, snap)
    cdf[1] = .0000499996  # server rounds delta to .000050000
    assert N.validate_cdf(cdf, snap)[1] == .0000499996
    cdf[1] = .0000499994
    with pytest.raises(ValueError):
        N.validate_cdf(cdf, snap)


def test_aggregation_equal_mean_no_repeated_smoothing_and_exclusions():
    snap = snapshot()
    a = record(snap)
    b = record(snap, "gpt", [.1,.2,.3,.4,.5,.6,.7])
    agg = A.build_aggregate(snap, [a, b])
    mean = [round((x+y)/2, 10) for x, y in zip(a["continuous_cdf"], b["continuous_cdf"])]
    assert agg["continuous_cdf"] == mean and agg["aggregation_version"] == N.AGG_VERSION
    assert agg["cdf_sha256"] == hash_obj(mean)
    assert A.build_aggregate(snap, [a])["continuous_cdf"] == a["continuous_cdf"]
    for bad in ({"audit_flags": ["leak_confirmed"]}, {"model_check": "mismatch"}, {"cdf_sha256": "wrong"},
                {"cdf_version": "numeric_cdf_v1"},
                {"continuous_cdf": [0,1]}, {"percentiles": values(snap, [.1,.2,.3,.4,.5,.6,.7])}):
        result = A.build_aggregate(snap, [{**a, **bad}, b])
        assert result["input_attempt_ids"] == [b["attempt_id"]]
    assert A.build_aggregate(snap, [])["status"] == "no_valid_inputs"
    assert S.forecast_payload(snap, agg) == [{"question": snap["question_id"], "continuous_cdf": mean}]


def test_numeric_escalation_floor_nonoverlap_flags_and_no_logit(monkeypatch):
    snap = snapshot(zero_point=-10)
    monkeypatch.setattr(E, "logit", lambda _: pytest.fail("numeric must never call logit"))
    a = record(snap, us=[.49,.495,.499,.5,.501,.505,.51])
    b = record(snap, "gpt", [.50,.505,.509,.51,.511,.515,.52])
    assert not E.evaluate(snap, [a,b], now=NOW)["escalation"]["candidate"]
    b = record(snap, "gpt", [.7,.71,.72,.73,.74,.75,.76])
    esc = E.evaluate(snap, [a,b], now=NOW)["escalation"]
    assert esc["version"] == N.ESCALATION_VERSION
    assert {t["type"] for t in esc["triggers"]} == {"model_disagreement", "nonoverlapping_iqr"}
    a.update(escalation_flags=["resolution_ambiguity","evidence_conflict"], market_refs=[{"probability":None}])
    assert {t["type"] for t in E.evaluate(snap, [a], now=NOW)["escalation"]["triggers"]} == set(a["escalation_flags"])


def test_dynamic_gate_defaults_fail_closed_and_old_snapshot_admitted(enabled):
    snap = snapshot(supported=False)
    assert F.eligible(snap, load_run_config(), NOW) is None
    enabled.write_text('{"numeric":false,"discrete":false}')
    assert F.eligible(snap, load_run_config(), NOW) == "unsupported_type"
    assert "numeric" not in P.SUPPORTED_TYPES
    for text in ('{}', '{"numeric":1,"discrete":true}', 'not json'):
        enabled.write_text(text)
        assert Q.supported_types() == Q.LEGACY_TYPES
    enabled.write_text('{"numeric":true,"discrete":false}')
    snap.pop("unit")
    assert F.eligible(snap, load_run_config(), NOW) == "unsupported_scaling"
    enabled.unlink()
    assert Q.supported_types() == Q.LEGACY_TYPES


def test_main_feed_filter_reloads_flags(enabled):
    class Client:
        def get(self, path, params):
            self.params = params
            return {"results": [], "next": None}
    client = Client()
    list(P.main_site_pages(client))
    assert client.params["forecast_type"] == "binary,multiple_choice,numeric,discrete"
    enabled.write_text('{"numeric":false,"discrete":true}')
    list(P.main_site_pages(client))
    assert client.params["forecast_type"] == "binary,multiple_choice,discrete"


def test_legacy_bitwise_golden_and_protected_files():
    golden = json.loads((ROOT / "tests/fixtures/m5_legacy_golden.json").read_text())
    for row in golden["rows"]:
        snap = row["snapshot"]
        current = P.snapshot_from_post(row["post"], "synthetic")
        assert {k:v for k,v in snap.items() if k not in ("snapshot_id","tournament_id")} == current
        assert current["content_hash"].encode() == snap["content_hash"].encode()
        assert F.forecast_prompt(snap, "2026-11-03").encode() == row["prompt"].encode()
        assert V.question_schema(snap) == row["schema"]
        assert json.dumps(S.forecast_payload(snap, row["aggregate"]), allow_nan=False).encode() == row["payload"].encode()
        # Inject numeric fields into a legacy API response: content/hash remain identical.
        post = copy.deepcopy(row["post"])
        post["question"].update({k:v for k,v in snapshot().items() if k in N.SCALING_FIELDS})
        assert P.snapshot_from_post(post, "synthetic") == current
    assert P.snapshot_from_post({"id":1,"group_of_questions":{"questions":[]}}, "synthetic") is None
    for path, digest in golden["protected"].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest, path
    assert json.loads((ROOT / "config/question_types.json").read_text()) == {"numeric":False,"discrete":False}
