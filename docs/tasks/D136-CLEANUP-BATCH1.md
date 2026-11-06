# D136：主賽前第一批文件與 dead code 清理

狀態：**本批已完成並套用**；Claude／Gemini r2 雙 PASS，完整回歸與正式部署驗證通過。來源：Louis 在三方盤點後指示「OK開始做」（2026-11-05）；整合者 GPT，PLAN v1.90。既有 D132–D135 未提交工作保留；尚未 commit／push。

## 本批範圍

對照 [清理盤點 A01–A10](CLEANUP-AUDIT-2026-11-05.md)：

- A01–A06：AGENTS 對齊 D79 的 Cup 排除；README 改用穩定操作摘要；M4／D128／D129／HANDOFF 加 D132 日期更正；兩份索引補齊；建立 DECISIONS-AFTER-D84 導覽 stub 修復六處連結；四份舊提案加取代標記。歷史正文與決策保留。
- A07：移除已有內容的 `launchd/.gitkeep`、`prompts/.gitkeep`。
- A08–A09：刪 `budget._positive`、`forecast._fraction`；移除盤點列出的 11 個非 adapter 未用 import（10 檔）。已搜尋受追蹤來源及工作區內私有維護 Python 腳本，未見兩個 helper 的呼叫。adapter 的 Path import 保留。`metadata_observer.MetaculusError` 在完整回歸中確認仍被當作模組屬性使用，已恢復；盤點的 12 個候選實際移除 11 個。
- A10：`tests/conftest.py` 統一所有入口的 synthetic host；host 與各案例 tmp_path 分開，runner 暫存改在專案外。原 `--synthetic-host` 參數保留為相容別名，不再重複掛 fixture。M5 舊案例明確關閉題型，另加 numeric/discrete 開／關測試；原有預期與案例目的不刪。

## 測試與防護的界線

正式 `config/` 不改。測試 host 僅把預設 submit_mode 的讀取隔離成 local、題型旗標設為明確測試值；讀取原始 config 的 schema 測試仍驗證正式檔案。工作目錄／ancestor／Gemini deny-rule 的主機相依行為使用合成目錄；不以主機當時的外掛與檔案布局作為測試結果。

建議入口：`.venv/bin/python tests/run_offline.py -q`。它在 pytest 執行期間阻擋主程序直接開啟專案 data／reports／.env／.agents／.codex 與直接呼叫三家模型 CLI，並允許本次 scratch 內的假 CLI。這是受信任測試的誤用防線，**不是任意程式的安全沙盒，也不攔截所有網路或子程序內部存取**。pytest 結束後停用該 hook，讓標準庫安全清除自身暫存目錄；audit event 無法將 dir_fd-relative 的 `data` 正確還原父目錄，因此不能把清理誤判成存取專案資料。直接 `pytest` 共享 fixture，但沒有這層入口 guard。

新增測試檢查 tmp_path 保持空白、資料路徑正規化／symlink 攔截、三家 CLI 只允許 scratch stub，及子程序內自行安裝 guard 的實際 canary 讀取。AGENTS 不在預測 attempt 的專案外祖先指示來源中；D121／M5 測試 hash 清單隨 D79 文字對齊更新 AGENTS 一項，M5 另移除已刪空占位檔 `prompts/.gitkeep` 的條目；所有 legacy 輸入／輸出金標準、正式設定／程式／prompt 內容 hashes 不動。

## r2 補證與測試範圍

Claude r1 要求補齊 import 的模組別名／屬性與字串消費者掃描；Gemini r1 PASS 不取代這項阻擋。GPT 已用 AST 掃描 `src/`、`tests/` 與工作區 `data/operations/**/*.py`（251 檔，0 解析錯誤）：11 個待移除 binding 全為 0 命中；正控制確實找到 `metadata_observer.MetaculusError` 的 1 個使用點，因此保留。另列出的 19 處同名屬性／字串呼叫均屬其他模組（budget、cycle、claude_sync_state、m2_prepare、poll_questions、v15_probe），不在刪除範圍。這是詞法／常數掃描，不能證明任意動態字串或工作區外使用者不存在。

新程序比較 import `quota`／`scheduler` 前後：禁寫檔、runtime data／.env 讀取、網路與 subprocess 的 audit hook 均未觸發，`sys.path` 一致。quota 少載入 adapters／adapters.base；前者僅 docstring，後者只有標準庫／flab.runner imports、常數、類別與函式定義，flab.runner 本來仍由 quota 直接載入，沒有註冊／啟動副作用。scheduler 經 forecast 仍會載入 poll_questions，前後專案模組集合完全一致。完整結果與來源 SHA 保存於私有 job。

synthetic host 讓測試在明確環境下驗證路徑、題型與送出契約；不等於實機全域環境已驗收。實際 host 的版本／基準／隔離狀態另由部署前後 preflight 核對。舊 M5 案例的輸出斷言保留，新增 False／True 兩種 feed query 和 numeric／discrete 支援斷言，沒有把測試改成無條件通過。

## 交付證據（完成後追加）

私有來源基線／測試／審查 CLI／備份與部署紀錄：`data/operations/d136-cleanup-20261105/`。實際回歸、雙審及套用結果見下方完成紀錄。

本批不改正式預測 prompt、參數、篩題設定、額度、D118 截止或主賽授權；沒有預覽開放 bot 題、補跑預測或 Metaculus POST。保留 data／驗收／舊帳冊相容與所有 B 類待清理項目。清理完成也不等於主賽就緒。

## 完成交付（2026-11-05T01:53:05.925Z）

- A01–A10 本批已完成；40 個受審目標（38 個新增／修改、2 個空占位檔刪除）經六鎖、閒置檢查、備份及原子套用，307 個原目標外檔案在套用前後 SHA 不變。原 D132–D135 未提交內容保留；未 commit／push。
- Claude r1 的 B1 補證已完成；[Claude r2](../../reviews/claude-d136-r2.md) 與 [Gemini r2](../../reviews/gemini-d136-r2.md) 對同一份最終完整封包均 PASS，且確認 END 標記。兩家只審提供的完整 diff／腳本／證據，未自行跑測試，不宣稱讀完所有既有來源全文。
- 專案外凍結副本：guarded 完整 **3368 passed／0 failed**（147.30s）；直接 pytest 完整 **3368 passed／0 failed**（109.53s）；原3351節點全保留、增加17，兩次完整執行的程式／測試 SHA 相同。相關直接 pytest430PASS、舊參數相容20PASS。舊基線的不同入口失敗數保留在原盤點，不能稱為100個 runtime bug 修復。
- 部署測試擴大至20個模組：stage976PASS（120.17s），逾時上限改240s；正式根目錄 guarded smoke **976PASS**。部署／回滾故障注入 **12PASS**，包含來源變動、審查替換、腳本 SHA、繁忙鎖、部分寫入、受保護檔、部署後前檢失敗及回滾不符。
- 正式套用前後各三線×兩層，共 **12 項 preflight 均 ok、無 waiver、無 line_guard**。沒有為清理重置驗收或放寬前檢；adapter／runner／netguard／config／正式 prompt 內容不變。
- 全部 Markdown 相對檔案連結：6處失效→0。程式 AST 排除指定刪除後一致；251檔模組屬性／字串掃描與 import 副作用補證見上節。保留 `metadata_observer.MetaculusError`，實際刪11個 import，非原候選12個。
- [GPT 驗證紀錄](../../reviews/gpt-d136-cleanup.md)、[受審／套用來源 manifest](../../reviews/d136-source-manifest.json)、[最終核對](../../reviews/d136-verification.json)。受審 SHA 描述套用時的位元組；本段、PLAN／交接／索引與原盤點的完成註記是部署後事實性追加，另列最終 SHA，沒有改程式或測試。

後續 B01–B09 仍依原盤點的交付基線、共用依賴、重播或隔離重驗條件分批處理。本批沒有啟用主賽、新窗口或 D135 研究候選，也沒有讀開放 bot 題預測、補跑模型預測或 Metaculus POST。
