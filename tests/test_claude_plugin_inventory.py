import copy
import json
from pathlib import Path

import pytest

import baseline as B

BUCKET = "11111111-1111-4111-8111-111111111111_22222222-2222-4222-8222-222222222222"
TOKEN = "33333333-3333-4333-8333-333333333333"
NEXT = "44444444-4444-4444-8444-444444444444"


def files(tmp_path):
    root = tmp_path / "home/.claude/plugins"
    folder = root / "synced" / BUCKET
    folder.mkdir(parents=True)
    marker = folder / ".last-complete-round"
    marker.write_text(TOKEN + " ")
    index = folder / ".marketplaces.json"
    value = {"etag": 'W/"first"', "parserVersion": 1002, "rows": [{
        "name": "official", "display_name": "Official", "id": "marketplace_1", "scope": "default", "source": {"source": "claudeai"},
        "updated_at": "2026-11-03T00:00:00.000Z"}]}
    index.write_text(json.dumps(value))
    skill = folder / "official/skills/example/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("benign synthetic skill")
    return root, marker, index, value, skill


def snapshot(home, *, legacy=False):
    src = next(s for s in B.default_sources({}) if s.source_id == "claude.plugins")
    if legacy:
        from dataclasses import replace
        src = replace(src, kind="dir")
    return B.take_snapshot(home=home / "home", sources=[src], attempt_base=home / "blank", gemini_credits=0,
                           versions=dict.fromkeys(B.PLAYERS, "synthetic")).to_record()


def test_sync_tokens_and_refresh_times_do_not_authorize_changed_content(tmp_path):
    root, marker, index, value, skill = files(tmp_path)
    before = snapshot(tmp_path)
    marker.write_text(NEXT + " ")
    value["etag"] = 'W/"second"'
    value["rows"][0]["updated_at"] = "2026-11-03T01:00:00.000Z"
    index.write_text(json.dumps(value, indent=2))
    after = snapshot(tmp_path)
    result = B.compare(before, after)
    assert result["decisions"] == dict.fromkeys(B.PLAYERS, "ok")
    assert result["changes"] and all(r["category"] == B.INFORMATIONAL for r in result["changes"])
    a, b = before["sources"]["claude.plugins"], after["sources"]["claude.plugins"]
    assert a["file_hashes"] == b["file_hashes"]
    assert a["raw_file_hashes"] != b["raw_file_hashes"]
    assert a["entries"] == b["entries"] == 3
    skill.write_text("changed instructions")
    assert B.compare(before, snapshot(tmp_path))["decisions"]["claude"] == "stop"


@pytest.mark.parametrize("field", ["name", "id", "scope", "source", "parserVersion", "unknown", "listedFor"])
def test_routing_versions_unknown_fields_and_listed_for_remain_protected(tmp_path, field):
    root, marker, index, value, _ = files(tmp_path)
    before = snapshot(tmp_path)
    if field == "listedFor": marker.write_text(TOKEN + " another-scope")
    elif field == "parserVersion": value[field] = 1003
    else: value["rows"][0][field] = {"source": "other"} if field == "source" else "changed"
    index.write_text(json.dumps(value))
    assert B.compare(before, snapshot(tmp_path))["decisions"]["claude"] == "stop"


@pytest.mark.parametrize("change", ["remove", "rename", "bad-token", "extra-token", "bad-json", "duplicate", "symlink"])
def test_metadata_missing_malformed_or_linked_stops(tmp_path, change):
    _, marker, index, value, skill = files(tmp_path)
    before = snapshot(tmp_path)
    if change == "remove": marker.unlink()
    elif change == "rename": marker.rename(marker.with_name("other"))
    elif change == "bad-token": marker.write_text("instructions here")
    elif change == "extra-token": marker.write_text(TOKEN + "  extra")
    elif change == "bad-json": index.write_text("{")
    elif change == "duplicate": index.write_text('{"etag":"x","etag":"y","rows":[],"parserVersion":1002}')
    else:
        marker.unlink()
        marker.symlink_to(skill)
    assert B.compare(before, snapshot(tmp_path))["decisions"]["claude"] == "stop"


def test_old_aggregate_baseline_is_never_silently_migrated(tmp_path):
    files(tmp_path)
    legacy = snapshot(tmp_path, legacy=True)
    candidate = snapshot(tmp_path)
    assert B.compare(legacy, candidate)["decisions"]["claude"] == "stop"
    assert B.compare(candidate, candidate)["decisions"]["claude"] == "ok"


def test_same_filename_in_a_plugin_is_still_ordinary_content(tmp_path):
    root, _, _, _, _ = files(tmp_path)
    nested = root / "synced" / BUCKET / "official/.last-complete-round"
    nested.write_text(TOKEN + " ")
    before = snapshot(tmp_path)
    nested.write_text(NEXT + " ")
    assert B.compare(before, snapshot(tmp_path))["decisions"]["claude"] == "stop"


def test_pin_wrapper_fails_closed_and_preserves_arguments(tmp_path):
    import shutil
    import subprocess
    import os
    import hashlib
    project = Path(__file__).resolve().parents[1]
    wrapper = tmp_path / "launchd/bin/claude"
    wrapper.parent.mkdir(parents=True)
    shutil.copy2(project / "launchd/bin/claude", wrapper)
    missing = subprocess.run(["/bin/sh", str(wrapper), "--version"], capture_output=True)
    assert missing.returncode == 69
    binary = tmp_path / "data/cli_pins/claude/2.1.316"
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$DISABLE_AUTOUPDATER" "$@"\n')
    binary.chmod(0o700)
    # Synthetic executable receives its own exact test pin. Production pin stays fixed.
    wrapper.write_text(wrapper.read_text().replace(
        "64c327e92b2c5e0f78c0a980c5a518121e1f0d140ca771ca5dd4488d1a2dcb3f",
        hashlib.sha256(binary.read_bytes()).hexdigest()))
    result = subprocess.run(["/bin/sh", str(wrapper), "--arg", "space and $literal"],
                            capture_output=True, text=True, env={**os.environ, "DISABLE_AUTOUPDATER": "0"})
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["1", "--arg", "space and $literal"]

    binary.write_text(binary.read_text() + "# changed bytes\n")
    assert subprocess.run(["/bin/sh", str(wrapper)], capture_output=True).returncode == 69
