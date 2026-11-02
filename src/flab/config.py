"""Configuration loading and strict schema validation for config/*.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

DEFAULT_CONFIG_DIR: Path = Path(__file__).resolve().parents[2] / "config"

PLAYERS: tuple[str, ...] = ("claude", "gemini", "gpt")
ACCEPTANCE_PLAYERS: tuple[str, ...] = PLAYERS
QUOTA_PLAYERS: tuple[str, ...] = PLAYERS
VALID_SUBMIT_MODES: frozenset[str] = frozenset({"local", "trial", "live"})


class ConfigError(ValueError):
    """Raised when a configuration file is missing a required field or has an invalid type/value."""

    def __init__(self, filename: str, field_path: str, reason: str) -> None:
        self.filename = filename
        self.field_path = field_path
        self.reason = reason
        location = f"{filename}: {field_path}" if field_path else filename
        super().__init__(f"{location}: {reason}")


def _join(parent: str, key: str) -> str:
    return f"{parent}.{key}" if parent else key


def _is_bool(val: Any) -> bool:
    return isinstance(val, bool)


def _is_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _is_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _is_nonempty_str(val: Any) -> bool:
    return isinstance(val, str) and len(val.strip()) > 0


def _require_dict(obj: Any, filename: str, path: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ConfigError(filename, path or "<root>", f"expected object/dict, got {type(obj).__name__}")
    return obj


def _require_field(mapping: dict[str, Any], key: str, filename: str, parent_path: str = "") -> tuple[Any, str]:
    field_path = _join(parent_path, key)
    if key not in mapping:
        raise ConfigError(filename, field_path, "missing required field")
    return mapping[key], field_path


def _require_str(mapping: dict[str, Any], key: str, filename: str, parent_path: str = "") -> str:
    val, field_path = _require_field(mapping, key, filename, parent_path)
    if not _is_nonempty_str(val):
        raise ConfigError(filename, field_path, f"expected non-empty string, got {type(val).__name__}")
    return val


def _require_bool(mapping: dict[str, Any], key: str, filename: str, parent_path: str = "") -> bool:
    val, field_path = _require_field(mapping, key, filename, parent_path)
    if not _is_bool(val):
        raise ConfigError(filename, field_path, f"expected boolean, got {type(val).__name__}")
    return val


def _require_int(
    mapping: dict[str, Any],
    key: str,
    filename: str,
    parent_path: str = "",
    *,
    min_value: int | None = None,
) -> int:
    val, field_path = _require_field(mapping, key, filename, parent_path)
    if not _is_int(val):
        raise ConfigError(filename, field_path, f"expected int, got {type(val).__name__}")
    if min_value is not None and val < min_value:
        raise ConfigError(filename, field_path, f"expected int >= {min_value}, got {val}")
    return val


def _require_number(
    mapping: dict[str, Any],
    key: str,
    filename: str,
    parent_path: str = "",
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    val, field_path = _require_field(mapping, key, filename, parent_path)
    if not _is_number(val):
        raise ConfigError(filename, field_path, f"expected number, got {type(val).__name__}")
    num = float(val)
    if min_value is not None and num < min_value:
        raise ConfigError(filename, field_path, f"expected number >= {min_value}, got {val}")
    if max_value is not None and num > max_value:
        raise ConfigError(filename, field_path, f"expected number <= {max_value}, got {val}")
    return num


def _require_clip_pair(mapping: dict[str, Any], key: str, filename: str, parent_path: str = "") -> list[float]:
    val, field_path = _require_field(mapping, key, filename, parent_path)
    if not isinstance(val, list) or len(val) != 2 or not all(_is_number(x) for x in val):
        raise ConfigError(filename, field_path, "expected 2-element numeric list [low, high]")
    low, high = float(val[0]), float(val[1])
    if not (0.0 < low < high < 1.0):
        raise ConfigError(filename, field_path, f"expected 0 < low < high < 1, got {val}")
    return [low, high]


def validate_run_config(data: Any, filename: str = "run_config_v2.json") -> dict[str, Any]:
    """Validate ``run_config_v2.json`` structure and types."""
    root = _require_dict(data, filename, "")
    _require_str(root, "version", filename)

    players_raw, players_path = _require_field(root, "players", filename)
    players = _require_dict(players_raw, filename, players_path)
    for p in PLAYERS:
        p_raw, p_path = _require_field(players, p, filename, players_path)
        p_dict = _require_dict(p_raw, filename, p_path)

        t1_raw, t1_path = _require_field(p_dict, "tier1", filename, p_path)
        t1 = _require_dict(t1_raw, filename, t1_path)
        _require_str(t1, "model", filename, t1_path)
        _require_str(t1, "effort", filename, t1_path)

        t2_raw, t2_path = _require_field(p_dict, "tier2", filename, p_path)
        t2 = _require_dict(t2_raw, filename, t2_path)
        _require_str(t2, "model", filename, t2_path)
        _require_str(t2, "effort", filename, t2_path)
        _require_bool(t2, "subagents_enabled", filename, t2_path)
        _require_int(t2, "max_subagents", filename, t2_path, min_value=0)

    timeouts_raw, timeouts_path = _require_field(root, "timeouts_s", filename)
    timeouts = _require_dict(timeouts_raw, filename, timeouts_path)
    _require_int(timeouts, "tier1", filename, timeouts_path, min_value=1)
    _require_int(timeouts, "tier2", filename, timeouts_path, min_value=1)
    _require_int(timeouts, "repair", filename, timeouts_path, min_value=1)

    esc_raw, esc_path = _require_field(root, "escalation", filename)
    esc = _require_dict(esc_raw, filename, esc_path)
    _require_number(esc, "binary_log_odds_diff", filename, esc_path, min_value=0.0)
    _require_clip_pair(esc, "binary_clip", filename, esc_path)
    _require_number(esc, "multiple_choice_prob_diff", filename, esc_path, min_value=0.0, max_value=1.0)
    _require_number(esc, "market_logit_diff", filename, esc_path, min_value=0.0)
    _require_number(esc, "market_price_epsilon", filename, esc_path, min_value=0.0, max_value=0.5)
    flags_val, flags_path = _require_field(esc, "flags", filename, esc_path)
    if not isinstance(flags_val, list) or not flags_val or not all(_is_nonempty_str(x) for x in flags_val):
        raise ConfigError(filename, flags_path, "expected non-empty list of strings")

    dl_raw, dl_path = _require_field(root, "deadlines", filename)
    dl = _require_dict(dl_raw, filename, dl_path)
    _require_int(dl, "safety_margin_minutes", filename, dl_path, min_value=1)
    _require_int(dl, "submit_lead_minutes", filename, dl_path, min_value=1)
    _require_number(dl, "duration_quantile", filename, dl_path, min_value=0.0, max_value=1.0)

    acc_raw, acc_path = _require_field(root, "acceptance", filename)
    acc = _require_dict(acc_raw, filename, acc_path)
    for ap in ACCEPTANCE_PLAYERS:
        ap_raw, ap_path = _require_field(acc, ap, filename, acc_path)
        ap_dict = _require_dict(ap_raw, filename, ap_path)
        _require_str(ap_dict, "model", filename, ap_path)
        _require_str(ap_dict, "effort", filename, ap_path)
        _require_str(ap_dict, "cli_status", filename, ap_path)

    return root


def validate_agg_config(data: Any, filename: str = "agg_v1.json") -> dict[str, Any]:
    """Validate ``agg_v1.json`` structure and types."""
    root = _require_dict(data, filename, "")
    _require_str(root, "version", filename)

    bin_raw, bin_path = _require_field(root, "binary", filename)
    binary = _require_dict(bin_raw, filename, bin_path)
    _require_str(binary, "method", filename, bin_path)
    _require_number(binary, "extremizing_factor", filename, bin_path, min_value=0.0)
    _require_clip_pair(binary, "clip", filename, bin_path)

    mc_raw, mc_path = _require_field(root, "multiple_choice", filename)
    mc = _require_dict(mc_raw, filename, mc_path)
    _require_str(mc, "method", filename, mc_path)
    _require_number(mc, "smoothing_epsilon", filename, mc_path, min_value=0.0, max_value=1.0)

    w_raw, w_path = _require_field(root, "weights", filename)
    weights = _require_dict(w_raw, filename, w_path)
    for p in PLAYERS:
        _require_number(weights, p, filename, w_path, min_value=0.0)

    return root


def validate_quota_policy(data: Any, filename: str = "quota_policy.json") -> dict[str, Any]:
    """Validate ``quota_policy.json`` structure and types."""
    root = _require_dict(data, filename, "")
    _require_int(root, "max_concurrency_per_player", filename, min_value=1)
    _require_number(root, "token_multiplier_cap", filename, min_value=1.0)

    prio_val, prio_path = _require_field(root, "priority_order", filename)
    if not isinstance(prio_val, list) or not prio_val or not all(_is_nonempty_str(x) for x in prio_val):
        raise ConfigError(filename, prio_path, "expected non-empty list of strings")

    players_raw, players_path = _require_field(root, "players", filename)
    players = _require_dict(players_raw, filename, players_path)

    agy_raw, agy_path = _require_field(players, "gemini", filename, players_path)
    agy = _require_dict(agy_raw, filename, agy_path)
    _require_number(agy, "weekly_cap", filename, agy_path, min_value=0.0, max_value=1.0)
    _require_number(agy, "tier2_per_question_cap", filename, agy_path, min_value=0.0, max_value=1.0)
    credits_zero = _require_bool(agy, "credits_must_be_zero", filename, agy_path)
    if not credits_zero:
        raise ConfigError(filename, _join(agy_path, "credits_must_be_zero"), "must be true")
    # D101: optional runaway guard; when present it caps registered weekly attempt caps.
    if "weekly_attempts_cap" in agy:
        g_cap = agy["weekly_attempts_cap"]
        if g_cap is not None and (not _is_int(g_cap) or g_cap < 1):
            raise ConfigError(filename, _join(agy_path, "weekly_attempts_cap"), "expected positive int or null")

    for p in ("claude", "gpt"):
        p_raw, p_path = _require_field(players, p, filename, players_path)
        p_dict = _require_dict(p_raw, filename, p_path)
        cap_val, cap_path = _require_field(p_dict, "weekly_attempts_cap", filename, p_path)
        if cap_val is not None and (not _is_int(cap_val) or cap_val < 1):
            raise ConfigError(filename, cap_path, "expected positive int or null")
        _require_number(p_dict, "token_multiplier_cap", filename, p_path, min_value=1.0)

    return root


def validate_tournaments(data: Any, filename: str = "tournaments.json") -> dict[str, Any]:
    """Validate ``tournaments.json`` structure and types."""
    root = _require_dict(data, filename, "")
    t_raw, t_path = _require_field(root, "tournaments", filename)
    tournaments = _require_dict(t_raw, filename, t_path)

    for key in ("main", "minibench", "bot_testing_area"):
        entry_raw, entry_path = _require_field(tournaments, key, filename, t_path)
        entry = _require_dict(entry_raw, filename, entry_path)
        _require_str(entry, "slug", filename, entry_path)
        id_val, id_path = _require_field(entry, "id", filename, entry_path)
        if id_val is not None and not _is_int(id_val):
            raise ConfigError(filename, id_path, "expected int or null")
        _require_bool(entry, "submit_whitelisted", filename, entry_path)

    tg_val, tg_path = _require_field(root, "trial_ground_types", filename)
    if not isinstance(tg_val, list) or not tg_val or not all(_is_nonempty_str(x) for x in tg_val):
        raise ConfigError(filename, tg_path, "expected non-empty list of strings")

    return root


def validate_submit_mode(data: Any, filename: str = "submit_mode.json") -> dict[str, Any]:
    """Validate ``submit_mode.json`` structure and types."""
    root = _require_dict(data, filename, "")
    mode = _require_str(root, "mode", filename)
    if mode not in VALID_SUBMIT_MODES:
        raise ConfigError(filename, "mode", f"expected one of {sorted(VALID_SUBMIT_MODES)}, got {mode!r}")
    return root


def validate_players_enabled(data: Any, filename: str = "players_enabled.json") -> dict[str, Any]:
    """Validate ``players_enabled.json`` structure and types."""
    root = _require_dict(data, filename, "")
    for p in PLAYERS:
        _require_bool(root, p, filename)
    return root


_VALIDATORS_BY_FILENAME: dict[str, Callable[[Any, str], dict[str, Any]]] = {
    "run_config_v2.json": validate_run_config,
    "agg_v1.json": validate_agg_config,
    "quota_policy.json": validate_quota_policy,
    "tournaments.json": validate_tournaments,
    "submit_mode.json": validate_submit_mode,
    "players_enabled.json": validate_players_enabled,
}


def validate_config(filename: str, data: Any) -> dict[str, Any]:
    """Validate ``data`` according to ``filename``."""
    name = Path(filename).name
    validator = _VALIDATORS_BY_FILENAME.get(name)
    if validator is None:
        raise ConfigError(name, "", f"unknown config file name: {name}")
    return validator(data, name)


def load_config_file(path: str | Path) -> dict[str, Any]:
    """Read and validate a single JSON configuration file."""
    file_path = Path(path)
    filename = file_path.name
    if not file_path.exists():
        raise ConfigError(filename, "", f"configuration file not found: {file_path}")
    try:
        raw_text = file_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ConfigError(filename, "<json>", f"invalid JSON: {exc.msg}") from exc

    return validate_config(filename, data)


def load_all_configs(config_dir: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Load and validate all 6 standard configuration files from ``config_dir``."""
    base_dir = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    result: dict[str, dict[str, Any]] = {}
    for filename in _VALIDATORS_BY_FILENAME:
        result[filename] = load_config_file(base_dir / filename)
    return result


def load_run_config(config_dir: str | Path | None = None) -> dict[str, Any]:
    base_dir = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    return load_config_file(base_dir / "run_config_v2.json")


def load_agg_config(config_dir: str | Path | None = None) -> dict[str, Any]:
    base_dir = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    return load_config_file(base_dir / "agg_v1.json")


def load_quota_policy(config_dir: str | Path | None = None) -> dict[str, Any]:
    base_dir = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    return load_config_file(base_dir / "quota_policy.json")


def load_tournaments(config_dir: str | Path | None = None) -> dict[str, Any]:
    base_dir = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    return load_config_file(base_dir / "tournaments.json")


def load_submit_mode(config_dir: str | Path | None = None) -> dict[str, Any]:
    base_dir = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    return load_config_file(base_dir / "submit_mode.json")


def load_players_enabled(config_dir: str | Path | None = None) -> dict[str, Any]:
    base_dir = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    return load_config_file(base_dir / "players_enabled.json")
