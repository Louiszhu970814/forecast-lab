# D143：移除 CLI 版本更新停線與放寬 `agy` 例行一般設定／專案授權基準阻擋

- **決策編號**：D143（`PLAN.md` v1.97）
- **日期**：2026-11-06（UTC）
- **授權來源**：Louis 於桌面對話指示：「能不能拔掉版本更新就停線的規定這個看起來非常沒用這個agy自動更新頻率很快 還有就是我不是有每5分鐘自動恢復線路的工具呢為啥現在還是被擋住」、「OK 你跟claude 還有 gpt 討論一下 可以了就直接去做吧」
- **主持與整合者**：Gemini（Antigravity CLI）
- **官方 CLI 獨立審查**：Claude（`claude` CLI, `claude-sonnet-5-5`）PASS、GPT（`codex` CLI, `gpt-6-sol`）r1 提出型別收緊建議、r2 複審

## 1. 背景與問題根因

1. **為何 Gemini 自 `2026-11-05T15:44:17Z` 起處於 `preflight_blocked`**：
   - 互動式使用 `agy` 時，`~/.gemini/antigravity-cli/settings.json` 的一般設定鍵（`agy.cli_settings` 的 `general_hash`，如 `agentMode`、`model`、`permissions`、`trustedWorkspaces`）以及 `~/.gemini/config/projects`（`agy.project_grants`）發生變動。
   - 在舊版 [`src/baseline.py`](../../src/baseline.py) 中，`agy.project_grants` 預設分類為 `content`（變動即 `stop`），`agy.cli_settings` 的非內容鍵分類為 `general`（變動即 `reaccept`），導致 [`src/forecast.py`](../../src/forecast.py) 的 `check_baseline()` 阻擋 Gemini 前檢。
   - 每 5 分鐘執行的 [`src/line_recovery.py`](../../src/line_recovery.py)（D140 `line_recovery_v1`）僅針對 `data/line_status.jsonl` 中因 `unknown tool: " view_file" — check spelling` 觸發的 `line_stop` 進行有界恢復，不處理 `baseline.check` 的 `preflight_blocked`，因此每 300 秒檢查皆回傳 `healthy` 而未解除基準阻擋。
2. **先行解鎖與 `qid=46575` 完賽**：
   - 於 `2026-11-06T01:09:42Z` 持鎖核對 `gemini_credits == 0` 後，以 `baseline.approve(..., player="gemini")` 寫入 `base_1793927382553_1a23704a`，三線兩層前檢全數恢復 `ok=True`。
   - 隨即於 `01:10:35Z` 週期（`cycle_1793927435532_7234a9cd`）自動補跑 FutureEval 主賽開放中數值題 `qid=46575`（`post_id=46342`）的 Gemini 第 1 層（`att_1793927485238_b3e93b35`），於 `01:20:31Z` 完成三線第 1 層整合，並於 `01:21:11Z` `forecast_ok`、`01:21:18Z` `comment_ok` 送出成功（距 `02:30:00Z` 決策截止尚餘 69 分鐘）。

## 2. 永久修改範圍（D143）

1. **移除 CLI 版本更新導致的停線／前檢阻擋**：
   - [`src/baseline.py`](../../src/baseline.py) `compare()`：`approved["versions"].get(player) != current["versions"].get(player)` 的變動分類由 `GENERAL`（`reaccept`）改為 `INFORMATIONAL`（僅記錄於 `changes`，`decisions[player]` 維持 `"ok"`）。
   - [`src/baseline.py`](../../src/baseline.py) `blockers()`：明確檢查 `isinstance(ver, str) and ver.strip()`；若 CLI 無法執行或版本未知（`None`、空字串或非字串），仍列為 `CONTENT` blocker（`stop`）。
   - [`src/forecast.py`](../../src/forecast.py) `check_acceptance()`：不再因 `latest.get("cli_version") != pf.cli_version` 或 `record.get("cli_version") != pf.cli_version` 阻擋前檢或跳過正式模型驗收紀錄，改為僅要求兩端皆為非空字串（`isinstance(..., str) and .strip()`）。其餘驗收不變量（`line_pass`、`judge_version == J.JUDGE_VERSION`、`evidence_sha256`、`tier2_equivalent_to_tier1`、`code_sha256 == fingerprint`、`(model, effort)` 相符）100% 保留。
   - [`src/flab/auto_reaccept_policy.py`](../../src/flab/auto_reaccept_policy.py) `candidate()`：從 `all_changes` 擷取 `cli_version:*` 並保留 D124 觀察模式對「文字＋版本同時變動」候選的 `cli_version_observe_only` 判定，不影響既有 D124 測試與帳冊語意。
2. **將 `agy` 例行互動設定與專案授權列為 `INFORMATIONAL`**：
   - [`src/baseline.py`](../../src/baseline.py) `default_sources()`：
     - `agy.project_grants`（`~/.gemini/config/projects`）：`category=INFORMATIONAL`。
     - `agy.shared_config`（`~/.gemini/config/config.json`）：`category=INFORMATIONAL, general_category=INFORMATIONAL`。
     - `agy.cli_settings`（`~/.gemini/antigravity-cli/settings.json`）：`general_category=INFORMATIONAL`，同時保留 `content_keys=AGY_SETTINGS_CONTENT_KEYS`（`hooks`、`mcpServers`、`plugins`、`skills`、`rules`、`agents`）為 `CONTENT`（變動仍 `stop`）。
   - 注意（Claude 審查註記）：`agy.cli_settings` 的一般鍵（如互動式 `permissions`、`model`、`agentMode`）與 `agy.project_grants` 變動不再觸發停線或重驗，因為正式預測每次執行皆由 [`src/adapters/gemini.py`](../../src/adapters/gemini.py) 以 `--agent` 與 `--settings` 單次覆寫工具清單與拒絕規則，並由事件監控強制攔截越權工具；目錄無法讀取、外部 symlink 或 JSON 損毀仍由 `blockers()` 硬性停線。
3. **保護雜湊與單元測試同步**：
   - 更新 [`tests/fixtures/d121_protected_hashes.json`](../../tests/fixtures/d121_protected_hashes.json) 中 `AGENTS.md` 的 SHA-256 為 D141 已提交版本（`8453160de3b3c69f859fa1c99b75742cb6be6279e98d8109751be62510558def`），其餘 10 個執行期與設定保護檔雜湊 100% 不變。
   - 更新 [`tests/test_baseline.py`](../../tests/test_baseline.py) 與 [`tests/test_forecast.py`](../../tests/test_forecast.py)，共 333 項相關測試全數通過。
