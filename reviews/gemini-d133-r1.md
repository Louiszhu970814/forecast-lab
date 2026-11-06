> 官方 CLI 審查原文；來源封包 `data/operations/d133-closed-practice-20261104/review-packet-r1.txt`。程式雜湊以機器產生的 `reviewed-files-r1.json` 為準，非模型轉寫字串。

# D133 完整程式碼與設計審查報告（Closed FutureEval Practice）

- **審查結論**：**`REQUEST_CHANGES`**
- **審查範圍**：`src/practice.py`、`src/flab/practice_contract.py`、`src/flab/external_usage.py`、`src/budget.py`、`src/forecast.py`、`tests/test_practice.py`，以及共用執行期相依模組（`src/advise.py`、`src/flab/advisory_usage.py`、`src/post_close_audit.py`）與 `docs/tasks/D133-CLOSED-PRACTICE.md` 契約。
- **審查立場與守則核對**：
  - 僅依據提供之 Source Packet 進行靜態設計與程式碼審查；**未執行任何測試或指令**（測試結果僅作為提供之書面證據評估）。
  - 保留 D132 工作樹變更，**未核准亦未啟用 FutureEval 主賽（33121）**。
  - 保護之執行期檔案（`adapters/*`、`flab/runner.py`、`flab/netguard.py`、`config/*`、`prompts/*`）未變更，全程無預測或留言 POST 路徑。

---

## 一、 實際證據盤點（Actual Evidence from Source Packet）

1. **API 與母體現況證據（書面提供）**：
   - 目前 FutureEval 主賽季（Project `33121`）共盤點出 108 題 `closed`/`resolved` 候選題，列表與 3 次單題 `get_post` 詳情查詢之 `description`／`resolution_criteria` 均被平台遮罩（masked）。
   - 既有正式 MiniBench 10 題均已通過 D126 `post_close_audit.py` 三道閘門。
   - 因此在當前一般驗證帳號權限下，D133 新題批次預期選題數為 `0`、模型呼叫數為 `0`，走了合法的 no-data fallback，未進行網頁爬蟲或借用其他帳號。
2. **架構隔離與安全邊界證據（程式碼靜態核對）**：
   - `src/practice.py` 僅使用 GET API（`client.get`、`client.list_posts`、`client.get_post`），無匯入 `submit` 或 `submit_transport`，亦無呼叫 `.post`／`submit_forecast`／`submit_comment`。
   - `src/flab/practice_contract.py`（`execution_cutoff`，第 10–38 行）強制要求 `ctx.tier == 1`、`kind == "initial"`、`prev is None`，且將 `forecasts.jsonl`、`audits.jsonl`、`raw/` 綁定於 `data/practice/<batch>/<qid>/<player>/` 獨立路徑，同時保留原始 `scheduled_close_time`（`deadline_utc`），不放寬 `forecast.eligible()` 對正式題 `status == "open"` 的預設檢查。
   - `src/budget.py`（`budget_v9`）改由 `flab.external_usage.usage` 讀取共用外部計帳，使未知（unresolved）或已啟動（started）之練習 attempt 計入真實週額度並阻擋同線重跑或新預約，且 `Budget.recover()`（第 140–142 行）不會釋放已登記於 `practice_usage.jsonl` 的未知嘗試。
   - `timing_classification()`（`src/practice.py` 第 114–122 行）將所有無事件發生時間證據的閉題保守歸為診斷組（`retrospective_diagnostic` 或 `outcome_timing_unknown_diagnostic`），且 `clean_evaluation_eligible` 一律為 `False`。
3. **測試證據與覆蓋缺口（書面提供 vs 靜態核對）**：
   - 書面提供結果為 40 個 practice 測試通過（相關測試集 490 passed / 12 個既有 live config 導致之 baseline scheduler 失敗）。
   - 但靜態審視 `tests/test_practice.py`（第 1–235 行）發現：**`practice.run()` 的實際選題執行路徑（第 336–372 行）與 `practice.score()` 的完整函式（第 400–438 行）在測試中從未被呼叫過**，且其中隱藏多處阻擋級缺陷。

---

## 二、 具體阻擋項（Concrete Blockers — 必須修正方可 PASS）

### Blocker 1：`score()` 重新合成未落盤的 Aggregate（產生幽靈 `record_id`）、在持六把正式鎖下執行 `fresh_index` 網路輪詢，且因 `formal_work_priority`／`content_hash`／同步題檢查導致事後計分遭誤殺
- **位置**：`src/practice.py` `score()` 第 400–438 行（特別是第 405–406、411、416–417、427–435 行）。
- **問題分析**：
  1. **Aggregate 追溯性斷裂（違反 D133 契約第 5 條「批次、題目、attempt、版本、來源、稽核、採用與缺漏皆可追溯」）**：
     - `run()` 在第 364–366 行已計算並將凍結的 `agg` 寫入 `root / "aggregates.jsonl"`。
     - 然而 `score()` 在第 427 行沒有讀取 `root / "aggregates.jsonl"`，而是重新呼叫 `aggregate.build_aggregate(snap, forecasts, audit_events=audits)`。這會產生全新的隨機 `record_id`（`new_id("agg")`）與新時間戳，導致寫入 `scores.jsonl` 的 `source_id` 無法對應回 `aggregates.jsonl` 中的實際紀錄。此外，若某題尚未執行 `run()`，`score()` 仍會憑空合成空 aggregate 並寫入計分紀錄。
     - 若某題僅 1 線成功（未達 aggregate quorum，`agg["status"] != "ready"`），若 `agg.get("input_attempt_ids")` 為空，連該單線有效預測也會無法計分，無法區分單線與整合表現。
  2. **本地事後計分被正式工作讓路規則與同步檢查誤殺，且不當長時間占用全部六把正式鎖（違反 D133 契約第 6 條）**：
     - `score()` 完全不呼叫模型（0 quota 消耗），卻在第 405–406 行呼叫 `lock_all(shared, locks)` 鎖住 `cycle`、`scheduler`、`submit` 與三條 `forecast.<player>` 鎖，並在持鎖期間執行 `fresh_index(client, root)`（輪詢所有賽事）與逐題 `client.get_post()`。
     - 當未來題目結算要執行 `score()` 時，只要當時有任何活躍 MiniBench 題目處於 `open` 狀態，`fresh_index()`（第 81–83 行）就會拋出 `ValueError("formal_work_priority")`，導致在整個 MiniBench 開題期間完全無法執行本地計分。
     - `score()` 呼叫 `closed_snapshot(post, index)`（第 415 行）並檢查 `fresh["content_hash"] != snap["content_hash"]`（第 416 行）。Metaculus 官方在結算題目時，常於 `fine_print` 或 `resolution_criteria` 補註結算說明（導致 `content_hash` 改變），或日後新賽事出現同名同步題（觸發 `open_or_upcoming_synced_copy`），這會使已凍結之練習預測在結算時直接拋出 `ValueError` 而無法計分。
- **具體修復方案**：
  1. `score()` 不應鎖定六把正式執行鎖（最多僅需讀取 `shared / "practice_batches.jsonl"` 的 `transaction(shared)`，或批次本地鎖），亦不應在 `score()` 呼叫 `fresh_index()`。
  2. 對已執行過 `run()` 的題目，從 `root / "aggregates.jsonl"` 讀取當時凍結的 aggregate 紀錄；單線計分應針對滿足 `F.adoptable(x)` 的 attempt（或明確區分各線 adoptable 輸出與已落盤之 aggregate）。
  3. 取得 `post = client.get_post(entry["post_id"])` 後，應驗證題目身分與題型不變（`post_id`、`question_id`、`question_type`、`scheduled_close_time`、選項／scaling 結構一致）及狀態屬於 `A.CLOSED_STATUSES`，再交由 `classify_resolution(post, snap)` 判定結算狀態；若 `content_hash` 因結算註記變動，應記錄為欄位變動標記而非直接崩潰拒絕計分。

---

### Blocker 2：`report()` 將 `local_scores` 寫死為 `None`、未讀取 `scores.jsonl`，且 `missing_outputs` 與成本／暴露統計不符合 D133 評估報告規格
- **位置**：`src/practice.py` `report()` 第 441–464 行。
- **問題分析**：
  1. **`local_scores` 永遠為 `None`**：第 459 行硬編碼 `"local_scores": None`，完全未讀取 `root / "scores.jsonl"`。即便已執行 `practice.py score`，產出的報告仍顯示無本地分數，且未依 D133 要求按資料分層組別（`group`）與題型（binary/MC vs numeric/discrete）分列診斷分數與結算狀態計數。
  2. **`missing_outputs` 與 `valid` 計算失真**：
     - 第 457 行 `"missing_outputs": len(manifest["selected"]) * 3 - len(rows)` 只扣除 `forecasts.jsonl` 的總行數。當某線執行失敗但寫入了一筆 `validation_status == "incomplete"` 或 `"invalid_json"` 的紀錄時，`len(rows)` 仍為 1，`missing_outputs` 會算出 `0`，掩蓋缺答／失敗。
     - 第 456 行僅統計 `r.get("validation_status") == "valid"`，未區分 `F.adoptable(r)`（例如格式 valid 但 `model_check == "mismatch"` 或含 `leak_confirmed` 的輸出不可採用）。
  3. **缺少未知成本與稽核／暴露統計**：D133 要求「報告分開列：有效輸出／缺答、耗時與額度、來源可核實性……未來資訊或答案暴露、各線與整合的差異……缺答、失敗及成本未知不可藏掉」。目前 `report()` 僅有 `usage_observed` 計數，未列出 `usage_unknown`（已啟動但無 token 紀錄或 pending 之嘗試）、稽核暴露標記計數（如 `suspected_leak`、`leak_confirmed`）及 `aggregates.jsonl` 的整合狀態統計。
- **具體修復方案**：
  1. 在 `report()` 中讀取 `root / "scores.jsonl"`、`root / "aggregates.jsonl"` 與各線 `audits.jsonl`（僅擷取計數與分組統計等 metadata，不洩漏題目正文或預測機率至共享報告）。
  2. 明確分列：`adoptable`、`valid`、`failed_or_invalid`、`missing_outputs`（`len(manifest["selected"]) * 3 - adoptable_count` 或明確分開 `not_run` 與 `failed`）、`usage_observed`、`usage_unknown`、`audit_flag_counts`、`aggregate_statuses`，以及按 `(group, question_type)` 分組的本地診斷分數摘要。

---

### Blocker 3：`prepare()` 的時間切分保留（Holdout）邏輯在候選題同截止時間或題數少時會吞掉全部可用題，永久凍結 0 題批次
- **位置**：`src/practice.py` `prepare()` 第 167–172 行。
- **問題分析**：
  ```python
  usable.sort(key=lambda r: (r["snapshot"]["scheduled_close_time"], r["question_id"]))
  latest_close = usable[-1]["snapshot"]["scheduled_close_time"] if usable else None
  holdout = [r for r in usable if r["snapshot"]["scheduled_close_time"] == latest_close]
  development = [r for r in usable if r not in holdout]
  ```
  - Metaculus 季度賽題經常成批在完全相同的 `scheduled_close_time` 關閉；或者當可用題數僅有 1～3 題且同屬一個截止時間群組時，`latest_close` 會等於所有 `usable` 題目的 `scheduled_close_time`。
  - 此時 `holdout` 會拿走 100% 的 `usable` 題目，導致 `development = []`、`selected = []`。
  - 由於 `prepare()` 隨即寫入單例帳冊 `shared / "practice_batches.jsonl"`（第 144、186 行禁止重建批次），一旦發生同截止時間，即使有合法可用題也會永久凍結出 `selected = []` 的空批次。
  - 此外，`prepare()` 在進場時未呼叫 `shared_ready(shared)` 確認正式賽本地無在途 attempt 或未收斂之送出回執（違反 D133 契約第 1 條）。
- **具體修復方案**：
  1. 在 `prepare()` 取得鎖後呼叫 `shared_ready(shared)`（或將正式帳冊收斂檢查納入 `prepare()` 前置條件）。
  2. 修正 `holdout` 切分規則：僅當不同 `scheduled_close_time` 群組數 `>= 2` 時才將最新截止時間群組列為 `holdout`；若所有可用題目均屬同一 `scheduled_close_time` 且 `len(usable) > 3`，則按決定性排序保留最後題目作為 holdout、前 3 題作為 `selected`；若 `1 <= len(usable) <= 3` 且無其他時間群組，應在 manifest 明確註記樣本數不足以切分獨立時間 holdout 並選入診斷批次（或保留最後 1 題當 `len(usable) >= 2`），絕不可讓非空 `usable` 變成 `selected == []`。

---

### Blocker 4：`run()` 在耗時的 `F.preflight()` 後未重新執行 `guard()` 即寫入不可逆的 `practice_question_started`，且 `F.run_attempt` 在 `before_attempt` 記帳後立刻重複呼叫 `recheck_inputs()` 存在 20 秒快取過期燒毀配額之競態
- **位置**：`src/practice.py` `run()` 第 338–361 行、`run_line()` 第 259–277 行；`src/forecast.py` `run_attempt()` 第 674、692、695 行。
- **問題分析**：
  1. **`preflight` 期間之正式工作讓路缺口**：
     - `run()` 在第 350 行呼叫 `guard()`（更新 `last[0]`），接著在第 352 行對三條線依序執行 `F.preflight()`（其中 Gemini preflight 包含最高 30 秒的 `quota.snapshot(timeout_s=30)` 與 CLI 版本／目錄檢查）。
     - 當 `F.preflight()` 完成時，距離第 350 行的 `guard()` 往往已超過 20 秒。但第 355–360 行只呼叫了 `shared_ready(shared)`，**沒有重新呼叫強制刷新的 `guard()`** 就直接把 `practice_question_started` 寫入 `root / "runs.jsonl"`。
     - 一旦 `practice_question_started` 寫入，第 334 行（`if any(r.get("question_id") == qid for r in read_complete(root / "runs.jsonl")): raise ValueError(...)`）就永久鎖死該題不得重跑。若在 `preflight` 期間剛好有正式賽題目進入 20 分鐘開題窗或題目狀態改變，三個 worker 進入 `run_line` 時才會在 `guard()` 失敗，白白永久報廢該題。
  2. **`before_attempt` 與其後 `recheck_inputs` 間的 20 秒快取邊界競態**：
     - 在 `src/forecast.py` 中，第 674 行先呼叫 `ctx.recheck_inputs()`（即 `guard()`），第 692 行呼叫 `ctx.before_attempt(kind, attempt_id)`（內部第 260 行又呼叫一次 `guard()`，隨即執行 `b.reserve()` 與 `register_admission()` 寫入 `shared / "practice_usage.jsonl"`），緊接著第 695 行（尚未寫入 `forecast_attempt_starts.jsonl` 前）**第三次**呼叫 `ctx.recheck_inputs()`（`guard()`）。
     - 若第 260 行呼叫 `guard()` 時距離 `last[0]` 為 `19.9` 秒（命中快取通過），寫完 `practice_admitted` 後在第 695 行剛好滿 `20.0` 秒觸發網路重查並遇到 API 瞬斷或狀態變更，此時 CLI 根本尚未啟動（`start_written == False`），但 `practice_admitted` 已寫入 `practice_usage.jsonl`。因為 `register_admission` 與 `runs.jsonl` 禁止同題同線第二次准入，該線的練習機會與 9 次上限配額會在 CLI 未啟動的情況下被永久消耗。
- **具體修復方案**：
  1. 在 `run()` 完成 `F.preflight()` 與 `shared_ready(shared)` 之後、寫入 `practice_question_started` 之前，將 `last[0] = None` 並強制執行一次新鮮的 `guard()`。
  2. 在 `before(kind, aid)` 準備進入 `with transaction(shared):` 執行 `b.reserve()` 與 `register_admission()` 前，確保 `guard()` 若距上次檢查已接近閾值則先行刷新，且避免在 `before_attempt` 剛完成後的毫秒內於 `recheck_inputs` 再次觸發網路輪詢（例如 `before()` 成功刷新或驗證後更新時間戳，使緊接其後的第 695 行 `recheck_inputs()` 不會在 `start_record` 落盤前發起多餘的外部網路請求）。

---

### Blocker 5：`run_line()` 對 `F.LineStop` 之處置存在重複寫入 `line_status.jsonl` 或遺漏錯誤原因／冷卻資訊的不一致，且 CLI 缺參數未於入口攔截
- **位置**：`src/practice.py` `run_line()` 第 287–293 行、`propagate_safety()` 第 220–235 行、`main()` 第 467–474 行。
- **問題分析**：
  1. **重複寫入 `line_stop`**：當 `F.run_attempt` 因 `tool_violations`、`safety_stop_after` 或 `leak_count >= 2` 拋出 `F.LineStop(..., notify=True)` 時，`F.run_attempt` 已先將紀錄寫入 `line_root / "forecasts.jsonl"`。接著 `run_line()` 的 `except F.LineStop as exc:`（第 288–290 行）會寫入一筆**不含 `attempt_id`** 的 `{"type": "line_stop", "source": "practice", ..., "reason": "practice_engine_stop"}`；隨後 `finally:` 呼叫 `finish_accounting()` -> `propagate_safety()`（第 231–235 行），因為找不到含該 `attempt_id` 的 `line_status.jsonl` 紀錄，又會再追加第二筆 `{"type": "line_stop", "source": "practice", ..., "attempt_id": aid, "reason": "practice_safety_event"}`。
  2. **非 notify 之 `LineStop`（如 Gemini 5h/weekly 配額閘門拒絕）遺失原因**：當 `F.run_attempt` 在 `before_attempt` 前因 `gemini_admission` 拋出 `LineStop(notify=False)` 時，第 291 行僅回傳 `{"player": player, "adoptable": False, "error_type": "LineStop"}`，未記錄 `reason: str(exc)` 與 `quota_retry_at_utc`。
  3. **CLI 參數驗證**：`main()`（第 470–472 行）在 `action == "run"` 時未檢查 `args.question is None`，會先取鎖並讀檔後才拋出 `"not a selected practice question"`。
- **具體修復方案**：
  1. 在 `run_line()` 的 `except F.LineStop as exc:` 中，若 `own` 已含 `aid` 且 `forecasts.jsonl` 已有紀錄，交由 `finally` 的 `propagate_safety()` 統一以含 `attempt_id` 的冪等方式寫入 `line_status.jsonl`；若 `forecasts.jsonl` 無紀錄（如 pre-admission stop）且 `exc.notify` 為真，才在 `except` 區塊寫入，並在回傳 dict 中保留 `"reason": str(exc)` 與 `"quota_retry_at_utc": exc.quota_retry_at_utc`。
  2. 在 `main()` 中當 `args.action == "run" and args.question is None` 時直接呼叫 `parser.error("--question is required for run")`。

---

## 三、 非阻擋設計建議與已知限制評估（Proposals & Known Limitations Assessment）

1. **單題執行持鎖時間（Bounded 12m Lock Hold）與無 Live Cancellation Hook 之評估（D133 契約第 6 條）**：
   - **現況評估**：由於 `adapters/*`、`flab/runner.py`、`flab/netguard.py` 屬受保護之已驗收執行期檔案，本輪未修改 subprocess runner 插入執行中強殺鉤子（live cancellation hook），而是採「每題獨立手動執行、單題最長 12 分鐘 cutoff、啟動前檢查 `active` MiniBench 無 `open` 題且未來 20 分鐘內無 `upcoming` 題開盤（`parse_iso(t["open_time"]) <= now + timedelta(minutes=20)`）」之設計。
   - **審查判斷**：在修正上述 **Blocker 1**（移除 `score()` 的全域鎖與 `fresh_index`）與 **Blocker 4**（補齊 `preflight` 後、`practice_question_started` 前的強制 `guard()` 刷新）的前提下，由於 MiniBench 題目皆有預定 `open_time`，20 分鐘前瞻緩衝大於 12 分鐘單題上限加上 post-quota 查詢時間，不會在單題練習執行中撞上已知預定開盤的正式題。此設計在目前「不更動受保護 runner 程式碼」的約束下可接受，但應在報告與交接文件中明列為已知架構限制（若平台無預警臨時插入即刻開盤題目，練習題會在最多 12 分鐘內結束並釋放鎖）。
2. **`src/flab/practice_contract.py` 單例批次檢查強化（Proposal）**：
   - 第 22–24 行檢查 `registered = read_complete(shared / "practice_batches.jsonl")` 時使用了 `not any(...)`；建議與 `practice.manifest_for()`（第 195 行）一致，加上 `len(registered) != 1`，在 contract 層同樣強制單例批次不變量。
3. **`src/forecast.py` 未完結外部嘗試之 `line_stopped()` 檢查（Proposal）**：
   - 目前未知外部嘗試（`set(admitted) - set(finished)`）已在 `Budget.reserve()`（`src/budget.py` 第 185 行）與 `practice.shared_ready()`／`advise.run()` 阻擋。建議在 `F.line_stopped()`（`src/forecast.py` 第 300 行附近）亦檢查 `external_usage.usage(log.parent)` 中是否有該 `player` 未完結的 attempt，使未走 `Budget.reserve()` 的本地 `forecast.py run` 入口也能一致攔截。

---

## 四、 缺失之有意義測試案例（Missing Meaningful Tests — 須補入 `tests/test_practice.py`）

依 D133 驗收要求，目前 `tests/test_practice.py` 缺少以下關鍵離線測試案例，修正上述 Blocker 後應一併補齊：

1. **`P.run()` 端到端完整執行與單題不可重跑測試**：
   - 對已登記批次中的 `selected` 題目實際呼叫 `P.run()`，驗證三線並行（含 mock preflight 與 adapter）、`runs.jsonl`（started/finished）、`observations.jsonl`、`aggregates.jsonl`（`submission_eligible: False`）、`practice_usage.jsonl` 與 `budget_events.jsonl` 完整落盤，且正式 `data/forecasts.jsonl` 與 `data/aggregates.jsonl` 未被寫入。
   - 驗證對同一 `qid` 第二次呼叫 `P.run()`（即使前次某線失敗缺答）立即拋出 `ValueError("question was already attempted; never restart or fill missing lines")`。
2. **`P.score()` 與 `P.report()` 完整整合測試（含各題型與非結算狀態）**：
   - 測試 `P.score()` 在題目 `resolved`（涵蓋 binary / multiple_choice / numeric）、`unresolved`、`ambiguous`、`annulled` 下的行為：僅 `resolved` 計算本地診斷分數，且 `source_id` 精確對應各線 `attempt_id` 與 `aggregates.jsonl` 中已落盤之 `record_id`；`official_peer_score` 恆為 `None`；正式 `data/resolutions.jsonl` 與 `data/scores.jsonl` 不被寫入。
   - 測試結算時平台在 `fine_print` 新增結算註記，或當時另有活躍 MiniBench 開放題時，`P.score()` 不會因 `formal_work_priority` 或說明文字微調而崩潰。
   - 測試 `P.report()` 能正確讀取 `scores.jsonl` 輸出分組／分題型本地分數摘要、正確統計失敗／缺答（`missing_outputs`）、未知成本（`usage_unknown`）與稽核標記。
3. **`P.run()` 執行前／執行中閉題重開或快照變更測試**：
   - 題目在 `prepare()` 時為 `closed`，但在 `P.run()`（包含 `preflight` 後與 `guard()` 重查時）變成 `open` 或內容改變，驗證 `guard()` 立即中止並拒絕啟動模型呼叫。
4. **同截止時間（Same `scheduled_close_time`）之 `prepare()` 選題與 Holdout 測試**：
   - 測試當所有可用候選題具有相同 `scheduled_close_time`（例如 1 題、3 題、5 題同時間關閉）時，`prepare()` 不會將全部題目放進 `holdout` 而導致 `selected == []`。
5. **`shared_ready()` 與 `line_ready()` 阻擋測試**：
   - 測試當共享帳冊存在未收斂之正式送出（`submissions.jsonl` 缺 `submission_readbacks.jsonl`）、未完成之正式 attempt（`forecast_attempt_starts.jsonl` 缺 `forecasts.jsonl`）、或共享線路處於 `quota_exhausted` backoff / `model_mismatch` cooldown / `leak_stop` 時，`P.prepare()` 與 `P.run()` 均會 fail-closed 拒絕執行。