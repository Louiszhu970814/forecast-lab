D139：[交付](../docs/tasks/D139-EXPANSION.md)、[Claude r2A](claude-d139-r2a.md)／[B](claude-d139-r2b.md)、[Gemini r2A](gemini-d139-r2a.md)／[B](gemini-d139-r2b.md)、[驗證](d139-verification.json)。

# `reviews/` 審查報告索引

- D138：[交付與還原](../docs/tasks/D138-DATA-CLEANUP.md)、[Claude r1 PASS](claude-d138-r1.md)、[Gemini r1 PASS](gemini-d138-r1.md)、[來源 manifest](d138-source-manifest.json)、[實際驗證](d138-verification.json)。只刪三份可還原原檔；實測約省 769 MB。

- D137：[交付](../docs/tasks/D137-MAIN-LAUNCH.md)、[Claude r3 PASS](claude-d137-r3.md)、[Gemini r3 PASS](gemini-d137-r3.md)、[來源manifest](d137-source-manifest.json)、[最終驗證](d137-verification.json)。

- D136：[交付](../docs/tasks/D136-CLEANUP-BATCH1.md)、[GPT驗證](gpt-d136-cleanup.md)、[Claude r1（已補證取代）](claude-d136-r1.md)／[r2 PASS](claude-d136-r2.md)、[Gemini r1](gemini-d136-r1.md)／[r2 PASS](gemini-d136-r2.md)、[來源manifest](d136-source-manifest.json)、[最終核對](d136-verification.json)。

- 2026-11-05 清理盤點（**REVIEW ONLY，非部署 PASS**）：[整合清單](../docs/tasks/CLEANUP-AUDIT-2026-11-05.md)、[GPT](gpt-cleanup-audit-20261105.md)、[Claude r1](claude-cleanup-audit-20261105-r1.md)／[r2](claude-cleanup-audit-20261105-r2.md)、[Gemini r1](gemini-cleanup-audit-20261105-r1.md)／[r2](gemini-cleanup-audit-20261105-r2.md)、[來源 SHA](cleanup-audit-20261105-source-manifest.json)。Gemini r1 封包截斷；r2 定點補證，限制與分歧見整合清單。

- D135：[Claude r2](claude-d135-r2.md)／[Gemini r2](gemini-d135-r2.md) 同SHA來源雙PASS，另有[Gemini部署／整合補審](gemini-d135-r2-supplement.md)；[來源與部署manifest](d135-source-manifest.json)。

> 本目錄保留各批次的**最終通過（PASS）審查證據**、尚未結案的阻擋審查與關鍵提案審查紀錄（部分早期報告曾於 D126 清理；目前保留的 r1／r2 等報告依表格狀態解讀，不能只按輪次判定可刪）。

| 檔案 | 對應決策／批次 | 審查者 | 狀態 | 說明 |
|---|---|---|---|---|
| [`gpt-d149-r18.md`](gpt-d149-r18.md) | D149 | gpt official CLI (`gpt-6-astra` `medium`) | `CLOSED (PASS)` | 拔除永久停線規則、不等缺線、live 窗口自動續期與截止前至少一模型完整答案保底送出（`deadline_fallback_v1`）獨立審查 PASS |
| [`claude-d132-r1.md`](claude-d132-r1.md) | D132／D134 | claude official CLI | `CLOSED (SUPERSEDED)` | 人類賽有界入口審查；實際操作與英文稿見 D132 任務單 |
| [`claude-d132-r2.md`](claude-d132-r2.md) | D132／D134 | claude official CLI | `CLOSED (PASS)` | 人類賽有界入口審查；實際操作與英文稿見 D132 任務單 |
| [`gemini-d132-r1.md`](gemini-d132-r1.md) | D132／D134 | gemini official CLI | `CLOSED (SUPERSEDED)` | 人類賽有界入口審查；實際操作與英文稿見 D132 任務單 |
| [`gemini-d132-r2.md`](gemini-d132-r2.md) | D132／D134 | gemini official CLI | `CLOSED (SUPERSEDED)` | 人類賽有界入口審查；實際操作與英文稿見 D132 任務單 |
| [`gemini-d132-r3.md`](gemini-d132-r3.md) | D132／D134 | gemini official CLI | `CLOSED (PASS)` | 人類賽有界入口審查；實際操作與英文稿見 D132 任務單 |
| [`claude-d135-r1.md`](claude-d135-r1.md) | D135 | claude official CLI | `CLOSED (SUPERSEDED)` | 健檢修正；補審與來源範圍見 D135 任務單 |
| [`claude-d135-r2.md`](claude-d135-r2.md) | D135 | claude official CLI | `CLOSED (PASS)` | 健檢修正；補審與來源範圍見 D135 任務單 |
| [`gemini-d135-r1.md`](gemini-d135-r1.md) | D135 | gemini official CLI | `CLOSED (SUPERSEDED)` | 健檢修正；補審與來源範圍見 D135 任務單 |
| [`gemini-d135-r2.md`](gemini-d135-r2.md) | D135 | gemini official CLI | `CLOSED (PASS)` | 健檢修正；補審與來源範圍見 D135 任務單 |
| [`gemini-d135-r2-supplement.md`](gemini-d135-r2-supplement.md) | D135 | gemini official CLI | `CLOSED (PASS)` | 健檢修正；補審與來源範圍見 D135 任務單 |
| [`claude-d133-r4.md`](claude-d133-r4.md) | D133 | claude official CLI | `CLOSED (PASS)` | 最終來源 r4；六鎖部署與canonical SHA見 d133-source-manifest.json |
| [`gemini-d133-r4.md`](gemini-d133-r4.md) | D133 | gemini official CLI | `CLOSED (PASS)` | 最終來源 r4；六鎖部署與canonical SHA見 d133-source-manifest.json |
| [`claude-d133-r1.md`](claude-d133-r1.md) | D133 | claude official CLI | `CLOSED (SUPERSEDED)` | 原文保留，修正與最終判定見 r4 |
| [`gemini-d133-r1.md`](gemini-d133-r1.md) | D133 | gemini official CLI | `CLOSED (SUPERSEDED)` | 原文保留，修正與最終判定見 r4 |
| [`claude-d133-r2.md`](claude-d133-r2.md) | D133 | claude official CLI | `CLOSED (SUPERSEDED)` | 原文保留，修正與最終判定見 r4 |
| [`gemini-d133-r2.md`](gemini-d133-r2.md) | D133 | gemini official CLI | `CLOSED (SUPERSEDED)` | 原文保留，修正與最終判定見 r4 |
| [`claude-d133-r3.md`](claude-d133-r3.md) | D133 | claude official CLI | `CLOSED (SUPERSEDED)` | 原文保留，修正與最終判定見 r4 |
| [`gemini-d133-r3.md`](gemini-d133-r3.md) | D133 | gemini official CLI | `CLOSED (SUPERSEDED)` | 原文保留，修正與最終判定見 r4 |
| [`d131-restoration-verification.md`](d131-restoration-verification.md) | D131 | GPT independent metadata verification | `CLOSED (PASS)` | 固定候選接受、各3call／29項PASS、僅來源替換、六項前檢；來源與證據見d131-restoration-manifest.json |
| [`claude-d130-pin-review-r2.md`](claude-d130-pin-review-r2.md) | D130 | claude official CLI | `CLOSED (PASS)` | Gemini pin／有界維護；補可重入鎖證據，來源SHA見d130-pin-manifest.json |
| [`gemini-d130-pin-review-r1.md`](gemini-d130-pin-review-r1.md) | D130 | gemini official CLI | `CLOSED (PASS)` | 同一份wrapper及維護script獨立審查 |
| [`claude-d130-pin-review-r1.md`](claude-d130-pin-review-r1.md) | D130 | claude official CLI | `CLOSED (SUPERSEDED)` | 缺少ledger來源之阻擋已由r2補證解除 |
| [`claude-d129-code-review-r2.md`](claude-d129-code-review-r2.md) | D129 | claude official CLI | `CLOSED (PASS)` | 最終程式複審；來源SHA見d129-source-manifest.json |
| [`gemini-d129-code-review-r2.md`](gemini-d129-code-review-r2.md) | D129 | gemini official CLI | `CLOSED (PASS)` | 最終程式複審；來源SHA見d129-source-manifest.json |
| [`claude-d129-code-review-r1.md`](claude-d129-code-review-r1.md) | D129 | claude official CLI | `CLOSED (SUPERSEDED)` | r1阻擋已於後續修正；Gemini封包截斷亦已補全 |
| [`gemini-d129-code-review-r1.md`](gemini-d129-code-review-r1.md) | D129 | gemini official CLI | `CLOSED (SUPERSEDED)` | r1阻擋已於後續修正；Gemini封包截斷亦已補全 |
| [`claude-m4-code-review-r6.md`](claude-m4-code-review-r6.md) | D127 M4 | Claude (Opus 5.5) | `CLOSED (PASS)` | M4 `resolve.py`／`score.py` 最終複審 PASS（附 P1～P4 後續建議） |
| [`gemini-m4-code-review-r6.md`](gemini-m4-code-review-r6.md) | D127 M4 | Gemini (Argon High) | `CLOSED (PASS)` | M4 最終獨立複審 PASS |
| [`claude-m4-code-review-r2.md`](claude-m4-code-review-r2.md) | D127 M4 | Claude (Opus 5.5) | `CLOSED (SUPERSEDED)` | r2 完整複審：B1-R blocker 與 E1～E3 證據要求（r3 起已修正並補證據） |
| [`gemini-m4-code-review-r2.md`](gemini-m4-code-review-r2.md) | D127 M4 | Gemini (Argon High) | `CLOSED (PASS)` | r2 完整獨立複審 PASS |
| [`claude-m4-code-review-r1.md`](claude-m4-code-review-r1.md) | D126／D127 M4 | Claude (Opus 5.5) | `CLOSED (SUPERSEDED)` | 初稿五類 blocker（B1～B5）與 C1～C6 證據要求，r2 起修正 |
| [`claude-d125-code-review-r2.md`](claude-d125-code-review-r2.md) | D125 (`autoload_v3`) | Claude (Opus 5.5) | `CLOSED (PASS)` | D125 Codex 載入量測相容性最終程式碼複審 PASS |
| [`gemini-d125-code-review-r2.md`](gemini-d125-code-review-r2.md) | D125 (`autoload_v3`) | Gemini (Argon High) | `CLOSED (PASS)` | D125 Codex 載入量測相容性獨立最終複審 PASS |
| [`claude-d124-final-code-review.md`](claude-d124-final-code-review.md) | D124 (Phase 1 & 2 observe) | Claude (Opus 5.5) | `CLOSED (PASS)` | D124 維護模式與 `auto_reaccept_v1` observe 最終複審 PASS |
| [`gemini-d124-final-code-review.md`](gemini-d124-final-code-review.md) | D124 (Phase 1 & 2 observe) | Gemini (Argon High) | `CLOSED (PASS)` | D124 維護模式與 `auto_reaccept_v1` observe 最終複審 PASS |
| [`claude-d121-m5-code-review.md`](claude-d121-m5-code-review.md) | D121 Batch 1 + M5 | Claude (Opus 5.5) | `CLOSED (PASS)` | D121 第一批與 M5 合併版程式碼審查 |
| [`gemini-m5-probe-review.md`](gemini-m5-probe-review.md) | D123 / M5-1 Probe | Gemini (Argon High) | `CLOSED (PASS)` | M5-1 試驗場地數值／離散題探測程式審查 |
| [`gemini-claude-fix-review.md`](gemini-claude-fix-review.md) | D119 | Gemini (Argon High) | `CLOSED (PASS)` | D119 Claude 基準誤擋與 CLI 固定版本修復複審 |
| [`gemini-fail-soft-review.md`](gemini-fail-soft-review.md) | D121 提案 | Gemini (Argon High) | `CLOSED` | 停線放寬提案審查意見（已整合為 PLAN D121） |
| [`gpt-fail-soft-review.md`](gpt-fail-soft-review.md) | D121 提案 | GPT (gpt-6-astra) | `CLOSED` | 停線放寬提案審查意見（已整合為 PLAN D121） |
