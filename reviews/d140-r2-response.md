D140 r2：對實際 Claude r1 blocker 與獨立合成重現的修正

r1 Gemini PASS、Claude REQUEST_CHANGES；r1 未部署。以下是整合者回應，仍須兩位依凍結來源作實際複審，不能視為 PASS。

BLK1：strict_events 在合併 tool_steps 之前檢查每個事件與每個 tool frame。index 僅非 bool 的 int、單調且不接受未知 step_type；每個工具恰為 ACTIVE→DONE/ERROR，名字與參數完全一致，開始沒有 output/error；唯一允許的 type 轉換是 finish 的 tool→finish DONE。拒絕 NaN/Inf、JSON 重複鍵、未知結構、非工具步驟附 tool_name、init/step/result 的 conversation ID 矛盾。原始每幀 tool identity 驗證可防止較早越權工具被同 index 後來 read_url_content 覆蓋。負向合成測試均已加入。真實事故精確符合已釘選 CLI 的這一 stream 格式，沒有一般化容錯。

BLK2：選擇保守方案 (a)，require_incident_environment 在開始前與 resume commit 前均要求 run_config_version 與事故相同，且任何 baseline_approved、Gemini acceptance call/result 均必須早於事故開始；事故之後重新驗收或改基準一律不適用此 fast path。另由兩次 capture 重新核對當前官方 CLI/pin/wrapper、來源、完整 F.preflight 兩tier 與正式 production model/effort 證據。RUNTIME_FILES 僅 adapter/runner/netguard，D140 三者均未變；新 pipeline 不偷換隔離驗收。真實 metadata probe 2026-11-05T12:08:04.069Z：候選 eligible，兩次正式 capture 相等，合計 13.90 秒，0 模型呼叫，acceptance 與 line_status bytes 完全未變。probe 使用 stage verifier、只把 evidence root 指向正式資料，不提供任何預測正文。base_1793803103661_66326b64 與 acc_1793802323516_b0387ed0、兩正式 production proofs 均早於事故。

BLK3：line_resume 是權威 commit。resume 已持久後，完成診斷 journal 失敗或 deadline signal 只回傳 resumed/completion_journal=pending；不寫 held、不重新執行、不聲稱線仍停。若不能確定帳冊狀態，main 回 check_failed_state_not_assumed。測試注入第二帳冊 OSError 以及 resume 已 append 後的 deadline exception。

BLK4：healthy 判斷在取得 recovery/line 鎖以前，正常 300 秒 tick 不碰 Gemini 線鎖。eligible recovery 最長 120 秒，只鎖自身與 Gemini；真實雙 capture 13.90 秒，不含模型等待。附完整 S.run/execute/finalize 證明 busy Gemini 只使該線本次 line_error/paused，其他兩線照常與到期 finalization；新增合成 integration test。所有鎖及交易來源與原 lock_all 部署安全點均納入 B 包，沒有重構隔離 runner。

BLK5：舊 line_stopped job 只在該玩家存在 finished_at 之後的 line_resume、當前 F.line_stopped 已解除時喚醒。discovery 原 stopped 集合仍納入 disabled 與 future quota_retry_at；新負向測試確認有效 quota backoff、無稍後 resume 以及非持久 stop 的既有等待都不被清除。這是喚醒補做，不移除正常 admission/eligibility/budget/deadline guards。

獨立重現 same attempt ID 新危害：F.line_stopped 的 auto exemption 現在比對整個安全 metadata projection SHA，不能只用 attempt ID；新 hazard 不被舊 grant 覆蓋。R.situation 對重複 attempt ID 直接 fail closed。

一次補做整合：保留原 initial→rerun 規則；在真實 F.run_attempt 的線鎖＋ledger transaction 內再驗 grant、寫唯一 start（帶 recovery_grant_id 與原 admission_decision_id）才啟 CLI；任何跨snapshot rerun start 佔用原一次槽，未知結果也不重用。新增 S.jobs→S.execute→Budget.reserve→real start journal 的合成測試，原失敗行保持 incomplete。原截止 fallback 在正式環境已於 UTC11:31 令主賽46572透過剩餘合格線 forecast_ok/comment_ok；此證據不包含預測內容。

部署仍須完整離線回歸及 Claude/Gemini 每一模組均 PASS，凍結 manifest 綁同一組來源，六鎖 idle 安全點先前檢後精確套用。原 campaign/caps/end/mode/run config 與新題1800秒、heartbeat60秒完全不變；啟動新的300秒恢復服務後以實際 resume/start/submission ledger 驗證結果。
