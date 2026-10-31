"""Tests for config loading and validation."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from flab.config import (
    ConfigError,
    load_agg_config,
    load_all_configs,
    load_config_file,
    load_players_enabled,
    load_quota_policy,
    load_run_config,
    load_submit_mode,
    load_tournaments,
    validate_config,
)


def test_all_actual_config_files_validate() -> None:
    configs = load_all_configs()
    assert set(configs.keys()) == {
        "run_config_v2.json",
        "agg_v1.json",
        "quota_policy.json",
        "tournaments.json",
        "submit_mode.json",
        "players_enabled.json",
    }

    run_cfg = load_run_config()
    assert run_cfg["players"]["claude"]["tier1"] == {"model": "claude-opus-5-5", "effort": "medium"}
    assert run_cfg["players"]["gemini"]["tier1"] == {"model": "gemini-4-argon-medium", "effort": "medium"}
    assert run_cfg["players"]["gemini"]["tier2"]["model"] == "gemini-4-argon-high"
    assert run_cfg["players"]["gpt"]["tier1"] == {"model": "gpt-6-astra", "effort": "medium"}
    assert run_cfg["timeouts_s"] == {"tier1": 600, "tier2": 1200, "repair": 300}
    assert all(run_cfg["players"][p]["tier2"]["max_subagents"] == 3 for p in ("claude", "gemini", "gpt"))
    assert run_cfg["escalation"]["binary_log_odds_diff"] == 1.0
    assert run_cfg["escalation"]["binary_clip"] == [0.02, 0.98]
    assert run_cfg["escalation"]["multiple_choice_prob_diff"] == 0.25
    assert run_cfg["escalation"]["market_logit_diff"] == 0.7
    assert run_cfg["escalation"]["market_price_epsilon"] == 0.005
    assert run_cfg["deadlines"] == {
        "safety_margin_minutes": 20,
        "submit_lead_minutes": 5,
        "duration_quantile": 0.9,
    }

    agg_cfg = load_agg_config()
    assert agg_cfg["binary"]["extremizing_factor"] == 1.0
    assert agg_cfg["binary"]["clip"] == [0.02, 0.98]
    assert agg_cfg["multiple_choice"]["smoothing_epsilon"] == 0.02

    quota_cfg = load_quota_policy()
    assert quota_cfg["players"]["gemini"]["weekly_cap"] == 0.60
    assert quota_cfg["players"]["gemini"]["tier2_per_question_cap"] == 0.05
    assert quota_cfg["players"]["gemini"]["credits_must_be_zero"] is True
    assert quota_cfg["players"]["claude"]["weekly_attempts_cap"] is None
    assert quota_cfg["players"]["gpt"]["weekly_attempts_cap"] is None
    assert quota_cfg["token_multiplier_cap"] == 3
    assert quota_cfg["max_concurrency_per_player"] == 1

    tournaments_cfg = load_tournaments()
    for t_info in tournaments_cfg["tournaments"].values():
        assert t_info["submit_whitelisted"] is False

    submit_cfg = load_submit_mode()
    assert submit_cfg == {"mode": "local"}

    enabled_cfg = load_players_enabled()
    assert enabled_cfg == {"claude": True, "gemini": True, "gpt": True}


def test_missing_field_and_wrong_type_report_filename_and_field_path(tmp_path: Path) -> None:
    run_cfg = copy.deepcopy(load_run_config())
    del run_cfg["players"]["gemini"]["tier2"]["model"]

    with pytest.raises(ConfigError) as exc_info:
        validate_config("run_config_v2.json", run_cfg)
    msg = str(exc_info.value)
    assert "run_config_v2.json" in msg
    assert "players.gemini.tier2.model" in msg

    # Wrong type (bool instead of int for timeout)
    run_cfg_bad_type = copy.deepcopy(load_run_config())
    run_cfg_bad_type["timeouts_s"]["tier1"] = True
    with pytest.raises(ConfigError) as exc_info2:
        validate_config("run_config_v2.json", run_cfg_bad_type)
    msg2 = str(exc_info2.value)
    assert "run_config_v2.json" in msg2
    assert "timeouts_s.tier1" in msg2

    # File-based error check
    bad_submit = tmp_path / "submit_mode.json"
    bad_submit.write_text(json.dumps({"mode": "invalid_mode"}), encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info3:
        load_config_file(bad_submit)
    msg3 = str(exc_info3.value)
    assert "submit_mode.json" in msg3
    assert "mode" in msg3
