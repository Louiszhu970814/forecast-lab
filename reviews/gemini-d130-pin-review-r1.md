# D130 Gemini pin／有界維護審查

官方CLI；對應來源SHA見 d130-pin-manifest.json。

**審查結論：PASS（無 Blocker，可安全部署並執行維護腳本 `repair_gemini_pin.py`）**

### 1. Blockers（阻塞項）
* **無**。Wrapper、維護腳本與測試均符合 D21 general-only 授權邊界與 fail-closed 安全要求，未替 Louis 批准任何 Claude/GPT 插件或未授權內容變更。

### 2. Actual Evidence（實際證據與腳本已內建之強制檢查）
* **Packet 已附實證（僅靜態與 Smoke 驗證，尚未執行實機模型驗收）**：
  1. **Wrapper 與 Binary 驗證**：`agy-wrapper.sh`（SHA `93743416...`）在執行前強制檢驗 `data/cli_pins/agy/1.4.12` 為非 symlink 之可執行一般檔案，且 SHA-256 必須等於 D124 實機版本 `34423fc9...`，否則以 exit 69 fail-closed；並強制 `export AGY_CLI_DISABLE_AUTO_UPDATE=true`。僅在單一參數 `changelog` 時映射至已驗章 binary 的 `--version`，不偽造版本字串。
  2. **Smoke 與單元測試證據**：`agy-real-wrapper-smoke-r2.json` 證實真 wrapper `changelog` 與直呼 binary `--version` 均輸出 `1.4.12\n`（returncode 0、`models_called: 0`）；`test_agy_cli_pin.py`（SHA `de3c6e2b...`）涵蓋參數原樣傳遞、環境變數覆寫、缺檔/權限錯誤/雜湊不符/symlink 之 exit 69 阻斷；`protected-checks.json` 證實 28 項受保護核心檔案 SHA 完全不變。
* **`repair_gemini_pin.py`（SHA `9ec3c9ff...`）實際強制檢查之安全條件**：
  1. **不重試與呼叫上限**：透過 `operations.jsonl` 檢查 `gemini_pin_reaccept_started` 確保單次執行不自動重試；`BoundedAdapter` 嚴格限制最多 3 次 `gemini-3.8-flash-low` 呼叫，超時或失敗即中止且標記 `restored=False`。
  2. **環境、配額與不變量守門**：在 `Session` 鎖與正式 `PATH` 下檢查無 `STOP_SUBMIT`、無未完成驗收或寫入、滿足 `HORIZON_S=2100` 閒置預算、週配額充足，以及前後新鮮觀測之 `remaining_credits == 0`；每通呼叫前後均再次驗證 `identity()`、`session.protected_hashes()` 與 `A.code_fingerprint("gemini")`。
  3. **既有正式證據與實機 Log 驗證**：`production_proofs()` 強制確認 Tier 1/2 既有 `1.4.12` 正式驗收證據與當前 runtime 指紋相符；`isolation_cli_logs()` 逐一驗證 3 個 phase 的 `agy_cli.log` 雜湊與正規表達式解析出之實際 CLI 版本均為 `1.4.12`。
  4. **最小範圍核准（Scoped Approval）**：`validate_snapshot()` 與 `B.approve(..., player="gemini")` 強制驗證 `agy.cli_settings` 之 `CONTENT` 指紋與舊 baseline 完全相同、僅更動已授權之 `GENERAL` 設定與恢復 `1.4.12`，完全不觸碰亦不批准 Claude/GPT 插件或其他來源，最後通過 Tier 1/2 `continuous=True` preflight（無 waiver）始完成。
* **狀態聲明**：目前僅完成腳本與 Wrapper 準備及無模型呼叫之 smoke 驗證，**尚未執行實機 3-call 模型驗收，不聲稱實機驗收已成功或產線已恢復**。

### 3. Proposals（建議事項）
* **無額外修改建議**：請直接按既定單次維護流程執行 `repair_gemini_pin.py`，並以實機執行後之 `operations.jsonl` 與 `acceptance.jsonl` 結果為準。