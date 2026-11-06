VERDICT: REQUEST_CHANGES

# D140 r2 模組 A 程式審查（R2A）

我是 Forecast Lab 指定的設計／程式審查者。以下只根據提供的來源包內容判斷，沒有使用工具，也沒有執行任何測試、指令或預測。本結論只涵蓋模組 A：
- 嚴格分類器
- 安全事件重放
- 驗收證據沿用
- 恢復提交
- 在 run_attempt 內再次確認 grant 並寫入 start

這份結論不是整個系統的核准，也不代表 Claude 或 Gemini 已經 PASS。部署仍需兩個官方模型對每個模組都給出 PASS。

## 一、來源包是否完整

- 來源包一直到最後標記 END-D140-CODE-R2A 都在，沒有傳輸截斷。
- 但以下內容本來就不完整：
  - `src/forecast.py:684-698` 只是片段，結尾停在 `if r.get("type") == "acceptance_call_started" and r.get("player") == player \`，看不到 `recover_acceptance` 的其餘部分。
  - manifest 列出的部分檔案沒有原文：`discovery.py`、`scheduler.py`、`budget.py`、`reconcile.py`、`practice.py`、`autoload_maintenance.py`、`advise.py`、`config/line_recovery.json`、launchd plist、`run.sh`、`test_scheduler.py`、`test_discovery_cadence.py`、`deploy.py`、`test_deploy.py`。
  - 審查需要依賴、但包內沒有的函式：`_acceptance_records`、`incomplete_acceptance`、`autoload`、`guards`、`usage` 和 `quota`。
- `forecast.py` 只有片段，所以我無法確認這些片段的雜湊等於 manifest 中的 `c92749aa…`。
- B 模組負責的部分不在本次判斷範圍內，包括：喚醒（BLK5）、預算預留、scheduler 選題、busy 時的 `line_error`，以及部署。

## 二、r1 各項 blocker 的處理情況

**BLK1（嚴格事件檢查）：從原始碼看已修正。**
- `strict_events` 在呼叫 `G.tool_steps` 合併之前，就逐筆檢查每個事件與 frame：
  - JSON 不得有重複鍵，也不得有 NaN 或 Inf。
  - 頂層、step 與 tool_info 的欄位都必須在允許清單內。
  - `step_index` 必須是真正的 int（排除 bool），而且不得遞減。
  - 每個工具群組必須剛好兩幀：ACTIVE 之後是 DONE 或 ERROR。
  - 開始幀必須是 tool，參數必須是 dict，而且沒有 output 或 error。
  - 每一幀的 name 與參數都必須和第一幀相同，唯一例外是 finish 工具的 tool→finish DONE。
- 同一 index 被後來的 `read_url_content` 覆蓋、非工具步驟夾帶 `tool_name`、各處 conversation ID 互相矛盾，都會被拒絕，也都有對應的負向測試。
- `rejected_page_call` 另外再精確檢查一次被拒絕那一步，並稽核其他所有呼叫。
- 唯一的出入：允許清單沒有 ALWAYS_ADDED_TOOLS，所以這類事件會被拒絕。這是偏保守的做法，可以接受。

**BLK2（現行證據綁定事故當時的證據）：只修了一部分，仍是 blocker，見 B1。**

**BLK3（已恢復卻被記成 held）：已修正。**
- `except` 分支會在交易中先讀回帶有 `recovery_id` 的 `line_resume`。讀到就回傳 `resumed` 加上 `completion_journal=pending`，不寫 `held`。
- 若讀取本身失敗，例外會往上拋，最後由 main 回傳 `check_failed_state_not_assumed`。
- 兩種故障情境都有測試：完成紀錄寫入時 OSError，以及 resume 寫入後才收到截止訊號。

**BLK4（健康線鎖競爭，A 的部分）：已修正。**
- 是否健康的判斷在取得任何鎖之前完成，有測試證明健康時不會碰到 Gemini 線鎖。
- 恢復流程只持有自己的恢復鎖與 Gemini 線鎖。測試在 capture 期間成功取得 claude、gpt、cycle、scheduler、submit 這幾把鎖，可以作為證據。
- 「Gemini 忙碌時只有這條線本次 `line_error`」的 scheduler 端證據屬於 B，本包看不到原始碼。

**BLK5（喚醒）：屬於 B，`discovery.py` 不在包內，本次無法判斷。**

**另外兩項獨立合成重現的問題：**
- 同一 attempt ID 帶入新危害：`F.line_stopped` 現在比對的是整個 projection 的 SHA，而不只是 attempt ID；`R.situation` 遇到重複 attempt ID 會直接失敗。兩者都有測試。
- 同一步驟工具身分互相衝突：已由 BLK1 的逐幀檢查處理。

## 三、Blockers（必須修正）

### B1：事故當時與現行驗收證據的綁定，仍是推論出來的，而且有缺口

`require_incident_environment` 只靠時間戳推論「事故後沒有新的驗收」。原始碼中沒有任何地方把現行 capture 的 `acceptance_record_id` 和 `production_record_id`，與事故當時的證據直接比對或記錄下來。這個推論有三個缺口：

1. **autoload 豁免沒有說明。**
   - 條件 `record.get('source')!='autoload'` 讓 `source='autoload'` 的 `acceptance_call_started` 和 `acceptance_result` 完全跳過時間檢查。
   - 本包看不到 `_acceptance_records` 和 `check_acceptance` 是否會把這類紀錄當成 `records[-1]` 或 production 證據。
   - 如果會，事故後才產生的 autoload 通過紀錄，就能成為現行證據而走快速路徑。這正是 BLK2 要禁止的情況。
   - 沒有任何測試涵蓋這種情況。
2. **採用拒絕清單，未知類型會放行。**
   - 只檢查 `baseline_approved`、`acceptance_call_started`、`acceptance_result` 三種類型。
   - 其他類型，或欄位不同的 Gemini 或 common 紀錄，即使是事故後產生，也會被默默放行。
3. **最早的證據時間只來自主張。** 回應文件提到 `base_…`、`acc_…` 和兩筆 production 證據都早於事故，但這只出現在主機的 probe 投影中，程式不會強制檢查。

需要的修改：
- 移除 autoload 豁免；或在本包內附上原始碼與負向測試，證明 autoload 紀錄永遠不會進入 `check_acceptance` 或 production 的選擇。
- 改成允許清單：任何事故開始之後、player 為 Gemini 或 common 的 `acceptance.jsonl` 紀錄，只要類型不明或缺少時間欄位，一律讓恢復失敗。
- 在 capture 或提交時，明確確認每一筆 proof 的 `acceptance_record_id` 和 `production_record_id` 都早於事故開始，並寫進 resume 的 binding。
- 補負向測試，至少涵蓋：
  - 事故後由 autoload 產生的 `acceptance_result`
  - 事故後出現的未知類型紀錄
  - 事故後新增的 production 通過紀錄

### B2：grant 再次檢查會在綁定欄位缺漏時放行，而且這些分支完全沒有測試

`retry_grant` 對 binding 的每一項檢查都寫成 `if binding.get('runtime') and …`。runtime、`run_config_sha256`、`baseline_record_id`、`wrapper_sha256` 這幾項，只要欄位不存在或是空值，就直接跳過檢查。

`recover` 在提交前也不驗證 `after` 是否具備必要欄位。所有 recover 測試的 `capture_fn` 都回傳 `{}` 或 `{'proofs':['current']}`。也就是說，現有測試證明的反而是「完全沒有 binding 也能 resume 並拿到 grant」。

正式環境的 capture 雖然一定會填這些欄位，但 start 前的再次確認是 A 模組的契約責任。現在這部分是寫成放行、而且沒有測試的分支。

需要的修改：
- `recover` 在提交前要求 `after` 必須具備非空的以下欄位，否則記為 held：`baseline_record_id`、runtime、`run_config_sha256`、`wrapper_sha256`、`pin_sha256`、`judge_version`，以及剛好兩筆、tier 1 與 tier 2 各一的 proofs。
- `retry_grant` 遇到任何必要的 binding 欄位缺漏時，必須回傳 None。
- 測試要改用真實形狀的 binding。
- 新增負向測試，從 resume 到 start 之間分別改動 runtime、run_config、baseline、wrapper 或 pin，並經過真實的 `run_attempt`。預期結果：
  - 拋出 `QuestionDone`
  - 不寫 start journal
  - `admission_outcome(…, False)` 釋放預算預留
  - 原失敗紀錄仍是 `incomplete`

## 四、非阻擋問題與建議

1. **分類在共用 ledger 鎖內執行。** 每次 tick 最多呼叫三次 `candidate`，每次都會讀最多 32 MB 的 raw、計算兩次 SHA 並解析 JSON，期間會阻擋其他線的交易和送出。建議把分類移到交易外，交易內只比對 raw SHA 與 metadata SHA。
2. **兩處「已涵蓋」判斷標準不一致。** `situation` 的 covered 只比 attempt ID，而且 external 列不在重複 ID 檢查內；`F.line_stopped` 則比對 metadata SHA，比較嚴格。目前由 `line_stopped` 守住安全，但建議統一用 metadata SHA。
3. **`lifts` 型別檢查不一致。** `situation` 處理 `lifts` 時沒有像 `line_stopped` 那樣檢查 `isinstance(lifts, dict)`。
4. **main 不是每種例外都會捕捉。** 例如 `subprocess.TimeoutExpired`，或可能不是 ValueError 子類別的 JsonlParseError，會讓程式在沒有 JSON 輸出的情況下結束。結果仍是安全的，只是不易觀測。
5. **`completion_journal=pending` 沒有後續補登機制。**
6. **`strict_events` 的兩個小漏洞。** 它會略過空白行；`RecursionError`（極深的巢狀 JSON）沒有被捕捉。
7. **由 ledger 重放推出的 stop 永遠不會符合恢復資格。** 原因是 reason 字串不同。這是保守的設計，建議寫進文件。
8. **grant 失效時留下空目錄。** `raw_dir` 會先建立，grant 在之後才失效，會留下空目錄，只影響整潔。
9. **測試覆蓋的強度。** 目前的 grant→start 整合測試（`test_recovery_retry_reaches_real_start_journal_once` 和 scheduler 測試）都使用空 binding，強度受 B2 影響。

## 五、證據分類

**我實際從原始碼讀到並推論的：**
- 上述 BLK1、BLK3 的修正，以及 BLK4 中 A 的部分。
- `run_attempt` 在線鎖與 ledger 交易內，先呼叫 `retry_grant` 再寫入帶有 `recovery_grant_id` 的 start，之後才啟動 CLI。
- `retry_grant` 的「已用掉一次 rerun」判斷是跨 snapshot 的，任何 rerun start 都會佔用原本的一次槽。
- `_may_rerun` 的一般路徑仍然排除 `tool_scope_violation`。
- 恢復產生的 resume 不推進 `resumed_at` 和 leak 視窗，人工 resume 的原有語意保留。
- manifest 中 `adapters/gemini.py` 的雜湊等於 probe 中 runtime 的 `56b4…`，而且 runner、netguard、gemini adapter 都不在變更檔案清單內，彼此一致。

**主機回報、我未驗證的：**
- 3536 passed、15 passed。
- capture probe：eligible、兩次結果相等、13.90 秒、0 次模型呼叫、帳冊沒有變動。
- 證據 ID 早於事故。
- UTC 11:31 主賽 46572 由其他線完成的說法，本包沒有附任何證據檔。

**只是主張、本包沒有原始碼的：** BLK4 的 scheduler 端行為、BLK5，以及預算預留、釋放與 `admission_decision_id` 的來源。

## 六、結論

A 模組在 BLK1、BLK3 以及兩項合成重現問題上，從原始碼看已經修正。但事故當時與現行證據的綁定仍靠時間推論，留有 autoload 豁免和未知類型放行的缺口（B1）；start 前的 grant 再次確認在欄位缺漏時會放行，而且沒有測試（B2）。所以本模組維持 REQUEST_CHANGES。這不代表任何其他模組或整個系統已核准，也不能據此部署。

END-D140-CODE-R2A