> 官方 CLI 審查原文；來源封包 `data/operations/d133-closed-practice-20261104/review-packet-r3.txt`。程式雜湊以機器產生的 `reviewed-files-r3.json` 為準，非模型轉寫字串。

# D133 r3 最終差異審查（具名審查者：Claude）

## 結論：REQUEST_CHANGES

r2 的 B1、B2、D-B1 三項，依我讀程式碼的結果都已修正。這次新加的「正式與練習暴露紀錄按時間排序重播」，邏輯本身是對的。

但這個修正連帶多出一個共用正式路徑的風險：
- B1：`F.line_stopped` 現在會解析帳冊中「每一筆」紀錄的 `finished_at_utc`，不再只看本線的 `forecast_attempt`。
- D-B2：部署腳本的 smoke 測試直接在正式 root 跑四個測試檔，這可能改動正式帳冊，而且沒有任何檢查會發現。

兩項都很小，但部署腳本只要讀到 PASS 就會自動套用，我沒辦法用「附條件 PASS」把這兩點擋下來，所以只能判 REQUEST_CHANGES。修完後，r4 只需要看這兩個小 diff、對應測試和證據。

- 這份審查不核准部署，也不能拿來寫入 deployment-gate.json 的 claude PASS。
- 它不代表 Gemini 的審查，也不代表 Louis 的核准。
- 正式 root 維持不動。

---

## 0. 證據等級

| 類別 | 內容 | 我能確認到的程度 |
|---|---|---|
| 原始碼 | 六個最終檔案全文，加上 r2→r3 的完整 diff，以及部署腳本與它的故障測試 | 已逐段讀過。我沒有執行任何測試或指令 |
| 標準 SHA | 資料包附的 JSON | 只做記錄，我無法驗證檔案位元組。另外，budget.py 和 practice_contract.py 的 r3 SHA 和 r2 Gemini 報告抄錄的值一樣，但 diff 顯示這兩個檔案刪掉了開頭空行。這很可能是資料包包裝造成的假差異，請主持人用機器 diff 確認。這點不影響判定，因為部署腳本會在鎖內驗證實際位元組 |
| 測試比對 | 基線 3134 passed／100 failed；候選 3211 passed／100 failed；100 個失敗摘要相同；新增 77 個練習測試；相關測試 203 passed | 都是提交者的說法，我沒有重跑。新增測試的內容和 diff 對得上：71 + 6（None 參數、時間序、兩個版本參數、denied sibling、score 在途）= 77 |
| 部署故障測試 | 4 個案例 passed | 提交者的說法。四個案例都 mock 了 subprocess.run 和 preflight，所以沒有涵蓋 smoke 的真實行為 |
| 營運狀態 | 0 次新練習呼叫；108 題全被遮蔽；10 題既有輸出已稽核 | 提交者的說法，資料包沒有原始回應 |
| 部署證據 | 無 | 依規定只能在雙方 PASS 之後產生。我不推定它已經存在 |
| 準確度 | 無 | 沒有任何練習結果 |

---

## 1. r2 阻擋項核對（只依讀程式碼）

### r2-B1：被預算拒絕的同題 sibling 被誤判為正式生命週期 → 已修正

- run_line 建立 Budget 後，會先在交易內把 b.run_id 加進 budget_run_ids，之後才做第一次 guard() 或 reserve()。順序正確。
- _lifecycle_evidence 只豁免 budget_events.jsonl 中 run_id 屬於本次執行的紀錄，reserved 和 denied 都涵蓋。run_id 由 new_id("practice") 產生，舊批次或正式紀錄不會碰撞。這是精確 ID 豁免，不是前綴豁免，符合我在 r2 提的要求。
- budget_outcome 紀錄沒有 question_id，本來就不會命中。tier2_token_assessed 和 upgrade_stop 只屬於 tier 2 或沒有題目 ID，也不受影響。
- prepare() 呼叫時沒有傳豁免集合，仍然嚴格。測試也斷言：同一筆 denied 紀錄在不帶豁免的呼叫下會被視為 formal lifecycle。
- test_budget_denied_sibling_is_not_formal_history 用 denied event 保證 sibling 的 guard 一定在拒絕紀錄寫入之後才執行。在 r2 程式下這個測試會失敗，所以它確實能抓到這個回歸。

### r2-B2：my_forecasts is None 被當成明確沒有預測 → 已修正（採 r2 建議的 a 方案）

- 已刪除 None 的早退分支，現在 None 會落進 not isinstance(value, dict) 而被拒絕。
- fixture 改成明確的空結構，參數化測試也加上 None。
- 影響：如果官方 API 對未預測題實際回傳 null，所有候選題都會被排除。這是可接受的 fail-closed 行為，D133 文件也已寫明。

### r2 D-B1：部署雜湊檢查與寫入之間的空窗 → 已修正

- 鎖內一次讀入 stage 位元組，比對 SHA，寫入的就是這份記憶體內的位元組。
- 寫入後重算 root 雜湊並比對；apply_complete 會記錄 deployed_sha256。
- gate['files'] 必須等於 reviewed-files-r3.json，而且檔案集合必須正好等於六個 TARGETS。
- 審查參照要同時符合三點：固定路徑（JOB/<model>-r3/answer.md）、與 gate 一致的 SHA、檔頭判定為 PASS。
- 故障測試涵蓋「事後竄改 stage」和「事後替換審查檔」。
- 殘留風險（不阻擋）：reviewed-files-r3.json 和 gate 都由主持人寫入，必須和本資料包的標準 SHA JSON 逐字一致。verdict() 的解析方式也比較脆弱，見第 4 節。

---

## 2. r3 新增變更逐項審查

| 變更 | 判定 | 說明 |
|---|---|---|
| line_stopped 按時間排序 | 邏輯正確，但擴大了解析範圍，見 B1 | 修正了「一個月前的練習暴露排在當期正式暴露之後，因而誤觸 2 次／7 天門檻」的問題。新測試在 r2 程式下會失敗，在 r3 下通過 |
| practice_finished／guard 嚴格比對版本 | 可接受 | 寫入端都帶 closed_practice_v1，正式 reserve()、preflight()、line_stopped() 遇到未知版本會 fail-closed。等於否決了 r2 N2「允許版本集合」的建議，改走更嚴格的方向，可以接受，但未來升版時必須同步修改共用模組（見 N-d） |
| prepare 時間預算 | 可接受 | 每處理一題前和整個迴圈後都會檢查是否已超過 660 秒；超過就丟出 QuestionDone，manifest 和批次都不登記，六鎖由 ExitStack 釋放。缺少對應測試（見 N-c） |
| score 遇到在途 run 不計分 | 可接受，有一個邊界 | 有 started、沒有 finished 時記為 run_in_progress，不讀結算資料。但 run 崩潰後，這題會永遠停在 run_in_progress（見 N-a） |
| report 的 by_player | 可接受，但不完整 | 有 outputs、valid、adoptable、missing、admitted、pending、token 覆蓋率，cost_unknown 也把 pending 算進去，未知成本不會被當成 0。不過 failure_types 只統計 run_line 丟出的例外類型，不含紀錄本身的 error_type（見 N-b） |
| budget_run_ids 跨執行緒共用 | 可接受 | 只有 add 和 in，在 GIL 下安全。登記時包交易不是必要的，但無害 |

---

## 3. 剩餘阻擋項

### B1（程式，共用正式路徑）：line_stopped 的排序會解析所有紀錄的時間，不再只限本線

位置：forecast.line_stopped，新增的那一行：
records = sorted(records, key=lambda r: parse_iso(r["finished_at_utc"]))

問題：
- r2 版本只在 type == "forecast_attempt" 且 player == player 之後才 parse_iso(r["finished_at_utc"])。mismatch 段也先按 player 過濾。
- r3 在任何過濾之前，就對 forecasts.jsonl 全部紀錄（所有線、所有型別）加上 advisory 和練習 guard 解析時間。

後果：
- 正式帳冊只要有一筆「其他型別或其他 player、缺少或無法解析 finished_at_utc」的紀錄，三條線的 line_stopped 都會丟出 KeyError 或 ValueError。
- 受影響的呼叫點包括：forecast.run、練習 line_ready／shared_ready、resume 指令，以及所有呼叫它的正式週期。連 resume 都會失敗。

為什麼現有檢查抓不到：
- 這是共用正式路徑，MiniBench 正在 live。
- 部署腳本前後的 preflight 都不會呼叫 line_stopped。
- smoke 測試用的是 fixture，不是正式帳冊。
- 3211／100 的比對是在隔離副本上跑的，那份資料不等於正式帳冊。
- 所以這類回歸部署時看不出來，要到第一次正式 cycle 才會出現。

發生機率：低。目前已知的寫入端（run_attempt、recover_interrupted）都會寫 finished_at_utc。不過 leak_count 特地寫了 r.get("finished_at_utc") and ... 防禦，而且 history() 等處都先過濾型別，代表這個代碼庫並不假設所有紀錄都完整。我沒有正式帳冊的證據。

修法：一行。建議只排序原本就會解析的本線 attempt 紀錄，讓解析範圍恢復成 r2 的樣子：
ordered = sorted((r for r in records if r.get("type") == "forecast_attempt" and r.get("player") == player), key=lambda r: parse_iso(r["finished_at_utc"]))
迴圈改成 for r in ordered。mismatch 段繼續用原本的 records；它本來就會自己按 player 過濾並排序。

必補測試：
- 共享 forecasts.jsonl 放一筆其他 player 或其他型別、沒有 finished_at_utc 的紀錄時，line_stopped(..., 'claude') 不會丟錯。
- 保留現有的時間序測試；另外補一個反向案例：正式和練習的暴露都在 7 天內時，仍然會停線。

替代方案（不建議單獨使用）：在部署腳本前後的 preflight 子程序中，對三條線以唯讀方式呼叫 F.line_stopped(LINE_STATUS_LOG, p, FORECASTS_LOG)，用正式帳冊證明不會丟錯，並把結果存成證據。不論選哪個方案，都建議把這個唯讀檢查加進部署腳本。

### D-B2（部署程序）：smoke 在正式 root 執行四個測試檔，可能改動正式帳冊，而且檢查不到

位置：apply_reviewed.py 的 smoke 步驟，執行 pytest tests/test_practice.py tests/test_budget.py tests/test_advisory.py tests/test_forecast.py，cwd=PROJECT，timeout=120。

先說明：擴大 smoke 範圍是我在 r2 部署意見第 2 點提出的。重新評估後，在正式 root 這樣做有風險，我修正自己的建議。

問題：
1. 測試以真實的 PROJECT_ROOT 為預設值。forecast.Context 的預設路徑包括 LINE_STATUS_LOG、FORECASTS_LOG、QUOTA_LOG、RAW_ROOT，forecast.run 預設寫 RUNS_LOG，指向的都是正式 data/。
   - 我讀過 test_practice.py，所有路徑都有指定或已 monkeypatch，看起來沒問題。
   - 但 test_budget.py、test_advisory.py、test_forecast.py 不在資料包中，我無法確認它們完全隔離。
   - 任何一個沒隔離的測試，都可能在六鎖下往正式帳冊追加紀錄，例如 line_stop、quota、api_cooldowns。
   - 腳本的回滾只還原六個原始碼檔案；protected 檢查只涵蓋 src/、config/、prompts/、launchd/、AGENTS.md，不包括 data/。所以帳冊被改不會被偵測，也不會回滾。
2. 100 個基線失敗中，只要有任何一個落在這四個檔案，smoke 就一定失敗，每次部署都會回滾。這是 fail-safe，但會讓部署永遠無法完成。提交者說「related 203 passed」，但沒有說明這是否就是這四個檔案、用這條指令、在 120 秒內的結果。
3. 四個故障測試把 subprocess.run mock 掉了，所以上面兩點完全沒有測到。

修法（擇一，並附證據）：
- (a) 正式 root 的 smoke 退回只跑 tests/test_practice.py（我讀過，看起來已隔離）。其餘三個檔案用隔離副本、相同指令和 timeout 的通過結果作為證據。
- (b) 保留四個檔案，但在 smoke 前後對 PROJECT/data/ 全部檔案（至少所有 *.jsonl 和鎖以外的檔案）計算雜湊；有任何變化就回滾原始碼、記錄被改動的檔名並中止。另外提供這條指令在隔離副本 0 失敗、耗時小於 timeout 的證據。
- 同時補一個故障測試：smoke 回傳非 0 時，原始碼完整回滾，且 operations.jsonl 記錄 apply_rolled_back。

---

## 4. 非阻擋建議（不影響本輪判定）

- N-a：score 只要看到 started、沒有 finished 就判為在途。run 一旦崩潰（例如 aggregate 階段出錯），這題永遠不會計分，又因為規則是不重跑，沒有出口。建議另外區分 run_incomplete，例如判斷時同時參考 practice_usage 的 pending，或檢查 lock_events 有沒有 released；或至少在文件寫明需要人工對帳。另外，score 讀 runs.jsonl 和 run 寫入 started 之間還有很窄的競態，但 score 可以重跑，report 也取最新一筆，影響很小。
- N-b：by_player.failure_types 只統計例外類型（LineStop、QuestionDone、ValueError）。timeout、cli_error、invalid_output 這類正常落盤的失敗不會出現。建議同時列出每條線的 error_type 和 validation_status 分布，避免報告看起來「沒有失敗類型」。
- N-c：prepare 逾時這條路徑沒有測試。測試把 utc_now 固定為 NOW，永遠不會觸發。建議補一個測試：時間推進到 660 秒後，不寫 manifest、不登記批次，而且有 lock_released。
- N-d：嚴格版本比對寫死在共用的 external_usage。日後練習升到 v2 時，必須先在同一個模組加入新版本，否則正式的 reserve、line_stopped、preflight 會 fail-closed。請寫進 PLAN。
- N-e：部署腳本的 verdict() 抓的是前 35 行中，第一行含「結論」等關鍵字的那一行。如果審查在判定行之前有引用句（例如「前一輪結論為 PASS」），就可能誤判。另外 \bPASS\b 前面如果緊接中文字（如「結論PASS」），會抓不到。建議限定只認行首的「## 結論：(PASS|REQUEST_CHANGES)」這種固定格式，並補一個 REQUEST_CHANGES 會阻擋部署的測試，再補一個部署後雜湊不符時回滾的測試。
- N-f：D133 文件的「驗證與部署紀錄」仍以第二輪的 3205 為主，請補上 r3 的 3211／100、77 個新測試，以及證據路徑（延續 r2 的 N10）。
- r2 的 N1（recovery_no_rerun 一次就停線，需要讓 Louis 知悉）、N6（score 和 prepare 只捕捉 ValueError）、N7（group 題的排除理由）、N8（Cup 33108 是否納入同步題排除）、N11（共用 quota、cooldown、日期、題文編輯等既有限制）仍然有效，都不阻擋。

---

## 5. 下一步

1. 修 B1：縮小排序範圍，加 2 個測試。修 D-B2：採 (a) 或 (b)，加 smoke 失敗的故障測試，並附隔離副本的 smoke 證據。
2. 重跑同一套完整比對，保留「100 個失敗摘要相同」的證據，並產生新的標準 SHA JSON。
3. r4 只需要審 forecast.py 這一行的 diff、新測試和部署腳本的 diff，另外仍需 Gemini 對最終 SHA 的獨立審查。
4. 雙方都對同一組 SHA 判定 PASS 後，才在六鎖下執行：備份、前後 preflight（建議加上唯讀的 line_stopped 檢查）、雜湊驗證、套用。第一次實際執行預期只有 inventory_only，選題 0 題。
5. 全程不啟用主賽 33121，不改 D118 窗口和上限，不做 POST，保留 D132 和 D134 的未提交變更。

狀態總結：
- 程式是尚未套用的 r3 候選版本，本輪判定 REQUEST_CHANGES：1 個程式阻擋項（B1）、1 個部署程序必修項（D-B2）。r2 的三個阻擋項已修正。
- 實際練習 0 次呼叫，沒有部署證據，也沒有任何準確度結果。