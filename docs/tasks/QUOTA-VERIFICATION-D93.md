# D93：未使用第三方桶的滑動重置日期

D92 的有界補充量測在第一筆後依契約停止。這次沒有並行 agy，型號日誌確認指定 Gemini 4 Argon Medium，Gemini 週／5 小時桶都下降且各自 reset_time 不變；但原 `shared_group_consumed` 因不相關的第三方桶 reset_time 改變而回 false，最終 model_check 記為 undetermined。

## 操作證據（不包含預測內容）

- attempt：`att_1793584306275_b5ebf93d`，UTC 01:51:51.184～01:54:14.770。CLI 138.861 秒、完整 execute 156.19 秒、37,590 tokens。
- Gemini weekly：0.9040991067886353 → 0.9040411710739136；reset 固定 UTC 11/8 12:11:16。
- Gemini 5h：0.9780206680297852 → 0.9777889251708984；reset 固定 UTC 11/2 05:34:40。
- 第三方 weekly／5h：兩次都是恰為 1.0；查詢於 01:51:51.038、01:54:14.768，reset 分別為查詢時刻約 +7 天／+5 小時減 3 秒，隨時間往後移。
- 無模型回合的 `/usage`／`/credits` 診斷於 01:58:14.605 完成：Gemini 桶與上次相同，第三方桶仍 1.0、reset 仍跟著查詢移動。
- 原正式 M2 的 21 筆，在提議新規則的離線回放中仍沒有任何一筆變 verified。D92 這筆若以新規則回放會 verified，**但該筆歷史紀錄與正式補測報告均未改寫**，不作為新的實測通過。

官方 [/usage 說明](https://antigravity.google/docs/cli/commands/usage)只說會刷新後端模型設定與配額；未承諾未使用桶的 reset 語意。因此「未用額度桶的未來日期占位」是根據上述操作資料的有限推論，不是平台契約。官方 [CLI 變更紀錄](https://antigravity.google/changelog?tab=cli)另記唯讀 slash command 的 print mode 不啟動 agent 回合、不消耗模型額度；本次診斷沒有執行額外預測。

## 未來呼叫的窄範圍修正

`quota_model_verification_v2` 只在以下條件**同時成立**時，允許已知 `3p-weekly`／`3p-5h` 的 reset_time 移動：

1. 前後剩餘值都恰為 1.0；不使用近似容差把消耗隱藏成「未使用」。
2. 所有觀測／reset 時間可解析且 `before_observation <= after_observation < before_reset <= after_reset`；測量期間不能跨過舊重置點。
3. 兩個 reset 各自與對應觀測時間相差 7 天／5 小時，誤差不超過 5 秒。額外延遲或不明格式維持 undetermined。
4. 原有全部前提仍有效：型號 log verified；Gemini 自身週／5h 的 reset 嚴格相同且兩者消耗大於 epsilon；桶集合不變、其他桶沒有增減、沒有並行／過期觀測。

新 attempt 另記 `quota_model_verification_version`，模型／prompt／次數 cap／預留／安全停線不變。缺少型號證據不能靠額度推測；mismatch 仍優先拒絕。沒有更改原有模型 CLI 權限或隔離執行器。

這項修正只適用未來呼叫；不回填原 21 筆或 D92 新紀錄，不重新開始 D92、不另開第二個補測來消耗剩餘額度。未來合法正常執行仍須實測確認；目前 M2 仍不通過。

## 驗證

Gemini（官方 agy／Gemini 3.8 Flash medium）獨立審查 PASS，明確區分觀測推論與官方保證。新增 20 項合成回歸：全滿滑動、少量消耗、曾用桶、跨重置、錯誤時間／窗口、反向日期、Gemini 桶漂移／不動、並行／過期／缺桶、僅 idle 查詢不能證明模型、mismatch 優先。相關 216 passed；完整隔離測試結果於交接及變更紀錄記錄。

診斷帳冊：`data/quota_diagnostics.jsonl`。受測／審查副本與回放結果：`/var/folders/mz/kz1rwmd573d6g9rsxv_5nq480000gn/T/forecast-m2m3-flovkh4y/`（`review4/`、`d93-focused.txt`、`d93-full.txt`）。

最終完整隔離測試：**1518 passed in 76.30s**。三個程式／測試檔與受測副本逐位元一致。整合後重產 M2 報告仍為 13／14，沒有因修正而把歷史資料改成可信。
