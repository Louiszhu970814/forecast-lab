VERDICT: PASS

### 封包完整性與審查範圍確認
- **封包狀態**：完整，已確認讀至最終標記 `END-D140-CODE-R2A`，無任何截斷（truncation）。
- **審查範圍（Module A — D140 R2A）**：涵蓋 `src/line_recovery.py`、`src/forecast.py`（`line_stopped`、`latest_resume`、`run_attempt`、`_may_rerun`、`forecast_question`、`preflight` 等相關介面）、`src/adapters/gemini.py`、`src/flab/ledger.py`、`src/flab/jsonl.py`、`tests/test_line_recovery.py`、`PLAN.md`、`docs/tasks/D140-AUTO-RECOVERY.md` 與共用來源 manifest（SHA256: `d4feeb83673a3866a42c136e357bc2c8a233049344e36bec3eab76c16138b697`）。
- **權責邊界聲明**：本審查結論 `PASS` 僅針對 **Module A（D140 R2A）**，不構成全系統核准；正式部署前仍須待兩個官方模型對所有模組（含 Module B）均給出 `PASS`。審查過程僅依據來源封包資料進行靜態檢視，未執行任何外部指令或測試。

---

### Blocker 檢查結果（Blockers）
- **無（None）**。前次 R1 提出的所有阻斷項（BLK1–BLK4 涉及 Module A 部分）及獨立合成重現問題均已在凍結原始碼中完整修復並具備負向測試覆蓋。

---

### R1 阻斷項與獨立重現問題之原始碼核實（Actual Evidence）

1. **BLK1 與同 step 工具身分覆蓋問題（`strict_events` 與 `rejected_page_call`）**：
   - **原始碼證據**：於 `src/line_recovery.py:55-100`（`strict_events`）中，在呼叫 `G.tool_steps(events)` 合併步驟之前，即對每一筆原始 JSONL 事件與每一個 `step_index` 分組執行嚴格驗證：
     - 使用 `object_pairs_hook=unique` 與 `parse_constant=nonfinite` 拒絕重複 JSON 鍵與 `NaN`/`Inf`。
     - 驗證 `type(index) is int`（排除 `bool` 與字串）、`index >= last`（確保單調非遞減且同 step 事件連續）、`step_type in ('user_input', 'agent_response', 'tool', 'finish')`。
     - 對任何含 `step_type == 'tool'`、`tool_info` 或 `tool_name` 的步驟分組，強制要求 `len(updates) == 2`、狀態轉換恰為 `ACTIVE -> DONE/ERROR`、起始幀無 `output`/`error` 且 `parameters` 為 `dict`。
     - 逐幀比對 `tool_name`、`info['name']` 與 `info['parameters']` 必須與起始幀完全一致，唯一允許的 `step_type` 轉換為 `finish` 工具在完成幀轉為 `step_type == 'finish'` 且 `state == 'DONE'`。這徹底阻絕了同 `step_index` 先執行越權工具再被後續 `read_url_content` 覆蓋的合成漏洞。
     - 於 `rejected_page_call`（`src/line_recovery.py:103-141`）中，額外驗證 `init`（含頂層與 `init.conversation_id`）、所有 `step_update` 與 `result` 的 `conversation_id` 完全一致且符合 `CONVERSATION_ID_RE`。
   - **測試證據**：`tests/test_line_recovery.py` 中 `test_conflicting_conversation_identity_is_not_recoverable`、`test_earlier_tool_identity_cannot_be_overwritten_by_page_fetch`、`test_every_tool_step_identity_and_transition_are_checked`、`test_no_unparsed_tool_evidence_or_nonfinite_json` 已涵蓋上述所有負向情境。

2. **BLK2 當前隔離證據與事故發生時環境之綁定（`require_incident_environment` 與 `capture`）**：
   - **原始碼證據**：`src/line_recovery.py:242-251` 新增 `require_incident_environment(root, row, cfg)`，並於 `recover()` 啟動前（第 273 行）與提交 `line_resume` 前（第 295 行）的 ledger transaction 內雙重驗證：
     - 要求 `row.get('run_config_version') == cfg['version']`，且第 274–276 行核對當前 tier 的 `(model, effort)` 與事故 attempt 的 `(requested_model_id, requested_effort)` 完全相符。
     - 掃描 `acceptance.jsonl` 中所有紀錄，若存在任何 `approved_at >= started` 的 `baseline_approved`，或同玩家（非 `autoload`）`started_at >= started` / `finished_at >= started` 的 `acceptance_call_started` / `acceptance_result`，立即拋出 `ValueError` 拒絕快速恢復。
     - 結合 `capture()`（第 254–273 行）對雙層 `F.preflight('gemini', tier=tier, continuous=True, ...)`、零 waiver、新鮮 0 AI credits、CLI wrapper/pin SHA 與 `B.compare(old, current)` 的檢查，確保目前重用的正式驗收與基準紀錄即為事故發生前已生效且未曾變動的同一份證據。
   - **主機實證與測試證據**：封包內附 `capture-probe.json`（SHA256: `96801c9014bd21f3d2e3fca5d6c338dab4b797639b3a0d8192c6b778e3576fd9`）顯示真實事故 `att_1793865001054_737678fb` 對照之 `base_1793803103661_66326b64`、`acc_1793802323516_b0387ed0` 及兩層正式 production proofs 均早於事故時間，兩次 capture 完全相等、耗時 13.90 秒、0 次模型呼叫且帳冊未變；`test_incident_cannot_reuse_later_environment_approval` 覆蓋了事後重驗收或改設定的拒絕邏輯。

3. **BLK3 權威 `line_resume` 提交後之錯誤處理（防止誤記 `held`）**：
   - **原始碼證據**：`src/line_recovery.py:309-335` 中，當 `line_resume` 已寫入 `line_status.jsonl` 後，若寫入 `line_recovery.jsonl` 的 `recovery_finished` 發生 `OSError` 或觸發 `RecoveryDeadline`（`SIGALRM`），`except Exception as exc:` 區塊會在 `transaction(root)` 內先檢查 `line_status.jsonl` 是否已存在 `recovery_id == rid` 的 `line_resume`。若已提交，直接回傳 `{'status': 'resumed', ..., 'completion_journal': 'pending'}`，絕不補寫 `outcome: 'held'`，也不會將已恢復的線誤報為失敗；若連帳冊讀取都失敗則由 `main()` 回傳 `check_failed_state_not_assumed`。
   - **測試證據**：`tests/test_line_recovery.py` 的 `test_authoritative_resume_never_relabelled_held` 驗證了 `completion_log` 拋出 `OSError` 與提交後觸發 `RecoveryDeadline` 兩種故障注入。

4. **BLK4 健康線鎖競爭與持鎖邊界（Module A 部分）**：
   - **原始碼證據**：`src/line_recovery.py:265-268` 在進入 `with F.Lock(root/'line_recovery.lock'), F.Lock(root/'forecast.gemini.lock'):` 之前，先執行無鎖預檢 `preliminary = F.line_stopped(...)`；當路線健康（`None`）或處於既有冷卻（`until`）時直接返回，不在每 300 秒定時檢查時爭搶 `forecast.gemini.lock`。恢復期間亦從未取得 `cycle.lock`、`scheduler.lock`、`submit.lock` 或其他玩家線鎖。
   - **測試證據**：`test_healthy_line_never_acquires_recovery_or_forecast_lock` 與 `test_recovery_does_not_lock_other_lines_or_submit` 已明確驗證。

5. **同 Attempt ID 變造危害重播防護與單次補做 Claim（`F.line_stopped`、`R.situation`、`F.run_attempt`）**：
   - **原始碼證據**：
     - `src/forecast.py:362, 403-407` 於 `F.line_stopped` 重播時，將 `covered_attempts[attempt_id]` 綁定至 `proof.get("metadata_sha256")`，重播時比對 `hash_obj(projection(r))`，若同 ID 紀錄的投影雜湊改變則不再豁免；`src/line_recovery.py:167` 在 `R.situation` 中對重複 `attempt_id` 直接拋出 `ValueError('duplicate_attempt_id')` fail-closed。
     - `src/forecast.py:937-946` 在 `run_attempt` 準備啟動 CLI 前，於同線鎖與 `transaction(ctx.forecasts_log.parent)` 內呼叫 `retry_grant(...)` 再次驗證 grant 有效性，並將 `recovery_grant_id` 與 `admission_decision_id` 一併寫入 `forecast_attempt_starts.jsonl`（fsync）；一旦寫入 start 紀錄，`retry_grant`（`src/line_recovery.py:220-221`）即視為已消耗該問題／tier 的唯一 rerun 槽位。
   - **測試證據**：`test_same_attempt_id_cannot_cover_new_tool_hazard` 與 `test_recovery_grant_is_selected_and_budgeted_by_real_scheduler` 已涵蓋重播防護及排程器預算預留至真實 start journal 的完整鏈路。

---

### 非阻斷性建議（Non-blocking Proposals / Observations）
1. **`R.situation` 內部 `covered` 集合與 `F.line_stopped` 的對稱性（Defense-in-depth）**：
   - 在 `src/line_recovery.py:178-196` 中，`R.situation` 目前將已恢復的 `proof.get('attempt_id')` 放入 `covered` 集合（以 ID 比對），而 `F.line_stopped`（`src/forecast.py:362, 406`）則是比對 `covered_attempts.get(r.get("attempt_id")) == hash_obj(projection(r))`。由於 `forecasts.jsonl` 為僅追加帳冊，且 `R.situation` 第 167 行已對重複 `attempt_id` 直接拋出 `ValueError('duplicate_attempt_id')`，同時 `F.line_stopped` 也會攔截任何投影雜湊不符的紀錄，因此目前不會產生安全漏洞。未來若作維護，可考慮讓 `R.situation` 的 `covered` 也一併比對 `metadata_sha256` 以保持兩處寫法完全對稱。

END-D140-CODE-R2A