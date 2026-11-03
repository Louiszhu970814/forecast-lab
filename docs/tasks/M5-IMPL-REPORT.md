# M5 實作交付（離線、未啟用）

以下保留上一輪交付紀錄；本輪依規格第十四節及 Gemini 審查所做的修正與最新測試，見末節「Gemini 審查修正」。該節取代前文仍將縮放欄位布局列為未確認假設的描述。

依據：`M5-NUMERIC-SPEC.md` v2，第十三節優先。此次續作僅使用指定工作副本及合成 fixtures；沒有執行真實預測、模型 CLI、比賽查詢、API 寫入、部署或提交。

## 起點與比對限制

- 續作時已存在 `src/numeric.py`、`src/question_types.py`、四份 numeric prompt/schema、`config/question_types.json`、各管線 numeric 分支、`tests/test_numeric.py` 與 `tests/fixtures/m5_legacy_golden.json`。
- 依要求先嘗試讀取相鄰 `baseline` 以比對，作業系統回覆 `PermissionError: Operation not permitted`。未繞過限制；工作副本也不是可用的 Git checkout。因此**無法獨立證明整份差異相對於本任務 baseline 的範圍**，既有 golden 的來源也尚待整合者與 baseline 核對。
- 初次執行既有 M5 測試：111 passed。初次完整隔離回歸：2446 passed、2 skipped、2 failed；失敗詳見下節。
- 未讀取正式專案內容、工作副本 `data/`、`reports/`、`.env` 或其他工作對話。依執行邊界，未執行會存取工作對話帳冊的 `project_control status/publish`。指定的正式專案 `.venv/bin/python` 僅作為測試直譯器。

## 實作與版本

保留並驗證上一輪的 M5 分支：

| 範圍 | 版本／行為 |
|---|---|
| 初稿、修復、第二層 | `forecast_numeric_v1`、`repair_numeric_v1`、`reconcile_numeric_v1` |
| 嚴格輸出 schema | `output_schema_numeric_v1`；七個百分位鍵全部 required，禁止額外鍵 |
| 轉換及整合 | `numeric_cdf_v1`、`agg_numeric_v1`；u 空間插值、有界步長投影、等權 CDF 平均及冪等投影 |
| 升級 | `escalation_numeric_v1`；u 空間 IQR／中位數比較、0.02 下限，不計算市場 logit 差異 |
| 快照與修訂 | 僅 numeric／discrete 納入縮放白名單；`scaling_changed` 為結構修訂，不適用文字修訂降級 |
| 開關 | `question_types.json` 動態讀取；交付時 numeric、discrete **皆 false** |
| 送出及讀回 | 單元素 `continuous_cdf` payload、當下縮放一致性、5e-10 容差及既有身分三元組；unknown 不重送 |

本輪修正：

1. `scheduler.finalize` 原先會繞過數值題資格檢查，在開關關閉或舊快照缺少縮放欄位時仍呼叫整合，造成整輪排程例外。收尾現在與派工共用動態資格檢查；仍允許有效題目於截止後收尾。兩種開關狀態皆有重現及修正測試。
2. 不可表示為浮點數的巨大 JSON 整數，以及上下界各自有限但差值溢位的範圍，改為正常的 `unsupported_scaling`／`semantic_failed`，不讓 `OverflowError` 中斷管線。
3. 離散題半格檢查的容差改以格寬衡量，避免大座標的相對誤差放過整格偏移。反例使用合成大座標，原實作會錯誤接納十格偏移。另於夾回及浮點轉換後重查間距，避免大座標把百分位數捨入回封閉端點。
4. 區分模型數值語意錯誤與最終 CDF 驗證失敗：後者以 `InvalidCDF` 記錄 `invalid_output`；兩者均無格式修復或 rerun。
5. 新增 `tests/test_numeric_pipeline.py`：使用記憶體 adapter、合成帳冊與注入 HTTP 回應，走初稿／格式修復／第二層／留言／排程／送出前檢查／讀回完整路徑。

本輪沒有修改 adapters、runner、netguard、三份 campaign 固定設定、AGENTS.md、PLAN.md 或舊題型 prompt/schema 檔案。既有 golden 檢查涵蓋是非題／多選題 prompt、payload、快照內容與 content_hash；注入 numeric 縮放欄位也不改變舊題型快照。題組仍不建立預測快照。

## 實際驗證

直譯器：`/Users/ucchengg/forecast-lab/.venv/bin/python`。

所有 pytest 暫存均置於工作副本 `.d121-checks/tmp/`。`tests/run_offline.py` 阻擋專案執行資料讀取與真實模型 CLI；使用既有 `--synthetic-host` 注入合成專案根目錄、祖先鏈、目錄清單及 local mode，沒有修改正式設定來讓測試通過。

專項驗證命令：

```text
/Users/ucchengg/forecast-lab/.venv/bin/python tests/run_offline.py --synthetic-host tests/test_numeric.py tests/test_numeric_pipeline.py tests/test_validate.py tests/test_forecast.py -q -ra
```

最終專項結果：**243 passed in 2.07s**，其中 M5 146 項，既有驗證器與初稿流程 97 項。CDF 性質測試涵蓋 1440 組固定亂數樣本、N ∈ {2, 10, 20, 50, 200, 1000}、四種開放邊界組合、線性與兩種方向的對數尺度；另有極窄／界外尾部、冪等性、伺服器步長捨入、離散幾何及大數值反例。

流程測試包括匿名第二層、百分位變更判定、格式修復數字不變、語意錯誤不重跑、late values 分離、整合後 CDF 反插值留言及清洗、每個縮放欄位送出前變更、動態開關、模擬 HTTP 三元組讀回／容差／unknown 不重送。

完整回歸命令：

```text
/Users/ucchengg/forecast-lab/.venv/bin/python tests/run_offline.py --synthetic-host -q -ra
```

最終完整結果：**2481 passed、2 skipped、2 failed，136.07s，exit code 1**；輸出保留於 `.d121-checks/m5-full-verified.txt`。兩項失敗與最初完整回歸相同，沒有新增失敗；不可將本結果標為完整回歸全通過。另已檢查修改檔案的 Python 語法、M5 留存的 18 個受保護檔案雜湊，以及兩個開關皆 false。

完整回歸的失敗及跳過：

- `test_pin_wrapper_fails_closed_and_preserves_arguments`：合成 shell wrapper 呼叫系統 shasum 時，macOS 沙箱封鎖 `/System/Library/Perl/5.34/.../libperl.dylib`，程序回傳 134。沒有放寬沙箱、改 wrapper 或替換雜湊工具。
- `test_protected_runtime_and_campaign_hashes`：只有 `PLAN.md` 不符 D121 舊 fixture。fixture 預期 `9e8b943f1b3eb7dae204adaea2e0e8b7ad224a45524af37843eb61d7d1183c53`，副本實際為 `311743f108fdec1a55b795b0e8e87ca6be37a8fa72b8d80d455ad8c295c3b6af`，與上一輪 M5 golden 相符。其餘受保護檔案全部相符；沒有改寫 PLAN 或更新預期雜湊以掩蓋此差異。
- `tests/test_acceptance.py:899` 的兩個案例：沙箱禁止 loopback bind，依測試既有規則跳過；沒有放寬或模擬這兩項正式 netguard 驗收。

## 尚未完成的上線驗收／所需輸入

**本交付不能視為 D122 上線 PASS。** 本次禁止真實 API 查詢或送出，也無法讀取 baseline：

1. 整合者須在可讀環境比對本副本與任務 baseline，確認上一輪改動、golden 來源與 PLAN 雜湊差異。兩份歷史 fixture 目前互相不同，須核對後才能決定是否更新 D121 文件雜湊。
2. 尚未重新核對釘選的上游原始碼版本，亦未取得規格 §13.2／§13.3 要求的實測 API 回應。`read_scaling_fields` 的頂層／scaling 兩種布局、離散題名目端點及外推半格語意，**僅有合成測試，並非已確認平台契約**。
3. `submit_transport.NUMERIC_READBACK_FIELD = "forecast_values"` 同樣是待實測的隔離邊界。尚缺 bot testing area 數值／離散題真實讀回形狀確認。缺欄位、衝突或不符離散幾何的快照均拒絕，記 `unsupported_scaling`；不能靠預設推測通過。
4. 尚缺 Claude、Gemini 程式審查 PASS，以及規格指定三個試驗案例的預測與私人留言皆讀回 found。這些工作由具備相應權限的整合者執行，本輪未啟動子代理。
5. 須在允許系統 Perl 的測試環境重跑 wrapper 測試，核對 PLAN fixture 後重跑完整隔離回歸。全部驗收通過前維持兩個開關 false。

供整合者更新 PLAN §16 的建議文字：M5 引入上述 numeric 專用 v1 版本及動態開關；舊題型維持 `forecast_v2`、`output_schema_v2`、`agg_v1`。記錄實際程式審查、平台欄位確認、三題有界實測及完整回歸證據後，再依 D122 決定啟用；本報告不代替 PLAN 正文或驗收。

## Gemini 審查修正

依據：本輪任務六項修正要求及 `M5-NUMERIC-SPEC.md` 第十四節。這是已完成 M5 的審查修正，不是中斷續作。本輪未執行 GET、真實預測、模型 CLI、提交、部署或子代理；四份實測資料直接使用既有 `tests/fixtures/m5_real_scaling.json`，只作縮放契約的離線測試，百分位數全部使用固定的合成 u 座標，不涉及題目內容或研究。

本輪修改檔案：

- `src/numeric.py`：縮放來源、名目端點、格點驗證、格點複製與尾部錨點。
- `src/poll_questions.py`：讓包含非有限格點的拒絕紀錄仍可安全雜湊及寫入 JSON。
- `src/continuous.py`：開關重新啟用時，舊快照必須通過 `F.eligible` 才能進入 queued。
- `tests/test_numeric.py`、`tests/test_numeric_pipeline.py`：更新 API 結構，補齊拒絕、修訂、尾部、舊快照及四份既有實測縮放資料的測試。
- 本交付報告。未修改任務禁止的程式／設定、AGENTS.md、PLAN.md、舊題型 prompt/schema 或既有實測 fixture。

六項審查要求的處理：

1. `range_min`、`range_max`、`zero_point`、`nominal_min`、`nominal_max`、`continuous_range` 只讀 `question.scaling`，不採用或核對頂層同名欄位。`unit` 只讀頂層。上下界開放旗標及 `inbound_outcome_count` 只採用 scaling 內值，僅在頂層鏡像非 null 時核對；衝突記 `unsupported_scaling`。無 scaling 物件或必要欄位時拒絕，不從頂層補值。
2. 正式程式與 M5 測試全面採用 `nominal_min`／`nominal_max`，包含白名單、離散半格幾何檢查、prompt 與修訂判定。
3. `continuous_range` 必須為 N+1 個有限數字的 list，逐點與 `from_u(i/N)` 比較，使用 `math.isclose(rel_tol=1e-12, abs_tol=1e-10)`。缺失、型別錯誤、非有限值或超過容差都拒絕。格點加入快照內容、content_hash 與 `scaling_changed`；即使差異在幾何驗證容差內，也視為修訂並擋下舊 CDF。
4. 合成 API fixture 改為頂層 range/zero 為 null、unit 位於頂層、其餘位於 scaling。四份既有實測縮放資料均走快照 → 合成百分位數 → CDF → 本地驗證器，另逐格核對兩個離散案例的箱界中點等於名目結果、首尾各外推半格。既有線性／兩方向對數尺度、各 N 與開放邊界性質測試繼續執行。
5. 全部百分位數位於同一側開放界外（含恰在邊界）時，對另一端外推錨點做單調夾限；測試檢查投影前 CDF 已單調，並確認投影後保留相應方向的尾部質量。
6. `unsupported_type` 舊快照在開關啟用後，只有 `F.eligible(snap, cfg, now) is None` 才重新 queued。缺範圍、缺格點、已有 scaling_error、已過決策截止時間的 numeric／discrete 舊快照均有拒絕測試；完整的舊快照仍可啟用。

測試過程另外修正兩個由新格點欄位引出的問題：讀取時複製格點陣列，避免原 API 物件被修改時連帶改掉已建立快照、漏判修訂；非法 NaN／Infinity 在拒絕紀錄中轉為具型別標記的 JSON-safe 證據，避免還沒記下 `unsupported_scaling` 就因 content_hash 序列化失敗而中斷。

版本：CDF 轉換由 `numeric_cdf_v1` 升為 **`numeric_cdf_v2`**，用於追蹤尾部錨點行為修正；舊版本的結果不會被新整合流程當作相容輸入。numeric prompt/schema、`agg_numeric_v1`、`escalation_numeric_v1` 保持原版本。兩個題型開關仍為 false。PLAN 正文及 §16 屬整合者；建議整合時記錄本次轉換版本、§14 縮放契約與重新排隊條件修正。

### 本輪驗證

直譯器及隔離方式與上一輪相同，使用指定 `/Users/ucchengg/forecast-lab/.venv/bin/python`、`tests/run_offline.py --synthetic-host` 及工作副本內暫存目錄。

```text
/Users/ucchengg/forecast-lab/.venv/bin/python tests/run_offline.py --synthetic-host tests/test_numeric.py tests/test_numeric_pipeline.py tests/test_validate.py tests/test_forecast.py -q -ra
/Users/ucchengg/forecast-lab/.venv/bin/python tests/run_offline.py --synthetic-host -q -ra
```

專項：**290 passed in 2.56s**，exit code 0；輸出 `.d121-checks/m5-review-targeted.txt`。包含既有是非題／多選題 prompt、payload、快照內容／content_hash 逐位元 golden 回歸，18 個保護檔案雜湊及開關 false 檢查。新增測試初次揭露非有限格點雜湊及陣列共用問題，均已修正後重跑。

完整隔離回歸：**2528 passed、2 skipped、2 failed，135.75s，exit code 1**；輸出 `.d121-checks/m5-review-full.txt`。相較上一輪交付結果新增 47 項通過，沒有新增失敗，但仍不能標為完整回歸全通過：

- `tests/test_claude_plugin_inventory.py::test_pin_wrapper_fails_closed_and_preserves_arguments`：合成 wrapper 的系統 shasum 仍因沙箱封鎖 `/System/Library/Perl/5.34/.../libperl.dylib` 而回傳 134。未修改 wrapper、替換工具或放寬沙箱。
- `tests/test_d121_fail_soft.py::test_protected_runtime_and_campaign_hashes`：另逐一核對該 fixture 的 12 個檔案，只有 PLAN.md 不符；預期仍為 `9e8b943f1b3eb7dae204adaea2e0e8b7ad224a45524af37843eb61d7d1183c53`，實際仍為 `311743f108fdec1a55b795b0e8e87ca6be37a8fa72b8d80d455ad8c295c3b6af`，與上一輪紀錄一致。沒有修改 PLAN 或預期雜湊。
- `tests/test_acceptance.py:899` 的兩項測試因 loopback bind 被沙箱拒絕而依既有規則跳過；未放寬限制。

### 限制與整合者待確認

本輪再次嘗試讀取指定 baseline 的 `src/forecast.py`，沙箱回覆 `PermissionError: Operation not permitted`；未繞過限制。工作副本仍不是 Git checkout，因此無法提供可靠的整份 baseline diff。保護檔案及舊題型相容性的證據來自副本內既有 golden，整合者仍須核對其來源及 D121 PLAN 雜湊差異。

第十四節及四份既有 fixture 已提供縮放欄位與半格語意證據，因此不再把它們列為待猜測的 API 布局；本輪沒有自行對平台重做實測。讀回欄位契約、三個上線驗收案例、Claude／Gemini 程式審查 PASS 等先前待辦仍由整合者處理，不能將本次離線修正視為上線 PASS。
