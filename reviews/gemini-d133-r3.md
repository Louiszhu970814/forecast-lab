> 官方 CLI 審查原文；來源封包 `data/operations/d133-closed-practice-20261104/review-packet-r3.txt`。程式雜湊以機器產生的 `reviewed-files-r3.json` 為準，非模型轉寫字串。

# D133 第三輪最終差量與完整程式碼審查報告（Final Delta Review r3）

- **具名審查者**：Gemini（Forecast Lab 設計／程式碼審查者）
- **最終審查結論**：**`PASS`**
- **阻擋項（Blockers）**：**無（0 項）**
- **審查性質與邊界聲明**：
  1. 本報告為針對 D133 最終候選原始碼（r3 六個檔案全文及 r2→r3 精確 diff）之**靜態程式碼審查閘門（Code-Review Gate）**，**不代表實際部署已經發生**。
  2. 審查者僅依據所提供之 Source Packet 進行靜態檢視，**未執行任何測試、指令、檔案讀寫或外部網路查詢**，亦不宣稱全套測試為 0 failure PASS。
  3. 嚴格區分三項實際狀態：
     - **候選程式碼與離線測試狀態**：r3 六個候選檔案尚未複製或套用至根目錄正式執行期（須待 Claude 與 Gemini 最終審查皆為 `PASS`，且部署腳本核對實際檔案 bytes 與最終 manifest SHA256 完全一致後，始得於六把鎖保護下執行部署）。
     - **實際作業與 API 盤點狀態**：目前新練習模型呼叫為 **`0` 次**；官方 `33121` 閉題候選共 `108` 題之題文／結算條件欄位均受遮罩；既有 `10` 題正式 MiniBench 閉題輸出已完成 D126 三閘門控制與稽核。首次正式套用後執行 `prepare` 預期結果為 `inventory_only`（`selected: 0`、`model_calls: 0`，不消耗唯一非空練習批次名額）。
     - **預測準確度結果**：目前**無任何新練習準確度結果**。
  4. **安全與變更邊界**：FutureEval 主賽（`33121`）維持停用；無公開網頁爬取、無跨帳號借用、無任何 POST 操作、無新增額度上限或排程視窗；所有受保護執行期檔案（`adapters/*`、`flab/runner.py`、`flab/netguard.py`、`config/*`、`prompts/*`）維持不變，並完整保留 D132／D134 現有工作區變更。

---

## 一、 實際證據盤點（Actual Evidence from Source Packet）

### 1. 最終候選檔案 Canonical SHA256 核對紀錄（書面核對）
以下紀錄 Source Packet 中機器產生之最終 Canonical SHA256（同時修正本審查者於 R2 報告手誤多複製一段之 `practice_contract.py` SHA 轉錄字串；實際部署由腳本直接驗證檔案 bytes 與 manifest）：
- `src/practice.py`: `6ef4f14c0251098bdcb33225c974320123494c49b571c40d5b94aa17a8cc2468`
- `src/flab/practice_contract.py`: `aece3da3f3fcb96a5de46e2690ed55a79b2323d271ad09b939657c6d1da4339f`
- `src/flab/external_usage.py`: `1d16f8e6cc8afbf11475bfad9bbac676541cf920402f9ce23ae929946e4af1cf`
- `src/budget.py`: `10af6eafe08aefaa609a19666fa159af15d4bdb56ded5639668d53c66a2b7c7e`
- `src/forecast.py`: `1c7445da2a32366e36f802c7d4345fa59701d52944e7e9e2706a8672c166ffb1`
- `tests/test_practice.py`: `9a5b874f8e08c47b7455bf9846707cbf995b088b6833166d03a02b43bd15c8ae`

### 2. 測試比對數據（由提交者書面提供，審查者未親自執行）
- **Untouched Baseline**：`3134 passed / 100 failed`
- **Candidate (r3)**：`3211 passed / 100 failed`
- **失敗節點與摘要比對**：`same_failure_nodes: true`、`same_failure_summaries: true`（全部 100 個既有失敗節點不僅名稱一致，失敗摘要字串亦完全一致，無新增回歸失敗）。
- **專項與相關測試**：練習專項測試 `77 passed`（較 r2 新增 6 個測試）、相關測試群 `203 passed`、部署故障注入測試 `4 passed`。

### 3. r2 → r3 程式碼變更與迴歸風險靜態核對（Actual Code Inspection）

1. **跨帳冊安全事件時間序重播修正（`src/forecast.py` — `line_stopped()`）**：
   - **問題根因**：在 r2 中，`records = [*records, *({**r, "type": "forecast_attempt"} for r in advisory)]` 直接將外部守衛帳冊（`advisory_guards.jsonl` 與 `practice_guards.jsonl`）串接在正式 `forecasts.jsonl` 之後。而在後續計算 7 天內 2 次 `leak_confirmed` 的迴圈中，程式是以目前走訪紀錄的時間 `at` 檢查 `sum(t >= at - timedelta(days=7) for t in leaks) >= 2`。若正式帳冊有一筆近期的暴露（例如第 30 天），後面才串接一筆一個月前的舊練習暴露（例如第 1 天），走訪到第 1 天的舊紀錄時，`at` 變成第 1 天，導致 `leaks` 中第 30 天與第 1 天的兩筆紀錄都滿足 `t >= 第 1 天 - 7 天`，進而誤觸發 `repeated leak_confirmed` 停線！
   - **r3 修正驗證**：在進入重播迴圈前加入 `records = sorted(records, key=lambda r: parse_iso(r["finished_at_utc"]))`。
   - **迴歸檢查**：
     - `forecasts.jsonl` 內所有正式嘗試與 `recover_interrupted()` 寫入之紀錄皆必備合法 ISO 格式之 `finished_at_utc`；`external_usage.guards()` 載入之所有外部守衛紀錄亦在讀取時強制驗證 `parse_iso(r.get("finished_at_utc"))`，因此排序不會因缺欄位或格式錯誤拋出非預期例外。
     - 排序後 `at` 嚴格單調遞增（對所有已加入 `leaks` 的時間 `t` 恆有 `t <= at`），`t >= at - timedelta(days=LEAK_WINDOW_DAYS)` 能精確判定滑動 7 天視窗 `[at - 7d, at]` 內的暴露次數，且不會影響下方原本即已按時間排序的 `mismatches` 計算，亦不會影響上方 `completed` 集合之未決 CLI start 檢查。

2. **同題兄弟線預算拒絕／預約之生命週期豁免（`src/practice.py` — Claude R2 B1 修正）**：
   - **r3 修正驗證**：在 `run()` 閉包內建立單次執行專屬之記憶體集合 `budget_run_ids = set()`，並傳入 `guard()` 內的 `lifecycle_evidence(..., budget_run_ids)` 與 `run_line(..., budget_run_ids)`。`run_line()` 在初始化 `Budget` 後、呼叫任何 `guard()` 或 `b.reserve()` 之前，先於 `with transaction(shared):` 內執行 `budget_run_ids.add(b.run_id)`。`_lifecycle_evidence()` 則在檢查 `budget_events.jsonl` 時豁免 `r.get("attempt_id") in own_ids or r.get("run_id") in budget_run_ids`。
   - **迴歸檢查**：
     - 當同一題的三條平行線中有一條線因額度保留或快照問題寫入 `budget_denied`（不會進入 `practice_usage.jsonl` 的 `own_ids`），或剛寫入 `budget_reserved` 時，另外兩條兄弟線呼叫 `recheck()`／`before()` -> `guard()` 皆能透過 `budget_run_ids` 正確識別其屬於本次 `run()`，不會誤判為「正式生命週期（`question has formal lifecycle`）」。
     - `prepare()` 呼叫 `lifecycle_evidence()` 時不傳入 `budget_run_ids`（預設為空集合），維持 100% 嚴格檢查；且 `budget_run_ids` 為單次 `run(root, qid)` 區域變數，不持久化、不使用字首模糊比對，絕不會誤放過歷史批次或正式執行紀錄。

3. **遠端 `my_forecasts` 嚴格空結構檢查（`src/practice.py` — Claude R2 B2 修正）**：
   - **r3 修正驗證**：`remote_unforecasted(post)` 移除了 `if value is None: return`，強制要求 `my_forecasts` 必須為 `dict` 且同時滿足 `value["history"] == []`、`value["latest"] is None`、`value.get("score_data") in (None, {})`。
   - **迴歸檢查**：當 API 回傳 `my_forecasts: null`（可能代表權限不足或欄位不可用）時，現已嚴格 fail-closed 拒絕選入練習並導向 `post_close_audit`，完全符合規格契約。

4. **外部帳冊完成與守衛紀錄之嚴格版本檢查（`src/flab/external_usage.py` — 新增防護）**：
   - **r3 修正驗證**：除原先 `practice_admitted` 已檢查 `version == "closed_practice_v1"` 外，現於 `practice_finished` 與 `guards()` 讀取 `practice_guards.jsonl` 時均補上 `or row.get("version") != "closed_practice_v1"` 之強制驗證。未知版本一律拋出 `ValueError` fail-closed。

5. **逐選手統計與 Token 覆蓋率報告（`src/practice.py` — `report()` 強化）**：
   - **r3 修正驗證**：`report()` 新增 `by_player` 欄位，按 `claude`、`gemini`、`gpt` 分別統計 `outputs`、`valid`、`adoptable`、`missing_usable`、`admitted`、`pending`、`failure_types`、`cost_unknown`、`known_tokens` 及 `token_coverage: {"observed": ..., "outputs": ...}`。
   - **迴歸檢查**：
     - 所有新增欄位均為整數計數或錯誤類別計數，不含任何預測機率、分位數或題文，維持共享摘要 `metadata-only` 的安全邊界。
     - `known_tokens` 搭配 `token_coverage` 與 `cost_unknown` 呈現，可明確區分「已知 token 為 0」與「因 CLI 中斷／未回報導致 token 未知（`None`）」，防止未知成本被誤讀為零消耗。

6. **`prepare()` 持鎖時間上限檢查與 `score()` 執行中防呆（`src/practice.py` — Claude R2 N3 / N5 修正）**：
   - `prepare()` 於走訪 `list_posts` 迴圈期間及迴圈結束後（寫入 manifest 前）均檢查 `(utc_now() - lock_started).total_seconds() >= LOCK_BUDGET_S - 60`，逾時即拋出 `F.QuestionDone("inventory time budget exhausted; no batch registered")`，確保不會在持鎖逾時狀態下凍結批次。
   - `score()` 在計分前先檢查 `runs.jsonl`，若某題有 `practice_question_started` 但尚無 `practice_question_finished`，則記錄 `resolution_status = "run_in_progress"` 並跳過該題，避免讀到執行中途的不完整輸出；同時 `report()` 的 `score_groups` 亦能自然收納 `"run_in_progress"` 狀態而不發生鍵值衝突。

7. **部署程序鎖內記憶體雜湊驗證與回滾驗證（Claude R2 D-B1 修正）**：
   - 依提交說明與測試證據，部署程序已修正為在持有六把鎖內一次性讀取 stage bytes 並驗證 SHA256、寫入後重新驗證目標根目錄檔案 digest、以及核對真實審查檔案 SHA／結論，並通過 4 項部署故障注入測試。

---

## 二、 阻擋項（Blockers）

- **無（None）**。r2 階段所有剩餘阻擋項（Claude B1、B2、D-B1）與本次發現之跨帳冊時間序重播問題均已完整修復，且未引入任何新迴歸缺陷。

---

## 三、 非阻擋建議與審查後部署注意事項（Proposals & Post-Review Notes）

1. **雙審通過後之部署執行紀律（Post-Review Deployment Gate）**：
   - 本次 `PASS` 僅授權在 **Claude 最終審查亦為 `PASS`** 且 **六個目標檔案之實際 SHA256 與上方 Canonical SHA 完全吻合** 的前提下，執行鎖內部署腳本。
   - 部署前後請完整留存六把鎖（`cycle`、`scheduler`、`submit`、`forecast.claude`、`forecast.gemini`、`forecast.gpt`）之持鎖紀錄、受保護檔案（`adapters/*`、`flab/runner.py`、`flab/netguard.py`、`config/*`、`prompts/*`）前後雜湊不變證明、以及三線前後 `preflight` 紀錄。
2. **首次執行預期確認**：
   - 由於目前驗證帳號下 108 題閉題內容欄位仍受平台遮罩，部署完成後首次執行 `prepare` 應確認其回傳 `"status": "inventory_only"`、`"selected": 0`、`"model_calls": 0`，且未寫入 `data/practice_batches.jsonl`。