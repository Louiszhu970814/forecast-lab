### Forecast Lab M4 第六輪（r6）設計與程式碼複核報告

**審查結論：`PASS`**  
（本次僅依據來源封包進行靜態設計與程式碼審查，未執行任何測試、指令或外部操作；本結論僅代表無阻斷問題之技術審查意見，不替任何人核准。）

---

### 一、 Blocker（阻斷問題）

- **無（0 項）**。r5 待確認事項（C1）與三項收緊修補皆已完整落實，且維持 fail-closed 性質。

---

### 二、 實際證據審查（Actual Evidence）

以下依據來源封包（Source Packet）提供之 Diff、測試程式碼與驗證紀錄逐項核對：

1. **C1 證據與診斷分類反向收緊（`src/resolve.py`）**
   - **程式碼證據**：`src/resolve.py`（第 488–489 行）將原本依賴 `"error_type" in d` 的正向篩選，改為：
     ```python
     skipped = [d for d in diagnostics if "skip_reason" in d]
     errors = [d for d in diagnostics if "skip_reason" not in d]  # anything not explicitly skipped is an error
     ```
   - **審查判斷**：結構上確保唯有明確標記 `skip_reason` 的診斷才會進入 `skipped`，其餘任何型態（包含現有的 `MissingSubmittedSnapshot` 或未來可能新增但漏標 `error_type` 的診斷）一律歸入 `errors`，符合 fail-closed 原則。

2. **Skip 條件限縮至非 `live` 模式（`src/resolve.py`）**
   - **程式碼證據**：
     - 新增 `submitted_mode_by_qid: Dict[int, Any] = {}`（第 319 行），並於遍歷 `submissions` 時與 `submitted_snap_by_qid[qid]` 同步更新：`submitted_mode_by_qid[qid] = r.get("mode") or base.get("mode")`（第 345 行），且同樣受 `if submitted_real_by_qid.get(qid, False) and not is_real: continue` 保護，不會被非正式狀態覆蓋。
     - 候選篩選處（第 366 行）收緊為 `if sub_snap_id is None and submitted_mode_by_qid.get(qid) != "live":` 才加入 `skip_reason="submitted_without_snapshot_id"`。若 `mode == "live"` 且缺少 `question_snapshot_id`，則會落入下方 `snap is None` 分支，產生 `MissingSubmittedSnapshot` 錯誤。
   - **測試證據**：`test_snapshotless_probe_submission_is_skipped_not_an_error` 已涵蓋雙路徑驗證：
     - `mode: "trial"` 且 `question_snapshot_id: None`（題號 46451）→ `status == "ok"`、`errors == []`、`skipped` 含 `submitted_without_snapshot_id`、`polled == 0`。
     - `mode: "live"` 且 `question_snapshot_id: None`（題號 101）→ `skipped == []`、`errors` 為 `[{"question_id": 101, "snapshot_id": None, "error_type": "MissingSubmittedSnapshot"}]`。

3. **`score.py` 排除條件與 `resolve.py` 對齊（`src/score.py`）**
   - **程式碼證據**：`src/score.py`（第 1078 行）由 `if res is None and not sub.get("question_snapshot_id"):` 收緊為：
     ```python
     if res is None and sub.get("question_snapshot_id") is None and sub.get("mode") != "live":
     ```
     - 空字串 `""` 不再被當作 `submitted_without_snapshot_id`。
     - `mode == "live"` 的無 snapshot 紀錄不會被歸入 `submitted_without_snapshot_id` 靜默略過。
     - 當 `res is not None`（已有結算事件）時，依然不進入此分支，維持後續 provenance 檢查以 `missing_submitted_snapshot` 拒絕（fail-closed）。
   - **測試證據**：`test_snapshotless_probe_submission_is_excluded_separately` 中探針測試資料包含 `"mode": "trial"` 與 `"question_snapshot_id": None`，驗證 `excluded_counts["submitted_without_snapshot_id"] == 1` 且 `excluded_counts["no_resolution_event"] == 0`。

4. **來源封包所附之執行與測試紀錄（由提交方提供，審查者未親自執行）**
   - 正式 `resolve.py`：`status: ok`、結束碼 `0`、`errors: []`、`skipped: 2`（45708、46451，皆為 tournament 32977 之 `trial` 探針）、0 候選、0 API 請求。
   - `score.py --format markdown`：`no_resolution_event: 14`、`submitted_without_snapshot_id: 2`。
   - 測試結果紀錄：相關四檔 `268 passed`；完整隔離測試 `2913 passed / 100 failed`，失敗節點清單與基線逐行比對一致（新增 0、消失 0）。

---

### 三、 建議與非阻斷觀察（Proposals / Non-blocking Observations）

以下為細微觀察，**不構成 Blocker，亦不影響本輪 `PASS`**：

1. **空白字串正規化行為之微小差異（僅供備查）**：
   - 在 `resolve.py` 第 344 行中，`submitted_snap_by_qid[qid] = snap_id if isinstance(snap_id, str) and snap_id.strip() else None` 會將空字串 `""` 或純空白字串正規化為 `None`；而在 `score.py` 第 1078 行則是直接檢查 `sub.get("question_snapshot_id") is None`。
   - 對於實際的 D87 V15 探針（其值為 `None`）兩者行為完全一致；且 `score.py` 對空字串採更嚴格的不豁免態度，符合安全方向，無需強制修改。
2. **選用測試補充（未來可考慮）**：
   - `resolve.py` 已新增 `mode: "live"` 缺 snapshot 的單元測試；未來若有調整 `score.py` 測試，亦可考慮在 `test_snapshotless_probe_submission_is_excluded_separately` 中順帶斷言 `mode: "live"` 且 `question_snapshot_id: None`（無結算事件時）不會被計入 `submitted_without_snapshot_id`。此項純屬錦上添花，無需於本輪重開修改。