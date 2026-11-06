"""D105 offline selection tests: all posts and ledgers are synthetic."""
import copy
import json
from datetime import timedelta

import pytest

import continuous as Q
import forecast as F
from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import to_iso, parse_iso
from test_cycle import fake_api, policy
from test_forecast import NOW
from test_poll_questions import _post

SITE = 40000
LEDGER = "main_site_trial_selections.jsonl"


def setting(n=4, p=2):
    return {"version": Q.MAIN_SITE_VERSION, "max_questions": n, "max_pages": p}


def campaign(n=4, p=2):
    return {**policy(), "venues": ["main_site_questions"], "main_site_trial": setting(n, p)}


def post(qid, kind="binary", minutes=180):
    item = _post(qid + 1000, qid, kind, f"Synthetic private title {qid}",
                 open_time=to_iso(NOW - timedelta(minutes=10)),
                 scheduled_close_time=to_iso(NOW + timedelta(minutes=minutes)),
                 options=["A", "B"] if kind == "multiple_choice" else None)
    item["projects"] = {"site_main": [{"id": SITE}]}
    return item


@pytest.fixture
def main_api(fake_api, monkeypatch, tmp_path, request, question_type_flags):
    numeric_enabled = getattr(request, "param", False)
    question_type_flags(numeric_enabled)
    expected_types = "binary,multiple_choice,numeric,discrete" if numeric_enabled else "binary,multiple_choice"
    client, cfg = fake_api
    cfg["trial_ground_types"].append("main_site_questions")
    cfg["tournaments"]["main_site_questions"] = {"id": SITE, "submit_whitelisted": False}
    client.pages = [[post(100), post(101), post(102, "multiple_choice"), post(103), post(104)]]
    client.details = {}
    client.feed_calls, client.detail_calls = [], []
    original_get = client.get

    def page(number):
        return {"results": copy.deepcopy(client.pages[number]),
                "next": f"https://www.metaculus.com/api/posts/?offset={number+1}"
                if number + 1 < len(client.pages) else None}

    def get(path, params=None):
        if path != "posts/":
            return original_get(path)
        assert params == {"main_feed": "true", "statuses": "open",
                          "forecast_type": expected_types, "limit": 50, "offset": 0,
                          "include_description": "true"}
        client.feed_calls.append(0)
        return page(0)

    def next_page(url):
        number = int(url.rsplit("=", 1)[1])
        client.feed_calls.append(number)
        return page(number)

    def detail(pid):
        client.detail_calls.append(pid)
        return copy.deepcopy(client.details.get(pid) or next(
            item for items in client.pages for item in items if item["id"] == pid))

    monkeypatch.setattr(client, "get", get)
    monkeypatch.setattr(client, "_get_url", next_page, raising=False)
    monkeypatch.setattr(client, "get_post", detail, raising=False)
    return client, lambda cfg=None, now=NOW: Q.Poller(
        client, tmp_path, cfg or campaign(), now=lambda: now)()


@pytest.mark.parametrize("rollover", [False, True])
@pytest.mark.parametrize("bad", [
    {"main_site_trial": None}, {"main_site_trial": {}},
    {"main_site_trial": {**setting(), "version": "unknown"}},
    *({"main_site_trial": {**setting(), key: value}}
      for key, values in (("max_questions", [0, 7, True, 1.5, "4", None]),
                          ("max_pages", [0, 6, False, 2.5, "2", None])) for value in values),
    *({"version": f"continuous_v{i}"} for i in range(2, 8)),
    {"venues": ["bot_testing_area"]},
])
def test_policy_rejects_invalid_settings(tmp_path, bad, rollover):
    cfg = {**campaign(), **bad}
    if rollover:
        cfg["rollover"] = {"version": Q.ROLLOVER_VERSION, "windows": 2}
    with pytest.raises(ValueError):
        Q.active_campaign(tmp_path, cfg, now=lambda: NOW)
    assert not read_complete(tmp_path / "continuous_campaigns.jsonl")
    assert not read_complete(tmp_path / "continuous_series.jsonl")


@pytest.mark.parametrize("rollover", [False, True])
def test_policy_requires_setting(tmp_path, rollover):
    cfg = campaign()
    del cfg["main_site_trial"]
    if rollover:
        cfg["rollover"] = {"version": Q.ROLLOVER_VERSION, "windows": 2}
    with pytest.raises(ValueError):
        Q.active_campaign(tmp_path, cfg, now=lambda: NOW)


@pytest.mark.parametrize("n,p", [(1, 1), (6, 5)])
@pytest.mark.parametrize("rollover", [False, True])
def test_valid_policy_is_frozen(tmp_path, n, p, rollover):
    cfg = campaign(n, p)
    if rollover:
        cfg["rollover"] = {"version": Q.ROLLOVER_VERSION, "windows": 2}
    first = Q.active_campaign(tmp_path, cfg, now=lambda: NOW)
    assert Q.active_campaign(tmp_path, cfg, now=lambda: NOW) == first
    assert len(read_complete(tmp_path / "continuous_campaigns.jsonl")) == 1
    changed = copy.deepcopy(cfg)
    changed["main_site_trial"]["max_questions"] = 2
    with pytest.raises(ValueError, match="cannot change"):
        Q.active_campaign(tmp_path, changed, now=lambda: NOW)


def test_legacy_v7_non_main_rollover_still_works(tmp_path):
    cfg = {**policy(), "version": "continuous_v7",
           "rollover": {"version": Q.ROLLOVER_VERSION, "windows": 2}}
    assert Q.active_campaign(tmp_path, cfg, now=lambda: NOW)["window_index"] == 0


@pytest.mark.parametrize("mode", ["local", "trial"])
def test_bounded_stable_selection_and_content_isolation(main_api, tmp_path, monkeypatch, mode):
    client, run = main_api
    monkeypatch.setattr(Q, "load_submit_mode", lambda: {"mode": mode})
    first, second = run(), run()
    assert list(first) == [100, 102, 101, 103]
    assert set(second) == set(first)
    assert {s["question_type"] for s in first.values()} == {"binary", "multiple_choice"}
    assert all(first[q]["snapshot_id"] == second[q]["snapshot_id"] for q in first)
    assert client.feed_calls == [0]
    assert set(client.detail_calls) == {1100, 1101, 1102, 1103}
    assert all(project != SITE for project, _ in client.calls)
    rows = read_complete(tmp_path / LEDGER)
    assert len(rows) == 4
    assert all(set(row) == {"type", "version", "campaign_id", "question_id", "post_id",
                           "question_type", "decision_deadline", "selected_at"} for row in rows)
    assert {s["question_id"] for s in read_complete(tmp_path / "questions.jsonl")} == set(first)
    for filename in (LEDGER, "poll_log.jsonl"):
        text = (tmp_path / filename).read_text()
        assert "Synthetic" not in text and "background" not in text and "aggregations" not in text
    assert all(s["tournament_id"] == SITE and s["ground"] == "main_site_questions"
               and s["priority"] == "main" and s["status"] == "open" for s in first.values())


def test_candidate_exclusions(main_api, tmp_path):
    client, run = main_api
    invalid = [post(qid) for qid in range(200, 222)]
    invalid[0]["question"]["type"] = "numeric"
    for i, tournament in enumerate((33108, 1, 2), 1):
        invalid[i]["projects"]["tournament"] = [{"id": tournament}]
    invalid[4]["title"] = "Tournament private title"  # title copy of fake_api's excluded post
    invalid[5]["question"]["id"] = 20  # ID copy
    invalid[6]["id"] = 10  # post ID copy
    invalid[7]["question"]["open_time"] = to_iso(NOW + timedelta(minutes=1))
    invalid[8]["question"]["open_time"] = None
    invalid[9]["question"]["scheduled_close_time"] = to_iso(NOW)
    invalid[10]["question"]["scheduled_close_time"] = None
    invalid[11]["question"]["my_forecasts"] = {"history": [{"id": 1}], "latest": None}
    invalid[12]["projects"] = {"category": [{"id": 777}]}
    invalid[13]["question"]["status"] = "closed"
    invalid[14]["question"]["open_time"] = "invalid private time"
    invalid[15]["group_of_questions"] = {"questions": []}
    for i, filename in enumerate(("forecasts.jsonl", "forecast_attempt_starts.jsonl", "submissions.jsonl"), 16):
        append_record(tmp_path / filename, {"question_id": 200+i})
    append_record(tmp_path / "campaigns.jsonl", {"campaign": {"questions": [{"question_id": 219}]}})
    plan = {"bot_id": 42, "question_id": 220}
    append_record(tmp_path / "v15_probes.jsonl", {"state": "prepared", "probe_id": "p",
                  "plan": plan, "plan_sha256": hash_obj(plan)})
    append_record(tmp_path / "v15_probes.jsonl", {"state": "request_started", "probe_id": "p", "operation": "forecast"})
    append_record(tmp_path / LEDGER, {"type": "main_site_trial_dropped",
                  "campaign_id": "old", "question_id": 221, "reason": "closed", "at": to_iso(NOW)})
    client.pages = [invalid + client.pages[0]]
    assert set(run()) == {100, 101, 102, 103}
    summary = read_complete(tmp_path / "poll_log.jsonl")[-1]
    assert sum(summary["reasons"].values()) == len(invalid)
    assert "private" not in json.dumps(summary)


@pytest.mark.parametrize("kind", ["binary", "multiple_choice"])
def test_single_type_fills_in_deadline_id_order(main_api, kind):
    client, run = main_api
    client.pages = [[post(105, kind, 500), post(104, kind, 400), post(103, kind, 400),
                     post(102, kind, 350), post(101, kind, 300)]]
    assert list(run()) == [101, 102, 103, 104]


def test_type_coverage_precedes_deadline_and_n1_prefers_binary(main_api):
    client, run = main_api
    client.pages = [[post(100, minutes=500), post(101, "multiple_choice", 300)]]
    assert list(run(campaign(1))) == [100]


@pytest.mark.parametrize("change", ["closed", "tournament", "synced", "deadline", "open_time", "identity"])
def test_dropped_slot_never_replaced(main_api, tmp_path, change):
    client, run = main_api
    run()
    changed = copy.deepcopy(client.pages[0][0])
    if change == "closed":
        changed["question"]["status"] = "closed"
    elif change == "tournament":
        changed["projects"]["tournament"] = [{"id": 33108}]
    elif change == "synced":
        changed["title"] = "Tournament private title"
    elif change == "deadline":
        changed["question"]["scheduled_close_time"] = to_iso(NOW)
    elif change == "open_time":
        changed["question"]["open_time"] = None
    else:
        changed["question"]["id"] = 999
    client.details[1100] = changed
    assert set(run()) == {101, 102, 103}
    assert set(run()) == {101, 102, 103}
    assert client.feed_calls == [0]
    rows = read_complete(tmp_path / LEDGER)
    assert len(rows) == 5 and rows[-1]["type"] == "main_site_trial_dropped"
    assert rows[-1]["question_id"] == 100


def test_drop_stops_additions_even_when_previous_scan_underfilled(main_api, tmp_path):
    client, run = main_api
    client.pages = [[post(100)]]
    assert set(run()) == {100}
    client.pages = [[post(100), post(101), post(102)]]
    client.pages[0][0]["question"]["status"] = "closed"
    assert run() == {}
    assert run() == {}
    assert len(client.feed_calls) == 2
    assert len(read_complete(tmp_path / LEDGER)) == 2


@pytest.mark.parametrize("k", [1, 2, 3, 4])
def test_restart_after_reservation_write(main_api, tmp_path, monkeypatch, k):
    client, run = main_api
    original = Q.append_record
    count = 0

    def interrupted(path, record):
        nonlocal count
        original(path, record)
        if record["type"] == "main_site_trial_selected":
            count += 1
            if count == k:
                raise RuntimeError("synthetic crash")

    monkeypatch.setattr(Q, "append_record", interrupted)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        run()
    monkeypatch.setattr(Q, "append_record", original)
    assert set(run()) == {100, 101, 102, 103}
    rows = read_complete(tmp_path / LEDGER)
    assert len(rows) == len({r["question_id"] for r in rows}) == 4
    assert len(read_complete(tmp_path / "questions.jsonl")) == 4
    assert len(client.feed_calls) == (1 if k == 4 else 2)


def test_unterminated_ledger_fails_closed(main_api, tmp_path):
    _, run = main_api
    (tmp_path / LEDGER).write_text('{"type":"main_site_trial_selected"')
    with pytest.raises(ValueError, match="unterminated"):
        run()
    assert not read_complete(tmp_path / "questions.jsonl")


def test_rollover_uses_window_ids_and_never_reselects(main_api, tmp_path):
    client, run = main_api
    cfg = {**campaign(2), "rollover": {"version": Q.ROLLOVER_VERSION, "windows": 2}}
    assert set(run(cfg)) == {100, 102}
    boundary = parse_iso(cfg["budget_window_end"])
    for item in client.pages[0]:
        item["question"]["scheduled_close_time"] = to_iso(boundary + timedelta(days=1))
    assert set(run(cfg, now=boundary)) == {101, 103}
    rows = read_complete(tmp_path / LEDGER)
    assert [r["campaign_id"] for r in rows] == [cfg["campaign_id"] + ":week:0"] * 2 + [
        cfg["campaign_id"] + ":week:1"] * 2


def test_pagination_bound_scans_all_p_pages_before_ranking(main_api):
    client, run = main_api
    client.pages = [[post(100, minutes=700)], [post(101, minutes=300)], [post(102, minutes=200)]]
    assert list(run(campaign(1, 2))) == [101]
    assert client.feed_calls == [0, 1]


def test_unknown_membership_candidate_is_skipped_not_selected(main_api, tmp_path):
    client, run = main_api
    client.pages[0][-1]["projects"] = {}
    assert set(run()) == {100, 101, 102, 103}
    assert 104 not in {r["question_id"] for r in read_complete(tmp_path / LEDGER)}
    polls = [r for r in read_complete(tmp_path / "poll_log.jsonl") if r["type"] == "main_site_trial_poll"]
    assert sum(polls[-1]["reasons"].values()) == 1


def test_new_selection_requires_competition_length_lead(main_api, tmp_path):
    client, run = main_api
    # 170-minute close leaves a 145-minute decision window (< 150): never newly selected.
    client.pages = [[post(100, minutes=170), post(101, minutes=180)]]
    assert list(run(campaign(2))) == [101]
    # An already selected question is not dropped merely because its deadline approaches.
    later = run(campaign(2), now=NOW + timedelta(minutes=60))
    assert 101 in later
    assert not [r for r in read_complete(tmp_path / LEDGER) if r["type"] == "main_site_trial_dropped"]


def test_live_main_site_remains_rejected(main_api, monkeypatch):
    client, run = main_api
    monkeypatch.setattr(Q, "load_submit_mode", lambda: {"mode": "live"})
    with pytest.raises(F.PreflightError, match="whitelisted"):
        run()
    assert not client.feed_calls and not client.detail_calls


def test_concurrent_selection_change_aborts_stale_scan(main_api, tmp_path, monkeypatch):
    client, run = main_api
    original = client.get

    def raced(path, params=None):
        if path == "posts/":
            append_record(tmp_path / LEDGER, {"type": "main_site_trial_selected",
                "campaign_id": campaign()["campaign_id"], "question_id": 999, "post_id": 1999,
                "question_type": "binary", "version": Q.MAIN_SITE_VERSION,
                "selected_at": to_iso(NOW), "decision_deadline": to_iso(NOW + timedelta(hours=1))})
        return original(path, params)

    monkeypatch.setattr(client, "get", raced)
    with pytest.raises(F.PreflightError, match="changed during refresh"):
        run()
    assert len(read_complete(tmp_path / LEDGER)) == 1
    assert not read_complete(tmp_path / "questions.jsonl")


def test_underfilled_window_can_add_later_without_replacing(main_api, tmp_path):
    client, run = main_api
    client.pages = [[post(100)]]
    first = run()
    client.pages = [[post(101), post(100), post(102, "multiple_choice"), post(103)]]
    second = run()
    assert set(second) == {100, 101, 102, 103}
    assert second[100]["snapshot_id"] == first[100]["snapshot_id"]
    assert [r["question_id"] for r in read_complete(tmp_path / LEDGER)] == [100, 102, 101, 103]


def test_fresh_revisions_keep_original_t0(main_api, tmp_path):
    client, run = main_api
    first = run()
    client.pages[0][0]["question"]["description"] = "Synthetic revised description"
    second = run(now=NOW + timedelta(minutes=10))
    assert second[100]["snapshot_id"] != first[100]["snapshot_id"]
    assert second[100]["revision_of"] == first[100]["snapshot_id"]
    assert second[100]["t0"] == first[100]["t0"]
    assert len(read_complete(tmp_path / LEDGER)) == 4


@pytest.mark.parametrize("fault", ["results", "next_type", "cycle"])
def test_main_poller_bad_pagination_never_selects(main_api, tmp_path, monkeypatch, fault):
    client, run = main_api
    original = client.get
    page = {"results": {} if fault == "results" else [post(100)],
            "next": 123 if fault == "next_type" else "https://www.metaculus.com/api/posts/?offset=1"}
    monkeypatch.setattr(client, "get", lambda path, params=None:
                        page if path == "posts/" else original(path, params))
    monkeypatch.setattr(client, "_get_url", lambda url: page)
    with pytest.raises(F.PreflightError, match="pagination"):
        run()
    assert not read_complete(tmp_path / LEDGER)
    assert not read_complete(tmp_path / "questions.jsonl")


def test_selected_post_later_in_bot_feed_does_not_flip_snapshot_mapping(main_api, tmp_path):
    client, run = main_api
    cfg = {**campaign(), "venues": ["main_site_questions", "bot_testing_area"]}
    client.posts[32977] = []
    run(cfg)
    shared = copy.deepcopy(client.pages[0][0])
    shared["projects"]["tournament"] = [{"id": 32977}]
    client.posts[32977] = [shared]
    second, third = run(cfg), run(cfg)
    assert second[100]["ground"] == third[100]["ground"] == "bot_testing_area"
    assert second[100]["snapshot_id"] == third[100]["snapshot_id"]
    rows = read_complete(tmp_path / "questions.jsonl")
    assert len(rows) == 5
    assert rows[-1]["question_id"] == 100 and rows[-1]["tournament_id"] == 32977


@pytest.mark.parametrize("url", ["https://evil.example/api/posts/", "http://www.metaculus.com/api/posts/",
    "https://www.metaculus.com/elsewhere/", "https://www.metaculus.com/api/%2e%2e/posts/",
    "https://www.metaculus.com/api/posts/#fragment"])
def test_bad_next_url_at_page_limit_is_rejected_without_fetch(main_api, tmp_path, monkeypatch, url):
    client, run = main_api
    original = client.get
    monkeypatch.setattr(client, "get", lambda path, params=None:
        {"results": [post(100)], "next": url} if path == "posts/" else original(path, params))
    monkeypatch.setattr(client, "_get_url", lambda url: pytest.fail("must not fetch beyond page limit"))
    with pytest.raises(F.PreflightError, match="pagination"):
        run(campaign(1, 1))
    assert not read_complete(tmp_path / LEDGER)
    assert not read_complete(tmp_path / "questions.jsonl")


@pytest.mark.parametrize("main_api", [False, True], indirect=True)
def test_main_feed_query_obeys_explicit_type_rollout(main_api):
    client, run = main_api
    assert run()
    assert client.feed_calls == [0]  # Fake API asserts the exact requested type set.
