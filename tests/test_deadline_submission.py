from datetime import timedelta

import pytest

from flab.jsonl import append_record
from flab.timeutil import to_iso
from test_forecast import NOW
from test_scheduler import environment


def test_wait_timeout_uses_earliest_unsealed_current_snapshot(environment):
    obj, snapshots, _, _ = environment(continuous=True)
    snapshots[100]["scheduled_close_time"] = to_iso(NOW + timedelta(seconds=1510))
    snapshots[101]["scheduled_close_time"] = to_iso(NOW + timedelta(seconds=1540))
    assert obj._wait_timeout(snapshots) == 10
    append_record(obj.root / "aggregates.jsonl", {
        "question_id": 100, "question_snapshot_id": snapshots[100]["snapshot_id"],
        "scheduler_campaign_id": obj.campaign["campaign_id"], "completion_status": "complete_local"})
    assert obj._wait_timeout(snapshots) == 40
    snapshots[100]["snapshot_id"] = "revised"
    assert obj._wait_timeout(snapshots) == 10


@pytest.mark.parametrize("continuous, expected", [(False, None), (True, 60)])
def test_wait_timeout_is_bounded_only_in_continuous_mode(environment, continuous, expected):
    obj, snapshots, _, _ = environment(continuous=continuous)
    assert obj._wait_timeout(snapshots) == expected


def test_expired_without_answers_is_sealed_without_hook(environment):
    calls = []
    obj, snapshots, _, _ = environment(continuous=True, on_finalized=calls.append)
    for snap in snapshots.values():
        snap["scheduled_close_time"] = to_iso(NOW + timedelta(seconds=1500))
    assert obj.finalize(snapshots) == []
    assert calls == [] and obj._wait_timeout(snapshots) == 60


@pytest.mark.parametrize("continuous", [False, True])
def test_ready_hook_is_continuous_only_and_outside_ledger_transaction(environment, continuous):
    import threading
    from flab.ledger import transaction
    from test_aggregate import answer

    calls, checks, workers = [], [], []
    def on_finalized(ids):
        calls.append(ids)
        done = threading.Event()
        def write():
            with transaction(obj.root):
                append_record(obj.root / "hook_probe.jsonl", {"at": to_iso(NOW)})
            done.set()
        worker = threading.Thread(target=write)
        workers.append(worker)
        worker.start()
        checks.append(done.wait(1))

    obj, snapshots, _, _ = environment(count=1, continuous=continuous, on_finalized=on_finalized)
    snapshots[100]["scheduled_close_time"] = to_iso(NOW + timedelta(seconds=1500))
    append_record(obj.root / "forecasts.jsonl", answer("gpt", snapshot=snapshots[100], aid="synthetic", reasoning_summary="Synthetic evidence."))
    assert obj.finalize(snapshots) == [100]
    for worker in workers:
        worker.join(2)
        assert not worker.is_alive()
    assert calls == ([[100]] if continuous else [])
    assert checks == ([True] if continuous else [])
    assert obj.finalize(snapshots) == []  # No duplicate notification for an existing final/comment.
