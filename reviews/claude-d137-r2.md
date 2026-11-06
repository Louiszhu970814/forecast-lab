# D137 r2 完整 patch／部署腳本審查（Claude）

## 一、審查範圍與證據等級

**我讀過的內容（只限封包內文字）：**
- 完整差異：涵蓋 18 個受審目標，包括 PLAN、README、3 個 config、3 份 docs、`budget_handoff.py`、`continuous.py`、2 份 fixture、6 份測試。
- 完整 after 版 `src/budget_handoff.py`。
- `continuous.py` 第 1–106 行，含完整 `register`。
- `cycle.py` 第 140–194 行、完整 `live_window.py` 與完整 `require_idle`。
- 私人營運腳本全文：`apply_reviewed.py`、`test_deployment.py`、`gate_and_deploy.py`、`simulate.py`、`kickstart_reviewed.py`。
- host 證據：`full-tests-r3.txt`、`deployment-fault-tests-r2.txt`、`stage-smoke-r2.txt`、`collection-delta.json`、`budget-replay.json`、`official-metadata.json`、`d114-historical-audit.txt`，以及 VERSION 消費者掃描、輸入 SHA 清單、目標 manifest。

**封包沒提供、我不能宣稱看過的內容：**
- `continuous.py` 第 106 行之後、`cycle.py` 其餘部分。
- `budget.py`、`submit.py`、`forecast.py` 的 preflight、`advise.lock_all`、`launchd/run.sh`。
- 實際安裝的 plist、`AGENTS.md` 全文、PLAN 的未改動部分。

**證據等級：**
- 3393／569／17 PASS、預算重播結果、官方 GET 結果、D114 稽核，全部是整合者在 host 上實際量測的證據。
- 我沒有執行任何測試、指令或 launchctl。以下所有判斷都是靜態閱讀推論。
- 本審查不等於啟用核准，也不代替 Gemini 的獨立審查。

## 二、授權與範圍：沒有發現矛盾

1. **授權解讀合理。** 原話「複審完 修改完可以 就直接開始正式賽了」是在 D136（主賽前清理）期間說的，MiniBench 早已是正式參賽，所以解讀成加入主賽 33121 是合理的。條件「複審完、修改完」由 D136 在 01:53:05Z 套用完成來滿足。授權時間 01:49:59.950 早於 D136 套用，這不構成矛盾。原話建議持續逐字保存在 `authorization.json`。
2. **沒有新增額度或續期。** 130／200／130 上限、根 `accounting_start` 11/02T13:03Z、截止 11/10T04:00Z 都沒變。`budget-replay` 的 6 組 before 與 after 完全相同（26／25／24 已用、accounting_start 相同），這是實際量測，支持「共用一份額度、不重設」的說法。
3. **證據限制有如實揭露。** PLAN §5、§10 M6、§15 D137、§16 v1.91 與 D137 任務單都明寫：D114 只完成 24／168 輪、`ready_for_replay=false`、`m3_passed=false`；M4 分數尚未結算；不得作準確度或完整 ITT 結論。沒有冒稱 M3 或 M4 已通過。
4. **固定設定沒動。** run_config 與 quota 的 hash、prompt、模型、權重、600 秒排程都沒改。protected fixture 只有 `cycle_campaign.json` 的 hash 改變，和 P5 說法一致。
5. **未驗證項目。** `AGENTS.md` 不在封包內。它的 hash 沒變，所以我無法確認裡面有沒有類似「主賽須待 M4」的舊規則。這是未查項目，不算 blocker，建議整合者用 rg 確認一次。

## 三、產品程式審查（budget_handoff／continuous／config／tests）：沒有 blocker

**實際鏈 D105→D118→D137 的靜態追蹤：**
- `ancestors(d137)` 走 v2 分支：版本、key 集合、授權 `PLAN D137`、沒有 rollover 欄位，全部符合。
- `registered(d118)` 的 hash 會與 `source_sha256` 6239cd… 比對。舊的 `submit_mode` 對 d118 的 `campaign_sha256` 也是同一值，前後一致。
- 遞迴 `ancestors(d118, seen=(d137,))` 時，`len(seen)=1`，於是走 legacy 路徑。`_legacy_source` 會驗 d105 完整、d105 唯一的後繼是 d118，回傳 `[d105]`。
- 最後 chain=`[d118, d105]`，長度 2，沒有重複。
- 時間窗：11/03 04:00 < 11/05 01:49:59.950 < 11/10 04:00，且 end 等於來源 end，沒有延長。
- `accounting_start` 必須等於 `chain[-1]`，也就是 d105 的起點 11/02T13:03Z，符合 config。
- caps、兩個固定 hash、`expected_new_questions`、`repair_reserve`、`urgent_slack_s` 都和 d118 相同。venues 限定在 {main, minibench}。d118 的後繼只有 d137。

**防護有成立：**
- 第三跳：`ancestors(mini, seen=(third, main))` 一進入就因 `len(seen)>=2` 拒絕。
- 分叉：由 `transaction` 加上 successor 檢查擋下，8 執行緒測試確認只有一個成功。
- root 被改寫：連同重新計算 hash 也一樣，會因 d118 內的 `source_sha256` 不符被擋。
- 無關窗口重疊：`overlap_ids` 只豁免真正的祖先。
- v10 未登記或 runtime 被改：`accounting_start` 對 v10 先比對登記 hash，拿掉 handoff 也不能領預算。
- v1 與 v9 的相容性維持：v1 設定配 v10 policy、或 v2 設定配 v9 policy，都會被拒。

**其他確認：**
- 實際 ledger 能登記：`simulate.py` 在真實 ledger 副本上 `C.register` 成功，而且 allowance 相同。這是實際量測，間接證明 D137 不會撞上真實 ledger 裡的其他重疊窗口。
- `live_window.bounds` 要求 start／end 字串和 `campaign_sha256` 與 campaign 完全相同。config、fixture 與 manifest 的 hash 彼此一致。
- 測試計數 3368→3393，`removed=[]`。新增的 25 筆中，24 筆是 handoff 測試，1 筆是 v10 E2E 參數；v9 參數有保留。

**P4 與 P5 的說法和程式碼相符：**
- `test_expired_source_remains_blocking` 的 'active' 其實是由 register 的「window 必須 active」條件命中。
- 15 種 invalid 案例是整體拒絕契約，不是逐條 guard 的覆蓋證明。

## 四、部署腳本審查

### Blocker B1：`schedule_disabled()` 依賴的 launchctl 輸出格式很可能和 host 不符，會在已停用排程後卡死，而且沒有任何受審路徑能恢復

**現況：**
- 程式用 `re.search(r'"org\.forecast-lab\.cycle"\s*=>\s*true', print-disabled 輸出)` 判斷是否停用。
- 就我所知，macOS 13 Ventura 起，`launchctl print-disabled` 改印 `"label" => disabled／enabled`，不再印 `true／false`。
- 本審查執行環境是 Darwin 25.5.0；若 host 是同一台機器，正規式就永遠不會命中。
- 封包沒有任何一份 host 上的真實 `print-disabled` 輸出；17 個故障測試裡，`schedule_disabled` 被 Fake 固定回傳 False。這個解析器從沒碰過真實資料。

**會發生什麼（推論）：**
1. 第一次執行時，「人類原本已 disabled 就拒絕」的檢查會失效，因為永遠判成未停用。
2. `pause_schedule` 在 `disable` 和 `bootout` 都成功之後，`not self.schedule_disabled()` 為 True，於是丟出 'schedule pause unconfirmed'。
3. 這個例外發生在 try 區塊之外，產品檔案沒動，MODE 仍是 D118 live，但 timer 已移除而且被 disable。MiniBench 就此停止作答。
4. 再跑同一 gate 的 apply，會在同一個點失敗。`kickstart_reviewed.py` 要求最後一筆是 `activation_complete`，所以無法恢復。
5. 唯一出路是改 `apply_reviewed.py`，但 SHA 一變，gate 就會拒絕，必須重審。
6. 這違反 P1 與 P3 的契約：失敗要能由同一 gate 的 apply 回到 `activation_complete` 再 resume。

這是 fail-closed，不會造成錯誤送出，但它直接擋住被授權的目標，而且會讓已核准的 MiniBench 在截止前停擺。修正成本很低，所以列為 blocker。

**可實作的修正：**
1. 把解析改成三態，同時接受新舊兩種格式，並對歧義或未知值 fail closed：
   ```python
   PAT = re.compile(r'^\s*"org\.forecast-lab\.cycle"\s*=>\s*(true|false|disabled|enabled)\s*$', re.M)
   found = PAT.findall(out)
   if len(found) > 1: raise ValueError('ambiguous disabled state')
   if not found: return False          # 從未被覆寫＝預設 enabled
   return found[0] in ('true', 'disabled')
   ```
2. `pause_schedule` 的確認改成正向確認：輸出中必須明確出現此 label，且值為 true 或 disabled。`bootout` 後的 `schedule_loaded()` 改成有界輪詢（例如每 0.5 秒查一次，最多 10 秒），避免非同步卸載造成誤判。
3. 在任何 launchctl 變更之前，先用唯讀方式把 host 真實的 `print-disabled` 與 `print` 原文存入 job 證據。若無法解析出已知狀態，就在 disable 之前直接拒絕。
4. 在 `test_deployment.py` 新增解析器單元測試，涵蓋：舊格式 true／false、新格式 disabled／enabled、label 不存在、未知值或重複 label 要拒絕。最好再加一筆整合者從 host 唯讀擷取的真實輸出樣本。
5. 所有 launchctl 呼叫改用絕對路徑 `/bin/launchctl`。`gate_and_deploy` 會把 `ROOT/launchd/bin` 放到 PATH 最前面，應避免任何 shim 被誤用；`kickstart_reviewed.py` 也一樣。
6. `apply_reviewed.py` 與 `test_deployment.py` 的 SHA 會因此改變，需要重產 gate 輸入並進入 r3 審查，同時重跑故障測試（17+N）。產品 18 個目標沒變，3393 與 569 的證據可以沿用。

### 部署腳本的其他確認（不是 blocker）

- **順序正確。** 六鎖 → `gate_check` → STOP 檢查 → `require_idle` → 備份與 `prepared.json` → 先 disable 再 bootout → MODE 改 local → 前檢 → runtime → pre smoke → config → 驗 candidate 合約 → register → ledger 必須是精確前綴加唯一候選 → post smoke 與前檢 → 檢查 protected 與 deployed bytes → 最後才寫 live。
- **登記點前後的處理正確。** 登記前失敗：先還原 MODE 以外的檔案並驗證，最後才還原 MODE（P2，有 rollback 失敗注入測試）。登記後失敗：維持 local，同一候選可冪等 roll-forward（`register` 遇到同 hash 的 prior 會直接 return），絕不回滾 ledger。ledger 半行壞掉會走 `stopped_unknown_ledger`。
- **窗口過期時的復原會被拒。** 截止後再 recovery，`contract()` 和 `register` 的時間條件都會拒絕，不會變相延長窗口。
- **gate 綁定方式。** 從不可變 baseline 加 stage 重建 diff，逐字比對 `patch.diff`；packet-inputs 的 SHA 嵌在 END 之前；review 答案必須只有一個 PASS heading 且含 `END-D137-R2`。設計合理。

## 五、非 blocking 提案

1. **原 D118 排程沒有受審的恢復路徑。** 登記前失敗（或 B1 這類 pause 失敗）會讓 D118 檔案保持 live，但排程停著，MiniBench 等於停擺。建議新增受審的 `resume_original`：條件是所有檔案等於 initial、ledger 等於 backup、STOP 不存在、`W.require_active` 對原 D118 合約通過、plist SHA 不變，才 enable 加 bootstrap。不然至少要在任務單寫清楚這是需要人類決定的手動步驟。
2. **bootout 前確認沒有執行中的實例。** bootout 會對執行中的實例送出 SIGTERM。六鎖和 `require_idle` 已排除真正的 worker，但一個剛被 timer 啟動、還卡在拿鎖或剛寫 `overlap_skipped` 的 cycle 仍可能被終止。建議 bootout 前先解析 `launchctl print` 確認沒有 pid（或 state 不是 running），否則就拒絕，讓「不殺 worker」成為嚴格保證。
3. **把 assert 換成明確 raise。** `gate_and_deploy.py` 與 `kickstart_reviewed.py` 大量用 assert；若有人以 `python -O` 或 `PYTHONOPTIMIZE` 執行，這些檢查會全部失效。
4. **kickstart 要確認狀態。** bootstrap 之後應再確認已 loaded 且未 disabled（使用修正後的解析器），並把原始輸出記入 `operations.jsonl`。
5. **字串比較版本號的風險。** P6 只掃了 continuous 的 import 別名，不涵蓋 `continuous.py` 內直接使用 VERSION 的地方，也不涵蓋版本字串的大小比較。`"continuous_v10" < "continuous_v5"` 在字典序下成立，建議用 rg 搜尋 `continuous_v` 搭配 `<`、`>`、`sorted`、`max` 的用法並附上輸出。v10 E2E 參數通過可以降低這個風險，但不能證明沒有分支漏掉。
6. **主賽路徑的證據缺口。** 封包裡沒有顯示主賽 venue 在可送出白名單下實際 poll 和送出的 E2E 測試，D113–D115 主要是試驗場地。建議把首輪安全 metadata 定為明確的停損條件：主賽題數、支援題數、attempt 與 POST 狀態、有沒有 unknown 寫入。另外要揭露：主賽和 MiniBench 共用 130／200／130，`expected_new=10` 與 `repair_reserve=2` 原本是為 MiniBench 設定的，主賽題量可能排擠 MiniBench 覆蓋率（P7 已提及「不承諾覆蓋全部題」）。
7. **文件改動的時間點。** `kickstart_reviewed.py` 要求 gate 內的檔案（含 D137、HANDOFF、INDEX、PLAN）hash 不變，所以只能在 kickstart 完成後才補寫「已啟用」證據。README 的「D118 live 窗口」與 INDEX D133 列的「主賽未啟用」，啟用後也要一併更新。
8. **`require_idle` 會讓復原延後。** 登記後停在 local 的那段期間，若反覆遇到「題目即將截止」，`require_idle` 會讓 recovery 一直延後。建議在操作紀錄中記下每次被拒的原因與時間，方便事後清點漏答。

## 六、結果彙整

| 類別 | 內容 |
|---|---|
| 實際證據（整合者量測） | 3393 PASS／569 PASS／故障 17 PASS（fake hooks）、預算重播 before 與 after 相同、官方 GET（bot 311763 `is_bot=true`、33121 `is_ongoing=true`）、D114 24／168 未完成 |
| 我的靜態結論 | 產品 handoff／continuous／config／tests 沒有 blocker；授權與範圍沒有矛盾；部署腳本有 1 個 blocker（B1） |
| 不是平台實測 | 所有 launchctl 行為、真實停用與恢復排程、12 項真實 preflight、主賽首輪 |
| 提案 | 第五節 1–8 項 |

## 結論：REQUEST_CHANGES

唯一的 blocker 是 B1：`apply_reviewed.py` 用 `=> true` 判斷 launchd 停用狀態，很可能不符合 macOS 13 起 `=> disabled` 的輸出格式，而且從未用真實輸出驗證過。若不符，會在已 disable 加 bootout 之後失敗，讓 MiniBench 停擺，而且同一 gate 永遠無法到達 `activation_complete`。

請照第四節修正解析器、改用絕對路徑、補唯讀真實輸出證據與解析器測試，再進入 r3 審查。產品 18 個目標沒有 blocker，可以原樣沿用。本結論不構成啟用核准；修正之前不應執行 `gate_and_deploy`。

END-D137-R2