import json
from pathlib import Path
from string import Template

import pytest
from flab import health_diagnostics as H


def test_numeric_dispersion_is_unit_aware():
    from test_numeric import snapshot
    rows=[{'percentiles':{'p50':v}} for v in [20,60]]
    a=H.dispersion(snapshot(),rows)
    s=snapshot(range_min=0,range_max=10000,unit='cents')
    b=H.dispersion(s,[{'percentiles':{'p50':v*100}} for v in [20,60]])
    assert a['value']==40 and b['value']==4000
    assert a['scale_coordinate_range']==b['scale_coordinate_range']
    assert not a['high'] and not b['high'] and a['warning_threshold'] is None


def test_missing_numeric_scale_does_not_invent_one():
    d=H.dispersion({'question_type':'numeric','unit':'dollars'},[{'percentiles':{'p50':v}} for v in [1,5]])
    assert d['value']==4 and d['scale_coordinate_range'] is None and d['scale_status']=='unavailable'
    assert not d['high']


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),True,None,'5'])
def test_nonfinite_values_not_compared(bad):
    d=H.dispersion({'question_type':'numeric'},[{'percentiles':{'p50':bad}},{'percentiles':{'p50':2}}])
    assert d['n']==1 and d['value'] is None and not d['high']


def test_multichoice_requires_complete_matching_probability_vectors():
    rows=[{'option_probabilities':x} for x in [{'A':.9,'B':.1},{'A':.4,'B':.6},{'A':1},{'A':.8,'B':.8},{'C':.5,'B':.5}]]
    d=H.dispersion({'question_type':'multiple_choice','options':['A','B']},rows)
    assert d['n']==2 and d['value']==.5 and d['high'] and d['unit']=='probability'


def test_missing_failed_started_and_unrequested_are_different():
    a=[{'player':'failed','tier':1,'attempt_id':'a','validation_status':'invalid','error_type':'timeout'},
       {'player':'ok','attempt_id':'b','validation_status':'valid'}]
    starts=[{'player':'begun','attempt_id':'c'}]
    jobs=[{'player':'skipped','reason':'quota_backoff','outcome':'deferred'}]
    d=H.lifecycle(['failed','ok','begun','skipped','unknown'],a,starts,jobs,{'input_attempt_ids':['b']})
    assert {p:d[p]['1']['state'] for p in d}=={'failed':'failed_attempts','ok':'selected','begun':'start_without_output','skipped':'not_started_with_scheduler_evidence','unknown':'no_recorded_attempt'}
    assert all(v['2']['state']=='not_requested_or_unobserved' for v in d.values())


def test_cycle_block_is_observation_not_complete_causal_history():
    cycles=[{'cycle_id':'c','players':{'p':{'status':'preflight_blocked','preflight':{'ok':False,'tier':1,'problems':['baseline']}}}}]
    d=H.lifecycle(['p'],[],[],[],{},cycles)['p']['1']
    assert d['state']=='no_attempt_with_cycle_block'
    assert 'not a complete opportunity history' in d['cycle_observations'][0]['binding']
    cycles[0]['players']['p']['preflight']['tier']=2
    assert H.lifecycle(['p'],[],[],[],{},cycles)['p']['1']['state']=='no_recorded_attempt'


def test_flags_and_inherited_evidence_are_never_cleared():
    rows=[{'attempt_id':'b','audit_flags':['suspected_leak']}]
    events=[{'attempt_id':'b','prev_attempt_id':'a','own_audit_flags':[], 'audit_flags':['suspected_leak'], 'audit_reasons':['inherited'], 'keyword_hits':[{'kind':'cli_deny_setting'}]}]
    d=H.exposure(rows,events)
    assert d['flags_retained']==['suspected_leak'] and d['events'][0]['own_audit_flags']==[]
    assert d['keyword_hit_kinds']=={'cli_deny_setting':1} and d['status']=='requires_evidence_review'


@pytest.mark.parametrize('text,label',[('0/4 observed','empirical_style_claim'),('I estimate from judgment','judgmental_self_label'),('some data','unclassified'),('', 'missing')])
def test_base_rate_labels_are_not_verification(text,label):
    d=H.research({'base_rate':text,'sources':[{'url':'https://example.org'}]}, {})
    assert d['base_rate_claim_kind']==label and d['base_rate_evidence']=='not_independently_verified'
    assert d['sources_status']=='model_reported_links_only'


def test_candidate_templates_render_without_changing_contract():
    root=Path(__file__).parents[1]
    candidates=list((root/'docs/experiments/d135-research-prompts').glob('*.md'))
    assert len(candidates)==5
    for p in candidates:
        if p.name=='README.md':continue
        original=Template((root/'prompts'/p.name).read_text()); candidate=Template(p.read_text())
        assert set(candidate.get_identifiers())==set(original.get_identifiers())
        rendered=candidate.substitute({k:'synthetic' for k in candidate.get_identifiers()})
        assert 'numerator and denominator' in rendered and 'publication date' in rendered
        assert 'Do not introduce new top-level fields' in rendered


@pytest.mark.parametrize('where,key,flag',[
    ('forecast','audit_flags','leak_confirmed'),
    ('forecast','own_audit_flags','leak_confirmed'),
    ('audit','audit_flags','leak_confirmed'),
    ('audit','own_audit_flags','leak_confirmed'),
    ('audit','audit_flags','suspected_leak'),
    ('audit','own_audit_flags','suspected_leak'),
])
def test_exposure_uses_all_bound_flags_at_highest_severity(where,key,flag):
    row={'attempt_id':'a'};event={'attempt_id':'a','prev_attempt_id':'previous','keyword_hits':None}
    (row if where=='forecast' else event)[key]=[flag]
    d=H.exposure([row],[event,{'attempt_id':'unrelated','audit_flags':['leak_confirmed']}])
    assert d['flags_retained']==[flag]
    assert d['status']==('confirmed_exposure_requires_review' if flag=='leak_confirmed' else 'requires_evidence_review')
    assert d['events'][0]['prev_attempt_id']=='previous'


def test_exposure_confirmed_dominates_suspected():
    d=H.exposure([{'attempt_id':'a','audit_flags':['suspected_leak']}],[{'attempt_id':'a','own_audit_flags':['leak_confirmed']}])
    assert d['flags_retained']==['leak_confirmed','suspected_leak']
    assert d['status']=='confirmed_exposure_requires_review'
