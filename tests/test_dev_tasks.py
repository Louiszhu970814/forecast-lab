from __future__ import annotations

import json
from pathlib import Path

import pytest

from flab.dev_tasks import TaskError, TaskStore, parse_dev_command, public_text
from flab.timeutil import parse_iso
from test_telegram_bot import FakeAPI, message, project
import telegram_bot as T

NOW = parse_iso("2026-11-02T13:01:00Z")


def store(project):
    return TaskStore(project, now=lambda: NOW)


@pytest.mark.parametrize("text,kind", [("開發：加上月報", "dev_new"), ("/dev 修復錯誤", "dev_new"),
    ("開發進度", "dev_progress"), ("/progress D123456789A", "dev_progress"), ("開發任務", "dev_tasks"),
    ("補充：要有圖表", "dev_followup"), ("/followup D123456789A 加圖", "dev_followup"),
    ("停止任務", "dev_cancel"), ("/cancel D123456789A", "dev_cancel"), ("開發模式", "dev_help")])
def test_dev_command_parser(text, kind):
    assert parse_dev_command(text)[0] == kind
    assert T.parse_command(text)[0] == kind


def test_plain_chat_does_not_spend_model_quota():
    assert parse_dev_command("新增一個功能") is None
    assert parse_dev_command("看報告") is None


def test_submit_idempotency_scoping_and_limits(project):
    s = store(project)
    task = s.submit("bot:owner", "1", "增加月報")
    assert s.submit("bot:owner", "1", "增加月報")["task_id"] == task["task_id"]
    assert len(s.tasks()) == 1
    with pytest.raises(TaskError, match="找不到"):
        s.describe("other", task["task_id"])
    for n in range(2, 7):
        s.submit("bot:owner", str(n), "排隊任務")
    with pytest.raises(TaskError, match="佇列已滿"):
        s.submit("bot:owner", "7", "太多任務")


def test_secrets_never_enter_task_ledger(project):
    s = store(project)
    values = ["123456789:" + "A" * 35, "METACULUS_TOKEN=secret", "sk-" + "a" * 25]
    for value in values:
        with pytest.raises(TaskError, match="憑證"):
            s.submit("owner", "1", "請使用 " + value)
    assert not s.path.exists()


def test_running_supplements_queue_another_turn_and_restart_persists(project):
    s = store(project)
    task = s.submit("owner", "1", "增加月報")
    tid = task["task_id"]
    s.record("started", task_id=tid, owner="owner", run_id="run", revision=1, workspace="/fake/work")
    active = s.tasks()[tid]
    s.control("owner", "2", "supplement", tid, "請加入圖表")
    s.control("owner", "2", "supplement", tid, "請加入圖表")
    assert s.tasks()[tid]["revision"] == 2
    s.finish(active, "review_ready", summary="第一版完成")
    fresh = store(project).tasks()[tid]
    assert fresh["status"] == "queued" and len(fresh["requests"]) == 2
    assert "第一版完成" in s.describe("owner", tid)


def test_cancellation_wins_over_completed_task(project):
    s = store(project)
    task = s.submit("owner", "1", "工作")
    tid = task["task_id"]
    s.record("started", task_id=tid, owner="owner", run_id="run", revision=1, workspace="/fake/work")
    active = s.tasks()[tid]
    s.control("owner", "2", "cancel", tid)
    assert s.tasks()[tid]["status"] == "cancelling"
    s.finish(active, "review_ready")
    assert s.tasks()[tid]["status"] == "cancelled"
    s.control("owner", "3", "supplement", tid, "繼續修正")
    assert s.tasks()[tid]["status"] == "queued"


def test_authorization_freshness_and_crash_replay_in_telegram(project):
    api = FakeAPI()
    bot = T.Service(project, api, 123, 42, now=lambda: NOW)
    bot.handle(message(1, "開發：禁止建立", owner=99))
    bot.handle(message(2, "開發：過期", date=1))
    assert not bot.tasks.tasks()
    # Simulate task creation succeeded, but Telegram cursor/reply was not persisted.
    task = bot.tasks.submit(bot.dev_owner, "update:3", "測試需求")
    bot.handle(message(3, "開發：測試需求"))
    assert len(bot.tasks.tasks()) == 1
    bot.handle(message(4, "補充：新需求"))
    bot.handle(message(5, "開發進度"))
    bot.deliver()
    assert any(task["task_id"] in text for _, text in api.sent)
    assert bot.tasks.tasks()[task["task_id"]]["revision"] == 2


def test_completion_notifications_deduplicate_across_restart(project):
    api = FakeAPI()
    bot = T.Service(project, api, 123, 42, now=lambda: NOW)
    task = bot.tasks.submit(bot.dev_owner, "1", "工作")
    tid = task["task_id"]
    bot.tasks.record("started", task_id=tid, owner=bot.dev_owner, run_id="run", revision=1, workspace="/fake/work")
    bot.tasks.finish(bot.tasks.tasks()[tid], "waiting_input", summary="需釐清需求", question="月份採 UTC 還是台北時間？")
    bot.enqueue_dev_notifications()
    bot.deliver()
    bot = T.Service(project, api, 123, 42, now=lambda: NOW)
    bot.enqueue_dev_notifications()
    bot.deliver()
    assert len(api.sent) == 2
    assert "月份採 UTC" in api.sent[-1][1]


def test_task_request_does_not_change_prediction_files(project):
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    s = store(project)
    task = s.submit("owner", "1", "新增功能")
    s.control("owner", "2", "cancel", task["task_id"])
    assert all(path.read_bytes() == content for path, content in before.items())


def test_emoji_results_fit_telegram_limit(project):
    assert len(public_text("😀" * 3000, 1000).encode("utf-16-le")) <= 2000
    s = store(project)
    task = s.submit("owner", "1", "😀" * 1000)
    s.record("started", task_id=task["task_id"], owner="owner", run_id="run", revision=1, workspace="/fake")
    s.finish(s.tasks()[task["task_id"]], "waiting_input", summary="😀" * 1400,
        tests="😀" * 1400, question="😀" * 1400)
    assert len(s.describe("owner").encode("utf-16-le")) // 2 <= 4096
