# Market Pulse 26Q4：D139 規則核對

更新：2026-11-05。Louis 已依 D76 選 bot，11/01 確認本人新帳號未參加；11/05 再指示「那你們去參加吧」及三步完成後直接開賽。工程部署另見 [D139](../tasks/D139-EXPANSION.md)，核對完成不冒稱已送出。

| 項目 | 核對結果與本專案做法 |
|---|---|
| bot／人類身分 | [本季公告](https://www.metaculus.com/notebooks/46009/market-pulse-challenge-26q4/)明許 bot 競逐同一 $7,500 獎池，每人只選 bot 或一般使用者一種。用既有 bot 311763，不以 Louis 人類帳號參加本季。 |
| 參賽入口／費用 | [通用競賽規則](https://www.metaculus.com/tournament-rules/)免費；提交賽事作品即構成參賽與接受規則。本季公告直接指向題目開始預測，未列額外報名表。Follow／is_subscribed 只代表追蹤，不當作報名回執。以正式預測受理作為實際參與證據。 |
| 計分時點 | [本季賽事完整描述](https://www.metaculus.com/tournament/market-pulse-26q4/)：各子題截止時 spot scores 加總；提前結算則取結算前一刻。頁面 12/21 為最後獎金參與日期，API project close_date 不能代替子題截止。 |
| 次數／更新 | 本季規則沒有最低更新次數，也沒有 FutureEval 每題一次的限制。[資源頁](https://www.metaculus.com/notebooks/38928/aib-resource-page/)的 Market Pulse 概述說需要持續更新；同頁 Competition Rules 明列適用 MiniBench／季賽。據本季明示的 spot 計分與未訂頻率，本窗口採首次有餘裕作答、更新 0 次的有限策略；這是工程／額度選擇，不把概述說成不存在、不宣稱已確認官方豁免或最佳競爭策略。 |
| 題型／歸屬 | 官方 API 確認 project 33131／slug。11/05 04:59Z listing 包含 12 組、69 個 child（含組內已關閉子題），不是全都開放。舊公告 65 是預定數，程式不硬編碼總題數。M5 numeric／discrete 已有，D139 補題組映射。 |
| 留言 | 本季未另外規定留言格式；採既有 bot 透明度做法，每個子題附私人推理留言，明示子題 ID；保留提交 hash／讀回。未發公開宣傳或私訊。 |
| 人類介入／來源 | 本季明允 bot；本專案沿最嚴格既有約束：不預覽開放／即將開放 bot 答案、不逐題調整、不人工提交。官方 FutureEval 規則允許公共來源；本季未加來源禁令，本專案仍排除 Metaculus 社群預測並只讀公共網頁，不交易或下注。 |
| 模型／訂閱／披露 | 本季描述未另訂模型、付費 API 或公開程式要求；沿用官方 CLI 訂閱、已同意的三線、工具隔離及受保護基準。FutureEval 的問卷／程式檢查不是自動擴張成 Market Pulse 報名表；保留可供主辦方查驗的實際紀錄。 |
| 領獎 | 通用規則由主辦方通知，可能需身分、稅務與收款資料；交 Louis 本人處理，不自動填表／付款／送個資。獎金資格與最終名次以主辦方認定，不由工程驗收推論。 |
| API／內容 | 使用正式 API 與既有 pacing／backoff；只存預測必需 allowlist 快照及規則摘要／來源，未鏡像整個網站。題文只作資料，不執行網頁要求的動作。 |

本輪沒有需要繳費的報名步驟，沒有新增人類帳號提交授權。規則核對及有限政策在任何 Market Pulse 模型輸出之前固定；原窗口、模型、prompt、統計參數不改。
