# Forecast Lab

多模型協作的預測專案：Claude、Gemini、GPT 三個模型先各自獨立研究 Metaculus 比賽的題目，再由程式整合成一個預測，以機器人帳號參加 Metaculus 的 FutureEval 機器人預測賽與 Market Pulse，目標是在三家訂閱額度內最大化比賽分數（`PLAN.md` D31）。機器人賽用剩的額度，可以為人類賽提供參考預測。本專案不下注、不交易。

## 現行營運狀態（2026-11-11）

- **正式參賽場地**：FutureEval 秋季主賽（`33121`）、MiniBench 與 Market Pulse 26Q4（`33131`）已啟用 `live` 模式，共用機器人帳號 `311763` 與每週嘗試上限（Claude 130／Gemini 200／GPT 130）。`cycle` 每 60 秒執行本地 heartbeat，新題搜尋由 `discovery_v1` 控制為每 1800 秒（短窗口 300 秒）。
- **自動續期與不永久停線（D149）**：7 天 live 窗口到期時由 `live_window_v1` 自動續簽下一週窗口；前檢只保留手設開關（`players_enabled`、`submit_mode`、第 2 層子代理禁用）擋線，`line_stopped` 只認帶結束時間的額度／模型不符退避；已有完整答案時不再乾等慢線／缺線，且題目截止前只要至少一個模型有完整有效答案與必要說明且無 `leak_confirmed`，即以 `deadline_fallback_v1` 保底送出（0 個完整答案時不送中性先驗）。
- **題型與輔助工具**：支援是非、多選、數值、離散題（M5）；M4 結算／計分（`resolve.py`、`score.py`）、Metaculus Cup 人類賽參考（`human_advice.py`，由本人判斷與手動送出）、閉題練習（`practice.py`）與閉題健檢（`post_close_audit.py`）皆提供手動 CLI 入口。
- **最新進度與規格**：即時狀態見 [交接紀錄](docs/tasks/HANDOFF.md) 與 `.venv/bin/python src/project_control.py status`；完整規格與決策以 [PLAN.md](PLAN.md) 為唯一真實來源。

## 離線驗證

```sh
.venv/bin/python tests/run_offline.py -q
```

`tests/conftest.py` 為所有測試提供獨立主機、local 送出模式與固定題型 fixture；不修改 `config/`。離線入口在 pytest 主程序執行期間另外阻擋直接開啟專案資料／憑證檔及直接呼叫真實模型 CLI，並使用專案外的暫存目錄；它是誤用防線，不是安全沙盒，詳見 [D136 防護界線](docs/tasks/D136-CLEANUP-BATCH1.md)。

## AI 協作者必讀順序

1. [`AGENTS.md`](AGENTS.md)（Claude 透過 [`CLAUDE.md`](CLAUDE.md) 匯入）：三方共同守則與硬性規則。
2. [`docs/tasks/HANDOFF.md`](docs/tasks/HANDOFF.md)：最新交接狀態與營運守則。
3. [`PLAN.md`](PLAN.md)：完整規格與唯一真實來源（以最新正文、§15 決策紀錄與 §16 變更紀錄為準）。
4. [`docs/telegram.md`](docs/telegram.md)：手機進度與狀態查詢（D91／D148 `project_control.py` 與 `telegram_bot.py`）操作說明。

## 核心檔案地圖

| 路徑 | 用途 |
|---|---|
| `PLAN.md` | 完整計畫書（Single Source of Truth） |
| `AGENTS.md` / `CLAUDE.md` | 三方 AI 協作守則與硬性規則 |
| `docs/tasks/HANDOFF.md` | 最新交接紀錄與操作邊界 |
| `docs/tournament_checks/` | 比賽規則核對紀錄（摘要） |
| `config/` | 執行期設定、場地白名單、題型開關與 `cycle_campaign.json` |
| `src/` | 預測排程、三線 CLI adapters、整合、送出、基準檢查與 Telegram 服務 |
| `prompts/` | 預測、格式修復與對帳提示詞及輸出 schema |
| `launchd/` | macOS LaunchAgent 週期排程與說明（[`launchd/README.md`](launchd/README.md)） |
| `tests/` | 離線單元與回歸測試（含受保護雜湊與 M5 金標準 fixtures） |
