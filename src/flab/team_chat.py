"""Shared operational board, bounded Q&A queue and explicit decision handoff.

Only the local coordinator publishes facts/options/actions. Telegram cannot supply
commands or action paths. Every state transition is append-only and transactional.
"""
from __future__ import annotations

import hashlib
import re
import threading
import uuid
from pathlib import Path

from flab.dev_tasks import TaskError, safe_text, public_text
from flab.jsonl import append_record
from flab.ledger import read_complete, transaction
from flab.timeutil import utc_now, to_iso, parse_iso

VERSION = "team_chat_v2"
UNSTARTED = {"open", "queued", "waiting_coordinator", "waiting_condition"}
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
選項送出即核准該選項範圍；未開始且未到期可用同一指令修改。
只有已登記的離線程式會自動執行；TG 點名模型只問答，不會喚醒桌面 agent。
未登記續作須在電腦啟動指定主持人；回覆會列接手者、阻塞與下一步。
決策接手／結果／條件達成會通知；一般進度由你主動查詢。電腦須保持開機連網。"""


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

    def decisions(self, events=None):
        result = {}
        for r in self.events() if events is None else events:
            if r["type"] == "decision_open":
                result[r["decision_id"]] = {**r, "status": "open", "handoffs": dict(r.get("handoffs", {})), "revision": 0}
            elif r["type"] == "decision_handoff" and r["decision_id"] in result:
                d = result[r["decision_id"]]
                d["handoffs"][r["option"]] = r["handoff"]
                d["at"] = r["at"]
                if "status" in r:
                    d["status"] = r["status"]
            elif r["type"] in {"decision_accepted", "action_started", "action_finished", "decision_closed",
                               "condition_ready", "coordinator_started"}:
                if r["decision_id"] in result:
                    if r["type"] == "decision_accepted":
                        result[r["decision_id"]]["revision"] += 1
                    result[r["decision_id"]].update(r)
        return result

    def milestones(self):
        return {r["milestone_id"]: r for r in self.events() if r["type"] == "milestone_completed"}

    def ready_status(self, d, choice):
        condition = d["handoffs"].get(choice, {}).get("condition")
        if condition and condition not in self.milestones():
            return "waiting_condition"
        return "queued" if d["actions"].get(choice) else "waiting_coordinator"

    def handoff_spec(self, *, coordinator, next_step, blocker="", condition=None):
        spec = {"coordinator": checked_text(coordinator, 80), "next_step": checked_text(next_step, 500),
                "blocker": checked_text(blocker, 500) if blocker else "", "condition": condition}
        if condition is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", condition):
            raise TaskError("條件須為已登記或將登記的里程碑編號。")
        return spec

    def handoff(self, decision_id, choice, **handoff):
        """Local, factual routing metadata; never changes options or registers code."""
        spec = self.handoff_spec(**handoff)
        with transaction(self.root):
            d = self.decisions().get(decision_id)
            if not d or d["status"] not in UNSTARTED or choice not in d["options"]:
                raise TaskError("只能補充未開始決策的既有選項交接。")
            d["handoffs"][choice] = spec
            state = {"status": self.ready_status(d, choice)} if d.get("choice") == choice else {}
            self.record("decision_handoff", decision_id=decision_id, option=choice, handoff=spec, **state)

    def complete_milestone(self, milestone_id, summary, *, source):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", milestone_id):
            raise TaskError("里程碑編號不合法。")
        summary, source = checked_text(summary, 1000), checked_text(source, 500)
        with transaction(self.root):
            if milestone_id not in self.milestones():
                self.record("milestone_completed", milestone_id=milestone_id, summary=summary, source=source)
        self.refresh_conditions()

    def refresh_conditions(self):
        with transaction(self.root):
            for d in self.decisions().values():
                if d["status"] == "waiting_condition" and parse_iso(d["expires_at"]) > self.now():
                    status = self.ready_status(d, d["choice"])
                    if status != "waiting_condition":
                        self.record("condition_ready", decision_id=d["decision_id"], status=status,
                                    condition=d["handoffs"][d["choice"]]["condition"])

    def claim(self, decision_id, *, coordinator, revision):
        coordinator = checked_text(coordinator, 80)
        with transaction(self.root):
            d = self.decisions().get(decision_id)
            if not d or d["status"] != "waiting_coordinator":
                raise TaskError("此決策不是待人工接手；請先查最新狀態。")
            if d["revision"] != revision:
                raise TaskError("選項已修改；請重讀決策及版本再接手。")
            if parse_iso(d["expires_at"]) <= self.now():
                raise TaskError("決策已到期，不能開始；請發布新決策。")
            self.record("coordinator_started", decision_id=decision_id, status="coordinator_running",
                        executor=coordinator, started_at=to_iso(self.now()))

    def notify(self, owner, summary, *, source):
        summary, source = checked_text(summary, 3000), checked_text(source, 500)
        with transaction(self.root):
            self.events()
            self.record("notice", owner=owner, summary=summary, source=source)

    def health(self, *, worker_alive):
        with transaction(self.root):
            latest = next((r for r in reversed(self.events()) if r["type"] == "service_health"), None)
            if not latest or latest["worker_alive"] != worker_alive or (self.now() - parse_iso(latest["at"])).total_seconds() >= 30:
                self.record("service_health", worker_alive=worker_alive)

    def health_text(self, events=None):
        events = self.events() if events is None else events
        latest = next((r for r in reversed(events) if r["type"] == "service_health"), None)
        if not latest:
            return "TG 執行器：尚無健康紀錄，無法確認自動續作可用。"
        age = (self.now() - parse_iso(latest["at"])).total_seconds()
        status = "在線" if 0 <= age <= 90 and latest["worker_alive"] else "離線或心跳過期；請在電腦檢查／重啟 TG 服務"
        return f"TG 執行器：{status}（最後檢查 {latest['at']}）。這不是桌面主持人的在線證據。"

    def ask(self, decision_id, question, options, *, expires_at, actions=None, handoffs=None):
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
        handoffs = handoffs or {}
        if set(handoffs) - set(choices):
            raise TaskError("交接必須對應既有選項。")
        prepared_handoffs = {key: self.handoff_spec(**spec) for key, spec in handoffs.items()}
        with transaction(self.root):
            if decision_id in self.decisions():
                raise TaskError("決策編號不可重用；修訂請使用新編號。")
            self.record("decision_open", decision_id=decision_id, question=question,
                options=choices, actions=prepared, handoffs=prepared_handoffs, expires_at=expires_at)

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
            if not d:
                raise TaskError("此決策編號不存在；請查「待決事項」。")
            if d["status"] == "closed":
                raise TaskError("此決策已結案，不能修改；請主持人發布新決策。")
            if d["status"] in {"running", "coordinator_running"}:
                raise TaskError("此決策已開始執行，不能修改選項；請聯絡接手者。")
            if d["status"] not in UNSTARTED:
                label = {"succeeded": "已完成", "failed": "執行失敗", "unknown": "執行結果不明"}.get(d["status"], "狀態不明")
                raise TaskError(f"此決策{label}，不能修改或重跑；請主持人查核。")
            if parse_iso(d["expires_at"]) <= self.now():
                raise TaskError("此決策已到期；未開始也不能新增核准，請主持人發布新決策。")
            if d.get("owner") not in {None, owner}:
                raise TaskError("此決策由其他配對帳號選定，不能修改。")
            if choice not in d["options"]:
                raise TaskError("選項不在待決清單，未記錄決定。")
            action = d["actions"].get(choice)
            if action:
                self.action_spec(action)
            self.record("decision_accepted", owner=owner, key=key, decision_id=decision_id,
                choice=choice, status=self.ready_status(d, choice), action=action,
                revision=d["revision"] + 1, accepted_at=to_iso(self.now()))
            return self.decisions()[decision_id]

    def close(self, decision_id, summary):
        with transaction(self.root):
            d = self.decisions().get(decision_id)
            if not d or d["status"] in {"running", "queued", "closed"}:
                raise TaskError("執行中／排隊續作不可直接關閉，或決策不存在。")
            self.record("decision_closed", decision_id=decision_id, status="closed", summary=checked_text(summary))

    def describe_decision(self, d):
        labels = {"open": "等你決定", "queued": "決定已收到／續作排隊", "running": "工作已開始",
            "waiting_coordinator": "決定已收到／待主持人接手", "succeeded": "續作完成",
            "failed": "續作失敗，待檢查", "unknown": "執行結果不明，不會自動重跑", "closed": "已結案",
            "waiting_condition": "決定已收到／等待條件", "coordinator_running": "主持人已接手"}
        expired = parse_iso(d["expires_at"]) <= self.now()
        status = "已到期／不能開始或修改" if expired and d["status"] in UNSTARTED else labels.get(d["status"], "狀態待確認")
        out = [f"{d['decision_id']}：{status}（版本 {d['revision']}；更新 {d['at']}）"]
        if d["status"] == "closed":
            out.append(d.get("summary", ""))
            return "\n".join(out)
        out.append(d["question"])
        if d.get("choice"):
            out.append(f"已選 {d['choice']}：{d['options'][d['choice']]}\n此選項範圍已核准，無須重複核准；不擴大到其他工作。")
            spec = d["handoffs"].get(d["choice"], {})
            if d["status"] in UNSTARTED and not expired:
                if d["status"] == "queued":
                    health = self.health_text()
                    estimate = "預計前項工作結束後下一輪檢查（約 1 秒）開始；以開始通知為準。" if health.startswith("TG 執行器：在線") else "尚無可用執行器證據，開始時間無法估計；請在電腦檢查／重啟 TG 服務。"
                    out.append("接手者：TG TeamWorker（已登記離線程式）。" + estimate + health)
                elif d["status"] == "waiting_condition":
                    out.append(f"接手負責人：{spec.get('coordinator', 'Claude')}；尚無接手登記。條件 {spec['condition']} 未登記完成，開始時間未定；主持人登記條件完成後會自動通知。")
                else:
                    out.append(f"接手負責人：{spec.get('coordinator', 'Claude（PLAN D95）')}；尚無接手登記。沒有登記自動續作，也沒有桌面 agent 喚醒機制，開始時間未定。")
                    out.append(f"需要你做：在電腦開啟該主持人，請它查 project_control.py status 並接手 {d['decision_id']}；在 TG 點名只會問答，不會開工。")
                if spec.get("condition") and d["status"] != "waiting_condition":
                    out.append(f"條件 {spec['condition']} 已達成。")
                if spec.get("blocker"):
                    out.append("阻塞：" + spec["blocker"])
                if spec.get("next_step"):
                    out.append("下一步：" + spec["next_step"])
            elif d["status"] == "coordinator_running":
                out.append(f"實際接手者：{d['executor']}；開始 {d['started_at']}。此為接手紀錄，不是持續在線心跳。")
            elif d["status"] == "failed":
                out.append(d.get("reason", "離線續作未成功；請主持人在電腦檢查，未自動重跑。"))
        if d["status"] in UNSTARTED and not expired:
            out.append("可選／未開工可修改（以開始交易先後為準）：")
            out += [f"{k}：{v} → 決定 {d['decision_id']} {k}" for k,v in d["options"].items()]
        return "\n".join(out)

    def decision_text(self, events=None):
        rows = list(self.decisions(events).values())
        out = [f"待決與續作狀態（查詢 {to_iso(self.now())} UTC；最新帳冊）"]
        # Active decisions must never disappear behind historical closures.
        rows = [d for d in rows if d["status"] != "closed"] + [d for d in rows if d["status"] == "closed"][-3:]
        out += [self.describe_decision(d) for d in rows]
        if not rows:
            out.append("目前沒有主持人登記的待決事項。")
        return "\n\n".join(out)

    def snapshot(self):
        events = self.events()
        latest = next((r for r in reversed(events) if r["type"] == "progress"), None)
        if not latest:
            head = "尚無主持人發布的專案進度；無法確認 M2／M3 完成情況。"
        else:
            age = (self.now() - parse_iso(latest["at"])).total_seconds()
            head = f"主持人進度更新：{latest['at']}（UTC）\n依據：{latest['source']}\n{latest['summary']}"
            if age > 3600:
                head += "\n注意：此紀錄超過一小時，不能當作即時執行狀態。"
        return self.health_text(events) + "\n\n" + self.decision_text(events) + "\n\n以下是主持人摘要；若與上方較新決策衝突，以較新決策為準：\n" + public_text(head, limit=5500)

    def state_token(self):
        return next((r["event_id"] for r in reversed(self.events()) if r["type"] in {
            "progress", "decision_open", "decision_accepted", "decision_handoff", "condition_ready",
            "milestone_completed", "action_started", "action_finished", "coordinator_started", "decision_closed"}), None)

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
        with transaction(self.root):
            return {"facts": self.snapshot(), "as_of": to_iso(self.now()), "state_token": self.state_token(),
                    "recent_chat_untrusted": history[-4:]}


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
        b.refresh_conditions()
        with transaction(b.root):
            # Only this paired owner can start queued work. Re-pairing cannot run old requests.
            d = next((d for d in b.decisions().values() if d["status"] == "queued" and d["owner"] == self.owner), None)
            if d:
                if parse_iso(d["expires_at"]) <= b.now():
                    b.record("action_finished", decision_id=d["decision_id"], status="failed", reason="續作開始前決策已到期，未執行；請主持人發布新決策。")
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
            context = b.context(self.owner, q["request_id"])
            try:
                result = self.answer(b.project, q["player"], q["question"], context, self.halt)
                reply = checked_text(result, limit=2500)
                outcome = "answered"
            except Exception:
                reply = "本次模型暫時無法回答（可能忙碌、額度或連線問題），沒有重試或換模型。傳「進度」可直接查看紀錄。"
                outcome = "unavailable"
            with transaction(b.root):
                b.record("answer_finished", request_id=q["request_id"], status="finished", answer=reply, outcome=outcome,
                         context_at=context["as_of"], state_token=context["state_token"])
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
