### 封包與審查身分確認

- 我已看到結尾標記 **END-D136-R2**。封包內容依序有：授權與 r1 結果、FROZEN FILES r2（40 項）、TEST PROVENANCE、guarded／direct 全量結果、COLLECTION（+17）、AST PROOF、掃描器完整原始碼與輸出、import 副作用結果、adapters 套件 init 與 adapters.base 全文、forecast 的直接 import 邊、D136 完整最終 diff、部署腳本全文、gate builder、12 個故障測試與結果、smoke 量測。中間沒有截斷，**封包完整**。
- 我以 Claude 完整審查者身分審查整個 D136 delta，不只看 r1 的 B1。我沒有使用任何工具，沒有執行任何測試、掃描或 import，也沒有讀取任何檔案、憑證或預測。下文的測試、掃描與副作用數字，全部是 **GPT 提供的實際執行證據**，我只檢查它們內部是否一致，以及是否足以支持結論。這些不是我的執行結果。
- 我的判定只代表 Claude r2 的審查結果。它不是 Louis 核准，也不能代替 Gemini r2 獨立審查；r1 的 PASS 也不能代替 r2。部署能否進行，仍由 gate 決定：兩份 r2 都要 PASS，且 hash 必須一致。

### 一、內部一致性（我能直接核對的部分）

1. **檔案數**：FROZEN FILES r2 共 40 項：14 份文件、2 個 .gitkeep（null）、reviews/INDEX、11 個 src、12 個 tests。這和部署腳本的 `TARGETS` 集合逐項相符。gate 也會檢查 `set(canonical)==TARGETS`。
2. **AGENTS 新 hash**（509bf93c…）在三處相同：FROZEN 清單、`d121_protected_hashes.json`、`m5_legacy_golden.json`。兩個 fixture 的 diff 只改了 AGENTS 那一行，m5 另外只刪了 `prompts/.gitkeep` 條目。其餘 runner、netguard、adapter、config、prompt 內容的 hash，在 diff 中都沒有變動。
3. **測試案例數**：新增清單逐項相加是 2+11+4=17，3351+17=3368，`removed` 為空，和「原有 node 全部保留」的說法一致。D135 記錄的是 3251 passed＋100 個既有失敗＝3351；現在兩種入口都是 3368 全部通過，可見那 100 個舊失敗在 synthetic host 下變成通過（見殘餘風險 R3）。
4. **掃描器正控制**：自帶的 sample 預期 6 筆命中。逐項推算：屬性 1、from-import 1、getattr 1、限定名字串 2、動態 import 別名 1，合計 6，與 assert 一致。真實掃描也只命中保留下來的 `metadata_observer.MetaculusError` 1 處，說明掃描器確實能偵測這類使用。
5. **runtime diff 範圍**：11 個 runtime 檔只刪 import 行和兩個 helper 定義（`budget._positive`、`forecast._fraction`），和「沒有改 config／prompt／adapter／runner／netguard／預測策略／窗口／額度／main」的說法相符。

### 二、B1（r1 唯一的阻擋項）判定：證據足夠，解除阻擋

**外部使用者掃描**
- 涵蓋 src、tests 和工作區 `data/operations/**/*.py`，共 251 檔，0 個解析錯誤。掃描時點是刪除之前，原本的宣告還在，這個時點是對的。
- 涵蓋的使用形式有：`import X as Y` 別名、`from X import name`、多層屬性、把模組物件賦值給變數（迭代 3 輪）、`importlib.import_module`／`__import__`／`sys.modules[...]`、getattr／setattr／hasattr／delattr／`patch.object` 的字串屬性、`monkeypatch.setattr("mod.name")` 和 `mock.patch("mod.name")` 的限定名字串，以及目標模組的 star import。
- 關鍵設計：凡是屬性名稱落在 11 個目標名稱內、但擁有者解析不到目標模組的存取，一律列入 `unresolved`。所以「模組物件透過 fixture 參數傳入」這類解析不了的情況不會漏掉，會出現在 unresolved 清單裡。
- 那 19 筆 unresolved 我逐筆看過，擁有者都是非目標模組：`budget.transaction`、`cycle.os`、`claude_sync_state.os`、`m2_prepare.latest_snapshots`、`poll_questions.latest_snapshots`、`v15_probe.new_id`。沒有一筆指向 auto_reaccept、comment、continuous、flab.team_chat、forecast、m2_supplement、quota、reconcile、resolve 或 scheduler。
- 11 個目標的命中數全部為 0，star import 為 0。
- 封包已誠實寫明限制：這是詞法／常數掃描，擋不了運算產生的屬性名或專案外的使用者。我接受這個限制，作為殘餘風險。

**quota 移除 `adapters.base` 的 import 副作用**
- `adapters/__init__` 只有 docstring。`adapters.base` 全文我看過：import 的只有標準庫和 `flab.runner.RunResult`，其餘是常數 tuple、frozen dataclass、Protocol 和純函式。沒有模組層級的 I/O、註冊、全域狀態修改或 sys.modules 操作。
- quota 本來就直接 `from flab import runner`，所以 `flab.runner` 照樣會載入。這和 GPT 的前後結果一致：只少了 adapters 和 adapters.base 兩個模組，`sys.path` 相同。
- 剩下一個理論情境：某段程式依賴「import quota 之後 sys.modules 裡就有 adapters.base」。封包說整個 src 沒有存取 sys.modules，而全量測試也在載入集合變小之後通過。

**scheduler 移除 `poll_questions.latest_snapshots` 的 import 副作用**
- 封包列出的 forecast 直接 import 邊包含 `from poll_questions import latest_snapshots`，所以 scheduler 經由 forecast 仍會載入 poll_questions。前後專案模組集合完全相同（35 個），sys.path 也相同，不會因此造成載入順序或註冊上的差異。
- 掃描也確認沒有任何人 patch 或存取 `scheduler.latest_snapshots`。測試裡 patch 的是 `poll_questions` 和 `m2_prepare` 上的同名屬性。

**沙盒條件**：副作用比較是在全新子程序、BEFORE／AFTER 兩份副本上做的，audit hook 拒絕寫檔、讀 runtime／.env、socket 和 subprocess。4 次 import 都 exit 0，沒有觸發任何被禁止的事件。設計合理。

**結論**：B1 要求補的別名、模組屬性、from-import、getattr、monkeypatch、mock 字串掃描，以及兩條副作用檢查，都已補齊，而且證據內部一致。**B1 解除。**

### 三、其餘完整 delta 審查

**A. 測試 host 統一（conftest／offline_workcopy／run_offline）**
- 每個測試都由 autouse `isolated_host` 透過 `tmp_path_factory.mktemp` 取得獨立的 host_root，和該測試的 `tmp_path` 分開，這修正了舊 fixture 污染 tmp_path、讓「目錄應為空」的斷言失準的問題。新測試 `test_host_does_not_populate_test_data_directory` 直接驗證這點。
- 題型旗標改用 monkeypatch 指到 host 內的 JSON，不改正式 `config/`。新增的參數化測試對 numeric／discrete 的 False 和 True 都斷言 `supported is enabled`，這也間接證明 question_types 每次都即時讀取設定、沒有快取，monkeypatch 確實生效。
- 舊 M5 案例改成明確關閉題型，以保留原本的斷言（舊的「numeric 待 M5」斷言在 v1.74 開啟後已經和正式設定衝突）。另外補了 True 方向的案例，沒有把斷言改成無條件通過。
- `run_offline.py` 的改動：
  - scratch 移到專案外的 `/tmp` 私有暫存目錄，並明確拒絕落在專案內的情況。
  - guard 先 `resolve()` 再比對，可以擋 `..` 正規化和 symlink；新測試涵蓋了這兩種，還有子程序中真實讀取 canary 的測試。
  - 舊 `--synthetic-host` 只是被濾掉，不會重複掛 fixture。
  - hook 在 pytest 結束後才停用，讓標準庫用 dir_fd 清除暫存目錄時不被誤擋。這個理由合理，停用範圍也只限於清理階段。
- 文件明確寫出它不是沙盒、不保護子程序、直接執行 pytest 時沒有這層 guard，界線描述正確。

**B. 部署腳本、gate 與故障測試**
- 流程依序是：六鎖 → `require_idle(1200s)` → 在鎖內只讀一次 stage 並比對 hash → preflight 六項（ok、無 waiver、無 line_guard）→ 307 個保護檔與目標檔的並行變更檢查 → 備份 → 寫入 40 個目標 → 部署後 hash 檢查 → 20 個模組的 smoke → postflight 六項 → 再次檢查目標與保護檔 hash → 寫 `apply_complete` 記錄。這個記錄已移進 try 內，寫入失敗也會觸發回滾。
- 時間預算：smoke 逾時上限 240s，加上兩次 preflight 各 300s，最壞約 840s，小於 idle horizon 1200s。量到的 smoke 是 120s，約有 2 倍餘裕。
- 回滾會依反序還原已完成的檔案、刪除新增檔、把已刪的空 .gitkeep 寫回，再對全部 40 個檔案比對 hash 並記錄。`except BaseException` 也涵蓋 smoke 的 `TimeoutExpired`。
- 審查 gate：路徑必須是 `JOB/{model}-r2/answer.md`、hash 要和 gate 一致、verdict 正則必須剛好命中一次 PASS。gate builder 另外要求答案中出現 END-D136-R2。部署腳本會先驗證自己的 SHA 才開始寫入。
- 12 個故障測試涵蓋：stage 不一致、驗證後 stage 被改仍只寫入已釘住的 bytes、postflight 失敗、smoke 失敗、審查檔被換掉、真實的 REQUEST_CHANGES、保護檔和目標檔競態、idle 拒絕、寫到一半出錯、回滾不一致、腳本 SHA。設計合理（GPT 提供的結果：12 passed）。

**C. 文件與規則**
- AGENTS 規則 2 只把 Metaculus Cup 從試驗場地中移除、對齊 D79，是**收窄**，沒有擴大任何權限，屬於 A01 的授權範圍。封包說明預測 attempt 在專案外的祖先指示清單不包含專案 AGENTS，所以 hash 改變不會影響預測輸入。
- PLAN v1.90 有決策紀錄、§16 和整合者標記，D136 的定位是 non-blocking、沒有改預測行為，和實際 diff 一致。
- D128、D129、M4、HANDOFF、INDEX 的日期都改成依 D132 的「最早台北 11/13 20:00、最晚 11/14 12:59 預定結算」，並寫明「預定不等於已結算」。歷史正文保留，新增的是取代標記和導覽 stub；stub 只附唯讀的 `git show`，並警告不可據此重啟已結案工作。
- README 改成穩定的營運邊界描述，沒有新增主賽、窗口或額度授權。
- D136 任務單沒有提前宣稱 PASS 或已套用，B 類項目明確延後。

### 四、阻擋項（Blockers）

**無。** r1 的 B1 已由上述證據解除，這輪沒有發現新的阻擋性缺陷。

### 五、非阻擋建議（Proposals，不是 PASS 的條件）

- **P1：補模組內未定義名稱的靜態證據。** 這次掃描查的是跨模組使用；AST proof 依封包自己的說法也不是使用證明。11 個 import 和 2 個 helper「在本模組內沒有被引用」，目前只有盤點清單和全量回歸支撐。建議在套用前，對 stage 的 11 個 runtime 檔跑 pyflakes 或 ruff（F821、F401）並保存輸出，以排除「很少走到的分支仍引用被刪名稱、到 live 才拋 NameError」的可能。全量回歸加上 postflight import 已降低這個風險，所以我不把它列為阻擋。
- **P2：smoke 少了兩組相關測試。** 目標檔 `tests/test_cycle.py` 不在 smoke 清單裡；`continuous.py` 有改動，但沒有看到 continuous 相關的測試模組。建議下一批補進 smoke，並重新量測、調整 timeout。本批 stage 的全量測試已涵蓋相同 bytes。
- **P3：把部署腳本 SHA 寫進審查封包。** 部署腳本和 gate builder 的 SHA 是在建 gate 時從磁碟取得的，只能防止「建 gate 之後被改」，不能讓審查者確認磁碟上的 bytes 就是封包裡的全文。建議把這兩個檔案（和故障測試檔）的 SHA 列進 reviewed manifest 或封包，並讓 gate builder 對常數做 assert。目前 gate 有記錄 `packet_sha256`，事後仍可稽核。
- **P4：對齊 CLAUDE.md。** README 寫 `AGENTS.md / CLAUDE.md` 都是協作守則。如果 CLAUDE.md 是獨立副本而不是引用，請確認它沒有殘留把 Cup 當作試驗場地的舊文字。封包沒有給 CLAUDE.md 內容，我無法核對。
- **P5：確認沒有程式掃描 prompts 目錄。** 建議補一筆證據，確認沒有程式用 glob 或 iterdir 對 `prompts/`、`launchd/` 整個目錄做 hash 或指紋（否則刪 .gitkeep 會改變指紋）。即使漏掉，postflight preflight 的 baseline 檢查加上回滾也是安全網。
- **P6：任務單狀態用語。** D136 任務單寫「回歸…待完成」，但 GPT 已有 3368 PASS 的回歸證據，字面上偏保守。部署後依規定追加事實時，請改成「stage 回歸已完成（GPT 證據），root smoke／postflight 待部署」。
- **P7：主站試驗 True 方向的覆蓋偏薄。** `test_main_site_trial` 的既有案例現在預設 numeric 關閉，和正式設定（開啟）不同；新增的 True 案例只驗證 feed query 和 run 成功。建議之後補「主站 feed 含 numeric／discrete 時完整處理」的案例。

### 六、殘餘風險（已知、已揭露，不構成阻擋）

- **R1**：詞法掃描證明不了運算產生的屬性名，也涵蓋不到專案外的使用者。
- **R2**：回滾過程本身若失敗，沒有交易性保證；目錄 fsync 的限制沒有被擴大宣稱。
- **R3**：synthetic host 讓原本 100 個 host 相依的失敗節點通過。這代表測試環境變得可重現，**不代表實機環境已驗收**。實機狀態要靠部署前後的六項 preflight 核對，封包和文件都已這樣說明。
- **R4**：runner 只守護主程序，子程序不受保護，文件已寫明。

## 結論：PASS

補充：這是我（Claude r2 完整審查者）根據本封包做出的審查判定。B1 已解除，沒有其他阻擋項，P1–P7 是建議，不是條件。這不等於 Louis 核准或部署完成，也不能代替 Gemini r2；所有測試數字都是 GPT 的執行證據，我沒有執行任何東西。已確認封包完整並看到 END-D136-R2。