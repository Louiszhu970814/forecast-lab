DECISION: PASS

**D138-R1 審查意見（Forecast Lab 設計／程式審查者）**

結論：沒有發現會阻擋刪除的問題，只有一些可選的改進。這個 PASS 只是我這一份審查的結論，不代表批准。Gemini 的審查結果、gate.json，以及官方 CLI 的開始／結束紀錄都還沒有出現，也不是我能代為完成的。

## 一、收到的內容與審查方式

1. **結束標記：有收到。** 封包最後一行是 `END-D138-R1`，內容完整，沒有被截斷。
2. **完整原文收到的檔案：**
   - archive_data.py、test_archive.py、apply_reviewed.py、preflight.py
   - manifest.json、restore-drill.json、archive-tests.txt、coverage-tests.txt、preflight-pre-review.json
   - D138 任務文件
   - src/coverage_sim.py 與 tests/test_coverage_sim.py 的 diff
   - 目前的 run_registered 全文
   - 決策與變更紀錄、source findings、輸入檔 hash 對照表
3. **列在 hash 對照表、但沒有附原文的檔案：**
   - 只有摘要或部分內容：PLAN.md（只有 D138 決策列與 v1.92 變更摘錄）、docs/coverage_sim.md、D130 文件、M3-PARALLEL-D116.md（後三份只在 source findings 裡描述）
   - 完全沒有附內容：HANDOFF.md、INDEX.md、protected-files.json
   - 只有 diff：tests/test_coverage_sim.py
   - 以上檔案的判斷都以封包的描述為準。
4. **我沒有執行任何東西。** 測試、還原演練、hash、前檢都沒有親自跑過，也無法確認 hash 對照表和實際檔案是否一致。下面所說的「實際證據」都是封包附上的 host 端產物。

## 二、我自己核對過的數字

- manifest.json 和 restore-drill.json 的三筆資料完全一致（SHA-256、長度、mode、mtime_ns）：
  - entry 0：f7b2…08f6，158,916,955 bytes，mode 420（0o644）
  - entry 1：b146…4703，454,508,530 bytes，mode 420
  - entry 2：3442…31a3，197,589,952 bytes，mode 384（0o600）
  - 三筆的 matches_original 都是 true。
- 節省空間計算正確：
  - 三個原檔合計 811,015,437 bytes
  - 兩個 gzip 合計 41,419,501 bytes
  - 淨減少 769,595,936 bytes，與封包所說相符
  - 兩份模擬檔 613,425,485 bytes，壓縮成 41,419,501 bytes，與文件寫的「約 613.4 MB → 41.4 MB」一致
- 測試數量：test_archive.py 有 14 個測試函式，其中一個參數化成 4 組，合計 18 個，和 archive-tests.txt 的「18 passed」相符。coverage-tests.txt 顯示 71 passed（含新加的兩個測試）。
- 六項前檢都是 `ok=true`，沒有 waivers 和 problems，line_guard 全部是 null。CLI 版本：claude 2.1.316、gemini 1.4.12、gpt 0.166.0。

## 三、逐項審查

**1. 正確性與刪除範圍**
- 刪除清單寫死在 `SPECS` 的三筆。`load()` 會逐筆比對 original、archive、kind，tar 的 member 必須是 `antigravity`，所以改 manifest 也擴大不了刪除範圍（有 `test_manifest_cannot_expand_delete_allowlist` 測試）。
- `path()` 拒絕絕對路徑、`..`，以及路徑中任何一層 symlink。
- `regular()` 要求是普通檔案且 `st_nlink == 1`。這表示 AGY 候選檔不可能和現役 pin 或證據檔共用 hardlink，與 source findings 一致。
- tar 只透過 `getmembers` 檢查「恰好一個普通檔 `antigravity`」，再用 `extractfile` 串流讀出，沒有使用 extractall，不會有路徑穿越問題。

**2. 資料遺失風險**
- `apply()` 會先把三筆的封存來源全部驗證完（封存檔 SHA，加上解壓後的 SHA 和長度）、確認原檔 SHA 沒變、確認還沒有標記檔，然後才刪第一個檔。
- 每一筆都是先寫 `.archived.json` 標記（排他建立）、記 `delete_intent` 事件，刪除前再算一次 SHA，最後才 unlink 並 fsync 目錄。
- 中斷時用 `recover()` 補救：
  - 原檔還在且 SHA 相符：只移除標記。
  - 原檔不見：從封存還原。
  - 原檔內容不同：拒絕處理，不覆寫。
- `apply_reviewed.py` 用 `except BaseException` 包住整段，Ctrl-C 也會走 recover。recover 和 restore 本身不再拿鎖，所以在同一個 process 內不會被自己的鎖卡死。
- 以上都有合成測試對應：損壞封存、原檔變更、刪到一半中斷、recover 不覆寫已改動的原檔。

**3. 還原證明**
- `restore()` 寫到暫存檔後，依序做 fsync、fchmod、SHA 與長度驗證、utime(ns)，最後用 `os.link` 排他發布。
- 目的地已存在或是 symlink 就拒絕，發布時遇到競態也不會覆寫。
- 三筆實際還原演練的 SHA、長度、mode、奈秒 mtime 都和 manifest 相符。這是 host 端提供的證據，不是我執行的。
- gzip 用 `mtime=0`、`filename=''` 產生，輸出可重現。entry 2 還原後的 mode 是原檔的 0600，不是 tar 裡的 0755，這是正確的，因為目標是原檔逐位元組還原。

**4. append-only 歷史**
- 歷史行沒有被篩選、重寫或覆寫，只是改變儲存形式。
- `run_registered` 在兩個交易鎖內各加一道標記檢查：模擬開始前、以及追加結果前。這樣封存期間不會在原路徑重新長出一份空白歷史，避免歷史分岔。兩個新測試對應這個行為。
- `events.jsonl` 只追加並 fsync。manifest 和 applied.json 都是排他新建。

**5. 現行排程**
- 沒有停用排程。所有操作都以非阻塞方式拿六把營運鎖加兩把模擬器鎖，鎖被占用就不做事。
- 持鎖期間核心帳冊的 hash 必須維持不變。若有帳冊寫入者沒有遵守這些鎖，比對會失敗並自動 recover。這種情況只會「誤判後放棄」，不會遺失資料。

**6. 授權與 gate 綁定**
- `gate_check` 檢查以下各項：
  - files 對照表中每個檔案的 hash
  - 三個必要輸入必須在對照表內
  - 兩位審查者的答案都恰好有一行 `DECISION: PASS`，並含結束標記
  - 答案 hash 符合，ledger 的開始／結束紀錄各恰好一筆，且 packet_sha256 一致、status 為 completed
  - 封包 hash
- 有真實 gate 的接受與拒絕測試。

## 四、阻擋事項

無。

## 五、可選的改進（不阻擋）

1. **證據缺口：** 封包沒有附 `transaction()` 的實作，所以看不到 `.coverage_sim/ledger.lock` 的檔名是否和 LOCKS 一致；coverage_sim.py 是否只有 run_registered 這一處追加，也只能依 source findings 判斷。若兩者不一致，「標記檢查之後、刪除之前」會有一個極窄的競態窗口。因為模擬器只在離線手動執行，風險很低。
2. **讀取端：** 封存後，會讀取 coverage_sim_results 的報表類程式可能把「檔案不存在」當成「沒有歷史」。可以考慮讓讀取端也檢查標記檔；目前只靠文件禁止。
3. **apply_reviewed.py 沒有自己的測試。** 建議補一個合成測試，涵蓋 protected 檔案變動、核心帳冊變動、後檢失敗這三條路徑，確認都會走到 recover。
4. **重跑時的行為和訊息不一致：** 如果上次只刪了一半，重跑 `apply_reviewed` 時 `apply()` 會丟出「already marked; inspect before resume」，但外層 except 會直接自動 recover。結果是安全的，只是和「先人工檢查」的訊息不符。
5. **前檢輸出會被覆寫：** preflight.py 用 `write_text` 寫 `preflight-before/after-apply.json`，重跑會覆蓋舊結果，證據不是 append-only。建議改成排他建立或加上時間戳。
6. **gate 沒有強制完整對照表：** `gate_check` 只要求三個必要輸入在 files 對照表內，沒有檢查對照表是否「恰好等於」封包的輸入清單。這目前靠整合者手動保證，可以改成比對整個集合。
7. **持鎖時間：** 兩次前檢各有最多 90 秒逾時，加上雜湊約 0.8 GB，總持鎖時間可能長達數分鐘，可能讓一次排程延後或略過。建議在兩次排程之間執行。
8. **重試需要新一輪：** ledger 要求開始／結束紀錄各恰好一筆，所以若需要重新審查，必須換新的 round 名稱。這只是會擋下流程，沒有安全問題。
9. **封存檔必須一起備份：** 封存檔一旦損壞，recover 會停在 verify 這一步。文件已經要求封存目錄、manifest 和工具一起備份，請照做。另外，APFS 快照下實際釋放的空間可能和邏輯大小不同，文件也已說明。

## 六、狀態聲明

- 這次 apply 還沒有執行，刪除後的驗證也還沒有證據。
- 本 PASS 只涵蓋上面列出的來源內容和它們聲明的 hash。
- 我沒有執行任何測試或命令，沒有讀取任何預測內容，也沒有替 Gemini 或 Louis 做出任何批准。

END-D138-R1