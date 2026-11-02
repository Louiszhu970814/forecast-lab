# D98：不靠官方回信的平台線索（私人留言 429、多選題、留言長度）

2026-11-02，Louis 指示：「可以繼續找繞開跟官方寄信溝通的方法，我偏向叫 gemini 繼續海巡各大論壇、github 上其他開源的參賽者的模型之類的，應該都會有線索。」Claude 主持。沒有寄信、沒有任何平台寫入，也沒有讀取開放題的內容或預測。

## 一、Claude 直接核對的原始碼（無登入、唯讀的 GitHub API 與 raw 檔案）

### 官方客戶端 forecasting-tools（`main` = `a54dd64`，2026-10-31）

來源：`forecasting_tools/helpers/metaculus_client.py`、`forecasting_tools/util/misc.py`。

| 項目 | 官方客戶端的做法 | 本專案原本的做法 |
|---|---|---|
| 請求間隔 | **每一個**請求前都睡 3.5 秒，再加 0～1 秒抖動（`sleep_seconds_between_requests=3.5`、`sleep_jitter_seconds=1`） | 連續發送，沒有間隔；只在收到 429 後依 Retry-After 冷卻 |
| 重試 | 最多 3 次，起始 2.5 秒、底數 3、抖動 1～8 倍、上限 75 秒；429 會重試，其他 4xx 不重試 | GET 短暫重試；POST 不重試；預測永不重送 |
| 多選題送出 | `probability_yes_per_category`：以 `question.options` 的**選項標籤**為鍵的字典，順序由伺服器對應 | 同樣以標籤為鍵送出；但讀回時需要完整歷史順序，拿不到就在 POST 前拒絕 |
| 預測 payload | 每筆附 `"source": "api"` | 未附 |
| 私人留言 | `{on_post, text, is_private, included_forecast}` POST 到 `/api/comments/create/` | 相同 |
| bulk 端點 | `/api/questions/bulk-forecast-comment/`、`/bulk-forecast-read/` 只在設定 `acting_user` 時使用 | 未使用 |

客戶端睡多久是官方客戶端自己的選擇，不等於伺服器的限流數值；但它是官方對「怎樣算禮貌使用」的直接示範。

### 官方伺服器 metaculus（`main` = `6922ad0`，2026-11-02）

來源：`comments/views/common.py`、`comments/serializers/common.py`、`comments/services/common.py`、`projects/permissions.py`。

- `comment_create_api_view` 本身沒有 throttle 設定。
- 會回 4xx 的條件只有：貼文不可見（400「Post does not exist」）、沒有文字（400）、權限不是 forecaster／curator／admin／creator（403）、帳號不允許公開留言而送了公開留言（403）、垃圾訊息判定（D94 已核對私人留言不跑這項）。
- 我們的留言：私人、文字約 90 字元、該貼文可見（預測已成功）、`user_permission=forecaster`（D88 唯讀確認）。**以上條件都不成立**。

推論：第一次被拒的 4xx 很可能也是閘道層的限流（429），只是 v1 沒有記下數字。閘道不開源（D94），部署版本也可能不同，所以這是推論，不是證明。第二次 429 與 GET 也多次遇到 429（11/1 約 17:15、11/2 01:50 與 01:51，Retry-After 約 10 秒）的現象一致：限流針對整個帳號或來源，不是留言專屬。

## 二、已做的離線修正：請求間隔（`api_backoff_v2`）

- 正式連線（真實 opener）的每個 Metaculus API 請求，讀寫都一樣，前面至少間隔 3.5 秒，再加 0～1 秒抖動，與官方客戶端一致。
- 間隔跨程序共用：持鎖期間比對冷卻帳冊鎖檔的修改時間，請求開始時更新它；不新增帳冊。
- 時鐘回撥時最多等一個間隔；冷卻中的請求在任何等待之前就被擋下。
- 測試用的假 opener 不加間隔，既有測試不變慢。
- 新增 10 個測試；隔離完整 1547 passed。Gemini Argon high 審查第 1 輪抓到「新建鎖檔使第一個請求白等一個間隔」與「測試依賴未來的假時間」，已修正；第 2 輪 PASS。
- 不改 POST 次數上限、不重送 unknown、不重送預測；原 V15 probe 仍不加第三次留言。

## 三、Gemini 論壇與 GitHub 搜尋（`forumresearch_1793595887182_80129121`）

UTC 05:04:51 起，官方 agy、`gemini-4-argon-high`，只開搜尋與讀網頁、封鎖 metaculus.com，在專案外執行。實際 15 次搜尋、25 次讀頁、29 次讀取自己下載的頁面；沒有被拒動作，沒有發現提示詞注入。原始答案保存在 `reports/forumresearch_1793595887182_80129121/answer.json`，**屬未核對的模型輸出**；下表只列 Claude 逐行核對過的結論。

Gemini 搜尋了 GitHub 上的第三方實作與論壇，但沒有找到可採信的第三方案例（`site:github.com "api/comments/create" OR "bulk-forecast-comment"` 等查詢無結果）。有用的發現都來自官方原始碼與官方範本。

| 主張 | 核對結果（官方 `metaculus@6922ad0`） | 採用 |
|---|---|---|
| `/api/questions/bulk-forecast-comment/` 一般 bot token 就能用，`acting_user` 可省略 | 屬實。`@permission_classes([IsAuthenticated])`；`resolve_acting_user` 沒給值時回傳請求者本人；OpenAPI 的閘道限制清單沒有列這個端點 | 採用為候選方案 |
| 同一請求內預測與私人留言在同一個 transaction 寫入；任一筆驗證失敗就整批不寫入 | 屬實。先驗證所有預測與留言、有錯就 400，再於 `transaction.atomic()` 內 `create_forecast_bulk` 後逐筆 `create_comment`；`included_forecast` 會抓到同一請求剛寫入的預測；留言必須 `is_private=true` | 採用為候選方案 |
| 多選題送出用目前選項標籤為鍵的字典即可，不需要歷史順序 | 屬實（寫入端由伺服器依完整歷史轉成向量） | 採用 |
| 讀回向量濾掉 `None` 後，順序必定與目前 `options` 一致 | **部分屬實**。刪除保留相對順序、新增插在最後兜底選項之前、重排只在從未增刪時允許；但新增時的重複檢查只比對目前選項，**重新加回曾刪除的選項**時，它在完整歷史中的位置是原位置，順序就會不一致；新增選項的寬限期內也有例外 | 不直接採用；改用不依賴順序的核對 |
| bot 私人留言超過 500 字元、建立滿 30 天後，資料庫只留前 200 字元，列表查詢只回傳截斷內容 | 屬實（`ARCHIVE_AGE_DAYS=30`、`ARCHIVE_MIN_TEXT_LENGTH=500`、`ARCHIVE_STUB_LENGTH=200`，限 `author__is_bot=True, is_private=True`） | 採用為風險：30 天後的對帳不能再比對全文雜湊 |
| 留言 `full_clean()` 會強制 150,000 字元上限 | **不採用**。`TextField` 不會加上 `MaxLengthValidator`，Django 文件明載 max_length 不在 model／DB 層強制；這與 D94 已修正的結論相同 | 維持 D94 結論 |
| 官方範本 `metac-bot-template` 一次只處理一題（`Semaphore(1)`） | 與官方範本一致，屬客戶端選擇 | 參考 |

另外，Claude 讀 OpenAPI 時確認：bot 帳號查 `/api/comments/` 必須帶 `author=<自己的 ID>` 或 `author_is_staff=true`，否則閘道回 403。本專案的留言讀回已帶 `author`，沒有這個問題。OpenAPI 也說明閘道拒絕時回 403（附原因）或 400（格式錯誤），格式可能和後端不同。

## 四、對 M3 的意義與下一步

1. **請求間隔（已完成，見第二節）**：最可能直接降低 429 的修正。
2. **多選題可以解除「POST 前拒絕」（建議，離線可做）**：寫入改依官方做法用目前選項標籤為鍵；讀回不再要求完整歷史順序，改為核對身分（題目、作者、送出後的 start_time）、非 `None` 值的個數等於目前選項數、且非 `None` 值的多重集合與送出的機率完全相同。順序也相符時記為強比對；只有集合相符時記為收據確認；任何不符都維持 unknown、不重送。需要實作、測試與獨立審查。
3. **bulk 端點（候選，需評估）**：一次請求原子寫入預測與私人留言，寫入次數減半，也消除「預測成功但留言失敗」的半成功狀態。但它會改變 §3.6 的送出狀態機（例如明確 4xx 時整批未寫入，能否重送需要新規則），屬較大的設計變更；建議先完成第 1、2 項並做一次有界驗收，再決定是否改用。
4. **新的有界私人留言驗收（需 Louis 核准）**：原 V15 probe 不加第三次留言。建議另開一次 bot testing area 的新驗收：讀取與送出之間隔開，送出前至少 30 分鐘沒有其他 API 活動；最多 1 次預測 POST 加 1 次私人留言 POST，全程使用新的請求間隔；遇到 4xx／429 立即停止並記錄數字狀態碼與 Retry-After，不重試。
5. **30 天封存風險**：對帳流程需要在 30 天內完成全文比對；之後只能比對 ID 與前 200 字元。
