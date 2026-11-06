# D99：多選題讀回不依賴完整選項歷史（M3）

> **SUPERSEDED／歷史文件（D136 標記）**：本頁保留當時提案與驗證經過，不是現行授權或待決清單。現行規格見 [PLATFORM-REPLY-D115.md](PLATFORM-REPLY-D115.md) 與 [PLAN](../../PLAN.md) 的後續決策。

規格：Claude（主持）。實作：GPT（Codex 開發沙箱）。審查：Claude，之後 Gemini 獨立審查。依 Louis「研究回來之後有一定答案了就直接開始繼續 M3，中間的 coding 還有審查你們三個可以輪流做」。

## 背景（已核對的事實）

- bot 帳號的 `options_history`、`all_options_ever` 一律被閘道設為 null（官方 OpenAPI）。
- 官方 forecasting-tools 送多選題預測時，`probability_yes_per_category` 是以目前 `question.options` 標籤為鍵的字典；伺服器自己依完整歷史轉成向量（`questions/serializers/common.py` 的 `multiple_choice_validation`）。寫入端不需要歷史順序。
- 自己的歷史讀回 `my_forecasts.history[*].forecast_values` 是依完整歷史順序排列的向量；目前已不存在的選項位置為 `None`。
- 伺服器的不變量：刪除選項保留其餘選項的相對順序；新增選項插在最後的兜底選項之前；重排只在從未增刪時允許。**例外**：新增時的重複檢查只比對目前選項，所以重新加回曾刪除的選項時，它在完整歷史中的位置是舊位置，非 `None` 值的順序就會和目前 `options` 不同。新增選項的寬限期內，伺服器會另建一筆 `start_time` 在未來的預註冊預測。
- 現行程式（`api_transport_v4`）在拿不到 `all_options_ever` 時：`can_reconcile_forecast()` 回 False，`submit.py` 因此在第一次 POST 前就拒絕多選題；`serialized_forecast_receipt()` 的多選分支回 None。

## 要做的改動（`api_transport_v4` → `api_transport_v5`）

1. `APITransport.can_reconcile_forecast(post)`：多選題只要目前 `options` 是非空、元素皆為字串且不重複的 list，就回 True（`all_options_ever` 可為 null）。是非題不變。其他型別仍回 False。
2. `serialized_forecast_receipt()` 的多選分支：
   - 有可用的 `known_option_order(question)` 時，**保留現行的逐位置精確比對**，不改行為。
   - 沒有時，改用不依賴順序的核對，全部條件成立才接受：
     - 身分條件與現在相同（題號、作者、`started < start_time <= now`）。
     - 目前 `options` 有效（同第 1 項），payload 的 `probability_yes_per_category` 是 dict，鍵的集合恰好等於目前選項集合，值都是有限的 int／float 且在 [0, 1]。
     - `forecast_values` 是 list；其中每個元素要嘛是 `None`，要嘛是有限的 int／float 且在 [0, 1]；bool 一律拒絕。
     - 非 `None` 元素的個數等於目前選項數。
     - 非 `None` 元素的多重集合與 payload 值的多重集合**完全相同**（與是非題一樣用精確相等，不用容差）。
   - 接受時，回傳的 receipt 額外帶 `"option_order_verified"`：非 `None` 元素依序等於 `[payload[o] for o in options]` 時為 True，只有多重集合相符時為 False。有歷史順序的既有路徑一律為 True。
   - 任何條件不成立都回 None（呼叫端維持 unknown，**不重送**）。
3. 不改 `submit.py` 的送出 payload 建法（已是以目前選項為鍵的字典、六位小數、最後一項補足總和 1）。只需確認 pre-POST 檢查改經由第 1 項放行。
4. `VERSION` 改為 `api_transport_v5`；其他版本字串只在確實改到該模組時才升。

## 不能做的事

- 不改是非題路徑、留言路徑、退避／間隔（`api_backoff_v2`）、POST 次數上限、unknown 不重送與預測永不重送的規則。
- 不猜選項順序、不改用其他端點、不呼叫網路。
- 不修改 `PLAN.md` 正文與 `AGENTS.md`；文件由 Claude 整合。

## 測試（離線、合成資料）

新增或調整 `tests/test_api_readback.py`、`tests/test_submit.py`，至少涵蓋：

- 歷史為 null、讀回向量順序與目前選項相同 → found，`option_order_verified=True`。
- 歷史為 null、向量順序不同但多重集合相同（模擬重新加回刪除選項）→ found，`option_order_verified=False`。
- 向量中有 `None`（已刪除選項）且其餘相符 → found。
- 值不同、多一個或少一個非 `None` 值、含 bool／NaN／超出範圍值、payload 鍵與目前選項不符、目前選項重複或非字串 → None。
- `start_time` 在送出之前或在未來（寬限期預註冊）→ None。
- 有 `all_options_ever` 時沿用逐位置比對：原有測試保持通過；順序不符時仍為 None。
- `can_reconcile_forecast`：歷史 null 的有效多選題 → True；無效 options → False。
- 以既有的 Submitter 合成環境確認：歷史 null 的多選題不再在 POST 前被拒，送出一次後讀回 found，且不會重送。原本「未知順序絕不猜」的測試要改成新語意（未知順序時用多重集合核對，仍不猜順序）。

完成後執行相關測試與完整測試，回報實際結果與限制。

## 結果（2026-11-02）

- GPT（`gpt-6-astra` high）在專案外的開發沙箱（`flab.dev_runner` 的 `flabdev` 權限、無網路、不能 commit）完成實作，任務 `claudedev_1793597024965_b26b0f0a`。沙箱內完整測試有 62 個失敗，都是沙箱的目錄限制（暫存與驗收路徑），不是本次改動造成。
- Claude 審查：改動範圍符合規格。收緊一點：只有歷史被遮罩為 null 才用多重集合；歷史存在但異常一律 unknown。
- Gemini（Argon high）審查第 1 輪 FAIL：收緊後 POST 前檢查仍會放行「歷史存在但異常」的題目，送出後讀回必定 unknown。已讓 `can_reconcile_forecast()` 與讀回規則一致並補測試。第 2 輪 PASS。
- 隔離完整 **1620 passed**。
- 仍待：多選題的真實 POST 與讀回實測（M3），屬平台寫入。
