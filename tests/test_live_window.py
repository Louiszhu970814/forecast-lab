import copy
import json
from datetime import timedelta

import pytest

import cycle as C
import live_window as W
import submit_transport as T
from flab.ids import hash_obj
from flab.ledger import read_complete
from flab.timeutil import parse_iso, to_iso
from submit_contract import ConfirmedFailure
from test_submit import environment, set_mode, update_cfg, rows, NOW


def live(e):
    set_mode(e, mode="live")
    e["cfg"]["tournaments"]["bot_testing_area"]["submit_whitelisted"] = True
    update_cfg(e)
    return json.loads((e["config"] / "cycle_campaign.json").read_text())


def test_active_live_window_allows_normal_once_only_submission(environment):
    e = environment
    live(e)
    e["seed"]()
    e["make"]().run()
    e["make"]().run()
    assert [w[0] for w in e["transport"].writes] == ["forecast", "comment"]
    intent = next(r for r in rows(e) if r["status"] == "intent")
    assert intent["live_window_sha256"] == hash_obj(e["mode"]["live_window"])


@pytest.mark.parametrize("when", ["before", "at_end", "after_end"])
def test_no_write_outside_live_window(environment, when):
    e = environment
    live(e)
    window = e["mode"]["live_window"]
    start, end = parse_iso(window["start"]), parse_iso(window["end"])
    e["clock"]["now"] = {"before": start-timedelta(seconds=1), "at_end": end,
                         "after_end": end+timedelta(seconds=1)}[when]
    e["seed"]()
    e["make"]().run()
    assert not e["transport"].writes


@pytest.mark.parametrize("change", ["missing", "window", "campaign", "config", "role"])
def test_live_approval_cannot_be_silently_changed(environment, change):
    e = environment
    campaign = live(e)
    e["seed"]()
    if change == "missing":
        e["mode"].pop("live_window")
    elif change == "window":
        e["mode"]["live_window"]["end"] = to_iso(NOW + timedelta(days=5))
    elif change == "campaign":
        campaign["campaign_id"] = "other"
    elif change == "config":
        cfg = json.loads((e["config"] / "run_config_v2.json").read_text())
        cfg["deadlines"]["submit_lead_minutes"] += 1
        (e["config"] / "run_config_v2.json").write_text(json.dumps(cfg))
    else:
        campaign["venues"] = ["minibench"]
        e["mode"]["live_window"]["campaign_sha256"] = hash_obj(campaign)
    (e["config"] / "cycle_campaign.json").write_text(json.dumps(campaign))
    (e["config"] / "submit_mode.json").write_text(json.dumps(e["mode"]))
    e["make"]().run()
    assert not e["transport"].writes


def test_expiry_between_forecast_and_comment_blocks_comment(environment):
    e = environment
    live(e)
    e["seed"]()
    end = parse_iso(e["mode"]["live_window"]["end"])
    e["transport"].hook = lambda op: e["clock"].update(now=end)
    e["make"]().run()
    assert [w[0] for w in e["transport"].writes] == ["forecast"]
    assert rows(e)[-1]["blocked_operation"] == "comment"


def test_old_intent_cannot_borrow_a_different_live_authorization(environment):
    e = environment
    campaign = live(e)
    e["seed"]()
    e["transport"].effects["forecast"] = ["rejected"]
    e["make"]().run()
    intent = next(r for r in rows(e) if r["status"] == "intent")
    campaign["campaign_id"] = "new"
    e["mode"]["live_window"].update(campaign_id="new", campaign_sha256=hash_obj(campaign))
    (e["config"] / "cycle_campaign.json").write_text(json.dumps(campaign))
    (e["config"] / "submit_mode.json").write_text(json.dumps(e["mode"]))
    with pytest.raises(Exception, match="finite live authorization"):
        e["make"]()._gate(intent)


def test_transport_checks_expiry_after_shared_pacing_without_network(environment):
    e = environment
    campaign = live(e)
    calls = []
    class Delayed:
        def open(self, opener, req, timeout):
            e["clock"]["now"] = parse_iso(e["mode"]["live_window"]["end"])
            return opener(req, timeout)
    t = T.APITransport("synthetic", opener=lambda *args: calls.append(args), backoff=Delayed())
    t._bot_id = 42
    t.set_write_guard(lambda: W.require_active(e["mode"], campaign, e["config"], e["clock"]["now"]))
    with pytest.raises(ConfirmedFailure, match="before network"):
        t.write(T.FORECAST_PATH, [{"question": 7, "probability_yes": .3}], request_id="synthetic")
    assert not calls


@pytest.mark.parametrize("boundary", ["window", "question"])
def test_submitter_and_real_backoff_record_not_sent_when_pacing_crosses_cutoff(environment, boundary):
    from api_backoff import Backoff
    from test_m3_w9 import HTTP, live_environment
    import submit as S
    e = environment
    http = HTTP(e["post"])
    prepared = live_environment(e, http)
    end = parse_iso(e["mode"]["live_window"]["end"])
    cutoff = (end if boundary == "window" else
              parse_iso(e["post"]["question"]["scheduled_close_time"]) - timedelta(minutes=20))
    sleeps = []
    def advance(seconds):
        sleeps.append(seconds)
        if any(r["status"] == "request_started" for r in rows(e)):
            e["clock"]["now"] = cutoff
        else:
            e["clock"]["now"] += timedelta(seconds=seconds)
    backoff = Backoff(e["root"] / "pacing.jsonl", pace=True, pace_seconds=1, jitter_seconds=0,
                      sleep=advance, now=lambda: e["clock"]["now"],
                      wall=lambda: e["clock"]["now"].timestamp(), jitter=lambda a, b: 0)
    prepared.transport._backoff = backoff
    obj = S.Submitter(e["root"], config_dir=e["config"], transport=prepared.transport,
                      now=lambda: e["clock"]["now"], sleep=advance)
    obj.run()
    assert sleeps and not http.writes()
    assert any(r["status"] == "confirmed_failed" for r in rows(e))
    assert not any(r["status"] == "unknown" for r in rows(e))


def test_unknown_forecast_can_be_read_back_after_live_window_without_comment(environment):
    e = environment
    live(e)
    e["seed"]()
    e["transport"].effects["forecast"] = ["timeout_after"]
    e["make"]().run()
    assert rows(e)[-1]["status"] == "unknown"
    e["clock"]["now"] = parse_iso(e["mode"]["live_window"]["end"])
    e["make"]().run(reconcile_only=True)
    assert rows(e)[-1]["status"] == "forecast_ok"
    assert [w[0] for w in e["transport"].writes] == ["forecast"]


@pytest.mark.parametrize("when,state", [("before", "waiting"), ("tail", "draining")])
def test_scheduler_does_not_spawn_worker_outside_window(environment, monkeypatch, when, state):
    e = environment
    live(e)
    window = e["mode"]["live_window"]
    start, end = parse_iso(window["start"]), parse_iso(window["end"])
    now = {"before": start-timedelta(seconds=1), "tail": end-timedelta(seconds=5)}[when]
    monkeypatch.setattr(C, "PROJECT_ROOT", e["root"].parent)
    monkeypatch.setattr(C, "utc_now", lambda: now)
    monkeypatch.setattr(C, "load_submit_mode", lambda: e["mode"])
    monkeypatch.setattr(C, "supervise", lambda *a, **kw: pytest.fail("worker must not start"))
    assert C.main(["--campaign", str(e["config"] / "cycle_campaign.json")]) == 0
    assert read_complete(e["root"] / "live_schedule.jsonl")[-1]["status"] == state


def test_ended_window_renews_for_the_next_week(environment):
    # D149: an ended live window is followed by the next seven-day window, not a stop.
    import live_window as W
    from flab.config import load_submit_mode
    e = environment
    campaign = live(e)
    end = parse_iso(e["mode"]["live_window"]["end"])
    (e["config"] / "submit_mode.json").write_text(json.dumps(e["mode"]))
    assert W.renew(e["config"], end - timedelta(seconds=1)) is None
    event = W.renew(e["config"], end + timedelta(days=8))
    renewed = json.loads((e["config"] / "cycle_campaign.json").read_text())
    mode = load_submit_mode(e["config"])
    assert event["previous_campaign_id"] == campaign["campaign_id"] == renewed["renewed_from"]
    assert parse_iso(renewed["budget_window_start"]) <= end + timedelta(days=8) < parse_iso(renewed["budget_window_end"])
    assert renewed["venues"] == campaign["venues"] and renewed.get("weekly_attempt_caps") == campaign.get("weekly_attempt_caps")
    assert mode["live_window"]["campaign_sha256"] == hash_obj(renewed) and mode["approved_bot_id"] == e["mode"]["approved_bot_id"]
    W.require_active(mode, renewed, e["config"], end + timedelta(days=8))
    assert W.renew(e["config"], end + timedelta(days=8)) is None  # idempotent


def test_concurrent_renew_calls_emit_only_one_schedule_event(environment):
    from concurrent.futures import ThreadPoolExecutor
    import live_window as W
    e = environment
    live(e)
    end = parse_iso(e["mode"]["live_window"]["end"])
    (e["config"] / "submit_mode.json").write_text(json.dumps(e["mode"]))
    log = e["root"] / "live_schedule.jsonl"
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: W.renew(e["config"], end + timedelta(seconds=1), schedule_log=log), range(4)))
    assert sum(r is not None for r in results) == 1
    events = [r for r in read_complete(log) if r.get("type") == "live_window_renewed"]
    assert len(events) == 1


@pytest.mark.parametrize("tamper", ["venues", "caps", "config", "window_sha", "bot_id"])
def test_renew_rejects_unapproved_campaign_or_config_changes(environment, tamper):
    import live_window as W
    e = environment
    campaign = live(e)
    end = parse_iso(e["mode"]["live_window"]["end"])
    if tamper == "venues":
        campaign["venues"] = ["minibench"]
    elif tamper == "caps":
        campaign["weekly_attempt_caps"] = {"claude": 999, "gemini": 999, "gpt": 999}
    elif tamper == "config":
        cfg = json.loads((e["config"] / "run_config_v2.json").read_text())
        cfg["deadlines"]["submit_lead_minutes"] += 1
        (e["config"] / "run_config_v2.json").write_text(json.dumps(cfg))
    elif tamper == "bot_id":
        e["seed"]()
        e["make"]().run()
        e["mode"]["approved_bot_id"] = 99
    else:
        e["mode"]["live_window"]["campaign_sha256"] = "0" * 64
    (e["config"] / "cycle_campaign.json").write_text(json.dumps(campaign))
    (e["config"] / "submit_mode.json").write_text(json.dumps(e["mode"]))
    with pytest.raises(ValueError):
        W.renew(e["config"], end + timedelta(seconds=1))


@pytest.mark.parametrize("stage", ["after_campaign", "after_submit_mode", "after_schedule_append"])
@pytest.mark.parametrize("resume_offset_days", [0, 8])
def test_renew_recovers_and_records_schedule_event_after_midwrite_interruption(environment, monkeypatch, stage, resume_offset_days):
    import live_window as W
    from flab.config import load_submit_mode
    e = environment
    live(e)
    end = parse_iso(e["mode"]["live_window"]["end"])
    (e["config"] / "submit_mode.json").write_text(json.dumps(e["mode"]))
    log = e["root"] / "live_schedule.jsonl"
    real_write = W._write
    real_append = W.append_record
    if stage == "after_campaign":
        def fail_submit_mode(path, obj):
            if path.name == "submit_mode.json":
                raise RuntimeError("interrupted before submit_mode.json")
            real_write(path, obj)
        monkeypatch.setattr(W, "_write", fail_submit_mode)
    elif stage == "after_submit_mode":
        def fail_append(path, obj):
            raise RuntimeError("interrupted before schedule_log append")
        monkeypatch.setattr(W, "append_record", fail_append)
    else:
        def append_then_fail(path, obj):
            real_append(path, obj)
            raise RuntimeError("interrupted after schedule_log append before pending unlink")
        monkeypatch.setattr(W, "append_record", append_then_fail)
    with pytest.raises(RuntimeError):
        W.renew(e["config"], end + timedelta(seconds=1), schedule_log=log)
    if stage == "after_campaign" and resume_offset_days == 8:
        # Second interruption: >1 week later, after pending is updated to week-2 campaign C
        # but before cycle_campaign.json (still week-1 campaign B) is overwritten.
        calls = []
        def fail_second_campaign(path, obj):
            if path.name == "cycle_campaign.json":
                calls.append(path)
                raise RuntimeError("interrupted before writing week-2 campaign C")
            real_write(path, obj)
        monkeypatch.setattr(W, "_write", fail_second_campaign)
        with pytest.raises(RuntimeError):
            W.renew(e["config"], end + timedelta(days=8, seconds=1), schedule_log=log)
        assert len(calls) == 1
    monkeypatch.setattr(W, "_write", real_write)
    monkeypatch.setattr(W, "append_record", real_append)
    resume_now = end + timedelta(days=resume_offset_days, seconds=1)
    event = W.renew(e["config"], resume_now, schedule_log=log)
    assert not (e["config"] / ".live_window_pending.json").exists()
    mode = load_submit_mode(e["config"])
    campaign = W.campaign_from(e["config"])
    W.require_active(mode, campaign, e["config"], resume_now)
    events = [r for r in read_complete(log) if r.get("type") == "live_window_renewed"]
    if stage == "after_schedule_append" and resume_offset_days == 0:
        assert event is None and len(events) == 1
    else:
        assert event is not None and events[-1] == event
        assert len(events) == (2 if stage == "after_schedule_append" and resume_offset_days == 8 else 1)
    assert mode["live_window"]["campaign_id"] == campaign["campaign_id"] == events[-1]["campaign_id"]
    assert mode["live_window"]["start"] == campaign["budget_window_start"] == events[-1]["start"]
    assert mode["live_window"]["end"] == campaign["budget_window_end"] == events[-1]["end"]
    assert mode["live_window"]["campaign_sha256"] == hash_obj(campaign) == events[-1]["campaign_sha256"]


@pytest.mark.parametrize("target", ["disk_campaign", "pending_venues", "pending_caps"])
def test_renew_recovery_rejects_tampered_campaign_after_pending_written(environment, monkeypatch, target):
    import live_window as W
    e = environment
    live(e)
    end = parse_iso(e["mode"]["live_window"]["end"])
    (e["config"] / "submit_mode.json").write_text(json.dumps(e["mode"]))
    real_write = W._write
    def fail_before_campaign(path, obj):
        if path.name == "cycle_campaign.json":
            raise RuntimeError("interrupted after pending")
        real_write(path, obj)
    monkeypatch.setattr(W, "_write", fail_before_campaign)
    with pytest.raises(RuntimeError):
        W.renew(e["config"], end + timedelta(seconds=1))
    monkeypatch.setattr(W, "_write", real_write)
    if target == "disk_campaign":
        tampered = W.campaign_from(e["config"])
        tampered["venues"] = ["minibench"]
        (e["config"] / "cycle_campaign.json").write_text(json.dumps(tampered))
    else:
        pending_path = e["config"] / ".live_window_pending.json"
        pending = json.loads(pending_path.read_text())
        if target == "pending_venues":
            pending["campaign"]["venues"] = ["minibench"]
        else:
            pending["campaign"]["weekly_attempt_caps"] = {"claude": 999, "gemini": 999, "gpt": 999}
        pending_path.write_text(json.dumps(pending))
    with pytest.raises(ValueError, match="differs from approval"):
        W.renew(e["config"], end + timedelta(seconds=1))


def test_supervisor_reserves_cleanup_before_live_end(environment, monkeypatch):
    e = environment
    live(e)
    end = parse_iso(e["mode"]["live_window"]["end"])
    monkeypatch.setattr(C, "PROJECT_ROOT", e["root"].parent)
    monkeypatch.setattr(C, "utc_now", lambda: end-timedelta(seconds=100))
    monkeypatch.setattr(C, "load_submit_mode", lambda: e["mode"])
    calls = []
    def supervise(command, *, timeout_s):
        calls.append(timeout_s)
        assert parse_iso(command[command.index("--deadline")+1]) == end-timedelta(seconds=C.CLEANUP_RESERVE_S)
        return 0, False
    monkeypatch.setattr(C, "supervise", supervise)
    assert C.main(["--campaign", str(e["config"] / "cycle_campaign.json")]) == 0
    assert calls == [90]


def test_cycle_main_auto_renews_expired_live_window_and_spawns_worker(environment, monkeypatch):
    from flab.config import load_submit_mode as real_load_submit_mode
    e = environment
    old_campaign = live(e)
    end = parse_iso(e["mode"]["live_window"]["end"])
    (e["config"] / "submit_mode.json").write_text(json.dumps(e["mode"]))
    monkeypatch.setattr(C, "PROJECT_ROOT", e["root"].parent)
    monkeypatch.setattr(C, "utc_now", lambda: end + timedelta(seconds=10))
    monkeypatch.setattr(C, "load_submit_mode", lambda: real_load_submit_mode(e["config"]))
    spawned = []
    monkeypatch.setattr(C, "supervise", lambda command, *, timeout_s: (spawned.append((command, timeout_s)), (0, False))[1])
    assert C.main(["--campaign", str(e["config"] / "cycle_campaign.json")]) == 0
    assert len(spawned) == 1
    events = read_complete(e["root"] / "live_schedule.jsonl")
    assert [r["type"] for r in events] == ["live_window_renewed", "live_schedule"]
    assert events[0]["previous_campaign_id"] == old_campaign["campaign_id"]
    assert events[1]["status"] == "active" and events[1]["campaign_id"] == events[0]["campaign_id"]

