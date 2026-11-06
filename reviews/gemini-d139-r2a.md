## 結論：PASS

### 一、審查範圍與不變量核對（Scope A / r2）

本次審查僅根據提供的 D139 split review r2 來源封包（`source-manifest SHA256: 1602732ef71bc3778d308c8aa95739195c8ed4d25c3c809063862ae51f99ea2f`）進行靜態設計與程式碼審查，未執行任何測試或外部指令，亦未檢視封包外之程式碼，且本結論僅涵蓋模組 A。

經核對共用不變量與跨模組契約：
1. **預算根起點、上限與截止時間保留**：`config/cycle_campaign.json`（`continuous_v11` / `budget_handoff_v3`）與 `src/budget_handoff.py:expansion_ancestors` 強制驗證三段祖先鏈（D105 → D118 → D137 → D139）、保留根記帳起點 `2026-11-02T13:03:00.000Z`、既有週上限 `130/200/130`、固定截止時間 `2026-11-10T04:00:00.000Z`，以及相同的 `run_config_sha256` 與 `quota_policy_sha256`；禁止第四段接續、分叉、重置或展延。
2. **30 分鐘新題搜尋與 60 秒本地心跳分離（`discovery_v1`）**：
   - `launchd/org.forecast-lab.cycle.plist` 改為 60 秒觸發，`launchd/run.sh` 傳入 `--heartbeat`；`src/cycle.py` 在無須喚醒時回傳 exit code `3`，由 `run.sh` 轉為 `0` 結束且不寫入 `live_schedule.jsonl`、不啟動 worker 或維護程序。
   - `src/continuous.py:Poller` 複製並排除執行期 `questions` 欄位以固定 `campaign_sha256`；完整搜尋前先擷取 `previously_known = set(latest_snapshots(self.root/'questions.jsonl'))` 連同 `discovery.jsonl` 歷史判斷 `first_seen`，避免既有跨 campaign 題目誤觸縮短條件。
   - `src/discovery.py:record` 僅在完整搜尋成功後追加紀錄，失敗不推進時鐘；只保留未跳過且首見場地之題目；觀測窗口小於 3600 秒或首見決策餘裕小於 3240 秒時縮至 300 秒且不自動回升。
   - `src/discovery.py:needs_cycle` 與 `actionable_questions` 確保已知題截止決策、待送出 aggregate、未知送出讀回（含關題後無 `next_retry_at` 或已到期之 intent）及未完成 attempt 能於 60 秒心跳喚醒，而處於 `budget_wait` / `quota_*_wait` / `preflight_blocked` / `line_stopped` 且未達 `reevaluate_after` 或 `decision_deadline` 的題目不會每分鐘空轉啟動 worker。
3. **人類賽通用手動入口與長開題避讓修正（`advisory_v2`）**：
   - `config/human_tournaments.json` 與 `src/human_advice.py` 僅納入已核准之 Metaculus Cup Fall 2026（`33108`），與機器人賽事嚴格互斥；`list` 僅盤點不呼叫模型，且 `human_submission` 固定標為 `'unknown'`。
   - `research` 強制六鎖、單次請求三線各最多一筆 Tier 1 初始呼叫、跨批重跑須提供非空 `update_reason`，並透過 `src/advise.py:recover` 逐一走訪 `root / p` 子目錄恢復未決帳務。
   - 針對 r1 指出之 Market Pulse 長開題阻擋人類諮詢問題：`src/human_advice.py:bot_priority` 結合 `discovery.actionable_questions`，當主賽／MiniBench 有任何開放或即將開放題時一律避讓；對 Market Pulse 則僅在其存在本地可執行工作、待發布、已達截止／重試時間或即將開題時避讓。當 Market Pulse 長開題因全線等待額度／預算保留或停線而暫無可執行工作時，不會永久阻擋人類輔助研究，同時仍保留 22 次（`2 * expected_new_questions + repair_reserve`）機器人保護容量。
4. **Market Pulse 有限准入與題組映射（`market_pulse_v1` / `question_mapping_v1`）**：
   - `src/question_mapping.py` 於抓題（`poll_questions.py`）、前檢（`submit.py`）、讀回（`submit_transport.py`）、結算（`resolve.py`）與關題稽核（`post_close_audit.py`）共用一致之父 `post_id`、`group_id` 與子 `question_id` 映射，合併共用與子題描述／條件，並以子題自身狀態與時程為準，且不將社群預測複製進 snapshot。
   - `src/comment.py` 對 `group_of_questions` 自動於留言首行標註 `Question <question_id>: <title>`。
   - `src/scheduler.py` 確保主賽與 MiniBench 的 Tier 1 與 Tier 2 皆優先於 Market Pulse，且高優先題的 Tier 2 計算 `_protected` 時不計入 Market Pulse 未完成之 Tier 1；Market Pulse 呼叫 `budget.reserve` 時透過 `low_priority_protected` 保留 `2 * expected_new_questions + repair_reserve + 2 * 高優先題數` 的容量。

---

### 二、阻擋問題（Blockers）

- **無（None）**。r1 識別之實質政策與邏輯議題已於 r2 完整修復並涵蓋於合成單元測試中。

---

### 三、非阻擋改進建議（Proposals / Improvements）

1. **Upcoming 快照狀態與後續分析標記**：`Poller._poll_all` 在啟用 `discovery` 時會以 `statuses=['open', 'upcoming']` 寫入 `questions.jsonl`，使提前觀測到的 upcoming 題目建立初始 `t0`；雖然執行期 `_poll_all` 與 `_refresh_known` 均已明確覆寫 `'status': 'open'` 且不影響正式研究與送出閘門，未來若進行離線 `questions.jsonl` 歷史分析時，建議持續依 `D139-EXPANSION.md` 所載注意 `t0` 觀測定義在 D139 前後的差異。
2. **`actionable_questions` 未知結果之保守喚醒觀測**：`discovery.actionable_questions` 對未在已知等待集合內的結束狀態採保守喚醒（fail-safe toward waking the worker），部署後可透過日常觀測確認 `discovery.jsonl` 與 `live_schedule.jsonl` 的實際喚醒頻率符合預期。

---

### 四、實際證據紀錄（Actual Evidence from Packet）

- **來源清單綁定**：`reviewed-files-r2.json` SHA256 為 `1602732ef71bc3778d308c8aa95739195c8ed4d25c3c809063862ae51f99ea2f`。
- **程式碼與設定差異證據**：
  - `src/discovery.py`（SHA256: `b851ad87bf0c4d2c8ace66494fd0ea6da5ccee791c64fdd4c6b924d0ee55520e`）
  - `src/continuous.py`（SHA256: `55a8be97c8a60d94211ece271613e6912725007ed615a52350dae98c57d8cc95`）
  - `src/human_advice.py`（SHA256: `d60357fb5bc6a2d628191de82879f3fbbceadc11c093c3e894caa94898cc134c`）
  - `src/question_mapping.py`（SHA256: `c8ff0325999dbef63164ebb189cf5c41a3cdd532ca42572ec177866704fd21b7`）
  - `src/scheduler.py`（SHA256: `05f5a1c003beb1a2a1bfe371d6b660cf804b6c4bca3b857414fbf36f2c06d6f5`）
  - `src/budget.py`（SHA256: `4dafc0aa782a175591fa58c834e5f4a280fbf333ad2393cea0fbb72ad147aae3`）
  - `src/budget_handoff.py`（SHA256: `56603361a2d1e93ba80d0b92e426f1f73f75eb2d8185345e38a08c9d4ad36a63`）
  - `src/submit.py`（SHA256: `d31d0f510642c1f8e2d481a63138af9ff6a5a60ad59c9c43c1b923cb656cb351`）
  - `src/submit_transport.py`（SHA256: `2c5ffd42f2a191807288003c8633ef3a057f1fbb9f0ea0a7417b176834c8faff`）
  - `src/resolve.py`（SHA256: `dcb0c203e61cc64c5d06f9d415292d145900dc2a93b6469436432cf03213e85b`）
  - `src/post_close_audit.py`（SHA256: `f05310674f7ccb61f7a85aa49a70b1bfcf04aed00390d9099d944afe09284b6c`）
- **封包附帶之主機測試與重播摘要（由主機提供，審查者未執行）**：
  - 測試案例數由 3395 增加至 3454（`removed: []`），附帶之主機測試摘要顯示 3454 passed，新增測試涵蓋 `tests/test_discovery_cadence.py`、`tests/test_group_closure.py`、`tests/test_human_advice.py` 與 `tests/test_market_expansion.py`。
  - `budget-replay.json` 顯示轉換前後三線 Tier 1 / Tier 2 之已用次數（28 / 27 / 26）、週上限（130 / 200 / 130）、保護次數與根記帳起點（`2026-11-02T13:03:00.000Z`）完全一致，且 `real_model_predictions: 0`、`POST: 0`。

END-D139-R2A