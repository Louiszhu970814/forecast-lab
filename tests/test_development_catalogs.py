"""D144 catalog drift does not grant runtime capabilities or approve new evidence."""
import copy
import json

import pytest

import baseline as B
import forecast as F
from flab import auto_reaccept_policy as P


CASES = [
    ('claude.skills', 'claude'), ('claude.skills_synced', 'claude'),
    ('claude.plugins', 'claude'), ('claude.marketplace_index', 'claude'),
    ('claude.plugin_state', 'claude'), ('claude.agents', 'claude'), ('claude.commands', 'claude'),
    ('codex.skills', 'gpt'), ('codex.plugins', 'gpt'), ('codex.agents', 'gpt'),
    ('agy.skills', 'gemini'), ('agy.plugins', 'gemini'), ('agy.agents', 'gemini'),
]


def fixture(sid, player):
    src = next(s for s in B.default_sources({}) if s.source_id == sid)
    row = dict(player=player, kind=src.kind, category=src.category, path=src.path,
               exists=True, hash='old', content_hash='old', general_hash='old')
    old = dict(record_id='historical-baseline', sources={sid:row},
               versions=dict.fromkeys(B.PLAYERS, 'known'), gemini_credits=0)
    return old, copy.deepcopy(old)


@pytest.mark.parametrize('sid,player', CASES)
@pytest.mark.parametrize('operation', ['modify', 'create', 'remove'])
def test_development_catalog_lifecycle_does_not_pause_or_create_waiver(sid, player, operation):
    old, current = fixture(sid, player)
    original = json.dumps(old, sort_keys=True)
    if operation == 'create':
        old['sources'][sid]['exists'] = False
        original = json.dumps(old, sort_keys=True)
    elif operation == 'remove':
        current['sources'][sid]['exists'] = False
    else:
        current['sources'][sid].update(hash='new', content_hash='new', general_hash='new')
    changes = B.compare(old, current)
    assert changes['decisions'] == dict.fromkeys(B.PLAYERS, 'ok')
    assert changes['changes'] and all(c['category'] == B.INFORMATIONAL for c in changes['changes'])
    pf = F.Preflight(player)
    F.check_baseline(pf, old, current, allow_unapproved=False)
    assert pf.ok and not pf.waivers
    assert json.dumps(old, sort_keys=True) == original


@pytest.mark.parametrize('sid,player', CASES)
def test_catalog_errors_and_inventory_definition_changes_still_block(sid, player):
    old, current = fixture(sid, player)
    current['sources'][sid]['error'] = 'directory could not be fully read'
    assert B.compare(old, current)['decisions'][player] == 'stop'
    for key in ('path', 'kind'):
        changed = copy.deepcopy(old)
        changed['sources'][sid][key] = 'different'
        assert B.compare(old, changed)['decisions'][player] == 'stop'
    removed = copy.deepcopy(old)
    removed['sources'].pop(sid)
    assert B.compare(old, removed)['decisions'][player] == 'stop'


@pytest.mark.parametrize('sid,player', [
    ('codex.global_instructions', 'gpt'), ('codex.memories', 'gpt'),
    ('codex.hooks', 'gpt'), ('codex.managed_config', 'gpt'),
    ('claude.global_instructions', 'claude'), ('claude.hooks', 'claude'),
    ('claude.managed_mcp', 'claude'), ('agy.rules', 'gemini'), ('agy.mcp', 'gemini'),
    ('agy.hooks', 'gemini'),
])
def test_unrelated_protected_content_keeps_stop_policy(sid, player):
    old, current = fixture(sid, player)
    current['sources'][sid]['hash'] = 'new'
    assert B.compare(old, current)['decisions'][player] == 'stop'


def test_skills_drift_has_no_observe_candidate_and_does_not_change_exact_binding():
    old, current = fixture('codex.skills', 'gpt')
    current['sources']['codex.skills']['hash'] = 'new'
    result = P.candidate('gpt', old, current, context={
        'production_evidence_ok': True, 'identity': {'path':'synthetic'},
    }, scans={})
    assert result['kind'] == 'none' and result['scope'] == []
    assert result['model_calls'] == 0 and not result['baseline_approved']
    assert not B._same_protected(old['sources']['codex.skills'], current['sources']['codex.skills'])


def test_mixed_skill_and_hook_changes_still_stop():
    old, current = fixture('codex.skills', 'gpt')
    hook_old, hook_current = fixture('codex.hooks', 'gpt')
    old['sources'].update(hook_old['sources'])
    current['sources'].update(hook_current['sources'])
    for r in current['sources'].values():
        r['hash'] = 'new'
    assert B.compare(old, current)['decisions']['gpt'] == 'stop'


def test_acceptance_freezes_content_and_inventory_definition():
    from acceptance import baseline_content_changes
    old, current = fixture('codex.skills', 'gpt')
    current['sources']['codex.skills']['hash'] = 'new'
    assert baseline_content_changes(old, current)
    current = copy.deepcopy(old)
    current['sources']['codex.skills']['path'] = '/different'
    assert baseline_content_changes(old, current)[0]['what'] == 'source definition changed'
