"""Read-only operational summaries. Never serialize source rows or prediction fields."""
from __future__ import annotations

import fcntl
import json
from collections import Counter
from datetime import timedelta
from pathlib import Path

from flab.timeutil import parse_iso, to_iso, utc_now

PLAYERS = ("claude", "gemini", "gpt")
ERRORS = frozenset({"quota_exhausted", "timeout", "invalid_output", "tool_denied",
    "tool_scope_violation", "cli_error", "model_mismatch", "deadline_missed",
    "unsupported_type", "budget_skipped", "disabled"})
CYCLE_STATES = frozenset({"complete", "attention", "error", "timeout", "worker_failed", "overlap_skipped"})


class ReportError(ValueError):
    """A ledger/config could not be read safely; contains no source contents."""


def read_ledger(path: Path) -> list[dict]:
    """Lock against append_record; reject damaged ledgers without printing their contents."""
    try:
        with path.open(encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            rows = []
            for line in stream:
                if not line.endswith("\n"):
                    raise ValueError("incomplete row")
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("non-object row")
                rows.append(row)
            return rows
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        raise ReportError("無法讀取完整帳冊，請在電腦檢查資料。") from None


def _config(project: Path, name: str) -> dict:
    try:
        value = json.loads((project / "config" / name).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except (OSError, ValueError):
        raise ReportError("無法讀取專案設定，請在電腦檢查。") from None


def _time(row: dict, field: str):
    try:
        return parse_iso(row.get(field))
    except (TypeError, ValueError):
        return None


def _setting_lines(project: Path) -> list[str]:
    mode = _config(project, "submit_mode.json").get("mode")
    mode = mode if mode in {"local", "trial", "live"} else "未知"
    players = _config(project, "players_enabled.json")
    lines = [f"送出模式：{mode}",
        "送出暫停標記：" + ("已設定" if (project / "config/STOP_SUBMIT").exists() else "未設定"),
        "預測線設定：" + "、".join(f"{p} {'開' if players.get(p) is True else '關' if players.get(p) is False else '未知'}" for p in PLAYERS)]
    cycles = read_ledger(project / "data/cycles.jsonl")
    if cycles:
        row = cycles[-1]
        state = row.get("status")
        state = state if isinstance(state, str) and state in CYCLE_STATES else "未知"
        finished = _time(row, "finished_at")
        lines.append(f"最近已記錄週期：{state}（{to_iso(finished) if finished else '時間未知'}）")
    else:
        lines.append("最近已記錄週期：尚無紀錄")
    return lines


def status_text(project: Path, *, now=None) -> str:
    now = now or utc_now()
    lines = ["Forecast Lab 狀態", f"更新：{to_iso(now)}", *_setting_lines(project)]
    lines.append("以上為設定與歷史紀錄；不代表預測排程目前正在執行。")
    return "\n".join(lines)


def live_brief(project: Path, *, now=None) -> str:
    """Compact last-24h counts for the phone board and Q&A context (D148).

    Same information limits as build_report: no question titles, IDs, probabilities,
    options, reasons, raw errors or URLs; question IDs are only counted.
    """
    now = now or utc_now()
    start = now - timedelta(days=1)
    data = project / "data"
    attempts = read_ledger(data / "forecasts.jsonl")
    submissions = read_ledger(data / "submissions.jsonl")
    done = [r for r in attempts if (f := _time(r, "finished_at_utc")) is not None and start <= f <= now]
    counts = "、".join(f"{p} {sum(r.get('player') == p for r in done)}" for p in PLAYERS)
    errors = sum(bool(r.get("error_type")) for r in done)
    ok = [(t, r.get("question_id")) for r in submissions
        if r.get("status") == "forecast_ok" and (t := _time(r, "at")) is not None and start <= t <= now]
    comments = {r.get("question_id") for r in submissions
        if r.get("status") == "comment_ok" and (t := _time(r, "at")) is not None and start <= t <= now}
    questions = {q for _, q in ok}
    last = f"；最近一次 {to_iso(max(t for t, _ in ok))}" if ok else ""
    return "\n".join([f"即時營運（近 24 小時；直接讀帳冊、不經模型；資料截止 {to_iso(now)} UTC）",
        *_setting_lines(project),
        f"確認送出預測：{len(questions)} 題（含對帳確認）{last}",
        f"確認送出留言：{len(comments)} 題",
        f"完成預測嘗試：{counts}；回報錯誤 {errors} 次",
        "只列計數；題目內容、機率與理由不顯示。分數與排名不在此列。"])


def build_report(project: Path, *, days=1, now=None) -> str:
    """Counts only, with [start, end) attempt-start cohort and a data cutoff.

    No question titles, IDs, probabilities, options, reasons, raw errors, URLs or
    arbitrary report files can enter this output, including for closed questions.
    """
    if days not in (1, 7):
        raise ValueError("report period must be 1 or 7 days")
    now = now or utc_now()
    start = now - timedelta(days=days)
    data = project / "data"
    attempts = read_ledger(data / "forecasts.jsonl")
    starts = read_ledger(data / "forecast_attempt_starts.jsonl")
    submissions = read_ledger(data / "submissions.jsonl")
    budgets = read_ledger(data / "budget_events.jsonl")
    selected = [r for r in attempts if (t := _time(r, "started_at_utc")) is not None
        and start <= t < now and (f := _time(r, "finished_at_utc")) is not None and f <= now]
    completed_ids = {r.get("attempt_id") for r in attempts
        if isinstance(r.get("attempt_id"), str) and (f := _time(r, "finished_at_utc")) is not None and f <= now}
    pending = sum(start <= t < now and r.get("attempt_id") not in completed_ids
        for r in starts if (t := _time(r, "started_at_utc")) is not None)
    lines = [f"Forecast Lab｜近 {days} 天營運報告", f"期間：[{to_iso(start)}, {to_iso(now)})",
        "依嘗試開始時間歸期，資料截止為期間終點。", "", status_text(project, now=now), "",
        f"期間內開始、尚無完成紀錄：{pending} 次"]
    for p in PLAYERS:
        own = [r for r in selected if r.get("player") == p]
        errors = Counter(r.get("error_type") if isinstance(r.get("error_type"), str)
            and r["error_type"] in ERRORS else "other" for r in own if r.get("error_type"))
        known_tokens = []
        for row in own:
            usage = row.get("usage")
            if isinstance(usage, dict):
                a, b = usage.get("input_tokens"), usage.get("output_tokens")
                if type(a) is int and type(b) is int and 0 <= a <= 10**12 and 0 <= b <= 10**12:
                    known_tokens.append(a + b)
        lines.append(f"{p}：完成紀錄 {len(own)} 次；回報錯誤 {sum(errors.values())} 次")
        lines.append(f"  token 已知合計 {sum(known_tokens):,}（有資料 {len(known_tokens)}/{len(own)}；缺值不算 0）")
        if errors:
            lines.append("  錯誤：" + "、".join(f"{k} {v}" for k, v in sorted(errors.items())))
    window_submits = [r for r in submissions if (t := _time(r, "at")) is not None and start <= t < now]
    sent = {r["intent_id"] for r in window_submits if r.get("status") == "forecast_ok"
        and isinstance(r.get("intent_id"), str)}
    denied = sum(r.get("type") == "budget_denied" for r in budgets
        if (t := _time(r, "at")) is not None and start <= t < now)
    lines.extend(["", f"期間內確認預測送出：{len(sent)} 筆意圖（含對帳確認）",
        f"期間內預算拒絕事件：{denied} 次（非漏答題數）",
        "", "這是營運摘要；完成紀錄不代表通過有效性驗收。",
        "目前未提供完整結算分數、排名或剩餘訂閱額度；缺資料不推估。",
        "所有題目的答案、理由與原始輸出均不顯示。"])
    invalid_times = sum(_time(r, "started_at_utc") is None or _time(r, "finished_at_utc") is None for r in attempts)
    if invalid_times:
        lines.append(f"時間欄位不完整、未納入統計：{invalid_times} 筆完成紀錄。")
    return "\n".join(lines)
