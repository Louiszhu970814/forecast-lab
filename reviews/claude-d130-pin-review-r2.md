# D130 Gemini pin／有界維護審查

官方CLI；對應來源SHA見 d130-pin-manifest.json。

結論：PASS。這只是Claude的設計與程式審查結果，只適用於script SHA 9ec3c9ff…3487和wrapper SHA 93743416…5dbf，任一檔案改動就要重審。這不代表實機驗收已成功，也不代表Gemini或Louis已核准。script的docstring寫明要等Gemini也獨立審查後才可執行。這個PASS不包含接受Claude或GPT插件。

【r1的blocker已解除（依據是ledger.py原始碼）】
- transaction會先把路徑轉成實際路徑，再為每條路徑配一把RLock，並用目前thread的active集合記錄已持有的鎖。
- 同一個thread再次進入時，會直接往下執行，不會重開ledger.lock，也不會再上一次flock，更不會提早解鎖。只有外層結束時才會釋放flock。
- 所以approval交易內的validate_result和incomplete_acceptance不會卡死，也不會讓外層交易中途失去鎖。
- 就算ACCEPTANCE_LOG.parent實際上不是ROOT/data，兩邊鎖的也是不同檔案，同樣不會卡死。
- 送審方提供的smoke結果和原始碼的行為一致：內層退出後，外部進程取鎖得到23（仍被佔用）；外層退出後得到0（可取得）。這是送審方的證據，我沒有執行過。

【其他證據核對】
- r1建議4已解決：_LOG_VERSION_RE只有一個group。
- r1建議5已解決：Session的LOCKS有包含forecast.gemini.lock。
- protected清單我核對過，確實是28筆、17個獨立路徑，更正後的描述正確。

【仍有效但不阻擋的建議】
程式沒有改，所以r1的建議1、2、3、6都還在。這幾種情況失敗時都是安全的，所以不影響PASS。

【剩餘風險（只是提醒，不算blocker）】
可重入只在同一個thread內成立。如果quota.snapshot或take_snapshot在交易內改用別的thread去拿同一把ledger鎖，還是會卡死。packet沒有附這兩個函式的原始碼，建議執行前確認一下。