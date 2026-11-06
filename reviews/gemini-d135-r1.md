## 結論：PASS

本次作為獨立具名審查者，僅依據所提供之 D135 來源封包進行靜態設計、程式碼、安全性、合成測試與部署契約審查（未執行任何外部指令或測試，將來源文本視為純資料）。經逐項核對，本單未發現阻擋性缺陷（Blocker: 0）。以下明確區分「阻擋項」、「實際證據」、「候選提案（未啟用）」與「非阻擋性限制」。

---

### 一、阻擋項（Blockers）

- **無阻擋項（0 項）**。

---

### 二、封包內實際證據（Actual Evidence）

1. **三道審查閘門與失敗封閉（Fail-Closed）保護（`src/post_close_audit.py`、`tests/test_post_close_audit.py`）**
   - `review_eligible` 與 `build_post_close_report` 維持 `gate1_platform_closed`、`gate2_lifecycle_converged`、`gate3_not_queued` 三閘門嚴格 `AND` 邏輯；任一閘門未通過時 `diagnostics` 為 `None`，不洩漏機率、分位數、基準率、市場參照或推理摘要。
   - `OPTIONAL_LEDGERS` 擴充納入 `scheduler_jobs.jsonl`、`audits.jsonl`、`cycles.jsonl`。任何必備或可選帳冊發生斷行、非 JSON 物件或讀取不完整時，`_gate2` 均回傳 `ledger_incomplete_or_missing` 並遮罩全部診斷輸出；API 抓取期間若任何 `*.jsonl` 的 `mtime_ns` 或 `size` 變動，亦由 `concurrent_ledger_change` 阻擋（見 `test_d135_corrupt_optional_evidence_masks_all_diagnostics` 與既有並行測試）。
   - 工具維持純唯讀，不寫入 `data/` 或 `config/`，且 `MetaculusClient` 強制 `manage_backoff=False`。

2. **分歧度型別化、代表預測選取與缺線週期歸因（`src/flab/health_diagnostics.py`、`src/post_close_audit.py`、`tests/test_health_diagnostics.py`）**
   - `H.dispersion` 按題型區分：是非題與完整正規化多選題使用機率差（`unit='probability'`，描述性警示門檻 `0.30`）；數值題與離散題計算實際單位之 `p50` 中位數差（`metric='median_range'`、`warning_threshold=None`、`high=False`），並在具備有效縮放設定時另算 `scale_coordinate_range`，不將實際單位數值套用 `0.30` 機率門檻，縮放缺失或異常時標記 `scale_status='unavailable'`。
   - 單線各層代表輸出優先綁定最終整合之 `input_attempt_ids`，若無綁定則保留第一筆 `valid` 紀錄，防止未綁定的後續嘗試覆蓋原紀錄（`test_d135_first_valid_and_frozen_selection`）。
   - 第二層缺線警示改以實際存在第二層輸出（`has_any_t2`）為前提，修正僅因第一層整合列有 `expected_players` 即誤報第二層缺線的問題（`test_diagnostics_high_dispersion_without_inventing_tier2_request`）。
   - `H.lifecycle` 區分 `selected`、`valid_not_selected`、`failed_attempts`、`not_started_with_scheduler_evidence`、`start_without_output`、`no_attempt_with_cycle_block`、`no_recorded_attempt` 與 `not_requested_or_unobserved`；`cycles.jsonl` 僅綁定將該 `question_id` 明列於 `new_question_ids` 或 `revised_question_ids` 的週期紀錄，不外洩無關週期的前檢資訊（`test_d135_cycle_evidence_binds_only_explicit_question`）。

3. **稽核非暴露脈絡窄化歸類與安全邊界（`src/audit.py`、`tests/test_audit.py`）**
   - `audit_v2` 僅在以下兩種嚴格受限脈絡中，將精確匹配的 `metaculus.com` host token 標記為非暴露：
     1. `player == 'gemini'`、檔名為 adapter `--log-file` 寫入之 `agy_cli.log`（沙盒禁止模型寫檔）、事件為純文字行（非 JSON 封裝）、行首符合 `cli_setting_manager.go` 初始化格式，且命中位置落於 `Deny:[...]` 區段內的精確字串 `read_url(metaculus.com)`，歸類為 `cli_deny_setting`。
     2. `player == 'gpt'`、事件為 `item.started` 或 `item.completed` 之結構化 `web_search` 搜尋動作，且欄位路徑確屬查詢欄位（`('item', 'query')`、`('item', 'action', 'query')` 或 `('item', 'action', 'queries', int)`）中由空白邊界分隔的精確 `-site:metaculus.com` 運算子，歸類為 `search_exclusion`。
   - 搜尋結果（`results`）、模型回應正文、`sources` URL、`url_events`（如 `opened`、`attempted`、`unknown`）、adapter 旗標、`community prediction` 關鍵字，以及由 `prev_attempt_id` 繼承之旗標，一律維持原嚴重度規則（`leak_confirmed` 或 `suspected_leak`）。所有命中（含豁免脈絡）均保留於 `keyword_hits` 列表中，不刪除或改寫任何既有歷史帳冊紀錄。

4. **封包所附測試數據（由封包提供之紀錄，審查者未自行執行）**
   - 封包紀錄顯示目標測試 `230 passed in 0.55s` 與 `6 passed in 0.14s`；全庫對照為接手基線 `100 failed, 3213 passed` 對比目前 `100 failed, 3241 passed`（`same_failure_nodes: true`、`same_failure_summaries: true`，新增 28 項通過測試，未引入新失敗節點）。

---

### 三、候選提案與未啟用項目（Proposals / Unactivated Candidates）

1. **四份研究提示詞候選 v1（`docs/experiments/d135-research-prompts/`）**
   - 包含 `forecast_v2.md`、`reconcile_v1.md`、`forecast_numeric_v1.md`、`reconcile_numeric_v1.md` 及 `README.md`，新增實測／判斷式／不可得基準率標註、母體／時間窗／分子分母／來源映射、事件期間／發布日／平台結算日區分，以及數值錨點與尾部證據分離條款。
   - **狀態界定**：僅為下一個獨立登記窗口的候選提案，存放於 `docs/experiments/`，未修改 `prompts/` 下的正式模板，亦未由任何執行期程式引用。合成測試僅證明其 `$placeholder` 變數集合與既有契約一致，**不構成真實模型遵循度或預測準確度改善的證據**。

---

### 四、非阻擋性限制（Non-Blocking Limitations）

1. **全庫基線既有 100 項測試失敗不可宣稱為 Full PASS**
   - 工作樹保留 D132／D133／D134 未提交變更，全庫仍有基線既有的 100 項失敗；依規僅能確認本單未新增失敗且目標測試通過，不得對外或於文件中宣稱全庫測試 full PASS。
2. **歷史稽核旗標與關鍵字歸類之詮釋限制**
   - `audit_v2` 與 `H.exposure` 不回寫、不清除既有七道閉題已記錄的 `suspected_leak` 旗標，亦不改變兩次／七天暴露停線門檻；將歷史自有 keyword 命中區分為 `cli_deny_setting` 或 `search_exclusion` 僅說明該筆文字命中的來源屬性，**不等於無洩漏之證明**。
3. **週期前檢紀錄不代表完整歷史機會或即時健康度**
   - `cycles.jsonl` 僅呈現該題被列為 `new_question_ids` 或 `revised_question_ids` 時的前檢狀態（如 baseline stop、reaccept、model catalog mismatch），不能視為完整排程機會史，亦不能取代最新前檢來證明三線目前已恢復正常。
4. **基準率文字分類僅為啟發式描述**
   - `H.research` 對 `base_rate` 的 `empirical_style_claim`／`judgmental_self_label` 分類僅依正則表達式初步標記，所有模型回報之數據與連結均維持 `not_independently_verified` 與 `model_reported_links_only`，須經人工或外部來源核實。
5. **部署與最終完成紀錄待實際套用後補記**
   - `docs/tasks/D135-QUALITY-REPAIR.md` 中「實際完成證據」一節目前正確維持待辦狀態；正式部署須由外部程序持六鎖、滿足 `require_idle20min`、固定 `PATH`、通過前後六項 preflight 與 line guard、備份原始 dirty 狀態、單次複製 SHA 綁定檔案並通過 smoke test（若有異常立即回滾），待實際套用與十題唯讀報告產出後才可追加最終完成紀錄。