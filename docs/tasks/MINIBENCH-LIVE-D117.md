# D117：MiniBench 有限正式參賽

> 後續D118：Louis要求提前，已於台灣11/3 **12:12** 啟動；截止前移為11/10 **12:00**，舊用量保留計入。見 [D118啟動紀錄](MINIBENCH-EARLY-D118.md)。本頁21:03／waiting為D117當時的歷史狀態。

Louis 於 UTC2026-11-03 明確回覆「OK 請參賽」，接受 D116 條件式 M3-4／V11 及有限參賽方案。候選設定原樣採用：bot311763、僅MiniBench系列、台灣11/3 21:03～11/10 21:03、無自動續期；原D105窗口／用量不改，D114觀測繼續作補充。

GPT已完成配置與載入。UTC2026-11-03 01:53:56取得真實排程 `waiting` 回執；正式窗口尚未開始，尚未實際作答。完整操作證據追加於 `data/operations/d117-live/`；授權另記 `data/live_authorizations.jsonl` 的 D117。

## 實際啟用結果

- 範圍：bot **311763**，只有 MiniBench 系列送出白名單為 true；每線 Claude／Gemini／GPT 上限 **130／200／130** 次。runtime 已切為附有固定窗口合約的 `live`。
- 窗口：**UTC2026-11-03 13:03～11-10 13:03（台灣11/3 21:03～11/10 21:03）**。開始時間承接原D105預算窗口；不清零舊用量、不重疊，也不因延遲開始而順延截止。D114觀測不阻擋啟動。
- 設定：原樣採用 [D116候選](D116-MINIBENCH-CANDIDATE.json)，campaign物件SHA-256為 `1ab499dc15e0793c1534b91edbebb1c1f53a2a2e2e76397d7bd4731fc1d34466`。舊runtime設定逐位元封存於 `data/operations/d117-live/archived_config/`，manifest追加保存，沒有覆寫舊登記。
- 排程：`~/Library/LaunchAgents/org.forecast-lab.cycle.plist` 已lint、bootstrap及kickstart成功，600秒間隔、RunAtLoad=false；首次執行正常退出0，寫入 `data/live_schedule.jsonl` 的waiting。閒置時launchctl顯示not running是兩輪之間的正常狀態，不代表服務卸載。
- 驗證：安裝前後新增模型嘗試 **0**、預測／留言POST **0**，新campaign未提前寫入正式登記。第一個有效窗口內tick才以真正當下時間登記並執行前檢；到21:03後下一輪才可能開始，不保證整點即送出。
- 到期：不再啟動worker或新POST，無自動續期／擴大場地；已開始請求的回應可能稍後才返回。主機須維持開機登入、插電、連網且不休眠，筆電不要蓋上。沒有更改電源設定或安裝額外常駐防眠服務。

安裝與驗證紀錄：`operations.jsonl` 的 `runtime_armed`、`launchd_bootstrap`、`launchd_waiting_verified`；驗證時間UTC01:53:56.527。Telegram服務未更動；主持人只更新本地共用進度，不另發外部訊息。程式／文件尚未commit或push。

## 啟動前驗證

真實前檢：Claude／Gemini／GPT第一、第二層全部ready（`preflight.jsonl`）；只有官方CLI狀態／基準與額度查詢，沒有預測模型呼叫。官方API唯讀核對：帳號311763且is_bot=true；當時ongoing MiniBench IDs33132／33135、alias33135（`discovery.jsonl`）。執行時仍重新發現與核對，不將這個快照當永久白名單。

`live_window_v1`將live模式綁定已核准campaign全文hash、ID、UTC起訖與run/quota設定hash；沒有此合約不得送出。每筆live意圖記錄合約hash，舊意圖不能借用新窗口。`submit_v11`在每次寫入前檢查有效窗口與場地；`api_transport_v8`在共用節流等候完、實際網路opener之前重查模式／bot／白名單／STOP／窗口及題目截止。被此檢查拒絕時已知未送網路，記confirmed_failed而非unknown；GET對帳仍可執行，未知結果不重送。

`cycle_v8`在開始前只記waiting，不產生worker、API或模型呼叫；到期後expired、到期前10秒內draining，也不啟動。有效窗口內逐次用真正現在時間驗證並登記原campaign，再依原流程前檢。worker上限截至窗口截止前10秒，保留既有整樹清理時間；模型／prompt／額度／600秒間隔與窗口內排程不變。已開始的HTTP請求可能在截止後才收到回應，但不在截止後開始新POST。

相關489passed；最終完整隔離2154passed（80.72秒）。原v7歷史重播不改寫、不冒稱在v8重跑；這次修改是有限啟用窗口邊界。Gemini／Claude靜態審查均PASS；Claude要求最終程式完整測試通過的條件已由2154passed滿足。依其非阻擋建議，另補真實檔案鎖Backoff→Submitter帳冊、題目截止及到期後GET對帳三項測試，相關19passed；完整測試後沒有再改正式程式。審查者沒有自行執行測試。

已揭露限制：本地最後寫入檢查拒絕會消耗一次既有請求上限（預測／留言各3次）；不放寬重試上限。只有這個已確認未呼叫opener的拒絕才記confirmed_failed；既有Deferred冷卻仍可能記unknown，不能把所有網路前錯誤都宣稱已分類為未送出。到期後launcher不再啟動worker，因此剩餘unknown需要另用GET-only `submit.py reconcile`對帳，不會自動重送。`APITransport`未設定callback的底層使用者仍須自行提供政策；正式Submitter每次都安裝callback並在finally清除，live亦已有交易內guard。`run_config_v3`是內容版本，讀取路徑仍為run_config_v2.json。
