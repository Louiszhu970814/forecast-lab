VERDICT: REQUEST_CHANGES

# D140 程式審查 第1輪（Forecast Lab 指定設計／程式審查者）

## 0. 審查範圍與證據狀態
- 來源封包從頭到最後的 END-D140-CODE-R1 標記都在，沒有截斷。封包自己聲明有省略的部分：budget.py 217–330 的配額計算；scheduler.py 中 LineStop／PreflightError 的處理；discovery 的 `stopped` 怎麼算；`F.history`、`tool_violations`、`RUNTIME_FILES`、`baseline`、`quota`、`lock_all`、`M.require_idle`。依審查契約，省略的程式不能當成「不存在」的證據，也不能當成「已正確」的證據。下面凡是依賴這些部分的地方，都標成「需補證據」。
- 我沒有執行任何測試、指令或工具。`full-tests-r2.txt`（3511 passed）和 `deploy-tests-r1.txt`（11 passed）是整合者提供的主機輸出尾段，只能算對方的主張，我沒有驗證。
- `reviews/d140-design-response.md` 是實作者的敘述，不是證據。下面的判斷都以實際程式碼為準。
- Claude 的設計審只是「有條件接受 proof reuse 的方向」，不是實作核准。本輪也沒有核准。deploy.py 要求兩份 review 都是 `verdict: PASS`，所以以本輪結果，部署不得進行。

## 1. 結論摘要
整體架構方向正確：episode 式恢復、三段 CAS、grant 在 start 交易內一次性 claim、沿用 Budget.reserve、fast path 零模型呼叫。B1 大致成立，B3、B4 的主路徑也成立。但有 5 項阻擋，包括嚴格事件解析的漏洞、和文件／使用者範圍不符的環境綁定，所以本輪不能 PASS。

## 2. 阻擋項（Blockers）

### BLK-1（B2／嚴格解析）`src/line_recovery.py::strict_events` 和 `rejected_page_call`：會被靜默略過的事件，沒有進入「獨立稽核」
- 程式事實：
  - `strict_events` 只檢查 `step_update` 的鍵集合，以及 `tool_info.name` 是否在允許清單。它沒有要求 `step_index` 必須是 int，沒有限定 `step_type` 的允許值，`tool_info` 不存在時也不檢查 `tool_name`。
  - 之後的 `G.tool_steps` 會略過兩類事件：`step_index` 不是 int 的；以及 `step_type != 'tool'` 且該 index 之前沒出現過的。
  - 被略過的事件不會進入 `rejected_page_call` 的「其他呼叫逐一稽核」迴圈，也不會進入 `classify` 的 counts。
- 失敗路徑：raw 裡出現 `{"event":"step_update","step_update":{"step_index":"47","step_type":"tool","tool_info":{"name":"view_file","parameters":{"AbsolutePath":"/Users/…/外部檔"},"output":"…"}}}`。
  1. `strict_events` 通過：鍵合法，`view_file` 在 TIER1_TOOLS 內。
  2. `tool_steps` 因為 index 是字串而略過這一步。
  3. 外部路徑檢查和 output 檢查都沒被觸發，`classify` 照樣回傳 proof。
  - 新 index 搭配非 `tool` 的 `step_type`（例如 `read_url_content` 去讀 metaculus）也能用同樣方式繞過。
- 補充：
  - `json.loads` 預設接受 NaN／Infinity。
  - `init.conversation_id`、頂層 `conversation_id`、每個 `step_update.conversation_id` 沒有被要求一致：`cid` 是用 init 內的值覆蓋頂層值。
  - 文件宣稱「拒絕非嚴格 JSON／未知事件結構」，程式並沒有完全做到。
- 真實 AGY 是否會產生這類事件，封包沒有證據。但這個分類器的定位是獨立驗證，必須 fail-closed。
- 必要修正：
  - `step_index` 必須是 int。
  - `step_type` 用允許清單（依釘選 1.4.12 實測）。
  - 任何帶 `tool_info` 或 `tool_name` 的 update，都必須能對應到 `tool_steps` 的結果，否則拒絕。
  - 用 `parse_constant` 拒絕 NaN 和 Infinity。
  - 所有 `conversation_id` 必須等於 `cid`。
  - 每一項都要補對應的負向測試。

### BLK-2（B6／使用者範圍「Runtime or permissions changed ⇒ HOLD」）`capture`／`recover`：沒有綁定「失敗當時」的證明，只確認「現在有效且兩次相同」
- 程式事實：
  - `capture` 比對的是「目前快照 vs 最新已核准 baseline」、「目前 preflight 兩層」、硬編碼的 pin 和 wrapper SHA。
  - `recover` 只要求 `before == after`。
  - 沒有任何比對涉及失敗 attempt X 當時生效的 acceptance／production record、baseline record 或 run_config。
  - `classify` 沒有檢查 `run_config_version == cfg['version']`。
- 失敗路徑：
  1. X 在 T0 失敗，當時生效的是 A1／B1。
  2. T1 有人重做驗收產生 A2，並核准新 baseline B2（例如改過 agy 設定或權限）。
  3. T2 恢復器跑起來，`capture` 對 A2／B2 兩次一致，於是 resume 並發 grant。
  - 結果：舊 episode 在不同環境下被自動放行。這違反 PLAN §7.3 和 D140 任務單寫的「既有正式兩層隔離證據／基準／實體CLI／runtime 均不變」，也違反使用者範圍的「changed ⇒ HOLD」。
- 必要修正（二擇一）：
  - (a) 程式綁定：要求 gemini 所有非 autoload 的 `acceptance_result`、`acceptance_call_started` 以及 baseline 核准紀錄都早於 X 的 `started_at_utc`（或核對失敗當時記錄的 record id 和現在相同），並加上 `run_config_version` 相同的檢查，補測試；
  - (b) 由 Louis／設計審明確把政策改為「現行證明有效即可」，同步修改 PLAN 和任務單文字。在此之前不能 PASS。

### BLK-3（B4／中斷處理）`recover` 的例外處理：已 resume 卻記成 held，帳冊自相矛盾
- 程式事實：
  - 第三段 transaction 依序 append 兩筆：`line_resume`（含 `retry_grant`）和 `recovery_finished resumed`。
  - SIGALRM 由 `main` 的 120 秒 `setitimer` 觸發，可能落在兩次 append 之間；第二次 append 也可能發生 OSError。這兩種情況都會進入 `except Exception`，再寫一筆 `recovery_finished outcome=held`。
- 失敗路徑：
  1. 第 120 秒剛好落在兩次 append 之間，`RecoveryDeadline` 被拋出。
  2. 帳冊結果：`line_status` 已經有 auto resume 和有效 grant，排程器會照常補做；但 `line_recovery.jsonl` 寫的是 held，`main` 輸出 `held_for_review`。
  - 營運者和稽核看到的狀態與實際相反。
- 必要修正：
  - 進入最後提交交易前先解除 alarm（`setitimer(0)`），或設一個 committed 旗標。
  - except 內先檢查 `line_status` 是否已有該 `recovery_id` 的 `line_resume`。如果有，記為 `resumed_journal_incomplete` 之類的真實狀態，不要記成 held。
  - 補一個「兩次 append 之間注入例外」的測試。

### BLK-4（B5／持鎖契約，需補證據）`recover` 每一輪都無條件取用 `forecast.gemini.lock`
- 程式事實：
  - `with F.Lock(root/'line_recovery.lock'), F.Lock(root/'forecast.gemini.lock')` 在 `candidate` 之前就執行。所以 Gemini 線健康時，每 300 秒也會搶一次這個線鎖。
  - 有候選時，鎖會持續握過兩次 `capture`（quota.snapshot、cli_versions、兩層 preflight 各兩次），最長可到 120 秒。
- 失敗路徑：恢復器持鎖期間，scheduler 的 tier-1 Gemini job 執行 `with F.Lock(...)`，或 `reconcile.run` 在入口取鎖，都因 `LOCK_NB` 拿到 `PreflightError("another forecast.py is running")`。scheduler 怎麼處理這個例外（記成 `preflight_blocked`／`reevaluate_after`？整輪 cycle 是否受影響？）不在封包內。在截止前，這可能延誤健康的 Gemini 工作，甚至影響「其他線照常提交」。
- 必要修正：
  - 先做不持鎖的唯讀預檢（`line_stopped` 和便宜的候選判斷）；只有存在合格候選時才取線鎖，持鎖後再完整重算 CAS。
  - 同時提供程式或測試證據：scheduler 和 reconcile 遇到線鎖被佔用時，視為暫時性、非終局的 job 結果，而且不會阻斷其他線或整輪 cycle。

### BLK-5（B7／無 grant 時的一般行為，需補證據或收窄）`src/discovery.py::waiting`：喚醒範圍擴大到所有 `line_stopped` job
- 程式事實：只要 job 結果是 `line_stopped`，且該 player 不在 `stopped` 內，`waiting` 就直接回傳 False，不再看 retry、reevaluate 或 released。可是非 notify 的 LineStop 也會產生 `line_stopped` 結果，例如：
  - 「quota_exhausted: fixed backoff active」
  - run_attempt 的 quota_exhausted
  - 「campaign configuration changed」
  - 「line disabled」

  其中 forecast 帳冊裡的 quota backoff 並不會被 `F.line_stopped` 推導成停線。而 `stopped` 怎麼算不在封包內。
- 失敗路徑：Gemini 處於 forecast 帳冊的 quota backoff，但不在 `stopped` 內，所以該題每次 60 秒 heartbeat 都變成 actionable。scheduler 會再跑 preflight（含 agy /usage），再得到 `line_stopped`，循環往復。沒有模型呼叫，但改變了沒有 grant 時的一般排程行為，也可能和 1800 秒新題政策互相干擾。測試只涵蓋「三線明確 stop、gemini 人工 resume」這一種情況。
- 必要修正：
  - 收窄條件：只有在該 player 之後真的出現 `line_resume`（任何 `by`）、而且 job 結束時間早於那筆 resume 時才喚醒；其餘情況維持原本的 retry、reevaluate、released 語意。
  - 或者提供 `stopped` 計算的原始碼，並為上面列出的非明確 stop 類型補測試。

## 3. B1–B7 逐項評估

**B1 精確 episode 不得掩蓋其他 hard stop：大致符合，不阻擋**
- 實際程式：
  - `F.line_stopped` 對 auto resume 只做兩件事：移除和 `lifts` 相同的那筆 stop，以及把 `proof.attempt_id` 加入 `covered_attempts`。它不推進 `resumed_at`，帳冊重播仍從最後一次人工 resume 開始。
  - covered 只豁免 tool_scope 分支；`safety_stop_reason`、legacy mismatch、leak 視窗、unresolved start、mismatch cooldown 照常生效。
  - `latest_resume` 排除 auto resume，所以 `leak_count` 不會被放寬。
  - 人工 resume 保留原語意，並且重置 covered。
  - `situation` 用 `other_uncovered_stop`、`len(unsafe) == 1`、`unresolved_start`／`external`、`confirmed_audit_exposure`、`recent_model_mismatch`、`active_quota_backoff` 擋下其他狀況。
  - `candidate` 要求 stop 的 reason 和 run_id 精確等於該 attempt 的 notify 文字。
- 有測試涵蓋：較早未覆蓋的 stop、晚到但時間回填的危害、晚到的 audit。
- 建議（非阻擋）：
  - `line_stopped` 目前即使 `lifts` 沒有對應到任何 pending stop，也會把 attempt 加入 covered。應改成只有真的移除了 stop、且 stop reason 指向同一個 attempt 時才加入。
  - `F.line_stopped` 和 `R.situation` 裡重複的 pending 計算應抽成同一個函式，避免日後兩邊不一致。

**B2 目標必須來自同 attempt 較早成功的 read_url_content：部分符合（見 BLK-1）**
- 實際程式：
  - 頁面路徑必須 fullmatch `brain/<cid>/.system_generated/steps/<n>/content.md`，並通過 `own_page_file`。
  - n 必須小於錯誤 step，對應 step 的 tool 必須是 `read_url_content` 且狀態 DONE，且在同一個 stdout.jsonl 內。
  - 錯誤 step 必須恰好是 ACTIVE→ERROR，兩階段都沒有 output，參數不變且鍵集合精確，錯誤字串精確相符。
  - 其他呼叫都要在允許清單內、沒有被拒、不讀 metaculus；`view_file` 只能讀自己 conversation 的頁面。
  - raw 檔讀前讀後的 SHA 必須相同；路徑元件不能有 symlink；檔案大小有上限。
- 只有敘述、沒有證據的部分：AGY 的 `steps/<n>` 目錄編號等於 `step_index`；真實事故（step46 指向 step39）只做了 metadata 核對。
- 測試缺口：沒有「頁面 step 晚於錯誤 step」的負向測試；沒有 step_type 或 step_index 異常的測試。

**B3 stop、人工 resume 和新安全事件共用 transaction／CAS：主路徑符合**
- 實際程式：
  - `recover` 分三段 transaction，每段都重算 `candidate`（含 `situation_sha256`，雜湊範圍是本線的 status、attempts 投影、starts、external、audits、external_usage），並重新檢查 `active()`。
  - 昂貴的 `capture` 放在交易外，但仍持有本線鎖。
  - 人工 resume 改成 line lock 加 transaction。`F.run` 和 `reconcile.run` 寫 notify `line_stop` 時也包進 transaction。
  - 有 superseding stop 的測試。
- 需補證據：scheduler、advise、practice 自己寫 `line_stop` 的路徑不在封包內。目前看到的程式在競態下會落到 ledger 推導的 stop，候選判斷因 reason 不符而 fail-closed，但這只是推論，不是證據。
- 建議：人工 `resume` 遇到線鎖被佔用會拋出未捕捉的 `PreflightError`，應改成友善的「忙碌、請稍後重試」提示。

**B4 一次性 claim、既有 pending-inclusive 預算准入、中斷處理：claim 與預算符合，中斷記錄有缺陷（BLK-3）**
- 實際程式：
  - grant 只能透過 `_may_rerun` 進入 `kinds = ['rerun']`。順序是先 `before_attempt`（Budget.reserve，含 pending、external、caps），再在 `transaction(forecasts root)` 內呼叫 `retry_grant`。
  - `retry_grant` 會核對：同 player／qid／tier 不存在任何 rerun start（跨 snapshot）、metadata 雜湊、raw SHA、到期時間、runtime、run_config、baseline record、wrapper 和 pin、目前未停線、晚到的 leak_confirmed audit。
  - 檢查通過後，`recovery_grant_id` 和 `admission_decision_id` 一起寫入同一筆 start record，然後才呼叫 CLI。grant 失效時拋出 `QuestionDone`，`finally` 呼叫 `admission_outcome(False)` 釋放預留。
  - 預留之後、start 之前崩潰，由 `Budget.recover` 釋放；start 之後狀態不明，則占用 rerun 槽並以 unresolved start 停線，不會重跑。
  - `recovery_started` 寫入後中斷，該 stop 永久記為 `already_attempted`（每 stop 一次）。
  - 有一個測試直接呼叫 `F.forecast_question` 驗證只 start 一次。
- 需補證據：沒有 scheduler 端到端的證據，證明排程器真的會選到這題並呼叫 `forecast_question`。scheduler、advise 或 finalization 裡如果有其他不帶 `recovery_root` 的 `_may_rerun` 呼叫，會把這題判成 exhausted，補做就永遠不會發生。這不影響安全，但影響「完成並提交」的目標。

**B5 實際持鎖契約、不巢狀 flock：持鎖契約符合，鎖競爭有問題（BLK-4）**
- 實際程式：`F.Lock` 會登記 `_HELD_LOCKS`；`recover_acceptance` 遇到已持有的同線鎖時改用 `nullcontext`；fast path 不呼叫 `acceptance.run_line`；`transaction` 是可重入的，`line_stopped` 在交易內再開交易不會造成自我死鎖。
- 我沒看到同一程序對同一把鎖重複 flock。

**B6 兩層 proof、baseline、CLI 實際雜湊、runtime、judge、canonical 限制不變：「現行有效」的部分符合，「與失敗時相同」不符（BLK-2）**
- 實際程式：
  - `capture` 檢查：wrapper 和 pin 的 SHA 與硬編碼值相符且不是 symlink；`shutil.which('agy')` 指向 wrapper；新鮮的 zero credits；`B.compare` 為 ok；tier1 和 tier2 的 `F.preflight` 都 ok 且沒有 waiver（preflight 內含最新 acceptance 和 evidence SHA、judge、CLI 版本、runtime fingerprint、正式 model／effort proof、baseline blockers）；binding 含 judge_version 和 run_config 雜湊；兩次 `capture` 必須完全相同。
  - `recover` 另外核對原 attempt 的 model／effort 等於目前設定。
- 建議：
  - `retry_grant` 的 binding 檢查目前是「欄位存在才檢查」（`if binding.get(...)`），應改成缺欄位就拒絕。
  - start 時也應比對 `ctx.preflight` 的 acceptance／production id 和 `binding.proofs`。
  - launchd 環境下 PATH 是否讓 `which('agy')` 指到 wrapper，封包沒有證據；如果不是，恢復器會永遠 hold。

**B7 保留既有 initial→rerun 上限，沒有 grant 時行為不變：rerun 部分符合，discovery 有問題（BLK-5）**
- 實際程式：「已有 rerun 就判 attempts_exhausted」、「已有 adoptable 就判 already_selected」的判斷順序沒變；`classify` 只接受 `attempt_kind == initial` 且沒有 prev；rerun 之後在 `kind == 'rerun'` 處 break，不會有第三次 base；原失敗紀錄維持 incomplete，有測試斷言。
- 建議：現在每個「只有一筆失敗 initial」的題目都會呼叫 `retry_grant`。它會讀 line_status，遇到殘行時 `read_complete` 會拋 ValueError；grant 存在但 raw 被清掉時 `safe_raw` 也會拋例外，例外會一路傳出 `forecast_question`。應在 `retry_grant` 內把 ValueError／OSError 轉成 None，讓一般線的例外行為和原本一樣。

## 4. 其他非阻擋建議
1. 啟用前，對真實事故 raw 做一次只輸出布林值和雜湊的 `classify` 乾跑。目前沒有證據顯示真實 stream 能通過（finish step、`tool_call_counts` 的計算口徑、step 目錄編號都只是假設）。如果通不過，恢復器會靜默地永遠 hold，也就不會補做。
2. 時間預算：兩次 `capture` 都要在 120 秒內完成。如果在 `recovery_started` 之後超時，該 episode 會被永久消耗。應先實測耗時，或把第二次 `capture` 改得更輕量。
3. `strict_events` 改用 `raw.split(b'\n')`，避免 `str.splitlines` 在 U+2028 等字元處斷行（目前是 fail-closed，不算危險）。另外 `RecursionError` 這類例外不在 `main` 的捕捉範圍內。
4. 部署（`deploy.py`）：
   - 程序在 `launchctl disable` 之後被強制終止時，cycle timer 會一直停用，影響其他線提交。應準備手動回復步驟或 watchdog。
   - 需確認 `lock_all` 是否包含 `ledger.lock`。如果包含，preflight 子程序的 transaction 會被擋住，等到 240 秒逾時，然後整個部署回滾。
   - 部署時的 smoke 測試只跑 4 個檔案，逾時 120 秒。
   - `activate` 沒有先檢查 `applied.json` 是否存在。
   - plist 註解「Install only after Louis approves trial」已經過時。
5. 補測試：人工 resume 遇到線鎖、兩次 append 之間的中斷、頁面 step 晚於錯誤 step、scheduler 端到端補做、discovery 的非明確 stop 類型。

## 5. 實際證據 vs 敘述
- 已由程式碼本身確認：B1 的 episode 語意；B2 的大部分精確條件；B3 的三段 CAS；B4 的 claim 位置和預算釋放；B5 的 `_HELD_LOCKS` 契約；B6 的 `capture` 項目；B7 的 rerun 判斷順序；恢復路徑沒有模型呼叫，也沒有讀取預測內容（fixture 斷言 proof 不含機率或理由；我也沒有檢視任何預測內容）。
- 只有敘述：真實事故 step46→39；AGY 步驟目錄編號；scheduler、advise、practice 已有共同 transaction；main46572 已由其他線完成；測試通過數；`RUNTIME_FILES` 不含這次修改的檔案（如果含，部署後的 preflight 會失敗、部署回滾）。
- 歷史 suspected keyword 標記沒有被升格成確認暴露，紀錄也保留著（有測試），這點符合範圍要求。

## 6. 重審所需
修正 BLK-1、BLK-3；BLK-2 依選項 (a) 實作，或取得政策變更的明確書面授權；BLK-4、BLK-5 修正或提供省略程式碼的證據。之後提交新的 source manifest 和完整 diff（含 scheduler、discovery 相關原始碼），重新進行雙審。本輪沒有核准，也不得據此部署或啟用。

END-D140-CODE-R1