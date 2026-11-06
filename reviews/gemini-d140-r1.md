VERDICT: PASS

### D140 程式碼審查報告（Round 1）

本次審查僅依據所提供之完整來源封包（含完整 Diff、上下文摘錄、`deploy.py`／`test_deploy.py` 及驗證雜湊，且已確認封包完整包含至最終標記 `END-D140-CODE-R1`）進行靜態程式碼與架構查核。本人未執行任何測試、外部命令或讀寫檔案，亦未檢視任何預測內容（prediction bodies）。

---

### 一、阻斷性問題（Blockers）

**無（None）。** 經逐一核對變更路徑與邊界條件，未發現任何阻斷性缺陷或安全／隔離退化。

---

### 二、B1–B7 設計審查條件逐項程式碼實證評估

#### B1：精確事件恢復（Exact-episode resume）不得掩蓋其他硬停線或回補危害
- **實際程式碼證據**：
  1. `src/forecast.py:347-370`（`line_stopped`）：當讀取到 `type == "line_resume"` 且 `by == "line_recovery_v1"` 時，僅在 `authorization == "PLAN D140"`、`isinstance(lifts, dict)`、`proof.get("diagnosis") == "unexecuted_page_tool_typo"` 且具備 `proof.get("attempt_id")` 時，從 `pending_stops` 移除與 `lifts` 完全相等（`s != lifts`）的該筆 stop 紀錄，並將 `proof["attempt_id"]` 加入 `covered_attempts`。**不會**清空其他未覆蓋的 `pending_stops`，也**不會**推進 `resumed_at`（`resumed_at` 僅在人工 `line_resume` 分支更新）。
  2. `src/forecast.py:398-415`（`line_stopped` 帳冊重播）：由於自動恢復不推進 `resumed_at`，所有自上次人工 resume 之後的紀錄仍會逐筆重播；`covered_attempts` 僅豁免該特定 `attempt_id` 的 `tool_violations`／`error_type == "tool_scope_violation"`（第 405-406 行），若同筆或其他紀錄含有 `safety_stop_reason`、`_legacy_mismatch` 或 `leak_confirmed`，仍會觸發停線。
  3. `src/forecast.py:443-446`（`latest_resume`）：明確過濾 `r.get("by") != "line_recovery_v1"`，確保 `run_attempt`（第 1091 行）計算 `leak_count` 與 `src/line_recovery.py:185`（`situation`）回溯安全事件時，不會把自動恢復當作人工時間豁免切點。
  4. `src/line_recovery.py:186-210`（`situation`）與 `216-227`（`candidate`）：若存在其他未覆蓋的顯式 `line_stop`（`pending != [stop]`），拋出 `ValueError('other_uncovered_stop')`；若 `unsafe` 事件數不為 1、存在未完成之 `forecast_attempt_starts.jsonl`（`unresolved_start`）、未結案外部用量（`unresolved_external_start`）、`audits.jsonl` 出現 `leak_confirmed`（`confirmed_audit_exposure`）、處於 `model_mismatch` 冷卻或 `quota_retry_at_utc` 退避中，均拒絕恢復。歷史 `suspected_leak` 關鍵字標記維持原狀且不被清除，亦不升格為確認洩漏。

#### B2：目標檔案必須源自同一 attempt 較早且成功的 `read_url_content`
- **實際程式碼證據**：
  1. `src/line_recovery.py:59-67`（`safe_raw`）：強制核對 `raw_dir` 必須等於 `data/raw/gemini/<question_id>_<attempt_id>` 相對路徑，檢查 `path` 與所有父目錄皆非 symlink、檔案存在且不超過 `MAX_RAW_BYTES`（32 MiB）。
  2. `src/line_recovery.py:70-98`（`strict_events`）：要求原始串流以 `\n` 結尾、嚴格 UTF-8 解碼、透過 `object_pairs_hook=unique` 拒絕重複 JSON key（`duplicate_json_key`），並嚴格驗證 `init`、`step_update`、`result` 頂層與內層欄位白名單，以及最後一筆必為 `result`。
  3. `src/line_recovery.py:101-139`（`rejected_page_call`）：
     - 驗證單一 `init` 事件與合法 `conversation_id`（`G.CONVERSATION_ID_RE.fullmatch(cid)`）。
     - 驗證全場僅有唯一一筆 `REJECTED_TOOL`（`' view_file'`），參數集合精確為 `{'AbsolutePath', 'StartLine', 'EndLine'}` 且行號為正整數（`1 <= StartLine <= EndLine`）。
     - 驗證目標路徑符合 `re.fullmatch(re.escape(prefix) + r'/\.system_generated/steps/[0-9]+/content\.md', target)` 且通過 `G.own_page_file(target, cid)`，並檢查目標與其父目錄均非 symlink（第 121 行）。
     - 驗證該步狀態序列精確為 `['ACTIVE', 'ERROR']`、錯誤訊息精確為 `REJECTION`（`'unknown tool: " view_file" — check spelling'`），且所有更新均無 `'output'` 欄位（證明未執行、無讀取輸出）。
     - 擷取路徑中的 `page_step` 編號，強制核對 `page_step < s['step']`，且同次 attempt 中存在 `step == page_step`、`tool == 'read_url_content'`、`state == 'DONE'` 的成功步驟（第 118-120 行）。
     - 逐一檢查同次 attempt 的所有其他工具呼叫均在 `G.TIER1_TOOLS` 內、無權限拒絕（`G.denial_of`）、`view_file` 僅讀自身 page、`read_url_content` 未讀取 Metaculus 網域，且最終 `result` 為單一 `SUCCESS` 且無 `denied_actions`。
  4. `src/line_recovery.py:142-167`（`classify`）：僅投影 `META_KEYS` 技術欄位（不含預測機率與理由），比對 `tool_violations` 兩筆結構與 `tool_call_counts` 完全一致，並在解析前後雙重驗證 `sha(path) == raw_sha`。

#### B3：跨 stop／manual resume／新安全事件的共用交易（Transaction）與 CAS
- **實際程式碼證據**：
  1. `src/forecast.py:1311-1314`、`src/reconcile.py:309-311`：停線寫入 `line_status.jsonl` 均納入 `with transaction(ctx.line_status_log.parent):`。
  2. `src/forecast.py:1336-1342`：人工執行 `forecast.py resume` 時，先取得 `with Lock(LINE_STATUS_LOG.parent / f"forecast.{args.player}.lock"), transaction(LINE_STATUS_LOG.parent):`，再判定 `line_stopped` 並寫入 `line_resume`。
  3. `src/line_recovery.py:303-355`（`recover`）：
     - 全程持有 `F.Lock(root/'line_recovery.lock')` 與 `F.Lock(root/'forecast.gemini.lock')`。
     - 第一階段在 `with transaction(root):` 內讀取所有相關帳冊（使用 `read_complete`，遇殘行直接拋錯 fail-closed）並計算 `situation_sha256` 與 `stop_sha256`。
     - 外部耗時檢查 `before = capture_fn(project, cfg)` 置於 `transaction(root)` 之外，避免阻塞其他線。
     - 第二階段在 `with transaction(root):` 內重算 `check, _ = candidate(root, now())` 與 `active()`，若 `check != current or active() != active_hash` 則拋出 `ValueError('superseded')`；確認無變動後才寫入 `recovery_started`。
     - 第三階段完成第二次 `after = capture_fn(project, cfg)` 並確認 `after == before` 後，再次於 `with transaction(root):` 內執行 CAS 比對 `check == current`、`active() == active_hash` 與最新 `baseline_record_id`，通過後才同筆交易寫入 `line_resume` 與 `recovery_finished`。

#### B4：單次 Grant Claim／Start 紀錄、既有含 Pending 之預算准入與崩潰處理
- **實際程式碼證據**：
  1. **每筆 Stop 單次恢復與每日上限**：`src/line_recovery.py:310-315` 於寫入 `recovery_started` 前檢查 `stop_sha256` 是否已存在於 `line_recovery.jsonl` 的 `recovery_started` 紀錄中，以及過去 24 小時（86400 秒）內 `recovery_started` 次數是否已達 `MAX_RECOVERIES_24H`（3 次）。由於 `recovery_started` 在第二次 `capture_fn` 前即已落盤，若中途崩潰或超時，下次執行必回傳 `already_attempted`，絕不重試同一 stop episode。
  2. **既有 Budget 預留與崩潰釋放**：補做由排程器（`src/scheduler.py:381-413` 或 `src/reconcile.py:265-286`）在持有 `forecast.gemini.lock` 下啟動 `F.forecast_question`。在 `src/forecast.py:921-923` 呼叫 `ctx.before_attempt(kind, attempt_id)` 進入 `Budget.reserve`（`src/budget.py:153-348`），其內部計入所有未結案預留、未完成 starts 與外部用量。
  3. **原子 Claim 與 Start 落盤**：`src/forecast.py:935-945` 在 `Budget.reserve` 成功後、實際呼叫 `ctx.adapter.run` 之前，於 `with transaction(ctx.forecasts_log.parent):` 內再次呼叫 `retry_grant` 二次驗證 grant 有效性，將 `start_record["recovery_grant_id"] = grant["recovery_id"]` 寫入 `forecast_attempt_starts.jsonl`（fsync 落盤）。
     - 若在 `Budget.reserve` 後、`starts_log` 寫入前發生例外或崩潰：正常例外會由 `finally`（第 968-969 行）以 `start_written=False` 呼叫 `ctx.admission_outcome` 釋放預留；若程序直接崩潰，下次排程 `Budget.recover()`（`src/budget.py:135-151`）會因 `attempt_id not in started` 自動釋放未啟動之預留。
     - 一旦 `starts_log` 寫入完成，`src/line_recovery.py:244-245` 的 `retry_grant` 檢查到 `forecast_attempt_starts.jsonl` 已有該 `(player, question_id, tier)` 的 `rerun` 紀錄即永久回傳 `None`，且 `unresolved_starts` 亦會阻擋重跑，確保絕無雙重 claim 或重複執行。

#### B5：真實持鎖契約（Held-lock contract）與避免巢狀 `flock`
- **實際程式碼證據**：
  - `src/line_recovery.py:303` 使用 `with F.Lock(root/'line_recovery.lock'), F.Lock(root/'forecast.gemini.lock'):`。
  - `src/forecast.py:1240-1255`（`Lock.__enter__`）在取得 `fcntl.flock` 後，會將 `self.path.resolve()` 寫入執行緒區域變數 `_HELD_LOCKS.paths`。
  - 當 `capture` 呼叫 `F.preflight` 進而呼叫 `recover_acceptance`（`src/forecast.py:681-689`）時，`held = lock_path.resolve() in getattr(_HELD_LOCKS, "paths", set())` 判定為 `True`，因此採用 `nullcontext()` 而非再次開啟新 fd 執行 `Lock(lock_path)`，避免了對同一鎖檔二次 `flock(LOCK_NB)` 導致 `BlockingIOError`／`PreflightError`。同時，fast path 不呼叫 `acceptance.run_line`，無模型呼叫。

#### B6：雙層（Tier 1 & Tier 2）隔離證據、Baseline、CLI 實體與 Wrapper SHA、Runtime、Judge 與權限限制維持不變
- **實際程式碼證據**：
  - `src/line_recovery.py:265-288`（`capture`）：
    1. 強制核對 `Path(shutil.which('agy') or '') == wrapper`、非 symlink，且 `sha(wrapper) == WRAPPER_SHA`、`sha(pin) == PIN_SHA`。
    2. 強制執行 `quota.snapshot(timeout_s=20)`，驗證 `observation_fresh` 且 `remaining_credits == 0`。
    3. 擷取當前 `B.cli_versions()` 與 `B.take_snapshot(versions=versions, gemini_credits=0)`，比對 `B.latest_approved(root/'acceptance.jsonl')` 決策必為 `'ok'`。
    4. 對 `tier in (1, 2)` 迴圈呼叫 `F.preflight('gemini', cfg=cfg, tier=tier, continuous=True, ...)`，要求 `pf.ok and not pf.waivers`。`F.preflight` 內部透過 `check_acceptance`（`src/forecast.py:195-244`）驗證最新驗收與該 tier 生產 `(model, effort)` 驗收紀錄之 `line_pass`、`J.JUDGE_VERSION`、`cli_version`、`evidence_sha256`、`tier2_equivalent_to_tier1`（Tier 2）及所有 `runtime_fingerprint('gemini')` 檔案雜湊完全相符。
    5. 結束前再次驗證 `quota.observation_fresh(observed, utc_now())`。
    6. `recover` 前後執行兩次 `capture_fn` 並要求回傳之 `binding` 完全一致（`after == before`），且 `retry_grant` 在排程器實際啟動補做時（`src/line_recovery.py:248-255`）會再次核對 `runtime`、`run_config_sha256`、`baseline_record_id`、`wrapper_sha256` 與 `pin_sha256`。

#### B7：維持既有 `initial -> rerun` 上限與無 Grant 時之一般行為
- **實際程式碼證據**：
  1. `src/forecast.py:1175-1179`（`forecast_question`）：`if pending_repair is None and any(r["attempt_kind"] == "rerun" for r in bases): return ... "outcome": "attempts_exhausted"` 位於 `_may_rerun` 之前，確保已執行過 rerun 的題目／線／tier 絕不可能再獲得第二次 rerun。
  2. `src/forecast.py:1133-1147`（`_may_rerun`）：僅在傳入 `recovery_root` 且 `retry_grant(recovery_root, chain, base, now=now)` 回傳有效 grant 時回傳 `True`；由於 `retry_grant` 強制要求 `len(chain) == 1` 且 `base['attempt_kind'] == 'initial'`，一旦補做（rerun）執行完畢（若再次失敗，`len(chain) > 1` 且 `starts_log` 已有 rerun），`_may_rerun` 即回退至原邏輯並回傳 `False`。原失敗之 `initial` 紀錄維持 `validation_status: "incomplete"`，絕不被採用。
  3. `src/discovery.py:140-142`：修正 `waiting(row)`，當 `row.get('outcome') == 'line_stopped'` 但 `row.get('player') not in stopped` 時回傳 `False`，使恢復後的題目能在下一個 60 秒 heartbeat 立即被排程器喚醒，無須苦等 1800 秒新題輪詢。

---

### 三、非阻斷性建議與觀察（Non-blocking Proposals / Observations）

1. **`init` 事件內外層 `conversation_id` 一致性斷言（非阻斷）**：
   - 於 `src/line_recovery.py:105`，`cid = ({**{'conversation_id': inits[0].get('conversation_id')}, **inits[0]['init']}).get('conversation_id')` 合併了頂層與 `init` 物件內的 `conversation_id`。目前因單一 `cid` 隨後即受 `CONVERSATION_ID_RE`、`prefix` 與 `G.own_page_file(target, cid)` 嚴格綁定，不會產生越權風險；未來若欲進一步收緊 schema 校驗，可考慮在兩處皆存在時額外斷言兩者字串相等。
2. **跨 Snapshot 之 Rerun Start 檢查具保守 Fail-closed 特性（非阻斷）**：
   - 於 `src/line_recovery.py:244-245`，`retry_grant` 檢查 `forecast_attempt_starts.jsonl` 時未過濾 `question_snapshot_id`。這意味著同一 `(player, question_id, tier)` 若曾在舊 snapshot 消耗過 rerun，即使後續新 snapshot 的 initial 發生此拼字錯誤，也不會發給 grant。此設計比單一 snapshot 上限更加保守（fail-closed），完全符合安全與配額邊界要求。

END-D140-CODE-R1