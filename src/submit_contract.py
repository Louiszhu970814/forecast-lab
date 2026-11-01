"""Transport outcomes shared by the submission CLI and API implementation."""
from dataclasses import dataclass


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

