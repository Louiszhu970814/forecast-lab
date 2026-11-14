"""D152: tier1/tier2 forecast timeouts raised to 1800 s with a bounded live-window config handoff."""
from __future__ import annotations

import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest

import budget_handoff as H
import continuous as C
import cycle
import live_window as W
from flab.config import load_quota_policy, load_run_config
from flab.ids import hash_obj
from flab.ledger import read_complete
from flab.timeutil import to_iso
from test_budget_handoff import finished, reserve
from test_d151_gemini_carbon import END, MID, START, _base_v11_campaign

D151_RUN_CONFIG_SHA256 = "e1cf8d051ed8b695b8f197045e5ccdbead86df143d5acf71f4ded5da8256c4c1"
D152_START = START + timedelta(days=3, hours=13, minutes=30)  # inside the D151 window, before END


def _v4_config(cfg: dict) -> dict:
    old = copy.deepcopy(cfg)
    old["version"] = "run_config_v4"
    old["timeouts_s"] = {"tier1": 600, "tier2": 1200, "repair": 300}
    old["_notes"].pop("forecast_timeouts")
    return old


def _v3_config(cfg: dict) -> dict:
    old = _v4_config(cfg)
    old["version"] = "run_config_v3"
    old["players"]["gemini"]["tier1"] = {"model": "gemini-4-argon-medium", "effort": "medium"}
    old["players"]["gemini"]["tier2"]["model"] = "gemini-4-argon-high"
    return old


def _successor(source: dict, campaign_id: str, start, run_config: dict, authorization: str) -> dict:
    succ = {k: copy.deepcopy(v) for k, v in source.items()
            if k not in ("renewed_from", "renewal_authorization", "budget_handoff")}
    succ.update(
        campaign_id=campaign_id,
        budget_window_start=to_iso(start),
        run_config_sha256=hash_obj(run_config),
        budget_handoff={
            "version": H.CONFIG_HANDOFF_VERSION,
            "source_campaign_id": source["campaign_id"],
            "source_sha256": hash_obj(source),
            "accounting_start": to_iso(START),
            "authorization": authorization,
        },
    )
    return succ


def test_run_config_v5_changes_only_forecast_timeouts() -> None:
    cfg = load_run_config()
    assert cfg["version"] == "run_config_v5"
    assert cfg["timeouts_s"] == {"tier1": 1800, "tier2": 1800, "repair": 300}
    assert "1800" in cfg["_notes"]["forecast_timeouts"]
    # Reverting only the version, timeouts and the D152 note reproduces the approved D151 object.
    assert hash_obj(_v4_config(cfg)) == D151_RUN_CONFIG_SHA256


def test_cycle_timing_limits_follow_longer_forecast_timeouts() -> None:
    limits = cycle.timing_limits(load_run_config())
    assert limits["worst_job_s"] == 2 * (1800 + 300 + 4 * 30)
    assert limits["handoff_s"] == 6000 - cycle.SAFETY_S
    assert limits["worker_timeout_s"] == 6000 + cycle.SUBMIT_TIMEOUT_S
    assert limits["supervisor_timeout_s"] == limits["worker_timeout_s"] + cycle.CLEANUP_RESERVE_S


def _chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cap: int = 3):
    new_cfg = load_run_config()
    v4_cfg, v3_cfg = _v4_config(new_cfg), _v3_config(new_cfg)
    base = _base_v11_campaign(old_run_hash=hash_obj(v3_cfg), cap=cap)
    monkeypatch.setattr(C, "load_run_config", lambda: v3_cfg)
    C.register(tmp_path, base, now=lambda: START + timedelta(hours=1))
    d151 = _successor(base, "d151_gemini_carbon_20261112", MID, v4_cfg, "PLAN D151")
    monkeypatch.setattr(C, "load_run_config", lambda: v4_cfg)
    C.register(tmp_path, d151, now=lambda: MID)
    monkeypatch.setattr(C, "load_run_config", lambda: new_cfg)
    d152 = _successor(d151, "d152_forecast_timeouts", D152_START, new_cfg, "PLAN D152")
    return base, d151, d152, new_cfg


def test_d152_handoff_chains_through_d151_and_keeps_root_accounting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir, config_dir = tmp_path / "data", tmp_path / "config"
    data_dir.mkdir()
    config_dir.mkdir()
    base, d151, d152, new_cfg = _chain(data_dir, monkeypatch, cap=3)
    finished(data_dir, "att-root", START + timedelta(hours=2))
    finished(data_dir, "att-d151", MID + timedelta(hours=1))
    finished(data_dir, "att-d151-2", MID + timedelta(hours=2))

    C.register(data_dir, d152, now=lambda: D152_START)
    assert [p["campaign_id"] for p in H.ancestors(d152, read_complete(data_dir / "continuous_campaigns.jsonl"))] \
        == [d151["campaign_id"], base["campaign_id"]]
    assert H.accounting_start(data_dir, d152) == START
    res = reserve(data_dir, d152, now=D152_START + timedelta(minutes=5))
    assert res["weekly_attempts_used"] == 3 and not res["allowed"]
    for old in (base, d151):
        with pytest.raises(ValueError, match="superseded"):
            reserve(data_dir, old, now=D152_START + timedelta(minutes=5))

    (config_dir / "run_config_v2.json").write_text(json.dumps(new_cfg, indent=2) + "\n")
    (config_dir / "quota_policy.json").write_text(json.dumps(load_quota_policy(), indent=2) + "\n")
    (config_dir / "cycle_campaign.json").write_text(json.dumps(d152, indent=2) + "\n")
    mode = {"mode": "live", "approved_bot_id": 311763,
            "live_window": {"version": W.VERSION, "campaign_id": d152["campaign_id"],
                            "campaign_sha256": hash_obj(d152), "start": d152["budget_window_start"],
                            "end": d152["budget_window_end"]}}
    (config_dir / "submit_mode.json").write_text(json.dumps(mode, indent=2) + "\n")
    assert W.bounds(mode, d152, config_dir) == (D152_START, END)

    event = W.renew(config_dir, END, schedule_log=data_dir / "live_schedule.jsonl")
    assert event is not None and event["campaign_id"] == "auto_20261117T0400Z"
    renewed = W.campaign_from(config_dir)
    assert "budget_handoff" not in renewed and renewed["renewed_from"] == d152["campaign_id"]
    assert renewed["run_config_sha256"] == hash_obj(new_cfg)
    C.register(data_dir, renewed, now=lambda: END + timedelta(minutes=1))


@pytest.mark.parametrize("mutation", ["unknown_auth", "skip_d151_source", "wrong_accounting_start",
                                      "same_run_hash", "extend_end", "cap_change"])
def test_d152_handoff_rejects_invalid_successors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str) -> None:
    base, d151, d152, new_cfg = _chain(tmp_path, monkeypatch, cap=10)
    if mutation == "unknown_auth":
        d152["budget_handoff"]["authorization"] = "PLAN D153"
    elif mutation == "skip_d151_source":
        d152 = _successor(base, "d152_forecast_timeouts", D152_START, new_cfg, "PLAN D152")
    elif mutation == "wrong_accounting_start":
        d152["budget_handoff"]["accounting_start"] = d151["budget_window_start"]
    elif mutation == "same_run_hash":
        v4_cfg = _v4_config(new_cfg)
        monkeypatch.setattr(C, "load_run_config", lambda: v4_cfg)
        d152["run_config_sha256"] = hash_obj(v4_cfg)
    elif mutation == "extend_end":
        d152["budget_window_end"] = to_iso(END + timedelta(minutes=1))
    elif mutation == "cap_change":
        d152["weekly_attempt_caps"]["claude"] += 1
    before = len(read_complete(tmp_path / "continuous_campaigns.jsonl"))
    with pytest.raises(ValueError):
        C.register(tmp_path, d152, now=lambda: D152_START)
    assert len(read_complete(tmp_path / "continuous_campaigns.jsonl")) == before
