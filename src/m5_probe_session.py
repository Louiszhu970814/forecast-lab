"""M5 synthetic CDF session. Requires independent review before operational use.

Holds cycle/scheduler/submit/player locks throughout temporary trial mode and
byte-exact live restoration. No models; API requests belong to v15_probe.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

import forecast as F
import submit as S
from flab.ids import new_id
from flab.jsonl import append_record
from flab.ledger import transaction
from flab.timeutil import to_iso, utc_now
from flab.workdir import PROJECT_ROOT
from v15_probe import BOT_ID, Probe

VERSION = 'm5_probe_session_v1'
AUTHORIZATION = 'PLAN D107 A1, D122, D123 M5-1'
LOCK_NAMES = ('cycle.lock', 'scheduler.lock', 'submit.lock',
              'forecast.claude.lock', 'forecast.gemini.lock', 'forecast.gpt.lock')
MAX_WAIT_SECONDS = 25 * 60
PROBE_GAP_SECONDS = 60


class RestoreError(RuntimeError):
    pass


class LockTimeout(F.PreflightError):
    pass


def atomic_bytes(path, content):
    """Replace on the same filesystem; preserve permissions and fsync both file and directory."""
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class Session:
    def __init__(self, project, *, transport_factory=S.cli_transport, probe_factory=Probe,
                 now=utc_now, monotonic=time.monotonic, sleep=time.sleep,
                 wait_seconds=MAX_WAIT_SECONDS):
        if not 0 <= wait_seconds <= MAX_WAIT_SECONDS:
            raise ValueError('lock wait must be between zero and 25 minutes')
        self.project = Path(project)
        self.root = self.project / 'data'
        self.mode_path = self.project / 'config' / 'submit_mode.json'
        self.stop_path = self.project / 'config' / 'STOP_SUBMIT'
        self.transport_factory, self.probe_factory = transport_factory, probe_factory
        self.now, self.monotonic, self.sleep = now, monotonic, sleep
        self.wait_seconds = wait_seconds
        self.session_id = new_id('m5session')

    @contextmanager
    def _locks(self):
        deadline = self.monotonic() + self.wait_seconds
        while True:
            stack = ExitStack()
            try:
                for name in LOCK_NAMES:
                    stack.enter_context(F.Lock(self.root / name))
            except F.PreflightError:
                stack.close()
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    raise LockTimeout('session lock wait expired') from None
                self.sleep(min(1., remaining))
            except BaseException:
                stack.close()
                raise
            else:
                break
        with stack:
            yield

    def _event(self, event_type, **extra):
        with transaction(self.root):
            append_record(self.root / 'mode_changes.jsonl', dict(
                type=event_type, version=VERSION, session_id=self.session_id,
                at=to_iso(self.now()), authorization=AUTHORIZATION, **extra))

    def _digest(self):
        return hashlib.sha256(self.mode_path.read_bytes()).hexdigest()

    def _restore(self, original, digest):
        try:
            atomic_bytes(self.mode_path, original)
            if self._digest() != digest:
                raise RestoreError('restored mode hash mismatch')
            self._event('mode_changed', previous_mode='trial', mode='live',
                        mode_sha256=digest, restored=True)
        except BaseException as exc:
            # Keep locks held while establishing the stop and recording failure.
            # Do not include exception messages or the live authorization body.
            try:
                atomic_bytes(self.stop_path, b'M5 mode restoration failed; review required.\n')
            finally:
                self._event('mode_restore_failed', error_type=type(exc).__name__,
                            expected_sha256=digest, stop_submit=self.stop_path.exists())
            raise RestoreError('live restoration failed; STOP_SUBMIT established') from exc

    def run(self):
        results = [dict(question_type=kind, status='rejected', reason='not_started',
                        forecast_requests=0, comment_requests=0)
                   for kind in ('numeric', 'discrete')]
        try:
            with self._locks():
                original = self.mode_path.read_bytes()
                mode = json.loads(original)
                if not isinstance(mode, dict) or mode.get('mode') != 'live' \
                        or type(mode.get('approved_bot_id')) is not int \
                        or mode['approved_bot_id'] != BOT_ID or self.stop_path.exists():
                    raise S.PrecheckError('session requires approved live bot without STOP_SUBMIT')
                digest = hashlib.sha256(original).hexdigest()
                trial = json.dumps({'mode': 'trial', 'approved_bot_id': BOT_ID}).encode()
                # The try begins BEFORE the first replacement: even a post-replace
                # fsync/audit failure must pass through restoration.
                try:
                    atomic_bytes(self.mode_path, trial)
                    self._event('mode_changed', previous_mode='live', mode='trial',
                                original_sha256=digest, mode_sha256=hashlib.sha256(trial).hexdigest())
                    probe = self.probe_factory(self.project, self.transport_factory(self.project),
                                               now=self.now, sleep=self.sleep)
                    for index, kind in enumerate(('numeric', 'discrete')):
                        prepared = probe.prepare(question_type=kind)
                        if prepared.get('status') == 'no_candidate':
                            result = dict(question_type=kind, status='no_candidate',
                                          forecast_requests=0, comment_requests=0)
                        else:
                            # Already hold every lock; do not recursively acquire
                            # Probe.run's non-reentrant scheduler/submit locks.
                            pid = prepared['probe_id']
                            try:
                                result = probe._run_numeric(probe.plan(pid))
                            except BaseException:
                                # Preserve conservative request counts even when
                                # the probe raises after journaling a write.
                                result = dict(question_type=kind, probe_id=pid, status='unknown',
                                              forecast_requests=None, comment_requests=None)
                                results[index] = result
                                rows = [r for r in probe.rows() if r.get('probe_id') == pid
                                        and r.get('state') == 'request_started']
                                result.update(forecast_requests=sum(r['operation'] == 'forecast' for r in rows),
                                              comment_requests=sum(r['operation'] == 'comment' for r in rows))
                                if not rows:
                                    result['status'] = 'rejected'
                                raise
                        results[index] = result
                        if result['status'] not in ('found', 'no_candidate'):
                            break
                        if index == 0 and result['status'] == 'found':
                            # Space the second probe's writes (an early probe saw HTTP 429 on
                            # a closely following comment); locks stay held throughout.
                            self.sleep(PROBE_GAP_SECONDS)
                finally:
                    self._restore(original, digest)
        except LockTimeout:
            return dict(status='busy', results=results, writes=0)
        except Exception as exc:
            return dict(status='blocked', results=results, error_type=type(exc).__name__)
        return dict(status='complete' if all(
            r['status'] in ('found', 'no_candidate') for r in results) else 'stopped', results=results)


def main(argv=None):
    import argparse
    import signal
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    def interrupted(signum, frame):
        raise SystemExit(128 + signum)
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = Session(PROJECT_ROOT).run()
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
