# Forecast Lab D124（Phase 0／Phase 1）實作程式碼審查報告

## 1. 審查結論（Verdict）

- **審查結果**：**PASS**（針對來源包所提供之 D124 Phase 0／Phase 1 實作程式碼與測試套件之靜態審查通過）。
- **證據等級與範圍聲明**：本報告完全基於來源包（Source Packet）內之程式碼與規格文本進行靜態審查；未執行任何工具、測試、指令、網路存取或即時預測，亦不宣稱已執行測試。本結論僅涵蓋 D124 Phase 0／Phase 1 程式碼本身，不構成對未部署之 Phase 2（`auto_reaccept`）之核准。

---

## 2. 阻擋項（Blockers）

- **程式碼阻擋項（Code Blockers）**：**無（0 項）**。
- **執行前程序前提（Operational Prerequisites，非程式碼缺陷）**：
  1. 題述指出完整隔離測試套件仍在執行中（目前已通過 175 項針對性測試）；正式套用 patch 與執行維護量測前，須待完整測試套件執行完畢並確認全數通過。
  2. 部署與執行時須維持受保護之執行期檔案（`src/adapters/{claude,gemini,gpt}.py`、`src/flab/runner.py`、`src/flab/netguard.py`）及凍結設定檔（`config/{run_config_v2,quota_policy,cycle_campaign,submit_mode,question_types}.json`）位元組完全不變（`Session.protected_hashes()` 亦會在執行前後自動強制核對）。

---

## 3. 逐項審查要求與實際程式碼證據（Actual Evidence）

### 3.1 六把鎖固定順序取得與釋放（Six Locks in Fixed Order）
- **證據位置**：`src/autoload_maintenance.py`（`LOCKS`、`MAX_WAIT_S`、`Session.locks`、`Session.recovery_locks`）、`src/forecast.py`（`Lock`）、`tests/test_autoload_maintenance.py`（`test_every_busy_lock_refuses_and_releases_partial_locks`、`test_failure_sets_stop_before_unlocking`）。
- **實作核對**：
  1. `LOCKS = ('cycle.lock', 'scheduler.lock', 'submit.lock', 'forecast.claude.lock', 'forecast.gemini.lock', 'forecast.gpt.lock')` 嚴格定義六把鎖之固定順序。
  2. `Session.locks()` 以 `ExitStack` 依序進入 `F.Lock(self.root/name)`（內部使用 `fcntl.flock(..., LOCK_EX | LOCK_NB)` 非阻塞取鎖）。
  3. 任一鎖被占用拋出 `F.PreflightError` 時，立即呼叫 `stack.close()` 以反向（LIFO）順序釋放已取得之部分鎖，並每次等待 `min(15, left)` 秒；逾 `MAX_WAIT_S = 900` 秒（15 分鐘）仍未取得全部鎖則拋出 `MaintenanceError('maintenance lock wait expired')`，期間不發送任何訊號或終止任何程序，亦未放入任何 canary。
  4. `Session.run()` 與 `Session.recover()` 若在持鎖期間發生異常，皆會在離開 `with self.locks():` 區塊前先執行 `self.stop(run_id)` 建立 `config/STOP_SUBMIT` 並 `fsync`，確保不會在釋放鎖後留下無防護之異常狀態。

### 3.2 模型呼叫前之閒置、預算、新鮮零點數與配額准入閘門（No Model Before Idle / Budget / Fresh Credits=0 Admission）
- **證據位置**：`src/autoload_maintenance.py`（`require_idle`、`weekly_usage`、`Session.snapshot`、`Session.run`）、`src/autoload.py`（`_start_call`、`run`）、`src/quota.py`（`observation_fresh`、`manual_admission`）。
- **實作核對**：
  1. **閒置與排程安全（`require_idle`）**：
     - 核對 `forecast_attempt_starts.jsonl` 與 `forecasts.jsonl` 無未完成之預測嘗試。
     - 核對 `acceptance.jsonl` 無未結案之 `acceptance_call_started`（排除目前正由 `recover` 處理之 `recovering` run）。
     - 收集所有歷史已啟動嘗試之 `workdir` 與 `raw_dir`，呼叫 `orphan.no_process(paths)` 同時以 `/bin/ps -axo pid=,command=` 與 `/usr/sbin/lsof -nP -Fpn` 確認無殘留程序（若指令失敗、有 stderr 或無輸出皆回傳 `False` 拒絕）。
     - 核對 `submissions.jsonl` 無 `unknown`／`request_started` 寫入請求，且無未以 `comment_ok`／`abandoned` 結案之 `intent`。
     - 核對 `questions.jsonl` 中尚未送出（`forecast_ok`）且 `supported` 之 `open`／`upcoming` 題目，其截止時間距今不得落於 `(0, horizon_s + DECISION_MARGIN_S]`（即 `bound + 1800` 秒）內，且完全不讀取預測內容。
  2. **雙階段預檢與新鮮零點數／配額准入（`Session.snapshot(admit=True)` 與 `Session.run`）**：
     - 在 `placement.prepare()`／`placement.place()` 之前，先執行 `before = self.snapshot(admit=True)`：呼叫 `quota.snapshot(timeout_s=30)`，強制要求 `quota.observation_fresh(observed, self.now())` 為真且 `observed.remaining_credits == 0`；並呼叫 `quota.manual_admission(...)` 確認 Gemini tier1 模型配額准入通過。
     - 對照 `B.latest_approved(self.root/'acceptance.jsonl')`，要求 `set(B.compare(approved, before)['decisions'].values()) == {'ok'}`。
     - 執行全部三線 Tier 1／Tier 2 `self.preflights()` 後，再次執行 `refreshed = self.snapshot(admit=True)`、比對 `before` 與 `refreshed` 無非資訊性變更、再次執行 `self.idle(...)`、再次核對 `identities()`、`protected_hashes()` 與 `budget_window_end` 餘裕。
     - 進入 `with transaction(self.root):` 後先執行 `capacity()` 確認三線週容量未滿，才寫入 `maintenance_started`。
  3. **逐線呼叫前再驗證（`before_call=capacity` 與 `_start_call`）**：
     - `autoload.run()` 於每位選手呼叫 `executor(req)` 前，先執行 `before_call(req)`（即 `capacity(req)`），再執行 `_start_call`（對 Gemini 再次執行 `quota.snapshot`、`manual_admission`、`observation_fresh` 並寫入 `acceptance_quota_snapshot`；對所有選手寫入含 `orphan.metadata` 之 `acceptance_call_started`），任何一步失敗皆不會呼叫模型。

### 3.3 精確還原與外部並行編輯衝突保留（Exact Restoration vs External Concurrent Edits）
- **證據位置**：`src/autoload.py`（`inspect`、`_read`、`_write`、`Placement.prepare`、`Placement.place`、`Placement.restore`）、`tests/test_autoload_conflicts.py`、`tests/test_autoload.py`。
- **實作核對**：
  1. **不跟隨 Symlink 與非一般檔案拒絕**：`_check_parents` 逐層以 `lstat` 檢查父目錄必須為真實目錄；`inspect` 要求既有目標必須為 `S_ISREG` 且 `st_nlink == 1`；`_read` 與 `_write` 皆強制帶入 `os.O_NOFOLLOW`。
  2. **原位截斷（In-place `ftruncate`）避免清空空窗**：
     - 既有檔案於 `place()` 時以 `O_APPEND` 附加 canary，並計算與驗證 `placed_sha256`、`placed_mode`、`placed_size`、`placed_dev`、`placed_ino`。
     - `restore()` 還原既有檔案時，先比對目前狀態是否等於 `original_matches` 或 `placed_matches`。若為 `placed_matches`，以 `os.O_RDWR | os.O_NOFOLLOW` 開啟檔案並取得 `flock(LOCK_EX | LOCK_NB)`，對開啟之 file descriptor 執行 `os.fstat` 再次確認 `(st_dev, st_ino, st_nlink) == (placed_dev, placed_ino, 1)` 且讀取內容雜湊等於 `placed_sha256`，接著呼叫 `os.ftruncate(stream.fileno(), state["size"])` 直接截除附加後綴（絕不先截斷為 0 再重寫）、`fsync`，再重讀驗證雜湊等於原始 `state["sha256"]`，最後以 `os.fchmod` 與 `os.utime(..., ns=(atime_ns, mtime_ns))` 還原權限與奈秒級時間戳記。
  3. **外部編輯保護**：
     - 若檔案在探測期間遭外部程序修改、附加、變更權限、刪除或替換為 symlink，則 `not original_matches and not placed_matches` 成立，拋出 `ValueError("target changed after placement")` 並轉記為 `restore_conflict`，保留外部修改後之檔案與備份目錄，不覆寫亦不 `unlink`。
     - 新建目錄僅在 `(st.st_dev, st.st_ino) == self.directory_ids.get(directory)` 相符時呼叫 `directory.rmdir()`（若目錄內有外部新增檔案則 `rmdir` 失敗並保留目錄、記錄失敗）。

### 3.4 持久化放置意圖與 `result + closed` 結案閘門（Durable Place Intent and `result + closed` Gating）
- **證據位置**：`src/autoload.py`（`Placement.create_directory`、`Placement.prepare`、`Placement.place`、`Placement.restore`、`unclosed`）、`src/forecast.py`（`preflight`）、`tests/test_autoload_maintenance.py`（`test_unclosed_requires_durable_result_and_manifest`、`test_partial_write_is_preserved_as_conflict_and_blocks_preflight`）。
- **實作核對**：
  1. **持久化意圖（Intent before Mutation）**：
     - 建立目錄前先寫入 `create_dir`，建立後寫入含 `dev`／`ino` 之 `created_dir`。
     - 寫入或附加目標檔案前先將預期之 `placed_sha256`、`placed_mode`、`placed_size`（及既有檔案之 `placed_dev`、`placed_ino`）寫入 `manifest.jsonl` 的 `place` 事件並加入 `self.touched`，實際寫入並重讀驗證後再寫入 `placed` 事件。
     - 若在 `_write` 發生部分寫入（partial write）或崩潰，因已有 `place` 意圖紀錄，還原與恢復流程皆會納管該路徑，並將不一致狀態判為 `restore_conflict` 而非忽略。
  2. **雙重結案條件（`unclosed`）**：
     - `Placement.restore()` 唯有在 `not failures`（所有檔案、新建目錄與 `attempt_base` 目錄權限皆無失敗）時，才會在 `manifest.jsonl` 末尾寫入 `{"type": "closed", "restore_ok": True, "run_id": run_id}`。
     - `autoload.unclosed(log)` 只檢查具有 v2 `autoload_run_started` 之受管執行（歷史 unmanaged v1 不受影響），要求同時滿足：(a) `acceptance.jsonl` 中該 `run_id` 最新一筆 `autoload_result` 的 `restore_ok is True`；且 (b) 預期路徑之 `manifest.jsonl`（無 symlink 父目錄或本體）可由 `read_complete` 完整讀取，且最後一筆事件為 `type == "closed"`、`restore_ok is True`、`run_id == run_id`。
     - 只要任一條件不成立，`forecast.preflight()` 立即加入 `"autoload_unclosed: global markers may remain; maintenance recovery required"` 並提早返回，硬性阻擋三線第 1、2 層預測。
     - 若先前發生 `restore_conflict`，在人工排除衝突並執行 `recover` 成功後，新附加於 `manifest.jsonl` 末尾之 `closed` 與新附加於 `acceptance.jsonl` 之 `autoload_result(restore_ok=True)` 可正確解除 `unclosed` 狀態。

### 3.5 `SIGKILL`／重啟與恢復機制之冪等性（SIGKILL / Restart and Recovery Idempotence）
- **證據位置**：`src/autoload.py`（`recover_placement`、`Placement.restore`）、`src/autoload_maintenance.py`（`Session.recover`）、`src/flab/orphan.py`（`boot_id`、`no_process`）、`tests/test_autoload_maintenance.py`（`test_recovery_replays_write_intent_and_is_idempotent`）。
- **實作核對**：
  1. **原控制器存活與孤兒程序檢查**：`Session.recover(run_id)` 於持六把鎖後，核對 `current_boot = orphan.boot_id()` 與 `start['boot_id']`（缺一即拒絕）；若同一次開機且 `pid != os.getpid()`，以 `os.kill(int(start['pid']), 0)` 探測，只要非 `ProcessLookupError`（包含程序仍存活或 `PermissionError`）一律拒絕恢復。接著對該 `run_id` 已啟動呼叫之 `workdir`／`raw_dir` 執行 `orphan.no_process(paths)` 及 `self.idle(..., recovering=run_id)`。
  2. **白名單範圍限制與重播安全（`recover_placement`）**：
     - 僅允許重播由 `locations(run_id, tuple(start["players"]), home, base)` 計算出之註冊目標集合 `allowed`，以及屬於目標父目錄或 `base` 之新建目錄；備份檔必須位於 `backup` 目錄內且非 symlink，防止惡意或損毀 manifest 觸碰任意路徑。
  3. **跨階段崩潰之冪等還原**：
     - 無論崩潰發生於寫入前（`before_write`）、寫入後（`after_write`）、或 `ftruncate` 後但 `utime`／`restored` 紀錄前（`after_truncate`），`Placement.restore()` 皆能透過 `original_matches`（含重新開啟 fd 驗證 `dev`、`ino`、`nlink`、`sha256` 後補做 `os.utime`）或 `placed_matches` 正確完成還原；對已移除之新建檔案或新建目錄再次執行亦為無副作用之 no-op。
     - `Session.recover(run_id)` 補寫未結案之 `acceptance_call_finished(error_type='interrupted')` 與 `autoload_result(passed=False, error='interrupted', recovered=True)`，驗證 `identities()`、`protected_hashes()` 與六項 `preflights()` 後，僅在 `STOP_SUBMIT` 內容完全等於 `f'{VERSION}:{run_id}\n'` 時將其移除，並確保 `maintenance_recovered` 不重複寫入。

### 3.6 D105+D118 呼叫計數與 3 次總呼叫／1 次零呼叫重試上限（Count D105+D118 Calls and 3-Call Bound / One Zero-Call Retry）
- **證據位置**：`src/autoload_maintenance.py`（`weekly_usage`、`Session.run`）、`src/budget_handoff.py`（`accounting_start`）、`tests/test_autoload_maintenance.py`（`test_retained_weekly_usage_matches_production_budget`、`test_zero_call_failure_allows_exactly_one_retry`）。
- **實作核對**：
  1. **D105+D118 跨窗口累計**：`weekly_usage` 呼叫 `H.accounting_start(root, campaign)` 取得保留之起算時間 `start`（D118 handoff 下即 D105 來源窗口之 `budget_window_start`），統計 `[start, end)` 期間內 `forecast_attempt_starts.jsonl` 與 `forecasts.jsonl` 之唯一 `attempt_id`，加上 `acceptance.jsonl` 中 `acceptance_call_started` 之唯一 `attempt_id`（含 `source="autoload"`），以及無對應 `acceptance_call_started` 之舊式 `acceptance_result` 的 `model_call_count`（預設 3），與正式 `Budget.reserve` 計數規則完全一致。
  2. **總呼叫數上限 3 次與單次零呼叫重試**：
     - `Session.run()` 讀取 `autoload_maintenance.jsonl` 中所有 `maintenance_started` 之 `prior_runs`，並檢查 `acceptance.jsonl` 中是否存在 `run_id in prior_runs` 之 `acceptance_call_started`。
     - 條件 `if calls or len(prior_runs) >= 2: raise MaintenanceError('D124 bounded probe already consumed')` 確保：只要過去曾發出任何 1 次模型呼叫（`calls` 非空），即永久禁止再次執行；若過去在第 0 次模型呼叫前失敗（`calls` 為空），最多僅允許重試 1 次（第 3 次嘗試時 `len(prior_runs) >= 2` 即拒絕）。單次執行固定對三線各發出至多 1 次呼叫，因此總模型呼叫數嚴格受限於至多 3 次。
  3. **時間上限與還原保留時間**：`bound = 3 * cfg['timeouts_s']['tier1'] + 3 * 120 + 300`，並以 `deadline = self.monotonic() + bound - 300` 與 `timeout_s = bound - 300`（`ITIMER_REAL`）限制模型執行階段，明確保留最後 300 秒供 `finally` 區塊執行檔案還原。

### 3.7 正式環境 CLI `PATH`／釘選／身分與 Model + Judge + Scope 檢查（Production CLI PATH / Pin / Identity / Model + Judge + Scope）
- **證據位置**：`src/autoload_maintenance.py`（`Session.production_path`、`Session.identities`、`Session.snapshot`、`Session.run`）、`launchd/bin/claude`、`src/autoload.py`（`execute`、`run`、`main`）。
- **實作核對**：
  1. **正式 `PATH` 與 Claude 2.1.316 釘選**：
     - `Session.production_path()` 將 `PATH` 固定設為 `launchd/bin:~/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin` 並設定 `DISABLE_AUTOUPDATER=1`。
     - `Session.identities()` 強制驗證 `shutil.which('claude')` 必須等於 `self.project/'launchd/bin/claude'`，同時記錄三線 CLI 執行檔與 `data/cli_pins/claude/2.1.316` 之 SHA-256；`launchd/bin/claude` 內部亦在執行前以 `/usr/bin/shasum -a 256` 驗證 `data/cli_pins/claude/2.1.316` 雜湊為 `64c327e92b2c5e0f78c0a980c5a518121e1f0d140ca771ca5dd4488d1a2dcb3f`；`Session.snapshot()` 亦驗證 `B.cli_versions().get('claude') == '2.1.316'`。
  2. **Production 模型與 Judge／工具範圍防護**：
     - `autoload.main()` 於授權執行時強制要求 `players == baseline.PLAYERS and not args.cheap` 並委派予 `Session(PROJECT_ROOT).run(...)`，使用 `config["players"][p]["tier1"]` 之正式 `model`、`effort` 與 `timeouts_s["tier1"]`。
     - `autoload.execute()` 對 Gemini 以 `(result.usage_raw or {}).get("log_model_check", "undetermined")` 呼叫 `adapter.apply_quota_model_check`（要求正面日誌模型驗證，不假設配額身分）；並呼叫 `acceptance_judge.extract_{claude,codex,gemini}` 檢查工具呼叫，只允許 `{"claude": {"StructuredOutput"}, "gpt": set(), "gemini": {"finish"}}`，任何越權呼叫或 Claude 非空之 `mcp_servers`／`skills`／`slash_commands`／非 `builtin` `plugins` 一律設為 `tool_scope_violation`。
     - `autoload.run()` 另以 `forecast.tool_violations(player, result, repair=True)`、`result.model_check == "verified"` 等條件把關；一旦出現 `tool_scope_violation` 或 `model_mismatch`，立即 `break` 停止後續選手呼叫，且 `Session.run()` 會拋出 `MaintenanceError('probe reported forbidden scope or model')` 並寫入 `STOP_SUBMIT`。

### 3.8 無不安全基準核准與無驗收帳冊污染（No Unsafe Baseline Approval or Poisoned Acceptance）
- **證據位置**：`src/autoload.py`、`src/autoload_maintenance.py`、`src/forecast.py`（`incomplete_acceptance`、`recover_acceptance`、`check_acceptance`）。
- **實作核對**：
  1. `src/autoload.py` 與 `src/autoload_maintenance.py` 全文皆不呼叫 `baseline.approve(...)`，亦不寫入任何 `baseline_approved` 或 `acceptance_result` 紀錄；量測結果僅產生 `autoload_result` 與 `autoload_maintenance.jsonl` 事件（`status` 為 `complete` 或 `measurement_failed`）。
  2. `autoload.run()` 寫入之 `acceptance_call_started`、`acceptance_call_finished` 與 `acceptance_quota_snapshot` 均標記 `source="autoload"`。
  3. `forecast.incomplete_acceptance()` 與 `forecast.recover_acceptance()` 皆明確過濾 `r.get("source") != "autoload"`，確保 autoload 探測呼叫在計入週預算的同時，既不會撤銷既有有效之隔離驗收通過證明，也不會被 `recover_acceptance()` 誤補寫成 `acceptance_result`。

---

## 4. 建議與後續注意事項（Proposals & Notes）

1. **測試套件完成確認（程序提醒）**：待目前執行中之完整測試套件跑完且確認 0 失敗後，方可於授權維護時段執行單次 D124 Phase 1 量測。
2. **Phase 1b／Phase 2 邊界維持**：依規格與本次審查範圍，Phase 1 量測結束後僅產出證據（`loaded`／`not_loaded`／`unknown`），不得自動變更 `baseline.py` 之來源分類或自動核准基準；Phase 2（`auto_reaccept`）及 PLAN §7.2／§15／§16 文件更新須留待後續獨立批次審查後再行部署。