# 移除全部 Seedance 節點設計

日期：2026-09-23  
狀態：已由使用者確認（方案 2：完整清除）

## 背景與目標

DMXAPI 節點選單目前註冊 7 個 Seedance 2.0 節點。使用者要求完整移除所有 Seedance 節點與相關實作，只保留 GPT Image 2、Agnes 2.1 Flash，以及兩個 MiniMax H3 節點。

## 移除範圍

- 刪除 `dmxapi_seedance2.py`，包含 6 個生成節點、1 個下載節點、Seedance 輪詢與回應解析。
- 刪除所有受 Git 追蹤的 `__pycache__/*.pyc`。這類通用編譯快取可能仍內嵌已刪除的 Seedance 名稱或程式碼；產生物已由 `.gitignore` 排除，不應納入版本控制。
- 從 `__init__.py` 移除 Seedance 模組 import 與 `_MODULES` 註冊。
- 從 `dmxapi_common.py` 移除只供 Seedance 使用的比例、解析度檔位、尺寸換算函式、尺寸輸入定義，以及因此不再使用的 `math` import；保留 MiniMax 仍使用的時長與影片共用功能。
- 從 `.env.example` 移除 `SEEDANCE_API_KEY` 與 `ARK_API_KEY`。
- 更新 `README.md`、`CLAUDE.md`、`AGENTS.md`，刪除 Seedance 使用方式、API 協定與維護規則，並把已註冊節點總數改為 4。
- 更新測試，使節點註冊表必須恰好包含 4 個非 Seedance 節點。

## 保留範圍

- 不更改 GPT Image 2、Agnes 2.1 Flash 或 MiniMax H3 節點的 ID、輸入、輸出與 API 行為。
- 保留 MiniMax 使用的 `/v1/responses` 輪詢、`DMXAPIVideoNodeBase`、影片下載、預覽與抽幀功能。
- 不修改使用者現有 workflow 檔案。

## 相容性與風險

這是刻意的破壞性變更。既有 workflow 若包含任何 `DMXAPI_Seedance2_*` 節點，重啟 ComfyUI 後會顯示缺失節點，且無法再由本套件執行或下載 Seedance 任務。其餘 4 個節點維持相容。

主要技術風險是誤刪 MiniMax 共用功能，或文件、註冊表與通用 bytecode 快取殘留 Seedance 名稱。防護方式是：先建立會失敗的註冊測試，再以最小修改移除註冊；之後用全域文字搜尋確認沒有 Seedance／ARK 殘留、確認 Git 不再追蹤任何 `__pycache__`，並執行完整測試、外部快取 `py_compile` 與 `INPUT_TYPES`／執行函式簽章比對。

## 測試策略

1. 先修改節點註冊測試，直接要求類別與顯示名稱映射的 key 都恰好等於 `DMXAPI_GPT_Image2`、`DMXAPI_Agnes_Image21Flash`、`DMXAPI_MiniMax_Video`、`DMXAPI_MiniMax_Reference2V`；在實作前確認測試因現有 7 個節點而失敗。
2. 移除註冊與程式後，確認該測試轉綠。
3. 執行完整 unittest，確保 MiniMax、GPT Image 2、Agnes 測試仍通過。
4. 編譯所有剩餘 Python 模組。
5. 執行節點宣告與函式簽章一致性檢查，預期顯示 `OK 4 nodes`。
6. 全域搜尋 Seedance 與 `ARK_API_KEY`，並執行 `git ls-files | rg -i seedance`；允許的結果只限歷史設計／實作計畫文件，目前生效的程式、設定與維護文件不得殘留。
7. 執行 `git ls-files __pycache__`，結果必須為空；語法檢查使用 `PYTHONPYCACHEPREFIX` 將 bytecode 寫到版本庫外。
8. 重啟 ComfyUI，確認 DMXAPI 選單只顯示 4 個保留節點。

## 文件流程說明

專案沒有 `requirements.md`、`spec.md`、`UIUX.md` 或 `todolist.md`，因此需求變更文件技能的標準前置條件不成立。本設計文件與後續實作計畫作為此次變更的規格來源，並同步更新現有 `README.md`、`CLAUDE.md` 與 `AGENTS.md`。
