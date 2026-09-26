# AGENTS.md

ComfyUI 自訂節點：DMXAPI（`https://www.dmxapi.cn`）純 API 客戶端。不載入本地生成模型。細節與歷史決策見 [CLAUDE.md](CLAUDE.md)。

## 環境

- Python：**只用** `/Users/barry/Documents/ComfyUI/.venv/bin/python`（3.12 + torch）。系統 `python3` 是 3.14、無 torch，勿用。
- 宿主：ComfyUI Desktop；使用者資料在 `/Users/barry/Documents/ComfyUI`，本體不在此。
- 目錄名含連字號 → **不能** `import ComfyUI_DMXAPI`；測試／冒煙須 `importlib.util.spec_from_file_location`（見 `tests/` 或 CLAUDE.md）。
- 改碼後必須**重啟 ComfyUI**（啟動時才掃 `custom_nodes/`）。
- 無獨立 lint／typecheck／建置；驗證 = `py_compile` + unittest + INPUT_TYPES 簽章比對。

```bash
VENV=/Users/barry/Documents/ComfyUI/.venv/bin/python
$VENV -m pip install -r requirements.txt
PYTHONPYCACHEPREFIX=/private/tmp/dmxapi-pycache $VENV -m py_compile __init__.py dmxapi_common.py dmxapi_agnes_image.py dmxapi_gpt_image2_node.py dmxapi_minimax_h3_nodes.py
PYTHONDONTWRITEBYTECODE=1 $VENV -m unittest discover -s tests -v
# 單一測試
PYTHONDONTWRITEBYTECODE=1 $VENV -m unittest tests.test_minimax_simplification -v
```

改過 `INPUT_TYPES` 或 `generate*` 參數後，務必跑 CLAUDE.md「改動節點後務必檢查的一致性」那段（載入時不報錯、執行才 TypeError）。

## 模組

| 檔案 | 職責 |
| --- | --- |
| `dmxapi_common.py` | 端點、key、HTTP 重試、輪詢、tensor／影片、`DMXAPIVideoNodeBase` |
| `dmxapi_gpt_image2_node.py` | GPT Image 2／2.5（1） |
| `dmxapi_agnes_image.py` | Agnes 2.1 Flash（1） |
| `dmxapi_minimax_h3_nodes.py` | MiniMax H3（2）：首尾幀生成、多模態參考生影片 |
| `__init__.py` | `_MODULES` 合併映射；重複 ID 直接 raise |

新增節點模組 → 必須加入 `_MODULES`，否則不載入。  
節點模組**禁止**自組 headers／重試／base64／輪詢 → 一律走 `dmxapi_common`。

共 4 個已註冊節點（測試會鎖這個數字）。

GPT 節點的可見名稱是 `DMXAPI GPT Image`；內部 ID／class `DMXAPI_GPT_Image2` 與模組檔名維持不變，避免破壞既有 workflow。專屬 key 後援仍為 `OPENAI_API_KEY`。

## 端點（易混）

| 端點 | 誰用 | 注意 |
| --- | --- | --- |
| `POST /v1/images/generations` | GPT Image 2／2.5 文生圖、**Agnes 全文生／圖生** | 同步 JSON |
| `POST /v1/images/edits` | GPT Image 2／2.5 **有參考圖時** | 同步 **multipart**；圖是檔案 bytes，不是 base64 |
| `POST /v1/responses` | MiniMax 提交**與**輪詢 | 靠 payload `model` 區分動作；查詢不是 GET |

**不要把 gpt 與 Agnes 協定互套：**

- GPT Image 2／2.5 參考圖 → `edits` multipart（generations 塞 `image` 會 400）。
- Agnes 參考圖 → 同一 generations，但 `image`／`response_format` 必須在 **`extra_body`**；data URI 陣列，無 edits。
- Agnes `size` = `1K`/`2K`/`3K`/`4K` + 另欄 `ratio`；**不是** `1024x768`。Agnes 2.0 參數不相容 → 要支援就開新節點。
- Agnes 逾時依 size：1K/2K 180s、3K/4K 300s（非 `DEFAULT_TIMEOUT`）。

`/v1/responses` 提交 model 固定為 `MiniMax-H3`，輪詢 model 為 `MiniMax-H3-get`。提交回 `task_id`；成功狀態在 `task.status`，影片 URL 在 `task.content.url`。

## 影片契約

- 兩個 MiniMax 影片節點都繼承 `DMXAPIVideoNodeBase`；輸出固定
  `VIDEO, IMAGE_FRAMES, LAST_FRAME, VIDEO_PATH, VIDEO_URL, TASK_ID`。
- 生成節點收尾用 `self.finish(...)`；共用輸入用 `common_inputs()` / `duration_input()`。
- H3 直接收 `resolution`（`768P` / `2K`）與 `ratio` 列舉，不收 `width` / `height`，也不做像素尺寸換算；`duration` 是 FLOAT，送 API 前由 `duration_seconds()` 轉成整數秒。
- `max_frames`（節點層）：`-1` 全解、`0` 不解（預設）、`N` 上限。底層 `video_to_frames` 的 `0`=不限 → **只經 `decode_frames()`**。
- `download_video` 預設 True（`VIDEO` 要本地檔）。MiniMax **無**公開下載節點；關掉下載後 H3 的 TASK_ID 無法在本套件事後取回。
- `to_video_output`：**延遲** import `comfy_api`，失敗回 None；勿改模組頂層 import。
- 內嵌預覽靠回傳 `ui`，**不要**為預覽設 `OUTPUT_NODE`（按次計費會被當執行根節點空跑）。
- 預覽檔必須在 ComfyUI output/input/temp 下；`save_dir` 指到別處會降級成文字路徑。

MiniMax 有 `DMXAPI_MiniMax_Video` 與 `DMXAPI_MiniMax_Reference2V`；payload `model` 固定 `MiniMax-H3`。前者可無幀／僅 first／僅 last／first+last。後者接參考圖、影片與音訊，與首尾幀互斥；參考音訊不能是唯一素材。

## 圖像／HTTP 雷區

- GPT Image 2.5 有 `gpt-image-2.5-sunburst`（品質優先）、`gpt-image-2.5-sunburst-cdx`、`gpt-image-2.5-sunburst-ssvip`、`gpt-image-2.5-flare`（節點的新預設、速度優先）、`gpt-image-2.5-flare-cdx`、`gpt-image-2.5-flare-ssvip`，六個都是使用者確認可用、由節點公開的選項。官方頁面列出兩個基礎 ID，文生圖頁面另提及兩個 CDX ID 的 `n<=3` 限制；兩個 `-ssvip` ID 是使用者確認。後綴變體依使用者需求實作相同的文生圖／圖片編輯路由，但本次未逐一執行兩個端點的付費冒煙測試，勿聲稱所有後綴都在兩個端點獲官方逐項記載或已實測。
- 舊版 `gpt-image-2-03` / `gpt-image-2` / `gpt-image-2-ssvip` 仍保留。`gpt-image-2-03` 僅 `n=1`；兩個 2.5 CDX 模型最多 `n=3`；其餘由節點限制為最多 4。特殊上限走 `MODEL_BATCH_LIMITS`，`SINGLE_IMAGE_ONLY_MODELS` 僅保留相容性。
- GPT `quality` 為 `auto` / `low` / `medium` / `high` / `xhigh` / `max`；`xhigh` / `max` 僅限 2.5，舊版要在解析 key 前本地拒絕。下拉**必須留在 `INPUT_TYPES` 最後**（workflow `widgets_values` 依位置；插入中間會錯位）。`auto` 時不送該欄位。
- 純文生圖：2.5 依文件／預設省略 `response_format`，舊版保留 `b64_json`。圖片編輯也不送該欄位；`fetch_image_item()` 解析器必須同時接受 `b64_json` 與 URL，但勿把解析能力寫成上游保證兩種格式都會回傳。
- 同步圖像逾時先降 `quality`，再改用有速度優先依據的基礎型號 `gpt-image-2.5-flare`；不可把 CDX／SSVIP 一併描述為速度優先。
- 同步閘道約 **60s** 斷線（無 HTTP status）。送出後斷線最多再試 1 次（`POST_SEND_MAX_ATTEMPTS=2`）；改重試前先想**重複計費**。無非同步 gpt 端點可繞。
- 401：auth 形式 fallback（`/v1/responses` 先裸 key，其餘先 Bearer），勿寫死。429：**不重試**。
- multipart：`build_headers` 不設 Content-Type；`files` 傳 **bytes**（重試會重讀）。
- 上傳參考圖：JPEG + 長邊≤2048（上傳時間算進 60s）。`tensor_to_*` 預設只取 batch `[0]`（Agnes 多圖是例外，自行迴圈）。
- `video_to_frames` 失敗要拋錯，勿靜默黑幀。

## Key 與慣例

- 解析：節點輸入 > `DMXAPI_KEY` > 模組後援（`OPENAI_API_KEY` / `AGNES_API_KEY` / `MINIMAX_API_KEY`）。
- 套件目錄 `.env` 由 `dmxapi_common` 載入；**系統環境變數優先於 `.env`**。勿 commit `.env`。
- 註解、log、節點顯示名：**繁體中文**。log：`logging.getLogger("DMXAPI")`，不用 `print()`。
- 錯誤訊息前綴 `[DMXAPI ...]`（前端直接顯示）。
