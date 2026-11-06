# Forecast Lab 主賽前過時文件與死碼（Dead Code）獨立盤點審查報告

---

## 一、審查範圍、證據邊界與不授權聲明（區分實際證據、提案與阻擋條件）

1. **任務性質（僅限盤點，無實作或變更動作）**：
   - 本次審查僅依 Louis 指示，針對主賽（FutureEval Main Tournament，`M6`）前之過時文件與疑似死碼進行**獨立靜態盤點（Inventory ONLY）**。
   - 本報告**不執行任何檔案刪除、程式碼修改、部署、Git commit/push，亦不請求或捏造任何核准（Approval）**。
2. **不構成主賽（M6）啟用授權**：
   - 本次盤點請求**絕不授權啟用 FutureEval 主賽（project `33121` / `M6`）**，亦不取消 `PLAN.md` §3.6、§10（M6）及 §15（D117、D118、D132、D133）所訂之主賽前置條件（仍須待 MiniBench 首輪與 M5 表現評估完成，並由 Louis 逐場明確核准）。
3. **審閱範圍與截斷限制（不宣稱全專案源碼審查）**：
   - **實際證據**：全專案快照共盤點 **335 個檔案**，惟本次僅由來源封包提供選取之代表性文件與源碼證據，且來源封包於 `src/auto_reaccept.py` 第 46 行處截斷（`<truncated 104917 bytes>`）。本報告**不宣稱已完成全專案 335 檔之完整源碼審閱**；凡未完整出現於可見封包內之檔案或符號，均嚴格標註為 `UNPROVEN` 或列明驗證前提。
   - **無敏感資料與無即時預測**：封包內未提供任何真實預測輸出、帳號憑證（`.env` / Token）或線上模型呼叫。
4. **Git 工作樹與測試基線現況（不宣稱已重跑測試或全數 PASS）**：
   - **實際證據（Git 與計畫版本）**：目前基準 commit 為 `HEAD 2f202bf`，工作樹上另有既有未提交（dirty）之 `D132`～`D135` 變更，`PLAN.md` 最新版本為 **`v1.89`**（`2026-11-05 UTC`）。
   - **實際證據（測試紀錄）**：本次審查**未執行任何測試指令**。依 `docs/tasks/HANDOFF.md:7` 與 `PLAN.md:1844`（v1.89 / D135）既有紀錄，最近一次完整測試套件結果為 **`3251 passed / 100 failed`（100 個既有基線失敗節點與摘要不變，另 D135 相關測試 240 passed、部署故障測試 6 passed）**。此為既有歷史紀錄，**並非本次重新執行之結果，亦非全套 PASS**。
   - **實際證據（D126 已清理項目）**：`D126`（commit `392335b`）已刪除 29 個零引用過時 `.md` 檔（`PLAN.md:1825`、`HANDOFF.md:116`）；本報告**不重複提議刪除已於 D126 移除之檔案**。

---

## 二、對靜態 AST 死碼掃描結果之獨立挑戰（為何 AST 候選不等於真死碼）

靜態 AST 匯入／引用掃描僅為**候選產生器（Candidate Generator）**，本身**不是死碼證明（Not Proof）**。根據本次封包源碼證據，直接依 AST「0 引用」刪除檔案將引發嚴重生產事故，具體挑戰如下：

1. **`from-package` 匯入盲區（Package-attribute Import Blind Spot）**：
   - **實際證據**：`src/auto_reaccept.py:21` 使用 `from flab import auto_reaccept_policy as P`。許多簡化版 AST 掃描器僅解析 `import flab.auto_reaccept_policy` 或 `from flab.auto_reaccept_policy import ...`，會將 `src/flab/auto_reaccept_policy.py` 誤判為「無任何模組匯入」的死碼。事實上該模組為 D124 已上線之 `auto_reaccept_v1` observe 核心策略模組。
2. **動態匯入（Dynamic Imports）與子程序執行（Subprocess Script Invocation）**：
   - **實際證據**：`src/auto_reaccept.py:9` 匯入 `importlib`；`src/flab/team_runner.py:196` 透過 `execute([sys.executable, str(Path(project) / spec["script"])], ...)` 由協調器動態執行已登記之離線續作腳本（`Board(project).action_spec(spec)`）。此類呼叫不會出現在靜態 `import` 圖中。
3. **頂層 CLI 入口與 Launchd / Shell 排程接點（CLI Entrypoints & Shell Hooks）**：
   - **實際證據**：`src/telegram_bot.py`（由 `launchd/org.forecast-lab.telegram.plist` 啟動）、`src/project_control.py`（D91 主持人命令列入口，`AGENTS.md:54`）、`src/post_close_audit.py`（D126/D135 每日閉題三閘門健檢工具）、`src/resolve.py` 與 `src/score.py`（D127/D129 M4 結算與計分入口）、`src/practice.py`（D133 閉題練習入口）、`src/auto_reaccept.py`（由 `run.sh` 於週期鎖釋放後呼叫，限時 120 秒，`HANDOFF.md:143`）、`src/claude_baseline_candidate.py`（D119/D131 外掛基準限縮核准入口），在 `src/` 內部皆無須被其他業務模組 `import`，但全數為現行有效之生產或運維入口。
4. **跨階段模組之共用函式耦合（Cross-Module Helper Coupling）**：
   - **實際證據**：
     - `src/flab/dev_runner.py` 雖屬已停用之 D73 舊手機開發模式，但現行 D91 生產模組 `src/flab/team_runner.py:18` 明確執行 `from flab.dev_runner import clean_env, codex_binary`。
     - `src/m2_campaign.py` 雖名為 M2 本地活動模組，但其 `discover_tournaments()`（第 24 行）仍被執行期模組（如 `continuous.py`，見 `HANDOFF.md:118` 階段 B′ 第 3 項）共用。
5. **安全防呆攔截（Safety Fallbacks）與舊帳冊向後相容（Old-Ledger Compatibility）**：
   - **實際證據**：
     - `src/telegram_bot.py:188, 281–283` 仍呼叫 `src/flab/dev_tasks.py:60` 的 `parse_dev_command()`，將舊 `/dev`、`開發：` 指令攔截並轉為 `team_help`。若刪除 `parse_dev_command()`，`開發：...` 等非 `/` 開頭文字將落入第 284 行 `elif kind == "help" ... kind, value = "question", ("gpt", incoming)`，**誤觸發 GPT 線上模型問答並消耗正式額度**！
     - `src/telegram_bot.py:241–245, 437–445` 保留 `Schedule` 與 `dev_notice:` / `scheduled_for` 取消邏輯，以確保重播 `data/telegram_events.jsonl` 歷史追加帳冊時不會崩潰或誤送舊訊息。
     - `src/score.py`（`PLAN.md:1826` D127 r5/r6）專門保留對非 live 且無 snapshot 之早期 V15 探針送出列（D87）的 `skipped` 相容處理，否則讀取正式 `data/submissions.jsonl` 時會產生假錯誤。
6. **受保護雜湊與驗收指紋（Protected Hashes & Acceptance Fingerprints）**：
   - **實際證據**：`PLAN.md:1844`（v1.89）載明測試套件驗證 **106 個保護檔不變**（另有 28 筆受保護雜湊，`PLAN.md:1826`），且 `src/adapters/*.py` 與 `src/quota.py` docstring 直接引用 `docs/tasks/M1-*.md` 原路徑（`docs/tasks/INDEX.md:44`）。任意刪除或搬移將直接破壞驗收指紋或引發回歸測試失敗。

---

## 三、過時文件與死碼獨立盤點明細表（18 項具體分組發現）

> **分類定義**：
> - **`NOW`**：可於當前免鎖範圍內立即修正／清理（不影響 D118 固定設定雜湊、不影響受保護指紋、不衝突 D132～D135 未提交變更）。
> - **`LATER`**：確屬過渡、耦合或待重構項目，但**必須滿足明確之事件前置條件（Event-based Prerequisites）**後方可執行。
> - **`KEEP`**：現行有效規格、CLI 入口、安全防呆、歷史帳冊相容邏輯或受保護之稽核證據，**不得刪除**。
> - **`UNPROVEN`**：因封包截斷或未附完整源碼／測試節點，僅有間接線索，未滿足刪除證據標準。
> *(註：審查註記依 `AGENTS.md` 格式標註 `[§章節] 建議｜理由｜blocking 或 non-blocking`，係針對「所提議之清理動作」判定是否阻擋，不構成新增專案核准門檻。)*

| 編號 | 精確檔案／符號 (Exact Files / Symbols) | 實際證據 (Actual Evidence) vs 提案與阻擋條件 | 處置分類 | 事件前置條件 (Event-based Prerequisites) ／ 保護邊界 | 風險優先序與 PLAN 審查註記 |
|---|---|---|---|---|---|
| **F-01** | `README.md`<br>（第 5～13 行：`目前狀態（截至 2026-11-03 UTC，計畫書 v1.78）`） | **實際證據**：`README.md:5` 狀態仍停留在 `2026-11-03 UTC`、`v1.78` 與 `D125`，未反映 `PLAN.md` `v1.89`（`2026-11-05`）及 `D126`～`D135`（M4 `resolve_v2`/`score_v2`、D128/D129 live ITT、D130/D131 三線 CLI pin 恢復、D132/D134 人類賽報告、D133 閉題練習、D135 `post_close_audit_v2`）。<br>**提案**：更新文字摘要至 v1.89，保留檔案本身（`dev_runner.py:25, 223` 引用該檔）。 | **`NOW`**<br>（僅更新內容，檔案 **`KEEP`**） | 免六鎖（純文件更新，不碰執行期模組），惟須確認不覆寫 D132～D135 dirty 狀態。 | **中（文件誤導風險）**<br>`- [§15 D126–D135 / §16 v1.89] 同步 README.md 狀態摘要至 PLAN v1.89｜避免協作者誤讀專案停留在 v1.78｜non-blocking` |
| **F-02** | `docs/tasks/INDEX.md`<br>（第 16 行 `M4-RESOLVE-SCORE.md` 說明欄、第 18 行 `HANDOFF.md` 決策範圍） | **實際證據**：`docs/tasks/INDEX.md:16` 仍寫「首題實戰結算待台北 11/06 21:00」，但 `HANDOFF.md:39` 與 `PLAN.md:1833`（D132）已透過官方 API 核對更正：已送出之 10 道正式 MiniBench 題最早預定結算為「台北 11/13 20:00、最晚 11/14 12:59」，舊 11/06 僅為 M4 開發驗收安排。同檔第 18 行標註 `HANDOFF.md` 為 `D118～D134`，漏列第 3 行已出現之 `D135`。 | **`NOW`**<br>（修正索引說明，原任務檔 **`KEEP`**） | 免六鎖；直接修正 `docs/tasks/INDEX.md` 第 16、18 行描述文字。 | **中（時程誤判風險）**<br>`- [§15 D132, D135] 更正 docs/tasks/INDEX.md 中 M4 正式題首題結算日描述為 11/13–11/14 並將 HANDOFF 範圍更新至 D135｜與 D132 官方 API 核對結果一致｜non-blocking` |
| **F-03** | `docs/tasks/HANDOFF.md`<br>（第 24、26～33 行 `D133 原交接歷史`；第 92～121 行 `階段 A ～ 階段 D` 舊時程表） | **實際證據**：`HANDOFF.md:10–16` 已記錄 D133 工程交付完成（`v1.88`），但第 26～33 行仍保留同日稍早「本輪只完成文件，練習尚未開始」之舊交接段落；第 117 行階段 B 仍列 `11/05 ～ 11/06 21:00 前` 上線 `resolve.py`/`score.py`（實際已於 D127/D129 上線）。<br>**阻擋條件**：目前工作樹含 `D132`～`D135` 未提交變更（`HANDOFF.md:8, 16`），立即大幅刪減交接檔易造成未 commit 證據混淆。 | **`LATER`** | **前置事件**：待 `D132`～`D135` 既有 dirty 變更完成 git commit 後，再於下一次交接整理時收斂歷史段落並加註階段 B 已完成標記。 | **低（交接閱讀冗餘）**<br>`- [§15 D126, D132–D135] 待 D132–D135 commit 後收斂 HANDOFF.md 內已完成的 D133 過渡段落與階段 B 狀態｜避免未提交工作樹發生衝突｜non-blocking` |
| **F-04** | `docs/tasks/INDEX.md` 第 2 節標註 `SUPERSEDED` 之 6 份任務檔：<br>`MINIBENCH-LIVE-D117.md`、`M3-MC-READBACK-D99.md`、`PLATFORM-CLUES-D98.md`、`TELEGRAM-DEV.md`、`FAIL-SOFT-RELAX-PROPOSAL.md`、`M3-V15-SCHEMA.md` | **實際證據**：`docs/tasks/INDEX.md:29–39` 已將此 6 檔明確標記為 `SUPERSEDED（僅作沿革證據保留）`。然而 `PLAN.md:992` 直接引用 `docs/tasks/MINIBENCH-LIVE-D117.md`，`PLAN.md:1818`（D121）直接引用 `docs/tasks/FAIL-SOFT-RELAX-PROPOSAL.md`；且 D126 階段 A 三方共識明確採「建索引不搬檔」（`HANDOFF.md:116`）。<br>**阻擋條件**：刪除將導致 `PLAN.md` 出現斷鏈（Broken Links），且可能違反 106 檔受保護清單。 | **`KEEP`** | 維持 `docs/tasks/INDEX.md` 之 `SUPERSEDED` 標記與原路徑保留，不刪除檔案。 | **高（斷鏈與保護檔風險）**<br>`- [§10 D117 / §15 D121, D126] 保留 docs/tasks/ 下 6 份已標註 SUPERSEDED 之沿革規格檔｜PLAN.md 正文與決策紀錄直接引用其路徑，且 D126 已確立建索引不刪檔原則｜blocking` |
| **F-05** | `docs/tasks/INDEX.md` 第 3 節 `CLOSED` 文件群：<br>1. `M1-adapters.md`、`M1-W2W3.md`、`M1-W5.md`、`M1-codex-audit.md`<br>2. `M1.md`、`M2*.md`、`M3*.md`、`QUOTA-*.md`、`CLAUDE-REPAIR-D119.md`、`M5-*.md` 等 | **實際證據**：`docs/tasks/INDEX.md:44` 明載 `src/adapters/*.py` 與 `src/quota.py` 的 docstring 直接引用 `M1-adapters.md` 等 4 檔路徑（**原路徑必須保留**）；而 `src/adapters/*` 具驗收指紋保護（`HANDOFF.md:167`），D118 期間嚴禁修改。此外 `PLAN.md:994–1004, 1814, 1820` 直接引用 `M1.md`、`M3-READINESS-D113.md`、`M3-METADATA-D114.md`、`M3-PARALLEL-D116.md`、`CLAUDE-REPAIR-D119.md`、`M5-PROBE-SPEC.md`。 | **`KEEP`** | 永久保留原路徑；嚴禁為清理這些文件而去修改 `src/adapters/*.py` 的 docstring。 | **極高（驗收指紋與稽核鏈風險）**<br>`- [§7.6 / §10 / §15 D121, D126] 嚴格保留 docs/tasks/ 下所有 CLOSED 里程碑文件與受 docstring 保護之 M1 規格檔｜避免觸動 adapters 驗收指紋與破壞 M1–M5 驗收證據鏈｜blocking` |
| **F-06** | `reviews/INDEX.md` 中標註 `CLOSED (SUPERSEDED)` 之 11 份早期審查報告：<br>`claude-d133-r1..r3.md`、`gemini-d133-r1..r3.md`、`claude-d130-pin-review-r1.md`、`claude-d129-code-review-r1.md`、`gemini-d129-code-review-r1.md`、`claude-m4-code-review-r1..r2.md` | **實際證據**：`reviews/INDEX.md:5` 說明早期已被最終版取代的 `r1` 過渡報告曾清理，但表中第 11～16、20、23～24、27、29 行仍保留 D127～D133 共 11 份 `CLOSED (SUPERSEDED)` 審查檔。<br>**阻擋條件**：(1) D133 屬於當前未 commit 的 dirty 變更；(2) `reviews/d133-source-manifest.json`、`d130-pin-manifest.json`、`d129-source-manifest.json`、`docs/tasks/M4-RESOLVE-SCORE.md` 及 `PLAN.md:1844` 的「106 保護檔不變」測試可能包含這些檔案。 | **`LATER`** | **前置事件**：(1) 完成 `D132`～`D135` git commit；(2) 核對各 `*-manifest.json` 與 pytest 106 保護檔清單，確認未鎖定 `r1`～`r3` 檔案雜湊後，方評估是否歸檔並同步更新 `reviews/INDEX.md`。 | **高（保護雜湊與未提交變更衝突風險）**<br>`- [§15 D127, D129, D130, D133 / §16 v1.89] 在 D132–D135 commit 及核對 106 保護檔清單前，不得刪除 reviews/ 下 11 份 SUPERSEDED 報告｜防止破壞 manifest 驗證與保護檔測試｜blocking` |
| **F-07** | `PLAN.md` §14「審查紀錄」早期歷史審查回合 | **實際證據**：`HANDOFF.md:118`（階段 B′ 第 1 項）已排定將 `PLAN.md` §14 早期審查回合移至歷史檔（保留 §15 決策編號與原位連結）。<br>**阻擋條件**：目前 `PLAN.md` 含 v1.85～v1.89（D132～D135）未提交修改，且拆分前須驗證不影響 `AGENTS.md` 與保護雜湊。 | **`LATER`** | **前置事件**：(1) `D132`～`D135` 變更已 commit；(2) 進入 `11/07 ～ 11/09` 換窗前平靜期（階段 B′）；(3) 確認拆分前後 pytest 保護雜湊 100% 通過。 | **中（合併衝突風險）**<br>`- [§14 / §15 D126 階段 B′] 依階段 B′ 計畫於 D132–D135 commit 後之平靜期再執行 PLAN.md §14 歷史拆分｜避免與當前 dirty tree 產生衝突｜non-blocking` |
| **F-08** | `src/flab/dev_runner.py`：<br>- 已停用執行器：`DevWorker` (L344)、`snapshot` (L118)、`permission_config` (L153)、`sandbox_probe` (L194)、`prepare_cli` (L238)、`export_changes` (L278)、`terminate_tree` (L316) 等<br>- **現行共用函式**：`codex_binary` (L50)、`clean_env` (L58)、`DevError` (L46) | **實際證據**：D73 舊手機獨立 Codex 開發任務模式已由 D91 取代（`docs/tasks/INDEX.md:36`），`telegram_bot.py:464` 僅啟動 `TeamWorker` 而非 `DevWorker`。<br>**對靜態結果之挑戰（阻擋直接刪除）**：現行生產模組 `src/flab/team_runner.py:18` 明確執行 `from flab.dev_runner import clean_env, codex_binary`，並於第 89、180、197 行呼叫！`HANDOFF.md:118`（階段 B′ 第 3 項）亦明載須先抽離 `clean_env, codex_binary` 並保留薄轉接 wrapper。 | **`LATER`**<br>（`clean_env`、`codex_binary` 及薄 wrapper 為 **`KEEP`**） | **前置事件**：(1) 階段 B′ 將 `clean_env`、`codex_binary` 抽至獨立共用模組，並在 `dev_runner.py` 保留 re-export wrapper；(2) 若修改 `team_runner.py` 須取得六鎖（`HANDOFF.md:118`）；(3) 核對 `tests/` 是否直接測試 `DevWorker` 沙箱邏輯。 | **極高（生產環境 `ImportError` 風險）**<br>`- [§7.1 / §15 D73, D91, D126 階段 B′] 嚴禁直接刪除 src/flab/dev_runner.py；須依階段 B′ 先抽離 clean_env 與 codex_binary 並保留相容 wrapper｜team_runner.py 直接匯入該兩函式，刪除將導致 Telegram 問答與續作崩潰｜blocking` |
| **F-09** | `src/telegram_bot.py` 與 `src/flab/dev_tasks.py`：<br>- 不可達分支：`Service.handle` L306 `elif kind.startswith("dev_")`、`Service.dev_command` (L388)、`Service.enqueue_dev_notifications` (L404)、`Service.enqueue_due` (L419)<br>- `TaskStore` 寫入方法 (`dev_tasks.py` L150–220) | **實際證據**：`telegram_bot.py:282–288` 將所有 `kind.startswith("dev_")` 強制改寫為 `"team_help"` 並交由 `handle_team()` 處理後直接 `return`，故第 293 行之 `dev_*` 檢查與第 306 行 `self.dev_command()` 在 `handle()` 中**永遠不可達**；且 `poll_loop()` (L474–499) 從未呼叫 `enqueue_dev_notifications()` 與 `enqueue_due()`。<br>**阻擋條件**：`tests/test_telegram*.py` 可能仍直接呼叫這些方法進行單元測試。 | **`LATER`** | **前置事件**：盤點並重構 `tests/` 中直接呼叫 `Service.dev_command`、`enqueue_due`、`enqueue_dev_notifications` 與 `TaskStore` 之測試案例後，再清理不可達方法。 | **中（測試耦合風險）**<br>`- [§15 D73, D91] 待核對並更新 Telegram 單元測試後，再移除 telegram_bot.py 中不可達之 dev_command / enqueue_dev_notifications / enqueue_due｜避免在未更新測試前引進新的 pytest 失敗｜non-blocking` |
| **F-10** | `src/flab/dev_tasks.py` 與 `src/telegram_bot.py` 之現行依賴與安全防呆：<br>`parse_dev_command` (`dev_tasks.py:60`)、`public_text` (L50)、`TaskError` (L38)、`SECRET` (L16) | **實際證據（挑戰靜態死碼判定）**：<br>1. `telegram_bot.py:33, 312, 338, 345, 494` 積極使用 `TaskError` 與 `public_text`（L345 為函式內局部匯入 `from flab.dev_tasks import public_text`）。<br>2. `telegram_bot.py:188` 呼叫 `parse_dev_command(text)`。若移除 `parse_dev_command`，使用者傳送舊語法 `開發：幫我改程式` 或 `補充：...` 時，`parse_command` 會回傳 `"help"`，進而在 `telegram_bot.py:284`（`elif kind == "help" ... and not incoming.startswith("/")`）**被當成對 GPT 的一般提問送出，意外消耗正式 Codex 訂閱額度**！ | **`KEEP`** | 必須保留 `public_text`、`TaskError`、`SECRET` 及 `parse_dev_command` 攔截轉導邏輯。 | **高（意外消耗模型額度與執行期崩潰風險）**<br>`- [§15 D73, D91] 保留 dev_tasks.py 之 public_text、TaskError、SECRET 與 parse_dev_command｜parse_dev_command 為防止舊「開發：」指令誤觸發 GPT 問答消耗額度的關鍵防呆，且 public_text/TaskError 為現行 team_chat 共用依賴｜blocking` |
| **F-11** | `src/telegram_bot.py` 之舊帳冊重播與排程相容邏輯：<br>`Schedule` (L155)、`State.apply` (L239)、`Service.deliver` 中之 `dev_notice:` 與 `scheduled_for` 取消邏輯 (L437–445) | **實際證據**：雖然 `Service.run()` (L463) 與 `handle()` (L303–305) 已強制將排程設為 `Schedule()` (`mode="off"`)，但 `Service.__init__()` (L262–264) 啟動時會透過 `read_ledger(self.log)` 重播 `data/telegram_events.jsonl` 歷史紀錄。舊帳冊中含有 `"schedule"` 欄位、未送出之 `dev_notice:` 與 `scheduled_for` 項目，依賴 `State.apply()` (L242) 與 `deliver()` (L437–445) 安全吸收與標記 `outcome="cancelled"`。 | **`KEEP`** | 依據 `AGENTS.md:48`「所有資料以 JSON Lines 追加寫入，不覆寫歷史紀錄」，只要歷史 `telegram_events.jsonl` 存在即須保留此相容層。 | **高（歷史帳冊重播崩潰風險）**<br>`- [AGENTS.md 開發慣例 / §15 D91] 保留 telegram_bot.py 之 Schedule 類別、State.apply 與 deliver() 舊訊息取消邏輯｜確保讀取追加寫入之歷史 telegram_events.jsonl 時不會因欄位不支援而崩潰或誤發過期訊息｜blocking` |
| **F-12** | `src/m2_campaign.py`：<br>- 共用函式：`discover_tournaments(client, *, configured=None)` (L24–44)<br>- M2 類別：`SafeSnapshots` (L47–104) | **實際證據（挑戰里程碑命名誤判）**：檔案雖名為 `m2_campaign.py`，但 `HANDOFF.md:118`（階段 B′ 第 3 項）明確指出：須「將 `src/m2_campaign.py` 的 `discover_tournaments` ... 抽到獨立共用模組（舊路徑保留薄轉接 wrapper 以維持相容）」，並註明若修改 `continuous.py` 需六鎖。這證明正式排程模組 `continuous.py` 仍依賴 `m2_campaign.discover_tournaments` 解析場地 API metadata！此外 `SafeSnapshots` 亦為 M2 安全測試所覆蓋。 | **`LATER`**<br>（抽離後原路徑薄 wrapper 與 `SafeSnapshots` 為 **`KEEP`**） | **前置事件**：於階段 B′ 抽離 `discover_tournaments` 至共用模組，在 `src/m2_campaign.py` 保留薄轉接 wrapper；若修改 `continuous.py` 匯入路徑，必須在無在途嘗試之空檔取得六鎖執行。 | **極高（正式排程 `continuous.py` 中斷風險）**<br>`- [§10 M2, M3 / §15 D126 階段 B′] 不得刪除 src/m2_campaign.py；階段 B′ 抽離 discover_tournaments 時必須在原路徑保留轉接 wrapper，且修改 continuous.py 須持六鎖｜避免破壞正式排程場地探索與既有測試｜blocking` |
| **F-13** | `src/claude_baseline_candidate.py`：<br>`approve(...)` (L24–99)、`SCOPE = 'claude.plugins'`、`VERSION = 'claude_baseline_candidate_v2'` | **實際證據（挑戰靜態 0 引用誤判）**：一般預測排程不會自動呼叫此模組（因 `auto_reaccept.py:3` 禁止自動核准基準），故靜態掃描易將其列為候選死碼。但 `PLAN.md:706`（D119）、`PLAN.md:707`（D130 每 3 天集中維護節奏，下次檢查登記於 `UTC 2026-11-07T14:25:43Z`）與 `PLAN.md:1832`（D131 於 `11/04 14:35Z` 剛完成核准）證明此為人工接受固定 SHA 候選並通過新隔離驗收後，**唯一合規只替換 `claude.plugins` 單一來源的維護工具**。 | **`KEEP`** | 現行有效之 §7.2 基準維護安全工具；未來若 Claude CLI 固定版本由 `2.1.316`（L39, L64）升級，須依 D130 節奏同步升版。 | **高（基準維護安全防線風險）**<br>`- [§7.2 D119, D130, D131] 保留 src/claude_baseline_candidate.py｜此為 D119/D131 單一來源（claude.plugins）限縮核准之核心安全工具，且 D130 每三天維護節奏仍持續運作｜blocking` |
| **F-14** | `src/auto_reaccept.py` (L1–46+) 與 `src/flab/auto_reaccept_policy.py`（於 `auto_reaccept.py:21` 以 `from flab import auto_reaccept_policy as P` 匯入） | **實際證據（AST `from-package` 盲區實證）**：<br>1. `src/auto_reaccept.py:21` 寫法為 `from flab import auto_reaccept_policy as P`，這正是本次任務提示所警告的 **AST `from-package` 匯入盲區**，極易導致 `src/flab/auto_reaccept_policy.py` 被靜態工具誤判為死碼。<br>2. `src/auto_reaccept.py` 本身由 `run.sh` 在正常週期釋放鎖後作為單一接點呼叫（上限 120 秒，`PLAN.md:708`、`HANDOFF.md:143`），且為階段 B 準備 R2-C 自動重驗之基礎。 | **`KEEP`** | 兩檔皆為 D124 已部署之線上生產模組，嚴禁刪除。 | **極高（破壞 `run.sh` 週期後接點風險）**<br>`- [§7.2 D124 / §15 D121, D124] 保留 src/auto_reaccept.py 與 src/flab/auto_reaccept_policy.py｜auto_reaccept_policy.py 係透過 from flab import ... 匯入（典型 AST 盲區），且 auto_reaccept.py 為 run.sh 現行生產接點｜blocking` |
| **F-15** | 頂層手動／排程 CLI 入口模組：<br>`src/post_close_audit.py` (D126/D135)、`src/resolve.py` (D127/D129)、`src/score.py` (D127/D129)、`src/practice.py` (D133)、`src/project_control.py` (D91) | **實際證據**：依 `AGENTS.md:54`、`HANDOFF.md:4–38, 100–107` 及 `PLAN.md:314–315, 1826–1838`，上述模組均為透過 `.venv/bin/python src/<script>.py`（或 pinned PATH）直接執行的頂層 CLI 工具。它們在 `src/` 內部不一定被其他模組 `import`，但全數為 D91～D135 現行運維、計分、閉題練習與健檢入口；其中 `practice.py` 與 `post_close_audit.py` 更包含工作樹未提交之 D133/D135 修改。 | **`KEEP`** | 全部保留；严禁因「無其他 `src/` 模組匯入」而誤判為死碼，亦不得動到 D132～D135 dirty 變更。 | **極高（毀損核心運維入口與未提交成果風險）**<br>`- [AGENTS.md / §4.3 D127–D135] 保留所有頂層 CLI 入口腳本（post_close_audit.py、resolve.py、score.py、practice.py、project_control.py）｜CLI 入口本就不被其他業務模組 import，且含 D133/D135 未提交部署成果｜blocking` |
| **F-16** | 暫駐於程式碼之設定常數與未啟用之研究候選：<br>1. `src/fail_soft.py` 與 M5 程式內常數（待遷入 `config/quota_policy.json` / `config/run_config_v2.json`）<br>2. `docs/experiments/d135-research-prompts`（4 份研究提示詞候選 v1） | **實際證據**：<br>1. `PLAN.md:1819`（D122）與 `HANDOFF.md:98, 119`（階段 C）明載：因 `config/cycle_campaign.json` 釘死 `run_config_v2.json` 與 `quota_policy.json` 雜湊，D118 窗口內不得直接改該兩份設定檔，故將新參數暫寫為有版本的程式常數，預定於 `11/10 04:00Z` 換窗（階段 C）時正式遷入設定檔。<br>2. `PLAN.md:314`（D135）明載 4 份研究提示詞候選 v1 存於 `docs/experiments/d135-research-prompts`，「未啟用，下一個獨立窗口另登記驗證」。 | **`LATER`**（常數遷移）<br>**`KEEP`**（D135 候選提示詞） | **前置事件**：須待 `UTC 2026-11-10 04:00`（台北 11/10 12:00，階段 C）D118 窗口結束、無在途嘗試時，持六鎖登記新 7 天窗口（或透過經雙審之 `budget_handoff_v2`），方能將 `fail_soft.py` 等常數遷入設定檔並驗證研究提示詞候選。 | **極高（破壞 D118 campaign 雜湊鎖定風險）**<br>`- [§4.3 D135 / §15 D118, D122, D126 階段 C] D118 窗口結束（11/10 04:00Z）前嚴禁修改 run_config_v2.json / quota_policy.json 或清理 fail_soft.py 內嵌常數，並保留 docs/experiments/d135-research-prompts｜防止破壞 cycle_campaign.json 雜湊鎖定與下窗驗證準備｜blocking` |
| **F-17** | 歷史帳冊診斷與 M3 覆蓋率／讀回相容分支：<br>1. `src/score.py` 中對非 live 無 snapshot 之 V15 探針列（D87）的 `skipped` 分支與 D52 歷史 30 筆未送出 episode 診斷<br>2. `metadata_observer_v1`、`coverage_sim_v4`/`v5`、`metadata_replay_v1` | **實際證據**：<br>1. `PLAN.md:1826`（D127）證實，`score.py` 上線冒煙測試讀取正式帳冊時，因歷史 V15 探針列存在而觸發誤報，經 r5/r6 專門補上 `skipped` 相容分支並雙審通過；`PLAN.md:301`（D128/D129）亦規定「歷史試驗資料保留獨立診斷，不預設既有 30 個未送出 episode 都可排除」。<br>2. `PLAN.md:988, 994–999` 載明 D117/D118 僅為 MiniBench 條件式有限准入，「不自動適用主賽」，主賽（M6）仍需 M3-4 / V11 覆蓋率與修訂重播工具。 | **`KEEP`** | 必須保留所有舊帳冊相容分支（V15 探針、D52 歷史 ITT 診斷）及 M3 觀測／重播模擬工具。 | **高（正式帳冊計分報錯與 M6 驗證缺口風險）**<br>`- [§4.3 D127–D129 / §10 M3, D117] 保留 score.py 之 V15 探針相容分支、D52 歷史 ITT 診斷與 M3 覆蓋率重播工具｜D127 r5/r6 實證正式帳冊含 V15 探針紀錄，且主賽仍需 M3-4 重播驗證｜blocking` |
| **F-18** | 1. `tests/test_*.py` 跨測試檔案互相 import 與 **100 個既有基線失敗測試**（`3251 passed / 100 failed`）<br>2. 來源封包截斷處（`src/auto_reaccept.py:46` 後之 `<truncated 104917 bytes>`）及其餘未附源碼之 335 檔快照項目 | **實際證據**：<br>1. `HANDOFF.md:118`（階段 B′ 第 2 項）指明 `test_*.py` 存在互相 import，預定分批抽至 `tests/helpers/`；同時 `PLAN.md:1844` 記錄完整測試為 `3251 passed / 100個既有失敗節點與摘要不變`，且受保護檔案達 **106 檔**（另有 28 筆受保護雜湊）。<br>2. 本次封包未包含該 100 個失敗測試之具體節點清單，且在 `src/auto_reaccept.py:46` 後截斷。在未取得未截斷源碼與 106 保護檔清單前，任何未見源碼之 AST 候選均無法被證明為安全可刪之死碼。 | **`LATER`**（`tests/helpers/` 抽離）<br>**`UNPROVEN`**（100 個基線失敗節點與截斷未見之 AST 候選） | **前置事件**：(1) 階段 B′ 抽離 `tests/helpers/` 前後，必須驗證 pytest 收集題數（collected count）與通過數（`3251 passed`）完全一致；(2) 對未見於封包之檔案或 100 個基線失敗測試，須先於後續批次提供未截斷之清單與 106 保護檔比對結果，不得盲目刪除。 | **極高（破壞測試收集與 106 保護檔雜湊風險）**<br>`- [§15 D126 階段 B′ / §16 v1.89] 將截斷未見之 AST 候選與 100 個基線失敗測試列為 UNPROVEN，並於階段 B′ 重構 tests/helpers/ 時嚴格核對收集題數與 106 保護檔雜湊｜缺乏未截斷源碼證據前不得盲刪任何測試或模組｜blocking` |

---

## 四、遺漏項目、跨文件不一致與潛在分歧點（Omissions & Disagreements）

在獨立審查過程中，我們識別出以下**靜態掃描容易遺漏的隱性問題**以及**三方審查者可能產生分歧的關鍵點**：

1. **跨文件狀態與日期不一致（已被靜態死碼掃描忽略之文件漂移）**：
   - **`README.md` 嚴重滯後**：`README.md` 仍停留在 `v1.78`（D125），遺漏了 `v1.79`～`v1.89`（D126～D135）的 10 個決策版本，包含已上線的 M4 `resolve_v2` / `score_v2`、D132 人類賽輔助、D133 閉題練習與 D135 健檢修正（見 **F-01**）。
   - **MiniBench 首題結算日期在索引檔未同步**：`PLAN.md`（D132）與 `HANDOFF.md:39` 已更正正式送出之 MiniBench 10 題最早結算日為**台北 11/13 20:00**（非 11/06 21:00），但 `docs/tasks/INDEX.md:16` 的 `ACTIVE` 表格仍記載「首題實戰結算待台北 11/06 21:00」（見 **F-02**）。
   - **設定檔版號命名與計畫文字之表面落差**：`PLAN.md:999`（D116）曾提及候選升為 `run_config_v3`，但現行 `HANDOFF.md:98, 166` 與 `PLAN.md:1819`（D122）在 D118 正式窗口中實際釘死並使用的設定檔路徑仍為 `config/run_config_v2.json`。若審查者僅看 `PLAN.md:999` 文字，可能誤判 `config/run_config_v2.json` 為過時檔案；實則它是現行 `cycle_campaign.json` 雜湊鎖定的核心正式設定檔！

2. **三方審查可能出現分歧之處與本報告之明確立場（Disagreements）**：
   - **分歧點一：是否應立即刪除 `src/flab/dev_runner.py` 與 `src/flab/dev_tasks.py`（因 D73 已被 D91 取代）？**
     - **可能之激進觀點**：認為 `TELEGRAM-DEV.md` 既已標註 `SUPERSEDED`，應立即刪除 `dev_runner.py` 與 `dev_tasks.py`。
     - **本報告立場（反對立即刪除，列為 `LATER` / 部分 `KEEP`）**：源碼證據確鑿顯示 `src/flab/team_runner.py:18` 直接 `from flab.dev_runner import clean_env, codex_binary`，且 `src/telegram_bot.py` 直接依賴 `dev_tasks.py` 的 `public_text`、`TaskError` 與 `parse_dev_command`。尤其 `parse_dev_command` 在 `telegram_bot.py:188, 282` 扮演「攔截舊 `開發：` 語法、防止落入第 284 行誤觸發 GPT 線上問答」的安全護欄。未完成階段 B′ 抽離與防呆遷移前，絕對不可刪除。
   - **分歧點二：是否應刪除 `reviews/INDEX.md` 中 11 份 `CLOSED (SUPERSEDED)` 的 `r1`～`r3` 審查報告？**
     - **可能之激進觀點**：依 `reviews/INDEX.md:5` 早期清理 `r1` 之慣例，主張立即刪除此 11 份檔案。
     - **本報告立場（反對立即刪除，列為 `LATER`）**：D133 與 D135 變更目前仍在工作樹處於 uncommitted dirty 狀態，且 `d129`/`d130`/`d133` 皆有 `*-manifest.json` 綁定審查與來源 SHA，加上 `PLAN.md:1844` 測試套件驗證 **106 個保護檔不變**。在未 commit 且未核對 106 檔保護清單前刪除，極可能直接破壞保護檔測試或來源 manifest。
   - **分歧點三：是否應刪除 `src/m2_campaign.py` 或 `src/claude_baseline_candidate.py`？**
     - **本報告立場（堅決反對）**：`m2_campaign.discover_tournaments` 仍被執行期排程共用（`HANDOFF.md:118`），而 `claude_baseline_candidate.py` 剛於 `11/04 14:35Z`（D131）用於恢復 Claude 線，且配合 D130 每 3 天版本維護節奏持續使用。

---

## 五、風險優先序與分階段處置建議（符合 D126 階段表，不涉及本次變更）

依風險由高至低排序，建議後續（待 Louis 另行指示時）嚴格依以下優先序與防線控管：

1. **第一優先（P0 — 絕對禁止誤刪之生產與安全防線，`KEEP`）**：
   - **受驗收指紋與雜湊鎖定之檔案**：`src/adapters/*`、`src/flab/runner.py`、`src/flab/netguard.py`、`config/run_config_v2.json`、`config/quota_policy.json`、`config/cycle_campaign.json`，以及 `PLAN.md:1844` 所列之 **106 個受保護檔案**與 `docs/tasks/M1-*.md`（受 docstring 引用保護）。
   - **頂層 CLI 與排程接點**：`src/auto_reaccept.py` 與 `src/flab/auto_reaccept_policy.py`（AST `from-package` 盲區）、`src/post_close_audit.py`、`src/resolve.py`、`src/score.py`、`src/practice.py`、`src/project_control.py`、`src/claude_baseline_candidate.py`、`src/telegram_bot.py`。
   - **歷史帳冊相容與防呆攔截**：`telegram_bot.py` 之 `Schedule` / `deliver()` 舊訊息取消邏輯、`dev_tasks.py` 之 `parse_dev_command` / `public_text` / `TaskError`，以及 `score.py` 之 V15 探針 `skipped` 分支與 D52 歷史診斷。
2. **第二優先（P1 — 立即可做之免鎖文件同步，`NOW`）**：
   - 待確認不干擾 `D132`～`D135` 未提交變更後，更新 `README.md` 狀態摘要（由 `v1.78` 同步至 `v1.89`）及更正 `docs/tasks/INDEX.md:16, 18` 之 M4 正式題首題結算日期（`11/13–11/14`）與 `HANDOFF.md` 決策範圍（至 `D135`）。
3. **第三優先（P2 — 具事件前置條件之平靜期重構，`LATER`：階段 B′ `11/07 ～ 11/09`）**：
   - **事件前提**：先完成 `D132`～`D135` 既有 dirty 變更之 git commit。
   - 抽離 `src/m2_campaign.py` 之 `discover_tournaments` 與 `src/flab/dev_runner.py` 之 `clean_env, codex_binary` 至共用模組，**舊路徑必須保留薄轉接 wrapper**；若修改 `continuous.py` 或 `team_runner.py` 則必須取得六鎖並執行前後六項 preflight。
   - 分批將跨測試共用 helper 抽至 `tests/helpers/`，重構前後嚴格核對 pytest 收集題數與 `3251 passed` 結果不變。
4. **第四優先（P3 — 週期換窗與未證實項目補證，`LATER` 階段 C `11/10 04:00Z` / `UNPROVEN`）**：
   - 於 `UTC 2026-11-10 04:00` 換窗時持六鎖將 `fail_soft.py` 等暫駐常數正式遷入新版設定檔，並登記驗證 `docs/experiments/d135-research-prompts`。
   - 針對本次因封包截斷（`src/auto_reaccept.py:46` 後）未見之檔案及 **100 個既有基線失敗測試**（`UNPROVEN`），需在獨立診斷批次檢視完整測試節點與 106 保護檔清單後，再決定處置方式。