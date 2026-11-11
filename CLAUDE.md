# Claude Code 專案設定

共同守則與專案說明都在 AGENTS.md，三方共用同一份：

@AGENTS.md

## 接續開發前先讀

使用者要求在額度中斷時保留接手進度。開始或恢復工作時，先閱讀
`docs/tasks/HANDOFF.md` 與其中連結的任務紀錄，再查看 `git status`。
交接檔只記進度；規格與決策仍以 `PLAN.md` 為準。不要覆蓋工作區內尚未提交的接手修正。

## 只給 Claude 的補充

- 開發模式（Louis 在旁操作時）可以在本資料夾內建立與修改檔案、執行 Python。
- 預測執行模式由程式以 `claude -p` 呼叫，權限僅限 WebSearch 與 WebFetch（第 2 層子代理固定停用），且在專案資料夾以外的暫存資料夾執行。

## 個人對話風格（檔案不進版本控制）

@persona.local.md
