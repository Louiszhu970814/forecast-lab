# D115：Luke 官方回信與實作影響

來源：Louis 於本輪對話貼上的 Metaculus／Luke 回信全文，接收紀錄為 UTC 2026-11-02，原信寄出時間未提供。全文僅保存在本機追加式 `data/operations/d115-official-reply/source.jsonl`。本頁是摘要與程式核對，不把回信視為 Louis 核准正式參賽或授權使用人類帳號。

## 已確認的平台說明

1. **多選讀回**：先前 `all_options_ever`／`options_history` 的 null 來自標準帳號的 API gateway 遮蔽。Luke 表示閘道已更新，所有 authenticated 帳號均應拿到這兩欄，不需 project grant。`forecast_values` 按 `all_options_ever` 對位；移除選項的位置保留 null，更名保留位置，Other 最後，`options_order` 只控制畫面。寫入沿用每個目前標籤的字典、單值 0.001～0.999、總和 1，inactive 省略或 null。
2. **限流**：429 來自 Cloudflare；同一來源 IP 共用每 10 秒 8 個請求的限制，涵蓋 `/api/*`、`/api2/*`、讀取及失敗請求。429 後至少停止所有流量 10 秒，並遵守更長的 Retry-After；提早重試會延長阻擋。專案共用鎖只能協調本專案的程序，不能控制同一 NAT 的其他電腦、瀏覽器或 bot。
3. **私人留言**：150000 Unicode code points 是官方確認的長度上限，超過回 400。超過 500 字元的 bot 私人留言在 30 天後封存，API 回短摘錄與完整文字連結，且不能再編輯。這補足 D113「只量到最大已接受長度」的限制；不需另送 150001 字元探測。
4. **Bulk**：官方確認批次預測＋私人留言為 atomic，任一錯誤就全批不儲存；bulk forecast read 可取回多題自己的預測歷史。`acting_user` 是平台能力，本專案仍只用核准 bot token，不使用 owner／人類帳號送出。

## 現有程式核對

- `submit_transport.py` 已優先使用 `known_option_order()` 與 `all_options_ever`，逐位置核對標籤對應值，inactive 必須為 null；不使用 `options_order`。D99 的多重集合相容分支只在 `all_options_ever is None` 時使用，不能把該分支的 found 當作標籤對應已驗證。兩位審查者均支持收緊新寫入；主持人採更嚴格的 `api_transport_v7`，並移除 null 多重集合讀回退路。缺失／異常順序均保持 unknown，不重送；已受理歷史不重寫。
- `api_backoff_v2` 已將正式讀寫共用 3.5～4.5 秒間隔與持久冷卻帳冊；沒有／零／過期 Retry-After 的 429 會等 60 秒。但舊版對有效且短於 10 秒的 header 仍照較短時間等待，已修為 `api_backoff_v3`：有效正值取至少 10 秒，更長 header 保留；非 429 不變，無／零／過期仍 60 秒。完整隔離測試與 Gemini 複審均已通過。
- `comment_v1` 的正式上限為 3000，使用 Python `len()` 計字元，低於官方上限；不需增大。封存後的摘錄不能當作完整留言 hash 相符；讀回未知仍維持未知，不觸發重送。既有本地 append-only 帳冊保留原留言與 hash。
- Bulk 會改變意圖／原子失敗／分項讀回契約，不能只換 URL。它可減少請求，是另外設計與驗收的候選；本輪原四題與 A2 已完成，不為新資訊重送。

## 觀測器的獨立審查

Claude 對回信到達前的 D114 原型指出 B1：offset 分頁若在列舉期間變動，可能漏掉 ID 卻被視為完整場地。已加入兩次完整盤點與集合、count 核對；Claude 複審通過後才登記並啟動觀測，實際窗口與限制見 [D114](M3-METADATA-D114.md)。

主持人另核對[官方 OpenAPI](https://github.com/Metaculus/metaculus/blob/main/docs/openapi.yml)、[PostFilterSerializer](https://github.com/Metaculus/metaculus/blob/main/posts/serializers.py)與 [feed](https://github.com/Metaculus/metaculus/blob/main/posts/services/feed.py)：公開文件提供 `order_by=published_at`，沒有 ID 排序；backend 對未識別排序退回預設，而非可靠地報錯。因此不能猜 `order_by=id` 已生效。即使明列 published_at，offset 列舉仍不是原子快照，需加完整性核對或明列不足。

官方 OpenAPI 在本輪讀取時仍寫這兩個歷史欄位受 gateway 遮蔽，與較新的回信不一致；已由有界 GET-only 核對確認 bot 帳號在既有測試題拿得到兩欄。這是該題的實機證據，不宣稱已逐題檢查整個平台。

## 兩位審查者與下一步

Louis 已明確要求 Claude、Gemini 都看過最新回信後再決定。全文、相關程式、D113 缺口與 Claude 的原觀測器審查已交付兩條官方 CLI，僅做無工具靜態評估；兩者均已完成回信評估（Claude Opus high、Gemini Argon high）：同意先修 429、分頁與文件，再以 bot 做有界 GET 核對、啟動觀測；bulk 後續另案，live 尚未核准。詳細原文在 consultations.jsonl。

回信沒有補上 V11 的實際可見性／輪詢／修訂及覆蓋率證據。M3 完成與正式窗口、系列白名單、live 核准仍依 PLAN §3.6／§10。

## GPT 整合決定與核對

- 429 與分頁修正採兩者共同建議；分頁不一致維持原定「整次停止」，不在看資料後改成容忍或續期。
- MC 兩者對保留只讀 fallback 的必要性意見不同。主持人於 UTC 22:56 僅核對請求狀態：正式帳冊 164 列、四筆 intent、未解決請求 0；probe 14 筆 request 也無未解決。因此採移除 fallback，沒有需要相容的未結請求。新缺欄位直接拒絕；送出後若欄位消失仍 unknown，永不重送。
- `submit.py:forecast_payload()` 已檢查全部 active 選項、原值與送出值均在 0.001～0.999、前 K−1 個六位小數及最後項補足總和 1；不新增 inactive 標籤。無需改 payload。
- 不採兩位回覆中超出證據的說法：未量出實際頁數前不稱 80 次 GET 必然足夠；3000 是最大長度，只有實際超過 500 字元的留言才符合封存條件；D107 A1 的既有有界 trial 常設授權仍存在，但本輪沒有使用平台寫入。
- Gemini 對 `api_transport_v7`／`api_backoff_v3` 的最終複審 PASS、無 blocking；Claude 對觀測器／重播工具的技術複審 PASS。完整隔離 **2089 passed（80.90 秒）**，來源檔與通過測試的副本已逐檔核對；未把工具通過當成 M3 實測通過。原文存於 `final_reviews.jsonl`，測試證據在 `data/development_checks.jsonl`。

## 有界實機核對結果

Gemini 複審後進行 schema 核對，每次計畫均先固定請求數、零自動重試，只保存 ID、欄位型別／數量／布林值，不保存或展示預測值、選項正文。

1. 第一組最多 2 GET：`users/me` 確認 bot 311763；第二次誤把 question 46450 當 post ID，取得 404 即停。失敗保留於 `schema_checks.jsonl`，不算成功。
2. 從既有凍結 probe 僅讀 ID 映射，取得正確 **post 46223／question 46450**。另行固定 2 GET 後，於 UTC 23:09 確認 bot 身分、`all_options_ever` 與 `options_history` 非 null、歷史向量長度與選項歷史順序長度相符、精確讀回前檢可用。未觀察到 inactive-null 的實際案例，此項仍由離線測試驗證。結果存於 `schema_checks_corrected.jsonl`。
3. 固定再做 1 GET 的結構核對，確認 `options_history` 是 `[字串, 選項列表]` 的 pair。時間字串未通過現有嚴格 ISO parser，不能稱已驗證歷史時間解析；目前精確向量讀回只依 `all_options_ever`，不受影響。未再猜測或請求正文，結果存於 `history_shape_checks.jsonl`。

合計 **5 GET、0 POST**（包括失敗的 404）；舊 probe 狀態機與舊受理紀錄不變。以上帳冊均在 `data/operations/d115-official-reply/`。後續 D114 的 metadata GET 另外計數。
