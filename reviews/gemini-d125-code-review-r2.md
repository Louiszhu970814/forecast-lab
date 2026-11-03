# D125 最終版本（`autoload_v3`）程式碼複審報告

**審查結論（Verdict）：PASS**
- **Blocking Issues（阻擋性問題）**：0 項
- **Non-blocking Proposals（非阻擋性建議）**：0 項（第一輪各項建議均已妥善收斂，無新增程式碼修改要求）

---

## 一、 實際證據核對（Actual Evidence）

### 1. 變更範圍與 SHA-256 雜湊核對
本次變更嚴格限縮於 `src/autoload.py` 與新增測試檔 `tests/test_autoload_model.py`，未觸碰任何驗收指紋保護檔案（`src/adapters/*`、`src/flab/runner.py`、`src/flab/netguard.py`）與 D118 凍結設定檔（`run_config_v2.json`、`quota_policy.json`、`cycle_campaign.json` 等），亦未修改 `src/autoload_maintenance.py` 之名額與鎖控邏輯：
- `src/autoload.py`：`c0a1a2e959c665d4f835bd6f10ff129601f89f7601ec3fd0b718f49978c928e9`
- `tests/test_autoload_model.py`：`876a9b286bb610f7115e31067dbc27ec87147587e563132f45a4084b9b2b0b4f`

### 2. PLAN §7.7 與 D121 C / D124 授權相容性核對
- **規範依據**：`PLAN.md` §7.7 明載「**codex**：目前的事件中沒有模型 ID，記為 `unverified`」，且 D124 原文並無要求三線一律 `model_check == "verified"`；`autoload_v2` 將 `result.model_check == "verified"` 硬編碼為三線共同條件屬於實作與 §7.7 衝突之 bug。
- **語意誠實性（不偽造身分）**：`model_evidence()` 完整保留 `result.model_check == "unverified"` 與 `result.reported_model_id is None` 的真實觀測狀態寫入 `rec["model_evidence"]["gpt"]`，不偽造 `reported_model_id`、亦不將 `model_check` 改標為 `"verified"`，而是以 `accepted: True` 與 `basis: "codex_requested_model_and_accepted_profile"` 加上驗收與 catalog 雜湊證據（`catalog_entry_sha256`、`acceptance_record_id`、`production_record_id`）放行。

### 3. 第一輪回饋與非阻擋建議（N1～N3、N5～N8）收斂查核
1. **相對驗收證據路徑解析（N1）**：
   - `autoload.run()` 呼叫 `model_evidence(..., acceptance_log=data_dir / "acceptance.jsonl", project_root=data_dir.parent)`，確保 `forecast.check_acceptance` 讀取 `evidence_path` 時以該次執行的專案根目錄（`data_dir.parent`）解析相對路徑。
   - `forecast.runtime_fingerprint("gpt")` 刻意維持對當前執行中的 `PROJECT_ROOT / "src"` 取指紋，防止外部傳入任意 `project_root` 下未執行的程式碼冒充本機 runtime 指紋。
   - 新增 `test_relative_acceptance_evidence_uses_the_run_project` 驗證相對路徑解析通過。
2. **`model_check == "mismatch"` 絕對優先與停線連動（N2 / N7）**：
   - 在 `autoload.run()` 的錯誤判定分支中：
     ```python
     error = ("model_mismatch" if result.model_check == "mismatch" else
         result.error_type or (model_proof["basis"] if not model_proof["accepted"]
                              else "incomplete run/version"))
     rec["errors"].append({"player": player, "error": error})
     if error in ('tool_scope_violation','model_mismatch'):
         break
     ```
   - 當 `result.model_check == "mismatch"` 時，無論 `result.error_type` 為 `None` 或其他一般錯誤（如 `'invalid_output'`），皆優先記錄為 `'model_mismatch'` 並立即 `break` 停止後續模型呼叫；進入 `autoload_maintenance.Session.run()` 後會命中 `if any(r.get('error') in ('tool_scope_violation','model_mismatch') for r in result['errors']): raise MaintenanceError('probe reported forbidden scope or model')`，進而在釋放鎖前寫入 `config/STOP_SUBMIT`。
   - 新增 `test_mismatch_always_stops_before_next_player`（涵蓋三線 × `None`/`invalid_output` 共 6 案例）與 `test_controller_stops_on_mismatch_even_with_other_error` 驗證。
3. **Docstring 與註解精確性（N3 / N5）**：
   - `model_evidence` docstring 明示 `result.argv` 為自我一致性檢查（self-consistency），而非伺服器回報之模型身分；並說明控制器另外檢查 CLI identity、baseline、config 與前後六項 preflight。
   - `unclosed()` docstring 已修正為 `"Managed starts need a result AND durable restoration closure."`（移除過時的 `"Only v2"` 字樣）。
4. **Profile 限制與測試覆蓋補強（N6 / N8）**：
   - `model_evidence` 明確限制 `if req.profile != "tier1" or result.argv != gpt.build_argv(req)`，並以 `test_unverified_codex_exception_is_tier1_only` 測試拒絕 `tier2`、`format_repair`、`acceptance` 三種非 `tier1` profile。
   - `accepted` fixture 直接讀取 `load_run_config()['players']['gpt']['tier1']` 對照正式設定，不硬編碼模型字串。
   - 新增 `test_controller_accepts_real_codex_parser_without_stop_or_new_approval` 驗證控制器搭配真實 `gpt.parse` 的完整成功路徑（不寫 `STOP_SUBMIT`、不新增 `baseline_approved` 或 `acceptance_result`）。
5. **測試數據核對**：
   - Packet 紀錄 r1 完整回歸為 `2826 passed in 144.77s`；本輪 `tests/test_autoload_model.py` 共計 49 個測試案例（1 + 6 + 6 + 10 + 2 + 1 + 6 + 5 + 1 + 3 + 6 + 1 + 1 = 49），targeted 測試為 `179 passed in 2.83s`。

### 4. 治理與操作邊界確認
- **不回填歷史帳冊**：D124 已執行的 `autoload_1793711226166_c2e156fc`（`measurement_failed`）及歷史 `acceptance.jsonl` / `autoload_maintenance.jsonl` 保持原狀、唯讀不重寫。
- **不重設名額、不另跑 canary**：`autoload_maintenance.py` 的 `if calls or len(prior_runs) >= 2: raise MaintenanceError('D124 bounded probe already consumed')` 未做任何放寬，本次修正不會消耗新呼叫或重跑 canary。
- **正式入口仍受控**：`autoload.main()` 的 `run` 仍強制限定三線正式設定並委派予 `autoload_maintenance.Session(PROJECT_ROOT).run()`。

---

## 二、 Blocking 與 Non-blocking 區分

### Blocking Issues（阻擋性問題）
- **無（0 項）**。程式碼邏輯、例外處理、失敗關閉（fail-closed）行為與測試覆蓋均完整無誤。

### Non-blocking Proposals / 部署前程序門檻（非程式碼修改項）
1. **R2 完整回歸測試完跑確認（程序門檻）**：Packet 註明「完整 r2 正在執行，套用前必須全過且 SHA 相同。不是拿 r1 完整測試冒充 r2」。主持人在實際套用前，須確認正在執行的 r2 完整回歸測試 100% 通過，且工作區檔案雜湊與本次審核之 SHA-256 完全一致。
2. **互斥套用與前後 Preflight（程序門檻）**：依既定維護程序，於雙審 PASS 及 r2 完整測試通過後，持六把鎖（`cycle.lock`、`scheduler.lock`、`submit.lock` 及三線 `forecast.<player>.lock`）並確認套用前後三線第 1、2 層共六項 preflight 全數通過，且與 `PLAN.md`（§7.7 / §15 / §16）及 D125 文件同步列入同一 commit。