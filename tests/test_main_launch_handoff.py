"""D137: preserve the complete D105 -> D118 -> main allowance and history."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

import budget_handoff as H
import continuous as C
from flab.ids import hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete
from flab.timeutil import parse_iso, to_iso
from test_budget_handoff import campaigns, finished, reserve
from test_forecast import NOW

LATER = NOW + timedelta(hours=1)


def chain(root, *, cap=40):
    original, mini = campaigns(root, cap=cap)
    C.register(root, mini, now=lambda: NOW)
    main = copy.deepcopy(mini)
    main.update(version="continuous_v10", campaign_id='main-join', venues=['main', 'minibench'],
                budget_window_start=to_iso(LATER))
    main['budget_handoff'] = {
        'version': H.VERSION, 'source_campaign_id': mini['campaign_id'],
        'source_sha256': hash_obj(mini), 'accounting_start': original['budget_window_start'],
        'authorization': 'PLAN D137',
    }
    return original, mini, main


def test_second_handoff_keeps_prefix_root_clock_and_single_allowance(tmp_path):
    original, mini, main = chain(tmp_path, cap=2)
    before = (tmp_path/'continuous_campaigns.jsonl').read_bytes()
    finished(tmp_path, 'root-call', NOW-timedelta(hours=1))
    finished(tmp_path, 'mini-call', NOW+timedelta(minutes=1))
    C.register(tmp_path, main, now=lambda: LATER)
    after = (tmp_path/'continuous_campaigns.jsonl').read_bytes()
    assert after.startswith(before)
    C.register(tmp_path, main, now=lambda: LATER+timedelta(minutes=1))
    assert (tmp_path/'continuous_campaigns.jsonl').read_bytes() == after
    assert H.accounting_start(tmp_path, main) == parse_iso(original['budget_window_start'])
    result = reserve(tmp_path, main, now=LATER)
    assert result['weekly_attempts_used'] == 2
    assert not result['allowed']
    for old in (original, mini):
        with pytest.raises(ValueError, match='superseded'):
            reserve(tmp_path, old, now=LATER)


def test_unresolved_original_attempt_still_blocks_main(tmp_path):
    _, _, main = chain(tmp_path)
    finished(tmp_path, 'unknown-old', NOW-timedelta(hours=1), result=False)
    C.register(tmp_path, main, now=lambda: LATER)
    result = reserve(tmp_path, main, now=LATER)
    assert not result['allowed'] and result['reason'] == 'unresolved_or_running_attempt'


@pytest.mark.parametrize('change', [
    'root-start', 'extend-end', 'cap', 'run-hash', 'quota-hash', 'reserve',
    'authorization', 'source-hash', 'self', 'extra', 'rollover', 'trial-venue',
    'future-source', 'source-end', 'drop-handoff',
])
def test_invalid_second_handoff_does_not_register_or_spend(tmp_path, change):
    original, mini, main = chain(tmp_path)
    h = main['budget_handoff']
    if change == 'root-start': h['accounting_start'] = mini['budget_window_start']
    elif change == 'extend-end': main['budget_window_end'] = to_iso(parse_iso(mini['budget_window_end'])+timedelta(seconds=1))
    elif change == 'cap': main['weekly_attempt_caps']['claude'] += 1
    elif change == 'run-hash': main['run_config_sha256'] = 'different'
    elif change == 'quota-hash': main['quota_policy_sha256'] = 'different'
    elif change == 'reserve': main['repair_reserve'] += 1
    elif change == 'authorization': h['authorization'] = 'PLAN D118'
    elif change == 'source-hash': h['source_sha256'] = 'different'
    elif change == 'self': main['campaign_id'] = mini['campaign_id']
    elif change == 'extra': h['reset'] = True
    elif change == 'rollover': main['rollover'] = {'version': C.ROLLOVER_VERSION, 'windows': 2}
    elif change == 'trial-venue': main['venues'] = ['bot_testing_area']
    elif change == 'future-source': main['budget_window_start'] = original['budget_window_start']
    elif change == 'source-end': main['budget_window_start'] = mini['budget_window_end']
    else: main.pop('budget_handoff')
    before = (tmp_path/'continuous_campaigns.jsonl').read_bytes()
    with pytest.raises(ValueError): C.register(tmp_path, main, now=lambda: LATER)
    with pytest.raises(ValueError): reserve(tmp_path, main, now=LATER)
    assert (tmp_path/'continuous_campaigns.jsonl').read_bytes() == before
    assert not read_complete(tmp_path/'budget_events.jsonl')


def test_third_link_cannot_extend_the_same_budget_again(tmp_path):
    _, _, main = chain(tmp_path)
    C.register(tmp_path, main, now=lambda: LATER)
    third = copy.deepcopy(main)
    third.update(campaign_id='third', budget_window_start=to_iso(LATER+timedelta(hours=1)))
    third['budget_handoff'].update(source_campaign_id=main['campaign_id'], source_sha256=hash_obj(main))
    with pytest.raises(ValueError, match='two links'):
        C.register(tmp_path, third, now=lambda: LATER+timedelta(hours=1))


def test_v2_fork_is_atomic_and_old_policy_cannot_restart(tmp_path):
    _, mini, main = chain(tmp_path)
    def start(i):
        candidate = {**main, 'campaign_id': 'a' if i % 2 else 'b'}
        try:
            C.register(tmp_path, candidate, now=lambda: LATER)
            return candidate['campaign_id']
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(start, range(8)))
    assert len({r for r in results if r is not None}) == 1
    assert len(read_complete(tmp_path/'continuous_campaigns.jsonl')) == 3
    with pytest.raises(ValueError, match='superseded'):
        C.register(tmp_path, mini, now=lambda: LATER)


def test_expired_source_remains_blocking(tmp_path):
    _, mini, main = chain(tmp_path)
    # An unrelated registration must not be mistaken for an allowed ancestor.
    unrelated = copy.deepcopy(mini)
    unrelated.pop('budget_handoff')
    unrelated.update(campaign_id='unrelated', budget_window_start=mini['budget_window_end'],
                     budget_window_end=to_iso(parse_iso(mini['budget_window_end'])+timedelta(days=1)))
    C.register(tmp_path, unrelated, now=lambda: parse_iso(mini['budget_window_end']))
    with pytest.raises(ValueError, match='active'):
        C.register(tmp_path, main, now=lambda: parse_iso(mini['budget_window_end']))
    assert len(read_complete(tmp_path/'continuous_campaigns.jsonl')) == 3


def test_nonancestor_overlap_is_not_exempted(tmp_path):
    _, mini, main = chain(tmp_path)
    unrelated = copy.deepcopy(mini)
    unrelated.pop('budget_handoff')
    unrelated.update(campaign_id='unrelated', budget_window_start=to_iso(LATER),
                     budget_window_end=to_iso(LATER+timedelta(hours=1)))
    # Corrupt/foreign history cannot acquire the ancestor overlap exemption.
    append_record(tmp_path/'continuous_campaigns.jsonl', {
        'campaign': unrelated, 'sha256': hash_obj(unrelated), 'at': to_iso(LATER)})
    before = (tmp_path/'continuous_campaigns.jsonl').read_bytes()
    with pytest.raises(ValueError, match='overlap'):
        C.register(tmp_path, main, now=lambda: LATER)
    assert (tmp_path/'continuous_campaigns.jsonl').read_bytes() == before


@pytest.mark.parametrize('rehash', [False, True])
def test_root_ancestor_hash_cannot_be_rewritten(tmp_path, rehash):
    _, _, main = chain(tmp_path)
    path = tmp_path/'continuous_campaigns.jsonl'
    rows = read_complete(path)
    rows[0]['campaign']['repair_reserve'] += 1
    if rehash: rows[0]['sha256'] = hash_obj(rows[0]['campaign'])
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    before = path.read_bytes()
    with pytest.raises(ValueError): C.register(tmp_path, main, now=lambda: LATER)
    assert path.read_bytes() == before


def test_registered_v2_runtime_cannot_drop_handoff_or_change_venues(tmp_path):
    _, _, main = chain(tmp_path)
    C.register(tmp_path, main, now=lambda: LATER)
    for action in ('drop', 'venue'):
        changed = copy.deepcopy(main)
        if action == 'drop': changed.pop('budget_handoff')
        else: changed['venues'] = ['minibench']
        with pytest.raises(ValueError): reserve(tmp_path, changed, now=LATER)
