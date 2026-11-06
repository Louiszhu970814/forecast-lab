VERDICT: PASS

### 1. 封包完整性與審查結論（Summary & Packet Integrity）
- **封包完整性**：已確認本審查封包完整無截斷，結尾精確包含最終標記 `END-D140-CODE-R3B`，且明確綁定共享來源清單 `EXACT SHARED SOURCE MANIFEST SHA256 c5748f030fb2c6b85c42e820f21ed90019f53dad7033c594deeae3cbe0bd87e2`（版本 `d140_review_source_r3_v1`）。
- **審查範圍**：本報告僅針對 D140 R3 **Module B**（同線鎖與 admission 呼叫端、含未決題之預算保留、Discovery 喚醒資格、部分線完成之截止結算、帳冊交易與安全傳播、Launchd wrappers 以及部署器與審查來源鏈驗證）進行評估。依跨模組契約，單一模組之 `PASS` 僅代表該模組通過，全系統須待兩家官方模型對所有模組（R3A、R3B）皆實際出具 `PASS` 後始得部署。

---

### 2. 阻擋項（Blockers）
- **無（None）**。前輪針對 Module B 提出之所有阻擋問題（B-B1、B-B2、B-B3）及 R1 歷史阻擋項（BLK4、BLK5）皆已在實際凍結原始碼與測試中完整解決。

---

### 3. 實際程式碼與測試證據核對（Actual Evidence）

#### (1) B-B1：部署器共享 Manifest、Context CAS 與完整審查來源鏈強制驗證（`deploy.py`、`test_deploy.py`）
- **共享來源與不變脈絡（Context）雜湊綁定**：
  - `Deploy.review_bundle()` 讀取 `review-modules-r3.json` 與 `source-manifest-r3.json`，強制驗證 `deploy-approval.json` 與模組清單內的 `source_manifest_sha256`、`review_modules_sha256`、`deployment_sha256` 及 `source_sha256`（`hash_obj(files)`）完全一致。
  - 對 `source['files']` 逐檔核對 `stage` 端 SHA256，並對 `source['contexts']`（未修改之依賴檔案與受審脈絡）及 `source['inputs']`（含 `deploy.py`、`test_deploy.py` 等作業輸入）逐一驗證 SHA256，防止替換不同 manifest 或混用舊輪次審查結果。
  - `Deploy.validate()`、套用後（`apply` 提交前）及啟用前（`activate`）均再次核對 `self.project` 現場檔案、`immutable` 設定與 `contexts` 雜湊，確保任何現場或暫存區漂移（drift）皆會立即拒絕。
- **端到端審查 Provenance 鏈驗證**：
  - 針對每個模組（`r3a`、`r3b`）與兩位審查者（`claude-opus-5-5` 使用 `StructuredOutput`、`gemini-4-argon-high` 使用 `finish`），`review_bundle()` 直接查驗：
    1. 審查封包（packet）存在、尺寸 `< 170000` bytes、結尾為對應 `END-D140-CODE-<MID>` 標記，且內文實際包含 `source_hash`。
    2. `reviews.jsonl` 中存在唯一對應的 `development_review_started` 與 `development_review_finished`，且 `player`、`model`、`effort == 'high'`、`packet_sha256`、`status == 'completed'`、`answer_sha256`、`answer_path` 完全吻合。
    3. 原始 `stdout.jsonl` 事件流中模型名稱吻合、僅呼叫允許的結構化輸出工具（且拒絕任何 `server_tool_use` 或 `denied_actions`），最終 `structured_output['answer']`（支援單層 JSON 解包）與 `answer.md` 內容逐字一致，且首行為 `VERDICT: PASS`、末行為 `END-D140-CODE-<MID>`。
    4. `review-usage.jsonl` 中唯一紀錄之 `stdout_sha256`、`packet_sha256`、`player`、`requested_model` 皆與實際檔案一致。
- **原子回滾與計時器安全**：
  - `apply()` 在六鎖（`lock_all`）與 `require_idle(..., horizon_s=900)` 保護下執行；若提交前發生任何異常（含 `before`/`after` preflight 或離線測試失敗），`finally` 區塊會反向還原已變更檔案（新增檔刪除、修改檔寫回備份），且無論回滾本身是否成功皆確保重新 `enable` 原 `org.forecast-lab.cycle` 計時器。
- **測試證據**：`test_deploy.py` 涵蓋 `before`/`after`/`smoke` 失敗回滾、回滾寫入失敗時仍重啟 timer、10 種漂移或 `STOP_SUBMIT` 阻擋、模組缺失／拒絕／截斷、8 種 provenance 鏈篡改，以及替換無關 manifest 之負向測試（主機測試紀錄 `deploy-tests-final-r3.txt` 顯示 30 項通過）。

#### (2) B-B2：同線鎖重入契約、中斷恢復、手動 Resume 與排程隔離（`src/forecast.py`、`src/scheduler.py`、`src/reconcile.py`、`tests/test_scheduler.py`）
- **跨程序非阻塞鎖與執行緒局部重入紀錄**：
  - `src/forecast.py:1243-1266` 之 `Lock` 使用 `fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)`，遇 `BlockingIOError` 時關閉檔案並拋出 `PreflightError("another forecast.py is running")`，成功取得鎖時將解析後路徑加入 `_HELD_LOCKS.paths`（`threading.local()`），釋放時移除。
- **手動 Resume 交易與鎖保護**：
  - `src/forecast.py:1336-1343` 在 `args.cmd == "resume"` 時同時取得 `Lock(LINE_STATUS_LOG.parent / f"forecast.{args.player}.lock")` 與 `transaction(LINE_STATUS_LOG.parent)`，確認 `line_stopped` 非空後始寫入 `line_resume`。
- **`recover_interrupted` 與排程器不互相卡死**：
  - `src/forecast.py:595-681` 之 `recover_interrupted` 先檢查 `lock_path.resolve() in getattr(_HELD_LOCKS, "paths", set())` 決定是否重用既有鎖；若未持鎖且因外部程序（如 `line_recovery`）正持有該玩家線鎖而觸發 `PreflightError`，外層 `except PreflightError: return []` 安全退回空清單，不會在 `scheduler.run()` 啟動階段（`src/scheduler.py:550` 呼叫 `self._recover(player)`）終止整個排程。
  - 當 `scheduler.execute()` 針對忙碌線執行時（Tier 1 於 `src/scheduler.py:399` 取得 `F.Lock`，Tier 2 於 `src/reconcile.py:162` 取得 `F.Lock`），鎖競爭拋出之 `PreflightError` 會在 `scheduler.run()`（`src/scheduler.py:581-593`）被捕獲並記為 `outcome: "line_error"`，將該玩家列入該輪 `paused`，而其他健康線（`claude`、`gpt`）與截止時間結算（`finalize`）照常完成。此行為已由 `tests/test_scheduler.py:342-352`（`test_busy_recovery_line_does_not_block_other_lines_or_deadline_finalization`）驗證。
- **保留額度計算（`_protected`）**：
  - `src/scheduler.py:342-346` 之 `_protected` 計算該玩家未達 `_terminal` 之 Tier 1 題目數，並傳入 `self.budget.reserve(..., pending_tier1=...)`，確保獲得 retry grant 尚未完成補做的題目仍受 Tier 1 容量保護。

#### (3) B-B3：Launchd Wrappers、CLI Pin 校驗與服務設定（`launchd/bin/claude`、`launchd/bin/agy`、`launchd/run.sh`、`launchd/org.forecast-lab.line-recovery.plist`、`config/line_recovery.json`）
- **`launchd/bin/claude` 與 `launchd/bin/agy`**：
  - `launchd/bin/claude` 驗證 `data/cli_pins/claude/2.1.316` 具執行權限且 SHA256 為 `64c327e92b2c5e0f78c0a980c5a518121e1f0d140ca771ca5dd4488d1a2dcb3f`，設定 `export DISABLE_AUTOUPDATER=1` 後執行。
  - `launchd/bin/agy` 驗證 `data/cli_pins/agy/1.4.12` 為一般檔案、非 symlink、具執行權限且 SHA256 為 `34423fc9b0af3841e7646aa72a2d40ffeab8218b22d1cb99eac7355f9eba31e3`，設定 `export AGY_CLI_DISABLE_AUTO_UPDATE=true`，並將單一參數 `changelog` 導向 `--version` 探測實際釘選執行檔版本。
- **`launchd/run.sh` 與 `autoload_maintenance.py`**：
  - 兩者皆將 `$project_dir/launchd/bin` 置於 `PATH` 最前端，確保 `cycle` 與 `line-recovery` 皆優先解析上述 wrapper，無須改動已驗收之底層 runner 或 CLI。
- **`launchd/org.forecast-lab.line-recovery.plist` 與 `config/line_recovery.json`**：
  - Plist 註解已更新為雙審通過後安裝與 Louis 明確授權說明，設定 `StartInterval` 為 300 秒、`RunAtLoad` 為 `false`；設定檔版本為 `line_recovery_v1`、`authorization` 為 `PLAN D140`。

#### (4) 預算帳務、Discovery 喚醒與安全事件傳播（`src/budget.py`、`src/discovery.py`、`src/flab/ledger.py`、`src/advise.py`、`src/practice.py`）
- **Budget 交易與未完成嘗試阻擋**：`src/budget.py` 之 `recover()`、`reserve()`、`outcome()` 均在 `with transaction(self.root):` 中執行；`reserve()` 會檢查未結案之 `active` reservations 與未寫入 `forecasts.jsonl` 的 `player_starts`，若有未決嘗試則以 `unresolved_or_running_attempt` 拒絕新預留。
- **Discovery 喚醒條件**：`src/discovery.py:84-162` 僅在玩家未處於 `stopped`（含停用、`line_stopped` 仍有效、或 `forecasts.jsonl` 中 `quota_retry_at_utc` 尚未到期）且 `line_status.jsonl` 中該玩家最新 `line_resume.at >= job.finished_at` 時，才解除舊 `line_stopped` job 的等待狀態；相關正向與負向情境已由 `tests/test_discovery_cadence.py:136-146, 230-243` 完整覆蓋。
- **安全事件傳播**：`src/advise.py:181-203` 與 `src/practice.py:303-322` 皆將 `recovery_no_rerun` 納入安全停線條件，並於 `transaction(shared)` 保護下寫入 `line_status.jsonl`。

---

### 4. 非阻擋建議與部署後觀測事項（Non-blocking Proposals / Post-deployment Observations）
1. **部署後實況驗證（依 `docs/tasks/D140-AUTO-RECOVERY.md` 既有計畫）**：在 R3A 與 R3B 雙審皆獲 `PASS` 並執行 `deploy.py apply` 與 `deploy.py activate` 後，請持續觀測 `data/operations/d140-auto-recovery-20261105/` 下之技術 metadata，確認自動 `line_resume`、排程器透過既有 rerun 槽發起之補做嘗試，以及待補題目之 `forecast_ok`／`comment_ok` 狀態（維持不檢視開放題預測內容之隔離原則）。

END-D140-CODE-R3B