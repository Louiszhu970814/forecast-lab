# D90：API Retry-After 與持久冷卻

整合：GPT，2026-11-02T00:53Z。授權：D84 例行技術工作，沿用 D89 不刻意耗盡額度的方針。僅離線合成測試與必要程式審查，沒有新增真實 API 預測或留言請求。

## 問題與修正

原本 `APITransport._safe_open` 會移除 Retry-After，GET 客戶端又把等待截成最多 60 秒；D88 私人留言 HTTP429 後，無法證明有遵守平台指定期限。

現在新增 `api_backoff_v1`／`metaculus_read_v1`，傳輸升為 `api_transport_v4`、送出器升為 `submit_v9`。所有正式預設 API 客戶端與傳輸共用 `data/api_cooldowns.jsonl`；只追加本地固定欄位、HTTP 狀態碼及 UTC 時間。秒數或 HTTP 日期經解析後保存正規化期限，遠端正文、reason、其他 headers 都不保存。解析依據為 [RFC 9110 §10.2.3](https://www.rfc-editor.org/rfc/rfc9110.html#name-retry-after)。

- 讀寫前檢、取得回應標頭與記錄冷卻共用程序鎖；重啟、新客戶端或不同 API 端點都不能跳過尚未到期的期限。
- 最多 60 秒的 GET 等候結束後再次核對時間；更長的等待直接交回排程，保留完整期限，不截短。送出器也不為長期限持續睡眠。
- 429 缺少有效未來期限（含零秒或已過期日期）時，採本地保守 60 秒；GET 同時保留原指數退避下限。這是本地政策，不冒稱官方限流規格。
- 極大秒數保守延至可表示的最晚日期；壞掉／不完整的帳冊會在網路請求前阻擋。新紀錄不縮短任何舊的有效期限。
- 寫入仍完全不自動重試；ConfirmedFailure 的下一次合法時機取本地退避與平台期限的較晚者。unknown 仍只能對帳，不因到期而重送。
- 昨晚 probe `v15_1793557715641_91989f46` 已達兩次留言上限，沒有第三次留言，也沒有重送預測。未回填它未保存的標頭。

## 驗證與獨立審查

隔離副本 `/var/folders/mz/kz1rwmd573d6g9rsxv_5nq480000gn/T/forecast-http-backoff-3djci50i`，從專案外父目錄執行，TMPDIR 位於副本外的 `.tmp`。最後相關測試 **410 passed**；完整 **1445 passed in 78.65s**。新增 41 個案例包含 HTTP 日期三種格式、超長等待、跨實例／並行、重啟、帳冊損毀、短睡眠被打斷、零秒／過期標頭、狀態值拒絕，以及只有一次 POST。

Gemini 官方 agy 首審 BLOCK；採納零秒／過期標頭造成零等待及 Deferred 資訊遺失兩項。對「60 秒再向上捨入毫秒會略超過內聯上限」保留保守延後的設計：它不會提前重試，也不丟掉期限，新增邊界回歸證明。另補長等待交回排程及異常 HTTP 狀態白名單。Gemini 複審 **PASS**，無剩餘問題。套回主專案前比對基準，套回後比對所有程式／測試 hash；驗證證據追加於 `data/development_checks.jsonl`。

## 本輪最後狀態

PLAN v1.38；模式 local。未啟用持續排程／launchd，未開新預測量測。D89 的最新 M2 報告仍為 `reports/m2report_1793579888231_71185368.json`，14 項過 13 項，只剩可信 Gemini 成本；V5 是被動蒐證（Claude 已觀察、Gemini／GPT 未觀察）。既有第 2 層週期熔斷沒有解除。

所有 Gemini 複審結束後，UTC 00:53:02 再測為 `interactive=1`、`ide=6`（程序數），無其他 headless review；依 D75 仍不能做可信成本量測。已請 Louis 完全退出互動式 agy 與 Antigravity 桌面 app 後告知；未強制終止使用者工作。

Metaculus 官網詢問已於 UTC 約 00:37 提交一次，使用持有人既有登記信箱，問 MC 選項對應／權限、私人留言 429／Retry-After 與長度上限。表單完成載入後關閉，沒有錯誤或工單編號；尚無獨立郵件送達回執／官方答覆，不因此重送。操作紀錄為 `data/platform_inquiries.jsonl`。此次程式修復不是平台契約答覆，也不是 V15／M3 實機驗收通過。
