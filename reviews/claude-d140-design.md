D140 設計審查 v3：窄化快速路徑（Forecast Lab 設計／程式審查員）

一、範圍與證據層級
- 本審查只根據提供的 source packet 做設計層面的技術判斷。我沒有讀到任何原始碼或 raw event，也沒有執行任何測試、指令或模型呼叫，更沒有看過任何 forecast 內容。
- 「已知技術證據」都來自主機回報的敘述，在本審查裡只算待驗證的主張。實作必須從綁定雜湊的 raw events 自行推導，不能直接採信這些敘述。
- 下文分成四類：【阻擋項】實作或啟用前必須解決；【建議】非阻擋的強化；【主張】packet 自述、尚未獨立驗證；【已知觀察】packet 中的靜態原始碼觀察。
- 本文件不構成實作批准、測試通過或啟用核准。啟用前仍須完成實際程式碼的 Claude／Gemini 審查與離線測試。

二、對 proof-reuse 快速路徑的立場：有條件接受（設計方向），目前不是批准

1. 為什麼合理
- acceptance 證明的對象是隔離性質，綁定的是（CLI 二進位 SHA、runtime/adapter/judge/source 雜湊、canonical 工具限制、catalog、run_config）這一組不變量。
- 一次「從未執行」的工具名拼錯不會改變上述任何一項。按主張，runtime 回傳 unknown tool，狀態從 ACTIVE 轉成 ERROR，而且沒有 output。這反而表示 fail-closed 機制如預期運作。
- 只要這組不變量經雜湊逐項確認未變，重新跑 6 次 acceptance（4320 秒以上、消耗共享配額）得不到關於這個 typo 的新資訊。要求每次都重跑，屬於沒有資訊增益的成本。
- 所以我不要求「重跑未變更的測試」。

2. 為什麼目前仍是有條件：缺少的確切證據如下，不是泛泛要求重測
- (a) 目標合法性（最關鍵）：必須證明「假設把工具名換成 canonical `view_file`，同一組參數會通過現行的 canonical 允許規則」。具體要確認：
  - conversation ID 等於 runtime 自己產生的 init 事件裡的 own conversation ID，不能取自模型生成的文字；
  - 路徑正規化後確實位於 own conversation 的 `.system_generated/steps/<n>/content.md`；
  - n 小於 46、該 step 確實存在，而且是同一 attempt 內成功的 `read_url_content` step，content.md 是它的 page file；
  - 路徑沒有 `..`、symlink、絕對路徑跳脫、URL 編碼或其他 Unicode 變形。
- 如果目標是非 read_url_content 的 step 內容（例如其他工具輸出或系統產物），就屬於「企圖越權讀取、因拼錯而碰巧失敗」，不是無害 typo，必須維持 HOLD。packet 只說「目標是 own conversation steps/<n>/content.md」，沒有證明 step n 的類型。這是唯一一項我判定為實質缺失的證據。
- (b) 錯誤訊息的來源：錯誤字串必須出自 runtime 產生的事件欄位（例如 step 的 error 欄位），不能出自模型文字或 tool arguments 的回顯。classifier 要用 schema 位置判定，不能用全文搜尋。
- (c) proof 綁定方式：要確認兩個 production tier 的既有 acceptance proof 是以雜湊綁定上述不變量，而不是只綁時間戳或版本字串。也要確認 proof 涵蓋目前生效的 canonical `view_file` 限制規則（雜湊相同）。如果 proof 早於「canonical view_file 僅限 read_url_content page」這條規則，proof reuse 就不成立，必須 HOLD。
- (d) 單一成因：把 step46 從違規集合移除後，重新跑完整審計，hard violation 集合必須為空，也就是「stop 的唯一原因」要由程式重算，不能沿用敘述。

3. 結論
- (a) 到 (d) 全部由程式從 raw events 與 ledger 推導並通過時，快速路徑可以辯護，不構成安全政策缺口。
- 任一項無法推導時就是 HOLD，不得降級成「大概沒事」。
- 只要 runtime、baseline、權限有任何變更，或出現未知 schema，一律 HOLD，等待人工審查後的 re-acceptance。不做自動修補、升級，也不接受 plugin。auto_reaccept.py 維持 observe-only。

三、權威事件 classifier（D140 Exact-Typo Classifier）

1. 輸入與完整性
- stop 記錄裡必須記載 raw event 檔的 sha256、attempt_id、question_id、player、tier、stop_id。classifier 要重算雜湊，不一致就 HOLD。
- 只接受已知的 AGY 1.4.12 事件 schema 版本。出現以下任一情況都 HOLD：未知 event type、未知欄位（至少在 tool-step 與 init 事件上）、JSON 重複鍵（parser 必須以拒絕重複鍵的模式解析）、超大事件、編碼錯誤、非 UTF-8。
- step 序號必須單調、唯一。同一 step 出現矛盾事件（例如一筆 ERROR、一筆 COMPLETED）要 HOLD。完全相同的重複事件也建議 HOLD，除非 schema 明文規定事件是冪等重送。
- 必須有終止事件或 attempt 完結記錄，否則視為截斷，HOLD。
- 二進位 SHA、model/effort、run_config 雜湊必須取自 runtime／adapter 寫入的 metadata，並與已接受的 baseline 和當前設定逐一相等。

2. 精確條件（全部以位元組比對，不做任何正規化）
- 恰好一個違規 step（step46）。tool name 的位元組必須等於 `0x20` 加上 `view_file`，總長 10 bytes。
- 狀態序列只能是 ACTIVE → ERROR，沒有 RUNNING、COMPLETED 或任何 output 事件。output 欄位必須「不存在」，`null`、`""`、空陣列一律 HOLD，除非 schema 定義它們與不存在等價，且有 fixture 佐證。
- error 欄位與 `unknown tool: " view_file" — check spelling` 位元組完全相同，包括 U+2014 em dash、ASCII 雙引號，結尾不得多出字元。
- 參數只有已知的 path 參數（其他已知可選參數須明列白名單），並通過第二節 (a) 的目標合法性檢查。
- 其餘每個 tool step 都在 canonical allowlist 內，並各自通過現行審計。audit_flags 和 ownflags 為空，模型驗證通過。
- 錯誤之後的 steps 沒有其他 unknown tool、拼寫變體或任何新的違規嘗試。
- 重算後的 hard-stop 集合恰好等於 {step46 typo}，而且 stop 記錄的 reason 只有 tool_scope_violation，沒有並列的其他 reason。

3. 必須拒絕的攻擊與變形（每一項都要 HOLD）
- 空白變形：前導 TAB、NBSP（U+00A0）、U+3000、ZWSP/ZWJ/BOM、兩個空格、尾隨空格、前後都有空格。
- 名稱變形：大小寫（`View_file`）、Cyrillic 同形字（例如 `е`）、全形字元、`view-file`、`viewfile`。
- 錯誤字串變形：連字號或 en dash 取代 em dash、智慧引號、多一個句點。
- 狀態變形：有 output（含空字串）、ACTIVE→COMPLETED、ERROR 後又 COMPLETED、缺少 ERROR 終態。
- 事件層級：重複且矛盾的 step46 事件；stop 之後 raw event 被替換（雜湊不符）；stop 記錄指向另一個 attempt；log 截斷藏住後續事件；錯誤字串只出現在模型文字裡。
- 路徑：conversation ID 不同或缺失、`../` 跳脫、symlink、絕對路徑、percent-encoding；step n 不存在、n ≥ 46、n 不是 read_url_content，或該 step 本身失敗。
- 其他：出現兩個以上 typo step（即使相同也 HOLD，保守處理）；同時存在其他 audit flag、ownflag 或模型不符；CLI SHA、catalog、run_config 或任一 tier 的 proof 有變或缺失。

4. 凍結
- classifier 的允許清單本身要硬編碼並綁定（runtime 版本 1.4.12、CLI SHA）。runtime 一升級，這條快速路徑自動失效，不能沿用到新版本。

四、Resume、Grant、崩潰與競態順序；危害視野（hazard horizon）

1. 【阻擋項】resume 必須限定在 episode 範圍
- 【已知觀察】F.line_stopped 以最新的 line_resume 時間戳壓制更早的安全記錄。這會讓一筆為 typo 寫入的 resume 順帶洗掉其他未審查的危害。
- 要求：新的 auto_recovery resume 記錄必須明確寫出 `covers_stop_ids=[該 stop_id]`。line_stopped 改為：只要存在任何未被「明確點名」覆蓋的 hard stop，該 line 就是 stopped。
- 歷史上沒有 covers 欄位的人工 resume，保留舊語意（覆蓋其 seq 之前的 stop），但只適用於遷移切點 seq 之前的記錄。這需要回歸測試，確保歷史判定不變。
- auto resume 的前置條件：該 line 未覆蓋的 hard stop 集合恰好等於 {D140 stop}。遇到任何已確認的 leak、safety_stop、未覆蓋的工具違規、legacy mismatch 或其他實際 hard stop，都拒絕 resume。

2. suspected_leak 的區分
- 55 筆 suspected_leak 關鍵字旗標從來不是 hard stop，應保留為歷史旗標，不阻擋這次 resume，也不得被 resume 記錄引用、覆蓋或刪除。
- 不能把「關鍵字命中」等同於「已確認洩漏」。反過來，只要任何一筆被正式升級為 confirmed leak，就是 hard stop，必須阻擋。
- 修改 line_stopped 時要測試：這些歷史旗標不會因語意改動而突然變成 stop，也不會被新的 resume 標成已處理。
- 原本無效的 forecast 和 audit 永遠保持無效，歷史上可見。

3. 【阻擋項】ledger 的共同序列化／CAS 紀律
- 所有寫入者都要走同一個 ledger 寫入鎖（短持有）並做 expected_seq 比對。寫入者包括：forecast.py main(resume)（【已知觀察】目前先讀 stop 再 append，兩者之間沒有共享交易）、auto_recovery、scheduler 的 admission，以及 stop 寫入。
- 每筆記錄是單行、fsync 後才算提交、帶 checksum。讀取時忽略 torn 的最後一行，視為未提交。

4. Recovery 狀態機與順序
- R0 偵測（只讀、不持 line lock）：找出候選 stop，檢查 rolling 24h 額度、該 stop 是否從未啟動過自動 recovery，以及時間餘裕。不符合就立即結束，不啟動 worker。
- R1 以非阻塞方式取得 auto_recovery.lock，再非阻塞取得 forecast.<player>.lock（本例為 forecast.gemini.lock）。取不到就記為 deferred，或不記錄；這不消耗額度，也不算「已啟動」。
- R2 在 ledger 交易中 append `recovery_started{recovery_id, stop_id, observed_seq}`。從這一刻起，這個 stop 就被標記為「已嘗試自動 recovery」，永遠不會再自動重試，崩潰後亦然，只能由人工處理。這筆記錄計入 3 次／24h 額度。
- R3 在 line lock 內做嚴格 raw-event 診斷，以及兩個 tier 的 zero-waiver 本地 preflight。preflight 不得暫時清除 stop 來測試，也就是 preflight 與 line-stop gate 必須是可分離呼叫的純函式。產出 evidence_digest。硬性逾時建議 60 至 90 秒。
- R4 在 ledger 交易中重讀尾端並驗證：從 observed_seq 起沒有新的安全事件、人工 resume、設定或 baseline 變更，也沒有同一 stop 的其他處理；該 stop 仍未被覆蓋，而且是唯一未覆蓋的 hard stop；所有雜湊不變。任何一項不符就 append `recovery_cancelled` 並結束。
- 驗證通過後，用「單一記錄」原子寫入 `line_resume{actor:"auto_recovery:D140", covers_stop_ids, recovery_id, evidence_digest, bindings}`，必要時在同一筆記錄內嵌 `recovery_retry_grant`。單一記錄可以避免出現「有 grant 沒 resume」的半狀態。
- 如果失敗的 attempt 本身就是 rerun、時間不足、已有提交、屬於 revision 或 tier 不符，就只寫 resume（其他問題可繼續），不發 grant。
- R5 釋放鎖。整個流程不持有 cycle、scheduler、submit 或其他玩家的鎖。

5. Grant 生命週期與重啟語意（回答「重啟後 grant 是否存活」）
- 狀態：ISSUED → CLAIMED → 由一般 attempt 記錄終結（完成或失敗）；也可能直接進入 EXPIRED 或 REVOKED。
- ISSUED 的 grant 可以撐過正常重啟，因為它已持久化在 ledger。但每次 claim 都要在交易中重新驗證所有綁定：
  - 原 stop、失敗 attempt 與 question/player/tier 完全一致；
  - config、model、effort、snapshot、baseline 雜湊都沒變；
  - 沒有新的 stop，line 未被重新停止；
  - 尚未提交，也沒有 revision；
  - 該 question/line/tier 還沒有任何 attempt_kind=rerun；
  - 現在時間仍早於 grant 到期點。到期點 = min(decision 時間 − 該 tier p95 執行時間 − 聚合安全邊際, 原 campaign deadline)；
  - 預算可以預留。
- 任一項不符，就 append REVOKED/EXPIRED，不重試。
- CLAIMED：在同一個 ledger 交易內 append `attempt_admitted{attempt_id, attempt_kind:"rerun", grant_id, reservation_id}`，並同時寫入預算 reservation，之後才啟動。grant_id 具唯一性，同一個 grant_id 第二次 claim 會被 CAS 拒絕。
- 崩潰語意：
  - CLAIMED 之後若沒有終結記錄，視為 unknown attempt，rerun 槽已消耗，永不重新發 grant，該 question 在這條 line 上視為 attempts_exhausted。這是避免重複的代價，屬可接受的保守做法。
  - R2 到 R4 之間崩潰：stop 維持原狀，而且已標記為「已嘗試」，轉人工處理。
  - 不允許因重啟而推論完成，也不允許重建成功狀態。
- 由此可避免兩種失誤：「遺失重試」只會發生在崩潰位於 CLAIMED 之前，而那時 grant 仍是 ISSUED 且可重驗、可 claim；「重複重試」則被 claim 的唯一性與「rerun 槽已占用」雙重阻擋。

6. 【阻擋項】rerun 規則
- 【已知觀察】forecast_question() 在呼叫 _may_rerun 之前，只要已有 attempt_kind=rerun 就回傳 attempts_exhausted。這個前置檢查必須保持在 _may_rerun 之前，順序與行為都不變。
- _may_rerun 只新增一個窄例外：存在一個 ISSUED 且綁定「這個 initial 失敗 attempt」的有效 grant。
- 沒有 grant 時，_may_rerun 對任何既有歷史記錄的輸出要與修改前逐筆相同（用語料庫做差分測試）。
- grant 不增加 attempt 預算，只使用既有的 initial → rerun 槽。replacement 失敗後不得再取得一般 rerun。所有既有 audit flags 都保留，不可被洗掉。
- 新 attempt 的結果必須通過完整的一般審計。

7. 【阻擋項】預算 admission
- 【已知觀察】autoload.weekly_usage 只計已發生的呼叫，不能單獨作為 admission 依據。
- admission 必須同時計入「已提交用量 + 所有 pending reservations + 外部用量保守邊際」，不得超過上限，而且要在同一個 ledger 交易裡判定。快速路徑本身不做 acceptance 呼叫，但新 attempt 的 forecast 呼叫要照常預留。不擴大配額，也不新增付費呼叫。

8. 【阻擋項】held-lock 契約
- 【已知觀察】acceptance.run_line() 會自己取 forecast.<player>.lock。在 macOS 上，flock 綁定 open file description，同一行程以另一個 fd 再 flock 同一把鎖，會阻塞或回傳 EWOULDBLOCK。
- 凡是需要在已持鎖狀態下呼叫、內部又會取鎖的函式（例如共用的 preflight），都要接受一個明確的 `held_lock` token，並驗證它確實是目前持有的同一把鎖（fd、inode 一致）。不能二次 flock，也不能直接略過保護。
- 快速路徑不呼叫 run_line。所有同玩家的 production、advisory、practice、acceptance 入口都必須服從同一把 line lock。

9. 額度
- 每條 line 在 rolling 24h 內最多 3 次 exact-typo recovery，以 recovery_started 計數，且全部不做新的 acceptance 呼叫。超過就 HOLD 並等待人工處理。
- 同一個 stop 永不自動重試。因忙碌或時間不足而 deferred，不消耗額度。
- 【建議】加一個跨 line 的全域上限，例如每 24h 5 次，並在同一 line 頻繁觸發時發出人工警示，因為那可能代表模型行為漂移。

五、排程器的部分 line 進度（與 recovery 互相獨立）
- 原則：recovery 絕不是提交的前置條件。line 停止時，deadline 聚合與提交照常以其餘 line 進行。packet 說既有的 deadline fallback 已處理部分 line，應以測試驗證，不要改寫。
- 停在 line_stopped 的 discovery job 不得快取等待狀態。每個 tick 都要重新評估目前的 line guard，resume 後才能重新取得資格；未 resume 時則不得被誤放行。
- recovery 只在該 line 閒置時以非阻塞方式持有 line lock，時間在數十秒等級，而且有硬性逾時。它不持有 cycle、scheduler、submit 或其他玩家的鎖，所以不會阻擋 qid46572（decision 11:30Z、close 12:00Z）其他 line 的完成與提交。
- 對 qid46572 本身：只有在 grant 到期點仍有餘裕時才發 grant；否則只做 line resume，該題這條 line 不補答，由其他 line 聚合。
- launchd 服務每 300 秒執行一次，沒有候選時立即結束、不啟動 worker，並以 auto_recovery.lock 保證單一實例。
- 【建議】在部署時間安排上避開 qid46572 的 decision 時段。新程式不應為了趕這題而跳過審查或測試。

六、精簡而有意義的測試（設計建議；我沒有執行任何測試）

1. Classifier
- 一個合成的 golden 正例。結構仿照事故，但不含任何 forecast 內容，並包含 step n 為 read_url_content 的 page file。
- 第三節第 3 點的每一種變形各一個反例，全部要 HOLD。
- 一個「canonical view_file 指向非 read_url_content step」的反例，必須 HOLD。
- schema 版本未知、JSON 重複鍵、截斷、雜湊不符等反例。

2. Hazard
- 較早有未覆蓋的 safety_stop，之後才有 typo stop：不可 resume。
- R2 之後插入新的安全事件、人工 resume 或 config 變更：R4 的 CAS 取消。
- resume 只覆蓋點名的 stop。
- 55 筆 suspected_leak 歷史旗標不阻擋、不被覆蓋、不被刪除；把其中一筆改為 confirmed leak 後必須阻擋。
- 遷移切點前的 legacy resume 判定不變（回歸測試）。

3. Grant 與崩潰（故障注入）
- 分別在 R2 後、R3 中、R4 提交前、R4 提交後 claim 前、CLAIMED 後啟動前、attempt 執行中注入崩潰，驗證各自的終態與「不遺失、不重複」。
- 兩個 recovery 實例並行：只產生一筆 resume/grant。
- 兩個 claimer 並行：只成功一個 claim。
- 重啟後 ISSUED grant 仍有效：可以 claim 一次；已過期：拒絕。
- 失敗 attempt 本身是 rerun：發 resume、不發 grant。
- attempts_exhausted 前置檢查的順序保留。
- replacement 失敗後不能再 rerun。
- _may_rerun 對歷史語料的無 grant 差分測試。
- 已提交、revision、tier 或 line 錯誤、時間不足、預算不足（含 pending reservation）都拒絕。

4. 額度
- 第 4 次 recovery 進入 HOLD。
- deferred 不計數。
- 已啟動但失敗的 stop 永不再自動重試。

5. 鎖
- held-lock token 正確時可用；token 錯誤時拒絕。
- 確認沒有二次 flock（模擬 macOS 語意）。
- recovery 持有 line lock 時，同玩家的 practice/acceptance 入口被擋下，其他玩家與 submit 不受影響。

6. 排程
- line 持續停止時，deadline 聚合仍以部分 line 提交。
- resume 之後，舊的 discovery job 重新評估並恢復資格。
- recovery 執行期間，排程器的 tick 與提交延遲不增加（以假時鐘測試）。

7. 不變式
- 無效的 forecast/audit 永遠無效，而且在報表中可見。
- auto_reaccept.py 保持 observe-only，沒有寫入。

七、總結
- 阻擋項：
  - B1 resume 限定 episode 範圍，並修正 line_stopped；
  - B2 目標必須是 canonical 允許的 read_url_content page；
  - B3 所有寫入者共用 ledger 交易／CAS；
  - B4 grant claim 與 attempt admission、預算 reservation（含 pending）同一交易完成；
  - B5 held-lock 契約；
  - B6 兩個 tier 的 proof 與所有不變量逐一雜湊比對，且確認 proof 涵蓋現行 canonical 限制；
  - B7 _may_rerun 只加窄例外，前置檢查順序不變。
- 建議：全域上限與漂移警示；部署避開 decision 窗口；功能旗標預設關閉。
- 本審查只有在 B1 到 B7 實作完成、經實際 Claude/Gemini 程式碼審查並通過離線測試之後，才可能進入啟用核准。目前沒有任何實作或測試獲得批准。

END-D140-DESIGN-V3