# D105 端到端監督交接（Claude → GPT，2026-11-02 UTC 13:40）

## D113 收尾（UTC 16:35；PLAN v1.62）

D105 四題及 D107 A2 三情境全部完成。41219／41418／41628／42336 的預測與私人留言各一次 POST、全部自動 found、unknown 0；A2 的重複歷史、四階合成留言及只補留言路徑也已核對。最新證據見 [M3-READINESS-D113.md](M3-READINESS-D113.md) 與 [A2 報告](D107-A2-BTA-REUSE-PROBE.md)。

16:35:10 已回 local，卸載本次 cycle LaunchAgent 並封存安裝副本，guard 及狀態監看均已結束，STOP_SUBMIT 不存在。原 campaign、正式帳冊與 TG 服務保持原狀；沒有新觀測在背景執行。M3 的 V11／實際輪詢重播仍有缺口，live 未核准。以下有時間標記的段落保留歷史，不代表目前仍在 trial 或仍有監看程序。

## D112 歷史驗收（UTC 15:33；PLAN v1.61）

M3 第二項的兩題型自動端到端已通過：41219／41418 是非題、42336 多選題的預測與私人留言均由 launchd 自動 found，各操作只 POST 一次，unknown 0。證據 `data/e2e_acceptance_checks.jsonl`（15:33:26）與 [M3-READBACK-D111.md](M3-READBACK-D111.md)。第四題 41628 尚待 Gemini 第 2 層與送出，A2 三情境還沒開始；不是 M3 全部完成，live 未核准。

已審查程式／文件提交 `50a0be5`；兩份 runtime config 未納入。現行 trial、STOP_SUBMIT 不存在，原窗口與 600 秒排程不變，監看 session 7274。下一步等第四題完成後做 A2，情境間至少 10 分鐘不存取 API，最後回 local。

Louis 指示：D105 端到端的監督交給 GPT，之後由 GPT 主持並分配工作；Claude 交接後待命（PLAN D108）。規格與決策仍以 `PLAN.md` 為準。

## GPT 接手後的更新（2026-11-02 UTC 14:00）

第二輪 `cycle_1793626870733_c065a358` 實際在 13:41:10.732 開始，13:42:16.969 三線預檢全部 ready，PATH 已修好。後續 Gemini 的有效額度讀數在等待題目刷新鎖時過了 106.720 秒，超過預算准入的 90 秒門檻，因此尚未啟動 Gemini 模型。Claude／GPT 各完成 3 次第 1 層；第二輪 13:51:44.979 自然結束，沒有新增平台 POST。

GPT 於 13:46:36 建立 `config/STOP_SUBMIT`，並在第二輪結束後取得 cycle／scheduler／submit／三線鎖，做有界離線修正審查；沒有變更 LaunchAgent。隔離副本 `queue_refresh_v2`／`cycle_v6` 完整測試 2029 passed，Gemini 第二輪獨立審查 PASS；UTC 14:05:33 已在互斥鎖保護下套用，PLAN v1.58 D109。第一輪審查逾時不算通過；依 Louis 新指示，後續開發審查上限改為 15 分鐘，預測 timeout 不變。不是端到端通過；A2 尚未開始。詳細證據、600 秒間隔評估與下一步見 [LAUNCHD-INTERVAL-D108.md](LAUNCHD-INTERVAL-D108.md)。

## 最新核對（UTC 14:50；歷史細節保留於後文）

41219：launchd 於 14:38:16 自動對帳預測成功，14:38:44 留言成功；主持人 14:48:52 以相同 request ID 另做 GET-only 留言讀回，結果 `found`，記於 `data/e2e_readbacks.jsonl`，沒有改寫正式送出帳冊。這是額外的驗收讀取，不把它冒充 launchd 自動讀回。

41418：14:43:07 預測只送一次，14:43:11 因回執格式未識別記 unknown，等待既有流程對帳；沒有重送。三線仍 ready，Claude／GPT 各完成六個工作，Gemini 完成兩個第 1 層工作。41628／42336 尚待 Gemini；A2 尚未開始。

14:50:17 官方 API 唯讀發現 MiniBench alias 為 33135，當前已開始的 ongoing IDs 為 33132、33135，主賽／MiniBench 排除集合為 33121、33132、33135。系列送出白名單仍 false，模式 trial，沒有比賽作答或 POST。證據 `data/m3_discovery_checks.jsonl`；這證明當時的發現結果，不是未來輪次或正式參賽核准。

## 執行設定

| 項目 | 值 |
|---|---|
| PLAN | v1.62（D113） |
| 模式 | `config/submit_mode.json` = local（UTC 16:35:10 回復） |
| launchd | 本次 `org.forecast-lab.cycle` 已卸載；600 秒安裝副本封存於 `data/launchd_archive/20261102T163510Z/` |
| 窗口 | `config/cycle_campaign.json`（未提交）＝ `d105_e2e_20261102T1303Z`，UTC 11/2 13:03～11/3 13:03；只有 `main_site_questions`，N=4、P=5；每線 40 次；13:29:15 已登記 |
| 選題 | 是非題 41219、41418、41628；多選題 42336（`data/main_site_trial_selections.jsonl`） |
| 熔斷 | GPT／Gemini 第 2 層熔斷已於 13:06:37 依 D107 A3 追加解除紀錄 |
| 相關提交 | `b926380`（D105）、`ee792f8`（A3）、`ac5edf2`（A2）、`481d8f6`（run.sh PATH） |

第一個週期 `cycle_1793626155531_f1126bca`（13:29:15～13:31:10）：選題完成，但三線都 `preflight_blocked`，因為 launchd 的 PATH 沒有 `~/.local/bin`，找不到 `claude`／`codex`／`agy`。沒有模型呼叫、沒有建立意圖、沒有 POST。`481d8f6` 已在 `launchd/run.sh` 補 PATH，在模擬的空環境下三個 CLI 版本都與驗收一致（2.1.316／0.166.0／1.4.12）。第二個週期實際於 13:41:10 開始，三線 ready；Gemini 刷新排隊的阻塞與修正見上文。

## 驗收目標（PLAN §10 M3 第二項）

是非題與多選題各至少 1 題，由 launchd 週期無人介入地完成：三線預測 → 整合 → 預測送出 → 私人留言 → 讀回 found。看 `data/submissions.jsonl` 中這 4 題的 `forecast_ok`、`comment_ok`（或 `unknown` 後經對帳 found）與 `data/cycles.jsonl` 的步驟狀態。

## 監督守則

- 只看 ID、狀態、計數、錯誤類型與時間。**不讀開放題目的預測機率、理由、留言正文或模型輸出**（D91 與 AGENTS.md 的進度規則；這 4 題雖是試驗題，也照同樣做法）。
- 先看每個週期 `cycles.jsonl` 最後一筆的 `players`：若仍有 `preflight_blocked`，看 `preflight.problems`。第一輪的 `no passing acceptance with the production model and effort` 推測是 CLI 版本為 None 連帶造成，PATH 修正後應消失；若沒消失要查原因，**不得用 waiver 或改驗收紀錄繞過**。
- 異常（非預期題目、重複 POST、4xx／429 連續、unknown 堆積、寫到 4 題以外）時：先建立 `config/STOP_SUBMIT` 停送出；需要整個停止時卸載指定的預測工作 `org.forecast-lab.cycle`，不影響 TG。D113 已在監督與收尾授權內實際完成卸載及封存；新安裝仍遵守 D78。unknown 只對帳、不重送；不刪帳冊。
- Gemini 線正在預測時不要啟動 Gemini 開發審查（`quota.detect_agy_activity()` 會把同時存在的 agy 視為不可信）。
- 模式為 trial 時，`tests/test_scheduler.py`、`tests/test_m2_supplement.py`、`tests/test_config.py` 有 26 個測試會讀真實的 `submit_mode.json` 而失敗，屬環境因素；在 local 下完整測試為 2023 passed。

唯讀檢查範例（只印 ID／狀態）：

```sh
tail -1 data/cycles.jsonl | python3 -c "import json,sys; r=json.loads(sys.stdin.read()); print(r['cycle_id'], r['status']); [print(p, v.get('status'), (v.get('preflight') or {}).get('problems')) for p, v in r.get('players', {}).items()]"
python3 -c "import json; Q={41219,41418,41628,42336}; [print(r.get('question_id'), r.get('status'), r.get('operation'), r.get('at')) for r in map(json.loads, open('data/submissions.jsonl')) if r.get('question_id') in Q]"
```

## 已核對的雜訊與待評估項目

`Submitter._event()` 已按 aggregate ID／原因去重，既有回歸測試通過。最初有 130 筆舊 M2 拒絕紀錄；後來新增的是新 aggregate 的檢查，並非每輪重寫 130 筆。「每天新增 1.9 萬」是原交接的錯誤推算，已撤回，不據此修改送出器。全歷史掃描的本地成本可另評估。

當時的 on_finalized 曾在 120 秒送出上限中止；D111 已經 Gemini 獨立審查，改為 300 秒並補獨立讀回，實機已完成四題。模型 timeout 與 600 秒排程間隔不變；詳見 [M3-READBACK-D111.md](M3-READBACK-D111.md)。

## 端到端之後的原定工作（D113 已完成 1～3 的本輪收尾）

1. 把端到端結果寫進本檔、`HANDOFF.md`，並由整合者更新 PLAN §10 M3 的狀態與 §15／§16。
2. D107 A2 的三個測試區情境（`docs/tasks/D107-A2-BTA-REUSE-PROBE.md`）：probe 會取得 scheduler／submit／三線鎖，與週期互斥，要在週期閒置或移除後執行；依序 `duplicate_forecast`（是非題）、`comment_length`、`comment_retry`，每個之間至少 10 分鐘且沒有其他 Metaculus API 活動，完成後回 local。D107 A1 已給常設授權，不必逐次請示，事後回報。
3. 原窗口截止仍為 UTC 11/3 13:03；既定四題與 A2 已完成，D113 已提前結束執行、回 local 並卸載封存排程，不改寫凍結窗口。下一個窗口須在看任何輸出前設定，且不得與本窗口重疊。
4. MiniBench 實機發現與 bulk 候選評估已記於 D111；V11 與實際輪詢重播缺口見 D113 對照。切換 `live` 仍須 M3 全部通過並經 Louis 另行核准。

## 已恢復實機（UTC 14:09）

UTC 14:06:48.813 既有 launchd 接續原窗口，GPT 自建 STOP_SUBMIT 已移除、審查互斥鎖已釋放。14:07:57.254 三線 ready；Gemini 14:09:10.982 真正啟動模型，准入時讀數年齡 54.108 秒，原有 90 秒門檻未放寬。仍待兩題型送出／留言／讀回完成。

## 跨週期監看（D110，PLAN v1.59）

`src/cycle_monitor.py --campaign config/cycle_campaign.json` 已完成九個新增合成測試、完整隔離 2038 passed，Claude 靜態審查 PASS。它每 5 秒檢查 ID／狀態／計數，變更時追加 `data/cycle_monitor.jsonl` 並更新本地進度板；不存取 API、不呼叫模型、不送出或重送。異常只建立 STOP_SUBMIT，不會自行移除。程式在窗口結束（UTC 11/3 13:03）、模式／設定改變、已有停送、異常停送或全部預測／留言成功紀錄齊全時結束；最後一種仍須主持人核對獨立讀回，不能直接算 M3 完成。這是本機有界程序，不是新增 LaunchAgent，也不是桌面模型持續在線；主機重啟後不會自動重啟它。

此工具的開發審查依 D109 採 15 分鐘上限；預測 timeout 不變。A2 仍只由主持人在端到端題目處理完且取得互斥後依原凍結計畫執行，本監看程式不自動開始 A2。若主持人發布較新的進度，監看程式不再覆寫該進度；後續狀態仍在監看帳冊和 STOP_SUBMIT。

目前操作證據：第一筆 41219 預測於 UTC 14:21:03 留下 request_started，14:21:07 因 `unrecognized write receipt` 記 unknown；只有一次 POST。既有 launchd 將對帳該意圖，不重送未知結果。前面的 on_finalized 曾在 120 秒前檢上限被中止、當時沒有 POST；同一輪最後的 submit 已接續意圖並發出一次請求。此尾段時間成本列入後續效能評估，沒有因此改 timeout 或放寬契約。

實際啟動：UTC 14:35:32.087，session 72155，初始 monitoring、hazards=[]、unknown_requests=1；Gemini 第 2 次模型工作已開始。監看持有 `data/cycle_monitor.lock`，可用 `lsof -n data/cycle_monitor.lock` 查核程序是否仍存在。帳冊只在狀態變化時追加，不是心跳；不能單憑舊紀錄宣稱程序在線。

## D111 套用（UTC 15:15）

`submit_v10`／`cycle_v7` 已經完整隔離 2055 passed 與 Gemini PASS，15:15:41 在互斥鎖下套用。成功回執由正式 CLI 補做 GET，證據在 `data/submission_readbacks.jsonl`；未知讀回不更改原已受理狀態、不重送。送出子程序上限改 300 秒，其餘預測設定和排程不變。兩題型的驗收應核對相同 request 的正式 `reconciled=true` 或新帳冊 found。詳見 [M3-READBACK-D111.md](M3-READBACK-D111.md)。15:05 時兩題 binary 已送出、unknown 0，四題 Gemini 第一層已完成；A2 尚未開始。

D111 恢復證據：UTC 15:19:48 只移除 GPT 本次自建 STOP_SUBMIT，審查鎖已釋放；15:19:53 狀態監看重新啟動（session 7274），unknown 0／hazards=[]。主持人以既有 launchd 的 kickstart 接續同一個凍結窗口，未重裝或改間隔；這次啟動為修正後恢復，不列入自然排程間距估計。尚待新週期的實機讀回。
