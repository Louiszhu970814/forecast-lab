"""Apply only a specific, manually reviewed Claude plugin baseline candidate.

This helper never obtains or infers human approval. The coordinator must supply
an explicit review reference after Louis has accepted the identified candidate.
The saved candidate is approved, never a fresh unreviewed filesystem snapshot.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import baseline as B
from flab.ids import hash_obj
from flab.ledger import transaction, read_complete
from flab.timeutil import parse_iso
from flab.workdir import PROJECT_ROOT

SCOPE = 'claude.plugins'
VERSION = 'claude_baseline_candidate_v2'


def approve(candidate: dict, *, expected_snapshot_sha256: str, reviewed_by: str,
            approval_reference: str, current: B.Snapshot, isolation_record_id: str,
            log: Path = B.ACCEPTANCE_LOG, project_root: Path = PROJECT_ROOT) -> dict:
    if not isinstance(reviewed_by, str) or not reviewed_by.strip() or \
            not isinstance(approval_reference, str) or not approval_reference.strip():
        raise ValueError('explicit human reviewer and review reference required')
    if candidate.get('type') != 'claude_baseline_candidate' or candidate.get('approved') is not False or \
            candidate.get('scope') != [SCOPE]:
        raise ValueError('not a pending, scoped Claude candidate')
    saved = candidate.get('snapshot')
    if not isinstance(saved, dict) or hash_obj(saved) != expected_snapshot_sha256 or \
            candidate.get('snapshot_sha256') != expected_snapshot_sha256:
        raise ValueError('candidate differs from the specifically reviewed snapshot')
    live = current.to_record()
    for snap in (saved, live):
        if B.blockers(snap, player='claude') or snap['versions'].get('claude') != '2.1.316':
            raise ValueError('candidate/current snapshot is blocked or not the pinned CLI')
        source = snap['sources'].get(SCOPE, {})
        if source.get('kind') != 'claude_plugin_dir' or \
                source.get('inventory_version') != 'claude_plugin_inventory_v2' or \
                source.get('player') != 'claude' or source.get('category') != B.CONTENT:
            raise ValueError('candidate/current plugin inventory schema differs')
    if any(c['category'] != B.INFORMATIONAL and c['player'] in ('claude', 'common')
           for c in B.compare(saved, live)['changes']):
        raise ValueError('protected content or CLI changed since candidate review')
    with transaction(log.parent):
        old = B.latest_approved(log)
        if old is None or old['record_id'] != candidate.get('replaces_baseline'):
            raise ValueError('approved baseline changed since candidate preparation')
        changes = B.compare(old, saved)['changes']
        if any(c['category'] != B.INFORMATIONAL and c['source'] != SCOPE
               and c['player'] in ('claude', 'common') for c in changes):
            raise ValueError('candidate would approve a different protected source or CLI')
        rows = read_complete(log)
        results = [(i, r) for i, r in enumerate(rows) if r.get('type') == 'acceptance_result'
                   and r.get('player') == 'claude']
        if not results:
            raise ValueError('fresh Claude isolation acceptance is required before approval')
        index, result = results[-1]
        if not isolation_record_id or result.get('record_id') != isolation_record_id or \
                result.get('line_pass') is not True or result.get('cli_version') != '2.1.316' or \
                parse_iso(result['finished_at']) < parse_iso(saved['taken_at']):
            raise ValueError('latest isolation result is not a fresh pass for this candidate')
        if any(r.get('type') == 'acceptance_call_started' and r.get('player') == 'claude'
               and r.get('source') != 'autoload' for r in rows[index + 1:]):
            raise ValueError('newer Claude isolation acceptance is incomplete')
        evidence_path = (project_root / result.get('evidence_path', '')).resolve()
        expected_root = (project_root / 'data/acceptance_runs').resolve()
        if not evidence_path.is_relative_to(expected_root) or evidence_path.name != 'evidence.json':
            raise ValueError('unexpected isolation evidence path')
        raw = evidence_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != result.get('evidence_sha256'):
            raise ValueError('isolation evidence digest differs')
        evidence = json.loads(raw)
        if evidence.get('player') != 'claude' or evidence.get('tier2_equivalent_to_tier1') is not True:
            raise ValueError('isolation evidence is not the Claude tool-profile acceptance')
        for key in ('baseline_before', 'baseline_after'):
            observed = evidence.get(key, {})
            source = observed.get('sources', {}).get(SCOPE, {})
            if observed.get('versions', {}).get('claude') != '2.1.316' or source.get('error') or \
                    source.get('kind') != 'claude_plugin_dir' or \
                    B._fingerprint(source)[0] != B._fingerprint(saved['sources'][SCOPE])[0]:
                raise ValueError('isolation did not test the reviewed plugin content')
        # Preserve all other approved sources byte-for-byte, including informational
        # fields. Use the saved reviewed source, never live bytes read during approval.
        sources = copy.deepcopy(old['sources'])
        sources[SCOPE] = copy.deepcopy(saved['sources'][SCOPE])
        scoped = B.Snapshot(taken_at=saved['taken_at'], sources=sources,
            versions=copy.deepcopy(old['versions']), gemini_credits=old['gemini_credits'],
            extra={'version': VERSION, 'scope': [SCOPE], 'previous_baseline': old['record_id'],
                   'reviewed_snapshot_sha256': expected_snapshot_sha256,
                   'approval_reference': approval_reference,
                   'isolation_acceptance_record': isolation_record_id})
        if B.compare(scoped.to_record(), live)['decisions']['claude'] != 'ok':
            raise ValueError('scoped candidate does not match current protected state')
        return B.approve(scoped, reviewed_by, old['autoload_record'], log=log, player='claude')
