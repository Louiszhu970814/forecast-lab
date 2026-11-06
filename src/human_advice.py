"""Manually invoked human-tournament inventory and bounded advisory research.

No human token, scheduler or submission transport. Final English reports and
Gemini-written comments remain a separate, manually requested writing step (D134).
"""
from __future__ import annotations

import argparse
import importlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import advise as A
import aggregate
import budget_handoff as H
import continuous as C
import forecast as F
import validate as V
from flab.advisory_usage import usage
from flab.config import load_tournaments, load_run_config, load_quota_policy
from flab.envfile import get_secret
from flab.ids import new_id, hash_obj
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso, utc_now
from flab.workdir import PROJECT_ROOT
from metaculus import MetaculusClient
from poll_questions import (poll, TournamentIndex, snapshots_from_post, is_synced_copy,
                            record_snapshots, latest_snapshots)
from question_types import is_supported

VERSION = 'advisory_v2'


def configuration(project=PROJECT_ROOT):
    cfg = json.loads((project/'config/human_tournaments.json').read_text())
    if set(cfg) != {'version','tournaments','max_questions_per_request','default_top','automatic_submission'} \
            or cfg['version'] != 'human_tournaments_v1' or cfg['automatic_submission'] is not False \
            or cfg['max_questions_per_request'] != 3 or cfg['default_top'] != 3:
        raise ValueError('invalid human advisory configuration')
    entries = cfg['tournaments']
    if not isinstance(entries,list) or not entries or any(not isinstance(e,dict) or
            set(e) != {'id','slug','name'} or type(e['id']) is not int or e['id'] <= 0 or
            any(not isinstance(e[k],str) or not e[k].strip() for k in ('slug','name')) for e in entries) \
            or len({e['id'] for e in entries}) != len(entries):
        raise ValueError('invalid human tournament identity')
    bot = load_tournaments(project/'config')['tournaments']
    if {e['id'] for e in entries} & {e.get('id') for k,e in bot.items() if k in ('main','minibench','market_pulse')}:
        raise ValueError('human and bot tournament identities overlap')
    return cfg


def exclusion_index(client, root, campaign, *, project=PROJECT_ROOT, now=utc_now):
    config = C.current_tournaments(client, configured=load_tournaments(project/'config'), now=now)
    ids = set(config['excluded_project_ids'])
    market = config['tournaments'].get('market_pulse',{}).get('id')
    if market is not None:
        ids.add(market)
    records = [poll(client,pid,metadata_only=True,poll_log=root/'exclusion_polls.jsonl') for pid in sorted(ids)]
    return TournamentIndex.from_polls(records), records


def safe_snapshot(post, qid, tournament_id, index, *, now, cfg):
    matches = [s for s in snapshots_from_post(post, 'human', allow_groups=True) if s['question_id']==qid]
    if len(matches)!=1:
        raise ValueError('missing or ambiguous requested child')
    snap = matches[0]
    if tournament_id not in snap['project_ids'] or not is_supported(snap['question_type']) \
            or snap['status'] != 'open' or F.eligible(snap,cfg,now):
        raise ValueError('requested human question is not eligible')
    if snap.get('scaling_status') == 'unsupported_scaling':
        raise ValueError('unsupported scaling')
    if is_synced_copy(snap,index):
        raise ValueError('overlaps open/upcoming bot tournament')
    snap['tournament_id'] = tournament_id
    return snap


def bot_priority(shared, records, *, now, horizon_s):
    completed = {r['question_id'] for r in read_complete(shared/'submissions.jsonl')
                 if r.get('status')=='comment_ok'}
    completed |= {r['question_id'] for r in read_complete(shared/'aggregates.jsonl')
                  if r.get('completion_status')=='complete_local' and r.get('status')!='ready'}
    import discovery as D
    import market_policy as MP
    market = [t for r in records if r['tournament_id'] == MP.PROJECT_ID for t in r['timing']]
    actionable = D.actionable_questions(shared, market, now)
    for r in records:
        for t in r['timing']:
            if t['question_id'] in completed:
                continue
            if (t['status']=='open' and (r['tournament_id'] != MP.PROJECT_ID or t['question_id'] in actionable)) or (t['status']=='upcoming' and t.get('open_time') and
                    parse_iso(t['open_time'])<=now+timedelta(seconds=horizon_s)):
                raise ValueError('bot has open or imminent work; advisory yields')


def inventory(client, root, *, project=PROJECT_ROOT, now=None, top=3):
    observed = now or utc_now()
    if type(top) is not int or not 1<=top<=20:
        raise ValueError('top must be between 1 and 20')
    cfg = configuration(project)
    campaign = json.loads((project/'config/cycle_campaign.json').read_text())
    index,_ = exclusion_index(client,root,campaign,project=project,now=lambda:observed)
    admitted,finished = usage(project/'data')
    rows, seen, posts, groups, skipped = [], set(), set(), set(), []
    for tournament in cfg['tournaments']:
        for post in client.list_posts(tournament['id'],forecast_type=None,statuses=['open']):
            if post.get('conditional') is not None or post.get('notebook') is not None:
                skipped.append({'post_id':post.get('id'),'reason':'unsupported_post_structure'});continue
            for snap in snapshots_from_post(post,'human',allow_groups=True):
                qid=snap['question_id']
                if qid in seen or snap['status']!='open':continue
                seen.add(qid)
                if tournament['id'] not in snap['project_ids']:
                    raise ValueError('human tournament membership unavailable')
                if is_synced_copy(snap,index):
                    skipped.append({'post_id':snap['post_id'],'question_id':qid,'reason':'bot_overlap'});continue
                close=parse_iso(snap['scheduled_close_time'])
                if close<=observed:continue
                prior=[r for r in admitted.values() if r['question_id']==qid]
                pending=any(r['attempt_id'] not in finished for r in prior)
                state='unresolved' if pending else 'researched' if prior else 'not_researched'
                hashes={r.get('content_hash') for r in prior if r.get('content_hash')}
                update='content_changed' if hashes and snap['content_hash'] not in hashes else 'not_assessed' if prior else 'none'
                posts.add(snap['post_id'])
                if post.get('group_of_questions') is not None:groups.add(snap['post_id'])
                rows.append({'tournament':tournament['name'],'tournament_id':tournament['id'],
                    'post_id':snap['post_id'],'question_id':qid,'title':snap['title'],
                    'url':f"https://www.metaculus.com/questions/{snap['post_id']}/?sub-question={qid}",
                    'close_utc':to_iso(close),'close_taipei':close.astimezone(ZoneInfo('Asia/Taipei')).isoformat(),
                    'seconds_remaining':(close-observed).total_seconds(),'question_type':snap['question_type'],
                    'supported':is_supported(snap['question_type']) and snap.get('scaling_status')!='unsupported_scaling',
                    'research_state':state,'update_review':update,'human_submission':'unknown'})
    rows.sort(key=lambda r:(r['close_utc'],r['question_id']))
    result={'type':'human_inventory','version':VERSION,'at':to_iso(observed),'open_posts':len(posts),
            'open_group_posts':len(groups),'open_child_questions':len(rows),
            'supported_child_questions':sum(r['supported'] for r in rows),'top':rows[:top],
            'questions':rows,'skipped':skipped,'model_calls':0,'submissions':0}
    append_record(root/'inventory.jsonl',result)
    return result


def register_admission(shared,b,root,request,protected,*,spec,snap):
    with transaction(shared):
        admitted,finished=usage(shared)
        own=[r for r in admitted.values() if r.get('request_id')==spec['request_id']]
        if len(own)>=3 or any(r['player']==request['player'] for r in own):
            raise F.QuestionDone('manual request already consumed; no automatic rerun')
        prior=[r for r in admitted.values() if r['question_id']==request['question_id'] and r['player']==request['player']]
        if any(r['attempt_id'] not in finished for r in prior):
            raise F.QuestionDone('unresolved prior advisory attempt')
        if prior and not spec.get('update_reason'):
            raise F.QuestionDone('already researched; explicit update reason required')
        decision=b.reserve(request,pending_tier1=0,expected_new=0,repair_reserve=0,advisory_protected=protected)
        if not decision['allowed']:raise F.QuestionDone('budget refused: '+str(decision['reason']))
        append_record(shared/'advisory_usage.jsonl',{'type':'advisory_admitted','version':VERSION,
            'at':to_iso(utc_now()),'attempt_id':request['attempt_id'],'player':request['player'],
            'question_id':request['question_id'],'batch':root.name,'batch_path':str(root.resolve()),
            'campaign_id':b.campaign['campaign_id'],'protected_attempts':protected,'decision_id':decision['decision_id'],
            'request_id':spec['request_id'],'content_hash':snap['content_hash'],'update_reason':spec.get('update_reason')})
        return decision


def run(root, post_id, question_id, tournament_id, *, update_reason=None, project=PROJECT_ROOT):
    root=A.batch_path(root,project);shared=project/'data'
    if tournament_id not in {e['id'] for e in configuration(project)['tournaments']}:
        raise ValueError('human tournament is not configured')
    if any(type(v) is not int or v<=0 for v in (post_id,question_id)) or \
            (update_reason is not None and (not isinstance(update_reason,str) or not update_reason.strip())):
        raise ValueError('explicit question identity and nonempty update reason required')
    with ExitStack() as locks:
        A.lock_all(shared,locks)
        campaign=json.loads((project/'config/cycle_campaign.json').read_text())
        cfg,policy=load_run_config(),load_quota_policy()
        if hash_obj(cfg)!=campaign['run_config_sha256'] or hash_obj(policy)!=campaign['quota_policy_sha256']:
            raise ValueError('fixed configuration mismatch')
        if not parse_iso(campaign['budget_window_start'])<=utc_now()<parse_iso(campaign['budget_window_end']):
            raise ValueError('active shared budget required')
        H.accounting_start(shared,campaign)
        admitted,finished=usage(shared)
        if set(admitted)!=set(finished):raise ValueError('unresolved advisory attempt; recover first')
        if any(r['question_id']==question_id for r in admitted.values()) and not update_reason:
            raise ValueError('already researched; explicit update reason required')
        if read_complete(root/'requests.jsonl'):raise ValueError('batch already used; create an explicit new request')
        starts=read_complete(shared/'forecast_attempt_starts.jsonl')
        done={r['attempt_id'] for r in read_complete(shared/'forecasts.jsonl')}
        if any(r['attempt_id'] not in done for r in starts):raise ValueError('unresolved bot attempt')
        if (project/'config/STOP_SUBMIT').exists():raise ValueError('project stopped')
        adapters={p:importlib.import_module('adapters.'+p) for p in A.PLAYERS}
        pfs={p:F.preflight(p,cfg=cfg,adapter=adapters[p],continuous=True) for p in A.PLAYERS}
        if any(not pf.ok for pf in pfs.values()):
            return {'status':'preflight_blocked','players':{p:pf.summary() for p,pf in pfs.items()}}
        for p in A.PLAYERS:
            if F.line_stopped(shared/'line_status.jsonl',p,shared/'forecasts.jsonl',now=utc_now()) or \
                    F.leak_count(shared/'forecasts.jsonl',p,utc_now(),resumed_at=F.latest_resume(shared/'line_status.jsonl',p))>=F.LEAK_STOP_COUNT:
                raise ValueError('production line stopped')
        for row in read_complete(shared/'forecasts.jsonl'):
            if row.get('error_type')=='quota_exhausted':
                until=row.get('quota_retry_at_utc') or to_iso(parse_iso(row['finished_at_utc'])+timedelta(seconds=F.QUOTA_BACKOFF_S))
                if utc_now()<parse_iso(until):raise ValueError('production quota backoff active')
        client=MetaculusClient(get_secret('METACULUS_TOKEN',project/'.env'))
        index,records=exclusion_index(client,root,campaign,project=project)
        bot_priority(shared,records,now=utc_now(),horizon_s=cfg['timeouts_s']['tier1']+120)
        snap=safe_snapshot(client.get_post(post_id),question_id,tournament_id,index,now=utc_now(),cfg=cfg)
        record_snapshots([snap],log=root/'questions.jsonl')
        snap=latest_snapshots(root/'questions.jsonl')[question_id]
        V.question_schema(snap)
        spec={'type':'human_request','version':VERSION,'request_id':new_id('human_request'),'at':to_iso(utc_now()),
              'post_id':post_id,'question_id':question_id,'tournament_id':tournament_id,'update_reason':update_reason,
              'max_initial_calls':3,'automatic_retries':0,'automatic_submissions':0}
        append_record(root/'requests.jsonl',spec)
        def admission(shared,b,line_root,request,protected):
            return register_admission(shared,b,line_root,request,protected,spec=spec,snap=snap)
        read_lock=threading.Lock()
        def recheck():
            if json.loads((project/'config/cycle_campaign.json').read_text())!=campaign:
                raise F.QuestionDone('shared campaign changed')
            with read_lock:
                current=safe_snapshot(client.get_post(post_id),question_id,tournament_id,index,now=utc_now(),cfg=cfg)
            if current['content_hash']!=snap['content_hash'] or current['scheduled_close_time']!=snap['scheduled_close_time']:
                raise F.QuestionDone('human question revised')
        for p in A.PLAYERS:(root/p).mkdir(mode=0o700,exist_ok=True)
        with ThreadPoolExecutor(max_workers=3) as pool:
            fs=[pool.submit(A.run_line,p,snap,root/p,shared,campaign,cfg,policy,pfs[p],adapters[p],
                            admission=admission,recheck=recheck) for p in A.PLAYERS]
            outcomes=[f.result() for f in fs]
        result=aggregate.build_aggregate(snap,[r for p in A.PLAYERS for r in read_complete(root/p/'forecasts.jsonl')],
                    audit_events=[r for p in A.PLAYERS for r in read_complete(root/p/'audits.jsonl')])
        append_record(root/'aggregates.jsonl',result)
        summary={'type':'advisory_batch_finished','version':VERSION,'at':to_iso(utc_now()),
                 'post_id':post_id,'question_id':question_id,'players':outcomes,'aggregate_status':result['status'],
                 'missing_players':result['missing_players'],'automatic_submissions':0,
                 'next_step':'English report and Gemini comment for human review (D134)'}
        append_record(root/'runs.jsonl',summary)
        return summary


def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='command',required=True)
    inv=sub.add_parser('list');inv.add_argument('--top',type=int,default=3)
    research=sub.add_parser('research');research.add_argument('--batch',type=Path,required=True)
    for field in ('post','question','tournament'):research.add_argument('--'+field,type=int,required=True)
    research.add_argument('--update-reason')
    args=ap.parse_args()
    if args.command=='list':
        root=PROJECT_ROOT/'data/advisory/inventory';root.mkdir(parents=True,exist_ok=True)
        result=inventory(MetaculusClient(get_secret('METACULUS_TOKEN',PROJECT_ROOT/'.env')),root,top=args.top)
    else:result=run(args.batch,args.post,args.question,args.tournament,update_reason=args.update_reason)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
