# Forecast Lab 設計／程式審查：Cup advisory（posts 45330、46106）

## 結論：REQUEST CHANGES（要求修改，不核准部署）

- 本審查只依據提供的 source packet，範圍限於程式、安全與預算契約。
- 我沒有執行任何測試，也沒有讀取檔案、呼叫模型或連網。「45 個 focused tests 通過」與「integration suite 執行中」都是提交者的說法，我沒有驗證。
- 依 packet 的要求，部署前需要 Claude 完整審查加上一份獨立的 Gemini 審查。本文是 Claude 審查，結論是要求修改。Gemini 審查我沒有看到。所以目前沒有任何部署授權，我也不會把本文寫成核准。
- 證據分三類，下面會標出：
  - 【證據】：直接引用 packet 程式碼得出的推論
  - 【條件】：要看到缺少的 source 才能判定
  - 【提案】：建議的修補，尚未實作，也沒有測試

---

## 一、阻斷性問題（BLOCKERS）

### B1．Advisory 的安全停線訊號被吞掉，bot 線看不到
**問題：** 同一個 CLI 在 advisory 中出現安全事件時，bot 照樣用同一條線、同一個 profile 繼續跑。

**【證據】**
- `F.run_attempt` 遇到以下情況，會在寫完紀錄後 `raise LineStop(..., notify=True)`：
  - tool_scope_violation
  - Gemini 執行後 credits 不為 0（D54）
  - 7 天內累積 2 次 leak_confirmed
- 只有 `forecast.run()` 會把 `notify` 寫進 `line_status.jsonl`。
- `advise.run_line` 用 `except Exception` 把 LineStop 全部吞掉，只回傳 `{"error_type": "LineStop"}`。
- bot 用的兩個閘門都讀不到 advisory 的紀錄：
  - `line_stopped()` 只讀 shared 的 `line_status.jsonl` 與 shared 的 `forecasts.jsonl`
  - `leak_count()` 也只讀 shared 的 `forecasts.jsonl`
  - advisory 的 forecasts／audits 都放在 `data/advisory/<batch>`
- packet 說 line status 會寫到 shared metadata，但程式裡沒有任何地方寫入。

**影響：** 這違反 §7.3／§7.5／D54／D60 的停線語意。Advisory 的 leak 也不會計入 bot 的 leak 視窗；跨 batch 的兩次 leak 甚至不會觸發停線。

**【提案】修補：**
```python
# advise.py
def record_line_stop(shared, player, run_id, qid, reason):
    with transaction(shared):
        append_record(shared / "line_status.jsonl", {"type": "line_stop", "player": player,
            "run_id": run_id, "question_id": qid, "source": "advisory", "version": VERSION,
            "reason": reason, "at": to_iso(utc_now())})   # 只寫 metadata，不含答案

# run_line 內，放在 generic except 之前
    except F.LineStop as exc:
        if exc.notify:
            record_line_stop(shared, player, run_id, snap["question_id"], str(exc))
        return {"player": player, "error_type": "LineStop", "notify": exc.notify, "adoptable": False}
```
- 另外，`run_attempt` 正常回傳後，若 `"leak_confirmed" in rec["own_audit_flags"]`，也要寫一筆 notify 等級的 line_stop。理由是 advisory 的 leak 無法進入 bot 的 `leak_count`，最保守的做法是交給 Louis 決定 resume。
- `model_mismatch` 與 `quota_exhausted`（非 notify）至少要寫一筆 shared metadata 事件，或由 PLAN owner 明確決定 bot 不需要知道。
- 要補的測試：
  - violation、nonzero credits、leak 各自產生 shared line_stop
  - 之後 `F.line_stopped(shared...)` 回傳 stop

### B2．「每題每模型一次、總共 6 次」只在同一個 batch 目錄內成立
**問題：** 換個 batch 目錄，就能對同一題、同一模型再跑一次，違反「six initial calls; zero automatic retries」。

**【證據】**
- `register_admission` 只在 `r.get("batch") == root.name` 時拒絕重跑。
- 新建 `data/advisory/b2/` 就能對 45330／46106 再跑三個模型。
- `root.name` 也不是唯一識別：不同父目錄下的同名資料夾會被當成同一個 batch。
- 全域沒有「總數 ≤ 6」的上限。

**【提案】修補（在 shared transaction 內做）：**
```python
MAX_TOTAL_CALLS = 6
mine = [r for r in admitted.values() if r.get("version") == VERSION]
if any(r.get("question_id") == request["question_id"] and r["player"] == request["player"] for r in mine):
    raise F.QuestionDone("player/question already admitted in any batch; no rerun")
if len(mine) >= MAX_TOTAL_CALLS or request["question_id"] not in SELECTED_POSTS.values():
    raise F.QuestionDone("advisory session call cap reached")
```
- 被拒絕的 admission 必須仍然計入次數（即使 started=False），不能因為這樣就拿到新額度。
- 以後的 session 要換新的 VERSION，並附上新的明確授權。
- 要補測試：跨 batch 重跑被拒；第 7 次被拒。

### B3．Gemini 的 Argon 額度沒有替後續 bot 工作保留
**問題：** 「保留 22 次」只對嘗試次數生效，Argon 額度沒有保留。

**【證據】**
- `register_admission` 呼叫時用 `pending_tier1=0, expected_new=0, repair_reserve=0, tier=1`。
- 因此 `reserve` 內 `protected = 0`，`protected_quota = min(0 * first_estimate, cap) = 0`。
- 結果是 Gemini advisory 只要守住 weekly_cap 線就能放行，可能吃掉 bot tier‑1 需要的 Argon 空間。這違反 PLAN §8「只在 shared remaining budgets 內」。
- 事後的 `weekly_attempts_used + protected + 1 > cap` 檢查只看次數。

**【提案】修補：**
- 讓 `budget.reserve` 正式接受 advisory 保護量。bot 呼叫端用預設值 0，行為不變。
```python
def reserve(self, request, *, pending_tier1, expected_new, repair_reserve, advisory_protected: int = 0):
    ...
    if type(advisory_protected) is not int or advisory_protected < 0 or (advisory_protected and tier != 1):
        raise ValueError("invalid advisory protection")
    protected = pending_tier1 + expected_new + repair_reserve if tier == 2 else advisory_protected
```
- 這樣次數上限（`used + protected + 1 > cap`）與 Gemini 的 `protected_quota` 會用同一個保護量，在同一個 transaction 內判定。
- `register_admission` 改傳 `advisory_protected=protected`。原本的事後檢查可以留著當第二道防線。
- 要補測試：Gemini 的 `argon_remaining` 只夠單次估計、不夠保留量時被拒。

### B4．Advisory 的孤兒 start 沒有恢復路徑，bot 線會被永久封鎖
**【證據】**
- `finish_accounting` 只在 `run_line` 的 `finally` 中執行。
- 程序遭 SIGKILL、OOM 或斷電時，`advisory_admitted` 就永遠沒有對應的 `advisory_finished`。
- `reserve` 會一直回傳 `unresolved_or_running_attempt`。
- bot 的 `recover_interrupted` 只看 shared 的 starts，看不到 `data/advisory/<batch>/forecast_attempt_starts.jsonl`。
- 孤兒的 raw 證據也沒有經過 audit 或 tool-violation 檢查。
- 「unknown 就保守收費並封鎖」本身沒錯。但 bot 自己的 unknown 在 `orphan.expired` 加上 `no_process` 後會被關閉，advisory 卻無期限封鎖 bot 線，與 §8「advisory 在 bot 之後」的優先順序相反。

**【提案】修補：** 新增 `advise.py recover --batch` 子命令，重用不變的 `F.recover_interrupted`：
1. 以非阻塞方式取得 shared 的 cycle／scheduler／submit 與對應的 `forecast.<player>.lock`。
2. 用與 `run_line` 相同的 batch 路徑建 `F.Context`（forecasts／audits／raw 指向 batch），呼叫 `F.recover_interrupted(ctx)`。
   - 注意：它會在 batch root 加 `forecast.<player>.lock`，並寫入 `recovery_events`。
   - 它的 budget_outcome 段讀的是 batch root 的 `budget_events`，在這裡不會動作，因此 shared budget 要另外補。
3. 對每個已關閉的 aid：
   - shared 中沒有 budget_outcome 時，補 `b.outcome(aid, True)`
   - 再呼叫 `finish_accounting`（此時已有 interrupted 紀錄，會寫 `started=True`）
4. 復原後的紀錄若有 `tool_violations`、`audit_flags` 或 `recovery_no_rerun`，依 B1 寫 line_stop。
- 要補測試：寫一筆孤兒 start → 未過期時仍封鎖 → 過期且無程序後解鎖，次數仍計入。

### B5．三個官方 CLI 在同一程序的 threads 中執行，可能和 acceptance 的執行模型不同
**【證據】**
- `run()` 用 `ThreadPoolExecutor(max_workers=3)`，在同一個 Python 程序裡同時呼叫三個 adapter 的 `run`。
- packet 說「adapters/runner/netguard unchanged」，也說 profiles 今天通過 acceptance。但沒有說明 acceptance 或 bot 是不是也在單一程序內用多執行緒跑三條線。
- 以下都是程序層級的狀態，多執行緒下會互相干擾：
  - `os.chdir`、`os.environ` 修改
  - `signal.signal`（只能在主執行緒呼叫，否則 ValueError）
  - socket／proxy 的全域 patch（netguard）
  - `subprocess` 的 `preexec_fn`
  - 共用暫存設定
- 另外，`F._HELD_LOCKS` 是 `threading.local`。worker thread 不知道主執行緒已持有 line lock；如果 adapter 或 runner 內部有東西檢查它，就會出錯。

**【條件】** 需要以下 source 才能判定：`flab/runner.py`、`flab/netguard.py`、`adapters/{claude,gemini,gpt}.py`，以及 continuous scheduler 平行跑三條線的方式。

**【提案】修補：** 擇一：
- (a) 提出證據：bot 本身就在同一程序內用多執行緒跑這三個 adapter，且 acceptance 涵蓋這種模式。
- (b) 改為每條線一個子程序：`ProcessPoolExecutor(mp_context=multiprocessing.get_context("spawn"))`。父程序持有 locks；子程序只執行 `run_line`。
- (c) 改成依序執行（最多約 3×(600+overhead) 秒，鎖會持有更久，需權衡）。

### B6．`admission_decision_id` 永遠沒有寫進 start／forecast 紀錄
**問題：** budget 決策 → CLI start 的會計追溯鏈斷了。

**【證據】**
- `shared_inputs = {}` 被當成 `ctx.reconciliation` 傳入。
- `run_attempt` 一開始執行 `inputs = ctx.reconciliation or {}`。空 dict 是 falsy，所以 `inputs` 是一個新的 `{}`，不是 `shared_inputs` 本身。
- 之後 `before()` 寫入的是 `shared_inputs["admission_decision_id"]`，`start_record` 和 `rec` 讀的是 `inputs`，結果一律是 `None`。

**【提案】修補：**
- 改成 `shared_inputs = {"advisory_batch": root.name, "advisory_version": VERSION}`，讓 dict 非空、保持同一個物件。（tier 1 對 `inputs` 只用 `.get`，多出的鍵不影響。）
- 要補測試：start 列與 forecast 列的 `admission_decision_id` 等於 `budget_reserved.decision_id`。

### B7．VERSION 升到 budget_v8 後，已完成但尚未評估的 v7 tier‑2 嘗試會永遠不被評估【條件性】
**【證據】**
- tier‑2 的 token 評估是延遲執行的：要等該 player 下一次 tier‑2 reserve 才做。
- 評估條件是 `reservation.get("version") != VERSION → continue`。
- `recover_interrupted` 仍寫 `"budget_v7"`，可以推知上一版是 v7。
- 部署當下，任何「v7 預約、已完成、但該 player 還沒有下一次 tier‑2 reserve」的嘗試，都不會產生 `tier2_token_assessed`。
- 結果是 `tier2_token_breaker` 少算 breach。packet 說「caps unchanged」，這點與它不符。

**【提案】修補：**
- `ASSESSABLE = frozenset({"budget_v7", VERSION})`，並把判斷改成 `if reservation.get("version") not in ASSESSABLE or ...`。
- 或者在部署前提出證據：shared ledger 中沒有未評估的 v7 tier‑2 forecast 列。
- 要補測試：v7 預約的 tier‑2 超量，在 v8 下仍會產生 exceeded，並觸發 breaker。

### B8．「只在 bot 工作之後」沒有檢查 bot 佇列中待處理的工作【條件性】
**【證據】**
- 開跑前只檢查兩件事：shared 沒有未結束的 starts；MiniBench 沒有 open 題。
- 沒有檢查以下工作：
  - continuous campaign 佇列（`questions`）中已排定但尚未開始的 tier‑1／tier‑2
  - 33121 等 bot 賽事中尚未預測的 open 題
- advisory 的 `protected` 也不包含 `pending_tier1`。

**【條件】** 需要 scheduler／cycle 原始碼，以及 `cycle_campaign.json` 的佇列結構。

**【提案】修補：** 擇一：
- 佇列中只要有非終態題目就 yield；
- 或把 `pending_tier1` 與 pending tier‑2 數量加進 `advisory_protected`。

---

## 二、需要補 source 才能判定的事項（可能升級為 blocker）
1. **鎖的名稱與機制：** `cycle.lock`、`scheduler.lock`、`submit.lock` 是否正是 cycle／scheduler／submit 實際使用的路徑，且都用 `fcntl.flock`？若名稱或機制不同，現在的閘門形同虛設。
2. **`cycle_campaign.json` 的欄位：** 是否有 `expected_new_questions`、`repair_reserve`、`run_config_sha256`、`quota_policy_sha256`？
   - `protected` 在 `run_line` 的 try 之外計算，缺欄位時 KeyError 會直接冒到 `f.result()`。
   - 這份檔案能否通過 `budget_handoff.accounting_start` 的註冊比對？
3. **`quota.estimate`／`argon_buckets`／`five_hour_gate`／`has_concurrent_activity`：** advisory 的 before／after snapshot 寫進 shared `quota.jsonl`，但對應的 forecast 不在 shared `forecasts.jsonl`。這會讓 window 估計偏高（保守）還是偏低？會不會讓 bot 的 model check 判成 concurrent？
4. **`orphan.expired`／`no_process` 與 runner 的程序群組處理：** SIGTERM 或 SIGINT 時，CLI 子程序會不會變成孤兒，在鎖釋放後繼續跑？（B4 會讓 bot 在該 player 上被封鎖，但 Argon 與並行行為仍要確認。）
5. **`MetaculusClient.get`／`get_post` 的介面，以及 `projects/minibenches/` 的回應格式。**
6. **其他讀 `budget_events.jsonl` 的程式**（狀態報表、campaign 結算等）：會不會把 advisory 的 `budget_reserved` 再算一次，造成會計不一致？
7. **有沒有程式遞迴掃描 `data/**`**（報表、備份、leak 掃描）：會不會把 `data/advisory/**` 的答案或 raw 混進 bot 流程？
8. **`V.question_schema(snap)` 預設是不是 tier 1；`baseline.ACCEPTANCE_LOG` 是否位於 `data/`**（這決定 `recover_acceptance` 是否辨認得出主執行緒已持有鎖）。

---

## 三、非阻斷問題與提案【提案】
- **Prompt 與 accepted profile 不一致：** advisory 傳 `to_iso(utc_now())` 當 `today_utc`，bot 用 `ctx.now().date().isoformat()`。建議改成相同格式，`prompt_sha256` 才能和 bot 的 prompt 比對。
- **設定 hash 只在執行中檢查：** `cfg`／`policy` 在 `run()` 載入時沒有和 campaign 的 hash 比對，`check()` 比的是當下的檔案。建議載入後立刻驗證 `V.hash_obj(cfg) == campaign["run_config_sha256"]`（policy 同理），確保記憶體中的設定和授權一致。
- **持鎖時間：** 單題 batch 期間，cycle／scheduler 最多被擋約 600 秒加 quota 查詢時間；MiniBench 狀態只在開頭檢查一次。需要 PLAN owner 確認這段延遲可以接受，以及 scheduler 釋放鎖後會立即補跑。也建議在第二題開始前重新做 MiniBench 檢查（每次呼叫都會重做，目前看起來 OK）。
- **爆炸半徑：** `advisory_usage.jsonl` 只要有一筆損壞，`budget.reserve` 對所有 player 都會 ValueError（fail‑closed），三條 bot 線一起停。這符合保守原則，但應補 runbook，並在寫入時採相同驗證（現在已大致如此）。
- **aggregate 在 CLI 之後拋例外：** 會導致 `advisory_batch_finished` 沒寫。會計不受影響（`finish_accounting` 已執行），但建議 try 起來，並把失敗寫進 `runs.jsonl`。
- **被拒時留下空的 raw 目錄：** admission 被拒時 `raw_dir` 已經建立。可接受，但可以在拒絕路徑清掉。
- **未使用的參數：** `fresh_inputs` 的 `campaign` 參數沒有用到。
- **aggregate 本身：** 需求是「回應分開儲存」。aggregate 也只寫在 batch 內，沒有越界；但請確認使用者確實要聚合值，還是只要三份獨立答案。

---

## 四、已核對、目前看起來成立的部分【證據】
- **與 bot 帳本分離：** forecasts／audits／raw 都寫在 `data/advisory/<batch>`；`batch_path` 拒絕 `data`、`data/advisory` 本身與外部路徑。
- **快照白名單：** 已濾掉 community 與 my_forecasts；只接受兩個指定的 post→question 對應；只接受 binary／MC、狀態 open、屬於 Cup 33108；與 bot 賽事題目（ID／post／標題）重疊時拒絕。
- **單次初始呼叫：** 只呼叫一次 `run_attempt(kind="initial")`，沒有 format repair，也沒有 rerun 或提交。
- **budget 寫入的原子性：** 同一個 shared transaction 內，`budget_reserved` 與 `advisory_admitted` 相鄰寫入。`recover()` 把 advisory_admitted 視同已啟動，不會誤釋放。`transaction` 對同一執行緒可重入，跨執行緒由 RLock 加 flock 序列化，沒有看到巢狀跨 root 的死結。
- **unknown 的處理：** 有 start 但沒有 outcome 時維持計費並封鎖；只有 completed 或 no‑start 才寫 finished；no‑start 不計次。
- **genexpr 中的 `aid`：** 不會外洩覆蓋 `reserve` 的 `aid`（Python 3 的 genexpr 有自己的作用域）。

---

## 五、要求補上的測試
（目前 packet 中的測試沒有涵蓋這些。我未執行任何測試。）
1. B1：notify 類 LineStop 與 leak 會寫入 shared line_status，bot 的 `line_stopped` 能偵測到。
2. B2：跨 batch 重跑與第 7 次呼叫都被拒。
3. B3：Gemini 的 Argon 保留量不足時被拒；bot 呼叫端的行為不變。
4. B4：孤兒恢復流程（未過期／仍有程序時不動作；可恢復時補 budget_outcome 與 advisory_finished，仍計次）。
5. B5：三條線並行的整合測試，用 fake adapter 檢查 cwd、env、signal 隔離；或改用子程序後的對應測試。
6. B6：`admission_decision_id` 的追溯一致。
7. B7：v7 預約的 tier‑2 仍會被評估。
8. 鎖衝突：scheduler 或任一 line lock 被占用時，batch 不會啟動，也不會寫任何 shared 帳本。

---

## 六、狀態
- **部署前必須完成：** B1–B6 修補並補上測試；B7、B8 及第二節的事項要提供 source 或證據後再判定；另外需要獨立的 Gemini 審查。
- 在此之前，不應部署任何 source，也不應發出那 6 次模型呼叫。