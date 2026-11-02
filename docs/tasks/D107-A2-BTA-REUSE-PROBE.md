# D107 A2：bot testing area 重送驗收（`v15_probe_v4`）

## D113 實機結果（UTC 2026-11-02 16:35）

D105 四題均已自動讀回完成、unknown 0 後，GPT 取得 cycle 互斥鎖執行既有已審查的 v15_probe_v4；source 與 ac5edf2 相同。三情境全部完成，結束已回 local；這不代表完整 M3 或 live 核准。

| 情境 | probe | 執行結果 |
|---|---|---|
| duplicate_forecast | v15_1793634954960_3d23c6ae | 15:55:42 開始、15:59:45.267 完成；預測 POST 2、留言 0，兩次 found；history_counts 1→2→3，accepted_with_history |
| comment_length | v15_1793636070757_0131103d | 16:14:17.612 開始、16:20:48.254 完成；預測 0、留言 POST 4，2000／10000／50000／150000 字元均全文長度與雜湊相符；沒有截斷，first_rejected_length=null |
| comment_retry | v15_1793637081218_dfd2b627 | 16:31:07.371 開始、16:34:54.943 完成；預測 POST 1、真實留言 POST 1，均 found；另有一次本地留言失敗注入，network_request_sent=false，沒有重送預測 |

首情境 target 為 ground 32977、question 43332／post 43327、bot 311763；只有合成輸入。歷史筆數 1 是測試前基準，兩次 POST 後分別為 2、3；found 時間為 15:58:12.486、15:59:45.266。所有 request／狀態在 `data/v15_probes.jsonl`，控制端時鐘與共享 API pace mtime 在 `data/a2_execution.jsonl`；靜默期間只讀本地狀態與整理文件，不呼叫 Metaculus API。正式比賽的禁止重送規則沒有改變。

第一、二項間實際 API 靜默為 872.322 秒，共用 pace mtime 未變。長度情境使用同一測試區與題目；四筆 found 依序為 16:16:48.795、16:18:10.008、16:19:29.901、16:20:48.252。`max_accepted_length=150000` 只代表最大已測可接受長度，沒有測到拒絕邊界，不能稱平台的絕對上限；正式留言上限仍為 3000 字元。第二、三項間靜默為 619.162 秒，前後 pace mtime 相同。

長度結果是 ASCII 合成私人留言的立即讀回證據，不保證長期保存全文；D98 已記錄官方後端對超過 500 字元的 bot 私人留言，滿 30 天後保留前 200 字元的規則。第一段靜默由執行 wrapper 在開始前核對單調時鐘與 pace mtime，另有本輪本地檢查；當時控制紀錄只保存間隔及上一項結束的 mtime，彙整器不能獨立重建期間全部 API 活動。第二段開始另記前後 mtime 與單調時鐘，增加可核對的中繼資料；不改既有 probe 或請求上限。

第三情境仍用同一 bot／ground／question／post。預測 request `request_1793637219615_bbef3c32` 於 16:33:52.187 found；本地注入 `request_1793637256133_7a87b9bc` 於 16:34:16.137 confirmed_failed，明確未送網路；原 Submitter 狀態機補留言 `request_1793637279480_61dcb606` 於 16:34:54.942 found。隔離送出帳冊最後為 comment_ok，位於 `data/v15_probe_submitter/v15_1793637081218_dfd2b627/`。這證明本地確認失敗後的正式補留言路徑，不冒充平台真的回過一次 4xx。

16:35:10.643 的 `data/a2_acceptance_checks.jsonl`：all_three_complete=true、hazards=[]；彙整另核對 probe 與控制結果、實際上限、四階長度與重建合成文字的 SHA-256、歷史筆數遞增及注入／真實請求分開計數。總計真實預測 POST 3、真實留言 POST 5，全部 found，未知 0。正式 `data/submissions.jsonl` SHA-256 一直保持 `92c23f8261efd92e3b78128915054feb425df305e66534ddfdbef7d72947d6ce`。

16:35:10.760 收尾完成：先原子寫回 local，再 bootout 並確認 `org.forecast-lab.cycle` 不再載入；安裝 plist 移至 `data/launchd_archive/20261102T163510Z/org.forecast-lab.cycle.plist`（SHA-256 `95e8cee76574cc1810c17fe85d8dace0dbcfe352072c979068afe870c07d36d2`），避免登入時再啟動。沒有改 TG 服務，沒有改凍結 campaign 或刪帳冊；cycle guard 已釋放，STOP_SUBMIT 不存在。既定四題及 A2 已做完，所以結束執行，沒有改寫原窗口截止或另開重疊窗口。操作、審查與核對器存於 `data/operations/d113-a2-20261102/`，含雜湊 manifest。

依 D107 A4，Claude 第一輪文件／證據審查要求更新過時狀態、限定長度與保存期間的結論，並強化彙整器對階梯／雜湊／請求計數／注入差異的核對；已修正。最後一輪 **PASS，沒有 blocking**（900 秒上限），只複核提供的程式、操作中繼資料與文件，沒有讀取開放題預測或重做平台請求。這不取代原 v15_probe_v4 的 Gemini 審查或 D111 的 2055 passed。


規格：Claude（主持）。實作：GPT（Codex 開發沙箱）。審查：Claude，之後 Gemini（送出／讀回相關，D107 A4 規定須獨立審查）。
Louis 於 2026-11-02 核准 D107 A1（試驗場地有界寫入常設授權）與 A2（bot testing area 允許重送）。

## 背景

- M3 還缺三項元件證據：私人留言的長度上限、「預測成功、留言確認失敗後只補送留言」的真實路徑（§3.6 狀態機第 4 點），以及同一題重複送出時平台的行為。
- bot testing area（32977）支援的兩題都已被 probe 用過（是非題 43332、多選題 46450）。`v15_probe_v3` 只選沒有自己預測、沒有被 probe 保留的題目，因此選不到題。D107 A2 取消這個自訂限制；平台的測試區本來就允許重送。

## 要做的改動（`v15_probe_v3` → `v15_probe_v4`）

### 共通

1. `prepare(question_type, scenario)`；CLI 加 `--scenario {single,comment_length,comment_retry,duplicate_forecast}`，預設 `single`。**`single` 就是現行 v3 的行為，選題與執行完全不變。**
2. 新情境的計畫帶 `reuse_authorization: "PLAN D107 A2"`、`scenario` 與事先固定的請求上限（`max_forecast_requests`、`max_comment_requests`）。`plan()` 逐項驗證；被竄改就拒絕。
3. 新情境允許選已有自己預測、或被先前 probe 保留的 bot testing area 題目，但：只限 32977；先前所有 probe 在該題都沒有未解決的請求。依 Claude 續作決定，同一 `request_id` 的 `unknown`／`write_unconfirmed` 之後若有 `found` 或具拒絕證據的 `confirmed_failed`，視為已解決；沒有後續終局結果、孤立或重複請求仍阻擋。其他選題條件（成員、開放中、已開題、關閉時間在 1 小時以後、多選題選項有效且可對帳）不變。
4. 一般送出器（`Submitter` 的 `_duplicate` 與 `probe_reservations`）對非 probe 意圖的阻擋不變：正式流程仍不會送到 probe 用過的題目。
5. 預測與留言的讀回、請求間隔、unknown 不重送都沿用現行程式。長度與重複預測情境遇 4xx／429 即停；`comment_retry` 依 Claude 續作決定沿用 Submitter 既有的確認失敗重試與退避（含 Retry-After），上限見下。每個計畫只執行一次，沒有復原旗標。

### 情境 `comment_length`

- 不送新預測；題目必須已有本帳號的預測（`included_forecast: true` 才有東西可附），且依 Claude 續作決定，須已有 forecast probe 保留紀錄；不擴充 `probe_reservations()`。
- 計畫固定長度階梯 `[2000, 10000, 50000, 150000]`（字元數），文字是 ASCII 的合成填充（含 probe_id 與「Synthetic length test; not a research forecast」），不含題文或模型輸出。
- 依序送出私人留言，每則都讀回並核對**全文雜湊與長度**（可偵測平台截斷）。遇到確認拒絕就停止，記錄 HTTP 狀態碼；遇到 unknown 或截斷也停止。
- 結果記 `max_accepted_length`、`first_rejected_length`（可為 null）、`truncation_detected`。留言請求最多 4 次。

### 情境 `comment_retry`

- 目的：用正式 `Submitter` 的既有程式（`_resume` 的留言補送與退避），在真實平台上走一次「預測成功、留言確認失敗 → 只補送留言」。**不得另寫一套補送邏輯。**
- 觸發方式是本地故障注入：只在 probe 內、只在 transport 包裝層、只對第一次留言請求生效，在該次 transport 呼叫的任何網路 I/O 之前丟出確認失敗；帳冊明確記 `injected_fault: true`、`network_request_sent: false`。依 Claude 續作決定，此次算全域 `COMMENT_ATTEMPTS=3` 的第 1 次，之後由 `Submitter` 的既有流程對同一份留言最多送出兩次真實請求並讀回。
- 為了不汙染正式帳冊，這個情境的 `Submitter` 使用獨立的資料根目錄（例如 `data/v15_probe_submitter/<probe_id>/`），合成的整合／留言／快照紀錄都標 `synthetic: true`。平台層的既有預測檢查與正式帳冊的 bot／question 去重仍要生效（可以讀正式帳冊做檢查，但不寫入）。
- 上限（Claude 續作決定，取代原真實留言一次的規格）：計畫固定 `max_forecast_requests=1`、`max_comment_requests=3`（含本地注入一次、真實留言最多兩次），`plan()` 驗證。預測合成值：是非題 50%、多選題均勻。第二次真實留言也被拒時，由既有狀態機以 `comment_failed` 結束。
- 不修改 `submit.py`，不新增每實例上限。只採既有 `_resume`／退避，不自行補送。

### 情境 `duplicate_forecast`

- 同一題送兩次預測（是非題先 0.5 再 0.6；多選題先均勻再固定的另一組合成值），不送留言。
- 每次都讀回 found；第二次的讀回必須認得是新的那筆（以送出前的預測 ID 基準區分），並記錄平台歷史筆數變化。預測請求最多 2 次。
- 結果說明平台對重複送出的行為（接受並保留歷史、或拒絕）。

## 不能做的事

- 不改 `submit.py`、`submit_transport.py` 的正式送出／讀回／退避規則與 POST 上限（`comment_retry` 只透過包裝與獨立資料根目錄使用它們）。
- 不改 prompt、模型、整合、預算。不修改 `config/`、`PLAN.md` 正文、`AGENTS.md`。不呼叫網路。
- `single` 與既有 v1～v3 計畫的驗證與執行規則不變。

## 測試（離線、合成 HTTP，沿用 `tests/test_v15_probe.py` 的假伺服器）

- `single` 的既有測試全部通過；v3 計畫在 v4 程式下照舊驗證。
- 新情境能選到已有預測／已被保留的 bot testing area 題目；有未解決請求的題目不選；非 32977 的題目不選。
- `comment_length`：全部接受、在第 k 階被拒、截斷（讀回文字不同）、unknown 四種情況，請求次數與結果欄位正確，停止後沒有額外 POST。
- `comment_retry`：注入的失敗沒有任何網路請求；`Submitter` 依既有退避補送同一份留言並 found；預測最多 1 次 POST、真實留言最多 2 次 POST；正式帳冊沒有被寫入；兩次真實留言均被拒時標記 `comment_failed`、不再送。
- `duplicate_forecast`：兩次 found 且第二次認得新紀錄；第二次被拒或 unknown 時停止。
- 計畫被竄改（上限、階梯、授權字串、情境）→ 拒絕。
- 一般 `Submitter` 對 probe 用過題目的阻擋仍有效。

完成後執行 `tests/test_v15_probe.py`、`tests/test_submit.py` 與完整測試；回報實際結果，沙箱目錄限制造成的失敗分開列出。若規格有矛盾或缺漏，照最保守（拒絕、不送出）的解讀實作，並在 summary 列出你的解讀。

## 執行（審查通過並提交之後，依 D107 A1 由主持人執行）

切到 trial → 依序執行 `duplicate_forecast`（是非題）、`comment_length`、`comment_retry`，每個情境之間至少間隔 10 分鐘且沒有其他 Metaculus API 活動 → 回 local。結果寫入本檔與交接檔，並在 TG 回報。

## GPT 工作副本實作紀錄（2026-11-02，待 Claude 審查）

狀態：**依 Claude 續作決定完成三情境的離線實作，needs_input=false；待 Claude 與 Gemini 審查。**
本輪沒有實際 API 活動、讀取正式帳冊、提交、合併或部署。測試只使用工作副本 `.tmp` 內的合成輸入。

- `v15_probe_v4` 新增 `--scenario`；預設 `single` 保留原流程、v1／v2 明確留言恢復及 v3 有界計畫。新情境以計畫外的情境欄位交叉核對計畫內情境，驗證授權、整數上限、階梯及第二份合成 payload。
- `duplicate_forecast` 支援已有自身預測／已被 probe 保留的題目；每次送出前都重查前檢，使用既有 `APITransport.readback()`，第二次以包含第一筆的基準排除舊紀錄。結果的 `history_counts` 是按平台識別去重後的自身預測筆數；分辨保留歷史、未保留完整歷史、拒絕及 unknown。
- `comment_length` 使用固定 ASCII 階梯，最多四則私人留言、零預測請求。既有讀回 found 後另核對全文 hash 與長度。讀回 unknown 時，只用回執 ID 或同一 probe／階梯的唯一文字前綴做截斷診斷，不會把診斷結果提升成 found。第一頁缺漏、身分不明、歧義或診斷讀取失敗均為 unknown；停止後沒有額外 POST。
- 新情境一次執行；中斷後拒絕續跑，完成後只回傳已保存結果。拒絕復原旗標。正式 `Submitter`、transport、設定與 POST 上限未修改；probe 使用獨立前檢根目錄及唯讀的正常送出紀錄來沿用正式意圖去重，不複製或改寫正常送出帳冊。

### 續作決定及完成項目

- `comment_retry` 計畫固定預測 1、留言 3（包含注入 1 次）。在 `data/v15_probe_submitter/<probe_id>/` 建立全數標記 `synthetic: true` 的快照、合成輸入、整合、留言及送出帳冊；使用原有 aggregate／comment 產生器，使 Submitter 原有凍結輸入驗證可直接核對。留言含 probe_id 與「Synthetic retry test; not a research forecast」，不含題文或真實模型輸出。
- probe 專用 Submitter 子類只接合去重與帳冊，不覆寫 `_resume`、`_write`、`_failed`、`_reconcile_request` 或 `_validate_frozen`。平台前檢沿用原程式，正式意圖去重唯讀沿用原帳冊。正常送出器仍會被 probe 的預測請求保留紀錄擋下。
- transport 包裝層只在第一次留言呼叫注入 `ConfirmedFailure`，同時在 probe 與隔離送出帳冊標記 `injected_fault: true`、`network_request_sent: false`。測試比對假 HTTP 的全部 GET／POST 次數，證明注入呼叫不委派任何網路 I/O；Submitter 在呼叫 transport 之前的正常身分／場地前檢仍保留。
- 每次真實寫入均使用既有 `APITransport.readback()` 與收據身分／payload 核對；即使 POST 回傳完整成功收據，未讀回 found 仍記 unknown 並停止。傳輸逾時但獨立讀回 found 可繼續，絕不重送該請求。預測只進入一次 `_resume`，拒絕或 unknown 後不再次呼叫；留言補送與 1／2 秒指數退避完全由原有狀態機控制，兩次真實留言均遭拒時為 `comment_failed`。
- 歷史請求按追加順序、相同 request_id 及 operation 判定；unknown／write_unconfirmed 之後的 found 或具證據 confirmed_failed 可解除阻擋。本地注入以明確的未送出證據識別，不冒用 HTTP 4xx；缺乏證據、孤立、重複請求仍拒絕重用。
- PLAN §14.2 原三則 blocking 意見已改為一則最終狀態的 non-blocking 意見；正文、§15／§16 與版本號留給整合者。

### 其他保守解讀

- 長度測試維持「已有自身預測且已有 forecast probe 保留紀錄」，不擴充 `probe_reservations()`。
- 較早的 found 不解決其後新增的 unknown；必須再有該請求後續的終局證據。
- 新情境 unknown 不提供續跑／讀回入口；只保存結果，不重送。`single` 的既有唯讀對帳仍保留。
- `comment_retry` 遵守原 Retry-After：若需等待超過原狀態機的 60 秒界線，或退避期間被停用，保存當次停止狀態，不提供後續復原入口。這可能少於請求上限，不強行補滿。
- 所有階梯都接受時，`max_accepted_length=150000` 只代表測過的最大長度，不宣稱平台實際上限；尚無接受值時為 0。

### 本輪離線驗證

cwd 保持工作副本，所有 pytest 命令均設定 `TMPDIR="$PWD/.tmp"`，使用允許的
`/Users/ucchengg/forecast-lab/.venv/bin/python`；所有新增案例均為合成 HTTP／合成帳冊。

- `python -m pytest -q tests/test_v15_probe.py tests/test_v15_probe_reuse.py tests/test_submit.py`：第一輪 **417 passed**；補上退避期間停用、長 Retry-After、中斷、目錄身分等案例後，最終 **433 passed**（3.15 秒）。
- `python -m pytest -q`：**1831 passed、62 failed、2 skipped**（18.02 秒），不是全數通過。輸出保存在工作副本 `.tmp/d107-full-pytest.txt`。
- 沙箱／目錄限制共 **61** 個失敗：29 個 Gemini 轉接／驗收案例在列舉 `/Users` 時被拒；11 個 baseline 案例因祖先目錄 `.claude`／`.codex`／`.gemini` 無法列舉而保守停止；21 個 scheduler／supplement／workdir 案例要求 attempt 暫存目錄在專案外，而本輪指定的 `.tmp` 在專案內。沒有移出工作副本或放寬權限重跑。
- 另 **1** 個失敗：`tests/test_acceptance.py::test_paths_match_exactly_and_relative_paths_resolve_against_the_workdir`。fixture 把含空白的絕對路徑直接組成未加引號的 `touch <path>`；既有 `shlex.split` 無法把它視為同一個路徑。這是路徑假設的斷言失敗，與上述存取拒絕分開列出，未在本任務修改。
- 此副本不是 Git repository，無法以 Git 取得 diff；未存取正式 repository。修改保留在 `src/v15_probe.py`、`tests/test_v15_probe_reuse.py`、本文件及 PLAN §14.2 指定段落，供 Claude 審查。

## 整合（Claude，2026-11-02）

- 第一輪 needs_input 的決定：不修改 `submit.py`、不新增每實例上限；`comment_retry` 計畫固定預測 1、留言 3（含本地注入 1 次，真實最多 2 次）；`comment_length` 保留只選已被 probe 保留題目的嚴格解讀；同一 request 之後已 found 或有拒絕證據者視為已解決。
- 第二輪（任務 `claudedev_1793624051552_03334732` r2）完成三個情境。Claude 審查未修改；主專案完整 **2023 passed**（切回 local 後；部分既有測試讀取真實的 `submit_mode.json`，trial 時會失敗，與本次修改無關）。Gemini（Argon high）獨立審查 PASS，沒有發現。
- 執行時機：probe 會取得 scheduler／submit／三線鎖，與 launchd 週期互斥；在 D105 端到端的題目處理完、週期閒置時再執行。
