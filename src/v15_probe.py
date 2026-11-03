"""Bounded synthetic API checks on bot testing area only.

Separate component evidence, never a real forecasting result or an M3 end-to-end pass.
Single preserves the v1-v3 contract. Reuse scenarios are one-shot; unknown requests
are never repeated. comment_retry uses the unchanged Submitter retry state machine.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import time
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path

import forecast as F
import numeric as N
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
from submit_transport import APITransport, valid_current_options

VERSION = 'v15_probe_v5'
REUSE_VERSIONS = ('v15_probe_v4', VERSION)
BOT_ID, GROUND_ID = 311763, 32977
SCENARIOS = ('single', 'comment_length', 'comment_retry', 'duplicate_forecast')
LENGTH_STEPS = (2000, 10000, 50000, 150000)
NUMERIC_US = (.20, .25, .35, .50, .65, .75, .80)
PREFERRED = {'numeric': (46451, 43325), 'discrete': (45708, 43322)}


def _numeric_payload(snapshot):
    try:
        scale = N.scaling(snapshot)
        values = dict(zip(N.KEYS, (scale.from_u(u) for u in NUMERIC_US)))
        cdf = N.validate_cdf(N.to_cdf(values, snapshot), snapshot)
        return [{'question': snapshot['question_id'], 'continuous_cdf': cdf}]
    except ValueError as exc:
        raise S.PrecheckError('invalid synthetic CDF') from exc


def _scaling_hash(snapshot):
    return hash_obj({k: snapshot[k] for k in N.SCALING_FIELDS if k in snapshot})


class _NumericSubmitter(S.Submitter):
    """Frozen synthetic input, with the normal Submitter write/gate implementation.

    The probe owns the one-request bound and mandatory readback. Never call
    _resume: its retry policy is for research submissions, not this probe.
    """
    def __init__(self, probe):
        super().__init__(probe.root, config_dir=probe.project / 'config',
            transport=probe.transport, allow_resubmit_by_venue={GROUND_ID: True},
            now=probe.now, sleep=probe.sleep, probe_question_types=N.TYPES)
        self.probe = probe

    def _validate_frozen(self, intent):
        if intent != self.probe.plan(intent['intent_id']):
            raise S.PermanentPrecheckError('synthetic input differs from frozen plan')

    def _precheck(self, intent):
        changed = super()._precheck(intent)
        if changed:
            raise S.PermanentPrecheckError('probe question changed')
        return changed

    def _event(self, intent, status, **extra):
        # Reserve in the probe journal FIRST. A crash between journals cannot
        # release a slot or permit another probe to select the same question.
        if status == 'request_started':
            self.probe.event(intent['intent_id'], status, operation=extra['operation'],
                request_id=extra['request_id'],
                request_started_at=extra['request_started_at_precise'],
                request_forecast_baseline=extra['forecast_readback_baseline'])
        return super()._event(intent, status, synthetic=True, probe_version=VERSION, **extra)


class _NumericTransport:
    """Observe sanitized failure metadata without changing transport semantics."""
    def __init__(self, transport):
        self.transport = transport
        self.http_status = None

    def __getattr__(self, name):
        return getattr(self.transport, name)

    def write(self, path, payload, *, request_id):
        self.http_status = None
        try:
            return self.transport.write(path, payload, request_id=request_id)
        except Exception as exc:
            status = getattr(exc, 'status', None)
            self.http_status = status if type(status) is int else None
            raise


def _second_payload(snapshot):
    if snapshot['question_type'] == 'binary':
        return [{'question': snapshot['question_id'], 'probability_yes': .6}]
    options = snapshot['options']
    snap = {**snapshot, 'snapshot_id': 'synthetic-probe'}
    aggregate = {k: snap.get(k) for k in ('question_id', 'post_id', 'tournament_id', 'question_type', 'options')}
    # Move a fixed fraction of one uniform share from the last to the first option.
    values = dict.fromkeys(options, 1 / len(options))
    values[options[0]] *= 1.2
    values[options[-1]] *= .8
    aggregate.update(question_snapshot_id=snap['snapshot_id'], option_probabilities=values)
    return S.forecast_payload(snap, aggregate)


def _length_text(probe_id, length):
    prefix = f'Forecast Lab {probe_id} length={length}. Synthetic length test; not a research forecast. '
    if not prefix.isascii() or len(prefix) > length:
        raise S.PrecheckError('invalid length probe identity')
    return prefix + 'x' * (length - len(prefix))


def _scenario_fields(scenario, snapshot):
    if scenario == 'comment_retry':
        return dict(max_forecast_requests=1, max_comment_requests=3)
    if scenario == 'comment_length':
        return dict(max_forecast_requests=0, max_comment_requests=4,
            comment_lengths=list(LENGTH_STEPS))
    if scenario == 'duplicate_forecast':
        return dict(max_forecast_requests=2, max_comment_requests=0,
            second_forecast_payload=_second_payload(snapshot))
    raise S.PrecheckError('invalid probe scenario')


def _retry_inputs(plan, now):
    """Synthetic audit graph accepted by the unmodified Submitter input validator."""
    pid = plan['intent_id']
    snap = dict(plan['snapshot'], type='question_snapshot', snapshot_id=pid + '_snapshot', synthetic=True)
    binary = snap['question_type'] == 'binary'
    attempt = dict(type='forecast_attempt', attempt_id=pid + '_synthetic', synthetic=True,
        player='gpt', tier=1, validation_status='valid', question_snapshot_id=snap['snapshot_id'],
        **{k: snap.get(k) for k in ('question_id', 'post_id', 'tournament_id', 'question_type', 'options')},
        probability=.5 if binary else None,
        option_probabilities=None if binary else dict.fromkeys(snap['options'], 1 / len(snap['options'])),
        reasoning_summary=f'{pid}: Synthetic retry test; not a research forecast')
    aggregate = S.A.build_aggregate(snap, [attempt], stage='final', now=now)
    aggregate.update(record_id=pid + '_aggregate', synthetic=True,
        submission_eligible=True, scheduler_campaign_id=pid)
    comment = S.C.build(snap, aggregate, [attempt, aggregate], [], now=now)
    comment.update(record_id=pid + '_comment', synthetic=True)
    return snap, attempt, aggregate, comment


class _RetrySubmitter(S.Submitter):
    """Only adapt dedupe and journal destinations; retain the entire state machine."""
    def __init__(self, probe, root, transport):
        super().__init__(root, config_dir=probe.project / 'config', transport=transport,
            allow_resubmit_by_venue={GROUND_ID: True}, now=lambda: probe.now(), sleep=probe.sleep)
        self.probe = probe

    def _duplicate(self, intent):
        super()._duplicate(intent)
        _ReuseChecks(self.probe)._duplicate(intent)

    def _event(self, intent, status, **extra):
        rid = extra.get('request_id')
        fault = self.transport.faults.get(rid, {})
        row = super()._event(intent, status, synthetic=True, **fault, **extra)
        # Reserve at the original journal boundary, before any transport call.
        # A crash between journals leaves an unresolved request, never a free slot.
        if status == 'request_started':
            self.probe.event(intent['intent_id'], status, synthetic=True,
                operation=extra['operation'], request_id=rid,
                request_started_at=row['request_started_at_precise'],
                request_forecast_baseline=row['forecast_readback_baseline'])
        elif status in ('forecast_ok', 'comment_ok'):
            self.probe.event(intent['intent_id'], 'found', synthetic=True,
                operation=extra['operation'], request_id=rid, receipt=extra['response'])
        elif status == 'unknown':
            self.probe.event(intent['intent_id'], 'unknown', synthetic=True,
                operation=extra['operation'], request_id=rid)
        return row


class _RetryTransport:
    """One local rejection, then ordinary writes with mandatory independent readback.

    Never retries, sleeps or changes a Submitter status. Only a verified readback
    can become the receipt returned to Submitter._write.
    """
    def __init__(self, probe, plan):
        self.probe, self.plan = probe, plan
        self.faults = {}
        self.submitter = None

    def __getattr__(self, name):
        return getattr(self.probe.transport, name)

    def write(self, path, payload, *, request_id):
        operation = {S.FORECAST_PATH: 'forecast', S.COMMENT_PATH: 'comment'}.get(path)
        if operation is None or payload != self.plan[f'{operation}_payload']:
            raise S.PrecheckError('unexpected retry probe write')
        rows = self.submitter._history(self.plan)
        starts = [r for r in rows if r['status'] == 'request_started' and r.get('operation') == operation]
        if not starts or starts[-1]['request_id'] != request_id \
                or len(starts) > self.plan[f'max_{operation}_requests']:
            raise S.PrecheckError('retry probe request limit exceeded')
        pid = self.plan['intent_id']
        if operation == 'comment' and len(starts) == 1:
            # No delegated method (including GET/account/precheck/readback) here.
            self.faults[request_id] = dict(injected_fault=True, network_request_sent=False,
                rejection_evidence='local_injected_before_network')
            self.probe.event(pid, 'confirmed_failed', operation=operation, request_id=request_id,
                synthetic=True, error_type='ConfirmedFailure', **self.faults[request_id])
            raise ConfirmedFailure('synthetic local rejection before network I/O')
        self.probe._write_gate(self.plan)
        try:
            receipt = self.probe.transport.write(path, payload, request_id=request_id)
            self.probe.event(pid, 'write_returned', operation=operation, request_id=request_id,
                synthetic=True, receipt_present=bool(receipt))
        except ConfirmedFailure as exc:
            status = getattr(exc, 'status', None)
            self.probe.event(pid, 'confirmed_failed', operation=operation, request_id=request_id,
                synthetic=True, error_type='ConfirmedFailure',
                http_status=status if type(status) is int else None)
            raise
        except Exception as exc:
            self.probe.event(pid, 'write_unconfirmed', operation=operation, request_id=request_id,
                synthetic=True, error_type=type(exc).__name__)
        started = starts[-1]
        lookup = {**self.plan, 'request_started_at': started['request_started_at_precise'],
            'request_forecast_baseline': started['forecast_readback_baseline']}
        try:
            observed = self.probe.transport.readback(operation, lookup, request_id=request_id)
            if observed.outcome == 'found' and observed.request_id == request_id \
                    and self.submitter._receipt_matches(operation, self.plan, observed.record):
                return observed.record
        except Exception:
            pass
        return {}  # _write records unknown and stops; no second write or recovery.


class _ReuseChecks(S.Submitter):
    """Use the unchanged formal-intent dedupe with separately checked probe reservations.

    The isolated root contains no submission inputs or copied production history.
    _rows reads the original ledger; this instance is only used for prechecks, never
    Submitter.run/_write. Normal Submitter instances still see every reservation.
    """
    def __init__(self, probe):
        super().__init__(probe.root / 'v15_probe_checks', config_dir=probe.project / 'config',
            transport=probe.transport, allow_resubmit_by_venue={GROUND_ID: True}, now=probe.now)
        self.probe = probe

    def _rows(self):
        return self.probe.sub._rows()

    def _duplicate(self, intent):
        if intent['question_id'] in self.probe._unresolved_questions(exclude=intent['intent_id']):
            raise S.PrecheckError('unresolved prior probe request')
        super()._duplicate(intent)


def _uniform_payload(snapshot):
    options = snapshot.get('options')
    if not valid_current_options(options) or len(options) < 2:
        raise S.PrecheckError('invalid synthetic probe options')
    # In-memory inputs only: use the submission contract without persisting model
    # or aggregate records for a synthetic component check.
    snap = {**snapshot, 'snapshot_id': 'synthetic-probe'}
    aggregate = {k: snap.get(k) for k in ('question_id', 'post_id', 'tournament_id', 'question_type', 'options')}
    aggregate.update(question_snapshot_id=snap['snapshot_id'],
        option_probabilities=dict.fromkeys(options, 1 / len(options)))
    try:
        return S.forecast_payload(snap, aggregate)
    except ValueError as exc:
        raise S.PrecheckError('invalid synthetic uniform payload') from exc


def _multiple_choice_included_forecast(included, intent):
    """Exact multiset comparison allows deleted historical options (None)."""
    if not isinstance(included, dict) or included.get('question_type') != 'multiple_choice' \
            or included.get('options') != intent['snapshot']['options']:
        return False, False
    values = included.get('probability_yes_per_category')
    if not isinstance(values, list) or any(value is not None and (
            type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value))
            for value in values):
        return False, False
    active = [value for value in values if value is not None]
    probabilities = intent['forecast_payload'][0]['probability_yes_per_category']
    wanted = [probabilities[option] for option in intent['snapshot']['options']]
    return sorted(active) == sorted(wanted), active == wanted


def _proven_rejection(row):
    status = row.get('http_status')
    return row.get('error_type') == 'ConfirmedFailure' and (
        (row.get('version') == 'v15_probe_v1' and status is None)
        or (type(status) is int and 400 <= status < 500))


def _resolved_rejection(row):
    return _proven_rejection(row) or (
        row.get('version') in REUSE_VERSIONS and row.get('operation') == 'comment'
        and row.get('error_type') == 'ConfirmedFailure'
        and row.get('injected_fault') is True and row.get('network_request_sent') is False
        and row.get('rejection_evidence') == 'local_injected_before_network')


class Probe:
    def __init__(self, project, transport, *, now=utc_now, sleep=time.sleep):
        self.project = Path(project)
        self.root = self.project / 'data'
        self.log = self.root / 'v15_probes.jsonl'
        self.transport, self.now = transport, now
        self.sleep = sleep
        self.sub = S.Submitter(self.root, config_dir=self.project / 'config', transport=transport,
            allow_resubmit_by_venue={GROUND_ID: True}, now=now)

    def _comment_record(self, comment_id, post_id):
        """Own comment via the documented list endpoint (author=<own id> is required for bots).

        The detail endpoint is not in the public gateway documentation and may be refused.
        Anything but exactly one row with this id yields {} and therefore an unverified result.
        """
        # Private comments are listed only with is_private=true (D102 finding).
        page = self.transport.client.get('comments/', {'post': post_id, 'author': BOT_ID, 'is_private': 'true', 'limit': 100})
        rows = page.get('results') if isinstance(page, dict) else None
        matches = [r for r in rows if isinstance(r, dict) and type(r.get('id')) is int and r['id'] == comment_id] \
            if isinstance(rows, list) else []
        return matches[0] if len(matches) == 1 else {}

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

    def _unresolved_questions(self, *, exclude=None):
        """Malformed/orphan histories fail closed; ambiguous outcomes remain reserved."""
        S.probe_reservations(self.root)
        rows = self.rows()
        plans = {r['probe_id']: r['plan'] for r in rows if r.get('state') == 'prepared'}
        unresolved = set()
        for pid in {r.get('probe_id') for r in rows}:
            if pid == exclude:
                continue
            if pid not in plans:
                if all(r.get('state') == 'no_candidate' and r.get('version') == VERSION
                       for r in rows if r.get('probe_id') == pid):
                    continue
                raise S.PrecheckError('probe event without plan')
            own = [r for r in rows if r.get('probe_id') == pid]
            starts = [r for r in own if r.get('state') == 'request_started']
            ids = [r.get('request_id') for r in starts]
            if any(not isinstance(rid, str) or not rid for rid in ids) or len(set(ids)) != len(ids):
                raise S.PrecheckError('invalid prior probe requests')
            outcomes = [r for r in own if r.get('state') in (
                'found', 'confirmed_failed', 'unknown', 'write_returned', 'write_unconfirmed')]
            if any(r.get('request_id') not in ids or not any(
                    s['request_id'] == r['request_id'] and s.get('operation') == r.get('operation')
                    for s in starts) for r in outcomes):
                raise S.PrecheckError('orphan prior probe outcome')
            for start in starts:
                # Append order matters: a later unknown reopens uncertainty, while
                # a later found/proven rejection resolves that exact request only.
                resolved = False
                for row in own[own.index(start) + 1:]:
                    if row.get('request_id') != start['request_id']:
                        continue
                    if row['state'] == 'found' or (
                            row['state'] == 'confirmed_failed' and _resolved_rejection(row)):
                        resolved = True
                    elif row['state'] in ('unknown', 'write_unconfirmed', 'confirmed_failed'):
                        resolved = False
                if not resolved:
                    unresolved.add(plans[pid]['question_id'])
        return unresolved

    def prepare(self, question_type='binary', scenario='single'):
        """Read-only, including in local mode. Deterministic selection, no output inspection."""
        if scenario not in SCENARIOS:
            raise S.PrecheckError('unsupported probe scenario')
        numeric = question_type in N.TYPES
        if question_type not in ('binary', 'multiple_choice', *N.TYPES) or (numeric and scenario != 'single'):
            raise S.PrecheckError('unsupported probe question type')
        if self.transport.account()['id'] != BOT_ID:
            raise S.PrecheckError('wrong probe account')
        reserved = {(bot, qid) for bot, qid, _ in S.probe_reservations(self.root)}
        if numeric:
            reserved.update((BOT_ID, r['plan']['question_id']) for r in self.rows()
                            if r.get('state') == 'prepared')
        unresolved = self._unresolved_questions() if scenario != 'single' else set()
        candidates = []
        for listed in self.transport.client.list_posts(GROUND_ID, forecast_type=question_type):
            if trial_membership_error(listed, GROUND_ID):
                continue
            listed_snap = snapshot_from_post(listed, 'bot-testing-area')
            if listed_snap is None or any(type(listed_snap.get(k)) is not int or listed_snap[k] <= 0
                    for k in ('post_id', 'question_id')) or listed_snap.get('question_type') != question_type:
                continue
            # Listing responses may omit authenticated own-history and full content.
            post = self.transport.get_post(listed_snap['post_id'])
            snap = snapshot_from_post(post, 'bot-testing-area')
            if snap is None or any(snap.get(k) != listed_snap[k] for k in ('post_id', 'question_id')):
                raise S.PrecheckError('probe detail identity differs from listing')
            if trial_membership_error(post, GROUND_ID):
                continue
            if snap is None or any(type(snap.get(k)) is not int or snap[k] <= 0 for k in ('post_id', 'question_id')) or snap.get('question_type') != question_type or snap.get('status') != 'open' \
                    or GROUND_ID not in snap.get('project_ids', []):
                continue
            try:
                previous = own_forecast_records(post)
            except ValueError:
                if numeric:
                    continue
                raise
            if scenario == 'single' and (previous or (BOT_ID, snap['question_id']) in reserved):
                continue
            if snap['question_id'] in unresolved:
                continue
            # The unchanged normal Submitter reserves only forecast attempts.
            # Until comment-only reservations are specified, length probes require
            # an existing probe reservation as well as a platform forecast.
            if scenario == 'comment_length' and (not previous or (BOT_ID, snap['question_id']) not in reserved):
                continue
            if not snap.get('open_time') or parse_iso(snap['open_time']) > self.now() \
                    or not snap.get('scheduled_close_time') \
                    or parse_iso(snap['scheduled_close_time']) <= self.now() + timedelta(hours=1):
                continue
            if question_type == 'multiple_choice' and (
                    not valid_current_options(snap.get('options')) or len(snap['options']) < 2
                    or not self.transport.can_reconcile_forecast(post)):
                continue
            if numeric:
                try:
                    N.scaling(snap)
                    _numeric_payload(snap)
                except ValueError:
                    continue
                if not self.transport.can_reconcile_forecast(post):
                    continue
            candidates.append(snap)
        if not candidates:
            if numeric:
                self.event(new_id('v15'), 'no_candidate', question_type=question_type)
                return {'status': 'no_candidate', 'question_type': question_type, 'writes': 0}
            raise S.PrecheckError(f'no eligible unused {question_type} bot-testing question')
        def priority(s):
            preferred = PREFERRED.get(question_type, ())
            return (preferred.index(s['question_id']) if s['question_id'] in preferred else len(preferred),
                    parse_iso(s['scheduled_close_time']), s['question_id'])
        snap = min(candidates, key=priority)
        probe_id = new_id('v15')
        distribution = 'CDF' if numeric else ('50%' if question_type == 'binary' else 'uniform')
        text = f'Forecast Lab API component check {probe_id}. Synthetic {distribution} test; not a research forecast.'
        # No question prose or model output enters this component-test ledger.
        metadata = {k: snap.get(k) for k in ('post_id', 'question_id', 'question_type', 'options',
            'scheduled_close_time', 'open_time', 'content_hash', 'venue')}
        metadata['tournament_id'] = GROUND_ID
        if numeric:
            metadata.update({k: copy.deepcopy(snap[k]) for k in N.SCALING_FIELDS if k in snap})
        intent = dict(intent_id=probe_id, mode='trial', bot_id=BOT_ID, tournament_id=GROUND_ID,
            post_id=snap['post_id'], question_id=snap['question_id'], snapshot=metadata, allow_resubmit=False,
            forecast_payload=([{'question': snap['question_id'], 'probability_yes': .5}]
                if question_type == 'binary' else (_numeric_payload(metadata) if numeric else _uniform_payload(metadata))),
            max_comment_requests=1,
            comment_payload={'on_post': snap['post_id'], 'text': text, 'is_private': True, 'included_forecast': True},
            comment_hash=sha256_hex(text))
        if numeric:
            intent.update(probe_version=VERSION, scaling_sha256=_scaling_hash(metadata))
        if scenario != 'single':
            intent.update(scenario=scenario, reuse_authorization='PLAN D107 A2',
                **_scenario_fields(scenario, metadata))
        if scenario == 'comment_retry':
            comment = _retry_inputs(intent, self.now())[-1]
            intent['comment_payload']['text'] = comment['text']
            intent['comment_hash'] = comment['text_sha256']
        self.event(probe_id, 'prepared', scenario=scenario, plan=intent, plan_sha256=hash_obj(intent))
        return {'probe_id': probe_id, 'post_id': snap['post_id'], 'question_id': snap['question_id'], 'writes': 0}

    def plan(self, probe_id):
        rows = [r for r in self.rows() if r['probe_id'] == probe_id and r['state'] == 'prepared']
        if len(rows) != 1 or rows[0]['plan_sha256'] != hash_obj(rows[0]['plan']):
            raise S.PrecheckError('probe plan missing or changed')
        p = rows[0]['plan']
        scenario = p.get('scenario', 'single')
        if rows[0].get('version') in REUSE_VERSIONS and rows[0].get('scenario') != scenario:
            raise S.PrecheckError('probe scenario changed')
        if scenario != 'single':
            if rows[0].get('version') not in REUSE_VERSIONS or p.get('reuse_authorization') != 'PLAN D107 A2' \
                    or p.get('intent_id') != probe_id or p.get('allow_resubmit') is not False:
                raise S.PrecheckError('invalid probe reuse authorization')
            fields = _scenario_fields(scenario, p['snapshot'])
            if any(type(p.get(k)) is not type(value) or p[k] != value for k, value in fields.items()):
                raise S.PrecheckError('invalid probe scenario bounds or payload')
            if any(k in p and k not in fields for k in ('comment_lengths', 'second_forecast_payload')):
                raise S.PrecheckError('unexpected probe scenario fields')
        elif any(k in p for k in ('reuse_authorization', 'max_forecast_requests', 'comment_lengths',
                'second_forecast_payload')):
            raise S.PrecheckError('unexpected reuse fields in single plan')
        question_type = p['snapshot']['question_type']
        if question_type == 'binary':
            expected = [{'question': p['question_id'], 'probability_yes': .5}]
        elif question_type == 'multiple_choice':
            expected = _uniform_payload(p['snapshot'])
        elif question_type in N.TYPES:
            if scenario != 'single' or rows[0].get('version') != VERSION \
                    or p.get('probe_version') != VERSION or p.get('intent_id') != probe_id \
                    or p.get('allow_resubmit') is not False \
                    or p.get('scaling_sha256') != _scaling_hash(p['snapshot']) \
                    or p['comment_payload']['text'] != (
                        f'Forecast Lab API component check {probe_id}. Synthetic CDF test; not a research forecast.'):
                raise S.PrecheckError('invalid synthetic CDF plan')
            expected = _numeric_payload(p['snapshot'])
            if hash_obj(p['forecast_payload']) != hash_obj(expected):
                raise S.PrecheckError('synthetic CDF payload differs from recomputation')
        else:
            raise S.PrecheckError('invalid synthetic probe question type')
        # Only binary plans prepared by v1/v2 may omit the bound and keep the D84/D88 recovery contract.
        legacy = question_type == 'binary' and rows[0].get('version') in ('v15_probe_v1', 'v15_probe_v2')
        if any(type(p.get(k)) is not int or p[k] <= 0 for k in ('post_id', 'question_id')) \
                or p['mode'] != 'trial' or p['bot_id'] != BOT_ID or p['tournament_id'] != GROUND_ID \
                or (scenario == 'single' and ('max_comment_requests' in p or not legacy) and (
                    type(p.get('max_comment_requests')) is not int or p['max_comment_requests'] != 1)) \
                or any(type(p['snapshot'].get(k)) is not int or p['snapshot'][k] != p[k]
                    for k in ('post_id', 'question_id', 'tournament_id')) \
                or p['forecast_payload'] != expected \
                or p['comment_payload']['on_post'] != p['post_id'] \
                or p['comment_payload'].get('is_private') is not True \
                or p['comment_payload'].get('included_forecast') is not True \
                or p['comment_hash'] != sha256_hex(p['comment_payload']['text']):
            raise S.PrecheckError('invalid synthetic probe plan')
        if scenario == 'comment_retry':
            if not probe_id or Path(probe_id).name != probe_id or probe_id in ('.', '..'):
                raise S.PrecheckError('invalid retry probe directory identity')
            if S.COMMENT_ATTEMPTS != 3:
                raise S.PrecheckError('Submitter comment attempt contract changed')
            comment = _retry_inputs(p, self.now())[-1]
            if p['comment_payload']['text'] != comment['text']:
                raise S.PrecheckError('retry comment differs from synthetic inputs')
        return copy.deepcopy(p)

    def _reuse_check(self, intent):
        self.local_gate()
        checks = _ReuseChecks(self)
        if checks._precheck({**intent, 'allow_resubmit': True}):
            raise S.PrecheckError('probe question changed')
        prior = checks._checked_forecast_baseline
        if not isinstance(prior, list):
            raise S.PrecheckError('probe baseline unavailable')
        if parse_iso(intent['snapshot']['scheduled_close_time']) <= self.now() + timedelta(hours=1):
            raise S.PrecheckError('probe requires more than one hour before close')
        if intent['scenario'] == 'comment_length':
            reserved = S.probe_reservations(self.root)
            if not prior or not any((bot, qid) == (BOT_ID, intent['question_id']) for bot, qid, _ in reserved):
                raise S.PrecheckError('length probe requires forecast and prior probe reservation')
        self.sub._checked_discovery = checks._checked_discovery
        return prior

    def _length_observation(self, intent, request, receipt):
        """Additional diagnostics only; never promotes a transport unknown to found."""
        remote_id = receipt.get('id') if isinstance(receipt, dict) else None
        try:
            if type(remote_id) is int:
                record = self._comment_record(remote_id, intent['post_id'])
            else:
                # An empty POST receipt cannot identify a truncated comment.
                # A unique request-specific synthetic prefix provides diagnostics
                # on the first page only; missing/ambiguous evidence stays unknown.
                prefix = intent['comment_payload']['text'].split(' Synthetic ', 1)[0] + ' Synthetic '
                page = self.transport.client.get('comments/', {
                    'post': intent['post_id'], 'author': BOT_ID, 'is_private': 'true', 'limit': 100})
                rows = page.get('results') if isinstance(page, dict) else None
                matches = [r for r in rows if isinstance(r, dict) and isinstance(r.get('text'), str)
                    and r['text'].startswith(prefix)] if isinstance(rows, list) else []
                record = matches[0] if len(matches) == 1 else {}
        except Exception:
            return None
        author = record.get('author')
        author = author.get('id') if isinstance(author, dict) else author
        text = record.get('text')
        try:
            recent = parse_iso(record['created_at']) > parse_iso(request['request_started_at'])
        except (KeyError, TypeError, ValueError):
            recent = False
        if type(record.get('id')) is not int or record['id'] <= 0 \
                or type(author) is not int or author != BOT_ID or type(record.get('on_post')) is not int \
                or record['on_post'] != intent['post_id'] or record.get('is_private') is not True \
                or not recent or not isinstance(text, str):
            return None
        return {'length': len(text), 'sha256': sha256_hex(text)}

    def _run_reuse(self, intent):
        pid, scenario = intent['intent_id'], intent['scenario']
        own = [r for r in self.rows() if r['probe_id'] == pid]
        finished = [r for r in own if r['state'] == 'run_finished']
        if finished:
            return copy.deepcopy(finished[-1]['result'])
        if any(r['state'] != 'prepared' for r in own):
            raise S.PrecheckError('reuse probe already started; no recovery permitted')
        prior = self._reuse_check(intent)
        self.event(pid, 'run_started', scenario=scenario, synthetic=True)
        if scenario == 'comment_retry':
            return self._run_comment_retry(intent)
        result = dict(probe_id=pid, status='complete', scenario=scenario)
        if scenario == 'comment_length':
            result.update(max_accepted_length=0, first_rejected_length=None, truncation_detected=False)
            steps = LENGTH_STEPS
        else:
            result.update(history_counts=[len(prior)], platform_behavior='unknown')
            steps = (intent['forecast_payload'], intent['second_forecast_payload'])
        for index, step in enumerate(steps):
            operation = 'comment' if scenario == 'comment_length' else 'forecast'
            current = copy.deepcopy(intent)
            if operation == 'comment':
                text = _length_text(pid, step)
                current['comment_payload']['text'] = text
                current['comment_hash'] = sha256_hex(text)
            else:
                current['forecast_payload'] = step
            baseline = self._reuse_check(current)
            if baseline != prior:
                raise S.PrecheckError('unexpected forecast appeared during reuse probe')
            self._write_gate(current)
            request = dict(request_id=new_id('v15request'),
                request_started_at=self.now().isoformat(timespec='microseconds').replace('+00:00', 'Z'),
                request_forecast_baseline=baseline)
            rid = request['request_id']
            self.event(pid, 'request_started', operation=operation, step=index, synthetic=True, **request)
            receipt = {}
            try:
                self._write_gate(current)
                receipt = self.transport.write(S.COMMENT_PATH if operation == 'comment' else S.FORECAST_PATH,
                    current[f'{operation}_payload'], request_id=rid)
                self.event(pid, 'write_returned', operation=operation, request_id=rid, receipt_present=bool(receipt))
            except ConfirmedFailure as exc:
                status = getattr(exc, 'status', None)
                status = status if type(status) is int else None
                self.event(pid, 'confirmed_failed', operation=operation, request_id=rid,
                    error_type=type(exc).__name__, http_status=status)
                result.update(status='confirmed_failed', http_status=status)
                if operation == 'comment':
                    result['first_rejected_length'] = step
                else:
                    result['platform_behavior'] = 'rejected' if index == 1 else 'first_request_rejected'
                break
            except Exception as exc:
                self.event(pid, 'write_unconfirmed', operation=operation, request_id=rid, error_type=type(exc).__name__)
            try:
                observed = self.transport.readback(operation, {**current, **request}, request_id=rid)
            except Exception:
                observed = S.Readback(request_id=rid)
            found = (observed.outcome == 'found' and observed.request_id == rid
                and self.sub._receipt_matches(operation, current, observed.record))
            if operation == 'comment':
                evidence = self._length_observation(current, request, observed.record if found else receipt)
                exact = evidence == {'length': step, 'sha256': current['comment_hash']}
                truncated = evidence is not None and evidence['length'] < step and not exact
                self.event(pid, 'comment_length_checked', request_id=rid, requested_length=step,
                    observed=evidence, verified=found and exact, truncation_detected=truncated)
                if not found or not exact:
                    result.update(status='truncated' if truncated else 'unknown', truncation_detected=truncated)
                    self.event(pid, 'unknown', operation=operation, request_id=rid)
                    break
                result['max_accepted_length'] = step
            elif found:
                try:
                    after = self.transport.forecast_readback_baseline(self.transport.get_post(intent['post_id']))
                except Exception:
                    after = None
                if not isinstance(after, list) or observed.record['id'] in baseline or observed.record['id'] not in after:
                    found = False
                else:
                    prior = after
                    result['history_counts'].append(len(after))
                    if index == 1:
                        result['platform_behavior'] = ('accepted_with_history'
                            if set(baseline) < set(after) else 'accepted_without_full_history')
            if not found:
                self.event(pid, 'unknown', operation=operation, request_id=rid)
                result['status'] = 'unknown'
                break
            self.event(pid, 'found', operation=operation, request_id=rid, receipt=observed.record)
        self.event(pid, 'run_finished', result=result)
        return result

    def _run_comment_retry(self, plan):
        pid = plan['intent_id']
        root = self.root / 'v15_probe_submitter' / pid
        # A pre-existing isolated ledger is not a recovery invitation.
        root.mkdir(parents=True, exist_ok=False)
        snap, attempt, aggregate, comment = _retry_inputs(plan, self.now())
        for name, record in (('questions', snap), ('forecasts', attempt),
                ('aggregates', aggregate), ('comments', comment)):
            append_record(root / f'{name}.jsonl', record)
        transport = _RetryTransport(self, plan)
        sub = _RetrySubmitter(self, root, transport)
        transport.submitter = sub
        intent = sub._candidate(aggregate, comment, {snap['snapshot_id']: snap})
        intent.update(intent_id=pid, synthetic=True)
        if intent['forecast_payload'] != plan['forecast_payload'] \
                or intent['comment_payload'] != plan['comment_payload']:
            raise S.PrecheckError('synthetic Submitter inputs differ from frozen plan')
        sub._event(intent, 'intent')
        # Exactly one invocation: forecast confirmed failure cannot trigger another
        # forecast. All comment retries/backoff/terminal states belong to _resume.
        sub._resume(intent)
        rows = sub._history(intent)
        status = rows[-1]['status']
        result = dict(probe_id=pid, scenario='comment_retry',
            status='complete' if status == 'comment_ok' else status)
        self.event(pid, 'run_finished', result=result, synthetic=True)
        return result

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

    def _numeric_diagnostics(self, intent, request_id):
        """Shapes only, never remote values, text or exception messages."""
        shapes = []
        error_type = None
        try:
            post = self.transport.get_post(intent['post_id'])
            for row in own_forecast_records(post):
                shapes.append({
                    'keys': sorted(row),
                    'fields': {key: {'type': type(value).__name__,
                                    'length': len(value) if isinstance(value, (list, dict, str)) else None}
                               for key, value in row.items() if key in (
                                   'forecast_values', 'continuous_cdf', 'centers',
                                   'interval_lower_bounds', 'interval_upper_bounds',
                                   'probability_yes_per_category')},
                })
        except Exception as exc:
            error_type = type(exc).__name__
        self.event(intent['intent_id'], 'readback_diagnostics', operation='forecast',
            request_id=request_id, diagnostics_version='numeric_shapes_v1',
            rows=shapes, error_type=error_type)

    def _run_numeric(self, intent):
        """Caller holds all probe locks. One _write per operation, no _resume."""
        pid = intent['intent_id']
        # Revalidate even when called by the session under its existing locks.
        if self.plan(pid) != intent:
            raise S.PrecheckError('numeric plan changed')
        sub = _NumericSubmitter(self)
        transport = _NumericTransport(self.transport)
        sub.transport = transport
        history = [r for r in self.rows() if r['probe_id'] == pid]
        starts = [r for r in history if r['state'] == 'request_started']
        ids = [r.get('request_id') for r in starts]
        if any(not isinstance(rid, str) or not rid for rid in ids) or len(ids) != len(set(ids)) \
                or any(r.get('operation') not in ('forecast', 'comment') for r in starts) \
                or any(sum(r.get('operation') == op for r in starts) > 1 for op in ('forecast', 'comment')) \
                or any(r['state'] in ('found', 'unknown', 'confirmed_failed') and not any(
                    r.get('request_id') == s['request_id'] and r.get('operation') == s['operation']
                    for s in starts) for r in history):
            raise S.PrecheckError('invalid numeric probe request history')
        # An interrupted/unknown request can only be reconciled. Even a later
        # found result does not authorize a new comment on a resumed invocation.
        readonly = any(r['state'] == 'unknown' for r in history) or any(
            not any(r['state'] in ('found', 'confirmed_failed') and r.get('request_id') == s['request_id']
                    for r in history) for s in starts)

        def result(status, **extra):
            requests = [r for r in self.rows() if r['probe_id'] == pid and r['state'] == 'request_started']
            return dict(probe_id=pid, question_type=intent['snapshot']['question_type'], status=status,
                forecast_requests=sum(r['operation'] == 'forecast' for r in requests),
                comment_requests=sum(r['operation'] == 'comment' for r in requests), **extra)

        for operation in ('forecast', 'comment'):
            own = [r for r in self.rows() if r['probe_id'] == pid and r.get('operation') == operation]
            if any(r['state'] == 'confirmed_failed' for r in own):
                return result('rejected', operation=operation)
            if any(r['state'] == 'found' for r in own):
                continue
            request = next((r for r in own if r['state'] == 'request_started'), None)
            if request is None:
                if readonly:
                    return result('unknown', operation=operation, reconcile_only=True)
                self.local_gate()
                checked = dict(intent, allow_resubmit=operation == 'comment')
                sub._precheck(checked)
                if operation == 'comment':
                    found = next(r for r in self.rows() if r['probe_id'] == pid
                                 and r.get('operation') == 'forecast' and r['state'] == 'found')
                    if sub._checked_forecast_baseline != [found['receipt']['id']]:
                        raise S.PrecheckError('unexpected forecast appeared before probe comment')
                # Uses the formal network-time guard, deadlines and write path.
                # No synthetic "intent" row: normal Submitter.run must never
                # adopt these bounded component requests for its retry policy.
                sub._write(intent, operation)
                request = next((r for r in self.rows() if r['probe_id'] == pid
                                and r.get('operation') == operation and r['state'] == 'request_started'), None)
                if request is None:
                    return result('rejected', operation=operation)
                last = sub._last_operation(intent, operation)
                if last and last['status'] == 'confirmed_failed':
                    self.event(pid, 'confirmed_failed', operation=operation, request_id=request['request_id'],
                        error_type='ConfirmedFailure', http_status=transport.http_status)
                    return result('rejected', operation=operation, http_status=transport.http_status)
                if transport.http_status is not None and 400 <= transport.http_status < 500:
                    sub._event(intent, 'unknown', operation=operation, request_id=request['request_id'],
                        reason='synthetic probe write returned HTTP 4xx without confirmed failure')
                    self.event(pid, 'unknown', operation=operation, request_id=request['request_id'],
                        http_status=transport.http_status)
                    return result('unknown', operation=operation, http_status=transport.http_status)
            rid = request['request_id']
            try:
                observed = self.transport.readback(operation, {**intent, **{
                    k: request[k] for k in ('request_started_at', 'request_forecast_baseline')}},
                    request_id=rid)
                found = observed.outcome == 'found' and observed.request_id == rid \
                    and sub._receipt_matches(operation, intent, observed.record)
                if found and operation == 'comment':
                    record = self._comment_record(observed.record['id'], intent['post_id'])
                    author = record.get('author')
                    author = author.get('id') if isinstance(author, dict) else author
                    found = (type(author) is int and author == BOT_ID
                        and type(record.get('on_post')) is int and record['on_post'] == intent['post_id']
                        and record.get('is_private') is True and isinstance(record.get('text'), str)
                        and sha256_hex(record['text']) == intent['comment_hash'])
            except Exception:
                found = False
            if not found:
                sub._event(intent, 'unknown', operation=operation, request_id=rid,
                    reason='synthetic probe readback cannot establish this request')
                self.event(pid, 'unknown', operation=operation, request_id=rid)
                if operation == 'forecast':
                    self._numeric_diagnostics(intent, rid)
                return result('unknown', operation=operation)
            sub._event(intent, operation + '_ok', operation=operation, request_id=rid,
                response=observed.record, remote_id=observed.record['id'], reconciled=True)
            self.event(pid, 'found', operation=operation, request_id=rid, receipt=observed.record)
        return result('found')

    def run(self, probe_id, *, retry_confirmed_comment=False):
        if type(retry_confirmed_comment) is not bool:
            raise S.PrecheckError('invalid comment recovery flag')
        with ExitStack() as stack:
            for name in ('scheduler.lock', 'submit.lock', 'forecast.claude.lock', 'forecast.gemini.lock', 'forecast.gpt.lock'):
                stack.enter_context(F.Lock(self.root / name))
            intent = self.plan(probe_id)
            if intent.get('scenario', 'single') != 'single':
                if retry_confirmed_comment:
                    raise S.PrecheckError('reuse probe forbids comment recovery')
                return self._run_reuse(intent)
            if retry_confirmed_comment and intent.get('max_comment_requests') == 1:
                raise S.PrecheckError('bounded probe forbids comment recovery')
            if intent['snapshot']['question_type'] in N.TYPES:
                return self._run_numeric(intent)
            for operation in ('forecast', 'comment'):
                rows = [r for r in self.rows() if r['probe_id'] == probe_id]
                own = [r for r in rows if r.get('operation') == operation]
                if any(r['state'] == 'found' for r in own):
                    continue
                starts = [r for r in own if r['state'] == 'request_started']
                if len(starts) > (intent.get('max_comment_requests', 2) if operation == 'comment' else 1):
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
            record = self._comment_record(comment['id'], intent['post_id'])
            author = record.get('author')
            author = author.get('id') if isinstance(author, dict) else author
            question_type = intent['snapshot']['question_type']
            included = record.get('included_forecast')
            properties = {}
            if question_type == 'multiple_choice':
                included_verified, order_verified = _multiple_choice_included_forecast(included, intent)
                properties['included_forecast_order_verified'] = order_verified
            else:
                included_verified = (isinstance(included, dict)
                    and included.get('question_type') == 'binary'
                    and type(included.get('probability_yes')) in (int, float)
                    and included['probability_yes'] == .5)
            verified = (type(record.get('id')) is int and record['id'] == comment['id']
                and type(author) is int and author == BOT_ID
                and type(record.get('on_post')) is int and record['on_post'] == intent['post_id']
                and record.get('is_private') is True and included_verified and isinstance(record.get('text'), str)
                and sha256_hex(record['text']) == intent['comment_hash'])
            self.event(probe_id, 'comment_properties_checked', verified=verified, **properties)
            return {'probe_id': probe_id, 'status': 'complete' if verified else 'comment_properties_unknown',
                'scope': f'synthetic {question_type} API component only'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run'))
    parser.add_argument('--probe-id')
    parser.add_argument('--question-type', choices=('binary', 'multiple_choice', *N.TYPES), default='binary')
    parser.add_argument('--scenario', choices=SCENARIOS, default='single')
    parser.add_argument('--retry-confirmed-comment', action='store_true',
        help='One explicit retry after confirmed comment rejection; never retries forecasts or unknown outcomes')
    args = parser.parse_args(argv)
    if args.command == 'run' and not args.probe_id:
        parser.error('--probe-id required')
    from flab.envfile import get_secret
    probe = Probe(PROJECT_ROOT, APITransport(get_secret('METACULUS_TOKEN', PROJECT_ROOT / '.env')))
    try:
        result = probe.prepare(question_type=args.question_type, scenario=args.scenario) if args.command == 'prepare' else probe.run(args.probe_id, retry_confirmed_comment=args.retry_confirmed_comment)
    except Exception as exc:
        result = {'status': 'blocked', 'error_type': type(exc).__name__,
            'http_status': getattr(exc, 'status', None) if type(getattr(exc, 'status', None)) is int else None}
    print(json.dumps(result))
    return 0 if result.get('status') in (None, 'complete', 'found', 'no_candidate') else 1


if __name__ == '__main__':
    raise SystemExit(main())
