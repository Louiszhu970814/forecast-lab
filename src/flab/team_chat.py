"""Shared operational board, bounded Q&A queue and explicit decision handoff.

Only the local coordinator publishes facts/options/actions. Telegram cannot supply
commands or action paths. Every state transition is append-only and transactional.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from pathlib import Path

from flab.dev_tasks import TaskError, safe_text, public_text
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import utc_now, to_iso, parse_iso

VERSION = "team_chat_v1"
MODELS = {"gpt": "gpt-6-sol", "claude": "claude-sonnet-5-5", "gemini": "gemini-3.8-flash-low"}
LABELS = {"gpt": "GPT", "claude": "Claude", "gemini": "Gemini"}
HELP = """Forecast Lab 工作對話
直接問：GPT，現在 M3 做完了嗎？卡在哪裡？
也可點名 Claude 或 Gemini；未點名預設 GPT。
三家讀取同一份專案進度與決策紀錄，每次只呼叫一家。
進度：直接看共用進度（不耗模型額度）
待決事項：查看選項與決定指令
決定 <編號> <選項>：例如「決定 M3-1 B」
看報告／週報：營運計數；狀態：服務設定

進度由主持人更新，附資料時間；手機不直接讀取桌面對話。
已登記續作的決定可啟動本機工作；其他決定顯示待主持人接手。
不定期推播，由你主動查詢。電腦須保持開機連網。"""


def checked_text(text, limit=3500):
    value = safe_text(text, limit=limit)
    if re.search(r"(?i)\b[A-Z_]*(?:TOKEN|API_KEY|PASSWORD|SECRET)\s*[:=]\s*\S+", value):
        raise TaskError("內容含疑似憑證，未記錄；請移除後重傳。")
    return value


def parse_team(text):
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text in {"進度", "開發進度", "/progress", "專案進度"}:
        return "board", None
    if text in {"待決事項", "需要我決定什麼", "/decisions"}:
        return "decisions", None
    if text in {"開發模式", "/dev", "/help", "/start", "工作群組"}:
        return "team_help", None
    if text.startswith(("決定", "/decide")):
        m = re.fullmatch(r"(?:決定|/decide)\s+([A-Za-z0-9_-]{1,40})\s+([A-Za-z0-9_-]{1,12})", text)
        return ("decision", (m[1], m[2].upper())) if m else ("decision_help", None)
    m = re.match(r"^(gpt|claude|gemini)(?:\s|[，,:：])+(.+)$", text, re.I | re.S)
    if m:
        return "question", (m[1].lower(), m[2].strip())
    return None


class Board:
    def __init__(self, project, *, now=utc_now):
        self.project, self.now = Path(project), now
        self.root = self.project / "data/team_chat"
        self.path = self.root / "events.jsonl"

    def events(self):
        try:
            return read_complete(self.path)
        except (ValueError, OSError):
            raise TaskError("共用進度帳冊不完整，請由主持人檢查；未執行操作。") from None

    def record(self, kind, **fields):
        append_record(self.path, {"type": kind, "version": VERSION,
            "at": to_iso(self.now()), "event_id": uuid.uuid4().hex, **fields})

    def publish(self, summary, *, source):
        summary, source = checked_text(summary, limit=5000), checked_text(source, limit=500)
        with transaction(self.root):
            self.events()
            self.record("progress", summary=summary, source=source)

    def decisions(self):
        result = {}
        for r in self.events():
            if r["type"] == "decision_open":
                result[r["decision_id"]] = {**r, "status": "open"}
            elif r["type"] in {"decision_accepted", "action_started", "action_finished", "decision_closed"}:
                if r["decision_id"] in result:
                    result[r["decision_id"]].update(r)
        return result

    def ask(self, decision_id, question, options, *, expires_at, actions=None):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", decision_id):
            raise TaskError("決策編號不合法。")
        question = checked_text(question, limit=1200)
        if not isinstance(options, dict) or not 2 <= len(options) <= 5:
            raise TaskError("每項決策須有 2～5 個選項。")
        choices = {}
        for key, val in options.items():
            if not re.fullmatch(r"[A-Z0-9_-]{1,12}", key):
                raise TaskError("選項代號不合法。")
            choices[key] = checked_text(val, limit=500)
        if parse_iso(expires_at) <= self.now():
            raise TaskError("決策有效期限必須在未來。")
        actions = actions or {}
        if set(actions) - set(choices):
            raise TaskError("續作必須對應既有選項。")
        # Store the reviewed script digest in the immutable decision itself.
        prepared = {key: self.action_spec(spec) for key, spec in actions.items()}
        with transaction(self.root):
            if decision_id in self.decisions():
                raise TaskError("決策編號不可重用；修訂請使用新編號。")
            self.record("decision_open", decision_id=decision_id, question=question,
                options=choices, actions=prepared, expires_at=expires_at)

    def action_spec(self, spec):
        if not isinstance(spec, dict) or set(spec) != {"script", "sha256", "timeout"}:
            raise TaskError("續作須提供 script、sha256、timeout。")
        rel = Path(spec["script"])
        if rel.is_absolute() or ".." in rel.parts or rel.suffix != ".py" or rel.parts[:2] != ("config", "team_actions"):
            raise TaskError("續作只接受 config/team_actions/ 的已審查 Python 程式。")
        path = self.project / rel
        if path.resolve() != path.absolute() or not path.is_file():
            raise TaskError("續作程式不存在或包含連結。")
        if type(spec["timeout"]) is not int or not 1 <= spec["timeout"] <= 600:
            raise TaskError("續作上限須為 1～600 秒。")
        if hashlib.sha256(path.read_bytes()).hexdigest() != spec["sha256"]:
            raise TaskError("續作程式與審查版本不一致。")
        return dict(spec)

    def choose(self, owner, key, decision_id, choice):
        with transaction(self.root):
            for r in self.events():
                if r["type"] == "decision_accepted" and r.get("owner") == owner and r.get("key") == key:
                    return self.decisions()[r["decision_id"]]
            d = self.decisions().get(decision_id)
            if not d or d["status"] != "open" or parse_iso(d["expires_at"]) <= self.now():
                raise TaskError("此決策不存在、已處理或已到期；請查「待決事項」。")
            if choice not in d["options"]:
                raise TaskError("選項不在待決清單，未記錄決定。")
            action = d["actions"].get(choice)
            if action:
                self.action_spec(action)
            self.record("decision_accepted", owner=owner, key=key, decision_id=decision_id,
                choice=choice, status="queued" if action else "waiting_coordinator", action=action)
            return self.decisions()[decision_id]

    def close(self, decision_id, summary):
        with transaction(self.root):
            d = self.decisions().get(decision_id)
            if not d or d["status"] in {"running", "queued"}:
                raise TaskError("執行中／排隊續作不可直接關閉，或決策不存在。")
            self.record("decision_closed", decision_id=decision_id, status="closed", summary=checked_text(summary))

    def decision_text(self):
        rows = list(self.decisions().values())[-12:]
        out = ["待決與續作狀態"]
        labels = {"open": "等你決定", "queued": "決定已收到／續作排隊", "running": "工作已開始",
            "waiting_coordinator": "決定已收到／待主持人接手", "succeeded": "續作完成",
            "failed": "續作失敗，待檢查", "unknown": "執行結果不明，不會自動重跑", "closed": "已結案"}
        for d in rows:
            status = labels.get(d["status"], "狀態待確認")
            if d["status"] == "open" and parse_iso(d["expires_at"]) <= self.now():
                status = "已到期"
            out.append(f"{d['decision_id']}：{status}\n{d['question']}")
            if status == "等你決定":
                out += [f"{k}：{v} → 決定 {d['decision_id']} {k}" for k,v in d["options"].items()]
            elif d.get("choice"):
                out.append(f"已選 {d['choice']}：{d['options'][d['choice']]}")
        if not rows:
            out.append("目前沒有主持人登記的待決事項。")
        return public_text("\n".join(out), limit=3000)

    def snapshot(self):
        latest = next((r for r in reversed(self.events()) if r["type"] == "progress"), None)
        if not latest:
            head = "尚無主持人發布的專案進度；無法確認 M2／M3 完成情況。"
        else:
            age = (self.now() - parse_iso(latest["at"])).total_seconds()
            head = f"進度更新：{latest['at']}（UTC）\n依據：{latest['source']}\n{latest['summary']}"
            if age > 3600:
                head += "\n注意：此紀錄超過一小時，不能當作即時執行狀態。"
        return public_text(head, limit=5500) + "\n\n" + self.decision_text()

    def questions(self):
        result = {}
        for r in self.events():
            if r["type"] == "question":
                result[r["request_id"]] = {**r, "status": "queued"}
            elif r["type"] in {"answer_started", "answer_finished"} and r["request_id"] in result:
                result[r["request_id"]].update(r)
        return result

    def submit(self, owner, key, player, question):
        if player not in MODELS:
            raise TaskError("未知模型。")
        question = checked_text(question, limit=1800)
        with transaction(self.root):
            questions = self.questions()
            old = next((q for q in questions.values() if q["owner"] == owner and q["key"] == key), None)
            if old:
                return old
            if sum(q["status"] in {"queued", "running"} for q in questions.values()) >= 5:
                raise TaskError("已有 5 則問題等待回答，請稍後再問；「進度」仍可直接查看。")
            if sum(q["created_at"][:10] == to_iso(self.now())[:10] for q in questions.values()) >= 60:
                raise TaskError("今日問答已達 60 則上限；「進度／待決事項」不耗模型額度，仍可使用。")
            rid = uuid.uuid4().hex
            self.record("question", request_id=rid, owner=owner, key=key,
                player=player, question=question, model=MODELS[player], created_at=to_iso(self.now()))
            return self.questions()[rid]

    def context(self, owner, request_id):
        # No source files, forecast logs, .env or CLI sessions are opened here.
        history = []
        for q in self.questions().values():
            if q["owner"] == owner and q["request_id"] != request_id and q["status"] == "finished":
                history.append({"player": q["player"], "question": q["question"], "answer": q["answer"]})
        return {"facts": self.snapshot(), "recent_chat_untrusted": history[-4:]}


class TeamWorker(threading.Thread):
    def __init__(self, project, owner, *, answer=None, action=None, now=utc_now):
        super().__init__(daemon=True)
        self.board = Board(project, now=now)
        self.owner, self.halt = owner, threading.Event()
        if answer is None or action is None:
            from flab.team_runner import answer_question, run_action
        self.answer = answer or answer_question
        self.action = action or run_action

    def stop(self):
        self.halt.set()

    def recover(self):
        with transaction(self.board.root):
            for q in self.board.questions().values():
                if q["status"] == "running":
                    self.board.record("answer_finished", request_id=q["request_id"], status="finished",
                        answer="上次問答中斷，未自動重跑；請重新提問。", outcome="interrupted")
            for d in self.board.decisions().values():
                if d["status"] == "running":
                    self.board.record("action_finished", decision_id=d["decision_id"], status="unknown")

    def step(self):
        b = self.board
        with transaction(b.root):
            # Only this paired owner can start queued work. Re-pairing cannot run old requests.
            d = next((d for d in b.decisions().values() if d["status"] == "queued" and d["owner"] == self.owner), None)
            if d:
                if parse_iso(d["expires_at"]) <= b.now():
                    b.record("action_finished", decision_id=d["decision_id"], status="failed")
                    return True
                b.record("action_started", decision_id=d["decision_id"], status="running")
                q = None
            else:
                q = next((q for q in b.questions().values() if q["status"] == "queued" and q["owner"] == self.owner), None)
                if not q:
                    return False
                # A queued query is not silently answered hours after reconnecting.
                if (b.now() - parse_iso(q["at"])).total_seconds() > 600:
                    b.record("answer_finished", request_id=q["request_id"], status="finished",
                        answer="問題等待超過 10 分鐘，未呼叫模型；請重新提問。", outcome="expired")
                    return True
                b.record("answer_started", request_id=q["request_id"], status="running")
        if d:
            try:
                b.action_spec(d["action"])
                self.action(b.project, d["action"], self.halt)
                outcome = "succeeded"
            except Exception:
                outcome = "failed"
            with transaction(b.root):
                b.record("action_finished", decision_id=d["decision_id"], status=outcome)
        else:
            try:
                result = self.answer(b.project, q["player"], q["question"], b.context(self.owner, q["request_id"]), self.halt)
                reply = checked_text(result, limit=2500)
                outcome = "answered"
            except Exception:
                reply = "本次模型暫時無法回答（可能忙碌、額度或連線問題），沒有重試或換模型。傳「進度」可直接查看紀錄。"
                outcome = "unavailable"
            with transaction(b.root):
                b.record("answer_finished", request_id=q["request_id"], status="finished", answer=reply, outcome=outcome)
        return True

    def run(self):
        self.recover()
        while not self.halt.is_set():
            try:
                if not self.step():
                    self.halt.wait(1)
            except Exception:
                # Ledger failures stop work rather than resetting the journal.
                return
