# Telegram 專案工作對話

D91 將舊版手機開發任務入口改成共用進度問答。沿用已配對的私人 Telegram 對話，一個 Bot 以 `[GPT]`、`[Claude]`、`[Gemini]` 標明回覆者；各自呼叫該家的官方 CLI，不是由一家模仿三家。

- `GPT，現在 M3 做完了嗎？卡在哪裡？`：GPT 回答；可換成 Claude、Gemini，沒點名預設 GPT。
- `進度`：直接看主持人發布的專案進度，不呼叫模型。
- `待決事項`：看決策選項、已收到的決定與續作狀態。
- `決定 M3-1 B`：選擇已發布的指定選項；編號不能重用，過期或已處理的決策不再接受。
- `看報告`、`週報`：既有營運計數；`狀態`：設定與週期狀態；`/help`：說明。

由 Louis 主動查詢；不定期推播進度。模型答案是對提問的回覆。舊定期報告設定在啟動時關閉，待送舊開發通知取消；舊任務與檔案保留，但不再啟動 DevWorker。`開發：...` 等舊命令改顯示新說明。

## 資料與模型

主持人維護 `data/team_chat/events.jsonl`，只有開發進度、來源、問題、回答、待決事項與執行狀態。只追加、不覆寫；UTC 時間。進度超過一小時明確標示可能過時，沒紀錄就回答無法確認。三家共用進度與最近四則同帳號已完成對話。

此入口不讀桌面的 Codex／Claude／agy 對話，也不讀 `.env`、模型登入檔、預測正文、機率或理由。模型只得到主持人整理的進度與決策文字。主持人不可把開放／即將開放的機器人賽個別預測放進進度板。

| 回覆者 | 官方 CLI | 固定模型 | 推理 |
|---|---|---|---|
| GPT | codex | `gpt-6-sol` | low |
| Claude | claude | `claude-sonnet-5-5` | low |
| Gemini | agy | `gemini-3.8-flash-low` | low |

每次只叫被點名的一家，最多同時一個問答或續作、排隊五問、UTC 每日六十問、模型執行 120 秒。沒有自動重試、格式修復、備援模型或定期模型呼叫。CLI 使用自己的訂閱登入，不提取或共用登入憑證；開發消耗與預測預算分開。其他預測／成本量測占用鎖時，問答直接回報暫不可用；免費的「進度」仍可用。

問答在專案外的空白暫存目錄、乾淨環境執行。移除工具、網頁、MCP、plugins、子代理；Claude 的 `StructuredOutput`、agy 的 `finish` 只用於格式化回傳。異常工具事件使答案不可用。CLI 原始輸出保留於短期私有暫存，清理後不送入 TG。

## 主持人操作

每個實作／驗證／阻塞檢查點先查決策，結束時發布最新狀態。PLAN 是唯一規格來源；進度板是附來源的作業摘要，不能藉此修改計畫門檻或預測設定。

```sh
.venv/bin/python src/project_control.py status
.venv/bin/python src/project_control.py publish --file /tmp/forecast-progress.txt --source 'PLAN v1.39 D91; docs/tasks/HANDOFF.md'
.venv/bin/python src/project_control.py ask --file /tmp/forecast-decision.json
.venv/bin/python src/project_control.py close M3-1 '已依選項完成，證據見指定紀錄'
```

決策 JSON 範例（只在本機發布，不是從 TG 接收 JSON）：

```json
{
  "decision_id": "EXAMPLE-1",
  "question": "下一步先做哪項？",
  "options": {"A": "整理交接文件", "B": "執行已審查的離線檢查"},
  "expires_at": "2026-11-03T00:00:00Z",
  "actions": {}
}
```

尚未登記續作的選項，只記錄「決定已收到／待主持人接手」。主持人須真的接手才能更新狀態；不能把收到決定當作已開始。

若要讓選定選項自動接續本機工作，主持人先完成並審查獨立 Python 程式，存入 `config/team_actions/`，在上述 `actions` 加入選項映射，例如：

```json
{"B": {"script": "config/team_actions/offline_check.py", "sha256": "實際檔案的 SHA-256", "timeout": 300}}
```

只能選已登記的選項，TG／問答模型不能提供命令、路徑、參數或新增程式。程式雜湊於發布、接受與執行前核對；禁用連結、shell、任意工作目錄，上限 600 秒。續作與預測鎖互斥，適用不另取預測鎖的離線工作；不要登記會再取得同一鎖的預測／量測／模型工作。這是本機主持人審查的程式執行，不是給問答模型執行權限。退出碼 0 代表該續作程序完成，實際檢查證據由程式／主持人寫回進度板。

先記錄開始再啟動；重啟遇到「已開始、沒有結果」會標記結果不明，不能自動重跑。待決事項可看到排隊／開始／完成／失敗／不明。不會喚醒同一個桌面 Codex 對話；需要人類介面的驗證仍由本人處理。沒有待決事項時不為展示功能捏造決策。

## 安裝與維護

沿用 `src/telegram_bot.py` 的 `pair`、`install`、`uninstall`、`preview` 指令，憑證只在 `.env`。只接受配對私人帳號，拒絕群組、轉傳、bot、代發；問題與決定超過十分鐘不執行。已消費 update／已接受決策不能重做。發送 TG 訊息的網路回執中斷可能造成重複回覆，但不會重複決策或續作。

常駐服務為 `org.forecast-lab.telegram`；Mac 須保持開機、登入、連網、未休眠。安裝或重載前確認沒有隔離驗收正在觀測 LaunchAgents；這不是預測排程服務。

參考：[Telegram Bot API](https://core.telegram.org/bots/api)、[Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)、[GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol)。實際參數以本機已驗證官方 CLI 為準。
