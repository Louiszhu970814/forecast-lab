### 總體審查結論 (Overall Review Verdict)

* **五個 Python 部署候選檔案程式碼審查 (`src/advise.py`、`src/flab/advisory_usage.py`、`src/budget.py`、`src/forecast.py`、`tests/test_advisory.py`)**：**PASS（無任何程式碼邏輯 Blocker，無須修改程式碼，亦無須重新執行模型 Acceptance）**。
* **封包完整性決定**：**REQUEST CHANGES（僅限補齊來源封包於 `src/continuous.py` 第 94 行處遭 `<truncated 251043 bytes>` 截斷之尾部驗證證據；已完整送達之 5 個部署候選程式碼檔案與 2 個核心依賴檔案均已通過審查）**。

---

### 一、 第一輪 (R1) Blocker 修復驗證與實際證據 (Actual Evidence Verified)

根據本封包中完整提供、未受截斷影響之 7 個檔案原始碼與 SHA256，第一輪審查提出的所有程式碼與設計問題均已逐項驗證解決：

#### 1. 全域 6 次呼叫上限與跨批次單題／單模型禁重跑
* **驗證檔案**：`src/advise.py`（SHA256: `95c9913bd401ed6f32afe3ff92e0b83581b7838cdce3e72d95fd44f4131c212b`）
* **實際證據**：
  * `register_admission`（第 76–94 行）於 `with transaction(shared):` 鎖保護下讀取 `usage(shared)`，篩選 `r.get("version") == "advisory_v1"` 之所有歷史准入紀錄 `mine`。
  * 若同一 `(question_id, player)` 曾在任何批次被准入（包含 `started=False` 之已釋放紀錄），立即拋出 `F.QuestionDone("player/question already admitted in any batch; no rerun")`。
  * 若 `len(mine) >= 6` 或 `request["question_id"] not in SELECTED_POSTS.values()`（`{45330: 45519, 46106: 46343}`），立即拋出 `F.QuestionDone("advisory session call cap or question limit")`。
  * `run`（第 224–230 行）在啟動時亦於 `transaction(shared)` 下檢查 `set(admitted) != set(finished)`（有未結案紀錄則拒絕啟動並要求先 `--recover`）以及該 `post_id` 對應之 `question_id` 是否已存在於 `admitted.values()`。

#### 2. Worker 私有帳本隔離與多執行緒安全
* **驗證檔案**：`src/advise.py`、`src/forecast.py`（SHA256: `9952e7c283c235d0ae24e13f24564447cca50bf30246eab7543ab8b34117fc8a`）
* **實際證據**：
  * `advise.run`（第 255–261 行）預先為每位 player 建立獨立子目錄 `(root / p).mkdir(mode=0o700, exist_ok=True)`，並將 `root / p` 傳入 `ThreadPoolExecutor(max_workers=3)` 的 `run_line`。
  * 每條執行緒各自寫入與讀取專屬的 `batch/<player>/forecasts.jsonl`、`batch/<player>/audits.jsonl`、`batch/<player>/forecast_attempt_starts.jsonl` 及 `batch/<player>/raw/`，徹底消除 `F.run_attempt` 寫入後立即讀取（例如 `leak_count`）時的跨執行緒競態條件。
  * 所有共享帳本（`advisory_usage.jsonl`、`budget_events.jsonl`、`advisory_guards.jsonl`、`line_status.jsonl`、`quota.jsonl`）均透過 `with transaction(shared):` 互斥寫入。
  * 待三個 worker 的 `futures` 全部完成（`outcomes = [f.result() for f in futures]`）後，主執行緒才讀取各 `root / p` 下之私有紀錄進行 `aggregate.build_aggregate`（第 262–265 行），共享目錄完全不落入任何人類預測答案。

#### 3. 預算保護容量與 Gemini Argon 配額之雙重原子預留
* **驗證檔案**：`src/budget.py`（SHA256: `9ff6a8446869651ab1bc48c77d689c75b3f2e0d3faf29ea45f4484f16943dcab`）、`src/advise.py`、`src/budget_handoff.py`（SHA256: `a55a56332d1edcaed1df0fd3c763748119fd4f9ecb898d6d2f91946f9a978dd7`）
* **實際證據**：
  * `run_line`（第 112 行）計算 `protected = 2 * campaign["expected_new_questions"] + campaign["repair_reserve"]`（於預期 10 題、修復預留 2 次時為 22 次），傳入 `b.reserve(..., advisory_protected=protected)`。
  * `Budget.reserve`（`budget_v8`，第 164–207、281–291 行）驗證 `advisory_protected` 為非負整數且僅限 `tier == 1`，設定 `protected = pending_tier1 + expected_new + repair_reserve if tier == 2 else advisory_protected`：
    1. **次數保護**：檢查 `used + protected + 1 > cap`，確保先保留 22 次給未來 bot 任務。
    2. **Gemini Argon 配額保護**：計算 `protected_quota = min(protected * first_estimate, FS.TIER1_QUOTA_RESERVE_CAP)`，並檢查 `argon_remaining - argon_estimate - protected_quota < 1 - self.policy["players"][player]["weekly_cap"]`，原子性地同時保護週次數與 Argon 週配額。
  * `TOKEN_POLICY_VERSIONS = frozenset({"budget_v7", VERSION})`（第 25、234 行）確保升級至 `budget_v8` 後，先前 `budget_v7` 預留之 tier-2 attempt 仍會執行 `tier2_token_assessed` 超標評估，不會遺失斷路器計數。

#### 4. Gemini 配額視窗估算之分離保守性證明
* **驗證檔案**：`src/quota.py`（SHA256: `dd3afe2d2c1266b1d97a907139f9bc17fc27fe60626f98f570612b30ca7d925e`）
* **實際證據**：
  * `quota.window_usage`（第 488–541 行）與 `quota.estimate`（第 544–555 行）以共享 `quota.jsonl` 中當前重置視窗內所有有效快照的最低剩餘值計算總消耗下界 `1.0 - lowest`（分子已包含 bot、advisory 及個人使用之總消耗），再除以傳入之 `attempts` 呼叫數 `calls`（分母），且最終估算值受 `min(float(fallback), value)` 上限約束。
  * 在 advisory 執行時，傳入之私有 `forecasts.jsonl` 完成數為 0（`< min_calls=5`），因此 `quota.estimate` 必回傳最高保守值 `fallback`（`tier2_per_question_cap`）。
  * 在共享 bot 執行時，`quota.jsonl` 已包含 advisory 的快照消耗（分子不減），而共享 `forecasts.jsonl` 不含 advisory 紀錄（分母較小），故計算出之 `mean_per_call` 只會大於或等於合併歷史（或因 `< min_calls` 而使用 `fallback`），絕不會因分離儲存人類預測結果而放寬配額准入。

#### 5. 六把生產鎖、組態一致性與 MiniBench 優先讓路機制
* **驗證檔案**：`src/advise.py`
* **實際證據**：
  * `lock_all`（第 198–200、217 行）在進入 `run` 或 `recover` 時先取得 `cycle.lock`、`scheduler.lock`、`submit.lock`、`forecast.claude.lock`、`forecast.gemini.lock`、`forecast.gpt.lock` 六把非阻塞鎖；且 `run_line` 內的 `check()` 會在每次 `recheck_inputs` 與 `before_attempt` 重新驗證 `run_config_sha256`、`quota_policy_sha256`、`players_enabled`、`STOP_SUBMIT` 與題目截止時間。
  * `fresh_inputs`（第 53–73 行）強制查詢 `projects/minibenches/`，將所有 `is_ongoing` 或未來 `start_date` 之 MiniBench 專案及主賽道 `33121` 納入重疊檢查索引（`is_synced_copy`），且只要任何進行中（`active`）的 MiniBench 存在 `status == "open"` 之題目，立即拋出 `ValueError("MiniBench has open work; advisory yields")`。
  * 單次 `advise.py` 調用僅處理一題，執行完畢即釋放全部六把鎖；不要求對未變動之 `adapters/<player>.py`、`flab/runner.py`、`flab/netguard.py`（見 `F.RUNTIME_FILES`）重新執行模型 Acceptance。

#### 6. 安全事件傳播、Metadata-only Guards 與跨產線退避／冷卻整合
* **驗證檔案**：`src/advise.py`、`src/flab/advisory_usage.py`（SHA256: `63e65e848e75c95d7803180566c8e2566084c4e6448a940c614b2f5278277c81`）、`src/forecast.py`
* **實際證據**：
  * **決策追蹤閉環**：`run_line` 中 `shared_inputs = {"advisory_batch": str(root), "advisory_version": VERSION}` 為非空字典，解決了 R1 中空字典 `{}` 在 `inputs = ctx.reconciliation or {}` 下建立新物件導致 `admission_decision_id` 無法寫入 `forecast_attempt_started` 與 `forecast_attempt` 的問題。
  * **Notify 傳播與單次洩漏停線**：`run_line` 捕獲 `F.LineStop` 時以 `stop=exc.notify` 寫入 `line_status.jsonl`；`propagate_safety`（第 156–177 行）在發現 `safety_stop_reason`、`tool_violations`、`tool_scope_violation`、`recovery_no_rerun` 或單次 `leak_confirmed` 時，一律保守寫入 `type: "line_stop"`（`reason: "advisory_safety_event"`），並具備冪等檢查避免重複寫入。
  * **Metadata-only Guards**：當發生 `model_mismatch` 或 `quota_exhausted` 時，`propagate_safety` 將不含預測內容的 10 個元資料欄位寫入 `shared / "advisory_guards.jsonl"`；`F.line_stopped`（`forecast_pipeline_v9`，第 290–346 行）在 `transaction(log.parent)` 下透過 `guards(log.parent)` 讀取並合併計算 `advisory_quota_backoff`、`model_mismatch_backoff` 與 `model_mismatch_cooldown`。

#### 7. 崩潰復原 (`--recover`) 與帳務一致性閉環
* **驗證檔案**：`src/advise.py`、`src/budget.py`、`src/forecast.py`、`src/flab/advisory_usage.py`
* **實際證據**：
  * `Budget.recover`（第 143–145 行）將 `advisory_admitted` 併入 `started` 集合，確保排程器不會將 advisory 預留當成未啟動的排程預留而釋放。
  * `finish_accounting`（第 97–107 行）對 `started and not completed` 直接返回，維持未結案狀態（計入 `used_ids` 並觸發 `unresolved_or_running_attempt` 阻斷），對 `completed and not started` 則拋出 `ValueError`。
  * `recover`（第 203–209 行）與 `recover_accounting`（第 180–196 行）在六把生產鎖保護下，針對每位 player 的 `root / p` 先執行 `F.recover_interrupted(ctx)`（驗證 `orphan.expired` 與 `orphan.no_process` 後離線剖析輸出並寫入 `error_type: "interrupted"` 及 `recovery_no_rerun`），再對已可結案之紀錄依序執行 `propagate_safety`、`b.outcome(aid, aid in starts)` 與 `finish_accounting`，全程不發起任何模型呼叫且不可重跑。
  * `Budget.reserve`（第 170–176 行）強制驗證 `advisory_finished["started"]` 與 `budget_outcome["status"]`（`"started"` vs `"released"`）的一致性，若矛盾則 fail-closed 拋出 `ValueError`。

#### 8. 測試套件驗證
* **驗證檔案**：`tests/test_advisory.py`（SHA256: `39742d9261e72f0b53446f6b495fbd0838caae5ab9e074b058d6b0aa6f2cdd52`）
* **實際證據**：完整檢視全部 23 項單元與整合測試，涵蓋配額扣抵、未結案阻斷、未啟動釋放、損毀帳本 fail-closed、目錄邊界、題目與重疊守衛、跨批次禁重跑、6 次全域上限、`budget_v7` 升級相容、Gemini 22 次 Argon 預留保護、安全事件停線、真實引擎 `admission_decision_id` 追蹤、孤兒程序復原及生產鎖衝突保護。

---

### 二、 阻斷項目：具體缺漏證據清單 (Specific Missing Evidence Due to Packet Truncation)

本封包於 `FILE src/continuous.py` 第 94 行處出現 `<truncated 251043 bytes>`，導致封包後半部內容在傳輸中被截斷。遵循「僅根據提供之來源封包工作、不虛構批准、具體指出缺少何種證據」之原則，**已驗證之 5 個部署候選程式碼檔案無須修改與重傳**，僅需補充以下因截斷而未送達之精簡證據即可完成最終 PASS 簽核：

1. **離線測試最終執行結果輸出**：
   * 封包前言敘明 `"final exact result will be appended to this packet before review invocation"`，因尾部 `<truncated 251043 bytes>` 截斷，未見最終附加之測試執行結果摘要（pytest 通過數／失敗數）。
2. **`PLAN.md`／文件部署候選變更內容**：
   * 封包前言將 `PLAN/docs` 列為部署候選項目（`"Only deploy candidates: src/advise.py, src/flab/advisory_usage.py, src/budget.py, src/forecast.py, tests/test_advisory.py plus PLAN/docs"`），該部分位於截斷處之後而未能檢視。
3. **被截斷之對照依賴關鍵片段（僅需提供精簡片段，無須重傳整包 250 KB 檔案）**：
   * `src/continuous.py` 第 94 行之後有關 `Queue` 僅巡迴當前 live open 題目的實作片段。
   * `config/cycle_campaign.json` 內容（確認生產環境設定之 `venues: ["minibench"]`、`expected_new_questions`、`repair_reserve` 等數值）。
   * `src/flab/workdir.py`（`_CREATED_ATTEMPT_BASES` 段落）與 `src/scheduler.py`（`ThreadPoolExecutor` 段落）之簡短片段。

---

### 三、 非阻斷性建議 (Non-Blocking Proposals / Optional Refactors)

以下觀察**均非 Blocker**，不影響系統安全性與帳務正確性，亦不阻礙本次兩題 Cup 緊急預測（45330、46106）之部署：

1. **`advisory_usage.jsonl` 中 `"batch"` 欄位命名語意（`src/advise.py` 第 91 行）**：
   * `run` 將 `root / p` 傳入 `run_line`，因此 `register_admission` 記錄之 `"batch": root.name` 值為 player 名稱（如 `"claude"`），而完整批次與 player 路徑已精確記錄於 `"batch_path": str(root.resolve())` 且與 `recover_accounting` 完全吻合。日後若希望 `"batch"` 顯示外層批次目錄名稱，可改為 `root.parent.name`（純屬可讀性，不影響任何功能或復原邏輯）。
2. **`F.line_stopped` 中 `advisory_quota_backoff` 與人工 `line_resume` 之互動（`src/forecast.py` 第 296–300 行）**：
   * 目前 `advisory_quota_backoff` 直接判斷 `now < parse_iso(r["quota_retry_at_utc"])`（固定 30 分鐘自動解除），未額外比對 `resumed_at`（這與 `forecast.run` 第 779 行處理 bot 自身 `quota_exhausted` 的行為一致，且更為保守）。日後若有需要支援透過 `forecast.py resume` 手動提前解除配額退避，可考慮加入 `resumed_at` 判斷。