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
