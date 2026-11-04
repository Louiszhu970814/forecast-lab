> 2026-11-04 D128 更新：Louis 已選 push＋B，D127 `b52f935` 已 push；ITT 規則見 [D128-LIVE-ITT.md](D128-LIVE-ITT.md)。下方待決記載為歷史，現行 score_v1 尚未切換母體。

# M4 結算與計分接手（2026-11-04，D126 階段 B／D127）

> **現況（UTC 2026-11-04 06:30）：手動入口已上線（PLAN v1.80 D127）。** 部署 SHA = r6 雙審 SHA（`data/operations/m4-takeover-20261104/r6-sha256.txt`）。未啟用排程；首題台北 11/06 21:00 結算前不宣稱實戰驗收。下方「最終交接狀態（05:37）」等段落為歷史紀錄。

來源：PLAN v1.79 §4、§5、D126；本輪 Louis 指示 GPT 接手 Gemini 未完成的 resolve.py / score.py，完成雙審及驗證後直接上線；若額度不足，交接 Claude。GPT 須預留下午預測額度（使用者告知台北 19:40 重置）。

## 目前驗證狀態

- 接手時四個檔案均未追蹤：src/resolve.py、src/score.py、tests/test_resolve.py、tests/test_score.py。初稿 38 項合成測試通過。
- 完整隔離測試：含 M4 為 2892 passed / 100 failed；移除四個 M4 檔案後相同基線為 2854 passed / 100 failed。這不是全套 PASS；既有失敗涉及正式設定與舊 fixture 假設，需另案修復。兩次失敗節點集合已比對完全相同，新增失敗 0。
- 隔離副本不含 .env、正式 data、reports 或登入憑證。只以合成資料測試，未讀開放題預測。
- 第一輪 Claude Opus / Gemini Argon high 官方 CLI：完整兩份程式已送審。Claude 於 UTC 05:33:21 完成，REQUEST CHANGES；Gemini 約 9 分鐘未回覆，GPT 於 UTC 05:37:24 為保留正式預測執行時段而結束程序，無審查結論，不當成 PASS。工具禁用、空白暫存工作目錄、乾淨環境，沿用既有 review_cli.py；審查期間持有 scheduler 與三線互斥鎖，審查控制程序已退出，鎖已釋放。
- 未啟用 resolve launchd，未執行正式 API 結算抓取；不得把本文件當上線完成證據。

## CDF 計分公式預先登記草案（cdf_score_v1）

固定於首次正式結算評估之前；需雙審確認後由整合者同步 PLAN §4、§15、§16 及版本號。這是本地診斷分數，不能冒充 Metaculus 官方 Peer Score。

- 是非題：raw log = ln(p_y)，Brier = (p-y)^2；另報 p 截至 [0.01,0.99] 的 log 診斷。
- 多選題：raw log = ln(p_y)，多類 Brier = Σ(p_k−1[k=y])²；另報結果選項機率截至 [0.01,0.99] 的 log 診斷，不重新正規化其他選項。
- 離散題：沿既有 numeric.py 的半格邊界，結果格 k 質量為 F[k+1]−F[k]；log 取其自然對數。RPS = Σᵢ(F[i]−1[i≥k+1])²/(N+1)。開放下尾 H=1、上尾 H=0，log 使用對應尾端質量。
- 連續數值題：沿既有線性／對數縮放至 u∈[0,1]，格內密度 f=N·(F[k+1]−F[k])；raw log=ln(f)。診斷只下限截到 0.01（密度可大於 1）。另計分段線性 CDF 的精確 ∫₀¹(F(u)−1[u≥u_y])²du；尾端用尾端質量的 log 與 H=0/1 積分。
- 初稿把 numeric CRPS / discrete RPS 放在共用 brier_score 欄位；正式報告需明確揭示其不同含義，不能宣稱是普通 Brier。
- 最新結算以 observed_at_utc ≤ as_of 為準；明確結算才能計分，取消／模糊不計。analysis_at 可納入較晚稽核，但不可早於 as_of。
- 不同 run_config、agg、prompt 分段；同題配對、同步 1000 次題目與題群 bootstrap；少於 30 題不下優劣結論。

## 官方契約核對與修補

2026-11-04 以 Metaculus 官方公開原始碼核對（只讀程式，未讀比賽預測）：

- [questions/serializers/common.py](https://github.com/Metaculus/metaculus/blob/main/questions/serializers/common.py)：自己的分數在 question.my_forecasts.score_data，key 為 score_type + `_score`。
- [scoring/urls.py](https://github.com/Metaculus/metaculus/blob/main/scoring/urls.py)、[scoring/views.py](https://github.com/Metaculus/metaculus/blob/main/scoring/views.py)：排名 GET `/api/leaderboards/project/{id}/?primary_only=true`，回傳 list，使用同專案主要排行榜的 `userEntry.rank`。
- 初稿 GET `/api/projects/{id}/` 不在官方路由，且猜測扁平排名欄位，已修正；不得從 entries 中其他人的名次推算自己，也不以 entries 長度冒充總參賽人數。權限拒絕或沒有本人排名時保留缺值。
- API 契約仍需正式已結算題的首次 GET 驗收；首題計畫時間台北 11/06 21:00 尚未到。

## 上線與操作界線

雙審無 blocker、專項及相關回歸通過、保護雜湊不變後，初版手動入口可依 D126 上線；本輪 Louis 已授權，不需重問。不要順便啟用新排程或更改預測設定。resolve 只做 GET，和正式送出共用 API 退避並在 cycle/submit 忙時讓路；score 不改 data/config，報告只新建。

```sh
.venv/bin/python src/resolve.py --dry-candidates
.venv/bin/python src/resolve.py --question-id 46289
.venv/bin/python src/score.py --format markdown --write-report
```

正式執行不要傳 `--include-dry-run` 或 `--allow-before-scheduled-resolve`。首題尚未結算時不可宣稱端到端實戰驗收通過。commit 依 AGENTS 由人類或 Louis 指示的 Claude 協調者執行。

操作證據（ignored）：data/operations/m4-takeover-20261104/，含初稿 source packet、雙審原始輸出、完整／基線測試及副本位置。後續需補最終 SHA、複審、專項驗證與實際上線結果。

## Claude 待續作清單（初審後，尚未實作）

先讀 `reviews/claude-m4-code-review-r1.md`。第一輪封包 SHA256：`80d2a3e49641fa83a82ea5004d8dd3ade0515846889e14cfaaf02e2e9fd2b50f`。審查完成後 GPT 只改排名 fetcher 與測試；score.py 尚未修補。

1. B1：未送出的升級題沒有合法 resolution 來源，不能把它們當暫時不可評估而仍宣稱完整 ITT。缺樣本、未送出、補零或待結算均需抑制優越性結論。正式 forecast 記錄沒有 `budget_cost` / `quota_weight`，不可默認每筆 1 然後宣稱實際額度效益；區分 attempt 計數與成本缺值。
2. B2：final vs tier1 的效果比較只納入升級題；樣本數與 CI 母體一致，補多個未升級零差值的反例。
3. B3：GPT 已按官方來源修正 fetcher，但還需保留來源／本人身份證據與報告賽事標示、複審；不要把多賽事最後一筆當全局名次。
4. B4：賽事排名另存 `ranking_observation`（仍 append-only），每次每賽事最多一筆；不讓一個排名變動為所有已結算題製造新 resolution_event。score 需配套截至時間讀取。
5. B5：移除無 provenance 時拿前幾筆同題 attempt、第一個 run_config、未知版本萬用比對等猜測歸段；資料不足列未歸屬，不把未採用的升級決定套到已送出系列。
6. **C2 已確認是真缺陷**：`src/submit.py:99` 的 `forecast_payload()` 對多選前 K−1 選項四捨五入到六位，最後一項取餘數；目前 score 用未四捨五入 aggregate。須用 intent 中凍結的 `forecast_payload`（`_candidate` 保存）計算 final，並核對 aggregate 轉換後 payload；缺值／不一致明確拒絕。合成測試目前未充分涵蓋真正送出契約。

補給下輪審查的相依模組證據：

- C1：`poll_questions.snapshot_from_post()` 與 PLAN 現行支援範圍需附上，確認正式不接題組／條件題；不要為審查臨時擴大支援題型。
- C3：`Submitter._resume()` 依 forecast、comment 順序執行，forecast 未成功即返回；`_candidate()` 凍結 payload，`_validate_frozen()` 比對。請附完整函式而非只附摘要。
- C4：`metaculus.MetaculusClient._get_url()` 只捕獲 HTTPError／Deferred／URLError／TimeoutError；`Backoff.open()` 只捕獲 HTTPError，PriorityYieldError 不被吞掉。已新增真跨程序 flock 測試，macOS F_GETLK 可觀測他程序鎖。
- C5：`numeric.TYPES = ("numeric", "discrete")`，不含 date。
- C6：`forecast.py` 寫入 tier2 `input_attempt_ids`、`prev_attempt_id`；應把輸入組裝及正式 record 寫入一起附給審查。

最新專項／相依回歸指令：

```sh
.venv/bin/python -m pytest tests/test_resolve.py tests/test_score.py tests/test_api_backoff.py tests/test_numeric.py -q
```

結果 **255 passed**（新增 7 個排名拒絕案例及 1 個跨程序鎖案例）。11+17 個受保護 SHA 完全相符。全套基線的 100 個失敗節點集合已逐一比對一致，新增失敗 0；下一輪變更後需重新驗證。不要用修改正式 config 的方式求 PASS。

## 最終交接狀態（UTC 2026-11-04 05:37）

**未上線／未 commit／未完成雙審。** Claude 目前只完成初審，尚未接手實作；須在桌面開啟 Claude 依本單續作，並先查看 TG status。沒有留下背景開發程序或待執行的自動部署。Gemini 原始輸出只到 user_input，未回覆審查意見；本輪不再重試以避免額外消耗。

目前四檔 SHA 已保存 `data/operations/m4-takeover-20261104/handoff-sha256.json`，相同內容已更新到 `work-path.txt` 指向的隔離副本；Claude 可直接在此副本修補。上線授權持續有效，但須修完 blocker、雙審與驗證通過才可執行。

## r2：Claude 接手修補（UTC 2026-11-04 05:40 起）

GPT 額度暫停，Louis 指示 Claude 接手。修補直接做在主工作區四個未追蹤檔；隔離副本 `work-path.txt` 同步測試。

- B1：未送出升級 episode 一律 `missing_outcome`（`unsubmitted_escalation_no_outcome_source`），移除 0.0 補值與 `_score_unsubmitted_tier1_baseline`；ITT 任一缺口（missing / not_yet / unsubmitted / fallback）即不可宣稱優越並列 `itt_incomplete_reasons`。成本缺值回 None，總額與每單位效益為 None，另列缺值筆數（正式紀錄沒有 `budget_cost`，故正式報告必為 None）。
- B2：`final_vs_tier1_aggregate` 只用升級題。
- B3：`fetch_ranking(tournament_id, expected_user_id)`，`userEntry.user.id` 必須等於該賽事送出列唯一 `bot_id`。
- B4：排名移出 `resolution_event`，改寫 `ranking_observation`（每次每賽事最多一筆、狀態改變才追加）；score 只讀已驗證觀測、依賽事列出，無全域最新與題目層級排名。
- B5：移除所有檔案順序／設定相似／萬用字元歸段後備；無來源的第 2 層嘗試不屬於任何分段；列層級 `escalated` 只依送出系列來源。
- C2：`final_aggregate` 以 intent 凍結 `forecast_payload` 計分，並以 `submit.forecast_payload()` 重算比對；缺值或不符 fail closed。
- 其他：cycles 實際時間欄位 `started_at`/`finished_at` 納入截止篩選；resolve 有錯誤時回 1；darwin 探鎖出錯改為讓路；報表註明 CRPS/RPS 不是 Brier。
- C1/C3/C5/C6 已以相依程式與正式帳冊結構（只看欄位、不看內容）確認，摘錄在 `packet-r2.txt`。

驗證：相關四檔 **263 passed**；隔離完整 **2908 passed / 100 failed**，失敗節點與基線完全一致（新增 0）。正式帳冊唯讀冒煙：14 筆 intent 凍結 payload 重算全部相符；score 可在正式帳冊執行；`--dry-candidates` 目前 0 題。r2 檔案 SHA 在 `data/operations/m4-takeover-20261104/r2-sha256.txt`。

審查：`packet-r2.txt`（139,770 bytes，sha256 `1970ac1c…`）同時送 Claude（1200 秒）與 Gemini（1500 秒，避免 r1 的 900 秒內未完成）；`review_cli_r2.py` 只把逾時參數化。結果見 `reviews.jsonl` 與 `claude-r2/`、`gemini-r2/`。

### r2 審查結果與 r3 修補（UTC 2026-11-04 06:00～06:40）

- Gemini r2 **PASS**（3 項 non-blocking）。Claude r2 **REQUEST CHANGES**：唯一 blocker B1-R（ITT 不完整時仍輸出只算子集的總增益與每次第 2 層效率，會高估）；E1～E3 需正式資料證據；N1～N8 建議。原文：`data/operations/m4-takeover-20261104/{gemini,claude}-r2/answer.md`。
- r3：B1-R 修正（不完整時總增益與效率為 None，另列 `evaluatable_subset_log_score_gain_nats`、`tier2_efficiency_status`；observed pair 與第 2 層配對比較同樣禁止宣稱）。含 unknown 的分段一律 unsegmentable。N1 白名單、N3 404 改診斷、N4 第 2 層 pv 嚴格、Gemini 2/3。
- 證據（只輸出計數）：E1 39 筆 candidate 全部有連結的第 2 層嘗試；E2 嘗試／整合版本欄位 0 缺、candidate snapshot 全對；E3 正式帳冊副本加合成結算後 14 題皆以凍結 payload 計分、拒絕 0、unsegmentable 0（副本已刪）。
- 驗證：相關 263 passed；完整 2909 passed / 100 failed，與基線集合一致；r3 SHA 在 `r3-sha256.txt`；r3 差異封包 `packet-r3.txt`（38,317 bytes）送 Claude／Gemini 複核。
- 交 Louis 的計畫層待決（不擋上線）：依現行 D52，未送出的升級題永久缺漏，正式資料已有 30 個未送出 episode，ITT 優劣與第 2 層效率實際上永遠不能宣稱；若希望日後能下結論，需預先登記 ITT 母體規則（例如只納入特定 run_config／送出模式後的 episode）。

### r4～r6 與上線（UTC 2026-11-04 06:10～06:30）

- r3 兩位 PASS；Claude r3 要求 E4～E6，r4 補 E6（單一分段外有無法分段題時 overall 第 2 層配對比較禁止宣稱）、D52 頂層 `itt_gap_reasons`、`no_escalated_episodes` 狀態、resolve 寫入端 rank 檢查 → r4 兩位 PASS。
- 上線冒煙：正式 `resolve.py` 因 2 筆 bot testing area V15 synthetic 探針（D87，無 snapshot）每次報 `MissingSubmittedSnapshot`、結束碼 1。r5 改為非 live 且無 snapshot 列記 `skipped`；r6 依 Claude r5 收緊（反向篩選：非明確 skip 一律為錯誤；live 缺 snapshot 仍是錯誤；score 同條件）→ r5、r6 兩位皆 PASS。
- 正式驗證（r6 程式）：`resolve.py` 鎖釋放後 status ok、結束碼 0、errors []、skipped 2（45708、46451）、0 候選、0 API；先前一次遇 cycle/submit 鎖回 `deferred_busy_lock`（讓路正常）。`score.py --format markdown` 結束碼 0（no_resolution_event 14、submitted_without_snapshot_id 2）。相關 268 passed；完整 2913 passed / 100 failed，失敗節點與基線相同（清單 `pytest-r6-failed-nodes.txt`）；受保護雜湊 28/28。
- 審查原文：`reviews/{claude,gemini}-m4-code-review-{r2,r6}.md`；r3～r5 原文與所有封包在操作目錄。

## 操作（上線後）

```sh
.venv/bin/python src/resolve.py --dry-candidates      # 列出到期候選，不打 API
.venv/bin/python src/resolve.py                       # 只 GET；遇 submit/cycle 鎖讓路（結束碼 0）；有錯誤結束碼 1
.venv/bin/python src/score.py --format markdown       # 只輸出到 stdout
.venv/bin/python src/score.py --format markdown --write-report   # 新建報告，不覆寫
```

首題 qid 46289 預定台北 11/06 21:00 結算：結算後先 `--dry-candidates` 確認在列，再跑 `resolve.py` 與 `score.py`，核對 `resolution_event`、官方分數欄位與（若有）`ranking_observation`，這才是實戰端到端驗收。

## 後續項目（不擋上線）

1. **P1**：skip 條件改 mode 白名單（只有明確非 live 值才 skip；mode 缺失或格式不符一律報錯），並補測試。
2. **P2**：resolve／score 共用 snapshot_id 與 mode 正規化 helper（空字串、純空白一致），補對照測試。
3. **P3**：score 的 `sub` 已是 `{**intent, **送出列}` 合併、live 優先於 dry_run；補一個「送出列無 mode、intent 有 live」的對照測試。
4. **P4**：live 缺 snapshot 的 resolve 測試補 status／結束碼；score 補 live 負向測試。
5. N3 後續：已穩定的已結算題停止重抓、排行榜重抓間隔。N8：非 darwin 探鎖改只探測。
6. agg 的 `reconcile_prompt_version`／`escalation.run_config_version` 仍為「有值才檢查」（正式整合沒有前者）；若日後補欄位再改嚴格。
7. **交 Louis 的計畫層待決**：依現行 D52，未送出升級題是永久缺漏；正式資料已有 30 個未送出的升級 episode，ITT 優劣與第 2 層效率實際上永遠無法宣稱。若希望日後能下結論，需在看結果前預先登記 ITT 母體規則（例如只納入某 run_config／live 模式之後的 episode）。
