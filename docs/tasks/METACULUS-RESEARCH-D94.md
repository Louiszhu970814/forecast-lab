# D94：平台三問的來源核對

2026-11-02，Gemini 透過官方 agy、`gemini-4-argon-medium`，使用 12,877 字元技術背景及搜尋所得頁面交叉研究；UTC 03:56:45～04:02:49。實際 9 次搜尋、15 次 URL 讀取、15 次自有下載頁閱讀；其中一個舊檔案路徑 404、一個官網站靜態 OpenAPI 403。沒有執行指令、寫檔工具、委派、登入憑證或開放題預測內容。搜尋包含平台與論壇相關問題；Gemini 未提供可採信、可直接解決三問的論壇案例，不能宣稱論壇已證實平台行為。

GPT 再從官方 GitHub 取得本輪 HEAD `6922ad077753da7d80dbe3ae76959e5ffbe10129`，對照 Gemini 使用的既有釘選版本 `9c1434b4cf3d3bd1b12836ad9085fe80153e6915`，並核對 Django／HTTP 原始文件。公開程式碼不代表實際部署版本。

## 1. 多選題歷史映射：存取權限是已確認的缺口

[官方 OpenAPI](https://github.com/Metaculus/metaculus/blob/6922ad077753da7d80dbe3ae76959e5ffbe10129/docs/openapi.yml) 明載過濾 gateway 不開源；不可讀欄位保留鍵但值設為 null。`options_history` 與 `all_options_ever` 的具體表格／schema 指定 Restricted、Bot Benchmarking 皆為 null，只有 Unrestricted 才可取得。雖通用段落提到 project grant 可解除欄位限制，不能據此保證能解除這兩欄；須請平台明確確認。

[官方 serializer](https://github.com/Metaculus/metaculus/blob/6922ad077753da7d80dbe3ae76959e5ffbe10129/questions/serializers/common.py) 的 MC 寫入依完整歷史選項排列向量；自己的歷史讀回直接回傳已儲存向量。現在的 options、向量長度一致或 null 歷史，都不足以證明可靠映射；bulk 讀回未提供可取代完整歷史的保證。

結論：已有官方文件解釋為何拿不到。解法需要平台提供可讀映射、受支持的替代格式或明確權限方案。維持 MC 在 POST 前拒絕；不使用猜測、錯誤訊息探測或切端點繞過。

## 2. 私人留言 429：具體原因仍待平台查帳號／gateway

官方文件確認主站有 request throttling，但本輪未找到正式數值、各端點是否共用桶或本帳號適用規則。開源 settings 未設定一般 DRF throttle；這不足以證明本次 429 一定由哪一層產生。預測、讀回與留言先後成功／失敗，可能涉及時窗、總頻率或端點規則，但仍屬推論。

[私人留言 spam 檢查](https://github.com/Metaculus/metaculus/blob/6922ad077753da7d80dbe3ae76959e5ffbe10129/comments/services/spam_detection.py) 在該版本直接略過私人留言；[公開留言權限檢查](https://github.com/Metaculus/metaculus/blob/6922ad077753da7d80dbe3ae76959e5ffbe10129/comments/services/common.py) 也只套用公開留言。這削弱「已知開源公開留言限制直接造成失敗」的解釋，不能排除部署差異或 gateway 限制。首次只有 4xx、無數字碼，不能事後稱其為 429。

[RFC 6585 §4](https://www.rfc-editor.org/rfc/rfc6585#section-4) 對 429 的 Retry-After 是可選欄位，不能假設每次必有；也不代表等完必成功。D90 把有效 Retry-After 作為我們自己的最低等待政策，缺失時保守退避；不可把此專案政策描述成 HTTP 對所有客戶端的 MUST。

結論：現有退避處理有依據，失敗根因及可接受發送頻率仍未確定。原 V15 已到兩次留言上限，不再寫入、不換題。官方客戶端的延遲也不能當成 SLA。

## 3. 留言長度：150,000 是設計線索，仍不是已驗證 API 上限

[Comment model](https://github.com/Metaculus/metaculus/blob/6922ad077753da7d80dbe3ae76959e5ffbe10129/comments/models.py) 宣告 `TextField(max_length=150_000)`；建立流程有 `full_clean()`。但 [Django TextField 文件](https://docs.djangoproject.com/en/5.2/ref/models/fields/#textfield) 明確說明此 max_length 不在 model／database 層強制執行；[CommentWriteSerializer](https://github.com/Metaculus/metaculus/blob/6922ad077753da7d80dbe3ae76959e5ffbe10129/comments/serializers/common.py) 又顯式定義沒有 max_length 的 CharField。因此不能僅憑 full_clean 就斷言硬上限已被驗證。

[官方 forecasting-tools](https://github.com/Metaculus/forecasting-tools/blob/main/forecasting_tools/forecast_bots/forecast_bot.py) 以 Python 字串長度在 150,000 做本地截斷，是客戶端保護。included_forecast 為獨立關聯欄位，原始碼沒有顯示它改變 text 欄位宣告；HTTP body／gateway byte 限制及部署實際行為仍未知。維持本專案現有 3,000 字元上限，不用長留言探測平台。

## 研究校正與下一步

Gemini 初稿把 TextField 的 full_clean 解讀為強制 MaxLengthValidator，也把一般 project grant、gateway 歸因與 Retry-After 的語氣說得過滿。GPT 依上列一手來源修正；Gemini 後續兩次審查接受 TextField 與具體存取 schema 的修正。這份整合結論優先於保留於 reports 的未校正模型初稿。

已送出的官方詢問不重寄。等待平台回答可集中在：（a）MC 可用的映射／讀取權限；（b）本帳號兩次留言拒絕及限流規則；（c）部署的字元／bytes 上限。三問已有較精確的依據，但私人留言與 MC 實機 gate 尚未通過。M2 額度驗證獨立進行，不需等待這封詢問。
