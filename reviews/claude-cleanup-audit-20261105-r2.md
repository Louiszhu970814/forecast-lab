【收到確認】我已讀到結尾標記 END-CLEANUP-SUPPLEMENT-20261105，這份資料包沒有被截斷。不過包內沒有 auto_reaccept.py 本體，只有 run.sh:27 呼叫它，所以 Gemini 說的 :46 那段，我這次仍然無法審。我沒有執行任何測試。以下標「已確認」的，只代表摘錄文字或程式碼支持該說法。這不是核准。

1. Cup 規則衝突：已確認，時間點是現在，屬高優先。AGENTS.md:29 仍把 Metaculus Cup 列為試驗場地，PLAN.md:87（D79）已明文排除。這是安全規則互相矛盾，會讓讀 AGENTS.md 的 agent 誤以為可以在 Cup 送出。
   - 修正：修改 AGENTS.md 不保證不影響 baseline。baseline.py:154 的 ancestor_sources 會掃描嘗試目錄的每一層上層目錄裡的 AGENTS.md。專案本身的 AGENTS.md 只有在它真的位於嘗試目錄的上層時才算輸入。規則 5 要求在專案外的暫存資料夾執行，所以理論上不算。
   - 另外 HANDOFF:118 提到「AGENTS.md 保護雜湊」，但包內沒有任何讀取這個雜湊的程式，這點未證實。
   - 建議：先查出實際有哪個雜湊或驗收讀取 AGENTS.md，再決定是普通文件修正，還是要配合重新驗收。方向不必再問 Louis，D79 已經決定了。

2. 兩個未使用的私有函式：部分確認，時間點是現在或 11/10 以外的任何空檔。
   - 我只看到兩個定義（budget.py:106、forecast.py:733）。「335 個檔案都沒有引用」我無法從包內驗證，未證實。搜尋時要包含字串形式的引用，例如 monkeypatch.setattr(模組, "名稱")、getattr 和 R.xxx 這類屬性存取（test_resolve:886 就是這種用法）。
   - 現行強制規定：
     - HANDOFF:98（D126）：不改兩份釘死設定雜湊的修補不必換窗。
     - HANDOFF:164：上正式環境須經 Gemini 獨立審查，再由 Claude 親自完整審查實際 diff。這份審查不能代替。
     - HANDOFF:165：套用時依六鎖互斥程序。
   - 修正：HANDOFF:119 明文規定 11/10 換窗批次「不夾帶程式重構」。所以「等 11/10 一起做」本身就違規，不是保守做法。
   - 另一個風險：forecast.py:70-71 的註解說，共用模組改動需要人工重新驗收。forecast.py 是否屬於這類共用模組未證實，要先釐清。
   - 我個人偏好（不是規定）：效益很小，可以併入下一個已經要取鎖的工程批次，不要另外占用一次鎖窗。
   - 13 個未使用的 import 未證實。adapters/gpt.py 一改就會改變 runtime_fingerprint，必須重新驗收，建議直接不動。

3. Telegram 舊開發模式：程式行為已確認，D91 原文不在包內。時間點是之後。
   - telegram_bot.py:282-283 把 dev_ 開頭的指令導向 team_help，所以 :293、:306 的 dev 分支已經走不到。poll_loop 也不再呼叫 enqueue_dev_notifications。
   - 同意依 D91 處理，不必再問 Louis。實際刪除是未來的提案批次。
   - 必須保留三樣：parse_dev_command 的導向、deliver:437 取消舊 dev_notice 待送訊息的處理、team_chat:15 依賴的 TaskError、safe_text、public_text。先把這些共用函式遷出 dev_tasks，再刪除。

4. D135 的 106 個檔案：部分修正。
   - 已確認：forecast.py:149-150 的指紋只涵蓋 adapters/{player}.py、flab/runner.py、flab/netguard.py。
   - D135 原文不在包內，「只是本次部署範圍的清單」這個說法未證實。
   - 補充：受保護的不只這 3 個檔。還有兩份設定雜湊、上層目錄的指示檔，以及註解要求重新驗收的共用模組和設定。
   - 同意文件不是永久不能動，可以搬到歸檔並在原路徑留指引檔。adapter 的 docstring 不要改，改了雜湊就會變。

5. 失效連結：未證實，包內沒有那 6 個檔案，也沒有 392335b 這個 commit。計畫合理，時間點是現在，屬純文件、不需取鎖：把連結改指向歷史紀錄或 commit 出處，並標「已取代」，不要把舊指示恢復成現行規則。

6. 依賴關係：部分確認，結論是保留。
   - 包內只看得到 team_chat:15 和 team_runner:18（引用 flab.dev_runner）。
   - repair_claude.py、validate.py、project_control.py 不在包內，未證實。保留本來就是保守選擇，不影響結論。

7. flab/__init__.py：已確認 28 個名稱全部列在 __all__ 裡（:5-22 匯入 28 個，:25-52 列 28 個），結論是保留。45 個檔案、123 處互相 import 的數字未證實。

8. 測試失敗：失敗機制已確認，數字未證實。
   - 已確認的機制：
     - test_main_site_trial:58 寫死 "binary,multiple_choice"。
     - test_poll_questions:93 仍斷言數值題要等 M5。
     - test_cycle:369 只預期 unsupported_type。
     - offline_workcopy:22-23 用 mkdir() 建 synthetic-project，沒有 exist_ok。test_config:97 會在同一個 tmp_path 再建一次。
     - 這個目錄也讓 test_resolve:883/895 對目錄呼叫 read_bytes，以及 :907 的空目錄斷言失敗。
   - 未證實：question_types.json 現值，以及 3317/34 這組數字（不是我跑的）。
   - 同意這和 D135 的 3251/100 是不同測試設定，不能比較，也不算改善。
   - 建議修法：測試改成依設定 monkeypatch 型別；plugin 改用 tmp_path_factory 另建目錄，同時保留 :30 的 is_relative_to 篩選。禁止刪掉失敗的測試，也禁止為了通過去改正式設定。純測試修改、不需取鎖，改動前後的測試收集數必須一致。

9. 時程：大致同意，需修正五點。
   - (a) 之後做的清理一律不得併入 11/10 批次（HANDOFF:119）。
   - (b) HANDOFF:118 的 B′ 階段寫死 11/07～11/09，要同步改成以條件觸發。
   - (c) 11/06 21:00 前的 M4（resolve/score）優先，清理工作不得搶鎖或占用審查額度。
   - (d) 「D132～D135 尚未提交的改動」的實際內容未證實。
   - (e) launchd/run.sh:14-15 的「Pending M3」註解已經過時，可以修正。但這個腳本正被 launchd 執行，要用原子替換，或選在沒有執行中嘗試的空檔。
   - 確認以上都不代表啟用主賽，也不構成任何核准。

【仍缺的證據】D79、D91、D126、D135 的原文；AGENTS.md 雜湊由哪裡讀取；question_types.json；335 個檔案的搜尋結果；測試紀錄；失效連結指向的檔案；repair_claude.py、validate.py、project_control.py；auto_reaccept.py。