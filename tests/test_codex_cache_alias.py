from pathlib import Path

import pytest

import baseline as B


def fixture(tmp_path):
    root = tmp_path / "plugins"
    version = root / "cache/openai-bundled/chrome/26.1027.51404"
    version.mkdir(parents=True)
    (version / "SKILL.md").write_text("synthetic skill")
    return root, version, version.parent / "latest"


def inspect(root, **kw):
    return B.inspect_source(B.Source(kw.pop("source_id", "codex.plugins"), "gpt", str(root), "dir", **kw), root.parent)


@pytest.mark.parametrize("absolute", [False, True])
def test_latest_alias_preserves_real_target_inventory(tmp_path, absolute):
    root, version, alias = fixture(tmp_path)
    alias.symlink_to(version if absolute else version.name, target_is_directory=True)
    before = inspect(root)
    assert "error" not in before
    assert before["entries"] == 2
    (version / "SKILL.md").write_text("updated synthetic skill")
    after = inspect(root)
    assert before["hash"] != after["hash"]
    assert "error" not in after
    approved = {"sources": {"codex.plugins": before}, "versions": dict.fromkeys(B.PLAYERS, "1.0")}
    current = {"sources": {"codex.plugins": after}, "versions": approved["versions"]}
    assert B.compare(approved, current)["decisions"]["gpt"] == "ok"
    assert B.compare(approved, current, catalog_drift=False)["decisions"]["gpt"] == "stop"


@pytest.mark.parametrize("case", ["outside", "private", "chain", "ancestor", "different_plugin", "arbitrary", "dangling", "excluded", "credential", "nested_escape", "other_source"])
def test_unsafe_aliases_or_targets_still_block(tmp_path, case):
    root, version, alias = fixture(tmp_path)
    target = version
    kw = {}
    if case == "outside":
        target = tmp_path / "26.1027.51404"; target.mkdir()
    elif case == "private":
        target = version.parent / "sessions"; target.mkdir()
    elif case == "chain":
        target = version.parent / "redirect"; target.symlink_to(version, target_is_directory=True)
    elif case == "ancestor":
        target = root
    elif case == "different_plugin":
        target = root / "cache/openai-bundled/other/26.1027.51404"; target.mkdir(parents=True)
    elif case == "arbitrary":
        alias = version.parent / "current"
    elif case == "dangling":
        target = version.parent / "26.1027.99999"
    elif case == "excluded":
        kw["exclude"] = (str(version.relative_to(root)),)
    elif case == "credential":
        (version / ".env").write_text("SYNTHETIC-SECRET")
    elif case == "nested_escape":
        outside = tmp_path / "outside"; outside.mkdir()
        (version / "outside").symlink_to(outside, target_is_directory=True)
    elif case == "other_source":
        kw["source_id"] = "codex.skills"
    alias.symlink_to(target, target_is_directory=case != "dangling")
    assert "error" in inspect(root, **kw)


def test_alias_retarget_changes_strict_inventory(tmp_path):
    root, version, alias = fixture(tmp_path)
    alias.symlink_to(version.name, target_is_directory=True)
    other = version.parent / "26.1027.51405"
    other.mkdir(); (other / "SKILL.md").write_text("other synthetic skill")
    before = inspect(root)
    alias.unlink(); alias.symlink_to(other.name, target_is_directory=True)
    after = inspect(root)
    assert "error" not in after
    assert before["hash"] != after["hash"]
