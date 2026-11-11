"""Fixed D121 policy for new decisions; campaign configuration stays pinned."""

VERSION = "fail_soft_v1"
WINDOW_S = 24 * 3600
BREACH_COUNT = 2
COOLDOWN_S = 6 * 3600
MISMATCH_BACKOFF_S = 30 * 60
CLEANUP_GRACE_S = 120
SNAPSHOT_MAX_AGE_S = 90
QUOTA_QUERY_ATTEMPTS = 2
TIER1_QUOTA_RESERVE_CAP = 0.05
TEMPORARY_RECHECK_S = 600
# D149 (Louis, 2026-11-10): no check may stop a line indefinitely. Findings are
# recorded as warnings; only time-bounded waits (quota, mismatch backoff) and the
# switches Louis sets by hand (players_enabled, submit_mode, STOP_SUBMIT) remain.
PERSISTENT_STOPS = False
HARD_PREFLIGHT = ("line is disabled", "submit_mode is", "tier-2 subagents")
TIMED_WAIT_REASONS = frozenset({
    "quota_5h_wait",
    "quota_weekly_wait",
    "quota_exhausted",
    "advisory_quota_backoff",
    "model_mismatch_backoff",
    "model_mismatch_cooldown",
})
# A line that cannot answer within this hold does not delay a result that is ready.
STRAGGLER_HOLD_S = 15 * 60
