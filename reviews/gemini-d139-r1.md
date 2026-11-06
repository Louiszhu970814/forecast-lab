## 結論：REQUEST_CHANGES

### 一、END 標記與來源封包完整性確認（實際證據）

- **END 標記確認結果**：**未見最終 `END` 標記**。本次提供的來源封包於 `=== Additional after context src/cycle.py:141-310 ===` 處被截斷，結尾顯示 `<truncated 84747 bytes>`。
- **實際可見證據範圍**：
  - 已完整檢視之 Unified Diff：`PLAN.md`、`config/cycle_campaign.json`、`config/human_tournaments.json`、`config/submit_mode.json`、`config/tournaments.json`、`docs/tasks/D139-EXPANSION.md`、`docs/tasks/HANDOFF.md`、`docs/tasks/INDEX.md`、`docs/tasks/NEXT-STEPS-2026-11-05.md`、`docs/tournament_checks/market-pulse-26q4.md`、`launchd/README.md`、`launchd/org.forecast-lab.cycle.plist`、`launchd/run.sh`、`src/advise.py`、`src/budget.py`、`src/budget_handoff.py`、`src/comment.py`、`src/continuous.py`、`src/cycle.py`、`src/discovery.py`、`src/flab/advisory_usage.py`、`src/human_advice.py`、`src/market_policy.py`、`src/poll_questions.py`、`src/post_close_audit.py`、`src/question_mapping.py`、`src/resolve.py`、`src/scheduler.py`、`src/submit.py`、`src/submit_transport.py`、`tests/fixtures/d121_protected_hashes.json`、`tests/fixtures/m5_legacy_golden.json`、`tests/test_config.py`、`tests/test_cycle.py`、`tests/test_discovery_cadence.py`、`tests/test_group_closure.py`、`tests/test_human_advice.py`、`tests/test_m3_integration.py`、`tests/test_m3_w8.py`、`tests/test_main_launch_handoff.py`、`tests/test_market_expansion.py`，以及 `src/scheduler.py`、`src/continuous.py`、`src/submit.py`、`src/submit_transport.py`、`src/advise.py`、`src/budget.py`、`src/cycle.py`（至 170 行附近）之附加脈絡。
  - **因截斷而缺漏之證據**：部署腳本本體（含六鎖、idle/orphan/unfinished-submit 檢查、launchctl 切換與 recovery/mode/source-hash 邊界實作）、3436 項測試與 28 項部署故障測試之附錄 log、實際 metadata 預算重播輸出，以及最終 `END` 標記均位於被截斷的 84,747 bytes 中。本審查未執行任何測試或外部指令，亦不捏造未見之證據或核准。

---

### 二、具體阻斷問題（Blockers）

以下 7 項為必須修正之具體 Blocker（含檔案／函式、觸發條件與修正方式）：

#### Blocker 1：`discovery.py` 計算 `campaign_sha256` 未排除動態注入的 `"questions"` 欄位，導致 cycle 執行期間每次 queue 刷新都誤判為全新 campaign 並觸發全量 `_poll_all()`
- **檔案／函式**：
  - `src/discovery.py`：`latest(root, policy)`、`record(root, policy, records, *, now, prior=None)`
  - `src/continuous.py`：`Poller.__init__`、`Poller.__call__`、`Queue.__call__`（第 491 行）
- **觸發條件**：
  1. `cycle.py` 載入 `campaign = json.loads(args.campaign.read_text())` 後，將同一個 `campaign` dict 傳給 `Poller` 與 `Queue`。
  2. Cycle 初次呼叫 `queue()` 時，`self.poller()` 先執行，此時 `self.policy` 不含 `"questions"`，`D.record()` 以無 `"questions"` 的 `hash_obj(self.policy)` 寫入 `data/discovery.jsonl`。
  3. 緊接著 `Queue.__call__()` 第 491 行原地修改同一份 dict：`self.campaign["questions"] = [...]`。
  4. 進入 `Scheduler.run()` 後，每當單一題目研究超過 30 秒觸發 `recheck()`（`src/scheduler.py:373`）或任一 job 完成觸發 `self.provider(force=True)`（`src/scheduler.py:570`），`Queue` 會再次呼叫 `self.poller()`。
  5. 此時 `self.poller.policy` 已帶有 `"questions"`，`D.latest(self.root, self.policy)` 計算出的 `hash_obj(policy)` 與 `discovery.jsonl` 中無 `"questions"` 的雜湊不符，回傳 `None`；`D.due(None, ...)` 回傳 `True`，導致每個 job 執行中與完成後都重新打 API 進行全量 `_poll_all()`，並將帶有 `"questions"` 的錯誤 `campaign_sha256` 寫入 `discovery.jsonl`（而下一輪 `cycle.py --heartbeat` 讀取乾淨 `config/cycle_campaign.json` 時又看不到這些紀錄，包含短窗縮短為 300 秒的狀態也會失效）。
- **修正方式**：
  - 比照 `src/budget_handoff.py:119`（`policy = {k: v for k, v in runtime.items() if k != "questions"}`），在 `src/discovery.py` 以 `_policy_hash(policy) = hash_obj({k: v for k, v in policy.items() if k != "questions"})` 計算 `campaign_sha256`，並在 `src/continuous.py` 的 `Poller.__init__` 將 `self.policy` 存為排除 `"questions"` 的複本。

---

#### Blocker 2：`discovery.needs_cycle` 未排除 `budget_wait`、`quota_*_wait`、`abandoned`、`comment_failed` 與不支援題型，導致 Market Pulse 進入保留額度等待後每 60 秒啟動完整 worker 與網路輪詢
- **檔案／函式**：
  - `src/discovery.py`：`needs_cycle(root, policy, now)`（第 77–95 行）
- **觸發條件**：
  1. `needs_cycle` 目前僅排除 `submissions.jsonl` 中 `status == 'comment_ok'`（`terminal`）以及 `aggregates.jsonl` 中 `completion_status == 'complete_local' and status != 'ready'`（`sealed`）的題目。
  2. Market Pulse 26Q4 含 12 個題組、69 個子題（多數截止時間在 12 月）。當任一選手消耗至 `low_priority_protected`（至少保留 22 次給主賽／MiniBench）時，其餘未作答的 Market Pulse 開放子題在 `scheduler_jobs.jsonl` 會進入 `outcome == "budget_wait"`（`weekly_attempt_capacity_reserved_or_exhausted`），且在決策截止前不會寫入 `aggregates.jsonl`。
  3. 同理，若題目在 `aggregates.jsonl` 為 `status == 'ready'` 但於 `submissions.jsonl` 被標記為 `status == 'abandoned'`、或在 `comments.jsonl` 為 `comment_failed`、或因 `unsupported_type`／`unsupported_scaling` 被 `Scheduler.jobs()` 與 `Scheduler.finalize()` 略過，該題既不在 `terminal` 也不在 `sealed`。
  4. 結果 `needs_cycle` 在每次 60 秒 launchd heartbeat 都會回傳 `True`（無法以 exit code 3 提早結束），每 60 秒啟動完整 cycle worker、對所有未凍結 post 發出 `get_post` 網路請求、執行三線 CLI `preflight` 與 `Scheduler.run()`，違反 D139「launchd 每 60 秒執行便宜的本地檢查；沒有搜尋到期或已知未完成工作時不開 worker／模型／維護程序」之固定契約。
- **修正方式**：
  - 在 `discovery.needs_cycle` 中，同步檢查 `questions.jsonl`、`question_queue.jsonl`、`scheduler_jobs.jsonl`、`line_status.jsonl`、`comments.jsonl` 與 `submissions.jsonl`：
    - 排除不支援題型／不支援 scaling／已過決策截止且已封存之題目；
    - 排除最新提交意圖已 `abandoned`（且無更新 snapshot 重排）或留言已 `comment_failed` 之題目；
    - 若某開放題尚未達 `decision_deadline`，且所有啟用中選手對該題皆已完成（terminal）或處於未到重試時間點的 `budget_wait`／`quota_5h_wait`／`quota_weekly_wait`／`line_stopped`，且無待發布／待送出／待對帳工作，則在對應喚醒時點前視為無需啟動 worker。

---

#### Blocker 3：`poll_questions.poll` 將 `skipped` 題目一併寫入 `rec["timing"]`，導致 `discovery.record` 將已排除／重複題存入 `state["known"]`，並在 `Poller._refresh_known` 繞過排除檢查直接入列
- **檔案／函式**：
  - `src/poll_questions.py`：`poll`（第 333–344 行）
  - `src/discovery.py`：`record`（第 46–50 行）
  - `src/continuous.py`：`Poller._refresh_known`（第 218–256 行）
- **觸發條件**：
  1. 在 `src/poll_questions.py:334`，`timing.append(...)` 在 `if reason: skipped.append(...); continue`（第 338–343 行）**之前**執行。
  2. `discovery.record()` 將 `rec["timing"]` 內所有項目（包含因 `is_synced_copy` 或 `already polled through another allowed venue` 而被 `skipped` 的題目）全部寫入 `state["known"]`。
  3. 60 秒後的 `_refresh_known(prior)` 遍歷 `state["known"]` 時，未檢查 `exclude`、未檢查跨賽場重複（`seen_question_ids`），也未過濾 `_poll_all` 已跳過的題目：
     - 原先在 `_poll_all` 被跳過的重複題或排除題，會在 `_refresh_known` 被 `record_snapshots` 寫入 `questions.jsonl` 並加入 `selected`；
     - 若同一題跨兩個 venue，`_refresh_known` 會把 `snap["tournament_id"]` 覆寫為第二個 venue，觸發 `poll_questions.py:232` 的 `mapping_changed` 結構性修訂，30 分鐘後 `_poll_all` 又改回第一個 venue，造成反覆的假結構修訂（structural revision）。
  4. 此外，`_refresh_known` 第 235 行允許 `entry.get('status') in ('open', 'upcoming')`，導致所有尚未到 `open_time` 的 `upcoming` 題目在每次 60 秒刷新及每個 job 完成後的 queue 刷新都會被無謂 `get_post`（若 `upcoming` 已到 `open_time`，`D.due()` 本就會回傳 `True` 走 `_poll_all()`）。
- **修正方式**：
  - 在 `discovery.record()` 建立 `known` 時排除 `rec["skipped"]` 中的 `question_id`（或在 `poll()` 中將非 `metadata_only` 的被跳過題目與有效題分離紀錄）。
  - 在 `Poller._refresh_known()` 中：僅刷新 `entry.get("status") == "open"` 的已知有效題目（未到期之 `upcoming` 由 `D.due()` 在開題時點觸發 `_poll_all()`），並保持 `if qid in selected: continue` 去重保護。

---

#### Blocker 4：`human_advice.py`、`continuous.py` 與 `submit_transport.py` 的 `metadata_only=True` 排除輪詢未傳入 `allow_groups=True`，導致 Market Pulse 題組在人類賽避讓與重疊排除中完全漏檢
- **檔案／函式**：
  - `src/human_advice.py`：`exclusion_index`（第 64 行）、`inventory`（第 111 行）
  - `src/continuous.py`：`Poller._poll_all`（第 282 行）
  - `src/submit_transport.py`：`tournament_index`（第 282 行）
  - `src/poll_questions.py`：`poll`（第 304、329 行）、`snapshots_from_post`（第 160–165 行）
- **觸發條件**：
  1. Market Pulse 26Q4（`33131`）的題目皆為 `group_of_questions` 結構。
  2. `human_advice.exclusion_index()` 雖將 `33131` 加入 `ids`，但第 64 行呼叫 `poll(client, pid, metadata_only=True, poll_log=root/'exclusion_polls.jsonl')` 時未傳入 `allow_groups=True`。
  3. 在 `poll()` 內部，`snapshots_from_post(post, venue, allow_groups=False)` 對所有 `group_of_questions` 皆回傳 `[]` 並以 `"not a single-question post"` 跳過，導致 Market Pulse 的 `rec["timing"]` 永遠為空陣列 `[]`，`TournamentIndex` 也不含任何 Market Pulse 子題 ID 或標題。
  4. 因此，`human_advice.bot_priority()` 永遠看不到 Market Pulse 的開放或即將開放子題，完全不會向 Market Pulse 讓路，違反 `PLAN.md §8` 與 `D139-EXPANSION.md` 第 5 點（「主賽／MiniBench／Market Pulse open 或 imminent 避讓」）；同時 `is_synced_copy` 也無法攔截與 Market Pulse 子題重疊的題目。
  5. 另外，當 `allow_groups=True` 或 `metadata_only=True`（`forecast_type=None`）時，若遠端回傳異常結構的 group post 或某子題使 `snapshot_from_post` 回傳 `None`，`snapshots_from_post` 會直接拋出未捕捉的 `ValueError` 或回傳含 `None` 的 list（`[None]`），導致 `poll()` 第 334 行或 `human_advice.inventory()` 第 112 行拋出 `TypeError`。
- **修正方式**：
  - 在 `poll()` 中當 `metadata_only=True`（或在 `exclusion_index`、`continuous.py:282`、`submit_transport.py:282` 對包含 `market_pulse` 的排除輪詢）啟用 `allow_groups=True`，使題組子題的 `question_id`、`title_key` 與 `timing` 納入 `TournamentIndex` 及 `records`。
  - 在 `human_advice.bot_priority()` 中，對於 Market Pulse 子題，若該題已因 `budget_wait`（如保留 22 次額度而無剩餘低優先容量）暫停，應與真正可執行的開放工作區分，避免永久阻斷人類賽研究。
  - 在 `snapshots_from_post()` 中捕捉 `ValueError` 回傳 `[]`，並確保若任一子題 `snapshot_from_post` 為 `None` 時不回傳含 `None` 的元素。

---

#### Blocker 5：`human_advice.py` 將各選手帳冊寫入 `root / p` 子目錄，導致 `advise.py --batch <batch> --recover` 無法恢復中斷的 `advisory_v2` 嘗試並永久卡死共用 Budget
- **檔案／函式**：
  - `src/human_advice.py`：`run`（第 223–226 行）、`register_admission`（第 158–159 行）
  - `src/advise.py`：`recover_accounting`（第 211–232 行）
- **觸發條件**：
  1. `human_advice.run()` 建立 `root / p`（即 `data/advisory/<new-request>/claude`、`gemini`、`gpt`），並將 `root / p` 作為 `root` 參數傳入 `A.run_line()`。
  2. 因此 `shared / "advisory_usage.jsonl"` 紀錄的 `"batch_path"` 為 `.../data/advisory/<new-request>/<player>`，且 `forecast_attempt_starts.jsonl` 與 `forecasts.jsonl` 皆位於 `<new-request>/<player>/` 子目錄下。
  3. 當 `human_advice.py research` 發生中斷／unknown 時，依 `docs/tasks/D139-EXPANSION.md` 第 26 行指示執行：
     `.venv/bin/python src/advise.py --batch data/advisory/<new-request> --recover`
     `advise.recover_accounting()` 會以 `r.get("batch_path") == str(root.resolve())` 比對（不會命中 `/<player>` 子目錄），且直接讀取頂層 `root / "forecast_attempt_starts.jsonl"` 與 `root / "forecasts.jsonl"`（檔案不存在）。
  4. 結果 `recover_accounting` 恢復 0 筆紀錄，未結案的 `advisory_admitted` 永遠無法寫入 `advisory_finished`，不僅使 `human_advice.py` 永遠報錯 `unresolved advisory attempt; recover first`，更會觸發 `src/budget.py:187`（`unresolved_or_running_attempt`），直接阻斷正式賽 bot 該選手的所有後續 `Budget.reserve()`！
- **修正方式**：
  - 修改 `src/advise.py` 的 `recover_accounting(shared, root, ...)`：同時支援頂層 `root`（相容舊 D132 批次）與 `root / player` 子目錄（D139 `advisory_v2` 批次）。針對每位 `player`，若 `(root / player).is_dir()` 則以 `line_root = root / player` 執行 `F.recover_interrupted`、讀取 `line_root / "forecast_attempt_starts.jsonl"` 與 `line_root / "forecasts.jsonl"`，並將 `line_root` 傳入 `propagate_safety` 與 `finish_accounting`；`mine` 則比對 `r.get("batch_path") in {str(root.resolve()), *(str((root / p).resolve()) for p in players)}`。

---

#### Blocker 6：`src/scheduler.py` 優先序篩選存在恆假死碼（Dead Code），導致主賽／MiniBench 第 1 層仍在執行時，閒置選手會先啟動 Market Pulse 第 1 層並餓死主賽／MiniBench 第 2 層升級
- **檔案／函式**：
  - `src/scheduler.py`：`Scheduler.jobs`（第 333–339 行）
- **觸發條件**：
  1. `src/scheduler.py:333-339` 寫為：
     ```python
     high = [j for j in (first if high_first else first + second) if j.priority != 'market_pulse']
     if high:
         candidates = high
     elif any(j.priority != 'market_pulse' for j in first):
         candidates = []
     else:
         candidates = first if unfinished_first else second
     ```
  2. 由於 `(first if high_first else first + second)` 恆包含 `first`，當 `high` 為空時，`any(j.priority != 'market_pulse' for j in first)` **永遠為 `False`**（第 336 行 `elif` 分支為永遠不會執行的死碼）。
  3. 當一題主賽或 MiniBench 題目在兩位選手（如 Gemini、GPT）已完成 Tier 1，而第三位選手（如 Claude）正在 `active` 執行 Tier 1 時：
     - 該題的 `tier1_done` 為 `False`（因此 `high_first` 為 `True`，且第 314 行尚未對該題計算 Tier 2 `candidate`）；
     - 因為 Claude 在 `active`，該題不會產生新的 Tier 1 待排 job，故 `high` 為 `[]`。
  4. 此時程式因第 336 行條件恆假而落入 `else`，將 `first` 中的 Market Pulse Tier 1 工作派給閒置的 Gemini 與 GPT。一旦數秒後 Claude 完成主賽 Tier 1 並需要升級 Tier 2，Gemini 與 GPT 已被 Market Pulse 的長耗時 CLI 呼叫佔用，違反 `PLAN.md`「主賽與 MiniBench 兩層皆優先」及第 329 行註解所述契約。
- **修正方式**：
  - 將 `src/scheduler.py:336` 的 `elif any(j.priority != 'market_pulse' for j in first):` 修正為 `elif high_first:`。

---

#### Blocker 7：來源封包截斷（缺少部署腳本、測試／重播證據與最終 `END` 標記）
- **檔案／函式**：
  - 來源封包結尾（`src/cycle.py:141-310` 之後 `<truncated 84747 bytes>`）及部署腳本。
- **觸發條件**：
  - 本次審查要求檢視部署腳本之六鎖、PID active 檢查、舊 600s timer `disable`+`bootout`、先切 `local` 再寫入設定、註冊前後 smoke/preflight、最後切 `live`、commit point 後禁止回滾帳冊、自動恢復不還原舊 campaign、60s heartbeat plist 安裝與 `kickstart`（不帶 `-k`），並確認最終 `END` 標記與測試 log。因封包被截斷 84,747 bytes，上述內容無法由封包內驗證。
- **修正方式**：
  - 修正前述程式碼 Blocker 後，請在重審封包中完整附上部署腳本、測試／重播摘要與最終 `END` 標記（避免單一脈絡區塊過大導致截斷）。

---

### 三、非阻斷改善建議（Proposals / Improvements，與 Blocker 區分）

1. **`Poller._refresh_known` 磁碟讀取效能優化（`src/continuous.py:251-253`）**：
   - 目前在 `for entry in state['known']:` 迴圈中，每一個子題都單獨呼叫一次 `record_snapshots([snap], ...)` 再呼叫一次 `latest_snapshots(self.root / 'questions.jsonl')`。當 Market Pulse 有數十個子題時，單次刷新會重複完整讀取 `questions.jsonl` 上百次。建議先收集所有待更新的 `snaps`，在迴圈外批次呼叫一次 `record_snapshots(snaps, ...)` 與一次 `latest_snapshots(...)`。
2. **`config/submit_mode.json` 工作樹預設狀態提醒**：
   - Diff 中 `config/submit_mode.json` 已直接帶有 `d139_main_minibench_market_20261105` 的 `live_window`。部署腳本實際執行時，務必確認在落盤新 `cycle_campaign.json` 與登記 `continuous_campaigns.jsonl` 之前，先把正式環境的 `submit_mode.json` 切換為 `local`，待登記與前後 preflight/smoke 全數通過後，最後一步才寫入 `live`。

---

### 四、已核實符合規格之項目（Actual Evidence）

在可見的 Diff 範圍內，以下設計與實作經核對符合 `PLAN.md` 與 D139 規範：
- **三段式有限預算接續（`budget_handoff_v3` / `continuous_v11`）**：`src/budget_handoff.py` 的 `expansion_ancestors` 嚴格限制 `D105 -> D118 -> D137 -> D139` 唯一三代祖先，保留根起點 `2026-11-02T13:03:00.000Z`、既有截止 `2026-11-10T04:00:00.000Z`、週上限 `130/200/130` 及固定配置雜湊，並拒絕第 4 跳、分叉、重置或 rollover。
- **題組映射與讀回安全（`question_mapping_v1` / `submit_v12` / `api_transport_v9` / `resolve_v3` / `post_close_audit_v3`）**：父 post 與子 question 條件合併、子題時程與數值尺度獨立、兄弟子題歷史隔離、私人留言標註 `Question <question_id>: <title>`、父題組仍開放時已結算子題可獨立結算與過關題三閘門。
- **`advisory_usage_v1` 與 `advisory_v2` 欄位隔離（`src/flab/advisory_usage.py`）**：`V2_ADMITTED_KEYS` 僅套用於 `version == 'advisory_v2'`，不放寬既有 v1 的嚴格 schema 驗證。
- **比賽規則核對與策略揭露（`docs/tournament_checks/market-pulse-26q4.md`、`PLAN.md`）**：已如實記錄資源頁「持續更新」描述與本季規則未訂最低更新頻率之差異，明確將「每子題最多一次、更新 0 次」定性為專案額度保留策略而非官方豁免。