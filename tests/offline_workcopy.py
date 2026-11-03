"""Opt-in synthetic host for restricted work-copy regression (no runtime changes).

Use with run_offline.py --synthetic-host. The test host has a separate project
root, an empty ancestor chain outside each fixture, a synthetic directory
inventory, and local submission mode. Original sandbox failures are reported
separately; this fixture never reads a denied path or changes pinned configs.
"""
from pathlib import Path
import sys

import pytest


class SyntheticHost:
    @pytest.fixture(autouse=True)
    def host(self, tmp_path, monkeypatch):
        import baseline
        import flab.workdir as workdir
        import flab.config as config
        import adapters.gemini as gemini

        project = tmp_path / "synthetic-project"
        project.mkdir()
        monkeypatch.setattr(workdir, "PROJECT_ROOT", project)
        if "test_workdir" in sys.modules:
            monkeypatch.setattr(sys.modules["test_workdir"], "PROJECT_ROOT", project)

        ancestors = baseline.ancestor_sources
        monkeypatch.setattr(baseline, "ancestor_sources", lambda base:
                            [s for s in ancestors(base) if Path(s.path).is_relative_to(tmp_path)])
        deny_rules = gemini.read_deny_rules
        def synthetic_rules(keep, listdir=None):
            if listdir is not None:
                return deny_rules(keep, listdir=listdir)
            def listing(path):
                # Only fixture-owned directories are actually listed.
                if path.is_relative_to(tmp_path):
                    return [p.name for p in path.iterdir()] if path.exists() else []
                return []
            return deny_rules(keep, listdir=listing)
        monkeypatch.setattr(gemini, "read_deny_rules", synthetic_rules)

        load = config.load_config_file
        default_config_dir = config.DEFAULT_CONFIG_DIR
        def local_mode(path):
            result = load(path)
            if Path(path) == default_config_dir / "submit_mode.json":
                return {"mode": "local"}
            return result
        monkeypatch.setattr(config, "load_config_file", local_mode)
