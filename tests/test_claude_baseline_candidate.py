import copy
import hashlib
import json
from datetime import timedelta
from flab.timeutil import parse_iso, to_iso
from dataclasses import replace
from pathlib import Path

import pytest

import baseline as B
import claude_baseline_candidate as C
from flab.ids import hash_obj
from flab.jsonl import append_record, read_records
from test_claude_plugin_inventory import files, snapshot, NEXT


def prepared(tmp_path):
    _, marker, index, value, skill = files(tmp_path)
    old = snapshot(tmp_path, legacy=True)
    old['versions']['claude'] = '2.1.316'
    old.update(type='baseline_approved', record_id='base_synthetic', autoload_record='auto_synthetic')
    log = tmp_path / 'ledger/acceptance.jsonl'
    log.parent.mkdir()
    append_record(log, {'type': 'autoload_result', 'record_id': 'auto_synthetic', 'passed': True})
    append_record(log, old)
    saved = snapshot(tmp_path)
    saved['versions']['claude'] = '2.1.316'
    candidate = {'type':'claude_baseline_candidate','approved':False,'scope':['claude.plugins'],
        'snapshot':saved,'snapshot_sha256':hash_obj(saved),'replaces_baseline':old['record_id']}
    path=tmp_path/'data/acceptance_runs/synthetic/claude/evidence.json'
    path.parent.mkdir(parents=True)
    evidence={'player':'claude','tier2_equivalent_to_tier1':True,
              'baseline_before':saved,'baseline_after':saved}
    path.write_text(json.dumps(evidence))
    append_record(log,{'type':'acceptance_result','record_id':'isolation_synthetic','player':'claude',
        'line_pass':True,'cli_version':'2.1.316',
        'finished_at':to_iso(parse_iso(saved['taken_at'])+timedelta(seconds=1)),
        'evidence_path':str(path.relative_to(tmp_path)),
        'evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    return old, candidate, log, marker, skill


def apply(candidate, log, current=None, **extra):
    args = dict(expected_snapshot_sha256=candidate['snapshot_sha256'], reviewed_by='Synthetic reviewer',
                approval_reference='synthetic only; not Louis approval',
                current=B.Snapshot(**(current or candidate['snapshot'])), log=log,
                isolation_record_id='isolation_synthetic',project_root=log.parent.parent)
    args.update(extra)
    return C.approve(candidate, **args)


def test_only_frozen_reviewed_source_is_approved_and_others_preserved(tmp_path):
    old,c,log,marker,_ = prepared(tmp_path)
    marker.write_text(NEXT + ' ')
    live = snapshot(tmp_path);live['versions']['claude']='2.1.316'
    result = apply(c,log,live)
    assert result['sources']['claude.plugins'] == c['snapshot']['sources']['claude.plugins']
    assert result['sources']['claude.plugins'] != live['sources']['claude.plugins']
    for name in old['sources'].keys()-{'claude.plugins'}:
        assert result['sources'][name] == old['sources'][name]
    assert result['extra']['reviewed_snapshot_sha256'] == c['snapshot_sha256']
    assert B.compare(result,live)['decisions'] == dict.fromkeys(B.PLAYERS,'ok')


@pytest.mark.parametrize('change', ['file','version','candidate','digest','reviewer','reference','baseline','other-source','schema'])
def test_stale_tampered_wrong_version_or_unreviewed_candidate_never_appends_approval(tmp_path,change):
    old,c,log,marker,skill = prepared(tmp_path)
    current=copy.deepcopy(c['snapshot']);extra={}
    if change=='file':
        skill.write_text('changed after review');current=snapshot(tmp_path);current['versions']['claude']='2.1.316'
    elif change=='version':current['versions']['claude']='2.1.317'
    elif change=='candidate':c['snapshot']['sources']['claude.plugins']['content_hash']='changed'
    elif change=='digest':extra['expected_snapshot_sha256']='0'*64
    elif change=='reviewer':extra['reviewed_by']=' '
    elif change=='reference':extra['approval_reference']=''
    elif change=='baseline':append_record(log,{**old,'record_id':'newer_baseline'})
    elif change=='schema':current['sources']['claude.plugins']['kind']='dir'
    else:
        c['snapshot']['sources']['extra']={'exists':True,'hash':'x','kind':'file','category':'content','player':'claude'}
        c['snapshot_sha256']=hash_obj(c['snapshot']);current=copy.deepcopy(c['snapshot'])
    count=len(list(read_records(log)))
    with pytest.raises(ValueError):apply(c,log,current,**extra)
    assert len(list(read_records(log)))==count


@pytest.mark.parametrize('change',['stale','failed','incomplete','mismatch','digest','content','missing'])
def test_no_baseline_approval_before_matching_new_isolation_pass(tmp_path,change):
    old,c,log,_,_=prepared(tmp_path)
    record=copy.deepcopy(list(read_records(log))[-1]);extra={}
    if change=='missing':extra['isolation_record_id']=''
    elif change=='incomplete':append_record(log,{'type':'acceptance_call_started','player':'claude'})
    else:
        if change=='stale':record['finished_at']='2020-01-01T00:00:00Z'
        elif change=='failed':record['line_pass']=False
        elif change=='mismatch':record['record_id']='wrong'
        elif change=='digest':record['evidence_sha256']='0'*64
        elif change=='content':
            path=tmp_path/record['evidence_path'];ev=json.loads(path.read_text())
            ev['baseline_after']['sources']['claude.plugins']['content_hash']='changed'
            path.write_text(json.dumps(ev));record['evidence_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        append_record(log,record)
    count=len(list(read_records(log)))
    with pytest.raises(ValueError):apply(c,log,**extra)
    assert len(list(read_records(log)))==count
    assert B.latest_approved(log)['record_id']==old['record_id']
    import forecast as F
    pf=F.Preflight('claude')
    F.check_baseline(pf,B.latest_approved(log),c['snapshot'],allow_unapproved=False)
    assert not pf.ok  # The old STOP gate remains until fresh acceptance and scoped approval.
