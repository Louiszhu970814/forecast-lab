# D129 官方 CLI 審查證據：claude r2

來源：本機 tools-disabled 官方 CLI；來源封包 SHA-256 `3f4e0edd4e775c31e76091d16164936c25f534b8a17a891dadd498bd7ba4fe56`。本文件保存審查者原文；執行測試與部署證據由主持人另列於 D129 文件。

# D128 實作 r2（部署 D129）獨立設計／程式審查

**審查人**：Forecast Lab 指定設計／程式審查人
**依據**：只看本次提供的 source packet。沒有執行任何測試、指令、檔案讀取或網路操作。下文所有測試結果都是 **host 提供的資料**，我沒有自己驗證。

## 結論：PASS（本審查人這一票，沒有 blocker）

- 這只是我個人的審查結論，**不代表 Gemini 的獨立審查已通過，也不是部署核准**。依 D128 規定，要等 Claude 和 Gemini 兩份審查都通過才能部署。
- 我能 PASS，主要是因為這版設計遇到問題一律保守處理：
  - selector 的 `decision_capture_complete` 固定為 `False`。
  - live 報告和歷史 D52 都固定帶 `decision_capture_incomplete` 缺口。
- 因此成員分類就算有誤，最多只會讓**描述性計數或缺口標籤**不準，不會產生優越性、完整 ITT 或效率結論。
- 下面列出的問題都是**非阻擋建議**，另外有一項建議在部署前完成的檢查。

---

## 一、收件確認

1. **已收到 `## Host r2 validation evidence`**，內容如下：
   - `baseline_failed=100`，`current_failed=100`
   - `added=[]`，`removed=[]`
   - `passed=3091`，`targeted_passed=446`
   - `protected_manifest_entries_matching=28`，`protected_distinct_paths=17`
2. **已看到結尾標記 `END_OF_PACKET_D128_R2`**，資料包完整，沒有截斷跡象。
3. **完整收到的新模組／新測試（全文）**：
   - `src/flab/itt_cohort.py`
   - `src/flab/itt_report.py`
   - `src/flab/submission_identity.py`
   - `tests/test_itt_cohort.py`
   - `tests/test_itt_report.py`
   - `tests/test_score_live_itt.py`
   - `tests/test_submission_identity.py`
4. **以差異檔加完整變更函式提供的檔案**：
   - `src/score.py`：差異檔，加上以下完整函式：
     - `_build_synchronized_draws`、`_ci_from_synchronized_draws`、`pairwise_conclusion_discipline`
     - `render_scoreboard_markdown`、`_no_claim`、`_gate_tier2_pairwise`、`_build_d52_escalation_accounting`
   - `src/resolve.py`：差異檔，加上 `_select_candidates_with_diagnostics`、`candidate_questions`、`main`。
   - `tests/test_resolve.py`、`tests/test_score.py`：只有差異檔。
5. **沒有提供、我視為未變更的既有程式**：
   - `raw_questions`、`raw_forecasts`、`raw_aggregates`、`raw_submissions`、`raw_subagents`、`resolutions`、`scorable_questions` 這些變數的定義。
   - `_fmt_metric`、`_render_subset_tables`。
   - `_gate_tier2_pairwise` 被呼叫的位置。

   「score 傳給報告的是 RAW 證據」這一點，我只能從變數名稱和 host 回報通過的 `test_raw_invalid_time_evidence_reaches_live_report` **推定**，無法從程式碼直接確認。

## 二、實際證據 vs. 我的推論

| 類別 | 內容 | 性質 |
|---|---|---|
| Host 測試結果 | 基線 100 個失敗節點和現在相同（added/removed 皆空）；3091 通過；targeted 446 通過 | host 回報，我沒有執行 |
| Host 受保護檔案檢查 | 28 筆 manifest 項目相符，涵蓋 17 個路徑 | host 回報；沒有列出路徑，我無法逐一核對 |
| Host 真實帳冊 metadata 檢查（2026-11-04T12:55:36.867Z） | included 3、excluded 32（全部是 before_live_start）、unknown 0；`decision_capture_complete=false`；`source_sha256=0cd0…5f63`；`no_outcomes_loaded=true` | host 回報，只涵蓋 selector 層 |
| Host 沒提供的 | 真實帳冊上的 `resolve --dry-candidates` 輸出；真實帳冊上 `live_itt` 報告層的缺口和資源統計 | 缺少證據 |
| 本審查 | 對上述原始碼和測試做靜態閱讀 | 推論，不是執行結果 |

**對真實帳冊數字的提醒**：32 個 excluded 和交接說的「30 個未送出 episode」不能直接畫等號。D128 規定不能預設兩者相同；本次證據也只顯示 32 個都屬於「決策時間早於起點」，不代表那 30 個都已被正確歸類。

## 三、R1 修正對照（靜態確認）

- **ClaudeB1 / GeminiB2（證據 run 有問題時歸為 unknown）**：已修正。
  - 程式對應：`unresolved_evidence_run`、`conflicting_run_metadata`、`undated_evidence_run`（`hi<lo` 也歸在這裡）、`ancestry_predates_live_start`。
  - 測試對應：`test_all_linked_evidence_runs_fail_closed_despite_valid_live_decision` 涵蓋 missing、conflicting、undated、reversed 四種情況；另有 `test_live_recorded_root_with_pre_live_ancestry_is_unknown`。
- **GeminiB1（job campaign 驗證順序、模糊 job）**：已修正。
  - job／reconciliation 的 campaign 驗證只在「時間窗內」分支執行，排在時間排除之後（`test_historical_job_campaign_does_not_override_proven_exclusion`）。
  - 模糊的 job 會標記所有符合的 root（`ambiguous_scheduler_job_root` / `ambiguous_reconciliation_root`）。
- **GeminiB3（孤兒診斷）**：已修正。
  - 孤兒 T2、沒有 root 的 job、沒有 root 的 reconciliation 都保留為 unknown 診斷。
  - 只依明確共享的來源憑證（`attempt/bundle/root/leaf` 等）合併，不會只因同一題就合併。
  - reconciliation campaign 衝突會歸為 unknown。
- **ClaudeB2（Markdown 報告）**：已修正。
  - Markdown 有列出：半開區間 [start, end)、cohort as-of、analysis cutoff、report version、interim／closed、「descriptive only; NOT full ITT」、成對數／納入數。
  - 被管制的 Tier 2 比較 tier 改為 `descriptive_only`。
- **ClaudeB3（歷史 D52）**：已修正。
  - 歷史 D52 固定帶 `decision_capture_incomplete:1`，所以 `itt_complete` 一定是 False。
  - `total_log_score_gain_nats`、每次嘗試增益、每預算單位增益都是 None。
  - 觀測到的子集數值保留。
  - 100 組完整配對的合成測試同時檢查了 overall 和 segments，並保留「非 Tier 2 比較仍可宣稱」的正向對照。
- **GeminiB4（無效時間證據）**：報告層已修正。
  - 只有確定在未來的證據才會被排除；無效時間的列會保留，並產生 `invalid_*_time` 缺口和資源不完整標記。
  - score 層是否真的傳 raw 資料，見第一節第 5 點的推定。
- **ClaudeS1（共用身分規則）**：已修正。
  - 共用 `submission_status` 和 `submission_identity`：sparse／空白 receipt 會繼承 intent；有矛盾或格式錯誤的 mode／snapshot 會回傳無身分，不能取得 probe 豁免；resolve 和 score 的判斷一致。
- **ClaudeS3（dry-candidates）**：已修正。
  - 輸出包含 `status/errors/skipped`；有錯誤時回傳 1；不建立 API client，也不寫入檔案。
- **資源欄位**：已修正。
  - 加上 `scope=recorded_linked_attempts_only`；`unknown_resources` 獨立列出。
  - 混用成本欄位、缺漏成本、有子代理、無效時間或衝突時，`budget_units=None`；缺值不補 0 或 1。

## 四、對照 D128 規格

- **固定常數**：cohort ID、campaign、bot 311763、START `2026-11-03T04:12:32.156Z`、END `2026-11-10T04:00:00.000Z` 都寫死；`START <= decision < END` 是半開區間。✔
- **不以結果選樣**：沒有用 `submissions.mode` 或 `aggregate.mode` 篩選；送出、Tier 2 完成與否、結算都不是納入條件。✔
  - 合成測試涵蓋：live 未開始、未送出、aggregate.mode=local、receipt 沒有 mode。
- **根身分**：`(question_id, snapshot_id, 根 record_id)`，同一來源鏈（共享 T1 leaf）取最早一筆。✔
  - 試驗期晚到的 retry 沿根事件歸屬並一起排除。
  - 跨窗延遲完成仍歸在原 cohort。
  - 祖先鏈不刪除（只排除已知未來列）。
- **缺口處理**：缺根、時間不明或有衝突時歸為 unknown，並阻擋完整 ITT。✔
- **同題多 episode**：全部標記 `multiple_episode_question`，不拿來配對，不灌高樣本數。✔ 這是保守做法：同題第一個正常 episode 也會失去配對，但規格允許。
- **分段與統計**：
  - 依 `config_segment` × `question_type` 分組，不合併。✔
  - 成對以不同題目計數，使用題群 bootstrap。✔
  - 沿用 `pairwise_conclusion_discipline` 的 100 題門檻和 question／cluster 兩種 95% CI 同向排除 0 的規則。✔
- **只讀 metadata**：`_meta` 白名單；測試確認改動機率／理由／結果不會改變輸出。✔
- **版本與報告**：score_v2、resolve_v2；報告列出規則版本、cohort ID、來源雜湊、決策區間、分析截止時間。✔
- **必要合成測試九項**：起點包含／終點排除、未開始／未送出、無 mode、aggregate local、late retry、跨窗完成、缺根／缺時間／campaign 衝突、同題多 episode、歷史診斷保留。在我看到的測試裡**都有對應**。

## 五、Blockers

**無。**

## 六、非阻擋建議

以下問題都不會產生錯誤宣稱，但會影響診斷的準確度或可讀性。依嚴重度排序。

1. **〔中〕混合來源的 final aggregate 被默默跳過**
   - 現象：`matches()` 只接受「leaf 是某個 episode 子集」的 final。groups 已依共享 leaf 合併，所以 `len(found)>1` 幾乎不會發生。跨兩條來源鏈的 final 因此既不綁定，也不標成 `ambiguous_final_aggregate`。
   - 後果：如果這種 final 被送出，報告會把該 episode 標成 `unsubmitted_escalation`，而不是身分／來源衝突。
   - 建議：leaf 和多個 episode 有交集時，對所有交集的 episode 加上錯誤。
   - 同類問題：final 有 ancestry 錯誤或建立時間早於決策時也是靜默略過，建議改成在 episode 加診斷。
2. **〔中〕報告沒有檢查 receipt 的 mode**
   - 現象：live 報告只檢查 snapshot／bot／aggregate 一致性。明確標記 `mode=local/trial`、但有 snapshot 的 `forecast_ok` receipt，若連到 included episode，會被算成 submitted。
   - 建議：明確的非 live mode 視為 `submission_identity_conflict`。這只是驗證送出列，不是拿 mode 篩母體，不違反 D128。
3. **〔中低〕`ancestry_predates_live_start` 的退回時間不夠保守**
   - 現象：沒有 `started_at*` 時，退回用 `_row_time`，而它取的是所有時間欄位的**最大值**。如果 attempt 的 `created_at_utc` 在起點前、`finished_at_utc` 在起點後，會被判為「沒有早於起點」。
   - 建議：退回時改用已知時間的最小值；任何時間欄位早於起點就判為 unknown。
4. **〔中低〕場地驗證偏弱**
   - 現象：snapshot 只檢查 ground（缺漏時放行）、`tournament_id` 是正整數、`project_ids` 一致，沒有核對 tournament 是否屬於 MiniBench。目前主要靠 campaign `venues==["minibench"]` 加 live scheduler 佐證。
   - 建議：補上 snapshot venue／slug 前綴或 MiniBench tournament 白名單的檢查，對應規格的「場地依根決策綁定的 snapshot」。
5. **〔低〕分段分母容易誤讀**
   - 現象：未送出或未結算的 episode 沒有 score row，會被標成 `unsegmented_episode`，不進任何分組。所以每組的 `episode_count` 不是該設定的完整分母（全域缺口仍會擋下宣稱）。
   - 建議：從 root／T1 metadata 推導分段，或在 Markdown 註明「未分段 episode 數」。
6. **〔低〕Markdown 缺少部分欄位**
   - 現象：Markdown 只顯示 excluded／unknown 的數量。排除原因計數、`unknown_resources`、`submission_time_unknown_episode_count` 只在 JSON 裡。
   - 建議：補進 Markdown，更貼近「保留排除原因與計數」。
7. **〔低〕snapshot ID 去空白和 mode 的處理不一致**
   - 現象：mode 有空白就視為格式錯誤，snapshot ID 卻會 strip 後比對（`" s1 "` 等於 `s1`）。
   - 建議：統一規則，或在程式註解中說明理由。
8. **〔低〕unknown episode 的 ID 可能重複**
   - 現象：兩筆 candidate 共用同一個 `record_id`（會觸發 `bad_agg`）時，`episode_id` 相同。included 不受影響，但 `unknown_episode_ids` 可能重複。
   - 建議：加上索引，讓 ID 不重複。
9. **〔低〕狀態值變更**
   - 現象：`poll_resolutions` 的 `status` 從固定 `"ok"` 改成有錯誤時為 `"error"`。exit code 行為不變，但下游如果解析 JSON 的 status 欄位，行為會改變。
   - 建議：確認 runner 沒有依賴這個欄位（packet 說 runner 未改動）。

## 七、建議部署前完成的檢查（不是程式 blocker）

1. **真實帳冊 `resolve --dry-candidates`（只讀 metadata，不打 API）**
   - 原因：新的身分規則下，沒有 mode 的 snapshotless 列會從「略過」改成 `MissingSubmittedSnapshot` 錯誤。如果歷史 D87 V15 probe 列沒有明確寫 `mode=local/trial`，每次 resolve 都會 exit 1。輪詢其他題目不受影響，但排程會持續報錯。
   - 這個檢查不會呼叫 API。建議確認 `errors` 是空的，或每一筆都可解釋。
2. **真實帳冊 `live_itt` 報告層摘要（只看計數，不看結果正文）**
   - 確認 3 個 included 的 `itt_gap_reasons` 和資源統計合理。
   - 確認 Markdown 第 3 節顯示 interim、`decision_capture_incomplete`、`complete ITT: False`。
3. **Gemini 獨立審查**：結果須另行取得，我沒有看到，也不能代替。

## 八、仍然存在的限制（packet 已聲明）

- 執行期沒有持久記錄每一個首次 candidate 決策，所以 `decision_capture_complete` 永遠是 False。**本批無法讓 D118 cohort 或歷史資料達到完整 ITT 或完整效率結論**，只能做觀測子集描述。要修正，須另一批改執行期記錄並重新審查。
- episode 分數沿用最新送出列；之後的修訂會造成缺少配對，作為已揭露缺口處理，不補值。
- 完整的 qid／aggregate receipt 衝突強化留到後續處理。
- 首題台北 11/06 21:00 的實戰驗收**仍未完成**，本審查不涵蓋。

## 九、總結

- 本審查人結論：**PASS（無 blocker）**。
- 第六節的非阻擋建議可以排入後續批次，其中第 1、2 項優先。
- 部署前建議先完成第七節的唯讀檢查。
- 我沒有執行任何測試；部署仍須 Gemini 獨立審查通過，並依授權由人工執行。
