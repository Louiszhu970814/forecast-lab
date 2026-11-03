# D121 第一批最終版與 M5 最終版合併驗證

本輪僅在提供的獨立工作副本實作及離線測試，依據 `D121-FAIL-SOFT-BATCH1.md` 與 `M5-NUMERIC-SPEC.md`（第十三、十四節優先）。沒有真實預測、比賽查詢、模型 CLI 呼叫、送出、commit、部署或子代理；未讀取正式專案、執行資料、憑證或其他工作對話。指定正式環境的 `.venv/bin/python` 僅作為直譯器使用。

## 衝突及合併核對

使用者提供的 patch 紀錄沒有 reject，副本實查亦無 `.rej`。有 `src/forecast.py.orig`、`src/scheduler.py.orig` 兩份 patch 備份；不存在需要人工選邊的 reject hunk。兩份備份與合併後檔案的差異已留存於 `.d121-checks/forecast-merge.diff`、`.d121-checks/scheduler-merge.diff`，備份檔已刪除。

| 交疊／問題 | 處理及保留語意 |
|---|---|
| `forecast.py`：D121 恢復、期限、quota 與 M5 題型分支 | 比對備份，沒有刪除既有頂層函式。變更限於 `forecast_prompt`、`repair_prompt`、`run_attempt`、`_may_rerun`、`eligible` 的 M5 擴充及 numeric imports。保留 D121 分開讀取工具與 final stream、`recovery_no_rerun`、已持線鎖的中斷恢復、鎖外 quota 重讀及重讀後期限檢查、mismatch 退避與僅 repair 繼承等修正；保留 M5 prompt/schema、百分位比較、late values、語意錯誤不修復／重跑、動態開關。此檔本輪沒有修改。 |
| `scheduler.py`：等待重評估與數值題資格 | 保留 D121 原因分類、各原因專用期限、quota snapshot 保留量與冪等解除事件；保留 M5 派工前重新驗證及 finalize 對關閉開關／不完整縮放的拒絕。僅新增下述 token 量測時間限制。 |
| `reconcile.py`：兩批共同修改 | 核對已持線鎖內的恢復、注入時鐘的停線判定及預算拒絕原因傳遞仍存在；numeric 匿名輸入、專用 prompt 與百分位參照保留。此檔沒有 patch 備份，本輪以現有程式、規格及兩批回歸核對，沒有臆造先前差異。 |
| M5 第十三、十四節 | 保留 `scaling` 來源與頂層 unit、三個鏡像欄位核對、`nominal_min/max`、N+1 格點驗證與複製、格點結構修訂、離散半格幾何、單側尾部錨點及 `numeric_cdf_v2`、舊 unsupported 快照重新排隊前的資格檢查。數值送出流程僅由注入傳輸回應測試，沒有接觸平台。 |
| D121 保護雜湊的 PLAN 假警報 | 依本次明確要求，只移除 `tests/fixtures/d121_protected_hashes.json` 的 `PLAN.md` 項目。其餘 11 項及其預期雜湊原樣保留，實際逐項核對相符。未修改 PLAN。 |

沒有改動 adapters、runner、netguard、三份固定 campaign 設定、AGENTS 或舊題型 prompt/schema。`config/question_types.json` 保持 `{"numeric": false, "discrete": false}`。既有 D121／M5 golden 均保留，用來驗證是非題／多選題的 prompt、payload、快照內容及 content_hash；未重新生成預期值來掩蓋差異。

## Gemini non-blocking 修正

`Scheduler._reevaluate_waits()` 對 `tier1_token_baseline_unmeasured` 僅接受：

- 與等待同線的第 1 層紀錄，非 format repair、非 model mismatch，且有正的有效 token 數。
- 有 `finished_at_utc`，完成時間嚴格晚於該筆等待的 `finished_at`。
- 完成時間位於目前 campaign 的 `[budget_window_start, budget_window_end)`，且不晚於注入時鐘的現在。

採用完成時間，因此等待前已開始、等待後才完成的量測可以解除等待；缺完成時間的紀錄不作為新證據。每筆等待獨立判斷，沿用 `scheduler_wait_reevaluated` 事件的冪等性。沒有改動 budget 計量、handoff 記帳或歷史終態。版本沿用待整合第一批的 `scheduler_v8`；M5 的專用版本未變。PLAN 正文及變更紀錄仍由整合者維護。

新增 17 個合成測試，涵蓋等待前／相等／之後、現在／未來、窗口前／起點／終點／之後、缺完成時間、他線、第 2 層、repair、mismatch、缺／零 token，以及同一量測僅解除較早等待與不重複追加事件。修正前實際重現 **8 failed、9 passed、21 deselected**，輸出 `.d121-checks/merge-red.log`。

## 實際測試

所有命令使用 `/Users/ucchengg/forecast-lab/.venv/bin/python`，cwd 為本副本。既有 `tests/run_offline.py --synthetic-host` 拒絕工作副本執行資料及真實模型 CLI，注入獨立測試根目錄、合成祖先／目錄清單及 local mode，暫存放在副本 `.d121-checks/tmp`。

專項命令：

```text
/Users/ucchengg/forecast-lab/.venv/bin/python tests/run_offline.py --synthetic-host -q -ra tests/test_d121_review.py tests/test_d121_fail_soft.py tests/test_numeric.py tests/test_numeric_pipeline.py tests/test_validate.py tests/test_forecast.py tests/test_scheduler.py tests/test_reconcile.py
```

完整命令：

```text
/Users/ucchengg/forecast-lab/.venv/bin/python tests/run_offline.py --synthetic-host -q -ra
```

專項實際結果：**447 passed，105.20 秒，exit code 0**，輸出 `.d121-checks/merge-targeted.log`。

完整隔離回歸實際結果：**2567 passed、1 failed、2 skipped，136.13 秒，exit code 1**，輸出 `.d121-checks/merge-full.log`。不能標為完整回歸全通過。剩餘失敗／跳過如下，均符合前批報告記錄的沙箱限制；原有 PLAN 雜湊失敗已消除：

| 測試 | 實際結果與原因 |
|---|---|
| `tests/test_claude_plugin_inventory.py::test_pin_wrapper_fails_closed_and_preserves_arguments` | failed；合成 shell wrapper 以系統 shasum 驗證合成執行檔時，`/System/Library/Perl/5.34/darwin-thread-multi-2level/CORE/libperl.dylib` 被沙箱阻擋，程序回傳 134，預期為 0。未改 wrapper、替換工具或繞過沙箱。 |
| `tests/test_acceptance.py:899` 的兩個案例 | skipped；既有 skip 條件回報 `loopback bind not permitted: [Errno 1] Operation not permitted`。未放寬網路權限或用模擬結果宣稱通過 netguard 驗收。 |

本輪新增測試完全使用合成資料；既有 M5 測試亦使用合成百分位數，並離線核對已提供的縮放欄位 fixture，沒有即時查詢或研究題目。

獨立靜態檢查：D121 保護雜湊 11/11 相符、兩個題型開關皆 false、無剩餘 `.rej`／`.orig`，原始碼／測試／設定／prompt／本次任務文件無 Git 衝突標記。既有 M5 golden 的保護雜湊檢查及兩套舊題型相容測試亦已包含於通過的專項回歸。

本輪交付變更只有 `src/scheduler.py`、`tests/test_d121_review.py`、`tests/fixtures/d121_protected_hashes.json`、本報告，以及刪除上述兩份 `.orig`。其餘兩批已套用內容保留。

## 沙箱與證據限制

- 本副本不是 Git checkout，`git status --short` 回覆 `fatal: not a git repository`。沒有讀取正式 repository 或外部 baseline，因此完整來源差異須由整合者在其授權環境核對；本輪證據為工作副本中的兩份 patch 備份、規格、既有保護雜湊與離線回歸。
- shell here-document 嘗試建立系統暫存檔時被沙箱拒絕，命令未執行；改用 `python -c` 在工作副本內保存差異，沒有讀取或繞過被拒絕的暫存目錄。
- 未執行會存取工作對話／執行資料的 TG status／publish。本輪明確禁止這類存取，且工作僅限開發驗證。
- 上線前的平台讀回契約與正式試驗驗收不屬本輪執行範圍，不能把本報告視為上線 PASS；兩個新題型開關繼續關閉。

本輪沒有新增需要 Louis 決定的功能政策，也不缺實作輸入。整合者仍須在允許系統 Perl 與 loopback 的隔離驗收環境補跑上述三項，並依其可讀來源核對完整合併差異；這些尚未取得的證據不能由本輪沙箱測試替代。
