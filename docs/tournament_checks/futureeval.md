# 比賽規則核對：FutureEval 主賽與 MiniBench

> 核對日期：2026-10-31
> 依據：Louis 以瀏覽器取得並提供的兩份原文（metaculus.com 拒絕自動抓取）
> - [FutureEval Bot Tournament Resources Page](https://www.metaculus.com/notebooks/38928/futureeval-resources-page/)（2026-10-29 版）
> - [Official Rules for the AI Forecasting Benchmark Tournament](https://www.metaculus.com/notebooks/28751/q1-ai-forecasting-benchmark-rules/)（2026-10-12 版）
>
> 本檔只記錄摘要與短引文；Metaculus 使用條款禁止複製或儲存大量內容。對應 `PLAN.md` §3.7。

## 比賽形式

- 主賽每季一輪（9 月、1 月、5 月開始），獎金池 5 萬美元，約 300～400 題；題型包括是非、數值、離散與多選。部分題目會同步到主站，以便與社群預測比較。
- 2026 秋季賽的 project ID 為 `fall-futureeval-2026`（33121）；MiniBench 為 `minibench`；測試區為 `bot-testing-area`（32977），每種題型各一題，不計分。
- MiniBench：每兩週一輪，獎金 1 千美元，約 60 題，自 2026-06-01 起由 LLM 自動產生與結算；多數題目在前幾天開出；獎金與主賽同時發放（一年三次）。
- 題目在隨機時段開出，每題開放 3 小時：「Several questions can be open at the same time (usually 5 or fewer, and on rare occasions up to ~10)」。
- 每題只送一次：「Bot makers should only submit one forecast per question in these bot-only tournaments」。需要重送的測試，請到測試區或其他允許重送的場地進行。
- 計分：spot peer score，以對數為基礎的 proper score，「only the last forecast made before the question closes counts」。新加入者從 0 分開始；剩約 50 題以上時加入，仍有機會進入獎金區。

## 人類介入（No human in the loop）

- 可以使用「any resources that are generally available to human forecasters」，包括其他平台與 Metaculus 本身公開的預測。
- 社群預測可以參考，但不得盲目照抄（不論以程式或請 LLM 照抄），以致預測持續貼近社群預測。
- 不得為了幫助機器人而在其他平台建立市場或題目。
- 機器人的預測不得受到持有人對該題的個人預測影響。
- 「A bot maker cannot preview how their bot forecasts on open or upcoming questions and then update their bot based on that preview.」
- 不得因為不喜歡答案而重跑。開發與測試請用比賽以外的開放題目；已關閉的比賽題目或主站題目也可以用來測試。
- 不得由人類複製貼上送出；人類可以每天手動啟動機器人，但預測與留言必須由機器人經 API 送出。
- 機器人可以在比賽期間更新，但建議不要在比賽中開放中或即將開放的題目上測試。

## 帳號與隊伍

- 每位參賽者（或隊伍）只有一個可以領獎的機器人。「your first bot is your primary bot」，之後建立的都是次要機器人，不能領獎。
- 使用條款另有較舊的寫法：一個人不得同時擁有或控制一個以上啟用中的機器人帳號。本專案只使用一個機器人帳號。
- 可以組隊：一個隊伍只送一個主要機器人，獎金付給代表隊伍的實體，或平分給登記的隊員；隊員不能另外參賽。
- 建立機器人帳號的人須取得每位協助者的明確同意，並提供一份規則。
- 人類帳號必須由人類建立；領獎時須由人類表明身分。

## 留言

- 所有作答的題目都必須留言；留言設為私人（`is_private=true`），題目關閉後會被定期公開。
- 正式規則：留言須包括預測的顯示（template 使用 `included_forecast=true`）。

## 領獎

- 須提供程式碼或運作說明，並同意 Metaculus 人員的檢查（展示程式碼、在新題目上示範、回答問題）。運作說明須包括比賽期間的重大更新。
- 「No purchase or payment is necessary to enter or win.」須年滿 18 歲。
- 以 Ramp 銀行轉帳付款，須先確認自己的國家在 Ramp 支援清單中；須驗證身分、國籍與居住地；稅與手續費由收款人負擔。
- 季內全部題目結算、問卷與驗證完成後才發放。

## 使用條款中與自動化相關的規定

- 不得以人工或自動方式 crawl、scrape 或 spider 任何頁面；不得複製或儲存大量內容。
- 不得共用帳號。
- 被 Cloudflare 擋下時，機器人不應尋求繞過的方法；不要讓 AI 寄信給 Metaculus。

## API 資料

- 開放中的題目一律可以取得題目內容。已關閉的題目，只能取得自己預測過的題目的內容與結算值。
- API 只在約 50 題提供社群預測；另有 Bot Benchmarking Access Tier（需申請），用於訓練與評估。

## 免費資源（本版未使用）

- AskNews：註冊的機器人作者免費使用，每月 1,000 次，只能用於本比賽，每個隊伍限一個帳號。
- LLM 額度：透過 OpenRouter 發放，需申請並經挑選。

## 對本專案的影響

| 規則 | PLAN.md 對應 |
|---|---|
| 每題只送一次 | §3.4 第 9 點、§3.6（D44） |
| 不得預覽後修改、不得重跑 | §3.6、§4.5；試驗期只在試驗場地作答（D45） |
| 留言私人、附預測，關閉後公開 | §3.6、§5.6 |
| 第一個機器人就是主機器人 | §2 |
| 不得爬取頁面、不得儲存大量內容 | §3.3、§5.1（D50、D51） |
| 只能取得自己預測過的題目的結算值 | §3.2、§4.1 |
| 協助者的同意與規則副本 | §2（D51） |

## 2026-11-01 補記：MiniBench 各輪計分與實戰練習

核對時間：2026-11-01T13:28:24Z，GPT；本次可直接讀取官方頁面，以下補充不改寫前次核對紀錄。

- 官方將 MiniBench 定義為連續舉辦、各有 1,000 美元獎池的兩週比賽；目的包含快速回饋及降低新參賽者門檻。[資源頁](https://www.metaculus.com/notebooks/38928/futureeval-resources-page/)
- 官方計分以該場比賽題目的加權 Peer score 總和決定排名與獎金。據此，各輪分開計算：上一輪負分不轉入下一輪，也不抵扣下一輪獎金；歷史成績仍保留。MiniBench 與季賽同時發獎，不代表合併各輪分數計獎。[計分 FAQ](https://www.metaculus.com/help/scores-faq/#tournament-scores)、[賽制及發獎時程](https://www.metaculus.com/notebooks/38928/futureeval-resources-page/)
- 官方允許賽中更新機器人，可用已關閉的比賽題目或合規試驗題測試；仍禁止人工預覽 open/upcoming 輸出後針對性修改、因不喜歡答案重跑，且每題只提交一次。需要反覆提交的功能測試先在允許重送的試驗場地完成。[參賽與更新規則](https://www.metaculus.com/notebooks/38928/futureeval-resources-page/#competition-rules)
- 「至少先試驗 14 天」是本專案原有的工程門檻，並非上述官方規則的參賽資格。Louis 已決定改為必要技術驗收通過即進 MiniBench；GPT 已記入 PLAN §14.2，待 Claude 整合正文及 §15／§16。此決定沒有取消必要驗收或開放個別預測的人為介入。
