# MiniMax 多模態本機影音輸入設計

日期：2026-09-22  
狀態：已由使用者確認

## 背景與目標

`DMXAPI MiniMax 多模態參考生影片` 目前只能從 ComfyUI 畫布接收 `IMAGE`；參考影片與參考音訊只能填寫公網 URL。目標是在不移除既有 URL 欄位、不改變輸出契約的前提下，讓使用者把內建 `Load Video` 與 `Load Audio` 直接接到節點。

## 使用者介面

新增六個選填插口：

- `reference_video_1`、`reference_video_2`、`reference_video_3`：`VIDEO`
- `reference_audio_1`、`reference_audio_2`、`reference_audio_3`：`AUDIO`

六個插口與既有 `reference_images` 同屬參考素材輸入；`reference_image_urls`、`reference_video_urls`、`reference_audio_urls` 全部保留。既有節點 ID、輸出型別、必要欄位與 URL 工作流程不變。

## 資料流程

### 本機影片

1. 從 ComfyUI `VIDEO` 物件讀取時長、容器與串流來源。
2. MP4／MOV 檔案採用原始 bytes，避免無謂轉碼。
3. 其他容器或組合型影片透過 ComfyUI `save_to()` 轉成 MP4/H.264；影片內原有音軌一併保留。
4. 轉成 `data:video/mp4;base64,...` 或 `data:video/mov;base64,...`，建立 `role=reference_video` 的 input 項目。

### 本機音訊

1. 從 ComfyUI `AUDIO` 字典取得 `waveform` 與 `sample_rate`。
2. 只使用 batch 第一筆，保留單聲道或雙聲道，轉為 16-bit PCM WAV。
3. 轉成 `data:audio/wav;base64,...`，建立 `role=reference_audio` 的 input 項目。

### 合併順序與數量

- API input 順序維持：文字、圖片、影片、音訊。
- 同類素材中，本機插口排在 URL 素材之前。
- 本機與 URL 合計：影片最多 3 段、音訊最多 3 段；本機素材先占額度，超出的 URL 截斷並記 warning。
- 參考音訊不可是唯一素材；至少還要有一張參考圖片或一段參考影片。

## 共用邊界

影音序列化與 base64 不放在節點模組自行處理，而是新增到 `dmxapi_common.py` 的共用 helper，符合專案「節點模組不自行做 base64」的規則。`dmxapi_minimax_h3_nodes.py` 只負責媒體排序、角色標註、數量限制與 payload 組合。

## 驗證與錯誤處理

在解析 API Key 或送出付費請求前檢查：

- 本機影片每段 2～15 秒，合計不超過 15 秒。
- 本機音訊每段 2～15 秒，合計不超過 15 秒。
- 單一影片編碼後不超過 50 MB。
- 單一音訊編碼後不超過 15 MB。
- 完整 JSON 請求體（包含 base64 膨脹後的圖片、影片、音訊）不超過 64 MB。
- 無法讀取或轉碼時，以 `[DMXAPI ...]` 前綴回報可操作的錯誤訊息。

URL 素材的時長與檔案大小無法在不額外下載的情況下可靠驗證，維持由上游檢查，避免節點重複下載大型媒體。

## 相容性與風險

- 變更為新增選填輸入，不影響既有 workflow 的必要參數與輸出。
- `generate()` 簽章與 `INPUT_TYPES` 必須同步，並執行專案的自動簽章比對。
- 影片 base64 會額外占用約 33% 記憶體；大型檔案可能超過 64 MB，錯誤訊息需建議改用 URL。
- 原生 `VIDEO` 介面屬 ComfyUI 新 API，import 必須延遲到實際序列化時，確保裸 Python 單元測試仍可載入套件。
- 現有工作樹已有上一輪 MiniMax 修正，實作不得覆蓋或倒退該變更。

## 測試策略

以測試驅動方式新增：

1. `INPUT_TYPES` 暴露三個 `VIDEO` 與三個 `AUDIO` 插口。
2. MP4/MOV 本機影片能產生正確 MIME、base64 與 `reference_video` role。
3. ComfyUI AUDIO 能產生可解析的 PCM WAV 與 `reference_audio` role。
4. 本機素材優先於 URL，兩類合計不超過三個。
5. 影片／音訊單段時長、合計時長、單檔大小與 64 MB body 限制在送出前生效。
6. AUDIO 單獨輸入仍被拒絕；圖片加 AUDIO、VIDEO 單獨輸入均合法。
7. 全套 unittest、`py_compile`、節點簽章比對與 `git diff --check` 通過。

## 變更影響

- `dmxapi_common.py`：新增 VIDEO／AUDIO 序列化 helper。
- `dmxapi_minimax_h3_nodes.py`：新增插口、合併本機與 URL 素材、限制檢查。
- `tests/test_minimax_simplification.py`：新增回歸與邊界測試。
- `README.md`、`CLAUDE.md`：更新接線方式、限制與維護決策。

專案沒有 `requirements.md`、`spec.md`、`UIUX.md` 或 `todolist.md`，因此第五階段文件同步技能的前置條件不成立；本設計文件連同既有 `README.md` 與 `CLAUDE.md` 作為此變更的規格來源。
