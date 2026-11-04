# Forecast Lab M4 r6 複核結論：PASS（無 blocker）

我只看了來源封包裡的 diff、測試全文和操作者報告，沒有執行任何測試或程式。這個 PASS 是審查意見，不代表我或任何人核准合併或部署。

---

## 一、證據等級

**1. 我直接讀過的內容（diff 和測試原文）**
- resolve.py 新增 `submitted_mode_by_qid`。它和 `submitted_snap_by_qid` 在同一個迴圈分支裡、從同一列寫入，所以一題的 snapshot 和 mode 一定來自同一列送出紀錄，不會混用不同列。
- 判定 skip 的條件從 `sub_snap_id is None` 收緊為 `sub_snap_id is None and mode != "live"`。live 列缺 snapshot 時，`sub_snap_id` 是 None，所以 `snap` 也是 None，會走到 MissingSubmittedSnapshot 那個分支。測試要求錯誤形態是 `{"question_id":101,"snapshot_id":None,"error_type":"MissingSubmittedSnapshot"}`，跟這段邏輯吻合。
- 篩選改成反向：`errors` 是所有不含 `skip_reason` 的項目。之後如果有人新增別種 diagnostics 形態，預設會被當成錯誤，不會悄悄消失。這點確實是 fail-closed 的改進。
- score.py 的條件改為 `res is None and question_snapshot_id is None and mode != "live"`。有結算事件的列不會走這條，會繼續走 provenance 檢查。

**2. 封包有說、但我看不到的內容（屬於操作者陳述）**
- C1：「全函式只有兩個 `diagnostics.append`」。diff 只露出部分程式，我沒辦法自己確認整個函式。不過 r6 已改成反向篩選，就算還有第三種形態，也會被歸到 errors。所以這項陳述對安全性的影響已經降低。
- 「有結算事件的無 snapshot 列會被 `missing_submitted_snapshot` 拒絕」：provenance 那段程式不在 diff 裡，沒有直接證據。
- 正式驗證結果：status ok、結束碼 0、errors []、skipped 2（45708、46451）、0 候選、0 API 請求；score 報告 no_resolution_event 14、submitted_without_snapshot_id 2；相關四檔 268 passed；完整測試 2913 passed / 100 failed，與基線比對新增 0、消失 0。這些都是操作者報告，我沒有核對失敗清單。另外，基線本身就有 100 個失敗，這不是 r6 造成的，但也不能算成「全綠」。

---

## 二、Blocker

**沒有。** 和 r5 相比，r6 的 resolve 判定只有變嚴：r5 是所有缺 snapshot 的列都 skip，r6 改成只有非 live 的才 skip。在正式資料上，被 skip 的兩題都是 32977 的 trial 探針，跟預期一致。

---

## 三、發現與建議（不阻擋，但建議處理）

**P1：沒有 mode 會被當成非 live（建議優先處理）**
- 條件寫的是 `mode != "live"`。所以 mode 欄位缺失、是 None、寫成 `"Live"` 或 `"live "` 等格式不對的情況，全部會被當成非 live 而 skip。換句話說，程式實際做的是「除非明確寫了 live，否則都 skip」，而設計上要的是「只有非 live 才 skip」。
- 後果：如果一筆真的 live 送出漏了 mode，或 mode 格式錯了，系統不會報 MissingSubmittedSnapshot，而是安靜地跳過。這正是 r6 想防的完整性問題。
- 建議改成白名單：只有 mode 屬於明確的非 live 集合（例如 `{"trial", "dry_run"}`，以實際允許的值為準）才 skip，其他一律報錯。另外補一個測試：mode 缺失加上沒有 snapshot，應該要報錯。
- 這次不列為 blocker，原因有兩個：跟 r5 比沒有退步，而且正式資料上 skipped 的只有那兩筆 trial 探針。

**P2：resolve 和 score 對空字串的處理不一致（封包說「相同條件」，實際並不完全相同）**
- resolve.py 會把 `""` 和純空白字串正規化成 None（`isinstance(str) and strip()` 不成立就是 None），所以非 live 列帶空字串 snapshot 時會被 skip。
- score.py 現在用 `is None` 判斷，封包也明說「空字串不再被當成缺值」。結果同一筆非 live、snapshot 為 `""` 的列，在 resolve 被 skip，在 score 卻被計入 no_resolution_event。
- r5 用的是 `not sub.get(...)`，在這一點上兩邊反而是一致的，所以這是診斷分類一致性的小退步。
- 這不影響分數，因為兩邊都會把這筆排除，只是分類計數不同。
- 建議兩邊共用同一個正規化 helper（snapshot 為 str 且 strip 後非空才算有值，mode 也做同樣處理），並補上空字串和純空白字串的對照測試。

**P3：score 的欄位來源要確認**
- resolve 讀 snapshot 和 mode 時，會在送出列沒有值時改讀 intent 本身（`r.get(...) or base.get(...)`）。
- score 只讀 `sub.get("question_snapshot_id")` 和 `sub.get("mode")`。封包沒有說明 `submitted_intent_by_qid` 裡的 `sub` 是否已經合併了 intent 的欄位，也沒說明同一題有多列時，是否和 resolve 一樣優先取真實送出、而不是 dry_run。
- 如果沒有合併，「送出列沒有 mode、intent 有 mode=live」這種情況會讓兩邊判定不同。請提供 score 端建構 `sub` 的程式片段。

**P4：測試覆蓋可以再補**
- resolve 的 live 測試沒有檢查 `res_live["status"]`，也沒有檢查 CLI 結束碼。建議補上，確認 live 缺 snapshot 時整體不會是 ok/0。
- score 端沒有負向測試：「live、snapshot 為 None、沒有結算事件」應計入 no_resolution_event，不能計入 submitted_without_snapshot_id。
- 沒有測試「已有結算事件的無 snapshot 列會被 missing_submitted_snapshot 拒絕」。目前這只是口頭說明，建議補一個 fail-closed 測試。
- 沒有測試 mode 缺失或大小寫不同的情況（對應 P1）。

---

## 四、需要提供的資料
1. score.py 裡 `submitted_intent_by_qid` 的建構方式：欄位是否有從 intent 補值、多列時的優先順序（對應 P3）。
2. provenance 段落中 `missing_submitted_snapshot` 的程式片段。
3. 正式資料中 live 送出列的 mode 是否都有填、值是否都是小寫 `"live"`（決定 P1 的實際風險有多大）。

---

## 五、總結
r6 確實回應了 r5 的待確認事項：反向篩選和 live 缺 snapshot 報錯都是方向正確的收緊。我沒找到 blocker，結論是 **PASS**。建議優先處理 P1（mode 改白名單）和 P2（resolve/score 共用正規化），P3、P4 可以之後補。最終是否核准由負責人決定，本審查不代表核准。