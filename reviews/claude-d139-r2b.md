## 結論：PASS

**範圍與證據說明**
- 本 PASS 只涵蓋本模組（B：整合、部署閘門、復原）。它不代表 A 模組通過，也不構成部署核准。依封包規定，部署需要 Claude 與 Gemini 兩個模型對 A、B 都給出 PASS，共四次實際呼叫，每次都有各自的 END 確認，且全部綁定同一份 source manifest（1602732e…99ea2f）。
- 我只讀了封包內的原始碼文字，沒有執行任何測試、指令或模型呼叫，也沒有看到封包以外的程式碼（例如 `continuous.register`、`Scheduler.execute`、`F.eligible`、`run.sh`、`cycle.py`）。文中「主機測試結果」是封包提供的資料，不是我跑出來的。
- 本封包完整到達，結尾 END 標記存在，沒有發現截斷。

---

### 一、阻斷項（Blockers）
**無。** 本模組範圍內沒有找到會造成錯誤上線、可回滾帳本被破壞、繞過審查閘門或超出預算的缺陷。下面幾個項目是「本模組無法驗證，需要 A 確認」，我沒有把它們當成已驗證，也沒有用它們推論 A 會通過。

---

### 二、從原始碼實際核對到的內容（Actual evidence）

**1. 預算交接鏈 D105 → D118 → D137 → D139**
- `ancestors()` 遇到 v3（`budget_handoff_v3`）會走 `expansion_ancestors`；若 `seen` 非空就拒絕。所以 v3 是最後一環，不能再有第四次交接。
- `expansion_ancestors` 要求以下條件：
  - 來源必須是已登記的 `continuous_v10`／v2 D137。
  - 鏈長正好 3 且身分不重複（`[D137, *ancestors(D137)]` = `[D137, D118, D105]`；D118 走 legacy v1 分支，回傳 D105）。
  - `accounting_start` 等於根起點。
  - `budget_window_end` 與 D137 相同。
  - caps、`run_config_sha256`、`quota_policy_sha256`、`expected_new_questions`、`repair_reserve`、`urgent_slack_s` 全部不變。
  - venues 只從 `['main','minibench']` 增加 `market_pulse`。
  - 單一後繼。
- `accounting_start()` 把 `continuous_v11` 納入「執行期必須與登記雜湊一致」的檢查。D139 登記後，D137 會被 `superseded` 拒絕，避免兩個 campaign 同時用預算。
- budget replay 顯示：claude、gemini、gpt 的 tier1/2 前後結果完全一致（cap 130/200/130，used 28/27/26，protected 0/12），`accounting_start` 都是 `2026-11-02T13:03:00.000Z`，`real_model_predictions: 0`，`POST: 0`。
- 結論：bot cap 與根起點的使用方式維持不變。

**2. 上線時窗（live_window）**
- 必須是有限時窗（最長 7 天），campaign 的 id、雜湊、起訖都要與核准一致。run_config 與 quota_policy 的雜湊也要一致，這和「不改模型、提示詞、統計」一致。
- `apply_reviewed.contract()` 在模式仍為 local 時驗證候選 live 合約；`kickstart_reviewed.py` 啟動前會再跑一次 `require_active`。

**3. 部署腳本 apply_reviewed.py**
- 順序：先取得六把鎖並通過 `require_idle(horizon_s=1200)`，接著 `gate_check`、`stop_check`、`idle`，然後暫停排程。暫停時會檢查已安裝的 plist 是 600 秒版本；有 pid 時拒絕 bootout，並在變更前記錄 `print-disabled`／`print` 的實際輸出。
- 檔案切換：先寫入 `mode=local`，才切換程式碼與設定。登記前所有失敗都會還原原始位元組，`MODE` 在其他檔案逐一驗證後才最後還原。登記後的失敗只會停在 local 並等待前滾，帳本不回滾。
- `ledger_status` 只接受「原始前綴 + 零或一筆完全相同的後繼」。帳本狀態不明時記錄 `stopped_unknown_ledger` 並停止，不會回滾。
- 復原路徑要求：同一個 gate 雜湊、`schedule_was_enabled is True`、每個檔案只能是 {初始, 目標, local}、備份未變。
- 測試檔與封包提供的計數一致（我依原始碼數的參數化案例，不是執行結果）：
  - `test_deployment.py` 共 28 個案例，加上 `test_kickstart.py` 1 個，合計 29，對上「29 passed」。
  - `test_split_gate.py` 為 1+5+3+4+4+1=18，對上「18 passed」。
  - 涵蓋登記前／後故障注入、證據竄改、受保護來源漂移、STOP_SUBMIT、六鎖忙碌、還原失敗仍保持 local、暫停失敗不改來源、launchctl 解析器（兩種主機格式、缺省、歧義）、執行權限保留。

**4. 審查閘門 gate_and_deploy.py（r2 分模組）**
- 必須是 `d139_split_review_v1`，模組正好是 `[r2a, r2b]`，並綁定 source 與 input 兩份 manifest 的雜湊。
- 每個封包都要通過：雜湊與位元組數相符、小於 170000、以 `END-…\n` 結尾，且同時包含兩份 manifest（sorted-key JSON）。我比對了本封包內的兩份 manifest，鍵確實已排序，格式與 `json.dumps(..., sort_keys=True)` 相符。
- 每一份答案都要符合：
  - 恰好一個 PASS 標題。
  - 最後一行就是該模組的 END，且全文只出現一次。
  - 恰好一筆 started 與一筆 finished 紀錄；模型與 effort=high 相符；`packet_sha256` 相符；`answer_sha256` 相符。
- 四份答案與四個 stdout 都寫進 evidence，`review_modules` 保留四個 ref。舊的 `reviews` 鍵只對應 A 的答案。
- `apply_reviewed.gate_check` 會逐一比對所有 evidence 的雜湊，所以 B 的答案事後被替換也會被擋下。
- gate 不能被覆寫（`recovery cannot replace gate`）。
- 測試、collection、budget replay 都有檢查：`3454 passed` 出現兩次，collection 從 3395 增為 3454，`removed: []`。

**5. 恢復排程 kickstart_reviewed.py**
- 禁止 `-O`；要求已有 `activation_complete`，且其後只能出現允許的事件類型。所有 gate 檔案雜湊相符、沒有 STOP_SUBMIT、`require_active` 通過。
- 新 plist 從已審查的模板渲染：Label、`StartInterval=60`、`RunAtLoad=false`。已安裝的 plist 只能是暫停時記錄的雜湊或新雜湊，覆寫前先備份。
- 恢復後用同一個解析器確認有明確的 enabled 覆寫；kickstart 不帶 `-k`；bootstrap 失敗可安全重試，備份不會被覆寫。

**6. 審查執行 run_split_reviews.py**
- 每一波都重新取得六鎖並重新做 idle1200 檢查；900 秒的 CLI 逾時小於 1200 秒的 horizon。
- 已存在但未完成或失敗的審查一律不自動重試，啟動輸出檔以 `x` 模式開啟，避免覆寫。

**7. 排程、輪詢、送出的整合**
- `Scheduler.jobs`：主賽與 MiniBench 優先於 Market Pulse。沒有未完成的高優先 tier1 時，已可升級的高優先 tier2 不會被 Market tier1 擠掉。
- `Queue.__call__`：強制刷新失敗時會先讓快取失效，不會沿用舊的安全資料。
- `_precheck`：group 只允許 live 加 Market Pulse，子題映射改變就永久拒絕。比對欄位包含 `group_id` 與 `post_type`。
- readback：group 會核對 `_group_mapping.group_id`，任何讀取失敗都視為 unknown。
- `tournament_index` 使用 `metadata_only` 輪詢。這與 r1 的更正一致：r1「需要 allow_groups」的前提不成立。

---

### 三、本模組無法驗證、需由 A 確認的項目（不推論已通過）
1. **Market Pulse 的預算隔離是否真的接上。** `Budget.reserve` 有 `low_priority_protected`（與 tier 對應的 protected 取 max），但呼叫端 `Scheduler.execute` 不在本封包內。如果 Market tier1 沒有傳入這個保護值，tier1 的保護只剩 `advisory_protected`，Market 可能吃掉之後主賽與 MiniBench 新題的週配額。A 需要確認 diff 中的實際接線與對應測試。
2. **長期開放的 Market 題阻擋人工建議**（真正的政策問題）。屬於 `human_advice` 的行為範圍，本模組只能指出排程端「高優先未完成時 Market 候選為空」的設計，無法判定人工建議端的行為。
3. **`continuous.register()` 對同一 campaign 是否冪等。** 登記後復原（`after_register`/`verified`/`live`）的測試用的是 Fake register（先檢查 `ledger_status`）。如果真實 register 會重複追加，復原會因 `len(own)>1` 進入 `stopped_unknown_ledger`。這是 fail-closed，不會造成安全問題，但會讓前滾卡住。
4. **心跳從 600 秒改為 60 秒、搭配 1800 秒 discovery 的實作**（`run.sh`/`cycle.py`）在 A 的 diff 範圍內。B 只確認 kickstart 端的參數與紀錄是 `heartbeat_s=60`、`discovery_s=1800`。

---

### 四、改進建議（Proposals，非阻斷）
1. `apply_reviewed.gate_check` 可以額外要求 `review_modules` 正好四筆，並再驗證 B 答案的 PASS／END。目前它依賴 gate 只由 `gate_and_deploy` 產生；直接手動執行 apply 時只會檢查兩份 legacy 審查。
2. `gate_and_deploy` 寫 `deployment-gate.json` 時改用 `atomic()`。目前如果寫到一半當掉，下次會因「不能覆寫 gate」而卡住（雖然是 fail-closed）。執行 apply 的子程序也可以加 timeout。
3. 登記前失敗並還原後，D137 排程仍停在 disabled／bootout 狀態，需要人工恢復。建議在 `restored_before_registration` 事件或操作文件中明確寫出「D137 已暫停，需人工恢復」。
4. `_precheck` 把 Market Pulse 的 `tournament_id` 寫死為 33131，建議改為綁定 `config/tournaments.json` 的 `market_pulse` id，避免兩邊不一致時所有 group 送出都被永久拒絕。
5. 只要一題高優先 tier1 還沒完成（例如某個 player 正在執行），閒置的 player 就不能接 Market 工作，Market 題的時間餘裕可能因此變少。這符合 D139 的優先政策，但建議在文件中說明這項吞吐代價。
6. cadence replay 已誠實列出限制：45 分鐘的短窗在 1800 秒下會漏接；timeout 上限下的 10 題批次會漏接 8 題；歷史資料只有 45 個 180 分鐘窗。採用 1800 秒是明確授權的選擇，建議在上線後監測實際觀察到的短窗。
7. 小問題：`test_deployment.py` 的模組名稱仍是 `d137_deploy`；`preflight` 的輸出可以改用 atomic 寫入。

---

### 五、共享不變量（只就本封包可見內容確認）
- bot cap 130/200/130、根起點、截止 2026-11-10T04:00Z：由 `expansion_ancestors` 的保留檢查與 replay 確認。
- 模型、提示詞、統計不變：由 `live_window` 的 run_config／quota_policy 雜湊綁定確認。
- 部署前 D137 生產環境不受影響：基線雜湊檢查、六鎖、idle、先寫 local。
- 沒有 Market 預測或 POST：replay 中 POST 為 0，封包內沒有任何即時預測內容。
- 人工報告仍由人提交：本模組未見相反證據，相關行為留給 A。
- 歷史上的 M3/D114/ITT 限制仍然存在，本模組沒有宣稱解除。

END-D139-R2B