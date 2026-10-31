# Forecast Lab

多模型的雙盲預測實驗：Claude、Gemini、GPT 三個模型對 Metaculus 的是非題各自獨立給出機率，題目結算後以 Brier score 等指標比較準確度與校準。兩位人類可以選擇參加，但系統與計分都不依賴人類填答。本專案不涉及任何真錢。

**目前狀態**：計畫書 v0.3，第 3 回合審查中（里程碑 M0）

## 檔案地圖

| 檔案 | 用途 |
|---|---|
| `PLAN.md` | 完整計畫書，本專案唯一的真實來源 |
| `AGENTS.md` | 三個 AI 共用的協作守則（Codex 與 agy 會自動讀取） |
| `CLAUDE.md` | Claude Code 的專案設定，匯入 `AGENTS.md` |
| `.gitignore` | 排除 `.env` 等機密與暫存檔 |

## 審查流程

依序進行，每一步結束都先 commit 再交給下一位：

1. Gemini 審查 → commit
2. GPT 審查 → commit
3. Claude 依 Louis 的決定整合意見、升版 → commit
4. 重複以上步驟，直到某一回合沒有任何 blocking 意見，Louis 核准後凍結為 v1.0

每一步結束後在終端機執行：

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

> 請完整閱讀 PLAN.md 與 AGENTS.md。你是 Gemini，負責第 3 回合審查。請依 AGENTS.md 的編輯規則，只在 PLAN.md 的 §14.1 底下新增「第 3 回合審查」小節，不要修改正文。請特別做到三件事：一、對照 §15 的 D21 到 D29 與 §16 的 v0.3 變更紀錄，確認你第 2 回合的意見是否被正確整合；對未採納的部分（見 §16 v0.3 的「未採納」）若仍有異議，請說明理由；二、確認 §7.3 的 agy 規格與 §12 的 V8（`--settings` 與全域設定是合併還是取代、`"agentMode": ""` 的效果），並附上本機 help 輸出或官方文件來源；三、審查 §7.6 隔離驗收是否足以讓 M1 開始。只有仍然成立的問題才標為 blocking；若沒有 blocking 意見，請在小節最後明確寫出「本回合沒有 blocking 意見」。

（第 1、2 回合的指令已執行完畢，見 git 歷史。）

### 給 GPT 的審查指令（女友操作，或經她同意由 Claude 代為執行）

```
cd ~/forecast-lab
codex
```

進入後貼上：

> 請完整閱讀 PLAN.md 與 AGENTS.md。你是 GPT，負責第 3 回合審查。請依 AGENTS.md 的編輯規則，只在 PLAN.md 的 §14.2 底下新增「第 3 回合審查」小節，不要修改正文。請特別做到三件事：一、對照 §15 的 D21 到 D29 與 §16 的 v0.3 變更紀錄，確認你第 2 回合的意見是否被正確整合；若不同意 Gemini 第 3 回合的意見也請說明；二、確認 §7.3 的 codex 停用清單與 §5.5 的 token 與工具事件對應，並附上本機 help 輸出或官方文件來源；三、審查 §4.5 定期報告與 §7.5 採用規則是否已無漏洞、§7.6 隔離驗收是否足以讓 M1 開始。只有仍然成立的問題才標為 blocking；若沒有 blocking 意見，請在小節最後明確寫出「本回合沒有 blocking 意見」。

（第 1、2 回合的指令已執行完畢，見 git 歷史。）
