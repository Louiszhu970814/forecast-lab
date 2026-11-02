# D100／D101：有界私人留言驗收與每週次數上限（M3）

2026-11-02，Louis 在桌面對話核准：「d100 & d101 我核准了，然後 d101 照你建議去做」。Claude 主持。

## D100：有界私人留言驗收（未執行，沒有寫入）

核准範圍：trial、bot testing area 另一個未用過的是非題、synthetic 50%、最多 1 次預測加 1 次私人留言、不重試、唯讀前檢與送出相隔約 10 分鐘、結束立即回 local；遇到 4xx／429／unknown 就停，不換題再試。

| UTC | 動作 |
|---|---|
| 11:00:48 | `config/submit_mode.json` 改為 `{"mode": "trial", "approved_bot_id": 311763}` |
| 11:00:53 | `v15_probe.py prepare`（唯讀）：`PrecheckError: no eligible unused binary bot-testing question` |
| 11:01～11:02 | 唯讀列出 bot testing area 的開放題目（只看編號、題型、狀態、時間；不讀題文） |
| 11:02 | 還原為 `{"mode": "local"}` |

bot testing area（32977）開放中的題目：

| post | question | 題型 | 備註 |
|---|---|---|---|
| 43327 | 43332 | binary | 原 V15 probe 用過的題目，依規定不加第三次留言 |
| 46223 | 46450 | multiple_choice（3 個選項） | 唯一未用過、本系統支援的題目 |
| 46224／43323 | 46451／43325 | numeric | 不支援（M5） |
| 45517／43321 | 45708／43322 | discrete | 不支援（M5） |

所有請求都是 GET，經 `api_backoff_v2` 間隔；沒有 429，冷卻帳冊沒有新紀錄。沒有任何預測或留言 POST。改用多選題屬於不同的平台寫入範圍，另以 D102 請 Louis 核准（規格見 `M3-MC-PROBE-D102.md`）。

## D101：每週次數上限

Louis 選 A（照建議）：Claude 130、GPT 130、Gemini 200（防失控）。

### 設定（`quota_policy_v5`）

- `players.claude.weekly_attempts_cap`、`players.gpt.weekly_attempts_cap`：130。
- `players.gemini.weekly_attempts_cap`：200（新增；`config.py` 驗證為正整數或 null，可省略）。
- 上限由 `continuous.register()` 執行：登記每週預算窗口時，各線的 `weekly_attempt_caps` 不得超過上述值；窗口登記同時固定 `quota_policy` 雜湊。固定題集（M2）與離線工具不經過這個檢查，它們只在 local 模式執行。
- 沒有啟用任何排程或持續題集。

### 正式覆蓋率情境（事前登記，再執行）

`d101_formal_c130_c130_g200_o300_n10`（`coverage_sim_v3`，紀錄在 `data/coverage_sims.jsonl`／`coverage_sim_results.jsonl`，資料夾不進版本控制）。

- 沿用 D92 已固定的時間表與耗時樣本：UTC 10/5～10/19 主賽與 MiniBench 的 104 題，其中支援題型 67 題；來源情境 `capacityset_1793584005436_e0cdfe8d_c130_o300`。
- 上限 Claude 130、Gemini 200、GPT 130，每 7 天重置；每次呼叫額外開銷 300 秒；全部題目都升級第 2 層（escalation 1.0）；第 2 層另外保留預期新題 10 題（歷史同時開放題數的峰值）與格式修復 2 次；20 次重複，seed 10101。
- 事前登記的通過標準：支援題準時覆蓋率在 20 次重複中的第 10 百分位 ≥ 90%。

結果：

| 指標 | 數值 |
|---|---|
| 支援題準時覆蓋率 | 100%（20 次全部，p10＝1.0） |
| 全部題目覆蓋率 | 64.4%（漏答全部是 `unsupported_type`） |
| 單線單週最多嘗試次數 | 第一週 Claude 116、Gemini 110、GPT 114；第二週約 46～48 |
| `budget_skipped` | 0 |
| 超過 600 秒的週期比例 | 平均約 4% |

限制：時間表是歷史排定時間，不含實際可見性與修訂；耗時樣本來自 M2；額度只模擬次數上限，不模擬提供者端的額度耗盡。第一週的 116 次離 130 只有約 11% 餘裕，題量大於 10/5 那週時，第 2 層會先被預留邏輯擋下，第 1 層仍優先。

### 驗證

- 新增測試：Gemini 上限的型別驗證（0、負數、bool、小數、字串都拒絕；null 與省略接受）；`continuous.register()` 對三線各自超過上限 1 次都拒絕，剛好等於上限可登記。
- 隔離完整 **1627 passed**（TG 相關檔案由 GPT 另行修改中，測試副本使用已提交版本）。
- Gemini（Argon high）獨立審查 PASS，沒有提出問題。
