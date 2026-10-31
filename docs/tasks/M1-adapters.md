# M1 adapter 共同規格（W4、W5、W6 共用）

> 對應 `PLAN.md` §5.3、§5.5、§7.3、§7.5、§7.7。三個 adapter 都依這份介面實作，W8 的流程與 W3 的驗收都從這裡呼叫。

## 介面（`src/adapters/base.py`，由協調者在 W1 合併後建立；W4、W5、W6 沿用，不另外改介面）

```python
@dataclass(frozen=True)
class AdapterRequest:
    player: str                 # "claude" | "gemini" | "gpt"
    profile: str                # "tier1" | "tier2" | "format_repair" | "acceptance"
    prompt: str
    schema: dict                # 正式 JSON Schema（W8 提供 output_schema_v2.json）
    workdir: Path               # flab.workdir.make_attempt_dir() 建立的暫存資料夾
    raw_dir: Path               # 原始輸出存放處（stdout、stderr、事件、log）
    model: str
    effort: str
    timeout_s: int

@dataclass
class UrlEvent:
    url: str | None
    host: str | None
    state: str                  # "attempted" | "blocked" | "opened" | "unknown"
    source: str                 # 例如 "tool_use"、"search_result"、"permission_denied"

@dataclass
class AdapterResult:
    argv: list[str]             # 實際執行的參數（prompt 以 "<stdin>" 表示）
    run: RunResult              # flab.runner.run 的結果
    cli_version: str | None
    completed: bool             # CLI 正常完成（PLAN §7.5 的第一個條件）
    final_text: str | None      # 模型最後的文字輸出
    structured: dict | None     # CLI 回報的結構化輸出（未經主程式驗證）
    reported_model_id: str | None
    model_check: str            # "verified" | "unverified" | "mismatch" | "undetermined"
    usage_raw: dict | None
    usage: dict                 # input_tokens、output_tokens、reasoning_tokens、cached_tokens；沒有就是 None
    tool_call_counts: dict | None
    url_events: list[UrlEvent]
    denied_actions: list[dict] | None
    subagent_count: int | None
    error_type: str | None      # PLAN §5.5 的 error_type
    error: str | None
```

每個 adapter 模組提供：

- `build_argv(req) -> list[str]`：只組參數，不執行。
- `parse(req, run_result) -> AdapterResult`：只讀 `req.raw_dir` 中的檔案，不呼叫 CLI；`run_result` 可以是 `None`（例如以 fixtures 測試時）。
- `run(req) -> AdapterResult`：呼叫 `flab.runner.run`，再解析。
- `cli_version() -> str | None`。

已提供的輔助函式：`url_host(url)`、`is_metaculus_host(host)`（metaculus.com 與其所有子網域）、`model_check_from_ids(requested, reported, aliases)`。各 adapter 請直接使用，不要各自重寫。

## 共同規定

- 參數一律組成清單，不經過 shell；CLI 支援時，prompt 由標準輸入傳入。
- 不讀取、不解析任何 CLI 的登入憑證。`flab.runner.run` 的 `keep_env` 只保留登入必要的變數，並在模組中註明理由。
- 解析器要能處理：正常完成、逾時、CLI 錯誤、工具被拒、額度用完（樣態於 M2 補齊），以及輸出不完整。無法判斷的欄位設為 `None`，不猜測。
- `model_check`：CLI 回報的模型 ID 與指定的模型相符時為 `verified`，不符時為 `mismatch`，沒有回報時為 `unverified`；agy 依 PLAN §7.7 的額度判斷（由 W5 實作）。
- 單元測試使用 `tests/fixtures/<player>/` 中精簡過的樣本，不呼叫 CLI。樣本只保留結構與必要欄位，長文字改成短的替代文字；不得含有任何 token、cookie 或帳號資訊。

## 各 profile 的工具設定

| profile | 用途 | 工具 |
|---|---|---|
| `tier1` | 第 1 層預測 | 只有搜尋與讀網頁，封鎖 metaculus.com |
| `tier2` | 第 2 層 | `tier1` 加上子代理（只有通過 §7.6 驗收的線才開放；M1 先實作參數，預設關閉） |
| `format_repair` | 格式修復（PLAN §7.5） | 關閉所有工具 |
| `acceptance` | 隔離驗收（W3 呼叫） | 與 `tier1` 相同的限制，模型改用 `run_config_v2.json` 的 `acceptance` 設定 |
