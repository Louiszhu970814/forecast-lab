"""Dynamic M5 rollout gate; malformed/missing config never enables a new type."""
import json
from pathlib import Path

from flab.workdir import PROJECT_ROOT
from numeric import TYPES

CONFIG_PATH = PROJECT_ROOT / "config" / "question_types.json"
LEGACY_TYPES = ("binary", "multiple_choice")


def supported_types(path: Path | None = None):
    try:
        flags = json.loads((path or CONFIG_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return LEGACY_TYPES
    if not isinstance(flags, dict) or set(flags) != set(TYPES) or any(type(v) is not bool for v in flags.values()):
        return LEGACY_TYPES
    return (*LEGACY_TYPES, *(kind for kind in TYPES if flags[kind]))


def is_supported(kind, path: Path | None = None):
    return kind in supported_types(path)
