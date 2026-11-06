VERDICT: PASS
（範圍：僅限 D140 r3 模組 A，對象為共享 manifest c5748f03…87e2 綁定的 src/line_recovery.py 及 A 擁有的 forecast.py 恢復／grant／start-claim 路徑。這不是模組 B 的 PASS，不是整體系統核准，也不是部署授權。部署仍須兩個正式模型對每個模組都給出 PASS。）

## 0. 來源包完整性與證據分類

**完整性**
- 來源包已完整讀到最後標記 END-D140-CODE-R3A，沒有發現截斷。
- 有一處說法不符。範圍段寫「Every blocker-named function is complete below」，但 r3 回應 B-B1／B-B2／B-B3 提到的以下內容不在本包中：
  - S._recover、S._protected、F.recover_interrupted
  - scheduler.py、budget.py、discovery.py
  - deploy.py、test_deploy.py
  - launchd/bin/claude、launchd/bin/agy、plist、run.sh、config/line_recovery.json
- manifest 的 files 段顯示 src/reconcile.py 在本輪有修改（before 2230…→after 5d79…），但本包沒有它的原始碼或 diff。
- 這些都不屬於 A 的職責範圍，所以不阻擋 A 的判定。但它們不能被當成已審過，也不能由本回覆帶入任何核准。

**證據分類**
- (a) 我實際讀過的來源：line_recovery.py 全文、test_line_recovery.py 全文；forecast.py 的 line_stopped、latest_resume、run_attempt、_may_rerun、forecast_question、run、main(resume)、Lock、preflight、check_acceptance、recover_acceptance 等片段；ledger.py、jsonl.py、external_usage.py、gemini.py 相關片段；PLAN diff；D140 任務單。
- (b) 主機自報、我無法驗證：
  - full-tests 3574 passed、deploy-tests 30 passed。我沒有執行任何測試，也不能據此宣稱測試通過。
  - capture-probe-r3.json 的範圍是「read-only stage verifier」，不是一次真正的 recover() 提交，也不證明 live resume、補做或 forecast_ok/comment_ok。
- (c) 只有實作者陳述、本包無法驗證：B-B1（部署器 CAS 與審查鏈）、B-B2（S._recover 等）、B-B3（wrapper 內容）、discovery 喚醒、reconcile.py 的變更內容。

## 1. 前輪阻擋項的處理情況（依原始碼）

**BLK1：嚴格事件解析（已修正）**
- strict_events 現在會拒絕以下情況：
  - 非 '\n' 結尾、重複鍵、NaN/Infinity
  - 未知頂層事件與鍵集合，init 與 step_update 的鍵不在白名單
  - tool_info 名稱不在 TIER1_TOOLS 或不是 ' view_file'
  - tool_name 與 tool_info 衝突
  - step_index 不是嚴格 int（排除 bool 與字串）或順序倒退
  - 只要某 step 群組帶有工具跡象（step_type=tool、或有 tool_info／tool_name 鍵），就必須剛好兩筆：第一筆 ACTIVE、第二筆 DONE/ERROR。第一筆必須是 tool 型、參數為 dict、沒有 output/error。兩筆的名稱與參數必須一致。finish 只能由 'finish' 工具以 DONE 結束。
- 上一輪的獨立合成發現「同一步工具身份衝突在合併時消失」：現在 tool_steps 合併前會先做群組一致性檢查，test_earlier_tool_identity_cannot_be_overwritten_by_page_fetch 與 test_every_tool_step_identity_and_transition_are_checked 都有覆蓋。
- 非工具身分的 tool_name 也有覆蓋（test_no_unparsed_tool_evidence_or_nonfinite_json）。

**BLK2：當前證據綁定到事故當時的證據（已修正）**
- require_incident_environment 有明確的「類型→時間欄位」允許清單。我與 autoload._start_call／run、recover_acceptance 的實際寫入欄位比對過，相符（started_at／finished_at／at／taken_at、baseline_check.snapshot.taken_at）。
- 未知類型或缺時間就拒絕，不再豁免 autoload。
- 只略過 claude/gpt 的紀錄；無 player 欄位的紀錄（例如 baseline_approved、autoload_result）也納入檢查。
- incident_binding 對 capture 選中的 baseline ID 與兩層的 acceptance／production ID：
  - 在帳冊中各須找到唯一一筆
  - 類型正確；acceptance_result 必須 player=gemini 且 line_pass is True
  - production 的 mode、tier、model、effort 必須與現行設定一致
  - 時間必須嚴格早於事故 started_at_utc
  - ID、時間、整筆 row SHA 寫入 incident_evidence
- 綁定在四個時點驗證：兩次 capture 之後、commit 交易內、retry_grant（真正 start 前）。
- 等價推理：事故後 gemini/common 驗收帳冊沒有任何新紀錄，加上當前 preflight 選中的紀錄都在事故前且 row SHA 不變，就推得事故當時的 preflight 選擇與現在相同。runtime 同理：當前 runtime 等於 accepted evidence 的 code_sha256，而事故當時的 preflight 也通過同一份 evidence。
- 這符合「current 與 accident-time accepted identity 不變」。

**BLK3：已 resume 的 commit 不會被誤記為 held（已修正）**
- except 分支會在交易內先找同一 recovery_id 的 line_resume。找到就回傳 resumed 並標 completion_journal=pending，不寫 held。
- RecoveryDeadline 屬於 Exception，所以會走這條路；KeyboardInterrupt 不寫 held，下一輪得到 already_attempted。
- 兩種故障注入都有測試。

**BLK4：健康線的鎖競爭（A 端已修正）**
- recover() 在拿任何鎖之前先做 preliminary line_stopped；健康時回傳 healthy，backoff 時回傳 existing_backoff。
- test_healthy_line_never_acquires_recovery_or_forecast_lock 在兩把鎖都被持有的情況下仍得到 healthy。
- F.Lock 是 LOCK_EX|LOCK_NB 並有 thread-local 持鎖紀錄；capture→preflight→recover_acceptance 因 _HELD_LOCKS 而不會自鎖。
- 排程端的實際行為（S._recover、F.recover_interrupted 外層對 PreflightError 回傳空集合）不在本包，屬 B，A 無法確認。

**BLK5：喚醒（屬 B，本包無 discovery.py，無法判定）**
- 我在 A 端看到一個跨模組風險，請 B 明確確認：F.latest_resume 刻意排除 by=line_recovery_v1。如果 discovery 的喚醒條件「晚於舊 job finished_at 的實際 resume」是用 latest_resume 計算，自動恢復將永遠無法喚醒舊的 line_stopped job。

**獨立合成發現（已修正）**
- 「同 ID 改變的危害繞過重放」：line_stopped 現在只在 covered_attempts[attempt_id] 等於 hash_obj(projection(r)) 時才豁免；situation 遇重複 attempt_id 直接拋例外。有測試覆蓋。

## 2. A 合約逐項核對（依原始碼）

- **自動恢復時零模型呼叫**：capture 只做 wrapper/pin SHA、quota.snapshot（/usage）、cli_versions、本機基準快照，以及注入了 snapshot 的 F.preflight；不呼叫 adapter.run，也不跑 acceptance.run_line。recovery_started、recovery_finished、回傳值都帶 model_calls=0。
- **原失敗答案仍無效**：恢復過程不寫 forecasts/raw/audits。測試比對 raw 與 forecasts 位元組不變，以及補做後原列仍是 incomplete。
- **只用既有 initial→rerun 槽**：
  - retry_grant 要求 chain 長度為 1 且 base 是 initial。
  - 只要同 player/question/tier 存在任何 rerun start（含結果不明）就拒絕。
  - grant 在 run_attempt 的 transaction(forecasts_log.parent) 內重新檢查，並把 recovery_grant_id 寫進同一筆、已 fsync 的 start record，之後才呼叫 CLI。這是原子性的一次性認領；已記錄的 start 即使結果不明也會消耗原槽。
  - 失敗的 rerun 是 attempt_kind=rerun，classify 要求 initial，所以不能再次恢復；forecast_question 也會因已有 rerun 而回傳 attempts_exhausted。
- **原截止／設定／預算**：grant 的 expires_at 等於原列的 decision_deadline_utc。run_attempt 照常執行 require_time、quota admission、before_attempt（B 的 Budget.reserve）。grant 在保留後失效時拋 QuestionDone，finally 以 admission_outcome(aid, False) 釋放保留；預算漂移測試覆蓋 runtime、config、baseline、wrapper、pin、proof 六種情況，均為 0 CLI、0 start。
- **不持有其他鎖**：A 只持 line_recovery.lock 與 forecast.gemini.lock。test_recovery_does_not_lock_other_lines_or_submit 在 capture 期間成功取得 claude/gpt/cycle/scheduler/submit 鎖。
- **恢復 resume 的語意範圍**：只移除與 lifts 完全相同的 stop，不設定 resumed_at，所以其他由帳冊回放推導的危害（未解 start、mismatch backoff/cooldown、重複 leak、safety_stop、其他工具違規）在 resume 後仍會生效。人工 resume 保留歷史語意。
- **歷史 suspected 關鍵字**：保留且不升級為停線（有測試）。只有 leak_confirmed 會阻擋。

## 3. 阻擋項（模組 A）

以本包實際原始碼而言，模組 A 沒有發現必須修改才能過關的阻擋項。

## 4. 非阻擋建議（建議修，但不影響 A 判定）

1. retry_grant 在共享 ledger 交易內做了較重的工作：最多 32MB 的 raw 雜湊、runtime_fingerprint、多次讀 acceptance 帳冊與設定、巢狀 line_stopped。工作量有上限、都在本機，但與「短交易」原則有落差。建議在交易外預先計算，交易內只做 CAS 比對（例如比對 ledger 長度或雜湊），或至少記錄耗時。
2. 驗收證據的「未變」是靠時間戳判定。如果出現時鐘偏移或倒填的新 acceptance_result（時間早於事故但附加在帳冊尾端），require_incident_environment 擋不住；retry_grant 也沒有在位置上確認綁定的 acceptance／production ID 仍是最新選擇（baseline 有用 latest_approved 做位置檢查）。排程端的普通 preflight 可以緩解。建議加一個位置檢查：確認仍是 records[-1]，且仍是最後一筆符合條件的 production。
3. strict_events 用 str.splitlines()，會在 U+2028、\x85、\x1c 等字元處切行；它也是另外讀檔，不是解析已雜湊的同一份 bytes。目前會拒絕（fail-closed），雜湊前後比對也降低了 TOCTOU 風險，但建議直接解析 raw_sha 對應的 bytes，並以 b'\n' 切行。
4. safe_raw 用帳冊值組出期望路徑，但沒有驗證 attempt_id/question_id 的字元集，含 '..' 時理論上可以跳出目錄。建議以 ID 正規式驗證（威脅模型需要帳冊被竄改，風險低）。
5. 在寫入 recovery_started 之前的 capture 失敗（例如額度觀測過舊、preflight 失敗）不計入每 stop 一次或每日上限，每 300 秒會重做 /usage 與 CLI 版本探測。不是模型呼叫，但建議加觀測紀錄或退避。
6. capture 內的 F.preflight 可能觸發 recover_acceptance，寫入 interrupted 的 acceptance_result 並刪除孤兒目錄。這是既有副作用，之後 require_incident_environment 會因此永久走 fail-closed。建議在任務單寫明此路徑需要人工處理。
7. situation 的 covered 以 attempt_id 判定，line_stopped 以 metadata SHA 判定。目前靠 duplicate_attempt_id 拋例外維持一致，建議統一用 SHA。situation 也沒有檢查 isinstance(lifts, dict)，與 line_stopped 有微小差異。
8. result 事件內容只檢查 status、denied_actions、conversation_id，其餘鍵沒有限制。建議加白名單。
9. gemini 任何歷史 leak_confirmed 稽核（即使早於人工 resume）都會永久阻擋本路徑。比人工 resume 語意更嚴格，屬可接受的 fail-closed，建議寫入文件。
10. 120 秒 SIGALRM 可能在 quota／CLI 子程序執行中觸發，留下孤兒子程序。建議確認 quota.snapshot 會自行清理。
11. 測試缺口（程式碼本身已處理）：
    - 第二次 capture 與 commit 之間選中紀錄 SHA 改變的 commit 時測試
    - 倒填 acceptance 紀錄的測試
    - tier 為 True 這類型別混淆的 classify 測試（`r['tier'] in (1,2)` 會接受 True；validate_binding 已用 type() 擋下，classify 尚未）

## 5. 不在 A 範圍或無法判定（不得視為已通過）

- B-B1 部署器 CAS 與「review packet→started→finished→answer」來源鏈：deploy.py／test_deploy.py 只有雜湊。
- B-B2 S._recover/_protected、F.recover_interrupted、預算保留呼叫方、包含 pending 的預算、部分行收尾。
- B-B3 wrapper 內容（DISABLE_AUTOUPDATER、AGY_CLI_DISABLE_AUTO_UPDATE、pin 驗證），以及 cycle 的 PATH 是否解析到 launchd/bin/agy。capture 只驗證恢復服務自己的 PATH。
- discovery 喚醒（見 BLK5 風險），以及 scheduler stop 寫入者是否使用完全相同的 reason 與 run_id。probe 顯示真實 stop 判為 eligible，但這屬自報。
- src/reconcile.py 已變更但沒有提供。A 的 grant 檢查與 start 認領都集中在 run_attempt（tier 2 也經過這裡）。這個推論前提是所有 tier-2 CLI start 都經過 run_attempt。reconcile.py 必須在 B 包或整體 manifest 審查中提供並通過。
- 實際部署後仍須另外取得證據：自動 resume 確實寫入、排程啟動的補做、3 題待補題的 forecast_ok/comment_ok。本審查沒有觀察到任何一項。

## 6. 結論

依實際原始碼，模組 A 在 r3 已處理 BLK1、BLK2、BLK3 與 A 端的 BLK4，以及兩個獨立合成發現。A 合約（零模型呼叫、原答案無效、只用原 rerun 槽、在原截止／預算之內、原子一次認領、不持其他鎖、精確 episode 覆蓋）在原始碼層面成立。

本 PASS 只限模組 A 與上述 manifest。它不代表 B 或整體系統通過，不授權部署或恢復，也不代表任何測試由我執行。上方第 4 節的非阻擋建議由整合者自行決定是否採納；第 5 節各項仍待 B 審與兩個正式模型的同源雙審。
END-D140-CODE-R3A