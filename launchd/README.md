D139 現行已啟用：cycle timer 為60秒本地heartbeat，新題搜尋由discovery_v1控制為1800秒；已知工作不等慢搜尋。實際啟用及限制見 [D139](../docs/tasks/D139-EXPANSION.md)。以下保留原部署背景，600秒數值屬舊排程。

# Forecast Lab launchd 範本

最新部署：[D118](../docs/tasks/MINIBENCH-EARLY-D118.md)已於UTC11/3 04:12:32提前啟動，固定截止11/10 04:00；舊D105用量由`continuous_v9`／`budget_handoff_v1`保留計入`budget_v6`，來源封存不可重用，沒有額度清零或自動續期。D117原21:03候選已取代；既有600秒LaunchAgent直接kickstart，未重裝。

D117歷史部署見 [D117](../docs/tasks/MINIBENCH-LIVE-D117.md)：Louis已核准有限MiniBench live，UTC2026-11-03 01:53:56載入cycle並驗證waiting，後由D118提前取代。`cycle_v8`在開始前只記waiting、到期後只記expired，不啟動worker；live必須有綁定campaign全文hash與設定hash的`live_window_v1`合約。每600秒檢查，窗口開始後下一輪才登記與前檢；窗口截止另限制worker及實際POST，沒有自動續期。下文是通用範本／安裝說明，不取代PLAN中的當次授權。

Telegram 介面另見 [手機控制台設定指南](../docs/telegram.md)。`org.forecast-lab.telegram.plist` 已有對應程式；完成 Token 設定與帳號配對後，可用 `telegram_bot.py install` 安裝。它提供報告、通知排程與 D91 私密工作對話（舊 D73 Codex 開發任務已停用），不啟動預測或送出流程。**模型隔離驗收期間，不可安裝、重載或改動 LaunchAgents。**以下 trial／驗收前置條件適用於預測排程，與 Telegram 服務分開。

以下預測工作只提供範本，不會自動安裝或載入排程。安裝前須由 Louis 核准 `trial`、完成機器人帳號與場地核准、M1 隔離／模型／全域基準驗收，以及 M3-W1 的送出驗收與本次程式審查。`submit.py` 缺席時只記錄 unavailable，不代表已送出。不得用人類帳號。

`cycle` 每 600 秒觸發一次，前 510 秒允許接新工作；准入截止後不開始新工作、重跑或格式修復，等待已開始的嘗試依正常 timeout 與題目決策截止點結束，再刷新、finalize、publish、submit。第 1／2 層准入沿用 M2 的題目截止與預算規則，不要求工作在本次 600 秒內完成。尚未開始的嘗試留到下一輪。週期全程持有 `cycle.lock`；若另外啟動的週期遇到此鎖，直接記錄 `overlap_skipped`，不排隊；launchd 的 StartInterval 在工作仍執行時可能跳過觸發，不保證每個 tick 都有新程序。`submit.py run` 固定 timeout 為 300 秒（D111）。

supervisor 的硬上限由 `src/cycle.py:timing_limits()` 計算。單一工作以 `initial → format_repair → rerun → format_repair` 的完整鏈估算：`2 × (max(tier1 timeout, tier2 timeout) + repair timeout + 4 × QUOTA_QUERY_S)`；四次嘗試各含 Gemini 前後額度查詢，三條線平行所以取最長單線、不相加。再把「510 秒准入 + 單一工作 + 兩段 300 秒送出 + 30 秒安全餘裕」向上取至 600 秒刻度，handoff 在該刻度前 30 秒、worker 上限在該刻度後 300 秒，另留 10 秒清理。現行 600／1200／300 秒與額度查詢 30 秒設定得到工作 3240 秒、handoff 4770 秒、worker 5100 秒、含清理硬上限 5110 秒；這是故障保護上限，正常情況准入截止就不會開始鏈中的下一次嘗試。`cycles.jsonl` 的 `admission_deadline` 與 `timing_limits` 保存實際計算值（舊欄位 `deadline` 現也代表准入截止）。到 worker 上限才終止 worker 及其後代，包含 adapter 另建 session 的程序。若系統 `/bin/ps` 無法盤點後代，仍終止已知程序並記錄 `cleanup_unverified`（退出碼 125）。中斷留下的模型 start 或送出 intent 依既有帳冊處理，不自動重跑未知結果。

`resolve` 與 `report` 分別在每天／週一 21:00 執行，目標程式及 CLI 待後續 M3 工作確認，現在不可安裝這兩項。launchd 日曆使用 **macOS 系統時區**，必須將主機設為 `Asia/Taipei`；plist 的 `TZ` 不會改變日曆觸發時區。休眠可能錯過週期；`caffeinate -i` 只在工作執行期間防止閒置睡眠，蓋上螢幕仍會睡眠。

持續題集需要事先準備 `config/cycle_campaign.json`（不含憑證）；不沿用 M2 的 64 次量測值作預設。欄位如下：

- `version`: `continuous_v9`（含 `budget_handoff_v1` 時必須為 `v9`；一般題集相容 `continuous_v8`）；`campaign_id`: 唯一字串。
- `venues`: 已允許的場地名稱清單。local/trial 僅允許 `trial_ground_types`；live 每場都須 `submit_whitelisted`。
- `budget_window_start`／`budget_window_end`: UTC ISO 8601，有效窗口最多七天。
- `weekly_attempt_caps`: claude、gemini、gpt 各線明確正整數上限，包含舊嘗試與驗收；不得高於 quota policy 已設定上限。
- `expected_new_questions`、`repair_reserve`、`urgent_slack_s`: 事先固定的非負整數；前兩項為第 1 層與修復保留容量。
- `run_config_sha256`／`quota_policy_sha256`: 使用 `flab.ids.hash_obj` 計算目前設定的 hash。

local／trial 的 `venues` 包含 `main_site_questions` 時，必須使用 `continuous_v8` 或 `continuous_v9`，並加上
`"main_site_trial": {"version": "main_site_trial_v1", "max_questions": 4, "max_pages": 5}`。
`max_questions` 是每窗口最多 1～6 題，`max_pages` 是每次主站掃描最多 1～5 頁（每頁 50 筆），兩者只接受整數。
不含主站場地時不得帶此欄位。選題先涵蓋 binary、multiple_choice，再依決策截止與題號排序；
N=1 時依上述順序優先 binary。已選滿只刷新已選題；作廢仍占名額，而且該窗口停止新增，不補位。
選題帳冊 `data/main_site_trial_selections.jsonl` 僅追加 ID、型別、時間與原因；跨窗口不重選。
若追加中斷留下不完整 JSONL 行，拒絕繼續選題，交由人工檢查帳冊。
有限週系列沿用 `rollover: {"version": "weekly_rollover_v1", "windows": K}`，起始窗口必須恰為七天，
每個解析後的週窗口各自計算題數上限。未使用主站的既有 v7 系列維持相容；live 的場地白名單不變。

政策首次登記後不能修改。同名窗口不可變動，異名窗口不可重疊；到期即停止模型工作，不自動增加上限或續期。下一窗口須在看輸出前設定。原本的 `budget.py` 繼續管理每次嘗試、token、Argon 及保留線。此處不提供會改寫送出設定的命令。

安裝步驟（由 Louis 在正式專案目錄手動執行，不是在代理開發 clone）：

1. 準備上述設定、`.venv`、所需的官方 CLI 登入與 `.env`，確認所有核准及驗收完成。只由主程式讀取 Metaculus token。
2. 建立 `data/logs/` 及 `~/Library/LaunchAgents/`。
3. 將選定 plist 的 `__PROJECT_ROOT__` 全部替換為專案**絕對路徑**，用 plist 編輯器或 `plistlib` 保留 XML escaping，存到 `~/Library/LaunchAgents/`。
4. 執行 `plutil -lint ~/Library/LaunchAgents/org.forecast-lab.cycle.plist`。
5. 執行 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.forecast-lab.cycle.plist`。範本沒有 RunAtLoad，第一次執行依排程；需要立即啟動時，核准後使用 `launchctl kickstart gui/$(id -u)/org.forecast-lab.cycle`。

檢查：`launchctl print gui/$(id -u)/org.forecast-lab.cycle`；查看 `data/logs/` 的 stdout/stderr 與 `data/cycles.jsonl`。週期紀錄只有 ID、狀態及錯誤類型，不列出預測答案。`question_queue.jsonl` 保存加入、重新排入、作廢與文字修訂回退；所有歷史只追加。

停送可建立 `config/STOP_SUBMIT`；模型研究仍繼續。停單線使用 `players_enabled.json`。移除排程：`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/org.forecast-lab.cycle.plist`，再移除該安裝副本；其他工作換成相應名稱。不要刪除資料帳冊。

M3-W1 整合邊界：cycle 在共用 `scheduler.lock` 下整理佇列並發布 `submission_eligible=true` 的新整合紀錄，呼叫 `submit.py run` 前釋放該鎖，仍持有 `cycle.lock`。一題有 `status="intent"` 且其 `intent_id` 沒有 `abandoned` 紀錄時才凍結，由送出端對帳；某意圖被放棄，不會解除同題其他未放棄的意圖。所有意圖都已放棄後可依最新快照重排，包含輪詢曾記為 `already_submitted` 的題目。

依 M3-W1 第 1 輪契約，送出端須拒絕 `question_snapshot_id` 出現在 `question_queue.jsonl` 的 `snapshot_invalidated.old_snapshot_id` 中的整合結果與意圖。cycle 不再為作廢快照發布結果，已發布的過期 ID 僅列於 publish 步驟的 `stale_aggregate_ids`，新快照與其他題仍可發布並呼叫 submit。文字修訂回退不作廢舊整合，維持原快照與 `revision_fallback` 標記。送出端已整合；離線拒絕案例與真實端到端證據分開記錄，M3 狀態以 PLAN 與監督文件為準。

## D140 停線恢復

`org.forecast-lab.line-recovery.plist` 獨立每300秒呼叫 `run.sh line-recovery`，單次最長120秒，只持待恢復線鎖，不堵cycle／submit。原新題1800秒與heartbeat60秒不變。只做已審查精確技術分類／現行隔離證據核對，不啟動模型、不接受基準變更；補做由原scheduler依deadline／budget執行。部署狀態與限制見 `docs/tasks/D140-AUTO-RECOVERY.md`。
