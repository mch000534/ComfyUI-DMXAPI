# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 專案性質

ComfyUI 自訂節點包，封裝 **DMXAPI**（`https://www.dmxapi.cn`，OpenAI 相容的第三方模型聚合閘道）的圖像／影片生成服務，共 10 個節點：2 個圖像節點與 8 個影片節點。

這是**純 API 客戶端**，不做任何本地推論。`torch` / `numpy` / `Pillow` 只用於 ComfyUI tensor 與 base64 之間的轉換，`opencv-python` / `imageio` 只用於影片抽幀。修改時不要引入本地模型載入邏輯。

## 開發環境與指令

宿主為 **ComfyUI Desktop**（根目錄 `/Users/barry/Documents/ComfyUI`，App 在 `/Applications/Comfy Desktop.app`）。該根目錄只有使用者資料，ComfyUI 本體程式碼不在此處。

Python 環境是 `/Users/barry/Documents/ComfyUI/.venv`（3.12.11）。**不要用系統 `python3`**（3.14，沒有 torch）。

本專案使用標準庫 `unittest` 執行離線回歸測試，沒有獨立建置或 lint 設定。開發循環是：改檔 → 跑離線測試與檢查 → 重啟 ComfyUI → 在畫布上實測。

```bash
VENV=/Users/barry/Documents/ComfyUI/.venv/bin/python

# 安裝相依套件
$VENV -m pip install -r requirements.txt

# 語法檢查
$VENV -m py_compile *.py

# 離線回歸測試
PYTHONDONTWRITEBYTECODE=1 $VENV -m unittest discover -s tests -v

# 冒煙測試：確認節點註冊（目錄名含連字號，無法直接 import，須用 spec 載入）
$VENV -c "
import importlib.util, sys
p = '/Users/barry/Documents/ComfyUI/custom_nodes/ComfyUI-DMXAPI'
spec = importlib.util.spec_from_file_location('ComfyUI_DMXAPI', p + '/__init__.py', submodule_search_locations=[p])
m = importlib.util.module_from_spec(spec); sys.modules['ComfyUI_DMXAPI'] = m; spec.loader.exec_module(m)
print(len(m.NODE_CLASS_MAPPINGS), list(m.NODE_CLASS_MAPPINGS))"
```

改完程式後必須**重啟 ComfyUI**；ComfyUI 只在啟動時掃描 `custom_nodes/`。

### 改動節點後務必檢查的一致性

ComfyUI 最常見的失敗是 `INPUT_TYPES` 的欄位與 `FUNCTION` 指向的方法簽章對不上，載入時不會報錯、執行才拋 TypeError。改過任何 `INPUT_TYPES` 或 `generate()` 參數後，跑這段自動比對：

```bash
$VENV -c "
import importlib.util, sys, inspect
p = '/Users/barry/Documents/ComfyUI/custom_nodes/ComfyUI-DMXAPI'
spec = importlib.util.spec_from_file_location('ComfyUI_DMXAPI', p + '/__init__.py', submodule_search_locations=[p])
m = importlib.util.module_from_spec(spec); sys.modules['ComfyUI_DMXAPI'] = m; spec.loader.exec_module(m)
for name, cls in m.NODE_CLASS_MAPPINGS.items():
    s = cls.INPUT_TYPES(); declared = set(s.get('required', {})) | set(s.get('optional', {}))
    sig = inspect.signature(getattr(cls, cls.FUNCTION))
    params = set(sig.parameters) - {'self'}
    need = {k for k, v in sig.parameters.items() if k != 'self' and v.default is inspect.Parameter.empty}
    if (need - declared) or (declared - params):
        print('X', name, sorted(need - declared), sorted(declared - params))
    assert len(cls.RETURN_TYPES) == len(cls.RETURN_NAMES), name
print('OK')"
```

## 架構

### 模組佈局

| 檔案 | 內容 |
| --- | --- |
| [dmxapi_common.py](dmxapi_common.py) | **所有共用邏輯**：端點常數、Key 解析、HTTP 重試、輪詢迴圈、tensor 編解碼、影片下載與抽幀、影片節點基底類別 |
| [dmxapi_gpt_image2_node.py](dmxapi_gpt_image2_node.py) | GPT Image 2 圖像生成（1 個節點） |
| [dmxapi_agnes_image.py](dmxapi_agnes_image.py) | Agnes Image 2.1 Flash 圖像生成（1 個節點） |
| [dmxapi_minimax_h3_nodes.py](dmxapi_minimax_h3_nodes.py) | MiniMax H3 影片（1 個已註冊節點）；`DMXAPI_MiniMax_Reference2V` 類別刻意不註冊，等待後續重構 |
| [dmxapi_seedance2.py](dmxapi_seedance2.py) | 豆包 Seedance 2.0 影片（7 個節點） |

**節點模組不應自行組 headers、自行寫重試迴圈、自行做 base64 編碼或自行輪詢**——這些一律走 `dmxapi_common`。新增節點時先看共用模組有沒有現成的東西。

套件的註冊檔用 `_MODULES` 清單合併各模組的映射，並會在節點 ID 重複註冊時直接拋錯。新增節點模組時**必須**一併加進該清單，否則不會載入。

### 三個 API 端點

| 端點 | 使用者 | 型態 |
| --- | --- | --- |
| `POST /v1/images/generations` | gpt_image2（純文生圖）、agnes | 同步，OpenAI 相容 JSON，直接回傳 `data[].b64_json` 或 `url` |
| `POST /v1/images/edits` | gpt_image2（帶 `image` 時） | 同步，**multipart/form-data**，回傳格式同上 |
| `POST /v1/responses` | minimax、seedance | 非同步，提交 → 輪詢 |

**gpt-image-2 的參考圖不能塞進 generations 的 payload**：上游會回 400 `Unknown parameter: 'image'`（實測確認）。（Agnes 是另一回事——它的參考圖走同一個端點的 `extra_body.image`，見下節。）`generations` 是純文生圖端點，圖生圖一律走 `edits`，而且圖是 multipart 的**檔案欄位**，不是 base64 字串——所以 `_submit_edit()` 用 `common.tensor_to_image_bytes()` 取原始 bytes 走 `common.post_multipart()`，不用 `tensor_to_data_url()`。`edits` 也不送 `response_format`，避免再吃一次 `unknown_parameter`；回傳由 `fetch_image_item()` 判讀（`b64_json` 與 `url` 都吃）。

### Agnes Image 2.1 Flash 的參數事實（依官方文件）

文件：[文生圖](https://doc.dmxapi.cn/agnes-image-21-flash-t2i.html)、[圖生圖](https://doc.dmxapi.cn/agnes-image-21-flash-i2i.html)。

跟 gpt-image-2 同樣打 `/v1/images/generations`，但**協定細節完全不同**，不要把兩邊的
寫法互相套用：

- **文生圖與圖生圖是同一個端點**，差別只在有沒有帶 `extra_body.image`。這裡**不存在
  `/v1/images/edits` 那條路**，也不用 multipart——參考圖是 JSON 裡的 data URI 字串陣列。
- **`image` 與 `response_format` 都必須包在 `extra_body` 裡**，放到請求體頂層會被上游拒絕
  （官方文件明文標注）。`response_format` 上游預設 `url`，`b64_json` 也吃，兩者都由
  `fetch_image_item()` 判讀。
- **`size` 是解析度檔位（`1K` / `2K` / `3K` / `4K`）而不是像素尺寸**，寬高比另由 `ratio`
  指定（`1:1` / `3:4` / `4:3` / `16:9` / `9:16` / `2:3` / `3:2` / `21:9`，預設 `1:1`）。
  兩者的組合決定實際輸出尺寸，對照表抄在節點的 `DIMENSIONS` 常數裡，只用於 log 提示。
- **`agnes-image-2.0-flash` 的參數與 2.1 不相容**：2.0 收的是 `1024x768` 這種像素字串且
  沒有 `ratio`。要加 2.0 就開新節點，不要塞進同一個 model 下拉。
- `image` 是陣列，多張即「多圖合成」。節點把 IMAGE batch 逐張編碼送出，超過
  `MAX_REFERENCE_IMAGES`（5）會截斷並示警。
- 官方建議客戶端逾時抓 60~360 秒，因此節點依 `size` 分兩段（1K/2K 180 秒、3K/4K 300 秒），
  不是共用 `DEFAULT_TIMEOUT`。

### gpt-image-2 的參數事實（依官方文件）

文件：[文生圖](https://doc.dmxapi.cn/gpt-image-2-text-to-image.html)、[圖片編輯](https://doc.dmxapi.cn/gpt-image-2-image-edit.html)。

- **`quality`**：`auto`（預設）/ `high` / `medium` / `low`。這是**唯一能直接縮短生成時間**的參數，也就是撞上 60 秒上限時的第一順位解法。節點的預設維持 `auto`（不改上游預設），值為 `auto` 時不送這個欄位。
- **模型變體**：`gpt-image-2`、`gpt-image-2-ssvip`（官方標示「更穩定的服務品質和更快的回應速度」）、`gpt-image-2-03`。**`gpt-image-2-03` 只支援 `n=1`**，其餘變體 1~10；節點的 `batch_size` 上限是 4，遇到 `-03` 會夾成 1 並記 warning（`SINGLE_IMAGE_ONLY_MODELS`）。
- **`edits` 的 `image` 其實支援多張與公網 URL**，目前節點只送 batch 第一張，多張時記 warning。要做多參考圖時從這裡下手。
- 其他未接的參數：`background`、`output_format`（`png` / `jpeg` / `webp`）、`output_compression`。
- **沒有非同步模式**：gpt-image 系列沒有 `task_id` 或 `callback_url`，所以同步的 60 秒上限**沒有繞路可走**，只能靠 quality / 模型 / 尺寸 / prompt 長度把生成時間壓進去。新增節點前不要再花時間找非同步端點。

`quality` 的下拉**刻意擺在 `INPUT_TYPES` 最後**，不要為了排版把它移到中間——既有 workflow 的 `widgets_values` 是依位置存的，插在中間會讓舊檔的值整排錯位。

`post_json()` 與 `post_multipart()` 共用同一個 `_post()` 迴圈，因此重試策略與認證探測完全一致。差別只在標頭：帶 `files` 時 `build_headers()` **不設 `Content-Type`**，交給 requests 產生含 boundary 的那一行。multipart 的 `files` 值一律傳 bytes，不要傳開啟的檔案物件——重試時串流已經讀完了。

`/v1/responses` 是**單一端點多工**：MiniMax H3 與 Seedance 的提交和輪詢都 POST 到同一個 URL，靠 payload 裡的 `model` 欄位區分動作。目前查詢動作只有 `MiniMax-H3-get` 與 `seedance-2-0-get`。理解這點是讀懂本專案的關鍵——「查詢狀態」不是 GET 某個 task URL，而是換個 `model` 名字再 POST 一次。

### 兩套非同步協定

輪詢的**迴圈骨架已統一**在 `common.poll_task()`（計時、間隔、容錯、錯誤訊息格式），但各家的狀態欄位與字串是上游決定的，無法統一，因此透過 `parse` 回呼分別處理。`parse(data)` 回傳 `(state, value)`，`state` 為 `"done"` / `"pending"` / `"failed"`。

1. **MiniMax H3**（`_parse_h3`）：提交 → `task_id` → `model=MiniMax-H3-get` 輪詢 → `task.status == "succeeded"` → 影片在 `task.content.url`。
2. **Seedance 2.0**（`_parse_seedance`）：提交回傳的 key 是 **`id`** 而非 `task_id`；輪詢結果**多包一層 JSON 字串**——真正的狀態在 `data["output"][0]["content"][0]["text"]`，必須再 `json.loads()` 一次。解析失敗視為「仍在排隊」而繼續輪詢，不是錯誤。

兩套協定的成功狀態目前都使用小寫 `succeeded`，但回應結構不同，不要因此合併解析器。

### 與官方範本的介面對齊

整合的 `DMXAPI_MiniMax_Video` 直接對齊 ComfyUI 官方 H3 範本命名，可替換範本裡的本地推論子圖：`first_frame` / `last_frame` / `prompt` / `duration` / `noise_seed`。尺寸欄位是例外——H3 只收列舉，因此改成 `resolution` / `ratio` 兩個下拉（見下節），不再提供 `width` / `height`。範本的 `unet_name`、`clip_name`、`vae_name`、`audio_vae` 是本地模型載入用的，API 版換成 `model` 與 `api_key`。

七個影片生成節點在適用時共用 `prompt` / `duration` / `noise_seed` 命名（Seedance 仍收 `width` / `height`，H3 收 `resolution` / `ratio`）；以影格控制生成的節點使用 `first_frame` / `last_frame`。Seedance 的多模態參考、影片延長與影片編輯另有各自的圖片及影片 URL 欄位，不能視為與 H3 完全相同的介面。`DMXAPI_Seedance2_DownloadVideo` 是第八個影片節點，使用獨立的下載介面，不接收生成節點的 prompt、尺寸、時長或 seed 欄位。**新增生成節點時應沿用適用的共通命名，但專用輸入仍須清楚區分。**

公開 MiniMax 介面只有 `DMXAPI_MiniMax_Video`，其 `model` widget 只提供 `MiniMax-H3`。影格輸入有四種合法組合，與上游一致：不接影格為文生影片、只接 `first_frame`（首幀）、**只接 `last_frame`（尾幀）**、或兩者都接（首尾幀）。只接 `last_frame` 曾被節點擋下，但上游本來就支援，已解除限制——不要再加回這個檢查。兩個影格都沒接（純文生）時才強制 `prompt` 非空。H3 payload 的 `model` 固定為 `MiniMax-H3`，圖片 role 沿用 `first_frame` / `last_frame`，不要讓舊 workflow 傳入的 model 值改變實際 payload。

`prompt_optimizer`（bool，預設 `True`）**不在 DMXAPI 的 H3 文件欄位清單裡，但實測確認上游確實吃**——
同一組 prompt 開關兩次的結果有明顯差異。它是 Hailuo-02 / T2V-01 那代 `video_generation`
端點的參數，H3 顯然仍相容。`True` 時上游會先改寫、擴寫 prompt 再生成（短 prompt 效果較好），
`False` 則嚴格照原文，適合已寫細的長 prompt 或要求可重現的情況。**不要因為文件沒列就把它拿掉。**
同樣未記載的還有 `seed`（`noise_seed > 0` 時才送），但**實測結果相反：關掉 `prompt_optimizer`、
固定同一組 prompt 與 `noise_seed` 跑兩次，拿到的是兩支不同的影片**——H3 不保證可重現，
`seed` 形同被忽略。欄位仍保留（送出無害，且它是 ComfyUI 的快取鍵之一：改動 `noise_seed`
才能讓同參數的節點重新執行而不是直接回傳上次結果），但**不要在 UI 或文件上宣稱它能重現結果**。

### 尺寸：H3 直接收列舉，Seedance 收 width/height 再換算

**MiniMax H3 不做像素換算。** 上游只收兩個列舉欄位（[文生視頻](https://doc.dmxapi.cn/MiniMax-H3-text-to-video.html)、[圖生視頻](https://doc.dmxapi.cn/MiniMax-H3-image-to-video.html)）：

- `resolution`（必填）：`768P` / `2K`，常數 `H3_RESOLUTIONS`。
- `ratio`（條件必填）：`21:9` / `16:9` / `4:3` / `1:1` / `3:4` / `9:16`，常數 `MINIMAX_RATIOS`。
  **文生影片必填且不可為 `adaptive`；圖生影片則恆為 `adaptive`**（比例跟隨輸入圖片，
  傳其他值不報錯但會被忽略）。因此 `build_h3_payload()` 只在 `input` 裡沒有任何
  `image_url` 時才送 `ratio`，帶參考圖時直接省略並記一筆 log。

節點因此開兩個下拉，**不收 `width` / `height`**。曾經有一版是收寬高再用
`ratio_from_size()` / `resolution_from_size()` 換算，結果是常見的 `1280x720`、`1920x1080`、
`1344x768` 全都換算成同一組 `16:9` + `768P`（2K 需要短邊 ≥ 1105），使用者改寬高卻拿到
一模一樣的 1344x768 影片。介面收「像素尺寸」卻無法決定像素尺寸，是誤導——不要改回去。

**Seedance 仍收 `width` / `height`**（它的檔位較密，且支援 `adaptive`），送出前由 `common` 換算並把結果寫進 log：

- `ratio_from_size(width, height, options)`：以**對數距離**挑最接近的比例，避免 `21:9` 這種極端值因數值大而被系統性偏袒。寬高為 `0` 且清單裡有 `adaptive` 時回傳 `adaptive`。
- `resolution_from_size(width, height, tiers, default)`：比**短邊**，取線性最近的檔位。

| 常數 | 內容 |
| --- | --- |
| `SEEDANCE_RESOLUTION_TIERS` | `480p`=480、`720p`=720、`1080p`=1080、`4k`=2160 |

`H3_RESOLUTION_TIERS` 與 `MiniMaxVideoBase.resolve_size()` 已隨上述改動移除。

`duration` 也對齊範本改成 **FLOAT 秒數**，送出前由 `duration_seconds()` 四捨五入成整數秒並夾在合法區間（超界會記 warning）。

### 影片節點的統一契約

所有 8 個影片節點都繼承 `common.DMXAPIVideoNodeBase`，並共享相同的輸出簽章，因此下游輸出接線可以互換：

```python
RETURN_NAMES = ("VIDEO", "IMAGE_FRAMES", "LAST_FRAME", "VIDEO_PATH", "VIDEO_URL", "TASK_ID")
```

第一槽的 `VIDEO` 與官方範本一致，可直接接內建 `SaveVideo` / `PreviewVideo`。它由 `common.to_video_output(path)` 以 `comfy_api` 的 `VideoFromFile` 包本地檔案產生；`comfy_api` 只有在 ComfyUI 進程內才 import 得到（冒煙測試是裸 Python 載入本套件），所以那裡是**延遲 import 且失敗回傳 None**，不要改成模組層級 import。

七個生成節點的共用輸入由 `common_inputs()` 產生：`download_video` / `max_frames` / `save_dir` / `poll_interval` / `max_wait`；長度由 `duration_input()` 產生，尺寸則分兩路：Seedance 走 `size_inputs()`（width / height），H3 自行宣告 `resolution` / `ratio` 下拉。生成節點收尾呼叫 `self.finish(...)`，由它決定是否落地成檔案。Seedance 下載節點自行宣告獨立輸入，但維持相同輸出簽章。

- `download_video=False` → 不下載影片，只回 URL 與 task_id；`VIDEO` 是 `None`，`IMAGE_FRAMES` 使用空白影格。若上游有 `last_frame_url`，`LAST_FRAME` 仍可使用該圖片，否則也為空白影格。
- `download_video=True` → 下載影片並建立 `VIDEO` 與預覽；只有 `max_frames != 0` 才會解碼 `IMAGE_FRAMES`。

`VIDEO` 需要本地檔案，所以 MiniMax H3 與 Seedance 生成節點的 `download_video` **一律預設 True**。相對地 `max_frames` 預設為 `0`——有 VIDEO 與內嵌預覽就不必把影格拉進記憶體，要後製再自行調高。

`LAST_FRAME` 的取得優先序：上游給的 `last_frame_url` > `max_frames != 0` 時已解碼影格的最後一幀 > 空白影格。`download_video=True` 只保證下載檔案；預設 `max_frames=0` 不會為了取得末幀而額外解碼。

### 下載節點

只有 Seedance 提供公開的事後取件節點 `DMXAPI_Seedance2_DownloadVideo`，可用 `task_id` 或 `video_url` 下載影片。用途是 Seedance 生成當下關掉了 `download_video`、ComfyUI 中途重啟，或想跨工作流取回舊任務；它是 `OUTPUT_NODE = True`。

只填 `video_url` 時不會發任何任務查詢請求，也不需要 api_key。

MiniMax H3 目前沒有公開的事後取件節點。`DMXAPI_MiniMax_Video` 仍輸出 `TASK_ID`，但該 ID 無法透過另一個 MiniMax ComfyUI 節點事後取回影片；需要本地檔案時應在生成節點保持 `download_video=True`。

### 內嵌影片預覽

`finish()` 與 Seedance 下載節點都會回傳 `{"ui": ..., "result": ...}`，`ui` 由 `common.build_video_preview(path)` 產生，格式與 ComfyUI 內建 `SaveVideo` 一致：

```python
{"images": [{"filename": ..., "subfolder": ..., "type": "output"}], "animated": (True,)}
```

前端看到 `animated` 就會渲染影片播放器，直接在節點上播放，不需要 VideoHelperSuite 之類的外掛。

兩個關鍵限制：

1. **檔案必須位於 output / input / temp 之下**。前端是走 `/view?filename=&subfolder=&type=` 取檔，該端點只認這三個根目錄，並且擋掉絕對路徑與 `..`。`save_dir` 指到別處時 `build_video_preview()` 會降級成純文字路徑並記一筆 warning，不會產生壞掉的播放器。下載目的地與預覽根目錄都取自 `folder_paths.get_output_directory()`，所以兩者永遠一致。
2. **節點不要為了預覽改成 `OUTPUT_NODE`**。ComfyUI 對任何節點回傳的 `ui` 都會轉發到前端（`execution.py` 只檢查 `len(output_ui) > 0`，不看節點類型），所以預覽不需要 `OUTPUT_NODE`。而 `OUTPUT_NODE` 會讓節點即使在畫布上沒接任何線也被當成執行根節點跑起來——對按次計費的 API 節點等於每次 queue 都可能無故扣費。

`download_video=False` 時沒有本地檔案，`ui` 改成顯示 `video_url` 文字。

### 記憶體：max_frames 三態語意

節點層的 `max_frames` 是三態，所有影片節點一致，由 `common.decode_frames()` 統一解讀：

| 值 | 意義 |
| --- | --- |
| `-1` | 全部解碼 |
| `0` | 完全不解碼，只留 VIDEO 輸出、影片預覽與檔案（最省記憶體，生成節點的預設） |
| `N > 0` | 最多解碼 N 幀 |

注意底層的 `common.video_to_frames(path, max_frames)` 用的是另一套慣例（`0` = 不限），節點層不要直接呼叫它，一律走 `decode_frames()`，否則 `0` 的意思會顛倒。

`float32` 的 4K 影格單張約 **100 MB**，15 秒 24fps 全解會超過 **30 GB**。有了內嵌預覽之後，多數情況根本不需要把影格拉進 `IMAGE`——高解析度請設 `0` 或明確上限。

### Tensor 與編碼慣例

ComfyUI 的 `IMAGE` 型別是 `float32` tensor，形狀 `[B, H, W, C]`，值域 `0.0~1.0`，RGB。

編碼統一走 `common.tensor_to_image_bytes(tensor, fmt=..., max_side=...)`（原始 bytes，multipart 用）或包它一層的 `common.tensor_to_data_url()`（data URI，JSON payload 用），兩者都**只取 batch 第一張**（`tensor[0]`），多圖 batch 會被丟掉。

格式的選擇是有依據的，不要隨手改：**所有送出去的輸入圖一律 `fmt="JPEG"`**——影片節點的首尾幀、圖像節點的參考圖都是。實測 720p 照片內容下 JPEG q95 的 base64 比 PNG **小 77%**（563 KB → 129 KB）；`edits` 的參考圖實測 1536x2752 的定妝照 PNG 5.3 MB → JPEG 且縮到長邊 2048 後 582 KB（**小 89%**）。合成漸層或雜訊圖會得出相反結論，若要重新評估請務必用真實照片測。

`max_side` 只有 `edits` 的參考圖在用（`DMXAPI_GPT_Image2.REFERENCE_MAX_SIDE = 2048`）。這不是省流量而已：**上傳時間算在上游那 60 秒回應上限裡**，原尺寸 PNG 光傳就吃掉大半，實測 66 秒與 92 秒兩次都被切斷。

### 影片解碼

`common.video_to_frames()` 降級順序 cv2 → imageio →（僅首幀）ffmpeg，全部失敗會**拋出明確錯誤**。不要改回靜默回傳黑畫面——那會讓「沒裝 cv2」偽裝成「模型產出全黑影片」，極難除錯。

### API Key 與認證

**認證形式不是固定的，不要改成寫死。** DMXAPI 官方範例對 `/v1/responses` 用**裸 key**（`Authorization: sk-xxx`），但 `/v1/images/generations` 是 OpenAI 相容介面、慣例用 `Bearer`。原始碼裡兩種形式都出現過，無從斷定哪個才對。

因此 `common._post_with_auth_fallback()` 兩種都試：先送該 URL 偏好的形式（`/v1/responses` 先裸 key，其餘先 Bearer），收到 401 才換另一種，成功的形式記進 `_auth_style_cache` 供後續請求沿用，所以最多只浪費一次往返。只有兩種形式都被拒才判定為 `DMXAPIAuthError`。

Key 的解析優先序：**節點輸入 > `DMXAPI_KEY` > 模組專屬環境變數**。

| 模組 | 專屬後援 |
| --- | --- |
| gpt_image2 | `OPENAI_API_KEY` |
| agnes | `AGNES_API_KEY` |
| minimax | `MINIMAX_API_KEY` |
| seedance | `SEEDANCE_API_KEY`、`ARK_API_KEY` |

### 錯誤處理策略

`common.post_json()` 統一處理：

- **401** → 先換另一種認證形式重試一次（見上節），仍被拒才拋 `DMXAPIAuthError`，**不進入退避重試**
- **429** → 直接拋 `DMXAPIAuthError`，**絕不重試**（額度問題重試沒有意義，只會浪費時間與配額）
- **5xx** → 指數退避（`2 ** attempt`）重試，預設 3 次
- **其他 4xx** → 直接拋 `RuntimeError`，不重試（請求本身有問題）
- **請求送出後才斷線** → 最多嘗試 2 次（`POST_SEND_MAX_ATTEMPTS`），判準見下

最後一條是實測換來的：上游閘道對同步請求有**約 60 秒的回應上限**，超時就直接關閉連線、
連 HTTP 狀態行都不回（`RemoteDisconnected`）。實測 `gpt-image-2`：

| size | 結果 |
| --- | --- |
| `3840x2160` | 六戰六敗，每次都在 63~65 秒被斷 |
| `2048x1152` | 第 1 次 65 秒被斷、第 2 次 58 秒成功 |

也就是說生成時間就卡在上限附近，**重試救得回來，不能一律放棄**；但對必死的大尺寸，
第 3 次只是再等 65 秒、再冒一次重複計費的風險（這種斷線代表請求已送達上游並在跑，
生成類請求多半已經計費）。因此折衷成最多 2 次。

`_already_delivered()` 以耗時當判準：`ConnectTimeout`（連線根本沒建立、不可能計費）
照常完整重試；其餘錯誤只要耗時 ≥ `POST_SEND_GUARD_SECONDS`（20 秒）就算已送達，套用
上面的 2 次上限。**改動這裡前先想清楚計費**——調高等於對按次計費的 API 重複扣費，
調成不重試則會丟掉像 `2048x1152` 那種本來會成功的情況。

同步端點沒有 task_id 可以事後取件，撞到上限就只能**改小尺寸**（圖像節點在 `size`
超過 `SLOW_SIZE_PIXELS` 時會先記一筆 warning）、縮小參考圖，或改指定明確
尺寸而不是 `size="auto"`（`auto` 的輸出尺寸跟著參考圖走，圖大就慢）。影片節點走非同步端點，提交後就有
task_id，不受這個上限影響。

輪詢迴圈另有容錯：連續 20 次查詢失敗才放棄，避免長時間生成途中因短暫網路抖動前功盡棄
（輪詢本身不計費，且 `poll_task` 傳 `retries=1`，上面的判準對它不改變行為）。

錯誤訊息一律加 `[DMXAPI ...]` 前綴——ComfyUI 會把例外訊息直接顯示在前端。

### 慣例

- 註解、日誌、節點顯示名稱一律**繁體中文**。
- 日誌走 `common.logger`（`logging.getLogger("DMXAPI")`），不要用 `print()`。
