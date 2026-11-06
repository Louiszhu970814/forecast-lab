# Telegram 開發模式交接

> **SUPERSEDED／歷史文件（D136 標記）**：本頁保留當時提案與驗證經過，不是現行授權或待決清單。現行規格見 [TELEGRAM-TEAM.md](TELEGRAM-TEAM.md) 與 [PLAN](../../PLAN.md) 的後續決策。

實作者：GPT；版本 `telegram_ops_v2`、`telegram_dev_v1`。本文件供 Claude 獨立審查，以下自動化測試與實機驗證不替代獨立審查。

## 使用者要求與完成範圍

使用者已要求從手機 Telegram 交辦開發工作、補充需求、查進度及收取完成通知。介面僅接受原先配對的私人 Telegram 帳號。以「開發：需求」明確開始工作；一般聊天仍不觸發模型。

任務在主專案外的獨立工作副本開發與測試。補充需求排入下一輪；停止會保留副本。程式不自動套回正式專案、不提交或推送 Git、不部署、不送出預測。完成結果及累積 patch 供 Claude 審查整合。PLAN 正文與 §15／§16 仍由 Claude 依使用者決定維護；GPT 僅追加 §14.2 建議。

使用者另轉達 Claude 的協調要求：**模型隔離驗收進行時，不安裝、重載或修改 `~/Library/LaunchAgents`**。實作與離線測試期間維持原 Telegram 服務。啟用新版前，須同時確認最近驗收有完成結果、後面沒有新的 call start，並確認沒有正在執行的驗收程序。

## 主要檔案

| 檔案 | 用途 |
|---|---|
| `src/telegram_bot.py` | 授權與指令路由；不在輪詢主迴圈等待模型；狀態／結果通知使用原追加式 outbox |
| `src/flab/dev_tasks.py` | 以 Bot／使用者分隔的任務、需求修訂、停止、每日輪次限制與狀態投影 |
| `src/flab/dev_runner.py` | 單 worker、程式副本、官方 CLI、沙箱探測、程序生命週期、成果匯出 |
| `tests/test_dev_tasks.py` | 授權、去重、秘密格式拒絕、修訂、取消、通知與重啟 |
| `tests/test_dev_runner.py` | 副本邊界、權限參數、結果、續作、停止、重啟、匯出與路徑攻擊 |
| `docs/telegram.md` | 使用者操作、限制、維運與文件來源 |

沿用原本 `org.forecast-lab.telegram` LaunchAgent；開發 worker 為此服務內的單一背景執行緒，沒有新增第二個 LaunchAgent。不修改既有 forecast／scheduler／submit／模型 adapter。

## 審查重點

1. **資料邊界**：副本使用 git 的目前檔案清單（含未提交檔），再套允許目錄與拒絕規則；不含 `.git` 歷史、`.env`、`data/`、`reports/` 或其他聊天。大小、文字編碼、連結與已知秘密格式均有檢查。不要將它描述成從 HEAD 建立的 Git worktree。
2. **執行權限**：官方 `codex exec --ignore-user-config --ignore-rules --strict-config --ephemeral --json`；固定驗證 CLI 0.166.0。使用 `default_permissions=flabdev`，不混用會覆蓋命名政策的 `--sandbox`／`sandbox_mode`。根目錄預設 deny，工作副本可寫；僅必要 runtime、Codex 二進位套件、Command Line Tools 與主專案 `.venv` 唯讀。主專案其他位置不給讀寫權限。
3. **模型工具**：CLI 自己的模型目錄僅改成 direct tools、停用 node REPL，保留沙箱內 shell 與 apply_patch。停用外掛、MCP、hooks、Computer Use、網頁搜尋、子代理等其他入口。只由官方 CLI 處理已存在的 ChatGPT 登入；controller 不讀取或複製登入憑證。子程序環境採明確允許清單。
4. **前置隔離驗證**：每輪使用相同 profile 執行原生 sandbox 探測，確認工作副本讀寫、正式 README 讀取拒絕、控制 canary 讀寫拒絕、網路拒絕及隔離 Git 初始化；失敗不呼叫模型。macOS uv Python 與 Codex 安裝有 symlink chain，實測需允許相應 runtime／二進位套件目錄唯讀，不能只允許最終 executable。
5. **重送與費用**：Telegram update 與需求修訂有穩定去重鍵。一次執行一項，最多五項排隊；全部開發工作每 UTC 日十輪、每輪模型三十分鐘上限。失敗也算啟動輪次，不自動重試。每任務最多六輪、十二份需求。這是開發輪次預算，不是精確 token 額度，與預測帳冊分開。
6. **停止與恢復**：中斷標示 interrupted，不自動重跑未知結果。清理擁有的 process group 與可觀測後代；恢復時核對 PID 與唯一 run path，拒絕終止身分不符的 PID。不自動續用未完成的來源副本。補充內容於本輪結束後處理，非即時插入正在執行的模型回合。
7. **成果仍不可信**：模型成功結束不代表測試通過；驗證欄位明確標為助手回報。從工作副本匯出時用 anchored dirfd 與 `O_NOFOLLOW` 逐層開檔，阻擋模型藉中間目錄 symlink 讓主程式越權讀檔；只產出審查 patch／清單，沒有自動 apply。
8. **資訊揭露**：手機顯示任務與固定階段，不傳原始 CLI stdout/stderr。只在使用者要求開發時保存其需求，與舊版「一般操作原文不落盤」區分。已知 Token 格式會被拒絕，但不是完整秘密偵測器。這個入口只能查自身任務，不能讀桌面對話或其他模型的開發進度。

## 已完成驗證

- 新增的任務／執行器／Telegram 測試涵蓋合成執行器程序生命週期、並行查詢、symlink 越權與 Telegram UTF-16 字數上限；不會在一般 pytest 裡呼叫真實模型。
- 原生 Codex sandbox 探測通過：可寫副本、不能讀正式 README／控制 canary、不能寫控制資料、網路被拒絕、Git 可在副本初始化。
- 真實官方 Codex 開發驗收任務 `D2672733362`，只有獨立副本新增 `docs/telegram_dev_smoke.txt`，內容 `TELEGRAM_DEV_SMOKE_OK`。第一輪在建立模型 session 前被執行檔路徑權限擋下；補齊唯讀 runtime 後能實作，再找出 Git 的 Command Line Tools 範圍不足。修正後第三輪回報指定三份測試 **73 passed**，且 Python 開啟正式 README 得到 `PermissionError`。73 是當時副本的測試數；之後又新增 3 項控制端測試。
- 實機驗收任務的 scope 為 `internal-dev-smoke`，與手機使用者任務分隔；紀錄在忽略的 `data/telegram_dev/events.jsonl`，成果保存在主專案外，未套回主專案。
- 完整 pytest 已通過 953 項（之後另補 emoji 長度測試）。Gemini 第二層驗收於 `2026-11-01T11:11:23.937Z` 記錄 `line_pass=true`；確認後面沒有新驗收開始、也沒有驗收程序後才重載 Telegram。未修改任何已安裝的 LaunchAgent 檔案。已確認主服務 running、開發 worker 持有鎖，並成功送出啟用通知。

## 來源與後續整合

官方依據：[Codex 非互動模式](https://learn.chatgpt.com/docs/non-interactive-mode)、[檔案與網路權限政策](https://learn.chatgpt.com/docs/permissions)。介面刻意選用既有 Python／CLI 架構，沒有引入額外 API key、計費服務或新代理框架。

審查通過後由 Claude 記錄 PLAN §15／§16 及正文，依既有規則提交。本機 `.env`、工作副本、任務帳冊與驗收產物均不得進 Git。待審 patch 基於任務開始時的副本，主專案之後可能已有別人的修改，整合時須比較來源與目前狀態，不能直接覆蓋。

## 審查紀錄（Claude，2026-11-01）

結論：**通過**，整合為 PLAN v1.19、D73。沒有 blocking 意見。審查範圍：`src/telegram_bot.py`（授權、配對、指令路由）、`src/flab/ops_report.py`、`src/flab/dev_tasks.py`、`src/flab/dev_runner.py`、plist、文件與 `tests/test_cycle.py` 的修改；三個新測試檔與 `tests/test_cycle.py` 共 137 passed；主專案完整測試 954 passed。

實測：以 `dev_runner.config_args()` 的同一份 `flabdev` 政策執行 `codex sandbox`（不呼叫模型，只嘗試開檔）。`.env`、`data/acceptance.jsonl`、`~/.codex/auth.json`、`~/.codex/config.toml`、`~/.claude.json`、`~/.claude/settings.json`、`~/.ssh/known_hosts`、`~/Library/Keychains/login.keychain-db` 全部 `PermissionError`；`.venv/pyvenv.cfg` 可讀，符合設計。

- [授權] 私人對話、`from.id == chat.id == 配對 owner`、拒絕 `forward_origin`／`sender_chat`／`via_bot`／bot 帳號；配對碼以 `hmac.compare_digest` 比對；改變狀態的指令超過 10 分鐘作廢｜符合預期｜non-blocking（無需修改）
- [資訊揭露] 營運報告只輸出各線計數、錯誤分類與 token 合計，不輸出題號、機率、理由；開發副本不含 `data/`，模型無法讀取預測輸出，符合硬性規則 2 的「不先看輸出再修改機器人」｜符合預期｜non-blocking（無需修改）
- [N1] `telegram_bot.py install`／`uninstall` 建議在執行前檢查是否有進行中的隔離驗收（`forecast.incomplete_acceptance()` 任一線為真，或有 `acceptance.py` 程序），有則拒絕｜本日 11:0xZ 的 Gemini 第 2 層驗收，就因為驗收期間安裝了這個 LaunchAgent 而 `schedule` 測試失敗，多花一次 Argon 驗收；目前只靠文件提醒｜non-blocking
- [N2] 開發執行與 GPT 預測線共用同一個 ChatGPT 訂閱，且不計入 `budget_v1`。建議之後讓開發 worker 在 `scheduler.lock`／`cycle.lock` 被持有時暫緩啟動新一輪，或至少在 §9 的額度觀察中把開發輪次列為 GPT 線的額外消耗｜Pro 方案額度充裕，短期風險低；但 M3 的 GPT 線若出現 `quota_exhausted`，需要能分辨原因｜non-blocking
- [N3] 每輪的 `sandbox_probe` 建議再加上 `~/.codex/auth.json` 與專案 `.env` 的讀取拒絕檢查（只開檔，不讀內容）｜目前探測只驗證專案 README；本次手動實測已確認，但 CLI 升級後可能改變 `:minimal` 的範圍，需要每輪自動驗證｜non-blocking
- [N4] `SECRET` 不涵蓋 Metaculus 的 40 位十六進位 token 格式｜加入會和 git SHA 衝突；開發沙箱已讀不到 `.env`，風險低，維持現狀｜non-blocking
