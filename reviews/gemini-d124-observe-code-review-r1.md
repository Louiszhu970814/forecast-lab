### D124 Phase 2（Observe-Only）程式碼審查報告

#### 一、 審查結論（Verdict）
* **狀態**：**PASS**（0 項阻斷問題 / Blockers）
* **適用範圍**：D124 Phase 2 **Observe-Only（僅觀測）** 版本。此版本刻意不包含任何 `enforce`、自動重驗執行（`autoload` / `acceptance`）、自動核准基線（`baseline.approve`）、回滾（revert）或清除停機（clear stop）之 API，亦不宣稱 R2 自動恢復已完成。
* **審查聲明**：本報告完全基於所提供之 Source Packet 進行靜態程式碼與架構審查，未執行任何外部工具、測試命令或實際部署，亦不捏造任何未發生的核准或測試執行結果。

---

#### 二、 阻斷項（Blockers）與未來 Enforce 需求之區分

1. **目前 Phase 2（Observe-Only）阻斷項**：
   * **無（0 Blockers）**。所有安全邊界、無內容日誌（content-free logging）、鎖定機制、120 秒硬性超時、D118 活躍視窗閘門與 D123 相容性均已正確實作。

2. **與未來 Phase 3（Enforce）需求之明確區分（非本階段 Blocker）**：
   * **無自動執行與寫入路徑**：`src/flab/auto_reaccept_policy.py` 固定 `MODE = 'observe'`，且 `src/auto_reaccept.py:tick()` 於入口強制檢查 `if P.MODE != 'observe': raise ValueError('this release supports observe only')`。全流程固定 `model_calls: 0`、`baseline_approved: False`，且絕不寫入 `data/acceptance.jsonl`。
   * **24 小時 Token 預算預留與執行期防護**：由於本階段完全不觸發模型呼叫或重驗流程，未來 Enforce 階段所需的 24h rolling token 預算預留、自動執行失敗冷卻（cooldown）及自動回滾（revert）在 Observe-Only 階段刻意不實作，完全符合分階段發布規範。

---

#### 三、 實際程式碼證據核對（Actual Evidence from Source Packet）

1. **Allowlist 掃描邊界與檔案安全檢查（`src/flab/auto_reaccept_policy.py`）**
   * **嚴格白名單**：`ALLOWLIST` 僅限 8 個 Markdown 來源（`claude.global_instructions`、`claude.skills`、`codex.global_instructions`、`codex.skills`、`agy.gemini_md`、`agy.agents_md`、`agy.skills`、`agy.rules`），且僅接受 `source.kind in ('file', 'dir')`。任何插件、設定檔、Hooks、MCP、Agents、Memories 或上層目錄（`ancestor:*`）變更均排除在外（若發生變更會觸發 `source_outside_allowlist` 並使 `format_eligible` 為 `False`）。
   * **目錄與檔案系統防護**：
     * 逐層檢查 `path.parents` 之 `lstat().st_mode`，若任一存在之上層路徑非一般目錄（如 symlink）即回傳 `DENY / unsafe_parent`。
     * 拒絕 symlink 來源（`symlink`）、隱藏目錄/檔案（`.` 開頭）、`B.PRIVATE_DIR` 私密目錄、`B.CREDENTIAL_FILE` 憑證檔名、非 `.md` 副檔名（`unsafe_filename`）。
     * 以 `os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK` 開啟檔案並透過 `os.fstat` 驗證必須為一般檔案（`S_ISREG`）、無硬連結（`st_nlink == 1`）、無執行權限位元（`st_mode & 0o111 == 0`）。
     * 嚴格限制單檔上限 `MAX_FILE_BYTES = 64 KiB`、單一來源總上限 `MAX_SOURCE_BYTES = 512 KiB`、最多 `MAX_FILES = 64` 個檔案與 `MAX_DIRS = 256` 個目錄。
     * 讀取後再次比對 `file.lstat()` 之 `(st_dev, st_ino, st_size, st_mtime_ns, st_mode)`，並重新執行 `B.inspect_source(source, home)` 驗證 `digest == saved.get('hash') == current.get('hash')` 且 `B._same_protected(saved, current)` 為真，防止觀測期間的 TOCTOU 競態（`file_changed` / `snapshot_changed`）。
   * **內容語法與標記過濾（`text_reason`）**：
     * 拒絕非 UTF-8、控制字元、UTF-8 BOM、`@` 匯入語法（`IMPORT` 正則）、敏感標記與金鑰前綴（`MARKERS`：含 `metaculus`、`FLABAUTO-`、`FLABCANARY`、`PRIVATE KEY`、`sk-`、`ghp_`、`github_pat_`）。
     * Frontmatter 僅允許出現在 `*.skills` 來源下的 `SKILL.md`，且使用無 YAML 評估器的極窄單行純量語法（僅接受 `name` 與 `description` 鍵，拒絕任何集合/錨點/多行/型別字面值或未閉合引號）。

2. **無內容中繼資料日誌（Content-Free Metadata Logging）與隱私保護**
   * `P.scan()` 與 `P.text_reason()` 僅輸出固定狀態碼（`ALLOW` / `DENY` / `UNKNOWN`）、固定原因代碼（reason codes）、檔案數、位元組數與 SHA-256 雜湊，絕不將檔案內文或例外訊息字串（`str(exc)`）寫入回傳結構或 `data/auto_reaccept.jsonl`。
   * 觀測器與新增測試（`tests/test_auto_reaccept.py`）均未讀取任何真實預測 payload（如 `forecasts.jsonl`、`questions.jsonl` 等）。

3. **Scoped Candidate Hash 綁定與 Ledger 去重（`src/flab/auto_reaccept_policy.py`、`src/auto_reaccept.py`）**
   * **完整綁定（`binding`）**：`candidate_sha256` 透過 `hash_obj(binding)` 綁定 `policy_version`、`player`、`kind`、`replaces_baseline`（當前核准基線之 `record_id`）、該 `player` 與 `common` 所有非 `INFORMATIONAL` 來源之保護指紋（`B._fingerprint(r)`）、CLI `version`、`context`（含執行檔路徑與 SHA-256、Claude `2.1.316` pin SHA-256、`runtime_fingerprint`、`judge_version`、Tier 1/2 模型與 effort、`production_evidence_ok`、`production_record_ids`、GPT `catalog_sha256` 與 `catalog_changed`）以及 `scan_sha256`。
   * **範圍隔離與去重**：不納入 `taken_at` 時間戳、其他選手（player）來源或 `INFORMATIONAL` 類別來源；寫入 `data/auto_reaccept.jsonl` 前於 `with transaction(root):` 內透過 `B.latest_approved` 再次確認父基線未被取代（否則回傳 `superseded`），並以 `read_complete(log)` 檢查 `candidate_sha256` 是否已存在，確保相同候選狀態只記錄一次。

4. **Busy Locks、120 秒硬性超時、週期後單次觸發與 D118 閘門**
   * **鎖定機制**：`tick()` 先取得非阻塞全域鎖 `F.Lock(root/'auto_reaccept.lock')`，再逐線取得非阻塞選手鎖 `F.Lock(root/f'forecast.{player}.lock')`；遇鎖定衝突捕獲 `F.PreflightError` 並回傳 `'status': 'busy'`，絕不阻塞或干擾正常預測與驗收作業。同時檢查 `config/STOP_SUBMIT` 與 `autoload.unclosed(root/'acceptance.jsonl')`。
   * **120 秒 Deadline**：`main()` 設定 `signal.SIGALRM` 與 `signal.setitimer(signal.ITIMER_REAL, 120)`，超時拋出繼承自 `BaseException` 的 `ObservationDeadline`。因為繼承自 `BaseException`，不會被內層 `except (OSError, ValueError, TypeError, KeyError):` 誤吞，能正確觸發 `with transaction(root):` 與 `with F.Lock(...):` 的退出清理並回傳 `{'status': 'timeout', 'mode': 'observe', 'model_calls': 0}`。
   * **D118 活躍視窗閘門**：`main()` 在啟動計時與 `tick()` 前先呼叫 `W.require_active(mode, campaign, PROJECT_ROOT/'config', utc_now())`；若非活躍 `live` 視窗或設定不符，立即靜默回傳 `0`，不在 D118 合約外產生觀測紀錄。
   * **週期後單次觸發（`launchd/run.sh`）**：僅於 `.venv/bin/python "$target" --campaign config/cycle_campaign.json` 正常結束（釋放所有 cycle 鎖）後執行一次 `.venv/bin/python src/auto_reaccept.py tick || true`，且以 `|| true` 確保觀測器任何異常均不影響 `cycle` 工作本身的退出狀態。

5. **與 Production Preflight 及 D123 之相容性**
   * `observe_line()` 對 Gemini 線使用 `quota.snapshot(timeout_s=20)` 並在 `quota.observation_fresh` 時傳入 `gemini_credits`（符合 D54 對 `gemini` 線 `remaining_credits == 0` 之要求），對 `claude` 與 `gpt` 線則不進行多餘的 `agy` 配額查詢。
   * `P.scan()` 在目錄走訪時依 `source.exclude` 排除子路徑（如 D123 中 `claude.skills` 排除 `("synced",)`），使 `P.scan()` 計算出的 `source_hash` 與 `B.inspect_source()` 完全一致。

6. **測試套件確定性修復（`tests/test_scheduler.py`、`tests/test_quota_ultra.py`）**
   * `tests/test_scheduler.py`：在 `test_three_lines_parallel_breadth_first_then_tier2_and_comments` 中引入 `threading.Barrier(3)`，消除高負載下依賴 `time.sleep(.005)` 造成的執行緒排程競態假失敗。
   * `tests/test_quota_ultra.py`：在 3 個呼叫 `m2_report.build` 的測試中明確傳入固定時鐘 `now=NOW`，消除真實系統時間推進導致固定 fixture 視窗過期的時鐘耦合問題。
   * **測試證據引述**：依 Source Packet 記載，初步 36 項 observer 單元測試及後續 168 項相關測試已通過，完整測試套件正在執行中（本審查不另行宣稱執行測試）。

---

#### 四、 非阻斷建議與未來 Phase 3 演進項目（Proposals — Non-Blocking）

1. **複合來源之 `general_hash` 指紋細化（建議於 Phase 3 前評估）**：
   * 在 `src/flab/auto_reaccept_policy.py:139` 中，`binding['protected_sources']` 對所有 `r['category'] != B.INFORMATIONAL` 的來源直接記錄 `'fingerprint': B._fingerprint(r)`。
   * 對於 `category == CONTENT` 但 `general_category == INFORMATIONAL` 的複合來源（如 `claude.plugins`、`claude.marketplace_index`、`claude.plugin_state`），`B._fingerprint(r)` 的第二個元素為 `general_hash`（例如 `lastUpdated` 或 `usage-rows-queued`）。
   * **影響評估**：當沒有非資訊類變更時，`kind == 'none'` 會直接提早返回，不會寫入任何紀錄；但若 `claude` 線正好存在未決的候選變更（例如 `claude.skills` 修改），且期間 `general_hash` 發生刷新，會因 `binding` 內的 `general_hash` 改變而產生新的 `candidate_sha256`。建議未來若需嚴格將 `candidate_sha256` 與資訊性 `general_hash` 解耦，可在構造 `binding['protected_sources']` 時對 `r.get('general_category') == B.INFORMATIONAL` 的來源將指紋第二元設為 `None`。
2. **`kind == 'cli_version'` 與 `catalog_changed` 同時發生時的診斷原因補充（極小優化）**：
   * 在 `P.candidate()` 中，當 `kind == 'text'` 且 `catalog` 為真時會加入 `'mixed_candidate'`；若未來 `gpt` 線同時發生 `cli_version:gpt` 變更與 `catalog_changed`，目前因已有 `'cli_version_observe_only'` 故 `format_eligible` 必為 `False`（完全安全），但若在 `elif kind == 'cli_version':` 亦加上 `if catalog: reasons.append('mixed_candidate')`，可讓診斷原因更對稱。
3. **Phase 3（Enforce）實作前置條件提醒**：
   * 進入下一階段開放執行路徑前，需補齊 24h 滾動 Token 預算預留與扣抵、單一來源白名單變更重驗（`autoload`）與結果綁定、原子性基線核准與失敗自動回滾（revert）機制，並經獨立審查後始得切換 `MODE`。