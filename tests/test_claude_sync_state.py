import copy
import json
import os
from pathlib import Path

import pytest

import claude_sync_state as S

BUCKET = "11111111-1111-4111-8111-111111111111_22222222-2222-4222-8222-222222222222"
TOKEN = "33333333-3333-4333-8333-333333333333"
OTHER_TOKEN = "44444444-4444-4444-8444-444444444444"


def file(tmp_path, name, text):
    rel = f"synced/{BUCKET}/{name}"
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path, rel


def manifest():
    return {"etag": 'W/"first"', "parserVersion": 1002, "rows": [
        {"name": "anthropic-plugin-directory", "display_name": "Anthropic Directory",
         "scope": "default", "source": {"source": "claudeai"},
         "id": "marketplace_one", "updated_at": "2026-11-03T03:44:12.531165Z"}]}


def parse_manifest(tmp_path, value):
    path, rel = file(tmp_path, ".marketplaces.json", json.dumps(value))
    return S.metadata_parts(path, rel)


def test_round_token_only_is_state_but_listed_for_remains_content(tmp_path):
    path, rel = file(tmp_path, ".last-complete-round", TOKEN + " ")
    content, state = S.metadata_parts(path, rel)
    assert content["listedFor"] == [] and content["token_type"] == "uuid"
    assert state["token"] == TOKEN
    path.write_text(OTHER_TOKEN + " ")
    changed_content, changed_state = S.metadata_parts(path, rel)
    assert changed_content == content and changed_state != state
    path.write_text(OTHER_TOKEN + " skills,plugins,some.id-other_1")
    loaded_content, _ = S.metadata_parts(path, rel)
    assert loaded_content != content
    assert loaded_content["listedFor"] == ["skills", "plugins", "some.id-other_1"]


@pytest.mark.parametrize("suffix", ["", "  ", " \n", " \t", " plugins,", " ,plugins",
                                    " plugins,,skills", " plugins skills", " ../x", " /x", " x;y",
                                    " " + "a" * 65, " " + ",".join(["x"] * 65)])
def test_round_marker_rejects_extra_content_bad_names_or_excess(tmp_path, suffix):
    path, rel = file(tmp_path, ".last-complete-round", TOKEN + suffix)
    with pytest.raises(ValueError): S.metadata_parts(path, rel)


@pytest.mark.parametrize("token", ["not-a-uuid", TOKEN.replace("-", ""), TOKEN + "x", " " + TOKEN])
def test_round_requires_exact_uuid_token(tmp_path, token):
    path, rel = file(tmp_path, ".last-complete-round", token + " ")
    with pytest.raises(ValueError): S.metadata_parts(path, rel)


def test_marketplace_etag_and_timestamp_values_only_are_state(tmp_path):
    before = manifest()
    content, state = parse_manifest(tmp_path, before)
    after = copy.deepcopy(before)
    after["etag"] = '"second"'
    after["rows"][0]["updated_at"] = "2026-11-04T01:02:03+08:00"
    new_content, new_state = parse_manifest(tmp_path, after)
    assert content == new_content
    assert state["etag"] != new_state["etag"]
    assert state["updated_at"] != new_state["updated_at"]
    assert before == manifest()  # The parser does not mutate caller or input data.
    assert content["manifest"]["etag"] == {"state_type": "string"}
    assert content["manifest"]["rows"][0]["updated_at"] == {"state_type": "iso_timestamp_string"}


@pytest.mark.parametrize("change", ["parser", "name", "scope", "source", "id", "display", "unknown-root", "unknown-row", "unknown-source"])
def test_marketplace_routing_versions_and_unknown_fields_are_protected(tmp_path, change):
    before = manifest()
    content, _ = parse_manifest(tmp_path, before)
    after = copy.deepcopy(before)
    row = after["rows"][0]
    if change == "parser": after["parserVersion"] += 1
    elif change == "name": row["name"] = "different"
    elif change == "scope": row["scope"] = "user"
    elif change == "source": row["source"]["source"] = "github"
    elif change == "id": row["id"] = "other-id"
    elif change == "display": row["display_name"] = "Different"
    elif change == "unknown-root": after["unrecognized"] = {"content": "changed"}
    elif change == "unknown-row": row["unrecognized"] = ["changed"]
    else: row["source"]["unrecognized"] = True
    assert parse_manifest(tmp_path, after)[0] != content


def test_marketplace_row_order_is_protected_and_json_formatting_is_state(tmp_path):
    value = manifest()
    other = copy.deepcopy(value["rows"][0]);other["id"] = "second"
    value["rows"].append(other)
    content, state = parse_manifest(tmp_path, value)
    path, rel = file(tmp_path, ".marketplaces.json", json.dumps(value, indent=2, sort_keys=True))
    formatted_content, formatted_state = S.metadata_parts(path, rel)
    assert formatted_content == content and formatted_state["raw_sha256"] != state["raw_sha256"]
    value["rows"].reverse()
    assert parse_manifest(tmp_path, value)[0] != content


@pytest.mark.parametrize("key", ["etag", "rows", "parserVersion"])
def test_marketplace_required_top_level_fields_cannot_disappear(tmp_path, key):
    value = manifest();del value[key]
    with pytest.raises(ValueError): parse_manifest(tmp_path, value)


@pytest.mark.parametrize("key", ["name", "display_name", "scope", "source", "id", "updated_at"])
def test_marketplace_required_row_fields_cannot_disappear(tmp_path, key):
    value = manifest();del value["rows"][0][key]
    with pytest.raises(ValueError): parse_manifest(tmp_path, value)


@pytest.mark.parametrize("change", ["etag-type", "etag-empty", "etag-newline", "rows-type", "row-type",
                                    "parser-bool", "parser-zero", "source-type", "source-empty", "id-type", "name-empty"])
def test_marketplace_schema_errors_are_not_ignored_as_state(tmp_path, change):
    value = manifest();row = value["rows"][0]
    if change == "etag-type": value["etag"] = None
    elif change == "etag-empty": value["etag"] = ""
    elif change == "etag-newline": value["etag"] = "bad\nheader"
    elif change == "rows-type": value["rows"] = {}
    elif change == "row-type": value["rows"] = [None]
    elif change == "parser-bool": value["parserVersion"] = True
    elif change == "parser-zero": value["parserVersion"] = 0
    elif change == "source-type": row["source"] = "claudeai"
    elif change == "source-empty": row["source"] = {}
    elif change == "id-type": row["id"] = 123
    else: row["name"] = ""
    with pytest.raises(ValueError): parse_manifest(tmp_path, value)


@pytest.mark.parametrize("when", [None, 3, "2026-11-03", "2026-11-03T03:44:12", "2026-02-30T03:44:12Z",
                                  "2026-11-03T03:44:12z", "2026-11-03T03:44:12Z\n", "x", "2026-11-03T25:00:00Z",
                                  "2026-11-03T03:44:12+00:60", "2026-11-03T03:44:12+24:00"])
def test_marketplace_timestamp_requires_valid_timezone_aware_iso_value(tmp_path, when):
    value = manifest();value["rows"][0]["updated_at"] = when
    with pytest.raises(ValueError): parse_manifest(tmp_path, value)


@pytest.mark.parametrize("payload", ['{"etag":"one","etag":"two","rows":[],"parserVersion":1002}',
                                     '{"etag":"one","rows":[],"parserVersion":1002,"x":{"a":1,"a":2}}',
                                     '{"etag":"one","rows":[],"parserVersion":1002,"x":NaN}',
                                     '{"etag":"one","rows":[],"parserVersion":1002,"x":Infinity}',
                                     '{"etag":"one","rows":[],"parserVersion":1002,"x":1e999}',
                                     '{"etag":"one","rows":[],"parserVersion":1002,"x":"\\ud800"}',
                                     '[1,2,3]', '{'])
def test_duplicate_keys_invalid_json_and_nonfinite_values_are_rejected(tmp_path, payload):
    path, rel = file(tmp_path, ".marketplaces.json", payload)
    with pytest.raises(ValueError): S.metadata_parts(path, rel)


@pytest.mark.parametrize("rel", [".last-complete-round", f"synced/{BUCKET}/nested/.last-complete-round",
                                 "synced/not-a-bucket/.marketplaces.json", f"synced/{BUCKET}/.marketplaces.json\n",
                                 f"other/synced/{BUCKET}/.marketplaces.json", f"synced/{BUCKET}/auth.json"])
def test_unmatched_paths_are_not_opened(tmp_path, monkeypatch, rel):
    monkeypatch.setattr(S.os, "open", lambda *a, **k: pytest.fail("unmatched path opened"))
    assert S.metadata_parts(tmp_path / "do-not-open", rel) is None


def test_a_matching_relative_name_cannot_authorize_a_different_file(tmp_path):
    with pytest.raises(ValueError, match="relative name"):
        S.metadata_parts(tmp_path / "auth.json", f"synced/{BUCKET}/.marketplaces.json")


@pytest.mark.parametrize("which", ["file", "bucket", "synced", "root"])
def test_symlinks_at_each_opened_component_are_rejected(tmp_path, which):
    root = tmp_path / "root";root.mkdir()
    path, rel = file(root, ".last-complete-round", TOKEN + " ")
    link = {"file": path, "bucket": path.parent, "synced": path.parent.parent, "root": root}[which]
    target = link.with_name(link.name + "-target")
    link.rename(target);link.symlink_to(target)
    with pytest.raises((OSError, ValueError)): S.metadata_parts(path, rel)


def test_fifo_is_rejected_without_waiting_for_a_writer(tmp_path):
    path, rel = file(tmp_path, ".last-complete-round", "")
    path.unlink();os.mkfifo(path)
    with pytest.raises(ValueError, match="regular file"): S.metadata_parts(path, rel)


@pytest.mark.parametrize("kind", ["marker", "manifest", "rows", "depth"])
def test_input_bounds_fail_closed(tmp_path, kind):
    name = ".last-complete-round" if kind == "marker" else ".marketplaces.json"
    if kind == "marker": text = "a" * (S.MAX_MARKER_BYTES + 1)
    elif kind == "manifest": text = " " * (S.MAX_MANIFEST_BYTES + 1)
    elif kind == "rows":
        value = manifest();value["rows"] *= S.MAX_ROWS + 1;text = json.dumps(value)
    else:
        value = manifest();nested = []
        for _ in range(S.MAX_JSON_DEPTH + 1): nested = [nested]
        value["unknown"] = nested;text = json.dumps(value)
    path, rel = file(tmp_path, name, text)
    with pytest.raises(ValueError): S.metadata_parts(path, rel)


def test_file_replaced_during_read_is_refused(tmp_path, monkeypatch):
    path, rel = file(tmp_path, ".last-complete-round", TOKEN + " ")
    original = S.os.read
    replaced = False
    def replace(fd, count):
        nonlocal replaced
        block = original(fd, count)
        if not replaced:
            replacement = path.with_name("replacement")
            replacement.write_text(OTHER_TOKEN + " ");replacement.replace(path);replaced = True
        return block
    monkeypatch.setattr(S.os, "read", replace)
    with pytest.raises(OSError, match="changed during read"): S.metadata_parts(path, rel)


@pytest.mark.parametrize("which", ["bucket", "synced", "root"])
def test_directory_replaced_during_read_is_refused(tmp_path, monkeypatch, which):
    root = tmp_path / "root";root.mkdir()
    path, rel = file(root, ".last-complete-round", TOKEN + " ")
    directory = {"bucket": path.parent, "synced": path.parent.parent, "root": root}[which]
    original = S.os.read
    replaced = False
    def replace(fd, count):
        nonlocal replaced
        block = original(fd, count)
        if not replaced:
            directory.rename(directory.with_name(directory.name + "-old"))
            directory.mkdir();replaced = True
        return block
    monkeypatch.setattr(S.os, "read", replace)
    with pytest.raises(OSError, match="changed during read"): S.metadata_parts(path, rel)


def test_invalid_utf8_is_rejected(tmp_path):
    path, rel = file(tmp_path, ".last-complete-round", "")
    path.write_bytes(b"\xff")
    with pytest.raises(ValueError): S.metadata_parts(path, rel)
