# Forecast Lab

多模型加人類的雙盲預測實驗：Claude、Gemini、GPT 三個模型與兩位人類，對 Metaculus 的是非題各自獨立給出機率，題目結算後以 Brier score 比較校準度。本專案不涉及任何真錢。

**目前狀態**：計畫書 v0.1 審查中（里程碑 M0）

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

> 請完整閱讀 PLAN.md 與 AGENTS.md。你是 Gemini，負責第 1 回合審查。請依 AGENTS.md 的編輯規則，只在 PLAN.md 的 §14.1 底下新增意見，不要修改正文。請特別做到三件事：一、逐一回答 §12 的 Q1 到 Q8 並表態；二、補上 §5.3 與 §7.3 中 agy 的無互動參數、輸出格式與權限設定，並附上你查到的官方文件來源；三、指出設計中的漏洞，或你不同意的地方。

### 給 GPT 的審查指令（女友操作）

```
cd ~/forecast-lab
codex
```

進入後貼上：

> 請完整閱讀 PLAN.md 與 AGENTS.md。你是 GPT，負責第 1 回合審查。請依 AGENTS.md 的編輯規則，只在 PLAN.md 的 §14.2 底下新增意見，不要修改正文。請特別做到三件事：一、逐一回答 §12 的 Q1 到 Q8 並表態，若你不同意 Gemini 在 §14.1 的意見也請說明；二、補上 §5.3 與 §7.3 中 codex exec 的搜尋設定、`--output-schema` 與權限參數，並附上官方文件來源；三、指出設計中的漏洞，或你不同意的地方。
