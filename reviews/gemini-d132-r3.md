### 總體審查結論 (Overall Review Verdict)

* **總體決定 (Overall Verdict)**：**PASS（所有部署候選程式碼、對照依賴片段、測試結果摘要與 `PLAN.md`／D132 文件變更均已完整審查通過，無任何 Blocker）**。
* **五個 Python 部署候選檔案（與 R2 逐位元組相同，SHA256 完全一致）**：**PASS**
  * `src/advise.py`：`95c9913bd401ed6f32afe3ff92e0b83581b7838cdce3e72d95fd44f4131c212b`
  * `src/flab/advisory_usage.py`：`63e65e848e75c95d7803180566c8e2566084c4e6448a940c614b2f5278277c81`
  * `src/budget.py`：`9ff6a8446869651ab1bc48c77d689c75b3f2e0d3faf29ea45f4484f16943dcab`
  * `src/forecast.py`：`9952e7c283c235d0ae24e13f24564447cca50bf30246eab7543ab8b34117fc8a`
  * `tests/test_advisory.py`：`39742d9261e72f0b53446f6b495fbd0838caae5ab9e074b058d6b0aa6f2cdd52`
* **前次審查要求補充項目之核對**：前次（R2）審查因封包截斷而要求補齊之所有項目（離線測試執行結果摘要、`PLAN.md` 與 D132 任務文件、`src/continuous.py`、`config/cycle_campaign.json`、`src/flab/workdir.py`、`src/scheduler.py` 及相關底層帳本／孤兒程序模組片段）**均已於本補充封包中完整提供，無任何遺漏項目**。
* **狀態聲明**：審查僅依據所提供之來源封包資料進行；審查者未執行任何測試或工具命令（測試結果係採認封包內作者提供之執行輸出紀錄），目前程式碼僅處於 staged 狀態，尚未發生任何實際預測呼叫。

---

### 一、 阻斷項目 (Blockers)

* **無 (None)**。R1 提出之所有程式碼設計問題已於 R2 驗證修復，R2 因封包截斷而保留之所有缺漏驗證證據已於本補充封包全數補齊並驗證通過。

---

### 二、 實際證據審查 (Actual Evidence Verified in Supplemental Packet)

#### 1. `src/continuous.py` 佇列更新與預算保護容量一致性
* **實際證據**：
  * `Poller` 於輪詢各賽事時，僅在 `timing["status"] == "open"` 時將題目加入 `current[qid]`（標記 `"status": "open"`、`"ground": venue`、`"priority": "minibench" if venue == "minibench" else "main"`）。
  * `Queue.__call__` 呼叫 `self.poller()` 取得當前開放題目 `current`，經 `self.update(current)` 過濾已提交（`already_submitted`）、不支援題型（`unsupported_type`）與阻斷修訂（`blocked_revision`）後，將待處理之開放題目寫入 `self.campaign["questions"]`。
  * 這證明持續模式（continuous mode）下的 `campaign["questions"]` 僅包含當前實際處於 `open` 狀態之題目，已關閉（closed）之 MiniBench 題目不會殘留於佇列中。搭配 `advise.py` 在發現任何進行中 MiniBench 有 `open` 題目時立即讓路（`MiniBench has open work; advisory yields`），以及 `advisory_protected = 2 * campaign["expected_new_questions"] + campaign["repair_reserve"]` 之設計，完全確保機器人賽與人類賽輔助模式之間的預留容量計算精確且互不衝突。

#### 2. `config/cycle_campaign.json` 生產環境配置核對
* **實際證據**：
  * 確認 `version` 為 `"continuous_v9"`、`campaign_id` 為 `"d118_minibench_20261103T0400Z"`、`venues` 為 `["minibench"]`，與 `advise.py` 之 `campaign.get("venues") != ["minibench"]` 守衛完全吻合。
  * 確認 `expected_new_questions: 10`、`repair_reserve: 2`，因此 `advise.py` 為每條產線保留之未來機器人賽容量確為 `2 * 10 + 2 = 22` 次，且週上限（Claude 130、Gemini 200、GPT 130）、時間窗口（`2026-11-03T04:00:00.000Z` 至 `2026-11-10T04:00:00.000Z`）、固定雜湊（`run_config_sha256`、`quota_policy_sha256`）及 D105 `budget_handoff`（`accounting_start: "2026-11-02T13:03:00.000Z"`）皆維持原設定未變動。

#### 3. `src/scheduler.py`、`src/flab/workdir.py` 與底層帳本／復原模組對照驗證
* **實際證據**：
  * **`src/scheduler.py` 與 `src/flab/workdir.py`**：`scheduler.py` 同樣使用 `ThreadPoolExecutor(max_workers=3)` 並行執行三條模型產線，且 Tier 1 執行時持有各自的 `forecast.{job.player}.lock`。`workdir.py` 中每次 `make_attempt_dir` 皆以 `secrets.token_hex(16)` 建立獨立隨機子目錄並以該唯一路徑作為 `_CREATED_ATTEMPT_BASES` 的鍵值，在 `remove_attempt_dir` 中移除並提供 `default_base_dir().resolve()` 作為預設回退；此外 `advise.py` 執行期間同時持有 `scheduler.lock` 與三把 `forecast.<player>.lock`，不會與排程器並發競爭。
  * **`src/flab/ledger.py` 與 `src/flab/jsonl.py`**：`transaction(root)` 結合程序內 `threading.RLock()`（依 `root.resolve()` 分組並支援執行緒局部重入 `_local.active`）與跨程序 `fcntl.flock(..., LOCK_EX)`（鎖定 `root / "ledger.lock"`），確保 `advise.py` 三條執行緒寫入共用 `data/` 帳本與各自私有 `data/advisory/<batch>/<player>/` 帳本時具備完整互斥保護。`read_complete` 強制檢查結尾換行符號 `\n` 並以 `strict=True` 解析所有紀錄，遇未完成行或損毀紀錄立即拋出例外（fail-closed）。
  * **`src/flab/orphan.py` 與 `src/flab/fail_soft.py`**：確認 `expired` 要求超過 `hard_deadline_utc` 加上 `CLEANUP_GRACE_S`（120 秒），且 `no_process` 要求 `/bin/ps` 與 `/usr/sbin/lsof` 雙重掃描均成功回傳且無目標路徑殘留程序後，才允許 `F.recover_interrupted` 與 `advise.py --recover` 進行離線結案對帳；`fail_soft.py` 中 `TIER1_QUOTA_RESERVE_CAP = 0.05`、`MISMATCH_BACKOFF_S = 1800`、`COOLDOWN_S = 21600`、`BREACH_COUNT = 2` 等常數與 `budget.py`、`forecast.py` 完全一致。

#### 4. 作者離線測試執行結果紀錄 (`AUTHOR TEST EXECUTION RESULT`)
* **實際證據**：
  * 封包提供之作者測試執行結果顯示 `241 passed in 72.19s (0:01:12)`，通過率 100%，無失敗案例。

#### 5. `PLAN.md` 差異與新任務文件 (`D132 — 兩題 Cup 人類賽參考`) 審查
* **實際證據**：
  * `PLAN.md` 版本號由 `v1.84` 更新為 `v1.85`（整合者 GPT，不涉及要求 Claude 編輯實作階段文件），歷史決策條目（如 D127、D130、D131）保持原樣未遭竄改。
  * 狀態欄與新增之 `D132` 條目明確記載官方 API 核對結果（10 道已正式送出之 MiniBench 題目目前皆為 closed 未 resolved，最早預定結算時間為台北時間 11/13 20:00、最晚 11/14 12:59；並澄清舊 11/06 為 M4 開發驗收安排，且 FutureEval 主賽 M6 尚未啟用、無固定開跑日）。
  * `PLAN.md` §8、§16 與 `D132` 任務文件精確反映程式碼實作規範：僅限 post `45330`／`46106`（question `45519`／`46343`）兩題、每題三線各一次 Tier 1 初始呼叫（全域上限 6 次）、跨批次禁止重跑、無自動修復／Tier 2／自動提交、六把非阻塞鎖與 MiniBench 優先讓路、`budget_v8` 保留 22 次容量與 Gemini Argon 配額及 `budget_v7` 相容性、人類賽結果隔離於 `data/advisory/<batch>/<player>/`、共用安全帳僅記錄 metadata（`forecast_pipeline_v9`），以及中斷時僅能透過 `advise.py --recover` 在過期且無程序證據後對帳（未知狀態保守計費並阻斷）。

---

### 三、 非阻斷性建議 (Non-Blocking Proposals)

維持 R2 審查所列之兩項純可讀性／未來擴充性觀察（均非 Blocker，不影響本次部署與安全性，無須修改現有候選程式碼）：
1. **`advisory_usage.jsonl` 中 `"batch"` 欄位命名語意（`src/advise.py` 第 91 行）**：目前 `"batch": root.name` 記錄子目錄名稱（即 player 名稱），而完整路徑已精確記錄於 `"batch_path": str(root.resolve())` 且與 `recover_accounting` 完全吻合；未來若欲顯示外層批次名稱可改取 `root.parent.name`。
2. **`F.line_stopped` 中 `advisory_quota_backoff` 與人工 `line_resume` 之互動（`src/forecast.py` 第 296–300 行）**：目前固定 30 分鐘配額退避直接比較 `now < parse_iso(r["quota_retry_at_utc"])`（與機器人賽既有邏輯一致且更為保守）；未來若需支援手動提前解除配額退避，可再評估納入 `resumed_at` 比對。