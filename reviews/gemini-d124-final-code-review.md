# D124 最終複審（Phase 1）與 Phase 2（Observe-Only）完整審查報告

## 審查結論（Separate Verdicts）
- **Phase 1（`src/autoload.py`、`src/autoload_maintenance.py`、`src/forecast.py` 及相關測試）：`PASS`**
- **Phase 2 Observe-Only（`src/auto_reaccept.py`、`src/flab/auto_reaccept_policy.py`、`launchd/run.sh`、`tests/test_auto_reaccept.py`）：`PASS`**

---

## 一、阻斷問題（Blockers）
- **Phase 1 阻斷問題**：**無（0 項）**。先前審查指出之 `BLK1`–`BLK6` 已全部修正並由隔離測試套件覆蓋。
- **Phase 2（Observe-Only）阻斷問題**：**無（0 項）**。符合分階段規範（Staged Spec），完全限制於唯讀觀測與雜湊／原因碼紀錄，無任何執行期 enforce、自動重驗收、自動簽核 baseline 或清除 `STOP_SUBMIT` 之行為。

---

## 二、實際證據（Actual Code & Test Evidence）

### 2.1 Phase 1 先前阻斷項（BLK1–BLK6）修正驗證

1. **BLK1（Gemini probe 執行期模型與配額差分驗證、工具與宣告範圍強制檢查）**
   - **程式碼證據**：
     - `src/autoload.py` `execute()`（第 306–352 行）：在呼叫 Gemini adapter 前先擷取 `before = quota.snapshot(timeout_s=30)` 並驗證 `observation_fresh` 與 `credits_ok`；執行後擷取 `after = quota.snapshot(timeout_s=30)`，呼叫 `forecast.quota_model_check(before, after, result)`，且若 `result.usage_raw.get('log_model_check') != 'verified'` 則強制降為 `'undetermined'`，再透過 `adapter.apply_quota_model_check(result, check)` 更新 `result.model_check`，同時將配額證據寫入 `req.raw_dir / 'quota_evidence.jsonl'`；若 `!quota.credits_ok(after)` 則標記 `result.error_type = 'tool_scope_violation'`。
     - 對三條線均呼叫 `acceptance_judge.extract_{claude,codex,gemini}`，僅允許格式化回答所需之最小工具（`claude: {"StructuredOutput"}`、`gpt: set()`、`gemini: {"finish"}`）；Claude 額外驗證 `declared` 中 `mcp_servers`、`skills`、`slash_commands` 必須為空且 `plugins` 僅限 `builtin`。
     - `src/autoload.py` `run()`（第 429–440 行）：要求 `result.model_check == "verified"`、`result.tool_call_counts` 無越權工具且 `not forecast.tool_violations(player, result, repair=True)`；一旦出現 `tool_scope_violation` 或 `model_mismatch` 立即 `break` 中止後續 line 探測，並由 `src/autoload_maintenance.py`（第 247–248 行）觸發 `MaintenanceError` 與 `STOP_SUBMIT`。
   - **測試證據**：`tests/test_autoload.py::test_calls_journaled_before_real_executor_and_counted_by_budget`、`tests/test_autoload_maintenance.py::test_failure_sets_stop_before_unlocking[scope]`。

2. **BLK2（`Session.snapshot()` 與 `Session.identities()` 實機邏輯、Claude wrapper/pin 及 Gemini 0-credit 與 manual admission 前置檢查）**
   - **程式碼證據**：
     - `src/autoload_maintenance.py` `identities()`（第 127–139 行）：驗證三條線 CLI 路徑與 SHA-256，強制 `claude` 必須指向 `launchd/bin/claude` 且記錄 `data/cli_pins/claude/2.1.316` 之 `pinned_sha256`。
     - `src/autoload_maintenance.py` `snapshot(admit=...)`（第 141–158 行）：強制驗證 `quota.observation_fresh` 與 `remaining_credits == 0`；當 `admit=True` 時，於放置任何 canary 前先以 `quota.manual_admission` 驗證 Gemini tier1 模型配額准入；驗證 `versions.get('claude') == '2.1.316'`，並在 `B.take_snapshot` 完成後再次確認 `quota.observation_fresh(observed, self.now())`，且於 `run()` 進入寫入前（第 216–217 行）再度確認配額未過期。
   - **測試證據**：`tests/test_autoload_maintenance.py::test_real_snapshot_and_identity_methods_validate_pin_and_quota`。

3. **BLK3（跨程序恢復 `recover_placement` 對目錄 inode 身分、空 manifest 及檔案還原時間戳之安全性）**
   - **程式碼證據**：
     - `src/autoload.py` `Placement.create_directory()`（第 125–131 行）：建立目錄前寫入 `create_dir` intent，建立後立即 `lstat()` 並寫入 `created_dir`（含 `dev`、`ino`）。`Placement.restore()`（第 220–231 行）在刪除目錄前強制比對 `(st.st_dev, st.st_ino) == self.directory_ids.get(directory)`，若缺漏或不符則拒絕刪除並轉入 `failures`。
     - `Placement.restore()`（第 184–192 行）：當檔案內容已還原（`original_matches` 為真，例如前次還原於 `ftruncate` 後中斷）時，以 `O_RDONLY | O_NOFOLLOW` 開啟並驗證 `(st_dev, st_ino, st_nlink)` 與 `sha256` 後，補行 `os.utime` 還原 `atime_ns` 與 `mtime_ns`。
     - `src/autoload.py` `recover_placement()`（第 504–510 行）：若 `manifest.jsonl` 為空（於 `autoload_run_started` 後、`prepare` 前中斷），先以 `verify_resolved(targets, [], run_id)` 確認所有目標皆無 canary，再寫入 `closed`（`no_mutation: True`）。
   - **測試證據**：`tests/test_autoload_maintenance.py::test_controller_recovery_is_idempotent_at_every_preparation_stage[missing|managed|original|placed]`、`test_recovery_replays_write_intent_and_is_idempotent[before_write|after_write|after_truncate]`。

4. **BLK4（`Session.recover()` 鎖保護、原 controller 與子程序存活檢查、外源 `STOP_SUBMIT` 保留）**
   - **程式碼證據**：
     - `src/autoload_maintenance.py` `recovery_locks()`（第 186–194 行）與 `recover()`（第 260–316 行）：全程持有 `production_path()` 與 6 個 `LOCKS`，任何例外皆在釋放鎖前寫入 `STOP_SUBMIT`。
     - 強制驗證 `orphan.boot_id()`；若與 `autoload_run_started` 同次開機且 `pid != os.getpid()`，以 `os.kill(pid, 0)` 檢查原 controller 是否存活（存活則拒絕 recover）。
     - 針對所有已啟動之 probe call 路徑執行 `orphan.no_process(paths)`，並呼叫 `self.idle(..., recovering=run_id)`（不因平台未決連線或問題截止時間阻斷還原，但仍阻斷其他活躍模型程序）。
     - 僅在 `STOP_SUBMIT` 內容精確等於 `f'{VERSION}:{run_id}\n'` 時才予以移除（第 310–311 行），外源設置之 `STOP_SUBMIT` 完整保留。
   - **測試證據**：`tests/test_autoload_maintenance.py::test_recovery_retains_stop_and_preserves_evidence_when_unproven[live_pid|unknown_boot|child|preflight|foreign_stop]`、`test_recovery_ignores_platform_intents_and_near_deadlines`、`test_recovery_cleans_only_registered_workdir_and_closes_call`。

5. **BLK5（外部編輯衝突之處置與具名 Host Attestation 閉環）**
   - **程式碼證據**：
     - `src/autoload.py` `recover_placement(..., resolved_by=...)`（第 548–554 行）與 `verify_resolved()`（第 558–570 行）：當發生外部編輯衝突時，預設 `restore()` 保留外部編輯與備份並回報 `restore_conflict`；若由 host 人工排除後透過 `--resolve-conflict --resolved-by <name>` 呼叫，`verify_resolved()` 會唯讀掃描所有目標確認無任何 `FLABAUTO-<run_id>-` 標記殘留且無測試建立之目錄殘留，再將當前雜湊與 `resolution: 'host_attested'`、`byte_exact: False` 寫入 `manifest.jsonl` 與 `acceptance.jsonl`。
   - **測試證據**：`tests/test_autoload_maintenance.py::test_host_conflict_resolution_requires_canary_removal`、`tests/test_autoload_conflicts.py`。

6. **BLK6（預檢/時限拒絕不誤耗用 0-call 重試額度、與預留時間計算）**
   - **程式碼證據**：
     - `src/autoload_maintenance.py`（第 225–227 行）：`before_call(req)` 在寫入 `acceptance_call_started` 之前先檢查 `capacity(req)` 與 `self.monotonic() + req.timeout_s + 120 > deadline`；若剩餘時間不足則在紀錄任何 `acceptance_call_started` 前拋出 `MaintenanceError`，確保 0-call 失敗保有唯一一次重試機會（`calls or len(prior_runs) >= 2`）。
   - **測試證據**：`tests/test_autoload_maintenance.py::test_deadline_denial_is_before_started_and_allows_zero_call_retry`、`test_zero_call_failure_allows_exactly_one_retry`。

---

### 2.2 Phase 2（Observe-Only：`src/auto_reaccept.py`、`src/flab/auto_reaccept_policy.py`、`launchd/run.sh`、`tests/test_auto_reaccept.py`）完整審查

1. **唯讀觀測邊界（No Enforce / No Model Calls / No Baseline Approval）**
   - **程式碼證據**：
     - `src/flab/auto_reaccept_policy.py`：`MODE = 'observe'`；`candidate()` 固定輸出 `'mode': 'observe'`、`'model_calls': 0`、`'baseline_approved': False`。
     - `src/auto_reaccept.py`：`tick()` 首行強制檢查 `if P.MODE != 'observe': raise ValueError('this release supports observe only')`；若存在 `config/STOP_SUBMIT` 則直接返回 `{'status': 'stopped', 'mode': P.MODE}`。模組完全未匯入或呼叫 `baseline.approve`、`acceptance` 或任何模型執行器，亦從不寫入 `data/acceptance.jsonl`。
   - **測試證據**：`tests/test_auto_reaccept.py::test_observe_records_once_and_never_changes_acceptance`、`test_tick_has_singleton_and_no_enforce_escape`。

2. **Allowlist 範圍與受保護指紋綁定（Candidate Binding）**
   - **程式碼證據**：
     - `ALLOWLIST`（`src/flab/auto_reaccept_policy.py` 第 13–14 行）嚴格限定於 8 個全域使用者層級之 instruction / rule / skill 來源（`claude.global_instructions`、`claude.skills`、`codex.global_instructions`、`codex.skills`、`agy.gemini_md`、`agy.agents_md`、`agy.skills`、`agy.rules`），排除所有 `common` 祖先路徑、plugins、hooks、MCP、agents、settings 等來源。
     - `P.candidate()`（第 128–159 行）：
       - 綁定 `replaces_baseline`（當前核准之 baseline `record_id`）、該 line 與 `common` 所有非 `INFORMATIONAL` 來源之完整指紋（`protected_sources`）、CLI 版本、CLI 執行檔與 Claude pin 之 SHA-256（`identity`）、`runtime_fingerprint`、`judge_version`、tier1/tier2 模型與 effort、既有 production acceptance 證據有效性（`production_evidence_ok` 與 `production_record_ids`）、GPT model catalog 雜湊，以及 `scan_sha256`。
       - 對 `cli_version` 變更強制加入 `'cli_version_observe_only'`（若為 Claude 另加 `'claude_pin_changed'`），確保版本變更在觀測中永不標為 `format_eligible: True`。
       - 若同時發生文字來源變更與 GPT catalog 變更，標記 `'mixed_candidate'` 並設為不可合格（`format_eligible: False`）。
   - **測試證據**：`tests/test_auto_reaccept.py::test_candidate_binds_all_protected_sources_and_runtime`、`test_non_text_or_untrusted_candidates_remain_ineligible[common|plugin|mixed|version|evidence|identity|blocked]`。

3. **Bounded Markdown 掃描器、TOCTOU 防護與隱私保護**
   - **程式碼證據**：
     - `P.scan()`（`src/flab/auto_reaccept_policy.py` 第 49–125 行）：
       - 檢查所有父目錄 `lstat()` 確保無 symlink 或非目錄；對目錄使用 `os.walk(..., followlinks=False)`，限制 `MAX_DIRS = 256`、`MAX_FILES = 64`、`MAX_FILE_BYTES = 64 KiB`、`MAX_SOURCE_BYTES = 512 KiB`。
       - 排除隱藏檔（`.` 開頭）、非 `.md` 副檔名、符合 `B.CREDENTIAL_FILE` 或 `B.PRIVATE_DIR` 之名稱。
       - 以 `os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)` 開啟檔案，驗證 `S_ISREG`、`st_nlink == 1` 且無執行權限（`st_mode & 0o111 == 0`）；讀取後再次 `lstat()` 比對 `(st_dev, st_ino, st_size, st_mtime_ns, st_mode)`。
       - 計算與 `baseline.py` 相同結構之雜湊 `digest`，並在結尾重跑 `current = B.inspect_source(source, home)`，驗證 `digest == saved.get('hash') == current.get('hash')` 且 `B._same_protected(saved, current)`，防止快照與掃描間之 TOCTOU 競爭。
       - `text_reason()` 驗證 UTF-8、阻擋控制字元、UTF-8 BOM、`@` 匯入語法（`IMPORT`）、敏感與測試標記（`metaculus`、`FLABAUTO-`、`FLABCANARY`、私鑰標頭、`sk-`、`ghp_`、`github_pat_`），且僅允許 `.skills` 下之 `SKILL.md` 含有單行純字串 `name`/`description` frontmatter（無 YAML 解譯器風險）。
       - 所有掃描輸出與例外處理僅回傳固定原因碼與 SHA-256 雜湊，絕不將檔案內文或例外訊息寫入 `auto_reaccept.jsonl`。
   - **測試證據**：`tests/test_auto_reaccept.py::test_bounded_markdown_uses_baseline_digest_and_exclusion`、`test_text_rejections_are_codes_only`、`test_unsafe_inventory_cannot_be_eligible`、`test_snapshot_race_does_not_accept_new_digest`、`test_single_file_hash_and_frontmatter_rule`。

4. **鎖序、時限與排程整合（`src/auto_reaccept.py` & `launchd/run.sh`）**
   - **程式碼證據**：
     - `launchd/run.sh`（第 23–31 行）：僅在 `src/cycle.py` 正常結束並釋放所有 cycle/forecast 鎖後，才執行 `.venv/bin/python src/auto_reaccept.py tick || true`，且以 `|| true` 確保觀測異常不影響 cycle 退出碼。
     - `src/auto_reaccept.py`：`main()` 先以 `W.require_active` 確認處於有效活動視窗內，並設置 `MAX_TICK_S = 120` 秒 `SIGALRM` 保護；`tick()` 取得非阻塞單例鎖 `auto_reaccept.lock`；`observe_line()` 取得非阻塞 `forecast.<player>.lock`，先檢查 `autoload.unclosed()`，最後於 `transaction(root)` 內二次確認 `latest_approved` 未被取代且依 `candidate_sha256` 去重後才寫入 `data/auto_reaccept.jsonl`。

---

## 三、非阻斷建議與後續階段注意事項（Non-Blocking Proposals）

1. **Gemini 觀測時若遇暫時性配額讀取逾時之分類細化（可於後續 Phase 2 演進考慮）**
   - 在 `src/auto_reaccept.py` `observe_line()` 中，當 `player == 'gemini'` 時會呼叫 `quota.snapshot(timeout_s=20)`；若該次配額查詢逾時或 stale，則 `credits = None`，此時 `B.compare(old, current)` 會因 `B.blockers(current)` 產生一筆 `source = "agy.credits"` 之變更，使 `P.candidate()` 將其歸類為 `kind = 'text'` 且 `format_eligible = False`（原因為 `['baseline_blocker', 'format_not_allowed', 'source_outside_allowlist']`）。
   - 目前因為有 `candidate_sha256` 去重，且 `format_eligible` 為 `False`，在安全性與日誌容量上皆無問題；未來實作 enforce 階段時，建議可在 `P.candidate()` 將僅有 `agy.credits`（或 `B.blockers`）而無實質來源／版本／catalog 變更之情形獨立歸類為 `kind = 'blocker'`（或 `'none'`），使候選分類語意更直觀。