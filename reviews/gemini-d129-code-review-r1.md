# D129 官方 CLI 審查證據：gemini r1

來源：本機 tools-disabled 官方 CLI；來源封包 SHA-256 `52353b40e78cfbfa4443091ff11d39fd496145a76b8aaafe8fa93597a8ead43c`。本文件保存審查者原文；執行測試與部署證據由主持人另列於 D129 文件。

# D128 實作審查報告（Implementation Review r1）

**審查裁定：`BLOCKING`**

> **審查聲明与邊界**：本報告僅依據所提供之 Source Packet 進行靜態設計與程式碼審查，未執行任何測試、未讀取外部檔案或網路資源，亦未動用任何預測設定或憑證。「401 項針對性合成測試通過」與既有基線回歸結果均視為 Host 提供之外部證據（非本審查員執行）。由於已見程式碼存在具體邏輯阻斷問題，且來源封包末端發生截斷（`<truncated 46195 bytes>`），依「不捏造核准（Do not invent approval）」原則裁定為 **`BLOCKING`**。

---

## 一、實際證據（Actual Evidence — 已於可見封包驗證之正確設計）

1. **版本升版與保護路徑不變性（`src/score.py`、`src/resolve.py`、`src/flab/itt_cohort.py`、`src/flab/itt_report.py`）**
   - `src/score.py` 已升版為 `VERSION = "score_v2"`（維持 `CDF_SCORE_VERSION = "cdf_score_v1"`），`validate_report_path` 與 `write_report_exclusive` 維持對 `data/`、`config/`、`src/`、`prompts/`、`tests/`、`launchd/`、`.git/` 及核心文件之唯讀保護與 `O_CREAT | O_EXCL` 獨佔寫入。
   - `src/resolve.py` 已升版為 `VERSION = "resolve_v2"`；`poll_resolutions` 回傳 `"status": "error" if errors else "ok"`，且 CLI `main()` 於 `result.get("errors")` 非空時回傳非零退出碼 `1`（修正 P4）。
   - `src/flab/itt_cohort.py` 定義 `VERSION = "live_itt_cohort_v1"`、`COHORT_ID = "d118_live_itt_v1"`、`CAMPAIGN_ID = "d118_minibench_20261103T0400Z"`、`BOT_ID = 311763`、半開決策視窗 `[2026-11-03T04:12:32.156Z, 2026-11-10T04:00:00.000Z)`（第 214 行 `not start <= decision < end`），與事前登記規格完全一致。

2. **D127 歷史全量診斷與官方計分完整保留（`src/score.py`）**
   - `evaluate_scoreboard()` 完整保留 D127 全歷史 `overall`、`by_config_segment`、`tier2_escalation_analysis`、`official_scores` 與 `historical_d52_scope: "all_history_d127"`，並未對輸入預測帳冊做時間切片以人為製造「僅含成功送出之 live 子集」。
   - `scorable_questions` 新增 `"tier1_aggregate_id": tier1_agg.get("record_id") if tier1_agg is not None else None` 以供 D128 比對根基準（baseline root），未破壞既有 4-way 成對比較邏輯。

3. **Metadata-Only 白名單雜湊與未持久化首次候選決策之保守揭露（`src/flab/itt_cohort.py`）**
   - `_meta(row)` 僅抽取 `_COMMON`、`escalation`（不含預測機率或理由）與 `campaign` 欄位計算 `source_sha256`（`source_hash_scope: "whitelisted_metadata_only"`），不讀取預測機率、評論正文、結算結果或 `submissions.jsonl` 狀態。
   - **符合已知架構限制要求**：`select_cohort()` 固定輸出 `"decision_capture_complete": False`、`"capture_limitations": ["first_candidate_decisions_not_durably_logged"]` 及 `"complete_membership": False`（第 273–275 行），並在 `itt_report.py` 第 105–106 行於 `cohort.get("decision_capture_complete") is not True` 時強制寫入 `global_gaps["decision_capture_incomplete"] = 1`，不假裝已解決排程器記憶體內首次 candidate 未落盤之問題。

4. **同來源鏈去重與等效 Tier 1 基準別名（`src/flab/itt_cohort.py`）**
   - 第 128–159 行以 `(question_id, question_snapshot_id)` 及共享 Tier 1 葉節點（`bool(x["leaves"] & c["leaves"])`）合併同來源鏈之重跑／修復候選聚合，取最早決策為 `root`，並透過 `equivalent_baseline` 嚴格檢查 `aggregation_version`、`aggregation_config_sha256`、`implementation_version` 與 `sorted(input_attempt_ids)` 一致時才納入 `tier1_aggregate_ids`（否則僅列為 `related_tier1_aggregate_ids`），設計嚴謹。

---

## 二、阻斷性問題（Blockers — 必須解決事項）

### (A) 已見程式碼中之具體邏輯阻斷項（Concrete Code Blockers）

#### Blocker 1：`itt_cohort.py` 在時間視窗檢查前先對 `scheduler_jobs` 標記 `conflicting_job_campaign`，導致視窗外（試驗期）episode 被誤判為 `unknown` 而非 `excluded`
- **位置**：`src/flab/itt_cohort.py` 第 184–196 行 vs 第 206–215 行。
- **問題機制**：
  - 在第 184–196 行處理 `sources["scheduler_jobs"]` 時，只要 `len(found) == 1` 且 `job.get("campaign_id") != CAMPAIGN_ID`，程式立即對該 episode 加入錯誤：
    ```python
    if job.get("campaign_id") != CAMPAIGN_ID:
        found[0]["_errors"].add("conflicting_job_campaign")
    ```
  - 隨後在第 212–215 行進行狀態分類時：
    ```python
    if errors:
        ep["reasons"] = sorted(errors)
    elif not start <= decision < end:
        ep.update(status="excluded", reasons=["before_live_start" if decision < start else "after_cohort_end"])
    ```
  - 對照第 247–256 行，`scheduler_runs`（`conflicting_campaign_or_mode`）與 `budget_events`（`conflicting_budget_campaign`）的 campaign 檢查皆放在 `elif not start <= decision < end:` **之後**的 `else:` 分支內，因為視窗外（`decision < start` 試驗期或 `decision >= end`）的歷史 episode 本來就不屬於 `d118_minibench_20261103T0400Z`，應判定為 `status="excluded"`。
  - 但 `scheduler_jobs` 的 `job.get("campaign_id") != CAMPAIGN_ID` 卻在時間視窗判斷**之前**就寫入 `ep["_errors"]`，導致任何在 `START` 之前（試驗期）曾產生 `scheduler_job_finished`（`tier == 2`，且 `campaign_id` 為 `None` 或其他試驗 campaign）的歷史 episode 進入 `if errors:` 分支，被錯誤標為 `status="unknown", reasons=["conflicting_job_campaign"]`！
  - 這會直接污染 `counts["unknown"]`、`observed_membership_complete` 以及 `itt_report.py` 的 `global_gaps["unknown_cohort_membership"]`。
- **修正要求**：
  - 將 `job.get("campaign_id") != CAMPAIGN_ID`（`conflicting_job_campaign`）移至 `else:`（即確認 `start <= decision < end` 之後）再檢查，與 `scheduler_runs` 及 `budget_events` 保持一致。
  - 此外，在第 188–196 行若 `len(found) > 1`（同一個 `scheduler_job` 匹配多個 episode），應對 `found` 中各 episode 標記 `_errors.add("ambiguous_scheduler_job_root")`，而非僅落入 `missing_job_roots`。

#### Blocker 2：`itt_cohort.py` 對 `runs` 中缺失、重複衝突（`bad_run`）或時間無效之 `scheduler_runs` 執行 `continue` 靜默跳過
- **位置**：`src/flab/itt_cohort.py` 第 242–249 行。
- **問題機制**：
  ```python
  live_runs = []
  for rid in runs:
      r = by_run.get(rid)
      if r is None or rid in bad_run or _row_time(r) is None:
          continue
      if r.get("campaign_id") != CAMPAIGN_ID or r.get("mode") != "live" or r.get("continuous") is not True:
          errors.add("conflicting_campaign_or_mode")
      else:
          live_runs.append(r)
  ```
  - 若某個視窗內 episode 關聯到兩個 `run_id`（例如初始決策 `rid_1` 與後續重試／子工作 `rid_2`）：當 `rid_1` 為合法 live run 且涵蓋 `decision` 時，若 `rid_2` 在 `scheduler_runs.jsonl` 中存在雜湊衝突（`rid_2 in bad_run`）、時間欄位損毀（`_row_time(r) is None`）或紀錄缺失（`r is None`），第 245 行會直接 `continue` 靜默忽略 `rid_2`，使該 episode 仍被判為 `status="included"`。
  - 這違反事前登記規格 §固定母體第 4、5 點（「缺證據列歸屬不明，不能靜默排除」、「缺根、時間不明或互相衝突列歸屬不明」）。
- **修正要求**：
  - 當 `r is None or rid in bad_run or _row_time(r) is None` 時，不可無條件靜默跳過；若 `rid in bad_run` 或 `_row_time(r) is None`（或 `rid` 來自 `decision_links` 卻 `r is None`），應加入明確錯誤（例如 `errors.add("missing_or_conflicting_scheduler_run")`）。
  - 第 253–254 行檢查 `live_runs` 時間區間時，建議與第 232–233 行一致支援 `r.get("started_at_utc", r.get("started_at"))` 與 `r.get("finished_at_utc", r.get("finished_at"))`。

#### Blocker 3：`itt_cohort.py` 未對孤立或多重匹配之 `reconciliation_runs` 建立 `unknown` 診斷或歧義標記
- **位置**：`src/flab/itt_cohort.py` 第 184–205 行 vs 第 224–227 行。
- **問題機制**：
  - `itt_cohort.py` 對無根的 Tier 2 `forecasts`（第 173–181 行）與無根的 Tier 2 `scheduler_jobs`（第 184–205 行）皆會產生 `status="unknown"` 的診斷 episode。
  - 但對於 `sources["reconciliation_runs"]`（`type == "reconciliation_run"`），程式僅在 `for ep in episodes:` 的 `else:` 分支內（第 224–227 行）查找 `r.get("tier1_aggregate_id") in ep["related_tier1_aggregate_ids"]`。
  - 若某筆 `reconciliation_run` 因根決策未落盤或 `tier1_aggregate_id` 無法唯一對應至單一 `ep`（`len(found) == 0` 或 `len(found) > 1`），且尚未產生 Tier 2 `forecast_attempt` 或 `scheduler_job_finished`，該筆 `reconciliation_run` 將被完全靜默忽略，不會產生 `unknown` episode 或歧義錯誤。
- **修正要求**：
  - 比照 `scheduler_jobs`，對 `sources["reconciliation_runs"]`（`type == "reconciliation_run"`）進行歸屬檢查：若 `len(found) == 0` 應保留於 unknown 診斷 episode（如 `reconciliation_run_without_root`），若 `len(found) > 1` 應對相關 episode 標記歧義錯誤；且若視窗內 episode 關聯之 `reconciliation_run` 帶有衝突之 `campaign_id`（若有該欄位且不為 `None` 或 `CAMPAIGN_ID`），亦應 fail closed。

#### Blocker 4：`score.py` 將預先過濾後之 `submissions`、`forecasts`、`subagents` 傳入 `build_live_itt`，導致缺時間或時間格式損毀之紀錄在進入 D128 報告前被靜默丟棄
- **位置**：`src/score.py` 第 424–436 行、第 1150–1156 行；`src/flab/itt_report.py` 第 67、94–99 行。
- **問題機制**：
  - `score.py` 呼叫 `select_cohort` 時傳入未過濾之 `raw_questions`、`raw_forecasts`、`raw_aggregates`（因此 `select_cohort` 可保留 `_row_time(r) is None` 的紀錄並標記錯誤）。
  - 但 `score.py` 呼叫 `build_live_itt` 時（第 1150–1156 行），傳入的卻是已經被 `_record_before_as_of` 過濾後的 `submissions`、`forecasts`、`aggregates`、`subagents`！
  - 這造成兩個問題：
    1. 若某個已納入 live 母體的 episode 關聯之 `subagent`、`forecast_attempt` 或 `submission` 缺少時間戳或時間戳格式錯誤，`score.py` 第 425–436 行會在呼叫 `build_live_itt` 前將其靜默剔除，`build_live_itt` 完全不會得知該筆 `subagent`／`attempt`／`submission` 存在，進而漏算資源嘗試數或漏標成本缺值／衝突！
    2. `score.py` 第 436 行對 `subagents` 使用 `analysis_dt` 與 `("finished_at_utc", "started_at_utc", "created_at_utc", "at", "observed_at_utc")` 過濾，而 `itt_report.py` 第 98 行又用 `cutoff`（`as_of`）與 `("started_at_utc", "finished_at_utc", "created_at_utc", "at")`（不含 `"observed_at_utc"`）二次過濾。
- **修正要求**：
  - 傳入 `raw_submissions`、`raw_forecasts`、`raw_aggregates`、`raw_subagents` 至 `build_live_itt`（或在 `build_live_itt` 中顯式檢查與 live episode 關聯之紀錄是否存在無效／缺失時間戳或 ID 衝突），確保缺時間或損毀之 `subagents`／`forecasts`／`submissions` 觸發 fail-closed 缺漏／成本不完整標記，而非在 `score.py` 前置過濾中靜默消失。

---

### (B) 來源封包截斷導致無法驗證之審查阻斷項（Packet Truncation Blocker）

#### Blocker 5：Source Packet 於 `src/flab/itt_report.py` 第 158 行處截斷（`<truncated 46195 bytes>`），缺失 `itt_report.py` 後半部與 `src/flab/submission_identity.py`
- **位置**：Source Packet 結尾 `reports.append({"episode_i` 後方之 `<truncated 46195 bytes>`。
- **影響範圍與待驗證項目**：
  1. **`src/flab/submission_identity.py` 全文未見，且與 `src/resolve.py` 第 344–367 行存在介面契約風險**：
     - 在 `src/resolve.py` 第 344–346 行與第 367 行中：
       ```python
       identity = submission_identity(r, base)
       submitted_snap_by_qid[qid] = identity["question_snapshot_id"]
       submitted_mode_by_qid[qid] = identity["mode"]
       ...
       if is_snapshotless_probe({"question_snapshot_id": sub_snap_id, "mode": submitted_mode_by_qid.get(qid)}):
       ```
     - 注意 `resolve.py` 並未將完整的 `identity` dict 傳給 `is_snapshotless_probe`，而是只重建了包含 `"question_snapshot_id"` 與 `"mode"` 兩個鍵的字典。
     - 若 `submission_identity(r, base)` 在遇到 `mode="local"` 但 `r` 與 `base` 的 `question_snapshot_id` **互相衝突**（例如 `base` 為 `"snap_1"`、`r` 為 `"snap_2"`）或格式錯誤時，將 `identity["question_snapshot_id"]` 設為 `None` 並保留 `identity["mode"] = "local"`（僅透過其他欄位如 `identity["conflict"] = True` 標示衝突），則 `resolve.py` 重建的雙鍵字典 `{"question_snapshot_id": None, "mode": "local"}` 會被誤判為合法的 `is_snapshotless_probe` 而靜默跳過（`submitted_without_snapshot_id`），違反 P1/P2「malformed/conflicting mode/snapshot must fail closed」！
     - 必須提供 `src/flab/submission_identity.py` 全文以驗證其返回值設計，並建議 `resolve.py` 直接保存並傳遞完整 `identity` dict 至 `is_snapshotless_probe(identity)`。
  2. **`src/flab/itt_report.py` 第 158 行之後半部未見**：
     - **同題多 episode 之去重與缺漏抑制**：在可見的第 103、144–155 行中：
       ```python
       repeated_questions = {q for q, n in Counter(e.get("question_id") for e in included).items() if n > 1}
       ...
       if qid in repeated_questions:
           gaps.append("multiple_episode_question")
       ...
       if not gaps:
           observations[ep_id] = row
       ```
       當同一 `question_id` 有多個 included episodes 時，每個 episode 都會被加上 `"multiple_episode_question"` gap，導致 `not gaps` 為 `False`，所有該題的 episode 都不會進入 `observations`！必須檢視第 158 行之後如何由 `observations`／`reports` 計算 `paired_question_count`、`by_config_and_type`、題目級去重與群聚 bootstrap。
     - **資源與子代理成本去重及缺值處理**：需檢視第 158 行之後對 Tier 2 attempts 與 `subagents` 的成本加總是否嚴格去重（不重複灌入共享來源），以及當任一 attempt／subagent 缺成本時是否將總成本標為 `None`（絕不補 0 或 1）。
     - **完整 ITT 結論閘門（Gating）**：需檢視第 158 行之後是否確保當 `decision_capture_complete=False` 或 `global_gaps` 非空時，所有分組的 `can_claim_superiority` 均強制為 `False`，且 `total_log_score_gain_nats`、`log_score_gain_per_tier2_attempt`、`log_score_gain_per_budget_unit` 均強制為 `None`（僅保留觀測成對子集之描述性均值與信賴區間）。

---

## 三、非阻斷性建議與加固提案（Proposals / Optional Suggestions）

1. **`src/score.py` 第 1138 行 `cohort_ledger(name)` 檔案存在性防護**
   - `evaluate_scoreboard()` 第 410–421 行讀取帳冊時皆帶有 `if (data_dir / "...").exists() else []`，但第 1138 行 `cohort_ledger(name)` 直接呼叫 `read_complete(data_dir / name)`。若 `read_complete` 對不存在的檔案會拋出 `FileNotFoundError`（或為求風格一致），建議改為 `read_complete(data_dir / name) if (data_dir / name).exists() else []`。
2. **`src/flab/itt_cohort.py` 快照時間欄位納入 `"t0"`**
   - `score.py` 第 424 行對 `questions.jsonl` 檢查 `("seen_at", "t0", "created_at_utc")`，而 `itt_cohort.py` 的 `_COMMON` 與 `_row_time` 僅包含 `"seen_at"` 與 `"created_at_utc"`（未含 `"t0"`）。建議將 `"t0"` 加入 `_COMMON` 與 `_row_time`，避免僅含 `"t0"` 的快照被誤判為 `invalid_snapshot_time`。
3. **`src/flab/itt_report.py` 第 69、72 行狀態欄位讀取一致性**
   - `r.get("status", r.get("state"))` 在 `r` 含有 `"status": None` 且 `"state": "intent"` 時會回傳 `None`（因為 key `"status"` 存在）。建議改用與 `score.py`／`resolve.py` `_status(r)` 一致的 `(r.get("status") or r.get("state"))`。
4. **`src/score.py` 第 498 行與 `src/resolve.py` 第 339 行之 `question_id`／`aggregate_id` 繼承衝突檢查**
   - 在 `score.py` 與 `resolve.py` 合併 `base`（intent）與 `row`（completion）時，若 `base` 與 `row` 同時帶有不同之 `question_id` 或 `aggregate_id`，應確保 `submission_identity(row, base)` 將該筆送出標為衝突並使驗證 fail closed，不會靜默以 `base.get("aggregate_id") or row.get("aggregate_id")` 覆蓋。

---

## 四、審查結論摘要

- **當前狀態**：`BLOCKING`
- **下一步行動（Action Items for r2）**：
  1. 修正上述 **Blocker 1 ～ Blocker 4**（`itt_cohort.py` 視窗外 `scheduler_jobs` campaign 順序、`scheduler_runs` 靜默 `continue`、`reconciliation_runs` 孤立／歧義歸屬，以及 `score.py` 傳入 `build_live_itt` 之未過濾原始帳冊與時間檢核）。
  2. 於下一輪審查封包完整附上被截斷之 `src/flab/itt_report.py` 後半部與 `src/flab/submission_identity.py` 全文（**Blocker 5**），並確認 `resolve.py` 對 `is_snapshotless_probe` 傳遞完整 `identity` 資訊。
