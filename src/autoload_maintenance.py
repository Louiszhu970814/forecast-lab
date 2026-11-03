"""D124 bounded global-marker maintenance. No platform requests or baseline approvals."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

import autoload as A
import baseline as B
import budget_handoff as H
import forecast as F
import live_window as W
import quota
from flab import orphan
from flab.config import load_run_config, load_quota_policy
from flab.ids import new_id
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import parse_iso, to_iso, utc_now
from flab.workdir import PROJECT_ROOT

VERSION = 'autoload_maintenance_v1'
AUTHORIZATION = 'PLAN D121 C, D124'
LOCKS = ('cycle.lock', 'scheduler.lock', 'submit.lock',
         'forecast.claude.lock', 'forecast.gemini.lock', 'forecast.gpt.lock')
MAX_WAIT_S = 900
DECISION_MARGIN_S = 1800


class MaintenanceError(ValueError):
    pass


def weekly_usage(root, campaign, *, now):
    """Same retained accounting start and unique-call counting as Budget.reserve."""
    start, end = H.accounting_start(root, campaign), parse_iso(campaign['budget_window_end'])
    if not parse_iso(campaign['budget_window_start']) <= now < end:
        raise MaintenanceError('inactive campaign')
    starts = read_complete(root/'forecast_attempt_starts.jsonl')
    forecasts = read_complete(root/'forecasts.jsonl')
    acceptance = read_complete(root/'acceptance.jsonl')
    result = {}
    for player in B.PLAYERS:
        ids = {r['attempt_id'] for r in starts if r.get('player') == player and
               start <= parse_iso(r['started_at_utc']) < end}
        ids.update(r['attempt_id'] for r in forecasts if r.get('player') == player and
                   start <= parse_iso(r.get('started_at_utc') or r['finished_at_utc']) < end)
        calls = [r for r in acceptance if r.get('type') == 'acceptance_call_started' and r.get('player') == player]
        runs = {r['run_id'] for r in calls}
        count = len({r['attempt_id'] for r in calls if start <= parse_iso(r['started_at']) < end})
        count += sum(r.get('model_call_count', 3) for r in acceptance
                     if r.get('type') == 'acceptance_result' and r.get('player') == player and
                     r.get('run_id') not in runs and start <= parse_iso(r['finished_at']) < end)
        result[player] = len(ids) + count
    return result


def require_idle(root, *, now, horizon_s, recovering=None, process_check=orphan.no_process):
    starts = read_complete(root/'forecast_attempt_starts.jsonl')
    done = {r.get('attempt_id') for r in read_complete(root/'forecasts.jsonl')}
    pending = [r for r in starts if r.get('attempt_id') not in done]
    acceptance = read_complete(root/'acceptance.jsonl')
    completed_runs = {r.get('run_id') for r in acceptance
                      if r.get('type') in ('acceptance_result', 'autoload_result')}
    finished_calls = {r.get('attempt_id') for r in acceptance if r.get('type') == 'acceptance_call_finished'}
    pending += [r for r in acceptance if r.get('type') == 'acceptance_call_started' and
                r.get('run_id') != recovering and r.get('run_id') not in completed_runs and
                r.get('attempt_id') not in finished_calls]
    if pending:
        raise MaintenanceError('unresolved model or acceptance attempt')
    # Even closed attempts may have leaked a child. Scan their registered paths;
    # missing ps/lsof evidence is not proof of absence.
    paths = {Path(r[key]) for r in starts + [r for r in acceptance if r.get('type') == 'acceptance_call_started']
             for key in ('workdir', 'raw_dir') if r.get(key) and
             (recovering is None or r.get('run_id') != recovering)}
    if paths and not process_check(paths):
        raise MaintenanceError('registered attempt process still present or unknown')
    if recovering is not None:
        return  # Restoration must not wait for deadlines or unfinished platform writes.
    submissions = read_complete(root/'submissions.jsonl')
    requests = {r['request_id']: r for r in submissions if r.get('request_id')}
    if any(r.get('status') in ('unknown', 'request_started') for r in requests.values()):
        raise MaintenanceError('unresolved write request')
    intents = {r['intent_id'] for r in submissions if r.get('status') == 'intent'}
    closed = {r.get('intent_id') for r in submissions if r.get('status') in ('comment_ok', 'abandoned')}
    if intents - closed:
        raise MaintenanceError('unfinished submission intent')
    sent = {r.get('question_id') for r in submissions if r.get('status') == 'forecast_ok'}
    questions = {r['question_id']: r for r in read_complete(root/'questions.jsonl') if r.get('question_id')}
    for qid, question in questions.items():
        if qid in sent or not question.get('supported') or question.get('status') not in ('open', 'upcoming'):
            continue
        end = question.get('scheduled_close_time')
        if end and 0 < (parse_iso(end) - now).total_seconds() <= horizon_s + DECISION_MARGIN_S:
            raise MaintenanceError('unanswered question too close to maintenance horizon')


class Session:
    def __init__(self, project=PROJECT_ROOT, *, now=utc_now, monotonic=time.monotonic,
                 sleep=time.sleep, wait_s=MAX_WAIT_S, snapshot=None, preflight=None,
                 execute=A.execute, idle=require_idle):
        if not 0 <= wait_s <= MAX_WAIT_S:
            raise ValueError('lock wait out of bounds')
        self.project, self.now, self.monotonic, self.sleep = Path(project), now, monotonic, sleep
        self.root, self.wait_s = self.project/'data', wait_s
        self.log = self.root/'autoload_maintenance.jsonl'
        self.snapshot_fn, self.preflight_fn, self.execute, self.idle = snapshot, preflight, execute, idle
        self.quota_observed = None

    def event(self, kind, **values):
        with transaction(self.root):
            append_record(self.log, {'type':kind, 'version':VERSION, 'at':to_iso(self.now()),
                                    'authorization':AUTHORIZATION, **values})

    @contextmanager
    def locks(self):
        until = self.monotonic() + self.wait_s
        while True:
            stack = ExitStack()
            try:
                for name in LOCKS:
                    stack.enter_context(F.Lock(self.root/name))
            except F.PreflightError:
                stack.close()
                left = until - self.monotonic()
                if left <= 0:
                    raise MaintenanceError('maintenance lock wait expired') from None
                self.sleep(min(15, left))
            except BaseException:
                stack.close()
                raise
            else:
                break
        with stack:
            yield

    @contextmanager
    def production_path(self):
        old = os.environ.get('PATH')
        updater = os.environ.get('DISABLE_AUTOUPDATER')
        os.environ['PATH'] = ':'.join([str(self.project/'launchd/bin'),str(Path.home()/'.local/bin'),
            '/opt/homebrew/bin','/usr/local/bin','/usr/bin','/bin','/usr/sbin','/sbin'])
        os.environ['DISABLE_AUTOUPDATER'] = '1'
        try:
            yield
        finally:
            for name, value in (('PATH',old),('DISABLE_AUTOUPDATER',updater)):
                if value is None: os.environ.pop(name, None)
                else: os.environ[name] = value

    def identities(self):
        result = {}
        for player, name in (('claude','claude'),('gpt','codex'),('gemini','agy')):
            binary = shutil.which(name)
            if not binary:
                raise MaintenanceError('official CLI unavailable')
            path = Path(binary)
            if player == 'claude' and path != self.project/'launchd/bin/claude':
                raise MaintenanceError('Claude is not the project wrapper')
            result[player] = {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            if player == 'claude':
                pin = self.root/'cli_pins/claude/2.1.316'
                result[player]['pinned_sha256'] = hashlib.sha256(pin.read_bytes()).hexdigest()
        return result

    def snapshot(self, *, admit=False):
        if self.snapshot_fn:
            return self.snapshot_fn()
        observed = quota.snapshot(timeout_s=30)
        if not quota.observation_fresh(observed, self.now()) or observed.remaining_credits != 0:
            raise MaintenanceError('fresh zero-credit observation required')
        if admit:
            cfg = load_run_config(self.project/'config')
            reason, _ = quota.manual_admission(observed, load_quota_policy(self.project/'config'),
                model=cfg['players']['gemini']['tier1']['model'], history_root=self.root)
            if reason:
                raise MaintenanceError('Gemini admission refused before placement')
        versions = B.cli_versions()
        if versions.get('claude') != '2.1.316':
            raise MaintenanceError('Claude pin differs')
        snap = B.take_snapshot(versions=versions, gemini_credits=0).to_record()
        if not quota.observation_fresh(observed,self.now()):
            raise MaintenanceError('quota expired during inventory')
        self.quota_observed = observed
        return snap

    def preflights(self):
        result = {}
        for player in B.PLAYERS:
            for tier in (1,2):
                if self.preflight_fn:
                    pf = self.preflight_fn(player,tier)
                else:
                    import importlib
                    pf = F.preflight(player,tier=tier,continuous=True,
                                     acceptance_log=self.root/'acceptance.jsonl',
                                     adapter=importlib.import_module('adapters.'+player))
                result[f'{player}:{tier}'] = pf.summary()
                if not pf.ok:
                    raise MaintenanceError('post-maintenance preflight rejected')
        return result

    def stop(self, run_id):
        try:
            with (self.project/'config/STOP_SUBMIT').open('x') as stream:
                stream.write(f'{VERSION}:{run_id}\n'); stream.flush(); os.fsync(stream.fileno())
        except FileExistsError:
            pass

    def protected_hashes(self):
        paths = [f'config/{name}.json' for name in
                 ('run_config_v2','quota_policy','cycle_campaign','submit_mode','question_types')]
        paths += ['src/adapters/'+p+'.py' for p in B.PLAYERS]
        paths += ['src/flab/runner.py','src/flab/netguard.py']
        return {p:hashlib.sha256((self.project/p).read_bytes()).hexdigest() for p in paths}

    @contextmanager
    def recovery_locks(self, run_id):
        if not isinstance(run_id,str) or not A.re.fullmatch(r'autoload_[A-Za-z0-9_]+',run_id):
            raise MaintenanceError('valid managed run id required')
        with self.production_path(), self.locks():
            try:
                yield
            except BaseException:
                self.stop(run_id)
                raise

    def run(self, *, approved_by):
        if not approved_by.strip():
            raise ValueError('named maintenance authorization required')
        cfg = load_run_config(self.project/'config')
        # Full per-line timeouts, quota/catalog overhead and restoration reserve.
        bound = 3*cfg['timeouts_s']['tier1'] + 3*120 + 300
        run_id = new_id('autoload')
        with self.production_path(), self.locks():
            if (self.project/'config/STOP_SUBMIT').exists() or A.unclosed(self.root/'acceptance.jsonl'):
                raise MaintenanceError('stop or prior unclosed maintenance')
            campaign = json.loads((self.project/'config/cycle_campaign.json').read_text())
            mode = json.loads((self.project/'config/submit_mode.json').read_text())
            W.require_active(mode,campaign,self.project/'config',self.now())
            if (parse_iso(campaign['budget_window_end'])-self.now()).total_seconds() <= bound+DECISION_MARGIN_S:
                raise MaintenanceError('maintenance exceeds live window')
            history = read_complete(self.log)
            prior_runs = {r['run_id'] for r in history if r.get('type') == 'maintenance_started'}
            calls = [r for r in read_complete(self.root/'acceptance.jsonl') if
                     r.get('type') == 'acceptance_call_started' and r.get('run_id') in prior_runs]
            if calls or len(prior_runs) >= 2:
                raise MaintenanceError('D124 bounded probe already consumed')
            self.idle(self.root,now=self.now(),horizon_s=bound)
            identity = self.identities()
            before = self.snapshot(admit=True)
            approved = B.latest_approved(self.root/'acceptance.jsonl')
            if not approved or set(B.compare(approved,before)['decisions'].values()) != {'ok'}:
                raise MaintenanceError('baseline is not approved for every line')
            hashes = self.protected_hashes()
            self.preflights()
            policy = load_quota_policy(self.project/'config')
            def capacity(req=None):
                usage = weekly_usage(self.root,campaign,now=self.now())
                for player in ([req.player] if req else B.PLAYERS):
                    cap = min(campaign['weekly_attempt_caps'][player],policy['players'][player]['weekly_attempts_cap'])
                    if usage[player] >= cap:
                        raise MaintenanceError('weekly acceptance capacity exhausted')
            # Refresh after all external queries, still before global mutation.
            refreshed = self.snapshot(admit=True)
            if any(c['category'] != B.INFORMATIONAL for c in B.compare(before,refreshed)['changes']):
                raise MaintenanceError('baseline changed during precheck')
            before = refreshed
            self.idle(self.root,now=self.now(),horizon_s=bound)
            if identity != self.identities() or hashes != self.protected_hashes() or \
                    (parse_iso(campaign['budget_window_end'])-self.now()).total_seconds() <= bound+DECISION_MARGIN_S:
                raise MaintenanceError('precheck state or maintenance horizon changed')
            if self.quota_observed and not quota.observation_fresh(self.quota_observed,self.now()):
                raise MaintenanceError('quota expired before placement')
            with transaction(self.root):
                capacity()
                self.event('maintenance_started',run_id=run_id,approved_by=approved_by,
                           bound_s=bound,identities=identity,protected_sha256=hashes,
                           baseline_before=before)
            result = None
            deadline = self.monotonic()+bound-300
            def before_call(req):
                capacity(req)
                if self.monotonic()+req.timeout_s+120 > deadline:
                    raise MaintenanceError('insufficient remaining maintenance time before start')
            def execute(req):
                if self.monotonic()+req.timeout_s > deadline:
                    raise MaintenanceError('insufficient remaining maintenance time')
                return self.execute(req)
            try:
                result = A.run(approved_by=approved_by,confirmed=True,data_dir=self.root,
                               executor=execute,run_id=run_id,before_call=before_call,timeout_s=bound-300)
                if result['restore_ok'] is not True or A.unclosed(self.root/'acceptance.jsonl'):
                    raise MaintenanceError('autoload restoration is incomplete')
                calls=[r for r in read_complete(self.root/'acceptance.jsonl') if
                       r.get('type')=='acceptance_call_started' and r.get('run_id')==run_id]
                paths={Path(r[k]) for r in calls for k in ('workdir','raw_dir') if r.get(k)}
                if paths and not orphan.no_process(paths):
                    raise MaintenanceError('post-probe process absence unverified')
                after = self.snapshot()
                if any(c['category'] != B.INFORMATIONAL for c in B.compare(before,after)['changes']):
                    raise MaintenanceError('protected state changed during maintenance')
                if identity != self.identities() or hashes != self.protected_hashes():
                    raise MaintenanceError('CLI identity or frozen config changed')
                if any(r.get('error') in ('tool_scope_violation','model_mismatch') for r in result['errors']):
                    raise MaintenanceError('probe reported forbidden scope or model')
                preflights = self.preflights()
                record = {'status':'complete' if result['passed'] else 'measurement_failed',
                          'run_id':run_id,'autoload_record_id':result['record_id'],
                          'restore_ok':True,'preflight':preflights,
                          'outcomes':[{'player':r['player'],'kind':r['kind'],'outcome':r['outcome']}
                                      for r in result['locations']]}
                self.event('maintenance_finished',**record)
                return record
            except BaseException as exc:
                self.stop(run_id)
                self.event('maintenance_failed',run_id=run_id,error_type=type(exc).__name__,
                           restore_ok=bool(result and result.get('restore_ok')))
                raise

    def recover(self, run_id, *, resolved_by=None):
        with self.recovery_locks(run_id):
            starts = [r for r in read_complete(self.root/'acceptance.jsonl')
                      if r.get('type') == 'autoload_run_started' and r.get('run_id') == run_id]
            if len(starts) != 1:
                raise MaintenanceError('managed run missing')
            start = starts[0]
            current_boot = orphan.boot_id()
            if not current_boot or not start.get('boot_id'):
                raise MaintenanceError('controller boot identity unknown')
            if start['boot_id'] == current_boot and start.get('pid') != os.getpid():
                try: os.kill(int(start['pid']),0)
                except ProcessLookupError: pass
                else: raise MaintenanceError('original controller may still be alive')
            calls = [r for r in read_complete(self.root/'acceptance.jsonl') if
                     r.get('type') == 'acceptance_call_started' and r.get('run_id') == run_id]
            paths = {Path(r[k]) for r in calls for k in ('workdir','raw_dir') if r.get(k)}
            if paths and not orphan.no_process(paths):
                raise MaintenanceError('probe process absence not proven')
            self.idle(self.root,now=self.now(),horizon_s=0,recovering=run_id)
            records = [r for r in read_complete(self.root/'acceptance.jsonl')
                       if r.get('type') == 'autoload_result' and r.get('run_id') == run_id]
            if run_id in A.unclosed(self.root/'acceptance.jsonl'):
                base = Path(start['attempt_base'])
                for call in calls:
                    workdir = Path(call['workdir'])
                    if workdir.parent != base or not A.re.fullmatch(r'[a-f0-9]{32}',workdir.name):
                        raise MaintenanceError('unexpected registered attempt directory')
                    A.remove_attempt_dir(workdir,base)
                failures = A.recover_placement(self.root,run_id,resolved_by=resolved_by)
                record = {'type':'autoload_result','version':A.VERSION,'source':'autoload','record_id':new_id('auto'),
                          'run_id':run_id,'passed':False,'restore_ok':not failures,'restore_failures':failures,
                          'error':'interrupted','finished_at':to_iso(self.now()),'recovered':True}
                if resolved_by is not None:record.update(resolution='host_attested',byte_exact=False,resolved_by=resolved_by)
                with transaction(self.root):
                    finished = {r.get('attempt_id') for r in read_complete(self.root/'acceptance.jsonl')
                                if r.get('type') == 'acceptance_call_finished'}
                    for call in calls:
                        if call['attempt_id'] not in finished:
                            append_record(self.root/'acceptance.jsonl',{'type':'acceptance_call_finished',
                                'source':'autoload','run_id':run_id,'attempt_id':call['attempt_id'],
                                'player':call['player'],'completed':False,'error_type':'interrupted',
                                'finished_at':to_iso(self.now())})
                    append_record(self.root/'acceptance.jsonl',record)
                if failures:
                    self.stop(run_id)
                    raise MaintenanceError('restoration conflict remains')
            else:
                record = records[-1]
            maintenance = [r for r in read_complete(self.log)
                           if r.get('type') == 'maintenance_started' and r.get('run_id') == run_id]
            if len(maintenance) != 1 or maintenance[0]['identities'] != self.identities() or \
                    maintenance[0]['protected_sha256'] != self.protected_hashes():
                self.stop(run_id)
                raise MaintenanceError('maintenance identity or config changed; host review required')
            self.preflights()
            stop = self.project/'config/STOP_SUBMIT'
            if stop.exists() and stop.read_text() == f'{VERSION}:{run_id}\n':
                stop.unlink()
            if not any(r.get('type') == 'maintenance_recovered' and r.get('run_id') == run_id
                       for r in read_complete(self.log)):
                self.event('maintenance_recovered',run_id=run_id,restore_ok=True)
            return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('run','recover'))
    parser.add_argument('--approved-by',required=True)
    parser.add_argument('--i-understand-this-edits-global-files',action='store_true')
    parser.add_argument('--run-id')
    parser.add_argument('--resolve-conflict',action='store_true')
    parser.add_argument('--resolved-by')
    args = parser.parse_args()
    session = Session()
    if not args.i_understand_this_edits_global_files:
        parser.error('explicit global-file confirmation required')
    if args.resolve_conflict != bool(args.resolved_by and args.resolved_by.strip()):
        parser.error('host conflict resolution requires both --resolve-conflict and --resolved-by')
    result = session.run(approved_by=args.approved_by) if args.command=='run' else session.recover(
        args.run_id,resolved_by=args.resolved_by if args.resolve_conflict else None)
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()
