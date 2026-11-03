"""D124 candidates are observations, never permission to forecast or approve."""
import copy
import json
from pathlib import Path

import pytest

import auto_reaccept as R
import baseline as B
import forecast as F
from flab import auto_reaccept_policy as P
from flab.jsonl import append_record
from flab.ledger import read_complete


@pytest.fixture
def source(tmp_path):
    home=tmp_path/'home';home.mkdir()
    path=home/'.claude/skills/demo/SKILL.md';path.parent.mkdir(parents=True)
    path.write_text('---\nname: demo\ndescription: A simple writing aid\n---\nUse clear language.\n')
    src=next(s for s in B.default_sources({}) if s.source_id=='claude.skills')
    return home,path,src


def scan(source):
    home,path,src=source
    return P.scan(src,B.inspect_source(src,home),home=home)


def test_bounded_markdown_uses_baseline_digest_and_exclusion(source):
    home,path,src=source
    sync=home/'.claude/skills/synced';sync.mkdir();(sync/'script.py').write_text('print(1)')
    result=scan(source)
    assert result['status']=='ALLOW' and result['files']==1
    assert result['source_hash']==B.inspect_source(src,home)['hash']
    assert 'writing aid' not in json.dumps(result)


@pytest.mark.parametrize('text,reason',[
    (b'(@/tmp/foo)','import_syntax'),(b'"@~/secret"','import_syntax'),(b'`@./local`','import_syntax'),
    (b'x @foo','import_syntax'),(b'MetaCulus','restricted_marker'),(b'FLABAUTO-test','restricted_marker'),
    (b'-----BEGIN RSA PRIVATE KEY-----','restricted_marker'),(b'sk-example','restricted_marker'),
    (b'hi\x00','control_character'),(b'hi\xff','not_utf8'),
    (b'---\nhooks: hello\n---\n','frontmatter_key_or_structure'),
    (b'---\nname: [a,b]\n---\n','frontmatter_not_plain_string'),
    (b'---\nname: a\n  hook: b\n---\n','frontmatter_key_or_structure'),
    (b'---\nname: a\nname: b\n---\n','frontmatter_key_or_structure'),
    (b'---\ndescription: |\n text\n---\n','frontmatter_not_plain_string')])
def test_text_rejections_are_codes_only(text,reason):
    assert P.text_reason(text,skill=True)==reason


@pytest.mark.parametrize('unsafe',['script','hidden','executable','hardlink','symlink','private_dir','size','count'])
def test_unsafe_inventory_cannot_be_eligible(source,unsafe):
    home,path,src=source
    if unsafe=='script':(path.parent/'task.py').write_text('print(1)')
    elif unsafe=='hidden':(path.parent/'.secret.md').write_text('hidden')
    elif unsafe=='executable':path.chmod(0o700)
    elif unsafe=='hardlink':
        import os
        os.link(path,path.parent/'linked.md')
    elif unsafe=='symlink':(path.parent/'linked.md').symlink_to(path)
    elif unsafe=='private_dir':(path.parent/'.private').mkdir()
    elif unsafe=='size':path.write_bytes(b'a'*(P.MAX_FILE_BYTES+1))
    else:
        for i in range(P.MAX_FILES):(path.parent/f'{i}.md').write_text('text')
    assert scan(source)['status']!='ALLOW'


def test_snapshot_race_does_not_accept_new_digest(source):
    home,path,src=source
    saved=B.inspect_source(src,home);path.write_text('different')
    result=P.scan(src,saved,home=home)
    assert result['status']=='UNKNOWN' and result['reason']=='snapshot_changed'


def test_single_file_hash_and_frontmatter_rule(tmp_path):
    path=tmp_path/'CLAUDE.md';path.write_text('Use clear language.')
    src=B.Source('claude.global_instructions','claude',str(path),'file')
    assert P.scan(src,B.inspect_source(src,tmp_path),home=tmp_path)['status']=='ALLOW'
    path.write_text('---\nname: example\n---\n')
    assert P.scan(src,B.inspect_source(src,tmp_path),home=tmp_path)['reason']=='frontmatter_outside_skill'
    path.unlink()
    assert P.scan(src,B.inspect_source(src,tmp_path),home=tmp_path)['reason']=='absent'


def snapshots(source):
    home,path,src=source
    rec=B.inspect_source(src,home)
    current={'sources':{src.source_id:rec},'versions':dict.fromkeys(B.PLAYERS,'1'),'gemini_credits':0}
    old=copy.deepcopy(current);old['record_id']='original';old['sources'][src.source_id]['hash']='old'
    context={'identity':{'path':'/official/claude','sha256':'abc'},'production_evidence_ok':True,
             'catalog_changed':False,'runtime_fingerprint':{'runtime':'hash'},'models':{'tier1':'same','tier2':'same'},
             'judge_version':'current'}
    return old,current,context,{src.source_id:scan(source)}


def test_candidate_binds_all_protected_sources_and_runtime(source):
    old,current,ctx,scans=snapshots(source)
    result=P.candidate('claude',old,current,context=ctx,scans=scans)
    assert result['format_eligible'] and result['mode']=='observe'
    assert result['model_calls']==0 and result['baseline_approved'] is False
    altered=copy.deepcopy(ctx);altered['runtime_fingerprint']['runtime']='other'
    assert P.candidate('claude',old,current,context=altered,scans=scans)['candidate_sha256']!=result['candidate_sha256']
    changed=copy.deepcopy(current);changed['taken_at']='later'
    assert P.candidate('claude',old,changed,context=ctx,scans=scans)['candidate_sha256']==result['candidate_sha256']


@pytest.mark.parametrize('reason',['common','plugin','mixed','version','evidence','identity','blocked'])
def test_non_text_or_untrusted_candidates_remain_ineligible(source,reason):
    old,current,ctx,scans=snapshots(source)
    if reason in ('common','plugin'):
        sid='ancestor:/tmp/AGENTS.md' if reason=='common' else 'claude.plugins'
        extra={'kind':'file','player':'common' if reason=='common' else 'claude','category':B.CONTENT,'exists':True,'hash':'old'}
        old['sources'][sid]=extra;current['sources'][sid]={**extra,'hash':'new'}
    elif reason=='mixed':ctx['catalog_changed']=True
    elif reason=='version':current['versions']['claude']='new'
    elif reason=='evidence':ctx['production_evidence_ok']=False
    elif reason=='identity':ctx['identity']=None
    else:current['sources']['claude.skills']['error']='unknown'
    assert not P.candidate('claude',old,current,context=ctx,scans=scans)['format_eligible']


def test_observe_records_once_and_never_changes_acceptance(source,tmp_path,monkeypatch):
    old,current,ctx,scans=snapshots(source);root=tmp_path/'data'
    append_record(root/'acceptance.jsonl',{'type':'baseline_approved',**old})
    before=(root/'acceptance.jsonl').read_bytes()
    for i in range(2):
        result=R.observe_line('claude',root,snapshot=lambda:current,
                              context_fn=lambda *a:ctx,home=source[0])
        assert result['format_eligible'] and result['model_calls']==0
    assert len(read_complete(root/'auto_reaccept.jsonl'))==1
    assert (root/'acceptance.jsonl').read_bytes()==before
    with F.Lock(root/'forecast.claude.lock'):
        with pytest.raises(F.PreflightError):R.observe_line('claude',root)


def test_tick_has_singleton_and_no_enforce_escape(tmp_path,monkeypatch):
    seen=[]
    with F.Lock(tmp_path/'auto_reaccept.lock'):
        assert R.tick(tmp_path,observe=lambda *a:pytest.fail('concurrent tick'))['status']=='busy'
    result=R.tick(tmp_path,observe=lambda p,r:seen.append(p) or {'player':p,'model_calls':0})
    assert seen==list(B.PLAYERS) and result['model_calls']==0
    monkeypatch.setattr(P,'MODE','enforce')
    with pytest.raises(ValueError,match='observe only'):R.tick(tmp_path)
