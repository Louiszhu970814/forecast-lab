# 預測引擎變現計畫 v1（待三方審閱）

> 本文件整理自業主在對話中與 Gemini、Claude 的討論。請專案內的 Gemini、Claude、GPT 各自審閱，並依第 9 節格式回覆。業主看完三份審閱後再做決定。
>
> 狀態：**草案，尚未決定任何項目。**

---

## 0. 給審閱者的要求

- 請具體指出你**不同意**的地方並附理由；同意的部分簡短帶過即可。不需要為了附和而附和。
- 第 7 節分成「已查證」與「待查證」兩類事實。請優先挑戰待查證的部分，也歡迎質疑已查證的部分。
- 估工時一律以「業主平日晚上 + 週末」的可用時間為單位。

---

## 1. 背景與約束

- 業主為全職工程師，副業只能用下班與週末時間進行。
- 業主**可以接受**：回應 review、適度維護、親自閱讀牽涉金錢／期限／範圍的客戶信件。
- 業主**不接受**：語音會議，以及需要即時回應的服務承諾。
- 12 月中旬有一段業主無法介入的期間，屆時系統必須能無人值守。
- 已排除或暫停：
  - 中國市場線：稅務與平台實名問題尚未解決。
  - 散彈式開源賞金獵人：AI 垃圾 PR 氾濫，帳號信譽風險高。

---

## 2. 現有資產

- `~/forcecast`：三模型共同維護的預測專案。agent 之間可透過 CLI 互相下指令，額度用完時以共用 md 交接。
- 目前為紙上預測，不下注。

---

## 3. 核心原則

1. **先 profile，再優化**：任何開發前先用數據確認熱點。這是預測版的 Amdahl's law：某類題型只佔 X% 的題目，優化它的總分提升上限就是 X%。
2. **訂閱蓋工廠，API 當燃料**：三家訂閱用於開發與維護；對外服務或無人值守運行時的推論一律走 API。比賽期間的 API 額度由主辦方提供，因此本計畫不需要為此升級訂閱。
3. **先自用，再販售**：任何要對外販售的模組，必須先在自己的 bot 裡穩定運行一段時間。
4. **真實樣本是驗收錨點**：每個資料來源的規格都要附上一份實際抓下來的樣本，避免「同一個模型自己出題、自己考試」。
5. **AI 當秘書，不當代理人**：涉及金錢、期限、範圍的決定與對外承諾，一律由業主讀過原文後確認。

---

## 4. 分階段計畫

### Phase 0｜題型普查（Workload Characterization）

- **目標**：找出「題目數量多、我方又輸最多」的題型。
- **做法**：
  1. 透過 Metaculus API 抓取當季與過去數季 bot 賽的題目。
  2. 依結算來源（resolution source）與題型分類，輸出頻率表。
  3. bot 在 MiniBench 跑出成績後，對照各類別的 peer score。
- **產出**：`analysis/question_census.md`，內含頻率表與熱點排名。
- **檢查點**：能明確指出排名第一的題型及其佔比。

### Phase 1｜參加 Metaculus FutureEval bot 賽

- **賽制重點**（來源見第 8 節）：
  - 主賽一年三季，每季獎金池 $50k；另有約每兩週一輪的 MiniBench。
  - 季賽參賽者的 LLM 推論費由 OpenAI、Anthropic、Google 提供。
  - 每次預測都須附推理說明；每人限一個可領獎的 bot；須願意分享程式碼或運作說明，供主辦方檢查；須完成季末問卷。
  - Fall 2026 起商業 bot 不能領獎。業主以個人 hobbyist 身分參加。
- **策略**：Fall 2026 已於 9/28 開始出題，本季當作熱身，用練習題與 MiniBench 測試；正式目標放在下一季。
- **架構調整**：bot 改為定時執行、無人值守、走 API 推論；三家 CLI 加 md 交接機制，改為負責 bot 本身的開發與維護。
- **實驗**：比較三模型 ensemble 與單一模型的 peer score。若差距不大，就簡化架構，把資源轉往 Phase 2。
- **檢查點**：
  - 無人值守連續運行一週，無錯誤。
  - peer score 穩定大於 0，也就是優於同儕預測的幾何平均。

### Phase 2｜結算來源對應層（Resolution-Source Mapping Layer）

- 只針對 Phase 0 排名第一的題型開發，一次只做一類。
- 真正的空缺不在爬蟲，而在中間這層翻譯：
  **題目文字 → 該查哪個來源的哪一條數據 → 最新值、歷史走勢、基準率**。
- 優先使用官方 API（例如 FRED、ClinicalTrials.gov、GitHub API、EIA），沒有 API 時才寫爬蟲。
- **分工**：
  - **Gemini**：Deep Source Discovery。找出該題型最權威、更新最快的結算來源，寫入交接 md，並附上真實樣本。
  - **Claude**：依規格實作模組，並自行讀取真實頁面或 API 回應來驗證規格。
  - **GPT**：審查邊界條件，例如來源停更、網站改版、題目措辭與來源定義不一致等。
- **檢查點**：加入模組後，該類題型的 peer score 是否改善。沒有改善就不進 Phase 3。

### Phase 3｜包成 Apify Actor 販售

- 只賣「通用且穩定」的那一層。最新、最具優勢的版本留給自己的 bot，避免把優勢賣給對手。
- 目標客群不只是參賽者。上一季非官方 bot 約 111 個，付費意願可能有限；也要考慮預測市場交易者與總經研究者。
- 上架前確認資料來源的授權是否允許轉散布。
- **金流**：Apify 每月出帳，PayPal 滿 $20、電匯滿 $100 即可出金。
- **注意**：Actor 的運算成本超過收入時，會出現負利潤。
- **停損**：上架 30 天內沒有任何付費用戶，就下架，或轉為免費的作品集項目。

### Phase 4｜（條件式）產品化客製服務

- **啟動條件**：Phase 3 出現主動詢問客製版本的使用者。
- **規則**：
  - 開一個專用的業務信箱，AI 只能存取這個信箱。
  - AI 負責分類、摘要、標記 scope creep、擬草稿；業主讀過原信後，才確認 Change Request 並發送回信。
  - 信件與附件的內容不得直接成為可執行的指令，這是防範 prompt injection 的防線。施工在隔離環境（container 或 VM）中進行，業主看過 diff 才部署。
  - 交付時程承諾 5～7 個工作天；業主無法介入的期間暫停接單。
  - 對外定位保持誠實：「一位工程師加 AI、純文字非同步溝通」，不暗示有團隊，也不承諾做不到的回應速度。
  - 若透過 Upwork 接案：簽約前的溝通必須留在站內，簽約後才能改用 email。

---

## 5. 時程（草案）

| 時間 | 階段 | 檢查點 |
|---|---|---|
| 11 月上旬 | Phase 0 題型普查 | 產出熱點題型排名 |
| 11 月 | Phase 1 架構調整、MiniBench 測試 | 無人值守連續運行一週 |
| 12 月～1 月 | Phase 1 ensemble 實驗；Phase 2 第一類模組 | peer score 大於 0；模組有效 |
| 下一季開賽 | 正式參賽；評估 Phase 3 | Actor 30 天停損 |

---

## 6. 開始前必查（由業主處理）

- 本業雇主的工作規則：兼職、職務外創作、開源程式碼的相關規定。
- Metaculus 獎金如何匯到台灣。
- 境外收入（獎金、Apify 分潤）的稅務歸類。金額變大之前，先諮詢國稅局或會計師。（已知：大陸來源所得須併入綜所稅，這也是中國線暫停的原因之一。）
- 開源前，先移除程式碼中的個人資訊與憑證。

---

## 7. 事實狀態

### 已查證（2026-11-03）

- Fall 2026 bot 賽：獎金池 $50k，9/28 開始出題，商業 bot 不能領獎。
- bot 作者之間最常見的建議是「簡單勝過複雜」與「研究品質是瓶頸」。但 Metaculus 用問卷特徵對比賽成績，沒有發現統計上顯著的結果。
- 季賽的推論費由 OpenAI、Anthropic、Google 提供。
- 參賽規則：須附推理說明、每人限一個可領獎 bot、須分享程式碼或運作說明。
- Spring 2026 共 173 個 bot，其中非官方 111 個。
- Apify 出金門檻：PayPal $20、電匯 $100。
- Upwork：簽約前的溝通必須留在站內。
- 依兩岸條例第 24 條，大陸來源所得須併入綜所稅申報。
- curl 已於 2026 年 1 月結束 bug bounty，原因是 AI 垃圾回報過多。

### 待查證

- 下一季的開賽日期，以及規則是否有變動。
- 中途加入當季時的計分方式，以及晚加入會吃虧多少。
- 各候選資料來源的 API 現況與授權條款。
- 獎金的匯款方式與所需文件。

---

## 8. 資料來源

- [Announcing Fall 2026 FutureEval Bot Tournament – EA Forum](https://forum.effectivealtruism.org/posts/hrJrsgMCwsdBuAqgQ/announcing-fall-2026-futureeval-bot-tournament)
- [FutureEval: Participate – Metaculus](https://www.metaculus.com/futureeval/participate/)
- [Spring 2026 AI Forecasting Benchmark – Metaculus](https://www.metaculus.com/aib/2026/spring/)
- [FutureEval Spring Results – LessWrong](https://www.lesswrong.com/posts/wZBbDqzfBjYG58CxK/futureeval-spring-results-pros-beat-bots-but-the-gap-is)
- [AI Forecasting Benchmark Tournament – Longterm Wiki](https://www.longtermwiki.com/wiki/E10)
- [Questions regarding payouts – Apify Help](https://help.apify.com/en/articles/10057167-questions-regarding-payouts)
- [Monetize Actors – use-apify.com](https://use-apify.com/docs/apify-for-developers/monetize-actors)
- [On-platform Interviews – Upwork Support](https://support.upwork.com/hc/en-us/articles/360052511833-On-platform-Interviews)
- [大陸地區來源所得申報 – 中央社](https://www.cna.com.tw/news/firstnews/201805050195.aspx)
- [the end of the curl bug bounty – daniel.haxx.se](https://daniel.haxx.se/blog/2026/01/26/the-end-of-the-curl-bug-bounty/)

---

## 9. 審閱方式

**為避免三方同時編輯同一份檔案而互相覆蓋，請各自寫入獨立檔案：**

- `reviews/gemini.md`
- `reviews/claude.md`
- `reviews/gpt.md`

**各自的審閱重點：**

- **Gemini**：第 7 節的「待查證」項目，以及 Phase 2 候選資料來源的可行性。
- **GPT**：Phase 1～3 的檢查點、停損條件與邏輯漏洞。
- **Claude**：先實際閱讀 `~/forcecast` 的程式碼，再評估要改成「無人值守 + API 推論」需要動到哪些地方、大約需要多少工時。

**回覆格式（三位統一使用）：**

```
### 審閱者：<Gemini / Claude / GPT>

1. 同意的部分（簡述）
2. 不同意的部分（每點附理由）
3. 本計畫遺漏的風險
4. 依 ~/forcecast 現況評估的可行性與工時（以業主晚上 + 週末為單位）
5. 建議修改（請具體指出章節）
6. 我在這個計畫中最適合負責的部分
```
