# Forecast Lab

多模型協作的預測專案：Claude、Gemini、GPT 三個模型先各自獨立研究 Metaculus 比賽的題目，再由程式整合成一個預測，以機器人帳號參加 Metaculus 的 FutureEval 機器人預測賽，目標是在訂閱額度內最大化比賽分數。機器人賽用剩的額度，可以為人類賽提供參考預測。本專案不下注、不交易。

**目前狀態**：計畫書 v1.8（v1.0 已於 2026-10-31 凍結）。已接續 W8、M2 整合／稽核，以及[匿名摘要與第 2 層本地流程](docs/tasks/M2-W3.md)；全套 469 個離線測試通過。跨題預算排程與實機驗收尚待完成，最新進度與接續步驟見 [交接紀錄](docs/tasks/HANDOFF.md)。

## 檔案地圖

| 檔案 | 用途 |
|---|---|
| `PLAN.md` | 完整計畫書，本專案唯一的真實來源 |
| `AGENTS.md` | 三個 AI 共用的協作守則（Codex 與 agy 會自動讀取） |
| `CLAUDE.md` | Claude Code 的專案設定，匯入 `AGENTS.md` |
| `.gitignore` | 排除 `.env` 等機密與暫存檔，以及含題目內容的 `data/`、`reports/` |
| `docs/tournament_checks/` | 比賽規則核對紀錄（摘要） |

## 審查流程

依序進行，每一步結束都先 commit 再交給下一位：

1. Gemini 審查 → commit
2. GPT 審查 → commit
3. Claude 依 Louis 的決定整合意見、升版 → commit
4. 重複以上步驟，直到某一回合沒有任何 blocking 意見，Louis 核准後凍結為 v1.0

每一步結束後執行以下指令（由人類在終端機執行，或由 Louis 指示 Claude 代為執行；審查者本身不執行 commit）：

```
cd ~/forecast-lab
git add -A
git commit -m "review: <審查者> r<回合>"
```

### 給 Gemini 的審查指令（Louis 操作）

```
cd ~/forecast-lab
agy
```

進入後貼上：

> 請完整閱讀 PLAN.md、AGENTS.md 與 docs/tournament_checks/futureeval.md。你是 Gemini，負責第 5 回合審查。請依 AGENTS.md 的編輯規則，只在 PLAN.md 的 §14.1 底下新增「第 5 回合審查」小節，不要修改正文。請特別做到三件事：一、對照 §15 的 D44 到 D52 與 §16 的 v0.5 變更紀錄，確認你第 4 回合的意見是否被正確整合；對 §16 v0.5 中分歧裁決的部分若仍有異議，請說明理由；二、確認 §7.3 中 agy 第 2 層的最小自訂 agent 規格與 §7.7 的額度判斷是否與本機 help、changelog 一致，並附上來源；三、以 docs/tournament_checks/futureeval.md 的規則摘要為準，檢查 §3.4～§3.6 的送出流程與試驗期設計，是否還有違反比賽規則的路徑。只有仍然成立的問題才標為 blocking；若沒有 blocking 意見，請在小節最後明確寫出「本回合沒有 blocking 意見」。

（第 1～5 回合的指令已執行完畢，見 git 歷史。）

### 給 GPT 的審查指令（女友操作，或經她同意由 Claude 代為執行）

```
cd ~/forecast-lab
codex
```

進入後貼上：

> 請完整閱讀 PLAN.md、AGENTS.md 與 docs/tournament_checks/futureeval.md。你是 GPT，負責第 5 回合審查。請依 AGENTS.md 的編輯規則，只在 PLAN.md 的 §14.2 底下新增「第 5 回合審查」小節，不要修改正文。請特別做到三件事：一、對照 §15 的 D44 到 D52 與 §16 的 v0.5 變更紀錄，確認你第 4 回合的 5 項 blocking 是否已解決，以及 non-blocking 意見是否被正確整合；若不同意 Gemini 第 5 回合的意見或 §16 v0.5 的分歧裁決，也請說明；二、確認 §7.3 與 §7.6 中 codex 第 2 層（`--enable multi_agent`）的設定與子代理驗收，並附上本機 help 或官方文件來源；三、審查 §3.6 的送出契約與狀態機、§3.3 的稽核繼承與 §10 M3 的完成標準，是否足以讓 M1 開始。只有仍然成立的問題才標為 blocking；若沒有 blocking 意見，請在小節最後明確寫出「本回合沒有 blocking 意見」。

（第 1～5 回合的指令已執行完畢，見 git 歷史。）
