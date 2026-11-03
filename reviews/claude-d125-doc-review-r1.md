# D125 最終文件複核（只核對文件與 r2 程式是否一致）

**結論：BLOCKED（文件層級）。** 共 3 項必改，都只需要改文字：不用改程式或測試，SHA 不變，也不用再跑完整回歸。改完後只需複核那幾段文字。

## 範圍與依據
- 這次只核對 PLAN v1.78 的狀態列、§7.7 Codex 段、§15 的 D125 決策列、§16 的 v1.78 條目、D125 操作文件，以及 packet 裡的回歸記錄、Gemini 審查和下游引用清單。
- 這次不是新的 code review，也不是新決策。我沒有執行任何測試，也沒有讀任何檔案。
- 程式行為只能根據 packet 裡的 Gemini 報告內容（含程式片段）、下游 grep 結果，以及你說明的 r2 狀態來比對。packet 沒有附上我上一輪 r2 審查的原文或紀錄 ID。所以這份回覆不重新給程式 PASS，也不代替先前的審查紀錄。
- 這次不涉及新授權：沒有要求補使用者授權，也不代為授權新的 canary。

## 實際證據（packet 內看得到的）
1. **SHA**：`src/autoload.py` 是 `c0a1a2e9…28e9`，`tests/test_autoload_model.py` 是 `876a9b28…2b4f`。文件、Gemini 報告和你的說明三處一致。
2. **完整回歸**：只有最後幾行 `2838 passed in 142.85s (0:02:22)`，與文件寫的數字一致。數字也能對上：r1 是 2826，r2 是 2838，多 12 項；相關測試 167 到 179，也多 12 項；Gemini 逐組加總新測試檔是 49 項。但這段記錄沒有指令、時間戳，也沒有跑測試前後的 SHA。「同一 SHA」這件事目前只有你的說明，記錄本身證明不了。
3. **Gemini 的時間點**：Gemini 審的是 r2 程式（SHA 相同），結論 PASS、0 blocker。當時完整 r2 回歸還在跑，Gemini 列為程序門檻。現在 2838 passed 的記錄補上了這一點，前提是 SHA 確實沒變。
4. **與程式一致的文件內容**（依 Gemini 引用的程式片段核對）：
   - argv 自我一致的檢查（`result.argv == gpt.build_argv(req)`）
   - `reported_model_id=null` 和 `model_check=unverified` 保留不改
   - mismatch 優先：`error="model_mismatch"`，然後 break，控制器建立 `STOP_SUBMIT`
   - 驗收證據的相對路徑用 `project_root=data_dir.parent` 解析；runtime 指紋仍取正在執行的原始碼
   - Claude 和 Gemini 兩線仍須 verified
   - D124 名額守門邏輯沒有放寬，舊的 failed 也不回填
   - N-A 限制裡「model_mismatch 標籤蓋過 tool_scope_violation」的描述，與 `error = "model_mismatch" if mismatch else result.error_type…` 一致，而且兩種錯誤都會停止並建立 STOP。
5. **下游引用**：沒有程式依賴 `autoload_v2` 或 `incomplete run/version` 字串。`baseline.py` 仍要求明確指定一筆 passed 的 autoload_result 紀錄 ID，跟文件說的「不自動核准來源」一致。

## 必改事項（BLOCKER，只改文件）
**B-D1：缺了只限 tier1 的條件。**
- r2 程式的條件是 `req.profile != "tier1" or result.argv != gpt.build_argv(req)` 就拒絕，而且有 `test_unverified_codex_exception_is_tier1_only` 測試拒絕 tier2、format_repair、acceptance 三種 profile。
- §7.7 用「僅當……時，接受此 Codex 限制」列出條件，操作文件「修正範圍」只寫「GPT 的 unverified 且 reported_model_id=null」。兩處都沒寫只限 tier1，所以文件描述的接受範圍比程式寬。
- 請在 §7.7 和操作文件第 1 點都補上：「僅限 GPT tier1 profile；tier2／format_repair／acceptance 不適用」。

**B-D2：唯讀診斷和暫存帳冊複核沒寫用哪一版程式。**
- 操作文件寫了 UTC 2026-11-03 13:28:22 的診斷結果 `accepted=true`，以及在「專案外暫存目錄」用帳冊副本複核也接受。但沒寫是用 r1 還是 r2（哪個 SHA）跑的。
- r1 到 r2 正好改了 N1：驗收證據相對路徑改用本次 data_dir 的專案根目錄解析。在專案外暫存目錄做的複核，剛好會受這個改動影響。
- 請寫明這兩次各用哪個 `src/autoload.py` SHA。如果是 r1，就標成「r1 診斷，不代表 r2 行為」，或刪掉 accepted=true 的表述。我不要求為此重跑任何東西。文件已經明說這不是 PASS 證據，這部分寫得對。

**B-D3：§16 v1.78 的「49 項」說法不精確。**
- 原文是「新增 49 項真實 GPT parser 合成事件與控制器回歸」，讀起來像 49 項全都用真實 parser。
- 實際上 49 項分成錯誤 model／effort／argv、catalog、驗收、其他兩線、路徑、控制器等多組。操作文件的寫法（「新增共 49 項回歸，涵蓋真實 GPT parser 的合成 JSONL、……」）才準確。
- 請把 §16 改成跟操作文件一樣的寫法。

## 建議（不阻擋）
1. **補完整回歸的紀錄**：在操作帳冊補一筆完整回歸紀錄，寫上指令、開始和結束時間、跑之前和之後兩個檔案的 SHA、結果 2838 passed。這樣「SHA 相同」就有紀錄可查，不只是口頭說明。
2. **列出 `model_evidence` 的實際欄位**：操作文件可以寫出實際 key，例如 `accepted`、`basis`（如 `codex_requested_model_and_accepted_profile`）、`catalog_entry_sha256`、`acceptance_record_id`、`production_record_id`。現在「驗收 record ID」一詞沒分清兩個 record ID。
3. **注明審查紀錄出處**：文件寫「Claude 親自完整複審 PASS」，建議附上那次審查的紀錄 ID 或檔案位置。這次的 packet 沒有那份紀錄，我在這裡不重新確認，也不擴大那份結論。
4. **寫明尚未套用**：在操作文件「驗證與部署狀態」加一句「截至本文件，尚未套用」。套用的結果（六鎖、前後六項 preflight）寫進交接和帳冊。交接文件不在 packet 裡，我沒有審。
5. **N-A 的「原始工具／額度證據仍另存」**：packet 的程式片段沒呈現這一點，我這次無法核對，只能依先前審查結論。建議在文件中保留這句描述，但不要在這裡再擴大說法。

## 不構成阻擋、也不要求重做的項目
- 不要求重跑完整回歸或相關測試（前提是 SHA 不變）。
- 六鎖和前後 preflight 在實際套用時執行就好。文件現在沒有寫成已經完成，這部分沒問題。
- B-3 已由 Gemini 的 PASS 處理。文件把 N-A 列為保留的非阻擋限制，這也恰當。

## 下一步
修正 B-D1 到 B-D3 後，把修改後的 §7.7、§16 條目和操作文件相關段落（最好附 diff）送來，我只複核這幾段。如果程式 SHA 仍是上面兩個值，屆時可以針對文件給出 PASS 或 BLOCKED。這份回覆不構成套用核准，也不構成任何新的 canary 或量測授權。