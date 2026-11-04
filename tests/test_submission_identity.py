"""Synthetic identity contract shared by resolve and score (D128 P1-P4)."""
from __future__ import annotations

import pytest

from flab.submission_identity import (
    is_snapshotless_probe, normalize_mode, normalize_snapshot_id, submission_identity, submission_status,
)


@pytest.mark.parametrize('row,expected', [
    ({'status': None, 'state': 'FORECAST_OK'}, 'forecast_ok'),
    ({'status': '', 'state': ' intent '}, 'intent'),
    ({'status': ' Comment_OK ', 'state': 'intent'}, 'comment_ok'),
    ({'status': 7, 'state': 'intent'}, ''),
])
def test_submission_status_matches_shared_ledger_convention(row, expected):
    assert submission_status(row) == expected


@pytest.mark.parametrize('snapshot', [None, '', '   ', '\t\n'])
@pytest.mark.parametrize('mode', ['local', 'trial'])
def test_only_explicit_non_live_probe_can_be_skipped(snapshot, mode):
    identity = submission_identity({'question_snapshot_id': snapshot, 'mode': mode})
    assert identity == {'question_snapshot_id': None, 'mode': mode}
    assert is_snapshotless_probe(identity)


@pytest.mark.parametrize('mode', [None, '', ' ', 'Live', 'live ', ' trial', 'TRIAL', 'dry_run', 'invalid', 1, False])
def test_unknown_or_malformed_modes_never_acquire_probe_exemption(mode):
    identity = submission_identity({'question_snapshot_id': None, 'mode': mode})
    assert normalize_mode(mode) is None
    assert not is_snapshotless_probe(identity)
    # An explicit malformed receipt cannot borrow the historical intent's trial mode.
    inherited = submission_identity({'mode': mode}, {'mode': 'trial'})
    assert not is_snapshotless_probe(inherited)


def test_sparse_receipt_inherits_live_from_intent_and_preserves_snapshot():
    identity = submission_identity({}, {'mode': 'live', 'question_snapshot_id': ' s1 '})
    assert identity == {'mode': 'live', 'question_snapshot_id': 's1'}
    assert not is_snapshotless_probe(identity)
    assert normalize_snapshot_id(7) is None
    assert not is_snapshotless_probe(submission_identity({}))


@pytest.mark.parametrize('row,intent', [
    ({'mode': 'trial'}, {'mode': 'live'}),
    ({'mode': 'live'}, {'mode': 'trial'}),
    ({'mode': 'trial', 'question_snapshot_id': 's2'}, {'mode': 'trial', 'question_snapshot_id': 's1'}),
    ({'mode': 'trial', 'question_snapshot_id': 7}, {'mode': 'trial'}),
])
def test_conflicting_or_malformed_identity_fails_closed(row, intent):
    identity = submission_identity(row, intent)
    assert identity == {'question_snapshot_id': None, 'mode': None}
    assert not is_snapshotless_probe(identity)


@pytest.mark.parametrize('mode,skip', [
    ('local', True), ('trial', True), ('live', False), (None, False),
    ('', False), ('TRIAL', False), ('trial ', False), ('dry_run', False),
])
@pytest.mark.parametrize('snapshot_id', [None, '', '   '])
def test_resolve_score_classify_snapshotless_identity_consistently(tmp_path, mode, skip, snapshot_id):
    from flab.jsonl import append_record
    import resolve
    import score
    append_record(tmp_path / 'submissions.jsonl', {
        'question_id': 101, 'intent_id': 'i1', 'status': 'forecast_ok', 'mode': mode,
        'question_snapshot_id': snapshot_id, 'at': '2026-11-03T10:00:00Z',
    })
    resolved = resolve.poll_resolutions(tmp_path, now='2026-11-06T14:00:00Z')
    board = score.evaluate_scoreboard(tmp_path, as_of='2026-11-06T14:00:00Z', n_boot=10)
    assert bool(resolved['skipped']) is skip
    assert resolved['status'] == ('ok' if skip else 'error')
    assert board['excluded_counts'].get('submitted_without_snapshot_id', 0) == int(skip)
    assert board['excluded_counts']['no_resolution_event'] == int(not skip)


def test_score_sparse_receipt_live_intent_cannot_be_overridden_by_later_dryrun(tmp_path):
    from flab.jsonl import append_record
    import score
    for row in [
        {'question_id': 101, 'intent_id': 'i_live', 'status': 'intent', 'mode': 'live',
         'question_snapshot_id': None, 'at': '2026-11-03T09:00:00Z'},
        {'question_id': 101, 'intent_id': 'i_live', 'status': 'forecast_ok',
         'at': '2026-11-03T10:00:00Z'},
        {'question_id': 101, 'intent_id': 'i_dry', 'status': 'dry_run', 'mode': 'local',
         'question_snapshot_id': None, 'at': '2026-11-03T11:00:00Z'},
    ]:
        append_record(tmp_path / 'submissions.jsonl', row)
    board = score.evaluate_scoreboard(tmp_path, as_of='2026-11-06T14:00:00Z', include_dry_run=True, n_boot=10)
    assert board['submitted_questions_count'] == 1
    assert board['excluded_counts']['no_resolution_event'] == 1
    assert board['excluded_counts'].get('submitted_without_snapshot_id', 0) == 0


@pytest.mark.parametrize('receipt,intent', [
    ({'mode': 'trial'}, {'mode': 'live'}),
    ({'mode': 'live'}, {'mode': 'trial'}),
    ({'mode': None}, {'mode': 'trial'}),
    ({'mode': 'trial', 'question_snapshot_id': 's2'},
     {'mode': 'trial', 'question_snapshot_id': 's1'}),
])
def test_resolve_score_preserve_conflicts_before_receipt_merge(tmp_path, receipt, intent):
    from flab.jsonl import append_record
    import resolve
    import score
    for row in [
        {'question_id': 101, 'intent_id': 'i1', 'status': 'intent',
         'at': '2026-11-03T09:00:00Z', **intent},
        {'question_id': 101, 'intent_id': 'i1', 'status': 'forecast_ok',
         'at': '2026-11-03T10:00:00Z', **receipt},
    ]:
        append_record(tmp_path / 'submissions.jsonl', row)
    resolved = resolve.poll_resolutions(tmp_path, now='2026-11-06T14:00:00Z')
    board = score.evaluate_scoreboard(tmp_path, as_of='2026-11-06T14:00:00Z', n_boot=10)
    assert resolved['status'] == 'error'
    assert not resolved['skipped']
    assert resolved['errors'][0]['error_type'] == 'MissingSubmittedSnapshot'
    assert board['excluded_counts'].get('submitted_without_snapshot_id', 0) == 0
    assert board['excluded_counts']['no_resolution_event'] == 1


@pytest.mark.parametrize('mode', ['local', 'trial', 'live', None])
def test_score_resolved_row_with_missing_snapshot_is_rejected_even_for_trial(tmp_path, mode):
    from flab.jsonl import append_record
    import score
    append_record(tmp_path / 'submissions.jsonl', {
        'question_id': 101, 'intent_id': 'i1', 'status': 'forecast_ok', 'mode': mode,
        'question_snapshot_id': '', 'at': '2026-11-03T10:00:00Z',
    })
    append_record(tmp_path / 'resolutions.jsonl', {
        'type': 'resolution_event', 'question_id': 101, 'resolution_event_id': 'r1',
        'observed_at_utc': '2026-11-06T12:00:00Z', 'resolution_status': 'resolved', 'canonical_outcome': 1,
    })
    board = score.evaluate_scoreboard(tmp_path, as_of='2026-11-06T14:00:00Z', n_boot=10)
    assert board['scorable_resolved_count'] == 0
    assert board['excluded_counts'].get('submitted_without_snapshot_id', 0) == 0
    assert board['excluded_counts']['missing_submitted_snapshot'] == 1
