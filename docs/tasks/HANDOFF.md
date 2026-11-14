# 給下一個對話的接手紀錄

> 唯一真實規格來源仍為 [`PLAN.md`](../../PLAN.md)。本檔記錄現行營運狀態與近期重要變更交接；過時中間紀錄已於 D150 精簡。

## 現行營運摘要（2026-11-14，PLAN v1.106）

- **正式參賽與排程**：FutureEval 秋季主賽（`33121`）、MiniBench 與 Market Pulse 26Q4（`33131`）已啟用 `live` 模式，共用機器人帳號 `311763` 與每週嘗試上限（Claude 130／Gemini 200／GPT 130）；現行窗口為 `d152_forecast_timeouts_20261113`（計數起點保留 `2026-11-10T04:00:00.000Z`，至 `2026-11-17T04:00:00.000Z` 到期自動續期）。三線預測模型為 Claude `claude-opus-5-5`（medium／high）、Gemini `gemini-4-carbon-medium`／`gemini-4-carbon-high`（medium／high，D151）、GPT `gpt-6-astra`（medium／high）；三線第 1／2 層預測逾時皆為 1800 秒（`run_config_v5`，D152）。`org.forecast-lab.cycle` 每 60 秒執行本地 heartbeat，新題搜尋由 `discovery_v1` 控制為 1800 秒（距關閉不足 1 小時之短窗口 300 秒）。
- **回報偏好（D146）**：當 Louis 詢問「情況如何」時，除本地營運狀態外，一併查詢各已參賽場次的最新官方分數與排名（附來源與查詢時間；未計分、未上榜或查詢失敗須明確區分）。
- **手機進度查詢（D148）**：Telegram（`org.forecast-lab.telegram`，`telegram_ops_v5`／`team_chat_v3`）只顯示近 24 小時目前事項與營運計數，僅用於查詢今天有無卡線、預測情況與問答，不做手機操控或開發續作。

---

## 近期變更交接（D137～D152）

### D152：第 1／2 層預測逾時放寬為 1800 秒（2026-11-14，PLAN v1.106）
- **原因**：D151 換用 Gemini 4 Carbon 後，Gemini 第 1 層近 24 小時 7 次有 6 次撞 600 秒逾時（換模型前 0 逾時）；Louis 指示三線都放寬到 1800 秒。
- **內容**：`run_config_v5`（`timeouts_s` 1800／1800／repair 300）、`escalate.py` 允許 v5、`budget_handoff.py` 授權集合加入 `PLAN D152`；以 600／1200 秒校準的情境測試改用 `legacy_forecast_timeouts` fixture。完整離線回歸 3731 passed。
- **部署**：UTC `2026-11-13T17:56:41.870Z` 持 7 把鎖原子套用，登記 `d152_forecast_timeouts_20261113`（D152→D151→`auto_20261110T0400Z`，計數起點不變，`2026-11-17T04:00Z` 自動續期）；備份與 manifest 在 `data/operations/d152-forecast-timeouts-20261113/`。
- **部署後確認**：首個自然週期 `cycle_1794592625966_0121bd58`（UTC 17:57:05）complete、0 錯誤、preflight ok、`worker_timeout_s` 6300、campaign 為 D152。
- **待辦**：觀察 Gemini Carbon 第 1 層實際耗時與逾時是否消失；程式變更尚未 commit（待 Louis 指示）。

### D151：Gemini 預測線升級為 Gemini 4 Carbon 與窗口無縫接續（2026-11-12，PLAN v1.105）
- **模型與升版設定**：`config/run_config_v2.json` 升為 `run_config_v4`（`players.gemini.tier1` = `gemini-4-carbon-medium`／`medium`、`players.gemini.tier2` = `gemini-4-carbon-high`／`high`；`acceptance.gemini` 與 TG 問答維持 `gemini-3.8-flash-low`）。`src/adapters/gemini.py` 補入 `Gemini 4 Carbon (Low/Medium/High)` 日誌模型核對映射，`src/quota.py` 相容 `gemini-4-carbon-` 前綴，`src/escalate.py` 允許 `run_config_v4`，並同步兩份測試保護雜湊 fixture。
- **窗口接續（`budget_handoff_v4`）**：`src/budget_handoff.py` 新增有界 `continuous_v11` 設定接續，於 UTC `2026-11-12T15:19:42.805Z` 持全部 7 把鎖確認 0 在途工作後原子套用並登記 `d151_gemini_carbon_20261112`，完整保留 `2026-11-10T04:00:00.000Z` 根計數起點、每週上限與 `2026-11-17T04:00:00.000Z` 到期自動續期；3722 項離線回歸全過。

### D150：過時排程、文件、中間審查與本機舊備份清理（2026-11-11，PLAN v1.104）
- **背景排程退役**：卸載 D149 後已 100% 空轉的 `org.forecast-lab.line-recovery` LaunchAgent（`config/line_recovery.json` 設為 `"enabled": false`），移除未使用的 `line-recovery`／`resolve`／`report` plist 範本，並從 `launchd/run.sh` 移除每 60 秒後置的 `auto_reaccept.py tick` 觀察呼叫。
- **文件與規格對齊**：更新 `AGENTS.md`（同步更新兩份測試保護雜湊）、`CLAUDE.md`、`README.md`、`launchd/README.md`、`docs/telegram.md`、`docs/tournament_checks/metaculus-cup-fall-2026.md`、`config/tournaments.json` 與 `PLAN.md` §1～§13 過時條文，並移除 `PLAN.md` §14 早期回合制審查對話紀錄。`config/run_config_v2.json` 與 `config/quota_policy.json` 因連同 `_notes` 綁定現行 `live` 窗口雜湊，維持原位元組不動。
- **過程檔與本機空間清理**：刪除 `reviews/` 中間退件回合與作廢提案審查（保留全部最終 PASS 報告與驗證 manifest）、刪除 `docs/tasks/` 已取代提案草稿，並清理本機未進 Git 的 `scratch/`、`reports/` M2 舊報表及 `data/operations/` 舊外掛備份壓縮包（保留全部核心 JSONL 帳冊、`data/raw/`、`data/cli_pins/`、D138 無損封存與 `reports/advisory/`、`reports/practice/`）。

### D149：拔除永久停線、窗口自動續期與截止前保底送出（2026-11-11，PLAN v1.103，`b9faa7f`）
- **拔除永久停線**：前檢只留手設開關（`players_enabled`、`submit_mode`、第 2 層子代理禁用）擋線，其餘檢查與讀取例外皆降為 `warnings`；`line_stopped` 只認帶 `until` 的限時等待（`TIMED_WAIT_REASONS`）。
- **不等慢線／缺線與自動續期**：同一題至少有一模型完成答案時，等待超過 15 分鐘或趕不上截止的線不再阻擋整合（`lines_not_waited_for`）；7 天 `live` 窗口到期由 `live_window_v1` 自動續簽下一週窗口（已續為 `auto_20261110T0400Z` 至 `2026-11-17T04:00:00Z`）。
- **截止前保底送出（`deadline_fallback_v1`）**：題目截止前只要至少一個模型有完整預測答案與必要說明且無 `leak_confirmed`，即以 `salvaged_inputs` 整合送出並附保底留言；0 模型完整答案時維持 `no_valid_inputs`，不送中性先驗。經官方 Codex CLI（`gpt-6-astra` `medium`）獨立審查 PASS（[`gpt-d149-r18.md`](../../reviews/gpt-d149-r18.md)）與 3707 項離線回歸全過。

### D143～D147：基準檢查與 CLI 版本相容性放寬（2026-11-06～11-08）
- 移除三家 CLI 純版本更新停線規定（[D143](D143-BASELINE-RELAX.md)、[D145](D145-CLI-UPGRADE-CHECK.md)），日常開發目錄變更改列不擋線（[D144](D144-DEVELOPMENT-CATALOGS.md)），並補齊官方 Codex 外掛 `latest` 別名相容（[D147](D147-CODEX-CACHE-ALIAS.md)）。

### D137～D139：主賽、30 分鐘搜尋、Market Pulse 與資料封存（2026-11-05）
- **主賽與擴充啟用**：啟用 FutureEval 主賽＋MiniBench（[D137](D137-MAIN-LAUNCH.md)），隨後啟用 30 分鐘新題搜尋（60 秒 heartbeat）、Market Pulse 26Q4 機器人參賽與 `human_advice.py` 人類賽手動參考入口（[D139](D139-EXPANSION.md)）。
- **資料無損封存**：兩份大型覆蓋率模擬檔與一份重複 CLI 候選檔已完成無損封存與去重（`data/` 約減 769 MB），還原方式見 [D138](D138-DATA-CLEANUP.md)（請保留 `data/archive/d138/`、`data/operations/d138-data-cleanup-20261105/` 及對應 `.archived.json`）。
