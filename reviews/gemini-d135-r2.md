## 結論：PASS

### 1. 審查依據與證據性質區分
- **審查基礎**：本審查僅依據本次提供的 D135 最終 source packet 進行靜態程式碼與規格審閱，包含檔案清單（manifest）、三個核心程式檔（`src/audit.py`、`src/post_close_audit.py`、`src/flab/health_diagnostics.py`）、三個合成測試檔（`tests/test_audit.py`、`tests/test_post_close_audit.py`、`tests/test_health_diagnostics.py`）、`PLAN.md` 相關節次、`docs/tasks/D135-QUALITY-REPAIR.md`、四份研究提示詞候選模板與 `README.md`，以及差異紀錄與節點清單。
- **未執行聲明**：審查者未執行任何測試、指令、網路請求或部署操作，亦未讀取 packet 以外的本地檔案。Manifest 中所列之 SHA-256 雜湊值係由提交方提供，審查者僅就 packet 內完整呈現之文字內容進行核對。Packet 尾端 diff 區段有截斷（`<truncated 32926 bytes>`），但 manifest 所列之三個 `src/` 檔案、三個 `tests/` 檔案、四份候選提示詞與 `README.md`、`D135-QUALITY-REPAIR.md` 均已在前段完整提供並完成逐行審閱。
- **證據、分析與建議之區分**：
  - **實際證據（Actual Evidence）**：僅指 packet 內附之測試輸出數據與 pytest 收集節點差異清單（collection-node delta）。
  - **程式碼分析（Code Analysis）**：指審查者靜態檢視 packet 內程式碼、測試與文件所得出之技術判斷。
  - **非阻擋限制與建議（Proposals / Non-blocking Limitations）**：屬於防禦性強化或後續維運注意事項，非本次阻擋問題，亦不代表已完成之工作。
- **核准邊界**：本結論僅代表本次獨立審查通過，不構成單方部署授權，亦不發明或代替另一位審查者（如 Gemini）在同一 SHA 下之獨立審查結果或 Louis 之最終決定。

---

### 2. 第一輪阻擋問題（Blocker）與修正項目複核（程式碼分析）

1. **原阻擋問題 B1（`health_diagnostics.exposure()` 嚴重度與稽核帳冊旗標聯集）— 已修正並驗證**：
   - **修正內容**：於 `src/flab/health_diagnostics.py` 的 `exposure(rows, events)` 中，已將綁定同一 `attempt_id` 的預測列（`rows`）與稽核事件（`audits`）之 `audit_flags` 及 `own_audit_flags` 取聯集：
     `flags = {f for r in [*rows, *audits] for key in ('audit_flags', 'own_audit_flags') for f in (r.get(key) or [])}`
   - **嚴重度優先順序**：`status` 現以 `'leak_confirmed' in flags` 最高優先判定為 `'confirmed_exposure_requires_review'`，其次 `'suspected_leak' in flags` 判定為 `'requires_evidence_review'`，其餘才為 `'recorded_evidence_only'`。同時 `flags_retained` 保留聯集後排序之完整旗標。
   - **合成測試覆蓋**：於 `tests/test_health_diagnostics.py` 新增 `test_exposure_uses_all_bound_flags_at_highest_severity`（參數化 6 組，涵蓋 `forecast`/`audit` × `audit_flags`/`own_audit_flags` × `leak_confirmed`/`suspected_leak`，並驗證 `keyword_hits: None` 防護與無關 `attempt_id` 隔離）及 `test_exposure_confirmed_dominates_suspected`（驗證 `leak_confirmed` 與 `suspected_leak` 並存時由 confirmed 優先主導）。B1 已完全解除。

2. **原非阻擋項 N1（Gemini CLI 設定豁免之前界檢查）— 已修正**：
   - 於 `src/audit.py` 的 `_non_exposure_kind` 中，在比對 `chunk[match.start()-9:match.end()+1] == 'read_url(metaculus.com)'` 之外，新增前界條件 `(match.start()-9 == prefix.end() or chunk[match.start()-10].isspace())`，避免 `xread_url(metaculus.com)` 被誤判為豁免。
   - 於 `tests/test_audit.py` 新增 `test_cli_setting_requires_permission_token_boundary` 驗證。

3. **原非阻擋項 N5（週期證據 `question_id` 型別正規化與列表檢查）— 已修正**：
   - 於 `src/post_close_audit.py` 的 `_build_diagnostics` 中，改為：
     `cycles = [r for r in ledgers["cycles.jsonl"] if any(_same_id(qid, x) for key in ("new_question_ids", "revised_question_ids") for x in (r[key] if isinstance(r.get(key), list) else []))]`
   - 確保 `int`/`str` ID 透過 `_same_id` 一致比對，且當欄位非 `list`（例如誤傳字串）時不會發生子字串誤綁或型別崩潰。
   - 於 `tests/test_post_close_audit.py` 新增 `test_d135_cycle_ids_are_typed_and_normalized`（參數化測試 `["101"]` 與 `"101"`）驗證。

4. **原非阻擋項 N12（提示詞候選啟用之升版規範）— 已補充說明**：
   - 於 `docs/experiments/d135-research-prompts/README.md` 與 `docs/tasks/D135-QUALITY-REPAIR.md` 明確記載：啟用時須另給新的正式 prompt 版本識別（如 `forecast_v3`）並寫入 `PLAN.md` §16，不得以同名檔案直接覆寫既有固定版本。

---

### 3. 程式碼、整合、安全性與規格不變式審查（程式碼分析）

1. **三道閘門與防洩漏遮蔽（`src/post_close_audit.py`）**：
   - Gate 1（平台關閉且超過 `min_closed_minutes`）、Gate 2（本地生命週期收斂且無併發寫入或帳冊損毀）、Gate 3（未排隊且最新快照未失效）維持嚴格 `AND` 邏輯（`eligible = bool(g1 and g2 and g3)`）。
   - 新增的三個可選帳冊（`scheduler_jobs.jsonl`、`audits.jsonl`、`cycles.jsonl`）納入 `_load_ledgers` 嚴格掃描；一旦存在截斷、非 JSON 物件或讀取不完整，Gate 2 立即失敗（`ledger_incomplete_or_missing`），`diagnostics` 為 `None`，確保 fail-closed。
   - 僅唯讀操作，不寫入 `data/` 或 `config/`，live 模式下 `MetaculusClient` 以 `manage_backoff=False` 呼叫，不寫入 `api_cooldowns.jsonl`。

2. **健檢診斷精確性（`src/post_close_audit.py`、`src/flab/health_diagnostics.py`）**：
   - **代表輸出選取**：有整合 `input_attempt_ids` 時優先選取凍結輸入，否則取第一份 `valid` 輸出（不再以最後一筆覆寫）。
   - **分歧度（Dispersion）**：數值與離散題以題目實際單位計算 `p50` 中位數極差（`median_range`），並透過 `numeric.scaling(snapshot).to_u` 計算尺度座標差（無法建構尺度時標為 `scale_status: 'unavailable'`），不套用 0.30 機率門檻；是非題與完整多選題（各選項機率介於 0 到 1 且總和誤差 `<= 0.01`）維持 0.30 警示門檻。舊欄位 `tier1_spread`／`tier2_spread` 僅在 `unit == 'probability'` 時填值，語義一致。
   - **缺線與週期歸因（Lifecycle）**：不再將 `expected_players` 當作已要求 Tier 2 的證據；明確區分 `selected`、`valid_not_selected`、`failed_attempts`、`not_started_with_scheduler_evidence`、`start_without_output`、`no_attempt_with_cycle_block`、`no_recorded_attempt` 與 `not_requested_or_unobserved`，並標註週期紀錄僅為觀察而非完整因果歷史。
   - **研究品質（Research）**：將基準率文字分類為啟發式標籤，明示 `not_independently_verified` 與 `model_reported_links_only`，不將模型自報連結或文字視為已驗證事實。

3. **稽核分類精確性與安全性（`src/audit.py`）**：
   - **Gemini CLI 設定豁免（`cli_deny_setting`）**：嚴格限制於 `player == 'gemini'`、檔名為 host 端捕捉之 `agy_cli.log`、事件為純字串行、符合 `cli_setting_manager.go` 初始化格式、位於 `Deny:[` 與 `] Ask:` 之間，且具備空白或左括號邊界的精確 `read_url(metaculus.com)` token。模型於沙盒內無寫檔權限，無法偽造 host 端 `--log-file`。
   - **GPT 搜尋排除詞豁免（`search_exclusion`）**：嚴格限制於 `player == 'gpt'`、`item.started`/`item.completed` 之 `web_search` 事件、結構化 query 欄位（`('item', 'query')`、`('item', 'action', 'query')` 或 `('item', 'action', 'queries', int)`）中具備空白邊界的完整 `-site:metaculus.com` 運算子。搜尋結果（`results`）、模型回應正文、其他欄位與非排除語法（如 `site:metaculus.com`、`-site:metaculus.com/path`）一律維持 `exposure` 並觸發 `suspected_leak`。
   - **不變式維持**：所有命中仍完整保留於 `keyword_hits`；`sources` 含 metaculus、URL `opened` 事件、adapter 旗標與 `prev_attempt_id` 繼承旗標完全不放寬，亦不修改或清除任何歷史稽核紀錄與停止門檻。

---

### 4. 實際證據核對（僅依據 Packet 內附輸出，審查者未執行）

- **目標測試與部署故障測試輸出**：
  - 目標測試套件：`240 passed in 1.44s`（較第一輪 230 passed 增加 10 個測試案例：含 `test_audit.py` +1、`test_post_close_audit.py` +2、`test_health_diagnostics.py` +7）。
  - 部署故障測試：`6 passed in 0.16s`。
- **完整測試套件基線比對**：
  - 基線（baseline）：`100 failed, 3213 passed in 23.36s`（共 3313 節點）。
  - 目前（current）：`100 failed, 3251 passed in 23.37s`（共 3351 節點）。
  - Packet 記載 `same_failure_nodes: true`、`same_failure_summaries: true`，且附上 `added_nodes`（39 項）與 `removed_nodes`（1 項 `test_diagnostics_high_dispersion_and_missing_tier2` 改名），淨增 +38 個通過節點（包含既有參數化測試 `test_gate2_fails_when_ledger_has_truncated_json_line` 因 `OPTIONAL_LEDGERS` 新增 3 個帳冊而自動擴展之 3 個節點），第一輪的節點數量疑問已完整釐清。
  - **重要限制**：100 個既有失敗仍然存在，此結果僅證明相對於接手基線無新增退化（same-100 baseline comparison），**絕不可宣稱為完整測試通過（full PASS）**。

---

### 5. 非阻擋限制與後續建議（Non-blocking Limitations & Proposals）

1. **格式防禦性與邊界情況（建議）**：
   - `health_diagnostics.py` 假設傳入之帳冊列內部欄位符合預期結構（例如 `cycles.jsonl` 的 `players` 為 `dict`、`preflight` 為 `dict`、多選題 `options` 元素可雜湊）。若遇到 JSON 語法合法但內部欄位型別異常的損毀列，執行時會拋出例外中止（屬 fail-closed，不會洩漏未合格題目內容，但會使報告生成中斷）。日後可考慮補充欄位型別防護。
   - `lifecycle()` 內部以 `r.get('tier', 1) == tier` 及 `r.get('player') == player` 過濾，而 `post_close_audit.py` 的 `_tier()`／`_player()` 支援 `"tier2"`、`stage`、`player_id` 等別名；目前標準帳冊使用一致的 `player` 與整數 `tier`，但若未來讀取異質舊紀錄，建議統一共用正規化輔助函式。
   - `render_markdown()` 的 `_fmt()` 對 `list`/`dict` 直接輸出 JSON 字串而未跳脫 `|`，若診斷文字內含直線符號可能影響 Markdown 表格排版（既有非阻擋外觀限制）。
2. **Gemini Deny 規則格式範圍（限制說明）**：
   - `_non_exposure_kind` 僅豁免精確的 `read_url(metaculus.com)`。若未來 CLI 設定改寫為萬用字元或子網域形式，仍會保守歸類為 `exposure`。
3. **提示詞候選之使用限制（程序要求）**：
   - `docs/experiments/d135-research-prompts/` 下四份候選模板要求在 `key_factors` 記錄來源發布與擷取時間，與既有「每項一個短語」的簡短慣例可能存在張力；在下一個獨立窗口正式登記並以新版本號（如 `forecast_v3`）啟用前，須觀察模型實際輸出長度與格式遵循情形。
4. **部署與文件收尾（維運注意事項）**：
   - 本次 packet 的 diff 尾端因長度截斷未重現完整部署腳本，請在實際執行部署時嚴格遵守 D135 規範：持有六鎖、`require_idle` 20 分鐘、固定外部 `PATH`、套用前後各執行六項 preflight 與 `line_guard`、備份原始未提交狀態、驗證 SHA-256 並執行正式 root smoke 測試。
   - `docs/tasks/D135-QUALITY-REPAIR.md` 的「實際完成證據」一節目前維持「待實際雙審、部署及十題新版唯讀報告後補記」，符合「實際套用後才補記完成證據」之紀律。

---

### 6. 明確不可宣稱事項
- **不可宣稱完整測試套件全數通過（full PASS）**：目前仍有 100 個既有失敗節點。
- **不可宣稱提示詞候選已獲真實模型驗證或提升準確率**：四份候選提示詞僅完成合成模板渲染與佔位符相容性檢查，未在執行期啟用，亦無結算準確率證據。
- **不可宣稱歷史嘗試「無洩漏」或清除既有旗標**：舊有 7 題之 `suspected_leak` 等歷史旗標完全保留，不回寫、不抹除；新增分類僅說明特定關鍵字命中之來源性質。
- **不可重跑舊題、不可產生新預測或網路提交、不可啟用主賽**：固定之正式提示詞、模型、統計參數、額度上限與 D118 窗口均維持不變。