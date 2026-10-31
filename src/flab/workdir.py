"""Isolated temporary directory management for model attempts."""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

_CREATED_ATTEMPT_BASES: dict[Path, Path] = {}


def default_base_dir() -> Path:
    """Return the default base directory for attempt folders: ``<tempdir>/flab-runs``."""
    return Path(tempfile.gettempdir()) / "flab-runs"


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def make_attempt_dir(base: str | Path | None = None) -> Path:
    """Create a new mode-0700 directory with a random hex name outside the project root.

    Raises ValueError if the target directory lies inside the project directory,
    or FileExistsError if the target path already exists.
    """
    raw_base = Path(base) if base is not None else default_base_dir()
    # Resolve existing ancestor to handle macOS /tmp -> /private/tmp symlinks accurately
    resolved_project = PROJECT_ROOT.resolve()

    if raw_base.exists() and not raw_base.is_dir():
        raise FileExistsError(f"Base path exists and is not a directory: {raw_base}")

    # Check before creating base directory whether it resides inside PROJECT_ROOT
    probe_base = raw_base.resolve()
    if _is_within(probe_base, resolved_project):
        raise ValueError(f"Attempt base directory must be outside project root ({resolved_project}): {probe_base}")

    raw_base.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved_base = raw_base.resolve()
    # mkdir(mode=...) does not tighten a base that already existed with broader permissions.
    if resolved_base.stat().st_uid == os.getuid() and (resolved_base.stat().st_mode & 0o077):
        os.chmod(resolved_base, 0o700)
    if _is_within(resolved_base, resolved_project):
        raise ValueError(f"Attempt base directory must be outside project root ({resolved_project}): {resolved_base}")

    token = secrets.token_hex(16)
    if not token or "/" in token or "\\" in token or token in (".", ".."):
        raise ValueError("Invalid attempt directory token")

    target = resolved_base / token
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Attempt directory already exists: {target}")

    if _is_within(target, resolved_project):
        raise ValueError(f"Attempt directory cannot be inside project root ({resolved_project}): {target}")

    target.mkdir(mode=0o700, parents=False, exist_ok=False)
    os.chmod(target, 0o700)
    resolved_target = target.resolve()
    _CREATED_ATTEMPT_BASES[resolved_target] = resolved_base
    return resolved_target


def remove_attempt_dir(path: str | Path, base: str | Path | None = None) -> None:
    """Remove an attempt directory only if it resides strictly underneath ``base``."""
    raw_path = Path(path)
    if raw_path.is_symlink():
        raise ValueError(f"Refusing to remove symlink attempt directory: {raw_path}")

    resolved_path = raw_path.resolve()
    resolved_project = PROJECT_ROOT.resolve()

    if _is_within(resolved_path, resolved_project):
        raise ValueError(f"Refusing to remove directory inside project root: {resolved_path}")

    if base is not None:
        resolved_base = Path(base).resolve()
    else:
        resolved_base = _CREATED_ATTEMPT_BASES.get(resolved_path, default_base_dir().resolve())

    if _is_within(resolved_base, resolved_project):
        raise ValueError(f"Base directory cannot be inside project root: {resolved_base}")

    if resolved_path == resolved_base or resolved_path.parent != resolved_base:
        raise ValueError(
            f"Refusing to remove {resolved_path}: not a direct child of base directory {resolved_base}"
        )

    if not resolved_path.exists():
        _CREATED_ATTEMPT_BASES.pop(resolved_path, None)
        return

    if not resolved_path.is_dir():
        raise ValueError(f"Target is not a directory: {resolved_path}")

    shutil.rmtree(resolved_path)
    _CREATED_ATTEMPT_BASES.pop(resolved_path, None)
