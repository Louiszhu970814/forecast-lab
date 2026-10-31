"""Tests for timeutil, ids, and envfile modules."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flab.envfile import get_secret, load_env_file
from flab.ids import canonical_json, hash_obj, new_id, sha256_hex
from flab.timeutil import parse_iso, to_iso, utc_now


def test_utc_now_is_aware_utc() -> None:
    now = utc_now()
    assert now.tzinfo is timezone.utc
    assert now.utcoffset() == timedelta(0)


def test_to_iso_format_and_timezone_conversion() -> None:
    dt_utc = datetime(2026, 10, 31, 8, 52, 36, 123456, tzinfo=timezone.utc)
    iso_str = to_iso(dt_utc)
    assert iso_str == "2026-10-31T08:52:36.123Z"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", iso_str)

    # Non-UTC timezone should be converted to UTC
    tz_plus8 = timezone(timedelta(hours=8))
    dt_plus8 = datetime(2026, 10, 31, 16, 52, 36, 123000, tzinfo=tz_plus8)
    assert to_iso(dt_plus8) == "2026-10-31T08:52:36.123Z"


def test_to_iso_rejects_naive_datetime() -> None:
    naive = datetime(2026, 10, 31, 12, 0, 0)
    with pytest.raises(ValueError, match="timezone"):
        to_iso(naive)


def test_parse_iso_accepts_z_and_offset() -> None:
    dt_z = parse_iso("2026-10-31T08:52:36.123Z")
    dt_offset = parse_iso("2026-10-31T08:52:36.123+00:00")
    dt_plus8 = parse_iso("2026-10-31T16:52:36.123+08:00")

    assert dt_z == dt_offset == dt_plus8
    assert dt_z.tzinfo is timezone.utc


@pytest.mark.parametrize(
    "bad_input",
    [
        "2026-10-31T08:52:36",
        "2026-10-31T08:52:36.123",
        "2026-10-31",
        "2026-10-31T08:52:36z",
        "",
        "not-a-date",
    ],
)
def test_parse_iso_rejects_missing_or_invalid_timezone(bad_input: str) -> None:
    with pytest.raises(ValueError):
        parse_iso(bad_input)


def test_new_id_format_uniqueness_and_ordering() -> None:
    id1 = new_id("attempt")
    time.sleep(0.002)
    id2 = new_id("attempt")

    assert id1.startswith("attempt_")
    assert id2.startswith("attempt_")
    assert id1 != id2
    assert id1 < id2

    with pytest.raises(ValueError):
        new_id("")
    with pytest.raises(ValueError):
        new_id("___")


def test_canonical_json_and_hashes() -> None:
    obj1 = {"b": 2, "a": [1, {"d": "中文", "c": True}]}
    obj2 = {"a": [1, {"c": True, "d": "中文"}], "b": 2}

    c1 = canonical_json(obj1)
    c2 = canonical_json(obj2)
    assert c1 == c2
    assert c1 == '{"a":[1,{"c":true,"d":"中文"}],"b":2}'

    assert sha256_hex("abc") == sha256_hex(b"abc")
    assert len(sha256_hex("abc")) == 64
    assert hash_obj(obj1) == hash_obj(obj2)


def test_envfile_parsing_and_no_shell_expansion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "# Comment line\n"
        "\n"
        "PLAIN_KEY=plain_value\n"
        "QUOTED_DOUBLE=\"$HOME/${USER}/$(whoami)\"\n"
        "QUOTED_SINGLE='$NOT_EXPANDED'\n"
        "EXPORTED_KEY_VALUE=val=with=equals\n"
        "export WITH_EXPORT=exported_val\n",
        encoding="utf-8",
    )

    parsed = load_env_file(env_file)
    assert parsed["PLAIN_KEY"] == "plain_value"
    # Must NOT perform shell expansion
    assert parsed["QUOTED_DOUBLE"] == "$HOME/${USER}/$(whoami)"
    assert parsed["QUOTED_SINGLE"] == "$NOT_EXPANDED"
    assert parsed["EXPORTED_KEY_VALUE"] == "val=with=equals"
    assert parsed["WITH_EXPORT"] == "exported_val"

    # get_secret checks environment first, then file
    monkeypatch.setenv("PLAIN_KEY", "from_environ")
    assert get_secret("PLAIN_KEY", env_file) == "from_environ"

    monkeypatch.delenv("QUOTED_SINGLE", raising=False)
    assert get_secret("QUOTED_SINGLE", env_file) == "$NOT_EXPANDED"

    with pytest.raises(KeyError):
        get_secret("NON_EXISTENT_SECRET_XYZ", env_file)

    assert get_secret("NON_EXISTENT_SECRET_XYZ", env_file, default="fallback") == "fallback"


def test_envfile_invalid_syntax_does_not_leak_value(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.env"
    secret_marker = "SUPER_SECRET_VALUE_DO_NOT_LEAK"
    bad_file.write_text(f"INVALID LINE {secret_marker}\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_env_file(bad_file)
    assert secret_marker not in str(exc_info.value)
