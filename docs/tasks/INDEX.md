# `docs/tasks/` 任務與規格文件索引

- [正式營運後的輪詢與參賽擴張](NEXT-STEPS-2026-11-05.md)：**D139 已交付啟用**；三步已獲直接啟用授權，實際交付見 [D139](D139-EXPANSION.md)；候選賽與官方 CLI 諮詢證據。

- [2026-11-05 三方清理盤點](CLEANUP-AUDIT-2026-11-05.md)：**REVIEW ONLY**，現在可清／條件滿足後清／保留清單；該輪僅盤點；A 類實作進度見 D136，含測試環境漂移與兩家補審限制。

> 本索引供 AI 協作者快速判別各任務文件的生效狀態（`ACTIVE` / `SUPERSEDED` / `CLOSED`），避免審查或開發時誤讀已取代的過渡規格。
> 唯一真實規格來源仍為 [`PLAN.md`](../../PLAN.md)。

## 1. `ACTIVE`（現行有效規格、當次授權與待續作入口）

| 檔案 | 對應決策 | 說明 |
|---|---|---|
| [`D145-CLI-UPGRADE-CHECK.md`](D145-CLI-UPGRADE-CHECK.md) | D145 | Codex 0.167.0 升版未停線；三線版本規則清理、Claude 一般設定恢復、六項前檢通過 |
| [`D144-DEVELOPMENT-CATALOGS.md`](D144-DEVELOPMENT-CATALOGS.md) | D144 | 日常開發目錄內容變更不再停線、當日漏交／缺線盤點與部署證據 |
| [`D140-AUTO-RECOVERY.md`](D140-AUTO-RECOVERY.md) | D140 | **COMPLETE（已部署／啟用）**：Gemini自動恢復，3道待補Market題皆forecast_ok／comment_ok；正式累計25題 |
| [`D139-EXPANSION.md`](D139-EXPANSION.md) | D139 | **ACTIVE（已啟用）**：30分鐘搜尋、人類手動入口、Market Pulse；實際交付與提交狀態見任務單 |
| [`D138-DATA-CLEANUP.md`](D138-DATA-CLEANUP.md) | D138 | **ACTIVE（本批已交付）**：data 約 1.79 GB → 1.02 GB；雙審、無損封存與還原入口 |
| [`D137-MAIN-LAUNCH.md`](D137-MAIN-LAUNCH.md) | D137 | **ACTIVE（主賽已啟用）**：有限主賽、保留用量接續與實際啟動證據 |
| [`D136-CLEANUP-BATCH1.md`](D136-CLEANUP-BATCH1.md) | D136 | **ACTIVE（本批已交付）**：文件／測試隔離與小範圍 dead code 清理；交付驗證見任務單，B 類仍延後 |
| [`D135-QUALITY-REPAIR.md`](D135-QUALITY-REPAIR.md) | D135 | 已部署；研究提示詞候選未啟用 |
| [`D133-CLOSED-PRACTICE.md`](D133-CLOSED-PRACTICE.md) | D133 | **ACTIVE（手動入口生效；工程／雙審已完成，新題受權限限制）**：79專項／205相關／6部署故障測試，108遮蔽候選／0新呼叫；既有10題健檢；該輪主賽未啟用，後續已依D137啟用 |
| [`D132-CUP-ADVISORY.md`](D132-CUP-ADVISORY.md) | D132／D134 | **兩題已完成**：人類賽參考入口、共用預算與安全記帳、雙審及操作證據；已新增英文報告與 Gemini 最後留言，不自動重跑 |
| [`D130-THREE-LINE-REPAIR.md`](D130-THREE-LINE-REPAIR.md) | D130／D131 | **ACTIVE** 三線已恢復；固定CLI版本、三天更新節奏及外掛接受／驗收證據 |
| [`M4-RESOLVE-SCORE.md`](M4-RESOLVE-SCORE.md) | D126 階段 B／D127／D129 | **ACTIVE（手動入口已上線）**：r1～r6 修補與雙審紀錄、操作指令、P1～P4 已由D129完成；正式批次最早預定結算台北 11/13 20:00（D132 校正；舊 11/06 為開發安排） |
| [`D128-LIVE-ITT.md`](D128-LIVE-ITT.md) | D128 | **已事前登記**固定live母體，D129已實作診斷；完整捕捉限制保留 |
| [`D129-ITT-IMPLEMENTATION.md`](D129-ITT-IMPLEMENTATION.md) | D129 | **已上線** resolve_v2／score_v2、P1～P4與live ITT診斷，雙審／驗證／後續工作 |
| [`HANDOFF.md`](HANDOFF.md) | D118～D136 | **現行唯一交接入口**（D134 英文報告與D133工程交付已完成；含加速迭代窗口；原階段 A～D 表保留為歷史，新順序見 D136） |
| [`MINIBENCH-EARLY-D118.md`](MINIBENCH-EARLY-D118.md) | D118 | 現行 MiniBench live 窗口授權（`continuous_v9` + `budget_handoff_v1`，至 UTC 2026-11-10 04:00） |
| [`D125-CODEX-AUTOLOAD-COMPATIBILITY.md`](D125-CODEX-AUTOLOAD-COMPATIBILITY.md) | D125 | `autoload_v3` Codex tier1 載入量測相容性規格（已部署） |
| [`D124-BATCH2-OPERATIONS.md`](D124-BATCH2-OPERATIONS.md) | D124 | D124 維護模式（`autoload_maintenance_v1`）與 `auto_reaccept_v1` **observe** 模式操作紀錄 |
| [`D124-BATCH2-SPEC.md`](D124-BATCH2-SPEC.md) | D121 / D124 | R2-C（自動重驗預算／24h 限制／候選精確綁定）與 R12 實作規格（observe 已上線，enforce 待續作） |
| [`D121-BATCH2-PREPARATION.md`](D121-BATCH2-PREPARATION.md) | D121 / D124 | R2-C 第二批 canary 目標與接點盤點準備筆記 |
| [`D121-FAIL-SOFT-BATCH1.md`](D121-FAIL-SOFT-BATCH1.md) | D121 | D121 第一批 fail-soft 放寬現行規格（已上線） |
| [`M5-NUMERIC-SPEC.md`](M5-NUMERIC-SPEC.md) | D122 | M5 數值題（`numeric`）與離散題（`discrete`）CDF 轉換、整合與驗證規格（已上線） |
| [`PLATFORM-REPLY-D115.md`](PLATFORM-REPLY-D115.md) | D115 | Metaculus 官方回信確認之現行 API 契約（MC `all_options_ever`、私人留言 150000 字元與 30 天封存、429 IP 限流） |
| [`TELEGRAM-TEAM.md`](TELEGRAM-TEAM.md) | D91 | 現行 Telegram 私密工作對話與 `project_control.py` 規格 |

## 2. `SUPERSEDED`（已被後續決策或新版規格取代，僅作沿革證據保留）

| 檔案 | 原決策 | 狀態與替代關係 |
|---|---|---|
| [`MINIBENCH-LIVE-D117.md`](MINIBENCH-LIVE-D117.md) | D117 | `SUPERSEDED-BY: MINIBENCH-EARLY-D118.md (D118)` — 原 21:03Z 候選窗口已由 D118 提前啟動取代 |
| [`M3-MC-READBACK-D99.md`](M3-MC-READBACK-D99.md) | D99 | `SUPERSEDED-BY: PLATFORM-REPLY-D115.md (D115)` — D99 的 `null` 多重集合退路已被 D115 `api_transport_v7` 移除 |
| [`PLATFORM-CLUES-D98.md`](PLATFORM-CLUES-D98.md) | D98 | `SUPERSEDED-BY: PLATFORM-REPLY-D115.md (D115)` — D98「留言 30 天後只留前 200 字元」推定已被 D115 官方封存契約取代 |
| [`TELEGRAM-DEV.md`](TELEGRAM-DEV.md) | D73 | `SUPERSEDED-BY: TELEGRAM-TEAM.md (D91)` — 舊 TG 交辦獨立 Codex 開發任務模式已停用 |
| [`FAIL-SOFT-RELAX-PROPOSAL.md`](FAIL-SOFT-RELAX-PROPOSAL.md) | D121 前身 | `SUPERSEDED-BY: D121-FAIL-SOFT-BATCH1.md (D121)` — 提案階段草案，實際採納範圍以 PLAN D121 為準 |
| [`M3-V15-SCHEMA.md`](M3-V15-SCHEMA.md) | D85 | `SUPERSEDED-BY: PLATFORM-REPLY-D115.md (D115)` — 早期 V15 讀回草案，已演進至 `api_transport_v7` |

## 3. `CLOSED`（已結案之里程碑、驗收證據與被 docstring 引用的歷史規格）

| 檔案 | 類別 | 說明 |
|---|---|---|
| [`M1-adapters.md`](M1-adapters.md)、[`M1-W2W3.md`](M1-W2W3.md)、[`M1-W5.md`](M1-W5.md)、[`M1-codex-audit.md`](M1-codex-audit.md) | M1 規格（受保護引用） | `src/adapters/*.py` 與 `src/quota.py` docstring 直接引用路徑，**原路徑必須保留** |
| [`M1.md`](M1.md)、[`M1-W8.md`](M1-W8.md) | M1 結案 | `PLAN.md` 引用之 M1 分工與單線流程紀錄 |
| [`M2.md`](M2.md)、[`M2-W2.md`](M2-W2.md)、[`M2-W3.md`](M2-W3.md)、[`M2-W4-W6.md`](M2-W4-W6.md)、[`M2-D81.md`](M2-D81.md)、[`M2-M3-D92.md`](M2-M3-D92.md)、[`M2-VERIFY-D94.md`](M2-VERIFY-D94.md) | M2 結案 | M2 本地固定題集排程、稽核依賴、匿名第 2 層與補充量測紀錄 |
| [`QUOTA-VERIFICATION-D93.md`](QUOTA-VERIFICATION-D93.md)、[`QUOTA-OBSERVABILITY-D96.md`](QUOTA-OBSERVABILITY-D96.md)、[`AUDIT-ARGON-2026-11-01.md`](AUDIT-ARGON-2026-11-01.md) | 額度與稽核結案 | D93 滑動重置、D96 `/usage` 可觀測性結論（促成 D97）與 M3 前跨文件稽核 |
| [`METACULUS-RESEARCH-D94.md`](METACULUS-RESEARCH-D94.md)、[`EXPAND-TOURNAMENTS.md`](EXPAND-TOURNAMENTS.md)、[`OVERNIGHT-D84.md`](OVERNIGHT-D84.md)、[`TELEGRAM-HANDOFF.md`](TELEGRAM-HANDOFF.md) | 專項研究與交接結案 | 平台三問來源核對、場地擴充評估與早期交接紀錄 |
| [`M3.md`](M3.md)、[`M3-W9.md`](M3-W9.md)、[`M3-PROBE-CAPS-D100-D101.md`](M3-PROBE-CAPS-D100-D101.md)、[`M3-MC-PROBE-D102.md`](M3-MC-PROBE-D102.md)、[`M3-MAINSITE-TRIAL-D105.md`](M3-MAINSITE-TRIAL-D105.md)、[`M3-E2E-D105-MONITOR.md`](M3-E2E-D105-MONITOR.md)、[`D107-A2-BTA-REUSE-PROBE.md`](D107-A2-BTA-REUSE-PROBE.md)、[`D107-A3-TIER2-BREAKER-LIFT.md`](D107-A3-TIER2-BREAKER-LIFT.md)、[`LAUNCHD-INTERVAL-D108.md`](LAUNCHD-INTERVAL-D108.md)、[`M3-READBACK-D111.md`](M3-READBACK-D111.md)、[`M3-READINESS-D113.md`](M3-READINESS-D113.md)、[`M3-METADATA-D114.md`](M3-METADATA-D114.md)、[`M3-PARALLEL-D116.md`](M3-PARALLEL-D116.md)、[`MINIBENCH-READINESS.md`](MINIBENCH-READINESS.md) | M3 結案與驗收證據 | M3 試驗期各項端到端、probe、排程間隔與覆蓋率重播驗收證據 |
| [`CLAUDE-REPAIR-D119.md`](CLAUDE-REPAIR-D119.md) | D119 結案 | Claude 基準誤擋與 CLI 固定版本 wrapper 修復紀錄 |
| [`D121-BATCH1-REPORT.md`](D121-BATCH1-REPORT.md)、[`D121-M5-MERGE-REPORT.md`](D121-M5-MERGE-REPORT.md)、[`M5-IMPL-REPORT.md`](M5-IMPL-REPORT.md)、[`M5-PROBE-SPEC.md`](M5-PROBE-SPEC.md)、[`M5-PROBE-REPORT.md`](M5-PROBE-REPORT.md) | D121 / M5 交付證據 | D121 第一批與 M5 探測／合併上線驗收報告 |
