# Telegram 手機控制台

手機可以查詢 Forecast Lab 狀態、索取近 1 天／7 天營運報告，以及設定每日／每週定時傳送。另可用「開發：需求」交辦獨立 Codex 開發任務。一般自由文字不會自動消耗模型額度。版本：`telegram_ops_v2`、`telegram_dev_v1`。

## 第一次連接（約三分鐘）

1. 在 Telegram 找官方 [@BotFather](https://t.me/BotFather)，傳 `/newbot`，依指示建立這個專案專用的 Bot，保存它提供的 Token。參考 [Telegram 官方教學](https://core.telegram.org/bots/tutorial#obtain-your-bot-token)。
2. 在電腦編輯專案 `.env`，加入或填入這一行；保留原本的 Metaculus 設定：

   ```dotenv
   TELEGRAM_BOT_TOKEN=填入_BotFather_提供的_Token
   ```

   Token 只存 `.env`，不要貼到聊天、程式碼、Git、命令列參數或 plist。程式不會顯示 Token。

3. 在專案目錄執行：

   ```sh
   .venv/bin/python src/telegram_bot.py pair
   ```

   終端機會給一組 `/start 隨機配對碼`。五分鐘內把整行傳給**你剛建立的 Bot 的私人對話**。成功後，程式將你的 `TELEGRAM_USER_ID` 與 `TELEGRAM_CHAT_ID` 追加到 `.env`，並把檔案權限設為 `600`。其他帳號、群組、轉傳訊息及猜錯的配對碼都不會被授權。

4. 啟動 macOS 背景服務：

   ```sh
   .venv/bin/python src/telegram_bot.py install
   ```

   服務會立即啟動，之後登入 macOS 時自動啟動。只安裝 `org.forecast-lab.telegram`，與預測排程分開。手機傳「狀態」確認收到回覆；「看報告」取得第一份摘要。

5. 定期報告預設關閉。在手機傳 `/weekly 1 21:00` 開啟每週一台灣時間 21:00 報告（符合 PLAN §4.5 的每週頻率），或傳 `/daily 21:00` 改成每日營運摘要。以 `/schedule` 查看設定。

Mac 須保持開機、登入、連網且未休眠；蓋上筆電螢幕可能暫停服務。此版本沒有雲端主機，也不會修改電腦的休眠設定。

## 手機操作

| 輸入 | 功能 |
|---|---|
| `看報告`、`報告`、`日報`、`/report` | 最近 24 小時營運摘要 |
| `週報`、`/report 7d` | 最近 7 天營運摘要 |
| `狀態`、`/status` | 送出模式、三線啟用設定、STOP_SUBMIT 標記、最近已記錄週期 |
| `排程`、`/schedule` | 查看目前定期報告設定 |
| `每天 21:00 傳報告`、`/daily 21:00` | 每日台灣時間 21:00，傳近 1 天摘要 |
| `每週一 21:00 傳報告`、`/weekly 1 21:00` | 每週一台灣時間 21:00，傳近 7 天摘要；1～7 是週一～週日 |
| `停止報告`、`/pause` | 關閉定期報告，取消尚未傳出的定期訊息；手動查詢仍可用 |
| `/help` | 操作說明與按鈕選單 |
| `開發模式`、`/dev` | 開發模式說明 |
| `開發：新增每月報告`、`/dev 新增每月報告` | 在獨立程式副本建立開發任務 |
| `開發任務`、`/tasks` | 最近五項手機開發任務 |
| `開發進度`、`/progress 任務編號` | 最近／指定任務的階段、結果與測試回報 |
| `補充：需要圖表` | 補充最近任務；本輪結束後處理，或將已完成任務重新排隊 |
| `/followup 任務編號 補充內容` | 補充指定任務 |
| `停止任務`、`/cancel 任務編號` | 停止最近／指定任務，保留工作副本 |

所有排程固定採 `Asia/Taipei`，不受 macOS 系統時區影響。帳冊與報告時間用 UTC ISO 8601。排程變更只在電腦服務收到指令後生效；超過十分鐘的舊排程指令會要求重傳。

## 手機開發模式

例如傳「開發：在營運報告加上各線錯誤比例」，會取得 `D` 開頭的任務編號。服務開始工作與完成／需要補充時會通知你；中途可以查「開發進度」。開發執行在背景，報告和控制指令仍可回覆。

每個任務複製**當時的工作目錄內容**，包含符合範圍的尚未提交程式，保存在 `~/Library/Application Support/Forecast Lab/telegram-dev/<任務編號>/work/`。不複製 `.git`、`.env`、`data/`、`reports/`、CLI 登入檔或其他聊天紀錄。允許的範圍為專案根目錄的主要文件，以及 `src/`、`tests/`、`docs/`、`config/`、`prompts/`、`launchd/` 中的文字檔；符號連結、疑似憑證與過大副本會被拒絕。

開發模式使用官方 `codex exec` 與它自己的既有 ChatGPT 登入，會消耗 GPT 訂閱額度；Token 不會傳給模型。使用命名檔案權限政策，只允許修改工作副本，並唯讀使用 Python 相依環境與必要的 CLI／系統開發工具。正式專案其他檔案、控制帳冊與憑證禁止讀寫。命令網路關閉；也停用網頁搜尋、外掛、MCP、Computer Use、hooks 與子代理。每輪先實測工作區寫入、正式專案讀取拒絕、控制資料讀寫拒絕、網路拒絕與 Git 初始化，失敗即不啟動模型。參考 [官方權限政策文件](https://learn.chatgpt.com/docs/permissions) 與 [非互動執行文件](https://learn.chatgpt.com/docs/non-interactive-mode)。

- 一次執行一項、最多五項排隊；每 UTC 日最多啟動十輪，每輪模型執行最多三十分鐘。每項任務最多六輪、十二份需求。失敗也計入啟動次數；開發額度限制與預測預算分開。
- 補充執行中任務會排入**下一輪**；不打斷當前模型回合。下一輪使用同一工作副本與需求紀錄，重新開一個無持久對話的 Codex 執行。
- 結果包括開發助手回報的修改摘要、實際測試或限制；模型成功結束不代表測試全部通過，請查看驗證欄位。缺必要資訊時會轉成「等待補充」。
- `runs/<執行編號>/changes.patch` 與 `artifacts.jsonl` 保存相對於原始副本的累積修改。它們供 Claude 審查整合；不自動修改主專案、提交 Git、安裝服務、送出預測或部署。此入口不具備套用／合併命令。
- 中斷後保留工作副本、標示中斷，不自動重新消耗額度；傳「補充：繼續」可續作。服務重啟時只會清理 PID 與唯一執行標記相符的舊開發程序；身分不明時停止執行器，待電腦檢查。
- 只顯示由此 Telegram 入口建立的任務，不會同步桌面 Codex、Claude 或 Gemini 對話。第一版使用 GPT；跨模型審查由既有協作流程處理。
- `data/telegram_dev/events.jsonl` 追加保存開發需求、補充、狀態與經清理的摘要；這是原始聊天不落盤規則的明確例外。請勿貼入密碼、Token 或預測答案。已知格式的憑證會被拒絕，這不是通用秘密偵測器。
- 支援並固定驗證 `codex-cli 0.166.0`；版本改變後會拒絕新工作，須重新驗證。安裝額外相依套件等需要網路的工作，會回報限制，由電腦端準備。

**與隔離驗收協調**：任何模型隔離驗收進行中，都不要安裝、重載或修改 `LaunchAgents`。新版啟用須等驗收完成；服務安裝步驟不能與驗收並行。

## 報告目前提供什麼

- 各線完成嘗試數、錯誤次數與固定分類、已知 input + output token 合計及資料覆蓋比例。
- 期間內開始、尚無完成紀錄的嘗試數；它不等同「目前正在執行」。
- 已確認預測送出的不同意圖數，包含期間內完成的對帳確認；dry-run 不算送出。
- 預算拒絕事件數；事件數不等同漏答題數。
- 目前設定與最近已記錄週期。這些歷史資料不能證明預測 daemon 正在執行。

以嘗試開始時間落在 `[起點, 終點)` 且截至報告時已完成的紀錄統計。尚未完成的嘗試另外列出，不當成失敗。token 缺值不當成零，跨模型 token 數不直接代表相同成本。報告會列出資料與分析截止時間；讀取損壞或未完整寫入的帳冊時，停止產生報告並給固定提示。

這是第一版**營運報告**，不等同 PLAN §4.5 的完整評分報告。尚未接入結算計分、官方排名、成對比較或精確剩餘訂閱額度；也不據此宣告 M1／M2 驗收完成。

營運報告對所有題目一律只做彙總；不傳題目文字、題號、機率、選項、理由、原始錯誤、原始模型輸出或既有報告附件。這比 §3.6／§4.5 對開放題目的限制更保守。報告查詢不呼叫模型、不耗用模型訂閱額度，也不修改預測設定或執行送出。只有明確的開發指令會使用 Codex。

## 維運與可靠性

```sh
# 不需要 Token、沒有網路呼叫的報告預覽
.venv/bin/python src/telegram_bot.py preview --days 7

# 只檢查設定；加 --online 會向 Telegram 確認 Bot 身分與 webhook 狀態
.venv/bin/python src/telegram_bot.py check
.venv/bin/python src/telegram_bot.py check --online

# 前景執行，Ctrl-C 結束；不能與背景服務同時執行
.venv/bin/python src/telegram_bot.py run

# 查看背景服務
launchctl print gui/$(id -u)/org.forecast-lab.telegram

# 停止並移除背景服務，保留配對與全部歷史紀錄
.venv/bin/python src/telegram_bot.py uninstall
```

- 依 [Telegram Bot API](https://core.telegram.org/bots/api#getupdates) 使用 HTTPS 長輪詢，不開放外部入站連接埠。已有 webhook 時拒絕啟動，不會刪除其他程式的 webhook；同一 Bot 勿用在別的輪詢程式。
- `data/telegram.lock` 避免重複啟動。`data/telegram_events.jsonl` 保存配對後的 update 游標、排程設定、經清理的回覆與送達紀錄，全部追加寫入。配對碼與一般操作的原始文字不寫入帳冊；開發需求另存專用帳冊以支援續作。
- 更新游標與排程變更會一同持久化，重送同一 update 不會重複修改排程。失敗的訊息保存於待送佇列，重啟後繼續；429 使用 Telegram 的 retry-after。
- 休眠／離線恢復時，只補最新到期時段的報告，並以恢復時的資料截止點產生摘要，不追補所有錯過的日報／週報。已過期、已停止或已改排程的未送定期訊息會取消。
- `sendMessage` 沒有本程式可用的冪等鍵；若送出成功後、寫入確認前斷電，可能重複一則訊息。排程設定不會因此重複執行。無法保證離線時準點送達。
- 程式日誌在 `data/logs/telegram.stdout.log` 與 `telegram.stderr.log`，只輸出固定錯誤訊息／代碼，不列 Token、API URL 或 traceback。帳冊異常時不自動刪除歷史。
- 換手機但使用相同 Telegram 帳號，不需要重配對。若要更換帳號或 Bot：先 `uninstall`，在 `.env` 更新 Token 並移除兩個 ID 欄位，再重新 `pair`、`install`；最後在手機重新確認排程。舊帳冊保留並依 Bot ID／使用者 ID 隔離。

## 開發與交接

入口 `src/telegram_bot.py`；報告欄位投影在 `src/flab/ops_report.py`；開發佇列與執行器為 `src/flab/dev_tasks.py`、`src/flab/dev_runner.py`。沿用同一 Telegram 背景服務，不另外安裝 worker LaunchAgent。測試：

```sh
.venv/bin/python -m pytest -q tests/test_telegram_bot.py tests/test_dev_tasks.py tests/test_dev_runner.py
```

本次只新增營運介面，未改預測 prompt、模型、篩題、整合或送出行為。PLAN 正文、決策與版本仍由目前整合者 Claude 維護；GPT 的待整合建議列於 PLAN §14.2。
