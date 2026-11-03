# D121 第一批實作報告（GPT，待 Claude 審查）

本次僅修改指定獨立工作副本。未執行真實預測、模型 CLI、比賽查詢、送出、提交、推送或部署；測試使用合成資料。未讀取正式專案、正式 data/reports、憑證或其他對話。未修改 PLAN 正文或 AGENTS。

## Gemini 審查修正

本節記錄本輪依 Claude 決定完成的修正，優先於下方第一批歷史紀錄。這是第一批審查修正，並非中斷續作或 M5 功能開發。

### 修正內容

- **R7／R8**：`scheduler_job_finished` 保存 `budget_reason`、原預算窗口結束時間與原因對應的重評估時間。只有週桶／五小時桶不足與 `argon_personal_or_tier1_reserve` 能被新有效快照提前解除。每週次數等待依原窗口到期、token breaker 依停線紀錄的 `until`、未量測 token 基準依實際新增的第一層有效 token 量測恢復；新 `/usage` 不會解除這些非額度等待。第 2 層重新計算 `min(protected × first_estimate, 0.05)` 並扣除；五小時桶使用自己的估計單位。等待解除事件可重入，不反覆釋放同一筆等待。
- **R5**：新增 `quota.usage_fresh()`，同時核對快照帳齡與所有適用桶的 reset。credits 仍由獨立准入條件檢查，非零／未知不會被當成單純帳齡過期。`run_attempt()` 在檔案準備／重讀完成後、進入預算預約之前，再執行 `require_time(f"a {kind} attempt")`；重讀與期限檢查均在預算交易鎖外。
- **R3**：Gemini 工具擷取與 final stream 讀取分別保護；後者缺失、截斷或拋錯不清除已取得的 calls、unknown、url_events。恢復紀錄保存工具計數、網址事件及解析錯誤。工具違規仍由帳冊恢復持久 `tool_scope_violation` 停線；leak 維持既有稽核及七天兩次停線規則。新增 `recovery_no_rerun`：有違規／metaculus 證據，或 raw 有內容但解析失敗時，隔離、不可採用且不再重跑；僅解析失敗不造成持久停線，仍以 `error_type="interrupted"` 補帳。
- **R9**：`preflight()` 允許省略 cfg 或傳入 None，先載入設定，再恢復缺 timeout／hard deadline 的舊驗收紀錄；恢復結果仍為失敗，不偽造通過。
- **R1**：token breaker 冷卻截止改為最後一次造成熔斷的超標完成時間加六小時。延遲發現時若已到期，只追加已到期的計數邊界，不阻擋本次；下一次決策不重複觸發舊超標。
- **R3 舊目錄**：Claude 已接受保留無法定位的 workdir，因此下方「待確認的唯一實作例外」已結案。具名 `flab-<player>-*` 只查本線，無法辨識線別的隨機舊名稱仍保守查程序；不讀目錄內容、不猜測刪除、不發信號。此規則同時用於預測與驗收恢復。

本輪變更檔案：`src/forecast.py`、`src/scheduler.py`、`src/budget.py`、`src/quota.py`、`src/flab/orphan.py`；更新 `tests/test_d121_fail_soft.py`，新增 `tests/test_d121_review.py`、`tests/fixtures/d121_compatibility_hashes.json`，以及本報告。版本沿用待整合第一批的 `budget_v7`、`forecast_pipeline_v7`、`scheduler_v8`、`quota_admission_v2`、`quota_model_verification_v3`、`fail_soft_v1`；不另升 prompt、模型、campaign 設定或舊帳冊版本。固定政策常數未變。

### 驗證範圍與限制

- 新增 21 個合成回歸案例，涵蓋非額度等待、新快照與跨題重試、protected quota、reset／期限、部分輸出獨立讀取、隔離與停線差異、舊驗收 cfg、冷卻起算點、具名舊目錄，以及是非題／多選題的 prompt、payload、content、content_hash 位元組雜湊。
- 相容性 golden 取自副本中的第一批既有序列化／prompt 行為，相關函式本輪沒有修改；它不能替代被沙箱阻擋的 baseline 直接比對。既有 `d121_protected_hashes.json` 未改，仍驗證 adapters、runner、netguard、固定 campaign 設定、AGENTS 與 PLAN 的原始雜湊。
- 直接讀取指定 baseline 的 `src/acceptance.py` 遭 `Operation not permitted`，未繞過限制，故無法聲稱已完成 baseline 逐檔差異比對。改以副本內第一批報告、程式及既存保護雜湊核對。
- 副本內任務規格沒有「第十三節」，也沒有 `config/question_types.json`；本輪依使用者後段明確覆蓋的 Gemini 審查要求執行，未新增 M5 設定或啟用新題型。整合者若另有 M5 文件／設定，須另行提供該工作副本。
- 未執行需讀取執行資料的 TG status／publish；本輪僅開發與隔離驗證。Python 固定使用指定虛擬環境，未執行真實模型、預測或送出。

所有命令使用 `/Users/ucchengg/forecast-lab/.venv/bin/python`，cwd 為此工作副本：

| 命令（省略上述 Python 前綴） | 實際結果 |
|---|---|
| `tests/run_offline.py --synthetic-host -q tests/test_d121_review.py tests/test_d121_fail_soft.py` | **57 passed**，0.74 秒；已包含最後加強的真實離線 extractor → stream 遺失／截斷測試 |
| `tests/run_offline.py --synthetic-host -q` | **2357 passed、1 failed、2 skipped**，149.02 秒 |

唯一失敗：`tests/test_claude_plugin_inventory.py::test_pin_wrapper_fails_closed_and_preserves_arguments`。其合成 wrapper 透過系統 shasum 驗證合成執行檔，系統 Perl 載入 `/System/Library/Perl/5.34/darwin-thread-multi-2level/CORE/libperl.dylib` 被沙箱阻擋，回傳碼 134；與第一批相同，未改 wrapper 或放寬權限。兩項 skip 是 acceptance 的 loopback bind 受沙箱限制。完整輸出保存在 `.d121-checks/review-full.log`。

首輪相關模組回歸曾有 4 個失敗（兩個新測試 fixture 不完整、兩個 credits 拒絕被誤作快照重讀）；皆已修正並由上述完整回歸驗證。不能宣稱完整回歸「全部通過」：整合者仍須在正常驗收環境補跑上述 wrapper 與 loopback 測試，並執行 baseline 直接比對。沒有新增待 Louis 決定的功能政策。

## 改動與版本

| 檔案 | 實作 |
|---|---|
| `src/budget.py` | R1、R3、R5：`budget_v7`；保存准入當時的 token 基準，以新版本預約建立不可重判的判定事件；單題超標／完成但缺 token 只停該題；未完成不算超標。兩個不同題目才熔斷，到期／解除後重新計數。保留舊停線與解除事件效力、既有次數。解除工具擴及 Claude，要求 `PLAN D121`。預算 outcome 可重入，Gemini 保留額度封頂。 |
| `src/forecast.py` | R3、R4、R5、R8、R9：`forecast_pipeline_v7`；開始紀錄增加路徑、timeout、hard deadline、boot ID。取得實際線鎖並確認無程序後，稽核部分輸出、補不可採用的 interrupted、補預算、清理可定位的工作目錄；修復與重跑次數不歸零。單次 mismatch 讓出本輪且退避，rerun 不繼承 mismatch，format_repair 才繼承；重複 mismatch 有自動冷卻。額度過期最多重讀一次，位於預算交易鎖外並重新檢查准入與決策期限；事後查詢失敗留存 undetermined，不停線。 |
| `src/quota.py` | R5：`quota_admission_v2`、`quota_model_verification_v3`。查詢失敗重試一次；並行／ps 失敗不否決准入或清除實測 credits；manual_admission 同步調整。Ultra 模型證據改由單次 log 判定，額度差值不再是 verified 的必要條件。並行時不把用量／模型核對當作 verified 證據；明確 log mismatch 仍優先排除。 |
| `src/scheduler.py` | R7、R8：`scheduler_v8`。新暫時性拒絕保存為 budget_wait，不提早封存；期限到或新有效額度快照可追加 scheduler_wait_reevaluated。舊版終態與已封存結果保留。等待中的線不阻擋其他可工作的線。模型不符的有效格式輸出不算已取得答案。 |
| `src/baseline.py` | R10、R11：`baseline_v2`。祖先來源按實際 CLI 範圍分類，AGENTS 等共用來源仍影響多線。blocker 可逐線查詢；私人 logs/history/sessions 不讀內容、不進內容 hash，憑證及不安全連結仍阻擋。逐線核准不能改寫其他線／共用來源、版本或 credits。 |
| `src/claude_baseline_candidate.py` | R10：`claude_baseline_candidate_v2`。Claude 候選只檢查 Claude／共用來源的 blocker 和差異；他線未知 credits 不連帶阻擋，其他已核准來源仍逐欄保留。 |
| `src/flab/team_runner.py` | R6 程式部分：TG 問答在原有專案外暫存目錄、工具關閉模式執行，移除問答的 scheduler／三線鎖。既有 continuation 執行器未放寬。 |
| `src/acceptance.py` | R3／R9 支援：正式驗收只持受驗線鎖，開始紀錄補恢復用 metadata。 |
| `src/reconcile.py` | R3／R4／R7 支援：在已持有的線鎖內恢復中斷，冷卻使用注入時鐘，傳遞預算拒絕原因供排程分類；未改 prompt、整合或選題規則。 |
| `src/flab/fail_soft.py`（新增） | 有版本的固定政策常數，`fail_soft_v1`。 |
| `src/flab/orphan.py`（新增） | 保存開機識別／期限，使用 ps 與 lsof 確認 argv、cwd、開啟檔案無指向該次路徑；任何探查失敗皆不自動結案，從不終止其他程序。 |

`PROMPT_VERSION=forecast_v2`、`REPAIR_PROMPT_VERSION=repair_v1`、`quota_window_estimate_v1` 均未升版。正式 campaign 的三個固定設定檔未修改。整合者需依任務規格更新 PLAN §5.3、§7.2、§7.5、§7.7、§9、§15、§16；本副本不改正文。

## 固定常數

全部集中於 `src/flab/fail_soft.py`：

| 常數 | 值 | 用途 |
|---|---:|---|
| `WINDOW_S` | 86400 | token／mismatch 滾動 24 小時 |
| `BREACH_COUNT` | 2 | 兩個不同題目 token 超標；兩次實際 mismatch |
| `COOLDOWN_S` | 21600 | 六小時冷卻 |
| `MISMATCH_BACKOFF_S` | 1800 | 單次 mismatch 讓出本輪並退避 |
| `CLEANUP_GRACE_S` | 120 | 保存期限後的清理餘裕 |
| `SNAPSHOT_MAX_AGE_S` | 90 | 額度觀測最長有效時間 |
| `QUOTA_QUERY_ATTEMPTS` | 2 | 每個 slash 查詢最多兩次，受整體查詢期限約束 |
| `TIER1_QUOTA_RESERVE_CAP` | 0.05 | 額外保護第一層的總額度保留量最多五個百分點；個人保留線與本次估計不取消 |
| `TEMPORARY_RECHECK_S` | 600 | 新暫時預算等待的重新評估間隔 |

`budget.LIFT_AUTHORIZATION="PLAN D121"`，`LIFT_PLAYERS=("claude","gpt","gemini")`。手動解除仍僅供維護時段，賽中恢復不依賴它。

## 帳冊相容與恢復

- 首次新預算決策追加 `budget_policy_activated`，明列只適用新預約；舊輸出不合成新的 token 熔斷。新 `tier2_token_assessed` 綁定 attempt、當時基準／限制、事件時間與版本。
- 已存在的舊停線／舊授權解除照原效力重播，不歸零每週用量、不重開舊封存。新 token 熔斷與 mismatch 冷卻到期後排除前一輪事件。
- 中斷結果一律 quarantined、機率欄位為 null、不可採用；仍稽核部分工具／網址證據，確認暴露不得重跑，工具違規維持安全停線。結果、audit、budget outcome、清理完成事件分段可重入。
- R9 補寫的驗收結果仍是 `line_pass=false`，不偽造 PASS。若未觀測到安全違規，中斷結案不再遮蔽原有驗收證據；preflight 仍完整核對原證據 hash、runtime、CLI、正式模型及目前 baseline。沒有可用舊證據或發現安全違規時，仍需完成正式驗收。
- 開機識別無法查得時保存 null；不以此推論程序已退出，仍要求鎖與 ps/lsof 的實際證據。

## 待確認的唯一實作例外

**舊開始紀錄沒有保存 workdir，而原目錄名是不可逆的隨機值。** 新紀錄可精確清理；舊紀錄以既有規則找到 raw_dir，並保守掃描整個預設 flab-runs base，確認無相關程序後完成帳冊結案。無法唯一定位的舊 workdir 不猜測、不刪除，追加 `workdir_cleanup="legacy_path_not_recorded"`。請 Claude／Louis 確認接受這項保留，或提供舊 attempt_id→workdir 對照，才能完成該部分清理。這不會讓已結案的中斷繼續阻擋預算或重跑。

R2、R12 未納入本批。沒有部署或正式環境驗收結果。

## 測試檔案與方法

新增 `tests/test_d121_fail_soft.py`（36 個合成案例）、`tests/fixtures/d121_protected_hashes.json`、`tests/run_offline.py`、`tests/offline_workcopy.py`。

更新既有測試：`test_forecast.py`、`test_budget.py`、`test_budget_lift.py`、`test_quota_ultra.py`、`test_baseline.py`、`test_autoload.py`、`test_claude_baseline_candidate.py`、`test_scheduler.py`、`test_rollover.py`、`test_m2_supplement.py`、`test_acceptance.py`。調整舊政策斷言；D94 fixture 明確使用歷史 verifier v2，不修改正式 D94 規則；命令路徑 fixture 加上 shell quoting 以支援含空白的工作副本。

所有命令 cwd 都是此副本，Python 固定 `/Users/ucchengg/forecast-lab/.venv/bin/python`。`tests/run_offline.py` 拒絕讀取副本的執行資料／機密及呼叫真正模型 CLI，暫存資料只放在本副本 `.d121-checks/tmp`。`--synthetic-host` 僅在測試注入獨立專案根、合成祖先／目錄清單及 local 設定，不改正式原始碼或設定、不放寬沙箱。

| 實際命令（Python 路徑如上） | 結果 |
|---|---|
| `tests/run_offline.py --synthetic-host -q tests/test_d121_fail_soft.py` | **36 passed**，0.28 秒 |
| 同上專項，加跑 `tests/test_rollover.py::test_new_budget_wait_uses_fresh_week_capacity_without_reopening_sealed_answer` | **37 passed**，0.49 秒；加強確認跨週恢復只產生一份本地 final，既有獨立 publication offer 不算重複 final |
| `tests/run_offline.py --synthetic-host -q` | **2336 passed、1 failed、2 skipped**，133.33 秒 |
| `tests/run_offline.py -q` | **2220 passed、117 failed、2 skipped**，26.85 秒 |

完整隔離回歸沒有未解決的程式斷言失敗，但不能宣稱「全部通過」：唯一失敗為 `test_pin_wrapper_fails_closed_and_preserves_arguments`，合成 wrapper 呼叫 `/usr/bin/shasum` 時，系統 Perl 的 `/System/Library/Perl/5.34/darwin-thread-multi-2level/CORE/libperl.dylib` 被沙箱阻擋（exit 134）。未嘗試繞過。兩項既有 IPv4／IPv6 loopback server 測試因沙箱不能 bind 而略過。

受保護雜湊測試通過：全部 `src/adapters/*.py`、runner、netguard、三個固定設定檔、AGENTS、PLAN，共 12 檔，與工作開始時一致。可在允許系統 Perl 的正常隔離驗收環境補跑上述 wrapper 測試。

初期試跑曾有 multiprocessing spawn 重入測試 launcher 的問題；已加入 main guard，正式結果以上表最後兩次為準。原始主機失敗與最初未改碼結果比對，沒有新增未解釋的失敗（rollover 案例因新等待語意改名）；完整逐項清單如下。

## 原始主機回歸：117 項既有環境／設定失敗

- `tests/test_acceptance.py::test_gemini_denial_is_bound_to_the_tools_permission_and_target`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_cli_rejections_and_loopback_guard`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_own_page_file_is_web_content_and_other_reads_block_the_line`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_targets_come_only_from_each_tools_own_parameter`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_conversation_id_must_be_one_path_component[/etc]`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_conversation_id_must_be_one_path_component[../escape]`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_conversation_id_must_be_one_path_component[a/b]`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_conversation_id_must_be_one_path_component[]`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_declaration_requires_the_adapters_exact_agent_file[<lambda>0]`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_declaration_requires_the_adapters_exact_agent_file[<lambda>1]`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_declaration_requires_the_adapters_exact_agent_file[<lambda>2]`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_declaration_is_bound_to_the_agent_and_directory_agy_reports`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_declaration_failure_blocks_every_profile`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_manage_task_status_is_inert_and_send_input_is_not`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_symlink_in_brain_to_outside_is_a_local_read`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_conversation_files_are_copied_into_the_evidence`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_archive_alone_supports_the_canary_scan`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_gemini_agent_file_without_isolation_flags_is_no_declaration`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_acceptance.py::test_tier2_without_subagents_matches_tier1`：Gemini 權限清單建構需列出 /Users，沙箱拒絕；合成目錄清單回歸通過。
- `tests/test_adapter_gemini.py::test_build_argv_selects_minimal_agent_stream_json_and_loopback_guard`：同上：既有 adapter 的祖先目錄列舉受限；未修改 adapter。
- `tests/test_adapter_gemini.py::test_tier2_without_subagents_is_tier1`：同上：既有 adapter 的祖先目錄列舉受限；未修改 adapter。
- `tests/test_adapter_gemini.py::test_parse_tool_steps_urls_denials_and_usage`：同上：既有 adapter 的祖先目錄列舉受限；未修改 adapter。
- `tests/test_adapter_gemini.py::test_parse_flags_unexpected_tools_and_reads_outside_the_conversation`：同上：既有 adapter 的祖先目錄列舉受限；未修改 adapter。
- `tests/test_adapter_gemini.py::test_parse_soft_deny_stops_the_run_as_tool_denied`：同上：既有 adapter 的祖先目錄列舉受限；未修改 adapter。
- `tests/test_adapter_gemini.py::test_parse_errors_timeout_quota_and_invalid_output`：同上：既有 adapter 的祖先目錄列舉受限；未修改 adapter。
- `tests/test_adapter_gemini.py::test_finish_step_completes_under_its_own_step_type`：同上：既有 adapter 的祖先目錄列舉受限；未修改 adapter。
- `tests/test_adapter_gemini.py::test_pending_tool_step_is_kept`：同上：既有 adapter 的祖先目錄列舉受限；未修改 adapter。
- `tests/test_adapter_gemini.py::test_apply_quota_model_check`：同上：既有 adapter 的祖先目錄列舉受限；未修改 adapter。
- `tests/test_adapter_gemini.py::test_targets_and_page_files_use_only_each_tools_parameter`：同上：既有 adapter 的祖先目錄列舉受限；未修改 adapter。
- `tests/test_baseline.py::test_forbidden_paths_are_never_opened`：原始祖先鏈包含副本外來源／禁讀目錄，產生 blocker；合成祖先 fixture 回歸通過。
- `tests/test_baseline.py::test_no_change_is_ok_and_content_change_stops_that_line`：原始祖先鏈包含副本外來源／禁讀目錄，產生 blocker；合成祖先 fixture 回歸通過。
- `tests/test_baseline.py::test_marketplace_refresh_is_informational_but_marketplace_content_stops`：原始祖先鏈包含副本外來源／禁讀目錄，產生 blocker；合成祖先 fixture 回歸通過。
- `tests/test_baseline.py::test_marketplace_index_edge_cases_stay_conservative`：原始祖先鏈包含副本外來源／禁讀目錄，產生 blocker；合成祖先 fixture 回歸通過。
- `tests/test_baseline.py::test_interrupted_index_refresh_temporaries_are_ignored_only_at_top_level`：原始祖先鏈包含副本外來源／禁讀目錄，產生 blocker；合成祖先 fixture 回歸通過。
- `tests/test_baseline.py::test_plugin_state_only_observed_run_keys_are_informational`：原始祖先鏈包含副本外來源／禁讀目錄，產生 blocker；合成祖先 fixture 回歸通過。
- `tests/test_baseline.py::test_files_with_sensitive_sounding_names_are_hashed_by_content`：原始祖先鏈包含副本外來源／禁讀目錄，產生 blocker；合成祖先 fixture 回歸通過。
- `tests/test_baseline.py::test_settings_keys_split_into_general_and_content`：原始祖先鏈包含副本外來源／禁讀目錄，產生 blocker；合成祖先 fixture 回歸通過。
- `tests/test_baseline.py::test_codex_config_toml_is_informational_and_codex_home_is_respected`：原始祖先鏈包含副本外來源／禁讀目錄，產生 blocker；合成祖先 fixture 回歸通過。
- `tests/test_baseline.py::test_version_change_reaccepts_and_unknown_version_stops`：原始祖先鏈包含副本外來源／禁讀目錄，產生 blocker；合成祖先 fixture 回歸通過。
- `tests/test_baseline.py::test_approve_requires_clean_snapshot_and_passing_autoload`：原始祖先鏈包含副本外來源／禁讀目錄，產生 blocker；合成祖先 fixture 回歸通過。
- `tests/test_claude_baseline_candidate.py::test_only_frozen_reviewed_source_is_approved_and_others_preserved`：原始祖先來源 blocker 影響候選；合成祖先 fixture 回歸通過。
- `tests/test_claude_plugin_inventory.py::test_sync_tokens_and_refresh_times_do_not_authorize_changed_content`：原始祖先來源 blocker；合成祖先 fixture 回歸通過。
- `tests/test_claude_plugin_inventory.py::test_old_aggregate_baseline_is_never_silently_migrated`：原始祖先來源 blocker；合成祖先 fixture 回歸通過。
- `tests/test_claude_plugin_inventory.py::test_pin_wrapper_fails_closed_and_preserves_arguments`：系統 shasum→Perl 需要的 libperl.dylib 被沙箱拒絕；完整隔離回歸同樣失敗，待正常驗收環境補跑。
- `tests/test_config.py::test_all_actual_config_files_validate`：測試預期預設 local；副本固定設定是 live，不修改設定，以記憶體 local fixture 驗證。
- `tests/test_cycle.py::test_new_questions_append_resume_and_publish`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_stop_submit_does_not_stop_research`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_preflight_blocks_only_one_line`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_preflight_exception_is_line_local`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_revision_invalidates_and_restarts_tier1_without_rewriting`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_text_fallback_keeps_original_snapshot`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_structural_revision_too_late_blocks[change0]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_structural_revision_too_late_blocks[change1]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_structural_revision_too_late_blocks[change2]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_submitted_or_unknown_revision_only_records[forecast_ok]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_submitted_or_unknown_revision_only_records[intent]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_submitted_or_unknown_revision_only_records[unknown]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_submitted_or_unknown_revision_only_records[future_unknown_state]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_published_stale_result_allows_new_snapshot_and_other_questions`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_cycle_budget_defers_work_and_next_cycle_resumes`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_expired_decision_does_not_call_model`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_poll_error_is_logged_and_does_not_submit`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_started_attempt_keeps_full_timeout_and_submits_after_admission_close`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_revision_scopes_tier2_bundles_and_all_three_lines`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_text_fallback_finalizes_partial_unsealed_answers`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_deadline_reached_during_work_leaves_new_question_pending`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_supervisor_recovers_progress_into_one_cycle_summary`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_unmeasured_tier2_can_run_longer_than_cycle_interval`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_admission_close_defers_rerun_without_consuming_it[1]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_admission_close_defers_rerun_without_consuming_it[2]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_abandoned_intent_requeues_new_snapshot[False]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_abandoned_intent_requeues_new_snapshot[True]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_publish_never_offers_invalidated_snapshot`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_pending_format_repair_resumes_once_in_next_cycle[False-1]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_pending_format_repair_resumes_once_in_next_cycle[False-2]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_pending_format_repair_resumes_once_in_next_cycle[True-1]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_pending_format_repair_resumes_once_in_next_cycle[True-2]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_abandoned_unchanged_intent_does_not_invalidate_its_own_snapshot`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_finalized_submit_failure_is_recorded_and_cycle_continues[busy]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_finalized_submit_failure_is_recorded_and_cycle_continues[timeout]`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_stop_submit_records_hook_without_invoking_submit`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_cycle.py::test_callback_attention_survives_clean_final_submit`：local 合成情境受副本 live 設定／正式窗口檢查影響；合成主機回歸通過。
- `tests/test_m2_supplement.py::test_cannot_prepare_second_supplement`：既有 M2 測試要求 local，副本是 live；合成主機回歸通過。
- `tests/test_m2_supplement.py::test_real_scheduler_keeps_safety_and_only_gemini_tier1`：既有 M2 測試要求 local，副本是 live；合成主機回歸通過。
- `tests/test_m2_supplement.py::test_untrusted_sample_stops_further_jobs`：既有 M2 測試要求 local，副本是 live；合成主機回歸通過。
- `tests/test_m2_supplement.py::test_existing_weekly_cap_not_reset`：既有 M2 測試要求 local，副本是 live；合成主機回歸通過。
- `tests/test_m2_supplement.py::test_new_user_authorization_is_separate_and_cannot_repeat`：既有 M2 測試要求 local，副本是 live；合成主機回歸通過。
- `tests/test_m2_supplement.py::test_d94_cannot_skip_unfinished_predecessor_or_invent_authorization`：既有 M2 測試要求 local，副本是 live；合成主機回歸通過。
- `tests/test_m2_supplement.py::test_d94_cap_counts_failed_repair_and_rerun_starts`：既有 M2 測試要求 local，副本是 live；合成主機回歸通過。
- `tests/test_m2_supplement.py::test_d94_supervisor_bound_and_no_restart`：既有 M2 測試要求 local，副本是 live；合成主機回歸通過。
- `tests/test_m2_supplement.py::test_d94_verifier_drift_rejected`：既有 M2 測試要求 local，副本是 live；合成主機回歸通過。
- `tests/test_m2_supplement.py::test_same_job_followup_requires_trusted_initial_cost[False-repair]`：既有 M2 測試要求 local，副本是 live；合成主機回歸通過。
- `tests/test_m2_supplement.py::test_same_job_followup_requires_trusted_initial_cost[False-rerun]`：既有 M2 測試要求 local，副本是 live；合成主機回歸通過。
- `tests/test_m2_supplement.py::test_same_job_followup_requires_trusted_initial_cost[True-repair]`：既有 M2 測試要求 local，副本是 live；合成主機回歸通過。
- `tests/test_m2_supplement.py::test_same_job_followup_requires_trusted_initial_cost[True-rerun]`：既有 M2 測試要求 local，副本是 live；合成主機回歸通過。
- `tests/test_m3_w8.py::test_handoff_does_not_start_slow_tail`：預設 live 設定影響原始 cycle fixture；合成主機回歸通過。
- `tests/test_rollover.py::test_cycle_rolls_over_without_unsealing_unchanged_snapshot[False-False]`：預設 live 設定影響原始 cycle fixture；合成主機回歸通過。
- `tests/test_rollover.py::test_cycle_rolls_over_without_unsealing_unchanged_snapshot[False-True]`：預設 live 設定影響原始 cycle fixture；合成主機回歸通過。
- `tests/test_rollover.py::test_cycle_rolls_over_without_unsealing_unchanged_snapshot[True-False]`：預設 live 設定影響原始 cycle fixture；合成主機回歸通過。
- `tests/test_rollover.py::test_cycle_rolls_over_without_unsealing_unchanged_snapshot[True-True]`：預設 live 設定影響原始 cycle fixture；合成主機回歸通過。
- `tests/test_rollover.py::test_new_budget_wait_uses_fresh_week_capacity_without_reopening_sealed_answer`：預設 live 設定影響原始 cycle fixture；合成主機回歸通過。
- `tests/test_scheduler.py::test_three_lines_parallel_breadth_first_then_tier2_and_comments`：M2 scheduler 要求 local；副本固定 live 設定觸發既有拒絕，合成主機回歸通過。
- `tests/test_scheduler.py::test_restart_of_completed_campaign_never_calls_models_again`：M2 scheduler 要求 local；副本固定 live 設定觸發既有拒絕，合成主機回歸通過。
- `tests/test_scheduler.py::test_preflight_blockers_stop_before_model_calls_and_do_not_grant_waivers`：M2 scheduler 要求 local；副本固定 live 設定觸發既有拒絕，合成主機回歸通過。
- `tests/test_scheduler.py::test_no_high_acceptance_keeps_campaign_incomplete`：M2 scheduler 要求 local；副本固定 live 設定觸發既有拒絕，合成主機回歸通過。
- `tests/test_scheduler.py::test_attempt_cap_records_skips_without_blocking_other_lines`：M2 scheduler 要求 local；副本固定 live 設定觸發既有拒絕，合成主機回歸通過。
- `tests/test_scheduler.py::test_max_jobs_bounds_one_invocation_and_resume_can_continue`：M2 scheduler 要求 local；副本固定 live 設定觸發既有拒絕，合成主機回歸通過。
- `tests/test_scheduler.py::test_restart_recovers_comment_after_aggregate_was_written`：M2 scheduler 要求 local；副本固定 live 設定觸發既有拒絕，合成主機回歸通過。
- `tests/test_scheduler.py::test_twenty_question_report_is_not_real_acceptance`：M2 scheduler 要求 local；副本固定 live 設定觸發既有拒絕，合成主機回歸通過。
- `tests/test_scheduler.py::test_passive_v5_does_not_block_complete_report_or_change_ledgers`：M2 scheduler 要求 local；副本固定 live 設定觸發既有拒絕，合成主機回歸通過。
- `tests/test_scheduler.py::test_passive_v5_preserves_other_blocking_gates[gemini_window_cost_estimate]`：M2 scheduler 要求 local；副本固定 live 設定觸發既有拒絕，合成主機回歸通過。
- `tests/test_scheduler.py::test_passive_v5_preserves_other_blocking_gates[production_acceptance_current]`：M2 scheduler 要求 local；副本固定 live 設定觸發既有拒絕，合成主機回歸通過。
- `tests/test_scheduler.py::test_passive_v5_preserves_other_blocking_gates[no_unknown_started_attempts]`：M2 scheduler 要求 local；副本固定 live 設定觸發既有拒絕，合成主機回歸通過。
- `tests/test_submit.py::test_cli_stdout_is_single_json_counts_only`：CLI 測試在 live 模式需要 token，測試護欄未提供或讀取機密；local fixture 回歸通過。
- `tests/test_workdir.py::test_make_and_remove_attempt_dir_permissions`：沙箱只允許副本內寫入，與正式 helper 的專案外目錄要求衝突；合成專案根回歸通過。
- `tests/test_workdir.py::test_make_and_remove_attempt_dir_default_base`：沙箱只允許副本內寫入，與正式 helper 的專案外目錄要求衝突；合成專案根回歸通過。
- `tests/test_workdir.py::test_make_attempt_dir_rejects_existing_target`：沙箱只允許副本內寫入，與正式 helper 的專案外目錄要求衝突；合成專案根回歸通過。
