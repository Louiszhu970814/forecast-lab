# D138：data 無損封存與重複副本清理

狀態：**已完成並套用**。Claude／Gemini r1 對同一封包雙 PASS；三份實際還原演練、專項測試、前後正式檢查均通過。Louis 原話（2026-11-05）：「OK 幫我整理這些東西同時教另外兩個來審查一起刪」。整合者 GPT；本批不 commit／push，保留既有 D132–D137 工作。

## 精確範圍

| 原路徑（專案根目錄相對） | 原大小 | 保留方式 |
|---|---:|---|
| `data/coverage_sim_results.jsonl` | 158,916,955 bytes | `data/archive/d138/root-coverage-results.jsonl.gz`，9,594,263 bytes |
| `data/operations/d116-parallel-readiness/coverage_sim_results.jsonl` | 454,508,530 bytes | `data/archive/d138/d116-coverage-results.jsonl.gz`，31,825,238 bytes |
| `data/operations/d130-three-line-repair-20261104/agy-1.4.12-candidate` | 197,589,952 bytes | 已有 `agy-1.4.12-official.tar.gz` 的唯一 regular member `antigravity`，解壓後 SHA 與候選／現役 pin 一致；保留此 59,822,635-byte tar.gz |

兩份模擬檔由約 613.4 MB 壓成約 41.4 MB；第三項移除可從既有安裝包完整還原的重複副本。三份原檔已移除，封存本體淨減少 769,595,936 bytes（約 770 MB）；納入本批證據與正常執行新增資料後的實測快照見下方。這是檔案邏輯大小，不保證 APFS／快照下實際可用空間增加完全相同。

這是歷史紀錄的無損儲存遷移，沒有丟棄、篩選、重新產生或覆寫歷史行。每個原路徑保留 `.archived.json` 導覽，對應含原始 SHA、大小、mode、mtime、封存 SHA 的私有 `data/operations/d138-data-cleanup-20261105/manifest.json`。壓縮檔、原本的安裝包、manifest 與還原工具應一起備份；不能把 archive 目錄當快取刪掉。

## 保留與延後

- 保留現役 `data/cli_pins/`、核心 JSONL 帳冊、全部預測原始輸出與驗收證據、外掛候選 zip、D130 官方安裝包。正式版本、模型、prompt、額度、窗口、排程及送出設定不變。
- `raw/` 的 78 份模型型錄雖只有兩種內容，仍維持原路徑與獨立檔案。去重需另處理 adapter／驗收引用與隔離重驗；本批不為約 49 MB 的收益改動現役 adapter。
- 舊 acceptance、外掛快照不按日期刪除。現有驗收／還原依賴及尚未完成的 M3／M4 限制仍保留。

## 還原與後續使用

工具僅處理上表三個原檔，拒絕覆寫目的檔、symlink 與內容不符；不直接使用 tar 的 extractall。所有操作使用既有六把營運鎖及兩份模擬器交易鎖，繁忙即退出，不中止現有工作、不停用排程。

```sh
# 只驗證三個封存來源，不刪除、不還原
.venv/bin/python data/operations/d138-data-cleanup-20261105/archive_data.py verify

# 要使用原根目錄模擬結果前，還原 entry 0；D116 為 entry 1
.venv/bin/python data/operations/d138-data-cleanup-20261105/archive_data.py restore --entry 0

# 要重用 D130 舊 repair_gemini_pin.py 前，先還原 entry 2
.venv/bin/python data/operations/d138-data-cleanup-20261105/archive_data.py restore --entry 2

# 只想讀取一份歷史檔：指定新的 scratch 目的檔，原封存狀態保留
.venv/bin/python data/operations/d138-data-cleanup-20261105/archive_data.py restore --entry 1 --destination /tmp/d116-restored-results.jsonl

# 中斷／部分刪除後恢復：逐一核對 marker 與 SHA；不覆寫已有不同內容
.venv/bin/python data/operations/d138-data-cleanup-20261105/archive_data.py recover
```

`src/coverage_sim.py` 只加兩個封存標記檢查：開始模擬前及鎖內追加前均拒絕對已封存結果另起空白歷史。還原原路徑成功後才移除標記；不改模擬計算／版本或正式預測行為。D130 舊修復腳本及歷史引用保留，使用前按本頁還原候選。

## 審查與驗證

私有操作、來源、雙審原始輸出及開發用量：`data/operations/d138-data-cleanup-20261105/`。審查僅提供離線來源、差異、metadata 與驗證結果，不提供開放題的正文、機率、理由或憑證。審查者透過實際官方 CLI，無檔案／命令／網路工具；不宣稱他們親自執行 host 測試。

刪除前須完成：三份實際還原演練、兩份相同封包 PASS、精確來源 hash 綁定、合成錯誤／中斷測試、模擬器回歸、正式前檢。刪除後須核對封存可讀、非目標正式來源與核心帳冊不變，並通過正式後檢。實際結果另追加於本頁。

## 完成交付（2026-11-05）

- 實際完成時間：`2026-11-05T04:08:30.861635Z`（UTC）。未停用／重設正式排程；維護與雙審使用既有互斥鎖，持鎖期間其他工作可能因 busy 略過，沒有中止在途工作。
- 刪除前後檔名盤點確認：僅上表三個既有原檔消失，全部留有 `.archived.json`；兩份 gzip 與原官方 tar.gz 驗證通過。三份實際還原演練的 SHA／大小／權限／奈秒修改時間完全一致。
- 邏輯大小快照：**1,788,553,996 → 1,019,756,161 bytes**，約 **1.79 GB → 1.02 GB**；淨減少 **768,797,835 bytes（約 769 MB，43%）**。服務與本批證據仍會正常追加，不把此快照當永久固定總量。
- 驗證：封存／去重／還原／審查閘門 **18 PASS**，實際套用 wrapper 合成成功／後檢失敗自動恢復 **2 PASS**，離線模擬器 **71 PASS**。未宣稱本批重跑整套專案測試。
- 正式套用前後各三線×兩層，共 **12 項 preflight 全部 ok、無 waiver、無 line_guard**；套用前後 **107 個 src/config/prompts/launchd 檔案與 15 份核心帳冊 SHA 不變**。107 檔基線是在四行離線封存防護加入後建立；本批唯一既有程式差異就是這四行。兩份現役 CLI pin 的 SHA 另行核對不變。
- 已對兩個實際封存目錄確認：模擬器在讀取登記／執行模擬之前拒絕追加，提示先還原。
- 真實官方 CLI 審查：[Claude r1 PASS](../../reviews/claude-d138-r1.md)、[Gemini r1 PASS](../../reviews/gemini-d138-r1.md)，同封包 SHA `58f200fd7620c0f0d9a523166c07e642107b92e5016554e00937e2bffe03372a`。兩者均確認 END 標記；僅靜態審提供內容，host 執行測試與還原。Claude 明列部分文件只見摘要／hash 的限制，未宣稱完整讀取那些文件。
- 審查非阻擋建議處理：補做 wrapper 故障測試；host 核對 `.coverage_sim/ledger.lock` 一致、沒有現行結果讀取者；gate 檔案表與封包 input map 完全一致，套用前確認本次前後檢輸出不存在，未覆寫證據。外部 scratch 父目錄硬化及泛化重跑介面留待有需求時處理；本次一次性套用已結束，**不要重跑 apply／apply_reviewed**，還原請用本頁指令。
- [實際驗證](../../reviews/d138-verification.json)／[來源與交付 hash](../../reviews/d138-source-manifest.json)。開發審查用量保存在私有 `review-usage.jsonl`；沒有預測重跑、開放題輸出檢視、Metaculus POST 或 commit／push。
