# D124 第二批維護與觀察模式

此文件記錄 D121 C 的第一階段實作與操作限制；PLAN 是唯一規格來源。最終 Claude／Gemini 兩批審查均 PASS；UTC 13:06／13:10 已分批互斥套用。單次載入量測已執行及完整還原，但 GPT 身分未驗證使整批 measurement_failed，不是量測 PASS。

## 發布內容

- `autoload_v2`、`autoload_maintenance_v1`、`forecast_pipeline_v8`：全域標記的寫前紀錄、有條件還原、未結案前檢與六鎖維護入口。
- `auto_reaccept_v1` 固定 `observe`：週期結束後最多觀察 120 秒，只記候選指紋與格式代碼，不呼叫驗收模型、不寫 `baseline_approved`。沒有 `enforce` 執行入口；不能把這批稱為 R2 自動恢復已上線。
- 不修改 adapters、runner、netguard、judge、三份 D118 固定設定，也不改 M5 開關、預測 prompt、模型與 effort。

## 有界量測

Louis 已在 D124 接受今晚維護。先等現有模型、排程與平台寫入自然完成，再依序持 cycle、scheduler、submit、Claude、Gemini、GPT 鎖。等待鎖最多 15 分鐘，每次失敗全部釋放；不終止其他工作。

維護入口：

```sh
.venv/bin/python src/autoload_maintenance.py run --approved-by Louis --i-understand-this-edits-global-files
```

三線各最多一次正式模型呼叫，開始即入帳、包含在 D118 與 D105 接續週上限。僅在沒有任何 call-start 的第一次失敗後可重試一次。任何線已記 call-start 即禁止整批重跑。執行採正式 launchd PATH、Claude 2.1.316 pin；開始前及結束後驗證環境與受保護雜湊。Gemini 同時核對前後額度與實際模型日誌。

只量測註冊的全域／共同來源。結果是 loaded、not_loaded 或 unknown；沒有觀察到標記不代表證明不載入。Codex prompt-input 的負面證據仍是 unknown。量測不自行核准基準或調整來源類別。

操作前重查正式工作目錄：UTC 2026-11-03 12:52 的最近十筆正式工作都在 `/private/var/folders/mz/kz1rwmd573d6g9rsxv_5nq480000gn/T/flab-runs`，與互動程式的 `default_base_dir().resolve()` 相同。正式 plist 沒有覆寫 TMPDIR。執行前須再核對，不能假設另一台電腦或重登入後路徑仍相同。量測期間不另啟 CLI 審查工作。

## 還原與恢復

原有檔案只在完整內容、inode、權限都相符時截除自己的附加後綴，不先清空再重寫。新建檔案只在完整內容與已知 inode 相符時刪除。部分寫入、替換 inode、外部新增內容或權限變更均保留現場與備份，記錄衝突。新建目錄只移除身分相符的空目錄。

只有 `autoload_result.restore_ok=true` 與相同 run 的 manifest 最後一筆 `closed` 同時存在，三線前檢才不再受 `autoload_unclosed` 阻擋。失敗先建立 STOP_SUBMIT，再釋放維護鎖。

重啟後恢復：

```sh
.venv/bin/python src/autoload_maintenance.py recover --run-id autoload_REPLACE_ME --approved-by Louis --i-understand-this-edits-global-files
```

恢復先證明舊控制器及已登記模型程序不再運作；不因題目將截止或待處理平台 intent 而延後清除標記。只清除本 run 已登記的暫存目錄，補寫 source=autoload 的 finished/result，不製造或撤回 `acceptance_result`。缺少 manifest 的早期中斷，依所有修改前必須先持久化 intent 的不變量，加上目標沒有本 run 標記的核對，記錄 `no_mutation` 結案。

若編輯器以 rename 換掉 inode，單純重跑恢復不會覆蓋外部內容。主持人自行排除標記、清掉本次新建目錄後，才可明確結案：

```sh
.venv/bin/python src/autoload_maintenance.py recover --run-id autoload_REPLACE_ME --approved-by Louis --i-understand-this-edits-global-files --resolve-conflict --resolved-by Louis
```

此指令只檢查與記錄，不覆寫任何來源。仍有本 run 標記、非一般單連結檔案、或新建目錄未清除時拒絕。結案標示 `host_attested`、`byte_exact=false`、`passed=false`，不冒稱逐位元還原或量測 PASS；既有 baseline 與六項 preflight 仍須通過。只可移除內容精確等於本 run 所建立的 STOP，其他人建立的 STOP 保留。

## 已知限制與後續

- POSIX flock 是建議鎖；無法完全排除不合作的外部寫入恰好落在內容檢查與截斷之間。維護時避免同時編輯這些全域檔案。
- fsync 不等於硬體層斷電保證。SIGKILL／斷電可能需要恢復，不能宣稱所有情形都自動還原。
- 還原不包含官方 CLI 的歷史逐字稿／session／brain；那些歷史資料可能留有測試標記。備份位於 ignored 的 data/autoload_backups，保留作恢復證據，沒有送進 git。
- 純文字格式檢查不能證明內容沒有比賽資訊，不能取代禁止人為介入個別預測的規則。
- 真正自動重驗仍需另批完成滾動 24 小時限制、原子預算、精確候選／驗收／核准綁定與失敗恢復，依已核准 D121 更新 PLAN §7.2 並通過雙審查。CLI 版本更換與來源重新分類另案審查。

## 驗證與審查

修正後隔離完整回歸：2789 passed，143.20 秒；相關恢復／前檢／額度測試 302 passed。測試副本為 local 且 numeric/discrete=false，以符合既有測試夾具；這兩份測試設定不部署，正式 M5 仍為 true。

第一輪：Gemini 維護程式與 observe 程式 PASS；Claude 完整審查 BLOCKED。其六項問題已補修與控制器測試，最終 Claude 與 Gemini 對兩批均 PASS。原始報告在 reviews/；精確測試與來源雜湊位於 ignored 的 data/operations/d124-batch2-20261103/。

## 單次實測結果

run `autoload_1793711226166_c2e156fc`，三線各 1 次。restore_ok=true、restore_failures=[]、manifest closed，受保護項相符，六項正式 preflight 全過，無 STOP。整批 measurement_failed：GPT completed=true、tool_call_counts={}、violations=0，但 reported_model_id=None、model_check=unverified；既有 GPT adapter 沒有可核驗的身分證據。保留原判定、不增加模型呼叫、不核准基準或改類別。量測已占用本批唯一有呼叫的名額。

Claude 的個人 skill 標記記 not_loaded；GPT 指示與 skill 標記記 loaded；其餘為 unknown。這些紀錄須連同整批未 PASS 的限制閱讀。恢復時若 PID 被重用、其他模型工作仍未結或程序不存在無法證明，維持 STOP 並由主持人處理；不得直接刪除 STOP／manifest。

部署後自然週期 `cycle_1793711623971_6626bb2a` 於 UTC 13:15:09 complete、三線 ready、errors 空；隨後 observe 三線 no_candidate、model_calls=0。正式排程與觀察接點已核對，沒有新增背景監看程序。
