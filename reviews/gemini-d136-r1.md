# D136 第一批文件、測試隔離與 Dead Code 清理（r1）獨立程式碼與設計審查報告

## 1. 審查範圍與封包完整性確認

- **封包結束標記確認**：已明確確認封包結尾包含 `END-D136-R1` 標記，且 `FROZEN FILES`（40 項目標：含 38 個新增／修改檔案與 2 個刪除之空白 `.gitkeep` 占位檔）、`FULL D136 DIFF`、`COLLECTION DELTA`、`RUNTIME AST PROOF`、`FULL TEST OUTPUT`、`DEPLOY SCRIPT` 與 `DEPLOY FAULT TESTS` 內容完整一致，無截斷或遺漏。
- **機密與預測隔離確認**：封包內不含任何有效憑證（API keys / tokens）或進行中之未結算預測內容。
- **獨立審查原則**：本審查直接針對 D136 實際修補程式（patch diff）、測試隔離架構與六鎖部署腳本進行完整查驗，不直接沿用先前清理盤點（cleanup audit）之候選結論。

---

## 2. 逐項變更技術審查（A01–A10 與部署腳本）

### 2.1 文件、治理邊界與索引同步（A01–A07）
1. **A01（`AGENTS.md` 對齊 D79）**：
   - 硬性規則第 2 條將原「bot testing area、Metaculus Cup 與主站題目」修正為「bot testing area 與經排除檢查的主站題目；Metaculus Cup 已依 D79 排除，不是機器人試驗場地」，與 D79 決策完全一致。
   - `AGENTS.md` 新雜湊值（`509bf93ca02844c32a3409a31e13b38db1090ff6a8daeb2213389133312a51ba`）已同步更新至 `tests/fixtures/d121_protected_hashes.json` 與 `tests/fixtures/m5_legacy_golden.json`。由於正式預測於專案外空白暫存目錄執行（`ancestor_sources` 不包含專案根目錄 `AGENTS.md`），此文字修正不影響任何預測行為或模型輸入。
2. **A02–A06（`PLAN.md`、`README.md`、任務單日期校正、導覽 Stub 與索引）**：
   - `PLAN.md` 升版至 `v1.90`，保留 D132–D135 未提交紀錄，新增 D136 決策與變更紀錄，且誠實標註雙審與六鎖套用為交付門檻，未提前宣稱已部署或已通過審查。
   - `README.md` 更新為 2026-11-05 現行營運邊界（D118 固定截止 `2026-11-10T04:00:00Z` 不自動續期、FutureEval 主賽未啟用），並清楚說明 `.venv/bin/python tests/run_offline.py -q` 與 `tests/conftest.py` 的防護性質（為受信任測試之誤用防線，而非安全沙盒）。
   - `D128-LIVE-ITT.md`、`D129-ITT-IMPLEMENTATION.md`、`M4-RESOLVE-SCORE.md` 與 `HANDOFF.md` 均加上依 D132 核對之結算日期更正（已送出 MiniBench 正式批次最早預定結算為台北 **11/13 20:00**、最晚 **11/14 12:59**；原 11/06 21:00 為開發驗收安排，且預定不代表已結算），同時完整保留歷史內文。
   - `FAIL-SOFT-RELAX-PROPOSAL.md`、`M3-MC-READBACK-D99.md`、`M3-V15-SCHEMA.md`、`TELEGRAM-DEV.md` 均於頂部加上 `SUPERSEDED／歷史文件（D136 標記）` 與現行規格連結，保留歷史沿革。
   - 新增 `docs/tasks/DECISIONS-AFTER-D84.md` 導覽 stub，修復 `392335b` 刪除後遺留之 6 處歷史連結，提供唯讀 `git show` 查閱指令與後續決策索引，並明確警告不得據歷史待決文字重啟工作或重送請求。
   - `docs/tasks/INDEX.md` 與 `reviews/INDEX.md` 補齊 D132／D134（5 份）、D135（5 份）審查報告及 D135／D136 任務單索引，並修正 `reviews/INDEX.md` 說明以避免僅憑輪次誤刪報告。
3. **A07（移除空白占位檔）**：
   - 刪除已有追蹤檔案之目錄下的空白占位檔 `launchd/.gitkeep` 與 `prompts/.gitkeep`（`FROZEN FILES` 標記為 `null`），並自 `tests/fixtures/m5_legacy_golden.json` 的 `protected` 字典移除 `prompts/.gitkeep` 條目（原雜湊 `e3b0c44...` 確為 0-byte 空檔之 SHA-256），其餘受保護程式、設定與 prompt 內容雜湊完全不變。

### 2.2 Runtime Dead Code 與 Unused Imports 清理（A08–A09）
1. **移除無引用私有 Helper（2 項）**：
   - `src/budget.py`：移除 `_positive(value: Any) -> bool`。
   - `src/forecast.py`：移除 `_fraction(v: Any) -> float | None`。
2. **移除非 Adapter 未使用 Import Bindings（10 個檔案共 11 項）**：
   - `src/auto_reaccept.py`：移除 `hashlib`、`os`（2 項）。
   - `src/comment.py`：移除 `math`（1 項）。
   - `src/continuous.py`：自 `from flab.ids import hash_obj, new_id` 移除 `new_id`（1 項）。
   - `src/flab/team_chat.py`：移除 `json`（1 項）。
   - `src/forecast.py`：自 `from typing import Any, Callable, Iterator` 移除 `Iterator`（1 項）。
   - `src/m2_supplement.py`：自 `from flab.ledger import read_complete, transaction` 移除 `transaction`（1 項）。
   - `src/quota.py`：移除 `from adapters.base import MODEL_CHECKS`（1 項）。
   - `src/reconcile.py`：自 `from flab.jsonl import append_record, read_records` 移除 `read_records`（1 項）。
   - `src/resolve.py`：自 `from typing import ...` 移除 `Mapping`（1 項）。
   - `src/scheduler.py`：移除 `from poll_questions import latest_snapshots`（1 項）。
3. **獨立查驗與保留判斷**：
   - 先前盤點列出的第 12 個候選 `metadata_observer.MetaculusError` 在完整測試回歸中確認仍被測試以模組屬性方式存取，因此實作中予以保留，證明本批次並非盲目套用盤點清單。
   - 受保護之 `src/adapters/*`（含 `Path` import）、`src/flab/runner.py`、`src/flab/netguard.py`、`config/*` 與 `prompts/*` 均未更動。
   - `RUNTIME AST PROOF` 驗證上述 11 個 runtime 檔案除移除指定之 2 個 helper 定義與 11 個 import 節點外，其餘 AST 結構與修改前完全一致。

### 2.3 測試隔離架構與 M5 題型開關修正（A10）
1. **統一 Synthetic Host 與 `tmp_path` 分離（`tests/conftest.py`、`tests/offline_workcopy.py`）**：
   - 舊版 `SyntheticHost` 僅在 `--synthetic-host` 啟用時掛載，且直接在測試案例的 `tmp_path` 下建立 `synthetic-project` 子目錄，導致依賴 `tmp_path` 為空的 7 個測試碰撞失敗；而直接執行 `pytest` 則因未掛載 `SyntheticHost` 產生環境相依失敗。
   - 新版透過 `tests/conftest.py` 的 `isolated_host`（`autouse=True`）統一為所有執行入口配置 `SyntheticHost`，並使用 `tmp_path_factory.mktemp("synthetic-host")` 將 `host_root` 與各測試案例的 `tmp_path`（`fixture_root`）徹底分離，確保 `tmp_path` 維持空白。
   - `SyntheticHost.configure` 在不修改正式 `config/` 的前提下，將預設 `submit_mode.json` 隔離為 `{"mode": "local"}`，並將預設題型設定指向 `host_root / "question-types.json"`（`numeric: True, discrete: True`）。
2. **明確控制題型 Rollout Fixture**：
   - 新增 `question_type_flags` fixture，讓原先假設 M5 尚未開啟的舊測試案例（`test_cycle.py`、`test_main_site_trial.py`、`test_poll_questions.py`）明確呼叫 `question_type_flags(False)`，同時新增參數化雙向測試（`False` / `True`）驗證 `numeric` 與 `discrete` 開關行為。
   - 原有 3,351 個測試節點全數保留（`removed: []`），新增 17 個測試節點，總計 3,368 個測試節點。
3. **離線測試入口防護與清理（`tests/run_offline.py`、`tests/test_offline_isolation.py`）**：
   - 改用 `tempfile.TemporaryDirectory(prefix="flab-offline-", dir="/tmp")` 將測試暫存區移出專案目錄外，並以 `Path(...).resolve()` 取代 `.absolute()`，有效攔截透過相對路徑（`..`）或 symlink 指向專案 `data`、`reports`、`.env`、`.agents`、`.codex` 的讀取，以及非 scratch 內之 `claude`、`codex`、`agy` CLI 呼叫。
   - 在 `pytest.main` 結束後的 `finally` 區塊將 `active[0] = False` 停用 audit hook，避免標準庫 `TemporaryDirectory` 清除含 `data` 名稱之測試暫存子目錄時，因 `dir_fd` 相對路徑 `open` 事件無法在 audit hook 中還原父目錄而誤觸 `PermissionError`。此設計與防護邊界已在程式註解、`README.md` 及 `D136-CLEANUP-BATCH1.md` 中如實揭露。

### 2.4 六鎖部署腳本與故障注入測試（`DEPLOY SCRIPT` & `DEPLOY FAULT TESTS`）
- 部署腳本嚴格驗證 `deployment-gate.json` 與 `reviewed-files-r1.json` 的 40 個目標檔案 SHA-256、測試全過旗標，以及 `claude` 與 `gemini` 兩份審查報告的雜湊與單一最終 `PASS` 結論。
- 在六鎖（`lock_all`）與閒置檢查（`require_idle(..., horizon_s=1200)`）保護下，單次讀取已審查之 stage bytes、執行部署前 3 線 × 2 層 `preflight` 與 `line_guard` 檢查、核對所有受保護檔案與目標檔案初始雜湊、建立備份、以 `atomic`（`fsync` + `os.replace`）或 `unlink` 套用變更、執行 10 個核心測試模組之離線 smoke 測試與部署後 `preflight`。
- 任何步驟失敗（`except BaseException`）皆依反向順序完整還原檔案內容、權限與新增／刪除狀態，並驗證還原雜湊與寫入 `apply_rolled_back` 紀錄。6 項故障注入測試完整涵蓋各種異常與回滾情境。

---

## 3. 阻擋問題（Blockers）、非阻擋建議（Non-Blockers / Proposals）與證據限制

### 3.1 阻擋問題（Blockers）
- **無（0 項）**。所有變更皆嚴格位於 Louis「OK開始做」授權之 A01–A10 範圍及 D126 確定性工程邊界內，未觸碰任何預測邏輯、統計參數、固定設定、D118 窗口或主賽開關。

### 3.2 非阻擋事項與後續建議（Non-Blockers / Proposals）
1. **`tests/run_offline.py` 的 `/tmp` 路徑假設（Non-blocking observation）**：
   - `tempfile.TemporaryDirectory(prefix="flab-offline-", dir="/tmp")` 固定使用 POSIX `/tmp`，在專案運作的 macOS／Linux 環境中可確保不繼承指向專案內部的 `TMPDIR`，符合本專案需求。
2. **部署後交付證據追加（Post-deployment factual update）**：
   - `docs/tasks/D136-CLEANUP-BATCH1.md` 目前如實記載回歸、雙審與六鎖套用待完成；待雙審 PASS 與六鎖部署完成後，僅需依約定追加事實性部署與驗證證據，無須再更動程式碼。

### 3.3 實際證據與證據限制（Actual Evidence & Evidence Limits）
- **現有靜態與封包證據**：
  - 40 項目標檔案之完整 Diff 與 SHA-256 清單。
  - 11 個 runtime 檔案之 AST 等價性證明（`RUNTIME AST PROOF` 全部為 `true`）。
  - 測試集合變化清單（原 3,351 項全數保留，新增 17 項，共 3,368 項）與封包附帶之測試通過輸出（`3368 passed in 147.30s`）及 6 項部署故障注入測試。
- **證據限制**：
  - 本審查僅依據封包提供之靜態資料進行查驗，審查者未執行任何外部指令、檔案讀寫或測試。
  - 靜態 AST 與儲存庫內（含私有 `data/operations` 腳本）搜尋可證明被移除的 2 個私有 helper 與 11 個 import 在專案內無任何引用，但理論上無法證明儲存庫外部不存在未納管的外部腳本引用。
  - 正式工作區之六鎖套用、前後 `preflight` 與部署後 smoke 測試尚待本輪雙審通過後實際執行。

## 結論：PASS