# `docs/tasks/` 任務與規格文件索引

> 本索引供 AI 協作者快速判別各任務文件的生效狀態（`ACTIVE` / `SUPERSEDED` / `CLOSED`）。已作廢的早期提案草稿已於 D150 清理；唯一真實規格來源仍為 [`PLAN.md`](../../PLAN.md)。

## 1. `ACTIVE`（現行有效營運入口、上線功能與還原說明）

| 檔案 | 對應決策 | 說明 |
|---|---|---|
| [`HANDOFF.md`](HANDOFF.md) | D137～D150 | **現行唯一交接入口**：目前營運狀態與近期重大變更摘要 |
| [`D139-EXPANSION.md`](D139-EXPANSION.md) | D139 | 30 分鐘新題搜尋（60 秒 heartbeat）、Market Pulse 26Q4 與 `human_advice.py` 人類賽手動入口 |
| [`D138-DATA-CLEANUP.md`](D138-DATA-CLEANUP.md) | D138 | `data/` 無損封存與去重交付及 `archive_data.py` 還原操作說明 |
| [`D137-MAIN-LAUNCH.md`](D137-MAIN-LAUNCH.md) | D137 | FutureEval 主賽＋MiniBench 啟用與預算接續紀錄 |
| [`D136-CLEANUP-BATCH1.md`](D136-CLEANUP-BATCH1.md) | D136 | 第一批文件整理与 `tests/run_offline.py` 離線防護邊界說明 |
| [`D135-QUALITY-REPAIR.md`](D135-QUALITY-REPAIR.md) | D135 | 閉題健檢修正與 `docs/experiments/d135-research-prompts/` 未啟用候選說明 |
| [`D133-CLOSED-PRACTICE.md`](D133-CLOSED-PRACTICE.md) | D133 | `src/practice.py` 閉題練習手動入口與安全記帳契約 |
| [`D132-CUP-ADVISORY.md`](D132-CUP-ADVISORY.md) | D132／D134 | `src/advise.py` 單題有界人類賽參考與英文報告交付 |
| [`M4-RESOLVE-SCORE.md`](M4-RESOLVE-SCORE.md) | D127／D129 | `src/resolve.py`／`src/score.py` 結算與計分手動入口操作說明 |
| [`D128-LIVE-ITT.md`](D128-LIVE-ITT.md)、[`D129-ITT-IMPLEMENTATION.md`](D129-ITT-IMPLEMENTATION.md) | D128／D129 | 事前登記 live ITT 母體與 `resolve_v2`／`score_v2` 診斷規格 |
| [`M5-NUMERIC-SPEC.md`](M5-NUMERIC-SPEC.md) | D122 | M5 數值題（`numeric`）與離散題（`discrete`）CDF 轉換、整合與驗證規格 |
| [`PLATFORM-REPLY-D115.md`](PLATFORM-REPLY-D115.md) | D115 | Metaculus 官方回信確認之現行 API 契約（MC `all_options_ever`、私人留言上限、429 限流） |
| [`TELEGRAM-TEAM.md`](TELEGRAM-TEAM.md) | D91／D148 | Telegram 私密工作對話基礎架構（現行僅作查詢與問答，見 `docs/telegram.md`） |

## 2. `SUPERSEDED`（已被 D137～D150 新機制取代，保留供歷史與測試查考）

| 檔案 | 原決策 | 狀態與替代關係 |
|---|---|---|
| [`D147-CODEX-CACHE-ALIAS.md`](D147-CODEX-CACHE-ALIAS.md)、[`D145-CLI-UPGRADE-CHECK.md`](D145-CLI-UPGRADE-CHECK.md)、[`D144-DEVELOPMENT-CATALOGS.md`](D144-DEVELOPMENT-CATALOGS.md)、[`D143-BASELINE-RELAX.md`](D143-BASELINE-RELAX.md) | D143～D147 | 基準檢查分批放寬紀錄；D149 起前檢已統一改為僅手設開關擋線、其餘降為 `warnings` |
| [`D140-AUTO-RECOVERY.md`](D140-AUTO-RECOVERY.md) | D140 | Gemini 工具拼字停線自動恢復交付紀錄；D149 拔除永久停線後已無需觸發，LaunchAgent 於 D150 退役 |
| [`D130-THREE-LINE-REPAIR.md`](D130-THREE-LINE-REPAIR.md) | D130／D131 | `launchd/bin/` CLI 固定版本 wrapper 建立紀錄（wrapper 仍在使用，停線規則已由 D149 取代） |
| [`D125-CODEX-AUTOLOAD-COMPATIBILITY.md`](D125-CODEX-AUTOLOAD-COMPATIBILITY.md)、[`D124-BATCH2-OPERATIONS.md`](D124-BATCH2-OPERATIONS.md)、[`D121-FAIL-SOFT-BATCH1.md`](D121-FAIL-SOFT-BATCH1.md) | D121～D125 | 早期 fail-soft 與 `auto_reaccept` 觀察紀錄（`auto_reaccept` 每分鐘呼叫已於 D150 退役，永久停線已由 D149 拔除） |
| [`MINIBENCH-EARLY-D118.md`](MINIBENCH-EARLY-D118.md)、[`D118-MINIBENCH-EARLY.json`](D118-MINIBENCH-EARLY.json) | D118 | 首個 MiniBench live 窗口紀錄（JSON 檔受 `test_itt_cohort.py`／`test_score_live_itt.py` 測試讀取，必須保留） |
| [`CLEANUP-AUDIT-2026-11-05.md`](CLEANUP-AUDIT-2026-11-05.md) | D136 前置 | 2026-11-05 清理盤點紀錄（後續已執行 D136、D138 與 D150） |
| [`M3-V15-SCHEMA.md`](M3-V15-SCHEMA.md) | D85 | 早期 V15 讀回草案，已演進至 D115 `api_transport_v7` |
| [`DECISIONS-AFTER-D84.md`](DECISIONS-AFTER-D84.md) | D85～D91 | 歷史連結導覽 stub（指向 `PLAN.md` §15） |

## 3. `CLOSED`（已結案之里程碑規格、驗收報告與受保護引用檔案）

| 檔案 | 類別 | 說明 |
|---|---|---|
| [`M1-adapters.md`](M1-adapters.md)、[`M1-W2W3.md`](M1-W2W3.md)、[`M1-W5.md`](M1-W5.md)、[`M1-codex-audit.md`](M1-codex-audit.md) | M1 規格（受保護引用） | `src/adapters/*.py` 與 `src/quota.py` docstring 直接引用路徑，**原路徑保留** |
| [`M1.md`](M1.md)、[`M1-W8.md`](M1-W8.md) | M1 結案 | M1 分工與單線流程紀錄 |
| [`M2.md`](M2.md)、[`M2-W2.md`](M2-W2.md)、[`M2-W3.md`](M2-W3.md)、[`M2-W4-W6.md`](M2-W4-W6.md)、[`M2-D81.md`](M2-D81.md)、[`M2-M3-D92.md`](M2-M3-D92.md)、[`M2-VERIFY-D94.md`](M2-VERIFY-D94.md) | M2 結案 | M2 本地排程、稽核依賴、匿名第 2 層與補充量測紀錄 |
| [`QUOTA-VERIFICATION-D93.md`](QUOTA-VERIFICATION-D93.md)、[`QUOTA-OBSERVABILITY-D96.md`](QUOTA-OBSERVABILITY-D96.md)、[`AUDIT-ARGON-2026-11-01.md`](AUDIT-ARGON-2026-11-01.md) | 額度與稽核結案 | D93 滑動重置、D96 `/usage` 可觀測性結論與跨文件稽核 |
| [`METACULUS-RESEARCH-D94.md`](METACULUS-RESEARCH-D94.md)、[`OVERNIGHT-D84.md`](OVERNIGHT-D84.md)、[`TELEGRAM-HANDOFF.md`](TELEGRAM-HANDOFF.md) | 專項研究與交接結案 | 平台來源核對與早期交接紀錄 |
| [`M3.md`](M3.md)、[`M3-W9.md`](M3-W9.md)、[`M3-PROBE-CAPS-D100-D101.md`](M3-PROBE-CAPS-D100-D101.md)、[`M3-MC-PROBE-D102.md`](M3-MC-PROBE-D102.md)、[`M3-MAINSITE-TRIAL-D105.md`](M3-MAINSITE-TRIAL-D105.md)、[`M3-E2E-D105-MONITOR.md`](M3-E2E-D105-MONITOR.md)、[`D107-A2-BTA-REUSE-PROBE.md`](D107-A2-BTA-REUSE-PROBE.md)、[`D107-A3-TIER2-BREAKER-LIFT.md`](D107-A3-TIER2-BREAKER-LIFT.md)、[`LAUNCHD-INTERVAL-D108.md`](LAUNCHD-INTERVAL-D108.md)、[`M3-READBACK-D111.md`](M3-READBACK-D111.md)、[`M3-READINESS-D113.md`](M3-READINESS-D113.md)、[`M3-METADATA-D114.md`](M3-METADATA-D114.md)、[`M3-PARALLEL-D116.md`](M3-PARALLEL-D116.md)、[`MINIBENCH-READINESS.md`](MINIBENCH-READINESS.md) | M3 結案與驗收證據 | M3 試驗期各項端到端、probe、排程間隔與覆蓋率重播驗收證據 |
| [`CLAUDE-REPAIR-D119.md`](CLAUDE-REPAIR-D119.md) | D119 結案 | Claude CLI 固定版本 wrapper 初次修復紀錄 |
| [`D121-BATCH1-REPORT.md`](D121-BATCH1-REPORT.md)、[`D121-M5-MERGE-REPORT.md`](D121-M5-MERGE-REPORT.md)、[`M5-IMPL-REPORT.md`](M5-IMPL-REPORT.md)、[`M5-PROBE-SPEC.md`](M5-PROBE-SPEC.md)、[`M5-PROBE-REPORT.md`](M5-PROBE-REPORT.md) | D121 / M5 交付證據 | D121 第一批與 M5 數值／離散題探測及合併上線驗收報告 |
