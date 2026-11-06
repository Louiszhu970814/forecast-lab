## 結論：REQUEST_CHANGES

審查範圍：我只依據本次提供的 D139 原始資料包，包括完整差異、補充程式脈絡、部署腳本、測試紀錄、replay 證據及 SHA 清單。我沒有執行任何測試，也沒有讀取資料包以外的檔案、連網或做即時預測。

本結論不構成核准。因為結論是 REQUEST_CHANGES，`gate_and_deploy.py` 照設計應拒絕部署。D137 主賽和 MiniBench 應照原狀繼續運作，直到修正並重新完成雙審。

資料包最後的結束標記 `END-D139-R1` 已確認存在，內容完整到這個標記為止。

---

### 一、阻擋項（部署前必須修正）

**B1. `src/scheduler.py` `Scheduler.jobs()`：Market Pulse 第 1 層可能搶在已可執行的主賽／MiniBench 第 2 層之前執行**
- 觸發條件：
  - 某道主賽或 MiniBench 題的第 1 層還沒完成，所以 `high_first=True`。
  - 但這道題剩下的第 1 層工作屬於另一條正在執行（active）的線。例如 Claude 正在跑主賽 A 的第 1 層，Gemini 的 A 第 1 層已完成。
  - 此時 `high = [first 中的非 market 工作]`，對 Gemini 來說是空的。
  - `elif any(... for j in first)` 也是 false。這個分支在 `high` 為空時永遠不可能成立，等於死碼。
  - 結果走到 `candidates = first`，裡面有 Market 第 1 層。
  - 於是 Gemini 會先跑最長約一個第 1 層 timeout 的 Market 工作，同時另一道主賽 B 已經符合條件的第 2 層被晾著。
- 影響：
  - 程式註解寫「market tier1 cannot starve an already eligible high-priority tier2 job」，PLAN 和資料包也宣稱「主賽／MiniBench 兩層都優先」。實際行為和這兩處不一致。
  - 第 2 層有可能因此錯過 decision deadline。
  - 新增的 `test_main_and_mini_both_tiers_precede_market_first_layer` 只測了 `order_jobs()` 的排序，沒有測 `jobs()` 的候選篩選，所以這個缺口沒被抓到。
- 修正：
  - 只要 first 或 second 中還有任何非 market 工作（包括因「第 1 層優先」暫時不能開的第 2 層），就不得把 Market 工作列入候選。
  - Market 只在兩層都沒有任何非 market 候選時才開放。
  - 刪除死碼分支。
  - 補一個 `jobs()` 層級的合成測試，涵蓋上述情境，至少要有「一條線 active、另一題第 2 層可執行、Market 第 1 層存在」。

**B2. `src/discovery.py` `record()`：campaign 第一次完整搜尋時，必然誤判為「首次發現」，且會不可逆地把間隔縮到 300 秒**
- 觸發條件：
  - `old_ids` 只取同一個 `campaign_sha256` 的 discovery 紀錄。D139 是新 campaign，第一次掃描時 `old_ids` 是空的。
  - 因此部署當下已經開放、而且 D137 早就知道的主賽／MiniBench 題，都會被當成 `first_seen_in_campaign=True`。
  - 只要其中任何一題剩不到約 84 分鐘就截止（`close-now-1800 < 3240`），就會加上 `first_seen_decision_slack_below_research_bound`。
- 這不是罕見情況：
  - 歷史稽核顯示 MiniBench 4 天約 41 題，每題窗口 3 小時。
  - 部署當下有一題剩不到 84 分鐘的機率很高。
- 影響：
  - 縮短後「只縮短、不自動回升」，所以 Louis 指定的「30 分鐘」新題搜尋，從部署那一刻起就可能因一個人為誤判，在整個 campaign 期間永久變成 5 分鐘。
  - 跟這組誤判一起寫下的 `observed_open_delay_s` 也沒有意義（量到的是部署時間，不是發現延遲）。
  - 這不是安全事故，但核心交付（第 1 步）的政策會被偷換，而且無法撤回。
- 修正：
  - 「首次發現」改用 `questions.jsonl` 的既有 `t0` 或 `snapshot` 紀錄判定，與 campaign 無關。
  - 或者：凡是在本 campaign `budget_window_start` 之前就已記錄的題，一律不觸發 slack 規則。
  - 補測試：既有題在新 campaign 第一次掃描時，不得觸發縮短。

**B3. `src/continuous.py` `Poller._refresh_known()`：刷新已知題時，沒有重新套用完整搜尋的排除與去重規則**
- 觸發條件：
  - `poll()` 在檢查 `is_synced_copy`、`seen_question_ids` 之前就先寫入 `timing`。所以 `rec['timing']` 也包含被 `_poll_all` 刻意跳過的題（同步副本、已由其他場地輪詢到的題）。
  - `discovery.record()` 把整份 `timing` 都存成 `known`。
  - `_refresh_known()` 會對 `known` 中所有 open 的題重新 GET，然後直接放進 `selected`，沒有排除檢查，也沒有依 qid 去重。
- 影響：
  - 兩次完整搜尋之間（最長 30 分鐘或 5 分鐘），原本被排除的題會變成可作答。到下一次完整搜尋又被排除，形成出現、消失的循環。
  - 同一個 qid 若以兩個 role 或 tournament_id 出現在 `known` 中，後處理的那筆會覆寫 `ground` 和 `priority`，並把 `snap['tournament_id']` 改成另一個 project。
  - `record_snapshots` 會因此判定 `mapping_changed` 而產生 revision。每次刷新來回翻轉，會造成 `requeued`／`blocked_revision`，以及 recheck 時出現「question snapshot revised」的中止。
  - 現有的 `test_full_scan_only_on_cadence_known_refresh_detects_revisions` 只有一個場地、一道題，沒有涵蓋這種情況。
- 修正：
  - `known` 只記錄 `_poll_all` 實際接受的題；或保存 skip reason，並在刷新時排除。
  - 依 qid 去重，以第一個場地為準。
  - 刷新路徑同樣檢查 `is_synced_copy`。如果完整排除索引無法在刷新時取得，就只允許先前已被接受的 qid。
  - 補「被排除的題在刷新時不得進入 `selected`」以及「重複 qid 不翻轉 mapping」的測試。

**B4. `src/human_advice.py` `exclusion_index()`／`bot_priority()`：PLAN 寫的「遇到 Market Pulse open／imminent 要避讓」實際上沒有實作，測試以合成資料掩蓋了這件事**
- 觸發條件：
  - `exclusion_index()` 對 Market 的 project 呼叫 `poll(..., metadata_only=True)` 時沒有傳 `allow_groups=True`。
  - `snapshots_from_post` 遇到 group post 回傳 `[]`，記成「not a single-question post」，不會進入 `timing`。
  - Market Pulse 是 12 個題組、69 個子題，所以 `bot_priority()` 實際上看不到任何 Market 子題。
  - `test_main_minibench_market_open_and_imminent_yield` 直接餵入合成的 records，沒有經過這條實際路徑。
- 影響：
  - PLAN §8 的 D139 段落和任務單第 5 點宣稱的優先序與實際行為不符。PLAN 是唯一規格來源。
  - Market 子題也沒有進入 `is_synced_copy` 的重疊檢查索引。
- 注意：天真修法會造成另一個問題。如果只改成 `allow_groups=True`，Market 子題會開放數週，還有因額度或型別不支援而永遠不會作答的子題，人類研究入口就幾乎永久無法使用，第 2 步等於失效。
- 修正：先明確選定政策，再讓程式碼和 PLAN 一致。
  - 做法 (a)：只對「仍有可准入 bot 工作」或「即將截止（horizon 內）」的 Market 子題避讓。
  - 做法 (b)：PLAN 改為「人類研究對 Market 只靠六鎖與共用額度保護，不做 open 避讓」。
  - 兩種做法都需要補走實際 `poll()` 路徑的測試，而且題組要納入重疊索引。

---

### 二、重要改進（不阻擋，強烈建議處理或明確記錄）

1. **「便宜的 60 秒 heartbeat」在本窗口實際上會變成每分鐘一次完整 worker**（`discovery.needs_cycle`）
   - 只要 `known` 中有任何 open、尚未 `comment_ok` 或 sealed 的題，就會回傳 True。會長期符合這條件的有：
     - Market 子題：開放數週，而且只在有剩餘容量時才作答。
     - 不支援型別的題（`timing` 也包含這些題）。
     - 因額度被拒的題。
   - 結果是幾乎每 60 秒就跑一次完整 cycle：三線 preflight、逐一 GET 已知 post、`auto_reaccept` tick，以及寫入 `live_schedule.jsonl`、`cycles`／`cycle_progress` 紀錄。
   - 這不違反安全性，但 API 讀取、preflight 和資料成長大約是原本 600 秒排程的 10 倍，也和「無工作不開 worker」的說法不符（D138 才剛清完 data）。
   - 建議：
     - 排除不支援、已排除、因政策無法作答的題。
     - 截止還很遠的 Market 子題，只在 discovery 週期刷新，或設最小刷新間隔。
     - heartbeat 的 no-op 不寫 live_schedule。
2. **`kickstart_reviewed.py` 沒有重入能力**
   - 新 plist 寫入後，`assert digest(path)==paused[...]` 和 `records[-1]=='activation_complete'` 都會失敗。
   - 如果 `bootstrap` 或 `enable` 失敗，就無法安全重跑；此時 cycle 已被 disable 和 bootout，正式主賽／MiniBench 會停擺（fail-closed）。
   - 建議：同時接受「舊 paused digest」或「已渲染的新 digest」，並允許在 `schedule_plist_installed` 之後重入；或在任務單寫明人工恢復步驟。
3. **登記前失敗回復原來源後，舊排程仍保持 disabled**，主賽和 MiniBench 會停止，直到有人介入。這不違反安全，但應在 D139 任務單明確寫出這個狀態，以及應走的同一道 gate 恢復流程。
4. **新增的 `statuses=['open','upcoming']` 讓主賽／MiniBench 的 upcoming 快照也寫入 `questions.jsonl`**
   - 題目從 upcoming 轉成 open 時會成為 revision，並保留較早的 `t0`。
   - 請確認洩漏稽核、ITT 或任何以 `t0` 為界的邏輯，不會因 `t0` 提前而改變判讀（例如把 t0 到作答之間的來源誤判為事後資訊）。
5. **小問題**：
   - `discovery.record` 寫死 1800 秒決策邊界，建議由 `run_config` 的 deadlines 推導。
   - `submit._precheck` 寫死 `33131`，建議改用 `MP.PROJECT_ID`。
   - heartbeat 用 exit code 3 當哨兵值，有和其他退出碼衝突的風險。
   - 證據命名 r1／r3 混用：`validation-r1` 搭配 `full-tests-r3`，建議統一。
6. **部署 smoke 清單沒有包含 `test_group_closure.py`、`test_cycle.py`**。另外沒有測試涵蓋 `cycle.main --heartbeat` 回傳 3 加上 `run.sh` 轉成 0 的路徑。
7. **策略觀察（不要求在本窗口變更）**：Spot score 是以子題截止時的預測計分，「一有餘裕就先作答、0 次更新」對截止還有數週的子題通常是較差的時機。現有文件已如實揭露這是額度策略、不是官方豁免，這點正確。若下個窗口要調整，應在看到任何 Market 輸出之前事先固定，不能依開放題結果調參。

---

### 三、已核對、未發現問題的部分

以下是讀程式碼核對的結果，不代表我執行過測試：

- **`budget_handoff_v3`（`expansion_ancestors`）**
  - 鏈結為 D139 → D137(v2) → D118(v1) → D105。
  - `chain[-1]` 的起點等於 `accounting_start`（2026-11-02T13:03Z）。
  - end 以字串完全相等比對；caps、run_config／quota hash、`expected_new_questions`、`repair_reserve`、`urgent_slack_s` 都保持不變。
  - venues 限定為原有兩個再加上 `market_pulse`；有唯一後繼檢查。
  - 第四段會被拒絕：v3 只接受 v10 來源；v2 往前追到 v3 時，因 `seen` 非空而拒絕。
  - 舊 v1／v2 的分支沒有被放寬。
  - `accounting_start` 對 v11 要求與登記紀錄的 hash 一致；D137 被取代後會因 `superseded` 而拒絕。
- **`live_window`**：campaign hash、起訖時間、七天上限的綁定都沒有改。
- **題組映射（`question_mapping`）**
  - 重複 ID、缺少子題、混合結構、錯誤的父 ID 或 group 都會拒絕。
  - 子題的狀態和時程具權威性。
  - `submit` precheck 會比對 `group_id`／`post_type`。
  - readback 只看選定子題的 `my_forecasts`，group 不符就回傳 unknown。
  - resolve 和 post_close_audit 用同一套映射。
  - 題組送出限定 live 和 33131。
  - 留言放在父 post、內文寫明子題 ID，hash 依子題區分。
- **`advisory_v2`**：使用量欄位與 practice 的固定 schema 分開；舊 v1 兩題的上限照舊；同一 request 每線一次；跨批重複必須附更新理由；unknown 必須先恢復。
- **部署 `apply_reviewed.py`**
  - 有六鎖、idle 檢查；PID 仍在時拒絕 bootout；先 disable 再 bootout 才改檔；先切 local。
  - 登記前、後各有 smoke 和 preflight；登記後不回滾帳冊；未知帳冊狀態就停止。
  - 恢復時要求同一份 gate hash，且只允許 initial、target 或 local 三種狀態。
  - kickstart 不使用 `-k`，並拒絕最佳化模式（-O）的 Python。

### 四、實際證據與聲明的區分

- **資料包附帶、我沒有自行驗證的紀錄**：full 3436 passed、deployment fault 28 passed、group closure 4 passed。collection delta 從 3395 到 3436，沒有移除任何測試。
- **budget replay**：只是 metadata 投影；六組 player／tier 前後相同（例如 Claude 28/130），不是官方分數證據。
- **cadence replay**：是合成算式，資料本身也聲明不是排程模擬或覆蓋率實證。
- 部署相關的變更測試都是 fake；launchctl 目前只做過唯讀。
- 沒有任何真實 Market 預測、POST 或 `forecast_ok` 證據，也不應宣稱已參賽或已送出。
- 歷史 M3／D114 與 ITT 的缺口仍然存在，這次測試沒有補上。

### 五、重審條件

修正 B1 到 B4（含各自的測試），重新產生 patch、manifest 和驗證紀錄，再由 Claude 和 Gemini 以官方 CLI 對新一輪資料包重新審查。

END-D139-R1 已確認。