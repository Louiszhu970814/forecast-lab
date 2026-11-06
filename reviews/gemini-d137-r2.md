# D137 正式啟用完整 Patch 與部署腳本獨立審查報告（r2）

## 1. 審查範圍與方法聲明

- **資料來源限制**：本審查僅依據封包（Source Packet）所提供之內容進行檢視，未使用任何外部檔案、指令、網路、委派或即時預測介面。
- **閱讀範圍誠實聲明**：
  - 封包內提供**完整修改後全文**之檔案（已全文檢視）：`src/budget_handoff.py`、`config/cycle_campaign.json`、`config/submit_mode.json`、`config/tournaments.json`、`docs/tasks/D137-MAIN-LAUNCH.md`、`tests/test_main_launch_handoff.py`，以及私人營運腳本 `apply_reviewed.py`、`test_deployment.py`、`gate_and_deploy.py`、`simulate.py`、`kickstart_reviewed.py` 與對照用之 `live_window.py`。
  - 封包內僅提供**差異（diff）或部分節錄**之檔案（僅就封包提供之片段與 diff 檢視，不宣稱已讀未提供之其餘全文）：`PLAN.md`、`README.md`、`docs/tasks/HANDOFF.md`、`docs/tasks/INDEX.md`、`src/continuous.py`（提供完整差異與第 1–106 行全文）、`cycle.py`（提供第 140–194 行節錄）、`autoload_maintenance.py`（提供 `require_idle` 全文與 `Session.__init__` 節錄）、`tests/fixtures/d121_protected_hashes.json`、`tests/fixtures/m5_legacy_golden.json`、`tests/test_budget_handoff.py`、`tests/test_config.py`、`tests/test_d121_fail_soft.py`、`tests/test_m3_integration.py`、`tests/test_numeric.py`。
- **測試與雜湊聲明**：本審查者未自行執行任何測試或計算雜湊；文中所引述之測試結果（3393 passed、569 passed、17 passed）、AST 掃描結果、帳冊重播與 SHA256 皆為整合者於封包中提供之實際量測證據。

---

## 2. Blocking 缺陷檢查（Blockers）

經逐項核對產品程式碼、設定檔、測試案例、文件敘述與私人營運／部署腳本（含 r2 新增之 P1–P8 補強），**未發現任何工程 Blocker 或權限／範圍矛盾（0 項 blocking 缺陷）**。

---

## 3. 權限、範圍與里程碑證據核對（Actual Evidence vs. Governance Boundaries）

1. **使用者授權與時程調整依據**：
   - 使用者於 `2026-11-05T01:49:59.950469Z` 明確指示：「複審完 修改完可以 就直接開始正式賽了」。
   - `PLAN.md`（v1.91 §3、§10 M6、§15 D137、§16）、`README.md`、`docs/tasks/D137-MAIN-LAUNCH.md`、`docs/tasks/HANDOFF.md` 與 `docs/tasks/INDEX.md` 均正確將此記錄為**有限准入決策**，取代原本「等待 M4 官方分數後才加入主賽」的時程安排，授權 `bot 311763` 在通過雙審與工程前檢後同時參與當季主賽 `33121`（`fall-futureeval-2026`）與既有 `minibench`。
   - 所有文件皆明確區分「已獲授權／隔離準備中」與「實際已啟動／已送出」，未將授權直接當作已上線或已送出預測。

2. **尚缺證據之誠實揭露（無冒稱通過）**：
   - **M3（D114 觀測缺口）**：封包附有 `d114-historical-audit.txt` 實際輸出，顯示 `observation_id: "d114_20261102T231631Z"` 僅完成 `24/168` 輪（截至 `2026-11-03T03:07:28.247Z`），含 `incomplete_poll`、`observation_not_completed`、`partial_poll_snapshots`、`question_uncertainties`，且 `ready_for_replay: false`、`m3_passed: false`。文件與封包清楚說明係在獨立副本取 `git b926380a` 同 SHA 原檔以原實作驗證指紋後取得該結果，未跳過指紋驗證亦未偽造或補造觀測資料。
   - **M4（官方分數與真實結算未完成）**：首批正式已送出題目最早預定結算時間為台北時間 `11/13 20:00`（D132），目前尚無 M4 官方分數與結算驗收。文件明確聲明本次啟用係以 D113–D115 試驗端到端、D116 已披露限制之歷史全樣本／壓力重播、M5 數值與離散題驗收、現有 live MiniBench 與工程雙審為有限依據，絕不宣稱 M3 完整觀測或 M4 統計／準確度驗收已完成，亦不作完整 ITT 結論。
   - **官方狀態核對**：`official-metadata.json` 記錄 `2026-11-05T01:55:17.478929+00:00` 僅使用 `GET` 確認 `bot 311763`（`is_bot: true`）及主賽 `33121`（`slug: "fall-futureeval-2026"`, `is_ongoing: true`），無任何 `POST` 或開放題預測內容進入封包或主工程模型。

3. **預算、窗口與固定參數邊界（P7）**：
   - 主賽與 MiniBench 共用同一預算窗口與額度：根計數起點 `accounting_start` 維持 `2026-11-02T13:03:00.000Z`，候選窗口起點為 `2026-11-05T01:49:59.950Z`，終點維持 `2026-11-10T04:00:00.000Z`（不延長、無自動續期）。
   - 三線每週嘗試上限維持 `claude: 130`、`gemini: 200`、`gpt: 130`；`expected_new_questions: 10`、`repair_reserve: 2`、`urgent_slack_s: 120`、`run_config_sha256`（`42936cd6...`）與 `quota_policy_sha256`（`3e11aeab...`）均完全不變。

---

## 4. 產品程式碼與設定審查（Actual Evidence）

### 4.1 `src/budget_handoff.py` 與 `src/continuous.py`
- **有界兩跳接續契約（`continuous_v10` / `budget_handoff_v2`）**：
  - `MAX_LINKS = 2`：支援 `D105 -> D118 (v1) -> D137 (v2)` 最多兩條來源連結；若嘗試第三跳（`len(seen) >= MAX_LINKS` 或 `len(chain) > MAX_LINKS`）、循環引用或重複祖先，均拋出 `ValueError`。
  - **向下相容性**：`_legacy_source` 完整保留 `continuous_v9` + `budget_handoff_v1`（`PLAN D118`）的單跳驗證規則，且當 `v2` 追溯至 `D118` 時，遞迴呼叫 `ancestors` 會透過 `setting.get("version") == LEGACY_VERSION` 驗證 `D118 -> D105` 的完整性。
  - **不可變性與參數鎖定**：`ancestors` 嚴格檢查每一跳的 `registered(source_campaign_id, rows)` 之 `hash_obj(old) == setting["source_sha256"]`，要求 `setting["accounting_start"] == chain[-1]["budget_window_start"]`（鎖定根起點 `D105`），要求候選窗口滿足 `old.start < start < old.end` 且 `start < end <= old.end`（禁止提前於來源起點或延後來源終點），並強制核對 `weekly_attempt_caps`、`run_config_sha256`、`quota_policy_sha256`、`expected_new_questions`、`repair_reserve`、`urgent_slack_s` 與來源完全一致。
  - **場地白名單與唯一後繼**：`v2` 僅允許非空之 `venues` 子集 `<= {"main", "minibench"}`；若同一 `old` 已存在其他 `campaign_id` 之延伸後繼，立即拒絕（防止分叉雙重計算或重置額度）。
  - **重疊豁免最小化**：`continuous.py::register` 透過 `allowed_overlap = H.overlap_ids(policy, rows)` 僅豁免驗證通過之直系祖先（`D118` 與 `D105`），任何非直系祖先之重疊窗口仍以 `"continuous budget windows must not overlap"` 拒絕。
  - **防止未登記或竄改之 `v10` 執行期政策**：`accounting_start(root, runtime)` 針對 `runtime.get("version") == "continuous_v10"` 強制比對 `hash_obj(registered(runtime["campaign_id"], rows)) == hash_obj(policy)`，確保即使執行期移除 `budget_handoff` 或修改 `venues`，亦無法規避檢查或動用額度。
- **版本常數與引用檢查（P6）**：
  - `src/continuous.py` 將 `VERSION` 升為 `"continuous_v10"`，並在 `validate_main_site_policy`、`register`、`active_campaign` 明確保留 `"continuous_v9"`。
  - 封包提供之 165 個 Python 檔案 AST 掃描證據確認僅 3 個測試檔引用 `VERSION`，產品程式碼無動態遺漏；且 `tests/test_m3_integration.py` 已補入 `"continuous_v9"` 參數化案例，確保舊案例不流失。

### 4.2 設定檔與保護雜湊（P5）
- `config/cycle_campaign.json`：更新為 `continuous_v10`、`campaign_id: "d137_main_minibench_20261105"`、`venues: ["main", "minibench"]`、`source_campaign_id: "d118_minibench_20261103T0400Z"`、`source_sha256: "6239cd390b401376660f386e82dd9b1d3b0c7da080010d9730137938db7adc3d"`（與修改前 `submit_mode.json` 記錄之 D118 campaign SHA256 完全一致）。其檔案本身之 SHA256 為 `572cb04c68d77ec69641bd73ec99ac25c843fa7a33416e7852f76523175bd9ca`，與 `tests/fixtures/d121_protected_hashes.json`、`tests/fixtures/m5_legacy_golden.json` 及受審目標 manifest 完全吻合。
- `config/submit_mode.json`：`live_window` 綁定 `d137_main_minibench_20261105` 與 `campaign_sha256: "405f0bc02c9a112b140bc25a0704c5c19c62795e393b0a9dfcce550c2b7d2b66"`，起訖時間與 `cycle_campaign.json` 完全一致。
- `config/tournaments.json`：僅將 `main`（`fall-futureeval-2026`, `id: 33121`）之 `submit_whitelisted` 由 `false` 改為 `true`，其餘場地保持不變。
- P5 已澄清兩份保護雜湊 fixture 實際僅更新 `config/cycle_campaign.json` 之 SHA256，其餘受保護檔案與 legacy golden 輸出均未變動。

---

## 5. 私人營運／部署腳本與 r2 補強審查（P1–P8）

1. **P1：部署期間實際停用 `launchd` 排程 timer（`apply_reviewed.py`）**：
   - 如 `cycle.py` 第 140–194 行所示，`mode == "local"` 僅跳過 `W.require_active` 並在最後 `submit` 階段阻擋網路 POST，若排程 timer 觸發仍會執行 `poll` 與啟動模型預測。
   - `apply_reviewed.py` 在持有六鎖（`lock_all`）並通過 `require_idle` 後，於首次執行時先確認 `not self.schedule_disabled()` 且 `self.schedule_loaded()`（若人類先前已停用或卸載排程則直接拒絕，絕不覆蓋人類停止狀態），並於 `prepared.json` 記錄 `'schedule_was_enabled': True`。
   - `pause_schedule()` 嚴格核對 `~/Library/LaunchAgents/org.forecast-lab.cycle.plist` 之 `Label`、`StartInterval == 600` 與 `ProgramArguments`，接著依序執行 `launchctl disable gui/<uid>/org.forecast-lab.cycle` 與 `launchctl bootout gui/<uid>/org.forecast-lab.cycle`，並再次驗證 `self.schedule_disabled() and not self.schedule_loaded()` 後才記錄 `schedule_paused`（含 `installed_plist_sha256`）及寫入任何產品檔案。全程不修改 plist、不影響其他服務、不使用 `-k` 強殺 worker；若部署中途失敗，排程維持停用狀態。

2. **P2：登記前回滾之安全順序（`restore_before_registration`）**：
   - 若於 `register()` 提交點前發生失敗，`restore_before_registration()` 會先還原所有 `f != MODE` 之檔案並逐一驗證其 SHA256 與 `self.initial` 完全一致後，**最後才將 `config/submit_mode.json`（`MODE`）從備份還原**。
   - `test_deployment.py::test_rollback_failure_keeps_mode_local` 已注入還原寫檔失敗（`OSError`），驗證當非 `MODE` 檔案還原失敗時，`submit_mode.json` 必定維持 `local`。

3. **P3：受審排程恢復腳本（`kickstart_reviewed.py`）**：
   - 納入受審證據鏈（SHA256 鎖定於 `packet-inputs-r2.json` 與 `deployment-gate.json`）。
   - 執行時先取得六鎖，強制驗證：自身 SHA256 相符、`operations.jsonl` **最後一筆紀錄必須為 `activation_complete`**、`prepared.json` 之 `schedule_was_enabled` 為 `True`、所有部署目標檔案 SHA256 與 `gate['files']` 一致、`config/STOP_SUBMIT` 不存在、`W.require_active` 驗證通過，且當前已安裝 plist 之 SHA256 與 `schedule_paused` 紀錄完全一致。
   - 在六鎖保護下執行 `launchctl enable` 與 `launchctl bootstrap` 並寫入 `schedule_resume_command` 紀錄；若中途失敗，因 `operations.jsonl` 最後一筆紀錄已變成 `schedule_resume_command`，操作者無法直接重跑 `kickstart_reviewed.py` 跳過檢查，必須先以同一份 `deployment-gate.json` 重跑 `apply_reviewed.py`（重新停用排程、完整跑完前後前檢與 smoke test 並寫出新的 `activation_complete`）後才能再次恢復排程。
   - 釋放六鎖後才執行無 `-k` 之 `launchctl kickstart gui/<uid>/org.forecast-lab.cycle`，並記錄確切指令、UTC 時間與 `returncode`。

4. **P4、P8 與帳冊演練證據（`gate_and_deploy.py`、`simulate.py`、`test_main_launch_handoff.py`）**：
   - **P4**：`test_main_launch_handoff.py` 已誠實區分過期來源阻擋測試（`test_expired_source_remains_blocking`）與 15 項 invalid handoff 參數化測試之整體拒絕契約，不誇大單一不可達分支之獨立 coverage。
   - **P8**：`gate_and_deploy.py` 除了逐一核對 `initial-manifest.json`、`reviewed-files-r2.json`、雙審紀錄與 `packet-inputs-r2.json` 所有 SHA256 外，新增由不可變 `baseline` 與 `stage` 重建完整 unified diff 並斷言與 `patch.diff` 逐字相同。
   - **帳冊副本重播（`simulate.py` / `budget-replay.json`）**：在六鎖與 ledger transaction 下僅抽取 metadata 欄位至暫存目錄重播，驗證 `before`（D118）與 `after`（D137）對三線模型（Claude 已用 26/130、Gemini 已用 25/200、GPT 已用 24/130）之 Tier 1（`protected_attempts: 0`）與 Tier 2（`protected_attempts: 12`）准入結果、剩餘額度與根計數起點（`2026-11-02T13:03:00.000Z`）完全一致，且 `real_model_predictions: 0`、`POST: 0`。
   - **測試集完整性**：`collection-delta.json` 與測試 log 顯示原 3368 案例全數保留（`removed: []`），新增 25 案例後共 `3393 passed`；stage smoke `569 passed`；部署故障注入測試由 15 項擴充為 `17 passed`（新增回滾失敗保留 `local` 與暫停排程失敗阻擋寫入兩項測試）。封包亦誠實註明該 17 項部署故障測試使用 fake hooks，真實 `launchctl` 停用／恢復與前後 12 項 preflight 證據將於雙審通過後之實際啟用時產生並留存。

---

## 6. 非阻斷性觀察與紀錄（Non-blocking Observations / Proposals）

- **無須修改程式碼之營運提醒**：實際執行 `gate_and_deploy.py r2` 與 `kickstart_reviewed.py` 後，請依 `docs/tasks/D137-MAIN-LAUNCH.md` 契約將實際登記時間、前後 12 項 preflight 結果、`launchctl` 停用／恢復／kickstart 紀錄及首輪安全 metadata（僅題數與 attempt 狀態，不含開放題預測數值或推理正文）留存於私人操作目錄。

## 結論：PASS

END-D137-R2