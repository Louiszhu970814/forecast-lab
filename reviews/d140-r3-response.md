D140 r3：實際 R2 A/B 審查回應（仍待同來源雙審，不是 PASS）

R2 Gemini A/B PASS，Claude A/B REQUEST_CHANGES。R2 未部署、未恢復。此輪保留原精確故障分類與所有預算、截止、工具隔離及提交政策。

A-B1：require_incident_environment 使用明確類型→時間欄位允許清單；Gemini/common 每筆均須有合法時間且早於事故開始，未知類型／缺時間拒絕，不再豁免 source=autoload。baseline_check 明確讀 snapshot.taken_at，其餘包含 quota／retracted／autoload／paused。incident_binding 對 capture 實際選中的 baseline ID、兩tier acceptance ID、production ID 在當前帳冊各找唯一 record；驗證 type/player/line_pass、時間嚴格早於事故，production mode/tier/model/effort吻合原設定。ID、時間、完整record SHA 寫進 binding.incident_evidence；兩capture以及resume commit前和真正rerun start前都重新核對。負向測試：autoload postincident結果、未知／缺時間、新production、缺失／重複／事後／錯玩家／未通過proof、production身份不符。

A-B2：validate_binding 要求非空 baseline/runtime/config SHA/wrapper SHA/pin SHA/judge/CLI/sources與剛好tier1/2各一完整proof。recover 空缺capture會記held，不resume；retry_grant 缺任何必要欄位或 incident_evidence都拒絕。全部成功測試已改完整shape、合成先前baseline/acceptance/prod紀錄與真實runtime/config SHA；不再使用空字典成功。新增真實 F.run_attempt 中在 Budget admission callback 之後改 runtime/config/baseline/wrapper/pin/selected proof：均拋QuestionDone、0 CLI、0 start、admission_outcome(aid,False)、原結果仍incomplete。原S.jobs→S.execute→Budget.reserve整合測試亦採完整binding。

B-B1：部署器由同伴直接補共享manifest/context CAS與原始review packet→started→finished→answer來源鏈；需以本輪ops實際程式與測試為準，不能只相信此文字。未變的依賴檔也須與審查bytes一致，不同manifest的舊PASS不可放行。

B-B2：本輪補齊 S._recover/_protected、F.recover_interrupted、F.Lock/threadlocal、manual resume以及完整acceptance選擇函式。F.recover_interrupted外層遇PreflightError返回空集合，不使run前的線鎖競爭終止全部排程。F.Lock使用跨程序LOCK_EX|LOCK_NB與thread-local持鎖紀錄。這是補既有來源證據，沒有改已驗收隔離runner。

B-B3：本輪提供現行 launchd/bin/claude 與 launchd/bin/agy 完整wrapper。Claude wrapper設定 DISABLE_AUTOUPDATER=1；AGY wrapper設定AGY_CLI_DISABLE_AUTO_UPDATE=true並驗實體pin SHA。恢復服務與原cycle同PATH先解析這些wrapper，毋須修改已驗收wrapper或CLI。新plist舊trial註解改為本輪明確授權與雙審門檻。

非阻擋建議維持最小範圍：未新增一般異常容錯、額外模型驗收或額外rerun。保留獨立300秒恢復器、30分鐘發現新題、60秒heartbeat與有限live窗口，實際部署後仍須驗證自動resume與3道待補題的forecast_ok/comment_ok。
