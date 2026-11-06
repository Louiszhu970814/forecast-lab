# D145：CLI 更新核對與 Claude 恢復

- 日期：台北 2026-11-07；所有操作時間以下均為 UTC；PLAN v1.99，GPT 整合。
- 授權：Louis 要求檢查 Codex 更新是否停線、有阻擋先恢復，並取消 Codex／Claude／agy 因 CLI 升版而停線的規定。

## 結果

Codex 已從 `0.166.0` 更新為 `0.167.0`，未因更新停線。自然排程 `2026-11-06T22:49:39.847Z` 的 GPT 前檢已為 ready；本輪 `22:53:19.281Z` 重新檢查兩層均通過。D143 的正式程式已將三家 CLI 的已知版本差異列資訊性，無須再次修改執行邏輯。

同一輪另發現 Claude 因 `claude.settings` 的一般設定雜湊變更而被擋：只有 `model`、`modelSettings`、`theme` 三個鍵，受保護內容雜湊前後皆 null。這與 Codex 更新無關。依本輪恢復授權及既有 §7.2／§7.6 流程，完成三次 `claude-haiku-5-5` 合成隔離呼叫、29 項全 PASS；沒有使用實際預測題。

- 驗收：`acc_1794005761652_a9a745b5`，完成時間 `2026-11-06T22:56:01.654Z`。
- 恢復：`2026-11-06T22:56:37.662Z`；基準 `base_1794005797661_7b933a73`，只替換 `claude.settings`，其餘來源與版本逐項保持原核准值。
- 最後前檢：`2026-11-06T22:57:26.939Z`，三線兩層共六項全部 `ok=True`、`waivers=[]`，三線 line guard 均無停線。
- 正式執行版本：Codex `0.167.0`、Claude 專案 pin `2.1.316`、agy 專案 pin `1.4.12`。本輪沒有升級或移除既有 pins。
- launchd 預測／恢復／TG 三個服務已載入；cycle 間隔60秒、恢復間隔300秒。服務等待下一次排程的 `not running` 狀態不代表停線。

## 規則與驗證

PLAN §7.2／§7.5 原本仍先寫「CLI 改版須自動重驗」，再附上 D143 已取代的註記；本輪直接改為現行規則：**Codex、Claude、agy 已知版本變更只記錄，不重驗、不停線**。§7.6 同步澄清觸發範圍與尚未落地的自動重驗。未知版本、實際工具違規、證據／runtime／模型型錄不符及其他設定變更仍依原規則處理。

既有版本回歸案例擴為三線參數化；`tests/run_offline.py -q tests/test_baseline.py tests/test_forecast.py tests/test_development_catalogs.py tests/test_auto_reaccept.py`：`192 passed in 0.84s`。正式程式、adapters、模型、prompt、額度、截止與排程均未修改；本輪未呼叫其他代理審查，不宣稱雙審。

恢復時持 cycle／scheduler／submit／三條 forecast 鎖；合成驗收由既有 `acceptance.run_line` 持 Claude 鎖。驗收完成後，主持腳本一度把包含 judge／harness 的完整驗收雜湊集合誤當成三項 runtime 集合，故在寫基準前安全退出；修正為完整驗收指紋與 runtime 子集各自核對後，沿用同一份 PASS 證據完成恢復，沒有重跑模型。最初另一個只查 Claude 的前置檢查也因未查 Gemini credits 而在零呼叫時退出，之後將該檢查限縮到 Claude／共同來源；最後六項前檢另查即時 credits，全部通過。

操作證據位於 `data/operations/d145-cli-update-20261107/` 的 `recovery.jsonl`、`validation.jsonl`、`completion.jsonl`，驗收原始證據位於 `data/acceptance_runs/accrun_1794005693147_eeb9049b/claude/evidence.json`。帳冊只追加，未新增 line_resume；本輪主持人未讀取開放題預測正文／機率／理由，未發起新預測或預測 POST，未重跑已提交題。原排程自行依既有授權繼續。

交付後 Louis 已指示 commit／push；實際提交與遠端核對回執記於 git 與手機進度帳冊。
