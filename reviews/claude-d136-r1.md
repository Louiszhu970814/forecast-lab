# D136 r1 完整程式／設計審查（Claude）

本報告只審 D136 r1 packet 裡的完整 diff、部署腳本和部署故障注入測試。我沒有執行任何測試、沒有計算 hash，也沒有讀任何檔案或使用工具。下文的「3368 passed」「AST proof」「6 passed」都是 packet 自己提供的證據，不是我驗證的結果。先前的清理盤點只審過候選項目，不能代替本 patch 的審查，本報告也不沿用它的結論。本報告不代表 Gemini 的獨立審查，不代表 Louis 核准，也不新增主賽、D118 延長、B 類項目或額度的授權。

## 〇、packet 完整性

- **END-D136-R1 標記**：有，位於部署故障測試之後，是 packet 的最後一行。
- **檔案對照**：FROZEN FILES 共 40 筆，和部署腳本的 `TARGETS` 40 筆逐一一致：14 份 docs、`reviews/INDEX.md`、2 個 `.gitkeep`、11 個 `src`、12 個 `tests`。
- **diff 的實際涵蓋**：
  - diff 內有 38 個檔案的 hunk。
  - `launchd/.gitkeep` 和 `prompts/.gitkeep` 是刪除空檔，unified diff 不會產生 hunk。manifest 裡兩者的值是 null，和刪除一致。
  - 從內容看，packet 算完整。
- **我看不到的部分**：
  - diff 只有 hunk 上下文，沒有完整檔案。例如我看不到 `budget.py`、`forecast.py` 刪 helper 後 `math` 是否還被使用（還在用的話也不會出錯）。
  - 私有輸入沒有附上：`deployment-gate.json`、`reviewed-files-r1.json`、`initial-manifest.json`、`stage-path.txt`，以及 `advise.lock_all`、`autoload_maintenance.require_idle`、`F.preflight` 的原始碼。
  - 我無法確認 frozen SHA 和 diff 的 after 內容逐位元組相符。

## 一、packet 內實際可見、內容互相一致的證據

1. **AGENTS.md 的 SHA 三處一致**：`509bf93…51ba` 同時出現在 FROZEN FILES、`d121_protected_hashes.json` 和 `m5_legacy_golden.json`。舊值 `f356a70…` 在兩份 fixture 都有替換。
2. **M5 golden 只改兩處**：AGENTS 換值，並刪除 `prompts/.gitkeep`（空檔 SHA `e3b0c442…`）這一列。runner、netguard、`adapters/gpt.py`、`quota_policy`、各 prompt 內容的列在 diff 裡都是未變動的上下文。
3. **測試數量**：
   - 收集數 3351 → 3368，`removed=[]`，新增 17 個 nodeid，都屬於本 patch 新增的三組測試。
   - 3368 passed 正好是 3351 + 17。
   - 所以 packet 內證據顯示沒有刪除既有案例，最後一輪全數通過。
4. **runtime 範圍**：
   - 只改 11 個 `src` 檔：刪 `budget._positive`、`forecast._fraction`，另刪 11 個 import binding（分布在 10 個檔）。
   - 數目逐一核對：`auto_reaccept` 2 個、其他 9 檔各 1 個。和描述相符。
   - `metadata_observer.py` 不在 TARGETS，和「`MetaculusError` 已保留」一致。
   - 11 檔的 AST proof 都是 true。
5. **D126 界線**：
   - 沒有改 `config/`、prompt 內容、adapter、runner、netguard、forecast 設定或 D118 窗口。
   - 唯一的治理文件改動是 AGENTS 規則 2：把 Cup 從試驗場地移除，對齊既有的 D79。範圍是收窄，不是放寬。
6. **部署腳本的順序合理**：
   - 依序是：審查 gate → 六鎖 → `require_idle(1200s)` → 在鎖內讀一次 stage 並比對 pinned hash → preflight-before → 檢查受保護檔與目標檔沒有被同時改動 → 備份 → 原子寫入或刪除 → 比對 hash → smoke → preflight-after → 再比對 hash 與受保護檔 → 寫完成紀錄。
   - 發生 `BaseException`（包含 `TimeoutExpired`）時，會反序回滾並驗證回滾後的 hash。
   - 寫入的位元組是在鎖內讀到、通過驗證的那一份，stage 之後再被改也不影響。
7. **新的 run_offline 沒有 import 副作用**：
   - 舊版在 import 時就會 mkdir、裝 audit hook，並把 TMPDIR 設到專案內。
   - 新版把這些都移進 `main()`，暫存目錄改到專案外的 `/tmp`，並檢查 `scratch` 不在 ROOT 底下。
   - 因此 `test_offline_isolation` 可以安全地 `from run_offline import make_guard`。

## 二、逐項審查

### A01–A06 文件

- **AGENTS**：措辭正確，只改規則 2。packet 也說明預測 attempt 使用專案外的 workbase，`ancestor_sources` 盤點不含專案 AGENTS，所以這次修改不影響模型輸入。不過它是「硬性規則」段落，PLAN 的 D136 列已記錄來源，建議在交接時明確向 Louis 標示這一點。
- **README**：
  - 舊版本號和舊狀態換成穩定的摘要，修正「与」→「與」。
  - 對 guard 的描述是「誤用防線，不是安全沙盒」，並註明直接執行 pytest 時沒有 audit guard。和程式行為一致。
- **D128／D129／M4 校正頁首、HANDOFF、INDEX**：
  - 都把「11/06 21:00」標為原開發安排，現行日期以 D132 為準：最早台北 11/13 20:00、最晚 11/14 12:59。
  - 我看不到 D132 原文，日期本身無法核對（證據限制）。
- **DECISIONS-AFTER-D84 stub**：
  - 清楚寫明這只是導覽，不是恢復舊草案，也不可據此重啟已結案工作。
  - `392335b`、`0423269b…` 兩個 commit 和「六處連結」我無法驗證。
- **四份 SUPERSEDED 標記**：保留歷史正文，指向後續文件。`TELEGRAM-TEAM.md`、`PLATFORM-REPLY-D115.md` 是否存在，我無法驗證。
- **reviews/INDEX**：補了 D132 和 D135 各輪的列。各輪狀態我無法驗證。

### A07

刪除兩個空的 `.gitkeep`。說明是兩個目錄都已有其他內容。部署腳本用 `unlink` 刪除，回滾時用備份的空位元組還原。fault test 有涵蓋這個情況。

### A08–A09 runtime

- 兩個 helper：packet 說已搜尋受追蹤來源和私有維護腳本，沒有呼叫。
- 11 個 import：AST proof 只證明「after 等於 before 刪掉這些節點」，不能證明「沒有任何地方用模組屬性取用這些名字」。這是我唯一的阻擋項，見第三節。

### A10 測試 host

- **conftest**：
  - 用 autouse fixture 給每個測試獨立的 synthetic host。`host_root` 由 `tmp_path_factory` 建立，和 `tmp_path` 分開，解決了舊 fixture 在 `tmp_path` 下建 `synthetic-project` 而撞到「空目錄」斷言的問題（packet 列出的 7 個 fixture collision）。
  - 預設 numeric／discrete 為 True，和正式 M5 rollout 一致。
- **`question_type_flags`**：
  - 在 autouse 之後覆寫 `CONFIG_PATH`。
  - 新的參數化測試同時斷言 True 和 False 兩種結果，等於有實際證據顯示旗標是在呼叫時才讀，沒有被快取。
- **舊案例的處理**：舊 M5 案例改成明確關閉旗標，保留原本的預期，並加了開啟的反向案例。這屬於「明確 fixture」，不是為了讓測試通過而改預期，可以接受。
- **guard**：
  - `resolve()` 處理了 `..` 和 symlink。
  - Popen 檢查只看 `argv[:2]`，沒有涵蓋 `sh -c "claude …"` 這類寫法；文件已如實說明這個限制。
  - 子程序 canary 測試是在子程序內自己安裝 guard，不是測試 guard 會被繼承；文件也這樣寫，沒有誇大。
- **hook 停用的時機**：在 `pytest.main` 返回後、`TemporaryDirectory` 清理前停用。這段處理 dir_fd 相對路徑誤判的理由合理，順序也正確。

### 部署腳本與 fault tests

整體設計正確：失敗時會回滾，不會繼續往下做。小問題列在第四節。

## 三、阻擋項（Blocker）

**B1：11 個被刪的 import binding，缺少「模組屬性／字串型消費者」的確定性掃描證據。**

**為什麼是阻擋項**：
- 本 patch 自己已經遇過這一類失敗：`metadata_observer.MetaculusError` 被當成模組屬性使用，靜態未用分析沒抓到，是完整回歸才發現的。
- packet 對 helper 有做 grep，但對這 11 個 binding 只有測試通過這一項證據。
- 測試只涵蓋有被執行到的路徑。D118 live 窗口期間，正式部署的 continuous、scheduler、quota、reconcile、resolve 裡如果有沒被測到的路徑，可能在執行時才出現 `AttributeError` 或 `ImportError`。這種錯誤落在正式線上。
- 補這份證據成本很低，而且是確定性的。
- 部署 gate 只認最終結論標題，不會執行任何附條件的建議，所以我不能用「PASS 但請先補」的方式處理。

**r2 需要補的證據（唯讀掃描即可，不必改程式）**：

1. 掃描 `src/`、`tests/`，以及工作區內私有的 `data/operations/**/*.py` 等維護腳本。對以下 10 個模組和 11 個名字，用 AST 列出所有：
   - 屬性取用 `<模組或別名>.<名字>`，別名要涵蓋 `import scheduler as S` 這類寫法；
   - `from <模組> import <名字>`；
   - 字串形式：`getattr(…, "<名字>")`、`monkeypatch.setattr("模組.名字", …)`、`mock.patch("模組.名字")`。

   10 個模組和 11 個名字是：
   - `auto_reaccept`：hashlib、os
   - `comment`：math
   - `continuous`：new_id
   - `flab.team_chat`：json
   - `forecast`：Iterator
   - `m2_supplement`：transaction
   - `quota`：MODEL_CHECKS
   - `reconcile`：read_records
   - `resolve`：Mapping
   - `scheduler`：latest_snapshots
2. 列出命中數。0 筆就照實記錄；有命中，就比照 `MetaculusError` 恢復該 binding，或附上理由。
3. 確認拿掉 `quota → adapters.base` 和 `scheduler → poll_questions` 這兩條 import 後，沒有其他程式依賴它們在 import 時的副作用或 import 順序。packet 只說「沒有動態 plugin export registry」，沒有涵蓋 import 副作用。
4. 掃描結果記錄到私有 job 目錄，摘要寫進 D136 任務單的證據段。

**注意**：部署腳本目前寫死 `{model}-r1/answer.md` 和 `reviewed-files-r1.json`。進入 r2 必須改腳本或把輪次參數化。改過的腳本本身也要放進 r2 packet 重新審查，不能沿用 r1 的審查。

如果 r2 只補上述證據（加上腳本的輪次調整），檔案 hash 都不變且掃描為 0 筆，阻擋理由就會消失。但這只是我的預期，不是預先核准；r2 仍要重新審查。

## 四、非阻擋建議（Proposal，不影響本次結論）

- **N1 smoke 範圍**：
  - 部署 smoke 沒有涵蓋 scheduler、continuous、quota、reconcile、comment、team_chat、auto_reaccept、m2_supplement 的對應測試。建議加進去，或至少對 11 個模組做一次純 import。
  - `timeout=120`，但完整套件要 147 秒。建議先在 stage 量一次 smoke 的耗時。超時會觸發回滾，結果安全，但會讓部署失敗。
- **N2 gate 的讀取時差**：`digest(path)` 和 `path.read_text()` 讀了兩次。建議讀一次位元組，用同一份計算 hash 和判定。
- **N3 腳本自身的 hash**：建議在 gate 裡事先釘住部署腳本的 hash，而不是只在完成紀錄裡事後記錄。
- **N4 fault tests 缺的情境**：建議補「受保護檔被改動就中止」「目標檔被同時改動」「`require_idle` 失敗」「寫到一半例外」「回滾後 hash 不符」五種。
- **N5 涵蓋範圍的轉移**：
  - autouse host 會替所有測試 mock `PROJECT_ROOT`、`submit_mode` 和題型旗標。真實主機上的不變式（例如 workbase 在專案外）改由正式 preflight 驗證，不再由測試套件驗證。建議在任務單寫明。
  - 保留 nodeid 不等於保留案例目的。建議抽查原本透過 API 讀正式 rollout 的舊測試，確認沒有變成無論如何都會過的狀態。
- **N6 完整回歸的出處**：完整回歸輸出沒有寫入口（`run_offline` 還是直接 pytest）、cwd、Python 版本，也沒有和 stage 的 SHA 綁定。另外，新 conftest 下直接執行 pytest 的完整結果也沒有提供。建議 r2 一併附上。
- **N7 文件小修**：
  - `docs/tasks/INDEX.md` 首行仍寫盤點「未執行清理」，D136 之後容易誤讀。
  - HANDOFF 列的描述仍是「含階段 A～D 計畫表」，沒註明已改為歷史。
  - D136 列沒有狀態標籤（例如 ACTIVE）。
  - reviews/INDEX 新增列的反引號格式和既有列不一致。
- **N8 部署紀錄的耐久性**：寫 `apply_complete` 紀錄的動作在 try 外面，失敗時會「已套用但沒有紀錄」。目錄也沒有 fsync。

## 五、證據限制總結

- 我無法驗證 frozen SHA 和 diff 相符、備份和 stage 的內容、私有 gate 檔、D132 的日期、git commit、六處連結、各份審查檔的狀態，也無法驗證測試的實際執行環境。
- 我無法證明完全沒有外部使用者；packet 也自己承認無法證明。
- B 類項目沒有執行，也不在本次範圍內。
- 本結論只適用 D136 r1 這組 hash；任何位元組變動都要重新審查。

## 結論：REQUEST_CHANGES