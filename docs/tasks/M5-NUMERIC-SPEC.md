# M5 規格：數值題與離散題（D122）

> 狀態：v2，已納入 Gemini 規格審查（`reviews/gemini-m5-spec-review.md`，有條件 PASS）；**第十三節的修訂優先於前文**；實作交 GPT，排在 D121 第一批套用之後（兩者都改 `forecast.py`）。
> 決策：PLAN D122。上線採 A：驗證通過就加入目前的 D118 MiniBench 窗口。
> 動機：覆蓋率。2026-11-03 MiniBench 開出 6 題，其中 4 題為數值題或離散題；歷史約 36%。

## 一、範圍

- **納入**：`numeric`、`discrete`。
- **不納入**：`date`（時間戳與格式另需設計，列為 M5b）、題組（Market Pulse 另案）。
- **是非題、多選題完全不變**：沿用 `forecast_v2`、`output_schema_v2`、`agg_v1`、現有留言與送出；新程式不得改變這兩種題型的 prompt、payload 或判定。回歸測試須證明兩者的 prompt 與 payload 逐位元相同。

## 二、平台規則（已查證）

來源：Metaculus 後端 `questions/serializers/common.py` 的 `ForecastWriteSerializer.continuous_validation`、`questions/models.py`、`utils/the_math/formulas.py`（main，2026-11-03 讀取，HEAD 約 `bfae5fe`）；官方 `forecasting-tools` 的 `numeric_report.py`、`questions.py`、`metaculus_client.py`，以及 `metac-bot-template/main.py`。實作時須重新核對釘選版本，並以試驗場地的實際送出與讀回為準。

- **payload**：`{"continuous_cdf": [...]}`，經既有 `POST /api/questions/forecast/`；不送 `probability_yes` 等欄位（送了會被拒）。
- **長度**：`inbound_outcome_count + 1`；未設定時 `inbound_outcome_count = 200`，即 201 點。離散題點數較少，必須讀題目欄位，不能寫死 201。
- **伺服器先把 CDF 四捨五入到 10 位小數**，再用四捨五入到 9 位的相鄰差值檢查：
  - 每步至少 `round(0.01 / N, 9)`（N = inbound_outcome_count；預設 0.00005）。
  - 每步至多 `0.2 × 200 / N`（預設 0.2）。
- **兩端**：下界開放 `cdf[0] ≥ 0.001`；下界封閉 `cdf[0] == 0`；上界開放 `cdf[-1] ≤ 0.999`；上界封閉 `cdf[-1] == 1`。`open_lower_bound`／`open_upper_bound` 為 None 時視為封閉。
- **x 軸**：CDF 第 i 點對應未縮放位置 `u = i / N`；換成實際數值的方式：
  - `zero_point` 為空：`range_min + (range_max − range_min) × u`。
  - `zero_point` 有值（對數尺度）：`r = (range_max − zero_point) / (range_min − zero_point)`，值 = `range_min + (range_max − range_min) × (r^u − 1) / (r − 1)`。
- 語意：`cdf[0] = P(結果 < range_min)`、`cdf[-1] = P(結果 ≤ range_max)`。
- 伺服器沒有 NaN／總和檢查，本地必須自行擋下。

## 三、題目快照

- 白名單新增：`range_min`、`range_max`、`zero_point`、`open_lower_bound`、`open_upper_bound`、`inbound_outcome_count`、`unit`（官方 template 也用 `nominal_lower_bound`／`nominal_upper_bound` 描述邊界，有則一併保存）。
- 這些欄位納入修訂判定：任何一個改變都視為題目修訂，照既有規則重跑。
- 缺 `range_min`／`range_max`、點數不合理、`zero_point` 讓對數公式無效（例如 r ≤ 0 或 r = 1）時，記為 `unsupported_scaling`，不作答。

## 四、模型輸出

- 新 prompt `forecast_numeric_v1`、新 schema `output_schema_numeric_v1`；共用欄位（reasoning_summary、key_factors、base_rate、market_refs、sources、escalation_flags、injection_notice 等）的意義與限制沿用 v2。
- 模型輸出 7 個百分位數：**5、10、25、50、75、90、95**，以題目單位表示，嚴格遞增。比官方 template 的 10～90 多出 5 與 95，用意是逼模型明確寫出尾部，這是對數計分最容易失分的地方。
- prompt 須提供：範圍、上下界是否開放（並說明封閉界外不可能發生，開放界外仍可能）、單位、對數尺度提示（`zero_point` 有值時）、今天日期，以及「寬一點的區間比窄而錯好」的提醒，比照是非題的校準提醒。
- 本地驗證：7 個值都有限、嚴格遞增、相鄰差不小於範圍的極小比例；封閉界外的值先夾回界內；超出範圍 ±2 倍的值判為無效輸出，走既有的格式修復與重跑流程。
- 離散題使用同一份 prompt 與 schema，另告訴模型結果只會落在等距的離散點上。

## 五、百分位數轉 CDF（`numeric_cdf_v1`，純函式）

1. 把 7 個值換成未縮放位置 u（`zero_point` 有值時用對數公式的反函數）。
2. 加入邊界錨點：下界封閉 → 在 u = 0 時 F = 0；下界開放 → 在 u = 0 放 F = 2.5%（5 百分位的一半），右側同理（上界封閉 → u = 1 時 F = 1；開放 → u = 1 時 F = 97.5%）。若模型自己的 5／95 已落在界外，錨點改由插值決定，並保持單調。
3. 在 N + 1 個格點上以 u 為橫軸做線性插值。
4. **標準化**：與均勻分布混合，確保每步差值 ≥ 最小步長的 1.1 倍；把單步質量限制在上限的 0.95 倍內，超出的部分重新分配後再正規化；依兩端規則設定 `cdf[0]`、`cdf[-1]`。官方的做法可作參考：`0.988 × F + 0.01 × u + 0.001`（兩端開放）等。
5. 四捨五入到 10 位小數。
6. **本地驗證器**：完整重現第二節的伺服器檢查，包括先四捨五入到 10 位、差值四捨五入到 9 位再比較，並另加 NaN、長度、單調性檢查。不通過就不送出，記為 `invalid_output`，不嘗試其他修補。
7. 保存 7 個百分位數、轉換版本與最終 CDF 的雜湊，供稽核與讀回。

## 六、整合（`agg_numeric_v1`）

- 各線先各自轉成 CDF（同一格點），**取算術平均（線性混合）**，再跑一次第五節的標準化與驗證。理由：數值題的分數依實際結果處的機率密度計算，分布太窄卻猜錯的代價很高；線性混合會保留各線的不確定性，比中位數或分位數平均保守。官方 forecasting-tools 用逐點中位數，作為未來的比較候選。
- 缺線規則與是非題相同；`leak_confirmed`、`model_mismatch` 的答案不納入。
- 權重相等；之後依已結算題目的成對比較再調整（§4.3 的結論紀律）。

## 七、升級（第 2 層）

- 觸發條件（`escalation_numeric_v1`，先寫成程式常數）：任兩線的中位數差距，在未縮放空間中大於兩線四分位距平均值的 1.0 倍；或任兩線的 25～75 區間完全不重疊；或模型標記 `resolution_ambiguity`／`evidence_conflict`。
- 第 2 層的 prompt 為 `reconcile_numeric_v1`：給模型看其他線的匿名百分位數與理由（比照 `reconcile_v1`），輸出同一份 numeric schema。
- 先後順序、預算與截止時間規則都與是非題相同。

## 八、留言

- 沿用私人留言與 `included_forecast = true`；內容包含整合後的中位數、10～90 區間（以題目單位、四捨五入到合理位數），以及理由摘要與來源。長度上限、清洗規則不變。

## 九、送出與讀回

- 送出前檢查沿用：題目 ID、帳號、比賽歸屬、修訂、截止與只送一次；另加「CDF 長度等於題目當下的 N + 1」，以及本地驗證器再跑一次。
- 讀回：讀取自己的預測歷史，與送出的 CDF 比對（伺服器會四捨五入到 10 位，所以送出前先自行四捨五入，比對時容許 1e-10 的誤差），配合既有的三元組比對。實際欄位名稱須在試驗場地實測後確認；確認之前不得上線。
- 未知結果不重送，沿用既有規則。

## 十、上線開關

- 新增 `config/question_types.json`：`{"numeric": false, "discrete": false}`。這是新檔案，不在 campaign 固定的 hash 內。
- 驗證通過後，上線動作就是把它改成 true，照互斥程序套用並記錄 §16；要撤回時改回 false 即可。
- `poll_questions.SUPPORTED_TYPES` 依此開關決定是否納入這兩種題型。

## 十一、限制

- 不改 `adapters/*`、`flab/runner.py`、`flab/netguard.py`（驗收指紋）；schema 透過既有的 AdapterRequest 傳入。若發現非改不可，先停下來回報，因為三線兩層都要重跑正式驗收。
- D118 窗口內不改 `run_config_v2.json`、`quota_policy.json`、`cycle_campaign.json`；新參數寫成有版本的程式常數，11/10 新窗口時移入 `run_config_v4`。
- 不在開放中的比賽題目上測試。

## 十二、驗收

1. **離線**：
   - 轉換器的性質測試：隨機產生百分位數、各種邊界組合、`zero_point`、N ∈ {10, 20, 50, 200}，輸出一律通過本地驗證器。
   - 極端輸入測試：質量全擠在某一端、區間極窄、值超出範圍。
   - 整合、升級、留言、讀回比對都要有測試。
   - 是非題與多選題的 prompt、payload 逐位元回歸測試。
   - 完整隔離回歸全部通過。
2. **審查**：Claude、Gemini 程式審查 PASS。
3. **試驗場地實測**（D107 A1 常設授權的有界寫入，正式送出路徑）：
   - bot testing area 的數值題與離散題各 1 題、主站非 Cup 數值題 1 題。
   - 每題預測 1 次、私人留言 1 次，都讀回 found。
   - 4xx／429 立即停止；unknown 只對帳、不重送。
4. 以上全部完成後，把上線開關改為 true（D122 選項 A），記錄 §16，並發布 TG 摘要。

## 十三、審查後修訂（v2，優先於前文）

以下依 Gemini 審查採納；與前文衝突時以本節為準。標「須實測」者，實作前先以只讀 GET 取得 bot testing area 數值題與離散題的真實 API 回應確認，不得猜測。

1. **payload 外層**：沿用既有傳輸層的單元素陣列 `[{"question": question_id, "continuous_cdf": [...]}]`；不得出現 `probability_yes`、`probability_yes_per_category`。
2. **欄位位置（須實測）**：`range_min`、`range_max`、`zero_point`、`inbound_outcome_count`、名目端點可能位於 `question["scaling"]`，`open_lower_bound`、`open_upper_bound`、`unit` 可能在 `question` 頂層。依實測結果讀取；讀不到時記 `unsupported_scaling`，並在報告中列出，不得默默當作缺值。
3. **離散題語意（須實測）**：離散題的 `range_min`／`range_max` 可能是名目端點各外推半格；N + 1 個 CDF 點是 N 個離散結果的箱界，`cdf[i] − cdf[i−1]` 是第 i 個離散值的機率。prompt 只給模型名目端點、步長與可出現的值，不給外推後的範圍。
4. **越界處理**：超出封閉界的容差（未縮放空間 u 超出 [−0.02, 1.02]）判 `semantic_failed`；容差內才夾回，夾回後仍須嚴格遞增，且與封閉端點保留最小間距。對數尺度先拒絕使 `(x − zero_point)/(range_min − zero_point) ≤ 0` 的值。開放界的長尾以 u 空間設界（例如 u ∈ [−1, 2]），不用線性 ±2 倍。這類數值問題一律歸為 `semantic_failed`，不走 `format_repair`（格式修復不得修改數值），也不觸發 rerun。
5. **標準化改為步長投影**：
   - (a) 先決定兩端值：開放端點是不等式（`0.001 ≤ cdf[0]`、`cdf[−1] ≤ 0.999`），不硬設；同時保證界內總質量 `S = cdf[−1] − cdf[0]` 足以容納 N 個最小步長（至少 `N × 1.1 × 0.01/N`，再加餘裕）。
   - (b) 對 N 個步長在總和 S 固定下，投影到 `[1.1 × 0.01/N, 0.95 × 0.2 × 200/N]`（有界單純形投影／water-filling）。
   - (c) 累加回 CDF，四捨五入到 10 位，再修補末位的捨入誤差，最後跑本地驗證器。
   - 取代第五節第 4 步「先混均勻、再限縮、最後設兩端」的順序；不使用官方的 `0.01 × u` 係數，因為它只有最小步長的 1.0 倍，在離散題經四捨五入後可能被拒。
6. **開放界錨點**：不固定 2.5%／97.5%；依 5 與 10 百分位數（或 90 與 95）的斜率外推到邊界，結果限制在 [0.001, 0.025]（上界為 [0.975, 0.999]）。
7. **整合**：各線 CDF 取算術平均後，只做冪等投影（第 5 點）與四捨五入，不再混入均勻分布，避免重複平滑。
8. **升級條件**：
   - 中位數差距須大於 `max(1.0 × 平均四分位距, 0.02)`（u 空間）；25～75 區間不重疊時，間距也須 ≥ 0.02。
   - 數值題略過 `market_disagreement`。
   - `escalate.py` 依題型分支，不得對數值題計算 `logit(probability)`；新常數另立 `escalation_numeric_v1`，不改 `escalation_v2` 的 policy 比對。
9. **送出前檢查**：除了 CDF 長度，所有縮放與邊界欄位（含 unit）都要與快照一致，否則視為修訂、不送出。讀回比對容差改為 5e-10；`can_reconcile_forecast` 納入 numeric、discrete。
10. **上線開關要動態生效**：
    - 「是否支援」在使用當下由題型與 `config/question_types.json` 決定，不依賴快照裡寫死的 `supported` 布林值。
    - `forecast.eligible`、排程器與送出前檢查都要即時讀開關；關掉時，已建立的數值題快照也不得再作答或送出。
    - 主站輪詢的 `forecast_type` 參數也要依開關增減。
    - 開關改為 true 後，現有數值題快照（舊 `supported: false`）要能被接納，不需要等題目內容改變。
11. **快照相容**：是非題、多選題、題組的快照 `content` 與 `content_hash` 必須逐位元不變；縮放欄位只在 numeric、discrete 時加入 `content`。縮放、邊界、單位變更另標 `scaling_changed`，視為結構性修訂，來不及重跑時不得用舊快照的 CDF 送出（D53 的降級規則不適用）。
12. **各模組都要有題型分支**：
    - `validate.py`：數值 schema、語意檢查、修復。
    - `forecast.py`：數值題的 repair basis／prompt／errors、late values；第 2 層的 `changed_from_tier1` 改為比較百分位數（不得對 None 做 `math.isclose`）。
    - `aggregate.py`：`_options`、`input_error`、`agg_numeric_v1`。
    - `comment.py`：允許 `agg_numeric_v1` 並驗證 `continuous_cdf`。
    - 不改設定檔與驗收指紋檔案。
13. **schema 與留言細節**：
    - `output_schema_numeric_v1.json` 的百分位數用物件 `percentiles`，鍵為 `p05`、`p10`、`p25`、`p50`、`p75`、`p90`、`p95`，全部列在 required，`additionalProperties: false`，以符合 GPT strict schema、不必修改 adapter。
    - `reconcile_numeric_v1` 的匿名輸入格式比照 `reconcile_v1`。
    - 留言的中位數與 10～90 區間由整合後 CDF 在 u 空間反插值再換回實際數值；`cdf[0] > 0.1` 時寫成「低於下界」，`cdf[−1] < 0.9` 時寫成「高於上界」；保留 3～4 位有效數字並附單位。
    - 合法的 N 為整數 2 ≤ N ≤ 1000，超出範圍記 `unsupported_scaling`。

## 十四、欄位實測結果（Claude，UTC 2026-11-03 06:19，唯讀 GET）

以正式 client 讀取 bot testing area（project 32977）開放中的 numeric 46451、43325 與 discrete 45708、43322（只 GET，沒有任何 POST）：

- `range_min`、`range_max`、`zero_point`、`nominal_min`、`nominal_max`、`inbound_outcome_count`、`open_lower_bound`、`open_upper_bound`、`continuous_range`（N + 1 個格點的實際值）都在 `question["scaling"]` 內。
- 頂層也有 `open_lower_bound`、`open_upper_bound`、`inbound_outcome_count`，數值與 `scaling` 內相同；頂層的 `range_min`、`range_max`、`zero_point` 為 null，**不可讀頂層**。
- `unit` 只在頂層（可能是空字串）。
- 名目端點的鍵名是 `nominal_min`／`nominal_max`，不是 `nominal_lower_bound`／`nominal_upper_bound`。
- **離散題外推半格已證實**：43322 名目 0～15、N = 16，範圍 −0.5～15.5，格點 [−0.5, 0.5, …, 14.5, 15.5]；45708 名目 0.1～0.8、N = 71，範圍 0.095～0.805（步長 0.01）。數值題的範圍等於名目端點（46451：4,000,000～10,000,000，N = 200）。
- 實作應以 `scaling.continuous_range` 與自行計算的格點互相核對，不一致就記 `unsupported_scaling`。
