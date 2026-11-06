VERDICT: PASS

### 審查範圍與封包完整性確認
- **審查範圍**：D140 R3 Module A（`src/line_recovery.py`、`src/forecast.py` 相關恢復／重試／安全回放邏輯、`src/adapters/gemini.py`、`src/flab/ledger.py`、`src/flab/jsonl.py`、`tests/test_line_recovery.py`、`PLAN.md`、`docs/tasks/D140-AUTO-RECOVERY.md` 及 `reviews/d140-r3-response.md`）。此 `PASS` 僅代表 Module A 程式碼審查通過，不構成全系統核准；實際部署仍須待兩個官方模型對共享 manifest（SHA256 `c5748f030fb2c6b85c42e820f21ed90019f53dad7033c594deeae3cbe0bd87e2`）之 Module A 與 Module B 均給出 `PASS`。
- **封包完整性**：已確認來源封包自首行 `D140 R3A.` 至末行標記 `END-D140-CODE-R3A` 完整無截斷。
- **審查方法聲明**：僅依據提供之靜態來源封包作為資料進行程式碼與設計查核，未執行任何測試、指令、檔案讀寫或外部呼叫，亦不宣稱已執行測試。

---

### 阻擋性問題（Blockers）
- **無（None）**。前輪（R1 及 R2）針對 Module A 提出之所有阻擋性問題均已於實際凍結程式碼中完整修復，且未放寬任何既有安全、配額、截止時間或工具隔離政策。

---

### 實際來源證據評估（Actual Source Evidence）

1. **事故當時環境與所選隔離證據之明確綁定（修復 R2 A-B1 / R1 BLK2）**
   - **`require_incident_environment`（`src/line_recovery.py:274-289`）**：建立明確的 10 種 `acceptance.jsonl` 事件類型至時間欄位白名單（`baseline_approved`、`acceptance_call_started`、`acceptance_call_finished`、`acceptance_result`、`acceptance_quota_snapshot`、`acceptance_retracted`、`acceptance_paused`、`autoload_result`、`autoload_run_started`、`baseline_check`）。凡 `player` 非 `claude`/`gpt` 之紀錄（含 `gemini`、`common` 及全域紀錄），若出現未知事件類型即拋出 `unknown_incident_acceptance_event`，缺少字串時間戳（其中 `baseline_check` 明確讀取 `snapshot.taken_at`）即拋出 `incident_acceptance_missing_time`，且不再豁免 `source="autoload"`；任何時間戳 `>= row['started_at_utc']` 均拋出 `acceptance_changed_after_incident`。
   - **`incident_binding`（`src/line_recovery.py:306-330`）**：對 `capture` 選出的 `baseline_record_id` 及 Tier 1 / Tier 2 的 `acceptance_record_id` 與 `production_record_id`，在 `acceptance.jsonl` 中逐一驗證唯一性（`len(matches) == 1`）、事件類型、`player == 'gemini'`、`line_pass is True`、時間嚴格早於事故 `started_at_utc`，且針對 production 紀錄驗證 `mode == 'production'`、`tier`、`model`、`effort` 與當前 `config` 完全吻合，並將各紀錄之 `{record_id, type, at, record_sha256}` 嵌入 `binding['incident_evidence']`。
   - **多重時點核對**：於 `recover()` 的兩次 `capture`（`src/line_recovery.py:392-394`）、寫入 `line_resume` 的 ledger transaction 內（`src/line_recovery.py:399-400`），以及 `retry_grant()` 實際授權 rerun 啟動前（`src/line_recovery.py:258-259`）均重新執行 `incident_binding` 與 `require_incident_environment` 比對。
   - **主機靜態投影證據**：封包內附之 `capture-probe-r3.json`（SHA256 `fa503bb37f0c9dd6834d2cb68abff40aaea381dff8a8ff06e364dd012e927a2a`）顯示 `incident_evidence` 已包含真實帳冊中 `base_1793803103661_66326b64`、`acc_1793802323516_b0387ed0`、`acc_1793537753400_66af4ff6`（Tier 1）與 `acc_1793537992394_7d15d846`（Tier 2）之時間與完整 row SHA，且兩次綁定一致（`bindings_equal: true`，`model_calls: 0`）。

2. **強制完整 Binding 結構與 Admission 後環境漂移防護（修復 R2 A-B2）**
   - **`validate_binding`（`src/line_recovery.py:292-303`）**：強制檢查 `baseline_record_id`、`run_config_sha256`、`wrapper_sha256`、`pin_sha256`、`judge_version`、`cli_version` 為非空字串，`runtime`（所有值均為非空字串）與 `sources` 為非空字典，`proofs` 為恰好涵蓋 Tier `{1, 2}` 的二元組列表且各自具備非空 `acceptance_record_id` 與 `production_record_id`。
   - **Fail-closed 處理**：`recover()` 中若 `capture_fn` 回傳缺漏欄位的 binding，在寫入 `recovery_started` 後會於 `incident_binding` 拋出 `ValueError`，並在 `except` 區塊記為 `outcome: 'held'` 而不寫入 `line_resume`（`src/line_recovery.py:392, 426-428`）。`retry_grant()` 遇到缺漏欄位或缺少 `incident_evidence` 的 `line_resume` 亦直接拒絕並回傳 `None`（`src/line_recovery.py:257-266`）。
   - **原子性 Claim 與預留釋放（`src/forecast.py:905-960`）**：`run_attempt()` 在 `ctx.before_attempt(kind, attempt_id)` 取得預算預留後、寫入 `forecast_attempt_starts.jsonl` 並呼叫 CLI 前，於 `transaction(ctx.forecasts_log.parent)` 中呼叫 `retry_grant()`；若此時環境或證據發生漂移（runtime、config、baseline、wrapper、pin 或 selected proof 改變），立即拋出 `QuestionDone("recovery grant no longer available")`，此時 `start_written` 仍為 `False`，`finally` 區塊必定呼叫 `ctx.admission_outcome(attempt_id, False)` 釋放預留且不執行任何 CLI 呼叫。
   - **測試覆蓋（`tests/test_line_recovery.py:338-413`）**：已檢視 `test_postincident_or_unknown_acceptance_evidence_is_never_reused`、`test_selected_proof_ids_are_explicitly_bound_to_unique_prior_records`、`test_incomplete_capture_is_held_without_resuming`、`test_incomplete_resume_binding_never_grants_retry`、`test_environment_drift_after_admission_releases_reservation_without_start` 及 `test_selected_production_identity_must_match_current_config` 之完整實作。

3. **嚴格事件解析、安全回放、Commit 狀態一致性與鎖分離（維持 R1 修復成果）**
   - **`strict_events` 與 `rejected_page_call`（`src/line_recovery.py:62-151`）**：拒絕重複 JSON key、非有限浮點數、未知 schema、非整數或倒退之 `step_index`（`type(index) is not int` 排除 `bool`），並強制每個含工具資訊之 step 必須恰好為 `ACTIVE` -> `DONE`/`ERROR` 兩筆更新且名稱與參數不可中途變更；精確驗證唯一一筆 ` view_file` 拒絕事件指向同 conversation 內先前已 `DONE` 之 `read_url_content` 產生的 `.system_generated/steps/<n>/content.md`。
   - **安全回放與同 ID 防護（`src/forecast.py:355-406`、`src/line_recovery.py:186`）**：`F.line_stopped()` 將 `covered_attempts[attempt_id]` 綁定至 `metadata_sha256`，僅豁免 `hash_obj(projection(r))` 完全一致的紀錄；`situation()` 亦拒絕任何重複 `attempt_id`。
   - **Commit 異常不誤記 `held`（`src/line_recovery.py:419-429`）**：若 `line_resume` 已寫入 `line_status.jsonl` 後才發生例外或逾時信號，會識別已提交的 `recovery_id` 並回傳 `status: 'resumed'` 與 `completion_journal: 'pending'`。
   - **健康線零鎖競爭（`src/line_recovery.py:368-371`）**：進入 `F.Lock` 前先以無鎖 `F.line_stopped()` 檢查，健康或 backoff 狀態直接返回。

---

### 非阻擋性建議與觀察（Non-Blockers / Proposals）

1. **`situation()` 與 `F.line_stopped()` 之 `covered` 集合表示法對齊（純程式碼清晰度觀察，非阻擋）**
   - 在 `src/forecast.py:362, 403` 中，`covered_attempts` 以 `{attempt_id: metadata_sha256}` 字典比對 `hash_obj(projection(r))`；而在 `src/line_recovery.py:209, 214` 中，`situation()` 的 `covered` 集合僅存 `attempt_id`。由於 `situation()` 第 186 行已強制檢查 `forecasts.jsonl` 無重複 `attempt_id`，且同函式第 184 行亦呼叫 `F.line_stopped()`，因此不會產生任何安全漏洞；未來若做一般維護，可考慮在 `situation()` 同樣以 `(attempt_id, metadata_sha256)` 判斷以保持兩處寫法完全對稱。
2. **部署後實機觀測門檻（操作程序提醒，非程式碼阻擋）**
   - 依 `docs/tasks/D140-AUTO-RECOVERY.md` 規範，待雙模型對 A/B 兩模組均通過審查並完成六鎖安全點部署後，仍須於 `data/operations/d140-auto-recovery-20261105/` 記錄自動 resume 及待補題之技術 metadata（`forecast_ok`／`comment_ok`），且不得檢視開放題之預測內容。

END-D140-CODE-R3A