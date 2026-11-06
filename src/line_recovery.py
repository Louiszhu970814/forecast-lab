"""D140 bounded recovery for proven, unexecuted AGY tool spelling failures.

No model calls, predictions, baseline approvals, CLI upgrades or platform writes.
Current isolation proofs must still pass; the scheduler owns any new attempt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import signal
from pathlib import Path

import baseline as B
import forecast as F
import live_window as W
import quota
from adapters import gemini as G
from flab.config import load_players_enabled, load_run_config, load_submit_mode
from flab.external_usage import guards, usage
from flab.ids import hash_obj, new_id
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso, utc_now
from flab.workdir import PROJECT_ROOT

VERSION = 'line_recovery_v1'
AUTHORIZATION = 'PLAN D140'
REJECTED_TOOL = ' view_file'
REJECTION = 'unknown tool: " view_file" — check spelling'
PIN_SHA = '34423fc9b0af3841e7646aa72a2d40ffeab8218b22d1cb99eac7355f9eba31e3'
WRAPPER_SHA = '93743416f5089792ada3540c24ef7dfd62ebe6951a525fff36714cc87b095dbf'
MAX_RAW_BYTES = 32 * 1024 * 1024
MAX_RECOVERIES_24H = 3
META_KEYS = ('attempt_id','player','tier','question_id','question_snapshot_id','run_id',
    'attempt_kind','prev_attempt_id','pipeline_version','cli_version','validation_status',
    'requested_model_id','requested_effort','run_config_version',
    'model_check','inherited_model_mismatch','error_type','safety_stop_reason',
    'audit_flags','own_audit_flags','tool_violations','tool_call_counts','subagent_count',
    'denied_actions','raw_dir','started_at_utc','finished_at_utc','decision_deadline_utc','preflight_waivers',
    'quota_after_error','quota_cost_status','quota_retry_at_utc','recovery_no_rerun')


def projection(row):
    return {k:row.get(k) for k in META_KEYS}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected(snapshot, player='gemini'):
    return {k:B._fingerprint(v) for k,v in snapshot['sources'].items()
            if v.get('player') in (player,'common') and v.get('category') != B.INFORMATIONAL}


def safe_raw(project, row):
    relative=Path(row['raw_dir'])
    expected=Path('data/raw/gemini') / f"{row['question_id']}_{row['attempt_id']}"
    if relative != expected or relative.is_absolute():
        raise ValueError('raw_identity')
    path=project/relative/'stdout.jsonl'
    if any(p.is_symlink() for p in [path,*path.parents]) or not path.is_file() or path.stat().st_size > MAX_RAW_BYTES:
        raise ValueError('raw_unavailable')
    return path


def strict_events(path):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate_json_key')
            result[key]=value
        return result
    def nonfinite(_):raise ValueError('nonfinite_json_constant')
    raw=path.read_bytes()
    if not raw.endswith(b'\n'):raise ValueError('truncated_stream')
    events=[json.loads(line,object_pairs_hook=unique,parse_constant=nonfinite) for line in raw.decode('utf-8').splitlines() if line.strip()]
    top={'init':{'event','conversation_id','init'},'result':{'event','result'},'step_update':{'event','step_update'}}
    step_keys={'conversation_id','duration_seconds','state','step_index','step_type','text_delta','usage','tool_info','tool_name'}
    for e in events:
        if not isinstance(e,dict) or e.get('event') not in top or set(e)!=top[e['event']]:
            raise ValueError('unknown_event_schema')
        if e['event']=='step_update':
            u=e['step_update']
            if not isinstance(u,dict) or set(u)-step_keys:raise ValueError('unknown_step_schema')
            info=u.get('tool_info')
            if info is not None:
                if not isinstance(info,dict) or set(info)-{'name','parameters','output','error'}:
                    raise ValueError('unknown_tool_schema')
                if info.get('name') not in (*G.TIER1_TOOLS,REJECTED_TOOL):raise ValueError('unknown_tool')
                if u.get('tool_name') and u['tool_name']!=info.get('name'):raise ValueError('tool_name_conflict')
        elif e['event']=='init':
            if not isinstance(e['init'],dict) or set(e['init'])-{'model','cwd','agent','tools','permission_mode','json_schema','conversation_id'}:
                raise ValueError('unknown_init_schema')
    if not events or events[-1].get('event')!='result':raise ValueError('missing_terminal_event')
    groups={};last=-1
    for e in events:
        if e['event']!='step_update':continue
        u=e['step_update'];index=u.get('step_index')
        if type(index) is not int or index<last or u.get('step_type') not in ('user_input','agent_response','tool','finish'):
            raise ValueError('step_order_or_type')
        last=index;groups.setdefault(index,[]).append(u)
    for updates in groups.values():
        if not any(u.get('step_type')=='tool' or 'tool_info' in u or 'tool_name' in u for u in updates):continue
        if len(updates)!=2 or updates[0].get('state')!='ACTIVE' or updates[1].get('state') not in ('DONE','ERROR'):
            raise ValueError('incomplete_or_conflicting_tool_steps')
        first=updates[0].get('tool_info') or {};tool=first.get('name');params=first.get('parameters')
        if updates[0].get('step_type')!='tool' or not isinstance(params,dict) or 'output' in first or 'error' in first:
            raise ValueError('invalid_tool_start')
        for u in updates:
            info=u.get('tool_info')
            if u.get('tool_name') and u['tool_name']!=tool:raise ValueError('changed_tool_identity')
            if info is not None and (info.get('name')!=tool or info.get('parameters')!=params):
                raise ValueError('changed_tool_identity')
            if u.get('step_type')=='finish':
                if tool!='finish' or u.get('state')!='DONE':raise ValueError('changed_step_type')
            elif u.get('step_type')!='tool' or info is None:raise ValueError('missing_tool_identity')
    return events


def rejected_page_call(events):
    """Exact pinned-CLI rejection, never normalization or a new allowed tool."""
    inits=[e for e in events if e.get('event')=='init' and isinstance(e.get('init'),dict)]
    if len(inits)!=1:return False
    cid=inits[0].get('conversation_id')
    if 'conversation_id' in inits[0]['init'] and inits[0]['init']['conversation_id']!=cid:return False
    if any(e.get('step_update',{}).get('conversation_id',cid)!=cid for e in events):return False
    if not isinstance(cid,str) or not G.CONVERSATION_ID_RE.fullmatch(cid):return False
    steps=G.tool_steps(events)
    bad=[s for s in steps if s.get('tool')==REJECTED_TOOL]
    if len(bad)!=1:return False
    s=bad[0];target=G._params_target('view_file',s.get('parameters'))
    params=s.get('parameters') or {}
    if set(params)!={'AbsolutePath','StartLine','EndLine'} or any(type(params.get(k)) is not int for k in ('StartLine','EndLine')) \
            or not 1<=params['StartLine']<=params['EndLine']:return False
    prefix=str(G.brain_dir()/cid)
    if not isinstance(target,str) or not re.fullmatch(re.escape(prefix)+r'/\.system_generated/steps/[0-9]+/content\.md',target) \
            or not G.own_page_file(target,cid):return False
    if s.get('state')!='ERROR' or s.get('error')!=REJECTION or 'output' in s:return False
    page_step=int(re.search(r'/steps/([0-9]+)/content.md$',target).group(1))
    source=next((x for x in steps if x['step']==page_step),None)
    if page_step>=s['step'] or not source or source.get('tool')!='read_url_content' or source.get('state')!='DONE':return False
    if any(p.is_symlink() for p in [Path(target),*Path(target).parents]):return False
    updates=[e['step_update'] for e in events if isinstance(e.get('step_update'),dict)
             and e['step_update'].get('step_index')==s['step']]
    if not updates or [u.get('state') for u in updates] != ['ACTIVE','ERROR']:return False
    for u in updates:
        info=u.get('tool_info') or {}
        if u.get('step_type')!='tool' or info.get('name')!=REJECTED_TOOL \
                or 'output' in info or info.get('parameters') != s.get('parameters'):return False
    # Independently audit all other calls. No file content or search text is retained.
    for call in steps:
        t=call.get('tool');state=call.get('state')
        if t==REJECTED_TOOL:continue
        if t not in G.TIER1_TOOLS or state not in ('DONE','ERROR'):return False
        if G.denial_of(call.get('error')):return False
        target=G._params_target(t,call.get('parameters'))
        if t=='view_file' and not G.own_page_file(target,cid):return False
        if t=='read_url_content' and (not target or G.is_metaculus_host(G.url_host(target))):return False
    finals=[e['result'] for e in events if e.get('event')=='result' and isinstance(e.get('result'),dict)]
    return len(finals)==1 and finals[0].get('status')=='SUCCESS' and not finals[0].get('denied_actions') \
        and finals[0].get('conversation_id',cid)==cid


def classify(row, project=PROJECT_ROOT):
    """Return evidence hashes only. Every unexpected condition is ineligible."""
    r=projection(row)
    if r['player']!='gemini' or r['cli_version']!='1.4.12' or r['tier'] not in (1,2) \
            or r['attempt_kind']!='initial' or r['prev_attempt_id'] is not None \
            or r['validation_status']!='incomplete' or r['error_type']!='tool_scope_violation' \
            or r['model_check']!='verified':return None
    if any(r.get(k) for k in ('audit_flags','own_audit_flags','safety_stop_reason',
            'inherited_model_mismatch','subagent_count','denied_actions','preflight_waivers',
            'quota_after_error','recovery_no_rerun')):return None
    vs=r['tool_violations']
    if not isinstance(vs,list) or len(vs)!=2:return None
    one=[v for v in vs if set(v)=={'type','tool','step','state'}]
    two=[v for v in vs if set(v)=={'type','tool','count'}]
    if len(one)!=1 or len(two)!=1 or one[0].get('state')!='ERROR' or two[0].get('count')!=1 \
            or any(v.get('tool')!=REJECTED_TOOL or v.get('type')!='unexpected_tool' for v in vs):return None
    path=safe_raw(Path(project),r)
    raw_sha=sha(path)
    try:events=strict_events(path)
    except (ValueError,UnicodeError):return None
    if sha(path)!=raw_sha or not rejected_page_call(events):return None
    steps=G.tool_steps(events)
    counts={t:sum(s.get('tool')==t for s in steps) for t in {s.get('tool') for s in steps}}
    if r['tool_call_counts']!=counts or one[0]['step']!=next(s['step'] for s in steps if s.get('tool')==REJECTED_TOOL):return None
    return {'diagnosis':'unexecuted_page_tool_typo','attempt_id':r['attempt_id'],
            'metadata_sha256':hash_obj(r),'raw_sha256':raw_sha}


def situation(root, player, now):
    """Bind every relevant line event, including external safety and unknown starts."""
    status=[r for r in read_complete(root/'line_status.jsonl') if r.get('player')==player]
    stop=F.line_stopped(root/'line_status.jsonl',player,root/'forecasts.jsonl',now=now)
    attempts=[projection(r) for r in read_complete(root/'forecasts.jsonl') if r.get('player')==player]
    if len({r['attempt_id'] for r in attempts})!=len(attempts):raise ValueError('duplicate_attempt_id')
    starts=[r for r in read_complete(root/'forecast_attempt_starts.jsonl') if r.get('player')==player]
    external=[r for r in guards(root) if r.get('player')==player]
    ids={r['attempt_id'] for r in attempts}
    audits=[{k:r.get(k) for k in ('audit_event_id','attempt_id','own_audit_flags','audit_flags')}
            for r in read_complete(root/'audits.jsonl') if r.get('attempt_id') in ids]
    admitted,finished=usage(root)
    if any(r['attempt_id'] not in {a['attempt_id'] for a in attempts} for r in starts):
        raise ValueError('unresolved_start')
    if any(aid not in finished for aid,r in admitted.items() if r['player']==player):
        raise ValueError('unresolved_external_start')
    resumed=F.latest_resume(root/'line_status.jsonl',player)
    pending=[];covered=set()
    for event in status:
        if event.get('type')=='line_stop':pending.append(event)
        elif event.get('type')=='line_resume':
            if event.get('by')==VERSION:
                proof=event.get('proof') or {}
                if event.get('authorization')==AUTHORIZATION and proof.get('diagnosis')=='unexecuted_page_tool_typo':
                    pending=[r for r in pending if r!=event.get('lifts')]
                    covered.add(proof.get('attempt_id'))
            else:pending=[];covered=set()
    if stop and pending and pending!=[stop]:raise ValueError('other_uncovered_stop')
    recent=[r for r in attempts+external if resumed is None or parse_iso(r['finished_at_utc'])>resumed]
    unsafe=[r for r in recent if ((r.get('tool_violations') or r.get('error_type')=='tool_scope_violation')
            and r.get('attempt_id') not in covered)
            or r.get('safety_stop_reason') or F._legacy_mismatch(r)
            or 'leak_confirmed' in (r.get('audit_flags') or [])
            or 'leak_confirmed' in (r.get('own_audit_flags') or [])]
    if any('leak_confirmed' in (r.get('audit_flags') or []) or
           'leak_confirmed' in (r.get('own_audit_flags') or []) for r in audits):
        raise ValueError('confirmed_audit_exposure')
    if any(r.get('model_check')=='mismatch' and
           (now-parse_iso(r['finished_at_utc'])).total_seconds()<F.FS.COOLDOWN_S for r in recent):
        raise ValueError('recent_model_mismatch')
    if any(r.get('quota_retry_at_utc') and now<parse_iso(r['quota_retry_at_utc']) for r in recent):
        raise ValueError('active_quota_backoff')
    return stop,unsafe,hash_obj({'status':status,'attempts':attempts,'starts':starts,'external':external,'audits':audits,
        'external_usage':[{k:v for k,v in admitted.items() if v['player']==player},
                         {k:v for k,v in finished.items() if v['player']==player}]})


def candidate(root, now):
    stop,unsafe,binding=situation(root,'gemini',now)
    if not stop:return None,'healthy'
    if stop.get('until'):return None,'existing_backoff'
    if len(unsafe)!=1:return None,'safety_review_required'
    row=unsafe[0]
    reason=f"tool_scope_violation on attempt {row['attempt_id']}: stop the line and notify Louis; re-acceptance required (§7.3, D60, D62)"
    if stop.get('reason')!=reason or stop.get('run_id')!=row.get('run_id'):return None,'safety_review_required'
    proof=classify(row,root.parent)
    if not proof:return None,'safety_review_required'
    return {'stop':stop,'row':row,'proof':proof,'situation_sha256':binding,
            'stop_sha256':hash_obj(stop)},'eligible'


def retry_grant(root, chain, base, *, now=None):
    """One existing rerun slot, never a fresh attempt budget or adopted old answer."""
    now=now or utc_now()
    if len(chain)!=1 or chain[0].get('attempt_id')!=base.get('attempt_id') \
            or base.get('attempt_kind')!='initial':return None
    p=projection(base)
    for row in reversed(read_complete(root/'line_status.jsonl')):
        g=row.get('retry_grant')
        if row.get('type')!='line_resume' or row.get('by')!=VERSION or not isinstance(g,dict):continue
        if g.get('attempt_id')!=p['attempt_id']:continue
        if g.get('version')!=VERSION or g.get('metadata_sha256')!=hash_obj(p) \
                or g.get('player')!=p['player'] or g.get('question_id')!=p['question_id'] \
                or g.get('tier')!=p['tier'] or g.get('question_snapshot_id')!=p['question_snapshot_id'] \
                or not parse_iso(row['at']) <= now < parse_iso(g['expires_at']):return None
        if any(r.get('player')==p['player'] and r.get('question_id')==p['question_id'] and r.get('tier')==p['tier']
               and r.get('attempt_kind')=='rerun' for r in read_complete(root/'forecast_attempt_starts.jsonl')):return None
        if row.get('authorization')!=AUTHORIZATION or sha(safe_raw(root.parent,p))!=g.get('raw_sha256'):
            return None
        binding=row.get('binding')
        try:
            validate_binding(binding)
            if incident_binding(root,p,binding)!=binding:return None
            require_incident_environment(root,p,load_run_config(root.parent/'config'))
            if binding['runtime']!=F.runtime_fingerprint(p['player']):return None
            if binding['run_config_sha256']!=hash_obj(load_run_config(root.parent/'config')):return None
            approved=B.latest_approved(root/'acceptance.jsonl')
            if not approved or approved['record_id']!=binding['baseline_record_id']:return None
            if binding['judge_version']!=F.J.JUDGE_VERSION or binding['cli_version']!=p['cli_version']:return None
            if sha(root.parent/'launchd/bin/agy')!=binding['wrapper_sha256'] or sha(root/'cli_pins/agy/1.4.12')!=binding['pin_sha256']:return None
        except (OSError,ValueError,TypeError,KeyError):return None
        if F.line_stopped(root/'line_status.jsonl',p['player'],root/'forecasts.jsonl',now=now):return None
        # A later audit must never be forgiven by an older diagnosis.
        audits=[r for r in read_complete(root/'audits.jsonl') if r.get('attempt_id')==p['attempt_id']]
        if any('leak_confirmed' in (a.get('audit_flags') or []) or
               'leak_confirmed' in (a.get('own_audit_flags') or []) for a in audits):return None
        return g
    return None


def require_incident_environment(root, row, cfg):
    started=parse_iso(row['started_at_utc'])
    if row.get('run_config_version')!=cfg['version']:raise ValueError('incident_config_changed')
    fields={'baseline_approved':'approved_at','acceptance_call_started':'started_at',
        'acceptance_call_finished':'finished_at','acceptance_result':'finished_at',
        'acceptance_quota_snapshot':'taken_at','acceptance_retracted':'at','acceptance_paused':'at',
        'autoload_result':'finished_at','autoload_run_started':'started_at','baseline_check':'snapshot'}
    for record in read_complete(root/'acceptance.jsonl'):
        if record.get('player') in ('claude','gpt'):continue
        kind=record.get('type')
        if kind not in fields:raise ValueError('unknown_incident_acceptance_event')
        stamp=record.get(fields[kind])
        if kind=='baseline_check':stamp=stamp.get('taken_at') if isinstance(stamp,dict) else None
        if not isinstance(stamp,str):raise ValueError('incident_acceptance_missing_time')
        if parse_iso(stamp)>=started:raise ValueError('acceptance_changed_after_incident')


def validate_binding(binding):
    if not isinstance(binding,dict):raise ValueError('invalid_recovery_binding')
    for key in ('baseline_record_id','run_config_sha256','wrapper_sha256','pin_sha256','judge_version','cli_version'):
        if not isinstance(binding.get(key),str) or not binding[key]:raise ValueError('missing_binding_'+key)
    if not isinstance(binding.get('runtime'),dict) or not binding['runtime'] or \
            not all(isinstance(v,str) and v for v in binding['runtime'].values()):raise ValueError('missing_binding_runtime')
    if not isinstance(binding.get('sources'),dict) or not binding['sources']:raise ValueError('missing_binding_sources')
    proofs=binding.get('proofs')
    if not isinstance(proofs,list) or len(proofs)!=2 or any(not isinstance(p,dict) for p in proofs) \
            or {p.get('tier') for p in proofs}!={1,2}:raise ValueError('missing_binding_proofs')
    for proof in proofs:
        if type(proof.get('tier')) is not int:raise ValueError('invalid_binding_tier')
        for key in ('acceptance_record_id','production_record_id'):
            if not isinstance(proof.get(key),str) or not proof[key]:raise ValueError('missing_binding_proof_id')


def incident_binding(root, row, binding):
    """Explicitly bind each selected proof, not just the absence of new approvals."""
    validate_binding(binding)
    records=read_complete(root/'acceptance.jsonl');started=parse_iso(row['started_at_utc'])
    cfg=load_run_config(root.parent/'config')
    def evidence(record_id,kind,time_field,production_tier=None):
        matches=[r for r in records if r.get('record_id')==record_id]
        if len(matches)!=1:raise ValueError('incident_evidence_not_unique')
        record=matches[0]
        if record.get('type')!=kind or (kind=='acceptance_result' and
                (record.get('player')!='gemini' or record.get('line_pass') is not True)):
            raise ValueError('incident_evidence_wrong_type')
        if production_tier is not None:
            expected=cfg['players']['gemini'][f'tier{production_tier}']
            if record.get('mode')!='production' or record.get('tier')!=production_tier or \
                    record.get('model')!=expected['model'] or record.get('effort')!=expected['effort']:
                raise ValueError('incident_production_identity_changed')
        stamp=record.get(time_field)
        if not isinstance(stamp,str) or parse_iso(stamp)>=started:raise ValueError('incident_evidence_not_prior')
        return {'record_id':record_id,'type':kind,'at':stamp,'record_sha256':hash_obj(record)}
    selected={'baseline':evidence(binding['baseline_record_id'],'baseline_approved','approved_at'),
        'proofs':[{'tier':p['tier'],
            'acceptance':evidence(p['acceptance_record_id'],'acceptance_result','finished_at'),
            'production':evidence(p['production_record_id'],'acceptance_result','finished_at',p['tier'])}
            for p in binding['proofs']]}
    return {**binding,'incident_evidence':selected}


def capture(project, cfg):
    root=project/'data'
    wrapper=project/'launchd/bin/agy';pin=root/'cli_pins/agy/1.4.12'
    if Path(shutil.which('agy') or '')!=wrapper or wrapper.is_symlink() or sha(wrapper)!=WRAPPER_SHA \
            or pin.is_symlink() or sha(pin)!=PIN_SHA:
        raise ValueError('cli_identity_changed')
    observed=quota.snapshot(timeout_s=20)
    if not quota.observation_fresh(observed,utc_now()) or observed.remaining_credits!=0:
        raise ValueError('fresh_zero_credits_required')
    versions=B.cli_versions()
    current=B.take_snapshot(versions=versions,gemini_credits=0).to_record()
    old=B.latest_approved(root/'acceptance.jsonl')
    if not old or B.compare(old,current)['decisions'].get('gemini')!='ok':raise ValueError('baseline_changed')
    proofs=[]
    for tier in (1,2):
        pf=F.preflight('gemini',cfg=cfg,tier=tier,continuous=True,acceptance_log=root/'acceptance.jsonl',
            versions=lambda:versions,snapshot=lambda _:current,adapter=G)
        if not pf.ok or pf.waivers:raise ValueError('preflight_failed')
        proofs.append({'tier':tier,'acceptance_record_id':pf.acceptance_record_id,
                       'production_record_id':pf.production_record_id})
    if not quota.observation_fresh(observed,utc_now()):raise ValueError('quota_expired')
    return {'baseline_record_id':old['record_id'],'sources':protected(current),'cli_version':versions['gemini'],
        'wrapper_sha256':sha(wrapper),'pin_sha256':sha(pin),'runtime':F.runtime_fingerprint('gemini'),
        'judge_version':F.J.JUDGE_VERSION,'proofs':proofs,'run_config_sha256':hash_obj(cfg)}


def recover(root=PROJECT_ROOT/'data', *, now=utc_now, capture_fn=capture):
    project=root.parent;cfg=load_run_config(project/'config')
    settings=json.loads((project/'config/line_recovery.json').read_text())
    expected={'version':VERSION,'enabled':True,'authorization':AUTHORIZATION,'interval_s':300}
    if settings!=expected:return {'status':'disabled'}
    def active():
        if (project/'config/STOP_SUBMIT').exists():raise ValueError('global_stop')
        campaign=W.campaign_from(project/'config')
        W.require_active(load_submit_mode(project/'config'),campaign,project/'config',now())
        if not load_players_enabled(project/'config').get('gemini'):raise ValueError('disabled_line')
        return hash_obj(campaign)
    active_hash=active()
    # Healthy lines must not compete for a forecast lock on every service tick.
    preliminary=F.line_stopped(root/'line_status.jsonl','gemini',root/'forecasts.jsonl',now=now())
    if preliminary is None:return {'status':'healthy'}
    if preliminary.get('until'):return {'status':'existing_backoff'}
    with F.Lock(root/'line_recovery.lock'),F.Lock(root/'forecast.gemini.lock'):
        with transaction(root):
            current,status=candidate(root,now())
            if not current:return {'status':status}
            require_incident_environment(root,current['row'],cfg)
            model=cfg['players']['gemini'][f"tier{current['row']['tier']}"]
            if (current['row']['requested_model_id'],current['row']['requested_effort'])!=(model['model'],model['effort']):
                return {'status':'model_configuration_changed'}
            history=read_complete(root/'line_recovery.jsonl')
            starts=[r for r in history if r.get('type')=='recovery_started']
            if any(r.get('stop_sha256')==current['stop_sha256'] for r in starts):
                return {'status':'already_attempted'}
            if sum((now()-parse_iso(r['at'])).total_seconds()<86400 for r in starts)>=MAX_RECOVERIES_24H:
                return {'status':'daily_limit'}
        # Expensive external checks stay outside the shared ledger transaction.
        before=capture_fn(project,cfg)
        with transaction(root):
            check,_=candidate(root,now())
            if check!=current or active()!=active_hash:raise ValueError('superseded')
            rid=new_id('recovery')
            append_record(root/'line_recovery.jsonl',{'type':'recovery_started','version':VERSION,
                'record_id':rid,'player':'gemini','at':to_iso(now()),'stop_sha256':current['stop_sha256'],
                'proof':current['proof'],'binding':before,'authorization':AUTHORIZATION,'model_calls':0})
        try:
            before=incident_binding(root,current['row'],before)
            after=incident_binding(root,current['row'],capture_fn(project,cfg))
            if after!=before:raise ValueError('environment_changed')
            with transaction(root):
                check,_=candidate(root,now())
                if check!=current or active()!=active_hash:raise ValueError('superseded')
                require_incident_environment(root,current['row'],cfg)
                if incident_binding(root,current['row'],after)!=after:raise ValueError('incident_evidence_changed')
                latest=B.latest_approved(root/'acceptance.jsonl')
                if not latest or latest['record_id']!=after['baseline_record_id']:
                    raise ValueError('baseline_superseded')
                row=current['row'];expires=row.get('decision_deadline_utc')
                # Resuming future work is useful even when this question has expired.
                grant=None
                if expires and now()<parse_iso(expires):
                    grant={**current['proof'],'version':VERSION,'player':'gemini',
                        'question_id':row['question_id'],'tier':row['tier'],
                        'question_snapshot_id':row['question_snapshot_id'],'expires_at':expires,
                        'recovery_id':rid}
                append_record(root/'line_status.jsonl',{'type':'line_resume','player':'gemini','by':VERSION,
                    'reason':'verified unexecuted tool spelling failure; current isolation proofs unchanged',
                    'lifts':current['stop'],'at':to_iso(now()),'authorization':AUTHORIZATION,
                    'recovery_id':rid,'proof':current['proof'],'binding':after,'retry_grant':grant})
                append_record(root/'line_recovery.jsonl',{'type':'recovery_finished','record_id':rid,
                    'version':VERSION,'player':'gemini','at':to_iso(now()),'outcome':'resumed',
                    'retry_granted':grant is not None,'model_calls':0})
            return {'status':'resumed','recovery_id':rid,'retry_granted':grant is not None,'model_calls':0}
        except Exception as exc:
            with transaction(root):
                committed=next((r for r in reversed(read_complete(root/'line_status.jsonl'))
                    if r.get('type')=='line_resume' and r.get('recovery_id')==rid),None)
                if committed:
                    return {'status':'resumed','recovery_id':rid,'retry_granted':bool(committed.get('retry_grant')),
                            'model_calls':0,'completion_journal':'pending'}
                append_record(root/'line_recovery.jsonl',{'type':'recovery_finished','record_id':rid,
                    'version':VERSION,'player':'gemini','at':to_iso(now()),'outcome':'held',
                    'error_class':type(exc).__name__,'model_calls':0})
            raise


class RecoveryDeadline(Exception):
    pass


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',nargs='?',default='tick',choices=('tick',))
    parser.parse_args(argv)
    old=signal.getsignal(signal.SIGALRM)
    def expired(*_):raise RecoveryDeadline()
    signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,120)
    try:
        try:result=recover()
        except F.PreflightError:result={'status':'busy'}
        except (OSError,ValueError,TypeError,KeyError,RecoveryDeadline):result={'status':'check_failed_state_not_assumed'}
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old)
    print(json.dumps({'version':VERSION,**result},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
