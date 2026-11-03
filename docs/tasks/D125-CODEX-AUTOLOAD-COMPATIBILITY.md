# D125 Codex 載入量測相容性

Louis 指出 Codex 的模型是 `gpt-6-astra`，並詢問 D124 的模型驗證問題如何解決。本批依 PLAN §7.7、D121 C 與 D124 的既有方向，修正載入量測的判定，不更換模型或推理強度。

## 原因

正式設定的 GPT 第 1 層為 `gpt-6-astra`／`medium`。受保護的 GPT adapter 沒有取得 CLI 回報的模型 ID，依 §7.7 保留 `reported_model_id=null`、`model_check=unverified`。D124 的 autoload_v2 卻要求三線一律 `verified`，使 Codex 必然失敗。原合成 executor 將三線都設為 `verified`，未涵蓋真實 GPT parser 的結果。

## 修正範圍

`autoload_v3` 保留原本 `verified` 路徑，另為 GPT 的 `unverified` 且 `reported_model_id=null` 加入窄範圍相容判定：

1. 僅限 GPT tier1 profile；tier2／format_repair／acceptance 不適用。結果記錄的完整 argv 必須與 adapter 對該次 request 產生的 argv 自我一致，包含模型、effort、忽略使用者設定、工具限制及原始檔路徑；執行中的 adapter 另由 runtime 指紋綁定驗收版本。這不是伺服器回報的實際模型身分。
2. 本次落盤的模型目錄必須已套用 D60 限制，其目標 entry 雜湊須與 adapter 結果及最新驗收相符。
3. 沿用正式 `check_acceptance()` 驗證 CLI 版本、judge、驗收證據檔 SHA、adapter／runner／netguard 指紋，以及正式模型與 effort 的成功驗收；未完成的新隔離驗收仍拒絕。
4. 維護控制器原有六鎖、正式 PATH／pin、前後基準與固定設定／CLI 雜湊、六項前檢仍適用。完成狀態、退出碼、工具限制與還原檢查不變。

新增 `model_evidence` 分別保存 requested model／effort、reported ID、原始 model_check、判定依據及驗收 record ID。這只證明固定請求與已驗收執行設定吻合，沒有聲稱伺服器回報或驗證了實際模型 ID。Claude／Gemini 不適用此相容路徑；三線的 `mismatch` 優先記為 `model_mismatch`，不被一般錯誤覆蓋，停止後續模型並由控制器建立 STOP。驗收證據的相對路徑明確以本次 data_dir 的專案根目錄解析，runtime 指紋仍核對正在執行的原始碼。

程式只改 `src/autoload.py`，新增 `tests/test_autoload_model.py`；adapters、runner、netguard、judge、D118 固定設定、M5、預測 prompt 均不變。未啟用真正自動重驗，未寫入新的基準核准或來源分類。

## 歷史量測與診斷

D124 的 `autoload_1793711226166_c2e156fc`／`auto_1793711262737_4e600c33` 保留 `measurement_failed`，不覆寫、重跑或以新規則回填 PASS；已用掉的三線量測名額不恢復。

UTC 2026-11-03 13:28:22 的唯讀診斷使用 r1 `src/autoload.py` SHA `55775ef01e691ad8402909417dc61869c5410892db0e9556285f72b356cdd12a`（r1 診斷，不代表 r2 行為）。該診斷從本次 canary 原始事件及 call-start metadata 重建 request，取得 completed=true、零工具呼叫，固定請求為 `gpt-6-astra`／`medium`。落盤模型目錄與現有正式驗收相符，新相容判定 accepted=true，且 reported ID／model_check 仍為 null／unverified。argv 是依 call-start metadata 與未變更 adapter 重建，並非新增的實際程序回執。此紀錄只診斷原缺陷，不是整批重新驗收，也不是歷史量測通過證據。

診斷及操作紀錄：ignored `data/operations/d125-autoload-model-20261103/operations.jsonl`。沒有讀取開放題預測內容，沒有新增 canary 模型呼叫。

UTC 2026-11-03 13:35:25 的複核使用 r2 `src/autoload.py` SHA `c0a1a2e959c665d4f835bd6f10ff129601f89f7601ec3fd0b718f49978c928e9`，直接呼叫 model_evidence 並明確傳入正式 project_root。在專案外的暫存目錄建立「截至該 autoload_result 為止」的唯讀帳冊副本，並確認受保護 runtime 與 D124 當時保存的 SHA 完全相同；結果同樣接受相容條件。這排除以日後新增驗收倒推歷史的混淆，仍不重分類舊結果。

## 驗證與部署狀態

第一輪隔離相關測試 167 passed、完整 2826 passed；兩家審查均無程式碼 blocker。採納路徑、錯誤優先序與測試缺口建議後，最終相關測試 179 passed；新增共 49 項回歸涵蓋真實 GPT parser 的合成 JSONL、錯誤模型／effort／argv、變更或缺漏的 catalog、驗收版本／證據／指紋、未完成驗收、其他兩線、相對路徑及控制器成功／STOP／還原。最終完整回歸 **2838 passed in 142.85s**；Claude 親自完整複審及 Gemini 獨立最終程式審查均 PASS。兩個受審／受測檔案 SHA 相符，截至本次文件複核尚未套用；正式套用仍須六鎖與前後六項 preflight，結果記於交接及操作帳冊。程式審查見 `reviews/claude-d125-code-review-r2.md`、`reviews/gemini-d125-code-review-r2.md`。

真正 R2-C 自動重驗的 24 小時限制、原子預算與候選／驗收／核准綁定仍屬後續批次，本修正不代表該功能已上線。

受審／受測 SHA-256：

- `src/autoload.py`：`c0a1a2e959c665d4f835bd6f10ff129601f89f7601ec3fd0b718f49978c928e9`
- `tests/test_autoload_model.py`：`876a9b286bb610f7115e31067dbc27ec87147587e563132f45a4084b9b2b0b4f`

D124 名額已消耗且整批載入量測仍無 PASS；本次修正不解除該次數限制，不能據此把來源改為資訊性。新增載入量測須另定有界授權。

保留的非阻擋限制：若同時有 model mismatch 與工具越權，主要錯誤標籤目前以 model_mismatch 優先，控制器同樣停止並建立 STOP；原始工具／額度證據仍另存。最新驗收與正式模型證據的 catalog 綁定方式沿用 forecast；本批不改既有驗收規則。

## 正式套用結果

UTC 2026-11-03 13:43:35～13:43:52，持 cycle／scheduler／submit／三線共六鎖套用兩個受審／受測檔案。套用前後各六項正式 preflight 全過，py_compile 通過，正式 SHA 與 r2 相同；固定設定、M5、adapters／runner／netguard 保持原 SHA，無 STOP_SUBMIT 或未結 managed run。Claude 文件複核已 PASS，見 `reviews/claude-d125-doc-review-final.md`；其中提及的 reviews/ 複本已確認和原始輸出逐位元相同。
