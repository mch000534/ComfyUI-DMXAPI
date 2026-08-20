# ComfyUI-DMXAPI

> 將 DMXAPI 的圖像與影片生成服務封裝成 ComfyUI 自訂節點。

這是一個純 API 客戶端，不在本機載入或執行生成模型。目前共註冊 10 個節點：2 個圖像節點與 8 個影片節點。節點會把 ComfyUI 的文字、圖片與影片輸入轉成 DMXAPI 請求，再將結果轉回 ComfyUI 可用的 `IMAGE`、`VIDEO`、影片路徑與 URL。

## ✨ 功能亮點

- 支援 GPT Image 2 與 Agnes Image 2.1 Flash 文生圖、圖生圖。
- MiniMax 僅支援 `MiniMax-H3`，透過單一整合節點完成文生影片、首幀及首尾幀生成。
- 支援 Seedance 2.0 的文生影片、圖生影片、參考、延長及編輯流程。
- 影片節點統一輸出 `VIDEO`、影格、末幀、檔案路徑、影片 URL 與任務 ID。
- 內建非同步任務輪詢、下載、ComfyUI 影片預覽、重試與 API key 認證形式探測。
- 參考圖會在上傳前轉成 JPEG 並限制尺寸，降低同步 API 的上傳與逾時風險。

## 📋 系統需求

- ComfyUI，建議使用專案目前測試過的 Python 3.12 環境。
- 可連線至 `https://www.dmxapi.cn` 的網路環境。
- 有效的 DMXAPI API key，以及對應模型的使用權限。
- `requirements.txt` 中的 Python 套件：`torch`、`numpy`、`Pillow`、`requests`、`opencv-python`、`imageio`。
- `ffmpeg` 可選；當 OpenCV 與 imageio 都無法解碼時，程式可用它嘗試抽取首幀。

## 🚀 快速開始

### 安裝

將本專案放入 ComfyUI 的 `custom_nodes` 目錄，然後使用 ComfyUI 自己的 Python 環境安裝依賴：

```bash
cd /path/to/ComfyUI/custom_nodes/ComfyUI-DMXAPI
/path/to/ComfyUI/.venv/bin/python -m pip install -r requirements.txt
```

若使用 ComfyUI Desktop，請把 `/path/to/ComfyUI/.venv/bin/python` 替換成 Desktop 使用的虛擬環境 Python。不要用另一個沒有 `torch` 的系統 Python 安裝依賴。

### 設定 API key

最簡單的方式是先複製範例檔，再填入 API key。ComfyUI-DMXAPI 啟動時會自動讀取與 `dmxapi_common.py` 同一層的 `.env`：

```bash
cp .env.example .env
# 編輯 .env，填入你的 API key
```

可直接參考 [`.env.example`](.env.example) 的欄位。

`.env` 是可選的；不存在時會維持原本的作業系統環境變數行為。若同一個變數同時存在於系統環境與 `.env`，系統環境變數優先。也可以不使用 `.env`，依照作業系統選擇以下指令。

#### macOS

只對目前 Terminal 視窗有效：

```bash
export DMXAPI_KEY="sk-your-key"
```

若希望之後開啟的 zsh 終端機都自動套用，可寫入 `~/.zshrc`：

```bash
echo 'export DMXAPI_KEY="sk-your-key"' >> ~/.zshrc
source ~/.zshrc
```

若使用 ComfyUI Desktop，請在設定環境變數後完全退出並重新開啟 Desktop。若 Desktop 沒有讀到 Terminal 的環境變數，可在啟動 Desktop 前執行：

```bash
launchctl setenv DMXAPI_KEY "sk-your-key"
```

#### Windows

PowerShell：只對目前視窗有效；請從同一個視窗啟動 ComfyUI：

```powershell
$env:DMXAPI_KEY = "sk-your-key"
```

PowerShell：永久設定目前使用者的環境變數。執行後請重新啟動 ComfyUI：

```powershell
[Environment]::SetEnvironmentVariable("DMXAPI_KEY", "sk-your-key", "User")
```

CMD：只對目前視窗有效：

```bat
set DMXAPI_KEY=sk-your-key
```

CMD：永久設定目前使用者的環境變數；新的 Terminal 或 ComfyUI 程序才會讀到：

```bat
setx DMXAPI_KEY "sk-your-key"
```

也可以在 Windows 圖形介面開啟「系統內容 → 進階 → 環境變數」，於「使用者變數」新增 `DMXAPI_KEY`，再重新啟動 ComfyUI。

也可以在每個節點的 `api_key` 輸入欄位直接填入 key，或使用模型專屬環境變數。優先序與完整清單請見[設定檔說明](#設定檔說明)。

請不要把真正的 `.env`、API key 寫入 workflow、原始碼或 Git；`.env.example` 只應保留空白或示範值。

### 啟動

正常啟動或重新啟動 ComfyUI。ComfyUI 只會在啟動時掃描 `custom_nodes/`，安裝或修改節點後必須重啟。

啟動後在節點搜尋欄輸入 `DMXAPI`，即可找到本套件的圖像與影片節點。

## 📖 使用說明

### 圖像生成

1. 加入 `DMXAPI GPT Image 2` 或 `DMXAPI Agnes Image 2.1 Flash`。
2. 填寫 `prompt`，選擇模型與輸出設定。
3. 在 `api_key` 填 key，或事先設定環境變數。
4. 如需圖生圖，將 ComfyUI 的 `IMAGE` 接到節點的 `image` 輸入。
5. 將 `IMAGE` 輸出接到預覽、儲存或後續工作流。

GPT Image 2 帶參考圖時會自動改用 `/v1/images/edits` multipart 請求；Agnes Image 2.1 Flash 的文生圖與圖生圖則都使用 `/v1/images/generations`，參考圖放在 `extra_body.image`。

GPT Image 2 的 `size` 是實際像素尺寸，支援 `auto`、`1024x1024`、`1536x1024`、`1024x1536`、`2048x2048`、`2048x1152`、`3840x2160`、`2160x3840`。`gpt-image-2-03` 只支援一張輸出，即使 `batch_size` 設得更高也會自動改成 1。

Agnes 的 `size` 是 `1K`、`2K`、`3K`、`4K` 檔位，`ratio` 另選畫面比例。參考圖 batch 最多使用前 5 張。

### 影片生成

影片生成節點會執行「提交任務 → 輪詢狀態 → 取得影片 URL → 可選下載」流程。生成節點的共同輸入如下：

| 輸入 | 說明 |
| --- | --- |
| `width` / `height` | 用來換算上游的比例與解析度檔位，不代表一定輸出這個像素尺寸。 |
| `duration` | 影片秒數；送給上游前會四捨五入為整數。一般範圍為 4–15 秒，Seedance 影片延長至少 8 秒。 |
| `download_video` | 預設開啟。開啟時下載本地檔案並建立 `VIDEO`；關閉時只取得 URL 與任務 ID。此設定本身不會要求解碼影格。 |
| `max_frames` | `-1` 解碼全部影格；`0` 不解碼影格；大於 0 時最多解碼指定幀數。 |
| `save_dir` | 影片保存目錄；留空時使用 ComfyUI `output` 目錄。 |
| `poll_interval` | 輪詢間隔，預設 8 秒。 |
| `max_wait` | 最長等待時間，預設 900 秒。 |

所有影片節點的輸出固定為：

| 輸出 | 說明 |
| --- | --- |
| `VIDEO` | 可接 ComfyUI 的 `SaveVideo` / `PreviewVideo`；需要 `download_video=True`。 |
| `IMAGE_FRAMES` | 依 `max_frames` 解碼的影格 batch。 |
| `LAST_FRAME` | 優先使用上游的 `last_frame_url`；否則只在 `max_frames != 0` 且確實解碼到影格時取最後一幀，再無則為空白影格。 |
| `VIDEO_PATH` | 本地影片檔案路徑。 |
| `VIDEO_URL` | DMXAPI 回傳的影片 URL。 |
| `TASK_ID` | 非同步任務識別碼；Seedance 任務可交給其下載節點事後取件。 |

高解析度影片請優先保持 `max_frames=0`。此時即使 `download_video=True`、影片已保存到本地，也不會解碼 `IMAGE_FRAMES` 或從影片抽取 `LAST_FRAME`；除非上游另有提供 `last_frame_url`，否則兩者使用空白影格。ComfyUI 的 `IMAGE` 是 `float32` tensor，4K 單張影格約 100 MB，整段影片全部解碼可能消耗數十 GB 記憶體。

### 可用節點

#### 圖像

| 顯示名稱 | 用途 |
| --- | --- |
| `DMXAPI GPT Image 2` | GPT Image 2 文生圖與圖生圖。 |
| `DMXAPI Agnes Image 2.1 Flash` | Agnes Image 2.1 Flash 文生圖、多參考圖合成。 |

#### MiniMax

| 顯示名稱 | 用途 |
| --- | --- |
| `DMXAPI MiniMax 影片生成` | 唯一公開 MiniMax 節點，模型固定為 `MiniMax-H3`。不接影格時為文生影片；可接首幀，或同時接首幀與尾幀。只接 `last_frame` 會在送出請求前報錯。 |

MiniMax H3 目前沒有公開的事後取件節點。生成節點仍會回傳 `TASK_ID`，但無法在另一個 MiniMax ComfyUI 節點中用該 ID 事後取回影片；如需本地影片，請在生成時保持 `download_video=True`。

#### Seedance 2.0

| 顯示名稱 | 用途 |
| --- | --- |
| `DMXAPI Seedance2 文生影片` | 純文字生成影片。 |
| `DMXAPI Seedance2 首幀生影片` | 以一張首幀圖生成影片。 |
| `DMXAPI Seedance2 首尾幀生影片` | 以首幀與末幀控制影片。 |
| `DMXAPI Seedance2 多模態參考生影片` | 使用多張參考圖或參考影片 URL。 |
| `DMXAPI Seedance2 影片延長` | 依來源影片 URL 延長影片。 |
| `DMXAPI Seedance2 影片編輯` | 使用圖片及／或影片 URL 編輯既有內容。 |
| `DMXAPI Seedance2 下載影片` | 以 `task_id` 或既有 `video_url` 下載影片。 |

`DMXAPI Seedance2 下載影片` 若已提供 `video_url`，不會再發任務查詢請求；只提供 `task_id` 時才需要 API key。

## ⚙️ 設定檔說明

目前沒有獨立設定檔；API key 可透過節點輸入、作業系統環境變數或專案目錄的 `.env` 提供。所有變數預設都是未設定。

`.env` 只會在套件載入時讀取一次，支援空白行、`#` 註解、`NAME=value`、`export NAME=value` 及單／雙引號。放置位置是：

```text
ComfyUI/custom_nodes/ComfyUI-DMXAPI/.env
```

| 環境變數 | 用途 | 優先序 |
| --- | --- | --- |
| `DMXAPI_KEY` | 所有節點的通用 fallback key。 | 節點 `api_key` 之後 |
| `OPENAI_API_KEY` | GPT Image 2 的專屬 fallback。 | `DMXAPI_KEY` 之後 |
| `AGNES_API_KEY` | Agnes Image 2.1 Flash 的專屬 fallback。 | `DMXAPI_KEY` 之後 |
| `MINIMAX_API_KEY` | MiniMax 節點的專屬 fallback。 | `DMXAPI_KEY` 之後 |
| `SEEDANCE_API_KEY` | Seedance 2.0 的專屬 fallback。 | `DMXAPI_KEY` 之後 |
| `ARK_API_KEY` | Seedance 2.0 的第二專屬 fallback。 | `SEEDANCE_API_KEY` 之後 |

解析順序是：節點輸入 `api_key` → `DMXAPI_KEY` → 該模組專屬環境變數。HTTP 請求會依端點先嘗試 Bearer 或裸 key，收到 401 時自動嘗試另一種形式；401 與 429 不會進入一般重試。

## 🔌 API 文件

所有請求都送往 DMXAPI：`https://www.dmxapi.cn`。

| 方法 | 路徑 | 使用節點 | 說明 |
| --- | --- | --- | --- |
| `POST` | `/v1/images/generations` | GPT Image 2 純文生圖、Agnes 文生圖／圖生圖 | 同步回傳圖像資料。 |
| `POST` | `/v1/images/edits` | GPT Image 2 圖生圖 | multipart/form-data，上傳參考圖檔案。 |
| `POST` | `/v1/responses` | MiniMax、Seedance | 提交非同步任務、輪詢狀態及取得結果。 |

官方模型文件：

- [DMXAPI Agnes Image 2.1 Flash 文生圖](https://doc.dmxapi.cn/agnes-image-21-flash-t2i.html)
- [DMXAPI Agnes Image 2.1 Flash 圖生圖](https://doc.dmxapi.cn/agnes-image-21-flash-i2i.html)
- [DMXAPI GPT Image 2 文生圖](https://doc.dmxapi.cn/gpt-image-2-text-to-image.html)
- [DMXAPI GPT Image 2 圖片編輯](https://doc.dmxapi.cn/gpt-image-2-image-edit.html)

## 🧱 專案結構

| 檔案 | 內容 |
| --- | --- |
| `__init__.py` | 合併並註冊所有節點模組。 |
| `dmxapi_common.py` | API key、HTTP 請求與重試、輪詢、tensor 編解碼、影片下載與共用影片輸出。 |
| `dmxapi_gpt_image2_node.py` | GPT Image 2 節點。 |
| `dmxapi_agnes_image.py` | Agnes Image 2.1 Flash 節點。 |
| `dmxapi_minimax_h3_nodes.py` | 一個已註冊的 MiniMax H3 整合節點，以及一個刻意不註冊、等待後續重構的參考圖實作。 |
| `dmxapi_seedance2.py` | Seedance 2.0 與下載節點。 |
| `requirements.txt` | Python 依賴清單。 |

新增節點模組時，除了定義該模組的 `NODE_CLASS_MAPPINGS` 與 `NODE_DISPLAY_NAME_MAPPINGS`，也要把模組加入 `__init__.py` 的 `_MODULES`，否則 ComfyUI 不會載入它。

## ❓ 常見問題（FAQ）

### 節點沒有出現在 ComfyUI

確認目錄是 `ComfyUI/custom_nodes/ComfyUI-DMXAPI`，並完全重啟 ComfyUI。可用以下指令確認註冊數量：

```bash
cd /path/to/ComfyUI/custom_nodes/ComfyUI-DMXAPI
PYTHONDONTWRITEBYTECODE=1 /path/to/ComfyUI/.venv/bin/python -c "import importlib.util,sys; p='.'; s=importlib.util.spec_from_file_location('ComfyUI_DMXAPI',p+'/__init__.py',submodule_search_locations=[p]); m=importlib.util.module_from_spec(s); sys.modules['ComfyUI_DMXAPI']=m; s.loader.exec_module(m); print(len(m.NODE_CLASS_MAPPINGS), sorted(m.NODE_CLASS_MAPPINGS))"
```

預期會看到 10 個節點。

### 收到 401 或認證失敗

確認 key 沒有多餘空白，並檢查節點 `api_key` 是否覆蓋了系統環境變數或 `.env`。若使用系統環境變數，請在啟動 ComfyUI 的同一個 shell 中設定它；修改 `.env` 後也必須重新啟動 ComfyUI。不同端點的認證格式可能不同，程式會在 401 時自動嘗試另一種格式。

### `.env` 沒有生效

確認檔案名稱正確、位置是 `custom_nodes/ComfyUI-DMXAPI/.env`，且內容使用 `NAME=value` 格式。ComfyUI 必須完全重新啟動，因為 `.env` 只在節點模組首次載入時讀取。

### 圖像生成逾時或連線被上游切斷

同步圖像端點約有 60 秒回應限制。請依序嘗試：降低 `quality`、改用 `gpt-image-2-ssvip`、指定較小的 `size`（如 `1024x1024` 或 `2048x1152`）、縮短 prompt，並縮小或減少參考圖。請注意請求送出後才中斷，可能代表上游已收單並計費，避免盲目重複執行。

### 影片沒有內嵌播放器

將 `download_video` 保持開啟，並讓 `save_dir` 留空，使影片存到 ComfyUI 的 `output` 目錄。前端只能直接服務 `output`、`input` 或 `temp` 底下的檔案；存到其他路徑時仍會回傳 `VIDEO_PATH`，但只顯示路徑文字。

### 影片影格造成記憶體不足

將 `max_frames` 設為 `0`，只保留影片物件、預覽與檔案；需要後製時再設為明確的正整數。`-1` 會解碼全部影格，不適合高解析度長影片。

### 影片無法抽幀

確認已在 ComfyUI 的 Python 環境安裝 `opencv-python` 或 `imageio`，且影片檔案可正常播放。程式會依序嘗試 OpenCV、imageio，並在只要求一幀時嘗試 `ffmpeg`。

### GPT Image 2 的參考圖為什麼不是直接送 JSON？

DMXAPI 的 GPT Image 2 `/v1/images/generations` 是純文生圖端點；帶參考圖時節點會改用 `/v1/images/edits` 的 multipart 檔案欄位。這與 Agnes Image 2.1 Flash 的 `extra_body.image` 協定不同，不能互換。

## 🤝 貢獻指南

1. 先確認新功能對現有節點輸入順序、輸出契約與既有 workflow 的相容性。
2. 共用的認證、重試、輪詢、編解碼與影片處理應放在 `dmxapi_common.py`，不要在節點內重複實作。
3. 使用 ComfyUI 的 Python 環境進行語法檢查：

   ```bash
   PYTHONDONTWRITEBYTECODE=1 /path/to/ComfyUI/.venv/bin/python -m py_compile __init__.py dmxapi_common.py dmxapi_agnes_image.py dmxapi_gpt_image2_node.py dmxapi_minimax_h3_nodes.py dmxapi_seedance2.py
   ```

4. 重啟 ComfyUI，在畫布上以真實 API 請求實測，並確認新模組已加入 `__init__.py` 的 `_MODULES`。
5. 不要在提交內容中包含 API key、生成結果或其他敏感資料。

目前專案使用 `unittest` 提供離線回歸測試，但沒有獨立建置或 lint 設定；提交前至少應完成單元測試、語法檢查、節點註冊冒煙測試與 ComfyUI 實測。

## 📄 授權

目前專案目錄沒有附帶 `LICENSE` 檔案，因此尚未在程式碼層明確宣告授權條款。重新發布或納入其他專案前，請先向維護者確認授權與 DMXAPI 服務的使用條款。
