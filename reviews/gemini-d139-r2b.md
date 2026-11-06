## 結論：PASS

### 1. 檢閱範圍與模組契約聲明（Scope & Cross-Module Contract）
- **檢閱模組**：D139 拆分檢閱 Module B（回合 `r2`，綁定 `source-manifest SHA256: 1602732ef71bc3778d308c8aa95739195c8ed4d25c3c809063862ae51f99ea2f`）。
- **生效範圍聲明**：本結論僅涵蓋本封包（Module B）所提供之排程、輪詢快取、提交預檢與讀回、預算交接與容量保護、部署與恢復腳本、四方檢閱閘門（split review gate）及封包內附之靜態證據。正式部署必須同時具備 Claude 與 Gemini 對 Module A 與 Module B 共四次獨立官方 CLI 呼叫之通過結論與四個獨立結束標記確認；本模組不推論 Module A 或另一模型之檢閱結果，亦不宣稱已親自執行任何測試或檢視封包外之程式碼。

---

### 2. 具體阻斷問題（Concrete Blockers）
- **無（None）**。經逐項核對 Module B 內所有整合脈絡、生命週期與部署閘門程式碼，未發現違反系統安全不變量、資料完整性或可恢復性之阻斷性缺陷。

---

### 3. 關鍵整合與不變量核驗分析（Actual Evidence from Source Packet）

#### 3.1 排程優先順序與隊列快取（`src/scheduler.py`, `src/continuous.py`）
- **高優先級賽場絕對優先（Main / MiniBench Precedence）**：`Scheduler.jobs` 透過 `unfinished_first` 與 `high_first`（`not tier1_done and entry["priority"] != "market_pulse"`）嚴格區隔優先級。當 `main` 或 `minibench` 仍有未完成之 Tier-1 工作時，`high_first` 為真，僅允許排入非 `market_pulse` 之 Tier-1 任務（若無可立即執行之高優先級 Tier-1 候選任務，則 `elif high_first: candidates = []`，防止閒置模型先行啟動 Market Pulse 任務而佔用執行緒或配額）。當高優先級 Tier-1 全部完成後，`high` 清單會優先收集所有非 `market_pulse` 之合格 Tier-2 升級任務，確保 Market Pulse 的 Tier-1 任務不會餓死（starve）已具備升級資格的 `main`/`minibench` Tier-2 任務。
- **並發安全與失敗失效快取（`Queue.__call__`）**：`continuous.py` 使用 `self.lock` 與 `self._refresh_generation`，使排隊等待同一次強制刷新的呼叫端可共享 30 秒內的新鮮結果，避免序列網路掃描阻塞 CLI 配額准入；同時在呼叫 `self.poller()` 前將 `self.cached = None` 清空，確保強制刷新失敗時絕不回退使用過期的安全快取資料。

#### 3.2 提交預檢、群組題型支援與讀回對帳（`src/submit.py`, `src/submit_transport.py`）
- **Market Pulse 群組題型（`group_of_questions`）邊界控制**：`Submitter._precheck` 明確限制 `post_type == 'group_of_questions'` 僅允許在 `intent['mode'] == 'live'` 且 `intent['tournament_id'] == 33131`（Market Pulse）時提交，否則拋出 `PermanentPrecheckError`；並透過 `select_question(post, intent['question_id'], allow_groups=True)` 解析子題映射，若映射變更則轉為永久預檢失敗。
- **排除索引與 r1 釐清確認**：`APITransport.tournament_index` 呼叫 `poll(..., metadata_only=True)` 建立 `TournamentIndex`。如 r1 查核結果所示，`metadata_only` 路徑已於快照轉換前使用 `exclusion_metadata` 處理群組與條件題型，無需額外傳入 `allow_groups`。
- **讀回驗證（`APITransport.readback`）**：預測讀回在 `post_type == 'group_of_questions'` 時啟用 `allow_groups=True`，並額外核對 `post.get('_group_mapping', {}).get('group_id') == intent['snapshot'].get('group_id')`、遠端 `post_id` 與 `question_id` 型別與數值，確保群組子題讀回不會誤認其他子題或損毀映射。

#### 3.3 預算交接、外部諮詢計帳與共享不變量（`src/budget_handoff.py`, `src/budget.py`, `src/flab/external_usage.py`, `src/live_window.py`）
- **三階終端交接鏈（D105 -> D118 -> D137 -> D139）**：`budget_handoff.py` 新增 `EXPANSION_VERSION = "budget_handoff_v3"` 與 `expansion_ancestors`，強制驗證 `policy.get('version') == 'continuous_v11'`、`authorization == 'PLAN D139'`、來源必須為已註冊之 `continuous_v10`（D137），且祖先鏈長度精確為 3（若對 v3 再次交接則因 `seen` 非空拋出 `v3 is terminal; fourth handoff is forbidden`）。
- **核心上限與時間窗不變量**：`expansion_ancestors` 強制 `accounting_start` 保留根源起算點（對應封包預算重放證據之 `2026-11-02T13:03:00.000Z`），`budget_window_end` 必須與 D137 完全一致（保留 `2026-11-10T04:00Z` 截止時間），且 `weekly_attempt_caps`（130/200/130）、`run_config_sha256`、`quota_policy_sha256`、`expected_new_questions`、`repair_reserve`、`urgent_slack_s` 皆不可變更；`venues` 僅允許從 `['main', 'minibench']` 擴展為 `['main', 'minibench', 'market_pulse']`，並呼叫 `discovery.validate(policy)` 與 `market_policy.validate(policy)`。
- **外部／人工諮詢與低優先級保護容量（`budget.py` & `external_usage.py`）**：
  - `Budget.recover` 將 `advisory_usage` 已准入之 `attempt_id` 納入 `started` 集合，防止排程器恢復時誤釋放已准入之人工諮詢或練習配額。
  - `Budget.reserve` 驗證 `advisory_admitted` 與 `advisory_finished` 狀態，若有未完成之諮詢嘗試則以 `unresolved_or_running_attempt` 阻擋新預約；已啟動之諮詢嘗試計入 `used_ids`。
  - 保護容量計算 `protected = max(pending_tier1 + expected_new + repair_reserve if tier == 2 else advisory_protected, low_priority_protected)` 確保低優先級工作（如 Market Pulse）或人工諮詢均受核心預留容量約束。

#### 3.4 部署、恢復與四方拆分檢閱閘門（`apply_reviewed.py`, `kickstart_reviewed.py`, `gate_and_deploy.py`, `run_split_reviews.py`）
- **雙階段原子部署與單向提交點（Commit Point）**：
  - `apply_reviewed.py` 在執行時透過 `lock_all` 取得全部 6 把鎖，驗證 `STOP_SUBMIT` 不存在，並以 `require_idle(..., horizon_s=1200)` 確認無進行中嘗試、未決平台寫入或 1200 秒內即將截止之未答題目。
  - 透過 `pause_schedule` 驗證既有 600 秒 `launchd` 設定且無執行中 PID 後，執行 `disable` 與 `bootout` 並確認服務已卸載且具明確停用標記。
  - 在寫入任何程式碼或設定前，先將 `config/submit_mode.json` 原子寫入為 `{"mode": "local"}`。
  - 在 `continuous.register()` 寫入 `data/continuous_campaigns.jsonl`（不可回滾之 append-only 提交點）**之前**發生任何異常，腳本會完整還原備份檔案（並在所有其他檔案雜湊核對無誤後才還原 `submit_mode.json`）；在提交點**之後**（或帳本狀態無法確認時）發生異常，絕不回滾程式碼或帳本，而是保持 `mode: local` 安全停止，允許後續使用同一檢閱閘門安全向後恢復（roll forward）。
- **排程恢復與 60 秒心跳切換（`kickstart_reviewed.py`）**：
  - 強制檢查未啟用 Python `-O` 最佳化（避免 `assert` 被移除），確認 `activation_complete`、6 把鎖、`live_window.require_active` 及所有部署檔案雜湊一致後，才將 `StartInterval=60`、`RunAtLoad=false` 之新版 plist 原子寫入 `~/Library/LaunchAgents/org.forecast-lab.cycle.plist`（同時保留舊版備份 `installed-plist-before.plist`），並在 `enable`/`bootstrap` 及雙重狀態讀回確認後執行不帶 `-k` 的 `launchctl kickstart`。
- **四方拆分檢閱驗證（`gate_and_deploy.py`, `run_split_reviews.py`）**：
  - `gate_and_deploy.py` 針對 `r2` 嚴格驗證 `review-modules-r2.json` 綁定之 `reviewed-files-r2.json` 與 `packet-inputs-r2.json` 雜湊、兩個模組封包大小（`< 170000` bytes）與結尾標記，並逐一核對 `claude-r2a`、`gemini-r2a`、`claude-r2b`、`gemini-r2b` 四份官方 CLI 檢閱紀錄（單一 `development_review_started` 與 `completed` 結束事件、模型與 `high` effort、封包與回答 SHA256、唯一 `PASS` 標題與末行獨立結束標記）。
  - `run_split_reviews.py` 在每一波雙模型檢閱前重新取得 6 把鎖與 `require_idle(horizon_s=1200)`，並在結束後呼叫 `record_usage.py`，對失敗或未完成之檢閱絕不自動重試。

#### 3.5 封包內附靜態證據核對（Static Evidence Verification）
- **測試與集合保持（宿主機實際執行紀錄，檢閱者未執行）**：封包顯示完整測試 `3454 passed`（兩次紀錄分別為 143.76s 與 101.98s），離線與專項測試 `29 passed`、`18 passed`；測試集合由 `3395` 增至 `3454`，`removed: []`。
- **預算重放（`budget-replay.json`）**：僅使用元數據投影（`real_model_predictions: 0`, `POST: 0`），三個模型在 Tier 1 與 Tier 2 的重放結果在變更前後完全一致（`same_allowance_and_admission: true`，上限維持 130/200/130，起算點維持 `2026-11-02T13:03:00.000Z`）。
- **探索週期重放（`cadence-replay.json`）**：誠實揭露歷史 `poll_log.jsonl` 樣本限制與悲觀批次算術假設（`model_calls: 0`, `POST: 0`），驗證 1800 秒（30 分鐘）探索間隔在歷史 180 分鐘視窗與 90 分鐘休眠／忙碌情境下較 3600 秒具備更佳決策餘裕。

---

### 4. 非阻斷性改進建議與政策觀察（Non-Blocking Proposals & Observations）
1. **長期開放之 Market Pulse 題目對人工建議（Human Advice）之互動觀察**：
   - 如 r1 摘要所指出，長期處於 `open` 狀態的 Market Pulse 題目與手動請求人工建議（human advice）之間的互動屬於真實政策議題。在 Module B 的 `budget.py` 與 `scheduler.py` 層面，系統已確保 `main`/`minibench` 排程絕對優先，且透過 `advisory_protected` 與 `low_priority_protected` 確保預算准入安全；針對長期開放題目是否會在特定條件下推遲或阻擋人工建議的具體策略細節（實作於 Module A 之 `human_advice.py`/`advise.py`），建議在部署後持續觀察實際佇列行為，必要時於後續任務微調長期開放題目的排除或冷卻規則。
2. **`apply_reviewed.py` 對 `review_modules` 的直接迭代檢查（未來維護建議）**：
   - 目前 `gate_and_deploy.py` 已在產生 `deployment-gate.json` 前完整驗證四份拆分檢閱（`r2a`/`r2b` x `claude`/`gemini`）的 `PASS` 標題與結束標記，並將四份 `answer.md` 與 `stdout.jsonl` 寫入 `gate['evidence']` 交由 `apply_reviewed.py` 驗證 SHA256，同時以 `r2a` 填入舊版 `gate['reviews']` 欄位。未來若常態化使用拆分檢閱，可考慮讓 `apply_reviewed.py` 的 `gate_check()` 直接一併檢查 `gate.get('review_modules', {})` 中的每一份檢閱檔案標題，進一步提升部署腳本單獨閱讀時的自明性。

END-D139-R2B