## 結論：PASS

**這份結論的意思：** 這一輪我沒有找到阻擋問題。這個 PASS 只是我這位審查者對這次提交的判斷，不代表部署已核准，不能代替 Gemini 的審查，也不代表主賽可以啟用。我只讀了 packet 裡的內容，沒有執行任何測試、指令、部署或線上預測，也無法比對 manifest 裡的 SHA-256。下文的「實際證據」只指 packet 內附的輸出；「分析」是我讀程式碼後的判斷；「建議」都還沒有做。

### 一、上一輪問題的處理

**B1（上一輪唯一的阻擋問題）：已修正**
- `health_diagnostics.exposure()` 現在把三處的旗標聯集起來：forecasts 列，以及同一 `attempt_id` 稽核事件的 `audit_flags` 和 `own_audit_flags`。
- 判讀順序是 `leak_confirmed`、`suspected_leak`、`recorded_evidence_only`。`leak_confirmed` 有自己的標籤 `confirmed_exposure_requires_review`，嚴重度高於 suspected。
- 原始旗標仍完整保留在 `flags_retained`。不屬於這次嘗試的稽核事件（例如 `unrelated`）不會被納入。
- 測試：`test_exposure_uses_all_bound_flags_at_highest_severity` 有 6 個參數組合，涵蓋 forecast 與 audit 兩個來源、兩種欄位、兩種嚴重度；另有 `test_exposure_confirmed_dominates_suspected`。上一輪要求的三種情況都有涵蓋：只有 confirmed、旗標只在稽核事件、從上一次嘗試繼承的 confirmed（後者以 `prev_attempt_id` 情境和 audit 的 `audit_flags` 呈現）。
- 這是修正 D135 新增程式碼的缺陷，沒有放寬任何規則。

**N1：Gemini 例外的前界檢查已修正**
- 新條件要求 `read_url(` 前面緊接 `Deny:[`，或是一個空白字元。
- 測試：`test_cli_setting_requires_permission_token_boundary` 確認 `xread_url(metaculus.com)` 仍會被判為 suspected。

**N5：週期題號的型別比對已修正**
- 欄位只在是 list 時才使用，逐項以 `_same_id` 比對。字串形式的欄位不會再被當成子字串比對。
- 測試：`test_d135_cycle_ids_are_typed_and_normalized`，涵蓋 `["101"]` 和 `"101"` 兩種寫法。

**N12：已補充說明**
- README 和 D135 文件都寫明：候選提示詞啟用時要給新的正式版本識別（例如 forecast_v3），並寫入 §16，不能用同名檔覆蓋。

**測試數量落差：已能解釋**
- 新增 39 個節點：test_audit 5 個、test_health_diagnostics 23 個、test_post_close_audit 11 個。
- 移除 1 個舊節點，`test_diagnostics_high_dispersion_and_missing_tier2` 改名。
- 淨增 38 個，正好等於 3213 到 3251 的差距。總節點數 3313 到 3351 也吻合。
- 上一輪多出 3 個無法解釋的節點，來源是既有的參數化測試 `test_gate2_fails_when_ledger_has_truncated_json_line` 多跑了三個新增的可選帳冊。這個說法已和節點清單核對過，是一致的。

### 二、實際證據（只採 packet 內附輸出，我沒有執行）

- **完整套件：** 基線 100 failed／3213 passed；現在 100 failed／3251 passed。packet 標示失敗節點與摘要逐項相同。
  - 這只代表沒有新增失敗，**不是完整通過**。100 個既有失敗仍在，不能稱為 fullPASS。
- **240 passed：** 推測是目標測試加上 smoke 範圍。比上一輪的 230 多 10 個，和新增的節點相符（6＋1＋1＋2）。實際涵蓋哪些檔案，packet 沒有直接證明。
- **6 passed：** 和部署故障測試的 6 個函式數量相符。
- **manifest 的 SHA：** 我無法比對，只能視為提供者的陳述。

### 三、讀碼核對過、未發現阻擋問題的部分

**閘門與遮蔽**
- 三道閘門仍是 AND。未合格題的 `diagnostics` 一律是 None，Markdown 也會略過。
- 新增的三個可選帳冊（scheduler_jobs、audits、cycles）損毀時，gate2 會失敗並遮蔽輸出，有測試涵蓋。
- 併發偵測涵蓋 data/ 底下所有 *.jsonl 的 mtime 和大小。

**audit_v2**
- **Gemini 例外的成立條件：**
  - 檔名必須是 `agy_cli.log`，且事件是無法解析為 JSON 的純文字行。
  - 該行必須符合 glog 開頭和 `CLI settings initialized: permissions=&{Allow:[…] Deny:[` 的格式。
  - 命中必須落在 `] Ask:` 之前，完整字串必須是 `read_url(metaculus.com)`，且前界正確。
  - 找不到 `] Ask:` 時不會豁免（因為 find 回傳 -1）。
  - 依 adapter 的程式片段，這個檔案來自 `--log-file` 參數指定的路徑，並在 `--sandbox` 下執行，和 packet 的陳述一致。
- **GPT 例外的成立條件：**
  - 只看 `item.started` 和 `item.completed` 事件，item 類型是 web_search，action 類型是 search。
  - 只限結構化欄位：`query`、`action.query`、`action.queries[i]`。
  - 必須是完整的 `-site:metaculus.com` 運算子，而且 span 精確對應到命中位置。
  - 搜尋結果和正文中的相同字串仍算 exposure，有測試涵蓋。
- **不受影響的部分：**
  - sources、opened 和 unknown 的 URL 事件、adapter 旗標、繼承旗標的判定流程都在關鍵字分類之前或之外，不受新例外影響。
  - 所有豁免的命中仍保留在 `keyword_hits` 裡，只是 kind 不同。
- 新稽核事件會寫入 `audit_version: audit_v2`。

**post_close_audit_v2／health_diagnostics_v1**
- 代表輸出的選擇：有整合 input id 就用它，否則取第一份 valid，不採最後一份。
- `expected_players` 不再被當成「有要求第二層」的證據。
- 數值題的分歧改用實際單位加尺度座標，不套用 0.30 門檻。非有限值和布林值不參與比較。
- 是非題和完整的多選機率向量仍保留 0.30 的描述性警示。
- 基準率和來源只做文字標記，一律標為 `not_independently_verified` 或 `model_reported_links_only`。

**提示詞候選**
- 只存在於 docs/experiments，runtime 沒有引用（這點依 packet 陳述，我無法獨立確認）。
- 合成測試只驗證 placeholder 集合相同和條款存在。

**部署腳本**
- 在六鎖下才呼叫 `require_idle(horizon 1200s)`。
- stage 的 bytes 只讀一次，比對 SHA 後寫入的就是那些 bytes。
- 套用前後各跑一次 preflight 和 line_guard，結果一律 fail closed。
- 會檢查受保護檔與目標檔是否被同時改動，並先備份原有的未提交內容。
- 以 atomic replace 寫入，保留原權限。
- smoke 或 postflight 失敗、或發生 BaseException 時會回復，並比對回復後的雜湊。
- 審查檔有雜湊綁定，且必須剛好一個結論標題。
- TARGETS 的 13 個檔案和 manifest 一致。

**文件**
- D135 文件的「實際完成證據」仍標為待補，沒有預先宣稱完成。

### 四、非阻擋限制與建議（都還沒做）

**健檢與暴露總結**
1. `exposure()` 的旗標聯集直接迭代 `r.get(key) or []`。如果 JSON 合法但欄位型別錯誤，例如 `audit_flags` 是字串 `"leak_confirmed"`，就會被拆成單一字元，嚴重度跟著消失。現行 audit.py 都寫 list，所以這只會發生在違反 schema 的資料上。建議改用 `_as_list` 這類正規化，或直接把型別異常標成需要審查。
2. 如果某次嘗試完全沒有對應的稽核事件，總結只會顯示 `recorded_evidence_only`。limitation 文字已寫明「沒有紀錄不代表沒有暴露」，但建議另外加一個 `audit_event_missing` 標記。
3. 遇到型別錯誤的列仍可能直接崩潰，例如 `players` 是 list、`preflight` 不是 dict、`attempt_id` 或 `input_attempt_ids` 不可雜湊、多選 `options` 含 dict。崩潰時不會洩漏內容（fail closed），但整份報告也出不來。這是上一輪 #6，保留。
4. `lifecycle()` 只認 `player` 欄位，tier 也是嚴格比對；`_player()`／`_tier()` 則接受 `player_id`、`model`、`"2"`、`stage` 等寫法。兩邊不一致時，線路覆蓋可能顯示錯誤的「沒有紀錄」。另外，在合格題上 `start_without_output` 實際上走不到。這是上一輪 #7，保留。
5. 週期證據沒有依 snapshot 綁定。週期的 reason／problems 若提到其他題目，可能出現在合格題的報告裡，建議人工抽查。這是上一輪 #8，保留。
6. `tier1_spread` 現在對多選題也會有值（最大選項差），意思和以前不同。`HIGH_DISPERSION_THRESHOLD` 這個常數已經沒在用。`_fmt` 對 list／dict 不會跳脫 `|`，可能弄壞表格。這些都是外觀或說明問題。
7. 新增的三個可選帳冊會被排程頻繁寫入。啟用 live API 時，併發偵測導致遮蔽的頻率可能上升。這是 fail-closed，屬於可用性的限制。

**audit_v2**
8. 如果 Deny 清單寫成 `read_url(*.metaculus.com)` 或 `www.` 形式，或大小寫不同，命中仍會算成 exposure。這是保守的處理，但代表 Gemini 的誤報不一定全部消除。
9. 理論上，如果 CLI log 會原樣寫入外部文字，有人可以偽造一整行 glog 設定行。即使如此，能被豁免的也只有精確的 host 字串，其他關鍵字仍算 exposure，所以風險很低。
10. audit_v2 會減少新嘗試的 `suspected_leak`，因此會間接影響兩次／七天停線門檻的觸發頻率。門檻值本身沒改，舊旗標也不回寫。packet 只附了 PLAN §4.3，請確認 §16 有對應的升版紀錄。

**真實資料的陳述**
11. D135 文件中對十道真實閉題的描述我無法核實，例如「六個 T1 缺口」「十一個命中＝十個 Gemini 加一個 GPT」。這只能算來源分類的觀察，不能當作沒有洩漏或線路健康的證明。

**提示詞候選**
12. 候選要求把發布時間和取得時間寫進 `key_factors`，和「每項一個短語」的慣例有衝突。合成渲染只能證明格式相容，不能證明模型會遵守，也不能證明準確度提升。啟用前要在新的獨立窗口另行登記並驗證。

**部署**
13. 部署的已知限制：
    - 回復時不會刪除新建的空目錄。
    - `apply_complete` 的 append 如果失敗，檔案已經套用，卻沒有完成紀錄。
    - 回復過程中若 `atomic` 失敗，`apply_rolled_back` 紀錄寫不出來，只能靠備份手動處理。
    - 受保護檔只涵蓋 initial manifest 已列的檔案。
    - 故障測試沒涵蓋受保護檔或目標檔被同時改動、套用後雜湊不符、回復雜湊不符、`require_idle` 拒絕、出現兩個結論標題等情況。
    - preflight 子程序在父程序持鎖時執行，它是否完全唯讀、會不會呼叫模型或網路，packet 沒給證據；目前靠 300 秒 timeout 才 fail closed。
    - smoke 也包含 test_practice，它是否只在暫存目錄操作，packet 沒給證據。

### 五、不能宣稱的事項

- 不能宣稱完整測試通過（100 個基線失敗仍在）、真實模型已驗證、準確度提升、本次已無洩漏，或線路目前健康。
- 不能宣稱已部署、已完成雙審，或 Gemini 已在這個 SHA 上通過。
- 本次不重跑舊題、不做新預測、不送出，主賽維持停用。
- 正式提示詞、模型、統計參數、cap、窗口和停線門檻都不變。歷史旗標不回寫、不清除。
- 要套用，仍須符合 D135 文件寫明的條件：同一份 SHA 由 Claude 與 Gemini 實際雙 PASS，並完成部署腳本的全部檢查。D135 文件的完成證據只能在實際套用之後補記。