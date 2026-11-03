# Forecast Lab

多模型協作的預測專案：Claude、Gemini、GPT 三個模型先各自獨立研究 Metaculus 比賽的題目，再由程式整合成一個預測，以機器人帳號參加 Metaculus 的 FutureEval 機器人預測賽，目標是在三家訂閱額度內最大化比賽分數（`PLAN.md` D31）。機器人賽用剩的額度，可以為人類賽提供參考預測。本專案不下注、不交易。

## 目前狀態（截至 2026-11-03 UTC，計畫書 v1.78）

- **D118 MiniBench 正式參賽進行中**：送出模式為 `live`（機器人帳號 `311763`，窗口 `d118_minibench_20261103T0400Z` 至 `2026-11-10T04:00:00Z`）。
- **支援題型**：是非題（binary）、多選題（multiple_choice），以及已於 v1.74 上線的 M5 數值題（numeric）與離散題（discrete）（見 `config/question_types.json`）。
- **防線與維護狀態**：
  - D121 第一批 fail-soft 放寬與 D123 `claude.skills_synced` 資訊性分類已生效。
  - D124 `autoload_maintenance_v1`、`forecast_pipeline_v8` 與 `auto_reaccept_v1`（目前僅啟用 **observe**，尚未啟用 R2-C enforce）已上線。
  - D125 `autoload_v3` Codex 載入量測相容性已修正。
- **接手與當日進度**：請先閱讀 [交接紀錄](docs/tasks/HANDOFF.md) 並執行 `.venv/bin/python src/project_control.py status`。

## AI 協作者必讀順序

1. [`AGENTS.md`](AGENTS.md)（Claude 透過 [`CLAUDE.md`](CLAUDE.md) 匯入）：三方共同守則與硬性規則。
2. [`docs/tasks/HANDOFF.md`](docs/tasks/HANDOFF.md)：最新交接狀態、營運守則與待辦事項。
3. [`PLAN.md`](PLAN.md)：完整規格與唯一真實來源（閱讀時請以 §15 最新決策與 §16 版本變更紀錄為準）。
4. [`docs/telegram.md`](docs/telegram.md)：手機工作對話（D91 `project_control.py` 與 `telegram_bot.py`）操作說明。

## 核心檔案地圖

| 路徑 | 用途 |
|---|---|
| `PLAN.md` | 完整計畫書（Single Source of Truth） |
| `AGENTS.md` / `CLAUDE.md` | 三方 AI 協作守則與硬性規則 |
| `docs/tasks/HANDOFF.md` | 最新交接紀錄与操作邊界 |
| `docs/tournament_checks/` | 比賽規則核對紀錄（摘要） |
| `config/` | 執行期設定、場地白名單、題型開關與 `cycle_campaign.json` |
| `src/` | 預測排程、三線 CLI adapters、整合、送出、基準檢查與 Telegram 服務 |
| `prompts/` | 預測、格式修復與對帳提示詞及輸出 schema |
| `launchd/` | macOS LaunchAgent 週期排程與說明（[`launchd/README.md`](launchd/README.md)） |
| `tests/` | 離線單元與回歸測試（含受保護雜湊與 M5 金標準 fixtures） |
