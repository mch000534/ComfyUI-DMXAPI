# MiniMax H3 節點精簡設計

## 目標

將 MiniMax 節點由五個精簡為一個對外註冊的整合節點，並將支援模型收斂為 `MiniMax-H3`。移除目前不再需要的獨立文生影片、圖生影片、下載影片，以及 H3-01、Hailuo-02 相關實作，降低維護與使用成本。

核心用戶故事：作為 ComfyUI workflow 使用者，我想用單一 MiniMax 影片生成節點完成 H3 的文字、首幀或首尾幀影片生成，以便減少重複節點與模型選擇混亂。

## 範圍

### 保留

- `DMXAPI_MiniMax_Video` 類別與顯示名稱 `DMXAPI MiniMax 影片生成`。
- `model` 輸入欄位及其現有位置，避免後續欄位因 ComfyUI `widgets_values` 位置式儲存而整體錯位。
- `model` 唯一選項 `MiniMax-H3`。
- 現有 `prompt`、`first_frame`、`last_frame`、尺寸、時長、seed、prompt optimizer、下載、抽幀、保存與輪詢設定。
- H3 提交、輪詢、下載結果與六槽影片輸出契約。
- `DMXAPI_MiniMax_Reference2V` 類別主體，供未來獨立重構使用，但本次取消對外註冊；其模型選項同步收斂為 `MiniMax-H3`，避免保留已移除模型的死碼。

### 刪除

- `DMXAPI_MiniMax_T2V` 類別與註冊。
- `DMXAPI_MiniMax_I2V` 類別與註冊。
- `DMXAPI_MiniMax_DownloadVideo` 類別與註冊。
- `MiniMax-H3-01` 模型選項與相關判斷。
- `MiniMax-Hailuo-02` 模型選項。
- Hailuo 任務解析、查詢、取檔、系列探測與 payload 組裝程式。
- 移除後不再使用的 import、常數及輔助方法。

### 明確不在本次範圍

- 不重新設計 `DMXAPI_MiniMax_Reference2V` 的角色圖、風格圖、音訊輸入或 payload；只將既有 model 選項收斂為 H3，並標示為非公開待重構類別。
- 不新增 MiniMax 下載替代 node。
- 不修改 Seedance、GPT Image 2 或 Agnes 節點。
- 不發送付費 DMXAPI 生成請求。

## 對外介面

精簡後只註冊一個 MiniMax node：

```text
DMXAPI_MiniMax_Video → DMXAPI MiniMax 影片生成
```

其 `model` 欄位保留為單一選項：

```text
MiniMax-H3
```

未提供任何影格時為文生影片；提供首幀或首尾幀時，使用現有 H3 image role payload。若只提供 `last_frame` 而沒有 `first_frame`，節點必須在送出 API 請求前拋出明確的 `ValueError`，不接受語意不完整的尾幀模式。輸出維持：

```text
VIDEO, IMAGE_FRAMES, LAST_FRAME, VIDEO_PATH, VIDEO_URL, TASK_ID
```

## 程式結構

修改集中在 `dmxapi_minimax_h3_nodes.py`：

- `MiniMaxVideoBase` 只保留 H3 所需的 API key、圖片編碼、尺寸換算、提交、H3 輪詢與 H3 payload 組裝。
- `DMXAPI_MiniMax_Video` 固定走 H3 流程，但保留 `model` 方法參數以維持 ComfyUI node 介面位置。
- `DMXAPI_MiniMax_Reference2V` 保留類別定義並依賴同一套 H3 基底，model 選項只有 H3，且以註解明確標示為非公開待重構類別；它不出現在兩個 mapping 中。
- `NODE_CLASS_MAPPINGS` 與 `NODE_DISPLAY_NAME_MAPPINGS` 只包含 `DMXAPI_MiniMax_Video`。

不拆分新檔案，避免把尚未開始的參考圖重構混入本次變更。

## 文件影響

- `README.md`：更新功能亮點、MiniMax 節點清單、模型支援、下載說明、專案結構與預期 node 數量。
- `CLAUDE.md`：更新節點總數、MiniMax 模組描述、非同步協定、尺寸檔位、下載節點及 Hailuo 相關開發指引。
- 專案目前沒有 `requirements.md`、`spec.md` 或 `todolist.md`，本次不新建這三份舊流程文件；以本設計文件及後續 implementation plan 作為變更依據。

## 相容性與風險

這是刻意的破壞性變更：

- 舊 workflow 使用三個已刪除 node 或已取消註冊的參考圖 node 時，ComfyUI 會顯示缺失節點。
- 整合 node 的舊 workflow 若保存 `MiniMax-H3-01` 或 `MiniMax-Hailuo-02`，需要手動改成 `MiniMax-H3`。
- 保留 `model` 欄位位置可避免其後所有 widget 值發生位置錯位，但不能讓已移除模型繼續工作。
- 參考圖類別仍在原始碼中但未註冊；後續重構不得被誤認為目前支援的公開 node。

## 驗證

完成條件：

1. 所有 Python 模組通過 `py_compile`。
2. 套件冒煙載入後共註冊 10 個 node，其中只有一個 MiniMax node。
3. `DMXAPI_MiniMax_Video.INPUT_TYPES()` 的 model 選項只有 `MiniMax-H3`。
4. `DMXAPI_MiniMax_T2V`、`DMXAPI_MiniMax_I2V`、`DMXAPI_MiniMax_Reference2V`、`DMXAPI_MiniMax_DownloadVideo` 均不在註冊 mapping。
5. 所有已註冊 node 的 `INPUT_TYPES` 與 `FUNCTION` 方法簽章一致，輸出名稱與型別數量一致。
6. 原始碼與文件中不再把 H3-01 或 Hailuo-02 描述為現行支援功能；保留的歷史或破壞性變更說明必須清楚標示為已移除。
7. 不發送 API 的行為檢查證明：H3 payload 的 `model` 固定為 `MiniMax-H3`，只提供 `last_frame` 時會在提交前失敗。
