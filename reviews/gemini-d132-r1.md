### 審查結論：**REQUEST CHANGES（要求修改 — 在完成修補與補齊缺失原始碼審查前，不得部署或執行任何模型預測）**

根據所提供的 Source Packet，本次變更針對兩題人工選定的 Metaculus Cup 問題（Post IDs `45330` 與 `46106`，對應 Question IDs `45519` 與 `46343`）實作了隔離儲存的 Advisory 流程（`src/advise.py`、`src/flab/advisory_usage.py`）與共享預算帳務更新（`src/budget.py` `budget_v8`）。

以下依審查要求，嚴格區分 **實際程式碼證據 (Actual Evidence)**、**阻斷性錯誤 (Blocking Bugs)**、**精確修補方案 (Precise Patches)**、**非阻斷性改進建議 (Proposals)**，以及 **需補充提供的缺失原始碼 (Missing Source Requests)**。

---

## 一、 阻斷性錯誤 (BLOCKING Bugs) 與實際程式碼證據

### BLOCKER 1：安全停線與配額退避契約失效（`F.LineStop` 被吞掉且隔離帳本導致安全事件對生產線與後續批次不可見）
* **審查維度**：Source Separation / Token, Credit & Quota Checks / Safety Contracts
* **實際證據**：
  1. 在 `src/forecast.py` 第 536–546 行，`F.run_attempt` 在寫入 `ctx.forecasts_log` 後，若發生以下安全或配額事件會拋出 `LineStop`：
     * `tool_scope_violation`（`notify=True`，第 536–538 行）
     * `safety_stop_after`（Gemini `agy AI credits are nonzero after the run (D54)`，`notify=True`，第 539–540 行）
     * `result.model_check == "mismatch"`（D121 退避/冷卻，第 541–542 行）
     * `leak_count(...) >= LEAK_STOP_COUNT`（`notify=True`，第 543–545 行）
     * `error_type == "quota_exhausted"`（1800 秒固定退避，第 546–547 行）
  2. 在正常 `forecast.run`（`src/forecast.py` 第 679–688 行）中，呼叫端會捕捉 `LineStop`，並在 `exc.notify` 為 `True` 時將 `{"type": "line_stop", ...}` 寫入 `shared / "line_status.jsonl"`。
  3. 然而在 `src/advise.py` 第 147–152 行 (`run_line`)：
     ```python
     try:
         rec = F.run_attempt(ctx, snap, kind="initial", prompt=F.forecast_prompt(snap, to_iso(utc_now())),
                             sch=V.question_schema(snap), prev=None)
         return {"player": player, "validation_status": rec["validation_status"],
                 "error_type": rec.get("error_type"), "adoptable": F.adoptable(rec)}
     except Exception as exc:
         return {"player": player, "error_type": type(exc).__name__, "adoptable": False}
     ```
     `run_line` 用廣泛的 `except Exception as exc:` 直接吞掉了 `F.LineStop`，**完全沒有在 `exc.notify` 為 `True` 時將 `line_stop` 寫入 `shared / "line_status.jsonl"`**！
  4. 更嚴重的是，因為 `advise.py` 將 `ctx.forecasts_log` 指向隔離目錄 `root / "forecasts.jsonl"`（第 142 行），而非 `shared / "forecasts.jsonl"`：
     * 生產線的 `F.line_stopped(shared / "line_status.jsonl", p, shared / "forecasts.jsonl")`（`src/forecast.py` 第 137–194 行）以及下一次 `advise.run`（`src/advise.py` 第 159–168 行）都只檢查 `shared / "forecasts.jsonl"`。
     * 因此，Advisory 執行期間發生的 **`tool_scope_violation`**、**Gemini 非零 AI credits (`D54`)**、**`model_mismatch` 退避/冷卻**、**`quota_exhausted` 1800 秒退避**，以及 **跨帳本累積的 `leak_confirmed` 次數**（例如生產帳本已有 1 次、Advisory 發生第 2 次），在生產線與下一題 Advisory 批次中**全部會被漏判而繼續放行**。

---

### BLOCKER 2：帳務對等性 (Accounting Equality) 破壞與 Gemini Argon 保護配額 (`protected_quota`) 漏算
* **審查維度**：Accounting Equality / Token, Credit & Quota Checks / Admission Priority
* **實際證據**：
  1. 在 `src/budget.py` 第 188 行：
     ```python
     protected = pending_tier1 + expected_new + repair_reserve if tier == 2 else 0
     ```
     因為 `Budget.reserve` 僅在 `tier == 2` 時計算 `protected`，`src/advise.py` 第 104–109 行 (`register_admission`) 採取了外部繞過寫法：
     ```python
     decision = b.reserve(request, pending_tier1=0, expected_new=0, repair_reserve=0)
     if not decision["allowed"]:
         raise F.QuestionDone("budget refused: " + str(decision["reason"]))
     if decision["weekly_attempts_used"] + protected + 1 > decision["weekly_attempt_cap"]:
         b.outcome(request["attempt_id"], False)
         raise F.QuestionDone("capacity reserved for future bot research and repairs")
     ```
  2. 這導致三個直接違反契約的阻斷性問題：
     * **Gemini Argon 週配額未保留 22 次未來 Bot 嘗試 (`protected_quota == 0`)**：在 `src/budget.py` 第 283–286 行：
       ```python
       protected_quota = min(protected * first_estimate, FS.TIER1_QUOTA_RESERVE_CAP)
       quota_info["protected_quota"] = protected_quota
       if valid and argon_remaining - argon_estimate - protected_quota < 1 - self.policy["players"][player]["weekly_cap"]:
           reason = "argon_personal_or_tier1_reserve"
       ```
       因為 `b.reserve` 內部看到的 `protected` 是 `0`，`protected_quota` 恆為 `0.0`！這表示當 Gemini 週配額僅剩剛好高於 `1 - weekly_cap` 的餘量時，人類 Advisory 呼叫被允許耗盡原本應保留給未來 22 次 Bot 嘗試的 Argon 配額。
     * **`budget_events.jsonl` 與 `advisory_usage.jsonl` 紀錄不等價**：
       * 當 Advisory 呼叫獲准時，`budget_events.jsonl` 中的 `budget_reserved` 紀錄寫入 `"protected_attempts": 0, "protected_quota": 0.0`，而 `advisory_usage.jsonl` 卻寫入 `"protected_attempts": 22`。
       * 當 `used + 1 <= cap` 但 `used + protected + 1 > cap` 時，`b.reserve` 會先在 `budget_events.jsonl` 寫入一筆**假的核准紀錄** (`"type": "budget_reserved", "allowed": True`)，接著 `register_admission` 才呼叫 `b.outcome(aid, False)` 寫入 `"status": "released"`，而非寫入 `"type": "budget_denied", "reason": "weekly_attempt_capacity_reserved_or_exhausted"`。
     * **`Budget.reserve` 缺乏 `budget_events.jsonl` 與 `advisory_usage.jsonl` 的雙向對等校驗**：
       * 在 `src/budget.py` 第 160–175 與 182–187 行，若某筆 `aid` 同時存在於 `reservations` 與 `advisory_admitted`：
         * 若 `advisory_finished[aid]["started"] is False`，但 `outcomes.get(aid, {}).get("status")` 為 `"started"`（或尚未寫入 `budget_outcome`），`active`（第 169 行 `and r["attempt_id"] not in advisory_finished`）與 `used_ids`（第 186 行）都會將其視為未啟動而不計費、不阻斷，無視 `budget_events.jsonl` 中的狀態矛盾。
         * 反之，若 `advisory_finished[aid]["started"] is True`，但 `outcomes.get(aid, {}).get("status") == "released"`，兩份共享帳本發生衝突卻不會觸發 `ValueError` fail-closed。
         * 第 162 行 `if aid in reservations:` 未檢查 `or aid in advisory_admitted`。

---

### BLOCKER 3：Gemini 配額估算輸入分裂（`gemini_admission` 讀取空的隔離帳本）
* **審查維度**：Token, Credit & Quota Checks / Source Separation
* **實際證據**：
  1. 在 `src/advise.py` 第 141–145 行，`F.Context` 的 `forecasts_log` 被設為 `root / "forecasts.jsonl"`（隔離目錄），而 `quota_log` 被設為 `shared / "quota.jsonl"`（共享目錄）。
  2. 在 `src/forecast.py` 第 422 行，`F.run_attempt` 呼叫 `gemini_admission(ctx, quota_before)`，進而呼叫 `argon_estimate(ctx.quota_log, policy, ctx.forecasts_log, ...)`（第 346–355、376、387 行）：
     ```python
     def argon_estimate(quota_log: Path, policy: dict[str, Any], forecasts_log: Path, *, tier: int = 1,
                        current=None, period: str = "weekly") -> tuple[float, int]:
         attempts = read_records(forecasts_log) if forecasts_log.exists() else []
         snapshots = read_records(quota_log) if quota_log.exists() else []
     ```
     因為 `ctx.forecasts_log` 是 `root / "forecasts.jsonl"`（在 Advisory 執行時為空或僅有當前批次紀錄），`gemini_admission` 在計算 5 小時與每週 Argon 消耗估算時，**完全看不到 `shared / "forecasts.jsonl"` 中的生產預測歷史**；但幾毫秒後 `Budget.reserve`（`src/budget.py` 第 279–295 行）卻使用 `shared / "forecasts.jsonl"` 呼叫 `quota.estimate`！
  3. 同時，當 Gemini Advisory 執行完畢後，`shared / "quota.jsonl"` 會新增 `before` 與 `after` 快照，但對應的 `forecast_attempt` 只存在於 `root / "forecasts.jsonl"` 而不在 `shared / "forecasts.jsonl"` 中（需結合 `src/quota.py` 確認 `quota.estimate` 是否依賴 `forecasts` 中的紀錄來配對 `snapshots` 或計算分母樣本數）。

---

### BLOCKER 4：跨批次目錄可繞過「每題每模型最多一次初始呼叫、總計 6 次呼叫」的限制
* **審查維度**：Admission Priority / Accounting Equality / Stale or Changed Inputs
* **實際證據**：
  1. 在 `src/advise.py` 第 100–103 行 (`register_admission`)：
     ```python
     admitted, _ = usage(shared)
     if any(r.get("batch") == root.name and r.get("question_id") == request["question_id"]
            and r["player"] == request["player"] for r in admitted.values()):
         raise F.QuestionDone("this player/question was already admitted; no rerun")
     ```
     重複檢查包含了 `r.get("batch") == root.name`！如果操作者對同一個 `post_id` 使用不同的 `--batch` 子目錄（例如 `data/advisory/batch_45330_1` 與 `data/advisory/batch_45330_2`），`r.get("batch") == root.name` 為 `False`，系統會允許對同一題、同一模型進行第 2 次甚至更多次呼叫，直接違反「每題 3 個模型各 1 次、共 6 次初始呼叫、零重試」的授權範圍。
  2. 此外，`advise.run` 在啟動批次前（第 150–175 行）完全沒有檢查該 `question_id` 是否已經在 `advisory_usage.jsonl` 中被錄入過。若誤對已執行過的題目再次執行 `advise.py`：
     * 會先發起 Metaculus API 輪詢並寫入 `root / "questions.jsonl"` 與 `root / "runs.jsonl"` (`advisory_batch_started`)；
     * 在 `F.run_attempt` 進入 `before_attempt` (`register_admission`) 之前，Gemini 線會先執行外部 `/usage` CLI 查詢並寫入 `shared / "quota.jsonl"`，且三條線都會建立空的 `root / "raw/<player>/<qid>_<aid>"` 目錄；
     * 最後 `advise.run` 還會在 `root / "aggregates.jsonl"` 重複寫入一筆聚合紀錄（若題目期間在 Metaculus 上有修訂產生新 `snapshot_id`，還會因 `mismatched_snapshot` 寫入 `no_valid_inputs` 的聚合紀錄）。

---

### BLOCKER 5：未知狀態 (Unknowns)、中斷恢復與程序清理 (Process Cleanup) 缺陷
* **審查維度**：Unknowns / Process Cleanup
* **實際證據**：
  1. **`finish_accounting` 在 `completed and not started` 時未 Fail-Closed (`src/advise.py` 第 118–130 行)**：
     ```python
     started = any(r.get("attempt_id") == aid for r in starts)
     completed = any(r.get("attempt_id") == aid for r in forecasts)
     # A start without a complete outcome stays charged AND blocks the line.
     if started and not completed:
         return
     ```
     若因任何異常導致 `completed` 為 `True`（`forecasts.jsonl` 有紀錄）但 `started` 為 `False`（`forecast_attempt_starts.jsonl` 缺失），`if started and not completed:` 為 `False`，`finish_accounting` 會寫入 `"started": False`，導致 `Budget.reserve` 將已完成的模型呼叫視為未啟動而不計費！
  2. **`advise.run` 啟動前未檢查未決的 Advisory 嘗試與孤兒程序 (`src/advise.py` 第 155–158 行)**：
     * `advise.run` 僅檢查 `shared / "forecast_attempt_starts.jsonl"` 與 `shared / "forecasts.jsonl"`，**未檢查 `shared / "advisory_usage.jsonl"` 是否存在未完結合約 (`set(admitted) != set(finished)`)**，也未檢查 `data/advisory` 下是否有未結束的孤兒程序 (`orphan.no_process`)。
     * 若第 1 題執行時某個模型（如 Gemini）被強行中斷，緊接著執行第 2 題時，`advise.run` 不會在入口處阻斷，反而會先對所有模型執行 `F.preflight`（觸發 Gemini `/usage` 與各 CLI 版本檢查），並啟動另外兩個模型的 CLI，甚至對已阻斷的 Gemini 模型也先執行 `F.run_attempt` 開頭的 `/usage` 快照查詢與 `raw_dir` 建立，直到 `before_attempt` 才被 `Budget.reserve` 攔下。
  3. **`b.outcome` 與 `finish_accounting` 在兩層不同 `finally` 分開執行導致的狀態撕裂 (`src/forecast.py` 第 505–509 行 vs `src/advise.py` 第 153–154 行)**：
     * `F.run_attempt` 在第 507 行（**在 `rec` 寫入 `root / "forecasts.jsonl"` 之前**）就先呼叫 `ctx.admission_outcome(attempt_id, start_written)`（即 `b.outcome`），接著在寫入 `root / "forecasts.jsonl"`（第 535 行）之後，才回到 `run_line` 的 `finally` 呼叫 `finish_accounting`。
     * 若在 `b.outcome(aid, True)` 寫入 `budget_events.jsonl` 後、`finish_accounting` 寫入 `advisory_usage.jsonl` 前發生中斷（或 `forecasts.jsonl` 寫入後程序終止），`budget_events.jsonl` 已有 `budget_outcome`，但 `advisory_usage.jsonl` 卻永久停留在未完成狀態，且沒有任何對既有批次目錄執行 `finish_accounting` 的重對帳機制。

---

### BLOCKER 6：錄入優先權 (Admission Priority)、Bot 題目重疊檢查不完整與 Stale/Changed Inputs
* **審查維度**：Admission Priority / Stale or Changed Inputs / Shared Locks
* **實際證據**：
  1. **未完整檢查 Bot 既有賽事與活動 Campaign 待辦工作 (`src/advise.py` 第 68–86 行)**：
     * `fresh_inputs` 僅輪詢 `{33121}` 與 `projects/minibenches/`，且第 81–83 行只檢查 `r["tournament_id"] in active`（不含 `33121`、不含 `config/tournaments.json` 其他白名單賽事，亦未檢查 `shared / "questions.jsonl"` 或 `config/cycle_campaign.json` 中是否仍有尚未完成 Tier 1 / Repair / Tier 2 / Submit 的開放題目）。
     * `TournamentIndex.from_polls(records)` 未將 `shared / "questions.jsonl"`（例如 M2 主站題目）的 `question_id`、`post_id`、`title_key` 納入重疊排除索引。
  2. **鎖外載入設定檔且未驗證 Campaign 註冊與交接狀態 (`src/advise.py` 第 146–152 行)**：
     * `campaign`、`cfg`、`policy` 在進入 `ExitStack` 取得 `cycle.lock` / `scheduler.lock` **之前**讀取；取得鎖後未重新讀取，也未在執行 `F.preflight` 與 `fresh_inputs` 前驗證 `V.hash_obj(cfg) == campaign["run_config_sha256"]`、`V.hash_obj(policy) == campaign["quota_policy_sha256"]` 以及 `budget_handoff.accounting_start(shared, campaign)`（確認 campaign 未被 supersede 且與 `continuous_campaigns.jsonl` 一致）。
     * `run_line` 內的 `check()` 也未檢查磁碟上的 `config/cycle_campaign.json` 是否已變更或 `budget_window_end` 是否已過期。
  3. **`safe_snapshot` 未檢查 `F.eligible` 與 `V.question_schema`，且 `forecast_prompt` 傳入錯誤的 `today` 格式 (`src/advise.py` 第 54–66、148 行)**：
     * `safe_snapshot` 只檢查 `parse_iso(snap["scheduled_close_time"]) <= current`，未檢查 `F.eligible(snap, cfg, current)`（即 `decision_deadline` 提前量）與 `V.question_schema(snap)`。
     * `run_line` 第 148 行呼叫 `F.forecast_prompt(snap, to_iso(utc_now()))`，傳入含毫秒的完整 UTC 時間戳（且三個執行緒各自呼叫 `utc_now()`，導致三個模型的 prompt 與 `prompt_sha256` 互不相同），而 `src/forecast.py` 第 579 行標準做法是傳入日期字串 `ctx.now().date().isoformat()`。

---

### BLOCKER 7：多執行緒並行安全 (Concurrency) 與帳本 Transaction 漏包
* **審查維度**：Concurrency / Shared Locks / Source Separation
* **實際證據**：
  1. **`src/flab/workdir.py` 第 14、73、104、110 行**：
     全域字典 `_CREATED_ATTEMPT_BASES: dict[Path, Path] = {}` 在 `make_attempt_dir` 與 `remove_attempt_dir` 中被 `ThreadPoolExecutor(max_workers=3)` 的三個執行緒並發修改與移除，未加 `threading.Lock` 保護。
  2. **`src/forecast.py` 第 542 行 (`leak_count`) 與 `src/advise.py` 多處帳本讀寫未包裹 `transaction`**：
     * 在 `ThreadPoolExecutor(max_workers=3)` 中，三個執行緒共享同一個 `root / "forecasts.jsonl"` 與 `root / "audits.jsonl"`。
     * 當執行緒 A 在 `with transaction(ctx.forecasts_log.parent):` 內執行 `append_record(ctx.forecasts_log, rec)`（第 533–535 行）時，執行緒 B 可能剛離開該區塊並在第 542 行執行 `leak_count(ctx.forecasts_log, ...)`（其內部呼叫未加鎖的 `read_records`），在並發寫入瞬間可能讀到半行 JSON 導致崩潰。
     * `src/advise.py` 第 84–85、155–168、170、176–181 行對 `shared` 與 `root` 下 JSONL 帳本的讀寫亦未包裹在 `with transaction(shared):` / `with transaction(root):` 中。
  3. **`src/flab/advisory_usage.py` 未對欄位做白名單限制**：
     `usage(root)` 未拒絕非元數據欄位（如 `probability`、`option_probabilities`、`reasoning_summary` 等），應強制驗證欄位白名單以在程式碼層面保證 `shared / "advisory_usage.jsonl"` 絕不可能混入預測答案。

---

## 二、 精確修補方案 (Precise Patches)

### Patch 1：`src/flab/advisory_usage.py`（強制元數據白名單與欄位型別檢查）
```python
"""Metadata-only shared accounting for separately stored human advisory attempts."""
from flab.ledger import read_complete
from flab.timeutil import parse_iso

VERSION = "advisory_usage_v1"
ADMITTED_KEYS = frozenset({
    "type", "version", "at", "attempt_id", "player",
    "question_id", "batch", "campaign_id", "protected_attempts", "decision_id",
})
FINISHED_KEYS = frozenset({
    "type", "version", "at", "attempt_id", "player", "started",
})


def usage(root):
    admitted, finished = {}, {}
    for row in read_complete(root / "advisory_usage.jsonl"):
        aid, player = row.get("attempt_id"), row.get("player")
        if not isinstance(aid, str) or not aid or player not in ("claude", "gemini", "gpt"):
            raise ValueError("invalid advisory usage identity")
        parse_iso(row.get("at"))
        if row.get("type") == "advisory_admitted":
            if not set(row).issubset(ADMITTED_KEYS) or aid in admitted or aid in finished:
                raise ValueError("duplicate or invalid advisory admission")
            if "question_id" in row and type(row["question_id"]) is not int:
                raise ValueError("invalid advisory question_id")
            admitted[aid] = row
        elif row.get("type") == "advisory_finished":
            prior = admitted.get(aid)
            if not set(row).issubset(FINISHED_KEYS) or prior is None or aid in finished \
                    or prior["player"] != player or type(row.get("started")) is not bool \
                    or parse_iso(row["at"]) < parse_iso(prior["at"]):
                raise ValueError("invalid advisory completion")
            finished[aid] = row
        else:
            raise ValueError("unknown advisory usage event")
    return admitted, finished
```

### Patch 2：`src/budget.py`（支援 Advisory `protected` 容量與 Gemini `protected_quota` 計算，並強制驗證 `budget_events.jsonl` 與 `advisory_usage.jsonl` 帳務對等性）
在 `Budget.reserve`（`src/budget.py` 第 155–195 行）中：
```python
    def reserve(self, request: dict, *, pending_tier1: int, expected_new: int, repair_reserve: int,
                advisory: bool = False) -> dict:
        with transaction(self.root):
            start, end = self._window()
            forecasts, starts, snapshots, events = self._data()
            advisory_admitted, advisory_finished = advisory_usage(self.root)
            if not any(r["type"] == "budget_policy_activated" and r.get("version") == VERSION for r in events):
                activation = {"type": "budget_policy_activated", "version": VERSION, "policy_version": FS.VERSION,
                              "at": to_iso(self.now()), "scope": "new reservations only",
                              "preserve_prior_stops_lifts_and_usage": True}
                append_record(self.log, activation)
                events.append(activation)
            player, tier, qid, aid = (request[k] for k in ("player", "tier", "question_id", "attempt_id"))
            if tier not in (1, 2) or not isinstance(advisory, bool) or (advisory and tier != 1) \
                    or any(type(v) is not int or v < 0 for v in (pending_tier1, expected_new, repair_reserve)):
                raise ValueError("invalid tier or protected capacity")
            reservations = {r["attempt_id"]: r for r in events if r["type"] == "budget_reserved"}
            outcomes = {r["attempt_id"]: r for r in events if r["type"] == "budget_outcome"}
            if aid in reservations or aid in advisory_admitted:
                raise ValueError("attempt already has a budget reservation")
            for xid, adm in advisory_admitted.items():
                res = reservations.get(xid)
                out = outcomes.get(xid)
                fin = advisory_finished.get(xid)
                if res is not None and res.get("player") != adm["player"]:
                    raise ValueError("advisory player mismatch with budget reservation")
                if fin is not None and out is not None:
                    expected_status = "started" if fin["started"] else "released"
                    if out.get("status") != expected_status:
                        raise ValueError("advisory completion contradicts budget outcome")
                elif fin is not None and res is not None and out is None:
                    raise ValueError("advisory completion missing budget outcome")
                elif fin is None and out is not None and out.get("status") == "released":
                    raise ValueError("released budget outcome missing advisory completion")
            player_starts = [r for r in starts if r.get("player") == player]
            window_starts = [r for r in player_starts if start <= parse_iso(r["started_at_utc"]) < end]
            done = {r["attempt_id"]: r for r in forecasts}
            active = [r for r in reservations.values() if r["player"] == player
                      and r["attempt_id"] not in done
                      and r["attempt_id"] not in advisory_finished
                      and outcomes.get(r["attempt_id"], {}).get("status") != "released"]
            reason = None
            if active or any(r["attempt_id"] not in done for r in player_starts):
                reason = "unresolved_or_running_attempt"
            if any(r["player"] == player and xid not in advisory_finished
                   for xid, r in advisory_admitted.items()):
                reason = "unresolved_or_running_attempt"
            acceptance = read_complete(self.acceptance_log) if self.acceptance_log else []
            calls = [r for r in acceptance if r.get("type") == "acceptance_call_started" and r.get("player") == player]
            journaled_runs = {r["run_id"] for r in calls}
            acceptance_count = len({r["attempt_id"] for r in calls if start <= parse_iso(r["started_at"]) < end})
            acceptance_count += sum(r.get("model_call_count", 3) for r in acceptance
                if r.get("type") == "acceptance_result" and r.get("player") == player
                and r.get("run_id") not in journaled_runs and start <= parse_iso(r["finished_at"]) < end)
            cap = self.campaign["weekly_attempt_caps"].get(player)
            used_ids = {r["attempt_id"] for r in window_starts}
            used_ids.update(r["attempt_id"] for r in forecasts if r.get("player") == player
                and start <= parse_iso(r.get("started_at_utc") or r["finished_at_utc"]) < end)
            used_ids.update(xid for xid, r in advisory_admitted.items() if r["player"] == player
                and start <= parse_iso(r["at"]) < end
                and advisory_finished.get(xid, {}).get("started") is not False)
            used_ids.update(xid for xid, r in reservations.items() if r["player"] == player
                and start <= parse_iso(r["at"]) < end
                and outcomes.get(xid, {}).get("status") != "released"
                and advisory_finished.get(xid, {}).get("started") is not False)
            used = len(used_ids) + acceptance_count
            protected = pending_tier1 + expected_new + repair_reserve if (tier == 2 or advisory) else 0
            if type(cap) is not int or cap <= 0:
                reason = "weekly_attempt_cap_unset"
            elif used + protected + 1 > cap:
                reason = "weekly_attempt_capacity_reserved_or_exhausted"
```
如此一來，當 `advise.py` 呼叫 `b.reserve(request, pending_tier1=0, expected_new=2 * campaign["expected_new_questions"], repair_reserve=campaign["repair_reserve"], advisory=True)` 時：
1. `protected` (= 22) 在 `b.reserve` 內部原子生效；
2. 若容量不足，`b.reserve` 直接寫入 `budget_denied`（包含正確的 `"protected_attempts": 22` 與 `"reason": "weekly_attempt_capacity_reserved_or_exhausted"`），不會產生假性 `budget_reserved` + `released`；
3. 對 Gemini 而言，第 283 行 `protected_quota = min(protected * first_estimate, FS.TIER1_QUOTA_RESERVE_CAP)` 會自動保留未來 Bot 嘗試所需的 Argon 配額！

### Patch 3：`src/flab/workdir.py`（為 `_CREATED_ATTEMPT_BASES` 加上執行緒鎖保護）
```python
import threading
...
_CREATED_ATTEMPT_BASES: dict[Path, Path] = {}
_BASES_LOCK = threading.Lock()
```
並在 `make_attempt_dir` 與 `remove_attempt_dir` 中讀寫 `_CREATED_ATTEMPT_BASES` 時以 `with _BASES_LOCK:` 包裹。

### Patch 4：`src/advise.py`（修正所有阻斷性問題）
```python
def batch_path(value: Path, project=PROJECT_ROOT):
    if value.is_symlink():
        raise ValueError("advisory batch path must not be a symlink")
    root = value.resolve()
    allowed = (project / "data/advisory").resolve()
    if root.parent != allowed or not root.is_dir():
        raise ValueError("advisory batch must be an existing direct child of data/advisory")
    return root


def safe_snapshot(post, index, *, cfg=None, now=None):
    snap = snapshot_from_post(post, "human_cup")
    if snap is None or SELECTED_POSTS.get(snap["post_id"]) != snap["question_id"] \
            or 33108 not in snap["project_ids"]:
        raise ValueError("not a user-selected single Cup question")
    if snap["question_type"] not in ("binary", "multiple_choice") or snap["status"] != "open":
        raise ValueError("unsupported or no longer open")
    if is_synced_copy(snap, index):
        raise ValueError("overlaps an open/upcoming bot tournament question")
    current = now or utc_now()
    if parse_iso(snap["scheduled_close_time"]) <= current:
        raise ValueError("question is already due")
    snap["tournament_id"] = 33108
    if cfg is not None and F.eligible(snap, cfg, current):
        raise ValueError("question is not eligible before decision deadline")
    V.question_schema(snap)
    return snap


def register_admission(shared, b, root, request, campaign):
    # Same transaction as the normal scheduler. No forecasts/answers enter shared ledgers.
    with transaction(shared):
        admitted, finished = usage(shared)
        if any(aid not in finished for aid in admitted):
            raise F.LineStop("unresolved advisory attempt exists")
        if any(r.get("question_id") == request["question_id"] and r["player"] == request["player"]
               for r in admitted.values()):
            raise F.QuestionDone("this player/question was already admitted; no rerun")
        if sum(1 for r in admitted.values() if r["player"] == request["player"]) >= len(SELECTED_POSTS) \
                or len(admitted) >= len(SELECTED_POSTS) * len(PLAYERS):
            raise F.QuestionDone("session advisory call limit reached")
        decision = b.reserve(
            request,
            pending_tier1=0,
            expected_new=2 * campaign["expected_new_questions"],
            repair_reserve=campaign["repair_reserve"],
            advisory=True,
        )
        if not decision["allowed"]:
            raise F.QuestionDone("budget refused: " + str(decision["reason"]))
        at = to_iso(b.now())
        append_record(shared / "advisory_usage.jsonl", {
            "type": "advisory_admitted", "version": VERSION, "at": at,
            "attempt_id": request["attempt_id"], "player": request["player"],
            "question_id": request["question_id"], "batch": root.name,
            "campaign_id": b.campaign["campaign_id"],
            "protected_attempts": decision["protected_attempts"],
            "decision_id": decision["decision_id"]})
        return decision


def finish_accounting(shared, root, player, aid, b=None):
    with transaction(root):
        starts = read_complete(root / "forecast_attempt_starts.jsonl")
        forecasts = read_complete(root / "forecasts.jsonl")
    started = any(r.get("attempt_id") == aid for r in starts)
    completed = any(r.get("attempt_id") == aid for r in forecasts)
    if completed and not started:
        raise ValueError("completed advisory attempt missing start record")
    # A start without a complete outcome stays charged AND blocks the line.
    if started and not completed:
        if b is not None:
            b.outcome(aid, True)
        return
    with transaction(shared):
        if b is not None:
            b.outcome(aid, started)
        admitted, finished = usage(shared)
        if aid in admitted and aid not in finished:
            append_record(shared / "advisory_usage.jsonl", {
                "type": "advisory_finished", "version": VERSION, "at": to_iso(utc_now()),
                "attempt_id": aid, "player": player, "started": started})
```
並在 `run_line` 與 `run` 中：
1. **將 `admission_outcome=None` 傳給 `F.Context`，改由 `finish_accounting(shared, root, player, aid, b)` 在 `forecasts.jsonl` 寫入完成後，於同一個 `with transaction(shared):` 內同步寫入 `b.outcome(aid, started)` 與 `advisory_finished`**，消除 `b.outcome` 與 `advisory_finished` 之間的時間差與狀態撕裂。
2. **在 `run_line` 中明確捕捉 `F.LineStop`**：
   ```python
   except F.LineStop as exc:
       if exc.notify:
           with transaction(shared):
               append_record(shared / "line_status.jsonl", {
                   "type": "line_stop", "player": player, "run_id": run_id,
                   "question_id": snap["question_id"], "reason": str(exc),
                   "at": to_iso(utc_now()),
               })
       return {"player": player, "error_type": "LineStop", "reason": str(exc),
               "notify_louis": exc.notify, "adoptable": False}
   ```
   同時在 `run_line` 完成後檢查跨帳本（`shared / "forecasts.jsonl"` + `data/advisory/*/forecasts.jsonl`）的 `leak_confirmed` 總數與 `model_mismatch` / `quota_exhausted` 狀態；若跨帳本 `leak_count >= F.LEAK_STOP_COUNT`，立即向 `shared / "line_status.jsonl"` 寫入 `line_stop`。
3. **修正 `F.forecast_prompt` 的日期參數**：
   在 `run` 主執行緒中固定 `today = utc_now().date().isoformat()`，並傳入 `F.forecast_prompt(snap, today)`，確保三個模型使用相同的標準日期格式與一致的 `prompt_sha256`。
4. **在 `run` 取得全部 6 把鎖之後，於 `with transaction(shared):` 內執行啟動前驗證**：
   * 重新讀取 `campaign`、`cfg`、`policy`，驗證 `V.hash_obj(cfg) == campaign["run_config_sha256"]`、`V.hash_obj(policy) == campaign["quota_policy_sha256"]`，並呼叫 `budget_handoff.accounting_start(shared, campaign)` 確認 campaign 有效且未被取代；
   * 檢查 `usage(shared)`：若存在任何 `aid not in finished`，或目標 `SELECTED_POSTS[post_id]` 已有任何 player 被錄入過，立即拒絕啟動（不發起 Metaculus 請求、不查詢 Gemini `/usage`、不寫入 `runs.jsonl` 或 `aggregates.jsonl`）；
   * 檢查 `shared / "forecasts.jsonl"` 與所有 `data/advisory/*/forecasts.jsonl` 是否有任何活躍的 `line_stopped`、`leak_count >= F.LEAK_STOP_COUNT`、`model_mismatch` 退避或 `quota_exhausted` 退避。

---

## 三、 需補充提供的缺失原始碼 (Missing Source Requests)

在給予最終 `PASS` 之前，請提供以下未包含於本次 Packet 的既有檔案原始碼（請勿假設其內部實作）：
1. **`src/quota.py`**：
   * 需確認 `quota.estimate(forecasts, snapshots, ...)` 如何關聯 `forecasts` 與 `snapshots`（當 `shared / "quota.jsonl"` 寫入 Gemini Advisory 的 `before`/`after` 快照，而 `shared / "forecasts.jsonl"` 無該筆 `attempt_id` 時，是否會影響 5h / weekly 估算值或樣本數計算），以及如何修補 `gemini_admission` 讀取隔離 `ctx.forecasts_log` 的問題。
2. **`src/continuous.py`（或排程器/週期執行模組）**：
   * 需確認正常 Bot 週期如何判定「目前無待處理的 Bot 工作（包含主站題目 Tier 1、待修復 Format Repair、Tier 2 升級、待提交 Aggregate）」，以確保 `advise.py` 的 Admission Priority 完全符合 PLAN §8「僅在 Bot 工作之後執行」的規定。
3. **`src/validate.py` 與 `src/audit.py`**：
   * 需確認 `V.question_schema`、`V.validate_output`、`audit.audit_attempt` 在 `ThreadPoolExecutor(max_workers=3)` 並發呼叫下是否完全無共享可變狀態 (thread-safe)。
4. **`config/cycle_campaign.json`、`config/run_config_v2.json`、`config/quota_policy.json`**：
   * 需核對當前線上設定值與 `2 * expected_new_questions + repair_reserve = 22` 的實際參數配置。

---

## 四、 非阻斷性改進建議 (Proposals)

1. **補充針對 `advise.run`、`register_admission` 與 `run_line` 的端到端單元測試 (`tests/test_advisory.py`)**：
   * 目前 `tests/test_advisory.py` 僅測試了 `Budget.reserve`、`usage`、`batch_path`、`finish_accounting` 與 `safe_snapshot`，尚未覆蓋 `register_admission`、`run_line`（包含 `LineStop(notify=True)` 傳播）、跨 `--batch` 目錄重複呼叫攔截，以及 Gemini `protected_quota` 攔截測試。建議在修補後將這 4 項測試加入測試套件。