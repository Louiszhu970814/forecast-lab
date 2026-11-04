# Forecast Lab M4 r2 最終複審：src/resolve.py、src/score.py

## 結論：REQUEST CHANGES（只剩 1 個範圍很窄的 blocker）

- B2、B3、B4、B5、C2 和「其他」修補，我從程式碼看都已修好。
- B1 主體已修好，但還留下一個 D52 缺口，記為 B1-R，是本輪唯一的 blocker。
- 另有 3 項證據缺口（E1–E3），用來判斷分段後會不會大量變成 unsegmentable，以及 escalated 的定義對不對。它們都是 fail-closed，不阻擋手動入口上線，但在依賴報表下結論之前必須補齊。
- 我沒有核准，也不替任何人核准。Gemini r1 未完成，是否要補第二位審查者，由專案負責人決定。

### 證據來源怎麼區分
- **【碼證】**：我直接讀封包裡的 resolve.py 完整檔、score.py diff、各個 EVIDENCE 摘錄和測試原始碼得出的判斷。
- **【主持人陳述】**：封包裡說主持人跑過的結果，包括 263 passed、2908/100、正式帳冊冒煙，以及 C6 的正式資料統計。我無法驗證，也沒有執行任何測試。
- **【推定】**：看不到的 r1 既有程式碼，只能依上下文推斷。
- 公式部分（log score、clamped、CRPS/RPS、bootstrap）本輪 diff 沒有改，沿用 r1 判定，不重審。

---

## 一、逐項判定

### B1 D52 ITT：主體修好，但留下 blocker B1-R
修好的部分【碼證】：
- 移除了 `_score_unsubmitted_tier1_baseline` 和 0.0 補值。
- 未送出的升級 episode 歸入 `missing_outcome`，原因為 `unsubmitted_escalation_no_outcome_source`；ambiguous/annulled 仍歸 `excluded_by_plan`。
- `itt_gaps` 有四項。任一項大於 0 時，`can_claim_superiority=False`，並揭示 `itt_incomplete_reasons`；`complete_itt_coverage` 改由四項共同決定。
- `_extract_attempt_budget_cost` 缺值時回 None，`_sum_budget_costs` 只要有缺值，總額就是 None。`foregone_tier1_question_equivalents` 和 `gain_per_budget_unit` 都有 None 防護。
- 正式紀錄沒有 budget_cost/quota_weight，所以這幾項必為 None，符合預期。

**B1-R（blocker）：ITT 不完整時，仍輸出只算到「可評估子集」的 Tier 2 效率和總增益。**
- `gain_per_attempt = total_itt_log_gain / itt_t2_attempts` 只在 `itt_t2_attempts > 0` 時計算，沒有看 `itt_gaps`。
- 分子只含已送出、已結算的 episode；分母 `itt_t2_rows` 也只含這些 episode 的嘗試。未送出升級 episode 已耗用的第 2 層嘗試，同時被排除在分子和分母之外。
- `total_log_score_gain_nats = total_itt_log_gain` 也一樣：名稱叫 total，實際只是部分加總。
- 這正是 PLAN §4.3 / D52 說的「只比較成功完成第 2 層的題目會高估效益，所以要包含全部升級題目」。
- 新增的 B1 測試還把這個值寫成斷言：`log_score_gain_per_tier2_attempt == (ln0.8 − ln0.6)/3`，而且是在 `complete_itt_coverage is False` 的情況下。
- 主持人陳述的正式資料是 39 筆 candidate、16 筆送出。照這個比例，正式報告會長期輸出這個偏高的效率值。`budget_unit` 版本因為成本缺值剛好是 None，`per_attempt` 版本則沒有防護。

最小修補（在 `itt_gaps` 算出之後；如果 `itt_gaps` 目前只在 `itt_n > 0` 分支內，請先移到分支外）：
```python
itt_complete = not any(itt_gaps.values())
if itt_complete and itt_t2_attempts > 0:
    gain_per_attempt = total_itt_log_gain / float(itt_t2_attempts)
if itt_complete and itt_t2_budget_units is not None and itt_t2_budget_units > 0.0:
    gain_per_budget_unit = total_itt_log_gain / itt_t2_budget_units
...
"total_log_score_gain_nats": total_itt_log_gain if itt_complete else None,
"evaluatable_subset_log_score_gain_nats": total_itt_log_gain,  # 明示為子集
"tier2_efficiency_status": "complete_itt" if itt_complete else "incomplete_itt",
```
測試也要改：B1 測試改成斷言 `log_score_gain_per_tier2_attempt is None`、`total_log_score_gain_nats is None`。

如果專案負責人決定保留描述性數字，替代做法是把欄位改名為 `complete_case_*` 並標記 `biased_upward=True`。這屬於計畫層的決定，不是我能核准的。

### B2：修好【碼證】
- `is_t2` 已包含 `tier1_aggregate`。
- `effective_paired_count = len(paired_indices)`，樣本數和 CI 母體一致。
- 測試在 2 題 escalated 加 6 題非 escalated 的情況下，驗證平均值沒有被 0 稀釋。
- 但 escalated 的定義是否正確另有疑問，見 E1。

### B3：修好【碼證】，API 回應形狀待補證據（fail-closed）
fetcher 的檢查：
- `expected_user_id` 必須是正整數。
- 回應必須是 list，且只接受 `project_id` 相符、`is_primary_leaderboard is True` 的唯一一筆。
- `userEntry.user.id` 必須是 int 且等於 expected。
- rank 必須是正整數。
- 不記錄參賽人數。

`poll_resolutions` 另外再檢查 `matched_user_id`；bot_id 集合不是恰好一個 int 時，寫入 `ranking_diagnostics` 且不發請求。

待補證據：「官方原始碼：project_leaderboard_view…」在封包中只有摘要，沒有原文摘錄或錄下來的回應樣本。list 或 dict、`is_primary_leaderboard`、`primary_only` 這幾點都無法由我確認。若形狀不符，結果只會是抓不到排名，不會寫錯資料，所以不阻擋（見 N2）。

### B4：修好【碼證】
resolve 端：
- `resolution_event` 的 fingerprint 和內容都已移除排名。
- `ranking_observation` 另外 append，用 `ranking_tournaments` 去重，所以每次執行每個賽事最多一筆。
- 只在狀態雜湊改變時追加，並帶 `supersedes_ranking_observation_id`。
- 只對本次看到 resolved 或有官方分數的賽事抓排名。

score 端：
- 只讀 `observed_at_utc ≤ as_of`、`ranking_verified is True`、rank 和 matched_user_id 都是 int、且 `tour_bots == {matched}` 的紀錄。
- 每個賽事取最新一筆；全域排名和題目層級排名都已移除。
- 會計數 unverified 筆數；報表標示「verified own entry, per tournament」。
- 新增 `official_score_first_observed_at_utc`。
- 測試涵蓋 as_of 之後、身分不符、未驗證三種情況。

### B5：大致修好【碼證】，有一處萬用匹配殘留（非阻擋）和兩項證據缺口
修好的部分：
- diff 確認移除了 `[:len(PLAYERS)]`、`first_rc`、unlinked_t2 配對、`rc_t1_cands`、`rc_aggs`、`q_aggs[-1:]`、anchor 後備、跨 snapshot 後備、`inferred_list[0]` 覆寫、`_match_against_known_segments`、覆寫已計分列分段、列層級 escalated 同步，以及單一分段的 cycle 後備。
- tier1 的 rc/pv 和 agg 的 `aggregation_version` 缺值時，不再萬用匹配。
- 第 2 層嘗試沒有同題第 1 層父連結時，回 False。
- 同題存在無來源第 2 層嘗試時，episode 的第 2 層版本記為 `unknown`。

殘留（N4，非阻擋）：
- `_attempt_matches_segment` 的 tier2 分支仍是 `if att_pv and seg_t2 and att_pv not in …`：嘗試沒有 prompt_version 時仍然萬用匹配。
- `_agg_matches_segment` 的 `rec_pv`、`esc_rc` 缺值時也仍然萬用匹配。
- 這和「缺欄位不再萬用匹配」的說法不完全一致。因為第 2 層嘗試必須有嚴格匹配的第 1 層父連結，影響只限於合併不同的 reconcile 版本。

### C2：修好【碼證】
- `submitted_prediction_record` 的檢查：payload 必須是單元素 list；`intent.snapshot` 若存在須等於 canonical snapshot；用 `submit.forecast_payload` 重算必須完全相等；question 必須相符。任何一項失敗都 fail closed，並計入 `rejected_submission_counts`。
- 多選題取凍結 payload 裡的 6 位四捨五入值（最後一項補餘數），測試驗證了 `ln(0.333334)`。竄改和缺值都會被拒。tier1 照原紀錄計分。
- 從測試推定，`sub` 會帶到 intent 的 `forecast_payload`。這點很重要：依 submit._event，forecast_ok 狀態列只白名單複製 intent_id、bot_id 等欄位，**不含 forecast_payload**。若 `sub` 是純狀態列，所有正式題都會變成 missing。C2 測試的 intent 和 forecast_ok 是分開兩列，而且能通過，代表程式有合併或改取 intent。
- 但這條路徑**還沒在正式帳冊上端到端跑過**：冒煙結果是 scorable 0，全部擋在 no_resolution_event。見 E3。

### 其他修補：修好【碼證】
- cycles 截止篩選已納入 `started_at` / `finished_at`。
- main 在有 errors 時回 1，讓路時回 0。
- darwin 鎖探測遇到 OSError 時讓路。
- 報表已註明 numeric/discrete 的 brier 欄是 CRPS/RPS。

---

## 二、是否引入新缺陷

### resolve.py：ranking 迴圈與讓路
- **讓路行為正確**【碼證】：
  - 迴圈前和每次抓排名前都會呼叫 `_higher_priority_busy`。
  - `priority_opener` 和 `priority_sleep`（Backoff 節流與重試都會用到）會丟出 `PriorityYieldError`。
  - Backoff.open 只捕獲 HTTPError；`_get_url` 只捕獲 HTTPError、Deferred、URLError、TimeoutError。`PriorityYieldError` 是 RuntimeError 子類，會直接穿出，由兩個迴圈各自回傳 `deferred_busy_lock`。這和 C4 一致。
  - 已知限制：請求送出後到 timeout 之間無法讓路，屬盡力而為，可以接受。
- **ranking 抓取觸發時機正確**：加入 `ranking_tournaments` 的位置在 fingerprint 相同而 `continue` 之前，所以題目事件沒變時，排名仍可能更新。
- 沒有發現新的 blocker。非阻擋事項見 N1–N3、N5。

### score.py：payload 比對
- 沒有新缺陷。風險在版本漂移：如果日後 submit 的 `PROBABILITY_MIN/MAX` 或四捨五入規則改變，舊 intent 會變成 `submitted_payload_recompute_failed`。這仍是 fail-closed，原因代碼也可區分，見 N6。

### 嚴格分段後，正式資料會不會大量變成 unsegmentable：無法從封包判定
- 有利證據：依主持人陳述的 C6，第 2 層嘗試全部透過 `input_attempt_ids` 連到第 1 層，所以 `has_unlinked_t2` 在正式資料上應為 False。
- 但正式帳冊冒煙的 scorable 是 0，分段邏輯在正式資料上**沒有實際跑過**。風險來源見 E2。

---

## 三、需補證據（不阻擋手動入口上線；依賴報表下結論前必須補齊）

**E1：candidate=True 是否等於 escalated。**
- 依 C6，escalation dict 沒有 `escalate` 鍵，所以 escalated 實際等於 candidate。
- 如果 candidate 裡有一部分從未被准入第 2 層（admission_status 非 admitted，且沒有第 2 層嘗試），會有兩個後果：
  - 已送出的那些列會被當成 escalated，而 final==tier1，等於重新引入 B2 要移除的結構性 0。
  - 未送出的那些會被算成永久的 ITT missing_outcome。
- 粗估：55 筆第 2 層嘗試約對應 18–20 個 episode，而 candidate 有 39 筆，差距明顯。
- 請以唯讀方式只輸出計數：39 筆 candidate 依 admission_status 分布，以及其中有、無同 snapshot 第 2 層嘗試的數量。
- 如果確認存在未准入的 candidate，escalated 應改成「有准入證據」（例如第 2 層嘗試的 `admission_decision_id`，或准入狀態），屆時升級為 blocker。

**E2：分段覆蓋率。** 請唯讀輸出以下計數：
- (a) 正式第 1、2 層嘗試缺 `run_config_version` / `prompt_version` 的數量。forecast.py 摘錄到 `audit_event_id` 就截斷了，看不到這兩個欄位。如果正式嘗試沒有這兩個欄位，所有列都會變成 unknown。
- (b) aggregates 缺 `aggregation_version` 的數量。
- (c) 39 筆 candidate agg 的 `question_snapshot_id`，有多少等於 `_target_snap_id_for_qid`。跨 snapshot 後備已移除，舊 snapshot 上的 episode 會變成 unsegmentable。
- (d) `unsegmentable_esc_qids` 的數量。

另外，`or not known_segments_list` 分支和已計分列的 `_format_segment_key` 可能讓「unknown|…」成為字面上的分段，把來源不明的列合併計算（r1 既有邏輯，推定）。請確認含 unknown 的分段一律標為 unsegmentable，且 `can_claim_superiority=False`。如果實際上會被當成一般分段並可宣稱優劣，屆時升級為 blocker。

**E3：C2 的正式端到端驗證。**
- 對 14 筆正式 intent，用 score 自己選出的 `sub`、`snap`、`final_agg`，直接呼叫 `submitted_prediction_record`，只輸出錯誤代碼計數。
- 目的是確認 `sub` 確實取到 intent 的 payload（理由見 C2）。
- 現有冒煙用的是 `submit.forecast_payload`，不是 score 的選擇路徑。

---

## 四、非阻擋建議

- **N1**：resolve 建 obs 時，`**ranking` 寫在 `"ranking_verified": True` 之後。注入的 fetcher 可以覆蓋 `type`、`ranking_verified`、`tournament_id`。建議改為白名單鍵，或把 `**ranking` 放在前面。
- **N2**：附上官方 leaderboard 原始碼摘錄，或一份去識別化的回應樣本測試，固定 list 形狀和 `is_primary_leaderboard` 鍵名。
- **N3**：
  - 排行榜 404（尚無排行榜）會進 errors，導致 main 每次回 1。建議把「沒有排行榜」歸入 `ranking_diagnostics`。
  - 已 resolved 且有分數的題目每次都會重抓，長期負載只增不減。可考慮在最終狀態穩定 N 天後停止重抓。
- **N4**：tier2 的 pv、agg 的 `rec_pv` / `esc_rc` 缺值時改為比對 `"unknown"`，與 tier1 一致。
- **N5**：確認正式 forecast_ok 列的 `bot_id` 是 int。`_event` 從 intent 取值，而 intent 的 `bot_id = mode.get("approved_bot_id")`；trial 模式下可能是 None，排名就永遠抓不到（fail-closed）。
- **N6**：
  - score 會 lazy import submit，請確認 submit 在 import 時不寫檔、不建鎖，以符合 score「只讀」的範圍。
  - 可考慮在 intent 記下 payload 轉換版本，降低版本漂移風險。
- **N7**（計畫層，交負責人決定）：
  - 在目前定義下，未送出的升級題是永久缺口，未結算的題目也會一直出現。所以 ITT 優劣宣稱實際上永遠無法成立。如果希望日後能下結論，需要預先登記 ITT 母體規則（例如只納入 `scheduled_resolve_time ≤ as_of` 的 episode）或補值規則。
  - 另外請確認 `observed_pair_log_score_diff`，以及只用 escalated 列的 `final_vs_tier1_aggregate` / `player_tier2:*` pairwise，它們的 conclusion_discipline 在 ITT 不完整時也被強制為 `can_claim_superiority=False`。目前要到 ≥100 題才會觸發，所以不阻擋。
- **N8**：非 darwin 的鎖探測會短暫取得鎖，可能讓 submit 的 LOCK_NB 誤判失敗。目前平台是 darwin，影響小。

---

## 五、C 類證據評估

- **C1**：接受。snapshot_from_post 在 `question` 不是 dict 時回 None，docstring 也寫明題組和條件題回 None；PostQuestionMismatch 只是防呆。
- **C3**：接受（碼證）。`_resume` 只有在 forecast_ok 時才 `continue` 到 comment；其餘路徑都 return。
- **C4**：程式碼部分接受。「已有真跨程序 flock 測試」只是陳述，封包沒有附測試碼。
- **C5**：接受。
- **C6**：屬主持人陳述的正式資料統計，我無法驗證。forecast.py 的第 2 層入口確實要求 `input_attempt_ids`，與陳述一致。

## 六、驗證聲明的定位

- 263 passed、2908/100 與基線逐一相同、14 筆 payload 相符、scorable 0、`--dry-candidates` 0 題：以上都是【主持人陳述】，不是我執行的結果。
- autouse fixture 只在測試端補 intent 和 aggregate 身分欄位，程式本身沒有放寬，我認為合理。
- 但正因為正式帳冊目前沒有可計分題，B5 嚴格分段和 C2 路徑在正式資料上都還沒被實際驗證過，所以需要 E2、E3。

## 七、下一步

1. 套用 B1-R 修補和對應的測試斷言。
2. 補 E1、E2、E3 的唯讀計數。
3. 以上完成後送 r3 或請我複核。如果 E1、E2 沒有發現問題，就可以判 PASS（附上述非阻擋建議）。

本審查只是審查意見，不構成核准。