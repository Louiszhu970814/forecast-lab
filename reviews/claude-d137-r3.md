# D137 r3 複審：B1 已解決，部署腳本無阻擋問題

我只根據本封包審查，沒有執行任何工具、測試或 launchctl。下文提到的測試通過數、replay 結果和 host 輸出，都來自封包內的 host 紀錄，我沒有自行驗證。產品部分我只看到 diff，以及 `budget_handoff.py` after 全文、`continuous.py` 的 register 段、未修改的 `live_window.py` 全文和 `require_idle` 全文；其他產品檔案的完整內容沒有看過。營運腳本 `apply_reviewed.py`、`test_deployment.py`、`gate_and_deploy.py`、`kickstart_reviewed.py` 都有完整全文，已逐行核對。

## 結論：PASS

**這個 PASS 的範圍：**
- 只是本輪的設計／程式碼審查結論，不是 Louis 的新核准，也不是部署授權。參賽授權仍以 UTC 2026-11-05T01:49:59.950469Z 的原話為準。
- 不代表已停用排程、已登記、已切換為 live、已 kickstart 或已送出任何預測。依封包，根目錄仍是 D118，disable、bootout、register 和 live 切換都還沒做。
- launchctl 的修改操作在測試裡全部用假物件代替，從未在真機上執行。

---

## 一、B1（r2 唯一 blocker）：已解決

**r2 的問題：**舊版只把 `true` 視為已停用。Darwin 25 可能輸出 `disabled`，結果停用後同一個 gate 無法辨識狀態，也就無法恢復。

**r3 的修正（以 `apply_reviewed.py` 全文核對）：**

1. **`disabled_state` 改為三態，未知情況一律拒絕。**
   - 輸出必須有 `disabled services = {` 這一行，且去尾空白後以 `}` 結尾，否則拋出 `unknown layout`。
   - 只挑出含帶引號完整 label `"org.forecast-lab.cycle"` 的行。像 `org.forecast-lab.cycle.x` 這類前綴相同的其他服務不會被誤認。
   - 同一個 label 出現多行時，以 ambiguous 拒絕。
   - 用 `fullmatch` 只接受 true、false、disabled、enabled 四種值，其他值拒絕。
   - 回傳規則：
     - 找不到這一行 → `None`（沒有覆寫設定）
     - true 或 disabled → `True`
     - false 或 enabled → `False`
2. **初次執行：**`schedule_disabled()` 回傳 `state is True`，所以「沒有覆寫」（`None`）會視為預設啟用，可以繼續。
   - host 實際證據：`target_lines=[]`、`target_override: absent; default enabled`，取值種類有 enabled 2、disabled 5。這證明 Darwin 25.5.0 確實用 enabled／disabled 這種寫法。
   - 測試 `test_actual_readonly_host_sample_has_no_override` 用的是 hash 已鎖定的真實樣本 `host-print-disabled-r3.txt`。
3. **停用後：**`pause_schedule` 最後呼叫 `schedule_disabled(explicit=True)`，`None` 會直接拋錯，必須明確讀到 `True` 才算確認停用。`test_post_pause_requires_explicit_override` 涵蓋了空的 disabled 區塊。
4. **恢復後：**kickstart 腳本用同一個 parser，要求結果必須是 `False`，`None` 和 `True` 都會擋下。所以必須明確讀到 enabled（或 false）才會進行 kickstart。
5. **用同一個 gate 恢復（B1 的核心）：**我推演了「已經停用並卸載後再重跑」的路徑，全部走得通：
   - `prepared.json` 存在，`schedule_was_enabled` 為 true
   - `pause_schedule`：`schedule_loaded()` 回傳 False，不會檢查 pid
   - 讀取狀態只做紀錄，不會因為「已停用」而拒絕
   - 只會再執行一次 disable（冪等），不會 bootout
   - 輪詢立即通過，明確讀到 disabled，記錄 `schedule_paused`

   r2 指出的「停用後無法恢復」已經排除。同時，初次執行時如果已經明確是 disabled，依然會用 `cycle job was already disabled` 拒絕，不會在已停用的排程上強行啟用。
6. **其他相關強化：**
   - 所有 launchctl 都改用絕對路徑 `/bin/launchctl`。
   - 第一次修改前，`schedule_loaded` 和 `schedule_disabled` 的原始 stdout/stderr/returncode 會先寫入 `launchctl-reads.jsonl`。
   - print 輸出中若出現 `pid = N`，就拒絕 bootout。
   - 每個子程序都有 30 秒 timeout，卸載輪詢上限 10 秒。

**仍未驗證：**真機上 disable 之後，print-disabled 是否立刻顯示 disabled；enable 之後是否顯示 enabled；卸載後的錯誤字串是否含 `Could not find service`。這些都還沒真實執行過。不過任何一項不符時都會拋錯並停在安全狀態（排程已停、MODE 不會進 live），影響的是能否順利完成，不會造成不安全。

## 二、部署狀態機的其他核對

- **gate：**
  - 腳本 hash、initial manifest、evidence、兩份 review 的 hash 都要相符。
  - 每份 review 的結論 heading 必須恰好一個且為 PASS，並含 `END-D137-R3`。
  - `reviews.jsonl` 的開始／結束紀錄各一筆，`packet_sha256` 和 `answer_sha256` 要相符。
  - `packet-inputs` 以 sort_keys 序列化後嵌在封包中（本封包末尾就是這段 JSON），並逐一比對 hash，其中包含 `kickstart_reviewed.py` 和 `gate_and_deploy.py` 本身。
  - diff 由 baseline 和 stage 重建，要求逐字一致。
  - 兩個入口都拒絕 `-O` 和 `PYTHONOPTIMIZE`，assert 不會被靜默移除。
- **登記前失敗：**
  - MODE 先改為 local。
  - 先還原所有非 MODE 檔案並核對 hash，最後才還原 MODE。
  - 如果還原失敗，MODE 維持 local（`test_rollback_failure_keeps_mode_local`）。
- **登記後或帳冊狀態未知：**
  - 不刪帳冊、不回滾，MODE 保持 local，排程維持停用。
  - `ledger_status` 只允許「原始前綴＋零或一筆完全相同的候選」。
  - 重跑時 `register` 是冪等的（登記數 = 1）。
  - 恢復時只接受 initial、target 或 local 三種位元組，備份和帳冊備份的 hash 都必須不變。
- **kickstart 前的檢查（都在持有六把鎖時進行）：**
  - 最後一筆事件是 `activation_complete`
  - 產品檔案 hash 相符
  - 沒有 STOP_SUBMIT
  - `require_active` 通過
  - plist hash 等於 pause 時記錄的值

  之後才執行 enable 和 bootstrap，釋放鎖後再讀狀態確認，最後執行不帶 `-k` 的 kickstart。如果 kickstart 中途失敗，`operations.jsonl` 的最後一筆不再是 `activation_complete`，必須重跑同一個 gate 的 apply（會先 disable 再 bootout），這和封包描述的流程一致。
- **測試數量：**`test_deployment.py` 依參數化展開共 27 個，與 host 紀錄的 27 passed 一致（這是數數核對，不是我執行的結果）。

## 三、產品 diff

封包說明這 18 個產品檔案與 r1/r2 位元組相同，manifest 也已列出。我順帶看了 `ancestors()` 的實際路徑：
- 鏈結是 main → D118（v2）→ D105（v1 legacy）
- 根 accounting_start 是 11/02T13:03
- 窗口落在 D118 之內，終點不超過 11/10T04:00
- caps 和固定 hash 都繼承上一段

這和 budget-replay 前後一致（26/25/24 次，上限 130/200/130）相符。沒有發現新問題，維持兩家先前「無 blocker」的結論；本輪不重新開啟產品審查。

## 四、Blockers

無。

## 五、非阻擋建議與殘留風險（不影響本次 PASS）

1. **kickstart 引用的 apply 程式沒有先驗 hash。**`kickstart_reviewed.py` 在驗 hash 之前，就已經在模組頂層執行 `from apply_reviewed import disabled_state`。如果 `apply_reviewed.py` 在啟用後被改動，就會先執行到未審查的程式碼。建議下一版改為先比對 `gate['deployment_script_sha256']` 再匯入。這屬於本機信任邊界的問題，不構成阻擋。
2. **恢復後的確認不擋 kickstart 之外的啟動。**enable 和 bootstrap 之後才讀狀態確認；這時如果確認失敗，排程其實已經載入，600 秒的計時器仍會觸發。由於這時已完成 `activation_complete`，live 也合法，所以不算安全問題。但在紀錄上應寫成「確認失敗、排程可能已在運行」，不要寫成「已阻止」。
3. **舊 D118 的 MiniBench 會暫停作答。**登記前失敗並還原後，排程仍維持停用，D118 MiniBench 會中斷，直到用同一個 gate 修好並啟用為止。封包已說明不另建恢復舊 D118 的流程；截止時間不變，所以中斷期間就是損失的作答時間，事實文件要記錄。
4. **路徑檔尾的換行。**`gate_and_deploy.py` 讀 `stage-path.txt` 和 `baseline-path.txt` 時沒有 strip，`apply` 卻有 strip。如果檔尾有換行，gate 會失敗並停下，不會造成不安全，但會擋住部署。
5. **事實文件的追加時機。**依封包，事實文件只在 kickstart 和觀測之後才追加。要注意：一旦 `D137-MAIN-LAUNCH.md` 等受控檔案被追加，之後再用同一個 gate 恢復會出現 `recovery source differs`。所以追加應確定在「不再需要重跑 apply」之後。
6. **掃描結果的雜湊。**`version-comparison-scan.txt` 的 SHA 是 e3b0c442…，也就是空檔的雜湊。這與「rg 沒有命中」一致，但單憑空檔無法和「沒有執行」區分。如果要當證據，建議同時保存命令和 returncode。
7. **卸載後的錯誤字串。**建議在真機上第一次 bootout 後，把 print 的實際錯誤字串連同 `launchctl-reads` 一起保存，以補上第一節提到的未驗證部分。

## 六、證據分類

- **封包提供的 host 證據（我沒有自行驗證）：**
  - 3393 passed（3368 + 25；collection-delta 無刪除）
  - stage 569 passed
  - 部署故障測試 27 passed
  - 6 組 budget replay 前後一致，真實預測 0、POST 0
  - 01:55 的官方 GET metadata（bot 311763 is_bot=true；主賽 33121 is_ongoing=true）
  - Darwin 25.5.0 唯讀 print／print-disabled 的投影（無覆寫、未運行、無 pid）
- **只是宣稱或尚未驗證：**
  - launchctl 的 disable、bootout、enable、bootstrap、kickstart 在真機上的行為
  - 實際登記、前檢和首輪結果
  - M4 官方分數和結算驗收（未完成）
  - D114 只有 24／168 輪，`m3_passed=false`
- **審查方提出的建議：**第五節各點，都不阻擋本次 PASS。

END-D137-R3