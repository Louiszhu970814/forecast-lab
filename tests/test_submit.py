from __future__ import annotations

import copy
import json
from datetime import timedelta
from decimal import Decimal

import pytest

import aggregate as A
import comment as C
import forecast as F
import submit as S
from flab.config import load_run_config
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import to_iso
from poll_questions import TournamentIndex, snapshot_from_post, title_key
from test_aggregate import NOW, answer
from test_poll_questions import _post


class Crash(BaseException):
    pass


class FakeTransport:
    def __init__(self, post):
        self.post = post
        self.bot = 42
        self.index = TournamentIndex(project_ids={33121, 33122})
        self.writes, self.reads, self.receipts = [], [], {}
        self.effects = {"forecast": [], "comment": []}
        self.read_effects = []
        self.hook = lambda operation: None

    def current_tournaments(self, configured):
        cfg = copy.deepcopy(configured)
        cfg["tournaments"].update({name: {"id": pid, "submit_whitelisted": False}
            for name, pid in (("minibench", 33122), ("metaculus_cup", 33108), ("main_site_questions", 144))})
        return {**cfg, "excluded_project_ids": [33121, 33122]}

    def account(self):
        return {"id": self.bot}

    def get_post(self, post_id):
        return copy.deepcopy(self.post)

    def tournament_index(self, configured):
        return self.index

    def write(self, path, payload, *, request_id):
        operation = "forecast" if path == S.FORECAST_PATH else "comment"
        assert path in (S.FORECAST_PATH, S.COMMENT_PATH)
        self.writes.append((operation, copy.deepcopy(payload), request_id))
        self.hook(operation)
        effect = self.effects[operation].pop(0) if self.effects[operation] else "ok"
        if effect == "rejected":
            raise S.ConfirmedFailure("synthetic definitive rejection")
        if effect == "timeout_before":
            raise TimeoutError("secret must not be logged")
        receipt = {"id": len(self.writes), "bot_id": self.bot, "payload": payload,
                   **({"question_id": payload[0]["question"]} if operation == "forecast" else {"post_id": payload["on_post"]})}
        self.receipts[request_id] = receipt
        if operation == "forecast":
            self.post["question"]["my_forecasts"].append({"id": receipt["id"]})
        if effect == "timeout_after":
            raise TimeoutError("secret must not be logged")
        if effect == "crash_after":
            raise Crash()
        if effect == "bad_receipt":
            return {"id": receipt["id"]}
        return receipt

    def readback(self, operation, intent, *, request_id):
        self.reads.append((operation, request_id))
        effect = self.read_effects.pop(0) if self.read_effects else "found"
        if effect == "error":
            raise OSError("secret must not be logged")
        if effect == "empty":
            return S.Readback()
        if isinstance(effect, S.Readback):
            return effect
        return S.Readback("found", self.receipts.get(request_id), request_id)


@pytest.fixture
def environment(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    cfg = {"tournaments": {
        "main": {"id": 33121, "slug": "main", "submit_whitelisted": True},
        "minibench": {"id": 33122, "slug": "mini", "submit_whitelisted": True},
        "bot_testing_area": {"id": 32977, "slug": "bot-testing-area", "submit_whitelisted": False}},
        "trial_ground_types": ["bot_testing_area", "metaculus_cup", "main_site_questions"]}
    (config / "tournaments.json").write_text(json.dumps(cfg))
    (config / "run_config_v2.json").write_text(json.dumps(load_run_config()))
    mode = {"mode": "trial", "approved_bot_id": 42}
    (config / "submit_mode.json").write_text(json.dumps(mode))
    post = _post(17, 7, "binary", "Synthetic question", my_forecasts=[],
                 open_time=to_iso(NOW - timedelta(hours=1)), scheduled_close_time=to_iso(NOW + timedelta(hours=3)))
    snap = {**snapshot_from_post(post, "bot-testing-area"), "tournament_id": 32977, "snapshot_id": "s1"}
    root = tmp_path / "data"
    clock, delays = {"now": NOW}, []
    def sleep(seconds):
        delays.append(seconds)
        clock["now"] += timedelta(seconds=seconds)
    transport = FakeTransport(post)
    def make(**kwargs):
        return S.Submitter(root, config_dir=config, transport=transport, now=lambda: clock["now"], sleep=sleep, **kwargs)
    def seed(snapshot=None, p=.3, eligible=True, aid="a1", player="claude"):
        snapshot = copy.deepcopy(snapshot or snap)
        row = answer(player, snapshot=snapshot, p=p, aid=aid, reasoning_summary="Synthetic evidence.")
        aggregate = A.build_aggregate(snapshot, [row], stage="final", now=NOW)
        aggregate.update(submission_eligible=eligible, scheduler_campaign_id="synthetic", completion_status="complete_local")
        comment = C.build(snapshot, aggregate, [row, aggregate], [], now=NOW)
        for path, record in (("questions", snapshot), ("forecasts", row), ("aggregates", aggregate), ("comments", comment)):
            append_record(root / (path + ".jsonl"), record)
        return aggregate, comment
    return {"root": root, "config": config, "cfg": cfg, "mode": mode, "snap": snap, "post": post,
            "transport": transport, "make": make, "seed": seed, "clock": clock, "delays": delays}


def rows(e):
    return read_complete(e["root"] / "submissions.jsonl")


def set_mode(e, **changes):
    e["mode"].update(changes)
    (e["config"] / "submit_mode.json").write_text(json.dumps(e["mode"]))


def update_cfg(e):
    (e["config"] / "tournaments.json").write_text(json.dumps(e["cfg"]))


def test_success_preserves_targets_payload_receipts_and_is_restart_idempotent(environment):
    e = environment
    agg, comment = e["seed"]()
    e["make"]().run()
    assert [r["status"] for r in rows(e)] == ["intent", "request_started", "forecast_ok", "request_started", "comment_ok"]
    intent = rows(e)[0]
    assert (intent["post_id"], intent["question_id"], intent["aggregate_id"]) == (17, 7, agg["record_id"])
    assert intent["comment_text"] == comment["text"] and intent["comment_hash"] == comment["text_sha256"]
    writes = e["transport"].writes
    assert writes[0][1] == [{"question": 7, "probability_yes": agg["probability"]}]
    assert writes[1][1] == {"on_post": 17, "text": comment["text"], "is_private": True, "included_forecast": True}
    assert rows(e)[2]["remote_id"] == 1 and rows(e)[4]["remote_id"] == 2
    assert e["make"]().run() == {"events": 0, "statuses": {}}
    assert e["make"]().run(reconcile_only=True) == {"events": 0, "statuses": {}}
    assert len(writes) == 2


@pytest.mark.parametrize("mutation,reason", [
    (lambda e: e["post"].update(id=999), "post_id"),
    (lambda e: e["post"]["question"].update(id=999), "question_id"),
    (lambda e: setattr(e["transport"], "bot", 99), "account"),
    (lambda e: set_mode(e, approved_bot_id=None), "bot ID"),
    (lambda e: e["post"]["question"].update(options=["revised"]), "options"),
    (lambda e: e["post"]["question"].update(type="numeric"), "question_type"),
    (lambda e: e["post"]["question"].update(status="closed"), "not open"),
    (lambda e: e["post"]["question"].update(scheduled_close_time=to_iso(NOW)), "close_time"),
    (lambda e: e["clock"].update(now=NOW + timedelta(hours=3)), "window"),
    (lambda e: e["post"].update(projects={}), "belong"),
    (lambda e: e["post"]["question"].update(my_forecasts=[{"id": 9}]), "already has"),
    (lambda e: e["post"]["question"].pop("my_forecasts"), "unavailable"),
    (lambda e: e["transport"].index.question_ids.add(7), "same question"),
    (lambda e: e["transport"].index.post_ids.add(17), "same question"),
    (lambda e: e["transport"].index.project_ids.add(32977), "belongs to"),
    (lambda e: e["transport"].index.title_keys.add(title_key("Synthetic question")), "title matches"),
    (lambda e: setattr(e["transport"], "index", None), "index unavailable"),
])
def test_precheck_rejects_unsafe_current_state(environment, mutation, reason):
    e = environment
    e["seed"]()
    mutation(e)
    e["make"]().run()
    assert rows(e)[-1]["status"] == "rejected_precheck"
    assert reason in rows(e)[-1]["reason"]
    assert not e["transport"].writes


@pytest.mark.parametrize("mode", ["local", "trial"])
def test_ineligible_scheduler_results_are_not_promoted(environment, mode):
    e = environment
    e["seed"](eligible=False)
    set_mode(e, mode=mode)
    e["make"]().run()
    assert rows(e)[-1]["status"] == "rejected_precheck"
    assert "not submission eligible" in rows(e)[-1]["reason"]
    assert not e["transport"].writes


def test_local_dry_run_never_touches_transport_and_is_idempotent(environment):
    e = environment
    e["seed"]()
    set_mode(e, mode="local", approved_bot_id=None)
    obj = e["make"]()
    obj.transport = S.OfflineTransport()
    obj.run()
    assert rows(e)[-1]["status"] == "dry_run"
    assert rows(e)[-1]["forecast_payload"][0]["question"] == 7
    assert obj.run()["events"] == 0
    assert not e["transport"].writes


@pytest.mark.parametrize("stop", ["file", "mode", "allowlist", "account"])
def test_stop_between_forecast_and_comment(environment, stop):
    e = environment
    e["seed"]()
    set_mode(e, mode="live")
    e["cfg"]["tournaments"]["bot_testing_area"]["submit_whitelisted"] = True
    update_cfg(e)
    def hook(operation):
        if stop == "file":
            (e["config"] / "STOP_SUBMIT").touch()
        elif stop == "mode":
            set_mode(e, mode="local")
        elif stop == "account":
            set_mode(e, approved_bot_id=999)
        else:
            e["cfg"]["tournaments"]["bot_testing_area"]["submit_whitelisted"] = False
            update_cfg(e)
    e["transport"].hook = hook
    e["make"]().run()
    assert [w[0] for w in e["transport"].writes] == ["forecast"]
    assert rows(e)[-1]["status"] == "rejected_precheck"
    e["make"]().run()
    assert len(e["transport"].writes) == 1


@pytest.mark.parametrize("operation", ["forecast", "comment"])
@pytest.mark.parametrize("effect", ["timeout_after", "crash_after", "bad_receipt"])
def test_accepted_lost_receipt_recovers_after_restart_without_duplicate(environment, operation, effect):
    e = environment
    e["seed"]()
    e["transport"].effects[operation] = [effect]
    if effect == "crash_after":
        with pytest.raises(Crash):
            e["make"]().run()
        assert rows(e)[-1]["status"] == "request_started"
    else:
        e["make"]().run()
        assert rows(e)[-1]["status"] == "unknown"
    initial = len(e["transport"].writes)
    e["make"]().run(reconcile_only=True)
    assert len(e["transport"].writes) == initial
    assert rows(e)[-1]["status"] == operation + "_ok"
    e["make"]().run()
    assert rows(e)[-1]["status"] == "comment_ok"
    assert [w[0] for w in e["transport"].writes] == ["forecast", "comment"]
    assert "secret" not in str(rows(e))


@pytest.mark.parametrize("operation", ["forecast", "comment"])
def test_readback_failure_and_delayed_visibility_remain_unknown(environment, operation):
    e = environment
    e["seed"]()
    e["transport"].effects[operation] = ["timeout_after"]
    e["make"]().run()
    count = len(e["transport"].writes)
    e["transport"].read_effects = ["error", "empty", "empty"]
    for _ in range(3):
        e["make"]().run()
        assert rows(e)[-1]["status"] == "unknown"
        assert len(e["transport"].writes) == count
    e["make"]().run()
    assert rows(e)[-1]["status"] == "comment_ok" and len(e["transport"].writes) == 2


@pytest.mark.parametrize("operation", ["forecast", "comment"])
def test_confirmed_nonacceptance_is_only_unknown_retry_path(environment, operation):
    e = environment
    e["seed"]()
    e["transport"].effects[operation] = ["timeout_before"]
    e["make"]().run()
    request = e["transport"].writes[-1]
    e["transport"].read_effects = [S.Readback("not_accepted", request_id=request[2], evidence="authoritative fake request log")]
    e["make"]().run(reconcile_only=True)
    assert rows(e)[-1]["status"] == "confirmed_failed"
    assert len(e["transport"].writes) == (1 if operation == "forecast" else 2)
    e["make"]().run()
    matching = [w for w in e["transport"].writes if w[0] == operation]
    assert len(matching) == 2 and matching[0][1] == matching[1][1]
    assert rows(e)[-1]["status"] == "comment_ok"


@pytest.mark.parametrize("operation", ["forecast", "comment"])
@pytest.mark.parametrize("bad", ["no_evidence", "wrong_request", "wrong_account", "wrong_payload", "wrong_target"])
def test_unreliable_readback_does_not_free_unknown_request(environment, operation, bad):
    e = environment
    e["seed"]()
    e["transport"].effects[operation] = ["timeout_after"]
    e["make"]().run()
    request_id = e["transport"].writes[-1][2]
    receipt = copy.deepcopy(e["transport"].receipts[request_id])
    if bad == "no_evidence":
        result = S.Readback("not_accepted", request_id=request_id)
    elif bad == "wrong_request":
        result = S.Readback("not_accepted", request_id="unrelated", evidence="not this request")
    else:
        if bad == "wrong_account":
            receipt["bot_id"] = 99
        elif bad == "wrong_payload":
            receipt["payload"] = {}
        else:
            receipt["question_id" if operation == "forecast" else "post_id"] = 999
        result = S.Readback("found", receipt, request_id)
    e["transport"].read_effects = [result]
    before = len(e["transport"].writes)
    e["make"]().run()
    assert rows(e)[-1]["status"] == "unknown" and len(e["transport"].writes) == before


@pytest.mark.parametrize("failures,final", [(2, "comment_ok"), (3, "comment_failed")])
def test_comment_retries_only_identical_comment_with_backoff(environment, failures, final):
    e = environment
    e["seed"]()
    e["transport"].effects["comment"] = ["rejected"] * failures
    e["make"]().run()
    assert rows(e)[-1]["status"] == final
    writes = e["transport"].writes
    assert [w[0] for w in writes] == ["forecast", "comment", "comment", "comment"]
    assert writes[1][1] == writes[2][1] == writes[3][1]
    assert e["delays"] == [1, 2]
    assert rows(e)[-1].get("notify_louis", False) == (final == "comment_failed")
    e["make"]().run()
    assert len(writes) == 4


@pytest.mark.parametrize("operation", ["forecast", "comment"])
@pytest.mark.parametrize("stop", ["file", "mode"])
def test_disabled_submission_still_reconciles_already_sent_requests(environment, operation, stop):
    e = environment
    e["seed"]()
    e["transport"].effects[operation] = ["timeout_after"]
    e["make"]().run()
    count = len(e["transport"].writes)
    if stop == "file":
        (e["config"] / "STOP_SUBMIT").touch()
    else:
        set_mode(e, mode="local")
    e["make"]().run(reconcile_only=True)
    assert rows(e)[-1]["status"] == operation + "_ok"
    e["make"]().run()
    assert len(e["transport"].writes) == count


def test_stop_before_first_request_and_during_last_read(environment):
    e = environment
    e["seed"]()
    (e["config"] / "STOP_SUBMIT").touch()
    e["make"]().run()
    assert not e["transport"].writes
    (e["config"] / "STOP_SUBMIT").unlink()
    original = e["transport"].tournament_index
    def index(cfg):
        (e["config"] / "STOP_SUBMIT").touch()
        return original(cfg)
    e["transport"].tournament_index = index
    e["make"]().run()
    assert not e["transport"].writes


def test_allowlist_and_trial_ground_membership_are_required(environment):
    e = environment
    e["seed"]()
    set_mode(e, mode="live")
    e["make"]().run()
    assert "whitelisted" in rows(e)[-1]["reason"]
    set_mode(e, mode="trial")
    e["cfg"]["trial_ground_types"] = ["main_site_questions"]
    update_cfg(e)
    e["make"]().run()
    assert "not allowed in trial" in rows(e)[-1]["reason"]
    assert not e["transport"].writes


def test_cross_tournament_dedupe_uses_bot_and_question_not_aggregate(environment):
    e = environment
    e["seed"]()
    e["make"]().run()
    # Fresh local and platform mapping to another tournament cannot free this question.
    other = {**e["snap"], "snapshot_id": "s2", "tournament_id": 33121}
    e["seed"](other, aid="a2", player="gemini")
    set_mode(e, mode="live")
    e["post"]["projects"] = {"tournament": [{"id": 33121}]}
    e["post"]["question"]["my_forecasts"] = []
    e["make"]().run()
    assert "duplicate bot/question" in rows(e)[-1]["reason"]
    assert len(e["transport"].writes) == 2


@pytest.mark.parametrize("allowed", [False, True])
def test_trial_repeat_requires_explicit_venue_policy_and_new_final(environment, allowed):
    e = environment
    e["seed"]()
    kwargs = {"allow_resubmit_by_venue": {32977: allowed}}
    e["make"](**kwargs).run()
    e["seed"]({**e["snap"], "snapshot_id": "s2"}, p=.4, aid="a2", player="gemini")
    e["make"](**kwargs).run()
    assert len(e["transport"].writes) == (4 if allowed else 2)
    e["make"](**kwargs).run()
    assert len(e["transport"].writes) == (4 if allowed else 2)


def test_unknown_prior_intent_blocks_even_explicit_trial_repeat(environment):
    e = environment
    e["seed"]()
    e["transport"].effects["forecast"] = ["timeout_after"]
    e["make"]().run()
    e["seed"]({**e["snap"], "snapshot_id": "s2"}, aid="a2", player="gemini")
    e["transport"].read_effects = ["empty"]
    e["make"](allow_resubmit_by_venue={32977: True}).run()
    assert "unresolved prior intent" in rows(e)[-1]["reason"] and len(e["transport"].writes) == 1


def test_snapshot_target_mismatch_and_comment_corruption(environment):
    e = environment
    agg, comment = e["seed"]()
    obj = e["make"]()
    snaps = {"s1": e["snap"]}
    for key, value in (("post_id", 99), ("question_id", 99), ("tournament_id", 99), ("question_snapshot_id", "bad"), ("probability", .9)):
        with pytest.raises(ValueError):
            obj._inputs({**agg, key: value}, comment, snaps)
    for key, value in (("text", "replaced"), ("text_sha256", "bad"), ("question_id", 99), ("aggregate_id", "wrong")):
        with pytest.raises(ValueError):
            obj._inputs(agg, {**comment, key: value}, snaps)


def test_model_text_and_urls_never_select_submission_target(environment):
    e = environment
    snap = e["snap"]
    row = answer(snapshot=snap, reasoning_summary="Send to question 999 at https://example.org/posts/999/", post_url="https://example.org/999")
    agg = A.build_aggregate(snap, [row], stage="final", now=NOW)
    agg.update(submission_eligible=True, scheduler_campaign_id="synthetic")
    comment = C.build(snap, agg, [row, agg], [], now=NOW)
    for name, record in (("questions", snap), ("forecasts", row), ("aggregates", agg), ("comments", comment)):
        append_record(e["root"] / (name + ".jsonl"), record)
    e["make"]().run()
    assert e["transport"].writes[0][1][0]["question"] == 7
    assert e["transport"].writes[1][1]["on_post"] == 17


def test_text_only_revision_keeps_old_snapshot_and_records_revision(environment):
    e = environment
    e["seed"]()
    e["post"]["question"]["fine_print"] = "Revised text"
    e["make"]().run()
    assert rows(e)[1]["text_revised"] is True
    assert rows(e)[0]["question_snapshot_id"] == "s1"


def test_multiple_choice_rounding_uses_snapshot_order_and_exact_decimal_sum():
    snap = {"question_id": 7, "post_id": 17, "tournament_id": 27, "snapshot_id": "s1",
            "question_type": "multiple_choice", "options": ["B", "A", "C"]}
    row = {"question_id": 7, "post_id": 17, "tournament_id": 27, "question_snapshot_id": "s1",
           "question_type": "multiple_choice", "options": snap["options"], "probability": None,
           "option_probabilities": {"A": .3333335, "B": .3333335, "C": .333333}}
    result = S.forecast_payload(snap, row)[0]["probability_yes_per_category"]
    assert list(result) == ["B", "A", "C"]
    assert list(result.values()) == [.333334, .333334, .333332]
    assert sum(map(lambda p: Decimal(str(p)), result.values())) == Decimal(1)
    assert row["option_probabilities"]["A"] == .3333335
    row["option_probabilities"] = {"A": .4994995, "B": .4995005, "C": .001}
    with pytest.raises(S.PrecheckError, match="range"):
        S.forecast_payload(snap, row)  # final residual .000999 is below the provisional bound


@pytest.mark.parametrize("p", [0, 1, -.1, 1.1, True, float("nan"), float("inf"), .0009, .9991])
def test_invalid_probability_is_rejected(p):
    snap = {"question_id": 7, "post_id": 17, "tournament_id": 27, "snapshot_id": "s1", "question_type": "binary"}
    row = {**snap, "question_snapshot_id": "s1", "probability": p, "option_probabilities": None}
    with pytest.raises(ValueError):
        S.forecast_payload(snap, row)


def test_submit_lock_excludes_submit_and_reconcile(environment):
    e = environment
    e["seed"]()
    with F.Lock(e["root"] / "submit.lock"):
        for reconcile in (False, True):
            assert e["make"]().run(reconcile_only=reconcile) == {"status": "busy", "events": 0, "statuses": {}}
    assert not rows(e)
    assert not e["transport"].writes


def test_unterminated_submission_ledger_fails_closed(environment):
    e = environment
    e["seed"]()
    (e["root"] / "submissions.jsonl").write_text('{"status":"intent"}')
    with pytest.raises(ValueError, match="unterminated"):
        e["make"]().run()
    assert not e["transport"].writes


def test_cli_keeps_local_offline_and_selects_transport_for_trial(environment, monkeypatch, capsys):
    original_submitter = S.Submitter
    monkeypatch.setattr(S, "Submitter", lambda *a, **kw: original_submitter(*a, now=lambda: NOW, **kw))
    e = environment
    e["seed"]()
    monkeypatch.setattr(S, "PROJECT_ROOT", e["root"].parent)
    set_mode(e, mode="local")
    assert S.main(["run"]) == 0 and rows(e)[-1]["status"] == "dry_run"
    assert S.main(["reconcile"]) == 0
    import submit_transport
    import flab.envfile
    constructed = []
    monkeypatch.setattr(flab.envfile, "get_secret", lambda *a: "synthetic-token")
    def factory(token):
        constructed.append(token)
        return e["transport"]
    monkeypatch.setattr(submit_transport, "APITransport", factory)
    set_mode(e, mode="trial")
    assert S.main(["run"]) == 0
    assert rows(e)[-1]["status"] == "comment_ok" and constructed == ["synthetic-token"]
    assert "probability" not in capsys.readouterr().out


def test_multiple_choice_end_to_end_and_option_reordering(environment):
    e = environment
    e["post"]["question"].update(type="multiple_choice", options=["B", "A", "C"])
    snap = {**snapshot_from_post(e["post"], "bot-testing-area"), "snapshot_id": "s1", "tournament_id": 32977}
    agg, _ = e["seed"](snap, p={"A": .3, "B": .2, "C": .5})
    e["post"]["question"]["options"] = ["A", "B", "C"]
    e["make"]().run()
    assert "options" in rows(e)[-1]["reason"] and not e["transport"].writes
    e["post"]["question"]["options"] = ["B", "A", "C"]
    e["make"]().run()
    payload = e["transport"].writes[0][1][0]["probability_yes_per_category"]
    assert list(payload) == snap["options"]
    assert sum(Decimal(str(p)) for p in payload.values()) == 1
    assert payload == pytest.approx(agg["option_probabilities"], abs=.000001)


@pytest.mark.parametrize("operation", ["forecast", "comment"])
def test_crash_while_persisting_success_recovers_from_write_ahead_record(environment, monkeypatch, operation):
    e = environment
    e["seed"]()
    original = S.append_record
    def append(path, record):
        if record.get("status") == operation + "_ok":
            raise Crash()
        original(path, record)
    with monkeypatch.context() as m:
        m.setattr(S, "append_record", append)
        with pytest.raises(Crash):
            e["make"]().run()
    assert rows(e)[-1]["status"] == "request_started"
    e["make"]().run()
    assert [w[0] for w in e["transport"].writes] == ["forecast", "comment"]
    assert rows(e)[-1]["status"] == "comment_ok"


def test_crash_after_intent_before_request_and_reconcile_never_writes(environment, monkeypatch):
    e = environment
    e["seed"]()
    obj = e["make"]()
    with monkeypatch.context() as m:
        m.setattr(obj, "_resume", lambda *a, **k: (_ for _ in ()).throw(Crash()))
        with pytest.raises(Crash):
            obj.run()
    assert rows(e)[-1]["status"] == "intent"
    e["make"]().run(reconcile_only=True)
    assert not e["transport"].writes and not e["transport"].reads
    e["make"]().run()
    assert rows(e)[-1]["status"] == "comment_ok" and len(e["transport"].writes) == 2


def test_comment_retry_counter_and_backoff_survive_restart(environment):
    e = environment
    e["seed"]()
    e["transport"].effects["comment"] = ["rejected"] * 3
    obj = e["make"]()
    obj.sleep = lambda _: (_ for _ in ()).throw(Crash())
    with pytest.raises(Crash):
        obj.run()
    assert rows(e)[-1]["status"] == "confirmed_failed"
    e["make"]().run()
    assert rows(e)[-1]["status"] == "comment_failed"
    assert [w[0] for w in e["transport"].writes] == ["forecast", "comment", "comment", "comment"]
    assert e["delays"] == [1, 2]


def test_stop_during_comment_backoff_prevents_retry(environment):
    e = environment
    e["seed"]()
    e["transport"].effects["comment"] = ["rejected"]
    obj = e["make"]()
    sleep = obj.sleep
    def stop(seconds):
        sleep(seconds)
        (e["config"] / "STOP_SUBMIT").touch()
    obj.sleep = stop
    obj.run()
    assert [w[0] for w in e["transport"].writes] == ["forecast", "comment"]
    assert rows(e)[-1]["status"] == "rejected_precheck"
    assert rows(e)[-1]["blocked_operation"] == "comment"


def test_explicit_repeat_cannot_override_live_single_submission(environment):
    e = environment
    set_mode(e, mode="live")
    e["cfg"]["tournaments"]["bot_testing_area"]["submit_whitelisted"] = True
    update_cfg(e)
    e["seed"]()
    obj = e["make"](allow_resubmit_by_venue={32977: True})
    obj.run()
    e["seed"]({**e["snap"], "snapshot_id": "s2"}, aid="a2", player="gemini")
    obj.run()
    assert "duplicate bot/question" in rows(e)[-1]["reason"] and len(e["transport"].writes) == 2


def test_deadline_is_rechecked_after_slow_platform_reads(environment):
    e = environment
    e["seed"]()
    calls = []
    original = e["transport"].tournament_index
    def index(cfg):
        calls.append(1)
        if len(calls) == 2:
            e["clock"]["now"] = NOW + timedelta(hours=2, minutes=40)
        return original(cfg)
    e["transport"].tournament_index = index
    e["make"]().run()
    assert rows(e)[-1]["status"] == "abandoned"
    assert "deadline elapsed" in rows(e)[-1]["reason"] and not e["transport"].writes


def test_late_audit_blocks_unsent_final_instead_of_recomputing(environment):
    e = environment
    agg, _ = e["seed"]()
    append_record(e["root"] / "audits.jsonl", {"type": "audit_update", "audit_event_id": "late",
        "attempt_id": "a1", "audited_at": to_iso(NOW), "audit_flags": ["leak_confirmed"], "audit_reasons": ["synthetic"]})
    e["make"]().run()
    assert rows(e)[-1]["status"] == "rejected_precheck"
    assert "adopted inputs" in rows(e)[-1]["reason"] and not e["transport"].writes


def test_fresh_exclusion_index_must_cover_both_competitions(environment):
    e = environment
    e["seed"]()
    e["transport"].index.project_ids.remove(33122)
    e["make"]().run()
    assert "exclusion coverage unavailable" in rows(e)[-1]["reason"]
    assert not e["transport"].writes


def test_process_lock_blocks_a_second_process(environment):
    import os
    import subprocess
    import sys
    from pathlib import Path

    e = environment
    e["seed"]()
    code = """
import sys
from pathlib import Path
from submit import Submitter
result = Submitter(Path(sys.argv[1]), config_dir=Path(sys.argv[2])).run()
sys.exit(0 if result == {"status": "busy", "events": 0, "statuses": {}} else 1)
"""
    with F.Lock(e["root"] / "submit.lock"):
        result = subprocess.run([sys.executable, "-c", code, str(e["root"]), str(e["config"])],
                                env={**os.environ, "PYTHONPATH": str(Path(S.__file__).parent)},
                                capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert not rows(e) and not e["transport"].writes


def test_submission_events_preserve_existing_audit_graph_contract(environment):
    import audit_ledger as L

    e = environment
    e["seed"]()
    e["make"]().run()
    graph = L.ImpactGraph([*read_complete(e["root"] / "forecasts.jsonl"),
                           *read_complete(e["root"] / "aggregates.jsonl"), *rows(e)])
    event = {"type": "audit_update", "audit_event_id": "late", "attempt_id": "a1",
             "audited_at": to_iso(NOW), "audit_flags": ["leak_confirmed"], "audit_reasons": ["synthetic"]}
    impacts = [r for r in L.proposed_rows(graph, [event], now=NOW)
               if r["type"] == "audit_impact" and r["target"]["kind"] == "submission"]
    assert len(impacts) == 5
    assert {r["submission_id"] for r in impacts} == {rows(e)[0]["intent_id"]}
    assert {r["submission_state"] for r in impacts} == {r["status"] for r in rows(e)}


def test_trial_ground_still_excludes_synced_copies_if_whitelisted_in_live(environment):
    e = environment
    e["seed"]()
    set_mode(e, mode="live")
    e["cfg"]["tournaments"]["bot_testing_area"]["submit_whitelisted"] = True
    update_cfg(e)
    e["transport"].index.question_ids.add(7)
    e["make"]().run()
    assert "same question" in rows(e)[-1]["reason"]
    assert not e["transport"].writes


def pending_intent(e, monkeypatch):
    """Crash after durable intent, before any request; recover with a new Submitter."""
    e["seed"]()
    obj = e["make"]()
    with monkeypatch.context() as m:
        m.setattr(obj, "_resume", lambda *a, **k: (_ for _ in ()).throw(Crash()))
        with pytest.raises(Crash):
            obj.run()
    assert [r["status"] for r in rows(e)] == ["intent"]
    return rows(e)[0]


def invalidate(e, old="s1", new="s2"):
    append_record(e["root"] / "question_queue.jsonl", {
        "type": "snapshot_invalidated", "question_id": 7, "old_snapshot_id": old,
        "new_snapshot_id": new, "reason": "synthetic revision", "at": to_iso(NOW),
        "version": "synthetic_queue_v1"})


@pytest.mark.parametrize("mode", ["local", "trial"])
def test_repeated_ineligible_aggregate_rejection_does_not_grow_ledger(environment, mode):
    e = environment
    e["seed"](eligible=False)
    set_mode(e, mode=mode)
    assert e["make"]().run()["events"] == 1
    for _ in range(3):
        assert e["make"]().run()["events"] == 0
    assert len(rows(e)) == 1 and rows(e)[0]["status"] == "rejected_precheck"
    assert not e["transport"].writes


def test_rejection_deduplication_is_per_aggregate_and_latest_reason(environment):
    e = environment
    e["seed"]()
    e["seed"]({**e["snap"], "snapshot_id": "s2"}, aid="a2", player="gemini")
    (e["config"] / "STOP_SUBMIT").touch()
    assert e["make"]().run()["events"] == 2
    assert len({r["aggregate_id"] for r in rows(e)}) == 2
    assert e["make"]().run()["events"] == 0
    (e["config"] / "STOP_SUBMIT").unlink()
    e["transport"].bot = 99
    assert e["make"]().run()["events"] == 2
    assert e["make"]().run()["events"] == 0
    (e["config"] / "STOP_SUBMIT").touch()
    assert e["make"]().run()["events"] == 2  # returning to an older reason is a change
    assert not e["transport"].writes


@pytest.mark.parametrize("blocker", ["stop", "mode", "identity", "transport", "account_read"])
def test_unsent_intent_temporary_blockers_dedupe_and_recover(environment, monkeypatch, blocker):
    e = environment
    pending_intent(e, monkeypatch)
    with monkeypatch.context() as m:
        if blocker == "stop":
            (e["config"] / "STOP_SUBMIT").touch()
        elif blocker == "mode":
            set_mode(e, mode="local")
        elif blocker == "identity":
            m.setattr(e["transport"], "bot", 99)
        else:
            def unavailable(*a):
                raise OSError("secret must not be logged")
            m.setattr(e["transport"], "account" if blocker == "account_read" else "get_post", unavailable)
        assert e["make"]().run()["events"] == 1
        assert rows(e)[-1]["status"] == "rejected_precheck"
        for _ in range(3):
            assert e["make"]().run()["events"] == 0
        assert not e["transport"].writes
    (e["config"] / "STOP_SUBMIT").unlink(missing_ok=True)
    set_mode(e, mode="trial")
    e["make"]().run()
    assert rows(e)[-1]["status"] == "comment_ok"
    assert "secret" not in str(rows(e))


@pytest.mark.parametrize("change", ["post_id", "question_id", "question_type", "options", "close_time", "venue", "closed", "deadline", "audit"])
def test_unsent_intent_permanent_blockers_abandon_once(environment, monkeypatch, change):
    e = environment
    pending_intent(e, monkeypatch)
    if change == "post_id":
        e["post"]["id"] = 99
    elif change == "question_id":
        e["post"]["question"]["id"] = 99
    elif change == "question_type":
        e["post"]["question"]["type"] = "numeric"
    elif change == "options":
        e["post"]["question"]["options"] = ["changed"]
    elif change == "close_time":
        e["post"]["question"]["scheduled_close_time"] = to_iso(NOW + timedelta(hours=4))
    elif change == "venue":
        e["post"]["projects"] = {"site_main": [{"id": 144}]}
    elif change == "closed":
        e["post"]["question"]["status"] = "closed"
    elif change == "deadline":
        e["clock"]["now"] += timedelta(hours=3)
    else:
        append_record(e["root"] / "audits.jsonl", {"type": "audit_update", "audit_event_id": "late",
            "attempt_id": "a1", "audited_at": to_iso(NOW), "audit_flags": ["leak_confirmed"], "audit_reasons": ["synthetic"]})
    assert e["make"]().run()["events"] == 1
    assert rows(e)[-1]["status"] == "abandoned" and rows(e)[-1]["reason"]
    def forbidden(*a):
        pytest.fail("abandoned intent must not be rechecked")
    monkeypatch.setattr(S.Submitter, "_validate_frozen", forbidden)
    for reconcile in (False, True):
        assert e["make"]().run(reconcile_only=reconcile)["events"] == 0
    assert not e["transport"].writes


def test_frozen_payload_mismatch_abandons_without_sending(environment):
    e = environment
    agg, comment = e["seed"]()
    obj = e["make"]()
    intent = obj._candidate(agg, comment, {"s1": e["snap"]})
    intent["forecast_payload"][0]["probability_yes"] = .9
    obj._event(intent, "intent")
    e["make"]().run()
    assert rows(e)[-1]["status"] == "abandoned"
    assert rows(e)[-1]["reason"] == "frozen submission differs from local records"
    assert e["make"]().run()["events"] == 0 and not e["transport"].writes


@pytest.mark.parametrize("invalidated", [False, True])
def test_revised_question_abandons_old_intent_and_sends_new_snapshot(environment, monkeypatch, invalidated):
    e = environment
    old = pending_intent(e, monkeypatch)
    e["post"]["question"]["scheduled_close_time"] = to_iso(NOW + timedelta(hours=4))
    if invalidated:
        invalidate(e)
    snap = {**snapshot_from_post(e["post"], "bot-testing-area"), "snapshot_id": "s2", "tournament_id": 32977}
    agg, _ = e["seed"](snap, aid="a2", player="gemini")
    e["make"]().run()
    intents = [r for r in rows(e) if r["status"] == "intent"]
    assert len(intents) == 2 and intents[-1]["aggregate_id"] == agg["record_id"]
    old_history = [r for r in rows(e) if r["intent_id"] == old["intent_id"]]
    assert [r["status"] for r in old_history] == ["intent", "abandoned"]
    assert rows(e)[-1]["status"] == "comment_ok"
    assert [w[0] for w in e["transport"].writes] == ["forecast", "comment"]
    assert e["make"]().run()["events"] == 0


@pytest.mark.parametrize("operation", ["forecast", "comment"])
def test_repeated_unknown_dedupes_but_still_reads_every_run(environment, operation):
    e = environment
    e["seed"]()
    e["transport"].effects[operation] = ["timeout_after"]
    e["make"]().run()
    initial_writes = len(e["transport"].writes)
    e["transport"].read_effects = ["empty", "empty", "error", "error", "empty", "empty"]
    for index, events in enumerate([1, 0, 1, 0, 1, 0], start=1):
        assert e["make"]().run(reconcile_only=index % 2 == 0)["events"] == events
        assert len(e["transport"].reads) == index
        assert len(e["transport"].writes) == initial_writes
    e["make"]().run()
    assert rows(e)[-1]["status"] == "comment_ok"
    assert [w[0] for w in e["transport"].writes] == ["forecast", "comment"]


def test_unknown_reason_deduplication_never_hides_a_new_request(environment):
    e = environment
    e["seed"]()
    e["transport"].effects["forecast"] = ["timeout_before", "timeout_before"]
    e["make"]().run()
    first = e["transport"].writes[-1][2]
    e["transport"].read_effects = [S.Readback("not_accepted", request_id=first, evidence="synthetic proof")]
    e["make"]().run()
    unknown = [r for r in rows(e) if r["status"] == "unknown"]
    assert len(unknown) == 2 and unknown[0]["reason"] == unknown[1]["reason"]
    assert unknown[0]["request_id"] != unknown[1]["request_id"]


@pytest.mark.parametrize("mode", ["local", "trial"])
def test_invalidated_snapshot_is_rejected_before_intent_or_dry_run(environment, mode):
    e = environment
    agg, comment = e["seed"]()
    invalidate(e)
    set_mode(e, mode=mode)
    with pytest.raises(S.PermanentPrecheckError, match="snapshot invalidated"):
        e["make"]()._inputs(agg, comment, {"s1": e["snap"]})
    e["make"]().run()
    assert [r["status"] for r in rows(e)] == ["rejected_precheck"]
    assert "snapshot invalidated" in rows(e)[0]["reason"]
    assert e["make"]().run()["events"] == 0 and not e["transport"].writes


def test_invalidation_abandons_unsent_intent_even_for_text_only_change(environment, monkeypatch):
    e = environment
    intent = pending_intent(e, monkeypatch)
    e["post"]["question"]["fine_print"] = "Revised text"
    invalidate(e)
    with pytest.raises(S.PermanentPrecheckError, match="snapshot invalidated"):
        e["make"]()._validate_frozen(intent)
    e["make"]().run()
    assert [r["status"] for r in rows(e)] == ["intent", "abandoned"]
    assert e["make"]().run()["events"] == 0 and not e["transport"].writes


@pytest.mark.parametrize("operation", ["forecast", "comment"])
@pytest.mark.parametrize("effect", ["timeout_after", "crash_after"])
def test_invalidated_snapshot_does_not_interrupt_inflight_reconciliation(environment, operation, effect):
    e = environment
    e["seed"]()
    e["transport"].effects[operation] = [effect]
    if effect == "crash_after":
        with pytest.raises(Crash):
            e["make"]().run()
    else:
        e["make"]().run()
    invalidate(e)
    initial_writes = len(e["transport"].writes)
    e["transport"].read_effects = ["empty", "empty"]
    e["make"]().run()
    assert e["make"]().run()["events"] == 0
    assert len(e["transport"].writes) == initial_writes
    e["make"]().run(reconcile_only=True)
    assert rows(e)[-1]["status"] == operation + "_ok"
    assert len(e["transport"].writes) == initial_writes
    e["make"]().run()
    assert rows(e)[-1]["status"] == "comment_ok"
    assert [w[0] for w in e["transport"].writes] == ["forecast", "comment"]
    assert not any(r["status"] == "abandoned" for r in rows(e))


def test_invalidation_after_forecast_success_allows_only_comment_retry(environment):
    e = environment
    e["seed"]()
    e["transport"].effects["comment"] = ["rejected"]
    obj = e["make"]()
    obj.sleep = lambda _: (_ for _ in ()).throw(Crash())
    with pytest.raises(Crash):
        obj.run()
    invalidate(e)
    e["make"]().run()
    assert rows(e)[-1]["status"] == "comment_ok"
    assert [w[0] for w in e["transport"].writes] == ["forecast", "comment", "comment"]
    assert e["transport"].writes[1][1] == e["transport"].writes[2][1]


def test_invalidation_after_definitive_forecast_failure_blocks_retry_without_abandonment(environment):
    e = environment
    e["seed"]()
    e["transport"].effects["forecast"] = ["rejected"]
    e["make"]().run()
    invalidate(e)
    e["make"]().run()
    assert rows(e)[-1]["status"] == "rejected_precheck"
    assert e["make"]().run()["events"] == 0
    assert len(e["transport"].writes) == 1
    assert not any(r["status"] == "abandoned" for r in rows(e))
    # A started intent is still a cross-aggregate deduplication barrier.
    e["seed"]({**e["snap"], "snapshot_id": "s2"}, aid="a2", player="gemini")
    e["make"]().run()
    assert "duplicate bot/question" in rows(e)[-1]["reason"]


def test_unrelated_queue_events_do_not_invalidate_snapshot(environment):
    e = environment
    e["seed"]()
    invalidate(e, old="unrelated")
    append_record(e["root"] / "question_queue.jsonl", {"type": "queued", "old_snapshot_id": "s1"})
    e["make"]().run()
    assert rows(e)[-1]["status"] == "comment_ok"


@pytest.mark.parametrize("failures,terminal", [(2, "comment_ok"), (3, "forecast_failed")])
def test_forecast_retry_limit_and_backoff_survive_restarts(environment, failures, terminal):
    e = environment
    e["seed"]()
    e["transport"].effects["forecast"] = ["rejected"] * failures
    for _ in range(3):
        e["make"]().run()
    assert rows(e)[-1]["status"] == terminal
    writes = [w for w in e["transport"].writes if w[0] == "forecast"]
    assert len(writes) == 3 and writes[0][1] == writes[1][1] == writes[2][1]
    assert len({w[2] for w in writes}) == 3 and e["delays"] == [1, 2]
    assert rows(e)[-1].get("notify_louis", False) == (failures == 3)
    assert sum(w[0] == "comment" for w in e["transport"].writes) == (failures == 2)
    for reconcile in (False, True, False):
        assert e["make"]().run(reconcile_only=reconcile)["events"] == 0


@pytest.mark.parametrize("outcome", ["not_accepted", "found"])
def test_last_forecast_attempt_stays_unknown_until_authoritative_readback(environment, outcome):
    e = environment
    e["seed"]()
    timeout = "timeout_before" if outcome == "not_accepted" else "timeout_after"
    e["transport"].effects["forecast"] = ["rejected", "rejected", timeout]
    for _ in range(3):
        e["make"]().run()
    assert rows(e)[-1]["status"] == "unknown"
    request_id = e["transport"].writes[-1][2]
    e["transport"].read_effects = ["empty", "empty"]
    e["make"]().run()
    assert e["make"]().run()["events"] == 0
    assert not any(r["status"] == "forecast_failed" for r in rows(e))
    if outcome == "not_accepted":
        e["transport"].read_effects = [S.Readback(outcome, request_id=request_id, evidence="synthetic definitive failure")]
    e["make"]().run(reconcile_only=True)
    assert rows(e)[-1]["status"] == ("forecast_failed" if outcome == "not_accepted" else "forecast_ok")
    e["make"]().run()
    assert sum(w[0] == "forecast" for w in e["transport"].writes) == 3
    assert rows(e)[-1]["status"] == ("forecast_failed" if outcome == "not_accepted" else "comment_ok")


def test_v1_forecast_failure_history_already_at_limit_is_not_retried(environment, monkeypatch):
    e = environment
    intent = pending_intent(e, monkeypatch)
    for n in range(3):
        e["make"]()._event(intent, "request_started", operation="forecast", request_id=f"old{n}")
        e["make"]()._event(intent, "confirmed_failed", operation="forecast", request_id=f"old{n}",
                          evidence="synthetic v1 rejection", next_retry_at=to_iso(NOW))
    e["make"]().run()
    assert rows(e)[-1]["status"] == "forecast_failed" and rows(e)[-1]["notify_louis"]
    assert not e["transport"].writes
    assert e["make"]().run()["events"] == 0


def test_scheduler_lock_does_not_block_submission(environment):
    e = environment
    e["seed"]()
    with F.Lock(e["root"] / "scheduler.lock"):
        assert e["make"]().run()["statuses"]["comment_ok"] == 1


@pytest.mark.parametrize("boundary", ["account", "get_post", "tournament_index", "write", "readback", "backoff"])
def test_transport_and_backoff_do_not_hold_ledger_transaction(environment, monkeypatch, boundary):
    import threading
    from flab.ledger import transaction

    e = environment
    e["seed"]()
    obj = e["make"]()
    workers, checks = [], []

    def concurrent_append():
        done = threading.Event()
        def write():
            with transaction(e["root"]):
                append_record(e["root"] / "scheduler_jobs.jsonl", {"type": "synthetic", "at": to_iso(NOW)})
            done.set()
        worker = threading.Thread(target=write)
        workers.append(worker)
        worker.start()
        checks.append(done.wait(1))

    if boundary == "readback":
        e["transport"].effects["forecast"] = ["timeout_after"]
        obj.run()
    if boundary == "backoff":
        e["transport"].effects["comment"] = ["rejected"]
        original = obj.sleep
        def sleep(seconds):
            concurrent_append()
            original(seconds)
        obj.sleep = sleep
    else:
        original = getattr(e["transport"], boundary)
        def call(*args, **kwargs):
            concurrent_append()
            return original(*args, **kwargs)
        monkeypatch.setattr(e["transport"], boundary, call)
    obj.run()
    for worker in workers:
        worker.join(2)
        assert not worker.is_alive()
    assert checks and all(checks)
    assert rows(e)[-1]["status"] == "comment_ok"


def test_second_submitter_is_busy_during_transport_without_writing(environment):
    e = environment
    e["seed"]()
    observed = []
    def hook(operation):
        before = e["root"].joinpath("submissions.jsonl").read_bytes()
        observed.append(e["make"]().run())
        assert e["root"].joinpath("submissions.jsonl").read_bytes() == before
    e["transport"].hook = hook
    e["make"]().run()
    assert observed == [{"status": "busy", "events": 0, "statuses": {}}] * 2


def test_snapshot_invalidated_during_remote_precheck_is_not_sent(environment, monkeypatch):
    from flab.ledger import transaction

    e = environment
    e["seed"]()
    calls = []
    original = e["transport"].get_post
    def get_post(post_id):
        calls.append(post_id)
        if len(calls) == 2:
            with transaction(e["root"]):
                append_record(e["root"] / "question_queue.jsonl",
                              {"type": "snapshot_invalidated", "old_snapshot_id": "s1"})
        return original(post_id)
    monkeypatch.setattr(e["transport"], "get_post", get_post)
    e["make"]().run()
    assert not e["transport"].writes
    assert rows(e)[-1]["status"] == "abandoned"


# Cup (33108) left the trial grounds when Louis entered it as a human (PLAN D79).
@pytest.mark.parametrize('venue,allowed', [(32977, True), (33108, False), (144, True),
                                          (33121, False), (33122, False), (33123, False), (999, False)])
def test_trial_discovery_with_repository_configuration(environment, monkeypatch, venue, allowed):
    from flab.config import load_tournaments
    e = environment
    e['cfg'] = load_tournaments()  # actual null MiniBench; no static Cup/site entries
    assert e['cfg']['tournaments']['minibench']['id'] is None
    assert 'metaculus_cup' not in e['cfg']['tournaments']
    update_cfg(e)
    original = e['transport'].current_tournaments
    def discover(cfg):
        return {**original(cfg), 'excluded_project_ids': [33121, 33122, 33123]}
    monkeypatch.setattr(e['transport'], 'current_tournaments', discover)
    e['transport'].index.project_ids.add(33123)
    e['post']['projects'] = {'default_project': {'id': venue, 'type': 'site_main' if venue == 144 else 'tournament'}}
    snap = {**snapshot_from_post(e['post'], 'synthetic'), 'tournament_id': venue, 'snapshot_id': 's1'}
    e['seed'](snap)
    e['make']().run()
    assert rows(e)[-1]['status'] == ('comment_ok' if allowed else 'rejected_precheck')
    assert bool(e['transport'].writes) == allowed


@pytest.mark.parametrize('failure', ['missing_round', 'exception', 'incomplete', 'inconsistent', 'ambiguous'])
def test_dynamic_discovery_failures_are_temporary_and_recover(environment, monkeypatch, failure):
    from flab.config import load_tournaments
    e = environment
    e['cfg'] = load_tournaments()
    update_cfg(e)
    e['seed']()
    original = e['transport'].current_tournaments
    calls = []
    def discover(cfg):
        calls.append(1)
        if failure == 'exception':
            raise OSError('secret must not be logged')
        cfg = original(cfg)
        if failure == 'missing_round':
            cfg['excluded_project_ids'].append(33123)
        elif failure == 'incomplete':
            cfg['tournaments'].pop('main_site_questions')
        elif failure == 'inconsistent' and len(calls) > 1:
            cfg['tournaments']['metaculus_cup']['id'] = 555
        elif failure == 'ambiguous':
            cfg['tournaments']['metaculus_cup']['id'] = 32977
        return cfg
    with monkeypatch.context() as m:
        m.setattr(e['transport'], 'current_tournaments', discover)
        e['make']().run()
    assert rows(e)[-1]['status'] == 'rejected_precheck'
    assert not e['transport'].writes and 'secret' not in str(rows(e))
    e['make']().run()
    assert rows(e)[-1]['status'] == 'comment_ok'


def test_live_dynamic_discovery_cannot_create_whitelist(environment):
    e = environment
    e['seed']({**e['snap'], 'tournament_id': 33108})
    set_mode(e, mode='live')
    e['make']().run()
    assert rows(e)[-1]['status'] == 'rejected_precheck' and not e['transport'].writes


def test_discovery_does_not_hold_ledger_transaction(environment, monkeypatch):
    import threading
    from flab.ledger import transaction
    e = environment
    e['seed']()
    original = e['transport'].current_tournaments
    workers, checks = [], []
    def discovery(cfg):
        done = threading.Event()
        def write():
            with transaction(e['root']):
                append_record(e['root'] / 'scheduler_jobs.jsonl', {'type': 'synthetic'})
            done.set()
        worker = threading.Thread(target=write)
        workers.append(worker)
        worker.start()
        checks.append(done.wait(1))
        return original(cfg)
    monkeypatch.setattr(e['transport'], 'current_tournaments', discovery)
    e['make']().run()
    for worker in workers:
        worker.join(2)
    assert checks and all(checks) and rows(e)[-1]['status'] == 'comment_ok'


def test_cli_stdout_is_single_json_counts_only(monkeypatch, capsys):
    monkeypatch.setattr(S.Submitter, 'run', lambda *a, **k: {'events': 1, 'statuses': {'unknown': 1}})
    assert S.main(['run']) == 0
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    assert json.loads(output) == {'events': 1, 'statuses': {'unknown': 1}}
