"""Pure post-close descriptions; never changes eligibility, forecasts or audit flags."""
from __future__ import annotations
import math
import re
from collections import Counter

VERSION = 'health_diagnostics_v1'


def finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def gap(values):
    return round(max(values)-min(values), 6) if len(values) >= 2 else None


def dispersion(snapshot, rows):
    """Typed distances: actual units and scale coordinates never share a p threshold."""
    kind = snapshot.get('question_type')
    if not kind:
        kind = 'binary' if any(finite(r.get('probability')) for r in rows) else 'unknown'
    result = {'question_type': kind, 'n': len(rows), 'metric': None, 'value': None,
              'unit': None, 'warning_threshold': None, 'high': False}
    if kind == 'binary':
        values = [r['probability'] for r in rows if finite(r.get('probability')) and 0 <= r['probability'] <= 1]
        result.update(metric='probability_range', value=gap(values), unit='probability', n=len(values), warning_threshold=.30)
    elif kind == 'multiple_choice':
        options = snapshot.get('options')
        complete = [r['option_probabilities'] for r in rows if isinstance(r.get('option_probabilities'), dict)
                    and options and set(r['option_probabilities']) == set(options)
                    and all(finite(v) and 0 <= v <= 1 for v in r['option_probabilities'].values())
                    and abs(sum(r['option_probabilities'].values())-1) <= .01]
        by_option = {o:gap([r[o] for r in complete]) for o in (options or [])}
        result.update(metric='maximum_option_probability_range', value=max((v for v in by_option.values() if v is not None),default=None),
                      unit='probability', n=len(complete), by_option=by_option, warning_threshold=.30)
    elif kind in ('numeric','discrete'):
        values = [r['percentiles']['p50'] for r in rows if isinstance(r.get('percentiles'), dict)
                  and finite(r['percentiles'].get('p50'))]
        result.update(metric='median_range',value=gap(values),unit=snapshot.get('unit') or 'unspecified',n=len(values),
                      scale_coordinate_range=None, comparison='descriptive_only; no universal high-dispersion threshold')
        try:
            import numeric as N
            scale=N.scaling(snapshot)
            coords=[scale.to_u(v) for v in values]
            if all(finite(v) for v in coords):result['scale_coordinate_range']=gap(coords)
        except (ValueError,TypeError,KeyError,OverflowError):
            result['scale_status']='unavailable'
        return result
    if result['value'] is not None and result['warning_threshold'] is not None:
        result['high']=result['value'] > result['warning_threshold']
    return result


def lifecycle(players, attempts, starts, jobs, aggregate, cycles=()):
    """Evidence per line; missing history stays unknown rather than guessed from final state."""
    selected=set((aggregate or {}).get('input_attempt_ids') or [])
    result={}
    for player in sorted(players):
        ar=[r for r in attempts if r.get('player') == player]
        sr=[r for r in starts if r.get('player') == player]
        jr=[r for r in jobs if r.get('player') == player]
        observations=[]
        for c in cycles:
            info=(c.get('players') or {}).get(player)
            if not isinstance(info,dict):continue
            pf=info.get('preflight') or {}
            observations.append({'cycle_id':c.get('cycle_id'),'started_at':c.get('started_at'),
                'status':info.get('status'),'reason':info.get('reason'),'preflight_problems':pf.get('problems') or [],
                'preflight_ok':pf.get('ok'), 'preflight_tier':pf.get('tier'),
                'binding':'qid listed as new/revised in this cycle; not a complete opportunity history'})
        tiers={}
        for tier in (1,2):
            rs=[r for r in ar if r.get('tier',1)==tier]
            js=[r for r in jr if r.get('tier',1)==tier]
            begun=[r for r in sr if r.get('tier',1)==tier]
            valid=[r for r in rs if r.get('validation_status')=='valid']
            chosen=[r['attempt_id'] for r in rs if r.get('attempt_id') in selected]
            state=('selected' if chosen else 'valid_not_selected' if valid else 'failed_attempts' if rs
                   else 'not_started_with_scheduler_evidence' if js else 'start_without_output' if begun
                   else 'no_attempt_with_cycle_block' if tier==1 and any(o['status']=='preflight_blocked' and o['preflight_tier']==1 and o['preflight_ok'] is False for o in observations)
                   else 'no_recorded_attempt' if tier==1 else 'not_requested_or_unobserved')
            tiers[str(tier)]={'state':state,'attempts':len(rs),'valid_outputs':len(valid),'selected_attempt_ids':chosen,
                'outcomes':[ {k:r.get(k) for k in ('attempt_id','validation_status','error_type','model_check','started_at_utc','finished_at_utc')} for r in rs],
                'cycle_observations':observations if tier==1 else [],
                'scheduler_evidence':[{k:r.get(k) for k in ('record_id','outcome','reason','budget_reason','finished_at')} for r in js],
                'interpretation':'historical evidence; does not establish current line health'}
        result[player]=tiers
    return result


def exposure(rows, events):
    """Retain original flags and distinguish evidence types; no automatic acquittal."""
    ids={r.get('attempt_id') for r in rows if r.get('attempt_id')}
    audits=[r for r in events if r.get('attempt_id') in ids]
    counts=Counter(h.get('kind','unknown') for r in audits for h in (r.get('keyword_hits') or []) if isinstance(h,dict))
    # Legacy forecasts may omit flags that remain in the bound audit event.
    flags={f for r in [*rows,*audits] for key in ('audit_flags','own_audit_flags') for f in (r.get(key) or [])}
    status=('confirmed_exposure_requires_review' if 'leak_confirmed' in flags else
            'requires_evidence_review' if 'suspected_leak' in flags else 'recorded_evidence_only')
    return {'flags_retained':sorted(flags),
            'keyword_hit_kinds':dict(counts),
            'events':[{k:r.get(k) for k in ('audit_event_id','attempt_id','prev_attempt_id','own_audit_flags','audit_flags','audit_reasons')} for r in audits],
            'url_events':[{k:e.get(k) for k in ('url','host','state','source')} for r in rows for e in (r.get('url_events') or []) if isinstance(e,dict)],
            'status':status,
            'limitation':'A keyword hit is not proof of answer exposure. Absence of a recorded hit is not proof of no exposure. No flag is removed.'}


def research(row, snapshot):
    text=str(row.get('base_rate') or '')
    label=('judgmental_self_label' if re.search(r'judg(e)?mental|no (?:reliable|defensible|measured)|not empiric|I (?:estimate|assume)',text,re.I)
           else 'empirical_style_claim' if re.search(r'\b\d+\s*(?:/|of|out of)\s*\d+\b',text,re.I)
           else 'unclassified' if text else 'missing')
    return {'base_rate_claim_kind':label,'base_rate_evidence':'not_independently_verified',
            'sources':row.get('sources') or [],'sources_status':'model_reported_links_only',
            'checks_required':['reference_class_matches_resolution','numerator_denominator_and_sampling_period',
                'claim_to_source_mapping','source_available_at_research_time','resolving_statistic_and_unit',
                'event_period_vs_publication_vs_platform_resolution'],
            'timing':{'forecast_close':snapshot.get('scheduled_close_time'),
                      'scheduled_platform_resolution':snapshot.get('scheduled_resolve_time'),
                      'research_started_at':row.get('started_at_utc'),
                      'event_period':'not_structured_in_legacy_output','publication_time':'not_structured_in_legacy_output'},
            'interpretation':'Text labels are heuristics, not factual validation; no empirical frequency is accepted from prose alone.'}
