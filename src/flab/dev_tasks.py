"""Append-only, owner-scoped development tasks; no prediction records are read."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from flab.ids import new_id
from flab.jsonl import append_record
from flab.ledger import transaction
from flab.ops_report import read_ledger
from flab.timeutil import to_iso, utc_now

VERSION = "telegram_dev_v1"
ID_PATTERN = r"D[0-9A-F]{10}"
SECRET = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{25,}\b|\bsk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----")
STATES = {"queued": "排隊中", "running": "執行中", "cancelling": "正在停止",
    "review_ready": "完成，待審查整合", "waiting_input": "等待補充", "failed": "執行失敗",
    "interrupted": "服務中斷，未自動重跑", "cancelled": "已停止"}
STAGES = {"prepare": "準備獨立工作區", "model": "閱讀與實作", "command": "執行開發指令",
    "test": "執行測試", "edit": "修改檔案", "finish": "整理結果"}
TERMINAL = {"review_ready", "waiting_input", "failed", "interrupted", "cancelled"}
DEV_HELP = """手機開發模式（GPT / Codex）
開發：你的需求 → 建立獨立開發任務
開發任務 或 /tasks → 最近任務
開發進度 或 /progress 任務編號 → 查進度與結果
補充：新需求 → 補充最近任務；執行中會在本輪完成後處理
/followup 任務編號 補充內容 → 指定任務
停止任務 或 /cancel 任務編號 → 停止最近／指定任務

在獨立程式副本開發與測試，完成後保留修改供 Claude 審查整合。
這裡不會自動修改正式專案、提交 Git 或部署。
會使用本機 Codex 的訂閱額度；最多同時 1 項，排隊 5 項，每日 10 輪，每輪最多 30 分鐘。
此處只能查經這個入口建立的任務，與目前的桌面聊天分開。
請勿在任務中貼入 Token、密碼或預測答案。"""


class TaskError(ValueError):
    pass


def safe_text(text, limit=3500):
    if not isinstance(text, str) or not text.strip() or len(text) > limit or "\0" in text:
        raise TaskError(f"請提供 1～{limit} 字的開發需求。")
    if SECRET.search(text) or re.search(r"(?i)\b(?:METACULUS_TOKEN|TELEGRAM_BOT_TOKEN|OPENAI_API_KEY)\s*=\s*\S+", text):
        raise TaskError("內容含疑似憑證，未建立任務。請移除 Token／API key 後重傳。")
    return text.strip()


def public_text(value, limit=1400):
    if not isinstance(value, str):
        return ""
    value = SECRET.sub("[憑證已隱藏]", value)
    value = re.sub(r"(?im)\b([A-Z_]*(?:TOKEN|API_KEY|PASSWORD|SECRET))\s*=\s*\S+", r"\1=[已隱藏]", value)
    clean = "".join(c for c in value if (c in "\n\t" or ord(c) >= 32) and not 0xD800 <= ord(c) <= 0xDFFF)
    # Telegram counts UTF-16 units; non-BMP characters must not overflow a reply.
    return clean.encode("utf-16-le")[:limit * 2].decode("utf-16-le", errors="ignore")


def parse_dev_command(text):
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text in {"/dev", "開發模式"}:
        return "dev_help", None
    if text in {"開發任務", "/tasks"}:
        return "dev_tasks", None
    if text in {"開發進度", "/progress"}:
        return "dev_progress", None
    if text in {"停止任務", "/cancel"}:
        return "dev_cancel", None
    if match := re.fullmatch(r"(?:/dev\s+|開發[：:]\s*)([\s\S]+)", text):
        return "dev_new", match[1]
    if match := re.fullmatch(r"(?:/progress|開發進度|/cancel|停止任務)\s+(" + ID_PATTERN + r")", text, re.I):
        return ("dev_cancel" if text.startswith(("/cancel", "停止")) else "dev_progress"), match[1].upper()
    if match := re.fullmatch(r"補充[：:]\s*([\s\S]+)", text):
        return "dev_followup", (None, match[1])
    if match := re.fullmatch(r"/followup\s+(" + ID_PATTERN + r")\s+([\s\S]+)", text, re.I):
        return "dev_followup", (match[1].upper(), match[2])
    if text.startswith(("/dev", "/followup", "/progress", "/cancel", "開發：", "補充：")):
        return "dev_help", None
    return None


class TaskStore:
    def __init__(self, project: Path, *, now=utc_now):
        self.project = project.resolve()
        self.root = self.project / "data/telegram_dev"
        self.path = self.root / "events.jsonl"
        self.now = now

    def events(self):
        return read_ledger(self.path)

    @staticmethod
    def fold(rows):
        tasks = {}
        for r in rows:
            tid = r.get("task_id")
            if r["type"] == "created":
                tasks[tid] = {"task_id": tid, "owner": r["owner"], "status": "queued", "revision": 1,
                    "requests": [r["text"]], "created_at": r["at"], "updated_at": r["at"],
                    "stage": "prepare", "runs": 0, "workspace": None, "summary": "", "tests": ""}
            elif tid in tasks:
                t = tasks[tid]
                t["updated_at"] = r["at"]
                if r["type"] == "supplement":
                    t["requests"].append(r["text"])
                    t["revision"] += 1
                    if t["status"] in TERMINAL:
                        t["status"] = "queued"
                elif r["type"] == "cancel":
                    t["status"] = "cancelling" if t["status"] == "running" else "cancelled"
                elif r["type"] == "started":
                    t.update(status="running", stage="prepare", run_id=r["run_id"],
                        run_revision=r["revision"], workspace=r["workspace"], pid=None, runs=t["runs"] + 1)
                elif r["type"] == "spawned":
                    t.update(pid=r["pid"], signature=r["signature"])
                elif r["type"] == "progress":
                    t.update(stage=r["stage"])
                elif r["type"] == "finished":
                    t.update(status=r["status"], summary=r.get("summary", ""), tests=r.get("tests", ""),
                        question=r.get("question", ""), changed=r.get("changed", []),
                        artifact=r.get("artifact"), stage="finish", pid=None)
        return tasks

    def tasks(self, owner=None):
        tasks = self.fold(self.events())
        return {k: v for k, v in tasks.items() if owner is None or v["owner"] == owner}

    def _record(self, kind, **fields):
        row = {"type": kind, "version": VERSION, "event_id": new_id("dev"), "at": to_iso(self.now()), **fields}
        append_record(self.path, row)
        return row

    def record(self, kind, **fields):
        with transaction(self.root):
            return self._record(kind, **fields)

    def _find(self, tasks, owner, task_id):
        own = [t for t in tasks.values() if t["owner"] == owner]
        if task_id:
            found = next((t for t in own if t["task_id"] == task_id), None)
        else:
            found = own[-1] if own else None
        if not found:
            raise TaskError("找不到你的開發任務；先傳「開發：需求」建立任務。")
        return found

    def submit(self, owner, key, text):
        text = safe_text(text)
        tid = "D" + hashlib.sha256(f"{owner}:{key}".encode()).hexdigest()[:10].upper()
        with transaction(self.root):
            tasks = self.fold(self.events())
            if tid in tasks:
                return tasks[tid]
            if sum(t["status"] in {"queued", "running", "cancelling"} for t in tasks.values()) >= 6:
                raise TaskError("開發佇列已滿（1 項執行、最多 5 項排隊），請稍後再交辦。")
            self._record("created", task_id=tid, owner=owner, key=key, text=text)
            return self.tasks(owner)[tid]

    def control(self, owner, key, kind, task_id=None, text=None):
        if kind == "supplement":
            text = safe_text(text)
        with transaction(self.root):
            rows = self.events()
            duplicate = next((r for r in rows if r.get("owner") == owner and r.get("key") == key), None)
            if duplicate:
                return self.tasks(owner)[duplicate["task_id"]]
            tasks = self.fold(rows)
            task = self._find(tasks, owner, task_id)
            if kind == "supplement":
                if task["runs"] >= 6 or task["revision"] >= 12:
                    raise TaskError("此任務已達續作上限，請另建任務並參考原任務結果。")
                if task["status"] == "cancelling":
                    raise TaskError("任務正在停止，請等停止完成後再補充。")
                if task["status"] in TERMINAL and sum(t["status"] in {"queued", "running", "cancelling"} for t in tasks.values()) >= 6:
                    raise TaskError("開發佇列已滿，請稍後再補充。")
            if kind not in {"supplement", "cancel"}:
                raise ValueError("invalid task control")
            self._record(kind, task_id=task["task_id"], owner=owner, key=key, **({"text": text} if text else {}))
            return self.tasks(owner)[task["task_id"]]

    def finish(self, task, status, **fields):
        with transaction(self.root):
            current = self.tasks()[task["task_id"]]
            if current["status"] in {"cancelling", "cancelled"}:
                status = "cancelled"
            elif status in {"review_ready", "waiting_input"} and current["revision"] > task["run_revision"]:
                status = "queued"
            return self._record("finished", task_id=task["task_id"], owner=task["owner"],
                run_id=task["run_id"], status=status, **fields)

    def describe(self, owner, task_id=None):
        t = self._find(self.tasks(), owner, task_id)
        lines = [f"{t['task_id']}｜{STATES[t['status']]}", f"需求：{public_text(t['requests'][0], 150)}",
            f"最近更新：{t['updated_at']}", f"階段：{STAGES.get(t['stage'], '處理中')}；已啟動 {t['runs']} 輪"]
        if t["status"] == "queued":
            today = to_iso(self.now())[:10]
            used = sum(r["type"] == "started" and r["at"][:10] == today for r in self.events())
            if used >= 10:
                lines.append("今日開發執行已達 10 輪，等待下一個 UTC 日。")
        if t.get("run_revision", t["revision"]) < t["revision"]:
            lines.append("另有新補充，將在本輪結束後處理。")
        if t["summary"]:
            lines.append("結果：" + public_text(t["summary"], 1000))
        if t["tests"]:
            lines.append("開發助手回報的驗證：" + public_text(t["tests"], 650))
        if t.get("question"):
            lines.append("需要補充：" + public_text(t["question"], 500))
        if t.get("changed"):
            lines.append(f"保留了 {len(t['changed'])} 個檔案的修改。")
        if t.get("artifact"):
            lines.append("修改與整合資料已保存在電腦的獨立工作區，待 Claude 審查。")
        return "\n".join(lines)

    def list_text(self, owner):
        tasks = list(self.tasks(owner).values())[-5:]
        return "最近開發任務\n" + ("\n".join(f"{t['task_id']}｜{STATES[t['status']]}｜{public_text(t['requests'][0], 55)}" for t in reversed(tasks))
            if tasks else "尚無任務。傳「開發：你的需求」即可建立。")
