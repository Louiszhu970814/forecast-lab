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


@pytest.mark.parametrize("when,state", [("before", "waiting"), ("at_end", "expired"), ("tail", "draining")])
def test_scheduler_does_not_spawn_worker_outside_window(environment, monkeypatch, when, state):
    e = environment
    live(e)
    window = e["mode"]["live_window"]
    start, end = parse_iso(window["start"]), parse_iso(window["end"])
    now = {"before": start-timedelta(seconds=1), "at_end": end, "tail": end-timedelta(seconds=5)}[when]
    monkeypatch.setattr(C, "PROJECT_ROOT", e["root"].parent)
    monkeypatch.setattr(C, "utc_now", lambda: now)
    monkeypatch.setattr(C, "load_submit_mode", lambda: e["mode"])
    monkeypatch.setattr(C, "supervise", lambda *a, **kw: pytest.fail("worker must not start"))
    assert C.main(["--campaign", str(e["config"] / "cycle_campaign.json")]) == 0
    assert read_complete(e["root"] / "live_schedule.jsonl")[-1]["status"] == state


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
