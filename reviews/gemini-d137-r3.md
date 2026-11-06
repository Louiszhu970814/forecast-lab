# D137 r3 設計與程式碼複審報告

## 結論：PASS

---

### 一、 阻斷項（Blockers）

- **無（0 項）**。
- **Claude r2 唯一阻斷項 B1 已確認解決**：
  1. **問題回顧（B1）**：r2 版本中 `schedule_disabled` 僅識別 `"org.forecast-lab.cycle" => true`，但在 Darwin 25（本機為 Darwin 25.5.0）環境下，`launchctl print-disabled` 可能輸出 `disabled`／`enabled`，且初始未設定 override 時該 label 不會出現在列表內（代表預設 enabled）。若僅認 `true` 或未區分預設無 override 與明確停用／啟用狀態，將在停用排程後無法確認狀態或無法於同一個 gate 內安全重入與恢復。
  2. **r3 修復核對**：
     - `apply_reviewed.py` 新增純函式 `disabled_state(output)`，先驗證外框必須符合 `^\s*disabled services\s*=\s*\{` 且以 `}` 結尾；若出現多行 `"org.forecast-lab.cycle"` 則拋出 `ValueError('ambiguous cycle disabled state')`；若無該行則回傳 `None`（代表無明確 override）。
     - 若存在該行，僅允許完整匹配 `\s*"org\.forecast-lab\.cycle"\s*=>\s*(true|false|disabled|enabled)\s*`，將 `'true'` 與 `'disabled'` 映射為 `True`，`'false'` 與 `'enabled'` 映射為 `False`，任何未知值或格式異常一律拋出 `ValueError`。
     - **三態語意與階段區分**：
       - 初次進入 `deploy_locked()`（`prepared.json` 尚未建立）時，呼叫 `self.schedule_disabled()`（預設 `explicit=False`），此時回傳 `None`（預設無 override）或 `False`（明確 enabled）均視為未停用而放行，並將 `'schedule_was_enabled': True` 寫入 `prepared.json`；若為 `True` 則拒絕部署。
       - 執行 `pause_schedule()` 時，先以 `/bin/launchctl print` 與 `/bin/launchctl print-disabled` 記錄原始輸出至 `launchctl-reads.jsonl`，確認無執行中 `pid` 後執行 `/bin/launchctl disable`（及 `loaded` 時執行 `/bin/launchctl bootout`），以 10 秒有界輪詢（每 0.5 秒）確認卸載，最後呼叫 `self.schedule_disabled(explicit=True)`——此時 `state is None` 會拋出 `ValueError('explicit disabled override required')`，強制要求停用後必須呈現明確的 `true` 或 `disabled`。
       - 若同 gate 重入恢復（`prepared.json` 已存在），不再要求當下排程仍處於 loaded／未停用狀態，而是核對 `prepared.json` 的 `schedule_was_enabled is True` 與 `gate_sha256`，並再次執行具冪等性的 `pause_schedule()`（僅在 `loaded` 為真時才 `bootout`）。
       - 在 `kickstart_reviewed.py` 中，於 `enable` 與 `bootstrap` 後重新執行 `/bin/launchctl print` 與 `/bin/launchctl print-disabled`，並強制檢查 `disabled_state(r.stdout) is not False` 時拋錯（要求恢復後必須具有明確的 `false` 或 `enabled` override，而非僅回到 `None`）。

---

### 二、 實際證據核對與審閱範圍界線（Actual Evidence）

本審查僅依據封包內提供之靜態文字與主機證據進行核對，審查模型未自行執行任何命令、檔案讀寫、網路請求或測試，亦不宣稱已執行實際停用、登記或開賽切換：

1. **產品 18 檔（與 r1/r2 位元組完全一致，無變更）**：
   - **已審閱完整變更後全文之產品檔案**：
     - `src/budget_handoff.py`（SHA256: `b318501ba...`）：實作 `budget_handoff_v2` 與 `MAX_LINKS = 2`，完整保留 `budget_handoff_v1`（`LEGACY_VERSION`）相容性；強制驗證 `continuous_v10`、`PLAN D137` 授權、雙跳上限、來源鏈 SHA256 不可變、根帳期起點 `accounting_start`（`2026-11-02T13:03:00.000Z`）保留、窗口不越界不延後、固定設定與配額上限（130/200/130）不變、場地限縮於 `{"main", "minibench"}`、單一後繼防分叉，且 `accounting_start()` 確保 `continuous_v10` 即使被移除 `budget_handoff` 欄位亦絕不放行未登記或不一致之 policy。
     - `docs/tasks/D137-MAIN-LAUNCH.md`（SHA256: `8cad7618f...`，新增檔完整 diff）：誠實披露有限准入依據、D114 原觀測僅 24/168 輪（`ready_for_replay=false`、`m3_passed=false`）與 M4 官方結算未完成之限制，以及不可回滾帳冊之部署復原契約。
     - `tests/test_main_launch_handoff.py`（SHA256: `a92de0226...`，新增檔完整 diff）：涵蓋二次接續、根時鐘與共用額度、未決嘗試阻斷、15 種無效突變拒絕、第三跳拒絕、多執行緒併發防分叉、過期來源整體拒絕、非祖先重疊拒絕、根祖先竄改拒絕及 runtime 欄位剝離／場地變更拒絕。
     - `config/cycle_campaign.json`（SHA256: `572cb04c6...`）、`config/submit_mode.json`（SHA256: `29e3241d0...`）、`config/tournaments.json`（SHA256: `a0c5007b8...`）：完整結構已於 diff 呈現，`d137_main_minibench_20261105` 綁定 `d118_minibench_20261103T0400Z`（`source_sha256: 6239cd39...`），並由 `submit_mode.json` 釘住 `campaign_sha256: 405f0bc02...`；白名單僅開啟 `main`（33121）與 `minibench`。
   - **僅審閱封包提供之 diff 或部分摘錄之產品檔案（未讀完整未變更全文）**：
     - `src/continuous.py`（SHA256: `440244584...`）：封包提供 unified diff 及檔案開頭至 `active_campaign` docstring 之片段（含完整 `validate_main_site_policy` 與 `register`），確認 `VERSION = "continuous_v10"`、各函式保留 `"continuous_v9"` 相容，且 `register` 透過 `H.overlap_ids(policy, rows)` 允許雙跳祖先窗口重疊並拒絕非祖先重疊；`active_campaign` 後半與其餘未變更函式未包含於封包全文。
     - `PLAN.md`、`README.md`、`docs/tasks/HANDOFF.md`、`docs/tasks/INDEX.md`：僅審閱 diff，確認狀態描述誠實區分「Louis 已授權」與「實際啟用待操作證據」，未把授權當成已啟動或 M3/M4 完整通過。
     - `tests/fixtures/d121_protected_hashes.json`、`tests/fixtures/m5_legacy_golden.json`：僅審閱 diff，兩份保護清單僅更新 `config/cycle_campaign.json` 之 SHA256 為 `572cb04c68d77ec69641bd73ec99ac25c843fa7a33416e7852f76523175bd9ca`（`submit_mode.json` 與 `tournaments.json` 並不在該 fixture 雜湊清單內，而由 `test_config.py` 等驗證）。
     - `tests/test_budget_handoff.py`、`tests/test_config.py`、`tests/test_d121_fail_soft.py`、`tests/test_m3_integration.py`、`tests/test_numeric.py`：僅審閱 diff，確認舊 `v9`/`LEGACY_VERSION` 測試固定與新增 `v10` 參數化覆蓋。
   - **封包提供之未變更參考程式**：
     - `src/live_window.py`（全文）與 `autoload_maintenance.py` 之 `require_idle`（全文）及 `AGENTS.md` 硬規則第 2 條摘錄（確認硬規則僅要求 Louis 核准與不干預個別預測，無必等 M4 字樣）。

2. **最終營運與部署腳本（完整全文審閱）**：
   - `apply_reviewed.py`（SHA256: `e57402097...`）：
     - 強制六鎖（`lock_all`）+ `require_idle(..., horizon_s=1200)` + `STOP_SUBMIT` 檢查。
     - 所有 `launchctl` 呼叫均改用絕對路徑 `/bin/launchctl` 並設 `timeout=30`；在執行 `disable`／`bootout` 前先記錄並解析 `print` 與 `print-disabled`，若 `loaded and self.schedule_has_pid` 則拒絕 `bootout`；確保持續 10 秒有界輪詢卸載並確認明確 disabled override 後，才寫入 `MODE = local` 並修改產品檔案。
     - 嚴格遵守帳冊單向提交點（commit point）：登記前失敗先還原其餘檔案並驗證 SHA256，最後才還原 `MODE`（若還原失敗則保留 `local`）；登記後失敗或帳冊狀態未知（`stopped_unknown_ledger`）一律強制保留 `MODE = local`、不回滾帳冊、不回退新程式碼、排程保持停用。
   - `gate_and_deploy.py`（SHA256: `2d66d4c38...`）：
     - 開頭拒絕 `sys.flags.optimize` 與 `PYTHONOPTIMIZE`，防止 `assert` 被 `-O` 靜默移除。
     - 驗證 `initial-manifest.json`、重建 `patch.diff` 逐字一致、核對 `3393 passed`／`27 passed`／`569 passed`、`collection-delta.json`（3368 -> 3393，0 移除）、`budget-replay.json`、兩家 review start/end 紀錄與 `packet-inputs-r3.json` 內嵌雜湊。
   - `kickstart_reviewed.py`（SHA256: `d1d72c42d...`）：
     - 同樣拒絕 `-O` 與 `PYTHONOPTIMIZE`；在六鎖內核對自身 SHA256、`operations.jsonl` 最末筆為 `activation_complete`、`prepared.json` 之 `schedule_was_enabled`、所有目標檔案 SHA256、無 `STOP_SUBMIT`、`W.require_active` 及 `installed_plist_sha256` 後，才執行 `/bin/launchctl enable` 與 `bootstrap`。
     - 釋放鎖後、執行 `kickstart`（不含 `-k`，避免殺死剛好由 timer 啟動的 worker）前，重新執行 `/bin/launchctl print` 與 `/bin/launchctl print-disabled`，強制確認 `returncode == 0` 且 `disabled_state(r.stdout) is False`（明確 enabled）。若 resume 中途失敗，因 `operations.jsonl` 已追加 `schedule_resume_command`，重跑 `kickstart_reviewed.py` 會被 `records[-1]['type']=='activation_complete'` 攔截，必須依約重跑同 gate 的 `apply_reviewed.py` 完成檢查並重新寫入 `activation_complete`。
   - `test_deployment.py`（SHA256: `837375917...`）：共 27 項測試，包含提交前還原、還原失敗保留 `local`、提交後 roll-forward、未知帳冊損毀不回滾、六鎖衝突拒絕、`pause_schedule` 失敗不改檔、`disabled_state` 四種枚舉值（`true`/`false`/`disabled`/`enabled`）與 `None`／未知／重複行拒絕、真實主機唯讀樣本（`host-print-disabled-r3.txt`）解析，以及停用後 `explicit=True` 要求明確 override。

3. **主機測試與唯讀證據核對**：
   - `host-launchctl-r3.json`：證實本機 Darwin 25.5.0 上 `print-disabled` 輸出含 `enabled: 2`、`disabled: 5`，且 `org.forecast-lab.cycle` 無 override（預設 enabled）；`print` 顯示服務已載入、`state = not running`、無 `pid`、`run interval = 600 seconds`。
   - `full-tests-r3.txt`（3393 passed）、`deployment-fault-tests-r3.txt`（27 passed）、`stage-smoke-r2.txt`（569 passed）。
   - `budget-replay.json`：三線 Tier 1/2 共 6 組真實用量 metadata 投影於接續前後配額與准入結果完全一致（Claude 26/130、Gemini 25/200、GPT 24/130，根起點皆為 `2026-11-02T13:03:00.000Z`，0 真實模型呼叫、0 POST）。
   - `official-metadata.json`：唯讀 GET 確認 bot `311763`（`is_bot: true`）與主賽 `33121`（`fall-futureeval-2026`，`is_ongoing: true`）。
   - `continuous-version-consumers.json`：165 個 Python 檔 AST 掃描僅 3 處測試直接引用 `VERSION`（動態計算存取不作過度宣稱）。

---

### 三、 非阻斷建議與後續執行注意事項（Proposals & Operational Notes）

1. **實際 `launchctl` 變更與恢復的現場核對**：单元测试中 `launchctl` 的寫入操作（`disable`、`bootout`、`enable`、`bootstrap`、`kickstart`）仍為 fake/mock，尚未對本機真實 launchd 執行。實際執行 `gate_and_deploy.py r3` 與 `kickstart_reviewed.py` 時，請確認 `launchctl-reads.jsonl` 與 `operations.jsonl` 中實際記錄的 `disabled_state` 轉換（`None -> True -> False`）與 returncode 均為預期值。
2. **首輪觀測與事實文件追加界線**：開賽後首輪觀測請嚴格維持僅檢視安全 metadata（題數、attempt 狀態、是否 POST 成功），不預覽開放中題目之機率、推理或留言正文；且區分「排程 active」、「模型 attempt 開始」與「POST 成功」三種狀態，待實際 kickstart 與觀測完成後再追加最終事實紀錄，不再更動已審核之程式碼與設定檔。

END-D137-R3