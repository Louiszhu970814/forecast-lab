# Forecast Lab 第二輪程式碼審查（Claude 具名審查者）

## 結論：PASS（沒有阻擋項）

- 這只是 Claude 這一側的程式碼與設計審查結果，不代表可以部署。
- 依 PLAN，部署還需要 Gemini 的獨立審查（本包沒有附）、PLAN 整合者（GPT v1.84）完成整合，以及 Louis 的決定。
- 我沒有執行任何測試。下文所說的「證據」都來自閱讀本包附的原始碼與作者的測試紀錄。
- 本包裡沒有任何預測內容、帳號憑證或模型答案，我也沒有讀到這類資料。

---

## 一、第一輪阻擋項逐項核對

1. **六次呼叫上限是全域的；同一題、同一玩家跨批次不能重跑**：已修正。
   - `register_admission` 在共用帳本的同一個鎖定交易內，依序做三件事：讀出 `usage`、檢查「同一玩家同一題」與 `len(mine)>=6`、呼叫 `b.reserve`。
   - `run()` 在鎖內另外先擋「這一題已登記過」。
   - 對應測試：`test_cross_batch_research_is_not_an_extra_attempt`、`test_six_admissions_cannot_be_extended`、`test_completed_player_question_cannot_be_rerun`。
2. **`advisory_protected` 同時扣住次數保留與 Argon 保留，且一次完成**：已修正，但 Argon 部分有上限，見 N1。
   - 次數部分：`used + 22 + 1 > cap` 時拒絕。
   - Argon 部分：保留量是 `min(22 × 第一層估計, TIER1_QUOTA_RESERVE_CAP)`。
   - 兩者在同一次 `reserve` 交易內判定。
3. **budget_v7 的 token 評估相容性**：已修正。`TOKEN_POLICY_VERSIONS` 同時包含 v7 與 v8，有對應測試。
4. **設定與開關在六把正式鎖下檢查**：已修正。`run()` 先拿到六把鎖才比對設定雜湊；每次呼叫前，`check()` 會重新讀設定雜湊、`players_enabled` 與 `STOP_SUBMIT`。
5. **只在 MiniBench 期間運作**：已修正。`campaign.venues != ['minibench']` 時拒絕。
6. **准入決定的 ID 有正確帶進紀錄**：已修正。
   - `run_attempt` 用 `inputs = ctx.reconciliation or {}`。如果傳入空字典，`or {}` 會換成另一個新字典，之後寫入的 `admission_decision_id` 就會遺失。
   - 這版傳入的字典本來就有內容，所以是同一個物件。`before()` 寫入的 ID 會進到開始紀錄與結果紀錄。
   - `test_real_engine_keeps_admission_trace_and_no_bot_answer` 驗證了三方 ID 一致。
7. **notify 有傳遞到共用線路狀態**：已修正。`except LineStop` 會依 `exc.notify` 寫入 `line_stop` 或 `advisory_line_yield`。
8. **單次洩漏即停住共用線路**：已修正。只要出現 `leak_confirmed`、工具違規、`safety_stop_reason` 或 `recovery_no_rerun` 任一項，就寫入 `line_stop`，需要 Louis 手動解除。
   - 寫入前會檢查重複，重放時不會重複寫；有對應測試。
   - 共用線路狀態只寫原因代碼，不含答案內容。
9. **只含元資料的 `advisory_guards` 有進到 `forecast.line_stopped`**：已修正。
   - 我追蹤了 `line_stopped` 的新流程：守衛紀錄轉成 `forecast_attempt` 後，非舊版的 `model_mismatch` 會跟機器人自己的紀錄合併計算退避與冷卻，並遵守 `resumed_at`。
   - 人類線的 `quota_exhausted` 會讓機器人那條線等到 `quota_retry_at_utc`。
10. **forecast_pipeline_v9 只改了守衛**：從完整原始碼看，與這次需求相關的只有 `line_stopped` 讀取守衛，以及版本號改為 v9。v9 不在舊版 model mismatch 清單（v1–v6）內，所以失配時是退避，不是硬停。但我手上沒有與前一版的差異檔，見 E2。
11. **復原流程**：已修正。
    - `--recover` 拿到六把鎖後，用原本的 `F.recover_interrupted` 處理批次目錄內的私有帳本。它的鎖、`recovery_events` 與 `budget_events` 都落在批次目錄，不碰正式帳本。
    - 之後只用元資料呼叫 `b.outcome` 與 `finish_accounting`，不呼叫模型。
    - 已開始但沒有結果的嘗試，維持已計費且封鎖該線；`orphan.expired` 與 `no_process` 兩項都成立才會關閉。
    - 「已登記但沒開始」可以結案為 `started=False`。理由是 `run()` 整個執行期間都握著六把鎖，復原能拿到鎖就代表原呼叫者已結束。
12. **共用帳本的結案資料互相矛盾時拒絕繼續**：已修正。`reserve` 會比對 `advisory_finished` 與 `budget_outcome`，矛盾時拋錯；有對應測試。
13. **三條人類線各寫自己的私有帳本**：已修正。三條線寫在各自的 `batch/<player>/` 目錄，`F.run_attempt` 寫入後讀取時不會跟其他線互搶。
    - 共用帳本只寫 `quota.jsonl`（Gemini 快照）、`budget_events`、`advisory_usage`、`advisory_guards` 與 `line_status`，全部走 `transaction(shared)`。
    - 這個鎖是「每個根目錄一個 RLock，再加 flock」，所以同一行程內的不同執行緒也會排隊。
14. **三條線都結束後才彙整，且只讀私有檔**：已修正。`ThreadPoolExecutor` 用 `with` 區塊，三個 `f.result()` 都回來後，才讀三個私有 `forecasts.jsonl` 與 `audits.jsonl` 做彙整。正式帳本不會寫入任何人類線的答案。

## 二、作者主張的評估

- **多執行緒**：我接受。
  - 三個工作執行緒只呼叫 `run_attempt`，它不會呼叫 `recover_interrupted`、`recover_acceptance` 或 `F.Lock`。所以 `_HELD_LOCKS` 是執行緒私有這件事不影響正確性；就算真的重複上鎖，也會拋 `PreflightError`，不會出錯放行。
  - `_CREATED_ATTEMPT_BASES` 只對各自不同的隨機鍵做 set 與 pop。
  - `runner` 沒有使用 `chdir`、沒有修改 `environ`、沒有 `preexec_fn`。
  - 我同意不必為此做全域鎖重構。
- **機器人優先**：我接受。
  - MiniBench 檢查放在 preflight 之後、開始呼叫之前，判定條件是「進行中的 MiniBench 有 open 題目就讓開」。
  - 已排定但還沒開始的 MiniBench 期次與 33121 只用來比對重疊題目。
  - 執行期間 cycle 會因 `cycle.lock` 被占用而得到 `overlap_skipped`，launchd 下一次觸發時自然恢復。
  - 最壞延遲約是一次第一層逾時（600 秒）、兩次 Gemini 額度查詢與 preflight 時間的總和，有上限。
- **Gemini 額度估計的資料來源分離**：我接受，方向是保守的。
  - 人類線在 `gemini_admission` 只讀私有 forecasts，呼叫數少於 `min_calls`，所以一定退回備用值 0.05（上限值）。
  - 機器人那邊的估計，分母不含人類線的呼叫，但讀數包含人類線的快照。結果只會讓每次呼叫的估計變大，不會讓額度看起來變多。
  - `Budget.reserve` 用的「呼叫前」快照，在 `run_attempt` 中正確寫入共用的 `quota.jsonl`，查得到。
- **執行時程式指紋**：我同意，adapter、runner 與 netguard 沒有變動時，不需要重新做模型驗收。preflight 執行時會比對 `runtime_fingerprint`，有變動就拒絕。

## 三、阻擋項

無。照目前的程式，兩題的研究可以安全執行。

## 四、非阻擋的建議（不影響這次兩題）

- **N1（文件需更正）Argon 保留上限是 0.05**
  - 包內說「完整保留 22 次呼叫的 Argon」，並不精確。實際保留量是 `min(22 × e1, FS.TIER1_QUOTA_RESERVE_CAP=0.05)`，第一層估計只要約 ≥0.0023，上限就會生效。
  - 這跟機器人第二層保護第一層用的是同一個已核定上限。D97 之後 Gemini 的 `weekly_cap` 是 1.0，人類線最多兩次 Gemini 呼叫，而且 `gemini_admission` 本身也要求剩餘量 ≥0.05，所以不算阻擋。
  - 建議 PLAN 與文件改寫成「次數保留 22 次，Argon 保留上限 5%」。如果 Louis 希望人類線嚴格排在第二層之後，需要另外訂規則。
- **N2（穩健性）`finish_accounting` 寫入結案前沒有確認 `budget_outcome` 存在**
  - 如果 `run_attempt` finally 區塊裡的 `admission_outcome` 因為帳本寫入失敗而拋錯，且這次沒有開始，`run_line` 仍會寫入 `advisory_finished(started=False)`。
  - 之後機器人每次 `reserve` 都會因為「結案與結果矛盾」而失敗，而且 `--recover` 會跳過已結案的紀錄，修不回來。
  - 結果是拒絕繼續、不會出錯放行，發生機率也低。建議在 `run_line` 的 finally 裡，`finish_accounting` 之前先冪等地呼叫一次 `b.outcome(aid, started)`，或讓 `finish_accounting` 在缺少 `budget_outcome` 時直接 return。
- **N3（測試缺口）**
  - `fresh_inputs` 的讓開判定沒有測試，包括：進行中期次有 open 題要讓開、未來期次只排除不讓開、索引不完整要拒絕。
  - `run()` 的三執行緒整合沒有測試，包括彙整與 `missing_players`。
  - Gemini 用私有歷史時退回備用值的路徑沒有測試。
  - 這些都是包內主張的關鍵性質。我讀過程式，判斷是正確的，但建議之後補上測試。
- **N4（取捨）沒有格式修復**
  - 輸出是可修復的 parse 或 schema 錯誤時，那條線這次就沒有可用答案，紀錄中仍會計算 `repair_basis`。
  - 這符合「每線一次、零自動重試、六次上限」的授權。如果想提高成功率，等於擴大範圍，需要重新核准上限，現在不建議。
- **N5（小問題）**
  - `fresh_inputs` 只檢查「至少一個」slug 是 minibench，`continuous` 那邊要求剛好一個，建議一致。
  - `check()` 不會重新抓題目狀態，只看時間。
  - `forecast.py resume` 無法提前解除人類線的 quota 退避，但它最多 30 分鐘就會自動到期。
  - `run_line` 的 `LineStop` 與 `propagate_safety` 可能各寫一筆 `line_stop`，原因代碼不同，無害。
- **N6** 彙整如果拋錯（例如之前當機留下有稽核紀錄但沒有預測的嘗試），沒有離線重新彙整的指令。私有資料仍然完整，可以之後補。

## 五、缺少的具體證據（不是猜測性的阻擋）

- **E1 測試紀錄沒有綁定程式版本**：作者附的「241 passed」沒有附上：
  - 執行的指令與測試檔清單（是否包含完整的 forecast、scheduler、fail-soft 測試）；
  - 執行時各檔案的 SHA256 是否等於本包所列的值；
  - 與先前「focused118」的關係。
  這需要補成一份可追溯的紀錄。我無法確認，也沒有自己執行。
- **E2 沒有與前一版的差異檔**：沒有 `forecast.py` 與 `budget.py` 相對前一個已核准版本的 diff，也沒有「受保護檔案未變動」的雜湊對照表。所以「只改守衛」與「受保護檔案未變」這兩點，我只能從完整原始碼推論，無法逐行確認。
- **E3 題號對應與 Cup ID 無法從包內驗證**：`SELECTED_POSTS` 的對應（45330→45519、46106→46343）和 33108 是否就是 Cup 專案 ID，只能照使用者的選擇接受。
- **E4 設定雜湊只能在執行時確認**：campaign 釘住的設定雜湊用的是 `hash_obj`，不是檔案的 SHA，所以無法從包內比對；執行時不符會拒絕。
- **E5 Gemini 獨立審查還沒附**，PLAN 整合者與 Louis 的部署決定也還沒有。
- **E6 Cup 規則（不在程式範圍）**：PLAN D79 規定機器人不在 Cup 送出，這個 advisory 也確實沒有送出功能。但 Cup 人類賽是否允許參賽者使用 AI 研究輔助，屬於規則核對，包內沒有證據。這要由 Louis 自己確認，不是程式阻擋項。

## 六、核准後的執行建議（我沒有執行）

- 兩題依序各跑一次 `--post`，不要同時跑，否則第二次會因拿不到鎖而失敗。
- 如果某次輸出裡有 `unresolved` 或當機，先執行 `--recover`；在 `advisory_usage` 清空之前，`run()` 會拒絕執行。
- 如果出現 `line_stop`，表示那條線需要 Louis 手動解除。