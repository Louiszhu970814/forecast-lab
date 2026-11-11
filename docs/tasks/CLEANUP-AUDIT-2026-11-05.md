# 主賽前文件與 dead code 清理盤點（2026-11-05）

> **執行更新（D136）**：Louis 已授權，A01–A10 第一批已完成並套用，詳見 [D136 交付](D136-CLEANUP-BATCH1.md)。A09 的 `metadata_observer.MetaculusError` 證實仍被使用而保留，實際移除11個 import。B01–B09 尚未執行。下方原文保留盤點當時的狀態與證據。

本輪依 Louis「你們三個先看哪些該清、現在清或過一陣子清」的要求，提供具體清單。**只完成盤點與審查文件，未執行清理。** 規格仍以 PLAN v1.89 為準；不新增主賽啟用、窗口、額度或設定決定。

## 結論與建議順序

1. **現在先整理入口文件、矛盾敘述、斷鏈與測試隔離。** 這些已經影響接手與驗證可信度，比大量移動檔案優先。
2. **小範圍 dead helper／unused import 可以另開工程清理批次。** 大部分不必等 11/10；要遵守既有程式雙審、回歸與互斥部署規則。受驗收指紋保護的 adapter import 留至實際重驗維護。
3. **較大的舊 Telegram／M2／測試結構整理，先保存目前交付、抽共用函式，再退役或封存。** 以依賴解除與驗證完成為條件，不把日曆日期當刪除許可。
4. **固定設定、研究提示詞啟用、舊帳冊相容與核心安全程式分開處理。** 其中不少仍是必要功能，不能以檔名含 M1/M2/v1、沒有 importer、或測試失敗來判死碼。

## 範圍與三方證據

- 基準：HEAD `2f202bf` 加上目前 D132–D135 未提交工作樹；完整來源 [SHA manifest](../../reviews/cleanup-audit-20261105-source-manifest.json)。盤點時共 **335 檔、81 個 Python 來源模組／28,005 行、79 個測試檔、64 份任務 Markdown**。
- GPT：來源快照、AST／全文引用／CLI 入口／連結檢查、針對性閱讀及隔離測試；[直接核對紀錄](../../reviews/gpt-cleanup-audit-20261105.md)。這不是正式程式的逐行正確性證明。
- Claude：官方 `claude-opus-5-5`、high；第一輪原文（已於 D150 清理中間輪次）／[定點補審](../../reviews/claude-cleanup-audit-20261105-r2.md)。
- Gemini：官方 `gemini-4-argon-high`、high；第一輪原文（已於 D150 清理中間輪次）／[定點補審](../../reviews/gemini-cleanup-audit-20261105-r2.md)。**第一輪自報封包截斷，不能當完整覆蓋；第二輪兩家均確認讀到精簡封包的結尾標記。** 補審只補特定議題，不冒充完整重讀原封包後半部。
- 兩家第一輪收到相同 296,045 bytes 封包（SHA `27dd36a79428e0e13495dfad8c888b38317b983155f6b294d12d563474406713`）；第二輪聚焦遺漏與分歧，35,997 bytes（SHA `8267e2c70f116abe4a5a53143c2c2116d1fcad4bcda5b2e2fac45ea2e39923b6`）。兩家只讀提供的程式／文件證據，沒有自行執行測試；不冒充三人各自完整逐檔讀過 335 檔。
- 私有執行證據：`data/operations/cleanup-audit-20261105/`，保存來源盤點、CLI 原始結果、審查用量與測試輸出。沒有讀開放 bot 題預測或憑證；沒有模型預測或 Metaculus POST。
- [最終核對紀錄](../../reviews/cleanup-audit-20261105-verification.json)：原 335 檔中 332 檔 SHA 不變；其餘僅 PLAN 自己的 §14 審查追加與兩份索引追加。所有原程式／測試／config／prompt／launchd 不變，4 次實際官方 CLI 審查用量另行記錄；新報告相對連結檢查通過。

### 三方收斂與仍需區分的意見

- **共識**：優先修 Cup 規則文字、狀態／日期／索引；保留現行共用依賴、舊語法攔截、舊 outbox 取消、安全防線與歷史證據；先修測試隔離，再整理大模組。
- **時機修正**：Claude 第一輪偏向 runtime 清理等 11/10 後；補審兩家都接受 D126 允許不動固定設定的小工程批次在目前窗口處理。效益低的小清理是否併入下一個工程批次，是排程偏好，不是必須等日期。11/10 換窗本身不能混入大重構。
- **權限修正**：D91 已決定停用舊 TG 開發模式，不重問同一方向；本輪仍只提出具體退役清單，沒有執行刪除。
- **保護範圍修正**：D135 的 106 檔是該次部署「目標以外保持不變」清單，來源是 `data/operations/d135-quality-repair-20261105/apply_reviewed.py:45–46`；不能推成所有舊文件永久不可移動。實際驗收指紋、固定設定 hash、指示來源盤點仍各自遵守。
- **證據層級**：Claude 補審對未附原文的搜尋數字、斷鏈、操作腳本引用與本輪測試計數保留「未獨立核實」；這些由 GPT 直接核對並保存證據，沒有標成兩家都親自跑過。Gemini 第一輪的截斷與計數／路徑誤差保留原文，以本清單與補審更正為準（例如實際路徑為 `src/flab/fail_soft.py`）。
- **範圍限制**：這是清理盤點，不是對所有程式正確性、主賽就緒或清理後實際 diff 的 PASS；未來 runtime patch 仍要做既有完整審查。

## A. 現在可以安排的項目

「現在」表示不需等新題結算或預算換窗；不表示本輪已修改。純文件與小範圍 runtime 清理分開提交／驗證。

| ID／優先 | 對象 | 已核對問題與建議動作 | 完成條件 |
|---|---|---|---|
| A01／最高 | `AGENTS.md` 硬性規則 2 | 仍列 Metaculus Cup 為 bot 試驗場地，與 PLAN §3.1／D79「Cup 不再是試驗場地」相反。更正共同守則，保留已核准 live 與人類賽邊界。 | 依 PLAN 既有決定對齊；核對實際載入／基準盤點範圍，不臆測所有專案文件都受指紋保護。 |
| A02／高 | `README.md:5` | 仍為 11/3、v1.78；目前 v1.89，已加入 M4、三線修復、人類參考、閉題練習與健檢。更新摘要，讓詳細即時狀態指向 HANDOFF／status。 | 與 PLAN 最新正文／決策一致；不要維護多份互相矛盾的完整現況。 |
| A03／高 | `docs/tasks/INDEX.md`、`M4-RESOLVE-SCORE.md`、`D128-LIVE-ITT.md`、`D129-ITT-IMPLEMENTATION.md`、HANDOFF | 部分「首題 11/06 21:00」尚未標成歷史；D132 已更正已送出正式批次最早**預定**台北 11/13 20:00、最晚 11/14 12:59。更新現況欄，舊紀錄追加更正來源。 | 不改寫歷史為當時已知；預定結算與已結算分開。 |
| A04／中 | `docs/tasks/INDEX.md`、`reviews/INDEX.md` | D135 在表格外，HANDOFF 範圍未跟上；D132 審查索引不全；「早期 r1 已清理」與現存多份 r1 不符。補齊索引，區分功能啟用、未完成工作與結案證據。 | 檔案存在、相對連結可達，狀態和最新交付一致。 |
| A05／高 | `M3-V15-SCHEMA.md:90`、`M3.md:13/44`、`MINIBENCH-READINESS.md:98`、`OVERNIGHT-D84.md:89/141` | **6 處斷鏈**指向 `392335b` 已刪的 `DECISIONS-AFTER-D84.md`。逐筆對照原決策後改連歷史來源／建立導覽 stub。 | 不復活過時規格為現行指令。掃描找到的 `PLAN.md:1306` `(...)` 是示例，不算第七處斷鏈。 |
| A06／中 | `M3-MC-READBACK-D99.md`、`TELEGRAM-DEV.md`、`FAIL-SOFT-RELAX-PROPOSAL.md`、`M3-V15-SCHEMA.md` | 原頁缺醒目的已取代標記；fail-soft 提案仍稱待決／尚未寫入 PLAN。補狀態、取代來源與保留理由。D117、D98 已有後續說明，不重做。 | 直接開原頁也不會把舊草案當現行規格。暫時保留原路徑。 |
| A07／低 | `launchd/.gitkeep`、`prompts/.gitkeep` | 目錄已非空，兩個占位檔無實際用途。 | 目錄仍有受追蹤檔案即可；不需要另建測試。 |
| A08／低 | `src/budget.py:106 _positive`、`src/forecast.py:733 _fraction` | 整份快照全文只出現定義，未見呼叫、測試或字串派送，是目前**高信心 dead helper 候選**。可做小批刪除。 | 先保存 D132–D135 原修改，檢查工作區外維護腳本引用；相關回歸、既有雙審、六鎖套用。不得順便改預測行為。 |
| A09／低 | 下表 12 個非 adapter 未使用 import | 可減少噪音；與 A08 合併小批處理，不必為此移動整個模組。 | 排除公開轉出、monkeypatch 與維護腳本屬性存取；按實際影響回歸。 |
| A10／高 | `tests/run_offline.py`、`offline_workcopy.py`、`test_config.py`、`test_resolve.py` 及 M5 相關舊測試 | 驗證入口受到正式 `live`／題型設定與 temporary directory 的影響；部分測試仍寫「numeric waits for M5」。先修隔離與契約，再判定哪些案例確實退役。 | 不改正式 `config/` 求 PASS；保留案例目的、記錄收集數與失敗節點；失敗不等於可刪。詳見測試段。 |

**A09 的具體 import 清單**（12 個名稱、11 檔）：

| 檔案 | 候選 |
|---|---|
| `src/auto_reaccept.py` | `hashlib`、`os` |
| `src/comment.py` | `math` |
| `src/continuous.py` | `new_id` |
| `src/flab/team_chat.py` | `json` |
| `src/forecast.py` | `Iterator` |
| `src/m2_supplement.py` | `transaction` |
| `src/metadata_observer.py` | `MetaculusError` |
| `src/quota.py` | `MODEL_CHECKS` |
| `src/reconcile.py` | `read_records` |
| `src/resolve.py` | `Mapping` |
| `src/scheduler.py` | `latest_snapshots` |

另有第 13 個 `src/adapters/gpt.py:10 Path`，移至 B08；不能因只是 import 就忽略驗收指紋。

## B. 延後至條件滿足再清理

| ID | 對象與建議 | 為什麼現在不直接刪 | 何時可以做 |
|---|---|---|---|
| B01 | 瘦身 `PLAN.md` §14 舊審查、HANDOFF 舊交接；早期 review 分批封存 | PLAN 436,878 bytes／2,109 行；§14 占 139,308 bytes／577 行。適合歷史分檔，但 D132–D135 尚未提交，且原鏈結／作者／時間／來源要保留。 | 先形成可恢復的交付基線，由整合者遷移並保留原位索引與 anchors；不需等結算。§15／§16 仍保持規格與決策主線。 |
| B02 | 退役舊 Telegram DevWorker、TaskStore 啟動路徑、`dev_command`／`enqueue_dev_notifications`／`enqueue_due` | D91 已停用舊開發模式；`dev_*` 入口先導向 `team_help`，後方分支不可達。但共用 helper、舊指令攔截、舊通知取消與測試仍在使用。 | 先抽 `clean_env/codex_binary/DevError`、`TaskError/safe_text/public_text/SECRET`，保留舊語法攔截與 outbox 相容；跑 TG 問答／決策／續作及舊事件重播，再於空檔部署／必要時重載。不用重問 D91 是否已停用。 |
| B03 | 共用測試 helper 移至 `tests/helpers/` | **45 個測試檔有 123 個跨測試 import 節點**；例如 numeric pipeline、advisory、practice 都借用舊測試的 builder。整檔刪除會連帶破壞收集與安全 fixture。 | 先完成 A10 的可重現基線，再逐群抽離。保留 fixture scope、autouse、參數化、案例 ID／數量，尤其真實資料與憑證防護。 |
| B04 | `m2_prepare.py`、`m2_supplement.py`、`m2_report.py` 整理成 legacy／手動工具 | 仍有 CLI／歷史報表與測試；`m2_campaign.discover_tournaments` 被 `continuous.py` 使用，`SafeSnapshots` 被 scheduler 使用。 | 先抽共用場地探索／安全快照、保留薄轉接；證明維護腳本及舊資料重播已有替代後，才能退役舊工具。主賽前沒有必要為名字大搬檔。 |
| B05 | `acceptance.argon_acceptance_gate`／`ARGON_CALL_ESTIMATE`、`numeric.cdf_fields` | 前者現在只被舊測試呼叫，正式 `_run` 用 `quota.manual_admission`；後者被 numeric 測試 builder 使用。 | 先移轉必要測試覆蓋到現行准入路徑／共用測試 helper；再評估刪除或搬移。這兩者目前不能稱零使用。 |
| B06 | `launchd/run.sh`、`org.forecast-lab.resolve.plist`、`org.forecast-lab.report.plist` 的未啟用範本 | `report` 指向不存在的 `src/report.py`；resolve 已有手動入口，但排程未啟用。這是未實作／未啟用入口，不是已證明 dead code。 | 現在可更正說明；之後決定移至 templates、明確拒絕誤裝或另行實作。清理不安裝或啟用排程。 |
| B07 | `continuous_v2–v9`、預算接續、V15 舊格式、TG 舊 outbox、score 舊資料相容 | 舊帳冊永久存在；這些分支可能仍負責對帳、扣額度、跳過探針與復原。到期不會讓歷史依賴自動消失。 | 有版本使用清單、完整重播／復原證據、在途與 unknown 收斂、明確退役方案才可縮減。D118 截止 UTC 11/10 04:00 本身不構成可刪條件。 |
| B08 | `src/adapters/gpt.py` 的未使用 `Path` import；任何 adapter／runner／netguard 純整理 | `forecast.runtime_fingerprint` 精確綁定每條線的 adapter 及 `flab/runner.py`、`flab/netguard.py`。一行改動也會改 SHA。 | 留至實際安排的隔離重驗維護批次；低價值 import 清理不值得單獨打斷已驗收線。 |
| B09 | `src/flab/fail_soft.py` 與 M5 內嵌設定常數遷入設定檔；D135 候選 prompt 啟用 | 是 D118 固定設定下有意保留的版本化實作，仍在執行；不是死碼。正式 prompt 變更會改變預測行為。 | 新事前登記窗口，或另經核准並完整驗證的有界 handoff；遵守版本／§16 記錄。換窗批次不夾帶大重構，不因清理自動啟用候選。 |

## C. 保留：容易被誤判但仍有用途

| 對象 | 具體保留理由 |
|---|---|
| `m2_campaign.py` | `continuous.py:18/163` 用場地探索；`scheduler.py:153/584/606` 用 SafeSnapshots。 |
| `flab/dev_runner.py`、`flab/dev_tasks.py` 的共用部分 | `team_runner.py:18`、`team_chat.py:15`、`project_control.py:6` 與 Telegram 都有依賴。刪整檔會壞現行服務。 |
| `claude_baseline_candidate.py` | 非 tracked src importer 也有真實維護使用：D130 `repair_claude.py:23/242` 匯入並呼叫 `approve`。 |
| `auto_reaccept.py`、`flab/auto_reaccept_policy.py` | `launchd/run.sh:27` 是現行 observe 接點；enforce 未實作不能推出 observe 無用。 |
| `flab/schema.py`、`flab/__init__.py`、框架 callback | validate.py 以 `from flab import schema` 使用；28 個表面 unused import 是 `__all__` 公開轉出；HTMLParser、HTTPServer 的 hook 由框架呼叫。 |
| `repair_v1.md`、`repair_v2.md` | forecast.py:132 依 tier1／tier2 分派，兩份皆在用。config 檔名 v2 同樣不能視為過時。 |
| `docs/experiments/d135-research-prompts/` | 4 份候選尚待下個獨立窗口驗證；標明未啟用即可，不能因未使用就刪。 |
| `acceptance`、`autoload`、`v15_probe`、`m5_probe_session`、coverage／metadata observer／replay、M4／practice／project_control | 手動驗收、復原、重播、計分或操作 CLI 不要求有一般 importer；其中部分仍屬 M6 驗證工具。 |
| M1 受引用文件、M2／M3／M5 驗收報告、最終審查、source manifests | 保留可核對的依據。需要封存時留原路徑導覽，不為搬文件修改驗收指紋內的 docstring。 |
| `data/*.jsonl`、原始 evidence、`data/cli_pins`、重要 fixtures、D132–D135 未提交內容 | 不是垃圾；關係到只追加歷史、實際用量、恢復、防重送與驗收。不得用通用「清 cache／刪 data」處理。 |

## 測試盤點：需要修隔離與舊契約，不能用刪測試取得 PASS

本輪在專案外複本執行，未改正式設定或測試原碼。下表三種紀錄**不可混成同一基線**：

| 執行方式／來源 | 結果 | 解讀 |
|---|---|---|
| D135 留存完整結果 | 3251 passed／100 failed | 原有證據；不是本輪重跑。 |
| 本輪 `tests/run_offline.py -q --tb=short` | **3241 passed／110 failed** | 原 100 失敗節點全部重現，另多 10 個；新增集中 advisory 1、practice 6、workdir 3，明顯涉及 runner 把 TMPDIR 放進工作副本而與「attempt 必須在專案外」防線衝突。尚未逐一證明全部根因。 |
| 本輪加 `--synthetic-host` | **3317 passed／34 failed** | 27 個涉及 M5 已啟用後的舊斷言／mock；7 個是 synthetic fixture 與 test_config／test_resolve 的目錄假設衝突。這不是把 110 或 100 個失敗修掉。 |
| 本輪 TG 相關三檔、synthetic host | **77 passed** | 現行與舊方法單元覆蓋可執行；不證明舊 DevWorker 正在服務中啟動。 |

具體可修的測試漂移：

- `test_main_site_trial.py:57` 的假 API 固定只接受 `binary,multiple_choice`，但正式 M5 已開啟 numeric/discrete；導致 25 個案例提早卡在相同斷言。
- `test_poll_questions.py:93` 仍寫 `numeric waits for M5`；`test_cycle.py:368` 的刷新結果仍期待排除 numeric。需要明確測「開／關」兩種題型設定，不能依賴目前正式開關。
- `test_config.py:97` 手動呼叫 SyntheticHost，而全域 plugin 已自動建立同名目錄，產生 `FileExistsError`。
- `test_resolve.py:883/907` 假設 tmp_path 只有檔案或是空目錄；全域 SyntheticHost 放入子目錄後，出現 `IsADirectoryError` 或空目錄斷言失敗。
- plain 模式有多項 `M2 scheduler only runs in local mode`／`supplement requires local mode`；隔離測試不應讀到真實 `live` 模式造成契約錯置。

建議獨立做「測試環境與舊斷言整理」批次，保存 3351 個案例的 collection、失敗節點、變更原因與必要替代覆蓋。**本輪兩種全套都不是 PASS；不能宣稱主賽前驗證已全綠。**

## 執行界線與後續交付

本輪只新增審查／清單／證據索引及 GPT §14 審查紀錄。原程式、測試、設定、prompt、launchd 與歷史交付保持原樣。未 commit／push；審查者不代替指定協調者提交。

下一批建議先做 A01–A06、A10；A07–A09 為低風險小批。B 類在各自條件完成後分批處理。完成清理也不取代 PLAN 的主賽前置條件。
