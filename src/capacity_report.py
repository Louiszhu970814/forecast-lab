"""Operational timing inventory. Never emit questions, forecasts, reasoning or CLI raw output."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

import budget as B
import forecast as F
import quota
from flab.ids import new_id
from flab.jsonl import append_record
from flab.ledger import transaction, read_complete
from flab.timeutil import parse_iso, to_iso, utc_now

VERSION = 'capacity_report_v1'


def elapsed(r, start, end):
    try:
        value = (parse_iso(r[end]) - parse_iso(r[start])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None
    return value if value >= 0 else None


def contained(attempt, job):
    if elapsed(attempt, 'started_at_utc', 'cli_finished_at_utc') is None:
        return False
    return (parse_iso(job['started_at']) <= parse_iso(attempt['started_at_utc'])
            and parse_iso(attempt['cli_finished_at_utc']) <= parse_iso(job['finished_at']))


def stats(values):
    values = [v for v in values if v is not None]
    return {'n': len(values), 'total': sum(values) if values else None,
            'p50': B.quantile(values, .5), 'p90': B.quantile(values, .9), 'max': max(values, default=None)}


def build(root, campaign):
    ids = {q['question_id'] for q in campaign['questions']}
    with transaction(root):
        rows = [r for r in read_complete(root / 'forecasts.jsonl') if r.get('question_id') in ids]
        starts = [r for r in read_complete(root / 'forecast_attempt_starts.jsonl') if r.get('question_id') in ids]
        jobs = [r for r in read_complete(root / 'scheduler_jobs.jsonl') if r.get('campaign_id') == campaign['campaign_id']]
        whole = [r for r in read_complete(root / 'worker_timings.jsonl') if r.get('campaign_id') == campaign['campaign_id']]
        quotas = read_complete(root / 'quota.jsonl')
    if any(not isinstance(r.get('attempt_id'), str) or not r['attempt_id'] for r in [*rows, *starts]):
        raise ValueError('malformed attempt identity; refuse to hide unknown starts')
    cells = {}
    for player in ('claude', 'gemini', 'gpt'):
        for tier in (1, 2):
            attempts = [r for r in rows if r.get('player') == player and r.get('tier') == tier]
            jrows = [j for j in jobs if j.get('player') == player and j.get('tier') == tier]
            full = [j for j in whole if j.get('player') == player and j.get('tier') == tier]
            # Match a whole job by run, snapshot and interval; do not add reruns from another job.
            overhead = []
            for j in jrows:
                duration = elapsed(j, 'started_at', 'finished_at')
                if duration is None:
                    continue
                calls = [r for r in attempts if r.get('run_id') == j.get('run_id')
                         and r.get('question_snapshot_id') == j.get('question_snapshot_id')
                         and contained(r, j)]
                cli = [elapsed(r, 'started_at_utc', 'cli_finished_at_utc') for r in calls]
                if cli and all(v is not None for v in cli) and sum(cli) <= duration:
                    overhead.append(duration - sum(cli))
            samples = {}
            for kind in ('initial', 'rerun', 'format_repair'):
                samples[kind] = [v for r in attempts if r.get('attempt_kind') == kind
                                 and (v := elapsed(r, 'started_at_utc', 'cli_finished_at_utc')) is not None]
            drops = [quota.attempt_drop(r, quotas) for r in attempts if r.get('model_check') == 'verified'] if player == 'gemini' else []
            cells[f'{player}.tier{tier}'] = {
                'attempts': len(attempts),
                'missing_cli_timing_attempts': sum(elapsed(r, 'started_at_utc', 'cli_finished_at_utc') is None for r in attempts),
                'invalid_job_timing_records': sum(elapsed(j, 'started_at', 'finished_at') is None for j in jrows),
                'questions': len({r['question_id'] for r in attempts}),
                'kinds': dict(Counter(r.get('attempt_kind') for r in attempts)),
                'errors': dict(Counter(r.get('error_type') or 'none' for r in attempts)),
                'model_checks': dict(Counter(r.get('model_check') for r in attempts)),
                'cli_seconds': stats([elapsed(r, 'started_at_utc', 'cli_finished_at_utc') for r in attempts]),
                'attempt_seconds_excludes_pre_quota': stats([elapsed(r, 'started_at_utc', 'finished_at_utc') for r in attempts]),
                'job_seconds_excludes_preflight': stats([elapsed(j, 'started_at', 'finished_at') for j in jrows]),
                'job_non_cli_seconds_excludes_preflight': stats(overhead),
                'full_execute_seconds': stats([elapsed(j, 'started_at', 'finished_at') for j in full]),
                'tokens': stats([B.tokens(r) for r in attempts]),
                'trusted_quota_fraction': stats(drops),
                'cli_samples_by_kind': samples}
    return {'type': 'capacity_report', 'version': VERSION, 'report_id': new_id('capacity'), 'at': to_iso(utc_now()),
        'campaign_id': campaign['campaign_id'], 'questions': len(ids), 'cells': cells,
        'unknown_attempts': len({s['attempt_id'] for s in starts} - {r['attempt_id'] for r in rows}),
        'formal_weekly_capacity_established': False, 'm3_coverage_accepted': False,
        'limitations': [
            'Historical scheduler jobs start after preflight; those durations are lower bounds on full worker occupancy.',
            'Full execute timing, when present, excludes queue/provider refresh and submission.',
            'CLI elapsed time is observable even when model identity or quota attribution is uncertain; their statuses remain separate.',
            'No untrusted quota cost is imputed. Zero quota observations are reported but not a reliable positive-cost estimate.',
            'Observed attempt counts are not a guaranteed weekly subscription capacity or a new budget policy.',
            'No platform private-comment/multiple-choice contract or launchd acceptance follows from these timings.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    args = parser.parse_args(argv)
    report = build(F.DATA, json.loads(args.campaign.read_text()))
    dest = F.PROJECT_ROOT / 'reports' / (report['report_id'] + '.json')
    with dest.open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    append_record(F.DATA / 'capacity_reports.jsonl', {'type':'capacity_report_created','at':report['at'],
        'report_id':report['report_id'],'campaign_id':report['campaign_id']})
    print(json.dumps({'path':str(dest),'formal_weekly_capacity_established':False,'m3_coverage_accepted':False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
