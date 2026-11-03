import copy

import pytest

import coverage_sim as C
import metadata_observer as M
import metadata_replay as R
from flab.config import PLAYERS
from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import parse_iso
from test_coverage_sim import settings
from test_metadata_observer import NOW, at, policy, post


def evidence():
    cfg = policy()
    replay = settings()
    replay['observation'] = {'start': cfg['start'], 'end': cfg['cohort_end']}
    replay['submission'] = {k: 90 for k in replay['submission']}
    cfg['replay_recipe'] = {'derived_settings': replay}
    snapshots, polls = [], []
    for slot in range(4):
        opening, finish = at(slot * 600), at(slot * 600 + 10)
        obj = post()
        obj['question'].update(scheduled_close_time=at(1500), resolution_criteria='PRIVATE RESOLUTION',
                               status='upcoming' if slot == 0 else 'closed' if slot == 3 else 'open')
        if slot == 3:
            obj['question']['description'] = None
            obj['question']['resolution_criteria'] = None
        rows = M.metadata(obj)
        for venue, pid in [('main', 1), ('minibench', 2)]:
            snapshots.append({'slot': slot, 'project_id': pid, 'venue': venue,
                'started_at': opening, 'first_pass_finished_at': at(slot*600+5), 'finished_at': finish,
                'inventory_consistent': True, 'server_atomic_snapshot': False,
                'first_pass_questions': copy.deepcopy(rows), 'questions': copy.deepcopy(rows)})
        polls += [{'type': 'poll_started', 'slot': slot, 'at': opening},
                  {'type': 'projects_discovered', 'slot': slot, 'at': opening,
                   'targets': [{'venue': 'main', 'project_id': 1}, {'venue': 'minibench', 'project_id': 2}]},
                  {'type': 'poll_finished', 'slot': slot, 'at': finish, 'ok': True}]
    lifecycle = [{'type': 'started', 'at': at(-1)}, {'type': 'completed', 'at': at(1810)}]
    return cfg, snapshots, polls, lifecycle


def converted():
    return R.convert(*evidence())


def save(root, data):
    cfg, snapshots, polls, lifecycle = data
    M.register(root, cfg, now=lambda: parse_iso(at(-2)))
    for filename, rows in [('snapshots.jsonl', snapshots), ('polls.jsonl', polls), ('lifecycle.jsonl', lifecycle)]:
        for row in rows:
            append_record(root / filename, row)


def capacity():
    return {'as_of': at(0), 'source_sha256': 'a'*64,
            'players': {p: {'initial_attempts_used': 0, 'stops': []} for p in PLAYERS}}


def test_complete_conversion_preserves_times_and_second_pass_availability():
    result = converted()
    q = result['schedule'][0]
    assert result['ready_for_replay']
    assert result['m3_passed'] is False
    assert len(result['cohort']) == 1
    assert result['cohort'][0]['venues'] == ['main', 'minibench']
    assert q['open_time'] == at(100)
    assert q['visible_at'] == at(610)
    assert q['visibility_intervals'] == [{'start': at(610), 'end': at(1500)}]
    assert not q['revisions']  # Closed gateway masking is not a text revision.


def test_partial_poll_still_keeps_questions_in_denominator():
    data = evidence()
    data[2][-1]['ok'] = False
    r = R.convert(*data)
    assert len(r['cohort']) == 1
    assert r['completed_polls'] == 3
    assert 'partial_poll_snapshots' in r['issues']
    assert not r['ready_for_replay']


def test_missing_project_cannot_claim_complete_poll():
    data = evidence()
    data[1].pop()
    with pytest.raises(ValueError, match='missing project'):
        R.convert(*data)


def test_first_pass_window_revision_does_not_shrink_denominator():
    data = evidence()
    for s in data[1]:
        for key in ('questions', 'first_pass_questions'):
            s[key][0]['open_time'] = at(1300)
    data[1][0]['first_pass_questions'][0]['open_time'] = at(100)
    result = R.convert(*data)
    assert len(result['cohort']) == 1
    assert result['cohort'][0]['supported']
    assert result['schedule'][0]['unreplayable']
    assert not result['ready_for_replay']


def test_mixed_shape_retains_supported_union():
    data = evidence()
    for s in data[1][-2:]:
        for key in ('questions', 'first_pass_questions'):
            s[key][0].update(post_type='group_of_questions', supported=False)
    result = R.convert(*data)
    assert result['cohort'][0]['supported']
    assert 'mixed_identity_or_type' in result['question_issues']['10']


def test_unknown_time_not_treated_as_zero_or_silently_omitted():
    data = evidence()
    data[1][0]['questions'][0]['open_time'] = None
    result = R.convert(*data)
    assert len(result['cohort']) == 1
    assert not result['ready_for_replay']


def test_entirely_unknown_opening_remains_in_audit():
    data = evidence()
    for s in data[1]:
        for key in ('questions', 'first_pass_questions'):
            s[key][0]['open_time'] = None
    result = R.convert(*data)
    assert result['unassignable_questions'] == [10]
    assert result['question_issues']['10'] == ['unknown_question_time']
    assert not result['ready_for_replay']


def test_revision_uses_completion_endpoint_and_retains_interval():
    data = evidence()
    for s in data[1][4:6]:
        for key in ('questions', 'first_pass_questions'):
            s[key][0]['content_sha256'] = 'b'*64
    result = R.convert(*data)
    assert result['schedule'][0]['revisions'] == [{'time': at(1210), 'structural': False}]
    assert result['revision_brackets'] == [{'question_id': 10, 'after': at(610), 'by': at(1210), 'structural': False}]


def test_structure_hash_change_alone_is_a_revision():
    data = evidence()
    for s in data[1][4:6]:
        for key in ('questions', 'first_pass_questions'):
            s[key][0]['structure_sha256'] = 'e'*64
    result = R.convert(*data)
    assert result['schedule'][0]['revisions'] == [{'time': at(1210), 'structural': True}]


def test_null_status_or_shape_is_an_explicit_uncertainty():
    data = evidence()
    for s in data[1][2:4]:
        for key in ('questions', 'first_pass_questions'):
            s[key][0]['status'] = None
    result = R.convert(*data)
    assert 'unknown_status_or_shape' in result['question_issues']['10']
    assert not result['ready_for_replay']


def test_unknown_visibility_change_is_not_faked_as_known_revision():
    data = evidence()
    for s in data[1][4:6]:
        for key in ('questions', 'first_pass_questions'):
            s[key][0]['content_fields_present'].append('fine_print')
            s[key][0]['content_sha256'] = 'b'*64
    result = R.convert(*data)
    assert 'ambiguous_content_visibility_change' in result['question_issues']['10']
    assert not result['ready_for_replay']


def test_carry_in_competes_without_becoming_cohort():
    data = evidence()
    for s in data[1]:
        for key in ('questions', 'first_pass_questions'):
            old = copy.deepcopy(s[key][0]); old.update(question_id=9, post_id=109, open_time=at(-500))
            s[key].append(old)
    result = R.convert(*data)
    assert [q['question_id'] for q in result['cohort']] == [10]
    assert [q['question_id'] for q in result['carry_in']] == [9]
    assert result['schedule'][0]['workload_only']


def test_actual_close_is_retained_and_shortens_availability():
    data = evidence()
    for s in data[1]:
        for key in ('questions', 'first_pass_questions'):
            s[key][0]['actual_close_time'] = at(900)
    result = R.convert(*data)
    assert result['schedule'][0]['actual_close_time'] == at(900)
    assert result['schedule'][0]['scheduled_close_time'] == at(1500)
    assert result['schedule'][0]['visibility_intervals'] == [{'start': at(610), 'end': at(900)}]


def test_raw_content_rejected_even_if_a_hash_also_exists():
    data = evidence()
    data[1][0]['questions'][0]['description'] = 'SHOULD NOT BE STORED'
    with pytest.raises(ValueError, match='allowlist'):
        R.convert(*data)


def test_first_pass_content_revision_cannot_disappear():
    data = evidence()
    data[1][2]['first_pass_questions'][0]['content_sha256'] = 'd'*64
    result = R.convert(*data)
    assert 'within_inventory_revision' in result['question_issues']['10']
    assert not result['ready_for_replay']


def test_register_rejects_unfinished_observation(tmp_path):
    data = evidence(); data[3].pop()
    save(tmp_path/'source', data)
    with pytest.raises(ValueError, match='incomplete'):
        R.register(tmp_path/'out', tmp_path/'source', 'sample', capacity(), deadline_policy='d116_margin10')


def test_capacity_evidence_required_and_frozen(tmp_path):
    save(tmp_path/'source', evidence())
    with pytest.raises(ValueError, match='capacity'):
        R.register(tmp_path/'out', tmp_path/'source', 'sample', {})
    rec = R.register(tmp_path/'out', tmp_path/'source', 'sample', capacity(), deadline_policy='d116_margin10')
    result = C.simulate(rec)['trials'][0]
    assert result['denominator_supported'] == 1
    assert result['questions'][0]['seen_at'] >= at(610)
    revised = capacity(); revised['players']['gpt']['initial_attempts_used'] = 1
    with pytest.raises(ValueError, match='cannot change'):
        R.register(tmp_path/'out', tmp_path/'source', 'sample', revised, deadline_policy='d116_margin10')


def test_empty_supported_denominator_cannot_pass():
    data = evidence()
    for s in data[1]:
        for key in ('questions', 'first_pass_questions'):
            s[key][0].update(question_type='numeric', supported=False)
    result = R.convert(*data)
    assert len(result['cohort']) == 1
    assert 'empty_supported_denominator' in result['issues']


def test_unexpected_invisibility_ends_at_last_positive_sample():
    data = evidence()
    for s in data[1][4:6]:
        for key in ('questions', 'first_pass_questions'):
            s[key][0]['status'] = 'closed'
    result = R.convert(*data)
    assert result['schedule'][0]['visibility_intervals'] == []
    assert result['availability_loss_brackets'] == [{'question_id': 10, 'after': at(610), 'by': at(1210)}]


def test_tail_questions_compete_without_becoming_cohort():
    data = evidence()
    for s in data[1]:
        for key in ('questions', 'first_pass_questions'):
            tail = copy.deepcopy(s[key][0]); tail.update(question_id=11, post_id=111, open_time=at(1250), scheduled_close_time=at(5000))
            s[key].append(tail)
    result = R.convert(*data)
    assert [r['question_id'] for r in result['cohort']] == [10]
    assert [r['question_id'] for r in result['tail_work']] == [11]
    assert result['schedule'][1]['workload_only']
    assert result['ready_for_replay']  # Background right censoring is disclosed, not a new gate.


def test_long_carry_in_is_a_warning_not_an_infinite_wait():
    data = evidence()
    for s in data[1]:
        for key in ('questions', 'first_pass_questions'):
            old = copy.deepcopy(s[key][0]); old.update(question_id=9, post_id=109, open_time=at(-500), scheduled_close_time=at(500000))
            s[key].append(old)
    result = R.convert(*data)
    assert result['ready_for_replay']
    assert 'background_or_unsupported_right_censored' in result['warnings']


def test_cross_venue_question_is_in_each_venue_and_once_overall(tmp_path):
    save(tmp_path/'source', evidence())
    record = R.register(tmp_path/'out', tmp_path/'source', 'venues', capacity(), deadline_policy='d116_margin10')
    trial = C.simulate(record)['trials'][0]
    assert trial['denominator_all'] == 1
    assert trial['per_venue']['main']['denominator_all'] == 1
    assert trial['per_venue']['minibench']['denominator_all'] == 1


def test_pair_id_conflict_does_not_leave_primary_registered(tmp_path):
    save(tmp_path/'source', evidence())
    cfg = evidence()[0]['replay_recipe']['derived_settings']
    cfg['sim_id'] = 'collision_fixed_grid'
    C.register(tmp_path/'out', cfg, [])
    with pytest.raises(ValueError, match='conflict'):
        R.register(tmp_path/'out', tmp_path/'source', 'collision', capacity(), deadline_policy='d116_margin10')
    assert len(read_complete(tmp_path/'out/coverage_sims.jsonl')) == 1
    assert not (tmp_path/'out/metadata_conversions.jsonl').exists()


def test_schema_strings_are_normalized_and_unknowns_retained():
    data = evidence()
    for s in data[1]:
        for key in ('questions', 'first_pass_questions'):
            s[key][0]['question_type'] = 'ARBITRARY BODY TEXT'
    result = R.convert(*data)
    assert 'ARBITRARY BODY TEXT' not in str(result)
    assert 'unknown_type' in result['question_issues']['10']


def test_repeated_mapping_and_timing_overrun_are_auditable():
    data = evidence()
    data[1][0]['questions'][0]['repeated_question_id'] = True
    data[2][-1]['at'] = at(2401)
    result = R.convert(*data)
    assert 'repeated_question_mapping' in result['issues']
    assert 'poll_timing_outside_bound' in result['issues']
    assert not result['ready_for_replay']


def test_runtime_upgrade_cannot_silently_replace_original_deadline_recipe(tmp_path, monkeypatch):
    save(tmp_path / 'source', evidence())
    with pytest.raises(ValueError, match='deadline policy differs'):
        R.register(tmp_path / 'out', tmp_path / 'source', 'original', capacity())
    assert not (tmp_path / 'out' / 'coverage_sims.jsonl').exists()
    legacy = copy.deepcopy(C.load_run_config())
    legacy['deadlines']['submit_lead_minutes'] = 5
    monkeypatch.setattr(C, 'load_run_config', lambda: legacy)
    rec = R.register(tmp_path / 'out', tmp_path / 'source', 'original', capacity())
    assert rec['frozen']['settings']['deadline_policy'] == 'd114_original'
    assert rec['frozen']['engine']['deadlines']['submit_lead_minutes'] == 5
    with pytest.raises(ValueError, match='deadline policy differs'):
        R.register(tmp_path / 'out', tmp_path / 'source', 'new', capacity(), deadline_policy='d116_margin10')
