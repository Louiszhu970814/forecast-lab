# D105：持續模式的主站試驗題（M3 端到端的題目來源）

規格：Claude（主持）。實作：GPT（Codex 開發沙箱）。審查：Claude，之後 Gemini 獨立審查。
Louis 於 2026-11-02 桌面對話核准（照建議 A）。**本規格只含離線實作與測試；實際以 `trial` 執行端到端、登記持續窗口與安裝 launchd 另依下方「執行計畫」進行，launchd 安裝時 Louis 須在場（D78）。**

## 背景

- M3 第二項要求以 `trial` 由 launchd 週期無人介入地完成抓題、預測、送出、留言與讀回，是非題與多選題都要有。
- bot testing area 支援的兩題都已用過（是非題 43332：原 V15 probe；多選題 46450：D102），依規則不能再送，因此持續模式在 trial 沒有可預測的題目。
- 主站題目是已核准的試驗場地（D45、`trial_ground_types` 已含 `main_site_questions`），`submit.py` 的 trial 前檢也已支援（`_discovery`／`_gate` 以官方探索取得 site_main 的 ID）。
- 缺口在 `continuous.Poller`：對 `main_site_questions` 會以 site_main 的 project ID 呼叫 `poll()`，等於列出主站全部開放題目。`list_posts` 最多 20 頁×50 筆，主站開放題遠多於此，會丟出 `more than 20 pages`；即使沒有丟錯，也會把上千題排入佇列，耗盡每週額度與 API 請求。
- `m2_prepare.py` 已有安全的有界選題（`main_site_pages`：`main_feed=true`、`forecast_type=binary,multiple_choice`、有頁數上限；`trial_membership_error`、site_main 歸屬、`is_synced_copy`、本地已預測排除、`F.eligible`／`decision_deadline`），本任務把同樣的規則帶進持續模式，並加上每窗口題數上限與穩定選題。

## 要做的改動（`continuous_v7` → `continuous_v8`）

1. **政策欄位**：`venues` 含 `main_site_questions` 時，campaign 必須有
   `"main_site_trial": {"version": "main_site_trial_v1", "max_questions": N, "max_pages": P}`，
   N 為 1～6 的整數，P 為 1～5 的整數。`venues` 不含 `main_site_questions` 時不得有此欄位。`register()` 驗證；版本接受 `continuous_v8`，舊版本 campaign 若含 `main_site_questions` 一律拒絕（舊行為本來就無法運作，不給相容路徑）。rollover 範本同樣適用，每個週窗口各自計算 N。
2. **共用有界列表**：把 `m2_prepare.main_site_pages` 移到共用位置（例如 `poll_questions.py`），加上 `max_pages` 參數；`m2_prepare.py` 改為引用，**行為與頁數上限（20）不變**，既有測試保持通過。
3. **選題帳冊**：新增追加式 `data/main_site_trial_selections.jsonl`，在 `transaction(root)` 內讀寫。
   - `main_site_trial_selected`：`campaign_id`（窗口 ID）、`question_id`、`post_id`、`question_type`、`decision_deadline`、`selected_at`、`version`。
   - `main_site_trial_dropped`：`campaign_id`、`question_id`、`reason`、`at`。
   - 只存 ID、型別、時間與原因；不存題文或描述。
4. **Poller 對 `main_site_questions` 的流程**（只限 local／trial；live 已因非白名單而拒絕，保持不變）：
   - 讀取本窗口已選題目。數量 < N 時才掃描候選：以共用列表讀最多 P 頁。數量 = N 時不掃描，只對已選題目逐題 `get_post` 重新核對。
   - 候選與已選題目都要通過：`trial_membership_error(post, bot_testing_area_id)` 為 None（任何 bot testing area 以外的 tournament 都拒絕，含 Cup、主賽、MiniBench）；`snapshot_from_post` 為單一題目；題型為 binary 或 multiple_choice；site_main ID 在 `project_ids` 中；`is_synced_copy(snap, index)` 為 None（index 沿用 Poller 現有的排除索引）；`F.eligible(snap, run_config, now)` 為 None 且 `F.decision_deadline` 不為 None；`open_time` 存在且已到。
   - 候選另外排除：本地任何紀錄過的題目（`forecasts.jsonl`、`forecast_attempt_starts.jsonl`、`campaigns.jsonl` 的題目、`submissions.jsonl`、v15 probe 保留，以及本帳冊所有窗口已選或已作廢的題目）。若列表資料可判斷本帳號已有預測，也排除；不能判斷時由送出端既有的平台檢查把關。
   - 新選題順序：先確保題型涵蓋——若本窗口還沒有 binary，選決策截止最早的 binary；multiple_choice 同理；之後依（決策截止, question_id）由早到晚補到 N。
   - 已選題目若不再符合（關閉、歸屬改變、成為同步題、過了決策截止），寫 `main_site_trial_dropped` 並從回傳結果移除；**同一窗口不補位**，避免無限替換。
   - 只對已選題目呼叫 `record_snapshots`（`tournament_id` 設為 site_main ID，與 `m2_prepare` 相同），其餘候選只在 `poll_log.jsonl` 寫一筆摘要（頁數、筆數、各原因的計數），不寫題文。
   - 回傳的題目與現行格式相同：`{**snap, "status": "open", "ground": "main_site_questions", "priority": "main"}`。
   - 帳冊寫入與選題要能重入：同一窗口重複執行、或程序在寫入一半時中斷再重啟，都不能超過 N，也不能把同一題選兩次。
5. **其他場地不變**：`bot_testing_area`、`main`、`minibench` 的輪詢、live 的 MiniBench 輪次探索、排除索引、`MEMBERSHIP_UNKNOWN` 的處理全部不變。
6. `continuous.VERSION` 改為 `continuous_v8`；`cycle.py` 若有版本相依的檢查一併更新。`launchd/README.md` 補一段 `main_site_trial` 欄位說明。

## 不能做的事

- 不改 `submit.py`、`submit_transport.py`、`submit_contract.py` 的送出／讀回／退避規則與 POST 上限。
- 不改 prompt、模型參數、整合公式、預算規則、熔斷與 `quota_policy.json`。
- 不修改 `config/`（不建立 `cycle_campaign.json`、不改 `submit_mode.json`）、`PLAN.md` 正文、`AGENTS.md`。
- 不呼叫網路、不安裝或載入 launchd。

## 測試（離線、合成 client，沿用 `tests/test_cycle.py` 的 Poller fixture 風格）

- `register()`：含 `main_site_questions` 但缺 `main_site_trial`、N 或 P 超出範圍或型別錯誤、版本不是 v8、不含主站卻帶 `main_site_trial` → 拒絕；合法設定通過；rollover 範本同樣驗證。
- 選題：混合 binary／multiple_choice／數值題、tournament 成員題（Cup、主賽、MiniBench）、同步題、已在本地預測過的題、未開題、過了決策截止的題 → 只選合格的；N=4 且兩種題型都有時，至少各 1 題；題型只有一種時照順序補滿。
- 上限與穩定性：重複呼叫 Poller 不超過 N、不重選；已選滿時不呼叫列表端點（以合成 client 記錄呼叫驗證），只對已選題目 `get_post`。
- 作廢：已選題目關閉或變成 tournament 成員 → 寫 dropped、從結果移除、不補位。
- 重入：模擬寫入第 k 筆後中斷，重啟後總數仍 ≤ N 且無重複。
- 頁數：列表超過 P 頁時只讀 P 頁、不丟錯；分頁網址異常時拒絕（沿用 `main_site_pages` 的檢查）。
- 隔離：`poll_log.jsonl` 與選題帳冊不含題文；`questions.jsonl` 只新增已選題目的快照。
- `m2_prepare` 既有測試與 bot testing area 的 Poller 測試全部保持通過。

完成後執行相關測試（`tests/test_cycle.py`、`tests/test_poll_questions.py`、與 m2_prepare／continuous 相關的測試）與完整測試，回報實際結果與限制；沙箱目錄限制造成的失敗請分開列出。

## 執行計畫（審查通過並提交之後；不屬於 GPT 的任務）

1. Claude 在 local 模式以唯讀方式預檢主站候選數量（只有 GET）。
2. 準備 `config/cycle_campaign.json`：`continuous_v8`、`venues: ["main_site_questions"]`、`main_site_trial` N=4（兩種題型各至少 1 題）、P≤5；預算窗口設短（約 2 天，避免占住之後正式窗口的時段）；每線次數上限不高於 D101；`expected_new_questions`、`repair_reserve`、`urgent_slack_s` 事先固定。登記前把設定列給 Louis。
3. 切到 `trial`（bot 311763，D78）。Louis 在場時安裝 `org.forecast-lab.cycle`，由 Louis 核准權限提示。
4. 驗收看 `cycles.jsonl`、送出與讀回帳冊（只看 ID、狀態、計數），不讀開放題目的預測內容。是非題與多選題各至少 1 題完成預測、送出、私人留言、讀回 found，才算第二項通過。
5. 結束或遇異常時：建立 `config/STOP_SUBMIT` 或切回 `local`，移除 launchd 週期，記錄原因；unknown 只對帳不重送。

## 實作與審查（2026-11-02）

- GPT（`gpt-6-astra` high）在開發沙箱完成實作，任務 `claudedev_1793623284147_c86b2c29`；沙箱內完整 1748 passed，62 個失敗都是沙箱目錄限制。
- Claude 審查改兩處：
  1. 主站候選的 project 歸屬無法辨識（`MEMBERSHIP_UNKNOWN`）時，原本整個 Poller 失敗；改為跳過該候選並計入原因。已選題目若變成無法辨識，仍照原設計作廢。唯讀預檢證實必要：主站前 5 頁 250 題中有 154 題屬此情況。
  2. 新選題的決策截止至少在 150 分鐘以後（`MIN_SELECTION_LEAD`，比照比賽 3 小時窗口減 20＋5 分鐘送出餘裕），避免選到來不及跑完的題目；已選題目的刷新不受此限。相關測試依此調整並補 2 個測試。
- 主專案完整 **1813 passed**。Gemini（Argon high）獨立審查 PASS，沒有發現。
- 唯讀預檢（UTC 13:2x，只有 GET，紀錄寫在暫存目錄）：主站前 5 頁 250 題，合格是非題 40、多選題 7；跳過原因為歸屬無法辨識 154、不屬於主站 23、屬於 bot testing area 以外的 tournament 26。
