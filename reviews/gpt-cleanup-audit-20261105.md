# GPT 清理盤點（2026-11-05）

本輪是 Louis 要求的清理前審查，不是刪除、部署或主賽啟用決定。依 PLAN v1.89、HEAD `2f202bf` 與 D132–D135 尚未提交的工作樹盤點。335 檔來源快照包含 81 個 Python 來源模組（28,005 行）、79 個 `test_*.py`、64 份任務 Markdown；未讀開放 bot 題預測、憑證或私人報告。

方法：來源快照、AST 定義／import 盤點、全文引用搜尋、CLI／launchd 入口核對、Markdown 連結檢查及針對性靜態閱讀。AST 掃描有動態載入、套件轉出及框架 callback 的限制；沒有找到 importer 不是 dead code 證明。Claude／Gemini 的獨立意見另存，本文件只代表 GPT 的直接核對。

## 可以現在安排的清理

- [§3.1／§3.6／§7.2；AGENTS.md 硬性規則 2] 更正仍將 Metaculus Cup 列為 bot 試驗場地的文字，明確指向 D79 與 PLAN 現行排除規則；這是規則矛盾，應優先處理。套用前核對該指示檔實際是否屬驗收盤點來源，不可假定「文件」必然沒有基準影響；不因檔名就要求所有線重驗｜PLAN 已明定 Cup 不再是試驗場地，共同守則仍寫相反內容｜blocking（對一致、安全的操作文件）
- [§5.2／§16；README.md:5] 更新停留在 2026-11-03／v1.78 的現況與版本，減少重複維護即時狀態，連到 HANDOFF 與 status；目前已是 v1.89，新增 M4、D130–D135 未反映｜新協作者會從過時入口開始｜non-blocking
- [§4.3／§10；docs/tasks/INDEX.md、M4-RESOLVE-SCORE.md、D128-LIVE-ITT.md、D129-ITT-IMPLEMENTATION.md、HANDOFF.md] 將「首題台北 11/06 21:00」標為當時安排；現況改指 D132 核對的已送出批次最早預定台北 11/13 20:00，不能把任何預定日期當已結算。保留歷史原文和更正來源｜新版頂部雖有更正，索引與多份操作待辦仍傳播舊日期｜non-blocking
- [§5.2；docs/tasks/INDEX.md、reviews/INDEX.md] 正規列入 D135、D132、D134 的現況與所有保留審查；更新 reviews 開頭「早期 r1 已清理」的概括文字；區分啟用中的功能、未完成工作、結案證據｜兩份索引已與新增文件和狀態不完全一致｜non-blocking
- [§5.2；docs/tasks/M3-V15-SCHEMA.md:90、M3.md:13/44、MINIBENCH-READINESS.md:98、OVERNIGHT-D84.md:89/141] 修復 6 處指向已刪 `DECISIONS-AFTER-D84.md` 的連結；依原決策建立歷史連結或導覽 stub，保留來源，不能恢復舊規格為現行授權｜該檔於 `392335b` 刪除；掃描另一個 `PLAN.md:1306` 的 `(...)` 是示例，不能算真斷鏈｜non-blocking
- [§5.2／§14；docs/tasks/M3-MC-READBACK-D99.md、TELEGRAM-DEV.md、FAIL-SOFT-RELAX-PROPOSAL.md、M3-V15-SCHEMA.md] 在原頁加入明確 SUPERSEDED／取代來源；其中 fail-soft 提案仍稱「待 Louis 決定／尚未寫入 PLAN」。D117 與 D98 已有後續更新提示，不應重複當成完全未標記｜只在 INDEX 標狀態，直接開文件仍會誤讀｜non-blocking
- [§5.2／§14；PLAN.md、HANDOFF.md] 可安排把 §14 舊審查與舊交接移至有索引的歷史檔，保留原標題入口、作者、時間、決策編號與連結；PLAN §15／§16 及現行規格仍由整合者維護。PLAN 2,109 行／436,878 bytes，其中 §14 為 577 行／139,308 bytes｜可以縮短閱讀負擔；封存不等於刪除依據，也不必為純文件整理等到預算換窗｜non-blocking
- [§5.2；launchd/.gitkeep、prompts/.gitkeep] 目錄已有受追蹤內容，可刪空占位檔｜無執行用途，低風險整理｜non-blocking
- [§5.2／§9；src/budget.py:106 `_positive`、src/forecast.py:733 `_fraction`] 列為高信心小範圍 dead helper 候選：整份來源快照只有定義，無呼叫、字串 dispatch 或測試引用。可在現在的窗口做獨立工程清理批次；仍須按既有正式程式雙審、互斥套用與回歸程序，不和 D132–D135 未提交修改混在一起｜不需改固定設定或等統計樣本，但這仍是 runtime 檔案變更｜non-blocking
- [§5.2；src/auto_reaccept.py `hashlib/os`、comment.py `math`、continuous.py `new_id`、flab/team_chat.py `json`、forecast.py `Iterator`、m2_supplement.py `transaction`、metadata_observer.py `MetaculusError`、quota.py `MODEL_CHECKS`、reconcile.py `read_records`、resolve.py `Mapping`、scheduler.py `latest_snapshots`] 共 12 個未使用 import 名稱，可納入小範圍清理；先核對轉出／外部操作腳本依賴。另 `adapters/gpt.py:10 Path` 也未使用，但屬驗收指紋檔，留到重驗維護批次｜清理噪音有益，為一個 import 觸發正式線停用則不划算｜non-blocking

## 要先拆依賴或補證據的項目

- [§5.2／D91；src/telegram_bot.py:282/306/388] `dev_*` 在 handle 前段已改為 `team_help` 並返回；後面的 `dev_command` dispatch 不可由該正常入口抵達。`enqueue_dev_notifications` 只剩測試呼叫；`enqueue_due` 也不在目前 poll loop 裡。可移除舊啟動／推送路徑，但保留對舊 outbox 的取消處理與歷史事件讀取｜刪除遷移防護可能讓舊通知重送；需現行 TG 行為測試及空檔重載｜blocking（對整段盲刪）
- [§5.2／D91；src/flab/dev_runner.py、dev_tasks.py] 舊 DevWorker 已不由服務啟動，可作退役候選；先抽離 `clean_env/codex_binary` 與 `TaskError/safe_text/public_text/SECRET`。`team_runner.py:18`、`team_chat.py:15` 和 Telegram 都仍依賴它們；還有 TaskStore 初始化、舊命令辨識和歷史通知取消需要遷移。舊路徑先保留薄轉接｜不能因舊 TG 開發模式停用而刪掉整個模組｜blocking
- [§5.2；tests/] 45 個測試檔有 123 個 `test_*.py` import 節點，先將共用 fixture／builder 抽到 `tests/helpers/`，保留測試案例與 collection。沒有發現同一 scope 的重名測試覆蓋｜刪某個舊測試檔會使別的現行測試無法 import；測試檔年代不是刪除理由｜blocking（對整檔刪除）
- [§7.6／§10；tests/test_cycle.py 等] 既有 100 失敗須逐類釐清，不能按「過時」批次刪掉。D135 留存分布：cycle 38、main_site_trial 25、m2_supplement 13、scheduler 12、rollover 5、numeric_pipeline 3，其餘 d121_review／m3_w8／poll_questions／submit 各 1。測試名稱不能證明是哪一方錯｜屬驗證可信度工作；修 fixture、修實作、退役契約須各自有根據｜blocking（對宣稱清理後全套 PASS）
- [§5.2／§7.6；acceptance.py:686 `argon_acceptance_gate`／`ARGON_CALL_ESTIMATE`] 目前只有一項舊測試直接呼叫此 helper，真實 `_run` 已改用 `quota.manual_admission`。先把必要准入覆蓋移到現行路徑，再決定刪除；不能把它當成今天仍使用的閘門｜tests-only 不等於可無條件刪；此項涉及安全驗收契約｜non-blocking
- [§5.2；numeric.py:277 `cdf_fields`] 目前只由 `tests/test_numeric.py:72` 的 builder 呼叫。可評估搬到測試 helper，保留等價 fixtures 與獨立數值斷言｜這是測試依賴，當前不是無使用者的 dead code｜non-blocking
- [§5.2；m2_prepare.py、m2_supplement.py、m2_report.py] 先標為 legacy／手動診斷入口，檢查營運腳本、CLI、舊資料重播與測試使用後，才可移到 legacy/tooling 或退役。`scheduler.py` 仍使用 `m2_campaign.SafeSnapshots`，`continuous.py` 仍使用 `discover_tournaments`；必須先抽共用實作及轉接｜M2 名稱不能推出整套已無用｜blocking（對整套刪除）
- [§5.2／§9；continuous_v2–v9、budget_handoff、v15_probe 舊版本分支] 先建立所需舊帳冊版本清單及重播案例，等當前 D118 窗口到期、在途／unknown 收斂且資料保留方案通過後再評估；11/10 到期本身不是刪除許可，換窗批次不混入重構｜舊版相容可能仍承擔預算累計、對帳與復原｜blocking
- [§5.2；launchd/run.sh:14–15、report／resolve plist、HANDOFF 階段 D] `report` 指向不存在的 `src/report.py`，屬未實作／不可安裝的範本；`resolve` 已有手動入口但排程未啟用。現在先改清楚說明，後續決定保留標示、移至 templates 或補實作；不要把缺失功能稱 dead code，亦不要藉清理安裝排程｜避免誤裝空入口或擴大啟用範圍｜non-blocking

## 必須保留／本輪未證明可刪

- [§5.2／§7.6] `claude_baseline_candidate.py` 在 tracked src 沒有 importer，但 `data/operations/d130-three-line-repair-20261104/repair_claude.py:23/242` 仍引用及呼叫；保留手動恢復工具｜只掃 src 會漏掉真實維護入口｜blocking（對刪除）
- [§6／§7.6] `repair_v1.md` 與 `repair_v2.md` 分別用於 tier1／tier2，forecast.py:132 明確分派；保留。D135 四份候選提示詞尚未完成實際驗證，應標實驗而非過時刪除｜不同用途及待驗工作均不等於死檔｜blocking（對刪除）
- [§5.2／§7.6] `flab/__init__.py` 的 28 個表面 unused import 屬 `__all__` 公開轉出；HTMLParser／HTTPServer callback 也由框架呼叫，保留｜靜態低引用誤報｜blocking（對機械刪除）
- [§7.2／§7.6] `auto_reaccept.py`／policy 仍由 launchd cycle 後 observe 接點使用；enforce 未實作不代表 observe 無用。`v15_probe.py`、`m5_probe_session.py`、metadata observer／replay、coverage 工具、acceptance／autoload 仍是手動驗收、診斷或復原用途｜手動入口不要求一般 src importer｜blocking（對刪除）
- [§5.5／§7.5／§7.6] 保留歷史 JSONL、原始證據、未決請求、審查最終原文、source manifest、驗收 pin 與 fixtures；D132–D135 尚未提交的內容也不能當暫存垃圾。早期審查可封存，先確認最後判定／修正鏈，不能只按 r1/r2 或 PASS/FAIL 刪除｜回溯與安全復原依賴這些資料｜blocking

## 本輪驗證

- 隔離副本 `tests/run_offline.py --synthetic-host -q tests/test_dev_tasks.py tests/test_dev_runner.py tests/test_telegram_bot.py`：**77 passed**。這也顯示舊路徑仍可被測試直接呼叫；不能把單元通過誤當成服務啟用證據。
- 全套加 `--synthetic-host`：**3317 passed／34 failed**。27 個失敗涉及仍假定 numeric/discrete 未啟用的斷言或 mock（main_site_trial 25、cycle 1、poll_questions 1）；7 個為全域 synthetic fixture 產生 `synthetic-project/` 對 test_config／test_resolve 的衝突。此執行模式與 D135 留存基線不同，不能宣稱從 100 修成 34，也不能稱全套 PASS。應修測試隔離／契約，不能修改正式題型開關求綠燈。
- plain `tests/run_offline.py -q --tb=short`：**3241 passed／110 failed**。D135 原 100 失敗節點全保留，另有 advisory 1、practice 6、workdir 3；直接見 runner TMPDIR 位於工作副本內而違反 attempt 目錄邊界的錯誤，其他新增失敗尚未逐一根因驗證。不把不同 harness／主機條件的計數差當修復或正式功能回歸。
- 完整回歸、兩家獨立審查與整合清單見 `docs/tasks/CLEANUP-AUDIT-2026-11-05.md`；本文件不預填未完成的結論。
- 本輪未執行刪除、正式程式修改、基準變更、預測／提交、主賽啟用或 commit/push。
