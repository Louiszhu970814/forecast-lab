VERDICT: PASS

### 一、 阻斷項（Blockers）
- **無（None）**：本次 D147 最小程式碼修復符合 PLAN D107 A4 與 D144 規範，未發現任何安全、邊界穿越、資料外洩或回歸阻斷問題。

---

### 二、 實際證據與五項審查維度評估（Actual Evidence）

根據所提供的源碼封包（`src/baseline.py` SHA256 `d2c471f2...`、`tests/test_codex_cache_alias.py` SHA256 `eee17dd6...`、`tests/test_baseline.py` SHA256 `f141d9ec...`、`tests/test_development_catalogs.py` SHA256 `15e936cd...`），逐項核對如下：

1. **邊界遍歷與路徑穿越防護（Boundary Traversal）**
   - **不穿越別名目錄**：在 `_hash_dir()` 中，`os.walk(..., followlinks=False)` 遇到 `p.is_symlink()` 時不會將 `d` 加入 `keep`（隨後 `dirnames[:] = keep`），確保掃描器絕不透過 `latest` 符號連結進入目錄樹，避免重複雜湊或無窮遞迴。實際版本目錄（如 `26.1027.51404`）由同層既有 `os.walk` 獨立走訪並雜湊內部檔案。
   - **嚴格結構與同層驗證**：`_codex_cache_alias()` 強制驗證 `len(rel.parts) == 4`、`rel.parts[0] == "cache"` 且 `rel.name == "latest"`（僅限 `cache/<provider>/<plugin>/latest` 四層結構）。
   - **禁止鏈式跳轉與外部目標**：透過 `real = target.resolve(strict=True)` 搭配 `target.absolute() != real`（阻擋符號連結鏈 `chain` 以及包含 `..` 的相對路徑跳轉）、`real.parent != path.parent.resolve()`（強制目標必須與 `latest` 位於同一個已解析之父目錄下，阻擋跨 plugin、指向祖先目錄或外部目錄）、`not real.is_dir()`，以及 `re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", real.name)`（強制目標名稱為三段式純數字版本號，並使用 `fullmatch` 防止字尾換行繞過）。
   - **排除清單檢查**：對 `(target_rel, *target_rel.parents)` 檢查 `exclude`，避免透過別名指向被排除的子路徑。

2. **未讀取路徑與憑證防護（Not-Read Paths）**
   - `_codex_cache_alias()` 僅呼叫 `os.readlink()` 與路徑解析，不讀取任何檔案內容，且明確檢查 `any(PRIVATE_DIR.fullmatch(p) or CREDENTIAL_FILE.search(p) for p in rel.parts)`，拒絕路徑中包含私密目錄或憑證名稱的情況。
   - 目標名稱受限於 `[0-9]+(?:\.[0-9]+){2}`，不可能匹配 `PRIVATE_DIR` 或 `CREDENTIAL_FILE`。
   - 目標版本目錄內的實際內容由一般 `os.walk` 掃描：若版本目錄內含憑證檔（如 `.env`），仍會進入 `not_read` 並觸發 `contains private folders or credential files that are not read; review by hand` 阻斷錯誤；若含私密目錄（如 `sessions`），則依 D121 記錄於 `informational_private_dirs` 且不展開讀取；若含指向外部的巢狀符號連結，仍會列入 `unsafe_symlinks` 並阻斷。

3. **精確綁定與 D144 證據約束（Exact Binding）**
   - 合法別名會以 `[rel, "symlink->plugin-version-dir", alias]`（其中 `alias` 為 `target_rel.as_posix()`）寫入 `entries` 與 `raw_entries`，並納入 `out["hash"] = hash_obj([entries, not_read])` 及 `entries` 計數。
   - 當 `latest` 重新指向其他版本目錄（如 `26.1027.51405`）或目標版本目錄內的 `SKILL.md` 內容變更時，`rec["hash"]` 必然改變：
     - 於一般執行期漂移比對（`compare(..., catalog_drift=True)`）中，因 `codex.plugins` 屬於 `DEVELOPMENT_CATALOGS` 且無 `error`，變更歸類為 `INFORMATIONAL`（`decisions["gpt"] == "ok"`）。
     - 於嚴格驗收與證據綁定（`compare(..., catalog_drift=False)` 與 `_same_protected()`）中，`codex.plugins` 維持原始 `CONTENT` 類別，雜湊變更會回報 `stop` 且 `_same_protected()` 回傳 `False`，不產生自動核准或覆寫既有基線。

4. **回歸風險與窄化範圍（Regression & Narrow Scope）**
   - `inspect_source()` 僅在 `src.source_id == "codex.plugins"` 時傳入 `codex_cache_aliases=True`，預設值為 `False`。其他來源（如 `codex.skills`、`claude.plugins`、`agy.plugins`、祖先目錄等）行為完全不變。
   - 提示詞（prompts）、權限、執行期設定、模型參數、聚合邏輯與排程器均未更動。
   - 封包內記錄焦點測試 106 項通過（完整測試套件另行執行中；本審查未執行任何測試或指令）。
   - 部署計畫具備完整安全防護：於閒置安全點取得 6 把既有鎖、驗證前後 SHA256、備份並原子替換 `baseline.py` 與新測試、三方 player 雙層 preflight 無豁免（no waiver）驗證、受保護雜湊不變、失敗自動回滾、不附加 `baseline_approved` 或 `line_resume`、不修改使用者 plugin 檔案，並由既有自然排程器推進第四筆排隊預測。

---

### 三、 非阻斷建議項（Proposals）
1. **目錄走訪期間的競態容錯細化（可選、非阻斷）**：目前 `_codex_cache_alias()` 內的 `os.readlink(path)` 與 `target.resolve(strict=True)` 若在 `os.walk` 判定 `p.is_symlink()` 後發生瞬間刪除（TOCTOU 導致 `FileNotFoundError` / `OSError`），會由外層 `inspect_source()` 捕捉 `(OSError, ValueError)` 並寫入 `rec["error"] = "cannot inventory: ..."`（同樣會安全阻斷該條線）。未來若欲讓 `_hash_dir()` 直接呼叫時也具備一致的 `unsafe_symlinks` 記錄，可在 `_codex_cache_alias()` 內捕捉 `OSError` 並回傳 `None`。目前實作已確保 fail-closed，不影響本次部署。

END D147 R1
