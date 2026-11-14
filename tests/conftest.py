"""Deterministic test host shared by pytest and the guarded offline entrypoint."""
import json

import pytest

from offline_workcopy import SyntheticHost


@pytest.fixture(autouse=True)
def isolated_host(tmp_path, tmp_path_factory, monkeypatch):
    # Keep host/config directories separate from each test's data directory.
    host_root = tmp_path_factory.mktemp("synthetic-host")
    SyntheticHost().configure(tmp_path, host_root, monkeypatch)


@pytest.fixture
def question_type_flags(tmp_path_factory, monkeypatch):
    """Explicit rollout settings; never rewrite the repository config."""
    import question_types

    path = tmp_path_factory.mktemp("question-types") / "flags.json"
    monkeypatch.setattr(question_types, "CONFIG_PATH", path)

    def set_flags(enabled):
        path.write_text(json.dumps({"numeric": enabled, "discrete": enabled}))

    return set_flags


@pytest.fixture
def legacy_stops(monkeypatch):
    """Pre-D149 persistent-stop rules, kept for tests of the legacy stop/recovery machinery.

    Production default is FS.PERSISTENT_STOPS = False (D149): no check stops a line indefinitely.
    """
    from flab import fail_soft as FS
    monkeypatch.setattr(FS, "PERSISTENT_STOPS", True)


@pytest.fixture
def legacy_forecast_timeouts(monkeypatch):
    """Pre-D152 forecast timeouts (tier1 600 s, tier2 1200 s) for scenario tests calibrated to them.

    Production run_config_v5 uses 1800/1800 s (D152). These tests exercise scheduling and
    handoff mechanics rather than the configured values, which test_d152_forecast_timeouts.py
    pins. Only the default run_config read is adjusted; copied project configs are untouched.
    """
    import copy
    from pathlib import Path

    import flab.config as config

    load = config.load_config_file
    target = config.DEFAULT_CONFIG_DIR / "run_config_v2.json"

    def legacy(path):
        result = load(path)
        if Path(path) == target:
            result = copy.deepcopy(result)
            result["timeouts_s"] = {**result["timeouts_s"], "tier1": 600, "tier2": 1200}
        return result

    monkeypatch.setattr(config, "load_config_file", legacy)
