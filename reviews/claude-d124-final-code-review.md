【D124 最終複審 + Claude 個人完整審查：Phase 1 / Phase 2】

═══ 結論 ═══
• Phase 1（修訂後的 autoload + autoload_maintenance 一次性全域標記維護）：PASS，附非阻擋建議與部署前條件
• Phase 2（auto_reaccept 只觀察版）：PASS，附非阻擋建議

這兩個 PASS 只是我依來源封包做的設計／程式碼審查結論。它們不代表 baseline 已核准，不代表 autoload 量測已通過，不授權放置 canary，也不取代使用者或其他審查者的決定。依封包所述，canary 要等所有審查者完成後才放置；目前沒有任何正式環境的量測結果。

═══ 證據界線 ═══
1. 我沒有執行任何測試、指令或工具，也沒有讀取封包以外的檔案。
2. 「2789 passed in 143.20s」與「302 targeted passed」是封包提供的說法，我無法驗證。「2774 那次執行早於這些修正」同樣是封包的說法。
3. sha256 清單我沒有逐一比對實際檔案。src/forecast.py 只附了節錄，雜湊涵蓋的是整個檔案，未附的部分我沒有審查。
4. E-a 到 E-h 中的部署事實都是作者陳述，我只能確認它們與附上的程式碼一致，無法確認主機現況。這些事實包括：launchd plist 內容、正式環境 attempt 的上層目錄、scheduler.py 第 531 行、本機檢查過的 codex rust-v0.166.0 commit。
5. 測試依賴的隔離設定（local、numeric/discrete=false）封包說不會部署，我把它當作測試環境限定的設定。

═══ Phase 1：BLK1–6 逐項複核 ═══

BLK1（計時器驗證順序；manifest 遺失或為空時的復原）— 已解決
• run() 會先檢查 timeout_s 與 getitimer，再寫入 autoload_run_started，計時器錯誤不會留下未關閉的 start。
• 預寫意圖的不變量在程式碼中成立：
  - 每次變更全域檔案前，都會先 append_record（fsync）一筆 place 或 create_dir 意圖。
  - attempt base 的模式會先記 directory_mode，之後 make_attempt_dir 才可能 chmod 它。
  - prepare() 在 backup_dir.mkdir 之前就完成全部 inspect。
• 因此「manifest 沒有事件 ⇒ 沒有變更」的推論成立。
• events 為空時的分支仍會呼叫 verify_resolved，確認每個目標都安全且不含本次 run 的 canary，之後才寫 closed/no_mutation，不是直接放行。
• 測試涵蓋 missing、managed、original、placed 四個階段，並以 Session.recover 連續呼叫兩次確認冪等。

BLK2（主機手動解決衝突）— 已解決
• CLI 必須同時提供 --resolve-conflict 與 --resolved-by，且要通過第二道全域檔案確認。
• 流程在六把鎖下執行。verify_resolved 要求：
  - 目標是單一連結的一般檔案，或不存在（inspect 遇到 symlink、硬連結、特殊檔都會丟出錯誤）；
  - 不含本次 run 的精確 canary 前綴；
  - 測試期間建立的目錄都已不存在。
• 不覆寫任何外部編輯。結果標記為 host_attested、byte_exact=false、passed=false。
• STOP 清除前仍必須通過 preflights，而 preflights 包含 baseline 比對。所以若主機誤改了內容，會以 CONTENT 變更停下該 line，不會被當成放行。
• 正確的操作流程是可行的：先跑一般 recover，可還原的部分會還原，衝突的會保留；主機處理衝突後，再以 resolve 關閉。restore 的 original_matches 分支是冪等的，所以這個流程成立。

BLK3（復原時不應被平台請求或截止時間卡住）— 已解決
• require_idle(recovering=run_id) 先檢查其他 model/acceptance 未完成項與 process 證據，然後直接 return，略過 submissions、intent 與截止時間。
• 本次 run 自己的 paths 由 Session.recover 另行以 orphan.no_process 檢查。
• 有測試涵蓋「有待處理 intent 加上即將到期的題目」這個情境。

BLK4（舊的 unmanaged autoload_result 不應影響 preflight）— 已解決
• unclosed() 只收集 run_id 屬於 managed start 的結果。
• 有對應測試：缺少 run_id 的舊紀錄不影響 preflight。

BLK5（截止時間與配額檢查要在 _start_call 之前）— 已解決
• before_call 在 _start_call 之前執行 capacity(req) 與「剩餘時間 ≥ timeout + 120s」檢查，被拒時不會留下 acceptance_call_started。
• executor 另保留一道防禦性檢查。
• 次數限制邏輯正確：只要先前的 maintenance_started 底下有任何呼叫，或已有兩次 run，就視為已用盡。test_zero_call_failure 與 test_deadline_denial 一起涵蓋「零呼叫失敗可重試恰好一次，第三次拒絕」。

BLK6（controller 測試覆蓋）— 已解決
新增的 Session.recover 測試涵蓋：
• 四個準備階段，各重複關閉一次；
• 只清理已登記的 workdir；
• 補寫 acceptance_call_finished，且不寫 acceptance_result，避免污染正式 acceptance；
• live PID、boot 不明、子行程仍在、preflight 拒絕、他人設定的 STOP 保留、主機證明流程。
• test_real_snapshot_and_identities 實際走到 PATH、wrapper 路徑、pin 雜湊與 admission 拒絕的邏輯。
測試是否實際通過，仍以封包所述為準，我無法證實。

P1 補充項目也已在程式碼中確認：
• probe 結束後檢查 orphan.no_process；
• maintenance CLI 要求第二次確認；
• Gemini executor 在呼叫前後都取 quota snapshot，用同一個 forecast.quota_model_check，加上 log_model_check=verified 與 credits=0；
• preflight 明確使用 self.root 的 acceptance log。

═══ Phase 1：其他發現（皆非阻擋，屬建議）═══

P1-a｜codex 鏡像目錄在 SIGKILL 後殘留
tempfile.mkdtemp 預設建在 T/，也就是 attempt base 的上層。controller 若在 codex probe 期間被 SIGKILL，T/flab-codex-home-*/home 會殘留：
• 不含憑證；
• 但含使用者 ~/.codex 指令與 canary 的副本。
它不在任何 attempt 的祖先路徑上，不會被載入，不構成隔離風險，但屬於隱私殘留。建議：把鏡像路徑寫入 manifest，或讓 recover 列出並提示清理。

P1-b｜主機證明路徑不還原 attempt base 模式
host_attested 路徑不呼叫 restore()，也不驗證 directory_modes，attempt base 的 0700 模式可能不會被還原。影響僅限暫存目錄，建議在 verify_resolved 加入模式檢查或提示。

P1-c｜備份檔無保留政策
data/autoload_backups/*/NN.bin 是使用者私人指令檔的完整副本（0600），成功還原後仍永久保留。建議訂定保留／清除政策，並確認 data/ 不會被同步或提交到外部。

P1-d｜attempt base 依賴執行環境的 TMPDIR
attempt_base 取自執行 maintenance 時的 default_base_dir()。E-b 依賴操作者手動複核；若改用其他 shell、sudo 或不同的 TMPDIR，量測會測到錯誤的祖先目錄。這不會造成不安全，但結果會失效。建議在放置 canary 前，由程式比對最近正式環境 attempt 的 parent 與 attempt_base.resolve()，不一致就拒絕。

P1-e｜時間餘裕偏緊
bound − 300 = 3T + 360，恰好等於三次「T + 120」。若前面的呼叫接近 timeout，後面的 line 會在開始前被拒，而且該 run 已有呼叫，所以算作消耗。這是安全的，但可能浪費一次量測機會，請操作者知悉。

P1-f｜復原可能長時間無法推進（都是 fail-closed）
• 同一次開機中，若原 PID 被同一使用者的無關行程重用，recover 會一直拒絕。
• os.kill 遇到 PermissionError 時會以非 MaintenanceError 拋出，但仍會設定 STOP。
• 其他未完成的 forecast start 也會阻擋 recover。
這些情況都會讓 STOP 一直維持，不會不安全，但需要人工介入，建議寫進操作手冊。

P1-g｜崩潰時機造成的未登記 workdir
若崩潰發生在 make_attempt_dir 與 _start_call 之間，會留下未登記的 workdir，使 base 的 rmdir 失敗而成為衝突，需要主機處理。這是 fail-closed，可接受。

P1-h｜Claude 若真的載入 canary skill 會被當成違規
此時 declared.skills 非空，會被判為 tool_scope_violation，維護失敗並設定 STOP。這符合 D123 的前提（headless 不載入 skills）。量測紀錄仍會保留 loaded 結果，可接受，請明白這個行為。

P1-i｜codex prompt-input 是否完全不碰網路
附上的 prompt_debug.rs 支持「不進入 run_turn、不產生回覆」。但 ThreadManager／models manager 是否會在無憑證的鏡像中嘗試網路，附件無法排除。這屬於殘餘不確定性；負面證據仍應記為 UNKNOWN，這點與程式一致。

已知限制（封包 limits 段已聲明，我同意這是誠實的界線）：
• 無法防範不合作的外部寫入者在 fd 雜湊與 truncate 之間競爭；
• 斷電時的 fsync 保證；
• SIGKILL 後需要人工復原；
• CLI 歷史 transcript 中可能殘留 canary。

═══ Phase 2：只觀察版審查 ═══

確認符合 staged spec，只觀察，不要求未來的 enforce 功能：
• 不呼叫模型。所有輸出都是 model_calls=0、baseline_approved=False。
• 不寫 acceptance.jsonl，不核准 baseline，不清除 STOP。
• MODE≠observe 時 tick 直接 ValueError，有測試涵蓋。
• STOP_SUBMIT 存在時不觀察；live window 不在有效期內時直接 return 0。

鎖與併發：
• tick 先取 auto_reaccept.lock，再逐 line 取 forecast.{player}.lock。全部是非阻塞 flock，不會死鎖。
• maintenance 持有六把鎖時，observe 會得到 busy；maintenance 若中途崩潰，observe 會回報 maintenance_unclosed。
• 所以 canary 存在期間不會被當作 baseline 候選紀錄。

run.sh：
• 只有 cycle 成功退出、鎖都釋放之後才執行 tick，並加上 `|| true`，tick 失敗不影響 cycle 的退出碼。
• else 分支的 "$?" 在 POSIX sh 中是 if 條件指令的狀態，語意正確。
• PATH 順序與 production_path 一致（E-a）。

隱私：
• scan 只讀取 allowlist 中、大小有上限的 .md 檔，只輸出雜湊與原因碼。
• 用 O_NOFOLLOW|O_NONBLOCK 開檔，再以 fstat 確認是單一連結的一般檔案、沒有執行位元。
• 讀取前後比對 stat，再重新 inventory，並與 saved hash 對齊，沒有 TOCTOU 接受新 digest 的路徑（有 snapshot_race 測試）。
• IMPORT、MARKERS、frontmatter 的規則偏保守。誤判只會讓候選變成不合格，方向是安全的。

紀錄：
• candidate_sha256 綁定所有受保護來源、runtime fingerprint、模型、catalog 與 scan 結果，排除 taken_at，可正確去重。
• 寫入前在 transaction 內重新確認 parent baseline 仍是最新，否則回報 superseded。

Phase 2 非阻擋建議：
P2-a｜tick 對每個 player 只捕捉 OSError、ValueError、TypeError、KeyError。gpt 的 load_catalog 若丟出 RuntimeError 等其他例外，會中止整個 tick（被 `|| true` 吞掉），其他 line 該輪沒有觀察結果。建議逐 line 捕捉 Exception，並只記錄型別名稱。
P2-b｜candidate 的分類問題：
  - 來自 blocker 的變更會被歸類成 text/cli_version，但仍不合格。這些變更包括：gemini credits 不明（quota stale）、CLI version 不明（cli_version:* CONTENT）、來源 error。
  - 建議另設 kind='blocker'，讓日後的 enforce 審查更清楚。
P2-c｜每次 cycle 後都會做三次完整 baseline snapshot，加上 CLI 版本查詢與 agy quota 查詢，最多 120 秒，可能讓下一次 cycle 晚開始。launchd 不會讓同一個 job 重疊執行，所以不會有安全問題，只是新題目的回應延遲稍增。建議觀察實際耗時，必要時改成每個 snapshot 共用給三個 line。
P2-d｜缺少以下測試：main() 的 ObservationDeadline／timeout 路徑、STOP 路徑、maintenance_unclosed 與 superseded 分支、live window 不在有效期內時直接 return 0、run.sh 的退出碼語意。建議補上。

═══ 部署／放置 canary 前條件（操作面，非程式阻擋）═══
1. 所有指定審查者都完成，包含這次 Claude 對修正的強制複審。
2. 操作者依 E-b 再次確認 attempt base 的正規路徑與正式環境一致；最好在程式中加檢查（P1-d）。
3. 確認目前正在處理的正式數值題已完成，require_idle、quota 與 baseline 預檢都通過。
4. 隔離測試設定（numeric/discrete=false）不得進入部署。
5. 失敗或崩潰時依 recover → 必要時 resolve 的流程處理；不得手動刪除 STOP 或 manifest。

═══ 總結 ═══
BLK1–6 在附上的程式碼中都有對應修正，也有相應測試（測試結果以封包所述為準，我未執行）。目前沒有找到新的阻擋問題。

Phase 1 = PASS；Phase 2 = PASS（只觀察）。

以上建議都不阻擋，可列入後續追蹤。這份審查不構成 baseline 核准，也不構成 autoload 量測通過。