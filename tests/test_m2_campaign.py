import copy

import pytest

import forecast as F
import m2_campaign as M
from flab.jsonl import read_records

CFG = {"tournaments": {"main": {"id": 11}, "minibench": {"id": 12}, "bot_testing_area": {"id": 13}}}
CAMP = {"campaign_id": "safe", "questions": [{"question_id": 71, "post_id": 81, "ground": "bot_testing_area"}]}
POST = {"id": 81, "title": "Synthetic trial question", "projects": {"tournament": [{"id": 13}]},
        "question": {"id": 71, "type": "binary", "status": "open", "scheduled_close_time": "2026-12-01T00:00:00Z"}}


class Client:
    def __init__(self):
        self.post, self.competition, self.calls = copy.deepcopy(POST), [], []
    def list_posts(self, project, **kwargs):
        self.calls.append((project, kwargs))
        return iter(self.competition)
    def get(self, path):
        assert path == "projects/minibenches/"
        return [{"id": 12, "slug": "minibench", "is_ongoing": True},
                {"id": 14, "slug": "minibench-previous", "is_ongoing": True}]
    def get_post(self, pid):
        assert pid == 81
        return self.post


def test_live_index_is_metadata_only_and_closed_or_synced_questions_disappear(tmp_path):
    client = Client()
    provider = M.SafeSnapshots(client, tmp_path, CAMP, tournaments=CFG)
    assert 71 in provider()
    assert {call[0] for call in client.calls} == {11, 12, 14}
    assert all(call[1]["statuses"] == ["open", "upcoming"] for call in client.calls)
    client.competition = [{**POST, "id": 90, "question": {**POST["question"], "id": 91}}]
    assert provider(force=True) == {}  # Suspected copy by title even with different IDs.
    saved = read_records(tmp_path / "questions.jsonl")
    assert len(saved) == 1 and saved[0]["question_id"] == 71
    assert "title" not in read_records(tmp_path / "poll_log.jsonl")[-1]["timing"][0]
    client.competition = []
    client.post["question"]["status"] = "closed"
    assert provider(force=True) == {}


@pytest.mark.parametrize("error", ["unknown_mini", "wrong_post", "wrong_qid", "wrong_ground", "same_project"])
def test_unknown_or_unproven_ground_cannot_reach_forecaster(tmp_path, error):
    client, cfg, camp = Client(), copy.deepcopy(CFG), copy.deepcopy(CAMP)
    if error == "unknown_mini": cfg["tournaments"]["minibench"]["id"] = None
    if error == "wrong_post": client.post["id"] = 90
    if error == "wrong_qid": client.post["question"]["id"] = 99
    if error == "wrong_ground": client.post["projects"] = {}
    if error == "same_project": client.post["projects"]["tournament"].append({"id": 11})
    provider = M.SafeSnapshots(client, tmp_path, camp, tournaments=cfg)
    if error == "same_project":
        assert provider() == {}
    else:
        with pytest.raises(F.PreflightError): provider()


def test_revision_keeps_history_and_caches_only_30_seconds(tmp_path):
    clock, client = [0], Client()
    provider = M.SafeSnapshots(client, tmp_path, CAMP, tournaments=CFG, monotonic=lambda: clock[0])
    before = provider()[71]
    client.post["title"] = "New synthetic wording"
    assert provider()[71] == before
    clock[0] = 31
    after = provider()[71]
    assert after["snapshot_id"] != before["snapshot_id"] and after["revision_of"] == before["snapshot_id"]
    assert len(read_records(tmp_path / "questions.jsonl")) == 2


def test_same_question_new_project_mapping_is_a_new_snapshot(tmp_path):
    from poll_questions import record_snapshots, snapshot_from_post, latest_snapshots
    snap = {**snapshot_from_post(POST, "first"), "tournament_id": 50}
    record_snapshots([snap], log=tmp_path / "questions.jsonl")
    first = latest_snapshots(tmp_path / "questions.jsonl")[71]
    record_snapshots([{**snap, "tournament_id": 51}], log=tmp_path / "questions.jsonl")
    second = latest_snapshots(tmp_path / "questions.jsonl")[71]
    assert second["snapshot_id"] != first["snapshot_id"] and second["revision"]["mapping_changed"]
    assert second["tournament_id"] == 51


def test_official_venue_discovery_does_not_change_submission_config():
    class Metadata:
        def get(self, path):
            return {"projects/tournaments/minibench/": {"id": 15, "slug": "minibench"},
                    "projects/site_main/": {"id": 144, "type": "site_main"},
                    "projects/tournaments/": [{"id": 77, "name": "Metaculus Cup Test", "slug": "test-cup",
                        "start_date": "2026-10-01T00:00:00Z", "is_ongoing": True}]}[path]
    cfg = copy.deepcopy(CFG)
    discovered = M.discover_tournaments(Metadata(), configured=cfg)
    assert cfg == CFG
    assert discovered["tournaments"]["minibench"]["id"] == 15
    assert discovered["tournaments"]["metaculus_cup"] == {"id": 77, "slug": "test-cup", "submit_whitelisted": False}
