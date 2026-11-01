"""Private Telegram operations console with an isolated Codex development queue.

Uses the official Bot API via long polling. Only .env holds credentials. Bot state,
command replies and delivery receipts are append-only; no incoming text is logged.
"""
from __future__ import annotations

import argparse
import fcntl
import hmac
import json
import os
import plistlib
import re
import secrets
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from flab.envfile import load_env_file
from flab.jsonl import append_record
from flab.ops_report import ReportError, build_report, read_ledger, status_text
from flab.timeutil import parse_iso, to_iso, utc_now
from flab.dev_tasks import DEV_HELP, TaskError, TaskStore, parse_dev_command

VERSION = "telegram_ops_v2"
PROJECT = Path(__file__).resolve().parents[1]
TAIPEI = ZoneInfo("Asia/Taipei")
LABEL = "org.forecast-lab.telegram"
HELP = """Forecast Lab 手機控制台
看報告 或 /report：近 24 小時摘要
週報 或 /report 7d：近 7 天摘要
狀態 或 /status：目前設定與最近週期紀錄
排程 或 /schedule：查看報告排程
/daily 21:00：每天台灣時間 21:00 傳近 1 天報告
/weekly 1 21:00：每週一 21:00 傳近 7 天報告（1～7 代表週一～週日）
停止報告 或 /pause：停止定期報告
/help：這份說明
開發模式 或 /dev：手機開發指令說明
開發：你的需求 → 交辦功能，完成後通知
開發進度 → 查最近任務進度

也可以說「每天 21:00 傳報告」或「每週一 21:00 傳報告」。
開發任務使用獨立程式副本與 Codex 訂閱額度，完成後待審查整合。
首次配對後定期報告預設關閉。"""
KEYBOARD = {"keyboard": [["看報告", "週報", "狀態"], ["開發模式", "開發任務", "開發進度"], ["排程", "停止報告", "/help"]],
            "resize_keyboard": True}
COMMANDS = [{"command": command, "description": description} for command, description in (
    ("report", "查看營運報告；可加 7d"), ("status", "查看專案狀態"),
    ("schedule", "查看報告排程"), ("daily", "每天定時報告，例如 21:00"),
    ("weekly", "每週定時報告，例如 1 21:00"), ("pause", "停止定期報告"), ("help", "操作說明"),
    ("dev", "交辦開發需求"), ("tasks", "最近開發任務"), ("progress", "查開發進度"),
    ("followup", "補充指定任務需求"), ("cancel", "停止指定開發任務"))]


class SetupError(ValueError):
    pass


class TelegramError(Exception):
    def __init__(self, code=0, retry_after=0):
        self.code = code if type(code) is int else 0
        self.retry_after = retry_after if type(retry_after) is int and retry_after > 0 else 0
        # Never retain Telegram description, HTTP URL, token or server response.
        super().__init__(f"Telegram API 暫時不可用（代碼 {self.code}）")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TelegramAPI:
    def __init__(self, token, *, opener=None):
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            raise SetupError("TELEGRAM_BOT_TOKEN 格式不正確。")
        self._token = token
        self._opener = opener or build_opener(NoRedirect())

    def call(self, method, payload=None, *, timeout=35):
        if method not in {"getMe", "getWebhookInfo", "getUpdates", "sendMessage", "setMyCommands"}:
            raise ValueError("unsupported API method")
        req = Request(f"https://api.telegram.org/bot{self._token}/{method}",
            data=json.dumps(payload or {}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self._opener.open(req, timeout=timeout) as response:
                result = json.load(response)
        except HTTPError as exc:
            retry = 0
            try:
                body = json.loads(exc.read())
                retry = body.get("parameters", {}).get("retry_after", 0)
            except (ValueError, AttributeError, OSError):
                pass
            finally:
                exc.close()
            raise TelegramError(exc.code, retry) from None
        except (OSError, URLError, ValueError):
            raise TelegramError() from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            if isinstance(result, dict):
                params = result.get("parameters")
                raise TelegramError(result.get("error_code", 0), params.get("retry_after", 0) if isinstance(params, dict) else 0)
            raise TelegramError()
        return result.get("result")

    def identity(self):
        me = self.call("getMe")
        hook = self.call("getWebhookInfo")
        if not isinstance(me, dict) or type(me.get("id")) is not int or me.get("is_bot") is not True:
            raise SetupError("無法確認 Telegram Bot 身分。")
        if not isinstance(hook, dict) or hook.get("url") != "":
            raise SetupError("這個 Bot 已有 webhook 或狀態不明，請使用專屬 Bot；程式不會移除既有 webhook。")
        return me

    def updates(self, offset, *, timeout=25):
        rows = self.call("getUpdates", {"offset": offset, "timeout": timeout,
            "limit": 100, "allowed_updates": ["message"]}, timeout=timeout + 10)
        if not isinstance(rows, list) or any(not isinstance(r, dict) or type(r.get("update_id")) is not int for r in rows):
            raise TelegramError()
        return rows

    def send(self, chat_id, text):
        if not text or len(text.encode("utf-16-le")) // 2 > 4096:
            raise ValueError("invalid message length")
        return self.call("sendMessage", {"chat_id": chat_id, "text": text,
            "protect_content": True, "link_preview_options": {"is_disabled": True}, "reply_markup": KEYBOARD})


def settings(project: Path, *, paired=True):
    try:
        env = load_env_file(project / ".env")
    except (OSError, ValueError):
        raise SetupError("請先在專案 .env 設定 TELEGRAM_BOT_TOKEN。") from None
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise SetupError("缺少 TELEGRAM_BOT_TOKEN；請在 .env 填入 BotFather 提供的 Token。")
    owner = None
    uid, cid = env.get("TELEGRAM_USER_ID", ""), env.get("TELEGRAM_CHAT_ID", "")
    if uid or cid:
        if not uid.isascii() or not uid.isdecimal() or uid != cid or int(uid) <= 0:
            raise SetupError("私人帳號配對設定不完整或不一致，請檢查 .env 的 TELEGRAM_USER_ID／TELEGRAM_CHAT_ID。")
        owner = int(uid)
    if paired and owner is None:
        raise SetupError("尚未配對手機，請先執行 .venv/bin/python src/telegram_bot.py pair。")
    return token, owner


@contextmanager
def single_instance(project):
    path = project / "data/telegram.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SetupError("Telegram 服務已在執行，請勿同時啟動或配對。") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@dataclass(frozen=True)
class Schedule:
    mode: str = "off"
    hour: int = 21
    minute: int = 0
    weekday: int = 0

    def __post_init__(self):
        if self.mode not in {"off", "daily", "weekly"} or not (type(self.hour) is int and 0 <= self.hour <= 23
                and type(self.minute) is int and 0 <= self.minute <= 59
                and type(self.weekday) is int and 0 <= self.weekday <= 6):
            raise ValueError("invalid schedule")

    def label(self):
        if self.mode == "off":
            return "定期報告：已關閉；仍可手動查詢。"
        frequency = "每天" if self.mode == "daily" else "每週" + "一二三四五六日"[self.weekday]
        return f"定期報告：{frequency} {self.hour:02}:{self.minute:02}（Asia/Taipei）。"

    def latest_slot(self, now):
        if self.mode == "off":
            return None
        local = now.astimezone(TAIPEI)
        slot = local.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if slot > local:
            slot -= timedelta(days=1)
        if self.mode == "weekly":
            slot -= timedelta(days=(slot.weekday() - self.weekday) % 7)
        return slot


def parse_command(text):
    """Strict, deterministic allowlist. Unknown input is never echoed or executed."""
    if parsed := parse_dev_command(text):
        return parsed
    if not isinstance(text, str) or len(text) > 200:
        return "help", None
    text = text.strip()
    aliases = {"看報告": "/report", "報告": "/report", "日報": "/report", "週報": "/report 7d",
        "狀態": "/status", "查看狀態": "/status", "排程": "/schedule", "停止報告": "/pause", "取消報告": "/pause"}
    text = aliases.get(text, text)
    if match := re.fullmatch(r"每天\s*(\d{1,2}:\d{2})\s*(?:傳報告|報告)", text):
        text = f"/daily {match[1]}"
    if match := re.fullmatch(r"每週([一二三四五六日天])\s*(\d{1,2}:\d{2})\s*(?:傳報告|報告)", text):
        text = f"/weekly {('一二三四五六日'.index(match[1]) + 1) if match[1] != '天' else 7} {match[2]}"
    if text in {"/start", "/help"}:
        return "help", None
    if text in {"/report", "/report 1d", "/report 7d"}:
        return "report", 7 if text.endswith("7d") else 1
    if text in {"/status", "/schedule"}:
        return text[1:], None
    if text == "/pause":
        return "set_schedule", Schedule()
    match = re.fullmatch(r"/(daily|weekly) (?:([1-7]) )?(\d{1,2}):(\d{2})", text)
    if match and ((match[1] == "weekly") == bool(match[2])):
        try:
            return "set_schedule", Schedule(match[1], int(match[3]), int(match[4]), int(match[2] or 1) - 1)
        except ValueError:
            pass
    return "help", None


def private_sender(update):
    message = update.get("message")
    if not isinstance(message, dict) or message.get("forward_origin") or message.get("sender_chat") or message.get("via_bot"):
        return None
    chat, sender = message.get("chat"), message.get("from")
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return None
    uid = sender.get("id")
    if chat.get("type") != "private" or type(uid) is not int or uid <= 0 or chat.get("id") != uid or sender.get("is_bot") is not False:
        return None
    return uid


@dataclass
class State:
    offset: int = 0
    schedule: Schedule = field(default_factory=Schedule)
    since: str | None = None
    schedule_id: str | None = None
    messages: dict = field(default_factory=dict)
    delivered: set = field(default_factory=set)

    def apply(self, row):
        if "update_id" in row:
            self.offset = max(self.offset, row["update_id"] + 1)
        if "schedule" in row:
            self.schedule = Schedule(**row["schedule"])
            self.since = row["at"]
            self.schedule_id = row["key"]
        if row["type"] == "outbox":
            self.messages[row["key"]] = row
        if row["type"] == "delivered":
            self.delivered.add(row["key"])


class Service:
    def __init__(self, project, api, bot_id, owner, *, now=utc_now):
        self.project, self.api, self.bot_id, self.owner, self.now = project, api, bot_id, owner, now
        self.log = project / "data/telegram_events.jsonl"
        self.state = State()
        self.retry_at = 0.0
        self.failures = 0
        self.dev_owner = f"{bot_id}:{owner}"
        self.tasks = TaskStore(project, now=now)
        for row in read_ledger(self.log):
            if row.get("bot_id") == bot_id and row.get("owner") == owner:
                self.state.apply(row)

    def record(self, kind, **fields):
        row = {"type": kind, "version": VERSION, "at": to_iso(self.now()),
            "bot_id": self.bot_id, "owner": self.owner, **fields}
        append_record(self.log, row)
        self.state.apply(row)

    def handle(self, update):
        uid = update["update_id"]
        if uid < self.state.offset:
            return
        if private_sender(update) != self.owner:
            self.record("ignored", update_id=uid)
            return
        kind, value = parse_command(update["message"].get("text"))
        extra = {}
        # Expired commands must not silently change a schedule after a long outage.
        sent_at = update["message"].get("date")
        fresh = type(sent_at) is int and abs(self.now().timestamp() - sent_at) <= 600
        if kind in {"set_schedule", "dev_new", "dev_followup", "dev_cancel"} and not fresh:
            reply = "這則操作指令已超過 10 分鐘，請重新傳送。"
        else:
            try:
                if kind == "report":
                    reply = build_report(self.project, days=value, now=self.now())
                elif kind == "status":
                    reply = status_text(self.project, now=self.now())
                elif kind == "schedule":
                    reply = self.state.schedule.label()
                elif kind == "set_schedule":
                    extra["schedule"] = vars(value)
                    reply = value.label()
                elif kind.startswith("dev_"):
                    reply = self.dev_command(kind, value, f"update:{uid}")
                else:
                    reply = HELP
            except ReportError:
                reply = "報告暫時無法產生：帳冊或設定尚不完整，請在電腦檢查。"
            except TaskError as exc:
                reply = str(exc)
        # Schedule and consumed update are committed together before any network send.
        self.record("outbox", key=f"update:{uid}", update_id=uid, text=reply, **extra)

    def dev_command(self, kind, value, key):
        if kind == "dev_help":
            return DEV_HELP
        if kind == "dev_tasks":
            return self.tasks.list_text(self.dev_owner)
        if kind == "dev_progress":
            return self.tasks.describe(self.dev_owner, value)
        if kind == "dev_new":
            task = self.tasks.submit(self.dev_owner, key, value)
            return f"已收到 {task['task_id']}，排入開發佇列。\n完成或需要補充時會通知你。\n傳「開發進度」查看進度。"
        if kind == "dev_followup":
            task = self.tasks.control(self.dev_owner, key, "supplement", value[0], value[1])
            return f"{task['task_id']} 已記錄補充。執行中任務會在本輪完成後接著處理；其餘任務已重新排隊。"
        task = self.tasks.control(self.dev_owner, key, "cancel", value)
        return f"{task['task_id']} 已收到停止要求，工作副本會保留。"

    def enqueue_dev_notifications(self):
        for row in self.tasks.events():
            if row.get("owner") != self.dev_owner or row["type"] not in {"started", "finished", "worker_stopped"}:
                continue
            key = "dev_notice:" + row["event_id"]
            if key in self.state.messages:
                continue
            if row["type"] == "worker_stopped":
                text = "開發執行器已停止，需要在電腦檢查；報告與查詢功能仍可用。"
            elif row["type"] == "started":
                text = f"{row['task_id']} 開始準備第 {row['revision']} 版需求。傳「開發進度」可查看狀態。"
            else:
                text = self.tasks.describe(self.dev_owner, row["task_id"])
            self.record("outbox", key=key, text=text)

    def enqueue_due(self):
        now = self.now()
        slot = self.state.schedule.latest_slot(now)
        if slot is None or self.state.since is None or slot < parse_iso(self.state.since):
            return
        key = f"schedule:{self.state.schedule_id}:{to_iso(slot)}"
        if key in self.state.messages:
            return
        days = 1 if self.state.schedule.mode == "daily" else 7
        # After downtime, generate only the latest due slot, with a fresh data cutoff.
        text = f"定期報告（排定 {to_iso(slot)}）\n" + build_report(self.project, days=days, now=now)
        self.record("outbox", key=key, text=text, scheduled_for=to_iso(slot))

    def deliver(self):
        if time.monotonic() < self.retry_at:
            return
        pending = [r for k, r in self.state.messages.items() if k not in self.state.delivered]
        for row in pending[:10]:
            # Pausing/changing schedule cancels unsent periodic messages, including after restart.
            if "scheduled_for" in row and (self.state.schedule.mode == "off"
                    or not row["key"].startswith(f"schedule:{self.state.schedule_id}:")
                    or parse_iso(row["scheduled_for"]) < self.state.schedule.latest_slot(self.now())):
                self.record("delivered", key=row["key"], outcome="cancelled")
                continue
            try:
                self.api.send(self.owner, row["text"])
            except TelegramError as exc:
                if exc.code in {401, 409}:
                    raise
                self.failures += 1
                self.retry_at = time.monotonic() + max(exc.retry_after, min(60, 2 ** min(self.failures, 6)))
                self.record("delivery_error", code=exc.code)
                return
            # Telegram sendMessage has no idempotency key. A crash between these two
            # operations may duplicate a reply, but never reapply the command.
            self.record("delivered", key=row["key"], outcome="sent")
            self.failures = 0

    def run(self):
        self.api.call("setMyCommands", {"commands": COMMANDS, "scope": {"type": "chat", "chat_id": self.owner}})
        from flab.dev_runner import DevWorker
        worker = DevWorker(self.project, self.dev_owner)
        worker.start()
        try:
            self.poll_loop()
        finally:
            worker.stop()
            worker.join(timeout=75)

    def poll_loop(self):
        backoff = 2
        while True:
            try:
                # Drain queued commands (especially /pause) before scheduled deliveries.
                for update in self.api.updates(self.state.offset, timeout=3):
                    self.handle(update)
                self.enqueue_dev_notifications()
                self.enqueue_due()
                self.deliver()
                backoff = 2
            except TelegramError as exc:
                if exc.code in {401, 409}:
                    raise
                print(str(exc), file=sys.stderr, flush=True)
                time.sleep(min(60, max(backoff, exc.retry_after)))
                backoff = min(backoff * 2, 60)
            except ReportError:
                # A partial ledger must never kill the command console or expose data.
                print("定期報告暫停本輪：資料讀取失敗。", file=sys.stderr, flush=True)
                self.deliver()
                time.sleep(5)


def save_pair(project, owner):
    path = project / ".env"
    with path.open("r+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        # Revalidate under lock so a competing edit cannot silently replace an owner.
        _, existing = settings(project, paired=False)
        if existing is not None:
            raise SetupError("已存在配對，不會覆蓋。")
        content = stream.read()
        stream.seek(0, os.SEEK_END)
        stream.write(("" if content.endswith("\n") else "\n") +
            f"TELEGRAM_USER_ID={owner}\nTELEGRAM_CHAT_ID={owner}\n")
        stream.flush()
        os.fsync(stream.fileno())
        os.fchmod(stream.fileno(), 0o600)


def pair(project, api, *, seconds=300):
    _, owner = settings(project, paired=False)
    if owner is not None:
        raise SetupError("已完成配對，請直接啟動 run 或 install。")
    me = api.identity()
    code = secrets.token_urlsafe(18)
    print(f"請在 5 分鐘內私訊你的 Bot：/start {code}", flush=True)
    deadline = time.monotonic() + seconds
    offset = 0
    while time.monotonic() < deadline:
        for update in api.updates(offset):
            offset = max(offset, update["update_id"] + 1)
            owner = private_sender(update)
            msg = update.get("message", {})
            text = msg.get("text")
            if owner and isinstance(text, str) and hmac.compare_digest(text.encode(), f"/start {code}".encode()):
                save_pair(project, owner)
                service = Service(project, api, me["id"], owner)
                service.record("outbox", key=f"update:{update['update_id']}", update_id=update["update_id"],
                    text="手機配對完成！在電腦啟動服務後即可使用。\n\n" + HELP)
                service.deliver()
                print("配對已保存至 .env。下一步：.venv/bin/python src/telegram_bot.py install", flush=True)
                return
    raise SetupError("配對逾時；尚未授權任何帳號，請重跑 pair。")


def launch_agent(project, *, remove=False):
    if sys.platform != "darwin":
        raise SetupError("install／uninstall 目前只支援 macOS；其他環境請使用 run。")
    path = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    domain = f"gui/{os.getuid()}"
    if remove:
        if not path.exists():
            print("Telegram LaunchAgent 尚未安裝。")
            return
        with path.open("rb") as stream:
            existing = plistlib.load(stream)
        if existing.get("WorkingDirectory") != str(project):
            raise SetupError("既有 Telegram LaunchAgent 屬於其他專案，未修改。")
        result = subprocess.run(["/bin/launchctl", "bootout", domain, str(path)], capture_output=True)
        if result.returncode != 0 and subprocess.run(["/bin/launchctl", "print", f"{domain}/{LABEL}"], capture_output=True).returncode == 0:
            raise SetupError("無法停止服務，保留安裝檔。")
        path.unlink()
        print("已停止並移除 Telegram LaunchAgent；資料與配對保留。")
        return
    token, _ = settings(project)
    TelegramAPI(token).identity()
    python = project / ".venv/bin/python"
    if not python.exists():
        raise SetupError("缺少 .venv/bin/python，請先建立 Python 3.11 以上的虛擬環境。")
    with (project / "launchd" / f"{LABEL}.plist").open("rb") as stream:
        template = plistlib.load(stream)
    def expand(value):
        if isinstance(value, str):
            return value.replace("__PROJECT_ROOT__", str(project))
        if isinstance(value, list):
            return [expand(v) for v in value]
        if isinstance(value, dict):
            return {k: expand(v) for k, v in value.items()}
        return value
    template = expand(template)
    if path.exists():
        with path.open("rb") as stream:
            if plistlib.load(stream) != template:
                raise SetupError("既有 LaunchAgent 與本範本不同，未覆蓋；請先檢查或 uninstall。")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            plistlib.dump(template, stream)
    (project / "data/logs").mkdir(parents=True, exist_ok=True)
    if subprocess.run(["/bin/launchctl", "print", f"{domain}/{LABEL}"], capture_output=True).returncode != 0:
        result = subprocess.run(["/bin/launchctl", "bootstrap", domain, str(path)], capture_output=True)
        if result.returncode:
            raise SetupError("launchd 載入失敗，請在電腦檢查登入 session 與 LaunchAgent。")
    print("Telegram 常駐服務已載入；請在手機傳「狀態」確認連線。Mac 須保持開機、登入、連網且未休眠。")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=PROJECT)
    sub = parser.add_subparsers(dest="command", required=True)
    preview = sub.add_parser("preview", help="離線預覽手機報告")
    preview.add_argument("--days", type=int, choices=(1, 7), default=1)
    check = sub.add_parser("check", help="檢查設定，不顯示 Token")
    check.add_argument("--online", action="store_true")
    for name in ("pair", "run", "install", "uninstall"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    project = args.project.resolve()
    try:
        if args.command == "preview":
            print(build_report(project, days=args.days))
        elif args.command in {"install", "uninstall"}:
            launch_agent(project, remove=args.command == "uninstall")
        else:
            token, owner = settings(project, paired=args.command == "run")
            api = TelegramAPI(token)
            if args.command == "check":
                if args.online:
                    api.identity()
                print(f"Token 已設定；手機{'已' if owner else '尚未'}配對；定期報告由 /schedule 查詢。")
            else:
                with single_instance(project):
                    if args.command == "pair":
                        pair(project, api)
                    else:
                        me = api.identity()
                        def shutdown(signum, frame):
                            raise KeyboardInterrupt
                        signal.signal(signal.SIGTERM, shutdown)
                        Service(project, api, me["id"], owner).run()
    except (SetupError, ReportError, TelegramError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception:
        # HTTP exceptions can contain the token-bearing URL; never print tracebacks.
        print("Telegram 服務遇到非預期錯誤；未輸出原始資料或憑證，請執行離線測試檢查。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
