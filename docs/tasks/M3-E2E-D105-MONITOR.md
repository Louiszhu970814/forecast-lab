# D105 端到端監督交接（Claude → GPT，2026-11-02 UTC 13:40）

Louis 指示：D105 端到端的監督交給 GPT，之後由 GPT 主持並分配工作；Claude 交接後待命（PLAN D108）。規格與決策仍以 `PLAN.md` 為準。

## 現況

| 項目 | 值 |
|---|---|
| PLAN | v1.57 |
| 模式 | `config/submit_mode.json` = trial，bot 311763（UTC 13:27:50 切換） |
| launchd | `org.forecast-lab.cycle` 已由 Louis 於 UTC 13:29 親自安裝，每 600 秒啟動；`~/Library/LaunchAgents/org.forecast-lab.cycle.plist` |
| 窗口 | `config/cycle_campaign.json`（未提交）＝ `d105_e2e_20261102T1303Z`，UTC 11/2 13:03～11/3 13:03；只有 `main_site_questions`，N=4、P=5；每線 40 次；13:29:15 已登記 |
| 選題 | 是非題 41219、41418、41628；多選題 42336（`data/main_site_trial_selections.jsonl`） |
| 熔斷 | GPT／Gemini 第 2 層熔斷已於 13:06:37 依 D107 A3 追加解除紀錄 |
| 相關提交 | `b926380`（D105）、`ee792f8`（A3）、`ac5edf2`（A2）、`481d8f6`（run.sh PATH） |

第一個週期 `cycle_1793626155531_f1126bca`（13:29:15～13:31:10）：選題完成，但三線都 `preflight_blocked`，因為 launchd 的 PATH 沒有 `~/.local/bin`，找不到 `claude`／`codex`／`agy`。沒有模型呼叫、沒有建立意圖、沒有 POST。`481d8f6` 已在 `launchd/run.sh` 補 PATH，在模擬的空環境下三個 CLI 版本都與驗收一致（2.1.316／0.166.0／1.4.12）。**第二個週期（約 13:39 起）是第一個實際會跑模型的週期，尚未確認結果。**

## 驗收目標（PLAN §10 M3 第二項）

是非題與多選題各至少 1 題，由 launchd 週期無人介入地完成：三線預測 → 整合 → 預測送出 → 私人留言 → 讀回 found。看 `data/submissions.jsonl` 中這 4 題的 `forecast_ok`、`comment_ok`（或 `unknown` 後經對帳 found）與 `data/cycles.jsonl` 的步驟狀態。

## 監督守則

- 只看 ID、狀態、計數、錯誤類型與時間。**不讀開放題目的預測機率、理由、留言正文或模型輸出**（D91 與 AGENTS.md 的進度規則；這 4 題雖是試驗題，也照同樣做法）。
- 先看每個週期 `cycles.jsonl` 最後一筆的 `players`：若仍有 `preflight_blocked`，看 `preflight.problems`。第一輪的 `no passing acceptance with the production model and effort` 推測是 CLI 版本為 None 連帶造成，PATH 修正後應消失；若沒消失要查原因，**不得用 waiver 或改驗收紀錄繞過**。
- 異常（非預期題目、重複 POST、4xx／429 連續、unknown 堆積、寫到 4 題以外）時：先建立 `config/STOP_SUBMIT` 停送出；需要整個停止時請 Louis 執行 `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/org.forecast-lab.cycle.plist`（Claude Code 的自動模式擋過代理安裝 LaunchAgent，移除也可能需要 Louis 本人）。unknown 只對帳、不重送；不刪帳冊。
- Gemini 線正在預測時不要啟動 Gemini 開發審查（`quota.detect_agy_activity()` 會把同時存在的 agy 視為不可信）。
- 模式為 trial 時，`tests/test_scheduler.py`、`tests/test_m2_supplement.py`、`tests/test_config.py` 有 26 個測試會讀真實的 `submit_mode.json` 而失敗，屬環境因素；在 local 下完整測試為 2023 passed。

唯讀檢查範例（只印 ID／狀態）：

```sh
tail -1 data/cycles.jsonl | python3 -c "import json,sys; r=json.loads(sys.stdin.read()); print(r['cycle_id'], r['status']); [print(p, v.get('status'), (v.get('preflight') or {}).get('problems')) for p, v in r.get('players', {}).items()]"
python3 -c "import json; Q={41219,41418,41628,42336}; [print(r.get('question_id'), r.get('status'), r.get('operation'), r.get('at')) for r in map(json.loads, open('data/submissions.jsonl')) if r.get('question_id') in Q]"
```

## 已知待修（不阻擋端到端）

- 送出器每個週期都會掃描所有舊的 M2 整合紀錄，寫入約 130 筆 `rejected_precheck`（`aggregate is not submission eligible` 40、`expected exactly one comment_v1 for aggregate` 90）；沒有意圖、沒有請求，但帳冊每天約多 1.9 萬筆，週期狀態一直是 `attention`。建議讓送出器只看目前持續窗口（或本次週期發布）的整合紀錄；屬送出相關，依 D107 A4 需 Gemini 獨立審查。

## 端到端之後的工作

1. 把端到端結果寫進本檔、`HANDOFF.md`，並由整合者更新 PLAN §10 M3 的狀態與 §15／§16。
2. D107 A2 的三個測試區情境（`docs/tasks/D107-A2-BTA-REUSE-PROBE.md`）：probe 會取得 scheduler／submit／三線鎖，與週期互斥，要在週期閒置或移除後執行；依序 `duplicate_forecast`（是非題）、`comment_length`、`comment_retry`，每個之間至少 10 分鐘且沒有其他 Metaculus API 活動，完成後回 local。D107 A1 已給常設授權，不必逐次請示，事後回報。
3. 窗口在 UTC 11/3 13:03 結束；之後週期不再做模型工作。若不另開窗口，請 Louis 移除 launchd 週期並切回 local。下一個窗口須在看任何輸出前設定，且不得與本窗口重疊。
4. M3 其餘項目：MiniBench 系列白名單與每輪 ID 的實機核對、bulk 端點評估（候選）。切換 `live` 仍須 M3 全部通過並經 Louis 另行核准。
