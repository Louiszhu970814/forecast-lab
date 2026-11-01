# MiniBench 正式參賽前的驗收清單

建立：Claude，2026-11-01T13:35Z。依據：PLAN v1.24 §3.6、§3.7、§10 M1～M4、§12（D77 取消固定的 14 天試驗期）。每一項都要有證據；狀態更新時在這份文件追加，不覆寫舊的判定。

圖例：✅ 通過（附證據）｜🟡 進行中或部分完成｜❌ 未開始或未通過｜👤 需要 Louis 決定或操作

## M1 隔離與基準

| 項目 | 狀態 | 證據或缺口 |
|---|---|---|
| 全域基準人工審查與自動載入測試 | ✅ | `auto_1793528405575_83ee265b`（passed）；最新基準 `base_1793538013655_8fc7b859`，三線都是 `ok`（D70、D71） |
| 三線六種正式設定的隔離驗收 | ✅ | `m2_report.py` readiness 的六項 `production_acceptance` 都是 ok；Gemini 在 Ultra 下重跑通過（2026-11-01） |
| 單線端到端（M1） | ✅ | M1 已完成，紀錄在 `docs/tasks/M1.md` |

## M2 三線量測（`m2_1793529615987_7867f9f1`，進行中）

| 項目 | 狀態 | 證據或缺口 |
|---|---|---|
| 20 題、三線第 1 層有效率 ≥ 90% | 🟡 | gpt 20/20；claude 19/20（46289 因誤判永久缺，95%）；gemini 11/20，仍在跑 |
| 整合、升級、第 2 層與留言 | ❌ | 第 2 層要等三線第 1 層都完成才開始 |
| 每題每層的耗時、token、額度 | 🟡 | 量測中；Ultra 下 Gemini 的成本在互動式 agy／Antigravity app 開著時記為不確定（D75） |
| 各 CLI 額度用完的錯誤樣態（V5） | ❌👤 | 只有 claude 有實例；gpt（Pro 方案）與 gemini（Ultra、管線會先擋）很難自然耗盡。要決定：刻意耗盡測試，還是把這一項改成「觀察到再記錄」 |

## M3 必要驗收（不設固定天數）

| 項目 | 狀態 | 證據或缺口 |
|---|---|---|
| 一、機器人帳號 | ✅ | Metaculus ID 311763，`is_bot`、`is_primary_bot`、`api_forecasting_access=enabled`（D70） |
| 一、參賽表單 | ❌👤 | API 的 `registered_campaigns` 是空的；請 Louis 填寫 FutureEval／MiniBench 的報名 |
| 一、核准切換到 `trial` | ❌👤 | `config/submit_mode.json` 仍是 `local`、沒有 `approved_bot_id` |
| 二、`trial` 端到端（launchd 無人介入地完成抓題、預測、送出、留言、讀回） | ❌ | 需要真實 Transport（見 V15）、持續模式的題集設定，以及安裝 launchd 週期（需 Louis 核准） |
| 三、送出測試（錯題、錯帳號、跨賽、修訂、同步題、已關閉、平台已有預測、回執遺失、讀回失敗、重啟、只補留言、停用開關） | 🟡 | 離線狀態機與假 Transport 測試已涵蓋（M3-W1、W6、W7、W8）；真實 API 上的驗證要等 V15 |
| 四、覆蓋率模擬（看結果前固定期間與分母，已支援題型 ≥ 90%） | 🟡 | 模擬器 `coverage_sim_v3` 已完成；尚未用真實的開題時間與 M2 實測耗時登記並執行 |
| 五、每週上限 | ❌ | claude、gpt 的 `weekly_attempts_cap` 仍是 `null`，等 M2 量測結束後依實測填入 |
| 五、持續模式跨週運作 | ❌👤 | 題集只能用 7 天、沒有自動滾動，`config/cycle_campaign.json` 也還不存在（稽核 B4）。要先決定每週上限的政策 |
| 六、V11（題目開放時間的實測分布） | 🟡 | `data/poll_log.jsonl` 已有 213 筆；還沒有針對 MiniBench 的分布報告 |
| 六、V15（真實送出、留言、讀回、重複送出的平台行為、留言長度上限） | ❌ | `submit.py` 預設仍是 `OfflineTransport`；要在 bot testing area 實測（M3-W4，需要切到 `trial`） |
| 七、MiniBench 系列白名單與每輪 ID 核對 | ❌ | `live` 目前只支援靜態 ID，`minibench.id` 是 `null`，所以現在送不了 MiniBench。需要新任務實作 |
| 七、規則核對經 Louis 核准（§3.7） | ❌👤 | `docs/tournament_checks/futureeval.md`（含 2026-11-01 MiniBench 補記）已完成，§15 還沒有 Louis 的核准紀錄 |

## 切換到 `live`（§3.6）

| 項目 | 狀態 | 證據或缺口 |
|---|---|---|
| MiniBench 系列列入送出白名單 | ❌👤 | 等上面全部通過後，由 Louis 核准並記錄於 §15 |
| Louis 核准正式參賽 | ❌👤 | 同上 |

## 其他待決事項（不阻擋 MiniBench）

- 👤 Louis 以人類身分參加 Cup 期間，機器人是否還能把 Cup 當試驗場地送出（D76）。建議在核對清楚之前，`trial` 只用 bot testing area 與主站題目；要改 `trial_ground_types` 需 Louis 核准。
- Market Pulse（12/21 截止）要先完成數值題與題組（M5 的一部分）、該賽的規則核對與專屬送出契約。
- 主賽是否提前、是否調整 M4／M5 的前置條件與 D49，另列取捨給 Louis。
