# Telegram 專案工作對話

一個 Bot 以 `[GPT]`、`[Claude]`、`[Gemini]` 標明回覆者，各自呼叫官方 CLI。`telegram_ops_v4`／`team_chat_v2` 修正決策與接手回報；沿用 D91 已配對私人對話及離線續作界線。

- `GPT，現在 M3 做完了嗎？卡在哪裡？`：可換 Claude、Gemini，未點名預設 GPT。**三者在 TG 都只有問答權限，不會啟動桌面 agent。**
- `進度`：即時重讀共用帳冊，列決策、接手紀錄、服務心跳及主持人摘要，皆附 UTC 時間。摘要超過一小時明示過時；較新決策優先於舊摘要。
- `待決事項`：查選項、已核准範圍、負責人、實際接手者、阻塞、條件及下一步。
- `決定 M3-1 B`：選擇或修改選項。**未開始且未到期可以修改**；已開始、完成、失敗、結果不明、結案、到期、不存在各自有明確拒絕原因。修改保留全部歷史，版本增加；同一 TG update 重送不會改回舊選項或重跑。
- `看報告`、`週報`：營運計數；`狀態`：設定與 TG 執行器健康狀態；`/help`：說明。

選了選項就是核准該選項寫出的範圍，不再重問同一核准。核准、接手、完成是不同狀態；例如容量數值獲准，不等於預測排程已啟用。若要做範圍外的新工作，須另外明確列出差異。

## 誰接手與何時開始

1. 有事前審查、登記雜湊的離線 Python 續作：TG TeamWorker 在前一項工作結束後，約下一個一秒檢查開始。收到選項只表示排隊；開始與結果另外通知。執行器不在線或沒有新鮮健康紀錄時不承諾開始時間。
2. 沒有登記續作：回覆指定負責人（既有決策預設 Claude，PLAN D95）、缺少自動觸發、開始時間未定，以及「在電腦開啟主持人，查 `project_control.py status` 並接手決策」的具體步驟。這不是等一個已啟動的背景 agent。若已在另一個對話交辦，讓實際接手者先登記 `claim`。
3. 有條件的選項：在 `handoffs` 填入里程碑 ID。主持人有完成證據後登記 `milestone`，TG 自動發條件達成通知；有離線續作則轉排隊，否則指出人工接手步驟。程式不從自然語言猜測條件已完成。

TG 服務 `org.forecast-lab.telegram` 與 M3 預測排程是不同程序。2026-11-02 問題回報當時，TG launchd 已安裝且在線；D100／D101 的 `actions` 都是空映射，故沒有任何會喚醒 Claude 的觸發器。「預測 launchd 安裝需 Louis 在場」不代表 TG 服務未安裝，也不能靠安裝預測排程修復交接。

## 資料與模型

`data/team_chat/events.jsonl` 只追加 UTC 事件。模型只取得開發進度、決策文字與同帳號最近四則已完成對話，不讀桌面對話、`.env`、模型登入檔或預測正文／機率／理由。開始問答時重讀最新帳冊，回覆附模型資料截點；生成期間若進度或決策變更，程式不送出舊答案，改送最新帳冊狀態，不再呼叫模型。

| 回覆者 | 官方 CLI | 固定模型 | 推理 |
|---|---|---|---|
| GPT | codex | `gpt-6-sol` | low |
| Claude | claude | `claude-sonnet-5-5` | low |
| Gemini | agy | `gemini-3.8-flash-low` | low |

最多同時一個問答或續作、排隊五問、UTC 每日六十問、模型執行 120 秒。沒有自動模型重試／修復／備援／定期呼叫。沿用三線共用互斥鎖，預測或量測忙碌時問答回報暫不可用；免費查詢仍可用。問答在專案外空白暫存目錄、乾淨環境執行，移除工具、網頁、MCP、plugins、子代理；Claude `StructuredOutput`、agy `finish` 只格式化回傳，其他工具事件使答案不可用。CLI 使用各自訂閱登入，不提取登入憑證。

健康紀錄由 TG 主迴圈每 30 秒追加（狀態改變則立即），超過 90 秒視為過期；記錄的是服務與 TeamWorker 執行緒，不代表桌面 Claude 正在線。主持人的 `claim` 也是接手事件，不是持續心跳。一般進度仍由本人主動查詢；決策狀態／條件變化及本輪明確要求的通知才推送，不啟用舊定期營運推播或 DevWorker。

## 主持人操作

每個實作／驗證／阻塞檢查點先查決策；PLAN 仍是唯一規格來源。收到選項後，先讀版本與核准範圍，**實際接手工作前以該版本 `claim`**，避免手機改選與工作開始競爭。版本已改或已到期會拒絕，必須重讀。開始交易與修改交易共用跨程序鎖，先成功者決定結果；執行開始後不允許修改。

```sh
.venv/bin/python src/project_control.py status
.venv/bin/python src/project_control.py ask --file /tmp/forecast-decision.json
.venv/bin/python src/project_control.py claim M3-1 --coordinator Claude --revision 1
.venv/bin/python src/project_control.py publish --file /tmp/forecast-progress.txt --source 'PLAN; docs/tasks/HANDOFF.md'
.venv/bin/python src/project_control.py close M3-1 '已依選項完成；證據：指定紀錄'
```

先查 `status` 再用實際版本，不固定沿用範例的 `1`。`close` 須由主持人確認完成後使用；自動續作執行中不能直接關閉。服務重啟將執行中但無結果的自動續作標成 unknown，不重跑；不把別的桌面主持人自動判作中斷。

發布決策時一次登記選項、續作與條件，避免分次登記之間被選定而提早執行：

```json
{
  "decision_id": "EXAMPLE-1",
  "question": "下一步先做哪項？",
  "options": {"A": "整理交接文件", "B": "等檢查通過再規劃驗收"},
  "expires_at": "2026-11-10T00:00:00Z",
  "actions": {},
  "handoffs": {
    "A": {"coordinator": "Claude", "next_step": "整理並發布交接", "blocker": "未登記自動續作"},
    "B": {"coordinator": "Claude", "next_step": "規劃驗收", "condition": "CHECK-1"}
  }
}
```

有完成證據後執行：

```sh
.venv/bin/python src/project_control.py milestone CHECK-1 '檢查通過' --source '測試紀錄路徑及提交'
```

對舊的、未開始決策，可用 `handoff <ID> <OPTION> --file /tmp/handoff.json` 補上同格式的單一交接物件；不能改選項、到期時間或登記程式。這只補充路由／條件，不替 Louis 改選或替主持人宣稱開工。已經完成的里程碑會在接受該條件選項時直接判為可接手。

只有本機主持人審查過的 Python 程式才能填入 `actions`，例：

```json
{"B": {"script": "config/team_actions/offline_check.py", "sha256": "實際檔案的 SHA-256", "timeout": 300}}
```

路徑、雜湊在發布、接受、執行前核對；不允許連結、shell、任意參數／工作目錄，上限 600 秒。TG 或問答模型不能提供程式。續作與預測鎖互斥，不登記會再取相同鎖的預測／量測／模型工作。程序退出 0 只表示程序完成，實際驗收證據仍須主持人／程式發布。

需要依使用者明確要求發送單次通知時：

```sh
.venv/bin/python src/project_control.py notify --file /tmp/forecast-notice.txt --source '本輪問題回報；docs/tasks/TELEGRAM-HANDOFF.md'
```

通知交給既有 TG outbox，綁定目前配對帳號；在 TG 服務在線且網路可用時送達。長回覆分段，不截掉後面的選項。通知入帳不等於已送達；只有 `telegram_events.jsonl` 的 `delivered` 才是送出回執。

## 安裝與維護

沿用 `src/telegram_bot.py` 的 `pair`、`install`、`uninstall`、`preview`。憑證只放 `.env`，只接受配對私人帳號；拒絕群組、轉傳、bot、代發。問題與決定超過十分鐘不執行；已消費 update 不重做，但新 update 可以修改未開工且未到期的選項。sendMessage 網路回執遺失仍可能重複訊息，不會重複決策或執行。

Mac 須開機、登入、連網且未休眠。重載前確認沒有隔離驗收正在觀測 LaunchAgents，且 TG 沒有執行中的問答／續作。只重載 `org.forecast-lab.telegram`；它不是預測排程服務。
