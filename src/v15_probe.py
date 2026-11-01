"""One synthetic binary forecast + private comment on bot testing area only.

Separate component evidence, never a real forecasting result or an M3 end-to-end pass.
A persisted unknown request is never repeated. One explicit recovery can retry a
confirmed-rejected comment with its frozen payload; forecasts are never retried.
"""
from __future__ import annotations

import argparse
import copy
import json
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path

import forecast as F
import submit as S
from flab.config import load_run_config, load_submit_mode, load_tournaments
from flab.ids import hash_obj, new_id, sha256_hex
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso, utc_now
from flab.workdir import PROJECT_ROOT
from poll_questions import snapshot_from_post
from project_policy import trial_membership_error
from submit_contract import ConfirmedFailure, own_forecast_records
from submit_transport import APITransport

VERSION = 'v15_probe_v2'
BOT_ID, GROUND_ID = 311763, 32977


def _proven_rejection(row):
    status = row.get('http_status')
    return row.get('error_type') == 'ConfirmedFailure' and (
        (row.get('version') == 'v15_probe_v1' and status is None)
        or (type(status) is int and 400 <= status < 500))


class Probe:
    def __init__(self, project, transport, *, now=utc_now):
        self.project = Path(project)
        self.root = self.project / 'data'
        self.log = self.root / 'v15_probes.jsonl'
        self.transport, self.now = transport, now
        self.sub = S.Submitter(self.root, config_dir=self.project / 'config', transport=transport,
            allow_resubmit_by_venue={GROUND_ID: True}, now=now)

    def rows(self):
        with transaction(self.root):
            return read_complete(self.log)

    def event(self, probe_id, state, **extra):
        with transaction(self.root):
            append_record(self.log, dict(type='v15_probe', version=VERSION, probe_id=probe_id,
                state=state, at=to_iso(self.now()), **extra))

    def local_gate(self):
        mode = load_submit_mode(self.project / 'config')
        cfg = load_tournaments(self.project / 'config')
        if mode.get('mode') != 'trial' or mode.get('approved_bot_id') != BOT_ID:
            raise S.PrecheckError('probe requires approved trial bot')
        if (self.project / 'config' / 'STOP_SUBMIT').exists():
            raise S.PrecheckError('STOP_SUBMIT')
        if cfg['tournaments']['bot_testing_area']['id'] != GROUND_ID or 'bot_testing_area' not in cfg['trial_ground_types']:
            raise S.PrecheckError('bot testing area is not approved')

    def prepare(self):
        """Read-only, including in local mode. Deterministic selection, no output inspection."""
        if self.transport.account()['id'] != BOT_ID:
            raise S.PrecheckError('wrong probe account')
        reserved = {(bot, qid) for bot, qid, _ in S.probe_reservations(self.root)}
        candidates = []
        for listed in self.transport.client.list_posts(GROUND_ID, forecast_type='binary'):
            if trial_membership_error(listed, GROUND_ID):
                continue
            listed_snap = snapshot_from_post(listed, 'bot-testing-area')
            if listed_snap is None or any(type(listed_snap.get(k)) is not int or listed_snap[k] <= 0
                    for k in ('post_id', 'question_id')) or listed_snap.get('question_type') != 'binary':
                continue
            # Listing responses may omit authenticated own-history and full content.
            post = self.transport.get_post(listed_snap['post_id'])
            snap = snapshot_from_post(post, 'bot-testing-area')
            if snap is None or any(snap.get(k) != listed_snap[k] for k in ('post_id', 'question_id')):
                raise S.PrecheckError('probe detail identity differs from listing')
            if trial_membership_error(post, GROUND_ID):
                continue
            if snap is None or any(type(snap.get(k)) is not int or snap[k] <= 0 for k in ('post_id', 'question_id')) or snap.get('question_type') != 'binary' or snap.get('status') != 'open' \
                    or GROUND_ID not in snap.get('project_ids', []) or own_forecast_records(post) \
                    or (BOT_ID, snap['question_id']) in reserved:
                continue
            if not snap.get('open_time') or parse_iso(snap['open_time']) > self.now() \
                    or not snap.get('scheduled_close_time') \
                    or parse_iso(snap['scheduled_close_time']) <= self.now() + timedelta(hours=1):
                continue
            candidates.append(snap)
        if not candidates:
            raise S.PrecheckError('no eligible unused binary bot-testing question')
        snap = min(candidates, key=lambda s: (parse_iso(s['scheduled_close_time']), s['question_id']))
        probe_id = new_id('v15')
        text = f'Forecast Lab API component check {probe_id}. Synthetic 50% test; not a research forecast.'
        # No question prose or model output enters this component-test ledger.
        metadata = {k: snap.get(k) for k in ('post_id', 'question_id', 'question_type', 'options',
            'scheduled_close_time', 'open_time', 'content_hash', 'venue')}
        metadata['tournament_id'] = GROUND_ID
        intent = dict(intent_id=probe_id, mode='trial', bot_id=BOT_ID, tournament_id=GROUND_ID,
            post_id=snap['post_id'], question_id=snap['question_id'], snapshot=metadata, allow_resubmit=False,
            forecast_payload=[{'question': snap['question_id'], 'probability_yes': .5}],
            comment_payload={'on_post': snap['post_id'], 'text': text, 'is_private': True, 'included_forecast': True},
            comment_hash=sha256_hex(text))
        self.event(probe_id, 'prepared', plan=intent, plan_sha256=hash_obj(intent))
        return {'probe_id': probe_id, 'post_id': snap['post_id'], 'question_id': snap['question_id'], 'writes': 0}

    def plan(self, probe_id):
        rows = [r for r in self.rows() if r['probe_id'] == probe_id and r['state'] == 'prepared']
        if len(rows) != 1 or rows[0]['plan_sha256'] != hash_obj(rows[0]['plan']):
            raise S.PrecheckError('probe plan missing or changed')
        p = rows[0]['plan']
        if any(type(p.get(k)) is not int or p[k] <= 0 for k in ('post_id', 'question_id')) \
                or p['mode'] != 'trial' or p['bot_id'] != BOT_ID or p['tournament_id'] != GROUND_ID \
                or p['snapshot']['question_type'] != 'binary' \
                or any(type(p['snapshot'].get(k)) is not int or p['snapshot'][k] != p[k]
                    for k in ('post_id', 'question_id', 'tournament_id')) \
                or p['forecast_payload'] != [{'question': p['question_id'], 'probability_yes': .5}] \
                or p['comment_payload']['on_post'] != p['post_id'] \
                or p['comment_payload'].get('is_private') is not True \
                or p['comment_payload'].get('included_forecast') is not True \
                or p['comment_hash'] != sha256_hex(p['comment_payload']['text']):
            raise S.PrecheckError('invalid synthetic probe plan')
        return copy.deepcopy(p)

    def _check(self, intent, operation, rows):
        self.local_gate()
        checked = copy.deepcopy(intent)
        checked['allow_resubmit'] = operation == 'comment'  # Permit the one already-confirmed forecast, not a second write.
        if self.sub._precheck(checked):
            raise S.PrecheckError('probe question changed')
        prior = self.sub._checked_forecast_baseline
        if not isinstance(prior, list):
            raise S.PrecheckError('probe baseline unavailable')
        if operation == 'comment':
            found = next(r for r in rows if r.get('operation') == 'forecast' and r['state'] == 'found')
            if prior != [found['receipt']['id']]:
                raise S.PrecheckError('unexpected forecast appeared before probe comment')
        return prior

    def _write_gate(self, intent):
        self.local_gate()
        self.sub._gate(intent, self.sub._checked_discovery)
        deadline = S.submit_deadline(intent['snapshot'], load_run_config(self.project / 'config')['deadlines'])
        if deadline is None or self.now() >= deadline:
            raise S.PermanentPrecheckError('probe submission deadline elapsed')

    def run(self, probe_id, *, retry_confirmed_comment=False):
        if type(retry_confirmed_comment) is not bool:
            raise S.PrecheckError('invalid comment recovery flag')
        with ExitStack() as stack:
            for name in ('scheduler.lock', 'submit.lock', 'forecast.claude.lock', 'forecast.gemini.lock', 'forecast.gpt.lock'):
                stack.enter_context(F.Lock(self.root / name))
            intent = self.plan(probe_id)
            for operation in ('forecast', 'comment'):
                rows = [r for r in self.rows() if r['probe_id'] == probe_id]
                own = [r for r in rows if r.get('operation') == operation]
                if any(r['state'] == 'found' for r in own):
                    continue
                starts = [r for r in own if r['state'] == 'request_started']
                if len(starts) > (2 if operation == 'comment' else 1):
                    raise S.PrecheckError('probe request limit exceeded')
                request_ids = {r['request_id'] for r in starts}
                if len(request_ids) != len(starts) or any(r['state'] in ('confirmed_failed', 'unknown', 'write_returned', 'write_unconfirmed')
                        and r.get('request_id') not in request_ids for r in own):
                    raise S.PrecheckError('probe operation record without unique request')
                if len(starts) == 2:
                    first = starts[0]['request_id']
                    if not any(r['state'] == 'confirmed_failed' and r.get('request_id') == first and _proven_rejection(r) for r in own) \
                            or any(r['state'] in ('unknown', 'write_returned', 'write_unconfirmed') and r.get('request_id') == first for r in own) \
                            or not any(r['state'] == 'comment_recovery_started' and r.get('rejected_request_id') == first
                                and r.get('max_total_comment_requests') == 2 and r.get('authorization') == 'PLAN D84/D88' for r in own):
                        raise S.PrecheckError('invalid comment recovery history')
                rejected = next((r for r in reversed(own) if starts and r['state'] == 'confirmed_failed'
                    and r.get('request_id') == starts[-1]['request_id']), None)
                recovery_of = None
                if rejected:
                    if not (operation == 'comment' and retry_confirmed_comment and len(starts) == 1 and _proven_rejection(rejected)):
                        return {'probe_id': probe_id, 'status': 'confirmed_failed', 'operation': operation}
                    if any(r['state'] in ('unknown', 'write_returned', 'write_unconfirmed') and r.get('request_id') == starts[0]['request_id'] for r in own):
                        raise S.PrecheckError('ambiguous comment rejection cannot be retried')
                    recovery_of = starts[0]['request_id']
                    starts = []  # Explicit, bounded new request after authoritative rejection only.
                if not starts:
                    prior = self._check(intent, operation, rows)
                    self._write_gate(intent)
                    if recovery_of:
                        self.event(probe_id, 'comment_recovery_started', operation=operation,
                            rejected_request_id=recovery_of, authorization='PLAN D84/D88', max_total_comment_requests=2)
                    request_id = new_id('v15request')
                    started = self.now().isoformat(timespec='microseconds').replace('+00:00', 'Z')
                    request = dict(request_id=request_id, request_started_at=started, request_forecast_baseline=prior)
                    self.event(probe_id, 'request_started', operation=operation, **request)
                    # Persist first; a crash or STOP between here and write remains unknown, never retried.
                    try:
                        self._write_gate(intent)
                        receipt = self.transport.write(S.FORECAST_PATH if operation == 'forecast' else S.COMMENT_PATH,
                            intent[f'{operation}_payload'], request_id=request_id)
                        self.event(probe_id, 'write_returned', operation=operation, request_id=request_id,
                            receipt_present=bool(receipt))
                    except ConfirmedFailure as exc:
                        self.event(probe_id, 'confirmed_failed', operation=operation, request_id=request_id,
                            error_type=type(exc).__name__,
                            http_status=getattr(exc, 'status', None) if type(getattr(exc, 'status', None)) is int else None)
                        return {'probe_id': probe_id, 'status': 'confirmed_failed', 'operation': operation}
                    except Exception as exc:
                        self.event(probe_id, 'write_unconfirmed', operation=operation, request_id=request_id,
                            error_type=type(exc).__name__)
                else:
                    request = {k: starts[-1][k] for k in ('request_id', 'request_started_at', 'request_forecast_baseline')}
                observed = self.transport.readback(operation, {**intent, **request}, request_id=request['request_id'])
                if observed.outcome != 'found' or observed.request_id != request['request_id'] \
                        or not self.sub._receipt_matches(operation, intent, observed.record):
                    self.event(probe_id, 'unknown', operation=operation, request_id=request['request_id'])
                    return {'probe_id': probe_id, 'status': 'unknown', 'operation': operation}
                self.event(probe_id, 'found', operation=operation, request_id=request['request_id'], receipt=observed.record)
            comment = next(r for r in self.rows() if r['probe_id'] == probe_id
                and r.get('operation') == 'comment' and r['state'] == 'found')['receipt']
            record = self.transport.client.get(f"comments/{comment['id']}/")
            author = record.get('author')
            author = author.get('id') if isinstance(author, dict) else author
            verified = (type(record.get('id')) is int and record['id'] == comment['id']
                and type(author) is int and author == BOT_ID
                and type(record.get('on_post')) is int and record['on_post'] == intent['post_id']
                and record.get('is_private') is True and isinstance(record.get('included_forecast'), dict)
                and record['included_forecast'].get('question_type') == 'binary'
                and type(record['included_forecast'].get('probability_yes')) in (int, float)
                and record['included_forecast']['probability_yes'] == .5 and isinstance(record.get('text'), str)
                and sha256_hex(record['text']) == intent['comment_hash'])
            self.event(probe_id, 'comment_properties_checked', verified=verified)
            return {'probe_id': probe_id, 'status': 'complete' if verified else 'comment_properties_unknown',
                'scope': 'synthetic binary API component only'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run'))
    parser.add_argument('--probe-id')
    parser.add_argument('--retry-confirmed-comment', action='store_true',
        help='One explicit retry after confirmed comment rejection; never retries forecasts or unknown outcomes')
    args = parser.parse_args(argv)
    if args.command == 'run' and not args.probe_id:
        parser.error('--probe-id required')
    from flab.envfile import get_secret
    probe = Probe(PROJECT_ROOT, APITransport(get_secret('METACULUS_TOKEN', PROJECT_ROOT / '.env')))
    try:
        result = probe.prepare() if args.command == 'prepare' else probe.run(args.probe_id, retry_confirmed_comment=args.retry_confirmed_comment)
    except Exception as exc:
        result = {'status': 'blocked', 'error_type': type(exc).__name__,
            'http_status': getattr(exc, 'status', None) if type(getattr(exc, 'status', None)) is int else None}
    print(json.dumps(result))
    return 0 if result.get('status') in (None, 'complete') else 1


if __name__ == '__main__':
    raise SystemExit(main())
