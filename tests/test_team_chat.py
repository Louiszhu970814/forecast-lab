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
    with pytest.raises(TaskError): b.choose(bot.dev_owner,'update:4','M3-1','A')
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
