# D144：日常開發目錄變更不再停線

- 日期：2026-11-06；PLAN v1.98；本回合整合者 GPT。
- Louis 直接授權：「可以也把這種限制拔掉嗎 我被這種當掉太多次了之後還會有別的開發一定會變的 看看今天因為這樣漏了幾題」。
- 範圍：移除開發目錄可讀內容變更造成的正式預測阻擋，保留正式執行限制及歷史證據；不重跑已提交題。

## 今日影響

期間為台北 2026-11-06 00:00 至 19:34:02.725（UTC `[2026-11-05T16:00:00Z, 2026-11-06T11:34:02.725Z)`）。以今天實際 queued 的正式題為母體：4 題都完成預測及留言，整題漏交 0，缺一條第 1 層模型線 3 題。

| question ID | 缺少線別 | 預測確認 UTC | 原因證據 |
|---|---|---|---|
| 46573 | Gemini | 2026-11-05T20:30:48.939Z | 納入至提交間 44 個週期均為 Gemini preflight_blocked；沒有 Gemini T1 嘗試 |
| 46574 | Gemini | 2026-11-05T23:30:46.639Z | 納入至提交間 43 個週期均為 Gemini preflight_blocked；沒有 Gemini T1 嘗試 |
| 46575 | 無 | 2026-11-06T01:21:11.478Z | D143 恢復後補齊三線，all_lines_accounted_for |
| 46576 | GPT | 2026-11-06T08:31:47.611Z | 納入至提交間 36 個週期均為 GPT preflight_blocked；沒有 GPT T1 嘗試 |

GPT 此次 skills 變動停線最早記錄為 `2026-11-06T02:24:42.794Z`；當前比對唯一受保護差異為 `codex.skills`。凌晨兩題是 D143 修復前的 Gemini 一般設定／專案授權基準阻擋。另有 GPT 第二層 token breaker 紀錄，與本次第 1 層缺線分開，不把未要求的第二層計作漏答。

盤點僅輸出帳冊 metadata，沒有預測正文、機率或理由。依據為 `data/operations/d144-development-baseline-20261106/impact.jsonl`（含各來源列數）；不據此宣稱全站完整發現、沒有其他原因漏題或估算分數損失。四題已關閉／已送出，不事後重跑。

## 生效規則

`baseline_v3` 的預設 `compare()` 將以下精確來源的可讀內容新增、修改、移除記為 `informational`，不再觸發 stop／reaccept，也不為它們建立 D124 observe 候選：

- Claude：`claude.skills`、`claude.skills_synced`、`claude.plugins`、`claude.marketplace_index`、`claude.plugin_state`、`claude.agents`、`claude.commands`。
- GPT：`codex.skills`、`codex.plugins`、`codex.agents`。
- Gemini：`agy.skills`、`agy.plugins`、`agy.agents`。

原始 inventory 分類／雜湊及舊核准帳冊不改寫；只變更營運差異分類。來源定義（kind／path／player）變動、來源盤點項目被增刪、讀取錯誤仍阻擋。可解析的 catalog 內容不是執行許可；獨立 hooks／MCP、全域指示／記憶、agy rules、管理者／上層設定及其他受保護設定仍沿原規則。

三線工具清單、CLI 參數、網路隔離、runtime 指紋、模型／effort、型錄檢查、驗收證據／SHA、未知結果、真正越權及暴露停線全部保留。首次沒有已核准基準仍不能執行。此修正不會追加 line_resume 或 baseline_approved，也不改排程、預算或 UTC 11/10 04:00 截止。

本政策不把工具隔離等同文字隔離。Codex 的 skill 名稱／描述可能被加入上下文；[官方技能文件](https://learn.chatgpt.com/docs/build-skills)與[設定參考](https://learn.chatgpt.com/docs/config-file/config-reference)亦區分技能載入與停用設定。本輪依使用者指示移除內容變更閘門，沒有宣稱現有 CLI 參數已排除全部 skill 文字，也沒有改 CLI 參數來製造未驗收的隔離證據。

固定候選核准、驗收前後快照與 autoload 維護使用 `compare(..., catalog_drift=False)`，維持精確內容綁定；`_same_protected()` 不放寬。營運恢復不能默默核准另一次驗收或維護中的內容變更。

## 驗證與交付

工作在專案外隔離副本完成；部署採六鎖、來源雜湊比對、備份與失敗還原。正式模型工具限制與設定雜湊須保持相同。實際測試、部署與恢復時間於下方追加。

- 完整離線測試：`3644 passed in 145.64s`；最後相關驗證 `163 passed`。保護目錄的實際宣告／越權測試亦在完整套件內。
- 修改前副本重現 3 個既有失敗：D143 已允許的已知版本變動仍被兩個舊測試要求阻擋、M5 fixture 的 AGENTS.md 雜湊未同步 D141。只更新這三處預期／單一文件雜湊，其他受保護 runtime／prompt／設定與金標準輸出不改。
- 初輪與後續測試摘要記於 operations 的 `test-runs.jsonl`；最後完整輸出為 `tests-full-final.txt`。本輪沒有另行呼叫其他模型審查，不宣稱雙審。

### 實際套用結果

- `2026-11-06T11:44:15.800Z` 六鎖套用完成；部署前 GPT 為 `stop`、部署後 Claude／Gemini／GPT 兩層共六項 `ok=True`、`waivers=[]`，三線 line guard 均無停線。部署後根目錄 smoke 92 passed。
- 28 項正式 runtime／設定／prompt／AGENTS／STOP_SUBMIT 保護雜湊（含不存在標記）前後一致；三個 LaunchAgent 均載入，cycle 60秒、line-recovery 300秒設定保持。
- 累計30題預測及留言全部確認、未確認送出0。主持人本輪0次模型呼叫、0次預測POST、沒有新增 baseline_approved／line_resume；自然排程照原設定執行，最新人工檢查通過不冒充新題研究已發生。
- 證據：`data/operations/d144-development-baseline-20261106/source-manifest.json`、`checks.jsonl`、`deployment.jsonl`、`completion.jsonl`。完整來源修改為18檔；保留原工作樹未提交內容，未commit／push。
