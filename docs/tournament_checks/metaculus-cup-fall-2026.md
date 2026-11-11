# Metaculus Cup Fall 2026：人類賽輔助規則與操作

> 狀態（D79／D132／D139／D142）：Metaculus Cup 已排除於機器人試驗場地之外，僅供 Louis 以本人帳號參加人類賽。人類賽參考預測入口已由 `src/human_advice.py`（`advisory_v2`，含 D142 英文預測與留言草稿）及有界單題 `src/advise.py`（[D132](../tasks/D132-CUP-ADVISORY.md)）啟用，一律由本人桌面手動呼叫、本人判斷並自行在網站送出。

## 已確認賽事規則（2026-11-01／11-04 核對）

- [本季賽事頁](https://www.metaculus.com/tournament/metaculus-cup-fall-2026/)：獎池 US$5,000；社群勝過客座預測者時另增 US$2,500（條件式獎金）。每週開出新題，各題可能提早關閉。
- [通用競賽規則](https://www.metaculus.com/tournament-rules/)：人類可用 AI 輔助研究，但最終提交須反映本人決定與理解；一般機器人帳號不得領獎。免費參加，領獎文件與收款由 Louis 本人處理。
- 官方 API `GET /api/projects/tournaments/metaculus-cup-fall-2026/`：`id=33108`、`slug=metaculus-cup-fall-2026`、`prize_pool=5000.00`（設定於 `config/human_tournaments.json`）。個別題目期限即時讀取，不以賽事結束日推算。

## 參考報告契約（`PLAN.md` §8）

- 透過桌面手動執行 `src/human_advice.py`（自動挑題或指定題號，單次最多 3 題，預設英文輸出與私人報告檔於 `reports/advisory/` 及 `data/advisory/`），包含題目連結、截止時間、各線預測、整合結果、主要理由、來源與不確定性，供 Louis 自行判斷。
- 人類賽資料與輸出獨立保存於 `data/advisory/` 與 `data/advisory_usage.jsonl`；用量計入共用預算守衛且優先序低於機器人賽（`main`、MiniBench、Market Pulse 優先），不影響機器人賽評估，也不讀取 Louis 私人預測來修改機器人。
- 執行前檢查是否與已核准機器人獎金賽的 open/upcoming 題目重疊；重疊或歸屬不明時拒絕產生參考預測。
- 模型不開啟 Metaculus 網站；程式不取得人類帳號 token，永不替人類帳號自動提交預測或留言。詳細擴充設計見 [D139 交付](../tasks/D139-EXPANSION.md)。
