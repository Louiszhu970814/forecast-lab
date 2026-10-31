from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import quota

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "gemini"


def _full_buckets_dict(
    *,
    argon: float = 0.7573143839836121,
    gemini_weekly: float = 0.9995706677436829,
    gemini_5h: float = 1.0,
    third_party_weekly: float = 1.0,
    third_party_5h: float = 1.0,
) -> dict[str, float]:
    return {
        "gemini-argon-wtu-weekly": argon,
        "gemini-weekly": gemini_weekly,
        "gemini-5h": gemini_5h,
        "3p-weekly": third_party_weekly,
        "3p-5h": third_party_5h,
    }


def test_snapshot_parses_usage_and_credits_fixtures() -> None:
    usage_text = (FIXTURES_DIR / "slash_usage.json").read_text(encoding="utf-8")
    credits_text = (FIXTURES_DIR / "slash_credits.json").read_text(encoding="utf-8")

    calls: list[list[str]] = []

    def fake_cmd(argv: list[str]) -> str:
        calls.append(argv)
        if "/usage" in argv:
            return usage_text
        if "/credits" in argv:
            return credits_text
        raise AssertionError(f"unexpected argv: {argv}")

    snap = quota.snapshot(_run_cmd=fake_cmd)
    assert len(calls) == 2
    assert snap.remaining_credits == 0
    assert quota.credits_ok(snap) is True

    argon = snap.buckets["gemini-argon-wtu-weekly"]
    assert argon.remaining_fraction == pytest.approx(0.8763402700424194)
    assert argon.reset_time == "2026-11-06T12:03:52Z"
    assert argon["remaining_fraction"] == pytest.approx(0.8763402700424194)
    assert argon["reset_time"] == "2026-11-06T12:03:52Z"

    gem_weekly = snap.buckets["gemini-weekly"]
    assert gem_weekly.remaining_fraction == pytest.approx(0.9995706677436829)
    assert gem_weekly.reset_time == "2026-11-06T14:08:50Z"

    gem_5h = snap.buckets["gemini-5h"]
    assert gem_5h.remaining_fraction == pytest.approx(1.0)


def test_credits_ok_requires_zero() -> None:
    snap_zero = quota.parse_snapshot(
        FIXTURES_DIR / "slash_usage.json",
        FIXTURES_DIR / "slash_credits.json",
    )
    assert quota.credits_ok(snap_zero) is True

    snap_positive = quota.parse_snapshot(
        FIXTURES_DIR / "slash_usage.json",
        {"command": {"name": "credits", "data": {"remaining_credits": 100}}},
    )
    assert quota.credits_ok(snap_positive) is False

    snap_missing = quota.parse_snapshot(FIXTURES_DIR / "slash_usage.json", {})
    assert quota.credits_ok(snap_missing) is False
    assert quota.credits_ok(None) is False


def test_judge_verified_when_only_expected_bucket_decreases() -> None:
    before = quota.parse_snapshot(FIXTURES_DIR / "usage_before.json")
    after_data = json.loads((FIXTURES_DIR / "usage_before.json").read_text(encoding="utf-8"))
    for grp in after_data["command"]["data"]["groups"]:
        for b in grp["buckets"]:
            if b["id"] == "gemini-argon-wtu-weekly":
                b["remaining_fraction"] = 0.7130000000000000
    after = quota.parse_snapshot(after_data)

    assert quota.judge(before, after, "gemini-argon-wtu-weekly") == "verified"


def test_judge_mismatch_when_flash_decreases_instead_of_argon() -> None:
    # Per coordinator ruling on Item 7 (PLAN §7.7): gemini-weekly and gemini-5h belong to the
    # same Flash/Pro group ("gemini"); both decreasing together counts as single-group consumption.
    before = quota.parse_snapshot(FIXTURES_DIR / "usage_before.json")
    after_data = json.loads((FIXTURES_DIR / "usage_after.json").read_text(encoding="utf-8"))
    for grp in after_data["command"]["data"]["groups"]:
        for b in grp["buckets"]:
            if b["id"] == "gemini-argon-wtu-weekly":
                b["remaining_fraction"] = before.buckets["gemini-argon-wtu-weekly"].remaining_fraction
    after = quota.parse_snapshot(after_data)

    # Expecting Argon -> mismatch because Argon did not decrease while Flash/Pro group did
    assert quota.judge(before, after, "gemini-argon-wtu-weekly") == "mismatch"
    # Expecting Flash/Gemini -> verified because only the Flash/Pro group decreased
    assert quota.judge(before, after, "gemini-weekly") == "verified"
    assert quota.judge(before, after, "gemini-5h") == "verified"


def test_judge_undetermined_when_multiple_groups_change_or_concurrent_use() -> None:
    # 1. In raw usage_before.json -> usage_after.json, both Flash/Pro AND Argon decreased
    before = quota.parse_snapshot(FIXTURES_DIR / "usage_before.json")
    after = quota.parse_snapshot(FIXTURES_DIR / "usage_after.json")
    assert quota.judge(before, after, "gemini-argon-wtu-weekly") == "undetermined"
    assert quota.judge(before, after, "gemini-weekly") == "undetermined"

    # 2. Argon unchanged, while BOTH Gemini (Flash/Pro) AND 3p groups decrease -> undetermined (never mismatch)
    b_dict = _full_buckets_dict()
    a_gemini_and_3p = _full_buckets_dict(
        gemini_weekly=0.98,
        gemini_5h=0.95,
        third_party_weekly=0.90,
    )
    assert quota.judge(b_dict, a_gemini_and_3p, "gemini-argon-wtu-weekly") == "undetermined"
    assert quota.judge(b_dict, a_gemini_and_3p, "gemini-weekly") == "undetermined"
    assert quota.judge(b_dict, a_gemini_and_3p, "3p-weekly") == "undetermined"

    # 3. Explicit concurrent activity flag forces undetermined even if only expected bucket changed
    a_argon_only = _full_buckets_dict(argon=0.70)
    assert (
        quota.judge(b_dict, a_argon_only, "gemini-argon-wtu-weekly", concurrent_activity=True)
        == "undetermined"
    )


def test_judge_undetermined_when_any_bucket_increases_or_window_resets() -> None:
    b_dict = _full_buckets_dict(argon=0.75, gemini_weekly=0.80, gemini_5h=0.80)

    # 1. Argon decreases, but a Gemini bucket increases (replenished/reset) -> undetermined
    a_other_up = _full_buckets_dict(argon=0.70, gemini_weekly=0.80, gemini_5h=1.0)
    assert quota.judge(b_dict, a_other_up, "gemini-argon-wtu-weekly") == "undetermined"

    # 2. Expected bucket increases -> undetermined
    a_expected_up = _full_buckets_dict(argon=1.0, gemini_weekly=0.80, gemini_5h=0.80)
    assert quota.judge(b_dict, a_expected_up, "gemini-argon-wtu-weekly") == "undetermined"

    # 3. Active bucket (remaining_fraction < 1.0) has its reset_time advance to next window
    # even though remaining_fraction is lower than before -> undetermined
    before_data = json.loads((FIXTURES_DIR / "usage_before.json").read_text(encoding="utf-8"))
    after_data = json.loads((FIXTURES_DIR / "usage_before.json").read_text(encoding="utf-8"))
    for grp in after_data["command"]["data"]["groups"]:
        for b in grp["buckets"]:
            if b["id"] == "gemini-argon-wtu-weekly":
                b["remaining_fraction"] = 0.70
                b["reset_time"] = "2026-11-13T12:03:52Z"
    assert quota.judge(before_data, after_data, "gemini-argon-wtu-weekly") == "undetermined"


def test_judge_undetermined_when_unknown_bucket_present_or_changes() -> None:
    b_dict = _full_buckets_dict(argon=0.75)
    a_dict = _full_buckets_dict(argon=0.70)

    # 1. Argon decreases while an unrecognized bucket also decreases (or is present) -> undetermined
    b_with_unknown = {**b_dict, "gemini-ultra-mystery": 0.90}
    a_with_unknown = {**a_dict, "gemini-ultra-mystery": 0.85}
    assert quota.judge(b_with_unknown, a_with_unknown, "gemini-argon-wtu-weekly") == "undetermined"

    # Even if the unknown bucket did not change, unrecognized bucket IDs must not return verified/mismatch
    a_with_unknown_same = {**a_dict, "gemini-ultra-mystery": 0.90}
    assert quota.judge(b_with_unknown, a_with_unknown_same, "gemini-argon-wtu-weekly") == "undetermined"

    # 2. Unrecognized expected_bucket -> undetermined
    assert quota.judge(b_dict, a_dict, "unknown-bucket-id") == "undetermined"

    # 3. Flat 5-bucket mapping (Argon 0.8 -> 0.7) with unknown bucket having non-numeric or
    # missing remaining_fraction values (None, bool, str, empty dict, dict without fraction, list, huge int)
    b_08 = _full_buckets_dict(argon=0.8)
    a_07 = _full_buckets_dict(argon=0.7)
    for bad_unknown_val in (
        None,
        True,
        False,
        "0.5",
        "",
        {},
        {"id": "new-bucket"},
        [],
        10**400,
    ):
        b_flat = {**b_08, "new-bucket": bad_unknown_val}
        a_flat = {**a_07, "new-bucket": bad_unknown_val}
        parsed_b = quota.parse_usage_json(b_flat)
        assert "new-bucket" in parsed_b
        assert quota.judge(b_flat, a_flat, "gemini-argon-wtu-weekly") == "undetermined"


def test_judge_unverified_when_required_buckets_missing_or_asymmetric() -> None:
    # 1. Both snapshots only contain Argon (decreasing from 0.8 to 0.7), missing Gemini buckets -> unverified
    only_argon_before = {"gemini-argon-wtu-weekly": 0.8}
    only_argon_after = {"gemini-argon-wtu-weekly": 0.7}
    assert quota.judge(only_argon_before, only_argon_after, "gemini-argon-wtu-weekly") == "unverified"

    # 2. Missing gemini-5h -> unverified
    missing_5h_before = {"gemini-argon-wtu-weekly": 0.8, "gemini-weekly": 0.9}
    missing_5h_after = {"gemini-argon-wtu-weekly": 0.7, "gemini-weekly": 0.9}
    assert quota.judge(missing_5h_before, missing_5h_after, "gemini-argon-wtu-weekly") == "unverified"

    # 3. Asymmetric buckets (3p-weekly present in before, missing in after) -> unverified
    b_full = _full_buckets_dict(argon=0.8)
    a_missing_3p = {
        "gemini-argon-wtu-weekly": 0.7,
        "gemini-weekly": 0.9995706677436829,
        "gemini-5h": 1.0,
        "3p-5h": 1.0,
    }
    assert quota.judge(b_full, a_missing_3p, "gemini-argon-wtu-weekly") == "unverified"


def test_judge_unverified_on_nan_infinity_or_out_of_range_fractions() -> None:
    b_valid = _full_buckets_dict(argon=0.8)

    for bad_val in (math.nan, math.inf, -math.inf, -0.01, 1.01, None, True, False, "0.7", {}, 10**400, -(10**400)):
        a_bad = dict(b_valid)
        a_bad["gemini-argon-wtu-weekly"] = bad_val  # type: ignore[assignment]
        assert quota.judge(b_valid, a_bad, "gemini-argon-wtu-weekly") == "unverified"

        # Even if the invalid value is on an unrelated bucket while Argon decreased validly
        a_bad_other = _full_buckets_dict(argon=0.7)
        a_bad_other["3p-weekly"] = bad_val  # type: ignore[assignment]
        assert quota.judge(b_valid, a_bad_other, "gemini-argon-wtu-weekly") == "unverified"

    # Direct BucketQuota with huge int (10**400) and credits_ok with huge int must not raise OverflowError
    snap_valid = quota.parse_snapshot(b_valid)
    buckets_overflow = dict(snap_valid.buckets)
    buckets_overflow["3p-weekly"] = quota.BucketQuota(id="3p-weekly", remaining_fraction=10**400)  # type: ignore[arg-type]
    snap_overflow = quota.QuotaSnapshot(buckets=buckets_overflow, remaining_credits=10**400)
    assert quota.judge(snap_valid, snap_overflow, "gemini-argon-wtu-weekly") == "unverified"
    assert quota.credits_ok(snap_overflow) is False
    assert quota.credits_ok({"remaining_credits": 10**400}) is False


def test_judge_undetermined_on_stale_delayed_or_reversed_timestamps() -> None:
    b_dict = _full_buckets_dict(argon=0.8)
    a_dict = _full_buckets_dict(argon=0.7)

    # 1. Explicit stale / delayed flags on judge()
    assert quota.judge(b_dict, a_dict, "gemini-argon-wtu-weekly", stale=True) == "undetermined"
    assert quota.judge(b_dict, a_dict, "gemini-argon-wtu-weekly", delayed=True) == "undetermined"

    # 2. Snapshot marked stale in payload
    snap_stale = quota.parse_snapshot(a_dict, stale=True)
    assert quota.judge(b_dict, snap_stale, "gemini-argon-wtu-weekly") == "undetermined"

    # 3. Reversed snapshot taken_at timestamps (after < before)
    b_ts = {**b_dict, "taken_at": "2026-10-31T10:05:00Z"}
    a_ts = {**a_dict, "taken_at": "2026-10-31T10:00:00Z"}
    assert quota.judge(b_ts, a_ts, "gemini-argon-wtu-weekly") == "undetermined"

    # 4. Unparseable / invalid explicit taken_at on both or either snapshot -> undetermined
    for bad_ts in ("bad", "", None, 12345, "2026-10-31"):
        b_bad_ts = {**b_dict, "taken_at": bad_ts}
        a_bad_ts = {**a_dict, "taken_at": bad_ts}
        assert quota.judge(b_bad_ts, a_bad_ts, "gemini-argon-wtu-weekly") == "undetermined"
        assert quota.judge(b_bad_ts, a_dict, "gemini-argon-wtu-weekly") == "undetermined"
        assert quota.judge(b_dict, a_bad_ts, "gemini-argon-wtu-weekly") == "undetermined"

    # 5. Reversed bucket reset_time (after reset_time earlier than before reset_time)
    before_data = json.loads((FIXTURES_DIR / "usage_before.json").read_text(encoding="utf-8"))
    after_data = json.loads((FIXTURES_DIR / "usage_before.json").read_text(encoding="utf-8"))
    for grp in after_data["command"]["data"]["groups"]:
        for b in grp["buckets"]:
            if b["id"] == "gemini-argon-wtu-weekly":
                b["remaining_fraction"] = 0.70
                b["reset_time"] = "2026-10-30T12:03:52Z"
    assert quota.judge(before_data, after_data, "gemini-argon-wtu-weekly") == "undetermined"


def test_judge_unverified_when_no_change_or_missing_snapshots() -> None:
    snap = quota.parse_snapshot(FIXTURES_DIR / "slash_usage.json")
    assert quota.judge(snap, snap, "gemini-argon-wtu-weekly") == "unverified"
    assert quota.judge(None, snap, "gemini-argon-wtu-weekly") == "unverified"
    assert quota.judge(snap, None, "gemini-argon-wtu-weekly") == "unverified"
