# D130 初輪審查（r2補證後解除阻擋）

結論：不能PASS，有1項blocker。
我只看了packet內容，沒有執行任何測試，也沒有代替Gemini或Louis核准。這份script只做Gemini的範圍限定核准，並保留Claude/GPT原本的來源紀錄，不會接受Claude/GPT插件。

【Blocker（程式碼本身就看得到）】
最後核准時，script在 `with transaction(ROOT/"data")` 裡面又呼叫一次 validate_result()。validate_result() 會呼叫 F.incomplete_acceptance()，而 F.incomplete_acceptance() 自己也會開 `transaction(B.ACCEPTANCE_LOG.parent)`。從 weekly_usage 和 require_idle 的寫法看，ACCEPTANCE_LOG.parent 就是 ROOT/data，所以同一把鎖被巢狀取用了兩次。
packet沒有附上 flab.ledger.transaction 的原始碼，我無法確認它可以重複進入：
- 如果它用flock：3次付費呼叫都成功後，程式會卡死，而這次唯一的執行機會已經用掉。
- 如果它用lockf：內層關閉時會提前放掉外層的鎖，核准就不再是一個完整的交易。
修法擇一：
1. 交易內改成不加鎖的重檢，例如用已讀出的rows確認最後一筆result之後沒有acceptance_call_started；或
2. 附上transaction可以重複進入的原始碼或測試證據。
修正後請附上新的script SHA重新送審。

【script實際有檢查的條件（修好後可作為PASS依據）】
- 安裝前：候選binary、wrapper、snapshot的SHA；檔案必須是一般檔案、只有一個連結、權限正確。
- 安裝方式：用O_EXCL只建立新檔，不覆寫別的檔案。
- 安裝後：PATH找到的agy必須是專案wrapper；snapshot版本必須是1.4.12、沒有blocker、credits是剛查到的0；只允許agy.cli_settings的一般設定變動，content指紋不變。
- 每次呼叫前：重驗pin/wrapper、baseline、protected hash、code指紋、每週額度和剩餘時間。每次呼叫後再驗一次pin/wrapper。
- 驗收結果：剛好3次呼叫、全部PASS、evidence SHA相符；三份這次呼叫產生的agy_cli.log版本都是1.4.12；既有兩層production證據沒有變。
- 核准時：sources和versions不變；核准後兩層preflight都要通過且沒有waiver。
- 自動更新：只靠官方env加上已驗證的wrapper，script也老實記錄 updater_branch_verified=False。

【非blocker建議】
1. started事件在安裝和安裝後snapshot之前就寫入。如果PATH或版本解析不符，會在0次模型呼叫的情況下用掉唯一一次執行機會。可以先做不呼叫模型的pin檢查。
2. 核准後若preflight失敗，事件會寫restored=False，但baseline其實已經核准了。建議另外記錄approval_recorded。
3. 測試讀的是已安裝的launchd/bin/agy，不是OP/agy-wrapper.sh，而且換掉hash後已不是逐位元相同的檔案。建議加上SHA 93743416…的斷言。
4. 如果_LOG_VERSION_RE有多個group，findall會回傳tuple，導致誤判失敗（這種失敗本身是安全的）。
5. script用_run_line，繞過了run_line的forecast.gemini.lock，需要確認Session.locks()有涵蓋這把鎖（packet沒附）。
6. 如果每次呼叫前的檢查失敗，acceptance_call_started已寫入但沒有finished，會留下要人工復原的未完成狀態（這種失敗本身是安全的）。

【證據更正】
- protected-checks.json共28列，但不重複的路徑只有17個（AGENTS.md、runner等重複出現），所以不能說是「28項」。清單裡也沒有baseline.py、forecast.py、acceptance.py，這些靠code_fingerprint/runtime_fingerprint另外涵蓋。
- smoke只證明wrapper回傳1.4.12且沒有呼叫模型，不代表實機模型驗收成功。