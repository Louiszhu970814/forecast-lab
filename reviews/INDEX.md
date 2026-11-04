# `reviews/` 審查報告索引

> 本目錄保留各批次的**最終通過（PASS）審查證據**、尚未結案的阻擋審查與關鍵提案審查紀錄（早期已被最終版取代的 `r1` 過渡報告已清理，見 git 歷史）。

| 檔案 | 對應決策／批次 | 審查者 | 狀態 | 說明 |
|---|---|---|---|---|
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
