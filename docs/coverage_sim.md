# 覆蓋率模擬器（coverage_sim_v5）

這是離線容量估計工具，不產生機率、模型答案、正式預測或參賽核准。請先固定觀察期間、題集與耗時假設，再登記並執行。主賽與 MiniBench 的真實中繼資料、M2 的實測分布，由協調者另行收集；單元測試使用合成資料；D116 的重播使用事前固定的歷史中繼資料與成本樣本，詳見該報告。

v4 新增事前固定的 `cycle_trigger`：`fixed_grid`（預設）或 `after_completion`。後者每輪實際完成（包含 timeout 清理）後再等 600 秒，對應 D108／D112 的有限實機觀測；不代表已重建睡眠與可見性。新版本須另登記 ID，不覆寫 v3 歷史結果。

目前時間政策使用 `run_config_v3`：20 分鐘關題安全距離＋10 分鐘送出緩衝，設定檔路徑沿用 `run_config_v2.json`。原 5 分鐘實驗須使用封存設定的隔離副本；新版不可直接重跑舊 ID。

v5 新增觀測／壓力重播輸入：首次可見時間與可見區間、實際提前關閉、停機區間、期初工作及已有用量。D116 同時提供 `metadata_replay.py`；它不變更正在執行的 D114 觀測器。v4 的已登記結果保留，不能以 v5 冒充原引擎重跑。操作與限制見 [D116](tasks/M3-PARALLEL-D116.md)。

## 使用

```sh
.venv/bin/python src/coverage_sim.py register path/to/settings.json
.venv/bin/python src/coverage_sim.py run experiment-001
```

預設只讀寫 `data/coverage_sims.jsonl`、`data/coverage_sim_results.jsonl`，交易鎖另放 `data/.coverage_sim/ledger.lock`。可在子命令前指定 `--data-dir path/to/disposable-data`，以便完全使用暫存目錄。沒有網路、CLI 或憑證讀取；正式程式只重用純計算函式與讀取非機密的 `run_config_v2.json` 時間設定。

登記時把設定、解析後的時間表及引擎政策一併寫入 JSONL，分別保存設定、時間表與整份內容的 SHA-256。同 ID 重登記僅接受完全相同的內容。`run` 只用已凍結的內容，原設定／時間表可以移走，之後的檔案修改也不影響重播。內容 hash 或現行引擎時間政策不符就拒絕；不允許以修改已登記內容的方式改分母。不同 ID 可以預登記不同假設，不把新 ID 視為原實驗的結果。

每次執行追加一筆結果；隨機結果本身可重現，外層 `recorded_at` 是實際寫入時間。終端機只印摘要，完整逐題、逐嘗試、輪詢與週期紀錄在結果 JSONL。

## 設定格式

以下是完整合成設定。除下方註明的政策／忙碌區間外，所有欄位均需明列，沒有隱含的無限額度。時間可使用帶時區的 ISO 8601，內部及輸出轉為 UTC。

```json
{
  "version": "coverage_sim_v5",
  "sim_id": "experiment-001",
  "schedule_file": "schedule.json",
  "observation": {
    "start": "2026-11-01T00:00:00Z",
    "end": "2026-11-02T00:00:00Z"
  },
  "denominator": {
    "venues": ["main", "minibench"],
    "question_ids": "all"
  },
  "seed": 42,
  "repetitions": 10,
  "expected_new_questions": 0,
  "repair_reserve": 2,
  "urgent_slack_s": 120,
  "escalation_rate": 0.3,
  "startup_s": 0,
  "poll_s": 0,
  "submission": {"comment_s": 2, "precheck_s": 3, "submit_s": 2},
  "submission_policy": "on_finalized",
  "submit_busy_intervals": [],
  "players": {
    "claude": {
      "tier1": [180, 240, 400], "tier2": [400, 600, 900], "repair": [30, 60],
      "overhead_s": 60,
      "format_repair_rate": 0.05,
      "tier1_rerun_rate": 0.05, "tier2_rerun_rate": 0.05,
      "rerun_failure_rate": 0.1, "repair_failure_rate": 0.05,
      "attempt_cap": 100, "reset_times": [], "stops": []
    },
    "gemini": {
      "tier1": [180, 240, 400], "tier2": [400, 600, 900], "repair": [30, 60],
      "overhead_s": 120,
      "format_repair_rate": 0.05,
      "tier1_rerun_rate": 0.05, "tier2_rerun_rate": 0.05,
      "rerun_failure_rate": 0.1, "repair_failure_rate": 0.05,
      "attempt_cap": 100,
      "reset_times": ["2026-11-01T12:00:00Z"],
      "stops": [{"start": "2026-11-01T11:00:00Z", "reason": "quota_exhausted"}]
    },
    "gpt": {
      "tier1": [180, 240, 400], "tier2": [400, 600, 900], "repair": [30, 60],
      "overhead_s": 60,
      "format_repair_rate": 0.05,
      "tier1_rerun_rate": 0.05, "tier2_rerun_rate": 0.05,
      "rerun_failure_rate": 0.1, "repair_failure_rate": 0.05,
      "attempt_cap": 100, "reset_times": [], "stops": []
    }
  }
}
```

- 分母是 `start <= open_time < end`、符合 `venues` 與 `question_ids` 的題目交集。`question_ids` 可為 `"all"` 或不重複的正整數清單；清單中的 ID 必須存在於時間表。v5 可用 `workload_only: true` 加入期初尚未關閉的題目作容量競爭，另列 `carry_in_questions`，不混入新開題分母；不能將本期開題標成 workload_only 以縮分母。
- `tier1`、`tier2`、`repair` 是正數秒數的經驗樣本，均勻有放回抽樣。用重複值表達權重；本版不提供參數式分布。超過正式單次上限的樣本會產生 timeout，不能當成功結果。`overhead_s` 是每次呼叫的驗證／稽核／額度查詢總耗時，與樣本分開計入。
- 初次呼叫以 `tier1_rerun_rate`／`tier2_rerun_rate` 抽樣「即使修復格式仍需重跑的失敗」。每個 base call 另外以 `format_repair_rate` 抽樣格式修復需求；格式修復不挽救前述非格式失敗。`repair_failure_rate` 控制修復本身失敗，`rerun_failure_rate` 控制唯一一次 rerun 的失敗。timeout 也進入唯一一次重跑路徑。每次 base 最多一次 repair，每快照、線、層最多 initial → repair → rerun → repair；跨週期不重置。
- `escalation_rate` 是每快照在目前可執行線的第 1 層已結束且至少一個答案有效時，成為第 2 層候選的機率。不重播機率分歧與市場來源；第 2 層失敗可回退第 1 層。
- `attempt_cap` 計入兩層、repair、rerun，每個 `reset_times` 時點重置為零；0 表示沒有容量。第 2 層另外保留已發現但未完成的第 1 層、`expected_new_questions` 與 `repair_reserve`。額度次數耗盡記 `budget_skipped`，這是該快照／線／層的終局，不因下一個 reset 重新選答。reset 後的新題可以使用新容量。
- v5 每線可明列 `initial_attempts_used`（非負整數，預設 0），表示模擬起點的既有用量；新 sim ID 不能替代真正 reset。實測轉換器強制要求有來源雜湊的期初用量與停線資料，沒有讀數不當作 0。這仍不等於可觀測三家實際訂閱剩餘額度。
- `unavailable_intervals` 是事前固定、排序且不重疊的 `[start,end)` 停機區間；跨過停機起點時，正在執行的工作保守記失敗並清理，停機結束後才恢復後續週期。這是外部故障壓力模型，不假裝重建 OS 或網路故障細節。
- `stops` 使用半開區間 `[start, end)`。`reason` 為 `quota_exhausted`、`disabled`、`preflight_blocked`；省略 `end` 則到下一個 `reset_times`。reset 必須排序且不重複。停線不截斷正在執行的嘗試，但後續呼叫須重查；週期起點已停線的線在該週期保持暫停。`disabled` 不阻擋其餘線完成整合，其他停線可能等到決策截止才以缺線結果整合。
- `startup_s` 表達每個週期的初始化／preflight 耗時；`poll_s` 表達每次真正刷新耗時。兩者也受 supervisor 限制。沒有可執行工作時，若最早未封存決策截止點在未來 780 秒內，就以最多 60 秒一段等待到截止點（共用 `scheduler.idle_delay`）；較遠則結束，模擬中的下一次啟動仍在固定格點。780 秒是沿用的閒置等待門檻，原依 600 秒間隔、120 秒送出尾段與 60 秒啟動餘裕設定；D111 將送出上限延長後沒有改這項門檻，不能把它當作新的最壞等待保證。
- `submission` 三個欄位是每題的固定耗時。每次送出呼叫各有正式 300 秒上限（D111，`cycle_v7`），未開始的題目留待下一次 hook 或週期結尾／下一週期。正在送出的題目若逾時，保守記為未知／漏答 `timeout`，不假設伺服器已接受。
- `submission_policy` 預設 `on_finalized`：每批新封存且有合格答案的題目即發布與送出，週期結尾仍再呼叫一次。可選 `cycle_end` 重播舊版「等所有工作結束才送出」及沒有計時喚醒的行為，用不同實驗 ID 比較。v1／v2 的凍結紀錄不直接以 v3 重播，應依相同合成輸入重新登記。
- `submit_busy_intervals` 預設空清單；每項為 UTC `start`／`end` 的半開區間，表示外部送出程序持有 `submit.lock`。呼叫時若忙碌就立即記 `busy`，不消耗送出時間、不改變逐題送出狀態；下次呼叫重試。不模擬外部程序自身送出的題目。每週期的 `submit_calls` 保存階段、觸發題號、起訖時間、結果及耗時。

時間表 `schedule.json` 是 JSON array，例如：

```json
[
  {
    "question_id": 1,
    "venue": "main",
    "question_type": "binary",
    "open_time": "2026-11-01T00:00:00Z",
    "scheduled_close_time": "2026-11-01T03:00:00Z",
    "revisions": [
      {"time": "2026-11-01T00:15:00Z", "structural": false}
    ]
  },
  {
    "question_id": 2,
    "venue": "minibench",
    "question_type": "numeric",
    "open_time": "2026-11-01T00:00:00Z",
    "scheduled_close_time": "2026-11-01T03:00:00Z"
  }
]
```

ID 必須唯一；修訂按時間排序，必須位於開題至關閉之間。`structural` 是題型、選項、截止或對應是否改變的預先分類；本版不接受修訂後的新截止時間／題型，仍用原排程估計剩餘窗口，分析這類改變應另登記情境。首次發現前的修訂已包含在最初快照；送出後只記修訂，不重跑。

v5 題目可明列 `visible_at`（時間，或 null 代表未觀察到可見）、`visibility_intervals`（排序不重疊的 `[start,end)`）、`actual_close_time`。這些條件都要滿足才可取得題目／開始新工作／計入成功送出，不改原 `open_time` 或分母；送出期間失去可見性也不算成功。實際提前關閉只在其發生後阻止作答／計入成功，不讓排程器事先知道而提早封存；決策期限仍按當時排定時間。`unreplayable: true` 使已支援題仍留在支援分母、按漏答計，不以改成不支援來美化覆蓋率。可見區間來自取樣時，只能表達最後一次完整觀測的狀態，不能保證兩次觀測間沒有短暫改變。

## 重用與模擬界線

- 直接重用 `forecast.decision_deadline`、`validate.submit_deadline`、`budget.duration_estimate`、`budget.quantile`、`scheduler.Job/order_jobs` 與 `cycle.timing_limits`。登記時凍結 timeout／截止政策；引擎版本或時間政策不符就拒絕執行。
- 自行重建 scheduler 的 state 與三線虛擬工作槽，不呼叫會讀正式帳冊的 `Scheduler.run()`、`Budget.reserve()` 或 `Queue`。遵循全域可執行第 1 層閘門（暫停、停線不阻擋健康線升級；仍在執行的第 1 層繼續優先）、主賽／MiniBench 優先序與緊急例外，測試直接與 `Scheduler.jobs()` 比較。
- p90 先以登記的每線每層樣本初始化，再加入已完成的模擬嘗試，沿用最近 50 筆與正式固定估時餘裕（60 秒；Gemini 再加 60 秒）。實際呼叫另以 `overhead_s` 保守預留完整開銷，未剩至少 60 秒就不啟動；不重建兩次 quota 查詢的細部分段。模擬嘗試的歷史耗時包含 overhead，因此後續估時計入正式固定餘裕可能偏保守。
- `fixed_grid` 模式從觀察起點每 600 秒啟動；`after_completion` 模式則從每輪完成後等 600 秒，且沒有虛構的固定格點 overlap skip。准入 510 秒限制新工作與後續 repair/rerun，不截斷已開始呼叫。每完成工作強制刷新；新政策也在最早未封存題目的決策截止或最多 60 秒後醒來，沿用 30 秒快取重查，因此可能比舊版提早發現新題。同一工作內下一次呼叫也使用快取重查。沒有背景輪詢執行緒，也不在新題開放時自動知悉。啟動與當前週期重疊時記 `overlap_skipped`。同時刻先處理既有完成事件，再判斷週期鎖是否釋放。固定格點是模擬假設：D108／D112 本機自然觸發的有限觀測為前輪結束約 600 秒後再啟動，並非嚴格每 600 秒開始一輪；睡眠、真實觸發相位與歷史實際可見性也未由此模擬重建。報告應保留這個限制，不把條件式通過等同實戰覆蓋率。
- 閒置等待也受總時間上限約束：`cycle.timing_limits` 在 510 秒准入、最長呼叫鏈與兩次送出餘裕之後，選下一個 600 秒格點；排程器在該格點前 30 秒交接，略過週期尾端的網路刷新與送出，避免尾段跨過下一次啟動。D111 的預設交接在 4770 秒，worker 上限 5100 秒，含清理的 supervisor 上限 5110 秒。這些值會隨 timeout 設定重新計算；舊情境必須按新政策事前另登記，不能覆寫舊結果。
- 每週期最多 180 個 scheduler 工作，沿用正式預設；同一工作內的 repair/rerun 不另外計入此上限，但各自消耗 attempt cap。到達上限後剩餘工作留待下一週期。
- 以正式單次 timeout、題目決策截止與 supervisor worker 上限截斷；額外計入 cleanup reserve。supervisor 中斷的工作保守視為終局失敗，不模擬程序樹、孤兒程序、帳冊修復與人工恢復。
- 以至少一條有效線表示可整合，不計算正式機率與留言文字。新政策對齊 `scheduler_v6`／`cycle_v4`：主執行緒在 finalize 後同步發布與依題號送出，期間不排新工作，既有模型嘗試及其修復／重跑仍依自己的時鐘前進；主迴圈恢復後才收取完成工作並刷新。週期結尾仍刷新、處理文字修訂 fallback 及補送。每次送出耗時加總後須嚴格早於該題送出截止；沒有合格結果不算覆蓋。同步 hook、輪詢本身耗時仍可能推遲別題的封存，不代表任意負載都保證準時。60 秒封存界線的排程驗證假設程序可被正常排程、交接後的啟動／輪詢可在剩餘餘裕內完成；長時間網路阻塞、睡眠與程序崩潰仍會反映為遲到或超時。
- 文字修訂 fallback 沿用正式 Queue 的排除方式：不接新模型工作，週期結尾才 force finalize；不提早將仍在執行、尚無合格答案的 fallback 永久封存。此為既有行為，與一般未封存題目的計時喚醒分開。
- 修訂在實際刷新時才被發現；剩餘時間大於三線最大 p90 就全部重跑第 1 層，舊工作仍占用線到完成，但不再採用。來不及則結構修訂阻擋、文字修訂保留舊快照。送出期間只檢查該題的修訂，不能藉此把其他新題偷放進排程。
- 沒有 Argon 百分比、token 熔斷、模型身分與稽核來源模擬，改由次數上限及明列停線區間表示。沒有遠端傳輸、重試／對帳、留言失敗與平台改題 payload；未知送出不算成功。所有抽樣彼此獨立，不宣稱捕捉跨線耗時／失敗相關性。

## M3-W8 差異與驗證

- 修正舊模擬器「無 active 工作就退出」及「暫停線阻擋所有第 2 層」兩項契約；正式程式固定 M2 模式仍保留原排程行為，模擬器代表持續模式。
- 新增單題缺線、其他兩線 60 秒即完成、決策截止為 300／575／610／1190／4175 秒的案例；不使用另一題長工作維持存活。另測缺線時兩條健康線完成第 2 層。
- `cycle_end` 保留舊的送出／不等待政策供比較，但使用目前的可執行第 1 層優先規則及時間上限；不代表逐行重播舊版程式。
- 額度等待仍以合成 `stops`／重置時間表示，不重建 Argon 週桶與 5 小時桶；實際 `quota_weekly_wait` 的分類與恢復由正式程式的單元測試驗證。

## 指標

逐題記準時送出、是否採用第 2 層、是否曾啟動第 2 層、漏答主因、所有觀測到的原因、首次發現與啟動時間、修訂處理。多個原因同時存在時，明確的修訂／送出錯誤優先；其次依 quota、停用、preflight、budget、timeout、輸出失敗排序。這是診斷標記，不是因果歸因。

`coverage_all` 的分母含不支援題型，`coverage_supported` 只含 `binary`／`multiple_choice`。90% 判定只套後者；空分母的比率與判定為 `null`。觀察期間只決定開題分母，模擬持續到最後一題關閉以免把期末開題右設限。

每線使用率是**觀察期間內**占用秒數／觀察期間秒數，包含設定的 overhead；不把期末尾段混入此分母。`queue_wait_s`／`queue_wait_by_player_s` 是可見待辦到取得線槽的等待（跨週期的待修復／重跑也計入），不含立即接續的呼叫。`open_to_tier1_s` 是每題首次第 1 層啟動減開題時間；另列 `not_started`，不把漏答延遲偽裝成零。

`peak_simultaneous_opens` 是同一時刻開題數，`peak_open_questions` 是同時尚未關閉的題目數。`cycles_over_600_ratio` 分母為真正啟動的週期，不含 `overlap_skipped`；空週期也計入。時間分布採最近秩分位數（與正式 budget 相同），提供 count／mean／p10／p50／p90／p95／max。

週期統計包含觀察期末至最後關閉的尾段；每線使用率則只計觀察期間。完整 `cycles` 可依登記的時間窗口另行切片，不應混淆兩種分母。

重複模擬使用 `seed + repetition_index`。summary 提供各次覆蓋率、使用率、峰值、重疊次數與超時週期比例的均值／分位數，以及各次等待／啟動延遲的 mean、p50、p90、p95、max 的跨次分布；完整 trials 保留每次的分布。這些是情境模擬結果，不是信賴區間或 M3 已通過的證明。

`metadata_replay.py register` 預設 `--deadline-policy d114_original`，只接受原 20／5／0.9 期限設定。使用目前新版的獨立對照時必須明選 `--deadline-policy d116_margin10`、另取 ID；保留原完整觀測與原結果，兩組都應在看觀測重播結果前登記。converter 會要求原觀測完整、明列期初容量，且不會自動啟用 M3／live。
