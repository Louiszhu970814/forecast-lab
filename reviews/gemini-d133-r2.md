> 官方 CLI 審查原文；來源封包 `data/operations/d133-closed-practice-20261104/review-packet-r2.txt`。程式雜湊以機器產生的 `reviewed-files-r2.json` 為準，非模型轉寫字串。

# D133 Round 2 完整程式碼與設計審查報告（Closed FutureEval Practice）

- **審查結論**：**`PASS`**
- **審查範圍**：`src/practice.py`、`src/flab/practice_contract.py`、`src/flab/external_usage.py`、`src/budget.py`、`src/forecast.py`、`tests/test_practice.py`、`docs/tasks/D133-CLOSED-PRACTICE.md`（含 PLAN v1.88 實作補記）及 R1 雙審（Claude R1、Gemini R1）所有阻擋項與決策項之修正驗證。
- **審查守則與邊界聲明**：
  - 本報告完全依據提供之 Source Packet 進行獨立靜態程式碼與規格審查；**審查者未執行任何測試、指令或外部查詢**。
  - 明確區分三種狀態：
    1. **候選程式碼與離線驗證**：已完成 R1 修正並通過本輪程式碼審查（`PASS`），但**尚未部署至根目錄正式執行期**（須待雙審皆 PASS 後，於六把鎖保護下執行套用程序並留存部署證據）。
    2. **實際練習執行**：尚未開始（`0` 次模型呼叫）。依目前已核實之 API 證據（主賽 `33121` 共 108 題閉題候選之 `description`／`resolution_criteria` 均被遮罩），首次實際執行 `prepare` 預期走 `inventory_only`（選題數 `0`、不消耗唯一非空批次名額）。
    3. **準確度結果**：無任何新練習準確度結果；既有 10 題正式 MiniBench 閉題僅完成 D126 三閘門後之工程與品質健檢，尚未結算。
  - **未授權亦未啟用 FutureEval 主賽（33121）**；受保護之執行期元件（`adapters/*`、`flab/runner.py`、`flab/netguard.py`、`config/*`、`prompts/*`）未變更。

---

## 一、 實際證據盤點（Actual Evidence from Source Packet）

1. **候選檔案 SHA256 雜湊紀錄（書面核對）**：
   - `src/practice.py`: `673d74c6dad260f1f32d13f81f0db38c7876dabc34ee6f1b751a870f86d2c8cd`
   - `src/flab/practice_contract.py`: `aece3da3f3fcb96a5de46e2690ed55a79b2323d271ad09b2323d271ad09b939657c6d1da4339f`
   - `src/flab/external_usage.py`: `9c104c2a48034b36ebf94737fdb9280d0e1f842a9217d53a674186054ebb6836`
   - `src/budget.py`: `10af6eafe08aefaa609a19666fa159af15d4bdb56ded5639668d53c66a2b7c7e`
   - `src/forecast.py`: `f2b17540e655e564b4b3f2011a8ae4107262d5b122eaaa6eb607497bc5761617`
   - `tests/test_practice.py`: `44d8f590e4e9c54f222a8c62597b65d013fee8d9ef3b6a65ca88a1d3fd8451e1`
2. **測試基線對照證據（書面提供，審查者未宣稱親自執行）**：
   - 基線（Baseline）：`3134 passed / 100 failed`
   - 候選版本（Candidate）：`3205 passed / 100 failed`
   - 失敗節點比對：`same_failure_nodes: true`、`baseline_only: []`、`candidate_only: []`，新增 `71` 個練習專項測試通過（涵蓋三線並行 `P.run`、無／空／損毀練習帳冊之正式路徑回歸、預約後未准入釋放、未知嘗試 `preflight` 攔截、慢速 `preflight` 持鎖上限、閉題重開、7 天 2 次暴露門檻、凍結 `aggregate` ID 計分、六把鎖佔用保護等）。未宣稱全套測試 0 failure PASS。
3. **三執行緒併發競態修正證據（靜態程式碼核對）**：
   - `lifecycle_evidence()`（`src/practice.py`）現已外包 `with transaction(shared):`，與 `run_line.before()` 中 `b.reserve()`＋`register_admission()` 的 `with transaction(shared):` 形成原子互斥。這確保三線並行時，兄弟執行緒在呼叫 `guard()` -> `lifecycle_evidence()` 時，不會讀到「已寫入 `budget_events.jsonl` 但尚未寫入 `practice_usage.jsonl`（`own_ids`）」的中間狀態，消除了將同批次兄弟線預約誤判為正式歷史生命週期的競態條件。
4. **安全與隔離不變量（靜態程式碼核對）**：
   - 全程僅使用 `MetaculusClient` 之 GET 方法（`get`、`list_posts`、`get_post`），無任何預測或留言 POST 路徑。
   - `practice_contract.execution_cutoff()` 嚴格限制 `ctx.tier == 1`、`kind == "initial"`、`prev is None`、`len(registered) == 1`，強制獨立帳冊路徑 `data/practice/<batch>/<qid>/<player>/`，且不修改正式 `eligible()` 對非 `open` 題目的拒絕邏輯。

---

## 二、 R1 阻擋項（Blockers）與決策項逐項複核

### 1. Gemini R1 阻擋項複核

- **Gemini Blocker 1（`score()` 重建幽靈 Aggregate、持六把正式鎖呼叫 `fresh_index`、因題文補充或活躍賽題誤殺計分）— 已完整修正**：
  - `score()` 現改用批次私有鎖 `with F.Lock(root / "score.lock"):`，不再佔用六把正式執行鎖，亦不再呼叫 `fresh_index()`，因此活躍 MiniBench 開題期間不會阻擋本地事後計分。
  - 改從 `root / "aggregates.jsonl"` 讀取 `run()` 落盤凍結之 `agg`，並檢查 `set(agg["input_attempt_ids"]) <= {x["attempt_id"] for x in inputs}`；計分時同時涵蓋各線通過 `aggregate.select_inputs` 的有效單線輸出與凍結之 `aggregate`（保留原始 `attempt_id` 與 `record_id`）。
  - 結算驗證改為核對 `question_id`、`post_id`、`question_type`、`scheduled_close_time`、`options`、數值題 `N.scaling_changed(snap, fresh)` 及 `A._gate1` 閉題滿 15 分鐘；結算時若官方微調題文，僅記錄 `text_changed_since_prediction=fresh.get("content_hash") != snap["content_hash"]` 而不崩潰；單題錯誤由 `except (ValueError, MetaculusError)` 捕獲並逐題記錄 `resolution_status="unavailable", error=str(exc)`。
- **Gemini Blocker 2（`report()` 將 `local_scores` 寫死為 `None`、`missing_outputs` 與未知成本／稽核統計失真）— 已完整修正**：
  - `report()` 現完整讀取 `scores.jsonl`（取每題最新計分紀錄），在共享摘要輸出按 `group/question_type` 分組的 `local_score_groups`（含題數、計分輸出數、結算狀態分布），並將完整診斷分數明細寫入私有報告 `reports/practice/<batch>_<id>.md`，共享回傳值不含任何預測機率或題文。
  - 明確分列 `valid`、`adoptable`、`failed_outputs`、`missing_outputs`（`selected * 3 - len(rows)`）、`missing_usable_outputs`（`selected * 3 - len(adopted)`）、`audit_flags`、`audits`、`durations_s`、`cost_unknown` 與 `usage_observed`。
- **Gemini Blocker 3 / Claude D2 & D3（`prepare()` 同截止時間吞沒全部候選題、0 題消耗唯一批次名額、缺少 `shared_ready()`）— 已完整修正**：
  - `prepare()` 進入 `practice_locks` 後立即呼叫 `shared_ready(shared)`。
  - 排序改用 `parse_iso(r["snapshot"]["scheduled_close_time"])`；僅當 `len(distinct_times) > 1` 時才將最新截止時間群組保留為 `holdout`；若所有可用題屬同一截止時間群組，則 `holdout = []`，最多選 3 題作為純工程診斷批次，並於 manifest 標記 `"validation_status": "independent_future_group_required"`。
  - 當 `selected` 為空時，manifest 標為 `"type": "practice_inventory"`，且**不寫入** `shared / "practice_batches.jsonl"`，不消耗唯一的非空練習批次名額。
- **Gemini Blocker 4 / Claude B4（`preflight()` 後未強制刷新 `guard()`、持鎖時間上限未涵蓋 `preflight()`、動態前瞻視窗）— 已完整修正**：
  - `practice_locks` 自取得六把鎖起記錄 `started = utc_now()` 並寫入 `lock_events.jsonl`（釋放時記錄 `elapsed_s`）。
  - `cutoff` 以 `lock_started + timedelta(seconds=LOCK_BUDGET_S)`（720 秒，含 `preflight`）與 `manifest["expires_at"]` 取最小值；`guard()` 於最後 60 秒（`utc_now() >= cutoff - timedelta(seconds=60)`）拒絕新准入。
  - `fresh_index()` 採用動態前瞻視窗 `horizon_s=(cutoff - utc_now()).total_seconds() + 120`（且 `max(1200, horizon_s)` 保底 20 分鐘）。
  - 在三線 `F.preflight()` 與 `shared_ready(shared)` 完成後、寫入不可逆的 `practice_question_started` 之前，明確重置 `last[0] = None` 並強制執行新鮮的 `guard()`。
  - 若准入後、CLI 啟動前（`start_written == False`）因重查失敗中止，雖消耗一次練習准入名額（不重跑），但 `b.outcome(aid, False)` 與 `practice_finished(started=False)` 會釋放真實週額度計費，行為保守且已明確寫入文件。
- **Gemini Blocker 5（`run_line()` 對 `F.LineStop` 重複寫入 `line_status.jsonl`、遺漏原因／冷卻時間、CLI `--question` 檢查）— 已完整修正**：
  - `except F.LineStop as exc:` 現檢查 `not any(aid in done for aid in own)`，若 `forecasts.jsonl` 已有紀錄則交由 `finally` 的 `propagate_safety()` 統一寫入含 `attempt_id` 的 `line_stop`，避免重複寫入；回傳 dict 保留 `reason` 與 `quota_retry_at_utc`。
  - `main()` 已加入 `if args.action == "run" and args.question is None: parser.error("run requires --question")`。

### 2. Claude R1 其餘阻擋項與決策項複核

- **Claude B2（共用執行期版本升級與相容性回歸）— 已完整修正**：
  - `src/budget.py` 升版為 `budget_v9`，並定義 `TOKEN_POLICY_VERSIONS = frozenset({"budget_v7", "budget_v8", VERSION})`，確保既有 `budget_v7`／`budget_v8` 的 tier-2 token 評估不因升版而失效。
  - `frozen_versions(cfg)` 事前凍結執行期程式碼與所有 `prompts/*` 的 SHA256 及模型設定，`run()` 執行前強制比對。
- **Claude B3（未知練習嘗試阻擋正式線之顯式告警與恢復）— 已完整修正**：
  - `src/forecast.py` 的 `preflight()` 在執行昂貴的 CLI 檢查前，先在 `transaction(acceptance_log.parent)` 下讀取 `practice_usage`，若存在未完結練習嘗試則立即回報 `practice_attempt_unknown: run practice.py recover for the registered batch; no rerun`（若帳冊損毀則回報 `practice_accounting_invalid`）。
- **Claude B5（正式生命週期證據與遠端 `my_forecasts` 檢查）— 已完整修正**：
  - 新增 `remote_unforecasted(post)`，強制要求 API 回傳之 `question` 具備 `my_forecasts` 欄位且明確為空（`None` 或 `history == []`、`latest is None`、`score_data in (None, {})`），否則拒絕選入練習並導向 `post_close_audit`。
  - `LIFECYCLE` 擴充涵蓋 `submission_readbacks.jsonl`、`budget_events.jsonl`、`advisory_usage.jsonl` 及 `practice_usage.jsonl`，同時比對 `question_id` 與 `post_id`。
- **Claude B6（預約後准入失敗時釋放預算預約）— 已完整修正**：
  - `finish_accounting()` 在 `aid not in a` 時，若 `aid not in starts` 且 `reserved` 為真，呼叫 `b.outcome(aid, False)` 釋放預約；若 `aid in starts` 則拋錯並保留計費。
- **Claude D1（暴露停線策略一致性）— 已完整修正**：
  - `propagate_safety()` 將 `leak_confirmed` 寫入 `practice_guards.jsonl`（`own_audit_flags`），由 `F.line_stopped()` 統一套用既有的「7 天內 2 次 `leak_confirmed` 停線」規則，不再因單次練習題暴露而立即停用正式線；工具越權、`safety_stop_reason`（如 credits 非零）與 `recovery_no_rerun` 則維持立即寫入 `line_stop`。

---

## 三、 剩餘阻擋項（Remaining Blockers）

- **無（None）**。R1 提出之所有程式碼與規格阻擋項均已妥善修復並通過靜態核對。

---

## 四、 部署程序審查與非阻擋提醒（Post-Review Deployment Gate & Operational Notes）

1. **雙審後正式套用（Deployment Gate）程序確認**：
   - 目前根目錄執行期尚未變更（符合「雙審皆 PASS 後才套用」之規定）。
   - 在本輪雙審皆取得 `PASS` 後，主持人執行 `apply_reviewed.py`／正式套用程序時，必須：
     1. 同時持有六把正式鎖（`cycle`、`scheduler`、`submit`、`forecast.claude`、`forecast.gemini`、`forecast.gpt`）；
     2. 核對受保護檔案（`adapters/*`、`flab/runner.py`、`flab/netguard.py`、`config/*`、`prompts/*`）套用前後 SHA256 不變，以及本次 6 個目標檔案之 SHA256 與送審版本完全一致；
     3. 保存套用前後之三線 `preflight` 與備份紀錄，並確認 `D132` 與 `D134` 未提交變更完整保留。
2. **已知架構邊界與首次執行預期**：
   - 因受保護的 `flab/runner.py` 不作修改，已啟動之單次 CLI 呼叫沿用既有 timeout 與清理機制（無執行中硬即時取消鉤子），並由「鎖內 12 分鐘總上限＋最後 60 秒停止准入＋至少 20 分鐘動態正式賽前瞻視窗」控制風險；此邊界已在 `D133-CLOSED-PRACTICE.md` 明確記錄。
   - 在目前一般驗證帳號權限下（108 題閉題內容遮罩），套用後首次執行 `prepare` 預期將產出 `inventory_only`（`selected: 0`、`model_calls: 0`）並透過 `report` 留存盤點證據，全程不啟用主賽 `33121`。