# D130 三線前檢恢復

**目前狀態（UTC 2026-11-04T14:25:43.706Z）：Gemini 已恢復；Claude／GPT 固定外掛候選仍待 Louis 接受，未重驗、未核准。** Gemini 專案固定 1.4.12 已部署，Claude/Gemini 官方 CLI 審查均 PASS，新增隔離 `acc_1793802323516_b0387ed0` 的 29 項檢查全 PASS，兩層正式 preflight 均 ok、無 waiver。基準 `base_1793802331069_37388663` 僅更新 Gemini 的已審一般設定，其他來源保持原核准。

來源：Louis 於 2026-11-04 指示「請處理3線阻塞」，GPT 接手。PLAN 是唯一規格來源；本頁記錄具體診斷與操作證據，恢復結果待完成後同步 PLAN。

## 已確認的原因

| 線 | 差異 | 最小必要驗收 |
|---|---|---|
| Gemini | CLI 1.4.12 → 1.5.0，`agy.cli_settings` 一般設定變更；沒有受保護內容變更 | 1.5.0 tier1 已用 3 calls 未通過；改為固定已驗收 1.4.12，再以便宜模型 3 calls 重驗，兩層正式證據保留 |
| Claude | `claude.plugins` 584 個檔案中 2 檔內容變更 | 接受下方固定候選後，便宜模型一組 3 calls；沿用未變的正式兩層證據 |
| GPT | `codex.plugins` 1282 個檔案總雜湊改變；模型型錄中兩個指示欄位改變 | 接受下方固定候選後，`gpt-6-astra / low` 一組 3 calls；沿用仍有效的正式兩層證據 |

正式 CLI：Claude 專案 pin 2.1.316、Codex 0.166.0、Antigravity 專案 pin 1.4.12；開發用全域 Antigravity 1.5.0 保留。Gemini 全域新版二進位 SHA-256 `e61679b8ba54abae9a56534208e21f65e1dede6e4eb0144dbd64535f42d07724`，本機 Google 簽章驗證通過；沒有找到可驗證的舊版本機副本。實測 Gemini credits 為 0。

開始時保留 accounting_start 的週用量：Claude 21/130、Gemini 17/200、GPT 19/130。本次最小路徑至多新增 12 次驗收呼叫，全部追加入原帳冊；不重設週窗口或歷史用量。訂閱的實際剩餘額度不能由此次數推定。

## 待 Louis 接受的兩個固定外掛候選

PLAN §7.2／D21 要求：「全域指示、記憶、skills、hooks、plugins 或 MCP 的內容改變……經人工審查後再重跑驗收。」D119/D120 已區分「修復指示」與「接受特定新內容」。因此不以本次一般修復要求直接核准這兩個外掛來源，也不把來源降為資訊性。

- **Claude：只接受 `claude.plugins` 新 content hash `cd60d598f7557c6c2346fd3265ae911669e19c2499ea8c1ddcd4a6c53375a605`**（舊 `39cebbf40115ef91210892ef733ff33a6f14257b7e78952a83d582f696756d1b`）。逐檔與 D119 備份對照，僅官方 marketplace.json 的 14 個外掛來源 commit SHA，以及 `.gcs-sha` 變動；其餘 582 檔受保護內容相同，沒有改外掛啟用設定。仍視為受保護變動，不忽略新版指標。
- **GPT：只接受 `codex.plugins` 新總 hash `9a54162305987971609f1da78db5eb025e8c55f91d0f1f6446eb26d273b070f2`**（舊 `75ef61b7f929e9537e6017999c50bf44a691c6213e45b08a6abdffeef15e8a90`）。舊紀錄沒有逐檔明細，現有備份亦不含舊目錄，**無法證明實際差異只限特定檔案**。目前檔數仍為 1282；雖有 Sites 及安裝 metadata 的重寫時間線索，不能以 mtime／ctime 證明內容差異。已封存候選的完整檔案與逐檔 SHA，供本次審查與未來精確對照；不宣稱已證明全部內容無變。

GPT 型錄（CLI 仍 0.166.0）僅 `base_instructions` 與 `model_messages.instructions_template` 兩欄變動：skills 來源及 authority.kind 由 orchestrator 改為 cloud，另刪除尾端空白；其他模型／工具欄位相同。受限條目 SHA 由 `46d3cc6ce9b9cde0bea7a0062867d1ee699995ee94225b2fd966006764422a24` 改為 `29a9c09bd450d93d421397e257c3a356fb4145f6ff37da51746c0348ec36574e`。此次重新驗收會測試新條目，不直接改驗收帳冊的舊 hash。

候選與證據保存在 ignored `data/operations/d130-three-line-repair-20261104/`：`snapshot-before.json`、`baseline-before.json`、`comparison-before.json`、`claude-plugin-files.json`、`gpt-plugin-files.json` 與兩份 zip。任何受保護候選再次改變就停止套用，不能把接受延伸為未來自動更新的概括授權。

## 執行約束與進度

- 維護取得 cycle／scheduler／submit／三線鎖，先確認沒有在途模型或未決送出，不中止現有工作；正式 PATH 與既有隔離測試入口沿用。沒有全域 canary 或使用者檔案改寫。
- Gemini 於 UTC 2026-11-04T13:54:32.343Z 開始重驗；tier1 三次呼叫已完成，record `acc_1793800722388_0b9d667e` **未通過**，未開始 tier2、沒有重試或更新基準。合成驗收日誌證明 1.5.0 已將 `read_url_content` 標為 deprecated 且不提供給模型，導致 URL 拒絕測試 inconclusive、正向讀頁失敗；搜尋、檔案／指令限制及格式修復測試通過。正在查新版相容修復或可驗證的既有版本，不能反覆跑相同設定。
- Claude／GPT 尚未開始重驗、尚未接受新外掛基準。兩線若取得候選接受，先取得對應新隔離 PASS，再只換被接受來源；失敗不更新基準。
- 完成後檢查三線兩層共六項 preflight，並確認正常排程重新顯示 ready。沒有查看開放題預測內容、沒有修改已送出的個別預測。

## Gemini 固定既有版本候選

- [Google 官方 1.4.12 發行包](https://github.com/google-antigravity/antigravity-cli/releases/tag/1.4.12)下載後，archive SHA `4c989b5ccf0e39c99a1ce6df82410abb628d4d83e0d3183527702df744d9c589`；binary SHA `34423fc9b0af3841e7646aa72a2d40ffeab8218b22d1cb99eac7355f9eba31e3`，197589952 bytes，與 D124 維護 identities 記錄逐位元相同。Google LLC／EQHXZ8M8AV 簽章驗證通過。
- 擬新增 `launchd/bin/agy`，僅比賽正式 PATH 走 `data/cli_pins/agy/1.4.12`，每次驗 SHA，拒絕缺檔／非執行檔／symlink／雜湊差異。[Google 官方文件](https://docs.cloud.google.com/docs/security/compliance/deploy-antigravity-us-public-sector)確認 `AGY_CLI_DISABLE_AUTO_UPDATE=true` 關閉背景更新；由 wrapper 在 exec 前設定。全域 1.5.0 保留。
- wrapper 合成測試 5 項通過（缺檔拒絕、參數逐字保留、更新開關強制 true、錯誤 hash 拒絕、symlink 拒絕）。待審查及維護套用後，以原 Flash 驗收組 3 calls 驗證；不修改 adapter／judge／runner／netguard，原 1.4.12 正式兩層證據可沿用，仍須完整兩層前檢通過。
- 初次 1.5.0 failed 保留原狀；新舊結果分開，不把失敗改寫為成功。Gemini 本輪預計總 6 次模型驗收呼叫，含已失敗的 3 次。

## Louis 補充的版本更新節奏

本輪 Louis 指示「我覺得如果有版本更新可以3天做一次」。正式預測環境固定已驗收版本，每 3 天集中檢查版本與執行必要隔離驗收，通過後才切換；開發用全域 CLI 可另行更新。這是更新節奏，不是跳過驗收或對未來外掛內容的概括核准，也不延後眼前故障修復。首個後續版本維護檢查登記於 UTC 2026-11-07T14:25:43.706000Z（台北11/07 22:25:43）；自動更新執行器尚未實作，不宣稱已啟用自動升版。

### 已修正的版本探測相容性

`agy changelog` 首行是上游發布清單，舊版 binary 也回 1.5.0，不能拿它判定已安裝版本。官方 `--version` 在舊 binary 回 `1.4.12`、全域新版回 `1.5.0`。候選 wrapper 在 SHA 驗證後僅將唯一參數 `changelog` 轉為官方 `--version`；不硬寫回覆字串，其他參數原樣轉交。這保留既有 preflight 呼叫介面，免改 fingerprinted baseline／adapter。合成 dispatch／fail-closed 測試通過，真實 wrapper 與直接 `--version` rc0且stdout相符，binary SHA不變；證據 `agy-real-wrapper-smoke-r2.json`。正式隔離仍須從每次 `agy_cli.log` 證明實際執行1.4.12。

## Gemini 實際完成結果

- 維護 run `d130_gemini_pin_1793802113795_7a294b4c`，UTC14:21:53取得空檔並開始，2026-11-04T14:25:43.706Z完成釋鎖；1.4.12 cheap組3calls均完成，29項PASS，搜尋/讀頁/本機限制/Metaculus封鎖/格式修復皆通過。舊1.5.0 failed保留，Gemini本輪共6call，週用量20→23/200（含前3次失敗）；Claude/GPT此次尚無新驗收呼叫。
- 三份合成CLI日誌皆實際回報1.4.12，且觀察到 `Auto-update disabled via environment variable AGY_CLI_DISABLE_AUTO_UPDATE`。控制器保留 `updater_branch_verified=false`，不把日誌標記當成所有內部路徑的形式證明。
- `launchd/bin/agy` SHA `93743416f5089792ada3540c24ef7dfd62ebe6951a525fff36714cc87b095dbf`，與受審受測檔一致；binary SHA不變。核心Python/設定沒有修改，保護清單28筆（17獨立路徑）全部一致；新增wrapper專項pytest 1 passed（涵蓋7條行為檢查），無模型冒煙與可重入跨程序鎖測試PASS。未因單一新增wrapper重跑既有全套。
- Claude初輪要求補交易鎖來源，r2已核對重入機制解除阻擋；Gemini r1 PASS。見 `reviews/claude-d130-pin-review-r2.md`、`reviews/gemini-d130-pin-review-r1.md`、`reviews/d130-pin-manifest.json`。剩餘非阻擋建議保留原審查。
- 三天一次是已登記更新政策；本輪未部署自動升版/自動接受新外掛的執行器。若要恢復Claude/GPT，先取得本文固定候選的明確接受，再執行已備妥的一次性腳本；不得把「3天更新」當成這兩個新內容已接受。
