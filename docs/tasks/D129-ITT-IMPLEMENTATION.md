# D129：D128 live ITT 診斷與 M4 相容性修補

> **D136 現況校正（依 D132）**：M4 手動入口已部署。原文「11/06 21:00 首題」為當時開發驗收安排；已送出 MiniBench 批次最早預定台北 **11/13 20:00**、最晚 **11/14 12:59** 結算。預定不代表已結算；以下歷史紀錄保留，現行日期以 [D132 核對](D132-CUP-ADVISORY.md) 與官方實際狀態為準。

已於 `2026-11-04T13:05:21.446Z` 上線手動入口。Louis 已授權完成、雙審驗證後上線並 push；GPT 整合，Claude 代 commit。規則沿用 [D128 登記](D128-LIVE-ITT.md)，不移動時間邊界、不按送出或結算成功選樣。

## 已部署

- `resolve_v2`、`score_v2`，新增 `flab/itt_cohort.py`、`itt_report.py`、`submission_identity.py`。
- P1～P4：只豁免明確 local/trial 無 snapshot 探針；稀疏收據繼承 intent，mode/snapshot 損毀或衝突 fail closed；共用狀態解析。一般 resolve 與 dry-candidates 有錯誤皆 `status=error`、非零退出；dry-candidates 不打 API、不寫帳冊。
- D128 固定 episode 母體由完整來源鏈分類；失敗、未啟動、未送出保留，舊 trial 重試不搬進 live。缺根、run 衝突／缺失、時間不明保留 unknown。歷史 job 依根決策時間排除；孤立 reconciliation/job 不消失。
- live 報表與歷史診斷分開；列固定區間、cohort/報告版本、來源 metadata 雜湊、截止時間、interim 狀態、納入／排除／未知、送出與未結算、配對分母。按設定與題型分段，重用同步 bootstrap，不把同題多 episode 當獨立新題。
- 原始損毀時間證據保留缺口；成本缺值、不明子代理、混合成本欄位不加總。資源標示只涵蓋已記錄且可連結的嘗試，unknown 資源另列。

## 必須保留的限制

現行 scheduler 在記憶體判定 candidate，T2 前檢後才可能由 reconcile 持久化根紀錄。因此無法證明每次首次觸發都已捕捉，也不能回填不存在的歷史。selector 一律 `decision_capture_complete=False`、`complete_membership=False`，限制碼 `first_candidate_decisions_not_durably_logged`。

**D128 與歷史 D52 均強制 `decision_capture_incomplete`；不宣稱完整 ITT 優越，不輸出完整總增益／每次嘗試／每單位額度效率。** 已觀測配對只作明示的子集描述；非第 2 層比較與官方／實際送出 payload 計分保留。此批上線是診斷能力，並不表示 B 已解除完整 ITT 限制。

同題後續修訂提交可能使舊 episode 缺少已驗證配對，明列 `missing_validated_pair`；不補 0。主計分器更廣泛的 receipt question_id／aggregate_id 衝突一致性、損毀 final 的未綁定原因細分、預算已准入但未落 attempt 的成本追蹤，列後續加固。

下一批須在 T2 前檢之前，以 append-only 帳冊保存首次 candidate、不可變根 ID、campaign/run/config/時間證據，經雙審與執行期維護程序後部署，並在**下一個窗口開始前**另行登記 cohort。不得藉此重設 D118 邊界或洗掉歷史缺口；D118 原限制保留。

## 驗證與審查

- 相關 **446 passed**；完整 **3091 passed / 100 failed**。100 個失敗節點與 D127 r6 基線完全一致，新增／移除皆 0；不能稱完整全綠。
- 合成測試包括固定邊界、正式未開始/未送出、舊 trial 晚重試、來源缺失/衝突、孤立 reconciliation、缺時間證據、共同身分解析、成本不完整，以及 100 個完整歷史配對仍禁止 Tier2 宣稱的正反對照。
- 兩份保護清單 **28/28** 相符（17 個不同檔案）；未改正式預測設定、prompt、adapter、runner、netguard、預算窗口或排程。
- metadata-only 唯讀正式結構檢查於 UTC 12:55:36.867：已記錄根 3 included、32 excluded（before_live_start）、0 unknown；沒有載入結算資料，也沒有揭露預測內容。這不是完整分母證據。
- 部署前正式帳冊唯讀檢查：`resolve --dry-candidates` 為 ok／exit 0、0 候選、0 錯誤、2 個已知探針 skipped；live 報表為 3 個已送出但未結算、0 配對，9 個 T2 嘗試皆缺成本值，故成本總額為空。Markdown 顯示 interim、捕捉缺口及 complete ITT=False；未打 API、未揭露預測值。
- r1 Claude/Gemini 均 BLOCKING；修正後 `r2` 兩位 PASS。Gemini r1 封包末端截斷，r2 已縮小且確認收到完整新增模組／測試與尾標記。最終審查：[Claude](../../reviews/claude-d129-code-review-r2.md)、[Gemini](../../reviews/gemini-d129-code-review-r2.md)。
- [受審／受測／已部署 SHA 清單](../../reviews/d129-source-manifest.json) 完全相同；備份後套用，正式入口以空白合成目錄冒煙，resolve 候選、score JSON/Markdown 均 exit 0。沒有跑正式結算 API。

## 操作與待驗收

沿用 [M4 操作](M4-RESOLVE-SCORE.md#操作上線後)。只更新手動工具，沒有安裝結算/計分排程。

首題 qid 46289 預定台北 **11/06 21:00** 結算後，再做 resolve＋score 的真實端到端驗收；目前未到時點，不宣稱完成。

完整操作／測試／封包／備份在 ignored `data/operations/d128-implementation-20261104/`。
