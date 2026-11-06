# Metaculus Cup Fall 2026：人類賽輔助準備

> 2026-11-04 補記：D79 已排除 Cup 機器人試驗；D132 的兩題有界 advisory 入口及私人檔案報告已完成，操作見 [D132](../tasks/D132-CUP-ADVISORY.md)。Telegram advisory 專屬發送入口仍未實作。下文 11/01 準備紀錄保留為歷史；本輪重新核對官方通用規則，仍允許人類使用 AI 輔助、本人判斷及提交。

核對時間：2026-11-01T13:09:04Z。狀態：Louis 接受 AI 協助研究、本人判斷及手動提交的方式；尚未啟用 advisory 管線。

## 已確認事項

- [本季賽事頁](https://www.metaculus.com/tournament/metaculus-cup-fall-2026/)：獎池 US$5,000；社群勝過客座預測者時另增 US$2,500，後者是條件式獎金，不列入確定收入。每週開出新題，各題可能提早關閉。
- [通用競賽規則](https://www.metaculus.com/tournament-rules/)：人類可用 AI 輔助，但最終提交須反映本人決定與理解；一般機器人帳號不得領獎。免費參加，領獎文件與收款由 Louis 處理。
- 唯讀官方 API `GET /api/projects/tournaments/metaculus-cup-fall-2026/` 確認 `id=33108`、`slug=metaculus-cup-fall-2026`、`prize_pool=5000.00`。API 的 `close_date=2027-01-04T00:00:00Z` 與頁面的 2027-01-01 不同，個別題目期限須即時讀取，不以賽事日期延長作答時間。
- 本次 API 使用既有機器人身分，只確認公開賽事資料，不能據此判定 Louis 人類帳號已報名或已作答。

## 參考報告契約

- 按 PLAN §8，由 Louis 指定題號；報告包含題目連結、截止時間、各線預測、整合結果、主要理由、來源、不確定性與產生時間，供 Louis 自行判斷。
- 人類賽的資料與輸出獨立保存；不進入機器人賽的評估、不讀取或使用 Louis 私人預測來修改機器人。
- 發送到已配對的私人 Telegram 前，檢查是否與機器人獎金賽的 open/upcoming 題目重疊；重疊或歸屬不明時不產生／傳送可供預覽後調整機器人的答案。
- 模型不開啟 Metaculus 網站；Louis 可自行參考社群預測。程式不取得人類 token，不替人類帳號提交預測或留言。

## 尚待核對與實作

- 本季預測／留言獎金細分、計分與更新規定，以及需要 Louis 完成的帳號／參賽步驟。
- 通用規則的單次參賽限制，與 PLAN §8 所述「同季機器人試驗、人類參賽」是否相容；沒有確認前，不新增同季 Cup 的機器人試驗送出。
- `advise.py` 與 advisory 專屬 Telegram 入口尚未實作。依現有 §8／§9，機器人賽排程及預算優先，不能直接讓新的人類賽工作繞過額度限制。

開發需求見 [EXPAND-TOURNAMENTS.md](../tasks/EXPAND-TOURNAMENTS.md)。本檔不表示已替 Louis 報名或提交預測。
