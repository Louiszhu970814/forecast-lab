# Forecast Lab M4 初稿審查：`src/resolve.py`、`src/score.py`

## 0. 結論

**結論：不通過（REQUEST CHANGES），需要修改。** 我沒有核准這份初稿，也不代表任何人核准。

- **審查方式**：只讀了封包裡的原始碼、PLAN §4 和測試名稱。沒有執行任何測試、指令或 API，也沒有讀正式預測資料、實際題目或部署設定。
- **「38 個合成測試 PASS」**：這是封包自己的說法，我沒有驗證。封包列出 25 個測試函式名稱，參數化展開後可能是 38 個，這點也無法確認。
- **行號**：封包沒有附行號。下面的「約 L」是我依封包文字估的，誤差大約 ±20 行。請以引用的函式名稱和程式片段來定位。
- **用語**：
  - **【Blocker】**：從程式碼本身就能推出的缺陷，修好之前不應通過。
  - **【需證據】**：要看到 submit.py、metaculus.py、numeric.py 或實際 API 結構才能判斷；在證據補上之前也不能 PASS。
  - **【建議】**：不擋合併的改進。

---

## 1. Blockers

### B1｜D52 的意向分析（ITT）一定會漏掉沒送出的升級題，結論資格會被高估
**位置**：
- resolve.py `_select_candidates_with_diagnostics`（約 L338–420，`allowed_statuses = set(SUBMITTED_STATUSES)`）
- score.py `_build_d52_escalation_accounting` 的 episodes 迴圈 `else:` 分支（約 L2165–2175）
- `_score_unsubmitted_tier1_baseline`（約 L1965，`return None, "no_resolution_event"`）
- `_classify_non_evaluatable`（約 L2120）
- `itt_disc`（約 L2235–2245，`if missing_outcome_reasons:`）

**問題鏈**（從程式碼推出）：
1. resolve.py 只會查有送出紀錄的題目（`forecast_ok` / `comment_ok`）。所以升級後沒送出的題目，永遠不會有 `resolution_event`，除非用了 `--include-dry-run`。
2. 在 score.py 裡，這類題目一定得到 `no_resolution_event`，接著被歸成 `not_yet_evaluable`，而不是 `missing_outcome`。
3. `itt_disc` 只有在 `missing_outcome_reasons` 不為空時才取消優越性結論。結果是：
   - `complete_itt_coverage` 會是 True；
   - 失敗、沒送出的升級題全部被排除，但仍可能得到 `can_claim_superiority=True`。

這正好是 PLAN §4.3／D52 寫明要避免的情況：「只比較成功完成第 2 層的題目會高估效益」。正式環境裡，`fallback_zero`（補 0.0）這條路徑實際上用不到，只有乾跑資料會走到。

另外有兩點相關問題：
- 就算有結算值，對沒送出的題目補 `0.0` 也等於假設「升級沒有造成損失」。這個規則沒有預先登記，而且 `fallback_zero_count > 0` 時也不會取消結論。
- `_extract_attempt_budget_cost`（約 L2045）在缺少 `budget_cost` / `quota_weight` 時默默當作 1.0。這不符合 PLAN §4.5「缺值不當成 0、該題合計標為不完整」的口徑。缺少成本的筆數完全沒有揭露，`log_score_gain_per_budget_unit` 卻照樣算出來。

**最小修補**：
- (a) 在 `else` 分支（沒送出的 episode），如果 `err_reason == "no_resolution_event"`，改歸為 `missing_outcome`，原因寫成 `unsubmitted_no_resolution_source`，表示永久缺失，不是「尚未」。
- (b) `itt_disc` 在以下任一情況成立時，一律設 `can_claim_superiority=False`：`escalated_unsubmitted_count>0`、`fallback_zero_count>0`、`not_yet_evaluable_count>0`。
- (c) 預算成本缺值時計入 `budget_cost_missing_count`；只要大於 0，`log_score_gain_per_budget_unit` 就設為 None，並標記為不完整。

**建議補的測試**：一題已送出且已結算的升級題，加上一題沒送出的升級題（不寫 resolution）。斷言 ITT 不能宣稱優越，而且 `missing_outcome_count==1`。

### B2｜「最終整合 vs 第 1 層整合」比較時，把沒升級的題目（差值固定 0）也算進去
**位置**：score.py
- `elif not escalated: tier1_agg = final_agg`（約 L1230）
- `_summarize_subset` 的 pairwise 迴圈：`is_t2 = comp.startswith("player_tier2:")`、`effective_paired_count = escalated_paired_count if comp == "tier1_aggregate" else ...`（約 L2400–2440）

**問題**：沒升級的題目 `final == tier1`，差值一定是 0，但仍被放進 `diffs_by_row` 和 bootstrap。結果是：
- 平均差值被稀釋，信賴區間被一堆 0 壓窄；
- 結論判定卻用「升級題數」當樣本數。

樣本數和信賴區間不是同一個母體，可能產生不一致的優越性宣稱。這也違反 PLAN §4.3「第 2 層的效果只能在升級題目內比較」。

**最小修補**（1 行）：改成 `is_t2 = comp.startswith("player_tier2:") or comp == "tier1_aggregate"`，讓這項比較只用升級題，`effective_paired_count` 跟著改成 `len(paired_indices)`。如果還要保留全題目的版本，就另外加一個只做描述的欄位，不給結論資格。

**建議補的測試**：100 題沒升級加 10 題升級，斷言 `final_vs_tier1_aggregate.paired_count==10`。

### B3｜官方排名不可信，卻以「Official Ranking」呈現
**位置**：
- resolve.py `_build_client_ranking_fetcher`（約 L690–715）
- `extract_official_ranking`（約 L168–215）
- score.py ranking 區塊（約 L2560–2700）
- `render_scoreboard_markdown` 的 `**Official Ranking**`

**證據**：
- 排名來自 `active_client.get(f"projects/{id}/")`，掃描最上層和 `leaderboard`、`primary_leaderboard` 等子物件裡任何名叫 `rank`、`total_participants` 的數字。這些數字沒有綁定本機器人的 user id，也沒有確認這個端點真的是排行榜。可能抓到的是專案或排行榜自己的欄位，不是我們的名次。
- 這份資料被標成 `ranking_source="tournament_leaderboard"`，名稱本身就有誤導。
- score.py 的 `latest_ranking` 取的是「所有賽事中最後觀測到的一筆」，有多個賽事時，標題列顯示的是哪個賽事的排名不確定。

**最小修補**：
- (a) fetcher 只接受能比對到自己 user id 的排行榜條目，並記下端點和 `matched_user_id`；比對不到就回傳 `{}`。
- (b) score.py 在報告裡把沒有通過比對的排名標成 `unverified`，或乾脆不顯示。標題列只列 `official_rankings_by_tournament`，不要挑一筆「全體最新」。
- 在證據補上之前，PLAN §4.1「官方排名」這一項不能算完成。

### B4｜排名變動會反覆寫入結算帳冊，污染只追加的紀錄
**位置**：resolve.py `fingerprint_payload` 含 `"official_ranking_data": ranking_data`（約 L585–595）、`ranking_cache` 每次執行都重新抓（約 L555）。

**問題**：每次執行都會重新查所有已送出、已關閉的題目，包括早就結算的題目，而賽事排名是所有題目共用的。只要排名一動，同一賽事裡每一道已結算題目都會各追加一筆新事件，而且帶著 `supersedes_resolution_event_id`。這和 PLAN §4.4「平台更正結算時追加新事件」的意思不一致。帳冊只能追加，一旦部署，這些紀錄就永久無法清除，所以要在部署前修。

**最小修補**：
- 把賽事層級的排名從 `fingerprint_payload` 拿掉，另外寫成 `type="ranking_observation"`，每個賽事每次執行最多一筆。
- `resolution_event` 只保留題目層級的分數和排名。
- score.py 的排名改從新的紀錄類型讀取。

### B5｜設定版本分段在缺乏來源證據時用猜的，沒有預設拒絕
**位置**：score.py
- `_derive_escalation_config_tuples`：`a_t1_atts = [f for f in q_atts if f.get("tier")==1][:len(PLAYERS)]`、`first_rc = next(...)`、`matching_t2_atts = unlinked_t2[:1]`、`linked_t1_atts = ...[:len(PLAYERS)]`、`anchor_t1_atts`（約 L1400–1600）
- `inferred_list[0]` 覆蓋 `_base_config_tuple`（約 L1610）
- `_match_against_known_segments` 的萬用字元歸段（約 L1650）
- 列層級的 `q["escalated"] = True` 同步（約 L1840）

**問題**：
- 沒有來源證據時，程式碼會拿同題裡「檔案順序最前面的 3 筆第 1 層嘗試」或「第一個出現的 `run_config_version`」來決定設定版本，也會把 `unknown` 段的題目歸到唯一能萬用比對上的已知段。這些都是拿不相關的執行結果來代替設定身分，違反檔頭自己的保證（Never substitutes ... unrelated run）和 PLAN §4.3 的分段原則。D52 各分段的題數、成本和預算跳過都可能歸錯段。
- 列層級的升級同步：如果同一快照曾有一筆沒被採用的第 1 層整合標記過升級，已送出但其實沒升級的題目也會被標成 `escalated=True`。這會影響 `player_tier2` 的分母和 B2 的樣本數。

**最小修補**：
- 拿掉所有 `[:len(PLAYERS)]`、`[:1]`、`first_rc` 這類後備邏輯，以及 `_match_against_known_segments` 的歸段。沒有來源證據就放進 `unsegmentable_question_ids`，D52 計為未歸屬。
- 列層級 `escalated` 只依據跟送出內容有來源連結的 `tier1_agg` 和 `p_t2` 判斷。

---

## 2. 需證據：證據補上之前不能 PASS

- **C1 題組題、條件題**：resolve.py 要求 `post["question"]` 是 dict，而且 `q_obj.get("id") == qid`（約 L530）。Metaculus 題組貼文的子題放在 `group_of_questions.questions[]`，會一直被判成 `PostQuestionMismatch`，永遠無法結算。score.py 有 `event_cluster_id`，看起來系統預期一篇貼文對多題。需要確認已送出的題目裡有沒有題組題；如果有，這就是 blocker，最小修法是依 `qid` 從 `group_of_questions.questions` 找出子題。
- **C2 計分依據是否等於實際送出值**：score.py 用 `aggregate_id` 找到的整合結果計分，沒有比對送出紀錄的 payload 或 hash（約 L1080–1100）。PLAN §4.1 要求「以實際送出的機率計算」。如果 submit.py 會做四捨五入、截斷或 CDF 標準化，本地分數就不是用實際送出值算的。需要 submit.py 的證據；如果送出紀錄有 `payload_sha256`，最小修法是比對它，不一致就拒絕計分並記錄原因。
- **C3 `comment_ok` 的語意**：兩支程式都把 `comment_ok` 當成已送出。需要確認它是否一定在 `forecast_ok` 之後才會出現，也就是「只留言成功、預測沒送出」的情況不存在。
- **C4 讓路例外與 429 退避**：`priority_opener` 和 `priority_sleep` 會拋出 `PriorityYieldError`（約 L643–675）。如果 `MetaculusClient` 或 `Backoff` 的重試迴圈用 `except Exception` 吃掉這個例外，就會先重試再丟出一般錯誤，而且 Backoff 可能已在 `api_cooldowns.jsonl` 寫入預約紀錄。需要看 metaculus.py 和 api_backoff.py。
- **C5 `N.TYPES` 是否包含 `date`**：如果包含，日期題的結算值是 ISO 字串，`float()` 解析失敗，會永遠停在 `unresolved`。
- **C6 第 2 層的來源格式**：`_validate_attempt_ancestors` 要求第 2 層嘗試的 `input_attempt_ids` 裡直接有第 1 層 ID，只有 `prev_attempt_id` 不夠，否則會判 `missing_tier2_parent_provenance`，整題拒絕。需要對照 forecast.py 實際寫出的欄位，避免正式資料全部被拒。

---

## 3. 建議（不擋合併）

1. **低優先序（`_is_lock_busy`，約 L106–133）**：
   - darwin 分支 `except OSError: return False` 是出錯時放行，建議改成回傳 True，也就是讓路。
   - 非 darwin 分支實際上會短暫取得鎖，可能讓 submit 的 `LOCK_NB` 失敗，形成優先序反轉。目前部署在 macOS，風險較低，但建議改成只探測、不取鎖，或者直接讓路。
   - `qqihh` 的結構順序和 darwin 的 `struct flock` 一致。至於 F_GETLK 能不能看到 `flock()` 鎖，要以 forecast.Lock 的實作為準。
2. **API 負擔**：已結算而且官方分數已經穩定的題目，建議設定停止查詢的期限。遇到 429 或冷卻中的錯誤，建議中止整次執行，不要繼續查下一題。另外，`main` 不管有沒有錯誤都回傳 0，建議有錯誤時回傳非 0（讓路的情況可以維持 0）。
3. **帳冊欄位**：
   - 建議加 `official_score_first_observed_at_utc`。目前排名或狀態一變，`observed_at_utc` 就跟著更新，官方分數原始的抓取時間會被蓋掉。
   - 建議保存原始 payload 的 hash，對應 §4.4「保存原始結算狀態」。
   - resolve.py 接受沒有 `at` 的送出紀錄，score.py 卻要求要有時間戳，兩邊口徑應統一。
4. **分段穩定性**：同一組基礎設定一旦出現第二個 reconcile prompt 版本，沒升級的題目就會從 `|r1` 改到 `|no_tier2`。舊報告的分段名稱會變，而且分段變成依「有沒有升級」切開，這是由結果決定的切法。建議改成讓任何 prompt 變更都升 `run_config_version`，再以它分段。
5. **Bootstrap**：同步抽樣、題群抽樣、1,000 次、百分位索引都正確。但抽樣是在整個分段上做，再取出子集合（例如只有升級題的 `player_tier2`）；子集合很小時，「抽到空集合就退回單一單位」會讓區間尾端變寬。建議另外報告子集合抽空的比例，或改成在子集合內部抽樣。
6. **公式（人工推導，沒有執行）**：
   - 是非題、多選題：符合 PLAN。多選題只截斷勝出選項的機率，不逐項截斷。
   - 連續型 CRPS：均勻分布 F(u)=u、u*=0.5 時，∫₀^½u² + ∫½¹(u−1)² = 1/12，正確。
   - 離散型 H_i：當 i≤k 時為 0、i≥k+1 時為 1，邊界和半格位移一致；兩端尾部的 H 值也正確。
   - 待確認：u* 剛好落在格線上時歸入上方格（floor）的慣例；RPS 除以 (n+1) 的正規化；CRPS 只在 [0,1] 內積分；截斷版的 log score 只設下限 0.01。這些都要和 M5-NUMERIC-SPEC 的預先登記一致。
   - 另外，不同題型的 log score 量綱不同，現在混在同一個平均裡。建議依題型另外報告。
7. **其他**：
   - `escalation.candidate=True` 也被當成升級，需要確認這符合 D52 的定義。
   - `_build_client_fetcher` 沒有被使用。
   - 如果 `_HELD_LOCKS` 沒有 `paths` 屬性，同一 process 自己持有的鎖就靠 OS 探測；POSIX 鎖在同一 process 內看不到，這點要確認。

---

## 4. 重點逐項判定

| 重點 | 判定 |
|---|---|
| 帳冊格式（ledger 契約） | **不通過**（B4；C1 也可能影響） |
| 未結算題目排除 | 通過。`resolved` 以外的狀態都排除並計數，模糊、取消的題目不計分 |
| 送出來源追溯（submitted provenance） | 有條件：來源鏈驗證嚴格，但缺少對實際送出值的比對（C2、C3、C6） |
| numeric/discrete 公式 | 沒發現 blocker（見建議 6） |
| 版本分段 | **不通過**（B5） |
| bootstrap | 通過，附建議 5 |
| D52 成本 | **不通過**（B1、B2） |
| HTTP 退避與低優先序 | 需證據（C4），附建議 1、2 |
| 官方排名可信度 | **不通過**（B3） |

---

## 5. 依據區分

- **直接證據**：只有封包裡的原始碼文字、PLAN §4，以及測試名稱清單。
- **推論**：所有判斷都來自靜態閱讀和人工推導，沒有執行驗證。B1、B2、B4、B5 可以直接從程式碼推出；B3 和 C1–C6 牽涉 API 結構或其他模組，需要另外補證據。
- **核准狀態**：沒有核准，也沒有替任何人核准。B1–B5 修好、C1–C6 有證據之後，需要重新審查。