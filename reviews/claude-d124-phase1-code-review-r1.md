# D124 階段 0／1 實作程式碼審查（Claude，指定的完整程式碼審查人）

## 結論：BLOCKED，不是 PASS

- 本審查不構成核准，也不代表 Gemini 的獨立審查已經完成。
- 我只讀了來源包裡的程式碼與測試原文。我沒有執行任何測試、指令或 CLI，也沒有做預測或放置 canary。
- 「175 項目標測試通過」和「完整測試仍在跑」都是來源包的說法，我無法驗證。
- 依來源包的描述，那 175 項是在加入最後一個 stop-on-scope 守衛之前通過的。也就是說，本次審查的程式碼（sha256：autoload.py `c99d4cd4…`、autoload_maintenance.py `fb62f076…`）目前沒有任何通過測試的證據。
- 證據等級：「靜態閱讀」是我讀程式碼得出的結論；「需證據」是來源包裡看不到、必須引用原始碼或實測才能確認的事項。

---

## 一、已確認符合的部分（靜態閱讀）

- **六把鎖的順序**：`autoload_maintenance.LOCKS` 依序是 cycle → scheduler → submit → forecast.claude → forecast.gemini → forecast.gpt。
  - `Session.locks()` 用非阻塞的 `F.Lock` 取鎖；任一把取不到就 `stack.close()` 全部釋放，再以 `min(15, left)` 秒退避，最多等 900 秒。
  - 用 ExitStack 以相反順序釋放。等鎖期間不會殺任何程序。
- **模型呼叫前的閘門順序**：`Session.run` 依序做以下事情，都在 A.run 之前：
  1. 檢查 STOP 與 unclosed；
  2. `require_active` 與視窗剩餘時間；
  3. 確認 probe 名額未用完；
  4. `idle`；
  5. `identities`；
  6. `snapshot(admit=True)`：要求快照新鮮、credits 為 0、Gemini 通過 manual_admission；
  7. 三線 baseline 都判 ok；
  8. 六個 preflight；
  9. 重新取 snapshot，再跑一次 idle；
  10. 核對身分、受保護雜湊與時間範圍；
  11. 在 transaction 內檢查 `capacity()` 並寫入 `maintenance_started`。

  Gemini 在 `_start_call` 內會再做一次新鮮度與准入檢查。
- **意圖先於寫入**：
  - `Placement.place()` 先寫 `place` 紀錄（fsync）才呼叫 `_write`；`create_directory()` 先寫 `create_dir` 才 `mkdir`。
  - attempt base 的 mode 在 `make_attempt_dir` 改權限之前就寫入 `directory_mode`。
- **有條件還原**：`restore()` 只在以下兩種情況動手：目前狀態等於原始狀態，或等於放置後的狀態（雜湊、mode、大小、inode 都相符）。
  - 附加的檔案用 `O_NOFOLLOW` 開檔，比對 fstat 的 inode 與 nlink，再 `ftruncate(original_size)`，所以不會有檔案被清空的空窗。
  - 其他情況一律記 `restore_conflict`，不覆蓋、不刪除。`test_autoload_conflicts.py` 涵蓋取代、附加、chmod、刪除四種情況。
- **result＋closed 閘門**：
  - `unclosed()` 只看 v2 寫的 `autoload_run_started`，所以 v1 manifest 不受管理，符合說明。
  - 判定條件是最新一筆 `autoload_result.restore_ok is True`，且 manifest 最後一筆是 `closed`、`restore_ok`、run_id 相符。closed 寫在最後，可以取代先前已解決的衝突。
  - `forecast.preflight` 在 unclosed 時對三線硬停。
- **還原失敗時**：先建立 STOP_SUBMIT 再釋放鎖（`except BaseException: self.stop()`，由 `test_failure_sets_stop_before_unlocking` 驗證），不會無限期占鎖，也不會寫 success。
- **呼叫上限**：
  - 每線最多 1 次呼叫。
  - 只要先前任何一次 `maintenance_started` 有 `acceptance_call_started`，或已有 2 次 started，就判「已用完」。
  - `before_call=capacity` 在每次呼叫前重算週用量，`weekly_usage` 沿用 `H.accounting_start`（D118 接續）。
- **Claude pin**：PATH 第一順位是專案 wrapper，wrapper 自己核對 sha256；`snapshot()` 要求版本是 2.1.316，並設定 `DISABLE_AUTOUPDATER`。
- **模型與工具範圍**：model、effort、timeout 取自 `players.*.tier1`，並沿用 judge 擷取、`tool_violations(repair=True)` 與 Claude 宣告檢查。
- **不寫基準核准**：沒有呼叫 `B.approve`。autoload 的呼叫列被 `incomplete_acceptance` 和 `recover_acceptance` 排除，不會撤銷或偽造驗收。

---

## 二、阻擋項（PASS 前必修）

### BLK-1：「已寫 started、但沒有 manifest」的崩潰窗口無法恢復，三線永久停住

- **位置**：`autoload.run()`、`autoload.recover_placement()`
- **證據**：
  - `run()` 先 `_append(autoload_run_started)`，之後才進入 `prepare()`。`prepare()` 刻意先 inspect 所有目標，再建立 backup 目錄與 `managed` 紀錄。
  - 另外，`if timeout_s <= 0 or old_timer[0]: raise ValueError(...)` 位在 try／finally 之外。這條路徑不會寫 closed，也不會寫 result。
  - 在這兩個窗口內被 SIGKILL、斷電或丟出例外，都會留下沒有 manifest 的 started。
  - 之後 `unclosed()` 永遠回報這個 run，而 `recover_placement()` 遇到 `if not events: raise ValueError("missing recovery evidence")`。系統內沒有任何路徑能關閉它，只能手動改帳冊。
- **必修**（二選一，或兩者都做）：
  1. 調整順序：先建立 backup 目錄並寫入 `managed`，再寫 `autoload_run_started`；計時器參數檢查移到寫 started 之前。
  2. `recover_placement`：manifest 不存在或為空，且沒有 `place`／`create_dir`／`directory_mode` 事件時，依據「意圖先於寫入」的不變量，可證明本 run 沒有改動任何東西，直接寫 `closed`（建議加上 `no_mutation: true`）。
- **需補測試**：分別模擬 started 後沒有 manifest、只有 `managed`、只有 `original` 的情況，經 `Session.recover` 應能關閉，而且可以重入。

### BLK-2：衝突沒有主持人可用的結案路徑，常見的外部編輯會形成死結

- **位置**：`Placement.restore()`、`unclosed()`、`Session.recover()`
- **證據**：
  - 檔案要被判為原始狀態，必須 `(dev, ino)` 與原始相同。很多編輯器以「寫暫存檔再 rename」的方式存檔，原檔一旦換了 inode，主持人就算把內容逐位元改回原樣，`restore()` 仍判衝突。
  - `recover()` 只會重播同一套條件，然後 `raise 'restoration conflict remains'`。
  - 主持人沒有任何方法讓 `closed` 合法出現，只能手寫 manifest／acceptance 帳冊，這違反只能附加的原則。規格寫明衝突「交給主持人處理」，但程式沒有提供出口。
- **必修**：新增明確的主持人結案指令，例如 `recover --resolve-conflict --resolved-by <name>`。必須同時滿足：
  - 持有全部六把鎖；
  - 驗證每個目標與已建立目錄裡都不再含 `FLABAUTO-{run_id}-` 字串；
  - 每個目標都是單一連結的一般檔案，或不存在；
  - 已建立的目錄不存在。

  結案時寫入 `closed`，帶 `resolution: host_attested`、`resolved_by` 與目前雜湊，並標記這不是逐位元還原。不得自動判 pass。之後由既有的 baseline compare 決定該線要不要停。
- **需補測試**：rename 換 inode 後由主持人結案；結案後若仍有 canary 殘留，必須拒絕。

### BLK-3：恢復流程被不相干的排程狀態擋住，canary 會留在全域檔案裡更久

- **位置**：`Session.recover()` 呼叫 `self.idle(..., horizon_s=0, recovering=run_id)`，即 `require_idle()`
- **證據**：
  - 即使 `horizon_s=0`，`require_idle` 仍套用 `DECISION_MARGIN_S=1800`：只要有一題支援中、尚未送出、30 分鐘內截止的題目，就拒絕恢復。
  - 未結的 submission request 或 intent 也會擋住恢復。
  - 但是 unclosed 期間 preflight 本來就讓三線全停，這些題目不可能作答。這道閘門只會讓 Louis 的個人全域檔案帶著 canary 更久。連續賽期間題目密集，可能長時間無法恢復。
- **必修**：恢復改用專屬的 idle 條件，只要求：
  - 其他 run 沒有未結的模型或驗收嘗試；
  - 已登記路徑的程序不存在；
  - 本 run 的 probe 程序不存在。

  跳過截止時間與 submission 閘門。恢復不呼叫任何模型，這樣做沒有額外風險。
- **需補測試**：有題目即將截止、有未結 intent 時，recover 仍能還原並關閉。

### BLK-4：`unclosed()` 遇到缺少 run_id 的舊紀錄會丟出 KeyError（條件性），導致三線 preflight 全部崩潰

- **位置**：`autoload.unclosed()` 的 `results = {r["run_id"]: r for r in rows if r.get("type") == "autoload_result"}`
- **證據**：
  - 這一行位在 try 之外，而 `forecast.preflight` 每次都會呼叫 `unclosed()`。
  - 只要正式的 `acceptance.jsonl` 有任何一筆舊 v1 `autoload_result` 缺 `run_id`，部署後三線所有 preflight 都會丟例外，不是回傳 problem。
  - 來源包看不到歷史帳冊，我無法排除這種情況。
- **必修**：改用 `r.get("run_id")`，只收 `version == "autoload_v2"` 或有對應 started 的紀錄。修改成本極低。若整合者能引用正式帳冊證明每筆都有 run_id，這項可降為建議，但仍建議修。

### BLK-5：缺少 deadline 前置檢查會把零呼叫的失敗記成已開始，違反「一次零呼叫重試」與計數準確性

- **位置**：`Session.run` 內部的 `execute(req)` 包裝，以及 `autoload.run()` 的呼叫順序
- **證據**：
  - 順序是 `before_call(capacity)` → `_start_call()`（寫入 `acceptance_call_started`）→ `executor(req)`。
  - 時間不足的 `MaintenanceError('insufficient remaining maintenance time')` 是在 executor 內才丟出，也就是 started 已寫入、但實際沒有任何模型呼叫的時候。結果有兩個：
    - 週上限多算一次不存在的呼叫；
    - 名額檢查看到 `calls` 不為空，判定已用完，拒絕本應允許的零呼叫重試。
  - 依時間預算推算，第 2、3 線每次呼叫若有超過約 140 秒的額外開銷（quota 30 秒、codex prompt-input 60 秒、judge），就可能觸發。
- **必修**：deadline 檢查移到 `before_call` 內（在 capacity 之後、`_start_call` 之前）。executor 只保留防禦性檢查。
- **需補測試**：時間不足時沒有 started，第二次 run 仍被允許。

### BLK-6：證據不足，不符合規格 §9 的執行前提

- `Session.recover()` 完全沒有測試，這是要求裡「SIGKILL／重啟與恢復冪等」的核心控制器。沒有涵蓋的部分包括：
  - boot_id／pid 判斷；
  - 已登記 workdir 的移除；
  - `acceptance_call_finished` 補寫與重入；
  - STOP_SUBMIT 只移除自己建立的那一份；
  - `maintenance_recovered` 不重複寫入；
  - 恢復後 preflight 被拒時的處理。
- `test_recovery_replays_write_intent_and_is_idempotent` 只測了 `recover_placement`，而且只有 claude 子集與 place 階段崩潰。
- Session 的測試把 `idle`、`identities`、`protected_hashes`、`snapshot`、`preflight` 全部替換成 stub。實際的 `Session.snapshot(admit=True)` 准入、在 `production_path` 下的 `identities()`、實際的 `F.preflight` 整合，都沒有測試證據。
- 加入最後一個守衛之後的程式碼沒有任何通過紀錄；完整回歸尚未完成。
- **必修**：補上述 recover 控制器測試，至少包含：崩潰後恢復兩次、衝突後主持人結案、沒有 manifest 的情況、STOP 由他人建立時不移除。另外至少要有一個以假 `quota.snapshot`／假 PATH 驅動實際 `Session.snapshot`／`identities` 的測試。以目前的程式碼雜湊跑完完整隔離回歸，再提交結果。

---

## 三、必修但不屬安全阻擋（P1，建議本輪一起修）

1. **跑完後沒有確認 probe 程序已不存在就放鎖**（`Session.run` 在 A.run 之後）
   - 逾時（SIGALRM → TimeoutError）的結果會走 `measurement_failed` → `maintenance_finished` → 放鎖，中間沒有對本 run 的 workdir／raw_dir 做 `orphan.no_process` 檢查。
   - 來源包沒有提供 runner 在例外時會終止子程序群組的證據。
   - 修正：發布結果前要求 `no_process` 通過；不通過就 STOP 並記 `maintenance_failed`。
2. **`autoload_maintenance.main()` 只要求 `--approved-by`**
   - 少了 D43 的第二道確認（`--i-understand-this-edits-global-files`），形成一個繞過 `autoload.main` 雙重確認的公開入口。
   - 修正：兩個入口都要求兩道確認，或移除 `autoload_maintenance` 的 run 子命令。
3. **Gemini 的模型核對標準較弱**（`autoload.execute`）
   - `apply_quota_model_check(result, usage_raw["log_model_check"])` 把 CLI log 標籤餵進 quota 核對欄位。但該函式的 docstring 與 §7.7 都說，不能只靠 log 標籤；這裡沒有 after 快照，也沒有 `quota.judge`。
   - 由於 `passed=True` 的 autoload_result 之後可被 `baseline.approve` 當作 autoload 依據（測試裡就這樣用），整合者必須二選一：
     - 補上 before／after quota 差異核對；
     - 或在維護產生的紀錄標記不可作為核准依據。
4. **`weekly_usage` 自行重寫預算計數**
   - 只有一個測試對齊 `Budget.reserve`，日後容易分歧。`capacity()` 只檢查 `usage < cap`，沒有替預測流程保留 pending tier1 或 repair 的容量；維護可能用掉最後一個名額。
   - 建議：直接呼叫 production Budget 的唯讀判定，或明文規定保留量。
5. **`Session.preflights()` 依賴預設的 `acceptance_log=baseline.ACCEPTANCE_LOG`**
   - 應明確傳入 `self.root/'acceptance.jsonl'`，避免 project 與 PROJECT_ROOT 不一致時讀錯帳冊。

---

## 四、需整合者引用原始碼或實測才能確認的事實（目前無證據）

- E-a：`production_path()` 寫死的 PATH 是否與 launchd plist 完全相同。建議改成從 plist 或共用常數讀取。
- E-b：正式 launchd job 的 TMPDIR 和 attempt base 是否等於互動執行時的 `tempfile.gettempdir()/flab-runs`。若 launchd 沒有設 TMPDIR，會退回 /tmp，共同上層來源就量錯位置。建議記錄並強制使用正式 attempt base。
- E-c：`scheduler.lock` 的實際路徑是否在 data/。整合者補充只確認了 submit.lock 與 cycle 的 root。
- E-d：`acceptance.py` 的手動驗收是否持有 `forecast.X.lock`。若沒有，六把鎖擋不住並行的手動驗收。
- E-e：runner 遇到 InterruptedError／TimeoutError 時，是否確實終止子程序群組。
- E-f：`codex debug prompt-input` 是否不產生模型呼叫或計費。
- E-g：`F.Lock(path)` 的建構方式。片段裡看不到 dataclass 宣告，但測試這樣使用。
- E-h：`QuotaSnapshot` 是否同時支援 `.get()`／`[]` 與屬性存取。`observation_fresh` 與 `remaining_credits` 兩種用法混在一起。

---

## 五、建議與殘餘風險（P2／P3，不阻擋，但應記錄）

- **finally 開頭的訊號窗口**：訊號如果正好落在 `finally` 執行 `setitimer(0)` 或 `SIG_IGN` 之前，handler 拋出的例外會跳過 `restore()`。結果等同 SIGKILL，需要 recover。BLK-1～3 修好後可以接受，但建議把還原放進對例外有韌性的重試迴圈。
- **ftruncate 前的檢查競態**：從 fstat／read 驗證到 `ftruncate` 之間，若有外部 `O_APPEND` 寫入，會被截掉。flock 只是建議鎖。這是 POSIX 下無法完全消除的殘餘風險，應寫入文件。
- **耐久性**：macOS 的 `fsync` 不等於 `F_FULLFSYNC`，而且沒有對目錄做 fsync。斷電保證有限，docstring 已說明需要手動處理。
- **canary 殘留**：Claude 的 `~/.claude/projects` 逐字稿、Codex 的 sessions／rollout、agy 的 brain 紀錄可能保留 canary 文字，還原範圍不包括這些。若 Codex memories 被觸發，S_post 會以 CONTENT 變更停線，屬於可偵測的失敗。
- **並行 agent 工作階段**：共同目標放在 `$TMPDIR` 上層（本審查環境的工作目錄也在 T/ 底下），全域檔案則在 HOME。維護期間新啟動的任何 Claude、Codex、agy 工作階段都會讀到 canary，六把鎖管不到它們。建議放置前用 ps 掃描未登記的 CLI 程序，並列為操作前提。
- **工具使用導致中止**：Claude 若真的載入 canary skill，或模型使用任何 tier1 允許的工具（例如網路搜尋），都會觸發 `tool_scope_violation` → `break` → STOP。後兩線不會被量測，probe 名額也已用掉。這和 D123 一致，但整合者應明確接受這個後果。
- **恢復時重算目標**：`recover_placement` 用當下的 `os.environ['CODEX_HOME']` 重新計算目標，環境不同時會失敗（不會誤動，只是無法恢復）。建議在 started 紀錄寫入解析後的目標清單。
- **備份保存**：`data/autoload_backups` 長期保存私人全域檔案的備份，建議訂定保存期限與刪除流程。
- **時間上限**：bound 沒有包含跑完後 snapshot 與六個 preflight 的時間，目前只靠 30 分鐘的 margin 吸收。
- **身分紀錄**：codex／agy 的 identity 雜湊算的可能是 symlink 啟動腳本，建議同時記錄 realpath。
- **註解與實作不符**：`_start_call` 的註解寫「autoload-only 20% gate」，實際用的是共用 policy 的 weekly_cap。方向較保守，但註解有誤導。
- **未結期間的核准與驗收**：建議在 unclosed 期間讓 `baseline.approve` 與 `acceptance.py` 也拒絕執行，以免把含 canary 的狀態核准成基準。preflight 仍會擋住預測，所以列為建議。

---

## 六、重新審查的條件

1. BLK-1～BLK-6 全部修正，並附上新測試。
2. 第四節 E-a～E-h 逐項附上原始碼引用或實測證據。
3. 以修正後的程式碼雜湊跑完完整隔離回歸，並提交結果。
4. Gemini 獨立審查另行完成。

在這些條件達成之前，結論維持 BLOCKED。不得放置 canary，也不得進入階段 1 的量測。