"""Synthetic host configuration for tests, with no runtime or pinned config edits.

conftest.py applies this once per test for every entrypoint. The host root is
separate from the test's tmp_path, preserving empty-directory assertions.
"""
import json
from pathlib import Path
import sys


class SyntheticHost:
    def configure(self, fixture_root, host_root, monkeypatch):
        import baseline
        import flab.workdir as workdir
        import flab.config as config
        import adapters.gemini as gemini

        import question_types

        project = host_root / "project"
        project.mkdir()
        monkeypatch.setattr(workdir, "PROJECT_ROOT", project)
        if "test_workdir" in sys.modules:
            monkeypatch.setattr(sys.modules["test_workdir"], "PROJECT_ROOT", project)

        ancestors = baseline.ancestor_sources
        monkeypatch.setattr(baseline, "ancestor_sources", lambda base:
                            [s for s in ancestors(base) if Path(s.path).is_relative_to(fixture_root)])
        deny_rules = gemini.read_deny_rules
        def synthetic_rules(keep, listdir=None):
            if listdir is not None:
                return deny_rules(keep, listdir=listdir)
            def listing(path):
                # Only fixture-owned directories are actually listed.
                if path.is_relative_to(fixture_root):
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
        flags = host_root / "question-types.json"
        flags.write_text(json.dumps({"numeric": True, "discrete": True}))
        monkeypatch.setattr(question_types, "CONFIG_PATH", flags)
