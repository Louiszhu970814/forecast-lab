# D121 第二批：R2-C／R12 接手準備

> GPT 於 2026-11-03 接手後的離線盤點；不是已生效規格，也不是驗收通過報告。
> 依據：PLAN v1.74 D121～D123、`FAIL-SOFT-RELAX-PROPOSAL.md` v2、兩位審查者的既有意見及目前原始碼。
> Louis 已在 D121 選 C；舊提案的「待選方向」是歷史文字，不必重問。D124（UTC 2026-11-03 12:08）已選今晚開工及實測時段；仍須完成審查與還原保護再放入 canary。

## 已完成的準備

- 唯讀執行 `src/autoload.py plan`，盤點 12 個目標：8 個各線全域位置、4 個共用暫存上層位置。只有 Codex 全域 `AGENTS.md` 在本次盤點中已存在，其餘測試路徑預計新建後移除；沒有寫入 canary、呼叫模型或改動全域檔案。
- 核對 `data/acceptance.jsonl` 的既有正式載入證據 `auto_1793528405575_83ee265b`（2026-11-01T10:20:05.575Z～10:20:30.754Z）：三線完成、`restore_ok=true`。這是舊證據，不冒充本輪重驗。
- 核對 `baseline.compare()`、`forecast.preflight()`、`check_acceptance()`、`claude_baseline_candidate.approve()` 與 `autoload.run()` 的接點。
- 尚未實作第二批、未改正式設定與驗收帳冊，未啟動 Claude／Gemini 審查。

## 載入證據與覆蓋缺口

| 來源 | 既有證據 | 第二批處理依據 |
|---|---|---|
| Claude 個人 skills | 11/1 測試 skill 未出現在完整 init skills 宣告，判 `not_loaded` | 不等於所有未測檔案、CLI 版本與執行方式均已排除；須綁定正式執行條件 |
| Claude 同步 skills | D123 已獨立列資訊性；正式輸出若宣告 skills／slash commands／MCP／非 builtin plugin 即停線 | 沿用 D123，不能擴張成全部個人來源已放行 |
| Claude 全域 `CLAUDE.md` | `unknown` | 未見 canary 不構成不載入證據 |
| Codex 全域 `AGENTS.md`、skills | 模型／原始輸出曾出現 canary，判 `loaded` | 不能直接降為資訊性；若設法排除載入，須取得正式路徑的新證據 |
| Gemini 指示、rules、skills | `unknown`，目前無可用完整來源宣告 | 重跑相同測試未必能補足負面證據，規格須先定義判準 |
| 共用暫存上層的 3 份指示及 `.agents/rules/` | 三線均 `unknown` | 共同來源仍保護；測試期間會影響所有線 |
| memories、agents、commands 等其餘來源 | 現有 `autoload.locations()` 未完整涵蓋 | 不可因本次 12 個位置完成就聲稱所有純文字來源均已驗證 |

Codex 的 `codex_declaration()` 使用無設定的 mirror 做 prompt-input 查核；程式明訂其負面結果不能證明正式 exec 不載入。此限制必須保留。

## 待 Claude 定稿與 Gemini 審查的實作契約

以下把已核准方向轉成需驗證的實作條件；若與 PLAN 衝突，以 PLAN 為準。

1. **來源白名單**：逐項列出允許自動重驗的來源及檔案形態，不能僅依目錄名稱稱為「純文字」。skills／agents 可能附帶腳本、執行宣告或連結；未知形態、憑證、外掛、hooks、MCP、共用上層與執行類設定仍不得自動核准。settings 的一般類別也不代表安全，須核對權限及巢狀鍵。
2. **固定候選**：候選綁定來源集合、受保護雜湊、原核准 ID、CLI／模型／effort、runtime 指紋及所需 catalog 雜湊。驗收前、後及核准瞬間均核對；期間再變動即不採用舊結果。
3. **單線限額**：每線滾動 24 小時最多啟動一次自動重驗，失敗、中斷也占名額；同候選雜湊合併。啟動事件須在呼叫前耐久入帳，並行程序只能有一個成功取得名額。每次實際模型呼叫仍計入原有週上限，不另開額度。
4. **限定範圍核准**：借用 D120 的證據綁定方式，不能直接把最新完整 snapshot 交給泛用 `baseline.approve()`。只替換已驗收的本線來源，其他線與共同來源保持原核准；驗收證據檔案雜湊、候選與 `isolation_record_id` 必須一致。
5. **真正被前檢採用**：目前 `latest_approved()` 只讀 `baseline_approved`，`preflight()` 另核對正式模型、runtime 及 catalog。須明定新事件如何被這些入口讀取，測試要證明恢復前檢，而非只新增一筆看似成功的帳冊。
6. **R12 證據種類**：一般基準重驗、正式模型／effort 驗收及 Codex catalog 變動不能混作同一種證據。便宜模型的隔離 PASS 不能取代兩層所需正式模型證據。CLI 固定版本參照 D119；安裝或更換版本及重新驗收另列具體步驟，不讓自動更新後直接沿用舊 PASS。
7. **中斷恢復**：沿用第一批 R9 的逾時、程序不存在與單線鎖條件；補齊自動重驗的 run／attempt 關聯、限流計數及結案冪等性。尚有活程序或證據不完整時不自行認定驗收成功。
8. **鎖的區別**：一般開發／審查使用隔離副本；只驗收單線且不改共享來源時持該線鎖。會放入全域及共同 canary 的測試須與全部預測工作互斥，不能套用「只持一條線鎖」的簡化。

## 維護時段的待執行程序

現有 `autoload.run()` 有備份、manifest 與 finally 還原，但不自行取得 cycle／scheduler／submit／三線鎖。因此不能在 live 窗口直接裸跑它；先準備經審查的持鎖入口及中斷恢復步驟。

1. Louis 確定時段後，先查 `project_control.py status`，確認無其他主持人／驗收工作，依題目 ID 與截止時間判斷是否有足夠空檔；不讀答案。
2. 待在途工作自然結束，再依既有順序取得 cycle、scheduler、submit 與三線鎖；取不到就延後，不中止 Louis 或既有模型程序。
3. 執行前固定目標清單、操作上限、還原 manifest、執行檔版本與受保護 hash。採正式 launchd PATH，避免 Claude 落到非專案 pin 的 CLI。
4. 放入測試標記、走正式 adapter／runner／netguard 條件；只記錄合成載入證據，Metaculus token 不傳給模型。證據不足維持 `unknown`。
5. 在釋放鎖前逐位元核對原檔與權限已還原，新建的檔案與目錄已移除。遇到同步或使用者同時改檔，按 manifest 處理衝突，不覆蓋外來變更。SIGKILL／斷電不能假定 finally 已執行。
6. 還原失敗不恢復送出，交主持人依 manifest 處理；還原成功後核對前檢與下一輪 cycle，發布只含狀態的結果。新的來源分類須經審查及正式套用流程，不能因 probe 結束自動改基準。

11/1 的三線測試實際約 25 秒，但不能據此承諾本輪維護只需 25 秒；鎖等待、CLI／額度查詢、還原與前檢均須包含在預先固定的上限中。

## 測試與交付清單

- 合成測試：允許／禁止來源、腳本與連結、未知設定鍵、其他線／共用 blocker、候選執行中變更、核准 ID 競爭、證據損毀、catalog 改變、正式兩層證據缺失。
- 限流與恢復：同候選去重、同線並行、24 小時邊界、失敗／中斷仍計數、崩潰重入、所有模型呼叫計入原窗口、候選變更不重置成本。
- 實際接點：追加核准後重新執行兩層前檢；錯誤證據仍拒絕。舊帳冊、D123 同步技能與第一批軟失敗語意不回退。
- 受保護檔案：D118 窗口內三份固定設定及 adapters／runner／netguard hash 不變；不靠更換 prompt 或選題規則恢復。
- 交付 patch、完整隔離回歸、還原演練證據與版本遷移說明。正式套用前仍須 Gemini 獨立審查及 Claude 親自完整審查；Claude 額度不足就延後。

本文件只有準備結果；PLAN 的決策、版本及目前預測行為未改變。
