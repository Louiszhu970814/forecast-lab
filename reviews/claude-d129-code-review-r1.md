# D129 官方 CLI 審查證據：claude r1

來源：本機 tools-disabled 官方 CLI；來源封包 SHA-256 `52353b40e78cfbfa4443091ff11d39fd496145a76b8aaafe8fa93597a8ead43c`。本文件保存審查者原文；執行測試與部署證據由主持人另列於 D129 文件。

【D128 實作審查 r1：結論 BLOCKING（不通過）】

審查人：Forecast Lab 設計／程式審查（獨立審查人，Claude）。
本次不核准部署，也不代表 Gemini 或其他審查已通過。修正後需要 r2 再審。

一、證據範圍
- 我只讀了來源包裡的程式碼和測試：src/score.py 全文、src/resolve.py 的 diff 和受影響函式、flab/itt_cohort.py、flab/itt_report.py、flab/submission_identity.py，以及四個測試檔。
- 我沒有執行任何測試、指令或檔案操作。「401 個測試通過」「D127 基線 100 個已知失敗節點不變」是主機端提供的證據，我無法驗證。
- 來源包沒有附排程器和預測相關受保護檔案的 diff。我只能說包內沒有看到修改，不能證實這些檔案確實沒變。
- 今天是 11/04，D128 窗口到 11/10 才結束。現在產生的任何 live 報告都只是中期報告。
- 以下「已確認」都只是靜態閱讀的結論，不是執行結果。

二、讀碼確認符合的部分
1. 固定窗口：START／END 寫死成常數。判斷式 `start <= decision < end` 是半開區間，有起點包含、終點排除的測試。起點沒有被「首次成功送出」取代。
2. 選樣不看送出或結果：
   - selector 不讀 submissions、resolutions 或機率。
   - `_meta` 白名單不含機率或理由。測試證明加入 probability 或 reasoning 後輸出不變。
   - 沒有用 submissions.mode 或 aggregate.mode 篩母體，fixture 刻意使用 aggregate.mode=local。
3. 來源鏈與重試：
   - 以共用 T1 葉節點合併同一條來源鏈，取最早的候選作為根。
   - 試驗期的晚到重試沿用舊根而被排除；跨窗口的晚完成仍歸原 episode。
   - 祖先鏈使用全部歷史帳冊，沒有刪除起點前的列。
   - 無根的 T2 和無根的排程器 T2 job 以 unknown 保留，不會消失。
4. 失敗與缺漏：
   - 未開始、未送出的 episode 仍在母體。未送出記為 unsubmitted_escalation，不補 0。
   - 已送出但未結算記為 not_yet_evaluable，不會被誤標為未送出。
   - ambiguous／annulled 另列。
5. 結果與身分：
   - 結算採 as_of 以前的最新事件，同時間時檔案順序較後者優先，與 score.py 一致。
   - 基準身分：計分列的 tier1_aggregate_id 必須是根或等價基準別名，否則記為 baseline_root_mismatch。
   - 同一題多個 episode 會整題標 gap 並退出配對，不會灌高題數。
6. 設定與題型分段：不合併 config 與題型，未知設定記為 unsegmented_episode。
7. 成本：缺值不當 0；有子代理時直接標為無法分離、不加總，避免重複計算。
8. 決策捕捉限制：
   - selector 永遠輸出 decision_capture_complete=False 和 first_candidate_decisions_not_durably_logged。
   - report 因此永遠帶 decision_capture_incomplete 缺口，強制 `can_claim_superiority=False`。
   - total／per-attempt／per-unit 一律為 None，只留 evaluatable_subset。
   - live 區段沒有宣稱已完整捕捉，也沒有要求重建未記錄的決策，符合要求。
9. P1–P4：
   - 只有完全等於 local／trial 的模式能豁免 snapshot。
   - 模式格式錯誤、大小寫或空白變體、互相衝突都會 fail closed；稀疏收據可以繼承 intent。
   - 有錯誤時 resolve 的 status 為 error，CLI 回傳 1。
   - resolve 和 score 共用 submission_identity。
10. 版本號已升為 score_v2、resolve_v2；歷史 D127 結構保持不變。

三、阻擋項（BLOCKING）

【B1】selector 靜默略過未知、衝突或無時間的佐證 run，違反登記第 4、5 點
- 位置：itt_cohort.select_cohort 的 live 判定段：
  `for rid in runs: r = by_run.get(rid); if r is None or rid in bad_run or _row_time(r) is None: continue`
- 問題：
  - `runs` 包含祖先 T1／T2 attempt 和預算事件上的 run_id。這些 run 如果查不到、元資料互相衝突（bad_run）或沒有時間，都被直接跳過，不會加入 errors。
  - 只要另外有一個 reconciliation_run 或 job 能證明根決策發生在 live run 內，該 episode 就會被判為 included。
  - 情境：T1 來自沒有 scheduler_run 紀錄的試驗或手動 cycle；試驗期的首次候選決策只在記憶體中、沒有落地；live 排程器之後重新評估同一條鏈並寫下根。這個試驗期的來源鏈就會被當成 live 母體成員。這正是登記禁止的「試驗期晚到重試搬進 live」，而且正好被已知的捕捉限制放大。
  - 登記明文規定「互相衝突、缺證據列歸屬不明，不能靜默排除」，同理也不能靜默納入。`rid in bad_run` 被 `continue` 吞掉，直接違反規則。
- 修正：
  1. 三種情況分別加入 errors，例如 `unresolved_evidence_run`、`conflicting_run_metadata`、`undated_evidence_run`，讓 episode 變成 unknown。
  2. 建議再加一條：根的任何 T1 葉節點時間早於 START（或連到非 live run）時，判為 unknown，原因如 `ancestry_predates_live_start`。因為根只是「第一筆有記錄的候選」，祖先早於起點時，無法證明首次觸發落在窗口內。
- 要補的測試：
  - T1 的 run_id 不在 scheduler_runs；
  - 同一 run_id 有兩筆互相衝突的 scheduler_run；
  - scheduler_run 沒有時間；
  - T1 早於 START、根在窗口內。
  以上都必須是 unknown，不能是 included。

【B2】Markdown 報告缺少登記要求列出的項目，觀測配對也沒有明示為子集描述
- 位置：render_scoreboard_markdown 的「## 3. Registered Live ITT (D128)」。
- 問題：
  - 登記要求「報告列規則版本、cohort ID、來源雜湊、決策區間及分析截止時間」。目前第 3 節沒有印出 start_utc／end_utc，也沒有印出 cohort 自己的 as_of 和 report 版本 live_itt_report_v1。這些值只存在 JSON 裡。
  - 登記要求「觀測到的成對結果只能明示為子集描述」。但表格標題只寫「Live seg/type」「Metric difference (final − tier 1)」，並列出平均值和 95% CI；全節沒有「observed-pair subset／descriptive only／not ITT」字樣。被 gate 過的 conclusion_discipline 仍保留 `tier: effect_size_with_ci`，容易被誤讀為效應量結論。
  - evaluatable_subset_log_score_gain_nats 沒有印出，只印「Full ITT log gain: None」。
- 修正：
  - 在第 3 節列出決策區間 [START, END)、cohort as_of、窗口是否已結束（as_of 是否 ≥ END，未結束標為 interim）、report 版本。
  - 表格標題改為「Observed-pair subset (descriptive only; NOT full ITT)」，並在旁邊列出 paired／included 分母。
  - 被 gate 時，tier 改為 descriptive 或另外標示。
- 要補的測試：markdown 必須包含窗口兩端、subset 字樣，並且不含任何可宣稱優越的字串。

【B3】歷史 D52 區段仍可能輸出完整 ITT、增益或效率，以及 Tier 2 優越宣稱，與「已知捕捉限制必須擋下所有完整 ITT 結論」衝突
- 位置：score.py 的 `_build_d52_escalation_accounting` 與 `_gate_tier2_pairwise`，屬 D127 原路徑，本批沒有改。
- 問題：
  - 歷史全史視圖包含 D118 之後的排程器紀錄，而未落地的首次候選決策同樣不在歷史分母裡。
  - 若某個 segment 中可觀察到的升級全部完整，且配對數 ≥ 100，legacy 程式仍會設 `complete_itt`，算出 total_log_score_gain_nats、gain/attempt、gain/unit，並允許 final_vs_tier1 優越宣稱。
  - 現有約 30 個未送出 episode 可能讓這條路徑暫時走不到，但程式層沒有保證。
  - 交辦明文要求「all full-ITT superiority/gain/efficiency」都要被擋下。
- 修正（範圍很小，不改歷史描述數值）：
  - 對歷史 D52 一律注入 `decision_capture_incomplete` 缺口，或在含排程器時代紀錄時注入，使 itt_complete=False。
  - Tier 2 pairwise 一律走 `_no_claim`。
  - 非 Tier 2 的比較（例如 final vs player_tier1）不受影響。
- 要補的測試：用合成資料做出 100 個以上完整歷史升級，`overall` 和 `by_config_segment` 都不得出現 complete_itt 或 Tier 2 宣稱。

四、應修但不阻擋（fail-closed 或報表精度問題）
- S1 itt_report 自建一套送出身分和狀態邏輯，與 score 和 resolve 不一致：
  - `row.get("status", row.get("state"))` 沒有 strip 和轉小寫；"status" 為 None 時不會退回 "state"。
  - 收據 snapshot 是空字串、intent 是 "s1" 時，score 視為繼承，itt_report 卻判 submission_identity_conflict。
  - 帶空白的 ID 在兩邊判斷也不同。
  - 結果是已送出的 episode 可能被標成 unsubmitted 或 conflict。方向雖然保守，但計數會錯。
  - 建議改用 submission_identity 和 score._status。
- S2 final aggregate 被靜默丟掉：
  - 祖先有錯、或建立時間早於決策時間的 final，不會綁進 episode，也不會留下原因。
  - 送出列因此對不上，被標成「unsubmitted_escalation」。
  - 建議加上 `unbound_final_aggregate` 原因，並分開計數。
- S3 `resolve --dry-candidates` 會丟棄 diagnostics，固定回傳 0。完整性錯誤（MissingSubmittedSnapshot）在這條路徑仍然不可見，與「任何 resolve 錯誤都要回 error／非零」不完全一致。
- S4 成本：
  - 只看 forecast_attempt；只有 budget_events 但沒有 attempt 列的 T2（例如超時或當機）不計入，budget_units 可能偏低，卻仍顯示「完整」。
  - budget_cost 和 quota_weight 單位可能不同，卻可能混合加總（D127 也有同樣問題）。
  - unknown episode 的 T2 資源沒有另外列出。
  - 建議比對 budget_events、記錄使用的成本欄位、另列 unknown 資源。
- S5 score 每題只取最新一次送出：live episode 的 final 如果之後被同題的新 snapshot 修訂送出取代，會永久記為 missing_validated_pair，觀測子集因而依「是否修訂」而偏。至少要在報告中說明，長期應改為逐 episode 綁定送出列。
- S6 `cohort_ledger` 沒有像其他帳冊一樣先做 exists() 檢查，依賴 read_complete 對不存在檔案的行為。主機測試若真的通過，表示它容錯，但寫法不一致，建議統一。
- S7 新增的 import 缺少 `# noqa: E402`，屬 lint 問題。
- S8 episode_id 用「最早有記錄的根」產生。日後追加較早或格式錯誤的列，會改變根和 ID，報告之間的 episode 可能無法逐一對應。建議記錄根變動或保存第一次的 ID 對照。

五、選擇性建議（非必要）
- P1 歷史的無根 T2 和舊格式候選目前都計為 unknown，會把 live 窗口內的不明列和很早以前的雜訊混在一起。可以依「決策時間 ≤ T2 或 job 開始時間 < START」另列為 unknown_provably_pre_window（仍然擋宣稱）。
- P2 窗口內有明確證據屬於其他 campaign 或 local 測試的列，目前判為 unknown。可考慮另設 out_of_scope 狀態，並在登記中說明。
- P3 場地目前只檢查 ground 和 project_ids；若 campaign 有登記 MiniBench tournament id，可再比對 snapshot 的 tournament_id。
- P4 已知 local／trial 探測列若晚於同題的 live 送出，會遮蔽它，導致 resolve 不輪詢（D127 就有）。可改為 live 優先。
- P5 效能：每個 episode 都掃描全部 budget_events 和 job，複雜度約為 O(E·A·B)，資料變多時可以先建索引。
- P6 部署前可在主機上用正式元資料（不讀機率或結果）先跑一次 selector，看 included／unknown／reasons 的分布。尤其要確認 reconciliation_run 的開始時間 ≤ 根的 created_at_utc，以及授權列的 campaign 欄位格式與 fixture 一致。否則所有 episode 可能都變成 unknown（fail-closed，但也就失去診斷價值）。

六、測試覆蓋
- 登記要求的合成測試大致都有：起點與終點；未開始或未送出；無 mode 的送出列；aggregate.mode=local；試驗期重試排除但保留祖先；跨窗完成；缺根、缺時間、campaign 衝突；同題去重；歷史診斷保留。
- 還缺：B1–B3 對應的測試、S1 的狀態大小寫與空白 snapshot、S2 的未綁定 final、歷史 D52 不得出現 complete_itt。
- 完整回歸和基線比對結果要由整合者提供主機證據。

七、需要的下一步
修正 B1、B2、B3，並補上對應測試。重跑相關測試與完整回歸、比對基線後送 r2，由我和 Gemini 分別獨立再審。兩邊都通過前不應部署或 push 上線版本。首題（台北 11/06 21:00）的實戰驗收仍未完成。
