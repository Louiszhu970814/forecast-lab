"""D124 observe-only baseline candidate inventory, after the normal cycle releases its locks.

This release cannot run acceptance, approve a baseline, reclassify a source or clear a stop.
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import shutil
import signal

import autoload
import baseline as B
import forecast as F
import live_window as W
import quota
from flab import auto_reaccept_policy as P
from flab.config import load_run_config,load_submit_mode
from flab.jsonl import append_record
from flab.ledger import transaction,read_complete
from flab.timeutil import utc_now,to_iso
from flab.workdir import PROJECT_ROOT

MAX_TICK_S=120


class ObservationDeadline(BaseException):
    pass


def identity(player, project=PROJECT_ROOT):
    name={'claude':'claude','gemini':'agy','gpt':'codex'}[player]
    binary=shutil.which(name)
    if not binary:return None
    path=Path(binary)
    if player=='claude' and path!=project/'launchd/bin/claude':return None
    result={'path':str(path),'sha256':B._hash_file(path)}
    if player=='claude':result['pin_sha256']=B._hash_file(project/'data/cli_pins/claude/2.1.316')
    return result


def context(player,current,root,*,cfg=None):
    cfg=cfg or load_run_config()
    fingerprint=F.runtime_fingerprint(player)
    results=F._acceptance_records(root/'acceptance.jsonl',player)
    proofs=[];valid=True
    for tier in (1,2):
        pf=F.Preflight(player,tier=tier);pf.cli_version=current['versions'].get(player)
        F.check_acceptance(pf,results,fingerprint,production=cfg['players'][player][f'tier{tier}'])
        valid=valid and pf.ok
        proofs.append(pf.production_record_id)
    catalog=None;catalog_changed=False
    if player=='gpt':
        adapter=importlib.import_module('adapters.gpt')
        models={cfg['players'][player][f'tier{t}']['model'] for t in (1,2)}
        # D118 has one model; different future models need a separately reviewed binding.
        if len(models)!=1:valid=False
        model=cfg['players'][player]['tier1']['model']
        catalog=adapter.catalog_entry_sha256(adapter.restricted_catalog(adapter.load_catalog(),model),model)
        catalog_changed=bool(results) and catalog!=results[-1].get('catalog_entry_sha256')
    return {'identity':identity(player),'runtime_fingerprint':fingerprint,'judge_version':F.J.JUDGE_VERSION,
            'models':{f'tier{t}':{k:cfg['players'][player][f'tier{t}'][k] for k in ('model','effort')} for t in (1,2)},
            'production_evidence_ok':valid,'production_record_ids':proofs,
            'catalog_sha256':catalog,'catalog_changed':catalog_changed}


def observe_line(player,root,*,snapshot=None,context_fn=context,home=None):
    home=home or Path.home()
    with F.Lock(root/f'forecast.{player}.lock'):
        if autoload.unclosed(root/'acceptance.jsonl'):
            return {'player':player,'status':'maintenance_unclosed'}
        old=B.latest_approved(root/'acceptance.jsonl')
        if not old:return {'player':player,'status':'no_approved_baseline'}
        if snapshot:
            current=snapshot()
        else:
            observed=quota.snapshot(timeout_s=20) if player=='gemini' else None
            credits=observed.remaining_credits if observed and quota.observation_fresh(observed,utc_now()) else None
            current=B.take_snapshot(home=home,gemini_credits=credits).to_record()
        info=context_fn(player,current,root)
        changes=[c for c in B.compare(old,current)['changes'] if c['player'] in (player,'common')
                 and c['category']!=B.INFORMATIONAL]
        inventory={s.source_id:s for s in B.default_sources()}
        scans={sid:P.scan(inventory[sid],current['sources'][sid],home=home)
               for sid in sorted({c['source'] for c in changes})
               if sid in P.ALLOWLIST and sid in inventory and sid in current['sources']}
        record=P.candidate(player,old,current,context=info,scans=scans)
        if record['kind']=='none':return {'player':player,'status':'no_candidate'}
        with transaction(root):
            # Inventory is an observation only, but do not record a stale parent as current.
            latest=B.latest_approved(root/'acceptance.jsonl')
            if not latest or latest.get('record_id')!=old.get('record_id'):
                return {'player':player,'status':'superseded'}
            log=root/'auto_reaccept.jsonl'
            if not any(r.get('candidate_sha256')==record['candidate_sha256'] for r in read_complete(log)):
                append_record(log,{**record,'at':to_iso(utc_now())})
        return {k:record[k] for k in ('player','kind','candidate_sha256','format_eligible','reasons','model_calls')}


def tick(root=PROJECT_ROOT/'data',*,observe=observe_line):
    if P.MODE!='observe':raise ValueError('this release supports observe only')
    if (root.parent/'config/STOP_SUBMIT').exists():return {'status':'stopped','mode':P.MODE}
    result={'type':'auto_reaccept_tick','version':P.VERSION,'mode':P.MODE,'model_calls':0,'players':[]}
    try:
        with F.Lock(root/'auto_reaccept.lock'):
            for player in B.PLAYERS:
                try:result['players'].append(observe(player,root))
                except F.PreflightError:result['players'].append({'player':player,'status':'busy'})
                except (OSError,ValueError,TypeError,KeyError):
                    result['players'].append({'player':player,'status':'observation_unavailable'})
    except F.PreflightError:return {'status':'busy','mode':P.MODE}
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('tick',))
    parser.parse_args(argv)
    # No observations after D118 expires, and no independent scheduling policy.
    mode=load_submit_mode();campaign=json.loads((PROJECT_ROOT/'config/cycle_campaign.json').read_text())
    try:W.require_active(mode,campaign,PROJECT_ROOT/'config',utc_now())
    except (OSError,ValueError,TypeError,KeyError):return 0
    old=signal.getsignal(signal.SIGALRM)
    def expired(signum,frame):raise ObservationDeadline()
    signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,MAX_TICK_S)
    try:result=tick()
    except ObservationDeadline:result={'status':'timeout','mode':P.MODE,'model_calls':0}
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old)
    print(json.dumps(result,ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
