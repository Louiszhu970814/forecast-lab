# M5-1 試驗場地探測規格（v15_probe_v5 與暫時試驗模式工作階段）

> 依據：PLAN D122（M5 驗證通過的定義）、D107 A1（試驗場地有界寫入常設授權）、D123 同日 Louis 選定 M5-1。
> Louis 原文選項：「M5-1 合成 CDF 探測」，即在 bot testing area 的數值題與離散題各送 1 次合成 CDF 與 1 則私人留言並讀回；執行時持排程鎖約 2 分鐘暫切試驗模式，之後原樣還原正式授權檔；主站數值題改為上線後監看第一題的讀回。

## 1. 目的

以正式送出路徑（`submit.Submitter`、`submit_transport`）在 bot testing area 實際驗證：

- 數值題 payload `[{"question": id, "continuous_cdf": [...]}]` 被伺服器接受；
- 讀回欄位 `submit_transport.NUMERIC_READBACK_FIELD`（目前假設為 `forecast_values`）與容差 5e-10 能讀回 found；
- 私人留言可送出並讀回。

探測只用合成 CDF，不是研究預測，不呼叫任何模型 CLI。

## 2. 範圍

1. 擴充 `src/v15_probe.py` 為 `VERSION = "v15_probe_v5"`，只在 `scenario == "single"` 新增 `numeric`、`discrete` 兩種題型；既有 binary／multiple_choice 與各 reuse 情境的行為、驗證與 plan 格式逐位元不變（以測試證明，舊版 plan 仍可依原規則驗證與恢復）。
2. 新增 `src/m5_probe_session.py`：暫時試驗模式工作階段（見第五節）。
3. `src/submit.py` 只允許下述最小變更：`Submitter.__init__` 新增僅限關鍵字參數 `probe_question_types: tuple[str, ...] = ()`；`_gate` 的題型檢查在 `question_type in probe_question_types` 且 `mode == "trial"` 且 `intent["tournament_id"] == 32977`（bot testing area）時放行，其他情況仍依 `config/question_types.json`。不得放寬任何其他 gate。
4. 不得修改 `src/adapters/*`、`src/flab/runner.py`、`src/flab/netguard.py`、`config/quota_policy.json`、`config/run_config_v2.json`、`config/cycle_campaign.json`、`config/question_types.json`，以及 numeric 轉換、整合與送出前檢查的語意。

## 3. 探測內容（每種題型一次）

- 候選題：bot testing area（tournament 32977）中狀態 open、題型相符、`F.eligible` 以外的縮放檢查 `numeric.scaling` 通過、`transport.can_reconcile_forecast(post)` 為真、且本探測帳冊中未使用過的題目。優先序：numeric 46451、43325；discrete 45708、43322；都不符合時才依既有規則選最早截止者。若無候選，該題型記錄 `no_candidate` 並結束，不送任何請求。
- 快照 metadata 除既有欄位外，加入 `numeric.SCALING_FIELDS` 全部欄位（取自 `poll_questions.snapshot_from_post` 的結果），供 plan 重算與送出前檢查。
- 合成分位數：在 u 空間固定為 `(0.20, 0.25, 0.35, 0.50, 0.65, 0.75, 0.80)`，以 `Scaling.from_u` 轉成實際值，再用 `numeric.to_cdf` 產生 CDF，並以 `numeric.validate_cdf` 驗證。plan 驗證時必須從 plan 內的 snapshot 重算並與 `forecast_payload` 完全相等，否則拒絕。
- 留言：`Forecast Lab API component check {probe_id}. Synthetic CDF test; not a research forecast.`，`is_private: True`、`included_forecast: True`，每個 probe 最多 1 次留言請求。
- 每個 probe 最多 1 次預測請求與 1 次留言請求；兩題型合計上限 2 次預測 POST、2 次留言 POST。請求間隔沿用既有 v15 規則。遇任何 4xx 或 429 立即停止全部後續寫入。結果為 unknown 時只做唯讀對帳，不重送。
- 讀回 unknown 時，額外記錄診斷：讀回列的鍵名清單，以及每個候選欄位（`forecast_values`、`continuous_cdf`、`centers`、`interval_lower_bounds` 等，若存在）的型別與長度，不記錄數值內容；供 Claude 修正 `NUMERIC_READBACK_FIELD`。不得因此自動重送。

## 4. 帳冊

沿用 `data/v15_probes.jsonl` 與 `data/submissions.jsonl`，事件格式依既有 v15 慣例，新增欄位要有版本標記。不得寫入任何比賽題目的預測。

## 5. 暫時試驗模式工作階段 `m5_probe_session.py`

單一命令完成：

1. 依序取得 `data/cycle.lock`、`data/scheduler.lock`、`data/submit.lock`、三條 `data/forecast.<player>.lock`（非阻塞重試，最多等 25 分鐘；逾時不做任何變更）。
2. 確認目前 `config/submit_mode.json` 為 `mode == "live"` 且 `approved_bot_id == 311763`、`config/STOP_SUBMIT` 不存在；保存原檔位元組與 SHA-256。
3. 以原子寫入改為 `{"mode": "trial", "approved_bot_id": 311763}`，追加 `data/mode_changes.jsonl` 的 `mode_changed` 事件（authorization `PLAN D107 A1, D122, D123 M5-1`）。
4. 依序執行 numeric、discrete 探測（第三節）。
5. 不論成功、失敗或例外，都在 `finally` 以原子寫入還原原檔位元組，並核對 SHA-256 與原值相同，再追加回到 live 的 `mode_changed` 事件，才釋放鎖。還原或核對失敗時建立 `config/STOP_SUBMIT`、記錄事件並以非零碼結束（不得留在 trial 模式而不停送）。
6. 輸出每個題型的結果摘要（found、unknown、rejected、no_candidate）與寫入次數。

## 6. 測試（合成資料與注入傳輸，不連網）

- numeric 與 discrete（含小 N 離散題、對數尺度、開放與封閉端點）合成 CDF 通過本地驗證器；plan 被竄改任一縮放欄位或 CDF 值時拒絕。
- gate：`probe_question_types` 只在 trial 模式且 bot testing area 放行；live 模式、其他場地或未傳參數時，開關 false 仍拒絕。
- 讀回 found、unknown（不重送且記錄診斷鍵名）、429 停止後續寫入、留言讀回。
- 工作階段：正常、探測例外、還原後核對不符（建立 STOP_SUBMIT）三種情境，原檔位元組都被還原或停送；鎖忙碌時不改任何檔案。
- 舊版 v15 binary／multiple_choice 與 reuse 情境既有測試全部通過。
- 完整隔離回歸。

## 7. 審查與執行

本任務涉及送出與模式切換：GPT 實作後須經 Gemini 獨立審查（D107 A4）與 Claude 完整審查，才由 Claude 在正式環境執行一次。執行結果全部 found 後，Claude 才把 `config/question_types.json` 改為兩者 true，並記錄 PLAN §16 與 TG。
