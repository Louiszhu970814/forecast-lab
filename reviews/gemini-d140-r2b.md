VERDICT: PASS

### 1. 資料包完整性與審查範圍確認
- **資料包完整性**：本次提供的 D140 R2B 審查資料包自開頭 `D140 R2B.` 至最終標記 `END-D140-CODE-R2B` 完整無截斷，包含跨模組契約、`reviews/d140-r2-response.md`、`src/flab/ledger.py`、`src/flab/jsonl.py`、`src/scheduler.py`、`src/reconcile.py`、`src/budget.py`、`src/discovery.py`、`src/advise.py`、`src/practice.py`、`src/autoload_maintenance.py`、`launchd/run.sh`、`launchd/org.forecast-lab.line-recovery.plist`、`config/line_recovery.json`、`PLAN.md` 與 `docs/tasks/D140-AUTO-RECOVERY.md` 差異及全文、`tests/test_scheduler.py` 與 `tests/test_discovery_cadence.py` 差異、`deploy.py`、`test_deploy.py`、主機測試結果摘要及共用來源清單（Manifest SHA256: `d4feeb83673a3866a42c136e357bc2c8a233049344e36bec3eab76c16138b697`）。
- **審查方法與限制**：本審查僅依據資料包中之凍結來源文字進行靜態檢視，未呼叫任何外部工具、未讀寫檔案或執行指令、未宣稱自行執行測試。本 `VERDICT: PASS` 僅針對 **D140 R2B 模組**，不構成全系統核准；依部署契約，必須待兩個官方模型對所有模組（R2A 與 R2B）均給出實際 `PASS` 後方可部署。

---

### 2. 阻斷性問題（Blockers）
- **無（None）**。前次 R1 涉及 B 模組之阻斷項（BLK4、BLK5）及跨模組介面契約均已於實際程式碼與測試中妥善解決。

---

### 3. 實際證據核對（Actual Evidence from Frozen Source）

#### 3.1 BLK4 修正與同線鎖／排程器隔離及部分線到期結算（`src/scheduler.py`、`src/reconcile.py`、`src/advise.py`、`src/practice.py`、`src/flab/ledger.py`）
- **同線鎖與零跨線阻塞**：
  - `src/scheduler.py:348-458`（`S.execute`）於 Tier 1 僅取 `F.Lock(self.root / f"forecast.{job.player}.lock")`；Tier 2 委派 `src/reconcile.py:163`（`R.run`）同樣僅取 `F.Lock(root / f"forecast.{ctx.player}.lock")`。
  - 當 `line_recovery` 針對已停線的 Gemini 執行實際恢復並短暫持有 `forecast.gemini.lock` 時，Claude 與 GPT 各自取得 `forecast.claude.lock` 與 `forecast.gpt.lock`，完全不受阻塞。
  - 在 `src/scheduler.py:540-611`（`S.run`）中，`ThreadPoolExecutor(max_workers=3)` 針對每條線並行提交工作；若某線因鎖忙碌拋出例外，在 `self.continuous` 模式下由 `S.run`（第 585–597 行）捕捉並寫入 `outcome="line_error"` 至 `scheduler_jobs.jsonl`，且因 `"line_error"` 不在 `TERMINAL`（第 65–66 行）中，僅將該玩家加入當次 `paused` 集合，不會將該題標記為永久終止，亦不影響其他兩線執行。
- **決策截止部分線結算（Partial-line Finalization）**：
  - `src/scheduler.py:460-508`（`S.finalize`）中，當 `expired = qid in force or (deadline is not None and self.now() >= deadline)` 成立時，即使單一產線尚未 `_terminal`，仍會呼叫 `A.build_aggregate` 建立 `stage="final"`、`completion_status="complete_local"`、`completion_reason="decision_deadline"` 之匯總結果並產生評論，確保單線故障不阻斷其他合格線在決策截止時整合送出。
  - `tests/test_scheduler.py:342-352` 新增 `test_busy_recovery_line_does_not_block_other_lines_or_deadline_finalization`，驗證持有 `forecast.gemini.lock` 時，`S.run()` 將 `gemini` 列為 `paused_players`、僅記 `line_error`，而 `claude` 與 `gpt` 正常完賽且 `finalize` 順利產出 `complete_local` 匯總與評論。
- **跨模組一次補做槽與狀態轉換**：
  - 初始嘗試因 `LineStop` 中斷時，`scheduler.py:431-438` 與 `reconcile.py:303-310` 記錄之工作結果為 `outcome="line_stopped"`，不在 `TERMINAL` 集合中；`S._terminal`（`src/scheduler.py:269-279`）回傳 `False`。當恢復器寫入 `line_resume` 與 `retry_grant` 後，排程器可重新排入該題並使用既有 `initial -> rerun` 槽位；若 `rerun` 後續再次失敗為 `timeout`/`cli_error`/`tool_denied`/`incomplete`，`scheduler.py:424-428` 檢查到已有同 snapshot 之 `rerun` 紀錄即轉為 `outcome="attempts_exhausted"`（屬於 `TERMINAL`），不會無限重試。
- **帳冊交易一致性**：
  - `src/flab/ledger.py:16-33` 提供基於 `root / "ledger.lock"`（`fcntl.LOCK_EX`）與執行緒重入鎖之 `transaction(root)`，以及嚴格檢查結尾換行與 JSON 物件結構之 `read_complete(path)`。
  - 所有寫入 `line_status.jsonl` 之呼叫點（`src/scheduler.py:436-438`、`src/reconcile.py:307-310`、`src/advise.py:173-178` 與 `199-203`、`src/practice.py:307-322` 與 `395-397`）均在 `with transaction(...):` 保護下執行 `append_record`。

#### 3.2 BLK5 修正與喚醒資格判定（`src/discovery.py`、`tests/test_discovery_cadence.py`）
- **實際稍後 Resume 與停線／配額退避守衛**：
  - `src/discovery.py:100-106` 建立 `stopped` 集合，涵蓋 `not enabled[p]`、`F.line_stopped(root/'line_status.jsonl', p, root/'forecasts.jsonl', now=now)` 為真者，以及 `forecasts.jsonl` 中存在未到期 `quota_retry_at_utc`（`now < parse_iso(row['quota_retry_at_utc'])`）之玩家；同時從 `line_status.jsonl` 讀取各玩家的 `line_resume` 時間戳 `resumes`。
  - `src/discovery.py:137-148`（`waiting(row)`）僅在 `row.get('outcome') == 'line_stopped'` 且 `row.get('player') not in stopped` 時，進一步檢驗 `resumed = resumes.get(row.get('player'))` 是否存在且滿足 `row.get('finished_at') and resumed >= parse_iso(row['finished_at'])`。唯有在該舊工作完成時間點之後確實發生過 `line_resume`、且目前無有效停線或配額退避時，才解除舊 `line_stopped` 等待並喚醒排程器。
  - `tests/test_discovery_cadence.py:132, 230-243` 已補齊 `finished_at` 欄位與兩項負向測試（`test_nonpersistent_line_yield_keeps_its_retry_wait_without_later_resume` 驗證無稍後 resume 或僅有較早 resume 時不誤喚醒；`test_later_resume_does_not_override_forecast_quota_wait` 驗證存在稍後 resume 但仍有未到期 `quota_retry_at_utc` 時不誤喚醒）。

#### 3.3 包含進行中嘗試之預算守衛（`src/budget.py`）
- **線鎖內預留與 Pending-Inclusive 計費**：
  - `scheduler.py:381-389` 與 `reconcile.py:276-296` 的 `before_attempt` 均在持有各自 `forecast.<player>.lock` 期間呼叫 `Budget.reserve`。
  - `src/budget.py:170-182` 將尚未完成且未釋放之 `budget_reserved`（`active`）以及已寫入 `forecast_attempt_starts.jsonl` 但尚未寫入 `forecasts.jsonl` 之啟動紀錄（`any(r["attempt_id"] not in done for r in player_starts)`）視為 `unresolved_or_running_attempt` 予以阻擋。
  - `src/budget.py:191-196` 計算週配額 `used_ids` 時同時納入 `window_starts`（`forecast_attempt_starts.jsonl`）與 `forecasts.jsonl`（含失敗之 initial 嘗試）及 `advisory_admitted`。CLI 啟動前若因故中止可由 `Budget.outcome(..., started=False)` 或跨次 `Budget.recover()`（第 130–145 行，僅針對不在 `started | finished` 的舊預留）釋放；一旦寫入 `forecast_attempt_starts.jsonl` 即永久佔用配額槽位，符合跨模組契約。

#### 3.4 部署閘門與服務設定（`deploy.py`、`test_deploy.py`、`launchd/*`、`config/line_recovery.json`）
- **六鎖與閒置安全點**：
  - `deploy.py:98-140`（`Deploy.apply`）在 `lock_all(self.project/'data', stack)`（鎖定 `cycle`、`scheduler`、`submit` 及三條產線鎖）與 `M.require_idle(..., horizon_s=600)`（確認無未決模型呼叫／驗收、無殘留程序、無未完成提交意圖、無逼近截止之未答題）下執行。
  - `Deploy.validate`（第 71–96 行）強制驗證 `PLAN D140` 授權、`source_sha256`、`deployment_sha256`、雙審（Claude 與 Gemini）對所有模組（`['r2a', 'r2b']` 或 `['r2']`）之 `VERDICT: PASS` 與結束標記及 SHA256，並禁止刪除檔案或修改受保護路徑（`src/adapters`、`src/flab/runner.py`、`src/flab/netguard.py`、`src/acceptance`、核心設定與 prompts）。
  - `apply` 與 `activate` 明確分離：`apply` 失敗時自動從備份原子還原所有已變更檔案並重新啟用原 `cycle` 定時器；`activate`（第 142–161 行）再次驗證已套用檔案與不可變設定之雜湊無漂移後，才安裝並啟用 `org.forecast-lab.line-recovery`（`StartInterval=300`、`RunAtLoad=false`）。
  - `test_deploy.py` 涵蓋前檢／煙霧測試失敗回滾、來源或審查漂移阻擋、模組缺漏或 `REQUEST_CHANGES` 阻擋等 15 項測試案例。

---

### 4. 非阻斷性建議與觀察（Non-blocking Proposals）
1. **`src/discovery.py` 多筆 `line_resume` 時間戳讀取韌性**：
   - 第 105–106 行以字典推導式 `resumes = {r['player']: parse_iso(r['at']) for r in read_complete(root/'line_status.jsonl') if r.get('type') == 'line_resume'}` 取各玩家最後一筆 `line_resume` 的時間。由於 `line_status.jsonl` 為單調追加寫入，正常情況下最後一筆即為最新時間；未來若欲防範極端系統時鐘回撥，可考慮對同一玩家取 `max(parse_iso(r['at']))`。此點不影響目前正確性。
2. **`deploy.py` 離線煙霧測試清單**：
   - `deploy.py:124-126` 於 `apply()` 階段執行之快速離線測試包含 `test_line_recovery.py`、`test_forecast.py`、`test_reconcile.py` 與 `test_discovery_cadence.py`。日後若調整部署腳本，亦可考慮將 `tests/test_scheduler.py` 納入 `apply()` 煙霧測試清單中（目前完整回歸測試 `full-tests-final-r2.txt` 已涵蓋全部 3536 項通過）。

END-D140-CODE-R2B