from __future__ import annotations

import builtins
import json
import os
from pathlib import Path

import pytest

import baseline as bl
from flab.jsonl import append_record

VERSIONS = {"claude": "2.1.316", "gemini": "1.4.12", "gpt": "0.166.0"}


def _make_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".claude" / "skills" / "synced").mkdir(parents=True)
    (home / ".claude" / "skills" / "synced" / "SKILL.md").write_text("skill v1")
    (home / ".claude" / "plugins" / "cache").mkdir(parents=True)
    (home / ".claude" / "plugins" / "cache" / "plugin.js").write_text("plugin v1")
    (home / ".claude" / "plugins" / "cache" / "hooks-handlers").mkdir()
    (home / ".claude" / "plugins" / "cache" / "hooks-handlers" / "session-start.sh").write_text("hook v1")
    (home / ".claude" / "settings.json").write_text(json.dumps({"model": "opus", "theme": "dark"}))
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {}, "oauthAccount": {"email": "x"}}))
    (home / ".codex" / "skills" / ".system").mkdir(parents=True)
    (home / ".codex" / "skills" / ".system" / "s.md").write_text("system skill")
    (home / ".codex" / "auth.json").write_text("SECRET-DO-NOT-READ")
    (home / ".codex" / "config.toml").write_text("[projects]\n")
    (home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    (home / ".gemini" / "antigravity-cli" / "settings.json").write_text(
        json.dumps({"agentMode": "auto", "permissions": {"allow": ["command(agy)"]}}))
    return home


def _add_private_items(home: Path) -> None:
    """A conversation folder and a credential file inside inventoried directories."""
    (home / ".claude" / "plugins" / "sessions").mkdir()
    (home / ".claude" / "plugins" / "sessions" / "out.jsonl").write_text("SESSION-OUTPUT")
    (home / ".codex" / "skills" / ".env").write_text("TOKEN=SECRET-ENV")


def _sources(home: Path) -> list[bl.Source]:
    # Keep the real inventory but drop absolute system paths so tests stay hermetic.
    return [s for s in bl.default_sources(env={}) if s.path.startswith("~")]


def _snap(home: Path, tmp_path: Path, **kw: object) -> bl.Snapshot:
    base = tmp_path / "runs"
    base.mkdir(exist_ok=True)
    kw.setdefault("versions", dict(VERSIONS))
    kw.setdefault("gemini_credits", 0)
    return bl.take_snapshot(home=home, sources=_sources(home), attempt_base=base, **kw)  # type: ignore[arg-type]


def _decisions(a: bl.Snapshot, b: bl.Snapshot) -> dict[str, str]:
    return bl.compare(a.to_record(), b.to_record())["decisions"]


def test_forbidden_paths_are_never_opened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _make_home(tmp_path)
    _add_private_items(home)
    opened: list[str] = []
    real_open = builtins.open

    def spy(file, *a, **k):  # type: ignore[no-untyped-def]
        opened.append(str(file))
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (opened.append(str(self)), real_open(self, encoding="utf-8").read())[1])
    snap = _snap(home, tmp_path)
    for forbidden in ("auth.json", ".claude.json", "/sessions/", "/.env"):
        assert not any(forbidden in p for p in opened), (forbidden, opened)
    text = json.dumps(snap.to_record())
    for secret in ("skill v1", "SECRET", "SESSION-OUTPUT", "oauthAccount"):
        assert secret not in text
    # D121: private session directories are informational; credentials still block.
    assert snap.sources["claude.plugins"]["informational_private_dirs"] == ["sessions"]
    assert snap.sources["codex.skills"]["not_read"] == [".env"]
    assert _decisions(snap, snap) == {"claude": "ok", "gemini": "ok", "gpt": "stop"}


def test_no_change_is_ok_and_content_change_stops_that_line(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    (home / ".claude" / "skills" / "mine").mkdir()
    (home / ".claude" / "skills" / "mine" / "SKILL.md").write_text("skill v1")
    a = _snap(home, tmp_path)
    assert _decisions(a, _snap(home, tmp_path)) == {"claude": "ok", "gemini": "ok", "gpt": "ok"}
    # D123: the claude.ai-synced bucket is informational; personal skills remain content.
    (home / ".claude" / "skills" / "synced" / "SKILL.md").write_text("skill v2")
    assert _decisions(a, _snap(home, tmp_path)) == {"claude": "ok", "gemini": "ok", "gpt": "ok"}
    (home / ".claude" / "skills" / "mine" / "SKILL.md").write_text("skill v2")
    assert _decisions(a, _snap(home, tmp_path)) == {"claude": "stop", "gemini": "ok", "gpt": "ok"}


def test_plugin_cache_is_inventoried(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    a = _snap(home, tmp_path)
    (home / ".claude" / "plugins" / "cache" / "plugin.js").write_text("plugin v2")
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "stop"


def test_marketplace_refresh_is_informational_but_marketplace_content_stops(tmp_path: Path) -> None:
    # claude 2.1.316 refreshed known_marketplaces.json (lastUpdated) and the plugin directory cache
    # during a real M2 forecast run; the marketplace files themselves did not change.
    home = _make_home(tmp_path)
    plugins = home / ".claude" / "plugins"
    (plugins / "marketplaces" / "official").mkdir(parents=True)
    (plugins / "marketplaces" / "official" / "plugin.json").write_text('{"name": "p"}')
    index = plugins / "known_marketplaces.json"
    entry = {"source": {"source": "github", "repo": "anthropics/claude-plugins-official"},
             "installLocation": "/x/marketplaces/official", "lastUpdated": "2026-11-01T06:00:00Z"}
    index.write_text(json.dumps({"official": entry}))
    (plugins / "plugin-directory-cache-v2.json").write_text('{"fetchedAt": "1", "listings": []}')
    a = _snap(home, tmp_path)
    index.write_text(json.dumps({"official": {**entry, "lastUpdated": "2026-11-01T11:49:08Z"}}))
    (plugins / "plugin-directory-cache-v2.json").write_text('{"fetchedAt": "2", "listings": [{"n": 1}]}')
    res = bl.compare(a.to_record(), _snap(home, tmp_path).to_record())
    assert res["decisions"]["claude"] == "ok"
    assert sorted((c["source"], c["category"]) for c in res["changes"]) == [
        ("claude.marketplace_index", bl.INFORMATIONAL), ("claude.plugin_directory_cache", bl.INFORMATIONAL)]
    # A new marketplace, a changed marketplace source, or changed marketplace files still stop the line.
    other = {**entry, "source": {"source": "github", "repo": "someone/else"}}
    for data in ({"official": entry, "new": other}, {"official": other}):
        index.write_text(json.dumps(data))
        assert _decisions(a, _snap(home, tmp_path))["claude"] == "stop"
    index.write_text(json.dumps({"official": entry}))
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "ok"
    (plugins / "marketplaces" / "official" / "plugin.json").write_text('{"name": "p", "hooks": "x"}')
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "stop"


def test_marketplace_index_edge_cases_stay_conservative(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    plugins = home / ".claude" / "plugins"
    (plugins / "marketplaces" / "official").mkdir(parents=True)
    index = plugins / "known_marketplaces.json"
    index.write_text(json.dumps({"official": {"source": "github", "lastUpdated": "1"}}))
    a = _snap(home, tmp_path)
    # Empty, unparsable or malformed indexes stop the line; they are never read as "{}".
    for bad in ("", "{not json", "[]", '{"official": "x"}'):
        index.write_text(bad)
        assert _decisions(a, _snap(home, tmp_path))["claude"] == "stop", bad
    index.write_text(json.dumps({"official": {"source": "github", "lastUpdated": "1"}}))
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "ok"
    # Only the top-level files are excluded; a same-named file deeper in the tree is plugin content.
    nested = plugins / "marketplaces" / "official" / "known_marketplaces.json"
    nested.write_text("{}")
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "stop"
    nested.unlink()
    # The excluded paths must not become symlinks, and nothing may point into them.
    index.unlink()
    index.symlink_to(plugins / "marketplaces" / "official")
    assert "error" in _snap(home, tmp_path).sources["claude.marketplace_index"]
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "stop"
    index.unlink()
    index.write_text(json.dumps({"official": {"source": "github", "lastUpdated": "1"}}))
    (plugins / "plugin-directory-cache-v2.json").write_text("{}")
    b = _snap(home, tmp_path)
    (plugins / "marketplaces" / "official" / "link.json").symlink_to(plugins / "plugin-directory-cache-v2.json")
    assert _decisions(b, _snap(home, tmp_path))["claude"] == "stop"


def test_interrupted_index_refresh_temporaries_are_ignored_only_at_top_level(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    plugins = home / ".claude" / "plugins"
    (plugins / "marketplaces" / "official").mkdir(parents=True)
    a = _snap(home, tmp_path)
    (plugins / "plugin-directory-cache-v2.json.tmp.7a422f2e").write_text("{}")
    (plugins / "known_marketplaces.json.tmp.abc123").write_text("{}")
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "ok"
    # Anything else stops the line: deeper copies, other names, and symlinks that look like temporaries.
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "ok"
    (plugins / ("plugin-directory-cache-v2.json.tmp." + "a" * 32)).write_text("{}")
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "ok"
    for bad in (plugins / "marketplaces" / "official" / "plugin-directory-cache-v2.json.tmp.1",
                plugins / "plugin-directory-cache-v2.json.tmp.bad.js", plugins / "hooks.json.tmp.1",
                plugins / "plugin-directory-cache-v2.json.tmp.", plugins / ("plugin-directory-cache-v2.json.tmp." + "a" * 33),
                plugins / "plugin-directory-cache-v2.json.tmp.a\n", plugins / "plugin-directory-cache-v2.json.tmp.é"):
        bad.write_text("{}")
        assert _decisions(a, _snap(home, tmp_path))["claude"] == "stop", bad.name
        bad.unlink()
    link = plugins / "plugin-directory-cache-v2.json.tmp.ln"
    link.symlink_to(plugins / "marketplaces" / "official")
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "stop"
    link.unlink()
    link.symlink_to(plugins / "plugin-directory-cache-v2.json.tmp.7a422f2e")  # symlink to a regular file
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "stop"
    link.unlink()
    (plugins / "plugin-directory-cache-v2.json.tmp.dir").mkdir()  # same-named directory is walked
    (plugins / "plugin-directory-cache-v2.json.tmp.dir" / "x.js").write_text("x")
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "stop"


def test_plugin_state_only_observed_run_keys_are_informational(tmp_path: Path) -> None:
    # claude 2.1.316 rewrites usage-rows-queued in ~/.claude/plugins/store on every run (M1 acceptance).
    home = _make_home(tmp_path)
    store = home / ".claude" / "plugins" / "store"
    store.mkdir()
    (store / "builtin.json").write_text('{"usage-rows-queued": "1", "open": false}')
    a = _snap(home, tmp_path)
    (store / "builtin.json").write_text('{"usage-rows-queued": "2", "open": false}')
    res = bl.compare(a.to_record(), _snap(home, tmp_path).to_record())
    assert res["decisions"]["claude"] == "ok"
    assert [(c["source"], c["category"]) for c in res["changes"]] == [("claude.plugin_state", bl.INFORMATIONAL)]
    for change in ('{"usage-rows-queued": "2", "open": true}',
                   '{"usage-rows-queued": "2", "open": false, "instructions": "x"}'):
        (store / "builtin.json").write_text(change)
        assert _decisions(a, _snap(home, tmp_path))["claude"] == "stop"
    (store / "builtin.json").write_text('{"usage-rows-queued": "1", "open": false}')
    (store / "new.json").write_text("{}")
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "stop"


def test_symlink_into_a_private_folder_is_unsafe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _make_home(tmp_path)
    _add_private_items(home)
    plugins = home / ".claude" / "plugins"
    os.symlink(plugins / "sessions" / "out.jsonl", plugins / "cache" / "innocent.txt")
    opened: list[str] = []
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (opened.append(str(f)), real_open(f, *a, **k))[1])
    snap = _snap(home, tmp_path)
    assert snap.sources["claude.plugins"]["unsafe_symlinks"] == ["cache/innocent.txt"]
    assert not any("/sessions/" in p for p in opened)


def test_files_with_sensitive_sounding_names_are_hashed_by_content(tmp_path: Path) -> None:
    # GPT review 3: an existing skills/token_helper.md changed from v1 to v2 went unnoticed.
    home = _make_home(tmp_path)
    helper = home / ".codex" / "skills" / "token_helper.md"
    helper.write_text("v1")
    a = _snap(home, tmp_path)
    assert _decisions(a, a)["gpt"] == "ok"
    helper.write_text("v2")
    assert _decisions(a, _snap(home, tmp_path))["gpt"] == "stop"
    hook = home / ".claude" / "plugins" / "cache" / "hooks-handlers" / "session-start.sh"
    hook.write_text("hook v2")
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "stop"


def test_only_credential_and_conversation_names_are_left_unread(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    skills = home / ".claude" / "skills" / "synced"
    for name in ("auth.md", "secret.py", "token_helper.md", "session-start.sh", "history.md"):
        (skills / name).write_text("ordinary file")
    snap = _snap(home, tmp_path)
    assert snap.sources["claude.skills"]["not_read"] == [] and "error" not in snap.sources["claude.skills"]
    for name in ("auth.json", "x.pem", ".env.local", "jetski-standalone-oauth-token", "cookies.sqlite"):
        assert bl.CREDENTIAL_FILE.search(name), name


def test_state_directory_holds_only_top_level_json(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    store = home / ".claude" / "plugins" / "store"
    store.mkdir()
    (store / "builtin.json").write_text('{"usage-rows-queued": "1"}')
    assert "error" not in _snap(home, tmp_path).sources["claude.plugin_state"]
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (store / "sessions").mkdir()
    assert "error" not in _snap(home, tmp_path).sources["claude.plugin_state"]
    (store / "sessions").rmdir()
    cases = [lambda: os.symlink(outside, store / "link.json"),
             lambda: (store / "output.txt").write_text("SESSION-OUTPUT")]
    for make in cases:
        make()
        assert "unexpected entry" in _snap(home, tmp_path).sources["claude.plugin_state"]["error"]
        for p in store.iterdir():
            if p.name != "builtin.json":
                p.rmdir() if p.is_dir() and not p.is_symlink() else p.unlink()
    locked = store / "locked.json"
    locked.write_text("{}")
    locked.chmod(0)
    try:
        assert "cannot read" in _snap(home, tmp_path).sources["claude.plugin_state"]["error"]
    finally:
        locked.chmod(0o600)


def test_agy_workspace_folders_are_inventoried_as_directories(tmp_path: Path) -> None:
    kinds = {Path(s.path).name: s.kind for s in bl.ancestor_sources(tmp_path)}
    assert all(kinds[n] == "dir" for n in (".agents", ".agent", "_agents", "_agent", ".claude"))
    assert kinds["AGENTS.md"] == "file"


def test_unreadable_directory_stops_the_line(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    locked = home / ".claude" / "skills" / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        snap = _snap(home, tmp_path)
        assert snap.sources["claude.skills"].get("error") == "directory could not be fully read"
        assert _decisions(snap, snap)["claude"] == "stop"
    finally:
        locked.chmod(0o700)


def test_settings_keys_split_into_general_and_content(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    a = _snap(home, tmp_path)
    (home / ".claude" / "settings.json").write_text(json.dumps({"model": "sonnet", "theme": "dark"}))
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "reaccept"
    b = _snap(home, tmp_path)
    (home / ".claude" / "settings.json").write_text(json.dumps({"model": "sonnet", "theme": "dark", "hooks": {}}))
    assert _decisions(b, _snap(home, tmp_path))["claude"] == "stop"


def test_unparseable_settings_stop_the_line(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    a = _snap(home, tmp_path)
    (home / ".gemini" / "antigravity-cli" / "settings.json").write_text("{")
    assert _decisions(a, _snap(home, tmp_path))["gemini"] == "stop"


def test_codex_config_toml_is_informational_and_codex_home_is_respected(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    a = _snap(home, tmp_path)
    (home / ".codex" / "config.toml").write_text('[projects."/tmp/x"]\ntrust_level = "trusted"\n')
    res = bl.compare(a.to_record(), _snap(home, tmp_path).to_record())
    assert res["decisions"]["gpt"] == "ok" and any(c["category"] == bl.INFORMATIONAL for c in res["changes"])
    srcs = {s.source_id: s.path for s in bl.default_sources(env={"CODEX_HOME": "/opt/codexhome"})}
    assert srcs["codex.skills"] == "/opt/codexhome/skills"


def test_version_change_reaccepts_and_unknown_version_stops(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    a = _snap(home, tmp_path)
    b = _snap(home, tmp_path, versions={**VERSIONS, "gemini": "1.4.13"})
    assert _decisions(a, b) == {"claude": "ok", "gemini": "reaccept", "gpt": "ok"}
    c = _snap(home, tmp_path, versions={**VERSIONS, "gpt": None})
    assert _decisions(a, c)["gpt"] == "stop"


@pytest.mark.parametrize("credits", [5, None])
def test_credits_must_be_confirmed_zero(tmp_path: Path, credits: object) -> None:
    home = _make_home(tmp_path)
    a = _snap(home, tmp_path)
    assert _decisions(a, _snap(home, tmp_path, gemini_credits=credits))["gemini"] == "stop"


def test_new_ancestor_instruction_file_stops_all_lines(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    a = _snap(home, tmp_path)
    (tmp_path / "AGENTS.md").write_text("injected instructions")
    assert _decisions(a, _snap(home, tmp_path)) == {"claude": "stop", "gemini": "stop", "gpt": "stop"}


def test_internal_symlink_hashes_target_and_external_symlink_stops(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    skills = home / ".claude" / "skills"
    (skills / "real.md").write_text("inside v1")
    os.symlink(skills / "real.md", skills / "link.md")
    a = _snap(home, tmp_path)
    (skills / "real.md").write_text("inside v2")
    assert _decisions(a, _snap(home, tmp_path))["claude"] == "stop"
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    os.symlink(outside, skills / "external.md")
    snap = _snap(home, tmp_path)
    assert snap.sources["claude.skills"]["unsafe_symlinks"] == ["external.md"]
    assert _decisions(snap, snap)["claude"] == "stop"


def test_symlinked_source_is_not_followed(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    real = tmp_path / "elsewhere"
    real.mkdir()
    (real / "a.md").write_text("x")
    os.symlink(real, home / ".claude" / "agents")
    snap = _snap(home, tmp_path)
    rec = snap.sources["claude.agents"]
    assert "symlink" in rec["error"] and "hash" not in rec


def test_approve_requires_clean_snapshot_and_passing_autoload(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    log = tmp_path / "acceptance.jsonl"
    assert bl.check(_snap(home, tmp_path), log=log)["decisions"] == {"claude": "stop", "gemini": "stop", "gpt": "stop"}
    with pytest.raises(ValueError):
        bl.approve(_snap(home, tmp_path), "Louis", "auto_missing", log=log)
    append_record(log, {"type": "autoload_result", "record_id": "auto_1", "passed": True})
    with pytest.raises(ValueError):
        bl.approve(_snap(home, tmp_path, gemini_credits=None), "Louis", "auto_1", log=log)
    bl.approve(_snap(home, tmp_path), "Louis", "auto_1", log=log)
    assert bl.check(_snap(home, tmp_path), log=log)["decisions"] == {"claude": "ok", "gemini": "ok", "gpt": "ok"}


def test_cli_versions_parsing() -> None:
    outputs = {"claude": "2.1.316 (Claude Code)\n", "codex": "codex-cli 0.166.0\n", "agy": "1.4.12:\n· note\n"}
    assert bl.cli_versions(lambda argv: outputs[argv[0]]) == {"claude": "2.1.316", "gpt": "0.166.0", "gemini": "1.4.12"}
    assert bl.cli_versions(lambda argv: None) == {"claude": None, "gpt": None, "gemini": None}
