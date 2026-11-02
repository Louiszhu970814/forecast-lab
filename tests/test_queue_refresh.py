from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading

import pytest

import continuous as Q
from test_forecast import NOW


def queue(tmp_path, poller, clock):
    obj = Q.Queue(tmp_path, {}, poller, now=lambda: clock[0])
    obj.update = lambda current: current
    return obj


@pytest.mark.parametrize('force', [False, True])
def test_waiting_readers_share_completed_refresh(tmp_path, force):
    clock = [NOW]
    polling, waiting = threading.Event(), threading.Event()
    mutex = threading.RLock()
    main_thread = threading.get_ident()
    calls = []

    class ObservedLock:
        def __enter__(self):
            if threading.get_ident() == main_thread:
                waiting.set()
            mutex.acquire()

        def __exit__(self, *_):
            mutex.release()

    def poll():
        calls.append('poll')
        if len(calls) == 1:
            polling.set()
            assert waiting.wait(2)
        clock[0] += timedelta(seconds=53)
        return {7: {'snapshot_id': f's{len(calls)}', 'ground': 'bot_testing_area', 'priority': 'main'}}

    obj = queue(tmp_path, poll, clock)
    obj.lock = ObservedLock()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(obj, force=True)
        assert polling.wait(2)
        second = obj(force=force)
        assert first.result(timeout=2) == second
        assert second[7]['snapshot_id'] == 's1'
    # Two serialized 53-second refreshes age a valid quota snapshot beyond 90s.
    assert (clock[0] - NOW).total_seconds() == 53
    assert calls == ['poll']
    second[7]['snapshot_id'] = 'caller mutation'
    assert obj()[7]['snapshot_id'] == 's1'
    # A later independent forced refresh still makes a new network read.
    assert obj(force=True)[7]['snapshot_id'] == 's2'
    assert calls == ['poll', 'poll']


@pytest.mark.parametrize('failure_phase', ['poll', 'update'])
def test_failed_forced_refresh_never_returns_previous_cache(tmp_path, failure_phase):
    clock, calls = [NOW], []

    def poll():
        calls.append('poll')
        if len(calls) == 2 and failure_phase == 'poll':
            raise RuntimeError('synthetic refresh failure')
        return {7: {'snapshot_id': f's{len(calls)}', 'ground': 'bot_testing_area', 'priority': 'main'}}

    obj = queue(tmp_path, poll, clock)
    def update(current):
        if len(calls) == 2 and failure_phase == 'update':
            raise RuntimeError('synthetic update failure')
        return current
    obj.update = update
    assert obj()[7]['snapshot_id'] == 's1'
    with pytest.raises(RuntimeError):
        obj(force=True)
    assert obj()[7]['snapshot_id'] == 's3'
    assert len(calls) == 3


@pytest.mark.parametrize('elapsed', [30, -1])
def test_cache_requires_nonnegative_age_below_30_seconds(tmp_path, elapsed):
    clock, calls = [NOW], []
    def poll():
        calls.append('poll')
        return {7: {'snapshot_id': f's{len(calls)}', 'ground': 'bot_testing_area', 'priority': 'main'}}
    obj = queue(tmp_path, poll, clock)
    obj()
    clock[0] += timedelta(seconds=elapsed)
    assert obj()[7]['snapshot_id'] == 's2'
