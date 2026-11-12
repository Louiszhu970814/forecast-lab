"""D151: Gemini Carbon model upgrade (run_config_v4), log label verification, and budget_handoff_v4."""
from __future__ import annotations

import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest

from adapters import gemini
from adapters.base import AdapterRequest
import budget_handoff as H
import continuous as C
import discovery as D
import escalate as E
import forecast as F
import live_window as W
import market_policy as MP
import quota
from flab.config import load_quota_policy, load_run_config
from flab.ids import hash_obj
from flab.ledger import read_complete
from flab.timeutil import parse_iso, to_iso
from test_adapter_gemini import OK_RESULT, _make_req, _write_stream
from test_budget_handoff import finished, reserve
from test_forecast import NOW

START = parse_iso("2026-11-10T04:00:00.000Z")
MID = parse_iso("2026-11-12T15:00:00.000Z")
END = parse_iso("2026-11-17T04:00:00.000Z")


@pytest.mark.parametrize(
    ("model_id", "effort", "label"),
    [
        ("gemini-4-carbon-medium", "medium", "Gemini 4 Carbon (Medium)"),
        ("gemini-4-carbon-high", "high", "Gemini 4 Carbon (High)"),
        ("gemini-4-carbon-low", "low", "Gemini 4 Carbon (Low)"),
    ],
)
def test_gemini_carbon_model_labels_verify_and_detect_mismatch(
    tmp_path: Path, model_id: str, effort: str, label: str
) -> None:
    req = _make_req(tmp_path, model=model_id, effort=effort)
    _write_stream(
        req,
        [],
        OK_RESULT,
        log=f'I1 x.go:1] Propagating selected model override to backend: label="{label}"\n',
    )
    res = gemini.parse(req, None)
    assert res.reported_model_id == label
    assert res.usage_raw["log_model_check"] == "verified"

    wrong_dir = tmp_path / "wrong"
    wrong_req = AdapterRequest(
        player="gemini",
        profile="tier2",
        prompt="Test prompt",
        schema={"type": "object", "properties": {"answer": {"type": "string"}}},
        workdir=wrong_dir / "work",
        raw_dir=wrong_dir / "raw",
        model="gemini-4-carbon-high",
        effort="high",
        timeout_s=600,
    )
    _write_stream(
        wrong_req,
        [],
        OK_RESULT,
        log='I1 x.go:1] Propagating selected model override to backend: label="Gemini 4 Carbon (Medium)"\n',
    )
    wrong_res = gemini.parse(wrong_req, None)
    assert wrong_res.usage_raw["log_model_check"] == "mismatch"


def test_quota_manual_admission_recognizes_carbon_prefix_on_legacy_layout(tmp_path: Path) -> None:
    legacy = quota.QuotaSnapshot(
        buckets={
            quota.ARGON_BUCKET_ID: quota.BucketQuota(
                id=quota.ARGON_BUCKET_ID,
                remaining_fraction=0.01,
                reset_time=to_iso(NOW + timedelta(days=2)),
                window="weekly",
                group_name="Gemini 4 Argon",
            ),
            quota.GEMINI_WEEKLY_BUCKET_ID: quota.BucketQuota(
                id=quota.GEMINI_WEEKLY_BUCKET_ID,
                remaining_fraction=0.95,
                reset_time=to_iso(NOW + timedelta(days=2)),
                window="weekly",
                group_name="Gemini Models",
            ),
            quota.GEMINI_5H_BUCKET_ID: quota.BucketQuota(
                id=quota.GEMINI_5H_BUCKET_ID,
                remaining_fraction=0.95,
                reset_time=to_iso(NOW + timedelta(hours=3)),
                window="5h",
                group_name="Gemini Models",
            ),
        },
        remaining_credits=0,
        taken_at=to_iso(NOW),
    )
    policy = copy.deepcopy(load_quota_policy())
    policy["players"]["gemini"]["weekly_cap"] = 0.8
    reason, _ = quota.manual_admission(legacy, policy, model="gemini-4-carbon-high", history_root=tmp_path)
    assert reason == "quota_exhausted"


def _base_v11_campaign(old_run_hash: str = "42936cd6c04e4e547dbe3369f868f1e338da04ab00a754cbe219037b8f196572", cap: int = 2) -> dict:
    return {
        "version": "continuous_v11",
        "campaign_id": "auto_20261110T0400Z",
        "venues": ["main", "minibench", "market_pulse"],
        "budget_window_start": to_iso(START),
        "budget_window_end": to_iso(END),
        "weekly_attempt_caps": {"claude": cap, "gemini": cap, "gpt": cap},
        "expected_new_questions": 0,
        "repair_reserve": 0,
        "urgent_slack_s": 120,
        "run_config_sha256": old_run_hash,
        "quota_policy_sha256": hash_obj(load_quota_policy()),
        "discovery": copy.deepcopy(D.SETTING),
        "market_pulse": copy.deepcopy(MP.SETTING),
        "renewed_from": "d139_main_minibench_market_20261105",
        "renewal_authorization": "PLAN D149",
    }


def _d151_successor(base: dict) -> dict:
    succ = {k: copy.deepcopy(v) for k, v in base.items() if k not in ("renewed_from", "renewal_authorization")}
    succ.update(
        campaign_id="d151_gemini_carbon_20261112",
        budget_window_start=to_iso(MID),
        run_config_sha256=hash_obj(load_run_config()),
        budget_handoff={
            "version": H.CONFIG_HANDOFF_VERSION,
            "source_campaign_id": base["campaign_id"],
            "source_sha256": hash_obj(base),
            "accounting_start": base["budget_window_start"],
            "authorization": "PLAN D151",
        },
    )
    return succ


def test_v4_config_handoff_preserves_accounting_start_and_renews_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    data_dir.mkdir()
    config_dir.mkdir()

    old_cfg = copy.deepcopy(load_run_config())
    old_cfg["version"] = "run_config_v3"
    old_cfg["players"]["gemini"]["tier1"] = {"model": "gemini-4-argon-medium", "effort": "medium"}
    old_cfg["players"]["gemini"]["tier2"]["model"] = "gemini-4-argon-high"

    base = _base_v11_campaign(old_run_hash=hash_obj(old_cfg), cap=2)
    monkeypatch.setattr(C, "load_run_config", lambda: old_cfg)
    C.register(data_dir, base, now=lambda: START + timedelta(hours=1))

    finished(data_dir, "att-before-1", START + timedelta(hours=2))
    finished(data_dir, "att-before-2", START + timedelta(hours=3))

    new_cfg = load_run_config()
    monkeypatch.setattr(C, "load_run_config", lambda: new_cfg)
    succ = _d151_successor(base)
    C.register(data_dir, succ, now=lambda: MID)
    assert H.accounting_start(data_dir, succ) == START

    res = reserve(data_dir, succ, now=MID + timedelta(minutes=5))
    assert res["weekly_attempts_used"] == 2
    assert not res["allowed"]
    with pytest.raises(ValueError, match="superseded"):
        reserve(data_dir, base, now=MID + timedelta(minutes=5))

    # Write config dir and verify live_window.bounds + auto-renewal at END.
    (config_dir / "run_config_v2.json").write_text(json.dumps(new_cfg, indent=2) + "\n")
    (config_dir / "quota_policy.json").write_text(json.dumps(load_quota_policy(), indent=2) + "\n")
    (config_dir / "cycle_campaign.json").write_text(json.dumps(succ, indent=2) + "\n")
    mode = {
        "mode": "live",
        "approved_bot_id": 311763,
        "live_window": {
            "version": W.VERSION,
            "campaign_id": succ["campaign_id"],
            "campaign_sha256": hash_obj(succ),
            "start": succ["budget_window_start"],
            "end": succ["budget_window_end"],
        },
    }
    (config_dir / "submit_mode.json").write_text(json.dumps(mode, indent=2) + "\n")
    assert W.bounds(mode, succ, config_dir) == (MID, END)

    event = W.renew(config_dir, END, schedule_log=data_dir / "live_schedule.jsonl")
    assert event is not None and event["campaign_id"] == "auto_20261117T0400Z"
    renewed = W.campaign_from(config_dir)
    assert "budget_handoff" not in renewed
    assert renewed["renewed_from"] == succ["campaign_id"]
    C.register(data_dir, renewed, now=lambda: END + timedelta(minutes=1))


@pytest.mark.parametrize(
    "mutation",
    [
        "same_run_hash",
        "wrong_accounting_start",
        "extend_end",
        "cap_change",
        "venue_change",
        "wrong_auth",
        "wrong_source_hash",
        "duplicate_successor",
    ],
)
def test_v4_config_handoff_rejects_invalid_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    old_cfg = copy.deepcopy(load_run_config())
    old_cfg["version"] = "run_config_v3"
    base = _base_v11_campaign(old_run_hash=hash_obj(old_cfg), cap=10)
    monkeypatch.setattr(C, "load_run_config", lambda: old_cfg)
    C.register(tmp_path, base, now=lambda: START + timedelta(hours=1))

    new_cfg = load_run_config()
    monkeypatch.setattr(C, "load_run_config", lambda: new_cfg)
    succ = _d151_successor(base)

    if mutation == "same_run_hash":
        monkeypatch.setattr(C, "load_run_config", lambda: old_cfg)
        succ["run_config_sha256"] = hash_obj(old_cfg)
    elif mutation == "wrong_accounting_start":
        succ["budget_handoff"]["accounting_start"] = to_iso(MID)
    elif mutation == "extend_end":
        succ["budget_window_end"] = to_iso(END + timedelta(minutes=1))
    elif mutation == "cap_change":
        succ["weekly_attempt_caps"]["gemini"] += 1
    elif mutation == "venue_change":
        succ["venues"] = ["main", "minibench"]
        succ.pop("market_pulse", None)
    elif mutation == "wrong_auth":
        succ["budget_handoff"]["authorization"] = "PLAN D139"
    elif mutation == "wrong_source_hash":
        succ["budget_handoff"]["source_sha256"] = "0" * 64
    elif mutation == "duplicate_successor":
        C.register(tmp_path, succ, now=lambda: MID)
        succ = copy.deepcopy(succ)
        succ["campaign_id"] = "d151_other_fork"

    before_rows = len(read_complete(tmp_path / "continuous_campaigns.jsonl"))
    with pytest.raises(ValueError):
        C.register(tmp_path, succ, now=lambda: MID)
    assert len(read_complete(tmp_path / "continuous_campaigns.jsonl")) == before_rows
