VERDICT: REQUEST_CHANGES

# D140 R2B（B 模組）設計／程式複審：Forecast Lab 指定審查者

## 0. 審查範圍與封包完整性
- 封包完整收到最後標記 `END-D140-CODE-R2B`，沒有截斷。scheduler.py、jsonl.py、advise.py、practice.py、autoload_maintenance.py 本來就只提供行號區段，這是封包的設計。
- 這次只審 B 模組：同玩家的鎖與 admission 呼叫端、計入待決的預算、喚醒資格、部分線最終化、部署。src/forecast.py、src/line_recovery.py、tests/test_line_recovery.py 屬於 A 模組（嚴格分類器、safety replay、proof 重用、recovery commit、grant/start claim），全文不在本封包，所以我對 A 的內容不下判斷。
- 本結論只涵蓋 R2B 模組，不代表整個系統核准，也不能代替 Gemini 或任何 A 模組的審查。部署前仍須兩位官方模型在每個模組都 PASS。
- 我沒有執行任何測試。封包中的「3536 passed」「15 passed」、真實 probe 的 13.90 秒與 0 次模型呼叫、主賽 46572 已 forecast_ok/comment_ok，都是主機或整合者提供的說法，不是我核實過的證據。我只依實際原始碼判斷。

## 1. 阻擋項（必須修正或補證據）

### B1：部署關卡沒有綁定共享 manifest 裡未改動的「全部現行檔」
- 實際原始碼：`deploy.validate()` 的 `approved['source_sha256'] == hash_obj(files)` 只涵蓋 `exact_files(stage, base)` 找出的「有差異檔」。對正式環境的漂移檢查也只看這些檔：`for rel,entry in files.items(): ... entry['before']`。
- manifest 的 `contexts` 列出的檔案都不在 `files` 中，因此部署時完全不會核對正式環境的實際 bytes：src/scheduler.py 05f5…、src/budget.py 4daf…、src/advise.py、src/practice.py、src/flab/ledger.py、src/flab/jsonl.py、src/autoload_maintenance.py、src/adapters/gemini.py。我在本輪審查的鎖、admission、預算、最終化行為，正是由這些檔決定。
- approval 沒有記錄 manifest SHA（d4feeb83…）。每份 review 也只檢查首行 PASS、末行 marker 和自己的 sha，沒有要求 review 引用這份 manifest。結果是：另一輪、但 marker 相同的 PASS 檔案，只要整合者把它的 sha 寫進 approval，就能通過。
- 這違反契約「Shared manifest binds BOTH modules and all current files」。
- 需要的修正：
  1. validate 要逐一核對 manifest 的 `contexts` 和 `files.after`。最好整個 base 樹都與正式環境逐 byte 相符。
  2. approval 寫入 manifest SHA，每份 review 都須引用同一個 SHA。
  3. 補上負向測試：context 檔漂移、review 引用其他 manifest，兩者都必須拒絕。

### B2：B 模組負責的同玩家鎖呼叫端與人工 resume 寫入者，原始碼沒有提供，無法核實
以下都影響「健康線鎖競爭只影響 Gemini 一線」與「所有 stop／manualresume 寫入者共用同一短 ledger 交易」這兩條契約，但封包裡都沒有：
- `Scheduler._recover`：它在 `run()` 進入主迴圈前會對每位玩家各呼叫一次，而且在 `execute()` 中位於 try 之外。如果它取得 `forecast.<player>.lock`，並在競爭時拋例外，那麼恢復器持有 Gemini 鎖的這段時間，整個 scheduler.run 都會中止，三條線一起受影響。新測試 `test_busy_recovery_line_does_not_block_other_lines_or_deadline_finalization` 表示它沒有中止，但我看不到原始碼，也不能把主機的 pass 當作證據。
- `F.Lock` 的語意（非阻塞或阻塞、`_HELD_LOCKS` 是程序層級還是執行緒層級）。新測試是在同一個程序裡持鎖；它是否等同真實的跨程序競爭（line_recovery 是獨立的 launchd 程序），要看這部分原始碼。
- 人工 resume 寫入者：PLAN v1.94 寫了「人工 resume 也遵守線鎖及 ledger 交易」，但它的原始碼不在封包。
- `Scheduler._protected`：它決定 pending_tier1 的保護量，屬於計入待決的預算。

我核對過封包內已提供的 stop 寫入者，下列都用了 `transaction(<data root>)`，這是實際證據：
- scheduler.execute、reconcile.run
- advise.line_event 與 advise.propagate_safety
- practice.propagate_safety、practice 392-404

**需要的修正**：提供上述原始碼的完整內容，並讓它們出現在 B 模組 manifest 中。

### B3（條件式阻擋，需證據）：恢復服務的執行環境可能與正式路徑不一致
- 實際原始碼：`autoload_maintenance.Session.production_path()` 明確把 `DISABLE_AUTOUPDATER=1` 當作正式 CLI 執行環境的一部分。但 launchd/run.sh 只設定 PATH，新的 org.forecast-lab.line-recovery.plist 也沒有 EnvironmentVariables。
- 恢復器在合格時會兩次 capture，跑官方 CLI／quota／pin 檢查與完整 F.preflight。這時如果觸發 CLI 自動更新：
  - 實體 pin 雖然會 fail closed，所以不算安全漏洞；
  - 但會讓正式 cycle 的 preflight 一起失效，損害其他線與到期送出的可用性；
  - 也會破壞 fast path 依賴的「CLI 不變」前提。
- 需要以下二者之一：
  - 證據：org.forecast-lab.cycle.plist 或 launchd/bin wrapper 已設定相同變數，而且新服務會繼承；
  - 修正：在 run.sh 或新 plist 加入同樣設定，並附測試或檢查。

## 2. 已確認的修正（依實際原始碼）
- **BLK3、BLK4 的鎖時序（B 側）**：tier1 路徑在 `F.Lock(forecast.<p>.lock)` 內先檢查 `F.line_stopped`、quota backoff 和 `F.eligible`，之後才進入 `forecast_question`。因此 `Budget.reserve` 是在線鎖內經由 `before()` → `admit()` 呼叫，符合「B 在線鎖下預留一般預算」。tier2 的 `R.run` 也是先拿線鎖。如果 `F.Lock` 在競爭時拋例外，continuous 模式的 `run()` 會把 future 例外記成 `line_error`，並只把該玩家加入 `paused`；其他線和 `finalize()` 照常（後者成立的前提是 B2 補證）。
- **計入待決的預算**：`Budget.reserve` 遇到同玩家的未決預留、沒有對應 forecasts 的 start、或未完成的 advisory，都會給 `unresolved_or_running_attempt`。`used_ids` 包含 window starts、forecasts 和 advisory，所以 rerun start 照常計入，沒有新增額度。`Budget.recover` 只釋放前一輪 run 留下、沒有 start 的預留，已有 start 的仍然計費。這與「已記錄的 start 即使結果未知也佔用原槽」一致。
- **不重複 rerun**：scheduler.execute 中，失敗結果遇到既有 rerun 會轉成 `attempts_exhausted`（continuous 模式依 snapshot 範圍判斷）。跨 snapshot 的 start 佔槽屬於 A 的範圍，本輪不判斷。
- **部分線最終化**：`finalize()` 在 `transaction(root)` 內執行。到期（`decision_deadline`）時，即使 Gemini 仍是非終止的 `line_stopped`/`line_error`，也會用其他合格線 build_aggregate，得到 `complete_local`。`jobs()` 會跳過已 sealed 的題，所以已凍結的 final 不會重開。
- **BLK5 喚醒**：discovery 只有在三個條件同時成立時才把舊 `line_stopped` job 視為不再等待：玩家不在 `stopped`、有 `line_resume`、而且該 resume 的 `at` ≥ job 的 `finished_at`。`stopped` 仍包含 disabled、`F.line_stopped`，以及 forecasts 中仍在未來的 `quota_retry_at_utc`。兩個新增的負向測試（較早的 resume 不喚醒、晚於 job 的 resume 不覆蓋 quota backoff）與原始碼一致。discovery 只負責喚醒，不負責 admission；真正的限制仍由 scheduler/Budget/F 把關，所以多喚醒一次的風險可以接受。
- **ledger 交易**：`flab.ledger.transaction` 是同程序可重入的 RLock，加上 thread-local 與跨程序 flock。`read_complete` 遇到結尾不完整的行會 fail closed，`append_record` 有 fsync。沒有發現放寬政策。
- **部署流程**：
  - apply 在六鎖下依序執行：require_idle → validate → STOP_SUBMIT 與 live window 檢查 → 停用 cycle timer → 兩次 preflight 與 smoke test → 不可變設定比對。
  - 提交前任何失敗都會還原原始 bytes 並重新啟用 timer。
  - apply 不安裝恢復服務；activate 只在 applied 的 bytes 不變時才安裝。
  - 保護清單排除 adapters/runner/netguard/acceptance/campaign/run_config/quota/submit_mode/cycle.plist/prompts。
  - test_deploy 的 15 個參數化案例和原始碼結構一致（3+1+6+1+4），但我沒有執行。

## 3. 非阻擋項與建議
1. 部署後的 smoke test 只跑 4 個檔，沒有包含這次有改動的 tests/test_scheduler.py，也沒有 budget 和 deploy 的測試。建議加入 test_scheduler.py。
2. apply 在六鎖下最長可能持有約 240+120+240 秒，加上複製時間，但 `require_idle(horizon_s=600)` 幾乎沒有餘裕。建議把 horizon 提高到至少 900 秒，或縮短 timeout。
3. rollback 的 finally 中，如果還原時 `atomic` 失敗，`launchctl enable` 不會執行，cycle timer 會一直停用。建議把 enable 放進獨立的 try/finally，並記錄還原失敗事件。
4. activate 沒有重新檢查 STOP_SUBMIT、idle 和 config/line_recovery.json 的 enabled。建議加上。
5. 恢復鎖不在 `lock_all` 裡。日後的維護、advisory、practice 安全點不會等恢復器鎖外的檢查階段結束。commit 時恢復器仍須取得 Gemini 鎖，所以不構成互斥漏洞。建議維護流程先停用 line-recovery 服務，或把恢復鎖納入 `lock_all`。
6. 如果 continuous scheduler run 期間 Gemini 因恢復鎖競爭而 `line_error`，它會在這輪剩餘時間被 paused；恢復後的補做要到下一輪 run 才會被選中，接近截止的題可能來不及。這符合「其他線與截止照常」，但建議記錄這點，或允許 paused 線在恢復後的下一個 tick 重新評估。
7. discovery 的 `resumes` 用 `r['at']` 直接取值，缺欄位時會 KeyError，結果是不喚醒而不是 fail-open。建議改為容錯並保守地喚醒。
8. 新 plist 的註解「Install only after Louis approves trial」與 D140 的授權（雙審後直接啟用）矛盾，建議更新文件。
9. BLK5 沒有新增「disabled 加上較晚 resume 仍不喚醒」的負向測試。原始碼雖已涵蓋，建議補上。

## 4. 結論
B 模組的主要設計方向正確：在線鎖內預留預算、計入待決的預算檢查、喚醒必須有較晚的實際 resume、到期時用部分線最終化。我沒有發現放寬政策的地方。但 B1（部署沒有綁定共享 manifest 與全部現行檔）、B2（B 模組負責的 `_recover`、`F.Lock`、`_protected`、人工 resume 寫入者原始碼缺漏）、B3（恢復服務執行環境一致性沒有證據）解決前，不能給 PASS。本輪沒有核准任何部署，也不構成整個系統的核准。

END-D140-CODE-R2B