# Forecast Lab launchd 排程說明

目前專案僅使用兩個 macOS LaunchAgent 範本（舊 `line-recovery`、`resolve`、`report` 範本與 `cycle` 後置的 `auto_reaccept` 觀察已於 D150 退役）：

1. **`org.forecast-lab.cycle.plist`**：正式預測排程，每 **60 秒**觸發一次 `launchd/run.sh cycle`（執行 `src/cycle.py --campaign config/cycle_campaign.json --heartbeat`）。
2. **`org.forecast-lab.telegram.plist`**：Telegram 進度查詢與問答常駐服務（見 [手機工作對話說明](../docs/telegram.md)），與預測排程獨立，不啟動預測或送出流程。

## 週期與搜尋節奏（D139／D149）

- **本地 heartbeat（60 秒）與新題搜尋（1800 秒／短窗口 300 秒）**：`cycle` 每 60 秒檢查一次；若無待處理題目且未達 `discovery_v1` 新題搜尋間隔（一般 1800 秒、距關閉不足 1 小時的短窗口 300 秒），即以退出碼 `3` 快速結束，不打 API、不啟動模型。已知在途或待整合題目優先處理，不等慢搜尋。
- **7 天窗口自動續期（D149 `live_window_v1`）**：當正式 `config/cycle_campaign.json` 的 `live` 窗口到期時，`cycle.py` 自動呼叫 `src/live_window.py:renew()` 延續原核准場地、機器人帳號與每週上限，簽出下一個 7 天窗口繼續運作（若設有 `config/STOP_SUBMIT` 或切離 `live` 模式則不續期）。
- **互斥鎖與硬上限**：週期全程持有 `data/cycle.lock`，重疊觸發直接記 `overlap_skipped` 退出。單輪准入時間預設 510 秒，supervisor 硬上限由 `src/cycle.py:timing_limits()` 依 `tier1`/`tier2`/`repair` timeout 動態計算；`submit.py run` 單段上限固定 300 秒。`caffeinate -i` 僅在程序執行中防止閒置睡眠，筆電闔蓋或斷電睡眠仍會暫停觸發。

## `config/cycle_campaign.json` 主要欄位（`continuous_v11`）

- `version`: `continuous_v11`（向下相容舊版格式）；`campaign_id`: 唯一識別字串。
- `venues`: 已核准場地清單（現行 `live` 為 `["main", "minibench", "market_pulse"]`）。
- `budget_window_start`／`budget_window_end`: UTC ISO 8601，單一窗口最長 7 天。
- `weekly_attempt_caps`: `claude`、`gemini`、`gpt` 每週嘗試次數上限（不得高於 `quota_policy.json`）。
- `expected_new_questions`、`repair_reserve`、`urgent_slack_s`: 第 1 層新題與格式修復保留參數。
- `run_config_sha256`／`quota_policy_sha256`: 綁定目前 `run_config_v2.json` 與 `quota_policy.json` 的雜湊值。
- `discovery`: `discovery_v1` 搜尋與 heartbeat 秒數設定。
- `market_pulse`: `market_pulse_v1` 子題首次餘裕作答政策（更新 0 次、每子題獨立私人留言）。

## 安裝、檢查與停送操作

安裝步驟（由 Louis 在正式專案目錄手動執行）：

1. 確認 `.venv`、三家官方 CLI（含 `launchd/bin/` 固定 wrapper）與 `.env` 設定完成。**模型隔離驗收進行期間，不可安裝、重載或改動 LaunchAgents。**
2. 建立 `data/logs/` 及 `~/Library/LaunchAgents/`，將 `org.forecast-lab.cycle.plist` 中的 `__PROJECT_ROOT__` 替換為專案絕對路徑後存入 `~/Library/LaunchAgents/`。
3. 驗證並載入：
   ```sh
   plutil -lint ~/Library/LaunchAgents/org.forecast-lab.cycle.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.forecast-lab.cycle.plist
   ```
4. 狀態檢查：`launchctl print gui/$(id -u)/org.forecast-lab.cycle`，並查看 `data/logs/cycle.stdout.log` 與 `data/cycles.jsonl`（週期帳冊僅記 ID、狀態與計數，不含預測內容）。
5. **暫停送出／停單線／卸載**：
   - 暫停平台送出與自動續期：建立 `config/STOP_SUBMIT`。
   - 停用單一模型線：修改 `config/players_enabled.json`。
   - 卸載排程：`launchctl bootout gui/$(id -u)/org.forecast-lab.cycle` 並移除 `~/Library/LaunchAgents/org.forecast-lab.cycle.plist`（請保留 `data/` 帳冊）。
