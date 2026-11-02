# D107 A3：提前解除第 2 層週期熔斷（追加式解除紀錄）

規格：Claude（主持）。實作：GPT（Codex 開發沙箱）。審查：Claude，之後 Gemini（預算相關，D107 A4 規定須獨立審查）。
Louis 於 2026-11-02 核准 D107 A3（女友同意對 GPT 線額度的影響）。

## 背景

- `data/budget_events.jsonl` 目前有兩筆仍生效的 `budget_upgrade_stop`：GPT `weekly_tier2_token_breaker`（至 UTC 2026-11-08T00:00:00Z）與 Gemini `weekly_tier2_argon_breaker`（至 2026-11-08T12:11:16Z，D97 前寫入，D97 規定不追溯解除）。
- `budget.py` 在 tier 2 時找最後一筆仍未到期的 stop 並拒絕；沒有任何解除機制。歷史紀錄不能刪除或改寫。

## 要做的改動（`budget_v4` → `budget_v5`）

1. 新事件 `budget_upgrade_stop_lifted`，追加到同一個 `budget_events.jsonl`：`player`、`lifted_reasons`（被解除的 stop reason 清單）、`lifted_stop_ats`（被解除的 stop 的 `at` 清單）、`authorization`（固定字串，例如 `"PLAN D107 A3"`）、`at`、`version`。
2. tier 2 的 `paused` 判斷：某筆 `budget_upgrade_stop` 若有同 player、`at` 較晚、且 `lifted_stop_ats` 含該 stop 的 `at` 的解除紀錄，就不再生效。只解除紀錄中明列的 stop；之後新寫入的 stop 照常生效。
3. **解除前的違規不再重新觸發**：`breach_reason`（`weekly_tier2_token_breaker`）只看在該 player 最近一次解除紀錄的 `at` 之後開始的 tier 2 紀錄。沒有解除紀錄時行為完全不變。每題的 `question_tier2_token_capacity`、token 倍數上限、每週次數上限、Gemini 的 credits／五小時桶／未知版面等檢查全部不變。
4. CLI：`budget.py lift-tier2-stops --player gpt --player gemini --authorization "PLAN D107 A3"`（若 `budget.py` 目前沒有 CLI，就新增最小的 `main()`）。在 `transaction(root)` 內執行；只解除該 player 目前仍生效的 stop；沒有可解除的 stop 時拒絕並不寫入；重複執行不會產生第二筆。輸出只有 player、被解除的 reason 與時間，不含其他資料。
5. `VERSION` 改為 `budget_v5`。

## 不能做的事

- 不刪除、不改寫任何既有紀錄；不改 `quota_policy.json`、`run_config`、D101 每週次數上限與 M2 報告判定。
- 不改送出、讀回、prompt、模型參數與整合。不修改 `config/`、`PLAN.md` 正文、`AGENTS.md`。不呼叫網路。

## 測試（離線，沿用 `tests/test_budget.py` 的 fixture）

- 有生效 stop 時 tier 2 被拒；寫入解除紀錄後同一題 tier 2 准入（其他條件都滿足時）。
- 解除前已超過 token 上限的題目不會讓 `weekly_tier2_token_breaker` 重新觸發；解除後新產生的超限會寫入新的 stop 並生效。
- 解除紀錄只影響明列的 stop、只影響同一 player；Gemini 的解除不影響 GPT，反之亦然。
- 每題 token 上限仍生效；tier 1 行為不變。
- CLI：沒有可解除的 stop 時拒絕；重複執行冪等；authorization 為空時拒絕。
- 沒有解除紀錄的既有測試全部保持通過。

完成後執行 `tests/test_budget.py` 與其他相關測試，再跑完整測試；回報實際結果，沙箱目錄限制造成的失敗分開列出。
