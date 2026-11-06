> 官方 CLI 審查原文；來源封包 `data/operations/d133-closed-practice-20261104/review-packet-r1.txt`。程式雜湊以機器產生的 `reviewed-files-r1.json` 為準，非模型轉寫字串。

# D133 FutureEval 已關閉題目練習：完整程式審查（具名審查者：Claude）

## 結論：REQUEST_CHANGES

不核准套用，也不核准以這版程式執行任何模型呼叫。

這份審查不是 Gemini 的獨立審查，也不是 Louis 的正式核准，不能拿來當成「雙審通過」或「測試 PASS」。

---

## 0. 審查範圍與證據等級

- **審查依據**：只依提供的資料包，未執行任何測試、指令或 API 呼叫，也沒有讀取 `data/` 的實際內容。
- **檔案雜湊**：六個檔案的 SHA256 只能記錄，無法比對。我審查的是資料包裡的文字內容。
- **測試結果**：「前 40 個練習測試通過；相關測試 490 個通過、12 個失敗」是提交者的說法，我沒有驗證。full baseline comparison 仍「進行中」。「12 個失敗是既有正式設定造成」這個歸因目前沒有證據。
- **行號**：資料包沒有附行號，以下用「檔案／函式＋程式片段」定位。
- **三種狀態要分清楚**：
  - 程式已完成：只是隔離副本裡的候選版本，尚未套用。
  - 實際練習已完成：否，0 次呼叫，也還沒執行 prepare。
  - 準確度：沒有任何結果。

---

## 1. 讀程式碼確認的正面性質（僅為程式碼閱讀，未執行）

1. **沒有 POST 路徑**：`practice.py` 只用 `MetaculusClient` 的 GET（`get`、`list_posts`、`get_post`），沒有匯入 submit。token 只在 `client_for` 主程式使用，不會傳給模型。
2. **練習時鐘範圍很窄**：`practice_contract.execution_cutoff` 只在以下條件全部成立時才放行，否則丟出錯誤：
   - tier 1、`initial`、`prev is None`；
   - 同時有 `before_attempt`、`recheck_inputs`、`admission_outcome`；
   - 批次已登記，且 manifest 雜湊吻合；
   - 題目快照與登記內容完全相同；
   - 寫入路徑必須是 `data/practice/<batch>/<qid>/<player>/`；
   - `cutoff ≤ start + 12 分鐘`，且不晚於預算窗口結束。

   正式路徑（`closed_practice is None`）回傳 None，`decide_by` 不變。`F.eligible` 照樣拒絕非 open 的題目，`deadline_utc` 保留原本的截止時間（測試 `test_actual_engine_uses_practice_clock_without_changing_close` 也這樣斷言）。
3. **不會自動修復或重跑**：`run_line` 直接呼叫 `F.run_attempt(kind="initial")`，不經過 `forecast_question`。
4. **正式帳冊不會混入練習資料**：練習的 forecasts、audits、raw、aggregates 都寫在批次目錄內。aggregate 另標 `purpose="closed_practice"`、`submission_eligible=False`。
5. **共用額度正確計入**：
   - `Budget.reserve` 經由 `external_usage.usage` 計入練習准入。unknown（未知結果）與已啟動的呼叫都算消耗，只有確定沒啟動的才不計。
   - `Budget.recover` 把已准入的嘗試當成已啟動，不會釋放。
   - 任何練習嘗試未結案前，該線會得到 `unresolved_or_running_attempt`。
6. **防止重跑與擴張**：
   - `practice_batches.jsonl` 只允許一個批次，`prepare` 拒絕第二次；
   - `runs.jsonl` 讓同一題拒絕第二次 run；
   - 同一（模型線，題目）不能重複准入；
   - 全域上限 9 次准入。
7. **共享帳冊只放 metadata**：`practice_usage` 和 `practice_guards` 用完整欄位集合比對，夾帶答案欄位會被拒絕。
8. **全部列為診斷組**：所有題目一律 `clean_evaluation_eligible=False`，`event_time` 永遠是 None，不會從關閉或預定結算時間推論事件時間。

---

## 2. 必修問題（Blockers）

### B1. 驗證證據不完整（流程阻擋）
- full baseline comparison 尚未完成；12 個 scheduler 失敗歸因於「既有 live config」，但沒有對照證據。**修法**：在未修改的 D132 工作樹跑同一套測試，證明失敗的是同樣 12 個、原因相同，並保存可重現的輸出。
- 沒有 Gemini 獨立審查。
- 動到共用執行期程式（`forecast.py`、`budget.py`）時，依交接文件第 4 步，正式套用必須在 cycle／scheduler／submit／三線六鎖下進行，並保存保護雜湊與套用前後的 preflight。目前完全沒有這些證據。

### B2. 共用執行期改動等同改變正式行為，尚未登記或證明無回歸
- `budget.py`：`VERSION = "budget_v9"`，改從 `flab.external_usage.usage` 讀資料（含練習）。
- `forecast.py`：`PIPELINE_VERSION = "forecast_pipeline_v10"`。另外，`line_stopped` 現在讀 `external_usage.guards`（含 `practice_guards.jsonl`），`run_attempt` 每次都 import 並呼叫 `execution_cutoff`。
- 已知正式版本是 `budget_v8`／`forecast_pipeline_v9`。D118 MiniBench 正在進行（窗口到 `2026-11-10T04:00:00Z`），這是窗口內的正式版本升級，必須寫入 PLAN §15／§16，並檢查所有讀取 `PIPELINE_VERSION` 或 budget 版本的地方（scheduler、報告、ITT、quota estimate 等）。這些程式不在資料包內，我無法確認。
- **連帶影響**：`practice_usage.jsonl` 或 `practice_guards.jsonl` 任何一行損毀或格式不符（例如 `version` 不是 `closed_practice_v1`），都會讓**正式**的 `Budget.reserve` 和 `F.line_stopped` 直接丟出錯誤，整條正式產線停擺。fail-closed 方向正確，但要明確寫成規格並取得核准。
- **修法**：
  - 補正式路徑的回歸測試：沒有練習帳冊、練習帳冊為空、練習帳冊損毀三種情況下，正式 `reserve`／`line_stopped` 的結果與 D132 相同或依規格 fail-closed；
  - 補一個測試：未帶 `closed_practice` 的 closed 題目仍會在 `require_time`／`eligible` 被拒；
  - 記錄版本升級原因與影響範圍。

### B3. 未知練習嘗試會無限期阻擋正式線，且沒有自動恢復或告警（違反 D133 §6「正式工作優先」）
- 位置：`budget.Budget.reserve` 的 `if any(r["player"] == player and aid not in advisory_finished ...)`，加上 `practice.finish_accounting` 遇到「有 start 沒結果」就 return。
- 情境：練習程序在 CLI 執行中被殺掉（睡眠、重開機、OOM）。鎖會隨程序消失而釋放，但正式 scheduler 對該線的每次 reserve 都會被拒。只有人手動跑 `practice.py recover`，而且要等 `orphan.expired` 加上 `no_process` 成立後才會解除。`line_stopped` 不會顯示這個原因，原因只出現在 `budget_denied`，MiniBench 窗口內可能因此靜默漏題。
- 「unknown 要保留計費並阻止重跑」是對的，但 D133 契約要求的是阻止**練習**重跑，不是讓正式線無聲停擺。
- **修法（擇一並加測試）**：
  - (a) 正式 scheduler 或 cycle 在自己的鎖內，呼叫只處理 metadata、不呼叫模型的練習恢復（等同 `recover` 的 `recover_interrupted`＋`finish_accounting`）；
  - (b) 至少讓正式 preflight 或 status 把「外部未知嘗試阻擋」列為明確問題，由進度發布帶出 metadata，並寫好操作手冊。
- 測試：程序中斷後，正式 reserve 的拒絕原因要看得到；恢復後計費仍保留（`weekly_attempts_used` 不減）。

### B4. 持鎖時間上限不完整，不能宣稱「最多 12 分鐘＋preflight」
- 位置：`practice.run`。`lock_all`（cycle、scheduler、submit、三線 forecast 共六把鎖）在函式一開頭就取得。之後依序在鎖內執行：`guard()` 的網路 GET、三線 `F.preflight`（CLI 版本、baseline snapshot、Gemini `/usage` 最多 30 秒、GPT catalog、`recover_acceptance`）、`shared_ready`，**之後**才設 `started`，並從這裡起算 12 分鐘。
- 因此 preflight 本身在鎖內**沒有任何上限**；CLI 一旦啟動就沒有取消機制；`fresh_index` 的 20 分鐘預看是固定值，不是依實際剩餘持鎖時間算出來的。
- **修法**：
  1. 記錄 `lock_acquired_at`，設總持鎖上限，例如 `cutoff = min(lock_acquired_at + N, started + 12m, expires_at)`；准入前若已超過上限就直接放棄，不准入。
  2. `fresh_index` 的預看改為動態：`cutoff - now + 後處理餘裕`（Gemini 事後 quota 查詢 30 秒、audit、aggregate、accounting），並保證不小於固定下限。
  3. 在 `runs.jsonl` 記錄 `lock_acquired_at` 和 `lock_released_at`，留下實際證據。
  4. 補證據：正式 scheduler 遇到 `PreflightError("another forecast.py is running")` 時會稍後重試，不會被標成失敗、不發通知、不漏題（相關程式不在資料包內）。
  5. 「CLI 啟動後不能取消」的殘餘風險，要由 Louis 明確接受並記錄。審查者不能代為接受。

### B5. 「從未進入正式生命週期」的證據不夠（任何非空選題前必修）
- 位置：`practice.lifecycle_evidence` 只看 `REQUIRED_LEDGERS` 和三個 reconciliation／start 帳冊。
- **問題一**：依官方 API 說明，一般帳號只能讀到「自己預測過的」關閉題題文。所以只要某題的 description 或 criteria 讀得到，就是強烈訊號：這個 bot 帳號曾經預測過它（可能來自本地帳冊以外的歷史或手動操作）。本地沒有紀錄不足以證明沒進過正式生命週期。
- **問題二**：沒有檢查 `submission_readbacks.jsonl`、`budget_events.jsonl`、`advisory_usage.jsonl`、`practice_usage.jsonl` 裡的 question_id，也沒有用 post_id 比對。
- **修法**：
  - 檢查原始 post 的帳號預測欄位（例如 `my_forecasts`，只讀存在與否，不讀數值、不放進快照）；有就排除並轉給 `post_close_audit`；
  - 擴大帳冊比對範圍，同時比對 question_id 和 post_id；
  - 補對應測試。
- 目前 108 題全部被遮罩，這條不會觸發；但修好之前不能執行有非空選題的批次。

### B6. 預約後准入失敗時，預約會被遺留
- 位置：`practice.run_line.before` 中，`b.reserve(...)` 允許後先 `own.append(aid)`，再呼叫 `register_admission`。若 `register_admission` 丟出錯誤（契約檢查或 `append_record` 的 OSError），`finish_accounting` 會在 `if aid not in a: return` 直接返回，**不會**呼叫 `b.outcome(aid, False)`。結果 `budget_reserved` 沒有 outcome，該線在正式 reserve 一直得到 `unresolved_or_running_attempt`，要等某個不同 run_id 的 `Budget.recover()` 才會釋放。
- **修法**：在 `finish_accounting` 裡，如果有 reservation、沒有 practice 准入、也沒有 start，就呼叫 `b.outcome(aid, False)`；如果沒有准入卻有 start，就丟出錯誤並停線。補測試，用 monkeypatch 讓 `register_admission` 丟錯。

---

## 3. 需要 Louis 決策的項目（審查者不能代為接受）

**D1. 一次練習暴露就停正式線，是否合適。**
- `propagate_safety` 遇到 `leak_confirmed`、`recovery_no_rerun`、`tool_violations` 或 `safety_stop_reason` 都會寫入 `line_stop`（source=practice），正式線要等 Louis 手動 resume 才恢復。
- 練習題已關閉、結果可能已公開，模型碰到 metaculus.com 的機率比正式題高。正式規則是 7 天內 2 次 leak 才停線，練習是 1 次就停，而且練習的 leak 不計入正式計數。
- 工具越權、credits 非零必須停正式線；`leak_confirmed`、`recovery_no_rerun` 要不要在 MiniBench 窗口內停正式線，需要明確決定。
- 程式上也要和 advisory 一致：改用 `own_audit_flags` 並涵蓋 `error_type == "tool_scope_violation"`。

**D2. 空批次會用掉唯一的 D133 批次。** 預期首批 0 題可選。照現行程式，`prepare` 一跑就永久用掉 singleton；之後就算取得 Bot Benchmarking Access Tier，也要改程式、升版才能再建批次。另外，`practice_usage` 寫死 `version == "closed_practice_v1"`，未來 v2 會讓正式解析失敗。建議改成：「0 題選中、0 次准入」只寫盤點紀錄，不佔 singleton；或事先登記這個規則。

**D3. holdout 規則。** 最晚截止的同一群題目全部保留為 holdout。FutureEval 常整批同時截止，可能出現「可用題全落在同一截止群 → 0 題選中」，或「只有 1 題可用就被保留」。需要事先登記最低選題數與分群方式（時間或事件群）。

---

## 4. 非阻擋改進建議

1. **manifest 缺版本資訊**：沒有 `F.PIPELINE_VERSION`、`PROMPT_VERSION`、`budget.VERSION`、`practice*.py` 原始碼 SHA、各線 model／effort、事件或題群欄位，不符合契約第 3 項「事前記錄版本」。建議補上。
2. **排序用字串比較時間**：`usable.sort(key=...scheduled_close_time)` 若同時出現 `Z` 和 `+00:00` 兩種格式會排錯，應改用 `parse_iso`。
3. **排除理由標錯**：`closed_snapshot` 遇到 group 或 conditional post 時，`snapshot_from_post` 回傳 None，被標成 `not current-season FutureEval`，母體排除理由會失真。
4. **例外型別未確認**：`V.question_schema` 遇到不支援的 numeric scaling 時，`N.UnsupportedScaling` 是否為 `ValueError` 的子類別？如果不是，`prepare` 會整批中止。需確認並補測試。
5. **同步排除範圍太窄**：只查 33121 和 MiniBench。建議納入 `config/tournaments.json` 白名單中目前開放或即將開放的場地（例如 Cup 33108、bot testing area），符合 D53。
6. **狀態轉換會讓題目永久無法使用**：`guard` 用 `fresh != entry["snapshot"]` 比對整個快照（含 `status`），closed 轉成 resolved 就會被判定「已變更」。建議只比對 `content_hash`、ID 和截止時間，狀態變化另寫入 observations。
7. **准入後的網路失敗會用掉名額**：`client_for` 設 `max_retries=0`，而 `guard` 在准入後的 recheck 仍會做網路 GET，暫時性錯誤會讓（模型線，題目）永久用掉一個名額，雖然沒有呼叫模型。這方向保守，但應寫進限制說明，並評估准入後只做本地檢查加 20 秒快取。另外，預設 Backoff 會在鎖內 pacing 並寫入 `data/api_cooldowns.jsonl`，需要確認可以接受。
8. **`score()` 的問題**：
   - 需要 `fresh_index` 沒有正式工作，MiniBench 有開放題時就無法計分；
   - 在六把鎖內做網路呼叫；
   - 任一題 `closed_snapshot` 失敗（遮罩、快照變動）就丟出錯誤，整批中止。應改為逐題記錄錯誤並繼續；
   - 每次呼叫都重複追加結果。
9. **`report()` 不符合契約要求**：缺耗時、額度、各線差異、audit 和暴露統計、各題型有效率、缺答原因；`local_scores` 永遠是 None。
10. **共用 quota 帳冊**：練習的 Gemini quota 快照寫進共用 `quota.jsonl`，會讓 window estimate 的單次成本偏高（方向保守）。需記錄並確認正式估計可以接受。
11. **練習紀錄可能被誤讀**：練習 forecasts 帶有 `tournament_id=33121`，應確認正式的 resolve、score、ITT 讀取程式不會遞迴掃描到 `data/practice/`，並補測試。
12. **題文可能已洩漏結果**：題文是關閉後才抓的，description 或 fine_print 可能已被編輯、加入結果；prompt 裡的「今天日期」也晚於截止日。結論已全部是診斷組，但報告要明確列出這兩點。

---

## 5. 缺少的有意義測試

1. `run()` 的端到端測試：三條線用 fake adapter、preflight 用 monkeypatch，驗證三執行緒並行時：
   - 9 次上限、重複准入、`runs.jsonl` 拒絕重跑都有效；
   - aggregate 不寫進正式帳冊；
   - 第二次 run 同一題被拒。
2. 任一正式鎖被佔用時，`prepare`、`run`、`recover`、`score` 都不寫任何帳冊（advisory 已有對應測試，practice 沒有）。
3. 執行中重新檢查：准入前或准入後、CLI 啟動前，題目重新開放，或 MiniBench 出現 open／20 分鐘內開放的題目，結果要是 QuestionDone，不啟動 CLI，計費與釋放結果正確。
4. B6：`register_admission` 失敗後，預約被釋放，正式線不被阻擋。
5. B3：程序中斷後正式 reserve 的拒絕原因看得到；恢復後計費仍保留。
6. `test_corrupt_or_answer_containing_metadata_fails_closed` 目前只測到「最後一行沒換行」。應另外寫一行完整、有換行、但多了 `probability` 欄位的准入紀錄，驗證完整欄位比對會拒絕它。
7. `test_guard_propagates_quota_and_confirmed_exposure` 同時放了 quota 和 leak，`line_stopped` 為真無法區分是哪個造成的。應拆開，分別驗證 `line_status.jsonl` 的 `practice_safety_event`，以及 guard 的冷卻到期行為。
8. `test_formal_work_yields_before_practice` 只斷言 `ValueError`。應用 `match` 區分 `formal_work_priority` 和 `uncertain coverage`。
9. `score()`：已結算、未結算、取消或模糊不計分、快照變動、遮罩各一種情況；確認 log 和 Brier 分開，且不出現 official 分數欄位。
10. `report()`：輸出中不能出現機率、題名、description 或來源 URL。
11. 正式路徑回歸（B2）：沒有、空、損毀的 practice 帳冊下，正式 `reserve`／`line_stopped` 的結果。
12. 持鎖時間（B4）：preflight 很慢時，不准入就放棄；`lock_released_at - lock_acquired_at` 不超過上限。
13. `prepare`：所有可用題同一截止群；group／conditional post；不支援的 numeric scaling；列表遮罩但 detail 可讀；manifest 寫入和 registry 寫入之間中斷。

---

## 6. 對照 D133 第 6 項「正式賽優先、共用真實額度」

| 要求 | 評估 |
|---|---|
| 不新增或重設窗口、cap | 符合：沿用 campaign 與 `Budget`，`protected = 2×expected_new + repair_reserve` |
| 每次准入檢查額度、停線、冷卻、未知嘗試 | 大致符合：`shared_ready`、`line_ready`、`Budget.reserve` |
| unknown 保留計費並阻止重跑 | 符合（計費），但造成 B3 的正式線阻擋問題 |
| 正式工作出現時讓路 | **只有 CLI 啟動前會讓路**；啟動後不能取消，preflight 在鎖內無上限（B4） |
| 不長時間佔鎖導致正式題逾時 | 未證明（B4，scheduler 重試行為沒有證據） |
| 共用帳冊、安全事件、異常恢復先實作並測試 | 部分完成；有 B3、B6 和多項測試缺口 |
| 不直接套用 D132 固定兩題／六次的入口 | 符合：有獨立的 `practice.py`，但仍依賴 `advise.lock_all` |

---

## 7. 下一步建議

1. 先修 B2 到 B6，補第 5 節的測試，完成 baseline 對照，再交給 Gemini 獨立審查。
2. Louis 決定 D1 到 D3，並寫入 PLAN。
3. 這些完成前，**不要執行 `prepare`**，因為它會永久用掉 D133 的唯一批次（D2）。如果只需要「無資料」的結論，可以另做一個不登記批次的唯讀盤點報告。
4. 正式套用時依交接第 4 步：六鎖、保護雜湊、套用前後 preflight，並記錄 PLAN §15／§16。過程中不得啟用主賽。

**狀態總結**：程式是未套用的候選版本，本審查為 REQUEST_CHANGES；實際練習 0 次呼叫，尚未開始；沒有任何準確度結果。