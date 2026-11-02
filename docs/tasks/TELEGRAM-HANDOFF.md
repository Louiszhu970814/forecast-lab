# TG 決策接手修正（2026-11-02）

範圍：Louis 回報 17:30～17:32 台北的 TG 問題並要求修正、Gemini 審查。後續明確分工為 D100／D101 由 Claude 執行，本輪 GPT 只處理 TG。不能把本文件或 Telegram 上的問答當成啟動預測工作、重新核准數值或啟動任意 agent 的授權。

## 根因證據

- `launchctl list org.forecast-lab.telegram`：調查時 PID 17771、啟動時間台北 09:31，已載入且運作；LaunchAgents 只有 Telegram 範本已安裝。D91 部署紀錄亦在 `TELEGRAM-TEAM.md`。不是 TG launchd 未安裝。
- 共用帳冊：D100-PROBE 在 09:15:50.317Z 選 B，D101-CAPS 在 09:16:02.706Z 選 A，兩者 `actions={}`，只有 `decision_accepted`，沒有開始事件。
- 舊 `Board.choose()` 只接受 `open`；選過一次變成 `waiting_coordinator` 就拒絕任何修改，並共用不存在／已處理／到期的錯誤訊息。
- 舊 `TeamWorker` 只消費有已登記 action 的 `queued`，不消費 `waiting_coordinator`，不會喚醒桌面 Claude。D95 的主持人是 Claude，指定負責人不是啟動程序。
- 05:34:30.995Z 主持人摘要早於兩項選定紀錄；原問答雖重讀帳冊，仍將已過時摘要與最新核准混在一起。沒有條件的結構化映射、完成里程碑、接手 CLI 或狀態變更通知。

## 修改

`team_chat_v2`／`telegram_ops_v4`：

- 接受未開始、未到期決策的改選；版本遞增、舊 update 冪等回傳目前選擇，保留歷史。開始與改選使用同一跨程序帳冊鎖，開始後不能改選。不存在、到期、開始、完成、失敗、不明、結案、帳號不符分別拒絕。
- 主持人以指定版本 `claim` 記錄實際接手。回覆列負責人、觸發器、阻塞、開始估計或無法估計及下一步。未登記自動續作明示要在電腦啟動指定主持人；問答模型沒有執行權限。
- `ask` 可原子登記每個選項的 `handoffs`（負責人／下一步／阻塞／條件 ID）。本機有證據的 `milestone` 事件使已選條件轉可接手或排隊，TG 通知一次；已改選的不釋放舊工作。舊的未開始決策可補 handoff，不能改選項或 action。
- 選項本身的範圍已核准，不重問同一核准；主持人摘要與新決策衝突時以新決策為準。服務與執行器健康每 30 秒入帳，90 秒過期，與桌面主持人在線情況分開。
- 問答開始時重讀帳冊，附模型資料截點；模型生成至入 outbox 期間若相關狀態已改，丟棄舊答案並回傳最新帳冊。長訊息按 UTF-16 上限分段，避免截掉選項與時間。
- 接手／完成／條件通知走原有配對帳號 outbox，重啟去重；單次本機 `notify` 用於使用者要求的說明，不重新啟用一般定期推播。原 hash 檢查、離線續作、工具隔離、模型額度／鎖、未知執行不重跑皆保留。

操作見 [telegram.md](../telegram.md)。D100／D101 的選擇、接手與結果由 Claude 更新，本輪不修改這兩項真實任務帳冊。

## 驗證與審查

- 第一輪相關 91 passed；新增原子登記條件案例後 92 passed。
- 隔離完整 1638 passed in 82.01s（副本只含原 HEAD 加 TG 修改；不包含 Claude 同時的容量變更）。這是在最後一項原子登記測試之前的完整結果；最終結果追加於下方。
- Gemini 官方 agy／Argon high 第一輪達 300 秒上限，未取得結論；不能記為通過。第二輪改為 Argon medium、縮小原始碼包，結果待追加。只提供本輪原始碼，專案外空白目錄、無工具除 `finish`、乾淨環境、共用互斥鎖，沒有預測資料或 token。

## 最終結果

- Argon medium 第二輪 PASS，兩項 non-blocking：未選／接受前的 handoff 可能重複通知；已到期條件仍轉 ready。均已修正並補回歸測試。第三輪定點複查 PASS，無剩餘具體問題。
- 最終相關 **94 passed**，隔離完整 **1641 passed in 77.52s**。副本為本輪開始時 HEAD 加最終 TG 程式／測試，沒有混入 Claude 同時的 D101／D102 修改；未對 Claude 工作作結案判定。
- 審查證據（ignored 私有工作檔）：`scratch/tg-handoff-review/2026-11-02T11-10-04.014Z/review.json`，packet SHA-256 `e3642a450f568f7bc8912ad50d526ab7184b51f4e2817e312d75b908686661ff`；複查 `2026-11-02T11-13-17.747Z/review.json`，packet SHA-256 `08a05c27c7ad3c578f3f743c99c62e6f9ef34a383df10e0384b9b84eeb1978fd`。
- Gemini Flash low 官方 CLI 合成問答實測（11:14:50.827Z）：使用過時的「仍需核准」摘要和較新的選 A 事件，正確回答已核准 130／130、未開工可改 B、Claude 是負責人但沒有自動觸發、開始時間未定、預測 launchd 與 TG 交接分開。`scratch/tg-handoff-review/smoke.json`。未傳真實預測資料。
- 部署前確認 TG 無排隊／執行中的問答與續作，無執行中的 acceptance／autoload 觀測。僅以 SIGTERM 正常重載既有 `org.forecast-lab.telegram`；新 PID 43521、`workgroup_mode` 為 `telegram_ops_v4`，11:15:36.960Z 心跳在線。未安裝或啟動預測排程。
- 保留 Claude 最新 M3 摘要後追加 TG 完成進度；依 Louis 明確要求發送單次 TG 修復說明。最終回執時間記於下方。
- PLAN 只在 GPT §14.2 新增整合建議；正文／§15／§16 與 git commit 由既有整合者 Claude 處理。未變更 `AGENTS.md` 或 Claude 同時的容量／驗收檔案。

- 單次修復通知的 TG Bot API 送出回執：2026-11-02T11:17:26.765Z，`outcome=sent`、`telegram_ops_v4`。
