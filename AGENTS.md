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
$VENV -m py_compile *.py
PYTHONDONTWRITEBYTECODE=1 $VENV -m unittest discover -s tests -v
# 單一測試
PYTHONDONTWRITEBYTECODE=1 $VENV -m unittest tests.test_minimax_simplification -v
```

改過 `INPUT_TYPES` 或 `generate*` 參數後，務必跑 CLAUDE.md「改動節點後務必檢查的一致性」那段（載入時不報錯、執行才 TypeError）。

## 模組

| 檔案 | 職責 |
| --- | --- |
| `dmxapi_common.py` | 端點、key、HTTP 重試、輪詢、tensor／影片、`DMXAPIVideoNodeBase` |
| `dmxapi_gpt_image2_node.py` | GPT Image 2（1） |
| `dmxapi_agnes_image.py` | Agnes 2.1 Flash（1） |
| `dmxapi_minimax_h3_nodes.py` | MiniMax H3（1 已註冊）；`DMXAPI_MiniMax_Reference2V` **刻意不註冊** |
| `dmxapi_seedance2.py` | Seedance 2.0（7） |
| `__init__.py` | `_MODULES` 合併映射；重複 ID 直接 raise |

新增節點模組 → 必須加入 `_MODULES`，否則不載入。  
節點模組**禁止**自組 headers／重試／base64／輪詢 → 一律走 `dmxapi_common`。

共 10 個已註冊節點（測試會鎖這個數字）。

## 端點（易混）

| 端點 | 誰用 | 注意 |
| --- | --- | --- |
| `POST /v1/images/generations` | gpt 文生圖、**Agnes 全文生／圖生** | 同步 JSON |
| `POST /v1/images/edits` | gpt **有參考圖時** | 同步 **multipart**；圖是檔案 bytes，不是 base64 |
| `POST /v1/responses` | MiniMax、Seedance 提交**與**輪詢 | 靠 payload `model` 區分動作；查詢不是 GET |

**不要把 gpt 與 Agnes 協定互套：**

- gpt 參考圖 → `edits` multipart（generations 塞 `image` 會 400）。
- Agnes 參考圖 → 同一 generations，但 `image`／`response_format` 必須在 **`extra_body`**；data URI 陣列，無 edits。
- Agnes `size` = `1K`/`2K`/`3K`/`4K` + 另欄 `ratio`；**不是** `1024x768`。Agnes 2.0 參數不相容 → 要支援就開新節點。
- Agnes 逾時依 size：1K/2K 180s、3K/4K 300s（非 `DEFAULT_TIMEOUT`）。

`/v1/responses` 輪詢 model：`MiniMax-H3-get`、`seedance-2-0-get`。  
Seedance 提交回 **`id`**（非 `task_id`）；狀態在 `output[0].content[0].text` 再 `json.loads` 一層；parse 失敗 = 繼續 pending。  
兩套 parse（`_parse_h3` / `_parse_seedance`）**不要合併**。

## 影片契約

- 全繼承 `DMXAPIVideoNodeBase`；輸出固定  
  `VIDEO, IMAGE_FRAMES, LAST_FRAME, VIDEO_PATH, VIDEO_URL, TASK_ID`。
- 生成節點收尾用 `self.finish(...)`；共用輸入用 `common_inputs()` / `size_inputs()` / `duration_input()`。
- 節點收 `width`/`height`/`duration`(FLOAT)；API 收 ratio + 解析度檔位 + 整數秒 → `ratio_from_size`（對數）、`resolution_from_size`（短邊線性）、`duration_seconds`。
- 檔位表：`H3_RESOLUTION_TIERS`（768P=768, 2K=1440）、`SEEDANCE_RESOLUTION_TIERS`。`1920x1080` → H3 的 `768P`；要 2K 用約 `2560x1440`。
- `max_frames`（節點層）：`-1` 全解、`0` 不解（預設）、`N` 上限。底層 `video_to_frames` 的 `0`=不限 → **只經 `decode_frames()`**。
- `download_video` 預設 True（`VIDEO` 要本地檔）。MiniMax **無**公開下載節點；關掉下載後 H3 的 TASK_ID 無法在本套件事後取回。
- `to_video_output`：**延遲** import `comfy_api`，失敗回 None；勿改模組頂層 import。
- 內嵌預覽靠回傳 `ui`，**不要**為預覽設 `OUTPUT_NODE`（按次計費會被當執行根節點空跑）。唯一 `OUTPUT_NODE=True`：`DMXAPI_Seedance2_DownloadVideo`。
- 預覽檔必須在 ComfyUI output/input/temp 下；`save_dir` 指到別處會降級成文字路徑。

公開 MiniMax 只有 `DMXAPI_MiniMax_Video`；payload `model` 固定 `MiniMax-H3`。可無幀／僅 first／first+last；**僅 last_frame 必須提前報錯**。

## 圖像／HTTP 雷區

- gpt `quality` 下拉**必須留在 `INPUT_TYPES` 最後**（workflow `widgets_values` 依位置；插入中間會錯位）。`auto` 時不送該欄位。
- `gpt-image-2-03` 僅 `n=1`（`SINGLE_IMAGE_ONLY_MODELS`）。
- 同步閘道約 **60s** 斷線（無 HTTP status）。送出後斷線最多再試 1 次（`POST_SEND_MAX_ATTEMPTS=2`）；改重試前先想**重複計費**。無非同步 gpt 端點可繞。
- 401：auth 形式 fallback（`/v1/responses` 先裸 key，其餘先 Bearer），勿寫死。429：**不重試**。
- multipart：`build_headers` 不設 Content-Type；`files` 傳 **bytes**（重試會重讀）。
- 上傳參考圖：JPEG + 長邊≤2048（上傳時間算進 60s）。`tensor_to_*` 預設只取 batch `[0]`（Agnes 多圖是例外，自行迴圈）。
- `video_to_frames` 失敗要拋錯，勿靜默黑幀。

## Key 與慣例

- 解析：節點輸入 > `DMXAPI_KEY` > 模組後援（`OPENAI_API_KEY` / `AGNES_API_KEY` / `MINIMAX_API_KEY` / `SEEDANCE_API_KEY`·`ARK_API_KEY`）。
- 套件目錄 `.env` 由 `dmxapi_common` 載入；**系統環境變數優先於 `.env`**。勿 commit `.env`。
- 註解、log、節點顯示名：**繁體中文**。log：`logging.getLogger("DMXAPI")`，不用 `print()`。
- 錯誤訊息前綴 `[DMXAPI ...]`（前端直接顯示）。
