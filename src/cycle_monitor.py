"""Bounded metadata-only supervision; no model calls, API requests or resubmissions."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import time

from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.team_chat import Board
from flab.timeutil import parse_iso, to_iso, utc_now
from flab.workdir import PROJECT_ROOT
from forecast import Lock

VERSION = 'cycle_monitor_v1'


def inspect(root, campaign, question_ids, *, now):
    """Project allowlisted metadata only; never return payloads, reasons or model text."""
    start = parse_iso(campaign['budget_window_start'])
    def recent(name, field):
        return [r for r in read_complete(root / (name + '.jsonl'))
                if r.get(field) and parse_iso(r[field]) >= start]
    starts = [r for r in recent('forecast_attempt_starts', 'started_at_utc')
              if r.get('question_id') in question_ids]
    jobs = [r for r in recent('scheduler_jobs', 'finished_at')
            if r.get('campaign_id') == campaign['campaign_id'] and r.get('outcome') == 'selected']
    submissions = recent('submissions', 'at')
    requests = [r for r in submissions if r.get('status') == 'request_started']
    hazards = []
    if any(r.get('question_id') not in question_ids for r in requests):
        hazards.append('write_outside_selected_questions')
    counts = Counter((r.get('question_id'), r.get('operation')) for r in requests)
    if any(n > (1 if op == 'forecast' else 3) for (_, op), n in counts.items()):
        hazards.append('duplicate_or_excess_write')
    if any(r.get('status') in {'confirmed_failed', 'forecast_failed', 'comment_failed'}
           and r.get('question_id') in question_ids for r in submissions):
        hazards.append('write_failure_requires_host')
    latest = {r['request_id']: r for r in submissions if r.get('request_id')}
    unknown = [r for r in latest.values() if r.get('status') == 'unknown']
    if len(unknown) >= 2:
        hazards.append('multiple_unknown_requests')
    if any(r.get('status') == 'request_started' and
           (now - parse_iso(r['at'])).total_seconds() > 180 for r in latest.values()):
        hazards.append('request_without_outcome')
    ok = {op: sorted({r['question_id'] for r in submissions
                     if r.get('status') == op + '_ok' and r.get('question_id') in question_ids})
          for op in ('forecast', 'comment')}
    return {'question_ids': sorted(question_ids),
            'attempts': {p: sum(r.get('player') == p for r in starts) for p in ('claude', 'gemini', 'gpt')},
            'selected_jobs': {p: sum(r.get('player') == p for r in jobs) for p in ('claude', 'gemini', 'gpt')},
            'forecast_ok': ok['forecast'], 'comment_ok': ok['comment'],
            'unknown_requests': len(unknown), 'hazards': hazards,
            'writes_complete': bool(question_ids) and set(ok['forecast']) == set(ok['comment']) == set(question_ids)}


def stop(project):
    """Never remove or overwrite another operator's stop switch."""
    try:
        with (project / 'config/STOP_SUBMIT').open('x') as f:
            f.write(VERSION + ' metadata hazard; host review required; ' + to_iso(utc_now()) + '\n')
    except FileExistsError:
        pass


def run(project, campaign_path, *, sleep=time.sleep, now=utc_now):
    with Lock(Path(project) / 'data/cycle_monitor.lock'):
        return _run(project, campaign_path, sleep=sleep, now=now)


def _run(project, campaign_path, *, sleep, now):
    project = Path(project)
    root = project / 'data'
    campaign = json.loads(Path(campaign_path).read_text())
    selected = [r for r in read_complete(root / 'main_site_trial_selections.jsonl')
                if r.get('campaign_id') == campaign['campaign_id']]
    ids = {r['question_id'] for r in selected}
    if not ids or len(ids) != campaign.get('main_site_trial', {}).get('max_questions'):
        raise ValueError('monitor requires a fully selected bounded main-site campaign')
    end = parse_iso(campaign['budget_window_end'])
    if not now() < end or (end - now()).total_seconds() > 86400:
        raise ValueError('monitor requires an active window ending within 24 hours')
    board = Board(project)
    progress = [r for r in board.events() if r.get('type') == 'progress']
    own_progress_id = progress[-1]['event_id'] if progress else None
    previous, read_failures = None, 0
    while True:
        at = now()
        status = 'monitoring'
        try:
            if json.loads(Path(campaign_path).read_text()) != campaign:
                status = 'campaign_changed'
            elif json.loads((project / 'config/submit_mode.json').read_text()).get('mode') != 'trial':
                status = 'mode_changed'
            elif at >= end:
                status = 'window_closed'
            elif (project / 'config/STOP_SUBMIT').exists():
                status = 'stopped_by_operator'
            observation = inspect(root, campaign, ids, now=at)
            read_failures = 0
        except (ValueError, OSError, KeyError, TypeError):
            # Writers may be between append and newline; retry without exposing the row.
            read_failures += 1
            if read_failures < 3:
                sleep(5)
                continue
            observation = {'hazards': ['ledger_unreadable'], 'writes_complete': False}
        if observation['hazards'] and status == 'monitoring':
            stop(project)
            status = 'stopped_for_hazard'
        elif observation['writes_complete'] and status == 'monitoring':
            status = 'writes_complete_requires_host_readback_check'
        state = {'status': status, **observation}
        if state != previous:
            record = {'type': 'cycle_monitor', 'version': VERSION, 'at': to_iso(at),
                      'campaign_id': campaign['campaign_id'], **state}
            append_record(root / 'cycle_monitor.jsonl', record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            # Publish metadata, not notifications. A new host progress supersedes this watcher.
            with transaction(board.root):
                progress = [r for r in board.events() if r.get('type') == 'progress']
                latest_id = progress[-1]['event_id'] if progress else None
                if latest_id == own_progress_id:
                    board.publish('自動狀態監看（不是桌面主持人持續在線）\n'
                    '來源：PLAN D108／D110；已核准 D105 窗口；cycle_monitor.jsonl。\n'
                    '完成／目前狀態：' + json.dumps(state, ensure_ascii=False) + '\n'
                    '待驗：兩題型端到端及獨立讀回須主持人確認；A2 尚未開始。\n'
                    '下一步：無異常時等待既有 launchd 接續；停送時由主持人處理，unknown 只對帳不重送。\n'
                    '本程式不呼叫模型、不存取 API、不送出預測、不執行 A2，也不解除停送。', source=VERSION)
                    own_progress_id = [r for r in board.events() if r.get('type') == 'progress'][-1]['event_id']
            previous = state
        if status != 'monitoring':
            return state
        sleep(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    args = parser.parse_args()
    run(PROJECT_ROOT, args.campaign)


if __name__ == '__main__':
    main()
