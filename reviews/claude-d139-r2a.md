## 結論：PASS

**範圍聲明**：這次只審 D139 r2 的 **A 模組**，涵蓋完整的變更檔 diff，以及 `exclusion_metadata`、`poll`、`recover_accounting`、`recover`、`advise.main` 的路由內容。所有資料都以 source-manifest `1602732ef71bc3778d308c8aa95739195c8ed4d25c3c809063862ae51f99ea2f` 的封包為準。我沒有執行任何測試，也沒有看封包以外的程式碼。本 PASS 只代表 A 模組的審查意見：
- 不代表 B 模組通過；
- 不代表 Gemini 的審查結果；
- 不構成部署核准。

依封包規則，部署需要 Claude、Gemini 兩方對 A、B 兩個模組都給出 PASS，共四份實際答案，各自有 END 確認，並綁定同一份 manifest。

---

### 一、證據分類

**實際證據（封包內提供，但不是我執行的）**
- 主機上兩次完整測試：3454 passed。另有 29 passed 與 18 passed 兩個子集。
- 測試收集數 3395 增為 3454，`removed: []`。
- Budget replay（只有 metadata）：六組「選手×層級」的 before/after 完全相同。
  - 已用次數：claude 28、gemini 27、gpt 26。
  - 上限：130／200／130。
  - 各組 tier2 的 protected 都是 12。
  - accounting_start 都是 `2026-11-02T13:03:00.000Z`。
  - 真實模型預測 0 次，POST 0 次。
- 受審目標檔與證據檔的 SHA256 清單。

**宣稱／提案（尚非實際結果）**
- D139-EXPANSION.md 的部署流程、30 分鐘排程的實際效果、Market Pulse 實際參賽，目前都只是計畫。文件已正確標為「尚未部署」，也沒有宣稱已有 forecast_ok 或已送出。
- 封包內沒有任何 live forecast 內容，也沒有 Market 預測或 POST 的宣稱。

---

### 二、共享不變量核對

| 不變量 | 程式碼依據 | 判定 |
|---|---|---|
| 上限 130／200／130 不變 | `expansion_ancestors` 要求以下欄位與 D137 來源完全相同：`weekly_attempt_caps`、`run_config_sha256`、`quota_policy_sha256`、`expected_new_questions`、`repair_reserve`、`urgent_slack_s`；replay 顯示上限不變 | 符合 |
| 截止 2026-11-10T04:00Z | v3 要求 `budget_window_end` 與舊值相同，且新起點落在舊窗口內；設定檔 end 值不變 | 符合 |
| 原根用量 | `accounting_start` 必須等於鏈尾 D105 的 `budget_window_start`；鏈長固定為 3（D137→D118→D105）；測試涵蓋三段用量全部計入、D137 被取代後不能再預留 | 符合 |
| 不得有第四段、分叉、rollover 或換根 | v3 若帶 `seen` 即拒絕；有 successors 檢查；rollover／series 相關鍵拒絕；來源必須是 v10＋handoff v2；十種變異的測試確認登記檔位元組不變 | 符合 |
| 模型、prompt、統計參數不變 | 兩個固定設定 hash 沒有變；`d121_protected_hashes` 只改了 `cycle_campaign.json` | 符合 |
| 人類賽不自動提交 | `human_advice` 沒有 submit transport，`automatic_submission: false`，研究結果寫在獨立 batch | 符合 |
| D76 選 bot、D137 正式設定在部署前不動 | 只改候選檔；launchd 與正式切換屬於 B 的範圍 | A 端符合 |

---

### 三、逐項審查

**1. `discovery_v1`（新題搜尋節奏）**
- `validate`：只接受精確的 `SETTING`，所以不能偷偷改成 60 分鐘（有測試）。continuous_v11 必須帶 discovery 設定。
- `due` 在以下情況回傳 True：
  - 沒有任何紀錄；
  - 時鐘倒退；
  - 已到 `next_discovery_at`；
  - 已知的 upcoming 題目已到開題時間。

  邏輯正確。
- `record` 只在完整搜尋成功後才追加紀錄，所以失敗不會推進時鐘（有測試）。
- 縮短節奏的原因會沿用、不會自動回升；「首次發現」以跨 campaign 歷史加上搜尋前已有的 snapshot 判定，D137 既有題目不會被誤標為新題（有測試）。
- `policy_hash` 排除執行時的 questions，Poller 也複製政策並去掉 questions，所以 discovery 身分不會因每輪題目不同而漂移。
- known 只保留被接受的題目；同一題出現在多個場地時，依 main → minibench → market_pulse 的順序先到者為準。

**2. Heartbeat 與 launchd 包裝**
- `cycle --heartbeat`：`needs_cycle` 為假時回傳 3，而且不寫 `live_schedule`。
- `run.sh` 把 3 轉成 0，並跳過 `auto_reaccept`（有子程序測試）。
- `else` 分支中的 `$?` 取的是條件指令的退出碼，符合 POSIX 語意。

**3. `_refresh_known`（已知題刷新）**
- 只 GET 已知、未凍結、狀態為 open、且未到排定截止的 post；同一個 post 只取一次。
- 以 `snapshots_from_post` 重新映射；子題對不上或專案歸屬改變時一律 fail-closed。
- 測試確認：同一 qid 不能被翻轉到另一個專案，upcoming 題目不會在刷新時被抓取。

**4. `question_mapping_v1`（父 post／group／子題映射）**
- 結構必須恰好只有一種（`question` 或 `group_of_questions`）。
- 以下情況拒絕：子題 ID 無效或重複、子題的 `post_id` 與父 post 不符、group ID 無效。
- 共用與子題專屬的 description／resolution／fine_print 會合併。
- 狀態與時程只取自選定的子題。
- 選定子題只保留自己的 own-history，snapshot 不含社群預測（測試確認快照中沒有 `my_forecasts`／`forecast_values`）。
- conditional 結構仍不支援。
- 單題 post 原樣返回；`content_hash` 計算方式不變，所以舊單題不會產生假的 revision。

**5. Submit／readback／comment**
- precheck：group 題只限 live 模式且 tournament 為 33131，並在 live 時驗證 Market 政策與身分。
- 比對鍵新增 `group_id`、`post_type`。
- readback 先選定子題，group ID 不符時回傳 unknown。
- 測試確認：兄弟題的既有預測不會讓選定子題被誤判為已找到；已存在的 baseline 會回傳 unknown；重啟後只寫入一次（forecast＋comment 共 2 筆寫入，留言含 `Question 7:`）。

**6. Resolve／post_close_audit**
- 依 snapshot 的 `post_type` 選定子題並驗證 group ID；不符時分別記錄 `GroupQuestionMismatch` 或 `group_mapping_unverified`。
- 測試確認：選定子題已結算、但兄弟題與父 post 仍 open 時，能正確結算；關題審查閘門只看選定子題。

**7. Scheduler 與 budget 優先序**
- 排序 rank：Market 為 4＋tier，所以主賽與 MiniBench 的兩層都排在 Market 之前（有測試）。
- 只要還有高優先的首層工作未完成，Market 就拿不到候選工作。
- Market 首層未完成時，已可執行的高優先 tier2 仍可先跑（有測試）。
- `low_priority_protected` 只用於 Market，計算為 `2×expected_new + repair_reserve + 2×高優先題數`。高優先題的升級不替 Market 預留（`_protected` 會排除 market）。
- `max()` 的寫法不改變原 tier1 與 tier2 的保留量，與 replay 完全一致的結果相符。

**8. 人類賽入口 `advisory_v2`**
- 人類賽清單獨立存放；初始只有 33108；與 bot 的 main／minibench／market 若有重疊會拒絕。
- `inventory` 不呼叫模型、不提交，篇／組／子題分開計數，附 UTC 與台北時間，`human_submission` 一律標為 unknown（有測試）。
- `research` 的保護：
  - 全程持有六把鎖；
  - 核對固定設定 hash 與預算窗口；
  - 驗證預算接續鏈；
  - 任何未恢復的 advisory 或 bot attempt 都會擋下；
  - 檢查 STOP_SUBMIT、三線 preflight、停線、quota backoff。
- 每個 request 三線各最多一次，跨 batch 重複需要更新理由（有測試）。
- 研究前會 recheck campaign 與題目內容是否改變。
- 避讓 bot：
  - 主賽與 MiniBench 有 open 或即將開題的工作就讓路；
  - Market 只在「本地可執行」時讓路；所有線都在等額度時，人類研究可以使用受 22 次保留限制的容量（有測試）。

  這修正了 r1 提出的 Market 長期開放會擋住人類研究的政策問題，且與 PLAN／D139 的文字一致。
- v2 admission 欄位集合是精確的 14 個鍵，與寫入紀錄相符。
- **r1 前提更正已確認**：`advise.recover` 會分派到各選手的子目錄 `root/p`；`recover_accounting` 以 `batch_path` 篩選。這和人類 admission 寫入的 line root 一致（有測試）。另外，metadata_only 模式確實先走 `exclusion_metadata`，不需要 `allow_groups`。

---

### 四、阻擋項（Blockers）

A 模組沒有阻擋項。我沒有發現會違反以下任何一項的缺陷：上限、截止、根用量、單次提交、主賽與 MiniBench 優先、人類不自動提交、不改模型與參數。

---

### 五、建議改進（非阻擋，建議在部署前或下一版處理）

1. **（P1）已知題刷新失敗會卡住最多一個搜尋間隔。**
   - 問題：已知 post 被刪除或子題消失時，`_refresh_known` 會拋出 PreflightError（mapping 或 membership 改變），而 `due()` 只看時間。
   - 影響：每分鐘 heartbeat 都會失敗，主賽與 MiniBench 會被停住最多 1800 秒，直到下次完整搜尋才把這筆過時資料剔除。
   - 建議：刷新出現結構性錯誤時，強制把下一次判為 due，立即做完整搜尋。成功時鐘仍不前移。

2. **（P1）Heartbeat 例外時沒有往「喚醒」方向失敗。**
   - 問題：`needs_cycle` 和 `actionable_questions` 內有直接取鍵（例如 `r['attempt_id']`）以及設定或停線查詢；一旦拋出例外，就不會啟動 worker，連完整搜尋與未知送出的對帳都停擺。
   - 這和文件「unknown records fail toward waking」的說法只部分相符。
   - 建議：例外時回傳 True（喚醒，交由 worker 內的完整守門處理），或至少留下可監控的紀錄。

3. **（P2）Snapshot 改版後的 comment_ok 題會每分鐘喚醒。**
   - 問題：`actionable_questions` 以 snapshot 篩選 `mine`。題目送出 comment_ok 之後若內容改版，新 snapshot 下看不到終態，於是在截止前每分鐘都會喚醒 worker；Market 子題的開放期可能長達數週。
   - 推論：不會造成重送，因為單次提交與凍結機制沿用舊有邏輯；但會讓省資源的 heartbeat 失效。
   - 建議：已提交或已凍結的題目應以 qid 層級判定為已完成。

4. **（P2）upcoming 轉 open 的時序競態。**
   - 問題：開題時間一到就搜尋，但若 API 仍短暫回報 upcoming，新紀錄中該題的 `open_time <= at`，之後不再提早喚醒；該題也已在 old_ids 中，不會觸發縮短節奏。結果可能延到下一個 1800 秒才被發現。
   - 建議：對「open_time 已過但仍是 upcoming」的題目設一個有界的短間隔重查。

5. **（P2）新場地首輪搜尋可能永久縮短節奏。**
   - 部署後第一次完整搜尋時，Market 的子題全部算首次發現；若有子題剩餘決策時間低於 3240 秒，節奏會不可逆地縮到 300 秒。
   - 這符合「只縮不升」的政策，但會增加 API 負載。建議在部署紀錄中明列這個可能性。

6. **（P3）readback 的錯誤型態。**
   - `submit_transport.readback` 中的 `select_question` 遇到結構或子題缺失會拋出 ValueError，而不是回傳 unknown。group ID 不符時卻回傳 unknown，兩者不一致。
   - 建議：統一包裝成 unknown，以維持對帳語意。

7. **（P3）文件格式。**
   - PLAN §15 的 D138 列與 D139 列之間多了一個空行，Markdown 表格會斷開，D139 列無法正確顯示。建議刪除空行。

---

### 六、證據缺口（交由 B 或後續確認，我沒有據此推論任何模組通過）

- 封包內沒有 `human_advice.run()` 的端到端合成測試（含鎖、preflight、平行 `run_line`、aggregate）；`advise.run_line` 被截掉的 eligible 檢查段落我也看不到。
- Market「更新 0 次、每子題最多一筆」依賴既有的凍結與單次提交機制；A 模組裡沒有 Market 專用的強制點，也沒有改版後拒絕重送的測試。
- `submit_mode.json` 的 campaign_sha256 `94f693…` 是否等於新政策的 hash_obj；起點 05:14:00.869Z 早於部署時，登記是否會被接受。
- 結束碼 3 是否與 cycle 其他路徑衝突；`refresh_known` 刻意排除已凍結題後，下游是否仍正確處理；idle 會跳過 `auto_reaccept` 的影響；v11 的完整 cycle 整合測試。以上都屬於 B 模組的整合範圍。
- 依封包規則，本結論不推論 B 或 Gemini 已通過；四份答案、串流與模組 hash 是否齊備，仍由 `gate_and_deploy` 獨立驗證。
END-D139-R2A