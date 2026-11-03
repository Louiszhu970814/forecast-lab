# D125 最終版本程式碼複審（Forecast Lab 設計／程式審查）

## 審查範圍與限制

- 只讀本次提供的 packet：最終全文、相對 baseline 的 diff、測試全文、forecast／gpt adapter／base／maintenance 相關片段、PLAN 摘錄、測試 log 文字，以及 D125 文件草稿。
- 我沒有執行任何測試、命令或模型，也沒有查看預測內容。下文所說的「測試結果」都只是 packet 裡貼上的 log 文字。我無法確認這些 log 對應的就是所列 SHA。
- 審查對象：
  - `src/autoload.py`：c0a1a2e9…8e97
  - `tests/test_autoload_model.py`：876a9b28…4b9b
- 我比對過：diff 與 `src/autoload.py` 全文一致；diff 只改 `autoload.py`，另新增一個測試檔；`adapters/*`、`flab/runner.py`、`flab/netguard.py` 在 packet 中沒有改動，符合 D121／D124「不改驗收指紋檔」的限制。

---

## 結論

**程式碼審查：PASS（有條件）。沒有 blocking 的程式缺陷。**

**部署／套用：目前仍 BLOCKED（流程未完成，不是程式缺陷）。** 套用前須依序滿足：
1. 完整回歸 r2 全部通過，而且受測的 SHA 和上列兩個雜湊相同。目前 packet 只有 r2 targeted（179 passed），r2 完整回歸仍在跑。r1 完整 2826 passed 是 N1～N8 修改前的版本，不能代替 r2。
2. 雙審：另一位審查者的獨立結論，加上主持人的完整測試。
3. PLAN §7.7／§15（D125 列）／§16 的最終文字與程式同一個 commit，並經審查。packet 只有草稿段落。
4. 在六鎖互斥下套用，套用前後各跑六項 preflight。

本審查只是程式碼審查意見，不代表核准部署，也不代表核准任何新量測、重設名額、新的 canary、基準核准或來源重新分類。這些都不在本次 PASS 的範圍內。

---

## 一、實際證據（packet 中能直接驗證的部分）

### 1. 修正和既有政策方向一致，不是新的模型政策
- PLAN §7.7 寫明「codex：目前的事件中沒有模型 ID，記為 `unverified`」。gpt adapter 的 `parse()` 固定產生 `reported_model_id=None`、`model_check='unverified'`。
- 正式預測路徑 `forecast.py` 只在 `model_check == "mismatch"` 時 LineStop，`unverified` 本來就能作答。
- autoload_v2 寫死 `result.model_check == "verified"`，等於要求 Codex 交出一個它在結構上不可能給的證據，和 §7.7 衝突。packet 引用的 D124 原文也沒有「三線一律 verified」的授權要求。
- v3 的做法反而比正式預測更嚴：只有 gpt + tier1 + `unverified` + `reported_model_id is None` 這個已知缺 ID 的情況，而且還要綁定 argv、catalog 和現行驗收，才會接受。

### 2. `model_evidence()` 的關卡
- 任何一線是 `verified` → 接受，和 v2 行為一樣。
- 非 gpt、非 `unverified`、或 `reported_model_id` 不是 None → 拒絕。Claude／Gemini 仍然必須 verified。
- profile 不是 tier1，或 `result.argv != gpt.build_argv(req)` → `codex_argv_differs`。
- catalog 的三項檢查：
  - 磁碟上的 restricted catalog 再套一次限制必須沒有變化；
  - 它的 entry sha 必須等於結果裡的 `catalog_entry_sha256`；
  - 也必須等於最新驗收紀錄的 `catalog_entry_sha256`。
- 驗收檢查重用 `forecast.check_acceptance`：CLI 版本、judge 版本、runtime fingerprint、evidence sha、正式 model+effort 的 production 紀錄都要成立。同時要求 `incomplete_acceptance` 為 False；這個函式本身會排除 `source=autoload` 的列，所以本次 run 自己的 call_started 不會誤判。
- 例外處理是 fail-closed：預期的例外改回 `codex_evidence_unavailable`；預期外的例外會落到 `A.run` 外層的 except，還原照常執行，結果是 not passed。
- 回傳的 evidence 保留 `reported_model_id=None` 和 `model_check='unverified'`，只另外寫 `requested_model`。它沒有把 Codex 標成「模型 ID 已驗證」，符合草稿「不稱實際模型 ID 已驗證」的說法。

### 3. 錯誤分類與 STOP
- 新的分類是：`model_check == "mismatch"` 一律標 `model_mismatch`，接著 break，控制器丟出 MaintenanceError 並建立 STOP。
- v2 的漏洞是：mismatch 同時帶有其他 error_type（例如 invalid_output）時，不會 break，也不會 STOP。v3 已補上，並有三線參數化測試和控制器測試。

### 4. 相對證據路徑
- `run()` 傳入 `project_root=data_dir.parent`。在 Session 中 `data_dir = project/'data'`，所以基準就是 project 根目錄。
- `evidence_path` 是絕對路徑時，Path 串接會直接取用該絕對路徑，行為正確。

### 5. runtime_fingerprint
- 刻意對「正在執行的 src」計算指紋，`project_root` 只用來讀 proof。這樣不能拿另一份沒在執行的程式碼冒充本機 runtime，設計正確。

### 6. 歷史與名額
- D124 run 已有 `acceptance_call_started`，控制器的 `calls or len(prior_runs) >= 2` 會永久回傳 consumed。所以這次修正本身不能讓任何新量測執行。這和「不重跑、不重設名額、不另跑 canary」一致。

### 7. 測試數量互相吻合
- 新增案例逐一點數：1+6+6+10+2+1+6+5+1+3+6+1+1 = **49**，和陳述一致。
- 2789（D124 基準）+ 37 = 2826，和 r1 log 一致。
- **交叉核對建議**：如果這批沒有其他測試異動，r2 完整回歸應該是 **2838 passed**（2789 + 49）。數字不同時應先查原因再套用。

---

## 二、Blocking

**程式碼：無。**

**部署流程（不是程式缺陷，但未滿足前不得套用）：**
- B-1：r2 完整回歸還沒有結果。必須全部通過，且受測 SHA 和上列兩個雜湊相同。
- B-2：PLAN §7.7／§15 D125 列／§16 v1.78 的最終文字不在 packet 內，只看到草稿，同一 commit 的文件需要審查。
- B-3：另一位審查者的獨立結論、主持人的完整測試，以及六鎖下前後六項 preflight 都還沒完成。

---

## 三、Non-blocking（建議修正或補強）

### N-A（建議合併前修正，成本很低）：mismatch 會蓋掉 tool_scope_violation 的標籤
- 說明寫的是「mismatch 優先於任何一般 error」，但程式也讓 mismatch 優先於 `tool_scope_violation`，兩者不一致。
- 例如 Gemini credits>0 被設為 `tool_scope_violation`，同時 model_check 是 mismatch 時，帳冊只會記 `model_mismatch`。
- STOP 和 MaintenanceError 照樣觸發，安全行為沒有變。但 D121／D54 把工具越權和 credits 非 0 列為停線並通知 Louis 的嚴重事件，主要帳冊卻失去這個標籤，事後判讀會被誤導。
- 建議：讓 `tool_scope_violation` 排第一，其次 `model_mismatch`；或保留兩者，例如加 `secondary_error` 欄位。另補一個 mismatch + tool_scope_violation 的測試。

### N-B：其他兩線 `unverified` 的測試沒有單獨測到 player 關卡
- `test_exception_does_not_apply_to_other_players` 用的 `_result()` 預設 `reported_model_id=req.model`。所以 claude／gemini 的 `unverified` 案例其實是被 `reported_model_id is not None` 擋下，不是被 player 判斷擋下。
- 拿掉 `req.player != "gpt"` 這個條件，該測試仍會通過。建議在 `unverified` 案例傳 `reported_model_id=None`，並 assert basis 為 `model_check_rejected`。

### N-C：反向測試大多只 assert `not accepted`
- catalog 和 acceptance 兩組參數化測試沒有 assert 具體的 basis（`codex_catalog_differs`／`codex_acceptance_differs`／`codex_evidence_unavailable`）。
- 如果之後出現無關的例外，這些測試會「因錯的理由而通過」。建議逐案 assert 預期的 basis。

### N-D：`missing_acceptance` 的故障注入刪掉了整份帳冊
- 這個案例會刪除整份 `acceptance.jsonl`，連本次 run 的 `autoload_run_started` 和 call_started 都一起刪掉，不像實際情境。
- 建議改成只移除或改寫 `acceptance_result`。

### N-E（既有問題，不在本批範圍）：forbidden tool 的標籤
- 結果 `completed`、error_type 為 None，但 `tool_call_counts` 含禁止工具時，標籤是 `incomplete run/version`，不會觸發 STOP。正式路徑靠 `A.execute` 裡的 `judge.extract_codex` 改設 `tool_scope_violation`，所以實務上有覆蓋。
- 但 `test_whole_measurement[tool]` 繞過了 `A.execute`，只驗證 not passed。建議日後另案讓 run 內的判斷直接標 `tool_scope_violation`，本批不要求。

### N-F：版本字串和錯誤字串的下游使用者需要確認
- VERSION 改成 `autoload_v3`；Claude／Gemini 未 verified 時的錯誤標籤從 `incomplete run/version` 改成 `model_check_rejected`。
- packet 沒有顯示其他模組（例如 auto_reaccept_v1 observe、baseline 或報表）是否比對 `autoload_v2` 或這些錯誤字串。
- 建議主持人以全 repo 搜尋 `autoload_v2`、`incomplete run/version`、`autoload_result` 的讀取端，把結果附在套用紀錄。我無法從 packet 驗證。

### N-G：argv 比對只是自我一致性檢查
- argv 本身是 adapter 自己回報的，比對是同義反覆，只能擋住非 gpt adapter 或參數漂移的結果。
- 真正的約束來自 runtime fingerprint、catalog sha 和現行驗收。docstring 和草稿已如實寫成「自我一致、非伺服器身分回報」，可以接受。

### N-H：文件草稿建議補一句
- 控制器對 D124 已永久判為 consumed，所以這次修正套用後，**R2-C 的載入量測仍然沒有 PASS 證據**，「證明不載入者改為資訊性」也還不能推進。
- 之後是否再做一次新量測（以及隨之而來的控制器授權或名額調整），需要 Louis 另行決定。主持人和審查者都不能代為決定。
- 建議在 D125 列明寫這一點，避免讀者以為套用 D125 就等於量測過關。另外建議在 §7.7 的 codex 那一行加上 D125 的交叉參照，並在 §16 記錄 r2 完整回歸的數字和雜湊。

### N-I：`model_evidence` 的 `project_root` 預設值
- 預設值在 import 時就綁定為 `autoload.PROJECT_ROOT`。正式路徑都會明確傳入，不影響結果；只提醒直接呼叫時要注意。

---

## 四、本次不採納的範圍外重構（同意維持）
- latest acceptance 和 production proof 的 catalog 綁定方式與 forecast preflight 一致（比對 `latest.catalog_entry_sha256`），本批不改，可以接受。
- `A.run` 低階呼叫不自帶完整控制器，正式 CLI 入口仍強制走 Session，可以接受。
- 舊量測只做唯讀診斷：已知它的 argv 是從 started metadata 重建，不能當作原程序的證明；不能用現在最新的帳冊倒推舊時段 PASS；歷史帳冊不改寫。同意。

---

## 五、給主持人的套用前檢查清單（我不代為執行，也不代為授權）
1. r2 完整回歸全部通過，預期 2838 passed；受測檔案的 SHA 等於本審查的兩個雜湊。
2. 處理 N-A（建議）。若改程式，雜湊會改變，需要重跑完整回歸並重新送審。
3. 附上 N-F 的搜尋結果。
4. PLAN §7.7／§15／§16 最終文字和程式同一個 commit，並納入 N-H 的說明。
5. 取得另一位審查者的獨立結論；在六鎖互斥下套用，前後各跑六項 preflight，確認 D118 三份固定設定和驗收指紋檔都沒變。
6. 套用不觸發任何模型呼叫或 canary；D124 的 measurement_failed 維持原狀。