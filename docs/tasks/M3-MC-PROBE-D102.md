# D102：多選題有界驗收（私人留言＋多選題讀回）

規格：Claude（主持）。實作：GPT（Codex 開發沙箱）。審查：Claude，之後 Gemini。**實際對平台送出需要 Louis 另行核准 D102；本規格的離線實作與測試不含任何平台寫入。**

## 背景

- D100 已核准的有界驗收（是非題）無法執行：bot testing area 開放中的是非題只剩原 V15 probe 用過的 43332（見 `M3-PROBE-CAPS-D100-D101.md`）。
- 唯一未用過、本系統支援的題目是多選題 46450（3 個選項）。在這題做一次有界驗收，可以同時驗證兩個 M3 缺口：加上請求間隔（D98）後的私人留言，以及歷史選項順序為 null 時的多選題送出與讀回（D99）。
- 官方原始碼（`metaculus@6922ad0`）：留言的 `included_forecast` 由 `ForecastSerializer` 序列化，欄位有 `start_time`、`probability_yes`、`probability_yes_per_category`、`continuous_cdf`、`options`（目前選項）、`question_type` 等。資料庫的 `probability_yes_per_category` 是依完整歷史排列的 list（已刪除的選項位置為 None），與 D99 讀回的 `forecast_values` 同樣的形狀；`options` 是目前選項。

## 要做的改動（`v15_probe_v2` → `v15_probe_v3`）

1. `prepare(question_type='binary')`；CLI 加 `--question-type {binary,multiple_choice}`，預設 binary。**binary 的選題、payload、計畫驗證與留言核對全部不變。**
2. multiple_choice 的選題：列表用 `forecast_type='multiple_choice'`，其餘條件與 binary 相同（試驗場地成員、開放中、已開題、關閉時間在 1 小時以後、沒有自己的預測、未被任何 probe 保留、詳細資料與列表身分一致）。另外要求目前 `options` 通過 `valid_current_options()` 且至少 2 個，並且 `self.transport.can_reconcile_forecast(post)` 為 True。
3. multiple_choice 的 synthetic 預測：目前選項的均勻分布，**沿用 `submit.forecast_payload()` 的捨入規則**（前 n−1 項四捨五入到 6 位小數，最後一項補足總和 1，並檢查 `PROBABILITY_MIN`～`PROBABILITY_MAX`）。最好直接呼叫 `submit.forecast_payload()`，或抽出共用函式，讓兩者不可能分歧；測試要確認 3 個選項時得到 `{a: 0.333333, b: 0.333333, c: 0.333334}`。
4. 留言文字沿用 binary 的格式（含 probe_id、「Synthetic … test; not a research forecast」），binary 是 50%，multiple_choice 改寫成 uniform。不放題文或模型輸出。
5. 新計畫（v3 起）在 plan 內寫 `max_comment_requests: 1`。`plan()` 驗證：
   - binary：沿用現行條件。
   - multiple_choice：`snapshot.question_type == 'multiple_choice'`、`snapshot.options` 有效且 ≥ 2、`forecast_payload` 等於依 `snapshot.options` 重新計算的均勻 payload、其他條件（身分、trial、bot、場地、留言 payload、hash）與 binary 相同。
   - 有 `max_comment_requests` 時必須是 1；舊計畫沒有這個欄位，維持舊行為。
6. `run()`：計畫帶 `max_comment_requests: 1` 時，`--retry-confirmed-comment` 一律拒絕（PrecheckError，沒有任何請求），留言請求最多 1 次。舊計畫（原 V15 probe）的 D84／D88 復原規則不變。預測永不重送、unknown 不重送、4xx／429 立即停止的行為不變。
7. 預測讀回走既有的 `transport.readback()`（D99 的多重集合核對）。`found` 事件的收據要保留 `option_order_verified`。
8. 留言屬性核對（`comment_properties_checked`）：
   - binary：不變。
   - multiple_choice：`id`、作者、`on_post`、`is_private`、文字 hash 的條件與 binary 相同；`included_forecast` 是 dict、`question_type == 'multiple_choice'`、`options` 等於計畫的目前選項；`probability_yes_per_category` 是 list，元素是 None 或有限的非 bool 數字且在 [0, 1]，非 None 的個數等於選項數，非 None 值的多重集合與送出的 payload 值完全相同（精確相等）。另記 `included_forecast_order_verified`（非 None 值依序等於依目前選項排列的 payload 值）。任何條件不成立時 `verified=False`，結果為 `comment_properties_unknown`，不重送。
9. 模組 `VERSION` 改為 `v15_probe_v3`；`_proven_rejection` 與舊紀錄的相容判斷不變。

## 不能做的事

- 不改 `submit.py` 的正式送出流程、`submit_transport.py` 的讀回規則、退避／間隔、POST 次數上限。
- 不呼叫網路；不修改 `config/`、`PLAN.md`、`AGENTS.md`。
- 不加入數值題或離散題。

## 測試（離線、合成 HTTP，沿用 `tests/test_v15_probe.py` 的 fixture 風格）

- binary 既有測試全部保持通過。
- multiple_choice：只有多選題可選時，`prepare(question_type='multiple_choice')` 選到它；計畫的 payload 是均勻分布、捨入正確、`max_comment_requests == 1`。
- 歷史為 null 的多選題：送出 1 次預測、讀回 found（`option_order_verified` 為 True；另一個案例讀回順序不同但多重集合相同時為 False）、送出 1 次私人留言、留言核對通過 → `complete`。
- 留言 `included_forecast` 的值不符、型別不符、選項不符、非 None 個數不符 → `comment_properties_unknown`，沒有額外 POST。
- 留言被拒（400 或 429）→ `confirmed_failed`；帶 `--retry-confirmed-comment` 再執行也不會送出第二次留言（新計畫）；原 V15 舊計畫的復原測試保持通過。
- 預測被拒或 unknown → 停止，不送留言、不重送。
- 計畫被竄改（payload 改成非均勻、選項變動、`max_comment_requests` 改成 2）→ 拒絕。
- 已被保留的多選題（之前的 probe）不會再被選。
- CLI：`--question-type` 預設 binary；`multiple_choice` 會傳到 `prepare()`。

完成後執行相關測試與完整測試，回報實際結果與限制。

## 執行方式（Louis 核准 D102 之後）

1. `config/submit_mode.json` 改為 trial（bot 311763），執行 `v15_probe.py prepare --question-type multiple_choice`（唯讀）。
2. 等約 10 分鐘，期間沒有其他 Metaculus API 活動。
3. `v15_probe.py run --probe-id <id>`，不帶 `--retry-confirmed-comment`。
4. 不論結果，立即改回 local；記錄數字狀態碼與 Retry-After（如有），不換題、不重試。

## 實作與審查（2026-11-02）

- Louis 於桌面對話核准 D102（照建議 A）。
- GPT（`gpt-6-astra` high）在專案外的開發沙箱完成實作，任務 `claudedev_1793617967687_6bf25683`；只改 `src/v15_probe.py` 與 `tests/test_v15_probe.py`。沙箱內指定測試 250 passed；完整測試的 62 個失敗都是沙箱目錄限制。
- Claude 審查：範圍符合規格。修改一處：最後的留言屬性核對原本讀 `comments/<id>/` 詳細頁，該端點不在公開閘道文件中（文件說部分網站專用端點回 403），改為讀文件明確允許的 `comments/?post=<post>&author=<自己>&limit=100`，只接受恰好一筆相同 ID 的紀錄，否則記為未核對。補 3 個測試。官方後端的列表與詳細頁都用 `serialize_comment_many`，欄位相同。
- Gemini（Argon high）第 1 輪 PASS，一項 non-blocking：新計畫若被刪掉 `max_comment_requests` 並重算雜湊，會回到舊的復原規則。已修正：只有 v1／v2 版本的是非題計畫算舊計畫，其他計畫一律要有 `max_comment_requests == 1`；補 3 個測試；真實帳冊中的原 V15 計畫仍可驗證。第 2 輪定點複查 PASS。
- 隔離完整 **1708 passed**（含 D103 的 TG 修改）。

## 實際執行（2026-11-02，UTC）

| 時間 | 事件 |
|---|---|
| 11:27:04～11:27:17 | local 模式唯讀前檢，凍結計畫 `v15_1793618837390_110f6deb`（post 46223／question 46450，3 個選項，payload 0.333333／0.333333／0.333334，`max_comment_requests: 1`）。前一次 API 請求在 11:02 |
| 11:27～11:37 | 約 10 分鐘沒有任何 Metaculus API 請求 |
| 11:37:41 | 切到 trial（bot 311763） |
| 11:38:39.615 | 預測 POST 開始；11:38:42.775 回應（無收據內容） |
| 11:38:51.685 | 預測讀回 **found**：伺服器 start_time 1793619522.579765，`option_order_verified: true` |
| 11:39:53.549 | 私人留言 POST 開始；11:39:56.217 **伺服器回應並附內容**（`receipt_present: true`），沒有 429 |
| 11:41:22.982 | 留言讀回記為 **unknown**（不重送） |
| 11:41:23 | 回 local |

整個過程沒有 4xx／429，冷卻帳冊沒有新紀錄；預測 POST 1 次、留言 POST 1 次，都沒有重試。

### 留言 unknown 的原因（唯讀查證）

- 讀回花了約 86 秒，以 3.5～4.5 秒的請求間隔推算約 20 個請求，等於分頁上限 `MAX_COMMENT_PAGES = 20`。
- 唯讀 GET `comments/?post=46223&author=311763&limit=100`：`results` 為空，但仍附 `next`（offset=100）。
- 官方後端 `comments/services/feed.py` 的 `get_comments_feed`：只有傳入 `is_private=true` 時才列出請求者自己的私人留言，否則一律 `is_private=False`；列表用 `CountlessLimitOffsetPagination`，最後一頁之後仍附 `next`。
- 同一查詢加上 `is_private=true` 後回傳恰好 1 筆：留言 1250911，作者 311763、`on_post` 46223、`is_private: true`、`created_at` 11:39:56.075Z（在請求開始之後）、文字雜湊與凍結計畫相同；`included_forecast` 為 multiple_choice，`probability_yes_per_category` 與送出的值逐位置相同，`start_time` 與預測讀回相同。

結論：**私人留言在加上請求間隔後已成功發布**；unknown 是本專案讀回程式的錯誤（沒有傳 `is_private=true`，空頁也不停止），不是平台拒絕。正式送出流程用的是同一段讀回程式，因此一併修正。

### 修正（`api_transport_v5` → `api_transport_v6`）

- 留言讀回要求凍結 payload 的 `is_private` 是 bool（否則直接 unknown，不發請求）；私人留言的查詢加上 `is_private=true`。
- 讀到空的 `results` 頁就視為列表結束。
- probe 的留言屬性核對同樣加上 `is_private=true`。
- 新測試用接近後端行為的假伺服器：私人留言只在帶旗標時列出、永遠附 `next`。
- Gemini（Argon high）第 1 輪 PASS，兩項 non-blocking（逐筆核對 `is_private`；補公開留言 found 的測試），已採納；第 2 輪定點複查 PASS。隔離完整 **1718 passed**。
