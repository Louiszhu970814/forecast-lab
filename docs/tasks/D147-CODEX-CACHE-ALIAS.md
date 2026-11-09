# D147：Codex 外掛版本別名相容與第 4 題續作

- 日期：台北 2026-11-08；整合者 GPT。
- 授權：Louis 指示「處理做一下第四題 順便看一下人類cup賽有哪些新的題目」；沿用 D144 日常開發目錄內容變動不再停線及 D139 正式排程。
- 狀態：已於 UTC 2026-11-08T10:12:26.669Z 正式部署、六項前檢全過；第4題三線兩層全部完成，預測及留言均已確認送達。

## 原因與修正

台北 11/8 的第 4 題尚未提交；Claude／Gemini 的兩層工作已完成，GPT 尚無該題嘗試。GPT 自 UTC 04:29:38 起因 `codex.plugins` 的目錄符號連結被前檢阻擋。實際連結為官方 Chrome 外掛快取 `cache/openai-bundled/chrome/latest`，指向同層的實體版本目錄 `26.1027.51404`。

排程器依原規則等待各線完成或決策截止才定稿。因此 `awaiting_scheduler_finalization` 與暫存整合缺少正式留言是等待 GPT 的結果，本次不修改整合／排程器、不強制定稿、不重跑已完成或已提交的題目。

`baseline_v4` 僅在 `codex.plugins` 支援 `cache/<provider>/<plugin>/latest` 指向同層實體三段數字版本目錄。連結本身記入雜湊但不遞迴跟隨；真正版本目錄由原走訪流程獨立盤點。外部路徑、跨外掛、私密路徑、連結鏈、祖先、未知格式、排除目標、憑證與目錄讀取錯誤仍拒絕；其他來源維持原處理。D144 資訊性差異與 `catalog_drift=False` 嚴格證據綁定保持。

不修改使用者外掛、既有基準核准、正式 CLI pins、模型／effort、prompt、工具權限、額度／截止或送出規則。由自然排程在恢復後接續 GPT 原本未開始的工作。

## 驗證與審查

- 隔離副本相關測試：106 passed；涵蓋絕對／相對合法別名、內容及目標變更的嚴格綁定、11 種拒絕情境。
- 實際外掛目錄唯讀盤點：1669 entries，無 unsafe_symlinks、not_read 或讀取錯誤；這不是模型隔離重驗或重新核准基準。
- Gemini 透過官方固定 agy／`gemini-4-argon-high`／high 完成工具關閉的來源封包審查，[r1 PASS](../../reviews/gemini-d147-r1.md)、無 blocking。其結論基於完整來源及主持人測試證據，沒有自行執行測試。用量另記開發審查帳，不混為正式預測。
- 私人證據：`data/operations/d147-codex-cache-20261108/`。完整回歸、六鎖原子套用、保護檔雜湊、前後三線兩層前檢與失敗回滾依既有 D144 流程；實際結果下方追加。

## Cup 唯讀盤點

UTC 10:08:22 的開放清單：29 道一般題，與 UTC 11/5 05:57:23 的舊清單相比無新增；紐西蘭組閣題已不在開放清單。公告 notebook 45384 不計為可作答題。COP31 為 D142 本人回報已提交；其他 28 題尚無本專案研究紀錄，不能據此判定本人平台作答狀態。

UTC 10:10:11 另查到 3 道 upcoming 題，均預定 UTC 11/9 18:00（台北 11/10 02:00）開放：台灣地方選舉國民黨縣市首長席次、Kiko Llaneras 與社群預測的分數差、Ilhan Omar 是否被提出刑事或民事移民指控。這是官方題目文字，並非對該人物的事實指控。題目、時間與來源保存在 `cup-extra.jsonl`。

依 D142，尚未研究題目維持截止前 48 小時再研究；未啟動人類賽模型研究、提醒或提交。

## 正式套用

- 完整隔離回歸 **3660 passed in 157.40s**；部署後根目錄相關 **106 passed**。
- UTC 2026-11-08T10:12:26.669Z 取得六把既有鎖後完成兩檔原子套用：`src/baseline.py`、`tests/test_codex_cache_alias.py`。115 項受保護來源／設定／prompt／wrapper／AGENTS 與 STOP_SUBMIT 存在狀態保持；前後 SHA 及備份見 `source-manifest.json`、`backup/`。
- 部署前 GPT baseline stop；部署後三線兩層前檢全過、waivers 空、無 line guard 停線。沒有新增 baseline_approved 或 line_resume，沒有修改外掛、手動模型預測／POST或 launchd 設定。
- UTC 10:13:44 既有自然 cycle 已實際記錄三線 ready；後續只觀測第4題的 attempt／整合／送出 metadata，完整轉換紀錄見 `recovery-monitor.jsonl`。
- Gemini 非阻擋建議為未來可細化盤點競態的錯誤記錄；目前競態會由外層安全拒絕。本次未擴充此項，也未將審查視為模型隔離新驗收。

[來源與驗證 manifest](../../reviews/d147-source-manifest.json)。

## 第4題實際完成

- GPT 第1層於 UTC 10:13:56.014 開始、10:14:44.380 完成；第2層於10:15:08.559開始、10:16:48.548完成，均 valid／無 error_type。原 Claude／Gemini 四次嘗試完整保留，沒有重跑。
- 最終整合 `all_lines_accounted_for`、missing_players 空。自動排程於 UTC 10:18:17.516 確認 forecast_ok、10:18:25.604 確認 comment_ok；台北均為18:18。留言於10:18:41.681獨立讀回 found。
- 只有一筆 forecast request_started 與一筆 comment request_started。預測初次回執 unknown 由既有自動對帳確認，沒有重送。該cycle因此保留歷史 attention，但 errors 空；不能把這個歷史標記解讀為仍待處理。
- UTC 10:18:59.848 核對：今日4題與累計36題全部預測／留言確認（主賽13、MiniBench10、Market Pulse13）；pending model attempt 0、未完成live intent 0，三線ready。證據：`completion.jsonl`、`recovery-monitor.jsonl`、正式submissions／submission_readbacks／cycles帳冊。
- 未檢視開放題機率／理由／正文，未人為改題目答案或強制定稿；本輪手動預測模型呼叫與POST均0（GPT兩次及實際送出由既有正式排程執行）。未commit／push，保留既有D146未提交文件變更。
