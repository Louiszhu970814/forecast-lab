# V15 真實讀取格式核對與修正草案

> **SUPERSEDED／歷史文件（D136 標記）**：本頁保留當時提案與驗證經過，不是現行授權或待決清單。現行規格見 [PLATFORM-REPLY-D115.md](PLATFORM-REPLY-D115.md) 與 [PLAN](../../PLAN.md) 的後續決策。

GPT 於 UTC 2026-11-01T16:20:52Z 以核准的機器人帳號 311763 做唯讀核對。模式仍為 `local`，POST 次數為零。帳冊 `data/transport_probes.jsonl` 只記 ID、欄位名稱、容器型別及筆數，沒有題文、機率、留言內容或憑證。

## 發現

1. bot testing area 的 `question.my_forecasts` 是含 `history`、`latest`、`score_data` 的物件。目前沒有預測時是 `history=[]`、`latest=null`。原本 `submit.py` 的前置檢查與 `submit_transport.py` 的讀回都假設它是列表，所以實際 trial 會被前置檢查擋下；遺失回執後也無法對帳。
2. 官方原始碼的 `MyForecastSerializer` 回傳 question／author ID、Unix 秒數與機率向量，沒有 forecast 資料庫 ID。官方預測建立端點成功回傳 `201 {}`。因此不能把「取得 numeric ID」設為唯一成功確認途徑。[官方 serializer](https://github.com/Metaculus/metaculus/blob/9c1434b4cf3d3bd1b12836ad9085fe80153e6915/questions/serializers/common.py#L394)、[官方 view](https://github.com/Metaculus/metaculus/blob/9c1434b4cf3d3bd1b12836ad9085fe80153e6915/questions/views.py#L124)。
3. 留言清單的貼文篩選參數為 `post`，原本用的 `post_id` 不在 serializer 中。仍需逐筆核對貼文、作者、時間及文字 hash；不能只信伺服器篩選。[官方留言 filter](https://github.com/Metaculus/metaculus/blob/9c1434b4cf3d3bd1b12836ad9085fe80153e6915/comments/serializers/common.py#L23)。

官方來源固定為 commit `9c1434b4cf3d3bd1b12836ad9085fe80153e6915`；公開程式碼不是部署版本保證，仍須實機驗收。

## 隔離副本中的修正

位置：`/var/folders/mz/kz1rwmd573d6g9rsxv_5nq480000gn/T/forecast-readback-7xedbl_v/work`。截至此紀錄尚未套回主專案。

- `submit_v7`／`api_transport_v3` 草案共用自己的歷史容器解析。缺欄位、錯型別、無法辨識的空物件都不代表從未預測；撤回後仍有歷史，也不能當成未預測。保留舊 transport 列表契約的相容性。
- 新格式的讀回嚴格核對 bot／question ID、原請求之後且非未來的 Unix 時間與完整機率向量。以 question／author／start-time 三元組形成明確標示種類的穩定識別，不捏造遠端資料庫 ID。歷史與 latest 指向同一筆時去重；有多筆不同時間的匹配紀錄時維持 unknown。
- 多選題只在完整的歷史選項順序可確認時對應向量，保留停用選項的 null。讀不到順序就 unknown，不把可能錯位的向量當成成功。
- 留言查詢改用官方 `post` 參數。空頁、讀回失敗、未找到或無法歸屬的紀錄維持 unknown，禁止因此重送預測。

新增 38 個案例。相關套件 **296 passed in 1.09s**；完整隔離測試 **1273 passed in 27.92s**。涵蓋官方空回執後重啟、只送一次預測再補留言、撤回歷史、錯帳號／題號、布林及非有限數值、舊／相同／未來時間、選項順序與機密欄位不外洩。

## 尚未通過的部分

- 真實成功送出、留言、延遲讀回及重複送出行為尚未實測。沒有把公開原始碼或離線案例當作 V15 完成。
- bot testing area 的多選題 46223／46450 有 3 個 options，但 `all_options_ever` 與 `options_history` 都為 null。公開 serializer 在沒有歷史時也可能回傳 null，而 API 權限亦可能隱藏額外欄位，因此現階段不能只由 null 判定從未改過選項。需要可靠的實機選項對應證據或官方契約，這個缺口未被放寬。
- 留言最大長度仍未以真實 API 驗證。
- Claude 的 00:30 複驗已改為在新鮮副本中額外套用這份草案，先跑完整測試再獨立審查；主專案與 M2 的凍結設定保持原狀。

## Claude 複審後的追加修正（仍未整合）

00:30 的複驗實際於 UTC 16:30:00 啟動，控制端 1273 項測試通過；Claude 於 16:34:14 回覆 FAIL。主要阻擋是先前 B1 共用索引仍略過題組／條件子題。這項意見成立，沒有因 Gemini 先前曾通過 B1 而忽略。

追加修正包括：

- `exclusion_metadata_v2` 解析父貼文與完整子題的 ID／標題 hash，涵蓋四個條件子題；可辨識的 notebook 才能略過，其餘缺欄位、未知結構與無法解析時間全部停止。舊的「not a single-question post」跳過紀錄不得拿來宣稱完整排除索引。
- 多選題的選項順序若無法可靠讀回，在 `request_started` 之前拒絕，POST 為零，避免先送出才永遠無法對帳。
- 預測請求保存送出前的既有三元組識別與微秒 UTC 起始時間，重啟後沿用；缺少基準的舊新格式請求維持 unknown。整數／浮點的同一時間戳記正規化成相同識別。
- GET 客戶端保留 HTTPS／同主機檢查，追加 API 路徑、fragment、上層路徑與 redirect 拒絕；錯誤只輸出本地狀態詞，不轉出遠端理由／URL。原先已拒絕 HTTP，因此 Claude 對舊版 scheme downgrade 的推測不成立；其餘邊界確有補強。
- `m2_prepare_v4` 與一般輪詢的跳過紀錄只保留有效正整數 ID，異常遠端字串不進操作紀錄；既有同步題理由是固定字串，未包含題文。
- 留言讀回掃完整個分頁才接受唯一不同 ID 的匹配；相同 ID 重複只計一次、多個不同匹配或後頁失敗均保持 unknown。

追加後 1311 項完整離線測試通過；Claude 16:47 的複審確認上述實質修正，但再次指出 metadata 請求仍有 `forecast_type` 限制。這個請求端問題也已修正：metadata 查詢完全省略該參數，一般預測輪詢保持原篩選，並直接驗證實際編碼出的 HTTP query。最新完整測試為 **1320 passed in 27.79s**，已送 Claude 最後針對性複審。

UTC **16:56:00.761Z** 用修正副本做唯讀實機查詢：主賽 33121 為 0 題加 1 篇 notebook，33132／33135 為 0 題，索引完整建立且 POST 為零。先前受題型篩選排除的 notebook 現在確實進入解析器，沒有讓它阻擋全場。當下沒有開放／即將開放的競賽題，因此這次不能取代「存在真實題組時」的動態觀察；離線完整結構與 HTTP query 案例仍是其證據。

殘留限制：留言沒有送出前的 ID 基準；對帳仍依賴嚴格時間與唯一匹配，客戶端／伺服器時鐘偏差可能留下 unknown。多選順序缺漏、實際 POST／讀回／私人留言與最大長度仍待 V15，正式參賽核准不變。

## 最後複審與整合

Claude 於 UTC 16:58:15 最後回覆 **PASS，無剩餘 blocking**；審查來源為針對性差異及控制端完整 1320 passed 的證據，沒有宣稱 Claude 自行跑測試。UTC 17:02:41 檢查主專案與隔離副本 baseline 逐檔相符後，套入已審查修正。PLAN v1.33／D85 已同步版本與限制。上方「尚未整合」為當時狀態；目前已整合但仍 local，真實 POST 為零。

## 權限與候選核對補記（UTC 17:14）

[官方 API 文件](https://www.metaculus.com/api/) 明確指出 gateway 會將不可讀欄位設為 null，受限帳號的 `all_options_ever`／`options_history` 不保證可讀。已查看的官方 `bulk-forecast-read` 仍用相同 `MyForecastSerializer`，沒有另外提供多選標籤映射，不能用它消除這個缺口。正式文件的存取層級／個別 project grant 由平台決定；本輪未申請、未寄信、未嘗試繞過。

官方 forecasting-tools 在產生留言時採 150,000 字元本地截斷，但那是客戶端上限，不能當成伺服器承諾或本帳號實測。現行本地上限仍維持原值，真實長度驗收未完成。[官方客戶端](https://github.com/Metaculus/forecasting-tools/blob/main/forecasting_tools/forecast_bots/forecast_bot.py)。

額外的 bot testing area binary 候選 GET 在 M2 執行時回 HTTP 429（寫入零次）；已追加失敗紀錄並停止額外探測。後續網路驗收應等固定 M2 釋放 API 負載後再做。

## V15 元件審查與唯讀準備（UTC 18:28）

Gemini 第一輪獨立審查於 18:27:02Z **PASS，無 blocking**。它保留候選可能重複被前檢拒絕、依賴 Submitter 內部介面、CLI 載入設定例外不包成 JSON，以及僅元件驗收的限制；這些沒有被當成完整 M3 通過。

唯讀準備另發現列表 API 未提供可驗證的個人歷史，原草案在 `own_forecast_records` 拒絕（ValueError，POST 0）。修正為先檢查列表 ID／題型，再讀取完整貼文，核對 post／question ID 一致，才檢查已有預測與其餘資格；詳細資料仍缺歷史時繼續拒絕。新增 3 個案例，共 33 個 probe 案例，相關 **181 passed**、完整 **1381 passed in 27.61s**。第一輪送審來源保持在 `gemini_prompt.txt`；新差異於 18:28:21Z 送 Gemini 針對性複審，沒有冒用前一輪 PASS。

修正後唯讀準備成功：`v15_1793557715641_91989f46`，bot testing area 32977／post 43327／question 43332，寫入 0 次。計畫已凍結於獨立 `data/v15_probes.jsonl`，僅 synthetic 50% 測試與私人測試留言；主專案仍 local、程式尚未整合，實際寫入等待複審。

私人留言的 included_forecast 依釘選官方來源使用 `ForecastSerializer`（question_type、probability_yes），不同於自己的歷史查詢所用 `MyForecastSerializer`。這解除來源層面的疑問，但部署結果仍須實際讀回檢查。

## UTC 18:32 實機部分結果與恢復草案

`v15_probe_v1`／`submit_v8` 已依兩輪 Gemini PASS 與 1381 項測試整合，PLAN v1.35／D87；18:30 依既有 D78 切到 trial。固定 probe `v15_1793557715641_91989f46` 的 forecast 在 18:30:53Z 開始，平台回空成功物件，18:30:57Z 獨立讀回為 found，驗證 D85 的三元組／基準對帳。預測 POST 僅一次。

第一段在留言前的讀取遇 `TransportError`，當時沒有 comment request；等待後接續同一筆，只在 18:32:29Z 發出第一次留言。18:32:30Z 回 `ConfirmedFailure`，該類別依現行傳輸契約只代表 HTTP 400～499。第一版漏記數字狀態碼，因此不能聲稱是限流、權限或格式哪一種；後續唯讀 comment readback 為 unknown，不把查無當成未受理的新證據。

唯讀確認帳號 active、bot／primary bot、forecasting enabled，該 post 的 user_permission=forecaster。釘選官方 `docs/openapi.yml` 的 `/api/comments/create/` 仍要求 included_forecast／is_private／on_post／text，現有請求符合所列形狀；這不是部署端的成功證據。**binary 預測成功，私人留言失敗，元件測試尚未完成。**

為落實 §3.6 的「明確失敗時只補留言」，`v15_probe_v2` 在專案外準備有界恢復：明確 CLI 旗標、最多總共兩個留言請求、保留相同凍結 payload／舊拒絕紀錄；首次或第二次 unknown 只讀回，預測永不重送。追加安全的數字 HTTP 狀態碼，不寫遠端錯誤文字。十個新回歸案例、完整 1391 passed；目前尚未整合，Gemini 於 18:39:42Z 開始獨立審查。若再次遭拒，保留結果並交平台釐清，不無限重試。未增加模型呼叫、解除預算保護或更換試驗題目。

## 本輪結束狀態（2026-11-01T18:46:56Z，台北 11/2 02:46）

- **PLAN v1.36**，已整合 `v15_probe_v2`／`submit_v8`；Gemini 最後複審 PASS，隔離完整 **1396 passed in 27.95s**。恢復草案第一次 FAIL 找到的第二次請求對帳 ID 錯用已修正，並補上不完整恢復歷史拒絕。合計 48 個 probe 案例，其中 15 個恢復案例。
- **實機結果未全數通過**：固定 probe `v15_1793557715641_91989f46`，bot 311763／ground 32977／post 43327／question 43332。預測 POST **1 次**，獨立讀回 found；私人留言 POST **2 次**，兩次均 confirmed_failed，第二次於 18:44:30.717Z 明確是 **HTTP 429**。第一個拒絕未記數字，不能事後說它也是 429。沒有第三次留言或第二次預測，也沒有改換題目來繼續試。
- API 元件報告：`reports/v15report_1793558816848_4ba002b2.json`。私人旗標、附帶預測、留言全文、補送成功都尚未實機確認；MC、長度、真正重複 POST 與 launchd 端到端仍未通過。這不是 M3 完成。
- 模式已回 **local**，正式白名單仍全 false，沒有持續 campaign 或 launchd。D78 trial 授權仍有效；這是本輪有界測試結束後停用寫入，不是要求 Louis 重新授權相同範圍。模型預算熔斷與所有歷史紀錄保留。
- 固定 M2 完整報告 `m2report_1793557427841_59e65bea`：三線首層各 20/20、整合／本地留言 20/20、無未知啟動，15 項檢查通過 13 項；未過的只有可信 Gemini 成本與三家真實耗盡樣態。不能把資料集跑完稱為全部 M2 驗收完成。

真正待 Louis 決定／操作與平台回覆的項目見 [待決報告](DECISIONS-AFTER-D84.md)。V5 非同步問題尚未回覆，原門檻繼續生效。程式開發仍有後續，尤其 HTTP 429 的節流／Retry-After 處理與留言實機驗證；本輪沒有把它列為已解決或以新請求次數繞過上限。
