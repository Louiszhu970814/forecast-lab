# D121 第一批：停線機制放寬實作規格（交 GPT 實作）

> 決策：PLAN D121（Louis：「好的就照你說的C 其他放寬」）。
> 詳細理由、現況行號與審查意見：`docs/tasks/FAIL-SOFT-RELAX-PROPOSAL.md`（v2）。本檔只定第一批的範圍、限制與驗收。
> 分工：GPT 在開發沙箱實作與測試；Claude 審查並套用；Gemini 獨立審查（涉及預算、權限隔離）。

## 範圍

第一批：R1、R3、R4、R5、R6（程式部分）、R7、R8、R9、R10、R11。
**不在第一批**：R2（選項 C 的純文字自動重驗與載入實測）、R12；兩者在第一批套用後另開第二批。

## 硬性限制（違反任何一項即退回）

1. **不得修改** `src/adapters/*.py`、`src/flab/runner.py`、`src/flab/netguard.py`。這三類檔案在驗收指紋（`forecast.runtime_fingerprint`）內，改動會讓三線兩層 preflight 立即停擺。
2. **不得修改** `config/quota_policy.json`、`config/run_config_v2.json`、`config/cycle_campaign.json`。正式 campaign `d118_minibench_20261103T0400Z` 固定了 run_config 與 quota_policy 的 hash，改動會讓正式排程拒絕執行，而 `budget_handoff_v1` 不允許第二次接續。新參數（24 小時、2 題、6 小時、R5 保留上限等）先寫成有版本的程式常數，並在報告中列出；之後登記新窗口時再移入 `quota_policy_v6`。
3. 不改 prompt、模型、整合規則、選題、送出流程（`submit*.py`）、`live_window.py`、`budget_handoff.py` 的語意。
4. 以下維持現狀：`tool_scope_violation` 停線與重新驗收、`leak_confirmed` 排除且七天兩次停線、agy credits 非 0 停線、送出完整性、CLI 額度用完 30 分鐘退避、每週次數上限。
5. 舊帳冊不改寫；新規則只影響升版後的新決策。舊的停線與解除依原版本語意重播，不重判舊輸出、不重開封存題、不歸零已用次數。
6. 賽中只跑預先固定的自動規則。手動解除工具可以保留（並擴及 Claude），但規則本身不得依賴人工操作才能恢復。

## 各項要求（細節以提案 v2 為準）

- **R1**（`budget.py`）：單題超標只停該題第 2 層；CLI 已完成但缺 token 數 → `tier2_tokens_unknown`，只停該題；CLI 未完成（timeout、cli_error、interrupted）不算超標，保留單次重跑；整線熔斷改為滾動 24 小時內 2 個不同題目超標才觸發，6 小時自動到期，計數起點在上次熔斷到期或解除之後；超標判定固定當時的 token 基準與版本；`lift_tier2_stops` 擴及 Claude，授權字串改為 `PLAN D121`。
- **R3**（`forecast.py`、`budget.py`）：開始紀錄新增 `workdir`、`raw_dir`、`timeout_s`、硬性期限、開機識別；自動結案須同時滿足：已過保存期限加清理餘裕、恢復者已實際取得該線鎖、`ps`／`lsof` 無任何程序指向該次 workdir／raw_dir（不得終止其他程序）；部分輸出跑工具範圍稽核與 `audit_attempt()`，有違規照第四節處理、證據不完整就隔離不採用；補寫 `error_type = "interrupted"`（不可採用、只計一次）、補 `budget_outcome(status="started")`、清理 workdir；全部可重入；`interrupted` 加入 `RERUN_ERRORS`，`format_repair` 中斷可修復或重跑但不重設既有次數；`budget.reserve()` 與 `forecast_question()` 不再因已結案的中斷而阻擋。舊開始紀錄缺欄位時以既有路徑規則與設定推算。
- **R4**（`forecast.py`）：單次 mismatch 丟棄答案、讓出本輪（或退避 30 分鐘）後重跑一次；`inherited_model_mismatch` 只在 `format_repair` 繼承；`_may_rerun()` 允許 mismatch 的重跑；24 小時內 2 次實際 mismatch 暫停 6 小時自動恢復，計數起點在上次暫停到期後；`line_stopped()` 依新門檻判定。
- **R5**（`quota.py`、`forecast.py`、`budget.py`）：同時執行的 headless agy、IDE、互動式 agy 與 `ps` 失敗都不再否決准入；`/credits` 查詢成功且未過期就用實測值，未知不得填 0；同步修改 `quota.manual_admission()`；同時執行時額度對帳與模型核對標 `undetermined`，Ultra 下模型核對以 `--log-file` 為準；`/usage` 過期重讀一次，必須在 `budget.reserve()` 的交易鎖之外完成並重新核對桶、reset、credits 與期限；`/usage`、`/credits` 單次失敗先重試一次，預測後的 `quota_after` 失敗只記 `undetermined`、不拋 `LineStop`；第 2 層准入的 `protected × first_estimate` 保留量設上限，不得因他人使用虛增到違反 D97。
- **R6**（`flab/team_runner.py`）：TG 問答在專案外暫存目錄、工具關閉執行，不再持 `scheduler.lock` 與三條 forecast locks（R5 完成後，同時執行的 agy 不再影響 Gemini 線）。
- **R7**（`scheduler.py`）：區分暫時等待與永久跳過；額度或預算暫時不足造成的 `budget_skipped`／`not_admitted`，在原因到期或有新快照後重新評估；已封存的舊結果不重開。
- **R8**（`forecast.py`、`scheduler.py`）：週桶估計不足的等待紀錄可被新的有效快照以「重新評估」事件解除。
- **R9**（`forecast.py`）：只有開始、沒有結果的驗收，在逾時且確認沒有程序後補寫中斷的驗收結果，`incomplete_acceptance()` 不再永久阻擋。
- **R10**（`baseline.py`、`claude_baseline_candidate.py`）：基準判定與候選核准改為逐線範圍；他線的 blocker 或 credits 未知不得連帶阻擋本線；真正共用的來源才影響多線。
- **R11**（`baseline.py`）：非憑證的私人目錄（自動產生的 logs／history／sessions）改為資訊性、不讀內容；憑證檔仍是硬 blocker。

## 版本

`budget_v6` → `budget_v7`、`forecast_pipeline_v6` → `forecast_pipeline_v7`、`scheduler_v7` → `scheduler_v8`；`quota.py`、`baseline.py` 有語意變動處各自升版。不升 `PROMPT_VERSION`。

## 測試與交付

- 新增測試涵蓋：跨版本重播、到期後不反覆熔斷、缺 token 數的兩種情況、崩潰重入、冷卻跨程序、孤兒程序判定（含程序仍在、鎖空閒但 CLI 存活）、format_repair 中斷、mismatch 讓出本輪與重跑可採用、同時執行時 credits 仍被讀取、交易鎖外重讀、R7～R11 各自的恢復條件，以及硬性限制 1、2 的防呆測試（指紋檔與設定檔 hash 不變）。
- 完整隔離回歸須全部通過；沙箱目錄限制造成的既有失敗要逐一列出並說明。
- 交付 patch 與報告：改動檔案、各項的實作摘要、新常數與其值、版本變更、測試結果，以及無法依規格完成或需要 Louis 判斷的地方。
- 不 commit、不 push、不部署、不重載 launchd、不讀正式 `data/`。
