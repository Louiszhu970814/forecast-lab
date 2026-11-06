# D135 — 十道閉題健檢修正

來源：Louis 桌面要求針對 D133 已有答案健檢問題修改；PLAN v1.89 §4.3／D135。GPT 整合，保留 D132／D133／D134 未提交內容。接手基線完整3213 passed／100 failed。

## 交付範圍

- `post_close_audit_v2`／`health_diagnostics_v1`：數值中位數差保留實際單位，另列尺度座標差，沒有通用0.30警示；是非與完整多選機率向量保留0.30描述警示。保留所有缺漏／失敗證據，明確區分未開始、開始無輸出、失敗與未觀察到第二層要求。週期證據限qid明列new/revised；不能當作完整歷史機會或目前線健康的證明。代表輸出使用整合input id，無綁定時取第一份valid，不採最後一次結果取代。
- `audit_v2`：只在host捕捉的`agy_cli.log`指定CLI設定來源行、Deny欄位的精確`read_url(metaculus.com)` host，以及GPT已知web_search事件query欄位的完整`-site:metaculus.com`運算子分類。來源、開啟、未知URL事件、結果／正文、其他關鍵字與繼承旗標照原規則；不刪舊帳、不改停止門檻、不自動解除停線。新稽核事件寫版本。
- `docs/experiments/d135-research-prompts/`四份候選v1：實測／判斷式／不可得基準率、母體／時間窗／分子分母／來源、主張來源對應、事件／發布／行政結算時間、正式統計口徑及尾部證據。沿用原schema／placeholders／120字限制，未由runtime引用、未啟用。這是具體可審查候選，不宣稱模型實際遵守或準確度提升。

## 驗證與上線門檻

三閘門不變，未合格題不能顯示新增診斷；新增可選帳冊損毀fail closed。合成測試覆蓋跨單位／多選／非有限值、缺線歸因、繼承旗標、偽造設定文字與混合搜尋結果、候選模板相容性。完整測試須與基線100失敗逐項比較；Claude完整及Gemini獨立官方CLI同SHA雙PASS後才套用。共用六鎖、來源備份、前後六項preflight及line_guard、正式root smoke、受保護雜湊與失敗回復必須成功；不新增模型預測、提交或主賽授權。

## 真實閉題複核與限制

十題重新通過三閘門後才讀原始事件；六個T1缺口均無預測／scheduler job，相關new-question週期前檢有Claude baseline stop、Gemini baseline reaccept及GPT model catalog mismatch。這支持當時被前檢阻擋的觀察，不等於所有機會均已覆蓋；D130／D131後三線恢復須看新鮮前檢。

舊七題suspected標記保留；十一個自有keyword命中為十個Gemini CLI禁止設定與一個GPT負向site查詢。這是這些命中的來源分類，不是無洩漏證明，不回寫或抹除原始稽核。既有未核實基準率不得稱為實測，來源連結不等於讀取或驗證。尚無結算準確率，不從小樣本改權重／clipping／信心／升級門檻。

## 實際完成證據

送審時本節尚待實際驗證；現已完成，結果與時間見下方「實際交付完成」。操作目錄：`data/operations/d135-quality-repair-20261105/`。

### 第一輪審查修正

Claude r1指出暴露總結可能低估confirmed／僅audit帳冊旗標，屬blocking；現合併forecast及相同attempt的audit（含own）旗標，以confirmed最高優先並保留原始來源。補六種旗標位置／嚴重度與混合旗標回歸測試。另補CLI permission token前界與cycle id型別／字串數字正規化，候選啟用須升正式prompt版本。Gemini r1 PASS仍不能代替新SHA雙審；以後續實際最終輪為準。


## 實際交付完成（2026-11-05T00:48:30.708Z）

- Claude r2／Gemini r2 官方CLI同packet SHA來源雙PASS；Gemini r2封包尾端截斷，另以完整小封包補審部署及整合並PASS，原始回覆與補審均歸檔；歸檔 `reviews/claude-d135-r2.md`、`reviews/gemini-d135-r2.md`，來源及部署證據 `reviews/d135-source-manifest.json`。五次開發審查（含Gemini部署／整合補審）另記真實用量，不算預測樣本。
- 相關240 passed、部署故障6 passed；全套3251 passed／100 failed，基線3213／100的100個失敗節點及摘要完全一致，非全套PASS。原始紀錄在本次operations。
- 六鎖實際套用、備份原dirty檔，前後各六項preflight全部ok且waiver/line_guard空；root smoke240 passed。持鎖38.10秒，106個保護檔雜湊不變；正式prompt／模型／窗口／cap／隔離未改。
- 部署後十題再過三閘門，缺口六個均附歷史前檢觀察；11個自有關鍵字命中分類為10個CLI禁止設定、1個負向site查詢。原七題suspected及全部歷史保留；不宣稱無暴露、不補跑。新報告 `reports/practice/d135-closed-health-20261105.md`、`reports/practice/d135-quality-repair-20261105.md`。
- 四份研究候選仍未啟用；下一個獨立窗口先事前登記與留出未用題群，真實遵循度／準確率仍待驗證。沒有新預測、提交或排程；主賽33121未啟用；保留D132／D133／D134未提交內容，未commit/push。

### 已知非阻擋限制

異質舊schema別名或JSON合法但欄位型別損毀仍可能使診斷缺失或中止，不能把未產生報告當作健康；正式帳冊使用標準型別，本輪十題已實測。CLI deny非已知精確格式仍保守算exposure；歷史旗標不重分類。週期證據不代表完整機會史；候選提示詞的來源時間短語長度與實際遵循度仍需獨立窗口驗證。其餘靜態審查限制留在雙審全文。
