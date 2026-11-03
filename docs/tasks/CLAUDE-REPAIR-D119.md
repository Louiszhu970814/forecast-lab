# D119 — Claude 基準誤擋與 CLI 固定版本修復

狀態：**已完成（D120，Claude 執行）**。新隔離驗收 PASS、限定 claude.plugins 的基準 `base_1793681992038_7cf51691` 已寫入、Claude 兩層 preflight 均 ok；Claude 線恢復可排程。詳見文末「D120 執行結果」。以下各節保留修復當時的狀態紀錄。

## 來源與診斷

Louis 指示「解決一下claude的問題」，GPT 依 D108 主持。首輪正式 preflight 因 `claude.plugins` raw aggregate hash 不符而停線；同時 `~/.local/bin/claude` 自動升至 2.1.317，正式驗收是 2.1.316。

舊外掛目錄 hash `e56f8ab74159e278f43b8ec9968e09edd3d3568f6068f8025556e45449bf784c`，近期 snapshot 為 `27e0d56aecd89489fe80322b509c2786ec23fe62fda97e1448153e561b4c2c5d`，皆 584 entries。近期 mtime 變動集中在同步 bucket 的 `.last-complete-round` 與 `.marketplaces.json`。舊基準、既有 inventory 報告與驗收快照都只保留總 hash，沒有逐檔 hash 或這兩個檔的歷史內容。因此檔數／mtime 不能證明其餘位元完全沒改，不能悄悄重設為已核准。

`claude.skills`（D69 的 13 個同步官方技能）與其他受保護內容的 hash 都與原已核准基準相同。本次 `claude.plugins` 內是官方 marketplace 本地目錄與同步 metadata；沒有因本修復啟用 marketplace 外掛。13 個技能位於另一個 source，不能把 marketplace 目錄內的 33 份 SKILL.md 當作新增載入的 33 個技能。

## 修復內容

- `claude_plugin_inventory_v2` / `claude_sync_state_v1`：只辨識精確的 `synced/<UUID>_<UUID>/.last-complete-round` 與 `.marketplaces.json`。UUID round token、etag、各 row 的 `updated_at` 值屬同步狀態；`listedFor`、來源、名稱、ID、scope、parserVersion、欄位存在／型別、row 順序與所有未知欄位仍屬內容。
- 路徑不符以普通內容處理；格式錯誤、重複 JSON keys、非有限數字、過大檔案、symlink／FIFO／讀取中替換均停線。新增逐檔 canonical／raw hash，之後能逐檔對帳。舊 aggregate 基準與新格式刻意不相等，禁止自動核准遷移。
- `launchd/bin/claude` 固定已安裝且已驗收的官方 2.1.316，複本放在 gitignored `data/cli_pins/claude/2.1.316`；每次核對 SHA-256，缺檔／改檔即拒絕，並設 `DISABLE_AUTOUPDATER=1`。全域 2.1.317 保留；只在比賽 launchd 的 PATH 前置專案 wrapper。
- 未改模型、prompt、整合、選題、預算或送出條件；不讀取開放或即將開放題目的輸出。D114 frozen observer 的相依檔不含本次改動。

## 官方依據與實機證據

[Claude Code 官方安裝文件](https://code.claude.com/docs/en/setup#disable-auto-updates) 說明 `DISABLE_AUTOUPDATER=1` 關閉背景更新。[官方 skills 文件](https://code.claude.com/docs/en/skills#where-synced-skills-load) 說明同步來源會在 session 期間刷新。

另從本機已簽章 2.1.316 執行檔唯讀核對實作：round 格式是 token + 空格 + `listedFor.join(',')`，故後半段不可忽略；marketplace etag 為條件式 GET 狀態。片段及 offset 記於 `data/operations/d119-claude-repair/cli_metadata_evidence.jsonl`，未讀 CLI 憑證。

Pin SHA-256：`64c327e92b2c5e0f78c0a980c5a518121e1f0d140ca771ca5dd4488d1a2dcb3f`；310114176 bytes；`codesign --verify --strict` 通過，簽章 Anthropic PBC、TeamIdentifier `Q6L2SF6YDW`。

## 已由 Louis 接受的具體候選（D120）

只更換 `claude.plugins` 一個來源，不核准其他全域變更。候選 content hash：`39cebbf40115ef91210892ef733ff33a6f14257b7e78952a83d582f696756d1b`，584 entries；完整含逐檔 hash 的 snapshot SHA：`4b29bd8b0a54ae7d8b0468f1f60a2deabaa0b4f492748d2f8667f2ef99881e41`，紀錄 `data/operations/d119-claude-repair/candidates.jsonl` 最後一筆。資訊狀態刷新可改 raw hash；受保護 content hash 不可變。

依 PLAN §7.2 / D21：「全域指示、記憶、skills、hooks、plugins 或 MCP 的內容改變…經人工審查後再重跑驗收。」這次舊基準缺少逐檔明細，無法證明為純 metadata 變更；Louis 已在本候選的明確確認後回覆「OK 先交接一下我給claude做」，接受此候選並指定交 Claude 執行（PLAN D120），不再重問。Claude 應先重跑新的隔離驗收；只有晚於候選的最新驗收通過、證據 hash 正確且前後盤點對應此候選，才可寫一筆限定來源的 baseline_approved，再核對兩層 preflight。新版 helper 強制要求這筆新的隔離證據；沒有它時舊 STOP 基準保持不變。既有 autoload ID 僅保留來源載入機制的歷史證據（CLI 版本、skills、設定與載入入口未變），不能代替新的隔離驗收。不清除舊帳冊、不補改已完成的正式預測。

## 驗證

- 第一版 parser／基準／wrapper：完整 2288 passed（81.65s），相關 131 passed。新增候選綁定 helper 的 10 項測試後：完整 2298 passed（81.02s）。其後補強新隔離證據先於核准的 gate，新增 7 項測試，相關共 148 passed；最後完整回歸 **2305 passed（81.32s）**。
- Claude／Gemini 工具關閉的核心獨立審查均 PASS。候選綁定 helper 複審 Gemini PASS；Claude 指出必須防止先核准後驗收的間隙，現已改為程式強制先取得對應候選的新隔離證據，並補測，最後複審 **Claude／Gemini 均 PASS**；Claude 的完整回歸條件已由 2305 passed 滿足。所有模型都由官方 CLI、專案外空白暫存目錄執行；持有三條 forecast locks，並核對當輪 admission 已結束且距 handoff 足夠，故不與正式模型呼叫重疊，cycle 的送出／收尾可繼續。
- 正式隔離重驗與新基準套用：Louis 已接受候選，交由 Claude 執行；GPT 本輪未開始這兩項。程式測試不代表 Claude 已恢復預測。

操作與候選證據：`data/operations/d119-claude-repair/`。未 commit／push。

## 部署後實測與接手

UTC2026-11-03 04:45:13Z 已部署 8 個經審查檔案。實際以 `runner` 清理環境、`netguard` 包裝後執行 `claude --version`，確認經專案 wrapper 取得 2.1.316；adapter.cli_version 亦為 2.1.316，PATH 未被 scrubber 清除。核對真實既有 acceptance_result／evidence.json 欄位符合新 helper 使用的格式。

新基準仍未寫入。實機比對：原已核准基準 Claude=stop、Gemini=ok、GPT=ok；新候選與目前受保護內容三線均 ok。這是候選一致性證據，不能當作新的隔離驗收或基準套用已完成。外掛檔案可審查封存：`data/operations/d119-claude-repair/plugin-content-39cebbf4.zip`，含全部 584 個檔案及該候選紀錄。沒有重啟 live cycle 或 TG。

Claude 接手順序（Louis 接受已記錄於事件 `d120_handoff_1793681518672_ce44cc95`，原話為「OK 先交接一下我給claude做」；UTC04:51:58.672 為記錄時間。approval_reference 引用該事件與原話，順序由主持人流程保證）：

1. 先查 project_control status，核對已接受的 snapshot `4b29bd8b0a54ae7d8b0468f1f60a2deabaa0b4f492748d2f8667f2ef99881e41`，不是泛用的「最新版」。
2. 持 forecast.claude 鎖；其他線不可在會互相污染的驗收期間啟動模型。若活躍 cycle 已過 admission 且三線 idle，可依本輪方式取得三線 locks，保持 cycle 送出運作；驗收不放全域 autoload canary。不要中止現有模型工作。
3. PATH 前置專案 launchd/bin，跑現有 Claude 隔離驗收（acceptance.py，預設便宜模型的 negative／positive／format_repair，3 次呼叫仍記預算）。本次沒有改 adapter／runner／模型，既有正式兩層模型證據繼續適用。
4. 驗收通過後，立刻重新取得實機 snapshot；用 `claude_baseline_candidate.approve`，指定上述固定 SHA、實際 Louis 接受參照與剛通過的 isolation_record_id；不能傳舊 candidate 當作 current，不能使用 baseline.py 的泛用 approve 重新拍照核准。helper 會拒絕基準已改、受保護內容變動、證據不符與未完成的新驗收。
5. 兩層實機 preflight 通過後，從既有後續正常排程接回 Claude，不重跑或改寫已完成的正式預測。記錄結果並更新 PLAN／HANDOFF／手機摘要。

D120 交接補充：使用 `claude_baseline_candidate.approve(candidate, expected_snapshot_sha256=固定候選SHA, reviewed_by="Louis", approval_reference=上述接受事件與原話, current=剛取得的實機Snapshot, isolation_record_id=新通過的record_id)`；`current` 所需 Gemini credits 應來自有效的實機觀測，不能只填預設值。從 Claude Code 啟動模型 CLI 時依 D70 清理子程序 `CLAUDE*`／`CLAUDECODE` 環境；專案 wrapper 本身會設 DISABLE_AUTOUPDATER。取得鎖後執行原 acceptance 入口即可，不需要重做已通過且未改動的2305項完整回歸。GPT 的交接不代表 Claude 已接手或已恢復；由 Claude 執行並發布實際結果。

## D120 執行結果（Claude，UTC 2026-11-03）

- **持鎖時機**：04:58:35.973 取得 claude／gpt／gemini 三條 forecast locks；當時 cycle `cycle_1793681178014_69e684ea` 已過 admission（04:54:48）、位於 submit 步驟，三線沒有模型在執行，沒有中止任何工作。04:59:54.460 釋放，持鎖約 78 秒；期間沒有排程工作結束或被拒（`scheduler_jobs.jsonl` 無該時段紀錄）。
- **環境**：PATH 前置 `launchd/bin`（固定官方 2.1.316），子程序移除 `CLAUDE*`／`CLAUDECODE`（D70）；沒有放全域 autoload canary，沒有重啟 live cycle 或 TG。
- **隔離驗收**：`acceptance.py run --players claude`（便宜模型，negative／positive／format_repair 共 3 次呼叫，計入預算）。record `acc_1793681985508_f9fec3a8`，`line_pass: true`，`cli_version: 2.1.316`，全部測試 pass；證據 `data/acceptance_runs/accrun_1793681916013_1fb442f6/claude/evidence.json`。
- **實機 snapshot**：04:59:51.759，CLI 版本 claude 2.1.316／gpt 0.166.0／gemini 1.4.12；Gemini credits 由實機 `/credits` 觀測為 0（無同時執行、未過期）。
- **限定基準套用**：`claude_baseline_candidate.approve`，`expected_snapshot_sha256 = 4b29bd8b0a54ae7d8b0468f1f60a2deabaa0b4f492748d2f8667f2ef99881e41`、`reviewed_by = Louis`、approval_reference 引用 `d120_handoff_1793681518672_ce44cc95` 與原話、`isolation_record_id = acc_1793681985508_f9fec3a8`。寫入 `base_1793681992038_7cf51691`，取代 `base_1793538013655_8fc7b859`；只替換 claude.plugins，其他來源逐位元沿用舊基準。沒有使用泛用 `baseline.py approve`。
- **兩層 preflight**：Claude tier 1、tier 2 均 `ok`，problems 與 waivers 皆為空，cli_version 2.1.316。
- 沒有修改程式、模型、prompt、預算或送出條件；沒有補改已完成的正式預測。操作紀錄 `data/operations/d120-claude-restore/operations.jsonl`，驗收輸出 `acceptance_stdout.txt`。
- **排程接回**：下一輪 `cycle_1793682455879_d3cbd187`（UTC 05:07:35）Claude preflight 為 ready，已進入 scheduler 步驟。同輪 Gemini 因同時執行的審查用 agy 而暫時 preflight_blocked（credits 記為未知），屬逐輪判定，與本修復無關。
