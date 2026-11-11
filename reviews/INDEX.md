# `reviews/` 最終審查報告與驗證索引

> 本目錄僅保留各批次的**最終通過（PASS）審查報告**、盤點結論與對應的來源／驗證 JSON manifest；中間退件輪次與已作廢提案審查已於 D150 清理。

## 1. 最終審查報告（Markdown）

| 檔案 | 對應決策／批次 | 審查者 | 結果 | 說明 |
|---|---|---|---|---|
| [`gpt-d149-r18.md`](gpt-d149-r18.md) | D149 | GPT (`gpt-6-astra` `medium`) | `PASS` | 拔除永久停線、不等缺線、live 窗口自動續期與 `deadline_fallback_v1` 獨立審查 |
| [`gemini-d147-r1.md`](gemini-d147-r1.md) | D147 | Gemini (`gemini-4-argon-high`) | `PASS` | 官方 Codex 外掛 `latest` 別名相容（`baseline_v4`）審查 |
| [`claude-d140-r3a.md`](claude-d140-r3a.md)、[`claude-d140-r3b.md`](claude-d140-r3b.md)、[`claude-d140-r4.md`](claude-d140-r4.md) | D140 | Claude (`claude-opus-5-5`) | `PASS` | Gemini 工具拼字恢復模組 A／B 與合併差異最終審查 |
| [`gemini-d140-r3a.md`](gemini-d140-r3a.md)、[`gemini-d140-r3b.md`](gemini-d140-r3b.md)、[`gemini-d140-r4.md`](gemini-d140-r4.md) | D140 | Gemini (`gemini-4-argon-high`) | `PASS` | Gemini 工具拼字恢復模組 A／B 與合併差異最終審查 |
| [`claude-d139-r2a.md`](claude-d139-r2a.md)、[`claude-d139-r2b.md`](claude-d139-r2b.md) | D139 | Claude (`claude-opus-5-5`) | `PASS` | 30 分鐘搜尋、Market Pulse 與 `human_advice.py` 分包最終審查 |
| [`gemini-d139-r2a.md`](gemini-d139-r2a.md)、[`gemini-d139-r2b.md`](gemini-d139-r2b.md) | D139 | Gemini (`gemini-4-argon-high`) | `PASS` | 30 分鐘搜尋、Market Pulse 與 `human_advice.py` 分包最終審查 |
| [`claude-d138-r1.md`](claude-d138-r1.md)、[`gemini-d138-r1.md`](gemini-d138-r1.md) | D138 | Claude / Gemini | `PASS` | `data/` 無損封存與重複副本清理雙審 |
| [`claude-d137-r3.md`](claude-d137-r3.md)、[`gemini-d137-r3.md`](gemini-d137-r3.md) | D137 | Claude / Gemini | `PASS` | FutureEval 主賽＋MiniBench 啟用與預算接續最終審查 |
| [`claude-d136-r2.md`](claude-d136-r2.md)、[`gemini-d136-r2.md`](gemini-d136-r2.md)、[`gpt-d136-cleanup.md`](gpt-d136-cleanup.md) | D136 | Claude / Gemini / GPT | `PASS` | 第一批文件、測試隔離與 dead import 清理審查與驗證 |
| [`gpt-cleanup-audit-20261105.md`](gpt-cleanup-audit-20261105.md)、[`claude-cleanup-audit-20261105-r2.md`](claude-cleanup-audit-20261105-r2.md)、[`gemini-cleanup-audit-20261105-r2.md`](gemini-cleanup-audit-20261105-r2.md) | 清理盤點 | GPT / Claude / Gemini | `AUDIT` | 2026-11-05 主賽前文件與 dead code 盤點最終補審 |
| [`claude-d135-r2.md`](claude-d135-r2.md)、[`gemini-d135-r2.md`](gemini-d135-r2.md)、[`gemini-d135-r2-supplement.md`](gemini-d135-r2-supplement.md) | D135 | Claude / Gemini | `PASS` | 閉題健檢修正最終審查與整合補審 |
| [`claude-d133-r4.md`](claude-d133-r4.md)、[`gemini-d133-r4.md`](gemini-d133-r4.md) | D133 | Claude / Gemini | `PASS` | 閉題練習手動入口與記帳隔離最終審查 |
| [`claude-d132-r2.md`](claude-d132-r2.md)、[`gemini-d132-r3.md`](gemini-d132-r3.md) | D132／D134 | Claude / Gemini | `PASS` | Metaculus Cup 人類賽有界參考入口最終審查 |
| [`d131-restoration-verification.md`](d131-restoration-verification.md) | D131 | GPT | `PASS` | Claude／GPT 基準重驗恢復獨立驗證 |
| [`claude-d130-pin-review-r2.md`](claude-d130-pin-review-r2.md)、[`gemini-d130-pin-review-r1.md`](gemini-d130-pin-review-r1.md) | D130 | Claude / Gemini | `PASS` | Gemini CLI pin wrapper 與有界維護最終審查 |
| [`claude-d129-code-review-r2.md`](claude-d129-code-review-r2.md)、[`gemini-d129-code-review-r2.md`](gemini-d129-code-review-r2.md) | D129 | Claude / Gemini | `PASS` | `resolve_v2`／`score_v2` 與 live ITT 診斷最終複審 |
| [`claude-m4-code-review-r6.md`](claude-m4-code-review-r6.md)、[`gemini-m4-code-review-r6.md`](gemini-m4-code-review-r6.md) | D127 M4 | Claude / Gemini | `PASS` | M4 `resolve.py`／`score.py` 最終複審 |
| [`claude-d125-code-review-r2.md`](claude-d125-code-review-r2.md)、[`gemini-d125-code-review-r2.md`](gemini-d125-code-review-r2.md) | D125 | Claude / Gemini | `PASS` | `autoload_v3` Codex 載入量測相容性最終複審 |
| [`claude-d124-final-code-review.md`](claude-d124-final-code-review.md)、[`gemini-d124-final-code-review.md`](gemini-d124-final-code-review.md) | D124 | Claude / Gemini | `PASS` | `autoload_maintenance_v1` 與 `auto_reaccept_v1` 最終複審 |
| [`claude-d121-m5-code-review.md`](claude-d121-m5-code-review.md) | D121 + M5 | Claude | `PASS` | D121 第一批與 M5 數值／離散題合併版審查 |
| [`gemini-m5-probe-review.md`](gemini-m5-probe-review.md) | D123 / M5-1 | Gemini | `PASS` | M5-1 試驗場地數值／離散題探測程式審查 |
| [`gemini-claude-fix-review.md`](gemini-claude-fix-review.md) | D119 | Gemini | `PASS` | D119 Claude CLI 固定版本 wrapper 修復複審 |

## 2. 來源雜湊與部署驗證 Manifest（JSON）

- **D147**：[`d147-source-manifest.json`](d147-source-manifest.json)
- **D140**：[`d140-review-evidence.json`](d140-review-evidence.json)
- **D139**：[`d139-source-manifest.json`](d139-source-manifest.json)、[`d139-verification.json`](d139-verification.json)
- **D138**：[`d138-source-manifest.json`](d138-source-manifest.json)、[`d138-verification.json`](d138-verification.json)
- **D137**：[`d137-source-manifest.json`](d137-source-manifest.json)、[`d137-verification.json`](d137-verification.json)
- **D136**：[`d136-source-manifest.json`](d136-source-manifest.json)、[`d136-verification.json`](d136-verification.json)
- **2026-11-05 清理盤點**：[`cleanup-audit-20261105-source-manifest.json`](cleanup-audit-20261105-source-manifest.json)、[`cleanup-audit-20261105-verification.json`](cleanup-audit-20261105-verification.json)
- **D135**：[`d135-source-manifest.json`](d135-source-manifest.json)
- **D133**：[`d133-source-manifest.json`](d133-source-manifest.json)
- **D131 / D130 / D129**：[`d131-restoration-manifest.json`](d131-restoration-manifest.json)、[`d130-pin-manifest.json`](d130-pin-manifest.json)、[`d129-source-manifest.json`](d129-source-manifest.json)
