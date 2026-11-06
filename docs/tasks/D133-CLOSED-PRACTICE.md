# D133 — FutureEval 已關閉題目練習交接

最新交付（UTC 2026-11-04T17:30，台北 11/05）：**程式已部署、Claude r4／Gemini r4 雙審 PASS；實際盤點 108 題全部遮蔽，0 新練習呼叫。** 已完成既有 10 道閉題健檢及合成驗證；新實題練習仍受合法資料權限限制，尚無準確度結論。主賽未啟用，原未提交變更保留，未 commit／push。以下保留原交接與各輪歷史，最終交付詳見文末。

原交接紀錄時間：2026-11-04T16:17:54Z（台北 2026-11-05）。來源：Louis 本輪桌面指示；PLAN v1.86 §4.3／D133。本輪主持及文件整合：GPT。

## 原交接（UTC 16:17）的使用者決定與完成範圍

Louis 原話：「我建議可以先拿你說的題目練習 這樣更好收斂 先幫我寫交接我開另一個對話實作」。

- 已決定下一步先用已關閉的 FutureEval 題目練習；下一個桌面對話負責實作，不必重問是否要做這個方向。
- 本輪只完成交接、PLAN 與本機共用進度同步。未選實際題集、未呼叫模型練習、未新增程式或排程、未更動正式預測行為。不得把交接寫成已開始執行。
- 未授權啟用 FutureEval 主賽，也未取消 M6 的主賽容量評估、M4／M5 結果評估及 Louis 正式參賽核准。11/13–14 是原計畫的評估時點，不是官方固定等待期，也不是預測品質最優的保證。

## 目標與資料分層

先讓已關閉題目的工程與研究品質回饋加快收斂；不要把事後可見資訊造成的高分當成正式賽優勢。

| 類別 | 使用方式 | 可得結論與限制 |
|---|---|---|
| 已有正式預測、題目已關閉 | 先用 `post_close_audit.py` 通過既有三道閘門，再檢查當時凍結的輸出與來源；不用先重跑模型 | 最省額度，能找格式、題意、來源及日期錯誤。未結算仍不能判定準確度；之後沿正式 resolve／score 帳冊計分 |
| 已關閉、事件尚未發生／結果尚未可知的新練習 | 以實際練習時間作答並凍結，等待日後結算；明確標記「關閉後作答」 | 可作前瞻練習；因作答較晚、資訊集不同，不能與原截止時的正式榜單直接公平比較 |
| 已結算，或事件已發生而平台尚未結算 | 用於流程與錯誤分析；若做歷史回測，另建截止時資料快照 | 無可信時間隔離者只列事後診斷；即使網頁限時，模型內部記憶與後改網頁仍可能洩漏，不聲稱乾淨盲測 |

`closed`、`resolved`、事件發生時間、官方預定結算時間必須分列。僅 `status=closed` 或 `resolution=null` 不足以證明結果未知。不得只要求模型「假裝回到當天」便標成有效歷史回測。

## 官方規則與資料取得

- [FutureEval 官方資源頁](https://www.metaculus.com/notebooks/38928/futureeval-resources-page/)於本輪核對，明確允許測試已關閉的比賽題目，也允許賽中更新機器人。禁止預覽開放／即將開放比賽題目的輸出後修改機器人，及因不喜歡個別答案而重跑。
- 同頁 API 說明：一般驗證帳號可取得自己預測過的關閉題之題文及結算；更廣的 Bot Benchmarking Access Tier 需另行申請。先盤點合法 API 權限與已有快照，不承諾能抓任意歷史 FutureEval 題。
- 只經既有 API transport 取得題目；token 留在主程式，不交模型。401／403／缺欄位／權限不足應記錄，不以網頁批量抓取或其他帳號繞過。沒有可用新題時，仍可完成既有答案健檢與合成測試，不自動把其他賽事混入 FutureEval 樣本。
- [官方規則](https://www.metaculus.com/notebooks/28751/q1-ai-forecasting-benchmark-rules/)與 [計分 FAQ](https://www.metaculus.com/help/scores-faq/#tournament-scores)另見 `docs/tournament_checks/futureeval.md`。關閉題練習不補送、不產生官方賽事分數；本地分數不得命名為官方 Peer Score 或排名。

## 原交接初版實作契約（下方補記為最新狀態）

以下為接手時的保守預設，不是已發生的呼叫或新增額度授權：

1. **先無模型盤點與既有輸出健檢。** 在揭露既有 bot 答案前，重查官方狀態為 closed／resolved 且截止至少 15 分鐘；本地無在途 attempt／unknown 送出或留言待對帳；已移出佇列。沿用 D126 `review_eligible`，不直接翻 `data/raw/`。新練習題需有明確的「從未進入正式生命週期」證據，不能用缺帳冊冒充閘門通過。
2. **首批最多 3 題、每題三線各 1 次初始嘗試，合計最多 9 次准入。** 使用目前接受的模型、隔離設定、原有題型 prompt 及整合規則；不自動修復／重跑／第二層，不因缺線而換題補成功數。這只驗證第一層與整合，不代表完整兩層策略效能。額度不足就縮減，沒有符合條件的題目就列原因。
3. **看輸出前固定題集與比較方式。** 先記候選母體、排除理由、題型、事件／題群、資料可得性、選題排序、截止／練習時間、版本、上限與停止條件。優先採用結果尚未知的題；未知是否已發生則只入診斷組。不可依結果或模型自信挑容易題；不足各題型覆蓋照實列示。
4. **先分開練習與保留驗證。** 首批是工程／研究品質診斷；後續調整需另登記未用來調整的驗證題，以時間或事件群分開。同題重跑若未來另作開發比較，保留全部版本，不當成新樣本或最佳成績。既有正式答案與較晚練習答案並列時，明示資訊集不同。
5. **全部只本地執行與保存。** 建議獨立入口及 `data/practice/<batch>/`、`reports/practice/`，JSONL 只追加、時間 UTC。批次、題目、attempt、版本、來源、稽核、採用與缺漏皆可追溯。正式 forecasts／aggregates／submissions／resolutions／ITT 母體不摻入練習紀錄。
6. **正式賽優先且共用真實額度。** 不為練習新增／重設窗口或 cap，不把獨立資料夾當免費額度。每次准入檢查剩餘額度、三線停線／冷卻、未知嘗試及未來 bot 容量保留；unknown 消耗保留並阻止重跑。正式工作出現時讓路，不長時間占鎖使正式題逾時。新練習模式的共用記帳、安全事件與異常恢復先實作測試，不能直接套用有固定兩題／六次限制的 D132 入口。
7. **排除開放／即將開放的同步題。** 新鮮查核 FutureEval 主賽及當前／即將開始 MiniBench 歸屬、question／post ID 與同步關係；不能只靠標題或本地截止時間。無法排除重疊即不練；模型不讀其他開放題的 bot 輸出。全程沒有預測或留言 POST 路徑。

## 評估與可改範圍

- 報告分開列：有效輸出／缺答、耗時與額度、來源可核實性、題意／結算條件／單位／時區錯誤、未來資訊或答案暴露、各線與整合的差異。格式 valid 不代表事實正確。
- 有正式結算值才計本地分數；沿用既有各題型純計分函式時，另保留練習資料來源與時點，不假造正式提交回執。log／Brier、數值／離散分數分開報告，不把不同題型直接混成單一「準確率」。取消、模糊、未結算不計分。
- 沒有原截止時對手分布與適當官方權限，不能由練習答案重建官方 Peer Score。練習來源／時間不同的組別不可合併評估，缺答、失敗及成本未知不可藏掉。
- 沿 PLAN §4.3、D126：少於 30 題只描述；30–100 題仍需信賴區間，不能把門檻當充分證據。解析、漏傳結算條件、單位等確定性缺陷可修；不能因少數題改三線權重、裁切、信心語氣、升級門檻或停用某條線。涉及 prompt／設定的行為變更必須升版、記錄、審查，且尊重 D118 固定設定。
- 主賽是否提前是另一步就緒度與參賽決策。小批練習可發現缺陷，不能取代主賽＋MiniBench 的併發／額度覆蓋率驗證，也不能證明每題期望 Peer Score 為正。

## 接手順序與現況

1. 在 `/Users/ucchengg/forecast-lab` 先執行 `.venv/bin/python src/project_control.py status`，讀 AGENTS、PLAN、HANDOFF 及本文件，核對有無較新決定。工作樹已有 D132 未提交變更，保留，不 reset／清除或誤當本輪變更。
2. 閱讀 `src/post_close_audit.py`、`src/advise.py`、`src/flab/advisory_usage.py`、`src/budget.py`、`src/forecast.py`、`src/resolve.py`、`src/score.py` 與相應測試；先釐清可重用的安全入口，勿為讓閉題進入練習而放寬正式送出／排程的 closed 拒絕規則。
3. 做 metadata／API 權限盤點，事前登記小批契約；實作獨立練習入口、記帳、結果時點分類及報告。模型在專案外空白暫存目錄，只能搜尋／讀網頁；不能將本地解答、他線輸出、正式 bot token 或歷史報告帶入。
4. 先離線驗證，再依既有要求由 Claude 完整審查及 Gemini 獨立審查。若改共用執行期程式，正式套用使用 cycle／scheduler／submit／三線六鎖、保護雜湊與前後 preflight；僅文件更新不消耗模型做形式審查。實際接手者才登記開始，完成後才發布完成。
5. 通過後依有界契約手動執行首批，留下可重現的結果與限制；每個實作／驗證檢查點先查 status，再 publish metadata-only 進度。不要自動新增週期練習、發送 TG 通知或啟用主賽。

現況僅供接手定位，正式操作前重查：

- D118 MiniBench live，窗口截止 `2026-11-10T04:00:00Z`，預算承接 D105 `accounting_start`，Claude／Gemini／GPT caps 為 130／200／130。D132 完成時帳面計次為 26／25／24，這不是即時剩餘量，也不包含所有開發／個人使用。
- D131 三線已恢復，D132 版本為 `advisory_v1`／`advisory_usage_v1`／`budget_v8`／`forecast_pipeline_v9`。手動預測或 preflight 要沿用固定 CLI：`PATH="$PWD/launchd/bin:$PATH" .venv/bin/python ...`；不可誤用全域版本。
- D132 兩題人類賽參考已完成，不重做；詳細證據見 [D132](D132-CUP-ADVISORY.md)。不把它們的開放題內容放入本次共享交接／研究資料集。
- 首批正式 MiniBench 10 題於 D132 查詢時均 closed 未 resolved，最早預定台北 11/13 20:00；尚不能聲稱有正式準確率。主賽 project 33121 尚未啟用。

## 驗收與交付

- 有意義的離線案例涵蓋：閉題後重開、同步開放題、狀態／權限不明、已發生未結算、結果欄位或搜尋摘要洩漏、跨批重複、上限／預算／冷卻、未知嘗試恢復、正式任務讓路，以及練習永遠不能 POST 或寫入正式預測／分數母體。
- 不假裝能偵測所有答案洩漏；已知暴露追加稽核，保留原答案，取消乾淨評估資格而非重跑洗白。
- 交付：事前登記題集與資料限制、可操作手動入口、獨立帳冊／報告、測試及雙審證據、實際呼叫／缺漏／成本清單、工程修正建議，以及未完成項與下一步。明確區分程式已完成、實際練習已完成、準確度仍待結算三種狀態。
- 以上為 UTC 16:17 的原交接歷史。實作成果以以下補記為準；不得將舊交接當成測試 PASS。


## D133 實作補記（PLAN v1.88；已部署，最終驗收見下）

來源：Louis 明確要求接手、依交接實作與完成驗證雙審。本輪 GPT 為整合主持；保留 D132 未提交變更及另一對話的 D134 英文偏好。隔離副本與原工作樹雜湊、測試、官方 CLI 雙審、套用腳本都在 `data/operations/d133-closed-practice-20261104/`。

### 定案契約與恢復

- 入口 `src/practice.py`，原截止不改；閉題時鐘只適用已登記快照、私有帳冊、初始 tier 1。版本 `closed_practice_v1`／`external_usage_v1`／`budget_v9`／`forecast_pipeline_v10`。受保護 adapters／runner／netguard／config／prompts 不改。
- prepare 先檢查共用未知消耗、停線、冷卻、未收斂提交，再盤點候選；本地完整生命週期含 submission_readbacks／budget／advisory／practice 與 post ID，並要求官方 my_forecasts 為明確空的 history/latest 結構；null／缺欄位一律不確定而拒絕。沒有這項證據不能靠本地無資料放行。
- 零選題只寫 inventory，不登記唯一非空批次；以後權限改善可另建 inventory，但不是自動續作。非空批次最多三題／九次准入，不重選補成功。按 UTC 截止時間分群，至少兩群才保留最新群；只有同一群時最多三題診斷，清楚標示獨立驗證尚缺，任何後續改動須先登記不同未用時間／事件群。
- 所有題的 event_time 都先標 unknown；已結算／結果已出現者為 retrospective，其餘仍是 outcome_timing_unknown，不因 closed 推論前瞻。題文可能被事後編輯、模型可能記得結果、搜尋摘要可能暴露答案；無法聲稱全部洩漏可偵測。
- 自取得六鎖起計入前檢時間，12 分鐘為准入／CLI 上限，末 60 秒拒絕新准入；每次檢查預看正式題至少 20 分鐘，前檢後強制刷新。模型啟動後沿既有 timeout，不更動保護 runner；突發新開題仍可能等到該有界呼叫及清理結束，沒有硬即時取消承諾。lock_events 保存實際持鎖秒數。正式 cycle 鎖忙會回 overlap_skipped，下一 tick 再試，不消耗題目准入。准入後若新鮮查核失敗，可能消耗一次練習名額但確認沒啟動不計 CLI 消耗；不補跑。
- usage/guards 僅中繼資料，正式 forecast／aggregate／resolution／score 母體沒有練習答案。Gemini 的真實 quota 快照仍共用，估計可能更保守，不新增額度。恢復未完成呼叫沿 D132 前例保守停線，需既有 resume 流程；未知嘗試保持消耗，正式 preflight 顯示 `practice_attempt_unknown`；主持執行 `recover`，僅對過期且程序已不存在的嘗試結案，不重跑。錯誤／損毀帳冊保留並修復，不刪除以繞過拒絕。既有 leak_confirmed 兩次／七天及 model mismatch／quota 冷卻規則沿用，沒有一次暴露新增停線政策。
- score 使用當時落盤 aggregate ID 及仍可採用的個別答案；只取官方結算，取消／模糊／未結算不計。只鎖自己批次，不占六把正式鎖，不因目前有正式開放題拒絕純計分。結算後題文修訂會註記，ID／題型／選項／量尺／截止變動拒絕；逐題記錄錯誤。私人報告分組、分題型、分線保存分數；共享摘要只有數量、缺漏、用量可觀測性、稽核與耗時。

### 操作（雙審通過並部署後才用）

```sh
mkdir -p data/practice/d133_inventory_20261104
PATH="$PWD/launchd/bin:$PATH" .venv/bin/python src/practice.py prepare --batch data/practice/d133_inventory_20261104
PATH="$PWD/launchd/bin:$PATH" .venv/bin/python src/practice.py report --batch data/practice/d133_inventory_20261104
# 只有非空已登記題集才 run，--question 必填；同題不可再次執行
# python src/practice.py run --batch data/practice/<batch> --question <registered_id>
# python src/practice.py recover --batch data/practice/<batch>
# python src/practice.py score --batch data/practice/<batch>
```

### 已完成的資料健檢與限制

- 10 道已送出 MiniBench 題均經 fresh API＋本地生命週期／佇列三閘門後才讀取答案。私人完整報告 `reports/practice/d133-existing-closed-audit-20261104.md`；品質註記 `reports/practice/d133-existing-quality-20261104.md`。
- 5/10 題有缺線、共 6/30 個第一層輸出缺漏（Claude 2／Gemini 3／GPT 1）；7/10 題保留疑似洩漏旗標，未冒稱已確認。原數值離散度以不同單位直接用 0.30 門檻僅能當工程旗標，不能跨題比高低；本輪未改整合或門檻。
- 題意／來源健檢揭露需要區分事件已發生與未來報告發布、歷史基準樣本數未核實、滾動來源頁不等於原時間快照。主資料源可讀不能證明答案正確。未結算，不計正式準確率，不調權重／裁切／信心／升級門檻。
- 主賽 33121 API 閉題清單 108 道全部遮蔽 description／resolution_criteria；三道 detail GET 一樣缺值。沒有繞過權限，沒有新練習呼叫，也沒有預測／留言 POST。

### 驗證與部署紀錄（持續更新）

- 原始隔離副本：3134 passed／100 failed；首輪實作：3174 passed／100 failed，失敗節點集合完全相同。這是既有基線失敗，不能聲稱全套 PASS。
- 第二輪專項 71 passed，包含三線 fake adapter 完整 run、正式路徑無／空／損毀帳冊回歸、預約後未准入釋放、未知前檢、慢前檢、重新開放、兩次暴露門檻、凍結整合 ID、busy 六鎖等。第二輪完整 3205 passed／100 failed，100 失敗摘要與基線一致；最終增補驗證另列。
- Claude/Gemini r1 REQUEST_CHANGES；Gemini r2 PASS，Claude r2 要求修正被拒准入的生命週期誤判、拒絕 null 預測歷史、部署雜湊時間差。已修正並待 r3 雙審；尚未部署。所有原審查保留。

- r3 增補：預算 run ID 在 guard 前以交易登記，僅此次執行的確切 ID 可豁免 denied/reserved 練習紀錄；prepare 完全不豁免。混合正式／練習暴露按時間重播，完成／guard 版本嚴格檢查。prepare 超時不登記，score 遇在途 run 暫不計分；報告增加逐線缺漏／失敗與用量覆蓋。API Backoff 仍可追加共用 cooldown，中途正式開題則 prepare 拒絕且需擇時重試，沒有自動輪詢練習。
- 部署腳本已在隔離假工作樹驗證 4 個案例：stage 事前變更拒絕、驗證後變更仍只寫已驗證位元組、後檢失敗完整回滾、審查檔變動拒絕。這是腳本測試，非正式部署證據。

- r3：Gemini PASS，Claude 要求縮小安全事件排序範圍與限縮正式目錄 smoke；r4 已採最小差量修正，只有 forecast.py 與兩個新增測試改變，其餘四檔 byte-identical。79 項練習／205 項相關測試 PASS；完整 3213 passed／100 failed，100 失敗摘要與基線一致。部署故障注入 6 passed。來源：`test-comparison-final.json`、`related-tests-final.txt`、`deployment-tests.txt`、`reviewed-files-r4.json`（均在本次 operations 目錄）。r4 雙審待回覆，仍未部署。
- 已知手動限制：若整題在 aggregate／summary 階段中斷，score 會保守保留 run_in_progress，主持須對帳，不能用再次 run 來填補；by_player.failure_types 是例外類別，正常落盤的 timeout／invalid 等細節仍以私人 forecast 紀錄為準。未來 practice v2 需同步更新共用解析器支援，不能直接加入新版本帳冊導致正式線 fail closed。


## 最終交付（UTC 2026-11-04T17:30，PLAN v1.88）

- **程式／審查已完成**：`practice.py prepare/run/recover/score/report` 已部署，維持原截止、獨立資料、共用真實額度、最多三題九准入、無自動修復／重跑／提交。Claude 完整 r1/r2/r3 與最終差量 r4、Gemini 獨立 r1/r2/r3/r4 都保留；最終 [Claude r4](../../reviews/claude-d133-r4.md) 與 [Gemini r4](../../reviews/gemini-d133-r4.md) 同一 canonical SHA PASS。來源／部署 manifest：[d133-source-manifest.json](../../reviews/d133-source-manifest.json)。不採用審查正文轉寫錯誤的 SHA 字串。
- **測試**：79 項練習專項 PASS；隔離四檔相關測試 205 passed in 1.47s；部署故障注入 6 passed。全套 3213 passed／100 failed，原始副本 3134 passed／100 failed；100 個失敗節點及摘要完全一致，**不是全套 PASS**。完整指令／輸出見本次 operations 的 `related-tests-final.txt`、`stage-tests-r4.txt`、`baseline-tests.txt`、`test-comparison-final.json`、`deployment-tests.txt`。
- **實際套用**：2026-11-04T17:28:20.665Z 持六鎖完成；六個目標檔符合送審 SHA，103 個未修改執行期／設定／隔離檔雜湊不變（接手時 105 檔，其中 forecast/budget 為本次核准目標）。備份於 `data/operations/d133-closed-practice-20261104/deployment-backup-172744`，實際持鎖 36.40s。前後各三線兩層共六項 preflight 全 ok、waiver 空、line_guard 空；正式 root smoke 79 passed in 0.30s。前後檢不包含被父鎖刻意略過的 acceptance 孤兒恢復。`operations.jsonl` 保存實際部署 SHA 與審查檔 SHA。
- **實際練習狀態**：已執行 `prepare`／`report`。批次 `data/practice/d133_inventory_20261104/`，108 候選全為 `data_permission_or_missing_description`，selected／admitted／started／pending 都為 0；0 新練習模型呼叫。沒有新增 `data/practice_batches.jsonl`，未耗唯一非空批次。manifest SHA：`ee38de05fd94c0b15ba185b4f76a2b06cb087ac5019007fac39902cab317c072`。報告：`reports/practice/d133_inventory_20261104_report_1793813407989_85b38d0f.md`。三次早期 detail GET 也缺 description／criteria，未繞過權限或換帳號。
- **既有答案健檢已完成**：10 題先過 D126 三閘門後檢查，私人報告 `reports/practice/d133-existing-closed-audit-20261104.md` 與 `reports/practice/d133-existing-quality-20261104.md`。這是既有正式答案，不算新閉題練習樣本，不補跑缺線；來源抽查範圍、單位／事件日期問題與未核實基準率已記錄。
- **開發成本獨立記錄**：本次 8 次官方 CLI 開發審查（Claude／Gemini 各四輪）另列 `review-usage.jsonl` 與 `reviews.jsonl`，保留回報用量及缺值；不是免費額度，也不是八個預測樣本。新練習消耗為 0，沒有新增／重設 cap 或窗口。
- **保留與邊界**：D132 原未提交工作及 D134 英文報告／Gemini 留言偏好完整保留；D118 固定 campaign、D105 accounting_start、130／200／130 caps、prompt／模型／整合／隔離設定未改；主賽 33121 未啟用，沒有新增排程或任何本次預測／留言 POST。未 commit／push。
- **未完成與下一步**：目前不能取得新閉題題文，故沒有實際新題研究答案、獨立驗證樣本或準確度結果。取得官方合法存取權後才另行人工盤點，非空批次仍遵守既定上限與正式賽優先；沒有自動續作。既有正式題等待真正結算後走既有 resolve／score；不因本次小樣本改參數、不由本次工程驗收推導主賽就緒。
