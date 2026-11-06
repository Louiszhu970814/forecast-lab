D139：30分鐘新題搜尋、人類賽手動入口、Market Pulse bot已啟用；原用量與台北11/10 12:00截止保留。實際參賽狀態與限制見 [D139交付](docs/tasks/D139-EXPANSION.md)。

# Forecast Lab

多模型協作的預測專案：Claude、Gemini、GPT 三個模型先各自獨立研究 Metaculus 比賽的題目，再由程式整合成一個預測，以機器人帳號參加 Metaculus 的 FutureEval 機器人預測賽，目標是在三家訂閱額度內最大化比賽分數（`PLAN.md` D31）。機器人賽用剩的額度，可以為人類賽提供參考預測。本專案不下注、不交易。

## 現行入口與營運邊界（2026-11-05）

- D118 MiniBench 已核准的 live 窗口固定至 `2026-11-10T04:00:00Z`；沒有自動續期。D137 已經雙審與驗證，FutureEval 主賽＋MiniBench 已啟用並共用原預算；實際首輪狀態及尚缺驗收見交接與 D137 任務單。
- M5 是非、多選、數值、離散題已支援；M4 結算／計分、人類賽參考、閉題練習與閉題健檢有手動入口。人類賽仍由本人判斷與送出；閉題練習的新題存取限制尚在。
- 三線正式 CLI 與驗收、D124 observe、D135 研究候選及後續清理的最新進度，見 [交接紀錄](docs/tasks/HANDOFF.md) 與 `.venv/bin/python src/project_control.py status`。
- 規格、核准範圍與決策以 [PLAN](PLAN.md) 為準。首次正式已送出批次的預定結算日期見 D132；舊文件的 11/06 開發安排不代表該批結算日。

## 離線驗證

```sh
.venv/bin/python tests/run_offline.py -q
```

`tests/conftest.py` 為所有測試提供獨立主機、local 送出模式與固定題型 fixture；不修改 `config/`。離線入口在 pytest 主程序執行期間另外阻擋直接開啟專案資料／憑證檔及直接呼叫真實模型 CLI，並使用專案外的暫存目錄；它是誤用防線，不是安全沙盒，詳見 [D136 防護界線](docs/tasks/D136-CLEANUP-BATCH1.md)。舊 `--synthetic-host` 參數保留為相容別名，不再安裝第二份 fixture。直接 `python -m pytest` 共用相同 fixtures，但沒有離線入口的 audit guards；優先使用上述入口。

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
| `docs/tasks/HANDOFF.md` | 最新交接紀錄與操作邊界 |
| `docs/tournament_checks/` | 比賽規則核對紀錄（摘要） |
| `config/` | 執行期設定、場地白名單、題型開關與 `cycle_campaign.json` |
| `src/` | 預測排程、三線 CLI adapters、整合、送出、基準檢查與 Telegram 服務 |
| `prompts/` | 預測、格式修復與對帳提示詞及輸出 schema |
| `launchd/` | macOS LaunchAgent 週期排程與說明（[`launchd/README.md`](launchd/README.md)） |
| `tests/` | 離線單元與回歸測試（含受保護雜湊與 M5 金標準 fixtures） |
