# Telegram 專案工作對話

一個 Bot 以 `[GPT]`、`[Claude]`、`[Gemini]` 標明回覆者，各自呼叫官方 CLI。`telegram_ops_v5`／`team_chat_v3`（D148）只呈現目前近 24 小時內的事項；依 D148，Telegram **只用於查詢進度、今天有無卡線與預測營運計數，不做手機操控或開發續作**。

- `GPT，今天有沒有卡線？預測情況如何？`：可換 Claude、Gemini，未點名預設 GPT。**三者在 TG 都只有問答權限，不會啟動桌面 agent。**
- `進度`：即時重讀共用帳冊，列服務心跳、近 24 小時即時營運計數（送出模式、預測線、最近週期、確認送出題數、各線完成嘗試與錯誤數；直接讀帳冊、不經模型、不含題號／機率／理由）、目前事項及主持人摘要，皆附 UTC 時間。摘要超過一小時明示過時，超過 24 小時只留標題；較新即時數據優先於舊摘要。
- **只顯示目前事項（D148）**：已結案、已完成或已到期超過 24 小時的歷史決策不再列出（只顯示隱藏數量）。問答模型只帶入 24 小時內的同帳號對話，不重述已結案歷史，除非明確詢問。
- `看報告`、`週報`：營運計數；`狀態`：設定與 TG 執行器健康狀態；`/help`：說明。

## 資料與模型隔離

`data/team_chat/events.jsonl` 只追加 UTC 事件。模型只取得開發進度、近 24 小時營運計數、目前事項文字與同帳號 24 小時內最近四則已完成對話，不讀桌面對話、`.env`、模型登入檔或預測正文／機率／理由。開始問答時重讀最新帳冊，回覆附模型資料截點；生成期間若進度變更，程式不送出舊答案，改送最新帳冊狀態。

| 回覆者 | 官方 CLI | 固定模型 | 推理 |
|---|---|---|---|
| GPT | codex | `gpt-6-sol` | low |
| Claude | claude | `claude-sonnet-5-5` | low |
| Gemini | agy | `gemini-3.8-flash-low` | low |

最多同時一個問答、排隊五問、UTC 每日六十問、模型執行 120 秒。沒有自動模型重試／修復／備援／定期呼叫。沿用三線共用互斥鎖，預測或量測忙碌時問答回報暫不可用；免費查詢（`進度`、`狀態`、`看報告`）仍可即時回覆。問答在專案外空白暫存目錄、乾淨環境執行，移除工具、網頁、MCP、plugins、子代理；CLI 使用各自訂閱登入，不提取登入憑證。

健康紀錄由 TG 主迴圈每 30 秒追加（狀態改變則立即），超過 90 秒視為過期；記錄的是 TG 服務執行緒，不代表桌面主持人正在線。

## 桌面主持人操作（`src/project_control.py`）

每個實作／驗證／阻塞檢查點先查狀態，完成後發布進度摘要；`PLAN.md` 仍是唯一規格來源：

```sh
.venv/bin/python src/project_control.py status
.venv/bin/python src/project_control.py publish --file /tmp/forecast-progress.txt --source 'PLAN; docs/tasks/HANDOFF.md'
```

需要依使用者明確要求發送單次通知時：

```sh
.venv/bin/python src/project_control.py notify --file /tmp/forecast-notice.txt --source 'PLAN; docs/tasks/HANDOFF.md'
```

通知交給既有 TG outbox，綁定目前配對帳號；在 TG 服務在線且網路可用時送達，只有 `telegram_events.jsonl` 的 `delivered` 才是送出回執。（早期 `ask`／`claim`／`close`／`milestone` CLI 子命令保留帳冊相容，日常依 D148 不再發起手機操控或自動續作。）

## 安裝與維護

沿用 `src/telegram_bot.py` 的 `pair`、`install`、`uninstall`、`preview`。憑證只放 `.env`，只接受配對私人帳號；拒絕群組、轉傳、bot、代發。問題超過十分鐘不執行。

Mac 須開機、登入、連網且未休眠。重載前確認沒有隔離驗收正在觀測 LaunchAgents，且 TG 沒有執行中的問答。只重載 `org.forecast-lab.telegram`；它與 `org.forecast-lab.cycle` 預測排程獨立運作。
