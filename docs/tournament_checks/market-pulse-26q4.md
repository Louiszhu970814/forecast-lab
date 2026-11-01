# Market Pulse 26Q4：參賽準備核對

核對時間：2026-11-01T13:09:04Z。狀態：Louis 已表達要以機器人參賽；規則核對與技術準備尚未完成，未啟用送出。

2026-11-01T13:12:22Z 更新：Louis 回覆「我新辦的帳號沒有在那邊參加過預測」。依本人確認，本季尚無人類帳號預測，採既有機器人身分參加；重複參賽的詢問已解決，不再列為待答問題。

## 依據與已確認事項

- [官方本季公告](https://www.metaculus.com/notebooks/46009/market-pulse-challenge-26q4/)：獎池 US$7,500；允許 forecasting bot 領獎，同一參賽者只能選機器人或人類其中一種身分。預定 12 組、65 個子題；使用截止時點的 spot scoring。公告列出 project ID 33131。
- [本季賽事頁](https://www.metaculus.com/tournament/market-pulse-26q4/)：標示 12/21 為最後可參與獎金的日期，須留意各子題自己的截止時間。
- [通用競賽規則](https://www.metaculus.com/tournament-rules/)：免費參加；領獎須依主辦方要求提供身分、稅務與收款資料，由 Louis 處理。
- 唯讀官方 API `GET /api/projects/tournaments/market-pulse-26q4/` 確認 `id=33131`、`slug=market-pulse-26q4`、`prize_pool=7500.00`。API 的 `close_date=2027-01-06T18:00:00Z` 與頁面日期不同，不得把 project 的 close_date 當作子題作答截止或延後參賽的依據。
- 機器人帳號 API 回傳 `is_subscribed=false`，只代表未追蹤；沒有查得報名成功證據，不把追蹤狀態當作參賽狀態。

## 尚待完成

- 依 PLAN §3.7 核對本季對重複更新、推理留言、人類介入、外部預測來源、模型與程式披露、參賽表單的完整規定。FutureEval 的「每題一次」及問卷／留言規則不可直接視為本季規定。
- 確認報名入口與成功證據；目前只做唯讀核對，沒有報名或寫入預測。
- 補上數值分布、數值題組與每個子題的時程／對帳支援；現有 `poll_questions.py` 只支援單一是非與多選題。
- 由 Claude 整合 Louis 的參賽決定至 PLAN §15，完成相應版本與驗收；沿用 §3.6 的 trial/live 完成條件，現有模式保持 local。

開發需求見 [EXPAND-TOURNAMENTS.md](../tasks/EXPAND-TOURNAMENTS.md)。本檔沒有任何賽題預測或社群機率。
