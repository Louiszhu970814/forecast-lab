## 結論：REQUEST_CHANGES

### 審查依據與證據性質
- 這份審查只讀了提供的 source packet，包括 manifest、三個 src 檔、三個測試檔、PLAN 相關節、D135 文件、四份候選提示詞與 README、diff、adapter／audit 整合片段、numeric scaling、部署腳本與部署故障測試。
- 我沒有執行任何測試、指令或部署，也沒有讀 manifest 以外的檔案。manifest 裡的 SHA-256 我無法比對，只能當作提供者的陳述。
- 文中的「實際證據」只指 packet 內附的測試輸出。「分析」是我讀碼得出的判斷。「建議」都不是已完成的工作。
- 這份結論不構成部署核准，也不代表 Gemini 或其他審查者已通過。

### 阻擋問題（Blocker）

**B1 `health_diagnostics.exposure()` 的總結 `status` 沒有把 `leak_confirmed` 當成要審查的情況，也沒看稽核帳冊的旗標**
- 位置：`src/flab/health_diagnostics.py` 的 `exposure()`。現在的寫法是：
  `'status':'requires_evidence_review' if any('suspected_leak' in (r.get('audit_flags') or []) for r in rows) else 'recorded_evidence_only'`
- 問題一：某次嘗試若只有 `leak_confirmed`，會被標成 `recorded_evidence_only`。這種情況可能來自 sources 含 metaculus.com、URL 被開啟、adapter 確認開啟，或從上一次嘗試繼承的 `leak_confirmed`。結果是最嚴重的暴露在新的健檢區塊裡顯得比 `suspected_leak` 還輕。這違反 packet 與 PLAN D135 寫明的不變式：「sources/open events/adapter flags still severe」「來源／開啟／未知及歷史繼承旗標不放寬」。`flags_retained` 雖然還列著原始旗標，但這個總結欄位是給人看的判讀標籤，現在等於在報表層把嚴重性放寬了。
- 問題二：`status` 只看 forecasts 列的 `audit_flags`，不看 `audits.jsonl` 事件自己的 `audit_flags`／`own_audit_flags`。如果舊 schema 的 forecasts 列缺旗標、但稽核事件裡有 `suspected_leak` 或 `leak_confirmed`，同樣會被標成「只有紀錄」。
- 這是 D135 新增程式碼的缺陷，屬於本次修正範圍，不是要求新功能。測試也沒涵蓋：`test_flags_and_inherited_evidence_are_never_cleared` 只測了 `suspected_leak`。
- 修正方式：把 forecasts 列與 audits 事件（含 `own_audit_flags`）的旗標聯集起來，只要有 `leak_confirmed` 或 `suspected_leak` 就標成要審查，`leak_confirmed` 最好另給更嚴重的標籤。要補的合成測試：(a) 只有 `leak_confirmed` 的列；(b) 旗標只出現在稽核事件；(c) 繼承來的 `leak_confirmed`。修完後重跑相同 SHA 的完整比對與雙審。

### 非阻擋限制與建議

**稽核（audit_v2）**
1. Gemini 例外只檢查 `chunk[start-9:end+1]=='read_url(metaculus.com)'`，沒檢查前一個字元是不是分隔符，所以 `xread_url(metaculus.com)` 這種寫法也會被豁免。這段落在 CLI 輸出的 Deny 欄位內，豁免的也只有 host 字串本身，實際風險很低。建議加上前界檢查，例如前一字元是 `[` 或空白。
2. 如果 Deny 清單用的是 `read_url(*.metaculus.com)` 或 `www.` 寫法，命中仍會算成 exposure。這是保守的處理，但代表 Gemini 的誤報不一定全部消除。只能說這些命中有了來源分類，不能說洩漏已排除。
3. audit_v2 會減少新嘗試的 `suspected_leak`，因此會間接影響線路停止門檻（兩次／七天）的觸發頻率。門檻值本身沒改。這屬於 D126 允許的工程修正，但請確認 PLAN §16 有對應的升版紀錄（packet 只附了 §4.3）。
4. 舊稽核紀錄不會重新分類。舊七題的 suspected 標記保留、不回寫，這點符合規格。

**閉題健檢（post_close_audit_v2／health_diagnostics_v1）**
5. 週期綁定用的是 `qid in (r.get("new_question_ids") or [])`，沒有沿用全檔一致的 `_same_id`（字串比較）。
   - int 與 str 型別不一致時，週期證據會被靜默漏掉，狀態退成 `no_recorded_attempt`。
   - 如果欄位是字串，`in` 會變成子字串比對，可能誤綁到別題，或直接拋出 TypeError。
   - 建議改用 `_same_id`，並先檢查是不是 list。
6. 遇到 JSON 合法但型別不對的列會直接崩潰，例如 `players` 是 list、`preflight` 不是 dict、`keyword_hits` 是 null、多選 `options` 是 dict 這類不可雜湊的值。崩潰時不會洩漏內容（fail closed），但整份報告也出不來。建議加型別防護。
7. `lifecycle()` 用 `r.get('tier',1)==tier` 嚴格比對，也只認 `player` 欄位；`_tier()`／`_player()` 則接受 `"2"`、`stage`、`player_id` 等寫法。兩邊不一致時，同一筆嘗試可能在線路覆蓋表裡消失。另外，在合格題上 gate2 已排除未完成的 start，`start_without_output` 實際上走不到。
8. 週期證據沒有依 snapshot 綁定。文件已註明這是觀察、不是完整的機會歷史，這點可以接受。但週期的 `reason`／`problems` 若提到其他題目，就會出現在合格題的報告裡，建議人工抽查。
9. 舊欄位 `tier1_spread` 現在對多選題也會有值（最大選項差）。意思和以前「多選為 None」不同，請在報表或文件說明。`HIGH_DISPERSION_THRESHOLD` 這個常數已經沒在用了。
10. Markdown 的 `_fmt` 對 list／dict 不會跳脫 `|`，可能弄壞表格。這個問題原本就有，屬外觀問題。
11. 原本就存在的限制（不在本次範圍）：併發偵測只涵蓋 fetch 前後，`_load_ledgers` 讀取期間的完整新增列偵測不到。

**提示詞候選**
12. 候選檔名和正式模板同名（`forecast_v2.md`、`forecast_numeric_v1.md` 等），README 卻稱為「候選 v1」。日後啟用時必須給新的版本識別（例如 `forecast_v3`），並寫入 §16，不能直接用同名覆蓋。
13. 候選要求把發布時間和取得時間寫進 `key_factors`，和「每項一個短語」的既有慣例有衝突。這只是建議性問題，要等獨立窗口驗證。合成渲染測試只能證明 placeholder 相容、條款存在，不能證明模型會遵守，也不能證明準確度提升。runtime 沒有引用這些候選，這點只有 packet 的陳述，我無法獨立確認。

**部署**
14. 已核對的部分（讀碼）：
    - 六鎖下才呼叫 `require_idle`，horizon 1200 秒。
    - stage 的 bytes 只讀一次，比對 SHA 後寫入的就是那些 bytes；部署故障測試涵蓋了 stage 在驗證後才被改動的情況。
    - 套用前後各跑一次 preflight 與 line_guard，結果一律 fail closed。
    - 會檢查受保護檔與目標檔是否被同時改動，並先備份原有的未提交內容。
    - 以 atomic replace 寫入，保留原權限。
    - smoke 測試失敗或逾時（BaseException）會回復，並比對回復後的雜湊。
    - 審查檔有雜湊綁定，且必須剛好一個 PASS 結論標題。
15. 部署的限制：
    - 回復時不會刪除新建的空目錄。
    - `apply_complete` 的 append 如果失敗，檔案已經套用，卻不會回復，也沒有完成紀錄。
    - 回復過程中若 `atomic` 失敗，`apply_rolled_back` 紀錄寫不出來，只能靠備份手動處理。
    - 受保護檔只涵蓋 initial manifest 已列的檔案，`src/` 底下新增的檔案偵測不到。
    - 故障測試沒涵蓋受保護檔被改、目標被同時改、套用後雜湊不符、回復雜湊不符、`require_idle` 拒絕，以及兩個結論標題的情況。
    - preflight 是否完全唯讀、會不會呼叫模型或網路，packet 沒給證據。

### 測試證據（只採 packet 內附輸出，我沒有執行）
- 完整套件：基線 100 failed／3213 passed；現在 100 failed／3241 passed。packet 標示失敗節點與摘要逐項相同（`same_failure_nodes: true`）。
- 這只代表沒有新增失敗，不是完整通過：100 個既有失敗仍在。
- 分項：230 passed（應為目標測試）、6 passed（應為部署故障測試）。
- 數字有落差：依 diff，新增測試約 25 個（audit 4、post_close 5、health_diagnostics 16），通過數卻多了 28。多出的 3 個無法從 packet 解釋，可能來自其他未提交的工作。建議附上通過節點的差異清單。

### 其他已核對、沒發現問題的部分（讀碼，非執行）
- 三閘門仍是 AND。未合格題的 `diagnostics` 一律是 None，Markdown 也會略過；新增的可選帳冊損毀時會讓 gate2 失敗並遮蔽輸出，有測試涵蓋。
- 代表輸出的選擇：有整合 input id 就用它，否則取第一份 valid，不再是最後一份覆蓋。
- `expected_players` 不再被當成「有要求第二層」的證據。
- 數值題的分歧改用實際單位加尺度座標，沒有 0.30 門檻；非有限值不參與比較。
- GPT 的排除只在結構化 query 欄位、而且是完整的 `-site:metaculus.com` 運算子時成立；搜尋結果與正文照舊算 exposure。
- 繼承旗標、sources 與 opened 事件的判定不受新例外影響，有測試涵蓋。
- D135 文件的「實際完成證據」仍標為待補，沒有預先宣稱完成。

### 不能宣稱的事項
- 不能宣稱完整測試通過、真實模型已驗證、準確度提升、本次已無洩漏、已部署或已雙審通過。
- 本次不重跑舊題、不做新預測、不送出，主賽維持停用。

修完 B1 並補上對應測試後，需要用新的 SHA 重新提交完整審查；在那之前不應套用部署。