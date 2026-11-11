# D137：保留原預算的有限主賽啟用

狀態：**主賽已啟用**（2026-11-05T02:49:33.366Z）；Claude／Gemini r3雙PASS、工程驗證及六鎖接續完成。整合者GPT，PLAN v1.91。原話「複審完 修改完可以 就直接開始正式賽了」保存於私人job的authorization.json，UTC2026-11-05T01:49:59.950469Z；不再重問是否參賽。D136已完成並保留，未commit／push。

## 准入依據與尚缺證據

依此次直接開賽指示，改為複審與工程驗證通過後有限加入，取代先等M4官方分數才進主賽的安排。仍保留官方每題一次、機器人獨立作答、不看開放題輸出改機器人、bot身份／白名單／live窗口／預算／基準前檢及停止開關。

已有D113–D115真實試驗端到端、D116已披露限制的歷史全樣本／壓力重播、M5數值／離散題驗收及D136工程回歸。這不是完整M3／M4結案：D114原觀測只有24／168輪、截至11/03T03:07:28.247Z、含不完整輪詢／不確定題目，ready_for_replay=false、m3_passed=false。當前程式因poll_questions升版不能直接驗證原指紋；已在獨立副本取git b926380a的同SHA原檔，通過原implementation比對後取得上述結果，沒有跳過指紋或補造觀測。M4官方分數／真實結算驗收尚未完成，不作準確度或完整ITT結論。D128舊母體不回填、不擴充成主賽證據。

UTC01:55官方GET確認bot311763（is_bot=true）、主賽33121／fall-futureeval-2026／is_ongoing=true；沒有POST、沒有開放題預測內容。官方規則核對及人類接受沿D106，並非新增人類帳號操作。

## 固定啟用範圍

- 主賽33121＋原MiniBench；bot_testing_area、Cup、主站均不加入live白名單。主賽與MiniBench共用原帳冊與容量。
- campaign `d137_main_minibench_20261105`，授權窗口起點UTC11/05T01:49:59.950Z，實際登記／啟動時間另記。終點仍UTC11/10T04:00Z（台北12:00），不自動續期。
- 根accounting_start仍UTC11/02T13:03Z；Claude／Gemini／GPT上限130／200／130，預期新題10、修復預留2、urgent_slack120秒均沿用。正式run/quota hashes與prompt／模型／統計參數不變。
- continuous_v10／budget_handoff_v2最多兩條來源連結；現有D105→D118→D137。每條不可變來源都須hash相符，拒絕分叉、循環、第三跳、任意重疊、改cap／固定設定／預留與延長截止；v10未登記或runtime與登記不同一律不能取得預算。v1歷史與continuous_v2–v9保留原相容。
- 只更新D121／M5測試清單的授權campaign hash，legacy golden輸出與adapter／runner／netguard／prompt／run_config／quota hashes均保持。

## 部署與復原契約

先完成同SHA雙審／完整回歸與只含用量metadata的帳冊副本演練；再持cycle／scheduler／submit／三模型六鎖，核對閒置、備份及原檔hash，通過三線兩層前檢。先以local作為切換期間的停止寫入狀態，再套用向後相容程式並測試；事前寫好候選config bytes，登記唯一後繼、通過後前檢與部署測試後，最後才切回綁定新campaign的live合約，釋鎖並kickstart既有600秒排程。

**不可刪除或回滾接續帳冊。**正式接續登記前的失敗可恢復原程式／設定；登記後原D118已superseded，任何失敗須保持local、不啟動排程，依同一份候選與SHA有界roll-forward，不能刪登記、重新命名或重起窗口。開始／送出結果未知維持原對帳規則。任何STOP_SUBMIT既有停止旗標不得移除。

## 驗證與交付（完成後追加）

私人證據：`data/operations/d137-main-launch-20261105/`。正式啟用與首輪metadata證據見下方交付；授權、排程active、模型實際開始與POST成功分開記錄。只觀測題數／attempt狀態等metadata，不顯示開放題機率、推理或正文。

## 完成交付（2026-11-05T02:49:33.366Z）

- GPT整合；[Claude r3](../../reviews/claude-d137-r3.md)與[Gemini r3](../../reviews/gemini-d137-r3.md)對同一凍結封包均PASS並確認END。審查依提供的完整diff／腳本／節錄及host證據，兩家未自行跑測試。Gemini r2把部分config列為全文的說法應限縮為實際提供的diff；AST掃描也只能證明165檔中明示import別名的引用，不能排除任意動態存取。原審查保留不改寫。
- 審查修補歷程：r1雙PASS後補強營運；Claude r2（`claude-d137-r2.md`，已於 D150 清理中間輪次）指出B1（launchctl新舊狀態格式）而REQUEST_CHANGES，正式設定尚未變更。本機唯讀輸出確認enabled／disabled格式後，補三態解析、未知／重複拒絕、停用後明確確認、絕對命令路徑及真實樣本測試，再經r3雙PASS。不以Gemini單方PASS取代共同門檻。
- 完整 **3393PASS**（147.96s），原3368節點皆保留＋25（24handoff、1新v10 E2E；舊v9明確保留）。部署故障注入27PASS、stage smoke569PASS；正式root前smoke：373 passed in 3.59s；後smoke：569 passed in 5.05s。前後各6項preflight全部ok、無waiver／line_guard。
- metadata副本演練的六組player/tier切換前後一致：當時已用Claude26、Gemini25、GPT24，上限130／200／130；根起點、Tier2預留12均保留。這是該時點記錄，後續實際呼叫仍會正常累加，開發審查用量另存私人review-usage，不宣稱免費。
- 正式六鎖套用18個受審目標、登記唯一後繼；continuous帳冊原prefix完整，無刪除／重寫。336個原目標外檔案SHA不變。最後切live，有限合約綁定同一campaign／hash；未移除停止旗標，沒有重置驗收／用量／帳冊，沒有commit／push。
- 切換時已disable＋bootout既有600秒launchd排程，完成後enable／bootstrap並kickstart（無-k）；截至2026-11-05T02:53:20.257Z，新campaign有active schedule，首輪cycle已complete且輪詢／前檢／排程／送出檢查正常；可處理題數0。模型開始metadata計數：`{}`；提交帳冊狀態計數：`{}`。本紀錄只描述狀態，不包含開放題預測內容；啟用不等於POST成功，沒有forecast_ok證據就不宣稱已送出。
- 主賽33121與MiniBench共用原預算，截止仍**2026-11-10T04:00Z（台北12:00）**，不自動續期。D114完整M3觀測及M4官方分數／真實結算驗收仍未完成，不能由工程通過推論品質達標。

[來源manifest](../../reviews/d137-source-manifest.json)、[最終驗證](../../reviews/d137-verification.json)。本段及PLAN／交接／索引的交付註記為事後事實性追加；程式、測試、固定配置保持受審位元組。D136原延後清理清單仍依各項條件，沒有趁開賽擴大刪除。
