# D140：停線自動診斷、恢復與補做

狀態：**COMPLETE（2026-11-05）**。已部署、啟用、實際自動恢復，3 道待補題全部取得 forecast_ok／comment_ok。GPT 整合；PLAN 為唯一規格來源。

Louis 指示停線可自動偵錯恢復，並明確補充以答題為優先，不能因一線失敗放棄整題；修改完成後直接恢復、完成未完成題並提交，不再等待確認。僅限已核准 bot 賽事與既有有限窗口，人類賽仍本人手動提交。

## 固定政策

- `line_recovery_v1`／`forecast_pipeline_v11`；恢復器是獨立每 300 秒的本地恢復檢查；新題搜尋仍 1800 秒、提交與已知工作仍 60 秒 heartbeat。最長單次檢查 120 秒；僅持自身恢復鎖與 Gemini 線鎖，其他線與 cycle／submit 繼續。無網頁研究或模型呼叫；官方 quota／CLI 版本與本機基準前檢不消耗模型呼叫額度。
- 首批唯一自動恢復分類：已釘選且 SHA 相符的 AGY 1.4.12，`view_file` 工具名稱多一個 ASCII 前置空白。原始 stream 必須完整，該步恰為 ACTIVE→ERROR，CLI 錯誤精確等於未知工具拒絕，兩階段都沒有輸出，參數不變且目標為本 conversation 的 `.system_generated/steps/<n>/content.md`。該 n 必須是本 attempt 中較早已成功的 read_url_content step，拒絕 symlink／非嚴格 JSON／重複鍵或未知事件結構。其他工具均在原 allowlist、沒有越權／拒絕／Metaculus 讀取；帳冊、兩筆違規與 raw 步驟／計數完全對應。不是把任意字串 trim 成合法工具。
- 原嘗試須 initial、沒有 prev、incomplete、model_check verified，且沒有本次 audit flags、非零 credits 等 safety_stop、子代理、憑證／基準 waiver 或未知 quota 結果。任何其他 hard stop、未知 start、未完成外部用量、確認暴露、尚在額度或模型冷卻期間，都保留停線。歷史 suspected keyword hits 不自動升格成確認暴露，也不清除其紀錄。
- 快速路徑只適用於「被 CLI 拒絕而未執行，既有隔離條件沒有變」：事故 run_config_version 須相同，Gemini／common 的 acceptance 帳冊只接受已知類型與完整時間，均須早於事故開始，不豁免 autoload 來源；capture 實際選中的 baseline、兩層 acceptance／production ID 各須唯一、已通過且時間早於事故，production model／effort／tier 符合原設定，ID／時間／row SHA 明確綁定在 resume；兩次獨立當前快照與 Gemini 的兩層 preflight 均須通過，原正式 model／effort 的驗收證據及 SHA、runtime／judge、baseline／共同來源、CLI wrapper／實體 pin、設定均相同，無 waiver。因此沿用仍有效的正式隔離證據，不為一次拼字拒絕再耗 6 次模型驗收。任何環境改變或真正隔離疑慮不走此路徑；D124 auto_reaccept 仍 observe，不核准新外掛／設定／版本。
- 開始及提交恢復決定都在 ledger transaction 中比對完整本線安全 metadata 與原 stop；不把別線的普通更新當作本線故障。每個 stop 只啟動一次，未結案也不自動重跑；24 小時最多 3 次此精確類型恢復。自動 resume 只覆蓋精確 lifts stop 與安全 metadata SHA 相符的該 attempt typo，不推進其他安全事件的時間豁免；人工歷史 resume 語意保留。追加 `line_resume`，by=`line_recovery_v1`，不冒用 Louis；舊 raw、答案、稽核及停止記錄不改。
- 原問題仍在決策截止前時，resume 同筆附綁定 failed attempt／question／snapshot／tier／metadata／raw SHA 的 retry_grant。它只開放原有 initial→rerun 的一次槽，沒有新增 attempts 配額；已做過 rerun 的題／線／tier不再給。實際 CLI start 前，在原線鎖及 ledger transaction 再核對 grant、原證據、最新安全與未知 start，必要 binding 欄位（baseline／runtime／設定／pin／wrapper／judge／兩層 proofs／事故證據）缺漏或變更均拒絕，未啟動者由原 admission_outcome 釋放預留；記 `recovery_grant_id`，已開始或中斷未知均不得重用。再次失敗不再追加普通 rerun。其餘原本允許的 format repair 仍受既有一次修復與額度／時間限制。
- scheduler 自行選題、計算優先序，所有 budget／quota／deadline、封存輸入與身份檢查照常。已提交／已凍結 final 不重開；人類主持不檢視開放題預測。舊失敗答案仍不可採用，補做是新的模型嘗試。該題已過期時仍可恢復未來工作，但不給該題 grant。
- 修正 discovery 對舊 line_stopped job 的等待：該玩家有晚於舊 job finished_at 的實際 resume 且當前 line guard 已解除才可醒來；disabled 與 quota backoff 仍阻擋，不等下次 30 分鐘搜尋。原 scheduler 截止 fallback 已能使用其他合格線完成題目，無需更改整合權重、模型或預測 prompt。

## 驗證與部署門檻

隔離工作副本來自當前 dirty tree，保留全部既有成果。合成測試覆蓋精確拒絕、曾執行／有輸出／錯誤字串或參數變動／外部路徑／不明工具／額外危害／raw竄改、線鎖、CAS、每日限制、重啟未知、過期與一次真正start journal及budget callback。另保留所有既有回歸。

需 Claude 實際設計與程式審查、Gemini 實際獨立程式審查；相同來源 manifest 通過後，六鎖與 idle 安全點套用精確受審檔案，先驗證現有正式前檢，再啟用恢復 LaunchAgent。部署本身不改 live campaign、不重登記／延長／重置用量；不改 adapters／runner／netguard／acceptance judge。

完成後觀測 resume、由排程啟動的補做以及 forecast_ok／comment_ok。unknown 依同 intent 讀回，不重送。只看技術 metadata，不看開放題機率或理由。操作證據：`data/operations/d140-auto-recovery-20261105/`。

## 設計審查對應

Claude 真實設計審同意 proof reuse 可降低無資訊增益的驗收成本；要求 page來源、精確episode、共用CAS、一次claim及原預算、正式proof綁定。實作採原 Budget.reserve 先持久預留，再於原線鎖＋ledger交易中將 grant ID 與 reservation ID 一起寫在既有 start record（即claim），最後才呼叫CLI。預留後、start前中斷代表尚未啟動，沿原 Budget.recover 釋放未使用預留；start後未知永不重用。因此不新增第二套預算或grant狀態機。原F.Lock的實際flock與_HELD_LOCKS已提供同線持鎖契約，fast path不呼叫acceptance.run_line；不改寫原互斥機制。帳冊維持既有fsync與完整行校驗，截斷時fail-closed，不依設計建議忽略殘行。

補充限制：只有明確寫入且精確符合原因的 stop 適用；由帳冊回放推導的其他 stop 保留停線。若某次 scheduler 因 Gemini 鎖忙而暫停該線，恢復後於下一輪排程重新選取，其他線與 deadline finalization 持續。


## 實際上線與完成結果（UTC）

- 2026-11-05T13:13:03.686Z：17 份受審檔案於六鎖安全點套用成功，前後 6 項正式 preflight 均無 waiver；guarded root smoke 通過。原 campaign／run config／quota／submit mode 雜湊不變。
- 恢復 LaunchAgent 已啟用，StartInterval=300；原 cycle StartInterval=60、新題發現1800秒不變。正常 tick 讀本機狀態後回 healthy 並退出；活躍答題持鎖時回 busy，不啟動額外模型。
- 2026-11-05T13:14:27.416Z：Gemini 由 line_recovery_v1 自動 resume，recovery_id=recovery_1793884447745_6e105d06，0 次模型呼叫，retry_granted=true。原失敗 record／raw／稽核保留。
- 46250 的既有單題 tier2 token capacity 阻擋額外 Gemini rerun；不放寬配額。正常 scheduler 使用既有合格結果完成 final 與提交，整題沒有因單線故障放棄。
- 46199、46279 各由正常 scheduler 執行一次 Gemini tier2 initial，兩筆皆 valid、model_check=verified、forecast_pipeline_v11；兩筆原凍結輸入及正常 budget/quota/deadline 檢查照常。

| Market Pulse question_id | forecast_ok | comment_ok |
|---|---|---|
| 46250 | 2026-11-05T13:21:37.064Z | 2026-11-05T13:21:43.492Z |
| 46199 | 2026-11-05T13:26:44.049Z | 2026-11-05T13:26:50.448Z |
| 46279 | 2026-11-05T13:36:47.844Z | 2026-11-05T13:36:55.360Z |

全部為已核准 bot 311763、Market Pulse project 33131。forecast 回應 unknown 時，沿相同 intent/request 讀回確認；沒有重送 forecast。46250 的讀回／留言另由主持人呼叫既有 Submitter.run() 推進，沿全部身份、唯一 intent、額度與提交檢查，不提供或修改任何預測值；其餘由 cycle 正常完成。正式累計 25 題：主賽2、MiniBench10、Market Pulse13。完成檢查時三線皆無停線。

驗證：完整 guarded offline 3574 PASS；含正式資料路徑存在但禁止讀取的 sentinel mirror smoke 266 PASS；部署故障測試35 PASS。R3 Claude/Gemini 對 A/B 共四份實際 PASS，R4 對一行測試fixture及deployment round selector補充雙PASS，runtime與R3相同。實際部署曾兩次在啟用前因該測試漏傳合成 acceptance_log 觸發 guard 而自動 rollback；測試修正後第三次成功，沒有放鬆隔離 guard，也未在失敗部署期間恢復或變動預測。

審查與來源鏈：`reviews/d140-review-evidence.json`、`reviews/claude-d140-r3a.md`、`reviews/claude-d140-r3b.md`、`reviews/gemini-d140-r3a.md`、`reviews/gemini-d140-r3b.md`、兩份 r4；操作與回執投影：`data/operations/d140-auto-recovery-20261105/completion.jsonl`。這些紀錄只包含技術metadata，不含開放題預測機率或理由。常規本機／平台檢查不呼叫模型；實際答題補做與一次性開發審查才消耗模型額度。

本輪未 commit／push；保留原 dirty tree 與歷史帳冊。既有 live 窗口仍於2026-11-10T04:00:00Z截止，未延長、未重置會計起點或cap。人類賽仍本人手動提交。
