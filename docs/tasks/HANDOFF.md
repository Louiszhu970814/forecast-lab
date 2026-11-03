# 給下一個對話的接手紀錄

## 三方共識：加速迭代窗口與分階段清理／迭代計畫表（UTC 2026-11-03 16:20，PLAN v1.79 / D126）

依 Louis 指示（「沒必要 0 寫入，不會有影響的直接處理，並加快模型迭代速度」），由 Gemini 盤點並經 Claude（`claude-opus-5-5` high）與 GPT（`gpt-6-astra` high）兩輪獨立 CLI 評估收斂，確立以下迭代窗口機制與分階段執行表：

### 1. 窗口與雙軌回饋機制
- **診斷／修 Bug 節奏（每天）與預算總窗口（7 天，至 UTC 11/10 04:00）拆開**：
  - `config/cycle_campaign.json` 只釘死 `run_config_v2.json` 與 `quota_policy.json` 兩份雜湊。所有不改這兩份設定的工程修補、測試隔離修正、文件同步與 M4 工具開發，**完全不必換窗**，驗證通過即可上線。
  - 備用設定切換逃生口：在 11/06 前完成 `budget_handoff_v2`（有界多跳鏈式 handoff：保留根窗口 `2026-11-02T13:03Z` 的 `accounting_start`、跨鏈累計用量不歸零、終點鎖定 `11/10 04:00Z` 不滑動延長、每週最多 2 跳）。
- **短期軌（每日「已關閉題目健檢」與 `review_eligible` 三道 AND 安全閘門）**：
  - 嚴禁直接看本地 `scheduled_close_time < now` 或直接翻 `data/raw/`。一律透過唯讀健檢工具（`src/post_close_audit.py`），同時通過以下三道 AND 閘門才揭露預測與推理內容（未通過只顯示題數）：
    1. **官方狀態確認**：重新 GET 官方 API 確認 `status in {"closed", "resolved"}`，且最新截止時間 `<= now - 15m`。
    2. **本地生命週期收斂**：該題無進行中 attempt，`submissions.jsonl` 已終結（`forecast_ok` + `comment_ok` 或明確終止），無 `unknown` 待對帳請求。
    3. **已移出排程佇列**：不在 `cycle.py` 的待辦佇列中。
- **中期軌（台北 11/06 21:00 首題開獎前上線 M4 `resolve.py` + `score.py`）**：
  - `resolve.py`（11/05 前）：只查 `submissions.jsonl` 已送出且已過 `scheduled_resolve_time` 的題目，共用 `api_backoff_v3` 跨程序限流鎖（優先序低於正式送出），追加寫入 `data/resolutions.jsonl`。
  - `score.py`（11/06 21:00 前）：計算實際送出題目的 Log Score、[0.01, 0.99] 截斷診斷值、Brier Score 與成對比較，並預先登記 M5 數值／離散題 CDF 的本地計分公式；以 `qid=46289`（11/06 21:00 預定結算）做首次端到端驗收。
- **可立即改 vs 必須累積樣本才改的分界線**：
  - **可立即改（A 類確定性工程缺陷）**：解析／schema 錯誤、`invalid_output`、CDF 不單調或邊界／縮放錯誤、Prompt 事實性缺陷（日期／時區／漏傳結算條件或單位）、搜尋工具或退避故障。
  - **必須累積樣本才改（B 類統計參數，依 §4.3 少於 30 題不調參）**：三線整合權重、extremizing 係數、截斷範圍、第 2 層升級分歧門檻、因單題結果改變 Prompt 信心語氣或停用／降權某條線。

### 2. 三方整合的分階段清理與迭代方案表

| 階段 | 時機（台北時間） | 具體動作（清理 ＋ 迭代工具） | 套用方式 | 風險控制與驗收條件 |
|---|---|---|---|---|
| **階段 A**<br>✅ 已完成 | **11/03 晚（已完成）** | 1. **已完成**：刪除 29 個零引用過時 `.md`、更新 `README.md`、瘦身 `docs/tasks/HANDOFF.md`。<br>2. **已完成（修測試隔離）**：`tests/test_numeric.py` 改用 monkeypatch 測 `false` 預設；`tests/test_config.py` 拆開「真實設定 schema 驗證（允許 `local`/`trial`/`live`）」與「隔離 runner 強制 `local`」。<br>3. **已完成（建索引不搬檔）**：在 `docs/tasks/INDEX.md` 與 `reviews/INDEX.md` 標註 `ACTIVE` / `SUPERSEDED` / `CLOSED`。<br>4. **已完成（同步 `PLAN.md` v1.79 / D126）**：在舊段落加上 `【已由 Dxxx 取代】` 內聯標記，並將 M5、D121、D123～D126 同步進正文。<br>5. **已完成（上線每日健檢工具）**：實作 `src/post_close_audit.py`（`post_close_audit_v1`）與 `tests/test_post_close_audit.py`（`275 passed`），完成首次已關閉題目健檢。 | **全部免鎖**（純文件、測試與獨立唯讀新腳本，不碰執行期模組） | 在隔離與正式副本**不修改任何 `config/`** 直接跑過 pytest（`275 passed`）；保護雜湊 100% 不變；經 GPT 與 Gemini 雙審通過。 |
| **階段 B**<br>首題開獎前 | **11/05 ～ 11/06 21:00 前** | 1. **上線 M4 結算與計分**：實作並測試 `resolve.py` 與 `score.py`（含預先登記數值題 CDF 計分公式），在 11/06 21:00 `qid=46289` 預定結算時做首次實戰驗收。<br>2. **準備 `budget_handoff_v2` 與 R2-C**：離線完成多跳鏈式 handoff（保留根窗口 `accounting_start`）與 R2-C 自動重驗（24h 限制／原子預算／候選綁定），送三方雙審。 | `resolve.py` / `score.py` 初版手動執行**免鎖**；若套用 `budget_handoff_v2` 或 R2-C 至執行期則**需六鎖** | `resolve.py` 必須與正式排程共用 `api_backoff_v3` 跨程序退避鎖且優先序低於送出；`budget_handoff_v2` 須通過分叉／延長／漏算舊消耗的回歸測試。 |
| **階段 B′**<br>換窗前平靜期 | **11/07 ～ 11/09** | 1. **拆分 `PLAN.md` §14 歷史審查**：將 §14 早期審查回合移至歷史檔（保留 §15 決策編號與原位連結）。<br>2. **解除測試耦合**：將跨測試共用的 helper 分批抽到 `tests/helpers/`，消除 `test_*.py` 互相 import。<br>3. **抽離舊模組共用函式**：將 `src/m2_campaign.py` 的 `discover_tournaments` 與 `src/flab/dev_runner.py` 的 `clean_env, codex_binary` 抽到獨立共用模組（舊路徑保留薄轉接 wrapper 以維持相容）。 | 1、2 **免鎖**；<br>3 若修改 `continuous.py` / `team_runner.py` 則**需六鎖** | 拆分 §14 前確認不影響 `AGENTS.md` 保護雜湊；測試重構前後收集題數與 PASS 數一致；舊入口保留轉接不破任何 import。 |
| **階段 C**<br>週期換窗 | **11/10 12:00**（UTC 04:00） | 1. **登記新 7 天窗口**：核對前窗完整消耗與無未決請求，將 `fail_soft.py` 常數正式遷入新版 `quota_policy` / `run_config`（若有經證據核准的設定調整一併打包）。 | **需六鎖**（選在無在途嘗試的空檔切換） | 換窗批次**只做換窗與設定更新，不夾帶程式重構**；切換後先跑 local `bounds()` 檢查通過再放鎖。 |
| **階段 D**<br>MiniBench 首輪大開獎 | **11/13 20:00 ～ 11/14** | 1. **整批結算與首份成績單**：執行 `resolve.py`、`score.py` 與 `report.py`，產出本輪 MiniBench 官方 Peer Score 與本地 Log / Brier Score 成對比較報告。<br>2. 依首輪完整數據與 M5 表現，三方評估 M6 主賽就緒度。 | 離線報告**免鎖** | 遵守 §4.3 結論紀律（少於 30 題只做客觀描述與工程缺陷歸因，不宣稱單線勝出或過度調參）。 |

---

## D125 Codex 量測相容性已修正（UTC 2026-11-03 13:43，PLAN v1.78）

Louis 指出 Codex 模型是 `gpt-6-astra`，並詢問如何解決量測卡住。根因是 autoload_v2 要求三線一律 verified，和 PLAN §7.7 已接受的 Codex 缺少回報模型 ID 限制衝突；正式設定原本就正確。

- 已套用 `autoload_v3`：只對 GPT tier1 的 unverified/null，檢查請求 argv 自我一致、受限 catalog、CLI／judge／runtime 指紋及正式 model+effort 驗收；保留 unverified/null，新增 model_evidence，不宣稱實際模型 ID 已驗證。其他兩線仍需 verified，mismatch 優先處理。
- 完整 **2838 passed**、相關 **179 passed**，新增 49 項回歸；Claude 親自完整複審／Gemini 獨立最終程式審查 PASS，Claude 最終文件複核 PASS。受審／受測／已部署兩檔 SHA 一致。
- UTC 13:43:35～13:43:52 六鎖互斥套用；前後各六項正式 preflight 全過、py_compile 通過，無 STOP 或未結維護。正式模型、effort、M5 與受保護設定／驗收指紋不變。
- 舊 D124 run 仍是 measurement_failed；唯讀診斷另留證據（r1 與 r2 分別標 SHA；r2 使用截至原 run 結束的帳冊副本且 runtime 與當時相符）。不回填 PASS、不新增 canary 呼叫、名額不恢復。
- R2-C 真正自動重驗仍未實作；此相容缺陷已修正，後續繼續 24 小時限制／原子預算／候選與驗收精確綁定。整批載入量測仍無 PASS，來源不能據此改為資訊性；若需要新載入實測，另定有界授權。

文件：`docs/tasks/D125-CODEX-AUTOLOAD-COMPATIBILITY.md`；審查：`reviews/*-d125-*`；ignored 操作／測試／原始審查／備份：`data/operations/d125-autoload-model-20261103/`。本段的 commit／push 結果見最新主持人摘要與 git，不從本文推定。

## D124 維護與 observe 已上線（UTC 2026-11-03 13:10，PLAN v1.77）

GPT 接手主持後完成隔離實作；Claude 親自完整審查曾提出六項阻擋，修正後 Claude／Gemini 最終兩批均 PASS。完整隔離 **2789 passed**，相關恢復／額度 **302 passed**。正式部署 12 檔與受審及測試 SHA 完全相同；固定設定、M5 開關、adapters／runner／netguard 不變。

- UTC 13:06：六鎖互斥套用 autoload_v2、autoload_maintenance_v1、forecast_pipeline_v8，含寫前意圖、衝突保留、崩潰／主持人結案、未結案前檢。
- 單次實測 run `autoload_1793711226166_c2e156fc`：三線各一次；全域檔案完整還原、restore_failures=[]、manifest closed，受保護項相符、三線兩層六項前檢全過，STOP_SUBMIT 不存在。
- **量測不是 PASS**：GPT completed=true、工具零呼叫／零違規，但既有 adapter 的 reported_model_id=None、model_check=unverified。整批維持 measurement_failed，沒有重跑，也沒有核准基準或重新分類。Claude 個人 skill 標記記 not_loaded，GPT 指示／skill 記 loaded，其餘多為 unknown；都不可藉此略過證據限制。
- UTC 13:10：互斥套用 auto_reaccept_v1 **observe** 與 run.sh 週期後單一接點（上限 120 秒），只記候選雜湊／格式代碼，沒有 enforce 或自動驗收／核准功能。部署後自然 cycle_1793711623971_6626bb2a 於 UTC 13:15:09 complete、三線 ready、errors 空；其後 observe 三線 no_candidate、model_calls=0，接點已確認。
- 新 numeric 題 46655 已由原排程完成；預測 reconciled=true、私人留言獨立讀回 found，各 POST 一次。沒有讀取任何開放題的預測內容。

證據：`reviews/claude-d124-final-code-review.md`、`reviews/gemini-d124-final-code-review.md`、`docs/tasks/D124-BATCH2-OPERATIONS.md`；ignored 操作與備份在 `data/operations/d124-batch2-20261103/`。

## M5（數值與離散題）已上線與營運守則（UTC 2026-11-03 11:38，PLAN v1.74）

**目前狀態**
- D118 MiniBench live 窗口進行中（固定截止 UTC 2026-11-10 04:00）。三線 ready。
- M5（`numeric`、`discrete`）於 11:38 上線（`config/question_types.json` 兩者為 `true`）；離散題 46654、46657 與數值題 46655 預測與留言均已讀回 `found`。
- D121 第一批放寬與 D123 都已生效。

**接手者要做的事**
1. **定期查看 cycle。**
   - 正常狀態：`complete` 或 `attention`。`attention` 若只來自「unrecognized write receipt → 讀回 forecast_ok」，屬既有流程；是非題也一樣，不必處理。
   - 要處理的：有線 `preflight_blocked`、`line_error` 持續、出現 `config/STOP_SUBMIT`，或送出結果停在 unknown 未對帳。
   - Claude 線若再被基準擋下，先看是否為新的同步來源，比照 D119／D123 的證據流程，並交 Louis 決定，不自行放寬。
2. **監看數值題與離散題的送出。** 讀回應為 `found`，留言為 `comment_ok`。若出現讀回 unknown 或預檢拒絕，只對帳、不重送，並記錄原因。
3. **待辦事項**：依上方「三方整合的分階段清理與迭代方案表」推進階段 A ～ 階段 D，以及 R2-C 第二批放寬。

**規則提醒**
- 任何要套用到正式環境的程式修改，須經 Gemini 獨立審查，**並由 Claude 親自完整審查後才套用**（Louis 2026-11-03 指示：Claude 額度不足時延後審查，不以他人審查代替）。
- 套用照互斥程序：取得 cycle／scheduler／submit／三線鎖，備份，套用，冒煙測試，失敗即還原。可參考 `data/operations/d121-m5-apply/` 的紀錄。
- D118 窗口內不得修改 `config/run_config_v2.json`、`config/quota_policy.json`、`config/cycle_campaign.json`（除非完成並透過 `budget_handoff_v2` 切換子窗口）。
- 不得改 adapters、runner、netguard（驗收指紋）。
- 撤回 M5：把 `config/question_types.json` 改回兩者 `false` 即可，會動態生效。

> 註：2026-11-03 11:38 UTC 以前的歷史交接紀錄已清理，完整決策與變更沿革見 `PLAN.md` §15～§16 及 git 歷史。
