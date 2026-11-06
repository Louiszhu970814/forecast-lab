# D136 r2 最終完整修補審查報告（主賽前第一批文件與 dead code 清理）

- **封包完整性與邊界確認**：已確認看到結尾標記 `END-D136-R2`，來源封包完整。本審查僅依據封包內提供之靜態資料、完整差異（Diff）、AST 掃描器原始碼與輸出、模組載入副作用比對紀錄、部署與門檻腳本進行獨立工程審查；審查過程未使用任何外部工具、未讀寫檔案或網路，亦**未宣稱親自執行任何測試**（所有測試與掃描結果均為整合者 GPT 於隔離環境提供之實際執行證據）。封包內不含任何未決預測數值或帳號機密憑證。
- **授權與範圍核對**：符合 Louis「OK開始做」對清理盤點 A01–A10 之授權範圍（PLAN v1.90 / D136）。完整保留既有 D132–D135 未提交工作與所有 B 類延後項目；未變更 `config/`、正式預測 prompt 內容、三線 adapters、`flab/runner.py`、`flab/netguard.py`、預測策略、D118 窗口、額度上限或 FutureEval 主賽未啟用狀態。

---

## 一、阻擋項（Blockers）

- **目前阻擋項：無（0 項）。**
- **r1 阻擋項（B1）解除評估**：
  1. **11 個移除 import 之消費者完整掃描（含模組別名、屬性、字串與動態呼叫）**：
     - 封包已附上完整 `SCANNER FULL SOURCE` 與 `SCANNER RESULT`。掃描器針對基準工作區 `src/**/*.py`、`tests/**/*.py` 及私有維護目錄 `data/operations/**/*.py` 共 **251 個 Python 檔案**進行 AST 靜態解析（`parse_errors: []`）。
     - 掃描涵蓋：`from <mod> import <name>`、`from <mod> import *`、`import` 與多輪賦值別名傳遞（3-pass alias resolution）、`sys.modules[...]`、`importlib.import_module` / `__import__`、模組屬性存取（`<alias>.<attr>`）、限定字串常數（涵蓋 `monkeypatch.setattr("mod.attr", ...)` 與 `mock.patch("mod.attr")`），以及雙參數屬性呼叫（`getattr` / `setattr` / `hasattr` / `delattr` / `mock.patch.object`）。
     - **正控制驗證**：掃描器內建 6 種存取型態之正控制自我斷言通過，且對 r1 決定保留的 `metadata_observer.MetaculusError` 精確命中 1 處實際消費者（`tests/test_metadata_observer.py:138`）。
     - **目標結果**：本次移除的 11 個非 adapter import bindings（`auto_reaccept.hashlib`、`auto_reaccept.os`、`comment.math`、`continuous.new_id`、`flab.team_chat.json`、`forecast.Iterator`、`m2_supplement.transaction`、`quota.MODEL_CHECKS`、`reconcile.read_records`、`resolve.Mapping`、`scheduler.latest_snapshots`）命中數全為 **0**，且 `target_star_imports` 為空。其餘 19 處同名屬性／字串存取均已確認歸屬其他模組（`budget.transaction`、`claude_sync_state.os`、`cycle.os`、`m2_prepare.latest_snapshots`、`poll_questions.latest_snapshots`、`v15_probe.new_id`），不在本次 10 個目標模組之修改範圍內。文件亦誠實標註此為詞法／常數層級掃描之方法論界線。
  2. **`quota -> adapters.base` 與 `scheduler -> poll_questions` 之載入期副作用（Import Side Effects）驗證**：
     - 封包提供獨立子程序在移除前後（BEFORE / AFTER）載入 `quota` 與 `scheduler` 之比對證據（`IMPORT SIDE EFFECTS RESULT`），並掛載阻擋寫檔、讀取 runtime/`.env`、網路連線與 subprocess 的 audit hook；四次載入之 `blocked_side_effects` 均為 `0`，`sys_path_same` 均為 `true`。
     - **`quota.py`**：移除 `from adapters.base import MODEL_CHECKS` 後，不再於 `quota` 載入時間接載入 `adapters` 與 `adapters.base`。核對封包所附 `adapters/__init__.py`（僅 docstring）與 `adapters/base.py` 全文，後者僅匯入標準庫與 `from flab.runner import RunResult`（而 `quota.py` 本身仍直接 `from flab import runner as flab_runner`），其餘皆為常數 tuple、純 `@dataclass`、`Protocol` 與無副作用之純函式定義，無任何模組載入期註冊或狀態變更。
     - **`scheduler.py`**：移除 `from poll_questions import latest_snapshots` 後，由於 `scheduler` 匯入 `forecast`，而 `forecast.py` 仍直接匯入 `from poll_questions import latest_snapshots` 與 `adapters.base`，因此 `scheduler` 載入前後之 `project_modules` 集合完全一致（`modules_removed: []`）。
     - 綜上，r1 的 B1 阻擋事由已由具體數據與原始碼完整排除。

---

## 二、實際證據核對（Actual Evidence）

### 1. 執行期程式碼清理（A08–A09，共 11 檔）
- **刪除 2 個無引用私有 helper**：
  - `src/budget.py`：移除 `_positive(value: Any) -> bool`。
  - `src/forecast.py`：移除 `_fraction(v: Any) -> float | None`。
- **刪除 10 個模組共 11 個未使用之非 adapter import**：
  - `src/auto_reaccept.py`（`hashlib`, `os`）、`src/comment.py`（`math`）、`src/continuous.py`（`new_id`）、`src/flab/team_chat.py`（`json`）、`src/forecast.py`（`Iterator`）、`src/m2_supplement.py`（`transaction`）、`src/quota.py`（`MODEL_CHECKS`）、`src/reconcile.py`（`read_records`）、`src/resolve.py`（`Mapping`）、`src/scheduler.py`（`latest_snapshots`）。
- **AST 一致性證據**：`AST PROOF` 顯示全部 11 個受影響執行期檔案除上述清理項目外，其餘語法樹結構完全不變。

### 2. 文件、索引、連結修復與空占位檔清理（A01–A07）
- **D79 與 D132 狀態對齊**：
  - `AGENTS.md` 硬性規則第 2 條明確補入「`Metaculus Cup 已依 D79 排除，不是機器人試驗場地`」。
  - `PLAN.md` 升版至 `v1.90`，於表頭狀態、實作紀錄、§15 決策表（D136）與 §16 變更紀錄完整登載本批範圍，並將舊 11/06 結算日描述對齊 D132。
  - `docs/tasks/D128-LIVE-ITT.md`、`docs/tasks/D129-ITT-IMPLEMENTATION.md`、`docs/tasks/M4-RESOLVE-SCORE.md`、`docs/tasks/HANDOFF.md` 均加上 D136 現況校正提示（原 11/06 21:00 為開發驗收安排；已送出 MiniBench 正式批次最早預定結算為台北 11/13 20:00、最晚 11/14 12:59，且預定不等於已結算）。
- **歷史標記、導覽 Stub 與索引同步**：
  - 新增 `docs/tasks/DECISIONS-AFTER-D84.md` 歷史導覽 stub，修復 6 處失效相對連結（`all-doc-links.json` 失效連結由 6 降為 0），並明確標示不得據以重啟已結案工作。
  - `FAIL-SOFT-RELAX-PROPOSAL.md`、`M3-MC-READBACK-D99.md`、`M3-V15-SCHEMA.md`、`TELEGRAM-DEV.md` 均加上 `SUPERSEDED／歷史文件（D136 標記）` 與現行規格導引。
  - `README.md` 更新為穩定營運與離線驗證說明（並修正簡體字「与」為「與」）；`docs/tasks/INDEX.md`、`reviews/INDEX.md` 及任務單 `docs/tasks/D136-CLEANUP-BATCH1.md` 均已同步 r2 補證內容，且維持部署前之誠實待套用狀態。
- **占位檔清理**：刪除已有實際檔案之目錄下的空占位檔 `launchd/.gitkeep` 與 `prompts/.gitkeep`（於 `FROZEN FILES r2` 中標記為 `null`）。

### 3. 測試隔離架構、M5 明確旗標與黃金雜湊更新（A10）
- **單一合成 Host 與暫存隔離（`tests/conftest.py`、`tests/offline_workcopy.py`、`tests/run_offline.py`）**：
  - `conftest.py` 提供 `autouse=True` 的 `isolated_host` fixture，使用獨立的 `tmp_path_factory.mktemp("synthetic-host")` 作為 `host_root`，與個別測試案例的 `tmp_path`（`fixture_root`）分離，確保測試案例對空白 `tmp_path` 的斷言不受污染。
  - `run_offline.py` 改用 `/tmp` 下的私有 `tempfile.TemporaryDirectory(prefix="flab-offline-", dir="/tmp")`，驗證 `scratch` 不在專案目錄內；`make_guard` 對路徑執行 `.resolve()`，有效攔截 `..` 穿越與指向專案 `data` / `reports` / `.env` / `.agents` / `.codex` 的 symlink，並限制僅允許執行 `scratch` 內的假 CLI stub（`claude`, `codex`, `agy`）。`pytest.main` 結束後於 `finally` 將 `active[0] = False` 停用 hook，避免標準庫清理暫存目錄時因 `dir_fd` 相對路徑觸發誤報。舊 `--synthetic-host` 參數保留相容但不再重複掛載 plugin。
- **M5 題型開關明確化與雙向覆蓋**：
  - 新增 `question_type_flags` fixture，將測試期間的題型開關寫入暫存檔並 monkeypatch `question_types.CONFIG_PATH`，絕不修改正式 `config/question_types.json`。
  - `test_cycle.py`、`test_poll_questions.py`、`test_main_site_trial.py` 明確傳入旗標狀態，並新增參數化測試驗證 `False`/`True` 與 `numeric`/`discrete` 雙向行為。
- **黃金雜湊與保護清單對齊**：
  - `tests/fixtures/d121_protected_hashes.json` 與 `tests/fixtures/m5_legacy_golden.json` 僅更新 `AGENTS.md` 之 SHA-256（`509bf93ca02844c32a3409a31e13b38db1090ff6a8daeb2213389133312a51ba`，與 `FROZEN FILES r2` 完全一致），且 `m5_legacy_golden.json` 移除已刪除的空檔 `prompts/.gitkeep`；其餘受保護執行期／設定／prompt 雜湊與歷史 golden 測試資料列均分毫未動。
- **回歸測試證據（GPT 執行紀錄）**：
  - 原有 **3,351** 個測試節點全數保留（`removed: []`），新增 **17** 個測試節點（含 `test_offline_isolation.py` 11 項、M5 雙向參數化測試 6 項），合計 **3,368** 項測試。
  - 離線防護入口 `tests/run_offline.py -q --tb=short`：**3368 passed in 147.30s**；直接 `pytest -q --tb=short`：**3368 passed in 109.53s**（由 `TEST PROVENANCE` 確認相同程式碼與測試雜湊）。

### 4. 部署門檻、六鎖套用腳本與故障注入測試（r1 非阻擋建議落實核對）
- **部署腳本強化（`FULL DEPLOY SCRIPT` & `GATE BUILDER`）**：
  - 針對 r1 建議，已將部署腳本自身雜湊 `deployment_script_sha256` 納入 `deployment-gate.json`，並在 `apply()` 開頭完成自我雜湊校驗。
  - 審查報告（`claude-r2/answer.md`、`gemini-r2/answer.md`）改為單次 `read_bytes()` 同時驗證路徑、SHA-256 與單一 `PASS` 結論標題，消除雙重讀取之 TOCTOU 視窗。
  - 部署 smoke 測試擴充至 **20 個測試模組**（涵蓋全部 11 個修改之執行期模組與隔離測試，實測約 120.17s–120.60s），並將 `timeout` 由 120s 調升至 **240s**。
  - `apply_complete` 帳冊寫入已移入 `try:` 區塊內，確保完成紀錄寫入前或寫入時若發生任何異常皆會觸發逆序原子回滾與 `apply_rolled_back` 紀錄。
- **故障注入驗證（`FULL FAULT TESTS`）**：
  - 擴充至 **12 項故障注入測試全數通過（`12 passed in 0.29s`）**，完整涵蓋：stage 竄改中止、驗證後 stage 並發變更仍寫入已釘選位元組、post-preflight 失敗回滾、gate 後審查報告遭替換拒絕、smoke 失敗回滾、真實 `REQUEST_CHANGES` 拒絕、受保護檔並發變更中止、目標檔並發變更中止、`require_idle` 忙碌中止、寫入中途 `OSError` 回滾、回滾雜湊不符通報，以及部署腳本自身 SHA 不符拒絕。

---

## 三、非阻擋建議與後續注意事項（Proposals / Non-Blocking Notes）

1. **部署執行與部署後紀錄邊界**：本審查通過後，請依既定流程由 `GATE BUILDER` 驗證雙審 r2 `PASS` 與 `END-D136-R2` 標記，並在六鎖與 `require_idle` 下執行 `apply_reviewed.py`（含部署前後三線 Tier 1/2 preflight、20 模組 smoke 與 343 檔雜湊核對）。部署完成後，僅限於文件追加客觀之測試、雙審與部署證據，不得更動已通過審查之程式碼、測試與 `AGENTS.md`。
2. **B 類清理項目維持條件鎖定**：`CLEANUP-AUDIT-2026-11-05.md` 中列為 B 類之條件式清理項目，應繼續嚴格等待各自前置條件（如 M4 實戰結算驗證、R2-C 或後續決策）達成後再另案處理，不因時間推移自動刪除。

## 結論：PASS