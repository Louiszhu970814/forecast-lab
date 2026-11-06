# D139：30 分鐘發現、人類賽入口、Market Pulse

狀態：**已部署啟用**（2026-11-05T05:57:07.361Z）。GPT 整合；Louis 明確指示「好的1 2 3照順序全做 做完之後直接啟用30分鐘 跟開始參賽 market pulse」。本文件為操作說明，PLAN 是唯一規格來源。

## 固定契約

1. `discovery_v1`：新題全量搜尋預設 1,800 秒。launchd 每 60 秒執行便宜的本地檢查；沒有搜尋到期或已知未完成工作時不開 worker／模型／維護程序。已知題刷新、截止整合、送出／未知對帳依既有 60 秒排程醒來與即時完成回呼，不等 30 分鐘。worker 的 510 秒准入、4770 秒 handoff、5100 秒上限保留，原 600 秒常數只為 worker 上限取整，與新題間隔無關。既有 780 秒 idle lookahead 保留為保守處理窗口。
2. 成功完整搜尋後才追加 `data/discovery.jsonl`，以固定 campaign hash 綁定時間與已知題 metadata。重啟不重置時鐘；失敗不推進；時鐘倒退重新取樣；已知 upcoming 到開題時間可提早搜尋。搜尋之間只 GET 已知、未凍結且未到排定截止的 post，仍以最新子題身分、歸屬、尺度、內容與時程刷新。未知送出沿既有意圖對帳，不能因 cadence 重跑模型或重送。
3. 任一已觀測排定開放窗口小於 1 小時，或首次發現的決策剩餘時間低於 3,240 秒既有最壞研究界線，該 campaign 後續縮至 300 秒。只縮短、不自動回升；沒有自動升至 60 分鐘。記錄原因、實際完整搜尋間距、首次發現延遲與剩餘決策時間。這是排程政策，不保證睡眠／網路／持鎖／送出尾端時仍準時。
4. `advisory_v2` 的人類賽清單獨立在 `config/human_tournaments.json`，初始只含已核准的 Cup 33108；Market Pulse 不混入人類名單。手動 `list` 只盤點，列篇／組／子題數、最急 3 題、UTC／台北時間、支援與研究狀態；本人是否手送一律未知，不能由 bot token 推測。加入其他人類賽前更新已核實數字 ID 與規則核對。
5. 手動 `research` 一次明確選一個 post／child／tournament，三線各最多一次初始呼叫、無自動重試；使用獨立 batch。主持人收到「做最急幾題」可按清單順序處理最多 3 題，不再逐題重問。跨批重複須有明確更新理由；結果未知先恢復，不能拿新 batch 重跑。六鎖、主賽／MiniBench open 或 imminent 避讓；Market Pulse 依本地工作狀態避讓（可執行、待發布、截止或重試已到、即將開題時避讓；所有線皆等待額度／停線且未到截止時可讓人類研究使用受保留限制的容量）、保留預期新題兩層＋修復共 22 次容量、原安全停線與共享消耗記帳皆保留。
6. 人類研究輸出獨立，不進 bot answers／submissions。英文報告與 Gemini 最後留言仍由主持人依 D134 完成有界寫作並記錄實際 CLI 用量，供 Louis 判斷、手動提交；`research` 不冒充已完成這個寫作階段，也不觸發寫作排程。D132 兩道已完成題的舊入口及恢復保留。
7. `market_pulse_v1`：bot 311763，project 33131，slug `market-pulse-26q4`。預先固定為首次有剩餘容量即研究，主賽／MiniBench 兩層都優先，Market Pulse 內依子題截止排序；在本有限窗口每個子題最多一個預測，更新 0 次、各附一則含子題 ID 的私人推理留言。這是本專案節省額度的政策，不宣稱官方禁止更新或保證最大 spot score。官方資源頁提及持續更新，而本季規則未訂最低更新頻率；判讀與來源見規則核對。
8. Market Pulse 首層／升級都保留 `2×expected_new_questions + repair_reserve + 2×目前高優先題數` 的次數容量；原 Gemini 個人保留與其他限制照常。高優先題的升級不替尚未做的 Market Pulse 首層預留。此政策不增加 130／200／130 上限。
9. `question_mapping_v1` 同一映射用於抓題、precheck、own-history baseline、讀回、結算及關題稽核：保留父 post、group ID、子 question ID；共同描述／解題條件與子題條件合併；時間、狀態、數值尺度採子題。重複 ID、混合結構、錯父 ID／group、缺少子題拒絕。group 只為 Market Pulse 的 live 送出開放，conditional 不支援。原單題行為不變。
10. `continuous_v11`／`budget_handoff_v3` 只容許 D105 → D118 → D137 → D139 三條不可改寫的接續，D139 必須直接承接已登記 v10／v2；第四段、分叉、換根、重置用量、增加 cap、改固定配置、延長或縮短原 end、rollover 均拒絕。v1／v2 原限制保留。原根起點及所有已用／未知呼叫留存，截止仍 **2026-11-10T04:00Z（台北 11/10 12:00）**。不自動續期。

## 手動入口

```sh
.venv/bin/python src/human_advice.py list --top 3
mkdir -p data/advisory/<new-request>
PATH="$PWD/launchd/bin:$PATH" .venv/bin/python src/human_advice.py research \
  --batch data/advisory/<new-request> --tournament 33108 --post <post-id> --question <child-id>
# 有明確更新需求時才加 --update-reason 'Louis 指定的更新原因'
.venv/bin/python src/advise.py --batch data/advisory/<new-request> --recover
```

列題不啟動模型。完成研究後，主持人讀取該人類 batch 的結果，完成英文報告與 Gemini 留言，不讀 bot 開放題輸出。機器人執行中的 lock busy、未恢復的 unknown 或額度不足要如實回報，不停止 bot 來搶研究額度。

## 工程與部署

工作副本建自 2026-11-05 當時的工作樹，未以 git HEAD 蓋掉既有未提交成果。舊正式服務持續運作至部署安全點。合成測試涵蓋 cadence 重啟／失敗／短窗／已知刷新、題組映射與不同截止、兄弟題既有預測、讀回錯題／基準、重啟一次送出、原兩題、人類新題／去重／機器人避讓、優先序／預留及完整 budget root。

對相同 3 小時窗口，理想最壞新題延遲 600／1800／3600 秒時，扣除既有關題前 30 分鐘決策截止，分別餘 140／120／90 分鐘；不是實測覆蓋率。歷史時間稽核的 45 題最新窗口皆 180 分鐘，但有缺失時段，不能推論不存在漏掉的短窗題。維持 D114 完整觀測與 D129 ITT 缺漏限制，不因這次工程測試而解除。

正式部署須綁定同一來源 manifest、測試證據與兩份實際 Claude／Gemini CLI 複審；主持人檢視結果後在六鎖與 idle 安全點暫停舊 timer，先切 local、套用精確受審檔案、前後 smoke／三線兩層 preflight、追加註冊、最後切 live，再安裝 60 秒 heartbeat 與啟動。登記前失敗可恢復原來源；登記後失敗維持 local／停排程，不回滾或重寫帳冊。只核對安全 metadata；沒有 forecast_ok 就不能宣稱首筆預測成功。

操作證據：`data/operations/d139-expansion-20261105/`。實際完成時間、雙審與部署證據將在完成後追加。

第一輪修正：discovery 身分排除執行時 questions 並複製設定；首見以跨 campaign 歷史及搜尋前已有 snapshot 判定；known 僅存接受題、跨場地先到優先，未開放題等開題時完整搜尋；刷新批次寫 snapshot。等待題只在完整搜尋、retry 或 decision deadline 時再評估，不每分鐘開完整 worker；未知提交讀回即使子題已關仍可喚醒。Market 人類避讓共用本地可執行判斷，正式准入仍由既有預算、安全與六鎖控制。

部署故障狀態：登記前恢復來源後排程仍 disabled；這是 fail-closed，不代表主賽仍運行。應用同份 reviewed gate 重跑 apply，再重跑 kickstart；登記後不可回復舊 campaign。kickstart 可在已寫入新 plist 的中斷點以同份 gate 恢復；不使用 -k。

Upcoming snapshot 的 t0 表示最早本地觀測，會保守提早來源時序界線；研究仍由 open/eligible 閘門控制，沒有預先研究或預覽。既有 ITT 統計需保留這項觀測方式差異，不宣稱跨版本直接可比。

## 實際交付（2026-11-05T05:57:07.361Z）

- Claude／Gemini 官方 CLI r2 對同一來源版本的A／B凍結封包完成複審，兩模組共四份PASS；所有較早輪次完整保留在reviews。提供來源審查，不聲稱兩位自行跑測試。
- 最終完整回歸 3454 PASS，既有3395節點保留；部署故障測試及專項細節見驗證manifest。正式root部署前後smoke與三線兩層共12項preflight全通過，無waiver或line_guard。實際預算metadata副本六組before/after相同，保留root用量、caps及截止。
- 六鎖套用精確受審來源，登記唯一D139後繼，continuous ledger原prefix不變。舊600秒timer暫停後，安裝並核實60秒本地heartbeat，live contract綁同一campaign/hash，再kickstart（無-k）。新題搜尋預設1800秒；觀測到的實際interval=1800秒、縮短原因=[]。
- 截至2026-11-05T06:09:04.009Z：新campaign已active且有成功discovery；開放known child數按venue：{"main": 1, "market_pulse": 13}。Market Pulse模型開始計數：{"gpt": 2, "claude": 3, "gemini": 2}；提交狀態計數：{"rejected_precheck": 1, "intent": 1, "request_started": 2, "unknown": 1, "forecast_ok": 1, "comment_ok": 1}。forecast_ok才是受理證據，comment_ok另列；若計數0，代表尚未完成，不能把啟用當成已提交。未預覽開放題答案。
- 首筆Market實證：qid46208／post46008，UTC06:06首次forecast request的回覆無法辨識，保留unknown；06:08:41同request_id讀回確認forecast_ok，06:08:49私人comment_ok。兩個request_started分別為forecast與comment，沒有重送forecast。上列unknown與rejected_precheck是追加帳冊歷史事件，並非此intent目前仍未決。
- 人類賽通用入口已部署；本輪沒有指派新的人類題目研究、沒有任何人類帳號提交。既有人類報告保持，之後按Louis詢問使用入口。
- 非阻擋追蹤：known刷新結構失敗可能等至下一次完整搜尋；heartbeat帳冊／設定例外仍fail-closed；已提交題revision及API晚轉open的喚醒效率留待安全metadata觀測。D139前後t0語義分開解讀。readback的ValueError已有外層Exception→unknown保護，非缺口。上述不改本輪受審程式或開放題策略。
- 原固定截止仍2026-11-10T04:00Z（台北12:00），沒有自動續期；M3完整觀測、M4官方結算與ITT缺漏限制保留。未commit／push。

來源與驗證：[來源manifest](../../reviews/d139-source-manifest.json)、[驗證manifest](../../reviews/d139-verification.json)。本段及導覽／PLAN中的完成狀態為事後事實更新，程式與設定仍為受審位元組。
