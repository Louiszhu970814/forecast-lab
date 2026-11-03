"""Offline conversion of frozen metadata observations into coverage replay inputs.

No API, models, forecast text or credentials. A complete observation is required
for registration; an unfinished window can only produce an integrity audit.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import coverage_sim as C
import metadata_observer as M
from flab.config import PLAYERS
from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso, utc_now

VERSION = 'metadata_replay_v1'
FIELDS = {'question_id', 'post_id', 'question_type', 'status', 'post_type', 'open_time',
          'scheduled_close_time', 'scheduled_resolve_time', 'content_fields_present',
          'content_sha256', 'actual_close_time', 'cp_reveal_time', 'spot_scoring_time',
          'structure_sha256', 'supported'}
TIMES = ('open_time', 'scheduled_close_time', 'actual_close_time', 'scheduled_resolve_time', 'cp_reveal_time', 'spot_scoring_time')


def checked(row):
    if not isinstance(row, dict) or not FIELDS <= set(row) or set(row) - FIELDS - {'repeated_question_id'}:
        raise ValueError('metadata fields differ from allowlist')
    for key in ('question_id', 'post_id'):
        if type(row[key]) is not int or row[key] <= 0:
            raise ValueError('invalid metadata identity')
    for key in ('content_sha256', 'structure_sha256'):
        if not isinstance(row[key], str) or re.fullmatch('[0-9a-f]{64}', row[key]) is None:
            raise ValueError('invalid metadata hash')
    if not isinstance(row['content_fields_present'], list) or not set(row['content_fields_present']) <= set(M.CONTENT_FIELDS):
        raise ValueError('invalid metadata availability fields')
    if type(row['supported']) is not bool or ('repeated_question_id' in row and type(row['repeated_question_id']) is not bool):
        raise ValueError('invalid metadata flags')
    row = copy.deepcopy(row)
    for key, allowed in {'status': {'open', 'upcoming', 'closed', 'resolved', 'pending', 'canceled', 'deleted'},
                         'post_type': {'question', 'group_of_questions', 'conditional'},
                         'question_type': {'binary', 'multiple_choice', 'numeric', 'date', 'discrete'}}.items():
        if row[key] is not None and (not isinstance(row[key], str) or row[key] not in allowed):
            row[key] = 'unknown'
    for key in TIMES:
        if row[key] is not None:
            parse_iso(row[key])
    return row


def visible(row, at):
    required = {'type', 'title', 'description', 'resolution_criteria'}
    if row['question_type'] == 'multiple_choice':
        required.add('options')
    return row['status'] == 'open' and required <= set(row['content_fields_present']) \
        and row['open_time'] is not None and parse_iso(row['open_time']) <= at \
        and row['scheduled_close_time'] is not None and at < parse_iso(row['scheduled_close_time']) \
        and (row['actual_close_time'] is None or at < parse_iso(row['actual_close_time']))


def convert(policy, snapshots, polls, lifecycle):
    """Pure transformation; first-pass/partial-poll questions never vanish from the audit."""
    M.validate(policy)
    start, cohort_end, end = (parse_iso(policy[k]) for k in ('start', 'cohort_end', 'end'))
    issues, by_slot, all_rows = set(), defaultdict(list), defaultdict(list)
    within_pass_changes = set()
    pairs = set()
    for source in snapshots:
        slot, pid = source['slot'], source['project_id']
        if type(slot) is not int or slot < 0 or (slot, pid) in pairs:
            raise ValueError('duplicate/invalid project snapshot')
        pairs.add((slot, pid))
        if source['inventory_consistent'] is not True or source['server_atomic_snapshot'] is not False:
            raise ValueError('invalid inventory consistency evidence')
        a, b, c = (parse_iso(source[k]) for k in ('started_at', 'first_pass_finished_at', 'finished_at'))
        if not start <= a <= b <= c:
            raise ValueError('invalid snapshot times')
        if c >= end:
            issues.add('snapshot_after_bound')
        passes = [[checked(q) for q in source[key]] for key in ('first_pass_questions', 'questions')]
        mapping = lambda rows: {(q['post_id'], q['question_id'], q['post_type']) for q in rows}
        if mapping(passes[0]) != mapping(passes[1]):
            raise ValueError('invalid dual inventory mapping')
        if any(len({q['question_id'] for q in rows}) != len(rows) or any(q.get('repeated_question_id') for q in rows) for rows in passes):
            issues.add('repeated_question_mapping')
        first = {q['question_id']: q for q in passes[0]}
        for row in passes[1]:
            prior = first[row['question_id']]
            if set(prior['content_fields_present']) == set(row['content_fields_present']) \
                    and (prior['content_sha256'] != row['content_sha256'] or prior['structure_sha256'] != row['structure_sha256']):
                within_pass_changes.add(row['question_id'])
        clean = {k: source[k] for k in ('slot', 'project_id', 'venue', 'started_at', 'finished_at')}
        clean['questions'] = passes[1]
        by_slot[slot].append(clean)
        for rows in passes:
            for row in rows:
                all_rows[row['question_id']].append((source['venue'], row))

    starts, finishes, targets = {}, {}, {}
    for event in polls:
        slot = event['slot']
        kind = event['type']
        destination = {'poll_started': starts, 'poll_finished': finishes, 'projects_discovered': targets}.get(kind)
        if destination is not None:
            if slot in destination:
                raise ValueError('duplicate poll event')
            destination[slot] = event
        elif kind == 'poll_missed':
            issues.add('missed_poll')
    expected = math.ceil((end - start).total_seconds() / policy['interval_s'])
    complete = []
    for slot in range(expected):
        finish = finishes.get(slot)
        if finish is None or finish.get('ok') is not True:
            issues.add('incomplete_poll')
            continue
        if slot not in starts or slot not in targets:
            raise ValueError('missing successful poll evidence')
        due = start + timedelta(seconds=slot * policy['interval_s'])
        a, b = parse_iso(starts[slot]['at']), parse_iso(finish['at'])
        if not due <= a <= b < min(end, due + timedelta(seconds=policy['interval_s'])) \
                or (b - a).total_seconds() > policy['poll_budget_s']:
            issues.add('poll_timing_outside_bound')
            continue
        wanted = {(r['venue'], r['project_id']) for r in targets[slot]['targets']}
        got = {(r['venue'], r['project_id']) for r in by_slot[slot]}
        if wanted != got or not wanted:
            raise ValueError('complete poll has missing project snapshots')
        if any(parse_iso(s['started_at']) < a or parse_iso(s['finished_at']) > b for s in by_slot[slot]):
            raise ValueError('project snapshot outside poll')
        complete.append((slot, b, (b - a).total_seconds()))
    if not lifecycle or lifecycle[-1].get('type') != 'completed':
        issues.add('observation_not_completed')
    if any(s not in {x[0] for x in complete} for s in by_slot):
        issues.add('partial_poll_snapshots')
    last = max((t for _, t, _ in complete), default=start)
    schedule, cohorts, carry, tail, brackets, losses = [], [], [], [], [], []
    question_issues, unassignable, warnings = {}, [], set()
    for qid, rows in sorted(all_rows.items()):
        variants = [r for _, r in rows]
        openings = sorted({r['open_time'] for r in variants if r['open_time'] is not None}, key=parse_iso)
        closes = sorted({r['scheduled_close_time'] for r in variants if r['scheduled_close_time'] is not None}, key=parse_iso)
        in_cohort = any(start <= parse_iso(t) < cohort_end for t in openings)
        carry_in = not in_cohort and any(parse_iso(t) < start for t in openings) and any(parse_iso(t) > start for t in closes)
        tail_work = not in_cohort and not carry_in and any(cohort_end <= parse_iso(t) < end for t in openings)
        if not openings or not closes:
            issues.add('unknown_question_time')
            unassignable.append(qid)
            question_issues[str(qid)] = ['unknown_question_time']
        if not (in_cohort or carry_in or tail_work):
            continue
        supported = any(r['post_type'] == 'question' and r['question_type'] in C.SUPPORTED for r in variants)
        venues = sorted({v for v, _ in rows})
        entry = {'question_id': qid, 'supported': supported, 'venues': venues}
        (cohorts if in_cohort else carry if carry_in else tail).append(entry)
        defects = set()
        if any(r['status'] in (None, 'unknown') or r['post_type'] in (None, 'unknown') for r in variants):
            defects.add('unknown_status_or_shape')
        if qid in within_pass_changes:
            defects.add('within_inventory_revision')
        if len(openings) != 1 or len(closes) != 1 or any(r[k] is None for r in variants for k in ('open_time', 'scheduled_close_time')):
            defects.add('unmodelled_time_revision')
        identities = {(r['post_id'], r['post_type'], r['question_type']) for r in variants}
        if len(identities) != 1:
            defects.add('mixed_identity_or_type')
        if not openings or not closes:
            question_issues[str(qid)] = sorted(defects | {'unknown_question_time'})
            continue
        opening = next((t for t in openings if start <= parse_iso(t) < cohort_end), openings[0]) if in_cohort else openings[0]
        closing = closes[0]
        if parse_iso(opening) >= parse_iso(closing):
            defects.add('invalid_window')
            question_issues[str(qid)] = sorted(defects)
            continue
        kind = next((r['question_type'] for r in variants if r['post_type'] == 'question' and r['question_type'] in C.SUPPORTED), variants[0]['question_type'])
        if not isinstance(kind, str) or kind == 'unknown':
            kind = 'unknown'; defects.add('unknown_type')
        actuals = [r['actual_close_time'] for r in variants if r['actual_close_time'] is not None]
        actual = min(actuals, key=parse_iso) if actuals else None
        effective_close = min(parse_iso(closing), parse_iso(actual)) if actual else parse_iso(closing)
        submit_cutoff = min(effective_close, C.V.submit_deadline({'scheduled_close_time': closing}, C.engine_policy()['deadlines']))
        if submit_cutoff > last:
            if in_cohort and supported:
                defects.add('right_censored')
            else:
                warnings.add('background_or_unsupported_right_censored')
        intervals, revisions, active_since, previous_visible = [], [], None, None
        first_visible = None
        for slot, at, _ in complete:
            candidates = [q for s in by_slot[slot] for q in s['questions'] if q['question_id'] == qid]
            # Cross-venue inconsistencies are preserved as uncertainty, not chosen away.
            if len({hash_obj(q) for q in candidates}) > 1:
                defects.add('cross_venue_inconsistent')
            row = candidates[-1] if candidates else None
            available = row is not None and visible(row, at)
            if available:
                first_visible = first_visible or to_iso(at)
                if active_since is None:
                    active_since = at
                if previous_visible:
                    prior_at, prior = previous_visible
                    changed = row['content_sha256'] != prior['content_sha256'] or row['structure_sha256'] != prior['structure_sha256']
                    if changed and set(row['content_fields_present']) != set(prior['content_fields_present']):
                        defects.add('ambiguous_content_visibility_change')
                    elif changed and parse_iso(opening) <= at < effective_close:
                        structural = row['structure_sha256'] != prior['structure_sha256']
                        revisions.append({'time': to_iso(at), 'structural': structural})
                        brackets.append({'question_id': qid, 'after': to_iso(prior_at), 'by': to_iso(at), 'structural': structural})
                previous_visible = (at, row)
            elif active_since is not None:
                # Unexpected disappearance may have happened immediately after
                # the previous positive sample, not at this negative sample.
                stop = effective_close if at >= effective_close else previous_visible[0]
                if at < effective_close:
                    losses.append({'question_id': qid, 'after': to_iso(previous_visible[0]), 'by': to_iso(at)})
                if active_since < stop:
                    intervals.append({'start': to_iso(active_since), 'end': to_iso(stop)})
                active_since = None
        if active_since is not None and active_since < min(last, effective_close):
            intervals.append({'start': to_iso(active_since), 'end': to_iso(min(last, effective_close))})
        if defects:
            question_issues[str(qid)] = sorted(defects)
        schedule.append({'question_id': qid, 'venue': 'main' if 'main' in venues else 'minibench', 'venues': venues,
            'question_type': kind if supported else 'unsupported_' + kind,
            'open_time': opening, 'scheduled_close_time': closing, 'actual_close_time': actual,
            'visible_at': first_visible, 'visibility_intervals': intervals, 'revisions': revisions,
            'workload_only': carry_in or tail_work, 'unreplayable': bool(defects)})
    if question_issues:
        issues.add('question_uncertainties')
    if not any(q['supported'] for q in cohorts):
        issues.add('empty_supported_denominator')
    return {'version': VERSION, 'observation_id': policy['observation_id'], 'policy_sha256': hash_obj(policy),
        'source_sha256': hash_obj({'snapshots': snapshots, 'polls': polls, 'lifecycle': lifecycle}),
        'cohort': cohorts, 'carry_in': carry, 'tail_work': tail, 'unassignable_questions': unassignable,
        'schedule': schedule, 'revision_brackets': brackets, 'availability_loss_brackets': losses, 'warnings': sorted(warnings),
        'question_issues': question_issues, 'issues': sorted(issues), 'completed_polls': len(complete),
        'expected_polls': expected, 'observed_until': to_iso(last),
        'max_poll_duration_s': max((d for _, _, d in complete), default=None),
        'poll_completions': [to_iso(t) for _, t, _ in complete],
        'ready_for_replay': not issues, 'm3_passed': False,
        'interpretation': 'Sampled state at completed polls, not an atomic or continuously observed server history; revision upper endpoints are not guaranteed worst cases.'}


def load_observation(root):
    policy = M.load(root, verify_code=True)
    # Read completed-poll markers first. Later appended snapshots can only make an
    # ongoing audit conservative; registration requires a terminal complete window.
    polls = read_complete(root / 'polls.jsonl')
    snapshots = list(M.snapshot_rows(root / 'snapshots.jsonl'))
    lifecycle = read_complete(root / 'lifecycle.jsonl')
    return policy, convert(policy, snapshots, polls, lifecycle)


def register(output, observation, sim_id, capacity, *, deadline_policy='d114_original', now=utc_now):
    policy, artifact = load_observation(observation)
    if not artifact['ready_for_replay']:
        raise ValueError('observation is incomplete or ambiguous; audit issues before replay')
    if capacity.get('as_of') != policy['start'] or set(capacity.get('players', {})) != set(PLAYERS) \
            or re.fullmatch('[0-9a-f]{64}', str(capacity.get('source_sha256', ''))) is None:
        raise ValueError('explicit start-of-window capacity evidence required')
    # The original observation was designed with a five-minute submit lead.
    # A later runtime config must never silently replace that baseline.
    leads = {'d114_original': 5, 'd116_margin10': 10}
    if deadline_policy not in leads or C.engine_policy()['deadlines'] != {
            'safety_margin_minutes': 20, 'submit_lead_minutes': leads.get(deadline_policy),
            'duration_quantile': .9}:
        raise ValueError('deadline policy differs: use the archived D114 config or explicitly select d116_margin10')
    settings = copy.deepcopy(policy['replay_recipe']['derived_settings'])
    settings.update(version=C.VERSION, sim_id=sim_id, cycle_trigger='after_completion')
    settings['deadline_policy'] = deadline_policy
    if sum(settings['submission'].values()) != 270 or settings['observation'] != {'start': policy['start'], 'end': policy['cohort_end']}:
        raise ValueError('frozen D114 replay recipe differs')
    settings['poll_s'] = max(settings['poll_s'], artifact['max_poll_duration_s'])
    settings['metadata_source_sha256'] = artifact['source_sha256']
    settings['capacity_evidence_sha256'] = hash_obj(capacity)
    settings['diagnostic_only'] = True
    for p in PLAYERS:
        line = capacity['players'][p]
        settings['players'][p]['initial_attempts_used'] = line['initial_attempts_used']
        settings['players'][p]['stops'] = copy.deepcopy(line['stops'])
    C.validate(settings, artifact['schedule'])
    frozen = {'artifact': artifact, 'settings': settings, 'capacity': copy.deepcopy(capacity)}
    record = {'type': 'metadata_conversion_registered', 'version': VERSION, 'sim_id': sim_id,
              'sha256': hash_obj(frozen), 'registered_at': to_iso(now()), 'frozen': frozen}
    comparison = copy.deepcopy(settings)
    comparison.update(sim_id=sim_id + '_fixed_grid', cycle_trigger='fixed_grid')
    # Check both IDs before appending either member. A process crash can leave
    # a prefix; identical registration resumes that prefix without new results.
    with transaction(output / '.coverage_sim'), transaction(output / '.metadata_conversion'):
        existing = read_complete(output / 'coverage_sims.jsonl')
        for cfg in (settings, comparison):
            expected = hash_obj({'settings': cfg, 'schedule': artifact['schedule'], 'engine': C.engine_policy()})
            for row in existing:
                if row.get('sim_id') == cfg['sim_id']:
                    C._verify(row)
                    if row['sha256'] != expected:
                        raise ValueError('registered conversion cannot change (comparison ID conflict)')
        prior = [r for r in read_complete(output / 'metadata_conversions.jsonl') if r['sim_id'] == sim_id]
        if any(r['sha256'] != record['sha256'] or r['sha256'] != hash_obj(r['frozen']) for r in prior):
            raise ValueError('registered conversion cannot change')
        if not prior:
            append_record(output / 'metadata_conversions.jsonl', record)
        primary = C.register(output, settings, artifact['schedule'], now=now)
        C.register(output, comparison, artifact['schedule'], now=now)
    return primary


def summary(artifact):
    return {k: artifact[k] for k in ('observation_id', 'completed_polls', 'expected_polls',
        'observed_until', 'issues', 'warnings', 'ready_for_replay', 'm3_passed')} | {
        'denominator_all': len(artifact['cohort']), 'denominator_supported': sum(r['supported'] for r in artifact['cohort']),
        'carry_in': len(artifact['carry_in']), 'tail_work': len(artifact['tail_work']), 'uncertain_questions': len(artifact['question_issues']),
        'unassignable_questions': len(artifact['unassignable_questions']),
        'revision_brackets': len(artifact['revision_brackets']),
        'availability_loss_brackets': len(artifact['availability_loss_brackets'])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('audit')
    reg = sub.add_parser('register')
    reg.add_argument('--output', type=Path, required=True)
    reg.add_argument('--sim-id', required=True)
    reg.add_argument('--capacity', type=Path, required=True)
    reg.add_argument('--deadline-policy', choices=('d114_original', 'd116_margin10'), default='d114_original')
    args = parser.parse_args(argv)
    if args.command == 'audit':
        _, artifact = load_observation(args.observation)
        result = summary(artifact)
    else:
        rec = register(args.output, args.observation, args.sim_id, json.loads(args.capacity.read_text()),
                       deadline_policy=args.deadline_policy)
        result = {k: rec[k] for k in ('sim_id', 'sha256', 'schedule_sha256')}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
