"""D107 A3: synthetic ledgers only; never invoke the CLI on its default root."""
import json
from contextlib import contextmanager
from datetime import timedelta

import pytest

import budget as B
import quota
from flab.jsonl import append_record, read_records
from flab.timeutil import to_iso
from test_budget import attempt, ledger, reserve, snapshot
from test_forecast import NOW, _qsnap


def stop(ledger, player="gpt", *, at=None, until=None, reason="weekly_tier2_token_breaker"):
    row = {"type": "budget_upgrade_stop", "player": player, "reason": reason,
           "at": to_iso(at or NOW - timedelta(hours=1)),
           "until": to_iso(until or NOW + timedelta(days=3)), "version": "budget_v4"}
    append_record(ledger.log, row)
    return row


def lift(ledger, players=None):
    return B.lift_tier2_stops(ledger.root, players or ["gpt"], B.LIFT_AUTHORIZATION, now=ledger.now)


def recorded_lift(ledger, stops, *, player="gpt", at=NOW):
    row = {"type": "budget_upgrade_stop_lifted", "player": player,
           "lifted_reasons": list(dict.fromkeys(s["reason"] for s in stops)),
           "lifted_stop_ats": [s["at"] for s in stops],
           "authorization": B.LIFT_AUTHORIZATION, "at": to_iso(at), "version": B.VERSION}
    append_record(ledger.log, row)
    return row


@pytest.mark.parametrize("player,reason", [
    ("gpt", "weekly_tier2_token_breaker"),
    ("gemini", "weekly_tier2_token_breaker"),
    ("gemini", "weekly_tier2_argon_breaker"),
])
def test_lift_allows_previously_blocked_question_and_preserves_history(ledger, player, reason):
    attempt(ledger, player=player)
    saved = stop(ledger, player, reason=reason)
    if player == "gemini":
        snapshot(ledger)
    assert reserve(ledger, player=player, tier=2)["reason"] == reason
    history = ledger.log.read_bytes()
    records = lift(ledger, [player])
    assert ledger.log.read_bytes().startswith(history)
    assert records == [{
        "type": "budget_upgrade_stop_lifted", "player": player,
        "lifted_reasons": [reason], "lifted_stop_ats": [saved["at"]],
        "authorization": "PLAN D121", "at": to_iso(NOW), "version": B.VERSION,
    }]
    decision = reserve(ledger, player=player, tier=2)
    assert decision["allowed"], decision["reason"]
    assert decision["version"] == B.VERSION


@pytest.mark.parametrize("old_total", [100, None])
def test_old_breach_is_forgiven_but_new_breach_persists(ledger, old_total):
    attempt(ledger, player="gpt")
    attempt(ledger, player="gpt", tier=2, aid="overrun", qid=20, total=old_total)
    # Keep p90 below the per-question limit, without deleting the old overrun.
    for i in range(10):
        attempt(ledger, player="gpt", tier=2, aid=f"small{i}", qid=30 + i)
    stop(ledger)
    assert reserve(ledger, player="gpt", tier=2, qid=8)["reason"] == "weekly_tier2_token_breaker"
    ledger.now = lambda: NOW + timedelta(seconds=1)
    lift(ledger)
    decision = reserve(ledger, player="gpt", tier=2, qid=8)
    assert decision["allowed"], decision["reason"]
    assert decision["token_estimate"] == 30
    ledger.outcome("new", False)
    assert reserve(ledger, player="gpt", tier=2, qid=20, aid="own")["reason"] == "question_tier2_token_capacity"
    attempt(ledger, player="gpt", tier=2, aid="new-overrun", qid=50, total=100,
            started=NOW + timedelta(seconds=2))
    ledger.now = lambda: NOW + timedelta(minutes=2)
    # An unversioned historical row must not synthesize a new-policy breaker.
    assert reserve(ledger, player="gpt", tier=2, qid=9, aid="next")["allowed"]
    ledger.outcome("next", False)
    stops = [r for r in read_records(ledger.log) if r["type"] == "budget_upgrade_stop"]
    assert len(stops) == 1
    # A higher baseline cannot undo the new persisted stop.
    for i in range(5):
        attempt(ledger, player="gpt", aid=f"baseline{i}", total=1000)
    assert reserve(ledger, player="gpt", tier=2, qid=10, aid="still")["allowed"]


@pytest.mark.parametrize("offset,expected", [(-1, None), (0, None), (1, None)])
def test_breaker_uses_start_strictly_after_lift_even_if_completion_is_later(ledger, offset, expected):
    attempt(ledger, player="gpt")
    stop(ledger)
    lift(ledger)
    attempt(ledger, player="gpt", aid="unknown", tier=2, qid=20, total=None,
            started=NOW + timedelta(seconds=offset))
    ledger.now = lambda: NOW + timedelta(minutes=2)
    assert reserve(ledger, player="gpt", tier=2)["reason"] == expected


def test_latest_lift_is_chosen_by_time_and_post_lift_usage_is_summed_per_question(ledger):
    attempt(ledger, player="gpt")
    first = stop(ledger, at=NOW - timedelta(hours=3))
    second = stop(ledger, at=NOW - timedelta(hours=2))
    # Deliberately append the older lift last.
    recorded_lift(ledger, [second])
    recorded_lift(ledger, [first], at=NOW - timedelta(hours=1))
    attempt(ledger, player="gpt", tier=2, aid="before", qid=20, total=None)
    assert reserve(ledger, player="gpt", tier=2)["allowed"]
    ledger.outcome("new", False)
    for i in range(2):
        attempt(ledger, player="gpt", tier=2, aid=f"after{i}", qid=30, total=50,
                started=NOW + timedelta(seconds=i + 1))
    ledger.now = lambda: NOW + timedelta(minutes=2)
    assert reserve(ledger, player="gpt", tier=2, aid="next")["allowed"]


def test_lift_does_not_reset_estimate_or_per_question_accumulated_tokens(ledger):
    attempt(ledger, player="gpt")
    attempt(ledger, player="gpt", tier=2, aid="deep", total=60)
    stop(ledger)
    lift(ledger)
    own = reserve(ledger, player="gpt", tier=2)
    assert own["reason"] == "question_tier2_token_capacity"
    assert own["token_estimate"] == 60 and own["token_limit"] == 90
    assert reserve(ledger, player="gpt", tier=2, qid=8, aid="other")["allowed"]


@pytest.mark.parametrize("lifted_player", ["gpt", "gemini"])
def test_lift_is_player_specific_for_stops_and_breach_cutoff(ledger, lifted_player):
    other = "gemini" if lifted_player == "gpt" else "gpt"
    attempt(ledger, player=other)
    attempt(ledger, player=other, tier=2, aid="overrun", qid=20, total=None)
    saved = stop(ledger, other)
    # Same timestamp across players must not match.
    stop(ledger, lifted_player)
    lift(ledger, [lifted_player])
    snapshot(ledger)
    assert reserve(ledger, player=other, tier=2)["reason"] == saved["reason"]
    # Expiry must not reinterpret an old output under the new policy.
    ledger.now = lambda: NOW + timedelta(days=3)
    if other == "gemini":
        snapshot(ledger, aid="next", taken_at=to_iso(ledger.now()), reset=to_iso(NOW + timedelta(days=5)))
    assert reserve(ledger, player=other, tier=2, aid="next")["allowed"]
    assert len([r for r in read_records(ledger.log)
                if r["type"] == "budget_upgrade_stop" and r["player"] == other]) == 1


def test_only_listed_stop_is_lifted_and_new_stop_remains_active(ledger):
    attempt(ledger, player="gpt")
    unlisted = stop(ledger, at=NOW - timedelta(hours=2))
    listed = stop(ledger)
    recorded_lift(ledger, [listed])
    assert reserve(ledger, player="gpt", tier=2)["reason"] == unlisted["reason"]
    lift(ledger)
    assert reserve(ledger, player="gpt", tier=2)["allowed"]
    ledger.outcome("new", False)
    stop(ledger, at=NOW + timedelta(seconds=1))
    ledger.now = lambda: NOW + timedelta(seconds=2)
    assert reserve(ledger, player="gpt", tier=2, aid="later")["reason"] == "weekly_tier2_token_breaker"


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_lift_must_be_later_than_stop_and_not_in_future(ledger, offset):
    attempt(ledger, player="gpt")
    saved = stop(ledger, at=NOW)
    recorded_lift(ledger, [saved], at=NOW + timedelta(seconds=offset))
    assert reserve(ledger, player="gpt", tier=2)["reason"] == "weekly_tier2_token_breaker"


def test_lift_keeps_weekly_cap_protected_capacity_and_tier1_behavior(ledger):
    attempt(ledger, player="gpt")
    stop(ledger)
    assert reserve(ledger, player="gpt")["allowed"]
    ledger.outcome("new", False)
    lift(ledger)
    assert reserve(ledger, player="gpt", aid="first")["allowed"]
    ledger.outcome("first", False)
    ledger.campaign["weekly_attempt_caps"]["gpt"] = 4
    assert reserve(ledger, player="gpt", tier=2, pending=1, incoming=1, repair=1,
                   aid="second")["reason"] == "weekly_attempt_capacity_reserved_or_exhausted"
    ledger.campaign["weekly_attempt_caps"]["gpt"] = 1
    assert reserve(ledger, player="gpt", aid="third")["reason"] == "weekly_attempt_capacity_reserved_or_exhausted"


@pytest.mark.parametrize("problem,reason", [
    ("credits", "untrusted_argon_snapshot_or_reset"),
    ("weekly", "argon_personal_or_tier1_reserve"),
    ("five_hour", "quota_5h_wait"),
    ("unknown", quota.UNKNOWN_LAYOUT_REASON),
])
def test_gemini_quota_guards_remain_after_lift(ledger, problem, reason):
    attempt(ledger, player="gemini")
    stop(ledger, "gemini", reason="weekly_tier2_argon_breaker")
    lift(ledger, ["gemini"])
    row = _qsnap(.9).to_dict()
    row.update(type="quota_snapshot", attempt_id="new", when="before", taken_at=to_iso(NOW))
    row["buckets"].pop(quota.ARGON_BUCKET_ID)
    if problem == "credits":
        row["remaining_credits"] = 1
    elif problem == "weekly":
        row["buckets"][quota.GEMINI_WEEKLY_BUCKET_ID]["remaining_fraction"] = .01
    elif problem == "five_hour":
        row["buckets"][quota.GEMINI_5H_BUCKET_ID]["remaining_fraction"] = .01
    elif problem == "unknown":
        row["buckets"] = {}
    append_record(ledger.root / "quota.jsonl", row)
    assert reserve(ledger, player="gemini", tier=2)["reason"] == reason


def cli(ledger, *args):
    return B.main(["lift-tier2-stops", "--root", str(ledger.root), *args])


def test_cli_multiple_players_deduplicates_and_repeat_refuses_without_writing(ledger, monkeypatch, capsys):
    monkeypatch.setattr(B, "utc_now", ledger.now)
    for player in ("gpt", "gemini"):
        stop(ledger, player)
    assert cli(ledger, "--player", "gpt", "--player", "gemini", "--player", "gpt",
               "--authorization", B.LIFT_AUTHORIZATION) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["player"] for r in rows] == ["gpt", "gemini"]
    assert all(set(r) == {"player", "lifted_reasons", "lifted_stop_ats", "at"} for r in rows)
    history = ledger.log.read_bytes()
    with pytest.raises(SystemExit) as exc:
        cli(ledger, "--player", "gpt", "--player", "gemini", "--authorization", B.LIFT_AUTHORIZATION)
    assert exc.value.code == 1
    assert ledger.log.read_bytes() == history
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("authorization", ["", " ", "unapproved"])
def test_cli_invalid_authorization_does_not_write(ledger, authorization, capsys):
    stop(ledger)
    history = ledger.log.read_bytes()
    with pytest.raises(SystemExit) as exc:
        cli(ledger, "--player", "gpt", "--authorization", authorization)
    assert exc.value.code == 1
    assert ledger.log.read_bytes() == history
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("state", ["absent", "expired", "other_reason", "same_time", "future"])
def test_cli_refuses_when_no_eligible_stop(ledger, monkeypatch, state):
    monkeypatch.setattr(B, "utc_now", ledger.now)
    if state == "expired":
        stop(ledger, until=NOW)
    elif state == "other_reason":
        stop(ledger, reason="unrelated_stop")
    elif state in ("same_time", "future"):
        stop(ledger, at=NOW + timedelta(seconds=state == "future"))
    history = ledger.log.read_bytes() if ledger.log.exists() else None
    with pytest.raises(SystemExit) as exc:
        cli(ledger, "--player", "gpt", "--authorization", B.LIFT_AUTHORIZATION)
    assert exc.value.code == 1
    assert (ledger.log.read_bytes() if ledger.log.exists() else None) == history


def test_lift_prevalidates_all_players_and_excludes_expired_stops(ledger):
    expired = stop(ledger, until=NOW)
    current = stop(ledger, at=NOW - timedelta(minutes=1))
    history = ledger.log.read_bytes()
    with pytest.raises(ValueError, match="gemini"):
        lift(ledger, ["gpt", "gemini"])
    assert ledger.log.read_bytes() == history
    result = lift(ledger)[0]
    assert result["lifted_stop_ats"] == [current["at"]]
    assert expired["at"] not in result["lifted_stop_ats"]


def test_lift_reads_and_writes_under_transaction(ledger, monkeypatch):
    stop(ledger)
    active = False
    real_transaction, real_read, real_append = B.transaction, B.read_complete, B.append_record

    @contextmanager
    def checked_transaction(root):
        nonlocal active
        with real_transaction(root):
            active = True
            yield
            active = False

    def checked_read(path):
        assert active and path == ledger.log
        return real_read(path)

    def checked_append(path, record):
        assert active and path == ledger.log
        real_append(path, record)

    monkeypatch.setattr(B, "transaction", checked_transaction)
    monkeypatch.setattr(B, "read_complete", checked_read)
    monkeypatch.setattr(B, "append_record", checked_append)
    lift(ledger)


def test_cli_does_not_print_malformed_ledger_contents(ledger, capsys):
    ledger.log.write_text('{"private": "synthetic-secret"}')
    history = ledger.log.read_bytes()
    with pytest.raises(SystemExit) as exc:
        cli(ledger, "--player", "gpt", "--authorization", B.LIFT_AUTHORIZATION)
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert output.out == "" and "synthetic-secret" not in output.err
    assert ledger.log.read_bytes() == history
