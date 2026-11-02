# D111：自動讀回缺口與送出時限

主持／實作：GPT。2026-11-02 UTC。範圍依 D108 接手監督、D107 A4 的送出／讀回獨立審查；沒有查看任何開放題的機率、理由、留言正文或模型原始輸出。

## 實機證據與目前狀態

- 41219 的預測只有一筆 request_started，launchd 於 14:38:16 讀回確認；私人留言 14:38:44 成功回執。主持人 14:48:52 另做 GET-only 留言讀回 found（`data/e2e_readbacks.jsonl`），它不是 launchd 自動對帳證據。
- 41418 的預測也只 POST 一次，15:05 前已由既有 launchd 自動對帳並送出留言。15:05:06 狀態為兩題 forecast_ok／comment_ok、unknown 0；Gemini 四題第 1 層完成，三線 ready。41628／42336 的升級與完整端到端仍待完成。
- `submit_v9` 遇有效成功回執會記 operation_ok，後續 `_resume()` 略過它；因此一般留言可能永遠沒有獨立讀回。先前的成功回執不能直接當作 M3 所需的自動 found。
- on_finalized 曾在 120 秒送出程序上限中止。首次新 trial 意圖會做候選前檢，再做送出前檢；三個競賽排除 project 各一頁時，到預測 POST 回應約需 31 個 API 請求，尚未含留言與額外讀回。每請求既有間隔為 3.5 秒加抖動，網路、分頁及同時輪詢還會加長時間。這是程式路徑估計，不是平台延遲保證。

## 已審查並套用的修正（UTC 15:15:41）

`submit_v10` 的正式 CLI `run`／`reconcile` 啟用成功回執的補充 GET 驗證。原已 `reconciled=true` 的成功已有讀回證據，不重查；其他成功按原 request 的開始時間、預測基準、帳號、目標與完整 payload 核對。只追加 ID、雜湊、時間、found／unknown、錯誤類型到 `data/submission_readbacks.jsonl`，不輸出正文；同一 frozen lookup 已 found 即去重。

驗證結果與正式送出狀態分開保存。讀回失敗、延遲可見、錯帳號、錯 request、錯 payload，甚至回覆 not_accepted，都不會推翻已受理回執，也不會進入重送邏輯。下次 CLI 只重做尚未 found 的 GET。local 不做這些查詢，網路期間不持有帳冊交易鎖；仍由 submit.lock 互斥。A2 probe 原本直接使用 `_resume()` 的流程不變。

`cycle_v7` 將送出子程序上限由 120 改為 300 秒，保留請求節流、模型 timeout、510 秒新工作准入與 600 秒排程。現有公式相應得到 handoff 4770、worker 5100、supervisor 5110 秒。300 秒用於容納有節流的正常前檢與讀回，仍可能不足以完成大量候選或異常網路；既有帳冊復原與 unknown 不重送規則繼續適用。覆蓋率模擬的凍結 engine policy 包含 cycle 版本與時限，舊情境不會被悄悄當作新版本結果。

## 測試與審查

- 16 個新增讀回案例在原版全部失敗；修正後通過。包括重啟去重、舊 ACK 補讀、延遲、矛盾拒絕不重送、各種身分／payload 不匹配、錯誤不洩露、local 零 I/O，以及網路不阻擋帳冊交易。
- 初輪相關測試 526 passed／5 failed；五個失敗皆是 cycle 測試仍固定舊時限，更新後 531 passed。
- 初輪完整測試 2050 passed／4 failed：三個覆蓋率模擬 fixture 仍以舊時限構造情境；另一個是隔離副本 local 設定多帶 `approved_bot_id:null`，與既有精確配置測試不符。只修隔離測試設定，沒有變更執行中的 trial 配置。
- 更新模擬的超時、跨週期排隊、handoff 情境，另新增 200 秒送出能在新上限內完成的案例；完整隔離 **2055 passed，79.64 秒**。
- Gemini Argon high 獨立審查於 UTC 15:12 開始，上限 900 秒；回覆 **PASS，無 blocking**，確認狀態隔離、GET 去重、local 零 I/O、交易鎖邊界與新時限公式。審查期間持有 cycle／scheduler／submit／三線鎖，另有主持人自行建立的 STOP_SUBMIT。沒有並行預測。Claude 隨後文件複核 PASS；採納其對來源範圍、雜湊證據、解鎖責任與新時限再逾時處理的補充。

## MiniBench 與 bulk 核對

UTC 14:50:17 官方 API GET-only 核對：alias 33135；已開始的 ongoing IDs 為 33132、33135；試驗排除集合包含主賽 33121 及這兩輪。系列送出白名單仍 false。證據 `data/m3_discovery_checks.jsonl`；不代表 live 核准，也不保證未來輪次 ID，任何後續使用前仍由程式重新發現並核對。

Bulk 候選維持後續優化：官方 `bulk-forecast-comment` 以交易合併預測及私人留言，但成功回應仍是空物件；`bulk-forecast-read` 讀取自己的預測，使用原有 serializer，無法直接取代留言對帳。這些是[官方後端原始碼](https://github.com/Metaculus/metaculus/blob/main/questions/views.py)的行為；[API 文件](https://www.metaculus.com/api/)另說明 gateway 可限制帳號的實際回傳。據此推論，遷移 bulk 並不能單靠回執解決本次缺口，而且主要前檢仍然存在。此次沒有對實際帳號探測 bulk 端點，結論僅來自官方程式與文件；沒有呼叫 bulk 寫入、沒有遷移送出端點；若之後採用，需另作原子失敗／回執遺失／分項讀回驗收。

## 完成條件

修正已經 Gemini PASS，UTC 15:15:41 在互斥鎖保護下套用，逐檔以 bytes 比較確認與受測副本一致，SHA-256 與套用時間追加在 `data/development_checks.jsonl`；PLAN v1.60。實機效果仍待後續週期驗證。D105 的每種題型都要有自動三線預測、送出、私人留言，以及相同 request 的 found（正式成功列 `reconciled=true`，或本次驗證帳冊 found）。兩題型完成前不標記 M3 第二項通過；四題與 unknown 處理完再做 A2。A2 三情境之間仍須至少 10 分鐘完全沒有 Metaculus API 活動。結束回 local，live 仍由 Louis 另行核准。

操作責任：GPT 在文件複核完成後只解除本次自建 STOP_SUBMIT，釋放 cycle／scheduler／submit／三線鎖，重啟狀態監看，並以既有 launchd 接續原窗口；不另手動送出或重算預測。隨後核對正式 CLI 所寫的自動讀回證據。若新版仍出現 300 秒 TimeoutExpired，先從 request 狀態判斷是否只中斷前檢或留下未知寫入，再評估原因，不自動再加時或重送。

D111 恢復證據：UTC 15:19:48 只移除 GPT 本次自建 STOP_SUBMIT，審查鎖已釋放；15:19:53 狀態監看重新啟動（session 7274），unknown 0／hazards=[]。主持人以既有 launchd 的 kickstart 接續同一個凍結窗口，未重裝或改間隔；這次啟動為修正後恢復，不列入自然排程間距估計。尚待新週期的實機讀回。

## 新版覆蓋率重播（UTC 15:23）

兩個情境均於 15:23:27 先登記後執行，沿用 D101 的同一份歷史開題表（schedule hash `9676300fe06bb93d453c1ae95e3deab828a05d8bbf7c3cf1f60e147066c83b51`）、每線上限、耗時樣本、種子與 20 次重播；沒有覆寫舊情境。

| 新情境 | 固定差異 | 支援題準時覆蓋 p10 | 全部題目覆蓋 p10 |
|---|---|---:|---:|
| `d111_engine_replay_c130_c130_g200_o300_n10` | 只換成 cycle_v7 引擎政策 | 100% | 64.42% |
| `d111_submit270_stress_c130_c130_g200_o300_n10` | 另將每題送出總耗時固定為 270 秒；診斷用 | 98.51% | 63.46% |

固定觀察範圍有 104 題，其中支援 67 題、不支援 37 題。第一情境每次都覆蓋 67 題；第二情境的 20 次中有 3 次各晚送 1 題，其他漏答都是未支援題型。正式重播的 p10 繼續高於原訂 90% 門檻；壓力情境也高於門檻，但它是附加診斷。結果在 `data/coverage_sims.jsonl`／`data/coverage_sim_results.jsonl`。

這是沿用 D101 凍結樣本的條件式重播，不是新一輪實機覆蓋率；Gemini 第 2 層的原耗時樣本只有一筆，尚不能由模擬推定所有未來研究時長。新版本 300 秒的實機送出時間與本輪後續工作仍須觀察。

## D112 實機驗收結果（UTC 15:33）

`cycle_1793632794963_9bfce6be`（15:19:54.962～15:32:24.525，cycle_v7）自動完成以下讀回。預測使用正式帳冊的 reconciled=true，留言使用同一 request、同一 frozen lookup 的新驗證帳冊 found；各個證據時間都落在已完成的 launchd 週期內，沒有採用主持人的額外 GET。

| 題號 | 題型 | 預測自動 found（UTC） | 私人留言自動 found（UTC） | 預測／留言 POST 次數 |
|---|---|---|---|---:|
| 41219 | binary | 14:38:16.075 | 15:26:11.766 | 1／1 |
| 41418 | binary | 15:04:34.859 | 15:26:27.022 | 1／1 |
| 42336 | multiple_choice | 15:31:39.795 | 15:32:24.017 | 1／1 |

15:33:26 的 `data/e2e_acceptance_checks.jsonl` 記錄 both_types_automatic_found=true、all_four_complete=false、unknown_requests=0、hazards=[]。三線均完成這些題目的第一層；MC 三線第二層也已完成。**M3 第二項通過；第四題 41628 仍待 Gemini 第 2 層與送出，A2 尚未開始，M3 整體與 live 都沒有宣稱完成。**

本輪 on_finalized（含本地發布與送出）耗時 171.938 秒，在新上限內完成；它先留下 MC 預測 unknown，最後 submit 在同一輪自動對帳成功。整輪仍顯示 attention，反映中途有 unknown，不代表目前仍有未解決請求；errors=[]，沒有 TimeoutExpired。這一輪支持 300 秒能容納此案例，不代表任意批次都能完成。

Claude 對 D112 的有限驗收結論與模擬報告複核 PASS，沒有 blocking。主持人依其提醒，核對器改為只接受唯一的完成週期時間區間，並將核對器 SHA-256 及正式來源提交 `50a0be5` 追加到 `data/development_checks.jsonl`。自動性依據包含本次實際操作紀錄、cycle 互斥及正式 CLI 唯一寫入新帳冊的路徑；僅時間落在週期內本身不是獨立的程序身分證明。

## D113 第四題與收尾（UTC 16:35）

41628 的預測於 15:53:36.006、私人留言於 15:54:21.263 自動 found，各一次 POST。完成週期 `cycle_1793634145052_4cea3378`（15:42:25.052～15:54:21.834，cycle_v7）無 errors；15:55:40.776 驗收紀錄為 all_four_complete=true、unknown_requests=0、hazards=[]。三線各完成 6 次實際工作，四題全部驗收通過。

A2 三情境也已完成，16:35:10 已回 local 並卸載封存本次預測排程，見 [A2 報告](D107-A2-BTA-REUSE-PROBE.md)。M3 整體仍有 V11／實際輪詢重播的證據缺口；本報告的覆蓋率數字只對凍結的模擬假設成立，尤其固定 600 秒啟動格點不等同本機觀測的前輪結束約 600 秒後再啟動。最新對照與下一步見 [M3-READINESS-D113.md](M3-READINESS-D113.md)。
