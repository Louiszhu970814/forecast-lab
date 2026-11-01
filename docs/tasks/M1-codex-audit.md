# M1：codex 工具組裝原始碼稽核（GPT 線證據範圍，決策 1 乙案）

- 日期：2026-11-01
- 執行者：Claude（協調者）
- 目的：GPT 第 2 次審查指出 `codex debug prompt-input` 不列出 session 開始時才加入的工具（實測看到 goals），D60 的工具宣告因此不完整。Louis 選擇乙案：先讀 codex 原始碼，確認工具怎麼組裝，再決定證據範圍。
- 來源：`openai/codex` tag `rust-v0.166.0`，commit `6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`（與本機 `codex-cli 0.166.0` 同版）。只做 sparse checkout 後閱讀，未建置、未執行。以下路徑都相對於 `codex-rs/`。

## 1. 每回合的工具從哪裡來

`core/src/tools/spec_plan.rs:121` `build_tool_router` 依序合併五個來源，最後由 `finalize_tool_router`（同檔 :370）處理 code mode 與 tool_search：

| 來源 | 程式位置 | prompt-input 是否涵蓋 |
|---|---|---|
| 核心工具：shell、MCP resource、utility、collaboration | `spec_plan.rs:1186` `add_core_tool_sources` | 是（同一段程式） |
| MCP 伺服器工具 | `spec_plan.rs:152` `mcp_handler_cache.append_mcp_tools` | 是 |
| 擴充模組（extension）提供的工具 | `spec_plan.rs:351` `extension_tool_executors`、`:1641` `append_extension_tool_executors` | **部分**（見第 2 節） |
| 用戶端傳入的 dynamic tools | `spec_plan.rs:1589` | `codex exec` 不傳（`exec/src` 內沒有任何 `dynamic_tools`） |
| 由供應商執行的 hosted 工具（hosted web_search） | `spec_plan.rs:692` `hosted_model_tool_specs` | 否，但只會是網路搜尋 |

## 2. prompt-input 為什麼看不到某些工具

- `cli/src/main.rs:2150` 的註解寫明：「Debug prompt construction bypasses app-server and needs its own extension registry.」prompt-input 自己建的擴充註冊表只裝 3 個：`multi_env_copy_file`（功能開啟時）、`git_attribution`、`skills`。
- `codex exec` 走 in-process app-server（`exec/src/lib.rs:737`、`:1028`），使用 `app-server/src/extensions.rs:52` `thread_extensions` 的註冊表。這份註冊表另外安裝了 goal、history_notes、agent_message_board、guardian_v2、memories、mcp、web_search、image_generation 與 queue。
- 結論：在本機執行的函式工具中，prompt-input 漏掉的只有「只由 app-server 安裝的擴充模組提供的工具」這一類，goals 就是其中之一，與 M1 實測現象一致。由供應商執行的 hosted 工具（第 1 節）同樣不在探測內，但只會是網路搜尋。

## 3. 每個會提供工具的擴充模組，在我們的設定下是否啟用

會提供工具的擴充模組（實作 `ToolContributor`）共 9 個，其中 skills 與 multi_env_copy_file 在 prompt-input 中也會安裝；只由 `codex exec` 安裝的有 7 個：goal、history_notes、agent_message_board、guardian_v2、memories、web_search、image_generation。queue、git_attribution、mcp 擴充不提供工具。`tool_suggest` 不是擴充，是核心工具（`spec_plan.rs` `add_core_utility_tools`），列在表後的核心工具說明。

| 擴充模組 | 提供的工具 | 啟用條件（原始碼） | 預設 | 我們的設定 |
|---|---|---|---|---|
| goal | create/get/update_goal | `Feature::Goals`（`app-server/src/extensions.rs:96`） | 開 | `--disable goals`（D60） |
| history_notes | 歷史與筆記工具 | `Feature::TokenBudget` 且 `token_budget.use_history_notes_extension`（`ext/history-notes/src/extension.rs:52`；`core/src/config/mod.rs` `resolve_token_budget_config`） | 關 | 另加 `--disable token_budget` |
| agent_message_board | 留言板工具 | `Feature::AgentMessageBoard` 且 `Feature::MultiAgentV2`，且非 ephemeral（`core/src/agent_message_board.rs:68`） | 關 | 另加 `--disable agent_message_board`、`--disable multi_agent_v2`；本來就以 `--ephemeral` 執行 |
| guardian_v2 | 對話歷史查詢工具 | `Feature::GuardianConversationHistoryTools`，且只在 guardian 審查子 session（`ext/guardian-v2/src/sync_reviewer/mod.rs:115`） | 關 | 另加 `--disable guardian_conversation_history_tools`；`approval_policy="never"` 不會產生審查 |
| memories | 記憶工具 | `Feature::MemoryTool` 且 `memories.use_memories` 且 `dedicated_tools`（`ext/memories/src/extension.rs:48`） | 關 | 另加 `--disable memories` |
| multi_env_copy_file | 跨環境複製檔案 | `Feature::MultiEnvCopyFile`（`app-server/src/extensions.rs` 安裝前檢查） | 關 | 另加 `--disable multi_env_copy_file` |
| image_generation | imagegen | 擴充本身只看供應商；`spec_plan.rs:1657` 再以 `Feature::ImageGeneration` 過濾 | 開 | `--disable image_generation`（原有） |
| skills | skills.list、skills.read | 技能目錄啟用工具時（`ext/skills/src/tools/mod.rs:57`） | — | prompt-input 也安裝此擴充；在無使用者設定的 mirror 中探測不到 |
| web_search | web.run | 獨立網路搜尋可用且 `web_search` 不是 disabled（`spec_plan.rs:1641` 起） | — | 這是 GPT 線要用的網路工具；format_repair 設 `web_search="disabled"` |

核心工具的開關（都在 prompt-input 涵蓋的程式路徑內）：shell 與 unified exec 已關；MCP resource 工具只在有 MCP 伺服器或 code-mode-only 時加入（D60 為 direct 模式，且 `--ignore-user-config` 下沒有伺服器）；apply_patch 只在模型目錄有 `apply_patch_tool_type` 時加入（D60 已移除）；view_image 已關；collaboration 由 `agents.enabled=false` 移除；`tool_suggest` 需要 apps 或 plugins 的候選，兩者已關，現在也明確關閉。

其他輸入：本機沒有 `/etc/codex`（無 managed config）；`--ignore-user-config` 略過 `~/.codex/config.toml`；工作資料夾內沒有 `.codex/`。

## 4. 結論

1. 在 codex 0.166.0 的這個 commit 上，GPT 線一個回合可能拿到的工具 = prompt-input 探測到的工具 + 只由 `codex exec` 安裝的擴充模組工具 + 由供應商執行的 hosted 工具。擴充模組工具在我們的設定下只剩 web.run；hosted 工具只會是網路搜尋（web.run 可用時不再提供）。
2. 因此「prompt-input 探測 + 本稽核 + 全面的事件監控」可以視為完整的工具宣告。監控規則是：任何非網路工具的事件都讓該次答案作廢並停線。
3. 為了不依賴預設值，把第 3 節的 6 個預設關閉的功能，以及預設開啟、但在 apps 與 plugins 關閉後沒有候選的核心工具 `tool_suggest`，共 7 個，明確寫進 `--disable`（`src/adapters/gpt.py` `DISABLED_FEATURES`）。

## 5. 殘餘風險與重驗條件

- 本稽核假設本機執行檔由 `rust-v0.166.0` 建置（官方發行版）。CLI 版本一變，就重做本稽核與 §7.6 驗收。
- 模型目錄每次執行都固定一份，並記錄 `catalog_entry_sha256`；目錄內容變動時，`experimental_supported_tools` 等欄位可能加入新工具，W8 會比對這個指紋。
- 伺服器端的行為不在原始碼內（例如 web.run 實際怎麼抓頁面），仍以事件監控把關。
