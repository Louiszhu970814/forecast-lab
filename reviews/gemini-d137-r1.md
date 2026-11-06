# D137 正式啟用完整 Patch 與部署腳本獨立審查報告（r1）

## 1. 審查範圍與方法聲明

- **資料來源限制**：本審查僅依據封包（Source Packet）提供之差異（diff）、完整檔案全文、節錄片段與整合者實際量測之證據紀錄進行獨立檢視。
- **未提供全文之聲明**：封包內已提供全文之檔案為 `docs/tasks/D137-MAIN-LAUNCH.md`、`src/budget_handoff.py`、` tests/test_main_launch_handoff.py`、`apply_reviewed.py`、`test_deployment.py`、`gate_and_deploy.py`、`simulate.py`、`live_window.py` 及 `require_idle` 函式；其餘檔案（如 `PLAN.md`、`README.md`、`src/continuous.py` 第 107 行以後之未變更區段、`budget.py`、`submit.py`、`tests/fixtures/*` 等）僅提供 diff 或關鍵節錄，審查者**不宣稱已讀畢未提供全文之檔案**。
- **測試與雜湊聲明**：封包內之測試結果（3393 passed、15 passed、569 passed）、SHA256 雜湊、D114 歷史稽核、官方 API GET metadata 與帳冊副本重播（`budget-replay.json`）皆為整合者於 host 實際量測之證據，審查者**未自行執行任何測試、指令或外部連線**。

---

## 2. Blocking 缺陷檢查（Blockers）

經逐項檢查本次受審之 18 個目標檔案 diff、核心預算接續邏輯、部署與故障復原腳本、受保護雜湊清單與權限範圍定義：

- **無任何工程 Blocker（0 Blockers）**。
- **無權限或範圍矛盾（0 Authorization/Scope Contradictions）**。

---

## 3. 權限、範圍與尚缺證據核對（Authorization & Scope Audit）

1. **直接開賽授權與里程碑順序調整之合規性**：
   - **實際證據**：使用者於 `2026-11-05T01:49:59.950469Z` 明確指示「複審完 修改完可以 就直接開始正式賽了」。`PLAN.md`（升版至 v1.91 之狀態欄、§3.2、§10 M6 表、§15 D137、§16）、`README.md`、`docs/tasks/D137-MAIN-LAUNCH.md`、`docs/tasks/HANDOFF.md` 與 `docs/tasks/INDEX.md` 均一致將此記錄為**取代原 M6 等待 M4 分數後才加入主賽的時間安排**，核准 `bot 311763` 於雙審與前檢通過後有限加入當季主賽 `33121`（`fall-futureeval-2026`）並保留 `minibench`。
   - **誠實揭露尚缺證據（無冒充通過）**：文件與 `d114-historical-audit.txt` 明確記載 D114 原觀測（以獨立副本 git `b926380a` 原版 `poll_questions.py` 驗證指紋，未跳過驗證或重造資料）僅完成 `24/168` 輪（截至 `2026-11-03T03:07:28.247Z`），包含 `incomplete_poll`、`observation_not_completed`、`partial_poll_snapshots`、`question_uncertainties`，且 `ready_for_replay=false`、`m3_passed=false`；同時明載 M4 官方分數與真實結算驗收尚未完成（首批正式已送出題最早預定結算為台北 11/13 20:00），不宣稱 M3/M4 完整通過，亦不作準確度或完整 ITT 結論。有限啟用依據明確界定為 D113–D115 試驗端到端、D116 已披露限制之歷史全樣本／壓力重播、M5 數值／離散題驗收、現有 live MiniBench 與本次工程雙審。
   - **授權與實際啟動狀態分離**：文件清楚區分「已獲授權／準備中」與「實際六鎖部署啟動／送出預測」，未將授權直接推定為已開跑。

2. **官方場地與開放題隔離邊界**：
   - **實際證據**：`official-metadata.json` 顯示僅使用 `GET` 驗證 `bot 311763`（`is_bot=true`）與主賽 `33121`（`fall-futureeval-2026`、`is_ongoing=true`），無任何 `POST` 且無開放題預測內容進入封包或模型。
   - **白名單與場地限制**：`config/tournaments.json` 僅將 `main.submit_whitelisted` 改為 `true`；搭配 `submit.py` 逐 POST 檢查，`live` 模式下僅允許 `main`（ID 33121）與確認進行中之 `minibench` 輪次，`bot_testing_area`、`metaculus_cup`、`main_site_questions` 均排除於 live 白名單外。

---

## 4. 工程實作與部署契約逐項核對（Actual Evidence）

### 4.1 有界第二次預算接續（`src/budget_handoff.py` & `src/continuous.py`）
- **雙跳上限與防循環／防分叉**：
  - `budget_handoff.py` 將 `VERSION` 升為 `"budget_handoff_v2"`，保留 `LEGACY_VERSION = "budget_handoff_v1"` 與 `MAX_LINKS = 2`。
  - `ancestors(policy, rows, *, seen=())` 嚴格限制最多兩條來源連結（目前為 `D105 -> D118 -> D137`）。若 `len(seen) >= MAX_LINKS` 立即拋出 `ValueError("handoff exceeds two links")`；若 `campaign_id` 重複或循環則拒絕。
  - 當 `old`（此處為 D118）含有 `budget_handoff`（v1）時，遞迴呼叫 `ancestors(old, rows, seen=(*seen, identity))` 並進入 `_legacy_source(old, rows)`，完整驗證 `D118 -> D105` 之不可變雜湊與根起點，確保連同根祖先 `D105` 若遭竄改（無論是否重算 row sha256）皆會因 `D118.budget_handoff.source_sha256` 比對失敗而被攔截（已由 `test_root_ancestor_hash_cannot_be_rewritten[False/True]` 驗證）。
- **根計數起點、窗口終點與固定參數鎖定**：
  - 強制 `setting["accounting_start"] == chain[-1]["budget_window_start"]`（即 `2026-11-02T13:03:00.000Z`），且 `accounting_start()` 回傳 `parse_iso(chain[-1]["budget_window_start"])`。
  - 強制 `parse_iso(old["budget_window_start"]) < start < parse_iso(old["budget_window_end"])` 且 `start < end <= parse_iso(old["budget_window_end"])`，確保 v2 接續不可超出或延後來源窗口終點（固定為 `2026-11-10T04:00:00.000Z`）。
  - 強制 `weekly_attempt_caps`（130/200/130）、`run_config_sha256`、`quota_policy_sha256`、`expected_new_questions`（10）、`repair_reserve`（2）、`urgent_slack_s`（120）與來源完全一致，且 `venues` 必須為 `{"main", "minibench"}` 之非空子集。
  - 在 `accounting_start(root, runtime)` 中，針對 `continuous_v10` 強制比對 `hash_obj(registered(runtime["campaign_id"], rows)) == hash_obj(policy)`，杜絕未登記之 v10 policy 或執行期拔除 `budget_handoff`／竄改 `venues` 的旁路行為。
- **重疊豁免最小化（`src/continuous.py`）**：
  - `register()` 透過 `allowed_overlap = H.overlap_ids(policy, rows)` 僅豁免經驗證之直系祖先（D105、D118）窗口重疊；任何無關之重疊窗口仍會觸發 `ValueError("continuous budget windows must not overlap")`。
  - 向下相容保留 `continuous_v2` 至 `continuous_v9` 及 v1 handoff 行為。

### 4.2 設定檔、受保護雜湊與回歸測試對齊
- **設定檔一致性**：
  - `config/cycle_campaign.json`（SHA256 `572cb04c68d77ec69641bd73ec99ac25c843fa7a33416e7852f76523175bd9ca`）與 `config/submit_mode.json` 綁定相同之 `campaign_id`（`"d137_main_minibench_20261105"`）、起訖時間（`2026-11-05T01:49:59.950Z` 至 `2026-11-10T04:00:00.000Z`）與 `campaign_sha256`（`405f0bc0...`，為 `hash_obj` 規範化雜湊）。
  - `tests/fixtures/d121_protected_hashes.json` 與 `tests/fixtures/m5_legacy_golden.json` 僅更新 `config/cycle_campaign.json` 之檔案 SHA256，其餘 adapter、runner、netguard、prompt、`quota_policy.json`、`run_config_v2.json`、`AGENTS.md` 雜湊與 golden 資料列完全未動。
- **測試覆蓋與參數保留**：
  - `tests/test_m3_integration.py` 明確參數化 `["continuous_v2", "continuous_v9", Q.VERSION]`，保留既有 `continuous_v9` 測試案例並新增 `continuous_v10`。
  - `collection-delta.json` 證實原 3368 個測試案例完整保留（`removed: []`），新增 25 個案例（含 24 個 `test_main_launch_handoff.py` 案例與 1 個 `continuous_v10` 整合案例），總計 `3393 passed`（`full-tests-r3.txt`）。
- **真實帳冊 Metadata 副本重播**：
  - `simulate.py` 與 `budget-replay.json` 證實在不讀取任何預測機率／推理文字的前提下，三線模型（Claude 已用 26/130、Gemini 已用 25/200、GPT 已用 24/130）於 tier 1 與 tier 2 在 D118（`before`）與 D137 接續後（`after`）之 `allowed`、`weekly_attempts_used`、`weekly_attempt_cap`、`protected_attempts`（0 / 12）與 `accounting_start`（`2026-11-02T13:03:00.000Z`）完全一致，且 `real_model_predictions: 0`、`POST: 0`。

### 4.3 六鎖部署與不可回滾帳冊契約（`apply_reviewed.py`、`gate_and_deploy.py`、`test_deployment.py`）
- **審核閘門綁定（`gate_and_deploy.py`）**：強制核對雙審 `## 結論：PASS`、`END-D137-R1`、審查事件紀錄 `reviews.jsonl`、`review-packet-r1.txt` 內嵌之 `packet-inputs-r1.json` 所有受審檔案與證據 SHA256，防止審後抽換。
- **部署順序與 Commit Point 安全復原（`apply_reviewed.py`）**：
  1. 持有全部六把鎖（`lock_all`），檢查 `gate_check()`、`stop_check()`（不刪除既有 `STOP_SUBMIT`）與 `idle()`（`horizon_s=1200`）。
  2. 先將 `config/submit_mode.json` 原子寫入為 `{"mode": "local"}` 停止送出。
  3. 登記前執行三線兩層 `preflight-before`、寫入向後相容程式碼、跑 `smoke('pre')`、寫入候選 `cycle_campaign.json` 與 `tournaments.json`、驗證記憶體中候選 `live` 合約（`contract()`）。
  4. **Commit Point**：呼叫 `self.register()` 將新 campaign 附加至 `data/continuous_campaigns.jsonl`，並以 `ledger_status()` 驗證原帳冊 prefix 完整且僅附加唯一預期候選紀錄。
  5. **故障處置**：若於登記前失敗，完整還原備份檔案（`restore_before_registration`）；若於登記後失敗（或帳冊狀態不明），強制維持 `mode="local"`，**絕不回滾或截斷 `continuous_campaigns.jsonl` 帳冊**，並保留新程式碼供同 gate 條件下安全 roll-forward 重試。
  6. 通過 `smoke('post')` 與 `preflight-after` 及最終雜湊核對後，最後一步才將 `config/submit_mode.json` 切換為 `live`。
  7. 上述故障注入情境已由 `test_deployment.py`（`15 passed`）於獨立暫存目錄驗證，且文件已明確區分 fake 故障測試與真實產品驗證。

---

## 5. 非阻斷性建議／觀察（Proposals / Non-blocking Observations）

- **無須修改本回合受審檔案之觀察**：
  - `src/budget_handoff.py` 的 `MAX_LINKS = 2` 目前正好滿足 `D105 -> D118 -> D137` 兩跳鏈結。在 `2026-11-10T04:00:00.000Z` 窗口結束前，此設計刻意禁止第三跳（`test_third_link_cannot_extend_the_same_budget_again`），符合本回合「有界第二跳、不續新窗口」之安全要求。
  - 部署完成並釋放六鎖後，請依 `D137-MAIN-LAUNCH.md` 契約將實際啟動時間、排程 kickstart 狀態與首輪安全 metadata 證據追加記錄於私人運維目錄與任務單，並持續維持開放題不預覽原則。

## 結論：PASS

END-D137-R1