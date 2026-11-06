VERDICT: PASS

# D140 R3B 程式審查（Forecast Lab 指定設計／程式審查者，Claude）

## 0. 範圍與效力
- 這個 PASS **只適用於模組 R3B**，也就是 B 負責的部分：同線鎖與 admission 呼叫端、含 pending 的預算、wake 判定、部分線路的 finalization，以及部署器。它**不代表**整個系統或 R3A 獲得核准。
- 依合約，Claude 與 Gemini 兩個正式模型都必須對**每個模組**（R3A、R3B）給出 PASS，才可部署。R3A（嚴格分類器、精確 safety replay、proof 重用、恢復 commit、grant／start 原子 claim）我在本封包中看不到 `src/line_recovery.py` 原始碼，所以沒有審查，也不作任何判定。
- 我沒有執行任何測試。封包附的 host 結果（full 3574 passed、deploy 30 passed，以及兩份檔案的 SHA）只是**封包內的文字主張**，我沒有獨立驗證。
- 封包完整性：封包到最終標記 `END-D140-CODE-R3B` 為止都完整，沒有看到截斷。不過 `src/forecast.py` 只給了片段，見第 5 節的證據缺口。

## 1. 依實際原始碼確認的證據

### 1.1 同線鎖：F.Lock 與 _HELD_LOCKS
- `Lock.__enter__` 使用 `fcntl.flock(LOCK_EX|LOCK_NB)`，搶不到鎖就關閉 fd 並拋出 `PreflightError`。成功時把 resolve 後的路徑加入 thread-local 的 `_HELD_LOCKS.paths`，`__exit__` 解鎖後再移除。
- 線鎖在所有呼叫點都是**非阻塞**的，所以線鎖之間不會因為取鎖順序不同而互相等待成死鎖，最壞情況只是取鎖失敗。ledger `transaction` 是阻塞鎖，但持有時間短，而且在 transaction 內不會再等待線鎖。這是正面的結構性證據。
- 同一 thread 重入：`recover_interrupted` 先看 `_HELD_LOCKS` 再決定用 `nullcontext`；`reconcile.run` 先持鎖再呼叫 `recover_interrupted`，路徑正確。其他 thread 或 process 持鎖時，則走 `PreflightError` 分支。

### 1.2 健康的線鎖競爭（先前 BLK4）
- `recover_interrupted` 在外層攔截 `PreflightError` 並回傳 `[]`。因此 `scheduler.run` 開頭的 `for player: _recover` 以及 `execute` 開頭的 `_recover`，遇到鎖忙都不會中止整個排程。
- `execute` 的 tier1 在 `with F.Lock(...)` 鎖忙時拋出 `PreflightError`，`execute` 不會攔截。在 continuous 模式下，`run()` 從 `future.result()` 取得例外後，記一筆 `line_error`（不屬於 TERMINAL），並把該玩家加入 `paused`，其他線繼續。
- tier2 的 `R.run` 也是在 `with F.Lock` 處拋出，結果相同。兩者都在預算 admission **之前**失敗，所以沒有預留、沒有 start、沒有 CLI 呼叫。
- 非 continuous（M2）模式下會直接 raise，但 `run()` 開頭已限定只能在 local 模式執行，與正式的 live continuous 路徑互斥，所以不影響正式環境。
- `tests/test_scheduler.py::test_busy_recovery_line_does_not_block_other_lines_or_deadline_finalization` 的斷言完全對應這個行為：Gemini 被 paused，只有 claude／gpt 被呼叫，截止 finalization 產生一筆 complete_local 與一筆 comment，並記錄 `line_error`。

### 1.3 admission 在線鎖內，預算維持原樣
- tier1：`before` 的順序是 `recheck → admit → Budget.reserve`，經由 `forecast_question` 在 `F.Lock` 內執行。tier2：`R.run` 全程持有線鎖，`before_attempt → admit`。這符合「B 在線鎖內預留一般預算」。
- `Budget.reserve` 在 `transaction` 內完成，沒有任何 recovery 專用的配額分支。`pending_tier1=self._protected(...)` 只在 tier2 使用，原始的未完成 tier1 題目仍被計入。重跑沿用一般 tier1 預留，沒有新增配額。
- `Budget.recover` 只釋放「其他 run、尚無 start、也沒有 outcome」的預留，start 帳冊的紀錄一律保持計費。這與「start 已記錄即消耗原槽」一致。
- `admission_outcome=self.budget.outcome` 已接上，`outcome` 具冪等性。

### 1.4 stop／manual resume 寫入者都使用同一個短 ledger transaction
- 我檢查到的寫入點：`scheduler.execute`（`transaction(self.root)`）、`reconcile.run`（`transaction(line_status_log.parent)`）、`advise.line_event`／`propagate_safety`、`practice.propagate_safety`、`practice` 的 `LineStop` 分支，以及 `forecast.main` 的 resume。最後這個是「線鎖 → transaction」順序，與 scheduler 和 recovery 的取鎖順序一致。
- 已核對路徑一致：manual resume 使用 `LINE_STATUS_LOG.parent / forecast.{player}.lock`，scheduler 使用 `root/forecast.{player}.lock`。

### 1.5 wake 判定（先前 BLK5）
- `discovery.waiting()`：一筆舊的 `line_stopped` job，只有在「玩家不在 `stopped`」而且「最後一次 `line_resume.at` ≥ 該 job 的 `finished_at`」時才不算等待。
- `stopped` 包含三類：停用的線、`F.line_stopped` 當前仍成立的線、forecasts 中 `quota_retry_at_utc` 尚未到期的線。所以停用與 quota backoff 仍會阻擋。
- 測試：
  - `test_nonpersistent_line_yield_...` 確實走到新分支：resume 早於 `finished_at` 時不醒，晚於時才醒。
  - `test_later_resume_does_not_override_forecast_quota_wait` 涵蓋 quota。
  - 停用的線：依程式碼檢查確實會被擋（`not enabled[p]` 會進入 `stopped`），但封包內**沒有看到**對應的負向測試，見第 5 節。

### 1.6 部分線路的 finalization
- `finalize` 的條件是 `expired`（已過決策截止）或「兩層都已結算」。卡在 Gemini 的題目會在截止時用其他合格線完成；`sealed` 與 `F.eligible` 會阻止已凍結或過期的題目重開。整合權重與 prompt 都沒有改動。

### 1.7 部署器（B-B1／B-B3）
- `review_bundle` 實際做了這些檢查：
  - 用 `exact_files` 重算 stage 與 base 的差異，必須等於 manifest 的 `files`，而且不允許刪除檔案。
  - approval 綁定 `source_sha256`、`deployment_sha256`、`review_modules_sha256` 與 `source_manifest_sha256`，manifest 與 modules 必須互相一致。
  - `contexts` 不可為空，stage 的 bytes 必須相符。`validate()` 要求正式環境中未變更的 context 必須等於審查時的 digest，已變更的則必須等於 `before`。
  - `inputs`（deploy.py、test_deploy.py、測試結果、capture probe）的 hash 必須相符。
  - 模組集合只能是 `['r3']` 或 `['r3a','r3b']`，而且 claude 與 gemini 都必須對每個模組有記錄。
  - 每份 review 需符合：packet 的 SHA／bytes／結尾標記，且內容含 source manifest hash；started 與 finished 各剛好一筆，綁定 player／model／effort=high／packet_sha；完成的 answer 檔 SHA 與路徑相符。
  - stdout 串流方面：模型集合必須剛好等於正式模型；工具只能是 StructuredOutput 或 finish，不可有 server_tool_use；result 必須成功；`structured_output` 只能有 `answer` 一個欄位，而且要等於 answer.md；usage 必須綁定 stdout SHA 與 packet SHA。
  - 受保護的路徑（adapters、runner、netguard、acceptance、campaign／run_config／quota／submit_mode、cycle plist、prompts）不得變更。
- `apply` 的流程：取得六把鎖 → `require_idle` → 檢查 STOP_SUBMIT 與 live window → 備份 → 停用 cycle timer → 前檢 → 原子複製 → offline 測試 → 再次前檢 → 檢查不可變設定 → 檢查部署後的 hash 與 context → 再跑一次 `review_bundle` → 寫入 `applied.json`。
  - 失敗時回滾到原 bytes，並在 `finally` 中重新 enable 原 timer；回滾本身失敗也一樣會 enable。
  - `apply` 從不啟動恢復服務。
- `activate` 會重新驗證已套用檔案、不可變設定、review 綁定與 context 都沒有漂移，才安裝 plist（`StartInterval=300`，`RunAtLoad=false`）。
- `run.sh` 的 PATH 會優先使用 `launchd/bin`。claude wrapper 會驗證 pin SHA 並設定 `DISABLE_AUTOUPDATER=1`；agy wrapper 會驗證 pin SHA、拒絕 symlink，並設定 `AGY_CLI_DISABLE_AUTO_UPDATE=true`。
- `test_deploy.py` 的負向案例涵蓋：stage／production／review／approval／script／STOP／context／manifest 漂移；缺少模組、實際 REQUEST_CHANGES、截斷；provenance 鏈（packet、finish、重複 start、缺少 finish、usage、model、tool、answer 不符）；替換成無關的 manifest；回滾失敗時仍重新 enable。

## 2. 先前 blocker 的狀態（只限 B）
- **BLK4**：已在程式碼層面解決，並有對應測試（見 1.2）。
- **BLK5**：「後來的 resume」與「quota」都有程式碼和測試證據。「停用」有程式碼證據，但缺少測試（非阻擋）。
- **B-B1／B-B2／B-B3**：本輪提供的原始碼與回應描述一致（見 1.1–1.7）。

## 3. Blockers（R3B）
無。我在本模組範圍內沒有找到會違反合約、可能造成越權呼叫、重複耗槽、預算繞過、錯誤 resume 或未經審查 bytes 被部署的具體缺陷。

## 4. 非阻擋建議（proposals，不影響本次判定）
1. **`recover_interrupted` 的 `except PreflightError` 範圍過寬**：它包住整個 body，不只取鎖。如果 body 內部（例如 `ctx.remove_dir`）拋出 `PreflightError`，會被靜默吞掉，`recovery_finished` 永遠不會寫入，而且之後每次都重複嘗試、沒有任何痕跡。建議只對 `Lock(...)` 取鎖失敗回傳 `[]`。
2. **manual resume 鎖忙時只會印出 traceback**：`main()` 的 resume 分支沒有攔截 `PreflightError`，雖然不會改變狀態，但使用者只看到 traceback。建議改成明確訊息並回傳非 0。
3. **tier1 的 `line_stop` 在線鎖釋放後才寫入**：例外會先離開 `with F.Lock`，`except` 才在 transaction 中 append；tier2 則是在鎖內寫入。目前因為 recovery 只接受明確寫入的 stop，這個空窗只會讓 recovery 延到下一次 tick，不會造成不安全。建議日後統一改在鎖內寫入。
4. **同一個 run 內，`paused` 讓已 resume 的 Gemini 要到下一輪 run 才會被選取**：文件已說明這點。若 continuous run 持續很久，可能壓縮補做時間。建議在部署後的觀測中記錄「resume 到補做 start」的實際延遲。
5. **部署的 `preflight` 要求六組全部 ok 且沒有 waiver**：如果 `F.preflight` 會因為線路停止或 quota 等待而判 not ok，部署會一直被擋（fail closed）。`require_idle(horizon_s=900)` 也可能因為待補題接近截止或有未結的 intent 而阻擋 apply。建議部署前先確認這兩點的實際狀態，以免錯過補做窗口。
6. **`contexts` 沒有涵蓋所有間接依賴**：例如 `src/cycle.py`、`orphan`、`quota`、`budget_handoff`、`flab/config`、`m2_campaign`。這些未列入的依賴如果在正式環境漂移，部署器偵測不到。建議日後加入 `src/` 全樹的 hash 或列出完整依賴清單。
7. **`activate` 與 `apply` 可能相隔很久**：`activate` 沒有重做前檢（line_recovery 本身會做兩次當前前檢，所以不算缺口），可考慮在 `activate` 也加上 STOP_SUBMIT 與 live window 檢查。
8. **`discovery` 中沒有重試時間、線路也已解除的 `line_stopped` job 會一直被視為等待**：只能靠 30 分鐘的完整掃描或截止時間喚醒。這是保守的作法，可接受。

## 5. 證據缺口（無法由本封包驗證；不是 blocker，但不能視為已驗證）
- `src/line_recovery.py` 與 `tests/test_line_recovery.py`（A）、`forecast_question`、`F.run`、`F.preflight`、`F.line_stopped`、`LineStop.outcome` 的完整原始碼都沒有附上。因此有幾點我無法確認：
  - `forecast_question` 內部寫入 `line_stop` 是否也使用 transaction（目前推論為線鎖保護）。
  - 第二次 resume 後，已有 initial 與 rerun 的題目會回傳什麼 outcome，以及能否穩定落入 TERMINAL。
  - A 是否真的只持有 Gemini 線鎖與自身的恢復鎖。
- `src/forecast.py`（`c92749aa…`）只附了片段，我無法確認 manifest 中各 SHA 與我看到的片段完全一致。我的判定是依據所附片段。
- 封包中沒有看到「線路停用時不會被 wake」的負向測試。
- 測試通過數只是 host 端的主張。

## 6. 部署前仍需具備（不是核准）
- R3A 必須由 Claude 與 Gemini 都實際判定 PASS，R3B 也必須有 Gemini 的實際 PASS。四份 review 都要通過 `review_bundle` 的完整來源鏈驗證。
- 本 PASS 只限 R3B，不能代表整個系統核准，也不能取代 Louis 的授權紀錄或部署後的觀測（自動 resume、由排程啟動的補做、3 題待補題的 `forecast_ok`／`comment_ok`）。

END-D140-CODE-R3B