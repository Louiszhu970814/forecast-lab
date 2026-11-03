# M5-PROBE 開發交接

日期：2026-11-03。依據：`M5-PROBE-SPEC.md`、PLAN D107／D122，以及本次工作副本任務指示。

本次僅實作與離線測試。尚未經 Gemini／Claude 審查，也沒有平台驗收結果；不得將本報告視為 M5 上線驗收通過。

## 修改檔案

- `src/v15_probe.py`：版本升至 `v15_probe_v5`。只有 single 支援 numeric／discrete；依指定優先序選題、保存縮放欄位、從固定 u 分位數重算及驗證 CDF。計畫驗證比對縮放雜湊與完整 payload 的 canonical JSON 雜湊，包含布林值與數值型別差異。
- `src/submit.py`：僅新增 keyword-only `probe_question_types` 參數與其 gate 例外。必須是 trial、32977，且題型明列於參數中；其餘 gate 沿用原規則。
- `src/m5_probe_session.py`：新增暫時 trial 工作階段，取得六把鎖、確認 live 與機器人身分、原子切換並記錄事件；finally 原子還原原始 bytes、核對 SHA-256、記錄回到 live 後才釋放鎖。還原、雜湊核對或還原稽核失敗會建立 STOP_SUBMIT 並非零結束。CLI 的 SIGTERM 轉為例外，使一般終止訊號走 finally。
- `tests/test_m5_probe.py`：新增合成 HTTP、計畫竄改、gate、數值尺度、讀回、工作階段及舊版相容測試。
- `docs/tasks/M5-PROBE-REPORT.md`：本報告。

沒有修改 AGENTS.md、PLAN.md、config/、adapters、runner、netguard、numeric.py 或 submit_transport.py。此副本不是 Git repository，故無 Git diff；變更由工作檔交付，沒有提交或推送。

## 送出與恢復設計

數值分支用 `_NumericSubmitter` 接上原 `Submitter._write`，保留正式的身分、場地、去重、縮放、期限與網路前 gate。它只以合成計畫替代研究輸入的凍結驗證，不呼叫 `_resume`，每個操作最多呼叫一次 `_write`。

請求先保留在 v15 帳冊，再追加正式 submissions 帳冊；讀回結果也同步寫入兩者。submissions 事件含 `synthetic` 與 `probe_version`，不建立可由一般 Submitter.run 接手重試的 `intent` 列。既有送出歷史仍供正式去重檢查使用。

每個探測最多 1 次預測與 1 次留言；工作階段依 numeric、discrete 執行，任何 rejected／unknown 或例外都停止後續探測。unknown 或中斷請求的再次執行只能對帳；即使之後預測讀回 found，也不會因此新送尚未開始的留言。已完成操作不重送。

`forecast_requests`／`comment_requests` 是 `request_started` 保留次數，為寫入請求的保守上限；網路前 gate 阻擋或記錄後中斷時，實際 POST 可能較少，不能將此數字當作伺服器接受次數。探測例外也保留已知計數；帳冊無法讀取則以 null 表示未知。未開始的題型會回報 rejected／not_started。

unknown 的額外診斷只記錄自身讀回列的鍵名與候選欄位的型別、長度，不記錄欄位值，也不藉此重送。數值 CDF 讀回仍使用既有 `NUMERIC_READBACK_FIELD` 與 5e-10 容差，未改傳輸契約。

session 直接在已持有鎖的範圍呼叫數值分支，避免重入非重入鎖。鎖競爭每次非阻塞嘗試、釋放當次已取得的鎖後再等候，總等待上限 25 分鐘；逾時不切模式、不建立模式或探測事件。鎖檔使用既有 `forecast.Lock` 管理。

## 測試

全部以指定 Python `/Users/ucchengg/forecast-lab/.venv/bin/python` 執行，cwd 保持工作副本；使用 `tests/run_offline.py --synthetic-host`。測試資料與 HTTP 都是合成 fixture，模式切換只作用於測試暫存目錄。

最終命令：

```text
/Users/ucchengg/forecast-lab/.venv/bin/python tests/run_offline.py --synthetic-host tests/test_m5_probe.py tests/test_v15_probe.py tests/test_v15_probe_reuse.py -q
/Users/ucchengg/forecast-lab/.venv/bin/python tests/run_offline.py --synthetic-host -q
```

最終相關測試 **399 passed（8.40 秒）**；最終完整回歸 **2688 passed、3 failed、2 skipped（157.78 秒）**。執行記錄保存於 `.d121-checks/m5-targeted-complete.log` 與 `.d121-checks/m5-complete-regression.log`。

完整回歸中的程序取消測試另以同一 runner 單獨重跑：

```text
/Users/ucchengg/forecast-lab/.venv/bin/python tests/run_offline.py --synthetic-host tests/test_dev_runner.py::test_worker_cancellation_kills_process_and_retains_workspace -q
```

結果 **1 passed（0.65 秒）**，記錄為 `.d121-checks/m5-cancellation-recheck.log`。單獨通過不抹除完整回歸的失敗。

涵蓋重點：

- numeric 線性與兩種對數尺度；discrete N=2／10／200；四種開閉端點組合。
- 全部 SCALING_FIELDS 的竄改、重算後 payload 比對、CDF 布林值竄改、送出前遠端縮放修訂。
- 題型開關 false 時的 trial／live／local、不同場地、有無 override，以及保留請求後的 STOP／模式／期限變更。
- 真正的 APITransport 與 Submitter 寫入路徑，透過合成 opener 驗證 found、回應遺失、unknown、400／429、私人留言、診斷鍵名與不重送。
- 兩題型同一 session 正好四次合成 POST；後續題型在失敗時不開始；沒有候選只記錄 no_candidate。
- 正常還原、探測／transport／稽核例外、KeyboardInterrupt、SIGTERM handler、還原寫入／雜湊／稽核失敗、六把鎖逐一忙碌及等待逾時。
- 原始 live 檔包含額外欄位與不同空白格式，還原後逐位元相同；還原稽核時仍持有全部鎖。
- binary／multiple_choice single 計畫 JSON 逐位元相同；v3／v4 single 計畫與 v4 全部 reuse 情境及本地注入拒絕紀錄仍相容。

### 完整回歸失敗明細

1. `tests/test_claude_plugin_inventory.py::test_pin_wrapper_fails_closed_and_preserves_arguments`：測試用 shell 包裝器呼叫系統雜湊工具時，dyld 載入 `/System/Library/Perl/5.34/darwin-thread-multi-2level/CORE/libperl.dylib` 被沙箱阻擋；returncode 134，預期 0。沒有繞過沙箱或修改此測試。
2. `tests/test_numeric.py::test_legacy_bitwise_golden_and_protected_files`：既有 `tests/fixtures/m5_legacy_golden.json` 的 PLAN.md 雜湊已過期。預期 `311743f108fdec1a55b795b0e8e87ca6be37a8fa72b8d80d455ad8c295c3b6af`，此副本實際為 `6e454b599b283447d8ab15b16e53d45df6352588bf29bfa6a8682d79568eb9eb`。本次未修改 PLAN.md，也未更新 golden 或放寬測試。該 fixture 其餘 protected 檔案的雜湊均吻合；失敗之前的舊題型 snapshot／prompt／schema／payload 比對均已通過。
3. `tests/test_dev_runner.py::test_worker_cancellation_kills_process_and_retains_workspace`：最終整套執行時，8 秒等待及 join 後狀態仍為 `cancelling`，預期 `cancelled`。前兩次完整回歸與最終單獨重跑均通過，屬此次觀察到的間歇性時序失敗；尚無證據將原因確定歸為沙箱。本次未修改 dev_runner 或該測試。

因此完整回歸尚不能宣稱全綠。整合者須在適當測試環境重跑第一項、依計畫版本處理第二項 golden 的一致性，並確認第三項整套執行的時序穩定性，再依 D122 判定。

## 審查交接

無缺少的開發輸入。後續為 Gemini 獨立審查與 Claude 完整審查，通過後才由 Claude 決定正式環境的一次驗收執行。

本次未連網、未呼叫真實模型 CLI、未讀取正式專案或工作副本的 data/、reports/、.env、登入憑證；未實際操作正式模式、預測或送出，未部署或操作 launchd。沒有執行 AGENTS 的 TG status／publish，因本次使用者明確限制離線、不查詢或送出。
