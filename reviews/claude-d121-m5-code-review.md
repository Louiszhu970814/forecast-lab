# D121 第一批與 M5 合併版程式審查：Claude（Opus 5.5）

> 審查對象：`claudedev_1793687704011_merge/runs/r1/changes.patch`（GPT 實作，50 檔），依據 `docs/tasks/D121-FAIL-SOFT-BATCH1.md`、`docs/tasks/M5-NUMERIC-SPEC.md`（含第十三、十四節）。審查時間 UTC 2026-11-03 09:01–09:15。

結論：修正下列兩項阻擋問題與一項效能問題後 PASS。修正由 Claude 直接完成，並新增 `tests/test_d121_claude_review.py`。這份測試在 GPT 原版上有 4 項失敗，修正後全部通過。

## 阻擋問題（已修正）

- [§7.5 R4／`src/forecast.py` `line_stopped`、`_may_rerun`] 舊版模型不符的判定寫成 `pipeline_version != PIPELINE_VERSION`，也就是只要不是目前版本，就當成舊版硬停線。下次預測流程升版（例如 v8）時，所有 v7 的軟性 mismatch 紀錄都會變成「從帳冊恢復的停線」，並禁止重跑，形成延後引爆的停線。修正：改用固定集合 `LEGACY_MISMATCH_PIPELINES`，只含 v1～v6 與未標版本；v7 以後一律走 30 分鐘退避與 24 小時內 2 次觸發 6 小時冷卻。｜blocking
- [§7.2 R10／`src/baseline.py` `approve(player=...)`] 逐線核准以整筆來源紀錄比對其他線與共用來源。baseline_v2 在每筆來源加上 `baseline_version`，又把祖先 CLAUDE.md 等改歸各線，因此對 v1 核准的基準做逐線核准時一定失敗。另外，其他線的資訊性變更（例如 codex.config）也會阻擋本線核准。修正：只比對受保護指紋，與 `compare()` 的分類一致。逐線核准只重新認證本線來源與本線 CLI 版本；其他線沿用原核准紀錄，各線檢查照常看得到自己的變更；共用來源若有非資訊性變更則拒絕。Gemini credits 若非 Gemini 線，沿用原值，不重新認證。紀錄加上 `scope` 與 `replaces_baseline`。｜blocking

## 非阻擋（已修正）

- [R3／`recover_interrupted`] 迴圈內每一筆歷史開始紀錄都重讀整份 `forecasts.jsonl`，成本是平方成長。目前每次呼叫約 0.7 秒；七週後估計每題數十秒，而且每題、每輪都會呼叫。修正：每次呼叫只讀一次，並在補寫恢復紀錄後同步更新查找表。｜non-blocking

## 已核對、無需修改

- **M5 數學：**
  - 尺度與格點驗證、離散半格幾何、對數尺度定義域、封閉界夾回後的間距重查都已核對。
  - 開放界尾部錨點與單側界外的單調處理已核對。
  - 有界單純形投影：二分法上下界的單調性、最小總質量、10 位捨入誤差相對於 1.1 倍最小步長的餘裕都已核對。
  - 冪等捷徑、伺服器規則鏡像（10 位與 9 位捨入、端點條件、步長上下限）已核對。
  - 整合採線性混合加冪等投影；讀回容差 5e-10、單元素 payload、送出前縮放一致性都已核對。
- **舊題型相容：** 是非題與多選題的 prompt、payload、快照 content 與 content_hash 有逐位元 golden 測試。數值題新欄位只在 numeric／discrete 寫入快照。
- **開關：** `question_types.json` 每次動態讀取；格式錯誤或缺檔一律只支援舊題型。
- **D121 其餘項目：**
  - R1 熔斷以完成時間起算，並以 boundary 推進避免重判。
  - R5 並行不否決准入，credits 非 0 仍是安全停線。
  - R7／R8 等待原因分類、快照解除只限額度相關原因並扣除保留量。
  - R9 不偽造 PASS；R11 私人資料夾不讀內容；R6 TG 問答不持鎖。
- **修改過的舊測試斷言：** 全部對應 D121 已決定的政策變更，沒有發現為了讓測試通過而放寬的斷言。
- **其他：** 驗收指紋檔案（adapters、runner、netguard）與三份固定 campaign 設定都未修改。

## 測試

正常環境（非沙箱）以 `tests/run_offline.py --synthetic-host` 執行完整回歸：**2575 passed，0 failed**。沙箱內受限的 Perl wrapper 與 loopback 兩項在正常環境都通過。

## 審查期間發現的獨立事件（不屬本 patch）

UTC 06:21 起 Claude 線 preflight_blocked，原因是 baseline `claude.skills` 內容變更。互動式 Claude Code 會把 claude.ai 帳號的官方技能同步到 `~/.claude/skills/synced/<bucket>/`，今天觀察到的同步時間為 docx 06:14Z、pdf 08:28Z、manifest 09:01Z。D120 驗收證據顯示，無人值守的 claude 2.1.316 在這個資料夾存在時，init 事件仍宣告 `skills=[]`、`slash_commands=[]`，工具只有 StructuredOutput、WebFetch、WebSearch。處理選項記錄於 HANDOFF，待 Louis 決定（D123）。
