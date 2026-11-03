"""D124 observation policy. Format eligibility is not approval or proof of content safety."""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat

import baseline as B
from flab.ids import hash_obj, sha256_hex

VERSION = 'auto_reaccept_v1'
MODE = 'observe'  # No executable enforce path in this release.
ALLOWLIST = frozenset(('claude.global_instructions','claude.skills','codex.global_instructions',
                      'codex.skills','agy.gemini_md','agy.agents_md','agy.skills','agy.rules'))
MAX_FILE_BYTES = 64*1024
MAX_SOURCE_BYTES = 512*1024
MAX_FILES = 64
MAX_DIRS = 256
IMPORT = re.compile(r'''(?:^|[\s(\[{"'`])@\S|@[/.~]''')
MARKERS = re.compile(r'metaculus|FLABAUTO-|FLABCANARY|Forecast Lab auto-load test|'
                     r'BEGIN (?:[A-Z ]+ )?PRIVATE KEY|\bsk-|\bghp_|\bgithub_pat_',re.I)


def text_reason(content: bytes, *, skill: bool) -> str | None:
    try: text=content.decode('utf-8')
    except UnicodeError: return 'not_utf8'
    if any((ord(c)<32 and c not in '\t\r\n') or 127<=ord(c)<=159 for c in text):
        return 'control_character'
    if IMPORT.search(text): return 'import_syntax'
    if MARKERS.search(text): return 'restricted_marker'
    if text.startswith('\ufeff'): return 'byte_order_mark'
    if text.startswith('---'):
        if not skill: return 'frontmatter_outside_skill'
        lines=text.splitlines()
        if lines[0]!='---' or '---' not in lines[1:]: return 'invalid_frontmatter'
        keys=set()
        for line in lines[1:lines.index('---',1)]:
            if not line.strip(): continue
            match=re.fullmatch(r'(name|description):[ \t]+(.+)',line)
            if not match or match[1] in keys: return 'frontmatter_key_or_structure'
            keys.add(match[1]); value=match[2].strip()
            # Deliberately narrow single-line scalar grammar; no YAML evaluator.
            if not value or value[0] in '[]{}&*!|>%-' or ': ' in value or \
                    value.lower() in ('null','true','false','yes','no','~') or value.isdecimal():
                return 'frontmatter_not_plain_string'
            if value[0] in "\"'" and (len(value)<2 or value[-1]!=value[0]):
                return 'frontmatter_not_plain_string'
    return None


def scan(source: B.Source, saved: dict, *, home: Path) -> dict:
    """Read only allowlisted bounded Markdown; emit hashes/codes, never its text."""
    result={'status':'UNKNOWN','reason':'not_allowlisted','files':0,'bytes':0}
    if source.source_id not in ALLOWLIST or source.kind not in ('file','dir'): return result
    path=B._expand(source.path,home)
    def reject(reason, status='DENY'):
        return {**result,'status':status,'reason':reason}
    try:
        for parent in path.parents:
            try: parent_mode=parent.lstat().st_mode
            except FileNotFoundError: continue
            if not stat.S_ISDIR(parent_mode): return reject('unsafe_parent')
        if str(path)!=saved.get('path') or source.player!=saved.get('player') or source.kind!=saved.get('kind'):
            return reject('inventory_changed','UNKNOWN')
        if not path.exists() and not path.is_symlink():
            return {**result,'status':'ALLOW','reason':'absent','source_hash':None} if saved.get('exists') is False \
                else reject('snapshot_changed','UNKNOWN')
        info=path.lstat()
        if stat.S_ISLNK(info.st_mode): return reject('symlink')
        if (source.kind=='dir') != stat.S_ISDIR(info.st_mode): return reject('source_type')
        files=[]; directories=0
        if source.kind=='file': files=[path]
        else:
            def walk_error(error): raise error
            for directory,dirs,names in os.walk(path,followlinks=False,onerror=walk_error):
                directories+=1
                if directories>MAX_DIRS: return reject('directory_limit')
                kept=[]
                for name in sorted(dirs):
                    child=Path(directory)/name; rel=str(child.relative_to(path))
                    if rel in source.exclude: continue
                    if child.is_symlink() or name.startswith('.') or B.PRIVATE_DIR.match(name):
                        return reject('unsafe_directory')
                    kept.append(name)
                dirs[:]=kept
                for name in sorted(names):
                    child=Path(directory)/name
                    if str(child.relative_to(path)) in source.exclude: continue
                    files.append(child)
                    if len(files)>MAX_FILES:return reject('file_limit')
        entries=[]
        for file in files:
            if file.name.startswith('.') or file.suffix!='.md' or B.CREDENTIAL_FILE.search(file.name):
                return reject('unsafe_filename')
            fd=os.open(file,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as stream:
                st=os.fstat(stream.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_mode & 0o111:
                    return reject('unsafe_file_type_or_mode')
                if st.st_size>MAX_FILE_BYTES:return reject('file_size_limit')
                content=stream.read(MAX_FILE_BYTES+1)
                if len(content)>MAX_FILE_BYTES:return reject('file_size_limit')
                result['files']+=1;result['bytes']+=len(content)
                if result['bytes']>MAX_SOURCE_BYTES:return reject('source_size_limit')
                reason=text_reason(content,skill=file.name=='SKILL.md' and source.source_id.endswith('.skills'))
                if reason:return reject(reason)
                after=file.lstat()
                if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_mode)!=(
                        st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_mode):
                    return reject('file_changed','UNKNOWN')
                entries.append([str(file.relative_to(path)) if source.kind=='dir' else file.name,
                                'file',sha256_hex(content)])
        digest=entries[0][2] if source.kind=='file' else hash_obj([entries,[]])
        # Same sorted traversal and digest schema as baseline._hash_file/_hash_dir.
        # Re-inventory only after every entry has passed the bounded type check.
        current=B.inspect_source(source,home)
        if current.get('error') or any(current.get(k) for k in
            ('not_read','unsafe_symlinks','walk_errors','informational_private_dirs')):
            return reject('inventory_blocker','UNKNOWN')
        if digest!=saved.get('hash') or not B._same_protected(saved,current) or current.get('hash')!=digest:
            return reject('snapshot_changed','UNKNOWN')
        return {**result,'status':'ALLOW','reason':'bounded_markdown','source_hash':digest}
    except (OSError,ValueError,KeyError,TypeError):
        return reject('unreadable_or_changed','UNKNOWN')


def candidate(player: str, old: dict, current: dict, *, context: dict, scans: dict) -> dict:
    changes=[c for c in B.compare(old,current)['changes']
             if c['player'] in (player,'common') and c['category']!=B.INFORMATIONAL]
    sources=sorted({c['source'] for c in changes})
    versions=[s for s in sources if s.startswith('cli_version:')]
    catalog=context.get('catalog_changed') is True
    kind='text' if sources and not versions else 'cli_version' if versions else 'catalog' if catalog else 'none'
    reasons=[]
    if B.blockers(current,player=player):reasons.append('baseline_blocker')
    if kind=='text':
        if catalog:reasons.append('mixed_candidate')
        if any(s not in ALLOWLIST for s in sources):reasons.append('source_outside_allowlist')
        if any(scans.get(s,{}).get('status')!='ALLOW' for s in sources):reasons.append('format_not_allowed')
    elif kind=='cli_version':
        reasons.append('cli_version_observe_only')
        if player=='claude':reasons.append('claude_pin_changed')
        if sources!=[f'cli_version:{player}']:reasons.append('mixed_candidate')
    elif kind=='catalog' and player!='gpt':reasons.append('catalog_not_gpt')
    elif kind=='none':reasons.append('no_candidate')
    if not context.get('production_evidence_ok'):reasons.append('production_evidence_missing_or_changed')
    if not context.get('identity'):reasons.append('cli_identity_unknown')
    binding={'policy_version':VERSION,'player':player,'kind':kind,'replaces_baseline':old.get('record_id'),
        'protected_sources':{s:{'player':r['player'],'kind':r['kind'],'category':r['category'],
                               'fingerprint':B._fingerprint(r)} for s,r in current['sources'].items()
                             if r['player'] in (player,'common') and r['category']!=B.INFORMATIONAL},
        'version':current['versions'].get(player),'context':context,'scan_sha256':hash_obj(scans)}
    return {'type':'auto_reaccept_candidate','version':VERSION,'mode':MODE,'player':player,'kind':kind,
            'candidate_sha256':hash_obj(binding),'scope':sources,'format_eligible':not reasons,
            'reasons':sorted(set(reasons)),'binding':binding,'scans':scans,
            'model_calls':0,'baseline_approved':False}
