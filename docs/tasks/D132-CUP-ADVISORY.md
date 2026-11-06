# D132 — 兩題 Cup 人類賽參考

來源：Louis 2026-11-04 本輪桌面指示；PLAN §8／§9、D76／D79／D132。GPT 主持及整合。

## 範圍

先答榜單及主賽開始時間，再依 Cup 截止時間選最急兩題：post 45330 / question 45519（台北 11/7 07:59）、post 46106 / question 46343（11/9 14:00）。每題 Claude／Gemini／GPT 各一次，共六次已准入初始嘗試，不自動修復／重跑／提交。未新增窗口或排程，沿用 D118 原 caps／終點及 D105 的 accounting_start。

## 實作

`advise.py` 只接受這兩個 post，GET 題目與跨 bot 賽事排除索引；不傳送個人憑證、沒有送出入口。六把生產鎖只在無 MiniBench 開放工作時取用，每題後釋放。三條線沿用原 F.run_attempt，分開存放答案與原始資料；只有 metadata 計入共用預算與安全帳。

`budget_v8` 同時替 bot 保留 22 次未來兩層／修復容量與既有 Argon 保留公式（上限 5%），v7 token 評估相容。unknown 計費並阻斷；completed/no-start 才結束。`forecast_pipeline_v9` 從 metadata guard 延續 quota／模型不符的退避；確認洩漏或工具／credits 違規會停共用線。新線上檔案及測試必須與第二輪審查 SHA 相符。

## 審查與驗證

第一輪兩份 REQUEST CHANGES 均保留。第二輪完整 source packet 包含依賴原始碼與修補理由；相關離線 241 passed（含真實 forecast engine 的假 adapter、全額度／handoff、恢復／停線、排程）。來源在 ignored `data/operations/cup-advisory-20261104/`，部署前由主持人讀取兩份完整審查，不把測試結果當成審查 PASS。正式套用、前後固定檔雜湊、六項 preflight 與每題實際完成數以操作帳冊為準。

## 操作與未知狀態

開始前執行 `project_control.py status`。建立專屬 `data/advisory/<batch>` 後，分別執行 `advise.py --batch <path> --post 45330` 及 `--post 46106`。重複題目／模型與超過六次的准入被拒。沒有成功結果也不能自動重跑。

若程序中斷，以 `advise.py --batch <path> --recover` 對帳。未過期或仍有程序的 start 保留 unknown；過期且程序已消失才重用原有證據稽核。完成對帳不代表新授權或允許重跑。若 metadata JSONL 損壞，停止並保留原檔，由主持人依 raw／start／outcome 證據修復；不能刪除歷史、把 unknown 改成免費、直接 resume 或略過閘門。

## Bot 時程核對

本輪官方 API 核對 10 題正式送出均 closed 未 resolved，最早預定台北 11/13 20:00、最晚 11/14 12:59。舊交接的 11/6 21:00 不代表這批正式題的首題結算。MiniBench 榜單由官方頁面 Leaderboard 查看；截至本輪查詢 current round leaderboard 無 entries／userEntry，尚無可報的實戰排名或準確率。

FutureEval 主賽尚未啟用、無固定開始日。現行 M6 在 MiniBench 結算與 M5 就緒度評估後由 Louis 逐場核准；11/13–14 是評估階段，不能承諾該日正式開跑。

## 正式套用紀錄

UTC 2026-11-04T15:46:19.194Z 完成六鎖部署，五檔 SHA 與受審／受測版本相同，16 項受保護檔未變，三線兩層六項正式 preflight 均 ok、無 waiver。Claude r2 PASS；Gemini r2 程式 PASS、r3 補充證據後 overall PASS。首次手動套用因 PATH 指到全域 CLI 而前檢拒絕，已完整還原；改用既有 launchd/bin 固定版本後才套用成功，沒有重新驗收或升級。

手動執行必須沿用排程的 PATH，例如：

```sh
PATH="$PWD/launchd/bin:$PATH" .venv/bin/python src/advise.py --batch data/advisory/cup_two_1793805103443_30c607fc --post 45330
```

Claude r2 N2（帳本寫入異常時的額外復原加固）與其他非阻擋項保留為後續工作；本輪不額外重構已通過的版本。若帳務結案互相矛盾，先停線保留證據，由主持人離線對帳，不能直接重跑。

## 首批實際結果（UTC 2026-11-04T16:03:23.411Z 核對）

兩題均已完成三線各一次：第一題 UTC 15:54:27 完成整合，第二題 15:59:43 完成整合；六份皆 valid／adoptable、兩份 aggregate ready、沒有缺線。私人報告：`reports/advisory/2026-11-04_45519.md`、`2026-11-04_46343.md`。本文件與共用進度不記錄開放題機率或理由。

共用 metadata 共 6 admitted、6 finished(started=True)，每線 2 次，pending 0、advisory line events 0；六筆准入均保留 22 次 bot 容量，Gemini 的 Argon 保留皆為 0.05。原預算帳依既有計次累積至 Claude 26/130、Gemini 25/200、GPT 24/130；這不是所有個人／開發訂閱使用量的估算。16 項受保護檔全部仍相符。正式 forecasts／aggregates／submissions 沒有人類 attempt 混入。

人類報告保留既有 suspected_leak 標記與來源品質附註，人工來源核對追加於私有 `source_checks.jsonl`，沒有改寫機率、旗標或調整 bot 設定。沒有任何人類預測／留言 POST；未 commit／push。

## D134 英文報告與 Gemini 最後留言（2026-11-04 UTC）

Louis 要求兩份最終報告改為英文，之後人類賽留言用英文，並指定最後留言由 Gemini 撰寫後自行貼上。已新增 `reports/advisory/2026-11-04_45519_en.md`、`2026-11-04_46343_en.md`；各報告最前面是 Gemini 留言，亦各自另存同題的 `_comment_en.md` 供直接複製。原中文報告保留，不覆寫歷史；manifest 追加英文修訂與留言來源。

Gemini 透過專案固定官方 agy 1.4.12、`gemini-4-argon-medium`／medium，單次只做兩篇英文編輯，沒有新研究或預測。沿共用閒置鎖、既有 quota 准入與 `useG1Credits=false`，專案外暫存目錄、無搜尋／讀寫／指令／委派工具；CLI 日誌回報 Gemini 4 Argon (Medium)，完成後 credits 仍為 0。消耗屬本次編輯呼叫，另記實際前後 quota；不冒充新的 D132 預測樣本。首次準備因 QuotaSnapshot 序列化錯誤在模型啟動前停止，修正既有 `to_dict()` 用法後才開始唯一一次編輯，保留零模型呼叫的失敗紀錄。

主持人核對後修正四處事實性措辭：紐西蘭多選整合是幾何平均與 2% 平滑，不是 log-odds；COP 的 ENB 紀錄是本報告核對，不聲稱 ENB 更正過紀錄；澳土為責任分工，不暗示兩國共同實體主辦；不同截止市場只記為不能直接比較，不聲稱模型完全排除。其餘 Gemini 留言文字保留。機率、英文原始模型摘要、原有外部來源連結與品質限制均核對保留；英文版不是更新預測。

完整追溯在 ignored `data/operations/d134-english-advisory-20261104/` 的輸入、原始 Gemini 回覆／CLI 證據、`writing.jsonl`、`verification.jsonl`；輸出索引追加於原 batch `reports.jsonl`。沒有任何人類預測或留言 POST，未改正式預測程式／設定／窗口或排程；未 commit／push。後續依 PLAN §8／D134 延續英文報告、Gemini 最後留言、本人判斷手動提交的偏好。

## D142 本人提交回報與後續安排（2026-11-05）

Louis 在桌面對話回報「這兩題我已經交上去了」：紐西蘭組閣 post 45330／question 45519 與 COP31 超時 post 46106／question 46343 均改列本人回報已手動提交。回報追加在 `data/advisory/manual_submissions.jsonl`；實際提交時間及留言是否送出未提供，也未以本人帳號讀回平台確認。上方「沒有任何人類預測／留言 POST」是程式執行紀錄，不否定 Louis 後來的本人操作；原報告與 manifest 不改寫。

其餘 Cup 題目依 Louis 安排，在各題截止前兩天再研究，完整規則見 PLAN §8／D142。依 UTC 2026-11-05T14:20:21.984Z 盤點，最近三題對應的開始時點為：維德角選舉 UTC 2026-11-11T23:59:00Z（台北11/12 07:59）、GTA VI UTC 2026-11-16T00:00:00Z（台北11/16 08:00）、西班牙席次 UTC 2026-11-27T08:00:00Z（台北11/27 16:00）；接手時須重查最新截止。這是本人工作安排，未建立定時提醒或自動研究排程，未延長現有預算窗口。
