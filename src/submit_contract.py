"""Transport outcomes shared by the submission CLI and API implementation."""
from dataclasses import dataclass


def own_forecast_records(post: dict) -> list[dict]:
    """Authenticated own-history container; unknown shapes are never empty history.

    The current API returns history/latest. Keep the original list contract for
    older transports; a missing latest/history field is not proof of absence.
    A withdrawn forecast still counts as a previous forecast.
    """
    question = post.get("question")
    values = question.get("my_forecasts") if isinstance(question, dict) else None
    if isinstance(values, dict):
        if "history" not in values or "latest" not in values or not isinstance(values["history"], list):
            raise ValueError("unrecognized own forecast container")
        latest = values["latest"]
        if latest is not None and (not isinstance(latest, dict) or not latest):
            raise ValueError("unrecognized latest forecast")
        values = [*values["history"], *([latest] if latest is not None else [])]
    if not isinstance(values, list) or any(not isinstance(v, dict) or not v for v in values):
        raise ValueError("unrecognized own forecast records")
    return values


class ConfirmedFailure(Exception):
    """Transport has authoritative evidence the write was NOT accepted (never a timeout).

    Evidence must be a credential-free description suitable for the local ledger.
    """


@dataclass(frozen=True)
class Readback:
    outcome: str = "unknown"  # found, not_accepted, unknown
    record: dict | None = None
    request_id: str | None = None
    evidence: str | None = None

