> 官方 CLI 審查原文；來源封包 `data/operations/d133-closed-practice-20261104/review-packet-r4.txt`。程式雜湊以機器產生的 `reviewed-files-r4.json` 為準，非模型轉寫字串。

## 結論：PASS

# D133 r4 最終差異審查（審查者：Claude）

**範圍：** 只看 r3 提出的兩個修正（B1、D-B2）、新增測試、部署腳本 diff，以及最終標準 SHA。r2／r3 已經全文審過的其他部分沒有重審。

**這份 PASS 只代表一件事：** 我讀完 r4 的程式碼，認為 r3 的兩個阻擋項都已修正，也沒有看到新的阻擋問題。它不代表以下任何一項：
- 部署已發生，或可以跳過 Gemini 的 r4 審查。gate 要求 `JOB/gemini-r4/answer.md`，Gemini 的 r3 PASS 不能替代。
- Louis 已核准。
- 預測準確。

在 Claude 與 Gemini 都對同一組 r4 SHA 實際判定 PASS 之前，部署仍然擋住。正式 root 維持不動。

---

## 0. 證據等級

| 類別 | 內容 | 我能確認到的程度 |
|---|---|---|
| 原始碼 diff | forecast.py 的 diff、test_practice.py 新增的 2 個測試、部署腳本 diff 與全文、6 個故障測試全文 | 已逐行讀過。我沒有執行任何測試或指令 |
| 標準 SHA | r3 與 r4 的 JSON | 只做記錄。兩份比對後，只有 `src/forecast.py`（`1c74…`→`42c3…`）和 `tests/test_practice.py`（`9a5b…`→`d3c0…`）改變，其餘四個檔案 SHA 相同，和「另外四個檔案 diff 為空」一致。我無法驗證實際位元組；這要靠部署腳本在鎖內比對 |
| 測試比對 | 基線 3134／100；候選 3213／100；失敗節點與摘要相同；練習測試 79 個；相關測試 205 passed；部署故障測試 6 passed | 都是提交者的說法，我沒有重跑。數字彼此對得上：77+2=79、203+2=205、3211+2=3213、4+2=6 |
| 隔離 smoke 耗時 | 說明寫約 1.3 秒，附的輸出寫 1.47 秒 | 兩者有小落差，不影響判定（都遠小於 120 秒）。建議主持人存檔時以實際輸出為準 |
| 營運狀態 | 0 次新練習呼叫；108 題全被遮蔽；10 題既有輸出已稽核 | 提交者的說法，資料包沒有原始回應 |
| 部署證據 | 無 | 依規定只能在雙方 PASS 之後產生，我不推定它存在 |
| 準確度 | 無 | 沒有任何練習結果 |

---

## 1. r3 阻擋項核對

### B1：line_stopped 排序擴大了解析範圍 → 已修正

**diff 內容：**
- `ordered = sorted((r for r in records if r.get("type") == "forecast_attempt" and r.get("player") == player), key=lambda r: parse_iso(r["finished_at_utc"]))`
- 迴圈改成 `for r in ordered`。

**我讀到的結果：**
1. 現在只有「型別為 forecast_attempt 且屬於本線 player」的紀錄會被解析時間，解析範圍回到 r2 的樣子。我在 r3 提的修法就是這樣。
2. 外部 guard 紀錄的 type 會被改寫成 forecast_attempt，所以仍會進入排序。這些紀錄已由 `guards()` 在讀取時驗證過 `finished_at_utc`，不會因此新增例外。
3. 其他地方仍用原本的 `records`：
   - `completed` 集合只用 `r.get("player")`，不解析時間。
   - mismatch 段的 generator 先過濾 player，再依 `pipeline_version`、`model_check == "mismatch"` 過濾，之後才解析時間，靠 `and` 短路保護。所以其他 player 的壞時間、或本線沒有 mismatch 的 note 都不會被解析。行為和 r2／r3 相同。
4. 迴圈裡原有的 type／player 判斷現在是多餘的，但無害。
5. 時間序修正仍然有效：`ordered` 單調遞增，7 天視窗的計算 `t >= at - 7d` 正確。

**新增測試：**
- `test_unrelated_metadata_and_other_player_bad_time_do_not_stop_line`：放一筆沒有 `finished_at_utc` 的 claude note，和一筆時間為 `malformed` 的 gpt attempt。r3 程式在排序時就會丟出 KeyError 或 ValueError，r4 則回傳 None。這個測試確實能抓到 B1 的回歸。
- `test_mixed_recent_formal_practice_exposures_still_stop`：正式暴露在 NOW，練習暴露在 NOW−2 天。排序後依序累積兩筆，第二筆時 7 天內有 2 筆，所以停線。這是我要求的反向案例。它在 r3 下也會通過，屬於回歸保護，不是 bug 重現，這是預期的。
- 一個月前那個案例的測試仍保留。

**殘留（不阻擋，r2 起就存在）：** 本線自己的 forecast_attempt 如果缺少或有壞的 `finished_at_utc`，仍會丟錯。這是 fail-closed，而且已知的寫入端都會寫這個欄位。

### D-B2：smoke 在正式 root 跑四個測試檔 → 已修正（採我在 r3 提的 a 方案）

1. 正式 root 的 smoke 現在只跑 `pytest tests/test_practice.py -q --tb=short`，timeout 仍是 120 秒。
2. 其餘三個測試檔的證據：提交者說在隔離 stage 用 `python -m pytest -q tests/test_practice.py tests/test_forecast.py tests/test_advisory.py tests/test_budget.py --tb=short` 跑出 205 passed、1.47 秒。這符合 a 方案要的「相同檔案、同一條指令、遠小於 timeout」。我沒有重跑。
3. 新增故障測試 `test_smoke_failure_restores_original_source`：mock 的 smoke 回傳 1 時，會丟出 RuntimeError('deployment smoke failed')，六個目標檔還原成初始雜湊（包括刪掉原本不存在的檔案），而且 operations.jsonl 記錄 apply_rolled_back。這補上我 r3 要求的故障案例。
4. 我把 r4 的 test_practice.py 全文重讀了一次：
   - 帳冊、line_status、quota、raw、batch 的路徑都指向 `tmp_path`，或已經 monkeypatch。
   - 練習的 `project` 參數都是 `tmp_path`。
   - adapter 一律用 FakeAdapter；我沒有看到真實模型呼叫，也沒有 POST。
   - 結論：我沒找到會寫入正式 `data/` 帳冊的具體路徑。兩個我看不到實作的預設值，列在 N-1。

### 我在 r3 的建議：部署前後加唯讀 line_stopped 檢查 → 已採納

- preflight 子程序現在對每個 (player, tier) 都多存一個 `line_guard=F.line_stopped(F.LINE_STATUS_LOG,p,F.FORECASTS_LOG)`。只要任何一項不是空的，gate 就會拒絕。
- 套用前用的是 root 的舊程式。套用後用的是 r4 程式，直接在正式帳冊上證明新的 line_stopped 不會丟錯，也不會判定停線。
  - 如果丟錯：子程序回傳非 0 → RuntimeError → 原始碼回滾。
  - 如果判定停線：gate 拒絕 → 回滾。
- 兩種情況都是 fail-safe，正好補上 r3 說的「部署時看不出共用路徑回歸」的盲點。
- 子程序裡 `transaction(log.parent)` 用的是交易鎖，不是那六把鎖之一；practice.run 本來就在持有六鎖時使用它，所以不會和父程序搶鎖。
- 預期影響：如果部署當時有任何一條線正處於停線或 quota backoff，部署會被拒絕或回滾。這是保守行為，不是 bug，但主持人要在部署前確認三條線的狀態。

### 我在 r3 的 N-e：判定解析太寬鬆 → 已修正

- 新的 `verdict()` 用 `re.M`，只接受行首的固定格式（兩個 #、空格、「結論」或「Verdict」、冒號、PASS 或 REQUEST_CHANGES），而且整份答案必須剛好出現一次，否則丟 ValueError。不再從引用句做模糊判定。
- `test_authentic_request_changes_is_not_pass` 驗證：「前一輪 PASS」這類引用句加上真實的 REQUEST_CHANGES 標題，會被正確擋下。
- 審查參照的固定路徑已改成 `JOB/<model>-r4/answer.md`，標準檔改讀 `reviewed-files-r4.json`。

---

## 2. 部署流程其他檢查（沒有發現阻擋項）

- 順序如下：
  1. 檢查 gate 和標準檔逐字相同、檔案集合等於 TARGETS、基線失敗相同。
  2. 檢查兩份審查都是固定路徑、SHA 符合、判定為 PASS。
  3. 取得六鎖，確認 `require_idle`。
  4. 一次讀入 stage 位元組並比對 SHA。
  5. 跑套用前的 preflight。
  6. 檢查受保護檔案和目標檔的初始雜湊。
  7. 備份。
  8. 用 atomic 方式寫入記憶體中的位元組，寫入後比對雜湊。
  9. 跑 smoke。
  10. 跑套用後的 preflight。
  11. 再比對一次目標檔與受保護檔的雜湊。
  12. 任何例外都回滾並檢查回滾後的雜湊。
  13. 記錄 apply_complete，包括 deployment_script_sha256。
- 套用前的 preflight 如果失敗，發生在寫入之前，也在 try 區塊外面，所以不需要回滾，正式 root 不會被改動。這是正確的。
- 沒有刪除或回滾任何正式帳冊，也沒有啟用主賽 33121。

---

## 3. 非阻擋建議（不影響本輪判定，不是新的實作範圍）

- **N-1：test_practice.py 有兩個依賴預設值的地方，我在資料包裡看不到實作。**
  - (a) `test_score_uses_frozen_id_and_allows_resolution_text_edits` 沒有覆寫 `ctx.make_dir` 和 `remove_dir`，所以會用預設的 `make_attempt_dir`。
  - (b) `test_unknown_practice_visible_in_preflight_before_cli` 呼叫 `F.preflight`，除了 acceptance_log 以外的路徑都是預設值。
  - 這兩點 r3 就已存在，我當時也接受了。正常情況下前者應該是系統暫存目錄，後者是唯讀。建議主持人確認 `make_attempt_dir` 不會寫進專案的 data/。另外，可以在 smoke 前後對 `PROJECT/data/*.jsonl` 計算雜湊並存檔（只記錄、不阻擋），作為縱深防禦。
- **N-2：line_guard 拒絕這條路徑沒有故障測試。**
  - 六個故障測試都把整個 `preflight()` mock 掉了，`d['line_guard']` 這段判斷沒有被執行到。
  - 程式只有一行，我讀過是正確的。建議補一個測試：mock subprocess.run 回傳含 `line_guard` 非空的 JSON，斷言會丟 RuntimeError 並回滾。
- **N-3：verdict 正規式的邊界。** `[:：]\s*` 和 `\s*$` 裡的 `\s` 會吃到換行，所以「結論：」標題下一行單獨寫 PASS 也會被接受。實際上很難誤判，但建議改成 `[ \t]*`。
- **N-4：smoke 耗時的記錄落差。** 說明寫 1.3 秒、輸出寫 1.47 秒，建議存檔時以實際輸出為準。D133 文件的驗證紀錄請更新為 r4 的 3213／100、79 個練習測試、205 個相關測試、6 個故障測試，以及證據路徑。
- **N-5：排序後停線原因裡的 attempt_id 可能改變。** 停線原因字串裡的 attempt_id，現在是時間最早的那一筆，不再是檔案順序中的第一筆。只影響可讀性，不影響安全。
- **仍然有效、但都不阻擋的舊建議：** r3 的 N-a（run 崩潰後永遠停在 run_in_progress）、N-b（failure_types 不完整）、N-c（prepare 逾時沒有測試）、N-d（版本升級要同步修改共用模組）；以及 r2 的 N1、N6、N7、N8、N11。

---

## 4. 部署前提與下一步

1. 這份 PASS 只對應上面的 FINAL R4 CANONICAL SHA。`reviewed-files-r4.json` 和 `deployment-gate.json` 的 files 欄位必須和那份 JSON 逐字相同。
2. 還需要 Gemini 對同一組 r4 SHA 寫出獨立的實際審查（`gemini-r4/answer.md`），內容要剛好有一個符合格式的 PASS 標題。
3. 雙方都 PASS 後，主持人才在六鎖下執行：備份 → 套用前 preflight（含 line_guard）→ 雜湊驗證 → 套用 → smoke（只跑 test_practice.py）→ 套用後 preflight → 雜湊再驗證。然後執行實際的 inventory 和 report。預期是 inventory_only，選題 0 題，模型呼叫 0 次。
4. 全程不啟用主賽 33121，不改 D118 窗口和上限，不改 prompt、模型、runner、adapters、netguard，不做 POST，保留 D132 和 D134 的未提交變更。

**狀態總結：**
- 程式是尚未套用的 r4 候選版本。本輪 Claude 判定 PASS，阻擋項 0 個；r3 的 B1 和 D-B2 都已修正。
- 實際練習 0 次呼叫，沒有部署證據，也沒有任何準確度結果。