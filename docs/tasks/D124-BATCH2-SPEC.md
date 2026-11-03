# D121 第二批（R2-C／R12）實作規格草案 v0（Claude，規格負責人）

> 本文件是規格草案，不是審查通過，也不是核准。內容只依據來源包撰寫。「證據」指我閱讀來源包程式碼得到的結論；我沒有執行任何測試、指令或 CLI。今晚的維護時段與 C 方向，依 coordinator_request 和 D121 引用，不再重問。正式套用前，仍須 Gemini 獨立審查，以及我對 patch 的完整審查。

## 0. 程式碼核對事實（證據等級：靜態閱讀）

- E1 `Placement.restore()`：在 `inspect()` 之後直接用備份覆寫原檔；遇到新建的檔案則直接 `unlink`。它不核對目前內容是否就是放入時的內容，所以外部同時改檔會被蓋掉或刪掉。另外它用 `O_TRUNC` 改寫，若在截斷之後被 SIGKILL，原檔會只剩空白。
- E2 `autoload.run()` 不取得 cycle／scheduler／三線鎖。`_start_call` 只寫 `acceptance_call_started`（`source=autoload`），不帶 orphan metadata，也不寫 finished。`recover_acceptance()` 和 `incomplete_acceptance()` 都排除 autoload，所以目前沒有任何恢復路徑。
- E3 `latest_approved()` 只取最後一筆 `baseline_approved`；`check_baseline()` 用 `compare()` 的逐線 decisions 判定。新的核准只要寫成 scoped `baseline_approved`，前檢就會自然讀到，不需要新增讀取入口。
- E4 `B.approve(player=X)` 會把 X 線所有來源換成傳入 snapshot 的內容。`claude_baseline_candidate` 的做法是先複製舊來源，只替換一個來源，再呼叫 `B.approve`。新流程必須沿用這個做法。
- E5 `check_acceptance()` 有兩個要求：最新一筆 `records[-1]` 必須 pass；另須有一筆 production 驗收，其 model／effort 和 runtime 指紋相符。GPT 線另外比對 `latest.catalog_entry_sha256`。因此：
  - 自動重驗若失敗，就算 Louis 把內容改回原樣，該線仍會停住（見 X4）。
  - Codex catalog 變動時，新的驗收紀錄會自然帶入新的 catalog 雜湊。
- E6 `_json_parts` 依 `content_keys` 計算雜湊。只要修改 `CLAUDE_SETTINGS_CONTENT_KEYS`（例如把 permissions 搬過去），已核准基準的 `content_hash` 就會失效，Claude 線會被判 stop。
- E7 `codex_declaration` 用的是 mirror；它的負面結果只能判 `unknown`。agy 沒有來源宣告。只有 Claude 的 init 宣告了完整 skills 清單，可以產生 `not_loaded` 結果。
- E8 封包裡看不到 `RUNTIME_FILES`／`FINGERPRINTED` 的實際內容、submit 鎖的路徑、預算計數函式、`_acceptance_records` 的過濾方式，也看不到 `cycle(root)` 是否就是 data 目錄。實作者必須先引用原始碼確認這幾點。

## 1. 範圍

**今晚（必做）**

- 階段 0：修好 autoload 的還原衝突處理，補上持鎖入口、journal、恢復機制與 PATH 釘選核對。
- 階段 1：完成審查後，在維護時段跑一次正式量測。
- 階段 2：在沙箱實作，出貨時預設為 `observe` 模式（只記錄候選與資格，不啟動重驗）。

**明確延後（day 1 不出貨）**

- 各線 agents、`claude.commands`、`codex.memories`。
- settings 的 GENERAL 自動重驗，原因是 E6，加上 permissions 目前落在 GENERAL。
- 共同上層目錄、plugins／hooks／MCP／managed、sidecars、project_grants、shared_config。
- 為 Codex／agy 新安裝固定版本。
- 依量測結果把來源改為資訊性的 reclassification patch（階段 1b）。
- `autoload.locations()` 尚未涵蓋的來源。

**檔案邊界**

- 可改：`src/autoload.py`、`src/forecast.py`（新增前檢條件）、`src/baseline.py`（只新增函式，不改既有雜湊演算法、類別、`content_keys`）。
- 新增：`src/autoload_maintenance.py`、`src/auto_reaccept.py`、`src/flab/auto_reaccept_policy.py`（版本與常數）。
- `acceptance.py` 不修改，改以 `run_id` 連結紀錄，避免 E8 指紋不明的風險。
- 不得改動：三份 campaign 設定檔、`adapters/*`、`flab/runner.py`、`flab/netguard.py`、`judge`／`JUDGE_VERSION`。

## 2. 階段 0：執行前的阻擋修補（全部完成並審查前，不得執行量測）

**B1 有條件還原**

- `place()` 寫入後立即重讀，記錄 `placed_sha256`、`placed_size`、`st_dev`／`st_ino`、`original_size`，並寫入 manifest 的 `placed` 紀錄。
- `restore()` 只有在目前檔案的雜湊等於 `placed_sha256` 且 inode 相同時才動手：
  - 原本就存在、本次是附加：以 `O_NOFOLLOW` 開檔，`ftruncate(original_size)`，再核對雜湊等於原始雜湊，最後還原 mode 與 utime。這樣不會出現檔案被清空的空窗。
  - 本次新建：雜湊相符才 `unlink`。
- 不相符時記為 `restore_conflict`：不覆蓋、不刪除、保留備份，`restore_ok=false`。
- 目錄只用 `rmdir`（只能移除空目錄），沿用現行做法。
- 每個路徑處理完寫 `restored`，全部結束寫 `closed`。

**B2 持鎖入口 `autoload_maintenance.py run`**

- 依序取得 `cycle.lock` → `scheduler.lock` → submit 鎖（若存在，路徑依 E8 確認）→ `forecast.{claude,gemini,gpt}.lock`，一律用 `F.Lock`（非阻塞）。
- 任何一把取不到，就全部釋放，15 秒後重試；累計 15 分鐘仍取不到就放棄。此時尚未放入任何東西，不終止任何程序。
- 取得全部鎖之後依序檢查：
  1. 沒有未結案的預測或驗收嘗試，且 orphan 檢查沒有發現 CLI 程序。
  2. 以正式 launchd PATH 解析 `claude`／`codex`／`agy`，記錄路徑與 SHA-256。Claude 必須是專案 wrapper 的 2.1.316，並設 `DISABLE_AUTOUPDATER=1`。只核對 HOME 與登入狀態，不匯出或複製任何憑證。
  3. `S_pre = take_snapshot`（先在鎖外讀 Gemini credits），對 `latest_approved` 比對，三線都必須是 `ok`；否則中止，不在未核准狀態下量測。
- 量測完成、還原之後：
  - 取 `S_post`，`compare(S_pre, S_post)` 不得有非資訊性變更。
  - 持鎖執行三線第 1、2 層 preflight。
  - 依反向順序釋放鎖。
- 時間上限固定為：鎖等待 15 分鐘 + Σ tier1 timeout + 3×120 秒 + 5 分鐘。若 status 顯示有未作答的開放題，其截止時間早於「現在 + 上限 + 30 分鐘」，就不開始。判斷只看題目 ID 與時間。

**B3 journal**

- `run()` 一開始就在 `acceptance.jsonl` 寫 `autoload_run_started{run_id, manifest, pid, boot_id}`。
- 每次呼叫補寫 orphan metadata 與 `acceptance_call_finished(source=autoload)`；這些呼叫照舊計入週上限。
- `forecast.preflight` 新增檢查：任何 `autoload_run_started` 沒有對應的 `autoload_result`，或 manifest 沒有 `closed` → 三線都加問題 `autoload_unclosed`。這是硬停，因為 canary 可能還留在全域檔案裡。

**B4 恢復指令 `autoload_maintenance.py recover`**

- 只能在維護時段、持全鎖時執行。
- 依 manifest 套用 B1 的條件逐一還原，衝突一律交給主持人處理。
- 補寫結果時 `passed=false`、`error=interrupted`；可重入。

## 3. 階段 1：今晚的量測

- 只跑 production 模式，三線一起，最多 1 次。只有在任何模型呼叫之前就失敗時，才允許重跑 1 次；總計最多 6 次模型呼叫。
- 結果只寫成證據：每個目標標為 loaded／not_loaded／unknown。不得自動改基準或改類別。
- 狀態機：`LOCKING → PRECHECK → PREPARED → PLACED → CALLING(player…) → RESTORING → POSTCHECK → PREFLIGHT → RELEASED → PUBLISHED`。
  - 任一步出錯都會先進 `RESTORING`。
  - 還原失敗則停在 `HELD_FOR_HOST`，送出維持停止。
- 階段 1b（延後、另經審查）：只有 `claude.skills` 有機會改為資訊性，條件是：
  - 用釘選的 2.1.316、正式 PATH，在完整 init skills 清單中判 `not_loaded`；
  - D123 的執行時保護（init 一宣告 skills 就以工具越權停線）仍然有效。
  - 改類別不影響雜湊，不需要遷移。
- Codex 已判 loaded、Gemini 與共同來源為 unknown：一律維持受保護，除非落入階段 2 白名單。

## 4. 階段 2：有界的自動重驗

### 4.1 白名單（day 1 只有這 8 個 source_id）

- 單檔：`claude.global_instructions`、`codex.global_instructions`、`agy.gemini_md`、`agy.agents_md`。
- 目錄：`claude.skills`（不含 synced）、`codex.skills`、`agy.skills`、`agy.rules`。

### 4.2 逐檔格式檢查

整個來源都要檢查，因為基準只有聚合雜湊，無法只看差異。檢查時只記錄雜湊與違規代碼，不保存內容。

- 必須是一般檔案，`nlink=1`，不是 symlink，沒有執行位元，不是隱藏檔。
- 副檔名只能是 `.md`。單檔 ≤64 KiB，單一來源 ≤512 KiB 且 ≤64 檔。
- 必須是合法 UTF-8，不含 NUL，除 `\t\r\n` 外不含控制字元。
- 禁止 `@` 匯入語法（正規式 `(^|\s)@\S`）。CLAUDE.md／GEMINI.md 可透過它載入未盤點的檔案。
- skill 的 frontmatter 只允許 `name`、`description`。出現 `allowed-tools`、`model`、`hooks` 或任何未知鍵即拒絕。
- 拒絕標記（不分大小寫）：`metaculus`、`FLABAUTO-`、`FLABCANARY`、`Forecast Lab auto-load test`、`BEGIN PRIVATE KEY`、`sk-`／`ghp_` 等常見 token 前綴。
- 檢查當下以 `_hash_dir` 的同一演算法重算雜湊，必須等於 snapshot 雜湊；不等就判 UNKNOWN。

### 4.3 三種判定

- **ALLOW**：屬於白名單，且所有檢查都通過。
- **DENY**：命中任何一條拒絕規則。照現行停線，等人處理。
- **UNKNOWN**：包括讀取錯誤、walk error、雜湊不一致、不在白名單、延後項目、新出現的 source_id、任何 blocker、common 來源變動。一律比照 DENY，不自動處理。
- 只要該線有任一非資訊性變更不屬於 ALLOW，整個候選就不合格。

### 4.4 候選種類（R12 的區分）

- `text`：只有白名單來源變動。
- `cli_version`：只限 gpt／gemini，可同時伴隨 gpt 的 catalog 變動。Claude 版本若改變，代表 pin 失效，判 DENY（依 D119）。
- `catalog`：只限 gpt；基準 ok，只有 catalog 雜湊不同。
- `text` 不得和其他種類混在同一候選。
- 以下一律 DENY：缺少符合目前設定的 production 證據（D118 期間設定凍結，不可能合法出現）、runtime 指紋改變、judge 版本改變。
- 便宜模型的 PASS 永遠不算證據。

### 4.5 候選綁定

`candidate_sha = hash_obj(...)`，內容包括：

- player、kind、各來源的 `_fingerprint`；
- 原核准的 `record_id`；
- 該線 CLI 版本、二進位檔路徑與 SHA-256；
- tier1／tier2 的 model／effort；
- `runtime_fingerprint`、`JUDGE_VERSION`；
- gpt 的 catalog 雜湊；
- 格式檢查結果的雜湊；
- `policy_version="auto_reaccept_v1"`。

### 4.6 限流與預算

- 啟動前必須依序滿足：
  1. 取得 `forecast.X.lock`；
  2. 在鎖外完成 snapshot、CLI 版本、quota 等外部查詢，並先做 Gemini 准入預檢；
  3. 進入 `ledger.transaction` 後只讀寫帳冊：該線滾動 24 小時內 `auto_reaccept_started` 筆數為 0；同一 `candidate_sha` 沒有失敗／DENY 紀錄；剩餘週次數 ≥ 計畫呼叫數；
  4. 寫入 `auto_reaccept_started{start_id, candidate_sha, kind, run_ids, planned_calls, pid, boot_id}`。
- 寫入 started 就占用名額。失敗、中斷、候選被取代都計數。
- 計畫的驗收次數取 tier1／tier2 中不同 (model, effort) 的組合，最多 2 次；每次 3 個呼叫，所以最多 6 次，全部計入原有週上限。
- 預算函式不明或預算不足時，不啟動，也不占名額。
- 只有 ledger 交易能決定誰取得名額，並行程序最多一個成功。

### 4.7 執行與核准

1. 以 `run_line(production=True, tier=t, run_id=f"autore_{start_id}_t{t}")` 執行驗收。
2. 逐項核驗結果：`line_pass`、mode、model／effort、`cli_version`、`judge_version`、evidence 雜湊、`code_sha256` 等於 runtime 指紋、`tier2_equivalent_to_tier1`、catalog 雜湊；evidence 的 `baseline_before` 與 `baseline_after` 中，候選來源的指紋和版本都必須等於候選。
3. 在交易鎖外取 live snapshot；進入交易後核對：
   - `latest_approved` 仍是候選記錄的原核准；
   - 沒有已用相同 `candidate_sha` 的核准；
   - 該線之後沒有新的非 autoload `acceptance_call_started`；
   - 複製舊來源、只替換候選來源（`cli_version` 種類則只換 `versions[X]`）組成 scoped snapshot 後，`compare(scoped, live)` 對 X 線判 ok，且沒有 blocker。
4. 透過 `B.approve(player=X)` 寫入核准：
   - `reviewed_by="auto_reaccept_v1:D121"`，不得冒用 Louis；
   - extra 欄位帶 `candidate_sha`、`start_id`、`isolation_record_ids`；
   - 同時寫 `auto_reaccept_finished{outcome=approved}`。
5. `catalog` 種類不寫基準核准，驗收結果就是前檢的依據。
6. 驗證不通過時，寫 `finished{failed|superseded}`。之後只發布狀態，不含任何內容。

### 4.8 狀態機

`IDLE → CANDIDATE`（記錄、不計數）`→ [observe 模式停在此] → STARTED`（計數）`→ RUNNING → VERIFYING → APPROVED | FAILED | SUPERSEDED | INTERRUPTED`

前檢不另開讀取入口，實際讀取者就是既有的 `latest_approved()`、`check_acceptance()`、catalog 比對。

## 5. 中斷恢復

`auto_reaccept.py tick` 開始時，先持該線鎖呼叫 `recover_acceptance(...)`（鎖已持有時 `recover_acceptance` 會跳過再取鎖），然後處理每筆沒有 finished 的 started：

- 對應 run 已有全部 pass 的結果 → 重新跑 4.7 的步驟 2～4。以 `candidate_sha` 確保冪等，不重複核准。
- 有 run 失敗，或被補寫為 interrupted → 寫 `finished{interrupted|failed}`。
- 仍有 `acceptance_call_started` 未逾時，或程序還在 → 跳過，不做任何判定。

所有補寫在寫入前都先檢查該事件是否已存在。

## 6. 鎖的區別

- 階段 1 會在全域與共同來源放 canary，必須取得 B2 的全部鎖。
- 階段 2 不修改共享來源，只持受驗那一線的鎖（D120 的前例）。
- 一般開發與審查使用隔離副本，不持任何鎖。

## 7. 回退

- 階段 2 以常數 `MODE ∈ {off, observe, enforce}` 控制，切換須升版並以互斥程序部署。
- 退回舊 patch 時，新事件會被舊程式忽略；既有的 `baseline_approved` 仍有效。
- 要撤銷一筆自動核准，只能在維護時段執行 `auto_reaccept.py revert --start-id`：重新寫一筆 scoped 核准，帶回被取代的來源與版本。帳冊不改寫。
- 階段 1 的回退就是 B1／B4 的還原。

## 8. 測試清單（應有，但尚未執行）

- **還原**：外部同時改檔、刪檔、改成 symlink 或 hardlink、在 ftruncate 前後被殺；有衝突時不覆蓋且 `restore_ok=false`；新建檔案被外部改過時不刪除。
- **持鎖入口**：任一鎖被占用時全部釋放後延後；逾時放棄時沒有放入任何東西；不呼叫 kill；PATH 或 pin 不符就中止；`S_pre`／`S_post` 有差異時中止。
- **未結案**：`autoload_unclosed` 會擋三線；recover 可重入。
- **白名單**：每條 ALLOW／DENY 規則；`@` 匯入；frontmatter 未知鍵；腳本；隱藏檔；檔案大小；雜湊競態；他線或 common 變動；blocker；延後來源；種類混合。
- **限流**：24 小時邊界；兩個程序並行只有一個成功；失敗、中斷、取代都計數；同一 `candidate_sha` 不重複；預算不足不占名額；呼叫計入週上限。
- **綁定**：驗收前、中、後候選改變；原核准 ID 競爭；evidence 被竄改或雜湊不符；catalog 改變；只有 tier1 有證據時 tier2 前檢應失敗；便宜模型 PASS 被拒。
- **實際接點**：核准後重新執行第 1、2 層 `forecast.preflight`，結果應為 ok；證據錯誤時仍拒絕；Claude 版本改變判 DENY。
- **回歸**：舊帳冊重播；D123 同步技能、D119 候選與第一批軟失敗語意不退化；受保護檔案雜湊不變；完整隔離回歸。

## 9. 執行前提（阻擋項）

1. B1～B4 完成。
2. 確認 E8 的五項事實。
3. 完整隔離回歸全部通過。
4. Gemini 獨立審查與 Claude 完整審查都判 PASS（目前兩者都尚未進行）。
5. 互斥套用並核對受保護雜湊。
6. 只能在 `MODE=off` 下執行階段 1；啟用 `enforce` 前，另須滿足 X1、X2。

## 10. 未解決的政策矛盾（需整合者或 Louis 處理）

- **X1**：PLAN §7.2 正文寫明，指示、skills、記憶的內容變動必須人工審查；`B.approve` 的 docstring 也寫「human content review」。D121 雖已核准 C，但正文尚未更新。在 §7.2 改寫並登記 §15／§16 之前，不得進入 `enforce`。
- **X2**：§7.2 要求基準內容不含比賽資訊或歷史答案。4.2 的拒絕標記只是啟發式篩選，無法保證這一點。D121 已接受 GPT 指出的這項殘餘風險，建議在 PLAN 中明文記載。
- **X3**：觸發點。`cycle.py`、`scheduler.py`、`launchd/run.sh` 都不在允許修改的清單內；若在 preflight 內同步執行驗收，會拖慢其他線。建議二選一：
  - 在 `run.sh` 加一行：cycle 結束、鎖釋放後執行 `auto_reaccept.py tick`；
  - 新增一個由 Louis 安裝的 launchd job。

  若從 preflight 以 detached 方式啟動，launchd 的程序組回收行為尚未驗證，不建議。
- **X4**：依 E5，自動重驗一旦失敗，最新一筆驗收就是失敗，即使內容改回原樣，該線仍停線。這符合安全原則，但與軟失敗的預期不同。day 1 維持現狀並明文記錄；同一 `candidate_sha` 失敗後不自動重試。
- **X5**：依 E6，修正「permissions 落在 GENERAL」需要遷移基準。在 D118 窗口內不建議處理，因此 settings 的自動重驗整項延後。

結論：本文件只是規格草案，不改變 PLAN、基準或目前的預測行為，也不構成任何審查通過或核准。

## 整合者補充（GPT，待 Gemini 審查）

- D124 已接受今晚實測時間；本階段不再詢問同一時段。規格 v0 的 observe/enforce 區分保留，observe 完成不得稱 R2 自動恢復已上線。
- E8 核對：forecast.runtime_fingerprint 是 adapters/{player}.py、flab/runner.py、flab/netguard.py；acceptance.code_fingerprint 也記錄 acceptance、judge、baseline，但前檢只逐項核對 runtime subset。submit 鎖為 data/submit.lock；cycle 的 root 是 data。budget.reserve 計入 acceptance_call_started 的唯一 attempt_id，另兼容沒有 started 的舊 acceptance_result。
- 只寫入部分 marker 但未取得可信 placed 證據時，不能假裝已完整還原；保存備份、報 restore_conflict 並拒絕預測，包含崩潰恢復。此行為須更新原本假設任何部分寫入都能自動清除的測試並記錄原因。
- 全域測試的成功定義包含逐位元還原、manifest closed、S_pre/S_post 的受保護項相符及兩層前檢；只有無衝突的還原才關閉 manifest。持鎖退出遇還原失敗須建立 STOP_SUBMIT、保留 unclosed，不能無期限占鎖，也不能寫 success。
- 階段 1 最多 3 次實際模型呼叫（每線 1 次）；只允許尚未呼叫任何模型的前置失敗重試 1 次。不得藉重跑增加已開始的模型次數。每次呼叫照原週上限入帳，開始前檢查 D118 及 D105 接續計入後仍有容量。
- X1：依已核准 D121 在 enforce 前由整合者更新 PLAN §7.2、§15、§16，不重問 C。X2：純文字隔離不能證明內容完全無比賽資訊，此證據限制必須誠實保留；不以字串篩選宣稱證明，硬性禁止人為介入仍適用。
- X3：cycle.py／scheduler.py／launchd/run.sh 不屬硬性禁止檔案，可提出受審查的單一排程接點；不是必須請 Louis 新裝 job。避免 preflight 阻塞其他線，observe 不呼叫模型。
- X4／X5 保留並明列後續範圍；本輪優先完整完成 0／1，再交付具測試的第二階段，沒有未審查的 enforce 切換。
