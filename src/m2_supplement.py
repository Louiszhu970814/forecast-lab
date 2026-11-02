"""One-shot Gemini tier-1 cost supplement. No submission, promotion or old-ledger edits."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import timedelta
import json
from pathlib import Path
import sys

from capacity_report import elapsed
import budget as B
import forecast as F
import m2_prepare
import quota
import scheduler as S
from cycle import supervise, CLEANUP_RESERVE_S
from continuous import current_tournaments
from flab.config import load_quota_policy, load_run_config, load_submit_mode, load_tournaments
from flab.envfile import get_secret
from flab.ids import hash_obj, new_id
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso, utc_now
from m2_campaign import SafeSnapshots
from metaculus import MetaculusClient
from project_policy import trial_membership_error

VERSION = 'm2_supplement_v1'
MAX_CALLS = 6
MAX_SECONDS = 3600


def parent_check(root, parent):
    records = read_complete(root / 'campaigns.jsonl')
    if not any(r.get('campaign') == parent and r.get('sha256') == hash_obj(parent) for r in records):
        raise ValueError('parent campaign is not frozen')
    if len(parent['questions']) < 20:
        raise ValueError('parent must be the existing M2 campaign')
    if parent['run_config_sha256'] != hash_obj(load_run_config()) or parent['quota_policy_sha256'] != hash_obj(load_quota_policy()):
        raise ValueError('parent configuration changed')
    if any(type(c) is not int or not 0 < c <= 130 for c in parent['weekly_attempt_caps'].values()):
        raise ValueError('parent weekly cap exceeds authorized ceiling')
    if not parse_iso(parent['budget_window_start']) <= utc_now() < parse_iso(parent['budget_window_end']):
        raise ValueError('original budget window is inactive')


def prepare(root, parent):
    if load_submit_mode()['mode'] != 'local':
        raise ValueError('supplement requires local mode')
    with F.Lock(root / 'scheduler.lock'):
        parent_check(root, parent)
        if any(r.get('type') == 'supplement_registered' and r['parent_id'] == parent['campaign_id']
               for r in read_complete(root / 'm2_supplements.jsonl')):
            raise ValueError('a supplement is already registered; do not reset its bound')
        path = m2_prepare.prepare(parent['weekly_attempt_caps']['gemini'], question_count=3, parent_campaign=parent)
        campaign = json.loads(path.read_text())
        if {q['question_id'] for q in parent['questions']} & {q['question_id'] for q in campaign['questions']}:
            raise ValueError('supplement overlaps parent')
        S.register(root, campaign)
        record = {'type': 'supplement_registered', 'version': VERSION, 'supplement_id': campaign['campaign_id'],
            'parent_id': parent['campaign_id'], 'campaign_sha256': hash_obj(campaign),
            'registered_at': to_iso(utc_now()), 'player': 'gemini', 'tier': 1,
            'max_questions': 3, 'max_calls': MAX_CALLS, 'max_seconds': MAX_SECONDS,
            'runtime_fingerprint': F.runtime_fingerprint('gemini'),
            'report_policy': 'separate_evidence_only_no_automatic_m2_promotion'}
        append_record(root / 'm2_supplements.jsonl', record)
        return path


def registration(root, campaign):
    rows = [r for r in read_complete(root / 'm2_supplements.jsonl')
            if r.get('supplement_id') == campaign['campaign_id']]
    frozen = [r for r in rows if r.get('type') == 'supplement_registered']
    if len(frozen) != 1 or frozen[0]['campaign_sha256'] != hash_obj(campaign):
        raise ValueError('supplement registration missing or drifted')
    rec = frozen[0]
    if rec['runtime_fingerprint'] != F.runtime_fingerprint('gemini'):
        raise ValueError('supplement runtime changed')
    if rec['version'] != VERSION or rec['max_calls'] != MAX_CALLS or rec['max_seconds'] != MAX_SECONDS:
        raise ValueError('supplement contract changed')
    parents = [r['campaign'] for r in read_complete(root / 'campaigns.jsonl')
               if r.get('campaign', {}).get('campaign_id') == rec['parent_id']]
    if not parents:
        raise ValueError('missing parent')
    parent_check(root, parents[0])
    if any(campaign[k] != parents[0][k] for k in ('budget_window_start', 'budget_window_end', 'weekly_attempt_caps',
                                                  'run_config_sha256', 'quota_policy_sha256')):
        raise ValueError('supplement cannot reset the original budget or configuration')
    if not 1 <= len(campaign['questions']) <= 3:
        raise ValueError('supplement exceeds question bound')
    return rec, rows


def own_attempts(root, campaign):
    ids = {q['question_id'] for q in campaign['questions']}
    return [r for r in read_complete(root / 'forecasts.jsonl') if r.get('question_id') in ids]


class BoundedBudget(B.Budget):
    def reserve(self, request, **kw):
        ids = {q['question_id'] for q in self.campaign['questions']}
        starts = read_complete(self.root / 'forecast_attempt_starts.jsonl')
        used = {r['attempt_id'] for r in starts if r.get('question_id') in ids}
        if request['player'] != 'gemini' or request['tier'] != 1 or request['question_id'] not in ids:
            raise F.QuestionDone('supplement scope violation')
        if len(used) >= MAX_CALLS:
            raise F.QuestionDone('supplement actual-call cap reached')
        counts, error = quota.detect_agy_activity()
        if counts or error:
            raise F.QuestionDone('supplement requires no concurrent agy activity')
        return super().reserve(request, **kw)


class SupplementScheduler(S.Scheduler):
    def jobs(self, snapshots, *, paused, active):
        rows = own_attempts(self.root, self.campaign)
        qs = read_complete(self.root / 'quota.jsonl')
        # A failed attribution stops the experiment, rather than buying more attempts.
        if any(r.get('model_check') != 'verified' or quota.attempt_drop(r, qs) is None for r in rows):
            return []
        return [j for j in super().jobs(snapshots, paused=paused | {'claude', 'gpt'}, active=active)
                if j.player == 'gemini' and j.tier == 1]

    def execute(self, job):
        start = utc_now()
        outcome = 'exception'
        try:
            result = super().execute(job)
            outcome = result.get('outcome')
            return result
        finally:
            append_record(self.root / 'worker_timings.jsonl', {'type': 'worker_timing', 'version': VERSION,
                'run_id': self.run_id, 'campaign_id': self.campaign['campaign_id'],
                'question_id': job.question_id, 'player': job.player, 'tier': job.tier,
                'started_at': to_iso(start), 'finished_at': to_iso(utc_now()), 'outcome': outcome,
                'scope': 'Full execute including preflight, before/after quota, CLI, repair/rerun, validation and audit; excludes queue/provider refresh.'})

    def finalize(self, snapshots, *, force=()):
        # These are cost samples, not a new three-model forecast campaign.
        return None


class StrictSnapshots(SafeSnapshots):
    def __call__(self, *, force=False):
        checked = self.checked
        result = super().__call__(force=force)
        if self.checked == checked:
            return result
        bot = self.tournaments['tournaments']['bot_testing_area']['id']
        for snap in result.values():
            if trial_membership_error(self.client.get_post(snap['post_id']), bot):
                raise F.PreflightError('trial ownership changed')
        return result


def worker(root, campaign):
    # All lines are reserved to exclude local development CLI work during measurement.
    # Gemini's own line lock is acquired by Scheduler.execute.
    with ExitStack() as stack:
        for name in ('scheduler', 'forecast.claude', 'forecast.gpt'):
            stack.enter_context(F.Lock(root / (name + '.lock')))
        rec, rows = registration(root, campaign)
        if load_submit_mode()['mode'] != 'local' or any(r['type'] == 'supplement_started' for r in rows):
            raise ValueError('supplement is local-only and one-shot; never restart an interrupted run')
        counts, error = quota.detect_agy_activity()
        if counts or error:
            raise ValueError('other agy activity prevents clean attribution')
        started = utc_now()
        deadline = started + timedelta(seconds=MAX_SECONDS)
        append_record(root / 'm2_supplements.jsonl', {'type': 'supplement_started', 'version': VERSION,
            'supplement_id': campaign['campaign_id'], 'at': to_iso(started), 'deadline': to_iso(deadline)})
        client = MetaculusClient(get_secret('METACULUS_TOKEN', F.PROJECT_ROOT / '.env'))
        cfg = current_tournaments(client, configured=load_tournaments())
        provider = StrictSnapshots(client, root, campaign, tournaments=cfg)
        engine = SupplementScheduler(root, campaign, snapshots=provider, execution_deadline=deadline)
        engine.budget = BoundedBudget(root, campaign, load_quota_policy(), engine.run_id,
                                      acceptance_log=engine.budget.acceptance_log)
        result = engine.run(max_jobs=3, lock_held=True, paused_players=('claude', 'gpt'))
        counts, error = quota.detect_agy_activity()
        append_record(root / 'm2_supplements.jsonl', {'type': 'supplement_finished', 'version': VERSION,
            'supplement_id': campaign['campaign_id'], 'at': to_iso(utc_now()), 'scheduler_run_id': engine.run_id,
            'jobs_completed': result['jobs_completed'], 'concurrent_agy_counts': counts,
            'activity_error': error})


def report(root, campaign):
    rec, rows = registration(root, campaign)
    attempts = own_attempts(root, campaign)
    quota_rows = read_complete(root / 'quota.jsonl')
    starts = [r for r in read_complete(root / 'forecast_attempt_starts.jsonl')
              if r.get('question_id') in {q['question_id'] for q in campaign['questions']}]
    unknown = sorted({r['attempt_id'] for r in starts} - {r['attempt_id'] for r in attempts})
    measurements = []
    for r in attempts:
        drop = quota.attempt_drop(r, quota_rows) if r.get('model_check') == 'verified' else None
        pair = {s['when']: s for s in quota_rows if s.get('attempt_id') == r['attempt_id'] and s.get('when') in ('before', 'after')}
        measurements.append({'attempt_id': r['attempt_id'], 'player': r['player'], 'tier': r['tier'],
            'question_id': r['question_id'], 'kind': r['attempt_kind'], 'model_check': r['model_check'],
            'tokens': B.tokens(r), 'seconds': elapsed(r, 'started_at_utc', 'finished_at_utc'),
            'quota_fraction': drop, 'quota_layout': quota.argon_buckets(pair.get('before')).layout,
            'error_type': r.get('error_type'), 'pipeline_version': r['pipeline_version']})
    out = {'type': 'm2_supplement_report', 'version': VERSION, 'report_id': new_id('m2cost'),
        'at': to_iso(utc_now()), 'supplement_id': campaign['campaign_id'], 'parent_id': rec['parent_id'],
        'finished': any(r['type'] == 'supplement_finished' for r in rows), 'actual_calls': len(starts),
        'unknown_attempt_ids': unknown, 'measurements': measurements,
        'trusted_attempts': sum(r['quota_fraction'] is not None for r in measurements),
        'm2_acceptance_changed': False,
        'limitations': ['Tier 1 only; never impute Tier 2 costs from these samples.',
                       'Original untrusted costs remain untrusted. This separate report does not promote M2.',
                       'At most three new questions; insufficient to estimate population capacity reliably.']}
    dest = F.PROJECT_ROOT / 'reports' / (out['report_id'] + '.json')
    with dest.open('x') as stream:
        json.dump(out, stream, ensure_ascii=False, indent=2)
    append_record(root / 'm2_supplements.jsonl', {'type': 'supplement_report_created',
        'supplement_id': campaign['campaign_id'], 'at': out['at'], 'report_id': out['report_id']})
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('prepare', 'run', '_worker', 'report'))
    p.add_argument('--campaign', type=Path, required=True)
    args = p.parse_args(argv)
    campaign = json.loads(args.campaign.read_text())
    if args.command == 'prepare':
        print(json.dumps({'campaign_path': str(prepare(F.DATA, campaign)), 'predictions_run': 0}))
    elif args.command == '_worker':
        worker(F.DATA, campaign)
    elif args.command == 'run':
        # Bound the whole controller including API, preflight and cleanup; timeout never retries.
        code, timed_out = supervise(
            [sys.executable, str(Path(__file__).resolve()), '_worker', '--campaign', str(args.campaign.resolve())],
            timeout_s=MAX_SECONDS - CLEANUP_RESERVE_S)
        append_record(F.DATA / 'm2_supplements.jsonl', {'type': 'supplement_supervisor_finished',
            'supplement_id': campaign['campaign_id'], 'at': to_iso(utc_now()),
            'exit_code': code, 'timed_out': timed_out, 'automatic_retry': False})
        print(json.dumps({'exit_code': code, 'automatic_retry': False}))
        return code
    else:
        print(json.dumps(report(F.DATA, campaign), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
