# V15 真實讀取格式核對與修正草案

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
