# 停線機制放寬提案 v2（已納入 GPT、Gemini 審查，待 Louis 決定）

> 狀態：Claude 修訂；尚未實作、尚未寫入 PLAN。D 編號由整合者指定。
> 來源：Louis 於 2026-11-03 指示「我需要都放寬」，理由兩點：
> 1. 一有問題就停線、一停就到週窗口結束（最長 7 天），成本太高。
> 2. 不可為了本專案額度估算的準確度，限制 Louis 在其他專案使用 Claude、Gemini、GPT。
> 審查：GPT（gpt-6-astra high）`reviews/gpt-fail-soft-review.md`；Gemini（gemini-4-argon-high）`reviews/gemini-fail-soft-review.md`。兩者皆「有條件同意」，工具關閉、以程式全文審查。
> v1 → v2 變更見文末。

## 一、設計原則

- **預設軟失敗**：異常時跳過本次嘗試或本題，下一輪自動重試；需要暫停時一律設自動到期時間。
- **只有真正的安全或規則違規，才停線等人**（第四節）。
- **機器人是借住的客人**：Louis 在其他專案的使用視為可容忍的雜訊；不設計要求清場的量測；開發與審查不持 forecast locks。
- **賽中只跑預先固定的自動規則**（GPT 審查）：機器人賽禁止人為介入，所以手動解除、事後撤銷、人工恢復只能在維護時段使用，不能當作賽中的常設手段。所有放寬規則須在套用前固定並升版。
- **不改驗收指紋涵蓋的檔案**（Gemini 審查）：`runtime_fingerprint()` 涵蓋 `adapters/<player>.py`、`flab/runner.py`、`flab/netguard.py`；改動任何一個，三線兩層 preflight 會立即全部停擺。本提案所有修改限於 `budget.py`、`forecast.py`、`quota.py`、`baseline.py`、`scheduler.py` 與新增 helper。
- 修改都要升版、寫入 §16；舊帳冊不改寫，新規則只影響升版後的新決策（見第六節）。

## 二、放寬項目

### R1　第 2 層 token 熔斷

- **現況**（兩位審查者確認）：`budget.py` 中任一題第 2 層 token 合計超過「第 1 層中位數 × 3」，或任一筆第 2 層紀錄缺 token 數，就寫入 `budget_upgrade_stop` 到窗口結束（最長 7 天）；單題的 `question_tier2_token_capacity` 會被整線熔斷覆蓋。熔斷在下一次第 2 層 reserve 才偵測。只能手動解除，且限 GPT／Gemini（`LIFT_AUTHORIZATION = "PLAN D107 A3"` 寫死）。
- **修改**：
  1. 單題超標只停該題第 2 層。
  2. 缺 token 數分兩種：CLI 已完成卻缺 token 數 → 記 `tier2_tokens_unknown`，該題不再升級；CLI 未完成（timeout、cli_error、interrupted）→ 不算超標，保留 §7.5 的單次重跑。
  3. 整線熔斷：同一線滾動 24 小時內 **2 個不同題目**超標才觸發，**6 小時自動到期**。計數起點取「最近一次熔斷到期或解除」之後，避免到期後被同一批舊事件立刻再熔斷。
  4. 超標判定固定當時的 token 基準、事件時間與政策版本，不因中位數日後變動重判舊題。
  5. 手動解除擴及 Claude，授權字串改為新決策編號；只在維護時段使用。

### R2　全域設定內容變動（D119 之後）——**待 Louis 選方向**

- **現況**：受保護內容只要改變，該線停到 Louis 核准（§7.2／D21）。另外 settings 的權限鍵若不在 `content_keys` 內，會落入 GENERAL 而不是 CONTENT，v1 說「權限類照舊停線」並不準確。
- **兩位審查者的共同點**：外掛（可能含 hooks、MCP、執行檔）、settings 中會執行程式或注入環境的鍵（如 `statusLine`、`apiKeyHelper`、`env`、`enabledPlugins`、`extraKnownMarketplaces`）、上層目錄的 `.claude`／`.codex`／`.gemini`、憑證檔與私人目錄的 blocker，**都不能自動核准**。
- **分歧**：
  - GPT：反對自動核准任何文字內容（指示、skills、記憶）。隔離驗收只證明權限邊界，證明不了內容沒夾帶答案或指令，事後撤銷救不回已送出的答案。替代做法是讓正式線**不載入**這些全域內容，並以實測證明；之後 Louis 怎麼改其他專案環境都不影響。
  - Gemini：同意對純文字的指示、skills、記憶、代理定義自動重驗並事後通知，但要嚴格限流與限縮範圍。
- **現有證據**（2026-11-01 `autoload_result`）：Claude 的全域 skills `not_loaded`、`~/.claude/CLAUDE.md` `unknown`；GPT 的 `~/.codex/AGENTS.md` 與全域 skills **會載入**；Gemini 各來源都是 `unknown`。所以 GPT 的做法對 Codex 需要另外設法排除載入，可行性尚未驗證。
- **選項**：
  - **A**：照 GPT 的方向。先逐線實測哪些全域來源會被正式預測載入；證明不載入的來源改為資訊性，Louis 可自由修改；會載入的來源（目前已知 Codex 全域 AGENTS.md、skills）設法排除，排除前仍照現在停線。
  - **B**：照 Gemini 的方向。純文字類來源變動時自動重驗，通過就繼續並通知；外掛、執行類設定、上層目錄、blocker 照舊停線。
  - **C（Claude 建議）**：先做 B 的安全子集，同時進行 A 的實測；某來源一旦證明不載入，就改為資訊性，不再需要重驗。
- **B／C 的實作條件**：每線每 24 小時最多自動重驗 1 次，以 `acceptance.jsonl` 中**已啟動**的自動重驗計數（含失敗與中斷），避免每輪重跑耗光每週次數；同一候選雜湊合併重驗；核准比照 D120，只替換該線變動的來源並綁定新的 `isolation_record_id`，不使用泛用 `baseline.approve`；新事件必須被 preflight 實際讀取。

### R3　中斷的嘗試（有開始、沒結果）

- **現況**：`line_stopped()`、`budget.reserve()`（`unresolved_or_running_attempt`）、`forecast_question()`（`interrupted_attempt_unresolved`）三處都會卡住；而且即使手動 `forecast.py resume`，後兩處也不看 `resumed_at`，仍會永久卡住（兩位審查者確認）。開始紀錄沒有工作目錄、時限或程序身分。
- **修改**：
  1. 開始紀錄新增 `workdir`、`raw_dir`、`timeout_s`、硬性期限與開機識別（寫在 `forecast.py`，不改 runner／adapters）。舊紀錄缺欄位時，由既有路徑規則與設定推算。
  2. 自動結案條件：已過保存的期限加清理餘裕；恢復者**實際取得**該線鎖後再查（鎖空閒不能證明 CLI 已死，因為 flock 不會傳給子程序）；`ps`／`lsof` 確認沒有任何程序的工作目錄、開啟檔案或參數指向該次 `workdir`／`raw_dir`；不得終止 Louis 的程序。
  3. 部分輸出要跑工具範圍稽核與 `audit_attempt()`；發現工具違規或打開 metaculus.com，照第四節處理並禁止重跑。證據不完整時，結果隔離、不採用。
  4. 補寫 `error_type = "interrupted"` 的結果（不可採用、計入每週次數一次），同時補 `budget_outcome(status="started")` 並清理 workdir；結案、稽核、預算對帳都要可重入。
  5. `interrupted` 加入 `RERUN_ERRORS`；中斷發生在 `format_repair` 時，允許用已保存的原始輸出修復或給一次重跑，但不重設既有修復／重跑次數。

### R4　模型被替換（`model_mismatch`）

- **現況**：任一次 mismatch 寫入持久 line_stop。另外還有三處硬攔截（Gemini 審查）：`line_stopped()` 掃描帳冊時只要見過一筆 mismatch 就合成永久停線；`_may_rerun()` 明確排除 mismatch；重跑會繼承 `inherited_model_mismatch`，導致重跑答案永遠不能採用。
- **修改**：
  1. 單次 mismatch：丟棄答案，**讓出本輪**（或退避 30 分鐘）後才重跑一次，避免額度降級時 0 秒後重跑又撞上第二次。
  2. `inherited_model_mismatch` 只在 `format_repair` 繼承，`rerun` 不繼承；重跑仍須核對指定模型。
  3. 同一線 24 小時內 2 次**實際** mismatch（不數繼承標記）→ 暫停 6 小時自動恢復，計數起點取上次暫停到期之後。
  4. `line_stopped()` 改依新門檻判定，避免升版後重讀舊帳冊又鎖死。

### R5　Gemini 的同時執行偵測與額度查詢

- **現況**：其他 headless `agy -p`（或 `ps` 失敗）會讓 Gemini 准入被拒；preflight 會把 credits 記為 `None`，觸發 `agy.credits` blocker 與基準 stop，**整條線該輪 preflight_blocked**。2026-11-03 05:07 的 cycle 實際發生（原因是審查本提案的 agy）。
- **修改**：
  1. 同時執行不再否決准入，也不讓 credits 變 `None`；只要 `/credits` 查詢本身成功、未過期，就用實測值（未知仍不得填 0）。同時移除 `quota.manual_admission()` 的同一檢查，否則自動重驗也會被擋。
  2. 同時執行時，該次額度對帳與模型核對標為 `undetermined`，不當作驗證證據；Ultra 下模型核對以 `--log-file` 為準（D96 已確認單次 `/usage` 差值不可靠），IDE 或互動式 agy 開著也不影響。
  3. `/usage` 過期時重讀一次，但必須在進入 `budget.reserve()` 的帳冊交易鎖**之前**完成，不可在跨三線共用的鎖內呼叫外部 CLI；重讀後重新核對桶、reset、credits 與決策期限，查詢耗時要計入。
  4. `/usage`、`/credits` 單次查詢失敗先重試一次；預測完成後的 `quota_after` 失敗只記 `None`／`undetermined`，不中斷後續題目。
  5. 第 2 層准入的 `protected × first_estimate` 在估計值被他人使用拉到 0.05 時，會虛增保留 50%～75% 的週額度，與 D97 不限 Gemini 額度的決定矛盾；改為對總保留量設上限，或使用較貼近 Ultra 實際消耗的估計上限。

### R6　開發與量測不再要求清場

- **修改**：不設計需要 Louis 或其他專案停用的量測；一般開發與審查在獨立暫存目錄執行、不寫正式帳冊，不持任何 forecast lock。只有會寫 `acceptance.jsonl` 的正式驗收（含 R2 自動重驗）持**受驗那一條線**的鎖；只有套用正式程式或設定時，照互斥程序排空。
- **更正 v1**：D120 實際持鎖約 78 秒、沒有排程被拒，v1 說「持鎖會造成延誤」證據不足（GPT 審查）。

### R7～R12　審查找到、v1 未涵蓋的長期阻擋

| 項目 | 現況 | 修改 |
|---|---|---|
| R7 預算暫時不足變成永久跳過 | `budget_skipped`、`not_admitted` 屬終態，題目被封存，額度恢復後也不再處理（GPT） | 區分「暫時等待」與「永久跳過」；暫時性原因到期後重新評估，不重開已封存的舊結果 |
| R8 週桶估計不足一路等到重置 | 排程依舊的等待紀錄阻擋，沒有用新快照撤銷的路徑；他人用量會把估計拉高（GPT） | 追加「重新評估」事件，新的有效快照顯示仍有餘裕時解除等待 |
| R9 未完成的驗收紀錄永久鎖線 | `incomplete_acceptance()` 看到只有開始、沒有結果的驗收就永遠擋該線 preflight（Gemini、GPT） | 比照 R3，逾時且確認沒有程序後補寫中斷的驗收結果 |
| R10 共同來源與他線狀態連帶阻擋 | 上層目錄的共同來源可擋三線；Claude 候選 helper 要求三線都無 blocker，Gemini credits 未知會連帶擋 Claude（GPT） | 判定改為逐線範圍；只有真正共用的來源才影響多線 |
| R11 私人目錄 blocker | 受盤點目錄下自動產生的 logs／history／sessions 會成為硬 blocker，連重驗都放不了（Gemini） | 非憑證的私人目錄改為資訊性、不讀內容；憑證檔仍是硬 blocker |
| R12 Codex 型錄雜湊、CLI 版本、正式模型證據不符 | preflight 只拒絕，沒有自動重驗的執行器（GPT、Gemini） | 納入 R2 的有界自動重驗；CLI 版本另比照 D119 固定版本，避免自動更新 |

## 三、預期效果

| 情況 | 現在 | 放寬後 |
|---|---|---|
| 某題深度研究超標或逾時 | 該線第 2 層停到週末 | 只停那一題；多題超標才熔斷 6 小時 |
| 全域設定改變 | 停到 Louis 核准 | 依 R2 選項處理 |
| 斷電、程序被殺、驗收中斷 | 整條線停到有人處理 | 確認程序不在後自動結案，繼續 |
| agy 換了模型 | 整條線停到有人處理 | 丟棄答案，下一輪重跑；重複才暫停 6 小時 |
| Louis 在別處用 agy | Gemini 整輪 preflight_blocked | 不受影響 |
| 預算暫時不足 | 題目永久跳過 | 額度恢復後重新評估 |

## 四、維持現狀（不放寬）

兩位審查者皆同意：
- `tool_scope_violation` → 停線並重新驗收（硬性規則 5）。
- `leak_confirmed`：答案排除且不重跑；七天兩次停線。
- agy credits 不為 0 → 停線（D54 付費控制）。
- 送出完整性：未知結果不重送、送出前檢查、正式窗口邊界與 hash、`STOP_SUBMIT`。
- CLI 額度用完固定退避 30 分鐘；週桶／五小時桶真的不足時等待重置。
- 每週次數上限：Claude／GPT 130、Gemini 200（防失控）。
- 不下注、Metaculus token 不給模型、無人值守不開其他工具。

## 五、不在本提案範圍

- 正式參賽窗口到期不續期（D117／D118）：屬參賽授權，12 月無人值守前另行決定。

## 六、帳冊相容性與測試

- 依事件與政策版本重播：舊停線、舊解除照舊；新增有生效時間、明確目標的遷移事件；不重判舊輸出、不重開封存題、不歸零已用次數。
- 測試涵蓋：跨版本重播、到期後不反覆熔斷、崩潰重入、冷卻跨程序、缺 token 數的兩種情況、孤兒程序判定、format_repair 中斷、mismatch 退避與重跑採用、同時執行時 credits 仍被讀取、交易鎖外重讀、R7～R12 各自的恢復條件，以及完整隔離回歸。

## 七、套用方式

- **第一批**（不需 Louis 另行選擇）：R1、R3、R4、R5、R6、R7～R11。
- **第二批**：R2 與 R12，待 Louis 選定方向。
- 各項分開實作、測試與可回退，可合併部署；照互斥程序排空並核對新政策與 campaign hash（只改 `quota_policy` 會被凍結設定拒絕）。
- 分工：GPT 實作；Claude 規格與審查；Gemini 獨立審查（涉及預算、權限隔離）。
- 完成後更新 PLAN §5.3、§7.2、§7.5、§7.7、§9，記錄 §15／§16。

## v1 → v2 變更

- 新增「賽中只跑預先固定的自動規則」與「不改驗收指紋檔案」兩項原則。
- R1：區分缺 token 數的兩種情況、到期後重新計數、固定判定基準。
- R2：改為待決選項，列出兩位審查者的分歧與現有載入證據；外掛與執行類設定確定不自動核准。
- R3：補程序身分、取得鎖後再查、部分輸出稽核、預算同步與 format_repair 重跑。
- R4：補三處硬攔截、讓出本輪後再重跑、重跑不繼承 mismatch。
- R5：補 `manual_admission()`、交易鎖外重讀、查詢失敗重試、第 2 層保留量虛增問題。
- R6：更正延誤描述；正式驗收只持受驗線的鎖。
- 新增 R7～R12。
