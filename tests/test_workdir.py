"""Tests for workdir attempt directory creation and safe deletion."""

from __future__ import annotations

import secrets
import stat
from pathlib import Path

import pytest

from flab.workdir import PROJECT_ROOT, make_attempt_dir, remove_attempt_dir


def test_make_and_remove_attempt_dir_permissions(tmp_path: Path) -> None:
    base = tmp_path / "flab-runs"
    attempt_dir = make_attempt_dir(base=base)
    try:
        assert attempt_dir.exists()
        assert attempt_dir.is_dir()
        assert attempt_dir.parent == base.resolve()
        mode = stat.S_IMODE(attempt_dir.stat().st_mode)
        assert mode == 0o700
    finally:
        remove_attempt_dir(attempt_dir)

    assert not attempt_dir.exists()


def test_make_and_remove_attempt_dir_default_base() -> None:
    attempt_dir = make_attempt_dir()
    try:
        assert attempt_dir.exists()
        assert attempt_dir.parent.name == "flab-runs"
        assert stat.S_IMODE(attempt_dir.stat().st_mode) == 0o700
    finally:
        remove_attempt_dir(attempt_dir)

    assert not attempt_dir.exists()


def test_make_attempt_dir_rejects_paths_inside_project() -> None:
    with pytest.raises(ValueError, match="outside project root"):
        make_attempt_dir(base=PROJECT_ROOT)

    with pytest.raises(ValueError, match="outside project root"):
        make_attempt_dir(base=PROJECT_ROOT / "sub" / "runs")


def test_make_attempt_dir_rejects_existing_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "flab-runs"
    monkeypatch.setattr(secrets, "token_hex", lambda _n=16: "deadbeef12345678")

    first = make_attempt_dir(base=base)
    try:
        with pytest.raises(FileExistsError):
            make_attempt_dir(base=base)
    finally:
        remove_attempt_dir(first, base=base)


def test_remove_attempt_dir_refuses_outside_base_or_base_itself(tmp_path: Path) -> None:
    base = tmp_path / "flab-runs"
    base.mkdir(parents=True, exist_ok=True)

    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    marker = outside_dir / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError):
        remove_attempt_dir(outside_dir, base=base)
    assert outside_dir.exists()
    assert marker.exists()

    with pytest.raises(ValueError):
        remove_attempt_dir(base, base=base)
    assert base.exists()

    with pytest.raises(ValueError):
        remove_attempt_dir(PROJECT_ROOT)
