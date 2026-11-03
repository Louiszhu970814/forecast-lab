# Forecast Lab 程式碼審查：autoload_v3（Codex unverified 相容性修正）

## 結論：PASS（有條件，只限程式碼審查）

- 我只讀了 source packet 裡的 diff 和完整原始碼，**沒有執行任何測試、指令或模型**。
- 在程式碼裡沒有找到 blocking 缺陷。
- 這個 PASS **不是**合併、上線、baseline、auto_reaccept enforce 或重跑 canary 的核准。這些都要 Louis 另外決定，我不代為核准。
- 下列任一項成立，結論就改為 BLOCKED：
  - 完整回歸測試沒有全部通過；
  - PLAN／決策紀錄的 D124 原文明確寫著「所有線都要 model_check==verified」，而且沒有修訂紀錄（見 G1）。

---

## 一、證據等級

**我實際讀過（靜態閱讀）**
- `src/autoload.py` 的 diff 和全文，`src/autoload_maintenance.py`、`src/adapters/gpt.py`、`src/adapters/base.py`、`tests/test_autoload_model.py`、`tests/test_autoload_maintenance.py` 全文。
- `forecast.py` 的節錄：`runtime_fingerprint`、`Preflight`、`_acceptance_records`、`incomplete_acceptance`、`check_acceptance`、`preflight`、`quota_model_check`、LineStop 段落。
- PLAN 第 865–887 行（§7.7）。

**作者自述，我無法驗證**
- 「167 targeted passed in 2.71s」。
- 隔離 workcopy 和 audit hook 有效。
- 兩個檔案的 hash 和 packet 一致。

**尚未完成**
- 作者自己說完整回歸還在跑，所以目前不能說回歸已通過。

**和作者說法不一致**
- 作者說新增 40 項回歸，但依 `tests/test_autoload_model.py` 的參數化計算是 37 項：1 + 6（argv）+ 6（catalog）+ 10（acceptance）+ 2（states）+ 1（reported_id）+ 2×3（其他線）+ 5（整段量測）= 37。
- 回報前請更正數字，或指出另外 3 項在哪裡。

**這次無法看到**
- `test_autoload.py` 的 `fx`／`_loaded_executor`／`_result`、`forecast.RUNTIME_FILES`、`flab.ledger.transaction` 的實作、gemini/claude adapter 遇到 mismatch 時會不會設 `error_type`，以及 D124 原文。

**關於 Louis 問的「Codex 模型是 gpt-6-astra」**
- 程式和測試只能證明**請求的** model 是 `gpt-6-astra`：`AdapterRequest` 從凍結的 config 讀到這個值，測試也斷言它。
- Codex 事件裡沒有模型 ID，所以紀錄上仍然是 `reported_model_id=None`、`model_check=unverified`。
- 我們不能說「已證實 Codex 實際跑的是 gpt-6-astra」，只能說「請求了 gpt-6-astra，而且有效的 production acceptance 允許這個組合」。

---

## 二、Blocking

**程式碼層面：無。**

**G1．治理條件（需要確認，可能升級為 blocking）**
- 位置：`autoload.run` 的完成判定，原本的 `result.model_check == "verified"` 改成 `model_proof["accepted"]`。
- 這改變了 D124 量測的接受條件。背景說 D124 上線時要求所有模型 `model_check==verified`。
- 如果這只是 autoload_v2 的實作和 §7.7 衝突，修正合理；如果 PLAN 的 D124 原文就這樣寫，就要在 PLAN／決策紀錄補一條修訂（例如 D125）並由 Louis 決定。修訂內容包括：Codex 依 §7.7 記為 unverified；autoload 只在下面「三」列出的窄範圍證據都成立時才接受；evidence 必須寫明這是請求的 model，不是回報的 model。
- 這批只改了 src 和 tests，沒有文件同步。我看不到 D124 原文，所以列為合併前必要確認，不代為判定。

---

## 三、重點檢查 (1)–(5)

### (1) 是否照 §7.7 實作，沒有偽造模型身分：符合

- `model_evidence` 原樣保留 `result.model_check` 和 `result.reported_model_id`。接受時的 basis 是 `codex_requested_model_and_accepted_profile`，同時記下 `requested_model`／`requested_effort`，**不會**把 unverified 改成 verified，也沒有替 reported ID 填值。
- 只有在 `player=="gpt"`、`model_check=="unverified"`、`reported_model_id is None` 三者都成立時才走例外路徑。有回報 ID 的 unverified、mismatch、undetermined 都會被拒絕，測試有涵蓋。
- 測試用的是**未修改**的 `gpt.parse()`，餵入合成 JSONL，沒有用假的 verified 代替。這點正確。
- 仍有殘餘風險：如果伺服器端換了模型，本機不可能偵測到。這和正式預測現行的風險相同（`forecast` 只在 mismatch 時 LineStop）。這次修正只是讓 autoload 和 production 的判定一致，沒有擴大風險。

### (2) 證據是否夠窄：大致足夠

| 情境 | 擋住的機制 | 評估 |
|---|---|---|
| 錯的 model | argv `-m` 必須等於 `build_argv(req)`；production acceptance 篩選 `(model, effort)`；catalog entry 用 `req.model` 計算 | 有效 |
| 不同 effort | `check_acceptance(production={model,effort})` | 有效（只要求有一筆符合的 production 紀錄，不要求是最新那筆，和 preflight 相同） |
| catalog 變更 | 磁碟上的 catalog 再套一次限制必須不變、要等於 `result.catalog_entry_sha256`、要等於 latest acceptance 的值 | 有效 |
| CLI 不同 | `Preflight(cli_version=result.cli_version)` 對照 latest 和 production 紀錄；binary sha 由維護控制器檢查 | 有效（前提是透過控制器執行，見 N9） |
| runtime code 不同 | `runtime_fingerprint("gpt")` 對照 proof 裡的 `code_sha256` | 有效 |
| proof 無效或缺失 | `evidence_sha256` 不符，或檔案不存在 | 有效 |
| 有未完成的 isolation acceptance | `incomplete_acceptance()`，並排除 `source=autoload` | 有效 |
| 讀檔或解析失敗 | 捕捉 OSError/ValueError/KeyError/TypeError/StopIteration 後拒絕；其他例外會被 `run()` 外層捕捉，仍然會還原、不會通過 | 失敗時封閉 |

注意：`result.argv != gpt.build_argv(req)` 在真實 adapter 下幾乎一定成立。`run`／`parse` 本來就用同一個 `req` 呼叫 `build_argv`，所以這只是**自我一致性檢查**，用來擋事後被竄改的結果，不能證明 CLI 實際收到什麼參數。真正把 adapter 綁定到驗收版本的是 runtime fingerprint 加上控制器的 protected_hashes（見 N3）。

### (3) 其他兩線的規則、工具和恢復安全

- claude／gemini 仍然只在 verified 時接受，undetermined／unverified／mismatch 都被拒絕，有 6 項測試。
- 工具白名單、`tool_violations`、退出碼、timeout／killed、`completed`、restore 和 manifest 流程都沒有改動。
- 版本改成 v3 後，`unclosed()` 和 `recover_placement()` 不依版本過濾，所以 v2 的歷史 run 仍然可以辨識和恢復。
- 錯誤標記的語意有變動（見 N2）：`error_type` 為 None 且 `model_check=="mismatch"` 時，現在會記成 `model_mismatch`，接著 `break`，維護控制器丟出例外並建立 STOP_SUBMIT。如果 claude 或 gemini 的 adapter 原本遇到 mismatch 不設 `error_type`，以前會記成 `incomplete run/version`，只得到 measurement_failed、不會 STOP；現在會 STOP。這是更嚴格的方向，也和 forecast 遇到 mismatch 就 LineStop 一致，但仍然是動到其他線的行為，要寫進文件並補測試。

### (4) 在六鎖內是否會死鎖，或讀錯 project/log

- 沒看到死鎖：`model_evidence` 不取 `F.Lock`；`incomplete_acceptance` 用的 `transaction(data_dir)` 和既有的 `_append` 相同，呼叫時沒有外層 transaction 包住；也沒有呼叫 `load_catalog`、`codex debug` 或任何模型 CLI。
- 讀 log 的位置正確：`acceptance_log=data_dir / "acceptance.jsonl"` 會跟著控制器的 `self.root`。
- 有一個潛在的讀錯 project 問題（N1）：`project_root` 預設是 `PROJECT_ROOT`，`runtime_fingerprint` 也固定讀 `PROJECT_ROOT/src`。`Session(project)` 用的 project 不是 `PROJECT_ROOT` 時，相對路徑的 `evidence_path` 會去正式專案根目錄找。正式入口一定是 `Session(PROJECT_ROOT)`，所以目前不會出事；測試用的是絕對路徑，所以沒有測到。

### (5) 測試能不能抓回原本的缺陷：能

- `test_whole_measurement_with_real_codex_parser[None]` 斷言 `passed is True`，而且 `gpt in completed_players`。在 autoload_v2 的 `model_check=="verified"` 條件下，這兩個斷言必定失敗，所以能抓回原缺陷。這是推論，我沒有實際執行。
- 測試也確認：不新增 `acceptance_result`、不出現 `baseline_approved`、acceptance log 只會在尾端追加、`SKILL.md` 已清除。

---

## 四、Non-blocking 與必要修正

- **N1**　`autoload.run` 呼叫 `model_evidence` 的地方（diff 中約第 609 行）：`project_root` 沒有和 `data_dir` 綁在一起。
  - 修正：傳入 `project_root=data_dir.parent`，或在 `run()` 裡確認 `data_dir == project_root/"data"`。
  - 補測試：相對路徑的 `evidence_path` 搭配非預設的 project。
- **N2**　`run()` 的 else 分支裡 `error = result.error_type or (...)`：如果 `error_type` 是 `invalid_output`／`cli_error`，同時又有 mismatch，mismatch 會被蓋掉，不會 break 也不會 STOP。所以「mismatch 明確記為 model_mismatch」只在 `error_type` 為 None 時成立。這個缺口 v2 就有，不是這次引入的。
  - 建議：先判斷 `if result.model_check == "mismatch": error = "model_mismatch"`，或另外記一個 `model_mismatch: true` 欄位。
  - 補測試：claude／gemini 在 `error_type=None` 加 mismatch 時會 break，maintenance 會 STOP。
- **N3**　`model_evidence` 的 docstring 和變更說明說「完整 argv 與已驗收 adapter 的 build_argv 一致」，應改寫成「結果 argv 和目前 `build_argv` 自我一致；adapter 版本由 runtime fingerprint 綁定」，避免高估這項證據。
- **N4**　catalog sha 只和 `latest` 比對，沒有和實際符合 model/effort 的 production 紀錄比對。這和 `forecast.preflight` 一致，屬於既有設計。建議日後一併比對 production 紀錄的 `catalog_entry_sha256`。
- **N5**　證據紀錄：更正 37 和 40 的落差；附上完整回歸的實際輸出，才能說「通過」。
- **N6**　測試缺口：
  - `profile != "tier1"`（例如 tier2）被拒絕；
  - mismatch 的 `break` 位置：gpt 排在最後，目前測不出來，可以換成 gpt 在中間的排列；
  - `Session.run` 端到端：gpt 為 unverified 時得到 `status == "complete"`，而且不會建立 STOP_SUBMIT。
- **N7**　測試把 `'gpt-6-astra'` 寫死，等於和 config 綁在一起。建議改成和 `accepted.model['model']` 比較，另外單獨確認這是 config 值。
- **N8**　`unclosed()` 的 docstring 還寫「Only v2 managed starts」，已經過時。請在完整回歸裡確認沒有其他程式依賴 `"autoload_v2"` 這個字串。我無法看到全部程式碼。
- **N9（既有問題）**　直接呼叫 `A.run()` 時不會經過控制器，CLI 只比對版本字串、不比 binary hash。`main` 已經強制走 `Session`，風險低。
- **N10（既有問題，影響「如何解除卡住」）**　`Session.run` 裡，`calls or len(prior_runs) >= 2` 會丟出「D124 bounded probe already consumed」。D124 canary 已經有模型呼叫，所以這次合併後**控制器仍然會拒絕新的量測**。要再量一次，需要 Louis 另外授權，控制器的 AUTHORIZATION 和次數上限也要另一批修改（例如 R2 自動重驗那一批）。這次沒有動它，和本批範圍一致。

---

## 五、量測卡住怎麼解決，以及歷史紀錄

1. 合併本批前：完整回歸要通過，並確認 G1，必要時補 D 修訂。
2. 歷史上 D124 run 的 `measurement_failed` 保留原狀，不改寫、不補 acceptance_result、不改量測帳冊。
3. 可以做唯讀診斷，但不要直接拿現在的 ledger 跑 `model_evidence`，因為 `latest` 會隨時間改變。做法：
   - 在暫存副本裡，只保留該 run 的 `finished_at` 之前的 `acceptance.jsonl` 列；
   - 讀取 `data/autoload_runs/<run_id>/gpt/model_catalog.json` 和 `events.jsonl`；
   - 確認舊紀錄裡 gpt 的錯誤是 `incomplete run/version`，而且屬於 `unverified`／`reported_model_id=None` 這種情況；
   - 注意 runtime fingerprint 會用到現在的程式碼，結果只能當作參考，不能寫回任何紀錄。
4. 下一次真正的量測需要 Louis 授權新的 bounded probe，屬於後續批次。

---

## 結論重申

程式碼審查 PASS（有條件），沒有程式碼層面的 blocking。合併前必須：完整回歸實際通過、確認 G1、更正測試數量。建議 N1、N2 在本批一起修。我沒有核准 baseline、上線、重跑 canary 或啟用 auto_reaccept enforce。