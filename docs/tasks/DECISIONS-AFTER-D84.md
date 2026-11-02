# D84 後需要 Louis 處理的事項

## 2026-11-02 晨間更新：D89 已採用（本段為最新狀態）

Louis 已選第一項 B，並明確表示額度不用刻意燒掉、還要開發其他專案。PLAN v1.37／`m2_report_v4` 已將 V5 改為被動蒐證：Claude 已觀察，GPT／Gemini 未觀察；未觀察不單獨阻擋 M2。模型核對、未知錯誤拒絕、退避／補跑及所有預算保護保持不變。Gemini 獨立審查 PASS；相關測試 108 passed，隔離完整 1404 passed。

最新報告 `reports/m2report_1793579888231_71185368.json`：14 項門檻通過 13 項，**只剩 `trusted_gemini_costs`**；21 筆 Gemini 成本仍不確定。此報告讀既有帳冊，未再跑模型題目。

Louis 表示已關閉其他 agy 工作；UTC 00:34:36、且本輪 Gemini 審查尚未啟動時，既有偵測仍為 `interactive=1`、`ide=7`（程序數，不是 7 個獨立工作）。依 D75，仍不能做可信歸因。需完全退出剩餘互動式 agy 與 Antigravity 桌面程序再量測；沒有擅自終止工作或回填成本，既有兩條第 2 層週期熔斷保持不變。

平台詢問已獲 Louis 直接寄送授權，UTC 約 00:37 經 Metaculus 官網 Contact Us／Bug report 提交一次；使用持有人 PrudentAggregate 的既有登記信箱（官網自動填入），詢問 bot 311763 的 MC canonical option mapping／grant、私人留言 HTTP429 與 Retry-After、私人留言長度上限。表單先顯示載入、之後關閉，無錯誤／工單 ID；沒有獨立郵件送達回執，待官方回覆，勿因此重送。未使用協助者 Gmail，未寄任何憑證／真實題文／模型推理。官網瀏覽身分已恢復 bot。安全紀錄追加在 `data/platform_inquiries.jsonl`。

模式仍 local，未新增預測或留言 POST、未啟用 launchd。下一個可獨立處理的技術工作是修復 Retry-After 保留與退避；既有 probe 已到兩次留言上限，不能再補第三次。

以下保留夜間報告原文；其中「V5 尚未決定／詢問尚未寄出」已由本段取代。

建立：GPT，2026-11-01T17:32Z。這是待決事項，不是核准紀錄；既有門檻繼續生效。當時固定 M2 仍在背景執行，V15 元件工具仍待獨立審查。本報告不要求中止已授權的量測。

## 1. V5 要維持前置門檻，還是改為被動蒐證

PLAN §10 M2／§12 V5 要求量到三家 CLI 的真實額度耗盡樣態。目前只有 Claude 有真實證據；GPT Pro、Gemini Ultra 尚未自然耗盡。離線的錯誤／停線／退避案例不能冒充這兩家的實機證據。

Gemini 兩次夜間審查均建議保留未完成標記，在正常授權工作中被動蒐集；不要為了關閉驗收欄位而刻意燒掉額度。GPT 採納這個預設。若要在自然耗盡之前完成 M2 驗收，改變既有門檻需要 Louis 決定。

- **維持現行門檻**：沒有政策變更；繼續有界試驗及離線工作，正式參賽仍等待真實樣態。缺點是高額度方案不一定很快出現耗盡。
- **改成被動蒐證**：提議把 M2 的這一項改為「已觀察的真實耗盡樣態列入 adapter 回歸；其餘列未觀察，保留既有模型核對、未知錯誤拒絕、停線／退避與最多一次補跑驗收」。未觀察不宣稱通過實機測試，但不再單獨阻擋 M2。這是驗收政策調整，尚未採用；若選此方案，須同步 PLAN 正文、決策／變更紀錄及報表判定，並再獨立審查。

有界耗盡探測是另一種選擇，但仍會消耗訂閱容量，不是本輪自行執行的預設。沒有額度重設、個別題目重跑或安全停線豁免。

## 2. Gemini 成本需要一段沒有其他互動程式的量測

依 D75，互動式 agy／Antigravity 開著時允許正常預測，但不能把帳號額度變化全部歸因給該次呼叫。第二個 M2 第一段的 20 次 Gemini 成本均不確定，可信樣本為 0；不把它們混入成本估計或覆蓋率驗收。

**17:42:03Z 已發生**：Gemini 第一筆第 2 層雖完成，但 `model_check=undetermined`，前後快照均顯示 2 個互動式 agy 與 6 個 IDE 程序。既有 `weekly_tier2_argon_breaker` 因成本不可信而暫停後續升級，`until=2026-11-08T12:11:16Z`（台北 11/8 20:11:16）。這不是額度耗盡的 V5 證據，也不能因為實際剩餘額度很多就自行解除。單純現在關閉程式不會清除既有熔斷紀錄。

需要在保留未存工作後，關閉其他互動式 agy／Antigravity，再安排剩餘的合法量測。GPT 未擅自終止使用者的互動工作。不能事後將這 20 次改標可信，也不能刪帳冊或重新取得同題嘗試額度。第二層若依既定政策因不可信成本觸發週期熔斷，也不由主持人直接解除。

## 3. 多選題的可靠讀回需要平台資料契約

bot testing area 的多選題目前把 `all_options_ever`／`options_history` 回傳 null。[官方 API 文件](https://www.metaculus.com/api/) 說明這些欄位受 gateway 權限限制；null 不等於從未修改選項。官方 `bulk-forecast-read` 使用同一個向量 serializer，仍不能取得向量對應的完整歷史標籤。

現行 `api_transport_v3` 會在首次 POST 前拒絕無法證明選項順序的多選題。是非題的元件驗收可獨立推進；多選缺口不能透過偷偷改用另一個存取路徑或猜測順序放行。若平台提供自己的逐選項讀回契約或適當 project grant，再據此實作及驗收。

以下為可寄給官方 API 聯絡窗口的草稿；**尚未寄出**：

> Subject: Reliable readback of our own multiple-choice forecasts (bot 311763)
>
> We are preparing a subscription-CLI forecasting bot for FutureEval. Our bot account is 311763. We are testing only in the approved bot testing area before tournament participation.
>
> For our own multiple-choice forecasts, MyForecastSerializer returns an ordered forecast_values vector. However, all_options_ever and options_history are null for our account, including on bot-testing post 46223 / question 46450. We understand these fields may be filtered by the API gateway.
>
> Is there a supported way to retrieve our own forecast keyed by option label, or to obtain the canonical option order for this vector (including inactive options)? If a project-specific grant is required, what is the appropriate process? We do not need community prediction history.
>
> We currently reject ambiguous writes and do not resend when a request outcome is unknown. We would also appreciate confirmation of the supported private-comment length limit.

## 已有授權，不再重問

D78 已核准機器人 311763 的 trial；固定 M2 執行結束及傳輸修正通過後可依原範圍做試驗。D84 已授權 GPT 主持與 Gemini 討論例行技術選擇。正式參賽的規則／白名單仍要 Louis 核准；launchd 若出現現場權限提示，按 D78 由 Louis 處理。這些未完成事項不能用「夜間自主執行」取代。

## 操作補記：GPT 的本地升級上限

17:54:01Z 已出現 `weekly_tier2_token_breaker`，至 2026-11-08T00:00:00Z（台北 11/8 08:00）。新題集 GPT 第二層已完成 4 題後，由既有「第 1 層 token 中位數 ×3」的每題規則停止後續升級。這不表示 GPT Pro 訂閱用完，也不是 V5 真實耗盡證據。既有週期保護不在這輪自行解除；之後若調整初始成本政策，須升版、獨立審查並明確處理既有熔斷紀錄，不能重設歷史。

已透過非同步問題請 Louis 在醒後決定 V5 是否改為被動蒐證；尚未收到回覆，原門檻繼續生效。相關模型工作與 API 元件驗收準備仍在既有授權內繼續。

## M2 最終報告補記（18:23:47Z）

固定題集已正常完成所有 20 題，三線首層各 20/20、整合與本地留言 20/20、無未知啟動；最新報告 `m2report_1793557427841_59e65bea` 僅剩可信 Gemini 成本與 V5 兩項。Gemini 成本不確定共 21 次（首層 20、第二層 1），不是只有先前第一段的 20 次。沒有解除任何既有預算保護。

## 本輪真正停下的位置（18:46:56Z）

有界 trial 已執行並回 local：synthetic binary 預測送一次、獨立讀回成功；私人留言兩次均被拒，第二次明確 HTTP429，已到本筆上限而停止。這是限流證據，**不是帳號無權限的證據**；第一筆的確切狀態未保存，不能回填。報告 `reports/v15report_1793558816848_4ba002b2.json`。尚未完成 V15／M3。

既有平台詢問草稿可再補充這段（仍未寄出）：

> Our private comment request to POST /api/comments/create/ at 2026-11-01T18:44:30Z received HTTP 429. It used on_post 43327, is_private=true, included_forecast=true and a short synthetic component-test message. Our one forecast on question 43332 was accepted and independently verified. We stopped after two rejected comment requests (the first exact 4xx status was not retained). What per-account/per-endpoint limits and Retry-After behavior should our client respect for private comments and the preflight reads? No additional comment or forecast retries are running.

429 節流與 Retry-After、完整留言／MC／pipeline 驗收仍是後續技術工作，不是要求 Louis 逐筆判斷預測。現行傳輸包裝會移除 Retry-After，此可靠性缺口已在交接列明，不能把現有短退避說成尊重了平台指定等待時間。沒有增加本次 probe 的次數或新開另一題繼續測。

V5 決策與 Gemini 量測環境仍須 Louis 處理；平台契約則待官方回覆。本輪已把可稽核的 code、測試、實機部分結果與失敗保存，未將任何缺口改記為通過。
