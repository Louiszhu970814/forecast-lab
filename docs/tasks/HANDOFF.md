D145 接手（台北2026-11-07，PLAN v1.99）：Codex 已升為0.167.0且未停線；D143已移除三家CLI純版本差異阻擋，本輪清理殘留舊規則文字、三線回歸192 passed。另處理Claude一般設定變動：三次Haiku合成呼叫／29項PASS，僅更新claude.settings基準，UTC 2026-11-06T22:56:37.662Z恢復；22:57:26.939Z六項前檢全部通過且無安全停線。正式程式與pins不變，未重跑已提交題；詳見 [D145](D145-CLI-UPGRADE-CHECK.md)。Louis隨後已指示commit／push，實際結果見git與手機進度帳冊；原排程繼續。

D144 接手（2026-11-06，PLAN v1.98）：Louis 已授權日常開發目錄變更不再停線；GPT 完成 `baseline_v3`、嚴格驗收綁定與完整3644項離線測試。今天排入4題全交，3題缺線（Gemini2、GPT1），不重跑已提交題。UTC 2026-11-06T11:44:15.800Z 六鎖部署完成，三線兩層前檢全過無 waiver；詳見 [D144](D144-DEVELOPMENT-CATALOGS.md) 與 project_control status；舊D143交接保留。

D143 接手（2026-11-06，PLAN v1.97）：依 Louis 指示，已先行解鎖 Gemini 使主賽 `qid=46575` 完成三線第 1 層整合並送出（`forecast_ok`＋`comment_ok`，累計 29 題已送出），並經 Claude／GPT 官方 CLI `r2` 雙 PASS、333 項測試通過與六鎖部署，永久移除 CLI 版本更新停線規定，且將 `agy.project_grants`、`agy.shared_config` 與 `agy.cli_settings` 一般設定鍵改列為 `informational`（內容鍵與讀取錯誤仍停線），三線兩層 6 項前檢全數 `ok=True`。詳見 [D143](D143-BASELINE-RELAX.md)。

D139 接手：Louis 已明確授權三步完成後直接啟用30分鐘與Market Pulse；GPT已完成複審／驗證與正式部署，30分鐘搜尋及Market Pulse已啟用，詳見 [D139](D139-EXPANSION.md)。下文D137為歷史沿革；現行啟用證據以D139任務單為準。

# 給下一個對話的接手紀錄

## D138：data 無損封存／去重（2026-11-05，PLAN v1.92）

已交付：Claude／Gemini 同封包雙 PASS，兩份模擬檔無損封存與一份 AGY 重複候選刪除；data 約 1.79 GB → 1.02 GB。三份還原演練、18＋2＋71 測試、前後 12 項正式前檢全過；核心帳冊與現役 pins 保留。還原與保留範圍見 [D138](D138-DATA-CLEANUP.md)。D137 正式排程及截止不變；不要重跑一次性 apply。

## D137：主賽＋MiniBench已啟用（2026-11-05，PLAN v1.91）

- 原話「複審完 修改完可以 就直接開始正式賽了」，不重問是否參賽。D136已部署並雙審；主賽第二次有界接續已於2026-11-05T02:49:33.366Z完成；Claude／Gemini r3雙PASS、完整3393PASS、前後12項preflight全過，既有排程已啟動並有active cycle證據。詳見 [D137](D137-MAIN-LAUNCH.md)。
- 主賽33121＋MiniBench共用bot311763與原130／200／130額度，根計數起點及UTC11/10T04:00截止不變。取代等待M4分數才加入的時機；完整M3觀測／M4分數未完成的限制保留，不冒稱全部里程碑通過。原D132–D136工作保留，未commit／push。


## D136 第一批清理（2026-11-05，PLAN v1.90）

- Louis 已授權「OK開始做」；A01–A10 已套用；Claude／Gemini r2 雙 PASS，兩種入口完整3368PASS，根目錄976PASS，前後12項preflight全過；證據見 [D136](D136-CLEANUP-BATCH1.md)。B 類待各自條件完成另批處理，不能因日期到了自動刪除。
- **日期統一**：D132 核對的已送出正式批次最早預定台北 11/13 20:00、最晚 11/14 12:59；預定不等於已結算。下方 11/06 21:00 與 qid 46289 是原開發安排，已被更正，不能作為這批正式題首題結算或主賽啟用依據。
- 原 D126 階段表保留為歷史；目前清理順序與延後條件以 [2026-11-05 盤點](CLEANUP-AUDIT-2026-11-05.md) 及 D136 交付為準。D132–D135 未提交工作保留，未 commit／push。


## D135 健檢修正已交付（2026-11-05T00:48:30.708Z，PLAN v1.89）

- GPT接手Louis對十題健檢的修正要求，完成型別／單位分歧、缺線週期證據與audit設定／搜尋排除詞誤報分類。Claude r2／Gemini r2同SHA雙PASS，六鎖部署、前後六項preflight與line_guard全過；詳見 [D135](D135-QUALITY-REPAIR.md)。
- 240項相關／6項部署測試PASS；完整3251 passed／100個原有失敗，集合及摘要不變。所有歷史旗標／缺口保留；十題新版三閘門報告已追加。六個T1缺口相關週期均有前檢阻擋紀錄；11個自有keyword命中分類為設定／負向site查詢，不代表無洩漏。
- 四份研究提示詞候選v1已寫好並合成驗證、未啟用；下個獨立窗口另登記驗證。正式prompt／參數／cap／D118窗口及主賽未啟用狀態保留；未補跑舊題、未新增模型預測或POST、未commit/push。接手先查status；D132／D133／D134未提交內容保留。

## D133 工程交付已完成；新實題因權限為零呼叫（UTC 2026-11-04T17:30，PLAN v1.88）

- 本輪 GPT 已實作並部署 `practice.py` 獨立 prepare/run/recover/score/report；Claude r4／Gemini r4 同一來源 SHA 雙 PASS。六鎖、備份、前後各六項 preflight／line_guard 均通過，103 個保護檔不變。詳見 [D133 最終交付](D133-CLOSED-PRACTICE.md) 與 [來源 manifest](../../reviews/d133-source-manifest.json)。
- 79 項 practice、205 項相關、6 項部署故障測試 PASS；完整 3213 passed／100 failed，相對基線 3134／100，失敗節點與摘要一致，不能聲稱全套 PASS。
- 實際已跑 `data/practice/d133_inventory_20261104/`：108 候選全遮蔽，selected/admitted/started/pending 皆 0、0 次新練習模型呼叫，未消耗唯一非空批次。既有正式 10 道閉題通過三閘門後已健檢、來源／缺線／時點限制留在私人報告；尚無準確度結論。
- 新實題練習仍待官方合法資料存取，沒有自動續作或新增排程。非空批次仍最多 3 題／9 初始准入、共用既有額度、正式題優先；main 33121 未啟用，D118 固定窗口/cap/設定不動。
- 保留 D132 全部原未提交變更與 D134 英文偏好／成果；本輪未 commit/push。接手先查 `project_control.py status`；手動入口用 pinned PATH。此段為最新狀態，下方「待下一輪」為原交接歷史。


## D134 人類賽英文報告／Gemini 留言已完成（2026-11-04 UTC，PLAN v1.87）

- Louis 指定之後人類賽最終報告與留言使用英文，最後留言由 Gemini 撰寫再由本人貼上。這是持續偏好，不重問；依 PLAN §8／D134 執行。
- 兩道人類賽英文完整版為 `reports/advisory/2026-11-04_45519_en.md`、`2026-11-04_46343_en.md`，同題 `_comment_en.md` 為可直接複製的 Gemini 留言。完整報告內也有留言；原中文報告、原預測與稽核保留。
- Gemini 已經官方 CLI 完成單次兩篇潤稿，主持人修正整合公式等四處事實性措辭，機率與研究依據不改。詳見 [D132 的 D134 補記](D132-CUP-ADVISORY.md#d134-英文報告與-gemini-最後留言2026-11-04-utc)；無提交、無新預測或正式設定變更，未 commit／push。
- **D133 已由另一個 GPT 桌面對話接手**：本輪 UTC 16:32 查詢到共用進度 UTC 16:29:24 的接手紀錄，該對話正在隔離副本實作閉題練習，資料盤點／驗證／雙審待完成。本輪僅完成 D134 英文稿，不重複接手 D133；下方「尚未開始」是原交接當時的歷史狀態。D133 整合者請保留本次 v1.87／D134 與英文輸出偏好，避免覆寫。人類賽報告與 bot 開放題資訊持續隔離。此為 UTC 16:32 的歷史摘要；D133 其後已完成，見最上方最新交付。

## D133 原交接歷史：已關閉 FutureEval 題目練習（UTC 2026-11-04T16:17:54Z，PLAN v1.86）

- Louis 已決定先用關閉題練習，要求本輪寫交接、另開對話實作。完整接手單：[D133-CLOSED-PRACTICE.md](D133-CLOSED-PRACTICE.md)。**本輪只完成文件，練習尚未開始，不重問是否要做這個方向。**
- 先用 D126 三道閘門健檢已有閉題輸出；新練習採保守首批最多 3 題／三線各一次／合計最多 9 次准入，無自動修復、重跑、第二層或任何提交。這是下一輪事前登記與實作預設，沒有新增額度或窗口。先確認合法 API 權限、正式題優先與共用預算，不能直接放寬 D132 固定兩題入口。
- 分開「關閉後但結果未知的前瞻練習」與「結果已知的事後診斷」；close 不代表事件未發生，較晚作答不可與原截止時官方榜單公平比較。保存時點、來源與所有缺漏，另留未用來調整的驗證題；小批結果不調統計參數、不宣稱正式賽優勢。
- 主賽仍未啟用，11/13–14 為原評估時點、非官方等待門檻；本輪未取消 M6 前置條件或授權主賽。D132 已完成人類兩題，不重做。接手先查 `project_control.py status`，實作沿既有 Claude 完整／Gemini 獨立審查；正式手動入口使用 pinned PATH。
- 已同步 PLAN §4.3／D133／§16 與索引；未改程式、設定、排程、預算或預測，未 commit／push。工作樹有 D132 既有未提交變更，須保留。

## D132 人類賽兩題已完成（UTC 2026-11-04T16:03:23.411Z 核對，PLAN v1.85）

- Louis 已選最急兩道 Cup 人類賽；每題三線各一次，六次上限，不自動修復／重跑／提交，未新增窗口或排程。實作與操作見 [D132](D132-CUP-ADVISORY.md)。
- Claude r2／Gemini r3 overall PASS，相關 241 passed；五檔 SHA 一致、16 項受保護檔不變、六項正式前檢全過無 waiver。手動入口必須用 `PATH="$PWD/launchd/bin:$PATH"`，不能誤用全域 CLI。
- 兩題各三線一次全部完成，6 valid／adoptable、2 aggregate ready、無缺線；6 筆已啟動用量結案、pending 0、無新增 advisory 停線事件。資料在 `data/advisory/cup_two_1793805103443_30c607fc/`，報告為 `reports/advisory/2026-11-04_45519.md`、`2026-11-04_46343.md`。原始疑似稽核標記與來源限制保留在私人報告；沒有送出人類預測。
- **日期更正**：本輪官方 API 核對已送出 MiniBench 10 題全部 closed 未 resolved，最早預定台北 11/13 20:00、最晚 11/14 12:59；下方舊 11/6 記載不是這批正式題的首題結算。FutureEval 主賽沒有固定開跑日、尚未啟用，11/13–14 只先評估 M6 就緒度，仍待 Louis 逐場核准。
- 未 commit／push；不從文件推定已提交。沒有讀取開放 bot 題的預測內容。


## D131 三線均已恢復（UTC 2026-11-04T14:38:26.329Z，PLAN v1.84）

- Louis已明確接受D130兩個固定外掛候選「OK 接受驗收後恢復」，**不是待決，不得重問**。兩份human-acceptance.json保存原話／時間／完整SHA。
- Claude新驗收 `acc_1793802906153_9ce65180`，GPT新驗收 `acc_1793803102170_aa565e83`；各3calls、各29項PASS，兩層前檢均ok且無waiver。新基準僅分別替換claude.plugins／codex.plugins，其他來源/版本逐位元保留；Gemini維持1.4.12專案pin與ready。
- 三線六項前檢已通過；核心/固定設定/預算/窗口不改，完整歷史保留。詳細metadata與獨立核對見 [D130／D131紀錄](D130-THREE-LINE-REPAIR.md)。自然排程與commit/push以最新主持人摘要為準。
- 下一次版本維護仍為台北11/07 22:25:43；3天政策已記錄，未新增自動升版或未來外掛概括核准。

## D130 歷史狀態：Gemini先恢復，另外兩線後由D131完成（UTC 2026-11-04T14:25:43.706Z）

- Louis已授權修復並指定版本每3天集中更新。Gemini 1.5.0停用read_url_content，已固定官方同SHA的1.4.12，官方雙審PASS；新3calls隔離29項PASS、兩層preflight無waiver。舊failed3calls保留，Gemini本輪共6calls。
- `changelog`原是上游發布目錄；wrapper精確轉官方`--version`，實際合成CLI日誌均1.4.12且記自更新disabled。全域1.5.0保留，核心/固定設定不改。
- 本段是D130當時的歷史狀態；[固定plugin候選](D130-THREE-LINE-REPAIR.md)已由D131接受並完成重驗/核准。已備妥 `data/operations/d130-three-line-repair-20261104/repair_claude.py`、`repair_gpt.py`，須先保存真實接受事件，不能自行創建授權。每線各3calls，新PASS後才窄範圍核准。
- 下一次版本維護檢查：UTC 2026-11-07T14:25:43.706000Z（台北11/07 22:25:43）。政策已登記，尚未新增自動升版執行器；3天節奏不等於外掛內容概括接受。詳見 [D130修復紀錄](D130-THREE-LINE-REPAIR.md)與最新主持人摘要；commit/push及自然排程以實際紀錄為準。

## D129 已上線：D128 診斷與 P1～P4（UTC 2026-11-04T13:05:21.446Z，PLAN v1.82）

- `resolve_v2`／`score_v2` 手動入口已部署；Claude／Gemini `r2` 皆 PASS，相關 446 passed、完整 3091 passed／100 個基線失敗（集合不變），保護清單28/28。詳見 [D129 交付](D129-ITT-IMPLEMENTATION.md)。
- live 母體來源鏈診斷已實作，固定 D128 邊界不變；已記錄根 metadata 核對為3納入／32起點前排除／0未知。首次 candidate 未全面持久化，**歷史與live完整ITT優越/總增益/效率一律禁止**，僅觀測子集描述。
- 下一步：台北11/06 21:00首題真實結算驗收；後續在新窗口前補首次決策持久化與新cohort登記。不得回填虛構根、重設D118窗口或把本次診斷當完整分母。receipt完整身分/未綁定final/預算准入未落attempt等加固見D129文件。
- 未改預測設定、預算或排程。上線與push授權已存在，不再詢問。commit/push結果以git及最新主持人摘要為準。

## D128 已決定 push＋B（登記時紀錄；實作狀態已由 D129 更新）（UTC 2026-11-04T11:57:44.514Z，PLAN v1.81）

Louis 已選現在 push 與 live ITT 母體事前登記，GPT 接手。D127 `b52f935` 已 push 至 `origin/main`。規則已在 PLAN §4.3／D128／§16 與 [D128-LIVE-ITT.md](D128-LIVE-ITT.md) 凍結；不得再當待決或重問。

- B 是 D118 固定 live campaign 全部根升級決策，區間 UTC 11/03 04:12:32.156～11/10 04:00（右端不含）。正式失敗、未開始、未送出都保留；不能用成功送出或 mode 欄位篩母體，不能預設 30 個舊缺漏全部可排除。
- D128 當輪只登記規則；selector、P1～P4 已由上方 D129 完成雙審驗證並部署，完整 ITT 證據限制仍保留。
- 未讀開放題預測或結算值；只查本機 resolutions 帳冊不存在。首題 11/06 21:00 實戰驗收仍待執行；不新增排程、不動預算窗口。


## M4 已上線（手動入口，UTC 2026-11-04 06:30，PLAN v1.80 D127）

- Claude 接手 GPT，依 r1～r5 審查修補 B1～B5、C2、B1-R、E6 與上線冒煙發現的 V15 探針誤報；**r6 Claude／Gemini 皆 PASS**，部署 SHA 等於 r6 審查 SHA。詳見 [M4-RESOLVE-SCORE.md](M4-RESOLVE-SCORE.md)（含操作指令與後續項目 P1～P4）。
- 驗證：相關 268 passed；完整 2913 passed / 100 failed，與基線失敗集合一致；受保護雜湊 28/28；正式 `resolve.py` ok／結束碼 0（skipped 2 探針、0 API），`score.py` 正常輸出。
- **未啟用排程**，未改正式預測設定，未讀開放題預測。首題 qid 46289 台北 11/06 21:00 結算後，手動跑 resolve＋score 才算實戰端到端驗收。
- P1～P4 與 ITT 診斷已由 D129 完成；11/06 首題實戰驗收仍待執行。

## M4 接手 Claude：未上線、待修正與雙審（2026-11-04，歷史紀錄，已由上方 D127 取代）

Louis 本輪要求 GPT 接手 Gemini 未完的 `resolve.py` / `score.py`，雙審與驗證完後直接上線；並要求 GPT 預留下午預測額度，做不完交接 Claude（台北 19:40 重置）。GPT 已完成初步接手及修補，但 Claude 第一輪提出五類 blocker，故停止擴大 GPT 實作並留下具體接手單。**目前 Claude 只完成審查，尚未接手實作；文件交接不會自動啟動桌面 Claude。**

- 詳細狀態、公式與操作：[M4-RESOLVE-SCORE.md](M4-RESOLVE-SCORE.md)；第一輪完整審查：[Claude](../../reviews/claude-m4-code-review-r1.md)。Gemini 第一輪約 9 分鐘未回覆；GPT 於 UTC 05:37:24 結束該次程序並釋放共用鎖，**沒有 Gemini 審查結論**，需由 Claude 修正後重新送審。
- 初稿 38 項專項測試 PASS；GPT 修正不存在的排名 API 路由，改讀官方主要排行榜的 `userEntry.rank`，補身份／專案／主要榜驗證與跨程序鎖測試後，相關 **255 passed**。兩組保護雜湊 **28/28** 相符。
- 全套 **2892 passed / 100 failed**；移除 M4 的相同基線 **2854 passed / 100 failed**，失敗節點集合完全一致（新增失敗 0）。不得寫成完整 PASS。
- Claude 下一步：先 `project_control.py status`；依 B1～B5 修正 D52 不完整樣本／成本、升級題成對比較、排名與結算事件分離、來源證據不足時不可猜設定版本。排名 fetcher 的初步修正尚需複審。補 C1～C6 的相依模組證據，新增最小回歸，再送 Claude/Gemini 最終完整複審。
- 通過後 Louis 已授權直接上線手動工具入口，同步 PLAN 正文／§15／§16／版本與交接並由 Claude commit；不需重問。首題台北 11/06 21:00 尚未到，不得宣稱真實結算驗收已完成。不額外啟用排程。
- 四個程式／測試檔仍未追蹤，GPT 未 commit；未更動正式預測設定、未讀取開放題預測、未執行正式 resolve/score。證據在 ignored `data/operations/m4-takeover-20261104/`。


## 歷史：三方共識加速迭代窗口與分階段清理／迭代計畫表（UTC 2026-11-03 16:20，PLAN v1.79 / D126）

依 Louis 指示（「沒必要 0 寫入，不會有影響的直接處理，並加快模型迭代速度」），由 Gemini 盤點並經 Claude（`claude-opus-5-5` high）與 GPT（`gpt-6-astra` high）兩輪獨立 CLI 評估收斂，確立以下迭代窗口機制與分階段執行表：

### 1. 窗口與雙軌回饋機制
- **診斷／修 Bug 節奏（每天）與預算總窗口（7 天，至 UTC 11/10 04:00）拆開**：
  - `config/cycle_campaign.json` 只釘死 `run_config_v2.json` 與 `quota_policy.json` 兩份雜湊。所有不改這兩份設定的工程修補、測試隔離修正、文件同步與 M4 工具開發，**完全不必換窗**，驗證通過即可上線。
  - 備用設定切換逃生口：在 11/06 前完成 `budget_handoff_v2`（有界多跳鏈式 handoff：保留根窗口 `2026-11-02T13:03Z` 的 `accounting_start`、跨鏈累計用量不歸零、終點鎖定 `11/10 04:00Z` 不滑動延長、每週最多 2 跳）。
- **短期軌（每日「已關閉題目健檢」與 `review_eligible` 三道 AND 安全閘門）**：
  - 嚴禁直接看本地 `scheduled_close_time < now` 或直接翻 `data/raw/`。一律透過唯讀健檢工具（`src/post_close_audit.py`），同時通過以下三道 AND 閘門才揭露預測與推理內容（未通過只顯示題數）：
    1. **官方狀態確認**：重新 GET 官方 API 確認 `status in {"closed", "resolved"}`，且最新截止時間 `<= now - 15m`。
    2. **本地生命週期收斂**：該題無進行中 attempt，`submissions.jsonl` 已終結（`forecast_ok` + `comment_ok` 或明確終止），無 `unknown` 待對帳請求。
    3. **已移出排程佇列**：不在 `cycle.py` 的待辦佇列中。
- **中期軌（台北 11/06 21:00 首題開獎前上線 M4 `resolve.py` + `score.py`）**：
  - `resolve.py`（11/05 前）：只查 `submissions.jsonl` 已送出且已過 `scheduled_resolve_time` 的題目，共用 `api_backoff_v3` 跨程序限流鎖（優先序低於正式送出），追加寫入 `data/resolutions.jsonl`。
  - `score.py`（11/06 21:00 前）：計算實際送出題目的 Log Score、[0.01, 0.99] 截斷診斷值、Brier Score 與成對比較，並預先登記 M5 數值／離散題 CDF 的本地計分公式；以 `qid=46289`（11/06 21:00 預定結算）做首次端到端驗收。
- **可立即改 vs 必須累積樣本才改的分界線**：
  - **可立即改（A 類確定性工程缺陷）**：解析／schema 錯誤、`invalid_output`、CDF 不單調或邊界／縮放錯誤、Prompt 事實性缺陷（日期／時區／漏傳結算條件或單位）、搜尋工具或退避故障。
  - **必須累積樣本才改（B 類統計參數，依 §4.3 少於 30 題不調參）**：三線整合權重、extremizing 係數、截斷範圍、第 2 層升級分歧門檻、因單題結果改變 Prompt 信心語氣或停用／降權某條線。

### 2. 三方整合的分階段清理與迭代方案表（歷史；清理順序由 D136 及新版盤點更新）

| 階段 | 時機（台北時間） | 具體動作（清理 ＋ 迭代工具） | 套用方式 | 風險控制與驗收條件 |
|---|---|---|---|---|
| **階段 A**<br>✅ 已完成 | **11/03 晚（已完成）** | 1. **已完成**：刪除 29 個零引用過時 `.md`、更新 `README.md`、瘦身 `docs/tasks/HANDOFF.md`。<br>2. **已完成（修測試隔離）**：`tests/test_numeric.py` 改用 monkeypatch 測 `false` 預設；`tests/test_config.py` 拆開「真實設定 schema 驗證（允許 `local`/`trial`/`live`）」與「隔離 runner 強制 `local`」。<br>3. **已完成（建索引不搬檔）**：在 `docs/tasks/INDEX.md` 與 `reviews/INDEX.md` 標註 `ACTIVE` / `SUPERSEDED` / `CLOSED`。<br>4. **已完成（同步 `PLAN.md` v1.79 / D126）**：在舊段落加上 `【已由 Dxxx 取代】` 內聯標記，並將 M5、D121、D123～D126 同步進正文。<br>5. **已完成（上線每日健檢工具）**：實作 `src/post_close_audit.py`（`post_close_audit_v1`）與 `tests/test_post_close_audit.py`（`275 passed`），完成首次已關閉題目健檢。 | **全部免鎖**（純文件、測試與獨立唯讀新腳本，不碰執行期模組） | 在隔離與正式副本**不修改任何 `config/`** 直接跑過 pytest（`275 passed`）；保護雜湊 100% 不變；經 GPT 與 Gemini 雙審通過。 |
| **階段 B**<br>首題開獎前 | **11/05 ～ 11/06 21:00 前** | 1. **上線 M4 結算與計分**：實作並測試 `resolve.py` 與 `score.py`（含預先登記數值題 CDF 計分公式），在 11/06 21:00 `qid=46289` 預定結算時做首次實戰驗收。<br>2. **準備 `budget_handoff_v2` 與 R2-C**：離線完成多跳鏈式 handoff（保留根窗口 `accounting_start`）與 R2-C 自動重驗（24h 限制／原子預算／候選綁定），送三方雙審。 | `resolve.py` / `score.py` 初版手動執行**免鎖**；若套用 `budget_handoff_v2` 或 R2-C 至執行期則**需六鎖** | `resolve.py` 必須與正式排程共用 `api_backoff_v3` 跨程序退避鎖且優先序低於送出；`budget_handoff_v2` 須通過分叉／延長／漏算舊消耗的回歸測試。 |
| **階段 B′**<br>換窗前平靜期 | **11/07 ～ 11/09** | 1. **拆分 `PLAN.md` §14 歷史審查**：將 §14 早期審查回合移至歷史檔（保留 §15 決策編號與原位連結）。<br>2. **解除測試耦合**：將跨測試共用的 helper 分批抽到 `tests/helpers/`，消除 `test_*.py` 互相 import。<br>3. **抽離舊模組共用函式**：將 `src/m2_campaign.py` 的 `discover_tournaments` 與 `src/flab/dev_runner.py` 的 `clean_env, codex_binary` 抽到獨立共用模組（舊路徑保留薄轉接 wrapper 以維持相容）。 | 1、2 **免鎖**；<br>3 若修改 `continuous.py` / `team_runner.py` 則**需六鎖** | 拆分 §14 前確認不影響 `AGENTS.md` 保護雜湊；測試重構前後收集題數與 PASS 數一致；舊入口保留轉接不破任何 import。 |
| **階段 C**<br>週期換窗 | **11/10 12:00**（UTC 04:00） | 1. **登記新 7 天窗口**：核對前窗完整消耗與無未決請求，將 `fail_soft.py` 常數正式遷入新版 `quota_policy` / `run_config`（若有經證據核准的設定調整一併打包）。 | **需六鎖**（選在無在途嘗試的空檔切換） | 換窗批次**只做換窗與設定更新，不夾帶程式重構**；切換後先跑 local `bounds()` 檢查通過再放鎖。 |
| **階段 D**<br>MiniBench 首輪大開獎 | **11/13 20:00 ～ 11/14** | 1. **整批結算與首份成績單**：執行 `resolve.py`、`score.py` 與 `report.py`，產出本輪 MiniBench 官方 Peer Score 與本地 Log / Brier Score 成對比較報告。<br>2. 依首輪完整數據與 M5 表現，三方評估 M6 主賽就緒度。 | 離線報告**免鎖** | 遵守 §4.3 結論紀律（少於 30 題只做客觀描述與工程缺陷歸因，不宣稱單線勝出或過度調參）。 |

---

## D125 Codex 量測相容性已修正（UTC 2026-11-03 13:43，PLAN v1.78）

Louis 指出 Codex 模型是 `gpt-6-astra`，並詢問如何解決量測卡住。根因是 autoload_v2 要求三線一律 verified，和 PLAN §7.7 已接受的 Codex 缺少回報模型 ID 限制衝突；正式設定原本就正確。

- 已套用 `autoload_v3`：只對 GPT tier1 的 unverified/null，檢查請求 argv 自我一致、受限 catalog、CLI／judge／runtime 指紋及正式 model+effort 驗收；保留 unverified/null，新增 model_evidence，不宣稱實際模型 ID 已驗證。其他兩線仍需 verified，mismatch 優先處理。
- 完整 **2838 passed**、相關 **179 passed**，新增 49 項回歸；Claude 親自完整複審／Gemini 獨立最終程式審查 PASS，Claude 最終文件複核 PASS。受審／受測／已部署兩檔 SHA 一致。
- UTC 13:43:35～13:43:52 六鎖互斥套用；前後各六項正式 preflight 全過、py_compile 通過，無 STOP 或未結維護。正式模型、effort、M5 與受保護設定／驗收指紋不變。
- 舊 D124 run 仍是 measurement_failed；唯讀診斷另留證據（r1 與 r2 分別標 SHA；r2 使用截至原 run 結束的帳冊副本且 runtime 與當時相符）。不回填 PASS、不新增 canary 呼叫、名額不恢復。
- R2-C 真正自動重驗仍未實作；此相容缺陷已修正，後續繼續 24 小時限制／原子預算／候選與驗收精確綁定。整批載入量測仍無 PASS，來源不能據此改為資訊性；若需要新載入實測，另定有界授權。

文件：`docs/tasks/D125-CODEX-AUTOLOAD-COMPATIBILITY.md`；審查：`reviews/*-d125-*`；ignored 操作／測試／原始審查／備份：`data/operations/d125-autoload-model-20261103/`。本段的 commit／push 結果見最新主持人摘要與 git，不從本文推定。

## D124 維護與 observe 已上線（UTC 2026-11-03 13:10，PLAN v1.77）

GPT 接手主持後完成隔離實作；Claude 親自完整審查曾提出六項阻擋，修正後 Claude／Gemini 最終兩批均 PASS。完整隔離 **2789 passed**，相關恢復／額度 **302 passed**。正式部署 12 檔與受審及測試 SHA 完全相同；固定設定、M5 開關、adapters／runner／netguard 不變。

- UTC 13:06：六鎖互斥套用 autoload_v2、autoload_maintenance_v1、forecast_pipeline_v8，含寫前意圖、衝突保留、崩潰／主持人結案、未結案前檢。
- 單次實測 run `autoload_1793711226166_c2e156fc`：三線各一次；全域檔案完整還原、restore_failures=[]、manifest closed，受保護項相符、三線兩層六項前檢全過，STOP_SUBMIT 不存在。
- **量測不是 PASS**：GPT completed=true、工具零呼叫／零違規，但既有 adapter 的 reported_model_id=None、model_check=unverified。整批維持 measurement_failed，沒有重跑，也沒有核准基準或重新分類。Claude 個人 skill 標記記 not_loaded，GPT 指示／skill 記 loaded，其餘多為 unknown；都不可藉此略過證據限制。
- UTC 13:10：互斥套用 auto_reaccept_v1 **observe** 與 run.sh 週期後單一接點（上限 120 秒），只記候選雜湊／格式代碼，沒有 enforce 或自動驗收／核准功能。部署後自然 cycle_1793711623971_6626bb2a 於 UTC 13:15:09 complete、三線 ready、errors 空；其後 observe 三線 no_candidate、model_calls=0，接點已確認。
- 新 numeric 題 46655 已由原排程完成；預測 reconciled=true、私人留言獨立讀回 found，各 POST 一次。沒有讀取任何開放題的預測內容。

證據：`reviews/claude-d124-final-code-review.md`、`reviews/gemini-d124-final-code-review.md`、`docs/tasks/D124-BATCH2-OPERATIONS.md`；ignored 操作與備份在 `data/operations/d124-batch2-20261103/`。

## M5（數值與離散題）已上線與營運守則（UTC 2026-11-03 11:38，PLAN v1.74）

**目前狀態**
- D118 MiniBench live 窗口進行中（固定截止 UTC 2026-11-10 04:00）。三線 ready。
- M5（`numeric`、`discrete`）於 11:38 上線（`config/question_types.json` 兩者為 `true`）；離散題 46654、46657 與數值題 46655 預測與留言均已讀回 `found`。
- D121 第一批放寬與 D123 都已生效。

**接手者要做的事**
1. **定期查看 cycle。**
   - 正常狀態：`complete` 或 `attention`。`attention` 若只來自「unrecognized write receipt → 讀回 forecast_ok」，屬既有流程；是非題也一樣，不必處理。
   - 要處理的：有線 `preflight_blocked`、`line_error` 持續、出現 `config/STOP_SUBMIT`，或送出結果停在 unknown 未對帳。
   - Claude 線若再被基準擋下，先看是否為新的同步來源，比照 D119／D123 的證據流程，並交 Louis 決定，不自行放寬。
2. **監看數值題與離散題的送出。** 讀回應為 `found`，留言為 `comment_ok`。若出現讀回 unknown 或預檢拒絕，只對帳、不重送，並記錄原因。
3. **待辦事項**：依上方「三方整合的分階段清理與迭代方案表」推進階段 A ～ 階段 D，以及 R2-C 第二批放寬。

**規則提醒**
- 任何要套用到正式環境的程式修改，須經 Gemini 獨立審查，**並由 Claude 親自完整審查後才套用**（Louis 2026-11-03 指示：Claude 額度不足時延後審查，不以他人審查代替）。
- 套用照互斥程序：取得 cycle／scheduler／submit／三線鎖，備份，套用，冒煙測試，失敗即還原。可參考 `data/operations/d121-m5-apply/` 的紀錄。
- D118 窗口內不得修改 `config/run_config_v2.json`、`config/quota_policy.json`、`config/cycle_campaign.json`（除非完成並透過 `budget_handoff_v2` 切換子窗口）。
- 不得改 adapters、runner、netguard（驗收指紋）。
- 撤回 M5：把 `config/question_types.json` 改回兩者 `false` 即可，會動態生效。

> 註：2026-11-03 11:38 UTC 以前的歷史交接紀錄已清理，完整決策與變更沿革見 `PLAN.md` §15～§16 及 git 歷史。
