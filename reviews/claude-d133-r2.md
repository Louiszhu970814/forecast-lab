> 官方 CLI 審查原文；來源封包 `data/operations/d133-closed-practice-20261104/review-packet-r2.txt`。程式雜湊以機器產生的 `reviewed-files-r2.json` 為準，非模型轉寫字串。

# D133 第二輪完整程式審查（具名審查者：Claude）

## 結論：REQUEST_CHANGES

整體來看，第一輪提出的主要問題大多已修正，設計方向也正確。不過依提供的程式碼，還有 2 個程式／規格阻擋項，以及 1 個部署程序必修項。三項都不大，可以在一個小 diff 內修完。修完之後，第三輪可以只審 diff 和對應的新測試。

- 本審查不核准部署，也不能當成 Gemini 的審查、Louis 的核准或「雙審通過」。
- deployment-gate.json 不能根據這份審查寫入 claude: PASS。
- Root runtime 應維持不動。

---

## 0. 證據等級

| 類別 | 內容 | 我能確認的程度 |
|---|---|---|
| 原始碼 | practice.py、practice_contract.py、external_usage.py、budget.py、forecast.py、test_practice.py 全文，以及相依模組 | 已逐段閱讀。沒有執行任何東西 |
| SHA256 | 六個檔案的雜湊 | 只能記錄。我無法驗證資料包內容就是這些雜湊對應的檔案 |
| 測試比對 | 基線 3134 passed／100 failed，候選版 3205 passed／100 failed，失敗節點集合相同，新增 71 個測試 | 提交者的說法，我沒有重跑。71 個新測試的存在與內容和資料包裡的測試檔大致相符 |
| API 盤點 | 108 題全部遮蔽，3 次 detail GET 也遮蔽，新練習呼叫 0 次 | 提交者的說法，資料包沒有原始回應 |
| 部署證據 | 無 | 依規定應在 PASS 之後才產生。現在不存在，我也不推定它存在 |
| 正式 cycle 讓路 | cycle.py 片段顯示鎖忙時回傳 overlap_skipped | 屬於部分程式證據。通知和漏題影響沒有完整證據 |
| 準確度 | 無 | 沒有任何練習結果 |

---

## 1. 第一輪問題修正核對（只依程式閱讀）

| R1 項目 | 現況 | 判定 |
|---|---|---|
| Claude B1：驗證證據 | 完整比對結果已附上（同 100 個失敗節點）；Gemini 第二輪和部署證據仍待補 | 程序上持續中，不是程式缺陷 |
| Claude B2：共用執行期回歸 | 已有「無／空／損毀」三種帳冊的回歸測試；版本升級寫入 PLAN v1.88；TOKEN_POLICY_VERSIONS 包含 v7、v8、v9 | 已修正。但「下游沒有寫死 v9／v8」只看到部分片段，無法完整確認 |
| Claude B3：未知練習靜默阻擋正式線 | F.preflight 在探測 CLI 之前就回報 practice_attempt_unknown／practice_accounting_invalid；recover 不重跑也不免費結案 | 已修正（採我上輪建議的 b 方案） |
| Claude B4：持鎖時鐘 | 從實際取得六鎖起算 720 秒；最後 60 秒不准入；preflight 後強制刷新；lock_events 記錄實際持鎖秒數；execution_cutoff 驗證 cutoff ≤ start+12m | 已修正。CLI 沒有硬即時取消屬於已記錄的實作邊界，不算程式缺陷 |
| Claude B5：正式生命週期證據 | 帳冊範圍已擴大，並比對 post_id；新增 remote_unforecasted | 大致修正，但 my_forecasts is None 被當成「沒有預測」（見 B2） |
| Claude B6：預約後准入失敗 | finish_accounting 在「有預約、沒准入、沒啟動」時會釋放；「沒准入卻有啟動」時丟錯 | 已修正，也有測試 |
| D1：單次暴露停線 | leak_confirmed 改走 guard，沿用既有「7 天內 2 次」規則；有測試 | 已修正。recovery_no_rerun 仍是一次就停（見建議 N1） |
| D2：空批次佔用唯一批次 | 零選題只寫 practice_inventory，不登記 singleton | 已修正 |
| D3：保留驗證題 | 至少兩個截止時間群才保留最新群；只有一群時標成診斷、需另登記未來的時間群；時間用 parse_iso 排序 | 已修正 |
| Gemini B1：score | 不取正式鎖、不呼叫 fresh_index；使用已凍結的 aggregate record_id 和個別 adoptable 輸出；檢查身分、題型、選項、量尺、截止時間；允許題文修訂並標記；逐題記錄錯誤 | 已修正 |
| Gemini B2：report | 分列 adoptable、valid、failed、missing、未知成本、稽核、耗時、按組別與題型分組；共享摘要不含數值 | 已修正 |
| Gemini B3：holdout 與 prepare 的 shared_ready | 都已修正 | 已修正 |
| Gemini B4：preflight 後未刷新 | 已強制刷新；准入後 20 秒快取邊界的情況已記錄為「可能用掉名額，但確認沒啟動就不計費」 | 已修正，或已記錄為可接受的保守行為 |
| Gemini B5：LineStop 與 CLI 參數 | 不會重複寫 line_stop；保留 reason 和 quota_retry_at_utc；run 必須帶 --question | 已修正 |

---

## 2. 剩餘阻擋項（必修）

### B1. 同題其他線被拒的預算紀錄，仍會被誤判為「正式生命週期」

位置：practice._lifecycle_evidence（由 run() 的 guard() 呼叫），以及 run_line.before() → budget.Budget.reserve。

問題：
- _lifecycle_evidence 只豁免 attempt_id 已出現在本批 practice_admitted（own_ids）的 budget_events 紀錄。
- 但 Budget.reserve 被拒時也會寫入帶有 question_id 的紀錄：{"type": "budget_denied", **decision, ...}。拒絕原因可能是額度保留、Argon 保留、5 小時 gate、快照不可信等。
- 這筆被拒的 attempt_id 永遠不會進入 practice_usage。

後果：
- 同題的其他線之後執行 recheck() 或 before() → guard() → lifecycle_evidence 時，會丟出 ValueError("question has formal lifecycle; use post_close_audit")。
- 如果該線已准入但還沒啟動，會用掉一次練習名額（確認沒啟動，所以不計 CLI 成本），而且失敗原因被誤標成「正式生命週期」。
- 「已預約但 register_admission 失敗」的那筆 budget_reserved 也屬於同一類問題。這種情況機率較低，因為 before() 已先在同一個交易內檢查過上限和重複。
- 這正好和提交說明中「sibling practice budget records cannot be mistaken for formal history」的修正相矛盾：目前只涵蓋准入成功的情況。

觸發條件：任一線在其他線最後一次 guard 之前被預算拒絕。例如 Claude 或 GPT 因保留額度被拒，而 Gemini 還在做 quota 查詢。機率不高，但屬於確定性的邏輯缺陷，修法也很小。

修法：
- 在 run() 的 closure 內維護本次執行的 practice_budget_run_ids。每個 run_line 建立 Budget(new_id("practice")) 後，先把它的 run_id 登記進去，之後才做任何 guard。
- _lifecycle_evidence 豁免 name == "budget_events.jsonl" 且 r.get("run_id") in practice_budget_run_ids 的紀錄（同時涵蓋 reserved 和 denied）。
- prepare() 維持完全嚴格，不傳入豁免集合。
- 不建議用 run_id 前綴做豁免，因為會連帶放過過去批次留下的紀錄。

必補測試：三線完整 P.run，讓其中一條線在 reserve 被拒（例如把該線的 cap 設成 used+protected）。斷言其他兩線仍正常准入或啟動，沒有任何結果出現 "formal lifecycle" 原因，且被拒線沒有留下 practice 准入紀錄。

### B2. my_forecasts is None 被當成「明確沒有預測」，和宣稱的契約不符

位置：practice.remote_unforecasted：if value is None: return。測試 fixture 的 my_forecasts: None 也被當成可選題。

問題：
- 提交說明和 D133 補記都寫「官方 my_forecasts 明確無歷史」。
- null 可能代表「沒有預測」，也可能代表欄位不可用、權限不足或序列化差異，並不是明確證據。
- 依官方 API 說明，一般帳號讀得到的閉題題文通常就是「自己預測過的」。因此只要候選題可讀，my_forecasts 的判讀就是排除舊預測的關鍵閘門。

修法（擇一）：
- (a) 只接受 dict，且 history == []、latest is None、score_data in (None, {})；None 一律視為不確定並排除。fixture 改成明確的空結構，另補 None 會被排除的測試。
- (b) 若要保留 None，需提供已登入 bot token 下「未預測題回傳 null」的實際 API 證據（只存 metadata），並在 manifest 記錄這個判讀規則。目前資料包裡沒有這項證據。

影響範圍：下一步的 zero-call 盤點，因為 108 題全被遮蔽，不會走到這段；但這段程式一旦 PASS，就會同時授權日後的非空批次。所以仍列為程式阻擋項。

### D-B1（部署程序必修）. 雜湊檢查和實際寫入之間有空窗，套用後也沒有驗證雜湊

位置：apply_reviewed.py。
- 取鎖前用 digest(STAGE/f) 驗證 stage 檔案。
- 取鎖後的 atomic(PROJECT/f, (STAGE/f).read_bytes()) 重新讀取 stage，寫入後也沒有比對 digest(PROJECT/f) == h。
- operations.jsonl 沒有記錄實際部署的檔案雜湊。

修法：
- 在鎖內一次把 stage bytes 讀進記憶體，驗證雜湊等於 gate 的值。
- 寫入的就是這份 bytes；寫入後對 root 重新計算雜湊並比對。
- 把 deployed_sha256 寫進 apply_complete。
- gate['files'] 必須和本輪審查的六個 SHA 完全一致（包括檔案集合本身）。
- deployment-gate.json 的 PASS 欄位只能引用實際審查輸出的路徑和雜湊，不能手寫。

---

## 3. 部署程序其他意見（非阻擋，建議同時改）

1. M.require_idle(..., horizon_s=300) 只預看 5 分鐘。但鎖內要跑 preflight 兩次（各三線 × tier1／tier2，Gemini 的 /usage 最多 30 秒 × 多次）加上最長 90 秒的 smoke 測試，總時間可能超過 5 分鐘。建議改用和練習相同的 20 分鐘預看，或者先記錄總持鎖時間的上限，再依此設定。
2. smoke 只跑 tests/test_practice.py。這次改動共用的 budget.py 和 forecast.py，建議至少加跑 test_budget.py、test_advisory.py 和 forecast 相關測試，逾時時間跟著調整。完整比對已在隔離副本做過，所以這是補強，不是阻擋。
3. preflight 子程序在父程序持有 forecast.<player>.lock 時執行，recover_acceptance 會因 PreflightError 被靜默跳過。這方向是安全的，但應在紀錄中註明：部署前後的 preflight 都不包含 acceptance 的孤兒恢復。
4. atomic() 一律用 0o644，不保留原檔權限。目前目標都是 .py，所以影響不大。
5. 建議先在一份 root 複本上用相同腳本試跑一次，包含刻意觸發回滾。

---

## 4. 非阻擋建議（不影響本輪判定）

- N1：recovery_no_rerun 一次就停正式線（propagate_safety）。
  - 正式路徑的 line_stopped 不會因 recovery_no_rerun 停線；練習和 advisory 會。
  - 閉題常帶 suspected_leak，被中斷的練習嘗試因此很容易觸發 recovery_no_rerun，進而停掉正式線。
  - 這比正式規則嚴格，但和 advisory 的前例一致。建議在 PLAN 或交接文件明確寫出，由 Louis 知悉。
- N2：practice_usage 寫死 version == "closed_practice_v1"。未來的 v2 帳冊會讓正式的 reserve 和 line_stopped 判定失敗（fail closed）。建議改成允許的版本集合。
- N3：prepare() 在六把鎖內的網路時間沒有上限。fresh_index、列表分頁和每個可讀候選題的 detail GET 都受 Backoff pacing 影響。目前 108 題全遮蔽，不會觸發 detail GET；但未來權限改善後可能變長。建議在登記前檢查已用時間，超過就放棄。
- N4：prepare() 遇到 MiniBench 有開放題會整個中止（formal_work_priority）。第一次盤點可能需要擇時重試，建議寫進操作說明。
- N5：score() 可能和執行中的 run 同時跑。它只取自己的 score.lock，可能讀到 run 中途的狀態。建議某題在 runs.jsonl 有 started、沒有 finished 時，標成 run_in_progress，不計分。
- N6：score() 和 prepare() 只捕捉 ValueError 和 MetaculusError。若出現 KeyError、TypeError，或尚未確認是否為 ValueError 子類的 N.UnsupportedScaling，整批會中止。方向是 fail-closed，但建議逐題捕捉並記錄固定的錯誤代碼。
- N7：group 或 conditional post 被標成 not current-season FutureEval，排除理由失真，建議改成 not_single_question。
- N8：同步題排除範圍只有 33121 和 MiniBench。建議評估是否納入 Cup 33108，避免看到和人類賽開放題同步的 bot 輸出。
- N9：run_line 的 except F.LineStop 中 exc.notify 分支，在現行引擎下實際上不會被執行到（notify 類停線都在寫入紀錄之後才丟出）。可以保留作防禦，但測試應標註。
- N10：D133 交接補記仍寫「完整比對正在執行」，和這次附上的 3205／100 比對結果不一致，請更新並附證據路徑。
- N11：既有限制仍然存在，建議寫進報告：
  - 共用的 quota.jsonl 會讓估計偏保守；
  - Backoff 會寫入 data/api_cooldowns.jsonl；
  - prompt 的「今天日期」晚於截止日；
  - 題文可能在事後被編輯。

---

## 5. 仍缺的有意義測試

1. B1：一條線被預算拒絕時，其他線不受影響（見上）。
2. B2：my_forecasts 為 None 時被排除，或提供證據後的對應測試。
3. 完整三線 run 中，Gemini 走 budget_denied 或 gemini_admission 拒絕的路徑：其他線正常，共享帳冊中准入數和結束數一致。
4. recover() 處理「已准入、沒有 start」：會釋放成本（started=False），不呼叫模型，正式 reserve 之後恢復可用。
5. score() 在 run 進行中，或 aggregate 尚未寫入時的行為。
6. 部署腳本：stage 檔案在驗證後被改動時要拒絕；回滾後 root 雜湊恢復成原值（可在複本上測）。

---

## 6. 關於「即時性」的界線

- 從取得鎖起算 12 分鐘、最後 60 秒不准入、20 分鐘正式題預看、preflight 後強制刷新、CLI 沿用既有 timeout 和清理：在目前已授權的 D133／D132 有界手動範圍內，我判定這不是程式缺陷。
- 「突發新開題時要立即取消 CLI」是更強的即時性需求，需要改動受保護的 runner，不屬於本輪範圍。PLAN v1.88 已記錄這項限制。
- 「准入後的新鮮檢查失敗可能用掉一次名額，但確認沒啟動就不計 CLI 成本」是保守、已記錄的行為，不算阻擋。

---

## 7. 下一步

1. 修 B1、B2 和 D-B1，補第 5 節第 1、2、6 項測試，並重跑同一套完整比對，保留「相同失敗節點」的證據。
2. 第三輪只需審這個 diff 和新測試，另外仍需要 Gemini 的獨立審查。
3. 兩方都 PASS 後，才可依修正後的部署腳本，在六鎖下備份、做前後 preflight、驗證雜湊並套用。第一次實際執行應該只產生 zero-call inventory，不登記唯一批次。
4. 全程不啟用主賽 33121，不改 D118 窗口和上限，保留 D132 未提交變更和 D134。

狀態總結：程式是未套用的候選版本，本輪判定 REQUEST_CHANGES（2 個程式／規格阻擋項、1 個部署程序必修項）。實際練習 0 次呼叫、沒有部署證據、沒有任何準確度結果。