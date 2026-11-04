# D129 官方 CLI 審查證據：gemini r2

來源：本機 tools-disabled 官方 CLI；來源封包 SHA-256 `3f4e0edd4e775c31e76091d16164936c25f534b8a17a891dadd498bd7ba4fe56`。本文件保存審查者原文；執行測試與部署證據由主持人另列於 D129 文件。

# D128 實作 r2（部署批次 D129）獨立設計與程式碼審查報告

## 1. 審查結論（Verdict）

- **結論：`PASS`（0 Blockers）**
- 本次 `D128` r2 實作（對應部署批次 `D129`，含 `score_v2`、`resolve_v2`、`live_itt_cohort_v1`、`live_itt_report_v1`、`submission_identity_v1`）已完整修復 r1 階段的所有阻斷問題（ClaudeB1～B3、GeminiB1～B4）與相容性建議（ClaudeS1、ClaudeS3），並嚴格符合 D128 事前登記規格與 PLAN.md §4.1–§4.4、§5.1 之所有不變量要求。
- 審查過程僅依賴所提供之來源封包文字作為資料分析，未執行任何指令或測試、未存取外部檔案或網路、亦未檢視任何預測內容或結算結果。

---

## 2. 封包與驗證證據接收確認（Receipt Confirmation）

1. **結尾標記與主機驗證證據確認**：
   - 確認已完整收到 `## Host r2 validation evidence` JSON 區塊：
     ```json
     {
       "baseline_failed": 100,
       "current_failed": 100,
       "added": [],
       "removed": [],
       "passed": 3091,
       "targeted_passed": 446,
       "protected_manifest_entries_matching": 28,
       "protected_distinct_paths": 17
     }
     ```
   - 確認已收到封包結尾標記 `END_OF_PACKET_D128_R2`（無截斷）。

2. **完整收到之新增模組與測試檔案清單（Fully Received New Modules & Tests）**：
   - `src/flab/itt_cohort.py`（完整模組，`live_itt_cohort_v1`）
   - `src/flab/itt_report.py`（完整模組，`live_itt_report_v1`）
   - `src/flab/submission_identity.py`（完整模組，`submission_identity_v1`）
   - `tests/test_itt_cohort.py`（完整測試檔）
   - `tests/test_itt_report.py`（完整測試檔）
   - `tests/test_score_live_itt.py`（完整測試檔）
   - `tests/test_submission_identity.py`（完整測試檔）

3. **收到完整變更函式與 diff 之既有檔案**：
   - `src/score.py`（deployed-D127 至 candidate-D129 diff 及 `_build_synchronized_draws`、`_ci_from_synchronized_draws`、`pairwise_conclusion_discipline`、`render_scoreboard_markdown`、`_no_claim`、`_gate_tier2_pairwise`、`_build_d52_escalation_accounting` 完整函式）
   - `src/resolve.py`（deployed-D127 至 candidate-D129 diff 及 `_select_candidates_with_diagnostics`、`candidate_questions`、`main` 完整函式）
   - `tests/test_resolve.py`（diff 與新增測試）
   - `tests/test_score.py`（diff 與新增測試）

---

## 3. 實際證據核對（Actual Evidence）

### 3.1 R1 阻斷問題與建議修正核對

1. **ClaudeB1 / GeminiB2（證據鏈 `scheduler_runs` 失敗關閉與跨起點祖先檢查）**：
   - **程式碼證據（`src/flab/itt_cohort.py`）**：
     - 在納入區間 `start <= decision < end` 內，針對 `ep["tier1_attempt_ids"]` 逐一檢查 `began = _dt(attempt.get("started_at_utc", attempt.get("started_at"))) or _row_time(attempt)`；若 `began < start`，加入 `"ancestry_predates_live_start"` 錯誤並將 episode 歸入 `unknown`，防止試驗期已啟動之 T1 嘗試因晚到聚合而混入正式 live 母體。
     - 針對 episode 關聯之所有 `runs`（含 `decision_links`、T1/T2 `attempt` 之 `run_id`、`budget_events` 之 `run_id`），逐一強制驗證：缺紀錄標記 `"unresolved_evidence_run"`、`run_id` 衝突標記 `"conflicting_run_metadata"`、缺起訖時間或 `hi < lo` 標記 `"undated_evidence_run"`、`campaign_id != CAMPAIGN_ID` 或 `mode != "live"` 或 `continuous is not True` 標記 `"conflicting_campaign_or_mode"`。任何一筆關聯 run 異常皆會使 `errors` 非空並將該 episode 判為 `unknown`。
   - **測試證據（`tests/test_itt_cohort.py`）**：`test_all_linked_evidence_runs_fail_closed_despite_valid_live_decision` 與 `test_live_recorded_root_with_pre_live_ancestry_is_unknown` 涵蓋上述所有情境。

2. **GeminiB1（時間窗排除優先於 job campaign 檢查；歧義 job/reconciliation 標記所有匹配根）**：
   - **程式碼證據（`src/flab/itt_cohort.py`）**：
     - `not start <= decision < end` 判斷位於 `else:`（live 證據與 campaign 驗證）之前；因此決策時間確證早於 `START` 的歷史 episode 即使帶有舊 trial `campaign_id` 的 `scheduler_job_finished`，仍直接歸類為 `excluded`（`before_live_start`），不會誤變為 `unknown`。
     - 當 `scheduler_jobs` 或 `reconciliation_runs` 匹配到多個根（`len(found) > 1`）時，迴圈 `for ep in found: ep["_errors"].add(ambiguous_reason)` 會將所有匹配到的根 episode 皆標記 `"ambiguous_scheduler_job_root"` 或 `"ambiguous_reconciliation_root"`。
   - **測試證據（`tests/test_itt_cohort.py`）**：`test_historical_job_campaign_does_not_override_proven_exclusion` 與 `test_ambiguous_jobs_and_reconciliation_mark_every_matching_root`。

3. **GeminiB3（孤兒／歧義 `reconciliation_runs` 與 `scheduler_jobs` 診斷保留、共享來源去重、campaign 衝突檢查）**：
   - **程式碼證據（`src/flab/itt_cohort.py`）**：
     - 將 `scheduler_jobs` 與 `reconciliation_runs` 統一納入無根／歧義根診斷產生流程，當 `len(found) != 1` 時建立 `root_aggregate_id: None` 的 `unknown` 診斷 episode。
     - 透過 `provenance(row)` 提取明確共享憑證（`attempt_id`、`prev_attempt_id`、`selected_attempt_id`、`reconciliation_id`、`reconciliation_run_id`、`tier1_aggregate_id`、T2 追溯之 T1 `leaf`、或無根 job 之 `run_id`），在 `diagnostic_groups` 中僅對同 `(question_id, snapshot_id)` 且具交集 provenance token 的無根診斷進行合併去重，絕不單憑題號合併。
     - 對 `reconciliation_run` 檢查 `link.get("campaign_id") not in (None, CAMPAIGN_ID)`，若衝突則標記 `"conflicting_reconciliation_campaign"`。
   - **測試證據（`tests/test_itt_cohort.py`）**：`test_orphan_reconciliation_without_forecast_or_job_is_unknown`、`test_orphan_job_reconciliation_and_attempt_share_one_provenance_diagnostic`、`test_conflicting_live_reconciliation_campaign_is_unknown`。

4. **ClaudeB2（Markdown 報告明確呈現半開區間、雙時間切點、報告版本、interim 狀態、描述性子集與分母、gated tier）**：
   - **程式碼證據（`src/score.py`）**：
     - `_no_claim` 將被阻斷優越性宣稱之 `conclusion_discipline["tier"]` 設為 `"descriptive_only"`。
     - `render_scoreboard_markdown` 第 1 節標題改為 `## 1. Historical 4-Way Series & Pairwise Summary (D127)`；第 3 節 `## 3. Registered Live ITT (D128)` 明確印出 `Report version`（`live_itt_report_v1`）、`cohort as-of`、`analysis cutoff`、半開決策區間 ``[`2026-11-03T04:12:32.156Z`, `2026-11-10T04:00:00.000Z`)``、視窗狀態 `interim (window still open)` 或 `closed for new episodes`、`Observed-pair subset (descriptive only; NOT full ITT)`、以及各分段 `Paired questions / included episodes in segment`。
   - **測試證據（`tests/test_score.py`）**：`test_d129_markdown_shows_fixed_interval_cutoffs_and_subset_label`。

5. **ClaudeB3（歷史 D52 永久包含 `decision_capture_incomplete` 並阻斷完整 ITT／效率宣稱，保留描述性統計與非 Tier 2 比較）**：
   - **程式碼證據（`src/score.py`）**：
     - `_build_d52_escalation_accounting` 的 `itt_gaps` 固定放入 `"decision_capture_incomplete": 1`，因此歷史 D52 的 `itt_complete` 恆為 `False`、`tier2_efficiency_status` 在有升級 episode 時恆為 `"incomplete_itt"`、`total_log_score_gain_nats` / `log_score_gain_per_tier2_attempt` / `log_score_gain_per_budget_unit` 恆為 `None`，而 `evaluatable_subset_log_score_gain_nats` 與 `observed_total_log_score_gain_nats` 仍保留觀測值。
     - `_gate_tier2_pairwise` 針對 `final_vs_tier1_aggregate` 與 `final_vs_player_tier2:*` 將整體及各指標之 `conclusion_discipline` 設為 `can_claim_superiority: False`、`tier: "descriptive_only"`，但不影響 `final_vs_player_tier1:*` 等一般基準比較。
   - **測試證據（`tests/test_score.py`）**：`test_d129_capture_gap_blocks_historical_claims_even_with_100_complete_pairs` 以 100 題完整配對合成資料同時驗證 Tier 2 比較被阻斷且 `final_vs_player_tier1:claude` 正向控制組仍通過 `can_claim_superiority is True`。

6. **GeminiB4（`score.py` 傳遞原始未過濾證據至 `build_live_itt`；僅排除確證未來列，保留無效時間列以標記缺口與成本不完整）**：
   - **程式碼證據（`src/score.py`、`src/flab/itt_report.py`）**：
     - `score.py` 呼叫 `build_live_itt` 時傳入 `raw_submissions`、`raw_forecasts`、`raw_aggregates`、`resolutions`、`raw_subagents`。
     - `itt_report.py` 的 `_time_state` 與 `_retained` 僅過濾 `"future"`（確證 `> cutoff`）紀錄，將 `"invalid"`（缺時間或格式損毀）紀錄保留下來，並在對應 episode 上標記 `invalid_attempt_time`、`invalid_aggregate_time`、`invalid_subagent_time`、`invalid_resolution_time`、`invalid_submission_time`，同時在 `resources` 中累加 `invalid_time_record_count` 並令 `budget_cost_complete = False`、`budget_units = None`。
   - **測試證據（`tests/test_itt_report.py`、`tests/test_score_live_itt.py`）**：`test_invalid_time_evidence_survives_and_blocks_episode_pair` 與 `test_raw_invalid_time_evidence_reaches_live_report`。

7. **ClaudeS1 / ClaudeS3（共用 `submission_identity` 與 `resolve.py --dry-candidates` 診斷強化）**：
   - **程式碼證據（`src/flab/submission_identity.py`、`src/resolve.py`、`src/score.py`、`src/flab/itt_report.py`）**：
     - 統一由 `submission_status`、`submission_identity`、`is_snapshotless_probe` 處理狀態與身分繼承：僅當 `question_snapshot_id` 為空且 `mode` 明確為 `"local"` 或 `"trial"` 時才視為無快照歷史探針；大小寫錯誤、空白、未知 mode、非字串 snapshot、或 intent 與 receipt 之間的 mode/snapshot 衝突皆失敗關閉（返回 `{"question_snapshot_id": None, "mode": None}`，不會被當作探針跳過）。
     - `resolve.py` 升級為 `resolve_v2`，`--dry-candidates` 不建立 API client 亦不寫檔，完整輸出 `status`、`errors`、`skipped`、`candidates`，並在有 `errors` 時以 exit code `1` 結束。
     - `itt_report.py` 明確標示 `resources["scope"] = "recorded_linked_attempts_only"`、新增 `unknown_resources`，並在混用 `budget_cost` 與 `quota_weight` 時標記 `mixed_cost_fields = True` 及 `budget_units = None`。
   - **測試證據**：`tests/test_submission_identity.py` 與 `tests/test_resolve.py` 完整覆蓋。

### 3.2 主機驗證與真實帳冊 Metadata 檢查證據

- **真實帳冊 metadata-only 檢查（Host metadata-only real ledger check）**：
  - 於 `2026-11-04T12:55:36.867Z` 對現有帳冊（95 snapshots、218 forecasts、213 aggregates、482 budget_events、167 scheduler_runs、216 reconciliation_runs、275 scheduler_jobs、2 campaigns、2 live_authorizations）進行純 metadata 篩選：`included: 3`、`excluded: 32`（全數為 `before_live_start: 32`）、`unknown: 0`、`decision_capture_complete: false`、`no_outcomes_loaded: true`。
- **主機測試回歸證據（Host r2 validation evidence）**：
  - `baseline_failed: 100`、`current_failed: 100`、`added: []`、`removed: []`（基線失敗節點完全一致，無新增回歸失敗）。
  - `passed: 3091`、`targeted_passed: 446`。
  - 受保護路徑與 manifest 檢查：`protected_manifest_entries_matching: 28`、`protected_distinct_paths: 17`。

---

## 4. 阻斷問題（Blockers）

- **無（None）**。

---

## 5. 非阻斷建議與後續追蹤事項（Proposals & Accepted Diagnostic Limitations）

以下項目皆屬規格已明確揭露並接受之診斷範圍限制或後續批次（Follow-up）事項，不構成 D129 部署阻斷：

1. **執行期首次 candidate 決策持久化紀錄（Runtime Decision Capture）**：
   - 現行執行期尚未將每一次首次觸發的 candidate 決策全部持久化寫入帳冊，因此 `select_cohort` 固定輸出 `decision_capture_complete: False`，且 `score.py` 與 `itt_report.py` 皆據此強制加上 `decision_capture_incomplete` 缺口以阻斷任何完整 ITT 優越性與效率結論。未來若需解除此永久限制，需在後續獲准的新 live 窗口開始前改善 runtime 帳冊記錄並另行登記新 cohort。
2. **同題跨次提交與 episode 綁定（Episode-to-Submission Binding）**：
   - `build_live_itt` 重用 `score.py` 已驗證之每題最新有效提交列（`scorable_questions`）；若同一題目在某個註冊 episode 之後又發生新的提交覆蓋，該 episode 將因匹配不到對應 `submitted_aggregate_id` 而明確標記為 `missing_validated_pair` 缺口（不補 0、不靜默排除），符合保守不變量。
3. **更廣泛的提交收據欄位衝突防護（Extended Receipt Conflict Hardening）**：
   - 目前 `submission_identity.py` 與 `itt_report.py` 已針對 `mode`、`question_snapshot_id`、`question_id`、`aggregate_id`、`bot_id` 實作衝突失敗關閉；主分板 `score.py` 對舊帳冊收據其他欄位的進一步統一強化可依計畫留待後續相容性小修批次處理。
