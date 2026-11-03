# Forecast Lab 獨立程式碼審查報告（`autoload_v3` Codex §7.7 相容性修正批次）

- **審查模型**：Gemini 4 Argon (High)
- **審查結論**：**PASS**（**0 項 Blocking 問題**；下述列出之事項皆為 Non-blocking 觀察或既有未改設計之標示）
- **證據邊界聲明**：本報告完全基於所提供之 Source Packet 進行靜態程式碼與契約審查。**審查過程未執行任何測試或外部指令**；Source Packet 所載之「隔離 workcopy 167 項 targeted 測試通過（2.71s）」為既有提交紀錄之陳述，且完整回歸測試仍在進行中、尚未宣稱完成。本報告不捏造任何測試執行結果或額外核准（approval）。

---

## 一、 變更範圍與不變量核對（Actual Evidence）

1. **變更檔案邊界**：
   - `src/autoload.py`（版本由 `autoload_v2` 升為 `autoload_v3`，SHA-256: `55775ef01e691ad8402909417dc61869c5410892db0e9556285f72b356cdd12a`）
   - `tests/test_autoload_model.py`（新增針對真實 `adapters.gpt.parse()` 的單元與整合回歸測試，SHA-256: `b632416570e1183e907077b4a7663f584aaa169f9c9c0cfb7306868a2ed6e3ef`）
2. **受保護元件與帳冊不變性**：
   - 未修改 `src/adapters/gpt.py`、`src/adapters/base.py`、`src/flab/runner.py`、`src/flab/netguard.py`、`src/acceptance_judge.py`、`src/autoload_maintenance.py`、凍結設定檔（config）或預測 prompt。
   - `src/autoload.py` 只寫入 `autoload_run_started`、`acceptance_call_started`（`source="autoload"`）、`acceptance_call_finished`（`source="autoload"`）及 `autoload_result`，**不寫入 `acceptance_result`、不寫入 `baseline_approved`、不竄改歷史 `measurement_failed` 紀錄**，亦未啟用 R2 `auto_reaccept` enforce。

---

## 二、 五項指定審查重點逐項分析

### (1) 是否忠實實現 PLAN §7.7 而沒有偽造模型身分
- **實證分析（`src/autoload.py:469-511`, `609-610`；`src/adapters/gpt.py:130-132`）**：
  - PLAN §7.7 明定：`codex` 目前的事件中沒有模型 ID，記為 `unverified`；受保護的 `src/adapters/gpt.py:130-132`（`parse()`）一律回傳 `reported_model_id=None, model_check='unverified'`。
  - 新增的 `model_evidence()` 在 `evidence` 字典中明確區分：
    - `"requested_model": req.model`
    - `"requested_effort": req.effort`
    - `"reported_model_id": result.reported_model_id`
    - `"model_check": result.model_check`
  - 當 Codex 通過窄範圍驗收證據鏈時（`src/autoload.py:508-511`），回傳 `accepted=True` 與 `basis="codex_requested_model_and_accepted_profile"`，但**保留 `reported_model_id=None` 與 `model_check="unverified"` 原值**，並未將 `req.model`（如 `"gpt-6-astra"`）填入 `reported_model_id`，也未將 `model_check` 改寫為 `"verified"`。
- **判定**：完全符合 PLAN §7.7，無偽造模型身分。

### (2) Evidence 是否足夠窄且能阻擋錯模型／不同 effort／變更 catalog／CLI／runtime／無效 proof／unfinished acceptance
- **實證分析（`src/autoload.py:482-511` 對照 `src/forecast.py:177-243`）**：
  1. **Player / 狀態 / Reported ID 收斂（`src/autoload.py:484-485`）**：
     - 僅當 `req.player == "gpt"`、`result.model_check == "unverified"` 且 `result.reported_model_id is None` 時才進入此路徑。
     - 若 Codex 的 `model_check` 為 `"mismatch"` 或 `"undetermined"`，或未來 CLI 回報了非 `None` 的 `reported_model_id` 卻仍為 `"unverified"`，一律拒絕（`basis="model_check_rejected"`）。
  2. **Profile 與完整 `argv` 綁定（`src/autoload.py:488-489`）**：
     - 強制 `req.profile == "tier1"` 且 `result.argv == gpt.build_argv(req)`（完整 list 相等）。
     - 任何模型參數（`-m` 或額外重複 `-m`）、`model_reasoning_effort`、缺少 `--ignore-user-config` / `--strict-config`、啟用任何 `DISABLED_FEATURES` 或 subagents、`model_catalog_json` 路徑或 `raw_dir`/`workdir` 變更，皆因 `result.argv != gpt.build_argv(req)` 被拒絕（`basis="codex_argv_differs"`）。
  3. **On-disk Restricted Catalog 與 Result Hash 雙重綁定（`src/autoload.py:491-497`）**：
     - 讀取實際 `req.raw_dir / gpt.CATALOG_FILENAME`，重新套用 `gpt.restricted_catalog(catalog, req.model)`（強制檢查 `tool_mode="direct"`、`node_repl_disabled=True` 且移除 `apply_patch_tool_type`），並計算 `catalog_sha = gpt.catalog_entry_sha256(catalog, req.model)`。
     - 驗證 `catalog_sha == gpt.catalog_entry_sha256(restricted, req.model)`（確保磁碟上的 catalog 已是受限後狀態）且 `catalog_sha == getattr(result, "catalog_entry_sha256", None)`（確保與 `result` 記錄之 hash 一致），否則拒絕（`basis="codex_catalog_differs"`）。
  4. **現行有效 Production Acceptance 綁定（`src/autoload.py:498-505`）**：
     - 呼叫 `forecast.check_acceptance(pf, forecast._acceptance_records(acceptance_log, "gpt"), forecast.runtime_fingerprint("gpt"), project_root=project_root, production={"model": req.model, "effort": req.effort})`：
       - 驗證最新一筆 `acceptance_result`（`latest`）之 `line_pass is True`、`judge_version == J.JUDGE_VERSION`、`cli_version == result.cli_version`。
       - 驗證 `latest` 的 `evidence_path` 檔案存在、SHA-256 與 `evidence_sha256` 吻合，且 `code_sha256` 與當前 `forecast.runtime_fingerprint("gpt")`（含 `adapters/gpt.py` 與 `RUNTIME_FILES`）完全一致。
       - 驗證存在通過的 `mode == "production"` 驗收紀錄，其 `(model, effort) == (req.model, req.effort)`，且同樣滿足當前 `cli_version`、`judge_version`、`evidence_sha256` 與 `runtime_fingerprint("gpt")`。
     - 驗證 `not forecast.incomplete_acceptance(acceptance_log, "gpt")`：若在最新 `acceptance_result` 之後有任何非 `autoload` 的未完成 `acceptance_call_started`，立即拒絕。
     - 驗證 `catalog_sha == latest.get("catalog_entry_sha256")`：確保本次執行的 restricted catalog entry 與最新驗收紀錄完全相同。
  5. **異常處理 Fail-closed（`src/autoload.py:506-507`）**：
     - 捕捉 `(OSError, ValueError, KeyError, TypeError, StopIteration)` 並回傳 `accepted=False, basis="codex_evidence_unavailable"`。
- **判定**：證據條件極為嚴密且完全唯讀（不呼叫 live CLI 或讀取預測資料），能有效阻擋題述所有異常情境。

### (3) 保留其他兩線既有規則、工具限制與恢復安全
- **實證分析（`src/autoload.py:482-485`, `608-625`）**：
  - 對 `claude` 與 `gemini` 而言，`req.player != "gpt"`，故 `model_evidence()` 僅在 `result.model_check == "verified"` 時回傳 `accepted=True`，其餘狀態（`unverified`、`undetermined`、`mismatch`）皆回傳 `accepted=False, basis="model_check_rejected"`，判定條件與 `autoload_v2` 完全相同。
  - 工具限制（`allowed_calls`、`result.tool_call_counts`、`forecast.tool_violations(player, result, repair=True)`）、程序退出碼／逾時／信號檢查、以及 `Placement` 備份／還原／`unclosed()`／`recover_placement()` 均保持原樣未動。
  - 錯誤分類與停線（`src/autoload.py:620-624`）：
    ```python
    error = result.error_type or ("model_mismatch" if result.model_check == "mismatch" else
        model_proof["basis"] if not model_proof["accepted"] else "incomplete run/version")
    rec["errors"].append({"player": player, "error": error})
    if error in ('tool_scope_violation','model_mismatch'):
        break
    ```
    當 `result.model_check == "mismatch"` 時（即便 adapter 未預先將 `result.error_type` 設為 `"model_mismatch"`），`error` 會明確記為 `"model_mismatch"`，在 `autoload.run()` 立即 `break`，並於 `autoload_maintenance.Session.run()`（`src/autoload_maintenance.py:261-262`）觸發 `MaintenanceError('probe reported forbidden scope or model')` 進而寫入 `config/STOP_SUBMIT`。

### (4) 在維護六鎖內是否有死鎖／讀取錯 project 或 log
- **死鎖檢查（無死鎖）**：
  - `autoload_maintenance.Session.locks()`（`src/autoload_maintenance.py:30-31`, `86-103`）持有的六把鎖為 `cycle.lock`、`scheduler.lock`、`submit.lock`、`forecast.claude.lock`、`forecast.gemini.lock`、`forecast.gpt.lock`。
  - `autoload.run()` 內呼叫 `model_evidence()` 時（`src/autoload.py:609`），前一個 `finally` 區塊的 `_append(data_dir / "acceptance.jsonl", ...)` 已離開 `with transaction(log.parent):`。
  - `model_evidence()` 內部呼叫之 `forecast.incomplete_acceptance(acceptance_log, "gpt")`（`src/forecast.py:186`）僅短暫取得 `with transaction(log.parent):` 讀取 `acceptance_log` 後即釋放，完全不涉及上述六把維護鎖，故在六鎖內執行**不存在死鎖風險**。
- **Project / Log 路徑讀取檢查**：
  - 在 `autoload.run()`（`src/autoload.py:609`）中，`acceptance_log=data_dir / "acceptance.jsonl"` 正確綁定至傳入的 `data_dir`（由 `Session.run` 傳入 `self.root`）。
  - 關於 `project_root` 預設值之細節，請見下方「四、Non-blocking 觀察與建議」第 1 點（在正式環境 `Session(PROJECT_ROOT)` 下完全一致，不構成阻斷）。

### (5) 測試是否能抓回原 Defect
- **實證分析（`tests/test_autoload_model.py:1-169`）**：
  - 原 Defect 成因是既有測試輔助函式 `_result(req)` 皆以合成的 `model_check="verified"` 測試三條線，忽略了受保護的真實 `adapters.gpt.parse()` 永遠產生 `reported_model_id=None, model_check="unverified"`。
  - 新增的 `tests/test_autoload_model.py` 透過 `parsed_result()` 將最小真實結構的 Codex 事件寫入 `events.jsonl`、`last.json` 與 `model_catalog.json`，直接呼叫未修改的 `gpt.parse(req)`，確認產出 `model_check == 'unverified'` 與 `reported_model_id is None`，並覆蓋：
    - 真實 `gpt.parse()` 輸出通過 `model_evidence()` 與完整 `A.run()`（且不竄改 `model_check` / `reported_model_id`、不新增 `acceptance_result` 或 `baseline_approved`）；
    - 6 種 `argv` 變異拒絕；
    - 6 種 catalog 缺失／未受限／雜湊不符拒絕；
    - 10 種 acceptance 紀錄缺失／失敗／版本／judge／runtime fingerprint／evidence hash／effort／model／pending call／missing proof 拒絕；
    - Codex `mismatch` / `undetermined` 與非空 `reported_model_id` 拒絕；
    - `claude` 與 `gemini` 在 `unverified` / `undetermined` / `mismatch` 下維持拒絕。
  - 若退回 `autoload_v2` 的 `result.model_check == "verified"` 條件，`test_real_parser_unverified_is_accepted_without_relabeling_identity` 與 `test_whole_measurement_with_real_codex_parser[None]` 會立即失敗，能確實抓回原 Defect。

---

## 三、 Blocking 問題清單

- **無（None）**。

---

## 四、 Non-blocking 觀察與建議（Proposals & Pre-existing Notes）

以下事項均不影響本次正式環境修正之安全性與正確性，僅供後續維護或測試擴充參考：

1. **[Non-blocking 建議] `autoload.run()` 呼叫 `model_evidence()` 與 `runtime_fingerprint()` 時之 `project_root` 傳遞（`src/autoload.py:499-502`, `609`）**
   - **位置**：`src/autoload.py:501`、`src/autoload.py:609`
   - **說明**：
     - `model_evidence()` 定義了關鍵字參數 `project_root: Path = PROJECT_ROOT`，並將其傳給 `forecast.check_acceptance(..., project_root=project_root)` 以解析相對路徑的 `evidence_path`，但同處的 `forecast.runtime_fingerprint("gpt")` 仍使用預設的 `src=PROJECT_ROOT / "src"`，且 `autoload.run()` 第 609 行呼叫 `model_evidence(req, result, acceptance_log=data_dir / "acceptance.jsonl")` 時未顯式傳入 `project_root`。
     - 在正式執行路徑（`autoload.main()` -> `Session(PROJECT_ROOT).run()`）中，`data_dir` 即為 `PROJECT_ROOT / "data"`，且此寫法與既有的 `forecast.preflight()`（`src/forecast.py:300`）完全一致；而在 `tests/test_autoload_model.py:25-32` 中，`evidence_path` 為 `tmp_path` 下的絕對路徑，因此 `project_root / evidence_path` 亦能正確解析。
   - **建議（非必要修正）**：若未來希望在 `test_autoload_maintenance.py` 等使用獨立 `project = fx.home.parent / 'project'` 且帶有相對 `evidence_path` 的測試中直接測試 Codex unverified 路徑，可考慮在 `model_evidence()` 中將 `src=project_root / "src"` 傳給 `forecast.runtime_fingerprint("gpt", src=project_root / "src")`（或維持現行與 `forecast.preflight()` 一致的行為）。

2. **[既有未改行為標示] `tests/test_autoload_maintenance.py` 預設 Fixture 仍使用合成 Verified Result（`tests/test_autoload_maintenance.py:34`）**
   - **位置**：`tests/test_autoload_maintenance.py:32-38`
   - **說明**：`test_autoload_maintenance.py` 的 `session` fixture 專注於測試維護控制器的六鎖、配額、中斷與恢復狀態機，其 `execute` mock 仍回傳 `_result(req)`（即 `model_check="verified"`）。由於本批次已在 `tests/test_autoload_model.py` 的 `test_whole_measurement_with_real_codex_parser` 補齊 `A.run()` 搭配真實 `gpt.parse()` 的整合測試，此既有測試結構無須在本批次更動。