"""Persisted new-question cadence; known work keeps its independent 60s heartbeat."""
from datetime import timedelta

from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import parse_iso, to_iso

VERSION = 'discovery_v1'
SETTING = {'version': VERSION, 'interval_s': 1800, 'short_window_interval_s': 300,
           'short_window_s': 3600, 'heartbeat_s': 60}


def validate(policy):
    setting = policy.get('discovery')
    if setting is not None and (policy.get('version') != 'continuous_v11' or setting != SETTING):
        raise ValueError('D139 requires the exact bounded discovery_v1 policy')
    if policy.get('version') == 'continuous_v11' and setting != SETTING:
        raise ValueError('continuous_v11 requires discovery_v1')


def policy_hash(policy):
    return hash_obj({k: v for k, v in policy.items() if k != 'questions'})


def latest(root, policy):
    rows = [r for r in read_complete(root / 'discovery.jsonl')
            if r.get('campaign_sha256') == policy_hash(policy)]
    return rows[-1] if rows else None


def due(state, now):
    if state is None:
        return True
    at = parse_iso(state['at'])
    # A clock rollback is not permission to trust stale inputs.
    if now < at or now >= parse_iso(state['next_discovery_at']):
        return True
    return any(q.get('status') == 'upcoming' and q.get('open_time')
               and at < parse_iso(q['open_time']) <= now for q in state['known'])


def record(root, policy, records, *, now, prior=None, previously_known=()):
    """Called only after a complete successful scan; failures never advance cadence."""
    known, metrics = [], []
    interval = prior.get('interval_s', SETTING['interval_s']) if prior else SETTING['interval_s']
    reasons = set(prior.get('shortening_reasons', [])) if prior else set()
    old_ids = {q['question_id'] for row in read_complete(root / 'discovery.jsonl')
               for q in row['known']} | set(previously_known)
    seen = set()
    for role, rec in records:
        skipped = {r.get('question_id') for r in rec.get('skipped', [])}
        for q in rec['timing']:
            if q.get('question_id') is None or q['question_id'] in skipped | seen:
                continue
            seen.add(q['question_id'])
            known.append({**q, 'ground': role, 'tournament_id': rec['tournament_id']})
            start = parse_iso(q['open_time']) if q.get('open_time') else None
            close = parse_iso(q['scheduled_close_time']) if q.get('scheduled_close_time') else None
            window = (close-start).total_seconds() if start and close else None
            first = q['question_id'] not in old_ids
            slack = (close-now).total_seconds()-1800 if close and q.get('status') == 'open' else None
            if window is not None and 0 < window < SETTING['short_window_s']:
                reasons.add('observed_window_below_one_hour')
            # Conservative bounded research horizon: existing two calls + repairs/queries.
            # This is a trigger for faster discovery, not a promise of coverage.
            if first and slack is not None and slack < 3240:
                reasons.add('first_seen_decision_slack_below_research_bound')
            metrics.append({'question_id': q['question_id'], 'scheduled_window_s': window,
                            'first_seen': first,
                            'observed_open_delay_s': max(0, (now-start).total_seconds()) if first and start else None,
                            'decision_slack_s': slack})
    if reasons:
        interval = SETTING['short_window_interval_s']
    result = {'type': 'discovery_completed', 'version': VERSION, 'at': to_iso(now),
              'campaign_id': policy['campaign_id'], 'campaign_sha256': policy_hash(policy),
              'interval_s': interval, 'next_discovery_at': to_iso(now+timedelta(seconds=interval)),
              'actual_discovery_gap_s': (now-parse_iso(prior['at'])).total_seconds() if prior else None,
              'shortening_reasons': sorted(reasons), 'known': known, 'metrics': metrics}
    append_record(root / 'discovery.jsonl', result)
    return result


def actionable_questions(root, known, now):
    """Local metadata-only wake decision, never an admission or permission to submit.

    Waits are rechecked by the ordinary full scan at most 30 minutes later, or at
    an earlier retry/deadline. Unknown records fail toward waking the worker.
    """
    import forecast as F
    from flab.config import load_run_config, load_players_enabled, PLAYERS
    from poll_questions import latest_snapshots
    from scheduler import QUOTA_WAITS
    cfg, enabled = load_run_config(), load_players_enabled()
    snaps = latest_snapshots(root / 'questions.jsonl')
    submissions = read_complete(root / 'submissions.jsonl')
    aggregates = read_complete(root / 'aggregates.jsonl')
    comments = read_complete(root / 'comments.jsonl')
    jobs = read_complete(root / 'scheduler_jobs.jsonl')
    released = {r['wait_id'] for r in jobs if r.get('type') == 'scheduler_wait_reevaluated'}
    stopped = {p for p in PLAYERS if not enabled[p] or
               F.line_stopped(root/'line_status.jsonl', p, root/'forecasts.jsonl', now=now)}
    for row in read_complete(root/'forecasts.jsonl'):
        if row.get('quota_retry_at_utc') and now < parse_iso(row['quota_retry_at_utc']):
            stopped.add(row['player'])
    resumes = {r['player']:parse_iso(r['at']) for r in read_complete(root/'line_status.jsonl')
               if r.get('type')=='line_resume'}
    intent_snapshots = {r['intent_id']: r.get('snapshot',{}).get('snapshot_id') for r in submissions
                        if r.get('status') == 'intent'}
    result = set()
    for q in known:
        qid = q['question_id']
        if q.get('status') != 'open':
            continue
        snap = snaps.get(qid)
        sid = snap.get('snapshot_id') if snap else None
        mine = [r for r in submissions if r.get('question_id') == qid and
                (not intent_snapshots.get(r.get('intent_id')) or not sid or intent_snapshots[r['intent_id']] == sid)]
        if any(r.get('status') in ('comment_ok', 'abandoned', 'forecast_failed', 'comment_failed') for r in mine):
            continue
        if q.get('scheduled_close_time') and now >= parse_iso(q['scheduled_close_time']):
            continue
        if snap is None:
            result.add(qid); continue
        if F.eligible(snap, cfg, now) in ('unsupported_type', 'unsupported_scaling'):
            continue
        finals = [r for r in aggregates if r.get('question_id') == qid and
                  r.get('question_snapshot_id') == sid and r.get('completion_status') == 'complete_local']
        if finals:
            final = finals[-1]
            failed = any(r.get('type') == 'comment_failed' and r.get('aggregate_id') == final.get('record_id') for r in comments)
            if final.get('status') == 'ready' and not failed:
                result.add(qid)
            continue
        deadline = F.decision_deadline(snap, cfg)
        if deadline is not None and now >= deadline:
            result.add(qid); continue
        by_line = {(r.get('player'), r.get('tier')): r for r in jobs
                   if r.get('question_id') == qid and r.get('question_snapshot_id') == sid
                   and r.get('type') == 'scheduler_job_finished'}
        def waiting(row):
            # D140: an old stop is not a permanent wait after the line has resumed.
            if row.get('outcome') == 'line_stopped' and row.get('player') not in stopped:
                resumed = resumes.get(row.get('player'))
                if resumed and row.get('finished_at') and resumed >= parse_iso(row['finished_at']):
                    return False
            if row.get('outcome') not in QUOTA_WAITS | {'budget_wait', 'preflight_blocked', 'line_stopped'}:
                return False
            if row.get('record_id') in released:
                return False
            retry = row.get('quota_retry_at_utc') or row.get('reevaluate_after')
            return not retry or now < parse_iso(retry)
        for player in PLAYERS:
            if player in stopped:
                continue
            one, two = by_line.get((player,1),{}), by_line.get((player,2),{})
            if waiting(one) or waiting(two):
                continue
            # Terminal calls with no aggregate still need finalization; this is
            # deliberately conservative and does not inspect prediction content.
            result.add(qid); break
    return result


def needs_cycle(root, policy, now):
    """No network/model call on idle ticks; deadlines and readback wake separately."""
    validate(policy)
    if 'discovery' not in policy:
        return True
    state = latest(root, policy)
    if due(state, now):
        return True
    rows = read_complete(root / 'submissions.jsonl')
    intents = {r['intent_id']: [] for r in rows if r.get('status') == 'intent'}
    for row in rows:
        if row.get('intent_id') in intents:
            intents[row['intent_id']].append(row)
    for history in intents.values():
        if any(r.get('status') in ('abandoned','forecast_failed','comment_failed','comment_ok') for r in history):
            continue
        last = history[-1]
        if not last.get('next_retry_at') or now >= parse_iso(last['next_retry_at']):
            return True  # Includes unknown outcomes after the question closes.
    starts = read_complete(root/'forecast_attempt_starts.jsonl')
    finished = {r['attempt_id'] for r in read_complete(root/'forecasts.jsonl')}
    if any(r['attempt_id'] not in finished for r in starts):
        return True
    return bool(actionable_questions(root, state['known'], now))
