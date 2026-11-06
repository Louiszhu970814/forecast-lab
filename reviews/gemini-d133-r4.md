> 官方 CLI 審查原文；來源封包 `data/operations/d133-closed-practice-20261104/review-packet-r4.txt`。程式雜湊以機器產生的 `reviewed-files-r4.json` 為準，非模型轉寫字串。

# D133 第四輪最終差量審查報告（Narrow Final Delta Review r4）

- **具名審查者**：Gemini（Forecast Lab 設計／程式碼審查者）

## 結論：PASS

- **具體阻擋項（Concrete Blockers）**：**無（0 項）**。Claude 於 r3 提出之兩項阻擋項（`B1` 共用正式路徑 `line_stopped` 排序解析範圍過廣、`D-B2` 部署腳本於正式根目錄執行四個測試檔之隔離風險）及相關部署閘門強化（固定標題格式解析、唯讀 `line_stopped` 預檢、回滾故障測試）均已精確修復且未引入任何新迴歸問題。
- **審查性質與邊界聲明**：
  1. 本報告為針對 D133 r4 最終候選原始碼（r3→r4 精確差量、`line_stopped` 最終實作、`tests/test_practice.py` 全文、部署程序 `apply_reviewed.py` 與其 6 項故障注入測試）之**靜態程式碼審查閘門（Code-Review Gate）**。
  2. 審查者僅依據所提供之 Source Packet 進行靜態檢視，**未執行任何測試、指令、檔案讀寫或外部網路查詢**。
  3. **區分三項實際狀態（不虛構核准或執行結果）**：
     - **程式碼審查與部署狀態**：本 `PASS` 僅為靜態審查結論，**不代表程式碼已部署至正式環境**。正式根目錄（105 個既有執行期檔案）在雙審（Claude r4 與 Gemini r4）皆為 `PASS` 且由部署腳本於六把鎖內完成雜湊驗證與預檢前，維持未套用狀態。
     - **實際作業與 API 盤點狀態**：目前新練習模型呼叫為 **`0` 次**；官方 `33121` 閉題候選共 `108` 題之內容欄位受遮罩；既有 `10` 題正式 MiniBench 閉題輸出受閘門保護與稽核。預期首次正式執行 `prepare` 為 `inventory_only`（`selected: 0`、`model_calls: 0`）。
     - **預測準確度結果**：目前**無任何新練習準確度結果**。
  4. **營運與安全邊界**：FutureEval 主賽（`33121`）維持停用；D118 既有活動視窗與配額上限不變；無任何新增外部呼叫或 POST 寫入；完整保留 D132 與並行 D134 之工作區狀態。

---

## 一、 實際證據盤點（Actual Evidence from Source Packet）

### 1. R3 與最終 R4 Canonical SHA256 核對紀錄（書面核對）
依據資料包所附之雜湊紀錄，r4 建置器已先驗證 r3 六個檔案之位元組與 r3 Canonical SHA 完全一致後才產生 diff，因此未變更之四個執行期檔案 diff 確定為空（消除了 r3 資料包切分符號造成的開頭空行假象），僅 `src/forecast.py` 與 `tests/test_practice.py` 有變更：

| 目標檔案 | R3 Canonical SHA256 | Final R4 Canonical SHA256 | r3 → r4 變更狀態 |
|---|---|---|---|
| `src/practice.py` | `6ef4f14c0251098bdcb33225c974320123494c49b571c40d5b94aa17a8cc2468` | `6ef4f14c0251098bdcb33225c974320123494c49b571c40d5b94aa17a8cc2468` | 完全不變（empty diff） |
| `src/flab/practice_contract.py` | `aece3da3f3fcb96a5de46e2690ed55a79b2323d271ad09b939657c6d1da4339f` | `aece3da3f3fcb96a5de46e2690ed55a79b2323d271ad09b939657c6d1da4339f` | 完全不變（empty diff） |
| `src/flab/external_usage.py` | `1d16f8e6cc8afbf11475bfad9bbac676541cf920402f9ce23ae929946e4af1cf` | `1d16f8e6cc8afbf11475bfad9bbac676541cf920402f9ce23ae929946e4af1cf` | 完全不變（empty diff） |
| `src/budget.py` | `10af6eafe08aefaa609a19666fa159af15d4bdb56ded5639668d53c66a2b7c7e` | `10af6eafe08aefaa609a19666fa159af15d4bdb56ded5639668d53c66a2b7c7e` | 完全不變（empty diff） |
| `src/forecast.py` | `1c7445da2a32366e36f802c7d4345fa59701d52944e7e9e2706a8672c166ffb1` | `42c34016fc2eb03d3502f68b986fd6883372f6aa2052f207e7a8e45cd5fa436b` | **Fix 1**：縮小 `line_stopped` 排序過濾範圍 |
| `tests/test_practice.py` | `9a5b874f8e08c47b7455bf9846707cbf995b088b6833166d03a02b43bd15c8ae` | `d3c0d2e6fc94fd967e22c931a2ce92759053dff5c3092a3e48fce3fd39310924` | 新增 2 個測試（共 79 個練習測試） |

### 2. 離線測試數據（由提交者於資料包提供，審查者未親自執行）
- **完整測試比對**：Baseline `3134 passed / 100 failed` vs Candidate (r4) `3213 passed / 100 failed`（較基線增加 79 個練習測試，較 r3 增加 2 個測試）。
- **失敗一致性**：`same_failure_nodes: true`、`same_failure_summaries: true`（100 個既有失敗節點與失敗摘要字串完全一致，無新增失敗）。
- **隔離環境四檔聯合測試**：於隔離 stage 執行 `python -m pytest -q tests/test_practice.py tests/test_forecast.py tests/test_advisory.py tests/test_budget.py --tb=short`，結果為 `205 passed in 1.47s`（小於 120 秒上限，0 失敗）。
- **部署故障注入測試**：`6 passed in 0.12s`（較 r3 新增 smoke 失敗回滾測試與真實 REQUEST_CHANGES 阻擋測試）。

### 3. Fix 1 靜態程式碼檢視（`src/forecast.py` 與 `tests/test_practice.py` — 對應 Claude r3 B1）
1. **問題與修正核對**：
   - 在 r3 中，`records = sorted(records, key=lambda r: parse_iso(r["finished_at_utc"]))` 會在過濾 `type == "forecast_attempt"` 與 `player == player` 之前，對 `forecasts.jsonl` 內所有紀錄解析 `finished_at_utc`。
   - 在 r4 中，已改為：
     ```python
     ordered = sorted((r for r in records if r.get("type") == "forecast_attempt"
                       and r.get("player") == player), key=lambda r: parse_iso(r["finished_at_utc"]))
     ```
     並將暴露與安全停線重播迴圈改為 `for r in ordered:`，同時保留原始 `records` 供下方 `mismatches` 計算段落按既有過濾條件（`r.get("player") == player` 及 `r.get("model_check") == "mismatch"`）使用。
2. **安全性與正確性確認**：
   - 其它選手（`player != player`）或非預測嘗試型別（`type != "forecast_attempt"`，如 `note`）即使缺少 `finished_at_utc` 或時間格式異常，都不會在 `line_stopped` 排序時被解析，使時間解析範圍嚴格恢復為 r2 原本的本線嘗試加上本線外部守衛紀錄。
   - 本線正式嘗試與外部守衛（`advisory` / `practice`）紀錄仍按 `finished_at_utc` 時間序單調遞增重播，不會破壞跨帳冊 7 天滑動視窗計算。
3. **測試覆蓋核對**：
   - 新增 `test_unrelated_metadata_and_other_player_bad_time_do_not_stop_line`：驗證 `forecasts.jsonl` 存在無時間欄位的 `type: "note"`（同選手 `claude`）以及 `finished_at_utc: "malformed"` 的其他選手（`gpt`）紀錄時，`F.line_stopped(..., 'claude', ...)` 不會拋出例外亦不會誤停線。
   - 新增 `test_mixed_recent_formal_practice_exposures_still_stop`：驗證正式帳冊（`NOW`）與練習帳冊（`NOW - 2 days`）各有一筆 7 天內的 `leak_confirmed` 時，經 `propagate_safety` 後 `F.line_stopped` 仍正確觸發停線。
   - 保留既有 `test_mixed_formal_and_practice_exposures_replay_chronologically`（相隔 30 天不誤停線）。

### 4. Fix 2 靜態程式碼檢視（部署腳本 `apply_reviewed.py` 與故障測試 — 對應 Claude r3 D-B2 與 N-e）
1. **正式根目錄 Smoke 測試範圍收斂（對應 Claude r3 D-B2 方案 a）**：
   - `apply_reviewed.py` 內的 smoke 步驟已改為只執行 `tests/test_practice.py`：
     `subprocess.run([sys.executable,'-m','pytest','tests/test_practice.py','-q','--tb=short'],cwd=PROJECT,text=True,capture_output=True,timeout=120)`。
   - 經逐行檢視資料包提供的 `tests/test_practice.py` 全文（共 34 個測試函式、展開後 79 個測試案例），所有涉及檔案讀寫的測試皆明確綁定 `tmp_path`（或建立於 `tmp_path/"data"` 之 `registered` fixture），且所有模型呼叫皆使用 `FakeAdapter` 或 mock `Client`，無任何寫入正式 `PROJECT/data` 帳冊或觸發真實 CLI 模型呼叫之行為。
   - 其餘三個相關測試檔（`test_forecast.py`、`test_advisory.py`、`test_budget.py`）已於隔離 stage 驗證通過（`205 passed in 1.47s`），兼顧測試覆蓋與正式帳冊絕對隔離。
2. **前後 Preflight 加入唯讀 `line_stopped` 檢查**：
   - `preflight(label)` 子程序在對三條線（`claude`、`gemini`、`gpt`）與兩個 tier（`1`、`2`）執行 `F.preflight(...).summary()` 時，一併以唯讀方式計算 `line_guard=F.line_stopped(F.LINE_STATUS_LOG,p,F.FORECASTS_LOG)`。
   - 檢查條件強化為 `if any(not d['ok'] or d['waivers'] or d['line_guard'] for d in data): raise RuntimeError(...)`。若正式帳冊在套用前或套用後使 `line_stopped` 拋出例外或回傳非空停線狀態，部署會立即中止並（於套用後失敗時）自動回滾。
   - `operations.jsonl` 的 `apply_complete` 紀錄亦明確記載 `preflight_note`：說明在父程序持有六把鎖期間，preflight 預期略過 acceptance 孤兒回收以避免自鎖衝突。
3. **審查結論標題嚴格解析與 r4 參照綁定（對應 Claude r3 N-e）**：
   - `verdict(answer)` 改為使用多行錨點正規表示式比對單一固定格式之結論標題行，且強制要求 `len(matches) == 1`，消除前 35 行模糊搜尋可能誤中引用句之風險。
   - 審查檔案路徑與 manifest 已更新為 `reviewed-files-r4.json` 與 `JOB/f'{model}-r4/answer.md'`，且在寫入後及 smoke／post-preflight 後皆再次驗證 6 個目標檔與 105 個受保護檔之 SHA256。
4. **新增部署故障測試核對**：
   - `test_smoke_failure_restores_original_source`：驗證當 smoke 子程序回傳非 0（`returncode=1`）時，`apply()` 拋出 `RuntimeError('deployment smoke failed')`，目標檔案完整回滾至初始雜湊，且 `operations.jsonl` 寫入 `apply_rolled_back`。
   - `test_authentic_request_changes_is_not_pass`：驗證即使審查報告內文提及前輪 PASS，只要唯一結論標題為不通過，且雜湊已寫入 `deployment-gate.json`，`apply()` 仍會在修改任何檔案前拋出 `ValueError('review gate incomplete or changed: claude')` 阻擋部署。

---

## 二、 阻擋項（Blockers）

- **無（None）**。

---

## 三、 非阻擋建議與後續部署注意事項（Proposals & Post-Review Notes）

1. **雙審通過後之鎖內部署與首次盤點**：
   - 待 Claude r4 與本報告（Gemini r4）皆確認為 `PASS`，且主持人確認 `reviewed-files-r4.json` 與本報告所列之 R4 Canonical SHA256 完全一致後，即可在無須額外人工授權之情況下執行已授權的 D133 鎖內部署腳本與後續首次 `prepare`／`report` 盤點（預期因 108 題閉題受遮罩而回傳 `inventory_only`、`selected: 0`）。
2. **既有非阻擋營運注意事項（延續 r2/r3 紀錄，不構成新開發範圍）**：
   - 如 Claude r3 第 4 節所列之非阻擋建議（例如 `score` 遇到 `run` 中途崩潰會維持 `run_in_progress` 需人工對帳、`external_usage` 未來若升版至 `v2` 須同步更新共用模組版本檢查、以及 D133 文件更新最終 r4 測試數據與證據路徑），作為營運與文件紀錄留存即可，無須再改動程式碼。