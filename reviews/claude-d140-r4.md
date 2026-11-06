VERDICT: PASS

# D140 R4 合併差異審查（Claude / claude-opus-5-5，Forecast Lab 設計／程式碼審查者）

## 結論
- 這個 PASS 只涵蓋本 R4 差異封包：測試檔 `tests/test_line_recovery.py` 新增的一行，以及 `deploy.py`、`test_deploy.py` 的 r4 路由差異，另外判斷未變更的 runtime 能否繼承四份 R3 實際審查。
- 我沒有重新審查封包外的 runtime 原始碼。
- 我沒有執行任何測試、指令或檔案存取。下面所有「通過數」都是封包提供的資料，不是我自己的觀察。
- 我沒有發現 blocker。下面列的改進建議都不影響這次 PASS。
- 本 PASS 不等於部署核准。部署還需要 Gemini 4 ArgonHigh 對同一份 R4 封包給出實際的 PASS，並且通過 deploy gate 的完整機械驗證。我不代替任何一方主張核准。

## 一、封包完整性
- 封包最後一行是 `END-D140-CODE-R4`，判定為完整封包。
- 封包開頭附近列出 shared source manifest 的 SHA256 `847a3efe…3992`。這符合 `review_bundle` 要求封包文字必須包含 `source_hash`。
- 封包內的雜湊互相吻合：
  - `r3_manifest_sha256`（c5748f…）＝ inputs 裡的 `source-manifest-r3.json`
  - `r3_verification_sha256`（defb91…）＝ `verification-r3.json`
  - 四份繼承審查記錄的 packet、answer、stdout 雜湊，和 inputs 裡對應的 packet-r3a/r3b、claude/gemini-r3a/r3b 的 answer.md 與 stdout.jsonl 逐一相符
  - 三份 validation 檔標頭的雜湊（347c0f…、0f2fea…、66e6e8…）和 inputs 相符
  - manifest 的 `contexts` 和 `files` 裡，`tests/test_line_recovery.py` 都是 `cae3b9…`，和 `only_source_delta.r4` 以及煙霧測試 JSON 的 `test_sha256` 一致
- 以上只是封包內部一致性的檢查。我無法獨立驗證這些雜湊是不是真的對應磁碟上的位元組。

## 二、實際證據（封包提供，我沒有執行）
1. **根因失敗紀錄**：在帶有守衛的煙霧測試中，`test_recovery_grant_is_selected_and_budgeted_by_real_scheduler` 失敗，結果是 1 failed、265 passed。
   - 呼叫鏈：`Scheduler.execute` → `forecast.run_attempt` → `before_attempt` → `admit` → `budget.reserve` → `read_complete(self.acceptance_log)`
   - 被開啟的是正式環境的 `/Users/ucchengg/forecast-lab/data/acceptance.jsonl`，被 `run_offline.py` 的 audit-hook 守衛以 `PermissionError` 擋下。
   - 這表示：測試沒有傳 `acceptance_log` 時，`Scheduler` 預設會指向專案根目錄下的 `data/acceptance.jsonl`，而不是測試的 `root`（tmp）。R3 時這個測試能在 stage 通過，看起來是因為 stage 沒有 `data/acceptance.jsonl`，屬於環境相依。在正式環境、data 有內容的根目錄下才會出事。
   - 守衛確實生效，失敗方向是 fail-closed。
2. **兩次 apply 失敗**：先前兩次 apply 都在守衛煙霧測試失敗，並在任何 recovery activation 之前回滾，cycle 已重新啟用。這和上面的根因一致。
3. **smoke-stage-r4.txt**：266 passed。
4. **smoke-populated-root-r4.txt**：266 passed。搭配的 JSON：`returncode` 0、`runtime_identical` true、`test_sha256` cae3b9…，stage 路徑和 manifest 的 stage 一致。
5. **deploy-tests-final-r4.txt**：35 passed。
6. **`runtime_model_calls_for_validation`**：0。

## 三、測試差異審查（tests/test_line_recovery.py，r3 → r4）
- 唯一的變更是建構 `S.Scheduler(...)` 時多傳 `acceptance_log=f.root/'acceptance.jsonl'`。
- 這直接修正上面的根因。`f.root` 是 `tmp_path/'project'/'data'`，在 pytest 的 tmp 底下，不在守衛保護的專案根目錄下，所以不會碰到正式資料。
- **隔離有變強**：
  - 原本測試在沒有守衛的環境（例如直接在專案根目錄跑 pytest）會悄悄讀到正式環境的 acceptance 帳本，是測試隔離缺陷。R4 消除了這個問題。
  - 封包聲稱 `run_offline.py` 和所有守衛位元組不變（395 檔相同）。差異裡也沒有看到任何守衛被放寬。
- **語意變化**：
  - 現在 `budget.reserve` 讀的是 fixture 寫入的合成記錄：`baseline_approved`，以及 accepted/prod1/prod2 三筆 `acceptance_result`，其中 prod 記錄帶 `mode: production` 和 tier 設定。R3 在 stage 讀到的實際上是空清單。
  - 我認為這更貼近正式環境的語意，而且原本的斷言全部保留：
    - `jobs` 只有一個，player 是 gemini、tier 是 1
    - `outcome` 是 selected，`adapter.ran==1`
    - `recovery_grant_id` 等於 `result['recovery_id']`
    - `admission_decision_id` 存在
    - forecasts 的 `validation_status` 是 incomplete
  - 這些斷言沒有被刪減或放寬。
- runtime 沒有任何變更。這個修正只在測試層，符合「只有一個測試加上明確合成 acceptance_log」的範圍聲明。

## 四、deploy.py r4 路由審查
**新增邏輯**：
- `review_round = approved.get('round','r3')`，只接受 r3 或 r4，其他值會丟 ValueError。
- module 清單檔依輪次選 `review-modules-{round}.json`。
- r3 只允許 `['r3']` 或 `['r3a','r3b']`；r4 只允許 `['r4']`。

**原有檢查全部保留**：
- authorization 必須是 PLAN D140；`source_sha256` 必須等於 `hash_obj(files)`；`deployment_sha256` 必須等於 `deploy.py` 本身的雜湊。
- module 清單檔的雜湊、shared source manifest 的雜湊雙重綁定（approval 與 modules 兩邊都要對）。
- stage/base 路徑和 files 完全一致；每個 stage 檔案、每個 context、每個 ops/evidence input 都做 CAS。
- 封包檢查：結尾 marker、雜湊、bytes 必須小於 170000、封包文字必須包含 `source_hash`。
- 兩位玩家都必須對每個 module 給出 PASS。answer 第一行必須是 `VERDICT: PASS`，最後一行必須是 marker。
- start/finish 記錄必須唯一，model、effort、packet、answer 的鏈結要一致。
- 實際模型集合必須等於預期模型；工具必須是 StructuredOutput 或 finish 的子集；Claude 不得出現 server_tool_use。
- 結構化輸出必須是只含 answer 的 dict，而且內容和 answer.md 完全相同。
- usage、stream、packet、model 的鏈結必須一致。
- 受保護路徑規則不變。

**降級與混用分析**：
- 拿 r3 的 approval 套到 R4 stage：`source-manifest-r3` 的 files 裡測試檔雜湊不同，`source['files']!=files` 會失敗。另外 approval 的 `source_sha256` 也綁定目前的 files。所以 r3 的審查無法放行 R4 stage。
- R4 approval 缺少 round：會預設成 r3，然後在 module 檔雜湊或 FileNotFoundError 那一關失敗。
- R4 approval 缺少 Gemini 的 r4 審查：在 `set(player_reviews)!=set(expected)` 失敗。
- r4 只接受 `['r4']`，不能和 r3a/r3b 混用。

**R3 實際審查成品的綁定**：
- R3 的 answer.md、stdout.jsonl、packet、`review-modules-r3.json`、`source-manifest-r3.json`、`verification-r3.json`、ops-r3 都列在 R4 manifest 的 inputs 裡，部署時會做 CAS，所以必須位元組不變。
- 部署時不會重新跑 R3 的 start/finish/model/usage 鏈驗證。這部分靠 host 的機械複查和 `verification-r3.json` 的雜湊綁定。

**test_deploy.py 新增測試**：
- 用 indirect 參數化的 `env` fixture，r3 和 r4 共用同一套合成環境。
- `test_r4_requires_both_actual_delta_reviews_and_preserves_context_cas`：驗證 r4 通過、context 雜湊保留，刪掉 Gemini 的 r4 審查後失敗。
- `test_r4_cannot_fall_back_to_another_round`：round 設為 unknown、r3，或移除 round，都會失敗。
- `test_r3_approval_without_round_remains_supported`：舊格式的 r3 approval 仍然可用（向後相容）。
- 斷言方向正確，沒有放寬既有檢查。

## 五、R3 實際審查繼承評估
- R3A 和 R3B 各有 Claude（claude-opus-5-5/high，工具只有 StructuredOutput）和 Gemini（gemini-4-argon-high/high，沒有工具）兩份 PASS。
  - end_marker 已驗證、usage 記錄已驗證。
  - 時間序列合理：同一 module 的兩位玩家同時開始；Claude 的 r3b 在 r3a 結束後才開始。
- R4 的 runtime、config、policy、prompt、adapter、wrapper 都聲稱和 R3 位元組相同（same_file_count 395）。唯一的原始碼差異是一行測試變更，不會改變 runtime 行為。
- 因此我認為：只要 host 的位元組相同聲明屬實，未變更的 runtime 可以合理繼承 R3 的四份實際 PASS。
- 這個繼承結論依賴 host 的機械複查，我無法獨立驗證 R4 和 R3 manifest 的 files/contexts 逐項相同，因為封包沒有附 R3 manifest 原文。

## 六、Blockers
沒有。

## 七、改進建議（不阻擋）
1. **標籤不一致**：`only_source_delta` 和 `reference_delta` 用 `r1` 當舊雜湊（f81dab…）的鍵，但差異標題是 r3 → r4。建議改成 `r3`，或在 manifest 註明「此檔自 R1 起到 R3 未變」，避免以後稽核時誤讀。
2. **在部署時機械化繼承**：r4 路由可以加一項檢查，要求 `source-manifest-r4` 的 files 和 contexts 必須等於 `source-manifest-r3`，只有宣告的 delta 鍵可以不同。這樣繼承就不只靠審查時的證明。因為兩份 manifest 都已 CAS 綁定，這不是 TOCTOU 風險，只是縱深防禦。
3. **補強填充根目錄煙霧測試**：
   - 目前 mirror 只放了 `data/acceptance.jsonl` 一個哨兵檔。正式環境的 `data/` 還有其他帳本（forecasts、line_status、budget 等）。如果某個測試以預設路徑、並且「檔案存在才開啟」的方式讀其他帳本，第三次 apply 的煙霧測試仍可能失敗，雖然一樣會 fail-closed 回滾。
   - 建議依正式環境 `data/` 的檔名清單建立空哨兵檔。只需要檔名，不讀內容。
   - 另外，`sentinel_access_forbidden_by_guard: True` 是寫死的值，不是量測結果。建議加一個負向對照：在同一個 mirror 跑 R3 版測試，證明它會因守衛而失敗。
4. **涵蓋範圍**：煙霧測試命令只包含 test_line_recovery、test_forecast、test_reconcile、test_discovery_cadence 四個檔，沒有 `tests/test_scheduler.py`。R3 有 `full-tests-final-r3.txt`，但 R4 沒有對應的全套結果。建議：
   - 確認 apply 時的守衛煙霧命令和這個 harness 完全相同；
   - 在填充根目錄下跑一次全套測試；
   - 檢查 test_scheduler.py 是否也有不傳 `acceptance_log` 就建構 Scheduler 的寫法。
5. **runtime 設計觀察**（屬於已繼承的 R3 runtime，不在本差異範圍）：`Scheduler` 的 `acceptance_log` 預設值看起來不是從 `root` 推導出來的。正式環境中 `root` 就是 `project/data`，所以行為正確。但以後可以考慮讓預設值由 `root` 推導，從源頭避免這類測試洩漏。

## 八、我不主張的事項
- 我沒有執行測試，沒有讀取任何檔案，也沒有驗證任何雜湊對應的磁碟位元組。
- 我沒有審查封包外的 runtime 原始碼。
- 我不知道 Gemini 對 R4 的審查結果。

## 九、部署前仍須滿足的條件
- 兩個實際模型都必須對這份 R4 封包給出 PASS，不做自動重試。
- deploy gate 的 r4 路由必須全部通過機械驗證。
- 使用者授權仍為 PLAN D140。
- recovery 的既有前提不變：
  - 兩項正式環境 preflight 與 proof identity 都要成立；
  - 被拒絕的 typo 必須完全吻合；
  - recovery 期間不呼叫任何模型；
  - 原始重跑只有一個有界的名額；
  - 走一般的 scheduler、預算與截止時間規則。
- 守衛煙霧測試失敗時，必須在 recovery activation 之前回滾，並重新啟用 cycle。

END-D140-CODE-R4