### 審查身分與範圍

我是 Forecast Lab 指定的設計／程式審查者（Claude），這是 D137 r1 審查。

- **審查依據：** 只使用本封包，包括相對於 D136 基線的完整 diff、`budget_handoff.py` 修改後全文、`continuous.py` 第 1–106 行（含完整 `register`）、`apply_reviewed.py`、`test_deployment.py`、`gate_and_deploy.py`、`simulate.py`、未修改的 `live_window.py`、`budget.py` 第 123–215 行、`submit.py` 第 225–280 行、`require_idle`、各項 host 證據與 SHA 清單。
- **沒有讀到全文的部分：** repo 其他檔案、`continuous.py` 第 106 行以後（只看到 diff 片段）、cycle／scheduler 主流程、`forecast.preflight`、`advise.lock_all`、flab 底層模組、`test_budget_handoff` 的 helper（`campaigns`、`finished`、`reserve`）、私人 `authorization.json`，以及 `reviewed-files-r1.json` 本體（我假設它等於封包中的 manifest）。
- **沒有做的事：** 我沒有執行任何測試，也沒有重算任何 SHA。下面提到的 3393／15／569 passed、budget replay 和官方 GET，都是整合者在 host 上量到的結果，我只檢查它們內部是否一致。
- **本審查 PASS 的意義：** 只代表工程審查通過。它不是 Louis 的新授權，也不代表主賽已經啟動、已經送出預測、M3 已完成、M4 已結算，或準確度已經驗證。Gemini 的獨立審查仍然是部署的必要條件。

---

### 一、實際證據（整合者量測，我只核對一致性）

1. **collection-delta：** 3368 → 3393，removed 為空。新增 25 項 = `test_main_launch_handoff.py` 的 24 項 + `test_m3_integration` 新增的 `[continuous_v10]` 1 項。我逐一數過 24 項的組成：1 + 1 + 15 + 1 + 1 + 1 + 1 + 2 + 1 = 24。參數改成 `["continuous_v2","continuous_v9",Q.VERSION]` 之後，舊的 v9 案例 id 仍然保留，符合「補留 v9，沒有偷偷少跑」的說法。
2. **deployment-fault-tests 15 passed：** 我數了 `test_deployment.py`，共 3+3+1+3+1+1+1+1+1 = 15 項，數量一致。這些測試全部使用 Fake（假的 preflight／smoke／contract／register，以及暫存 root），**不能當作平台實測**。
3. **budget-replay.json：** 在 metadata 副本上用 stage 程式真正呼叫了 `C.register(d137)` 並沒有報錯，代表真實 ledger 上的 D105→D118→D137 鏈、hash、重疊例外、前一段窗口仍有效等檢查，都在副本中通過。三條線 × 兩個 tier，前後的 allowed、used（26／25／24）、cap（130／200／130）、protected 與 accounting_start（11/02T13:03Z）完全相同。real_model_predictions=0、POST=0。
   - 限制：這裡的「before」是用新程式跑 d118 campaign，並不是用舊程式跑。新舊程式對 v9 的相容性要靠單元測試支撐。
   - 另外，`quota.snapshot` 會做一次 quota metadata 查詢，這不是預測。
4. **official-metadata：** 01:55:17Z 只用 GET 確認 bot 311763 的 is_bot=true，主賽 33121（`fall-futureeval-2026`）is_ongoing=true。
5. **d114-historical-audit：** 24／168 輪，`ready_for_replay=false`、`m3_passed=false`。D137 任務單、PLAN 和 HANDOFF 都如實寫出這項限制，沒有把它說成已通過。
6. **SHA 交叉一致：**
   - manifest 的 `cycle_campaign.json` = 572cb04c…，和 d121／m5 兩份 golden fixture 一致。
   - campaign 的 `source_sha256` 6239cd39… 等於舊 `submit_mode` 的 d118 `campaign_sha256`。
   - 新 `submit_mode` 的 start／end 與 campaign 的 `budget_window_start`／`end` 逐字相同。

---

### 二、權限／範圍核對（沒有發現矛盾）

- **授權解讀：** 「複審完 修改完可以 就直接開始正式賽了」是在 D136 處理「主賽前文件／dead code 盤點」時說的。當時 MiniBench 已經在 live，所以「正式賽」解讀成當季主賽 33121 是合理的。整合者的做法也偏保守：不新增額度、不延長截止、不重設 cap、不加入 bot_testing_area／Cup／主站，還加了雙審門檻。
  - 剩餘風險：原話沒有逐字講到「33121」或「第二跳」。這是解讀，而且我看不到 `authorization.json` 本體。D137 的文字已經把它定位為有限准入，並保留 M3／M4 的缺口，所以我不列為 blocker。
- **與 D117／D118 的關係：** D117 寫的「主賽未獲此授權」被 D137 明確取代。D118 的「無自動續期」在 D137 保留：終點仍是 11/10T04:00Z，v2 程式也禁止 `end > source end`。
- **與 D136 的關係：** D136 寫的「不新增主賽／窗口／額度授權」只限 D136 範圍，和 D137 不衝突。
- **M6 改寫：** 原本是「依 M4 與 M5 結果加入主賽」，現在改成依 Louis 的直接指示調整時程，並註明 M4 結算與完整 M3 觀測尚未完成，沒有冒充已通過。

---

### 三、程式審查：`budget_handoff.py` 與 `continuous.py`

我逐條追蹤了實際的 D105（根，沒有 handoff）→ D118（v1，`continuous_v9`）→ D137（v2，`continuous_v10`）路徑：

- **`ancestors(d137, seen=())` 的檢查：**
  - v10、v2、`PLAN D137` 和欄位集合都相符。
  - `registered(d118)` 會核對 hash。
  - 接著遞迴到 `ancestors(d118, seen=(d137,))`，走 legacy 分支 `_legacy_source`，驗證 D105、v9 與 D118 授權、D105 沒有 handoff，以及 D105 的唯一後繼是 D118。
  - 得到 chain=[d118, d105]，長度 2。
  - 起點必須嚴格晚於 d118 起點且早於 d118 終點；`end ≤ d118 end`。
  - `accounting_start` 等於 `chain[-1]`（根）的起點。
  - 六項 cap、固定設定與預留參數必須和直接來源相同。
  - venues 必須是 {main, minibench} 的子集合。
  - d118 的後繼只能是 d137。
- **第三跳：** 會遞迴到 `seen` 長度為 2，觸發 `exceeds two links`。測試 `test_third_link…` 有覆蓋。
- **循環與自指：** 自指和循環都會被 `identity in seen` 或 source id 檢查擋下。分叉由 successor 唯一性檢查加上 `transaction` 擋下，有並行測試覆蓋。
- **`register` 的重疊例外：** 例外從「唯一來源」改成 `overlap_ids`（整條祖先鏈）。v1 路徑的例外集合仍然只有 {source}，相容性不變；沒有 handoff 的政策例外集合是空的。有非祖先重疊的測試。
- **`accounting_start`：**
  - v10 的 runtime 一律必須已登記，而且 hash 要相符，所以拿掉 handoff 也無法取得預算。
  - 被取代的 d118 或 D105 runtime 會被 `superseded` 拒絕，因此登記之後，就算把舊設定還原，也拿不到預算。
  - 只剝除 `questions` 的規則和 D118 正在生產中使用的路徑相同。
- **`submit.py` 的 live 送出閘門（未修改）：**
  - 主賽 33121 會以 `name="main"` 命中設定。
  - 必須同時在 campaign venues 中、`submit_whitelisted=true`，live_window 的 hash 也要相符。
  - MiniBench 仍走 ongoing 輪次的發現路徑。
  - STOP_SUBMIT 仍會在每次 POST 前檢查。
- **`live_window`：** 新窗口約 4.9 天，不超過 7 天，start／end 與 campaign 一致，campaign 沒有 rollover。

結論：沒有發現能繞過 cap、讓窗口延長、開分叉或第三跳、讓未登記的 v10 拿到預算，或讓舊 campaign 重新取得額度的路徑。

---

### 四、部署腳本審查（`apply_reviewed.py`、`gate_and_deploy.py`）

- **gate 鎖定：**
  - 部署腳本自身的 SHA、initial manifest、所有受審輸入與兩份審查答案都被釘住。
  - 審查答案必須剛好有一個結論 heading 且為 PASS，並包含 `END-D137-R1`。
  - 審查答案還要和 `reviews.jsonl` 的開始／結束紀錄以及封包 SHA 對得上。
  - stage 的位元組要和 files 清單一致，protected 檔案要和 initial 一致。
  - 重新進入時不能更換 gate。
- **執行順序：** 先在持六鎖下做 idle／STOP 檢查，然後依序是：
  1. 寫入 local。
  2. 在尚未登記時跑 `preflight-before`。
  3. 寫入非設定檔。
  4. 跑 `smoke pre`（刻意排除需要新 config 的三個 hash 測試）。
  5. 寫入 campaign／venues。
  6. 用候選的 mode 位元組跑 `contract()`，此時不讓它生效。
  7. 在子程序中用新程式 `register`。
  8. 檢查 `ledger_status`。
  9. 跑 `smoke post`（加入 config／numeric／d121）和 `preflight-after`。
  10. 再次比對 protected 與已部署位元組的 hash。
  11. 最後才寫入 live。

  順序和契約一致。
- **登記前失敗：** 會恢復原檔、刪除新檔，ledger 不變。
- **登記後或狀態不明：** 保持 local，不回滾 ledger。同一份候選重跑時，`register` 是冪等的（已有同 hash 的登記就直接返回）。
- **`ledger_status`：** 只接受「原始前綴加上恰好一筆候選登記」。非 JSON 的尾巴會變成 `stopped_unknown_ledger`，fail-closed。如果意外中斷後 scheduler 自己先用 d137 登記了，比對 campaign 和 sha（不比對 `at`）仍然能 roll-forward。
- **鎖與排程：** 腳本內沒有 kickstart，沒有移除 STOP_SUBMIT，也不會 kill 任何程序。

---

### 五、Blocking 缺陷

**無。** 在本封包可見的範圍內，我沒有找到會造成額度重設、窗口延長、越權場地、ledger 回滾，或繞過 live／STOP 閘門的工程 blocker。

---

### 六、非阻斷建議（可實作，不影響 PASS）

1. **local 模式下 600 秒排程的實際行為需要補證據。**
   - 契約寫「登記後失敗須保持 local、不啟動排程」，但腳本只是不 kickstart，既有的 launchd 600 秒排程仍會照常觸發。封包沒有提供 cycle 在 local 模式下的程式碼。
   - 如果 local 模式仍會對 live 題目呼叫模型，就會消耗共用額度，並可能影響「每題一次」的狀態。
   - 建議：在 D137 證據中附上 cycle 在 local 下不會啟動模型或寫入的程式依據，或 D118／D136 的實測紀錄。如果做不到，就在登記後失敗時明確 `launchctl disable` 或 bootout 該 job，並記錄下來（不能移除既有 STOP_SUBMIT）。
2. **`restore_before_registration` 應該最後才恢復 MODE。**
   - 目前它依 files 的順序恢復，`config/submit_mode.json` 會先回到 live，之後才恢復 src。如果中途發生 rollback mismatch 而例外離開、釋放鎖，就會在 live 模式下跑混合程式。新程式對 v9 相容，舊 continuous 加新 handoff 也可用，所以風險低，但順序應該改。
   - 建議：先恢復並驗證所有非 MODE 檔案，最後才原子寫回 MODE；恢復失敗時保持 local。另外補一個 fault test。
3. **kickstart 指令不在受審腳本內。** 建議把確切指令（無 `-k`）、執行時間，以及「只在 `activation_complete` 之後執行」的條件寫進 `operations.jsonl`。啟用後也應該對磁碟上的 mode 和 campaign 做一次唯讀的 `W.require_active` 與 preflight 摘要。
4. **測試精準度：**
   - `test_expired_source_remains_blocking`：因為 main 的終點等於 mini 的終點，`now` 等於 mini 終點時，會先被 `register` 的「window must be active」擋下（訊息同樣包含 active），根本沒走到「source must still be active」分支。在 v2 規則下這個分支其實到不了，所以應該改測試名稱或註解。
   - `invalid_second_handoff` 的 15 種情況都只寫 `pytest.raises(ValueError)`，沒有 match。其中 future-source 和 source-end 很可能是被通用窗口檢查擋下，而不是 v2 的專屬檢查。建議加上 match 字串，避免「因為錯的理由而通過」。
5. **註解寫得比實際多。** `test_numeric.py` 和 `test_d121_fail_soft.py` 的新註解寫「campaign／mode／venue hashes」，但兩份 fixture 實際只更新了 `cycle_campaign.json` 的 hash。建議改成「只更新 cycle_campaign 的授權 hash」。
6. **`VERSION` 語意變更需要 grep 證據。** `continuous.VERSION` 從 v9 變成 v10。封包只顯示三處 tuple 有補上 v9。建議附一份全 repo 引用 `continuous.VERSION`／`Q.VERSION`／`C.VERSION` 的清單，證明沒有其他地方用等值判斷，悄悄把 v9 排除掉。
7. **營運提醒（不是程式缺陷）：**
   - 主賽與 MiniBench 共用剩下約 104／175／106 次額度，到 11/10 可能用完，造成漏答。建議每日觀測 metadata 中的已用量和 skip 原因。
   - 11/10T04:00Z 之後主賽會停止，再參賽需要另外授權。
8. **diff 與 manifest 的對應。** `patch.diff` 和 stage／manifest SHA 的對應是整合者自己量的，gate 沒有用 before 加 diff 重建去核對。建議日後在 gate 中加上這一步。

---

### 七、仍然欠缺、不能宣稱的事

- D114 只有 24／168 輪，`m3_passed=false`。
- M4 的官方分數和真實結算驗收尚未完成。
- 不能據此對準確度或完整 ITT 下任何結論。
- 實際六鎖套用、登記時間、排程進入 active、模型實際開始和 POST 成功，目前都沒有證據，必須在 D137 任務單分開記錄。
- 本 PASS 不能取代 Gemini 的獨立審查，也不代表已經開跑。

## 結論：PASS

END-D137-R1