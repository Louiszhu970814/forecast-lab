# D91：TG 共用工作對話

依 Louis 2026-11-02 指示，取代舊版獨立開發佇列入口。沿用私人對話，點名三家查專案進度、待決事項並回覆決定，Louis 主動 polling，無定期進度推播。

交付：`telegram_ops_v3`、`team_chat_v1`；共用進度／決策 JSONL、正式 CLI 中階模型無工具問答、私人帳號與新鮮度驗證、問題去重／每日上限、主執行緒不被模型阻塞、續作雜湊／一次啟動／中斷不重跑，以及本機主持人的 `project_control.py`。

操作與邊界見 `docs/telegram.md`。不把手機決策當作能直接續接桌面 Codex 對話；預先登記的離線續作可自動執行，其餘清楚列為待主持人接手。未新增待決事項、預測或平台請求。

相關 105 passed、隔離完整 1473 passed；Gemini 3.8 Flash Medium 獨立原始碼審查 PASS，未提出 blocking。測試覆蓋帳號、新鮮度、雜湊漂移、決策與問題重送、重啟中斷不重跑、自然提問路由、問答期間查詢／決策回應、歷史推播遷移與實際合成續作只執行一次。真實中階 CLI 僅使用合成 M3 進度與惡意指令文字測試，不使用真實預測；GPT/Gemini 各一輪、Claude 兩輪（第二輪確認 StructuredOutput 事件，離線回放驗證解析修正）。

部署：2026-11-02T01:31:06Z 既有 TG LaunchAgent 重載後已記錄 `telegram_ops_v3`／workgroup_mode，Bot API 指令更新成功、schedule=off，launchd 顯示 running。啟用前沒有隔離驗收／審查／執行中舊開發任務；正式預測服務未啟用。共用進度已發布，當時沒有待決事項；沒有額外 TG 推播或平台請求。
