# Forecast Lab M4 第二輪（r2）最終程式碼與設計複審報告（`src/resolve.py`、`src/score.py`）

## 總體複審結論：**PASS**（無剩餘 Blocking 缺陷）

* **審查聲明**：本複審僅根據封包提供之原始碼、diff、檔案摘錄與測試證據進行靜態審查，未執行任何程式或讀取任何開放題預測資料；本結論僅確認技術與設計層面已無阻擋上線之缺陷（No Blockers），**不代表替任何角色核准上線**。
* **公式判定沿用**：本地計分公式（是非題、多選題、`cdf_score_v1` 連續／離散題對數分數與分段線性單位尺度 CRPS/RPS 積分）於 r2 未作變更，沿用 r1 判定（數學推導與實作正確）。

---

## 一、r1 Blocker（B1–B5）與 C 類議題（C1–C6）逐項複審判定

### 1. B1（PLAN §4.3 D52 ITT 完整性紀律與 Tier 2 預算成本缺值處理）— **已修好**
* **實際證據**：
  1. **移除 `0.0` 補值與未送出基線推算**：`src/score.py` 已完整刪除 `_score_unsubmitted_tier1_baseline`；於 `_build_d52_escalation_accounting` 中（diff 行 2058–2067），凡未送出之升級 episode（`q not in submitted_intent_by_qid`），除平台結算為 `ambiguous` 或 `annulled` 依計畫歸入 `excluded_by_plan` 外，一律歸入 `missing_outcome`（原因固定為 `"unsubmitted_escalation_no_outcome_source"`），不再塞入 `0.0` 至 `itt_unit_diffs`。
  2. **ITT 結論紀律閘門**：`itt_gaps` 同時檢查 `missing_outcome`、`not_yet_evaluable`、`escalated_unsubmitted`、`fallback_zero` 四項計數（diff 行 2194–2216）；任一項 `> 0` 時，`complete_itt_coverage` 為 `False`，且強制覆寫 `superiority_assessment_eligible = False`、`can_claim_superiority = False`、`superiority_claim = "none_incomplete_itt_outcomes"`，並於 `itt_incomplete_reasons` 揭示缺口明細。
  3. **預算成本缺值不預設為 `1.0`**：`_extract_attempt_budget_cost`（diff 行 1899–1905）在無有限非負 `budget_cost` 或 `quota_weight` 時回傳 `None`（並以 `type(val) in (int, float)` 排除 `bool`）；`_sum_budget_costs`（diff 行 1907–1912）只要存在任一筆 `None`，即回傳 `(None, missing_n)`，使 `tier2_budget_units_consumed`、`foregone_tier1_question_equivalents` 與 `log_score_gain_per_budget_unit` 皆為 `None`，並公開 `tier2_budget_cost_missing_count` 與 `itt_tier2_budget_cost_missing_count`。
  4. **回歸測試驗證**：`test_b1_unsubmitted_escalation_blocks_itt_superiority_and_budget_cost_is_never_defaulted` 已驗證未送出升級題阻擋 ITT 宣稱，且 6 筆無成本欄位之 Tier 2 嘗試使預算總額為 `None`。

### 2. B2（Pairwise `final_vs_tier1_aggregate` 母體限縮於升級題與樣本數一致）— **已修好**
* **實際證據**：
  1. `src/score.py` 於成對比較迴圈（diff 行 2324–2348）將 `is_t2` 定義改為 `comp.startswith("player_tier2:") or comp == "tier1_aggregate"`，`pool_indices` 僅納入 `r["escalated"]` 為真之列，消除未升級列因 `final == tier1` 灌入結構性 `0` 差值而稀釋均值與壓窄信賴區間的問題。
  2. `effective_paired_count = len(paired_indices)` 與實際進入 `diffs_by_row` 及同步 bootstrap 重抽之配對題數完全一致。
  3. **回歸測試驗證**：`test_b2_final_vs_tier1_compares_only_escalated_and_b5_unadopted_escalation_never_flips_row` 已驗證 6 題未升級 + 2 題升級時，`final_vs_tier1_aggregate` 的 `eligible_questions == 2`、`paired_count == 2`，均值僅由 2 題升級題決定，而 `final_vs_player_tier1:claude` 仍維持 8 題。

### 3. B3（官方排行榜驗證契約與停用人數推算）— **已修好**
* **實際證據**：
  1. `src/resolve.py` 的 `_build_client_ranking_fetcher`（行 472–523）改為接收 `(tournament_id, expected_user_id)`，固定查詢 `leaderboards/project/{int(tournament_id)}/?primary_only=true`。
  2. 嚴格驗證回應為 `list`、篩出 `project_id == tournament_id` 且 `is_primary_leaderboard is True` 的唯一排行榜（`len(boards) == 1`），並要求 `board["userEntry"]["user"]["id"] == expected_user_id`（型別皆須為 `int`）且 `rank` 為 `>= 1` 的正整數。
  3. 完全不讀取其他參賽者 entries，亦不推算或記錄參賽總人數；`expected_user_id` 由 `_submitted_bot_ids_by_tournament` 從該賽事 `forecast_ok` / `comment_ok` 列提取，若非唯一正整數則拒絕抓取並記錄 `bot_identity_unavailable_or_ambiguous`。

### 4. B4（`ranking_observation` 獨立事件、截止篩選與賽事級別呈現）— **已修好**
* **實際證據**：
  1. `src/resolve.py` 已從 `resolution_event` 與 `fingerprint_payload` 移除 `official_ranking_data`，並新增 `official_score_first_observed_at_utc`（行 298–351），在官方分數未變但其他結算欄位更新時保留首次觀測時間。
  2. 賽事排名於 `poll_resolutions` 末段獨立寫入 `type="ranking_observation"`（行 359–419），每次執行每賽事最多一筆，且僅在 `hash_obj({"tournament_id": tour_id, "ranking": ranking})` 與該賽事最新 `ranking_observation.state_sha256` 不同時追加，並附 `supersedes_ranking_observation_id`。
  3. `src/score.py`（diff 行 2533–2575）只讀取 `type == "ranking_observation"` 且 `observed_at_utc <= as_of_dt` 之紀錄，並再次驗證 `ranking_verified is True`、`rank` 與 `matched_user_id` 為 `int`、且該賽事已送出列之 `bot_id` 集合恰好等於 `{matched_user_id}`；不符者一律忽略並累計至 `unverified_ranking_observations`。同時已移除跨賽事 `official_ranking` 與題目層級 `official_question_rankings`。
  4. **回歸測試驗證**：`test_b3_b4_rankings_come_only_from_verified_per_tournament_observations` 已涵蓋截止時間過濾、錯誤 `matched_user_id` 拒絕、缺 `ranking_verified` 拒絕及按賽事取最新紀錄。

### 5. B5（嚴格來源連結分段，移除所有猜測／位置／萬用歸段後備）— **已修好**
* **實際證據**：
  1. `_derive_escalation_config_tuples`（diff 行 1444–1585）已移除跨快照後備、`[:len(PLAYERS)]`、`first_rc`、`unlinked_t2` 依設定或檔案順序配對、`rc_t1_cands`、`rc_aggs`、`q_aggs[-1:]` 及 `anchor` 後備。
  2. 已移除以 escalation episode 覆寫已計分列 `unknown` 分段（原 `inferred_list[0]` 與 `scorable_by_qid[eqid]["config_segment"] = cand_seg`）、移除 `_match_against_known_segments` 萬用字元配對、移除列層級 `q["escalated"]` 跨來源同步（diff 行 1781–1782）、並移除單一分段時將無設定 cycle skip 強制歸入唯一分段的後備（diff 行 1888–1896）。
  3. `_attempt_matches_segment` 與 `_agg_matches_segment`（diff 行 1673–1730）對 `run_config_version`、`tier=1 prompt_version`、`aggregation_version` 缺值改以 `(val or "unknown")` 比對，不再萬用匹配；且 `tier == 2` 嘗試必須至少有一個同題同快照之 `tier == 1` 父嘗試（`linked_parent == True`）且全部父嘗試皆匹配該分段，否則回傳 `False`（計入 `unattributed_tier2_attempts`）。
  4. 當同題存在無來源連結之 Tier 2 嘗試（`has_unlinked_t2` 為真）且某升級 episode 本身無連結 Tier 2 嘗試時，其 Tier 2 版本標為 `"unknown"`，並由 `_format_segment_key` 輸出 `...|unknown` 進而歸入 `unsegmentable_esc_qids`。

### 6. C2（`final_aggregate` 依 intent 凍結之 `forecast_payload` 計分並驗證重算一致）— **已修好**
* **實際證據**：
  1. `submitted_prediction_record`（diff 行 771–805）強制從 `sub`（合併了 `intent` 之提交紀錄）讀取 `forecast_payload`，驗證 `sub.get("snapshot")`（若有）與 canonical `snap` 完全相等，並呼叫 `submit.forecast_payload(snap, final_agg)` 重算；若缺值、拋錯或 `expected != payload`，立即 fail closed 將 `series_scores["final_aggregate"]` 設為 `None`，並寫入 `scoring_errors["final_aggregate"]` 與 `_record_rejection`。
  2. 多選題直接取 `payload[0]["probability_yes_per_category"]`（即 `submit.py` 對前 `K-1` 個選項 `ROUND_HALF_UP` 至 `0.000001`、最後一個選項取 `1 - sum` 後實際送出之機率分布）計分；`tier1_aggregate` 與各線預測因未送出，維持按原始帳冊紀錄計分。
  3. **正式帳冊與測試證據**：封包驗證顯示正式帳冊 14 筆 intent 以 `submit.forecast_payload` 重算 100% 相符；`test_c2_scores_the_frozen_submitted_payload_and_rejects_missing_or_mismatched` 已驗證多選題六位小數補餘數差異、竄改拒絕與缺值拒絕。

### 7. C1、C3–C6 證據核對— **全部確認成立**
* **C1**：`poll_questions.snapshot_from_post` 在 `post.get("question")` 非 `dict`（題組／條件題）時回傳 `None`，不會產生 snapshot 亦不會送出；`resolve.py` 的 `PostQuestionMismatch` 為防呆檢查。
* **C3**：`Submitter._resume` 依 `("forecast", "comment")` 順序執行，`forecast` 未達 `forecast_ok` 前會直接 `return`，故正式帳冊中 `comment_ok` 必有先前的 `forecast_ok` 與對應 `intent`。
* **C4**：`MetaculusClient._get_url` 僅捕獲 `HTTPError`、`Deferred`、`URLError`、`TimeoutError`；`Backoff.open` 僅捕獲 `HTTPError`；故 `PriorityYieldError`（繼承自 `RuntimeError`）在 `priority_sleep` 與 `priority_opener` 觸發時會直接穿透至 `poll_resolutions` 並回傳 `deferred_busy_lock`。`resolve.py` 於 macOS 的 `_is_lock_busy` 在 `OSError` 時亦已改為回傳 `True`（保守讓路）。
* **C5**：`src/numeric.py` 第 16 行確認為 `TYPES = ("numeric", "discrete")`，不含 `date`。
* **C6**：正式 `forecasts.jsonl` 55 筆第 2 層嘗試全部具 `input_attempt_ids` 指向同題第 1 層嘗試，且 39 筆升級候選之 `escalation` 字典皆含 `candidate: True` 與 `run_config_version`。

---

## 二、重點項目與潛在新缺陷深度檢查

### 1. `resolve.py` 的 ranking 迴圈、退避與讓路機制
* **候選賽事收集時機正確**：在 `poll_resolutions`（行 313–326）中，`if type(tour_id) is int and (res_status == "resolved" or score_data) and tour_id not in ranking_tournaments:` 位於 `prev.get("state_sha256") == fp` 跳過判斷**之前**。這確保即使題目結算狀態在先前執行已寫入 `resolutions.jsonl`，只要該賽事在本次執行看到已結算或具官方分數之題目，仍會檢查該賽事排行榜是否更新；反之，尚未結算且無官方分數之題目不會觸發排行榜查詢。
* **讓路與錯誤隔離正確**：`ranking_tournaments` 迴圈在每次呼叫 `fetch_ranking` 前先檢查 `_higher_priority_busy(data_dir)`，且 `fetch_ranking` 共用同一個注入了 `priority_opener` 與 `priority_sleep` 的 `MetaculusClient`（受 `Backoff` 跨程序鎖與速率限制保護）；一旦 `submit.lock` 或 `cycle.lock` 出現，立即以 `deferred_busy_lock` 退出且 `main()` 回傳 `0`。若單一賽事排行榜 API 發生網路／HTTP 例外，則記入 `errors`（`stage: "ranking"`）並繼續處理其他賽事，`main()` 因 `result.get("errors")` 非空而回傳 `1`。
* **列表型別 JSON 回應相容性**：`MetaculusClient.get` 底層 `_get_url` 直接回傳 `json.loads(...)` 的結果（Python 執行期不強制 `-> dict[str, Any]` 標註），因此 `leaderboards/project/{id}/?primary_only=true` 回傳 JSON array 時，`isinstance(payload, list)` 可正常接收並解析。

### 2. `score.py` 的凍結 Payload 比對機制
* `submitted_prediction_record` 在 `final_agg is not None` 時執行；若 `final_agg` 本身因來源稽核失敗而已為 `None`，不會重複寫入無意義的 payload 錯誤；若 `final_agg` 存在但 `sub` 缺 `forecast_payload`、`snapshot` 不符、或 `submit.forecast_payload(snap, final_agg)` 重算不一致，則精確阻擋 `final_aggregate` 計分並記錄拒絕原因（以 `payload_err.split(":")[0]` 作為聚合計數鍵，避免例外名稱分散統計鍵）。

### 3. 嚴格分段（B5）後正式資料是否會大量變成 `unsegmentable`？
* **結論：不會。正式資料中不會產生非預期的 `unsegmentable`。**
* **原因與證據鏈**：
  1. **已送出題目**：正式帳冊的 `forecast_attempt` 皆帶 `run_config_version` 與 `prompt_version`，`aggregate` 皆帶 `aggregation_version` 與 `input_attempt_ids`，來源鏈完整，`_base_config_tuple` 不含 `"unknown"`。
  2. **已升級但未送出或未執行第 2 層之題目**：依 C6 證據，正式帳冊全部 55 筆第 2 層嘗試皆以 `input_attempt_ids` 指向同題第 1 層嘗試，因此 `has_unlinked_t2` 在正式資料恆為 `False`。對於 39 筆 `escalation.candidate == True` 的第 1 層整合：
     * 若有執行第 2 層，透過 `prov_t2_atts`（`input_attempt_ids` 交集）即可精確綁定並取得 `distinct_t2_pvs`。
     * 若因預算／截止時間未執行第 2 層（`matching_t2_atts` 為空），在 `has_unlinked_t2 == False` 下會產出 `(bt, "")`；接著 `_format_segment_key(bt, "")` 在同一基礎設定 `bt` 具有唯一第 2 層 prompt 版本（`len(distinct_t2) == 1`）時，會自動將其歸入 `f"{run_cfg_ver}|{agg_ver}|{t1_prompt_ver}|{distinct_t2[0]}"`（若整個基礎設定從未有第 2 層則歸入 `f"{run_cfg_ver}|{agg_ver}|{t1_prompt_ver}"`），完全不會落入 `unsegmentable`。
  3. 舊測試中變成 `unsegmentable` 的 `Q105`、`Q21` 是因為測試刻意構造了無 `input_attempt_ids` 的孤立第 2 層失敗嘗試（`105_c2_fail`、`21_c2_fail`），觸發 `has_unlinked_t2 == True` 防護，屬於符合 B5 設計的預期行為。

---

## 三、Non-Blocking 改進建議（Proposals，不影響上線）

1. **`src/score.py` `_attempt_matches_segment` 對第 2 層嘗試缺 `prompt_version` 的對稱檢查（行 1690）**：
   * 目前第 1 層嘗試與整合紀錄已改為 `(att_pv or "unknown") not in seg_t1.split("+")`，但第 2 層嘗試（行 1690）仍寫為 `if att_pv and seg_t2 and att_pv not in seg_t2.split("+"):`。雖然正式帳冊第 2 層嘗試皆有 `prompt_version` 且受 `linked_parent` 保護，建議未來可一併收緊為：
     ```python
     if seg_t2 and (att_pv or "unknown") not in seg_t2.split("+"):
         return False
     ```
2. **`src/score.py` `ranking_observation` 讀取時顯式檢查 `rank >= 1`（行 2562）**：
   * `resolve.py` 寫入前已強制 `type(rank) is int and rank >= 1`；`score.py` 讀取驗證時目前檢查 `type(row.get("rank")) is not int`，可順手加上 `or row.get("rank") < 1` 使讀寫兩端契約完全對稱。
3. **`src/resolve.py` `poll_resolutions` 早期讓路回傳結構一致化（行 219、230、272、288）**：
   * 進入 `ranking_tournaments` 迴圈前的 `busy` / `deferred_busy_lock` 回傳字典可一併補上 `"ranking_observations": []` 與 `"ranking_diagnostics": []`，使所有退出路徑的 JSON 鍵集合完全一致。