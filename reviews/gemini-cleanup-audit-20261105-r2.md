**【收尾確認】已完整讀取本補充資料包至結尾標記 `END-CLEANUP-SUPPLEMENT-20261105`，本次輸入無截斷（先前 `auto_reaccept.py:46` 截斷問題已排除；`launchd/run.sh:27` 亦已呈現 `auto_reaccept.py tick || true` 呼叫脈絡）。本回覆僅為獨立清理審查意見，不代表實作核准（No implementation approval），亦未執行任何測試。**

以下僅針對 9 項指定議題逐點給出「判定狀態（已確認／已修正／未獲證實）」、「時機（Timing）」及「實際證據／阻擋項（Blocker）／提案建議（Proposal）」區分：

### 1. `AGENTS.md` 規則 2 與 `PLAN.md` D79 之 Metaculus Cup 矛盾及基線影響
* **狀態**：**已確認並修正假設（Confirmed & Corrected）**。
* **實際證據**：`AGENTS.md:29` 仍將 `Metaculus Cup` 列為試驗場地；`PLAN.md:87`（D79）已明確排除 Cup 並由 M3-W9 實作排除。另 `src/baseline.py:149-165` 證明 `ancestor_sources(base)` 僅掃描 `[base, *base.parents]` 中的 `AGENTS.md`、`CLAUDE.md`、`GEMINI.md` 與特定隱藏目錄，一般 `.md`（如 `PLAN.md`、`HANDOFF.md`）不在基線內。
* **阻擋項 vs 提案**：程式已排除 Cup，故文字落差**非執行期 Blocker**。但是否觸動基線取決於 attempt 暫存目錄 `base`（`AGENTS.md:32` 規定在專案外）的祖先路徑是否包含專案根目錄。
* **時機（Timing）**：**條件式 NOW / LATER（高優先文件修正）**。提案先查驗實機 `base.parents` 是否涵蓋專案根目錄：若不涵蓋，專案內 `AGENTS.md` 不入 `ancestor_sources`，可 **NOW** 免鎖修正；若涵蓋，則屬基線保護範圍，歸入 **LATER** 配合重驗更新。

### 2. `budget._positive`／`forecast._fraction` 與 13 個未用 import 之現窗修補資格
* **狀態**：**政策面已確認並修正排程區分（Confirmed & Corrected；全庫 335 檔零呼叫與 12 個非保護檔清單於本包屬摘要陳述，未附全庫 grep 原文）**。
* **實際證據**：`src/budget.py:106` 與 `src/forecast.py:733` 定義該二私有函式；`src/forecast.py:69-72, 149-150` 證明 `runtime_fingerprint` 僅鎖 `adapters/{player}.py`、`flab/runner.py`、`flab/netguard.py`；`HANDOFF.md:98, 164-167`（D126）明定 `cycle_campaign.json` 只釘死 `run_config_v2.json` 與 `quota_policy.json`，不改這兩份雜湊與驗收指紋的工程修補**不必換窗**。
* **阻擋項 vs 提案**：
  * **強制阻擋項（Blocker）**：13 個候選中位於 `adapters/gpt.py` 的 1 個未用 import 觸及驗收指紋，未重驗前禁止修改；其餘程式碼修改則強制要求「常規回歸＋Gemini 獨立審查＋Claude 親自完整審查＋六鎖互斥程序」。
  * **保守排程偏好（Proposal）**：這 2 個 helper 與非保護檔未用 import **依 D126 強制規則完全有資格（Eligible）在不等待 11/10 換窗下，經雙審＋六鎖立即批次套用**。審查者傾向與其他非緊急重構併批以減少拿六鎖次數，純屬「保守排程建議」，並非政策阻擋項。
* **時機（Timing）**：政策上可 **NOW（需雙審＋六鎖＋回歸）**；提案實務上可併入 **LATER** 程式碼清理批次；唯 `adapters/gpt.py` 必須 **LATER（等重驗）**。

### 3. D91 舊 Telegram 開發模式退役與死碼清理順序
* **狀態**：**已確認（Confirmed）**。
* **實際證據**：`src/telegram_bot.py:282-283` 已將 `dev_*` 指令轉導至 `team_help`，第 `437-439` 行自動取消舊 `dev_notice:` outbox 訊息，第 `462-463` 行已遷移至 `workgroup_mode` / `TeamWorker`。同時 `src/flab/team_runner.py:18` 仍 import `flab.dev_runner.{clean_env, codex_binary}`，`src/flab/team_chat.py:15` 仍 import `flab.dev_tasks.{TaskError, safe_text, public_text}`。
* **阻擋項 vs 提案**：既有 D91 已確立退役方向，**無須再向 Louis 重複確認**。直接刪除舊模組會弄斷 `team_runner`／`team_chat` import（Blocker）。提案依 `HANDOFF.md:118` 先抽離共用 helper 並保留 `parse_dev_command` 轉導與 `dev_notice:` 取消邏輯，再刪除 `dev_command`／`enqueue_dev_notifications` 等死碼。
* **時機（Timing）**：**LATER（階段 B′ 先抽共用模組，後續批次再刪死碼；碰執行期需雙審＋六鎖）**。

### 4. D135「106 個受保護檔」性質與文件歸檔原則
* **狀態**：**已確認並修正（Confirmed & Corrected）**。
* **實際證據**：`src/forecast.py:149-150` 證實永久執行期驗收指紋僅限 `adapters/{player}.py`、`flab/runner.py`、`flab/netguard.py`；`HANDOFF.md:116` 亦證實階段 A 刪除 29 個過時 `.md` 後保護雜湊 100% 不變。D135 的「106 檔」僅為單次部署不變動清單（scoped deployment manifest），並非全庫文件永久指紋鎖死之證據。
* **阻擋項 vs 提案**：**嚴禁隨意修改受保護 `adapters/*.py` 的 docstring**（會破壞驗收指紋，屬 Blocker）；但不應以 docstring 提及為由要求所有舊文件永久滯留，提案可採原路徑保留薄 stub 或索引轉址安全歸檔。
* **時機（Timing）**：審查基準 **NOW** 修正生效；一般文件歸檔／建 stub 可免鎖執行，受保護模組 docstring 則 **LATER（等重驗）**。

### 5. 指向已刪除 `DECISIONS-AFTER-D84.md` 之 6 處失效連結修復
* **狀態**：**方向已確認／具體行號由補充包摘要提供（Confirmed direction；本包未附該 5 份 `.md` 原文，依審計摘要採認）**。
* **實際證據與區分**：`M3-V15-SCHEMA:90`、`M3:13/44`、`MINIBENCH-READINESS:98`、`OVERNIGHT-D84:89/141` 共 6 處指向 `392335b` 已刪除之檔案屬實質壞連結；`PLAN.md:1306 (...)` 則為格式範例而非壞連結。
* **阻擋項 vs 提案**：提案僅修正歷史出處（provenance）連結或標註已併入 `PLAN.md`，**嚴禁將已淘汰的舊指令還原為現行規範（Blocker）**。
* **時機（Timing）**：**NOW（免鎖，純文件修補批次）**。

### 6. 實務依賴確認（`claude_baseline_candidate`、`flab.schema`、`dev_tasks.TaskError`）
* **狀態**：**已確認保留（Confirmed KEEP；`TaskError` 直接見於本包 `dev_tasks.py:38` 與 `team_chat.py:15`，其餘呼叫點由審計摘要確認）**。
* **實際證據與行動**：`repair_claude.py:23,242` 使用 `claude_baseline_candidate.approve`、`validate.py:20` 與 `tests/test_validate.py:9` 使用 `flab.schema`、`project_control.py:6` 與 `team_chat.py:15` 使用 `dev_tasks.TaskError`。三者皆有實質依賴，移除會造成功能損壞（Blocker），**全部保留（KEEP）**。
* **時機（Timing）**：**NOW（立即自清理刪除候選清單移除）**。

### 7. 跨測試 import 與 `flab/__init__.py` 公開匯出盤點
* **狀態**：**已確認並修正計數（Confirmed & Corrected）**。
* **實際證據**：直接核對本包 `src/flab/__init__.py:24-53` 原始碼，`__all__` 明確宣告且匯出共 **28 個名稱**（修正先前 33 或 27 之誤算）；45 個測試檔／123 處跨 `test_*.py` import 且無同範疇重複測試定義則由審計摘要確認。
* **阻擋項 vs 提案**：`flab/__init__.py` 的 28 個 re-exports 與框架 callbacks 必須 **KEEP**（誤刪屬 Blocker）；跨測試共用函式提案依 `HANDOFF.md:118` 分批抽至 `tests/helpers/`。
* **時機（Timing）**：re-exports **NOW 保留**；測試解耦排入 **LATER（階段 B′，免鎖）**。

### 8. `--synthetic-host` 下 3317 pass / 34 fail 根因與基準區分
* **狀態**：**完全確認（Confirmed with direct source evidence）**。
* **實際證據**：
  1. **測試基準不同**：本次為掛載 `--synthetic-host` plugin 之隔離 harness，與 D135 先前 `3251/100` 並非同一 harness 基準，不能視為同基準改善或回歸。
  2. **27 題 M5 題型設定未隔離**：`tests/test_main_site_trial.py:58` 硬編碼 `"binary,multiple_choice"`（25 題）、`tests/test_poll_questions.py:93` 硬編碼 `# numeric waits for M5` 為 `False`（1 題）、`tests/test_cycle.py:368-369` 預期非二元題為 `unsupported_type`（1 題），皆因直接讀取現行已啟用 M5 的設定而未在測試中固定題型 fixture。
  3. **7 題 `--synthetic-host` 全域 fixture 衝突**：`tests/offline_workcopy.py:22-23` 的 `autouse=True` 在 `tmp_path` 建立 `synthetic-project` 子目錄，導致 `tests/test_config.py:97` 手動二次呼叫 `host.__wrapped__` 時 `mkdir()` 撞名（1 題）、`tests/test_resolve.py:883,895` 對 `tmp_path.iterdir()` 全數呼叫 `read_bytes()` 撞上子目錄報錯（5 題）、`tests/test_resolve.py:907` 斷言 `list(tmp_path.iterdir()) == []` 失敗（1 題）。
* **阻擋項 vs 提案**：**嚴禁為求測試 PASS 而刪除失敗測試或偷改正式 `config/question_types.json`（Blocker）**。提案修復測試隔離（在上述測試固定／monkeypatch 題型設定；`offline_workcopy.py` 改 `mkdir(exist_ok=True)` 或調整 `test_resolve.py` 受測資料夾為 `tmp_path / "data"`）。
* **時機（Timing）**：**NOW（免鎖，純測試隔離與 fixture 修正）**。

### 9. 整體分期（NOW / LATER / 換窗）與殘留歧見／待補證據盤點
* **狀態**：**已確認（Confirmed）**。
* **分期總結**：
  * **NOW（免鎖）**：文件 metadata 與狀態／`INDEX.md` 同步、第 5 點 6 處壞連結修復、第 8 點測試隔離與 `offline_workcopy` fixture 衝突修正。
  * **LATER（條件觸發，不綁任意日期）**：先收斂並記錄 D132–D135 工作區未提交變更（dirty work），完成 `tests/helpers/` 與共用模組（`dev_runner`、`dev_tasks`、`m2_campaign`）抽離後，再執行舊 TG 死碼與未用程式碼清理（碰執行期模組須經 Gemini＋Claude 雙審與六鎖）。
  * **換窗或重驗綁定**：凍結設定（`run_config_v2.json`、`quota_policy.json`、`cycle_campaign.json`）、`fail_soft.py` 常數遷移與 B 類參數僅能透過核准之 `budget_handoff_v2` 或 11/10 新登記窗口（階段 C）變更；驗收指紋檔僅能於重驗時變更。全程不隱含 M6 主賽啟用。
* **殘留歧見與待補證據（Missing / Pending Proofs）**：
  1. **無原則性歧見**：政策強制規定（D126）與保守排程偏好、以及驗收指紋實際範圍已完全對齊。
  2. **待補齊之 4 項實務證據**：(a) 不帶 `--synthetic-host` 的 plain harness 完整測試重跑結果（正另行執行中）；(b) 實機 `make_attempt_dir` 之 `base.parents` 是否包含專案根目錄 `AGENTS.md` 的路徑查驗；(c) D132–D135 dirty work 收斂前後的 `git status / diff` 紀錄；(d) 本短包僅以摘要敘述之第 2、5、6 點全庫 grep 與檔案行號，於正式提交 patch 前保留本地檢索輸出備查即可。