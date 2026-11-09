from __future__ import annotations

import hashlib
import json
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from flab.team_chat import Board, TeamWorker, parse_team, MODELS
from flab.team_runner import parse_answer, run_action, clean_env
from flab.dev_tasks import TaskError
from flab.timeutil import to_iso
from test_telegram_bot import project, message, service, NOW


def board(project):
    return Board(project, now=lambda: NOW)


def ask(b, *, actions=None, name='M3-1'):
    b.ask(name, '選擇後續工作', {'A':'整理文件', 'B':'離線驗證'},
        expires_at=to_iso(NOW+timedelta(hours=1)), actions=actions)


def script(project, text="print('done')"):
    p=project/'config/team_actions/check.py';p.parent.mkdir();p.write_text(text)
    return {'script':'config/team_actions/check.py','sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'timeout':5}


def test_board_never_reads_forecast_sources_and_warns_staleness(project):
    b=board(project);b.publish('M3 尚未完成，無需使用者決定。',source='docs/tasks/HANDOFF.md; D90')
    (project/'data/forecasts.jsonl').write_text('DO_NOT_READ')
    assert 'DO_NOT_READ' not in b.snapshot()
    late=Board(project,now=lambda:NOW+timedelta(hours=2))
    assert '超過一小時' in late.snapshot()


def test_no_snapshot_does_not_claim_completed(project):
    assert '無法確認 M2／M3' in board(project).snapshot()


@pytest.mark.parametrize('text,kind',[('GPT，M3 做完了嗎','question'),('claude hi','question'),
    ('Gemini: 卡在哪','question'),('進度','board'),('待決事項','decisions'),
    ('決定 M3-1 b','decision'),('決定 執行任意命令','decision_help')])
def test_parse(text,kind):
    assert parse_team(text)[0]==kind


def test_old_dev_route_retired_and_private_queries_default_gpt(project):
    bot=service(project)
    bot.handle(message(1,'開發：製作網站'));assert not bot.tasks.tasks()
    bot.handle(message(2,'現在 M3 完成了嗎？'));assert len(bot.board.questions())==1
    assert next(iter(bot.board.questions().values()))['player']=='gpt'
    bot.handle(message(3,'Gemini，說明進度',owner=99));assert len(bot.board.questions())==1
    bot.handle(message(4,'Claude，說明進度',date=1));assert len(bot.board.questions())==1


def test_decision_replay_and_old_message_does_not_execute(project):
    b=board(project);ask(b)
    bot=service(project)
    bot.handle(message(1,'決定 M3-1 B',owner=99));assert b.decisions()['M3-1']['status']=='open'
    bot.handle(message(2,'決定 M3-1 B',date=1));assert b.decisions()['M3-1']['status']=='open'
    # Crash between decision journal and Telegram cursor: one acceptance only.
    b.choose(bot.dev_owner,'update:3','M3-1','B')
    bot.handle(message(3,'決定 M3-1 B'))
    assert len([r for r in b.events() if r['type']=='decision_accepted'])==1
    assert b.decisions()['M3-1']['status']=='waiting_coordinator'
    b.choose(bot.dev_owner,'update:4','M3-1','A')
    assert b.decisions()['M3-1']['choice']=='A'
    assert '待主持人接手' in b.snapshot()


def test_unique_expiring_options_and_action_integrity(project):
    b=board(project);spec=script(project);ask(b,actions={'B':spec})
    with pytest.raises(TaskError): ask(b)
    with pytest.raises(TaskError): b.choose('o','1','M3-1','C')
    with pytest.raises(TaskError): Board(project,now=lambda:NOW+timedelta(hours=2)).choose('o','1','M3-1','B')
    (project/spec['script']).write_text('print("changed")')
    with pytest.raises(TaskError): b.choose('o','1','M3-1','B')
    assert b.decisions()['M3-1']['status']=='open'


def test_full_phone_decision_to_reviewed_local_continuation_once(project):
    b=board(project);spec=script(project,"from pathlib import Path\np=Path('marker.txt')\nassert not p.exists()\np.write_text('ran')\n")
    ask(b,actions={'B':spec})
    bot=service(project);bot.handle(message(1,'決定 M3-1 B'))
    w=TeamWorker(project,bot.dev_owner,answer=lambda *a:'unused',action=run_action,now=lambda:NOW)
    assert w.step();assert not w.step()
    assert (project/'marker.txt').read_text()=='ran'
    assert b.decisions()['M3-1']['status']=='succeeded'
    bot.handle(message(2,'待決事項'));bot.deliver()
    assert any('續作完成' in text for _,text in bot.api.sent)


def test_drift_after_acceptance_stops_before_spawn(project):
    b=board(project);spec=script(project);ask(b,actions={'B':spec});b.choose('o','1','M3-1','B')
    (project/spec['script']).write_text('CHANGED')
    w=TeamWorker(project,'o',answer=lambda *a:'unused',action=lambda *a:pytest.fail('must not spawn'),now=lambda:NOW)
    w.step();assert b.decisions()['M3-1']['status']=='failed'


def test_restart_never_replays_unknown_start(project):
    b=board(project);spec=script(project);ask(b,actions={'B':spec});b.choose('o','1','M3-1','B')
    b.record('action_started',decision_id='M3-1',status='running')
    q=b.submit('o','2','gpt','M3？');b.record('answer_started',request_id=q['request_id'],status='running')
    w=TeamWorker(project,'o',answer=lambda *a:pytest.fail('no retry'),action=lambda *a:pytest.fail('no retry'),now=lambda:NOW)
    w.recover();assert not w.step()
    assert b.decisions()['M3-1']['status']=='unknown'
    assert next(iter(b.questions().values()))['outcome']=='interrupted'


def test_answer_routing_history_and_replay(project):
    bot=service(project);bot.handle(message(1,'Gemini，M3？'))
    calls=[]
    def answer(p,player,q,context,halt):
        calls.append((player,context));return '依紀錄，尚未完成。'
    w=TeamWorker(project,bot.dev_owner,answer=answer,action=lambda *a:None,now=lambda:NOW)
    assert w.step();assert not w.step()
    bot.enqueue_answers();bot.enqueue_answers();bot.deliver()
    assert len(calls)==1 and calls[0][0]=='gemini'
    assert sum('gemini-3.8-flash-low' in text for _,text in bot.api.sent)==1
    bot=service(project,bot.api);bot.enqueue_answers();bot.deliver()
    assert sum('gemini-3.8-flash-low' in text for _,text in bot.api.sent)==1
    assert b'NEVER' not in (project/'data/team_chat/events.jsonl').read_bytes()


def test_queue_cap_secrets_and_repair_not_called(project):
    b=board(project)
    with pytest.raises(TaskError): b.submit('o','s','gpt','TOKEN=private_token_123')
    for i in range(5): b.submit('o',str(i),'gpt','M3?')
    with pytest.raises(TaskError): b.submit('o','6','gpt','M3?')
    count=[]
    def fail(*a): count.append(1);raise RuntimeError('SENSITIVE CLI ERROR')
    w=TeamWorker(project,'o',answer=fail,action=lambda *a:None,now=lambda:NOW);w.step()
    assert len(count)==1 and 'SENSITIVE' not in json.dumps(b.events())


def test_expired_query_and_other_owner_do_not_use_model(project):
    b=board(project);b.submit('o','1','gpt','M3?')
    w=TeamWorker(project,'other',answer=lambda *a:pytest.fail(),action=lambda *a:None,now=lambda:NOW)
    assert not w.step()
    w=TeamWorker(project,'o',answer=lambda *a:pytest.fail(),action=lambda *a:None,now=lambda:NOW+timedelta(minutes=11))
    assert w.step();assert next(iter(b.questions().values()))['outcome']=='expired'


def test_malformed_ledger_is_fail_closed(project):
    b=board(project);b.root.mkdir(parents=True);b.path.write_text('{"secret":"hidden"')
    with pytest.raises(TaskError): b.submit('o','1','gpt','hi')
    bot=service(project);bot.handle(message(1,'進度'));bot.deliver()
    assert 'hidden' not in str(bot.api.sent)


def test_model_context_contains_only_board_and_own_bounded_chat(project):
    b=board(project);b.publish('M3 pending',source='D90')
    q=b.submit('someone_else','1','claude','OTHER_PRIVATE')
    b.record('answer_finished',request_id=q['request_id'],status='finished',answer='OTHER_PRIVATE')
    assert 'OTHER_PRIVATE' not in json.dumps(b.context('owner','new'))


def test_environment_drops_credentials_and_app_injection(monkeypatch,tmp_path):
    monkeypatch.setenv('METACULUS_TOKEN','x');monkeypatch.setenv('TELEGRAM_BOT_TOKEN','x')
    monkeypatch.setenv('ANTHROPIC_API_KEY','x');monkeypatch.setenv('OPENAI_API_KEY','x')
    monkeypatch.setenv('PYTHONPATH','x')
    env=clean_env(tmp_path)
    assert not {'METACULUS_TOKEN','TELEGRAM_BOT_TOKEN','ANTHROPIC_API_KEY','OPENAI_API_KEY','PYTHONPATH'} & env.keys()


@pytest.mark.parametrize('player,event',[
    ('gpt',{'type':'item.completed','item':{'type':'command_execution'}}),
    ('claude',{'type':'assistant','message':{'content':[{'type':'tool_use','name':'Read'}]}}),
    ('gemini',{'event':'step_update','step_update':{'step_index':1,'step_type':'tool','tool_info':{'name':'run_command'}}}),
])
def test_unexpected_model_tools_rejected(player,event,tmp_path):
    with pytest.raises(ValueError): parse_answer(player,json.dumps(event),tmp_path)


def test_output_format_transport_is_allowed_but_model_mismatch_rejected(tmp_path):
    events=[{'type':'assistant','message':{'content':[{'type':'tool_use','name':'StructuredOutput'}]}},
        {'type':'result','modelUsage':{'claude-sonnet-5-5':{}},'structured_output':{'answer':'尚未完成'}}]
    assert parse_answer('claude','\n'.join(map(json.dumps,events)),tmp_path)=='尚未完成'
    events[-1]['modelUsage']={'unexpected-model':{}}
    with pytest.raises(ValueError):parse_answer('claude','\n'.join(map(json.dumps,events)),tmp_path)


def test_daily_bound_counts_original_request_date_not_completion(project):
    b=board(project)
    for i in range(60):
        q=b.submit('o',str(i),'gpt','M3?')
        b.record('answer_finished',request_id=q['request_id'],status='finished',answer='pending')
    with pytest.raises(TaskError):b.submit('o','extra','gpt','M3?')
    later=Board(project,now=lambda:NOW+timedelta(days=1))
    later.record('answer_finished',request_id=q['request_id'],status='finished',answer='late')
    for i in range(5):later.submit('o','new'+str(i),'gpt','M3?')
    assert sum(q['created_at'][:10]==to_iso(NOW)[:10] for q in later.questions().values())==60


def test_chat_worker_does_not_block_phone_status_or_decision(project):
    import time
    bot=service(project);bot.board.publish('M3 pending',source='synthetic')
    ask(bot.board);bot.handle(message(1,'GPT，M3？'))
    entered=threading.Event()
    def answer(*args):
        entered.set();args[-1].wait(3);return '尚未完成'
    w=TeamWorker(project,bot.dev_owner,answer=answer,action=lambda *a:None,now=lambda:NOW)
    w.start()
    try:
        assert entered.wait(1)
        before=time.monotonic()
        bot.handle(message(2,'進度'));bot.handle(message(3,'決定 M3-1 B'));bot.deliver()
        assert time.monotonic()-before<1
        assert bot.board.decisions()['M3-1']['status']=='waiting_coordinator'
    finally:
        w.stop();w.join(4)


def test_live_service_migrates_legacy_schedule_and_leaves_old_tasks_idle(project):
    from test_telegram_bot import FakeAPI
    import telegram_bot as T
    api=FakeAPI();bot=service(project,api)
    bot.record('old_schedule',key='legacy',schedule=vars(T.Schedule('daily')))
    bot.record('outbox',key='schedule:legacy:old',scheduled_for=to_iso(NOW),text='old push')
    task=bot.tasks.submit(bot.dev_owner,'legacy','old task')
    with pytest.raises(KeyboardInterrupt):bot.run()
    assert bot.state.schedule.mode=='off'
    assert bot.tasks.tasks()[task['task_id']]['status']=='queued'
    bot.deliver();assert not api.sent


def test_amend_queued_action_revokes_old_selection_and_replay(project):
    b=board(project);spec=script(project);ask(b,actions={'B':spec})
    b.choose('o','1','M3-1','B')
    b.choose('o','2','M3-1','A')
    assert b.choose('o','1','M3-1','B')['choice']=='A'
    assert b.decisions()['M3-1']['revision']==2
    w=TeamWorker(project,'o',answer=lambda *a:None,action=lambda *a:pytest.fail('revoked'),now=lambda:NOW)
    assert not w.step()
    b.choose('o','3','M3-1','B')
    calls=[];w.action=lambda *a:calls.append(1)
    assert w.step() and calls==[1]
    assert not w.step()
    with pytest.raises(TaskError,match='已完成'): b.choose('o','4','M3-1','A')


@pytest.mark.parametrize('status,expected', [('closed','已結案'),('running','已開始'),
    ('coordinator_running','已開始'),('succeeded','已完成'),('failed','執行失敗'),('unknown','結果不明')])
def test_decision_rejection_identifies_exact_state(project,status,expected):
    b=board(project);ask(b)
    b.record('action_finished',decision_id='M3-1',status=status)
    with pytest.raises(TaskError,match=expected): b.choose('o','1','M3-1','B')


def test_missing_expired_and_cross_owner_decisions(project):
    b=board(project);ask(b)
    with pytest.raises(TaskError,match='不存在'): b.choose('o','1','missing','A')
    b.choose('o','2','M3-1','A')
    with pytest.raises(TaskError,match='其他配對'): b.choose('different','3','M3-1','B')
    late=Board(project,now=lambda:NOW+timedelta(hours=2))
    with pytest.raises(TaskError,match='已到期'): late.choose('o','3','M3-1','B')
    assert '可選／未開工可修改' not in late.decision_text()
    with pytest.raises(TaskError,match='已到期'): late.claim('M3-1',coordinator='Claude',revision=1)


def test_atomic_claim_prevents_amendment_and_rejects_stale_revision(project):
    b=board(project);ask(b);b.choose('o','1','M3-1','A');b.choose('o','2','M3-1','B')
    with pytest.raises(TaskError,match='已修改'): b.claim('M3-1',coordinator='Claude',revision=1)
    b.claim('M3-1',coordinator='Claude',revision=2)
    with pytest.raises(TaskError,match='已開始'): b.choose('o','3','M3-1','A')
    text=b.decision_text();assert '實際接手者：Claude' in text and '可選／未開工可修改' not in text
    # Telegram worker restart cannot claim knowledge of a separate desktop agent.
    w=TeamWorker(project,'o',answer=lambda *a:None,action=lambda *a:None,now=lambda:NOW)
    w.recover();assert b.decisions()['M3-1']['status']=='coordinator_running'
    b.close('M3-1','完成，來源為離線測試');assert b.decisions()['M3-1']['status']=='closed'


def test_condition_releases_only_selected_option_and_notifies_once(project):
    b=board(project);ask(b)
    b.handoff('M3-1','B',coordinator='Claude',next_step='規劃驗收',condition='D99')
    bot=service(project);bot.handle(message(1,'決定 M3-1 B'))
    assert b.decisions()['M3-1']['status']=='waiting_condition'
    with pytest.raises(TaskError): b.claim('M3-1',coordinator='Claude',revision=1)
    b.complete_milestone('D99','審查通過',source='synthetic evidence')
    b.refresh_conditions();b.complete_milestone('D99','審查通過',source='same evidence')
    assert b.decisions()['M3-1']['status']=='waiting_coordinator'
    assert sum(r['type']=='condition_ready' for r in b.events())==1
    bot.enqueue_team_notifications();bot.enqueue_team_notifications();bot.deliver()
    assert sum('條件 D99 已達成' in text for _,text in bot.api.sent)==1
    # Notifications do not replay after restart.
    restarted=service(project,bot.api);restarted.enqueue_team_notifications();restarted.deliver()
    assert sum('條件 D99 已達成' in text for _,text in bot.api.sent)==1


def test_condition_can_gate_reviewed_action_and_amendment_cancels_it(project):
    b=board(project);spec=script(project);ask(b,actions={'B':spec})
    b.handoff('M3-1','B',coordinator='Claude',next_step='離線檢查',condition='D99')
    b.choose('o','1','M3-1','B')
    w=TeamWorker(project,'o',answer=lambda *a:None,action=lambda *a:pytest.fail('not ready'),now=lambda:NOW)
    assert not w.step()
    b.choose('o','2','M3-1','A');b.complete_milestone('D99','done',source='tests')
    assert not w.step() and b.decisions()['M3-1']['choice']=='A'
    b.choose('o','3','M3-1','B');calls=[];w.action=lambda *a:calls.append(1)
    assert w.step() and calls==[1]


def test_milestone_already_done_and_legacy_choice_are_supported(project):
    b=board(project);ask(b)
    # A v1 acceptance has no revision field, but it is still amendable and claimable.
    b.record('decision_accepted',owner='o',key='1',decision_id='M3-1',choice='B',status='waiting_coordinator',action=None)
    b.complete_milestone('D99','done',source='tests')
    b.handoff('M3-1','B',coordinator='Claude',next_step='規劃',condition='D99')
    assert b.decisions()['M3-1']['revision']==1
    assert '條件 D99 已達成' in b.decision_text()
    b.choose('o','2','M3-1','A');assert b.decisions()['M3-1']['revision']==2


def test_health_and_start_estimates_are_honest(project):
    b=board(project);spec=script(project);ask(b,actions={'B':spec});b.choose('o','1','M3-1','B')
    assert '開始時間無法估計' in b.decision_text()
    b.health(worker_alive=True);b.health(worker_alive=True)
    assert sum(r['type']=='service_health' for r in b.events())==1
    assert '約 1 秒' in b.decision_text()
    late=Board(project,now=lambda:NOW+timedelta(seconds=91))
    assert '心跳過期' in late.snapshot() and '開始時間無法估計' in late.snapshot()
    b.health(worker_alive=False);assert '開始時間無法估計' in b.snapshot()


def test_manual_receipt_gives_owner_next_step_and_no_duplicate_approval(project):
    bot=service(project);ask(bot.board)
    bot.board.handoff('M3-1','A',coordinator='Claude',next_step='登記 130/130 覆蓋率情境',blocker='未登記離線程式')
    bot.handle(message(1,'決定 M3-1 A'));bot.deliver()
    text='\n'.join(t for _,t in bot.api.sent)
    for expected in ('Claude','開始時間未定','130/130','無須重複核准','在電腦開啟','決定 M3-1 B'):
        assert expected in text


def test_question_uses_latest_decisions_and_discards_answer_if_state_changes(project):
    bot=service(project);b=bot.board;ask(b)
    b.publish('尚需 Louis 核准數值',source='old snapshot')
    b.choose(bot.dev_owner,'choice','M3-1','A');bot.handle(message(1,'Gemini，還需要核准嗎？'))
    seen=[]
    def answer(p,player,q,context,halt):
        seen.append(context)
        assert '已選 A' in context['facts'] and '無須重複核准' in context['facts']
        b.choose(bot.dev_owner,'newchoice','M3-1','B')
        return 'STALE_ANSWER'
    w=TeamWorker(project,bot.dev_owner,answer=answer,action=lambda *a:None,now=lambda:NOW)
    assert w.step();bot.enqueue_answers();bot.deliver()
    text='\n'.join(t for _,t in bot.api.sent)
    assert 'STALE_ANSWER' not in text and '已選 B' in text and '資料截點' in text
    assert seen[0]['as_of']==to_iso(NOW)


def test_notifications_are_paired_and_do_not_replay_old_v1_starts(project):
    bot=service(project);b=bot.board;ask(b)
    b.choose('other','1','M3-1','A')
    b.claim('M3-1',coordinator='Claude',revision=1)
    b.notify('other','private notice',source='test')
    b.notify(bot.dev_owner,'current notice',source='test')
    bot.enqueue_team_notifications();bot.deliver()
    text='\n'.join(t for _,t in bot.api.sent)
    assert 'current notice' in text and 'private notice' not in text and '主持人已接手' not in text


def test_long_snapshot_is_chunked_without_losing_options(project):
    bot=service(project)
    for i in range(14):
        ask(bot.board,name=f'LONG-{i}')
    bot.board.publish('😀'*2400,source='synthetic')
    bot.handle(message(1,'進度'));bot.deliver()
    text=''.join(t for _,t in bot.api.sent)
    assert '決定 LONG-13 B' in text and '😀'*2400 in text
    assert all(len(t.encode('utf-16-le'))//2<=3500 for _,t in bot.api.sent)
    assert bot.state.offset==2


def test_worker_start_and_phone_change_race_has_only_one_winner(project):
    b=board(project);spec=script(project);ask(b,actions={'B':spec});b.choose('o','1','M3-1','B')
    barrier=threading.Barrier(2);calls=[];errors=[]
    w=TeamWorker(project,'o',answer=lambda *a:None,action=lambda *a:calls.append('B'),now=lambda:NOW)
    def start():
        barrier.wait();w.step()
    def change():
        barrier.wait()
        try:b.choose('o','2','M3-1','A')
        except TaskError:errors.append('started')
    threads=[threading.Thread(target=start),threading.Thread(target=change)]
    for t in threads:t.start()
    for t in threads:t.join(2);assert not t.is_alive()
    if b.decisions()['M3-1']['choice']=='A':assert not calls and not errors
    else:assert calls==['B'] and errors==['started']


def test_publish_condition_and_action_atomically(project):
    b=board(project);spec=script(project)
    b.ask('M3-1','先等檢查',{'A':'稍後','B':'離線執行'},expires_at=to_iso(NOW+timedelta(hours=1)),
        actions={'B':spec},handoffs={'B':{'coordinator':'Claude','next_step':'執行檢查','condition':'D99'}})
    b.choose('o','1','M3-1','B')
    assert b.decisions()['M3-1']['status']=='waiting_condition'
    with pytest.raises(TaskError):
        b.ask('M3-2','錯誤映射',{'A':'a','B':'b'},expires_at=to_iso(NOW+timedelta(hours=1)),
            handoffs={'C':{'coordinator':'Claude','next_step':'x'}})


def test_handoff_for_unselected_option_or_before_acceptance_does_not_notify(project):
    bot=service(project);b=bot.board;ask(b)
    b.handoff('M3-1','B',coordinator='Claude',next_step='B next')
    bot.handle(message(1,'決定 M3-1 A'))
    bot.enqueue_team_notifications();bot.deliver()
    b.handoff('M3-1','B',coordinator='Claude',next_step='B revised next')
    bot.enqueue_team_notifications();bot.deliver()
    assert not any('決策執行更新' in text for _,text in bot.api.sent)
    # Updating the currently selected option still produces a notification.
    b.handoff('M3-1','A',coordinator='Claude',next_step='A next')
    bot.enqueue_team_notifications();bot.deliver()
    assert sum('決策執行更新' in text for _,text in bot.api.sent)==1
    # A new acceptance supersedes an undelivered old handoff.
    b.handoff('M3-1','A',coordinator='Claude',next_step='obsolete')
    bot.handle(message(2,'決定 M3-1 B'));bot.enqueue_team_notifications();bot.deliver()
    assert sum('決策執行更新' in text for _,text in bot.api.sent)==1


def test_resolved_history_drops_out_after_24h(project):
    b=board(project);ask(b,name='OLD-1');ask(b,name='OLD-2');ask(b,name='FAIL-1')
    b.close('OLD-1','完成，來源為離線測試')
    b.record('action_finished',decision_id='FAIL-1',status='failed')
    assert 'OLD-1' in b.decision_text()
    later=Board(project,now=lambda:NOW+timedelta(hours=26))
    text=later.decision_text()
    # Closed and long-expired items are hidden; items needing a check remain visible.
    assert 'OLD-1' not in text and 'OLD-2' not in text and 'FAIL-1' in text
    assert '另有 2 項' in text
    later.ask('NEW-1','新問題',{'A':'a','B':'b'},expires_at=to_iso(NOW+timedelta(hours=30)))
    assert 'NEW-1' in later.decision_text()


def test_old_summary_and_chat_do_not_reach_model_context(project):
    b=board(project);b.publish('GPT D147完成\nD100、D101 已結案的舊細節',source='old')
    q=b.submit('o','1','gpt','進度？')
    b.record('answer_finished',request_id=q['request_id'],status='finished',answer='D101 已結案')
    later=Board(project,now=lambda:NOW+timedelta(hours=25))
    ctx=later.context('o','new')
    assert ctx['recent_chat_untrusted']==[] and 'D101' not in ctx['facts']
    assert 'GPT D147完成' in ctx['facts'] and '細節已收合' in ctx['facts']
    assert len(b.context('o','new')['recent_chat_untrusted'])==1


def test_live_brief_counts_without_question_details(project):
    data=project/'data';data.mkdir(exist_ok=True)
    rows=[{'status':'forecast_ok','question_id':46707,'at':to_iso(NOW-timedelta(hours=1)),'probability':0.12345},
          {'status':'comment_ok','question_id':46707,'at':to_iso(NOW-timedelta(hours=1))},
          {'status':'forecast_ok','question_id':11111,'at':to_iso(NOW-timedelta(days=3))}]
    (data/'submissions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    (data/'forecasts.jsonl').write_text(json.dumps({'player':'gpt','finished_at_utc':to_iso(NOW-timedelta(hours=2)),
        'probability':0.98765,'reasoning_summary':'SECRET_REASON'})+'\n')
    text=board(project).snapshot()
    assert '確認送出預測：1 題' in text and 'gpt 1' in text
    for hidden in ('46707','11111','0.12345','0.98765','SECRET_REASON'):
        assert hidden not in text


def test_expired_condition_never_releases_or_queues_work(project):
    b=board(project);spec=script(project);ask(b,actions={'B':spec})
    b.handoff('M3-1','B',coordinator='Claude',next_step='run',condition='D99')
    b.choose('o','1','M3-1','B')
    late=Board(project,now=lambda:NOW+timedelta(hours=2))
    late.complete_milestone('D99','done late',source='test')
    assert late.decisions()['M3-1']['status']=='waiting_condition'
    assert not any(r['type']=='condition_ready' for r in late.events())
    w=TeamWorker(project,'o',answer=lambda *a:None,action=lambda *a:pytest.fail('expired'),now=late.now)
    assert not w.step()
