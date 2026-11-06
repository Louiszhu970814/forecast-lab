# Forecast Lab 過時文件／死碼獨立盤點：Claude 審查報告

## 0. 範圍與限制
- **這份報告只做盤點**：不刪檔、不部署、不 commit，也不核准任何事。這裡的 NOW／LATER 是分類建議，不是授權。是否清理、何時清理，仍由 Louis 決定，並依 AGENTS 規則與 PLAN §14/§15 流程處理。
- **我看過的範圍**：只看了來源封包裡的這些內容：AGENTS、README、docs/tasks/INDEX、reviews/INDEX、HANDOFF 部分段落、PLAN 節錄（§4.3、§7.2、§7.6、§10、D118–D135、§16）、11 個 src 檔的全文或片段、run.sh、3 個測試檔片段，以及靜態盤點 JSON。快照共 335 個檔案，我沒有逐檔看完，所以這不是全專案原始碼審查。
- **我沒有做的事**：沒有執行任何測試、grep 或 git 指令。下文的「證據」都是封包裡的行號或盤點欄位；「推論」會另外標明。
- **測試基線**：先前的證據是 3251 passed／100 個既有失敗。這不是本輪重跑的結果，也不代表全套 PASS。封包沒有附那 100 個失敗節點的清單。
- **靜態盤點的定位**：AST 盤點只是產生候選的工具，不能當死碼證明。它已知有 from-package 匯入的盲點，例如 scheduler.py:36 的 `from flab import fail_soft as FS` 沒有算進 flab.fail_soft 的 importers。它也看不到動態匯入、函式內延遲匯入、pytest fixture、字串路徑載入（prompts、config/team_actions），以及非 Python 的呼叫端（launchd）。
- **D126 已刪除的 29 份文件**：本報告沒有再提。

## 1. 分類定義
- **NOW**：不需要等任何事件，可以排進下一個 Louis 授權的清理批次。仍要先確認目標檔不在 D132–D135 未提交變更裡。
- **LATER**：要等到列出的前置事件發生後才處理。
- **KEEP**：有證據顯示還在使用，或屬於安全、相容、稽核用途，不應清理。
- **UNPROVEN**：證據不足，需要補證據才能分類。

共用前置事件代號：
- **E1**：D132–D135 未提交變更已依 Louis 指示 commit。
- **E2**：D118 窗口於 UTC 2026-11-10 04:00 結束，而且階段 C 換窗完成並放鎖。HANDOFF 規定換窗批次不夾帶重構。
- **E3**：HANDOFF 階段 B′ 第 3 項（共用函式抽離）經雙審並部署。動到 team_runner 或 continuous 需要六鎖。
- **E4**：Louis 明確決定 D73 Telegram 開發模式不再使用。
- **E5**：完整搜尋確認零引用。搜尋範圍包括 PLAN、src docstring、reviews 與 manifest、launchd plist、config/team_actions、tests。
- **E6**：測試基線重新登記（收集數加上 100 個失敗節點）。
- **E7**：階段 D（11/13–14）的 M6 就緒度評估完成。
- **E8**：R2-C enforce 有了實作或放棄的決定。

## 2. 優先風險（若誤刪會造成實害）
1. **整個刪掉 src/flab/dev_runner.py 會弄壞 Telegram 問答和已登記續作。** team_runner.py:18 匯入 `clean_env, codex_binary`，用在 89、180、197 行。
2. **移除 telegram_bot.py:188 的 parse_dev_command 會讓開發指令變成付費問答。** 「開發：…」這類文字會落到 284–285 行，被當成 GPT 問答，消耗模型額度。
3. **adapters/*、flab/runner.py、flab/netguard.py 一行都不能改，包括 unused import。** 這些檔案是驗收指紋（forecast.py:72 的 RUNTIME_FILES，PLAN §7.2，D121）。改動就要重驗；D118 live 期間會造成停線。
4. **測試裡看似沒用到的 import，有些其實是 fixture。** 例如 `_no_real_data`、`no_token_in_any_test_ledger_or_console`。刪掉會拿掉防止讀到真實資料或 token 外洩的保護。
5. **m2_campaign.discover_tournaments 名稱像舊碼，實際在 live 路徑上。** 它由 continuous.py:18、163 使用。
6. **刪除或改動測試會破壞「3251/100 不變」的比對方法。** 需要先做 E6。
7. **launchd/run.sh 的 resolve 分支可以直接執行 resolve.py。** 這和 D127「不啟用排程」的意圖可能衝突（下文 F07）。

## 3. 盤點表

| # | 檔案／符號 | 證據（封包內）與推論 | 分類 | 前置事件與理由 | 對該清理的阻擋性 |
|---|---|---|---|---|---|
| F01 | telegram_bot.py `Service.dev_command`(388–402)、`enqueue_dev_notifications`(404–417)、`handle` 第 306–307 行分支、293 行集合中的 dev_* | **證據**：282–283 行先把 `dev_*` 改成 team_help，所以 306 行進不去。poll_loop(474–498) 沒有呼叫 enqueue_dev_notifications。run() 462 行註解寫明 no old task worker。INDEX 標示 TELEGRAM-DEV 已由 D91 取代。**推論**：正式路徑上這些是死碼。 | LATER | 需要 E4、E1、E5、E6。tests/test_dev_runner.py:226 還在用 `bot.tasks.submit`。重啟 Telegram 服務要選在沒有進行中問答的空檔。 | 非阻擋 |
| F01-K | telegram_bot.py:188 `parse_dev_command`、437–439 行的 `dev_notice:` 取消分支、260 行的 `self.tasks` | 188 行把開發指令導向 team_help，防止誤耗額度。437–439 行把舊帳冊裡的 dev_notice 標成 cancelled，屬於舊帳冊相容。 | KEEP | 安全導流與舊帳冊相容。AGENTS 規定 JSONL 只追加。 | 若有人提議移除則為阻擋 |
| F02 | flab/dev_runner.py：`DevWorker`、`snapshot`、`export_changes`、`sandbox_probe`、`prepare_cli`、`build_argv`、`task_prompt`、`permission_config`、`file_bytes`、`eligible`、`DEVELOPMENT_RULES`、`SUPPORTED_CLI` | src 裡唯一匯入者是 team_runner:18，而且只匯入 clean_env 和 codex_binary。DevWorker 只出現在測試（test_dev_runner:227 註明 offline legacy worker coverage）。這個檔沒有 `__main__`。DEVELOPMENT_RULES 和 task_prompt 寫的「只在 GPT §14.2 留意見、由 Claude 整合」已和 AGENTS 的整合者規則不一致。 | LATER | 需要 E4，再加上 E3：先抽出 clean_env 和 codex_binary 才能移除其餘部分。也需要 E6。 | 非阻擋 |
| F02-K | dev_runner.py `clean_env`(58–70)、`codex_binary`(50–55) | Telegram 問答 `answer_question` 和續作 `run_action`(190–197) 都靠它們隔離環境變數。 | KEEP | 要等 E3 抽離後，舊路徑才可改成轉接 wrapper。 | 阻擋（不可直接刪） |
| F03 | flab/dev_tasks.py：`TaskStore`、`DEV_HELP`、`STATES`、`STAGES`、`describe`、`list_text` | `TaskError`、`safe_text`、`public_text`、`SECRET` 仍被 team_chat:15、telegram_bot:345、dev_runner:18 使用。project_control:6 從這個模組匯入，但封包沒顯示是哪個符號。 | 模組 KEEP；TaskStore 等 LATER；project_control 依賴 UNPROVEN | 跟 F01、F02 一起處理，需要 E4。data/telegram_dev 的歷史帳冊保留。 | 非阻擋 |
| F04 | telegram_bot.py 定期報告殘留：`enqueue_due`(419–430)、`/daily`、`/weekly` 解析(196–213)、`Schedule.latest_slot` | **證據**：poll_loop 沒有呼叫 enqueue_due。set_schedule 不管輸入是什麼都寫入關閉（304 行）。run() 463 行強制 workgroup_mode 關閉。**推論**：正式路徑上 enqueue_due 已不會執行。另外 441–445 行會取消舊帳冊裡排定的訊息，`State.apply` 也會重播 schedule，兩者都是相容邏輯。 | enqueue_due 為 UNPROVEN→LATER；441–445 行與 `Schedule.label` 為 KEEP | 需要 E5（確認測試有沒有直接呼叫 enqueue_due）和 E6。另一個體驗問題是：/daily 21:00 會被解析，但回覆「定期報告關閉」，容易誤導。這是 UX 問題，不是安全問題。 | 非阻擋 |
| F05 | src/m2_campaign.py `discover_tournaments`、`SafeSnapshots` | continuous.py:18、163 的 current_tournaments 是 D118 live 探索路徑。scheduler 在 153、584、606 行以函式內延遲匯入使用；m2_prepare 和 m2_supplement 也會用到。 | KEEP | 「m2_」這個名稱容易讓人誤判。HANDOFF B′ 的抽離屬於重構，要等 E2 之後，而且動到 continuous 需要六鎖。 | 若被列為死碼則為阻擋 |
| F06 | M2/M3/M5 歷史 CLI：m2_prepare、m2_report、m2_supplement、capacity_report、v15_probe、m5_probe_session、coverage_sim、metadata_observer、metadata_replay、cycle_monitor | 盤點標為 entrypoint=true，而且都有測試匯入。主賽就緒評估可能需要 coverage_sim 重播主賽覆蓋率（PLAN §10 M3 第四項）。v15、m5 探測和 D107 A1 常設有界寫入授權有關。cycle_monitor 只有測試匯入。 | KEEP；cycle_monitor 為 UNPROVEN | 需要 E7，加上 Louis 決定不再需要重現這些證據之後，才考慮「歸檔」而非刪除。cycle_monitor 要查 launchd plist 或 project_control 是否呼叫。 | 非阻擋 |
| F07 | launchd/run.sh 第 14–15 行 resolve／report 分支與註解 | 註解寫 Pending later M3 work，但 resolve.py 已存在（D127/D129 只上線手動入口）。81 個模組的盤點裡沒有 src/report.py，HANDOFF 階段 D 卻計畫執行 report.py。**推論**：如果有人替 resolve 安裝 plist，會不帶參數執行 resolve.py。 | LATER | run.sh 是 D124 已部署的週期接點，必須在維護窗口改並經審查。先用 E5 確認有沒有 resolve 或 report 的 plist，也要確認它是否在受保護清單裡。 | 非阻擋，但建議在 E2 後優先處理 |
| F08 | src/flab/__init__.py 的 33 個「未使用 import」 | 這些是透過 `__all__` 再匯出的符號。任何 `from flab.x import` 都會執行這個 package init，它也會載入 flab.runner。這正是 from-package 盲點。 | KEEP | 移除會改變 import 時的副作用，也會破壞 `from flab import X` 的公開 API。 | 阻擋 |
| F09 | importers=[] 但實際有在用：adapters.claude、adapters.__init__、flab.netguard、flab.auto_reaccept_policy、flab.health_diagnostics、flab.fail_soft | auto_reaccept.py:21 用 `from flab import auto_reaccept_policy`（盲點）。auto_reaccept.py:58 用 importlib 動態載入 adapters.gpt。forecast.py:150 以路徑字串對 adapters 取指紋。flab.fail_soft 有遺漏的 importer（scheduler:36）。health_diagnostics 是 D135 剛上線的 health_diagnostics_v1。 | KEEP | 其中 adapters 和 netguard 屬於 PLAN §7.2、D121 的驗收指紋。 | 阻擋 |
| F10 | flab/schema.py（105 行，importers=[]） | 盤點沒找到匯入者，但可能是 `from flab import schema` 或由測試匯入（盲點）。 | UNPROVEN | 需要 E5。 | 非阻擋 |
| F11 | 執行期檔案的 unused import：src/adapters/gpt.py:10 `Path` | adapters 在驗收指紋內。改動就要重驗，D118 期間會停線（HANDOFF 167 行禁止）。 | KEEP（不要動） | 屬於 PLAN §7.2、D121、D119。 | 阻擋 |
| F12 | 執行期檔案的 unused import：auto_reaccept.py:8 `hashlib`、:11 `os`；continuous.py:14 `new_id`；forecast.py:25 `Iterator`；comment.py:9 `math`；resolve.py:37 `Mapping`；m2_supplement.py:23 `transaction`；reconcile.py:27 `read_records`；team_chat.py:9 `json` | AST 候選，數值低。scheduler.py:35 `latest_snapshots`、metadata_observer.py:24 `MetaculusError`、quota.py:18 `MODEL_CHECKS` 可能是測試 monkeypatch 的目標，或被當成 `模組.X` 屬性存取。 | 前三者 UNPROVEN，其餘 LATER | 需要 E2，再用一個不和換窗合併、單獨審查的批次處理，同時需要 E5 和 E6。行為不變，所以依 PLAN §6.3 推論不需升 prompt 或 pipeline 版本，但部署仍要走互斥程序。 | 非阻擋 |
| F13 | 測試中的 fixture 型 import：test_api_readback:12、test_exclusion_complete:15、test_numeric_pipeline:28 的 `no_token_in_any_test_ledger_or_console`；test_quota_ultra:22 `_no_real_data`；test_rollover:16 `environment`；test_autoload_maintenance:19 `_loaded_executor`；test_m5_probe:21 `reuse` | pytest fixture 是靠參數名稱或 autouse 生效，AST 一律判成未使用。從命名推論，前兩者是 token 外洩與真實資料存取的保護。 | KEEP | 其中 `reuse` 是否為 fixture 待 E5 確認。 | 阻擋 |
| F14 | 測試中的一般 unused import（json、copy、Path、hash_obj 等，例如 test_dev_tasks:3–4、test_reconcile:6、10、16） | 價值很低，屬於純測試檔。 | NOW（可選，低價值） | 建議併入 B′ 第 2 項 tests/helpers 重構，一起做 E6 的收集數與失敗節點比對。 | 非阻擋 |
| F15 | 跨測試互相 import（約 120 處，例如對 test_forecast、test_scheduler、test_submit、test_telegram_bot 的依賴） | 屬於結構耦合，不是死碼。test_dev_runner 和 test_dev_tasks 依賴 test_telegram_bot 的 FakeAPI 和 project。 | LATER | 依 HANDOFF B′ 第 2 項，需要 E6。若進行 F01、F02 的刪除，要一併規劃。 | 非阻擋 |
| F16 | 低引用符號：唯一出現就是定義本身的 `budget._positive`(budget.py:106)、`forecast._fraction`(forecast.py:733) | lexical 命中只有 1 次，也就是定義行。**推論**：可能是真死碼，但仍要排除 getattr 這類動態存取。 | LATER | 需要 E2、E5、E6。budget 和 forecast 是執行期核心，要單獨審查並走互斥部署。 | 非阻擋 |
| F17 | 低引用的安全閘門與 M5：`acceptance.argon_acceptance_gate`(686)、`numeric.cdf_fields`(277) | 兩者 src 內只有定義，只被測試使用。argon gate 是個人 Argon 保留額度的閘門，可能已被 708 行的 quota.manual_admission 取代，但封包沒有決策證據。cdf_fields 可能和 M5 金標準 fixture 有關。 | UNPROVEN（暫 KEEP） | 要有 PLAN 決策或審查證明它已被取代，再加上 E5。安全後備不能只憑 lexical 命中刪除。 | 若提議刪除則為阻擋 |
| F18 | 舊帳冊相容：forecast.py:62–67 `LEGACY_MISMATCH_PIPELINES`／`_legacy_mismatch`、telegram 的 State.apply 重播、score 把 V15 探針列為 skipped（D127） | 移除會讓歷史紀錄被重新解讀。舊版 mismatch 原本的停線意義會消失（D121 註解）。 | KEEP | 依 AGENTS 只追加、PLAN §4.3 設定分段、D127。 | 阻擋 |
| F19 | 其他 KEEP：claude_baseline_candidate.py（只有測試匯入、沒有 CLI）；tests/run_offline.py、tests/offline_workcopy.py | 前者是人工核准外掛候選的工具（D119/D120），之後 D130 的三天版本節奏可能還會用到；D131 是否改用其他路徑，封包沒證據（UNPROVEN）。後兩者是測試安全工具：audit hook 擋住讀取 data、.env 和呼叫模型 CLI。`.d121-checks` 只是命名歷史，不是死碼。 | KEEP | 要確認 D131 的接受路徑後，才重新評估 claude_baseline_candidate。 | 若提議刪除則為阻擋 |
| F20 | README.md 第 5–12、28 行 | 寫的是「截至 2026-11-03、v1.78」，現在是 v1.89。缺 D127–D135：M4 手動入口、D132 人類賽參考、D133 閉題練習、D135 健檢。28 行有簡體字「与」。 | NOW | 只動文件。要先確認不在 D132–D135 未提交範圍內。README 不在 AGENTS 受保護雜湊的已知範圍，但需確認。 | 非阻擋 |
| F21 | docs/tasks/INDEX.md | 第 3 行 D135 條目在說明文字和表格之外。M4-RESOLVE-SCORE 與 D129 的「首題實戰結算待台北 11/06 21:00」和 HANDOFF 39 行、PLAN D132 的更正衝突：10 道正式題最早 11/13 20:00 結算，11/06 只是 M4 開發驗收。MINIBENCH-EARLY-D118 仍標 ACTIVE。 | 日期更正與格式為 NOW；D118 改 CLOSED 為 LATER | D118 要等 E2。D121-BATCH2-PREPARATION、D124-BATCH2-SPEC 要等 E8。 | 非阻擋 |
| F22 | INDEX 中 SUPERSEDED 的文件 | MINIBENCH-LIVE-D117.md 被 PLAN §10 第 992 行直接引用；FAIL-SOFT-RELAX-PROPOSAL.md 被 PLAN D121（1818 行）引用。TELEGRAM-DEV.md、M3-MC-READBACK-D99.md、PLATFORM-CLUES-D98.md、M3-V15-SCHEMA.md 的引用情況封包沒有涵蓋。 | 前兩份 KEEP；後四份 UNPROVEN | 需要 E5。TELEGRAM-DEV 要等 F01、F02 的程式處理後再一起評估，否則留下的程式碼會沒有規格文件。 | 前兩份若提議刪除則為阻擋 |
| F23 | reviews/INDEX.md 與 SUPERSEDED 審查檔 | 第 5 行說「早期 r1 過渡報告已清理」，表格卻列了多份 r1、r2、r3 SUPERSEDED，自相矛盾。HANDOFF 提到 D132 的 Claude r2／Gemini r3，INDEX 沒收錄。D135 條目在表格外。SUPERSEDED 審查檔可能被 d129、d130、d133 的 source-manifest 引用。 | 改說明與補索引為 NOW；刪除審查檔 UNPROVEN | 補 D132 前要先確認檔案存在。刪除需要 E5 掃過 manifest。 | 非阻擋 |
| F24 | docs/tasks/HANDOFF.md 與 PLAN §14 拆分 | 檔內多段標成「歷史」。階段表 B 段（11/05–11/06 的 budget_handoff_v2、qid=46289）沒有寫目前完成狀態。第 44–91 行不在封包內。 | LATER | 要等 E1，以及 11/06 M4 驗收的實際結果。§14 拆分依 B′ 第 1 項放在 11/07–11/09，前提是不影響 AGENTS 受保護雜湊並保留 §15 編號。 | 非阻擋 |

## 4. 對靜態結果的反駁（不能直接當死碼）
- **CLI entrypoint**：project_control（AGENTS D91）、auto_reaccept（run.sh:27）、resolve 和 score（手動入口）的 importers 很少或為零，但都在使用中。
- **動態與延遲匯入**：auto_reaccept.py:58 的 importlib；team_chat.py:376 在函式內匯入 team_runner；scheduler 在 153、584、606、607、608、609 行的函式內匯入。
- **from-package 盲點**：scheduler:36 的 fail_soft、auto_reaccept:21 的 policy、flab/__init__ 的再匯出。
- **字串路徑載入**：forecast 的 `_template('repair_v2.md')` 等 prompts；runtime_fingerprint 以路徑讀 adapters；team_chat.action_spec 以腳本路徑執行 config/team_actions/*.py（不經 import）；PLAN §7.2 的 data/cli_pins 與 launchd/bin wrapper。
- **低引用清單多是假陽性**：大部分是「定義＋1 次呼叫」的私有 helper（例如 post_close_audit._gate2、_gate3），正常使用中。只有 F16、F17 是「只有定義」或「只在測試裡用」。

## 5. 遺漏（盤點工具或流程沒涵蓋）
1. **非 Python 檔不在模組圖裡**：config/team_actions/*.py、prompts/*.md、launchd/*.plist、launchd/bin、docs/experiments/d135-research-prompts。最後一項是 D135 尚未啟用的研究提示詞候選（PLAN §4.3 第 314 行），屬於 KEEP。
2. **src/report.py 不存在，卻被 run.sh:15 和 HANDOFF 階段 D 引用**。需以 git ls-files 確認。
3. **沒有那 100 個既有失敗節點的清單**，所以不知道 dev 或 schedule 相關測試是否在其中。例如 test_dev_runner.py:238 斷言「無法確認 M2／M3」，這是 UNPROVEN。
4. **project_control.py:6 從 dev_tasks 匯入哪個符號，封包沒有顯示。**
5. **範圍外觀察（不屬於死碼）**：dev_runner.py:23 和 team_runner.py:91 把 `codex-cli 0.166.0` 寫死。D130 三天更新節奏若升級 Codex，Telegram 問答會失敗。team_runner 的 claude 和 agy 用的是全域 CLI，不是 pinned 版本。問答不是預測，不受 D119 pin 約束，但建議另外確認是否符合預期。

## 6. 分歧
- **和靜態盤點的分歧**：見第 4 節。我的判斷是 importers=[] 的模組沒有一個能直接列為 NOW 刪除。
- **和 HANDOFF B′ 第 3 項**：B′ 計畫把 clean_env 和 codex_binary 抽出來並保留轉接 wrapper。我認為應該先由 Louis 決定 D73 開發模式是否不再使用（E4）。若不再使用，抽完後 DevWorker 整組可以進 LATER；若保留，抽離的必要性就低很多。順序對了才不會白做重構。
- **文件之間的分歧**：
  - README 寫 v1.78，PLAN 已到 v1.89。
  - INDEX 和 D129 寫 11/06 首題結算，D132 已更正為正式題最早 11/13。
  - reviews/INDEX 的說明文字和表格內容不一致。
  - 這些都只是文件，修正不會影響執行期。
- **預期和其他審查者可能不同的地方**：m2_*、claude_baseline_candidate、argon_acceptance_gate 這幾類，看名稱或引用數很容易被判成死碼。我傾向 KEEP 或 UNPROVEN。

## 7. 可寫入 §14 的審查意見草稿（只是提議，不是新的核准閘門）
- [§7.2／D121] 任何清理批次都排除 adapters/*、flab/runner.py、flab/netguard.py，包括 unused import｜改動驗收指紋就要重驗，D118 期間會停線｜對該清理項 blocking
- [§3.3／D91] 移除 Telegram 開發模式時保留 parse_dev_command 導流和 dev_notice 舊帳冊取消邏輯｜否則開發指令會變成付費問答，舊帳冊也會被重新解讀｜對 F01 blocking
- [§5.2／D126 階段 B′] 先由 Louis 決定 D73 是否不再使用，再抽出 clean_env 和 codex_binary，最後才移除 DevWorker｜team_runner 的問答與續作依賴這兩個函式｜對 F02 blocking
- [§4.3／D126] 刪除或修改測試前，重新登記收集數與 100 個失敗節點｜維持「失敗節點不變」比對方法有效｜non-blocking
- [§10／D132] 修正 INDEX 和 D129 摘要裡的 11/06 首題結算字樣｜避免誤導 M4、M6 的時程判斷｜non-blocking
- [§10／D127] 在 E2 後的維護窗口確認或移除 run.sh 的 resolve 與 report 分支｜report.py 不存在，resolve 不應被排程誤用｜non-blocking
- [§6.3] 只刪死碼、不改行為的批次，在 §16 記為工程清理，不升 prompt 或 pipeline 版本｜避免設定分段被誤切｜non-blocking

## 8. 要把 UNPROVEN 改成定論還需要的證據
1. 全專案文字搜尋，對象是：flab.schema、`from flab import schema`、enqueue_due、cdf_fields、argon_acceptance_gate、四份 SUPERSEDED 文件名、各 review 檔名。
2. launchd plist 清單，確認有沒有 resolve、report、cycle_monitor 的排程。
3. 受保護雜湊清單（106 檔），確認 run.sh、README、budget.py、forecast.py 是否在內。
4. 那 100 個既有失敗節點的清單。
5. D131 外掛接受實際走的程式路徑。
6. project_control.py 第 1–50 行全文。

以上都是盤點結論與提議，沒有任何一項已核准、已執行或已驗證。