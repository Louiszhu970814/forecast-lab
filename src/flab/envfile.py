"""Strict .env file parser without shell expansion and secret lookup helper."""

from __future__ import annotations

import os
import re
from pathlib import Path

_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MISSING = object()


def load_env_file(path: str | Path) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines from ``path``.

    Ignores blank lines and ``#`` comment lines. Does not perform shell expansion.
    Never logs or prints secret values.
    """
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    result: dict[str, str] = {}

    for lineno, raw_line in enumerate(content.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if "=" not in raw_line:
            raise ValueError(f"Invalid env syntax at {file_path.name}:{lineno}: missing '='")

        key_part, _, val_part = raw_line.partition("=")
        key = key_part.strip()
        if key.startswith("export ") and key[7:].strip():
            key = key[7:].strip()

        if not _KEY_PATTERN.match(key):
            raise ValueError(f"Invalid variable name at {file_path.name}:{lineno}: {key!r}")

        val = val_part.strip()
        if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
            val = val[1:-1]

        result[key] = val

    return result


def get_secret(
    name: str,
    env_path: str | Path | None = None,
    *,
    default: str | None = _MISSING,  # type: ignore[assignment]
) -> str | None:
    """Look up secret ``name`` in ``os.environ`` first, then in ``env_path``.

    Raises :class:`KeyError` if not found (or empty) unless ``default`` is specified.
    """
    if not isinstance(name, str) or not _KEY_PATTERN.match(name):
        raise ValueError(f"Invalid secret name: {name!r}")

    env_val = os.environ.get(name)
    if env_val is not None and env_val != "":
        return env_val

    if env_path is not None:
        path_obj = Path(env_path)
        if path_obj.exists():
            file_vars = load_env_file(path_obj)
            file_val = file_vars.get(name)
            if file_val is not None and file_val != "":
                return file_val

    if default is not _MISSING:
        return default
    raise KeyError(f"Secret {name!r} is not set in environment or env file")
