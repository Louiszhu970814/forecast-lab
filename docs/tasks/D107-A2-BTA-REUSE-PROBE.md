# D107 A2：bot testing area 重送驗收（`v15_probe_v4`）

規格：Claude（主持）。實作：GPT（Codex 開發沙箱）。審查：Claude，之後 Gemini（送出／讀回相關，D107 A4 規定須獨立審查）。
Louis 於 2026-11-02 核准 D107 A1（試驗場地有界寫入常設授權）與 A2（bot testing area 允許重送）。

## 背景

- M3 還缺三項元件證據：私人留言的長度上限、「預測成功、留言確認失敗後只補送留言」的真實路徑（§3.6 狀態機第 4 點），以及同一題重複送出時平台的行為。
- bot testing area（32977）支援的兩題都已被 probe 用過（是非題 43332、多選題 46450）。`v15_probe_v3` 只選沒有自己預測、沒有被 probe 保留的題目，因此選不到題。D107 A2 取消這個自訂限制；平台的測試區本來就允許重送。

## 要做的改動（`v15_probe_v3` → `v15_probe_v4`）

### 共通

1. `prepare(question_type, scenario)`；CLI 加 `--scenario {single,comment_length,comment_retry,duplicate_forecast}`，預設 `single`。**`single` 就是現行 v3 的行為，選題與執行完全不變。**
2. 新情境的計畫帶 `reuse_authorization: "PLAN D107 A2"`、`scenario` 與事先固定的請求上限（`max_forecast_requests`、`max_comment_requests`）。`plan()` 逐項驗證；被竄改就拒絕。
3. 新情境允許選已有自己預測、或被先前 probe 保留的 bot testing area 題目，但：只限 32977；先前所有 probe 在該題都沒有未解決的請求（`request_started` 後沒有 `found` 或 `confirmed_failed`、或有 `unknown`／`write_unconfirmed`）；其他選題條件（成員、開放中、已開題、關閉時間在 1 小時以後、多選題選項有效且可對帳）不變。
4. 一般送出器（`Submitter` 的 `_duplicate` 與 `probe_reservations`）對非 probe 意圖的阻擋不變：正式流程仍不會送到 probe 用過的題目。
5. 預測與留言的讀回、請求間隔、4xx／429 即停、unknown 不重送都沿用現行程式。每個計畫只執行一次，沒有復原旗標。

### 情境 `comment_length`

- 不送新預測；題目必須已有本帳號的預測（`included_forecast: true` 才有東西可附）。
- 計畫固定長度階梯 `[2000, 10000, 50000, 150000]`（字元數），文字是 ASCII 的合成填充（含 probe_id 與「Synthetic length test; not a research forecast」），不含題文或模型輸出。
- 依序送出私人留言，每則都讀回並核對**全文雜湊與長度**（可偵測平台截斷）。遇到確認拒絕就停止，記錄 HTTP 狀態碼；遇到 unknown 或截斷也停止。
- 結果記 `max_accepted_length`、`first_rejected_length`（可為 null）、`truncation_detected`。留言請求最多 4 次。

### 情境 `comment_retry`

- 目的：用正式 `Submitter` 的既有程式（`_resume` 的留言補送與退避），在真實平台上走一次「預測成功、留言確認失敗 → 只補送留言」。**不得另寫一套補送邏輯。**
- 觸發方式是本地故障注入：只在 probe 內、只在 transport 包裝層、只對第一次留言請求生效，在任何網路 I/O 之前丟出確認失敗；帳冊明確記 `injected_fault: true`、`network_request_sent: false`。之後由 `Submitter` 的既有流程對同一份留言送出一次真實請求並讀回。
- 為了不汙染正式帳冊，這個情境的 `Submitter` 使用獨立的資料根目錄（例如 `data/v15_probe_submitter/<probe_id>/`），合成的整合／留言／快照紀錄都標 `synthetic: true`。平台層的既有預測檢查與正式帳冊的 bot／question 去重仍要生效（可以讀正式帳冊做檢查，但不寫入）。
- 上限：預測 POST 1 次（合成值：是非題 50%、多選題均勻）、真實留言 POST 1 次。
- 若不改 `submit.py` 的行為就做不到，回報 `needs_input=true` 並說明需要的最小改動，不要自行修改正式送出行為。

### 情境 `duplicate_forecast`

- 同一題送兩次預測（是非題先 0.5 再 0.6；多選題先均勻再固定的另一組合成值），不送留言。
- 每次都讀回 found；第二次的讀回必須認得是新的那筆（以送出前的預測 ID 基準區分），並記錄平台歷史筆數變化。預測請求最多 2 次。
- 結果說明平台對重複送出的行為（接受並保留歷史、或拒絕）。

## 不能做的事

- 不改 `submit.py`、`submit_transport.py` 的正式送出／讀回／退避規則與 POST 上限（`comment_retry` 只透過包裝與獨立資料根目錄使用它們）。
- 不改 prompt、模型、整合、預算。不修改 `config/`、`PLAN.md` 正文、`AGENTS.md`。不呼叫網路。
- `single` 與既有 v1～v3 計畫的驗證與執行規則不變。

## 測試（離線、合成 HTTP，沿用 `tests/test_v15_probe.py` 的假伺服器）

- `single` 的既有測試全部通過；v3 計畫在 v4 程式下照舊驗證。
- 新情境能選到已有預測／已被保留的 bot testing area 題目；有未解決請求的題目不選；非 32977 的題目不選。
- `comment_length`：全部接受、在第 k 階被拒、截斷（讀回文字不同）、unknown 四種情況，請求次數與結果欄位正確，停止後沒有額外 POST。
- `comment_retry`：注入的失敗沒有任何網路請求；`Submitter` 依既有退避只補送一次留言並 found；預測只有 1 次 POST；正式帳冊沒有被寫入；補送也失敗時標記 `comment_failed`、不再送。
- `duplicate_forecast`：兩次 found 且第二次認得新紀錄；第二次被拒或 unknown 時停止。
- 計畫被竄改（上限、階梯、授權字串、情境）→ 拒絕。
- 一般 `Submitter` 對 probe 用過題目的阻擋仍有效。

完成後執行 `tests/test_v15_probe.py`、`tests/test_submit.py` 與完整測試；回報實際結果，沙箱目錄限制造成的失敗分開列出。若規格有矛盾或缺漏，照最保守（拒絕、不送出）的解讀實作，並在 summary 列出你的解讀。

## 執行（審查通過並提交之後，依 D107 A1 由主持人執行）

切到 trial → 依序執行 `duplicate_forecast`（是非題）、`comment_length`、`comment_retry`，每個情境之間至少間隔 10 分鐘且沒有其他 Metaculus API 活動 → 回 local。結果寫入本檔與交接檔，並在 TG 回報。
