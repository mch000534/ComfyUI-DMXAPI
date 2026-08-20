"""
DMXAPI ComfyUI 節點共用工具

集中三個節點模組原本各自實作的邏輯：端點常數、API Key 解析、HTTP 請求與重試、
非同步任務輪詢、tensor 與 base64 互轉、影片下載與抽幀。

節點模組不應自行組 headers、自行寫重試迴圈或自行做 base64 編碼，一律走這裡。
"""

import io
import os
import math
import re
import time
import base64
import logging
import tempfile
from datetime import datetime

import numpy as np
import requests
import torch
from PIL import Image

logger = logging.getLogger("DMXAPI")


# ==================== 可選 .env ====================

_DOTENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_dotenv_line(line):
    """解析一行簡單 .env 語法，回傳 (name, value) 或 None。"""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()

    if "=" not in stripped:
        return None

    name, value = stripped.split("=", 1)
    name = name.strip()
    if not _DOTENV_NAME.fullmatch(name):
        return None

    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    elif " #" in value or "\t#" in value:
        value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()

    return (name, value)


def _load_dotenv(path=None):
    """載入可選 .env；既有 process environment 變數優先。"""
    default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    dotenv_path = os.fspath(path or default_path)
    try:
        with open(dotenv_path, "r", encoding="utf-8") as handle:
            lines = list(handle)
    except FileNotFoundError:
        return 0
    except OSError as error:
        logger.warning("[DMXAPI] 無法讀取 .env（%s）：%s", dotenv_path, error)
        return 0

    loaded = 0
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        parsed = _parse_dotenv_line(line)
        if parsed is None:
            logger.warning("[DMXAPI] 忽略 .env 第 %s 行：格式無效。", line_number)
            continue

        name, value = parsed
        if name not in os.environ:
            os.environ[name] = value
            loaded += 1

    if loaded:
        logger.info("[DMXAPI] 已從 .env 載入 %s 個環境變數。", loaded)
    return loaded


_load_dotenv()


# ==================== 端點 ====================

BASE_HOST = "https://www.dmxapi.cn"
IMAGES_URL = BASE_HOST + "/v1/images/generations"
# 圖生圖走 OpenAI 相容的編輯端點：/v1/images/generations 是純文生圖，
# 送 image 欄位會被上游以 400 unknown_parameter 擋下（實測確認）。
# 這個端點只收 multipart/form-data，圖片是檔案而不是 base64 字串。
IMAGES_EDITS_URL = BASE_HOST + "/v1/images/edits"
RESPONSES_URL = BASE_HOST + "/v1/responses"


# ==================== 選項清單 ====================
# 各家上游的解析度字彙不同（MiniMax H3 用 768P/2K，Seedance 用 720p/4k），
# 不能混用，因此按家族分開定義，但排列一律由低到高。

MINIMAX_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]

# 解析度檔位：{上游名稱: 對應的短邊像素}。節點層收的是 width / height，
# 由 resolution_from_size() 以短邊挑最接近的檔位換算成這裡的名稱。
#
# MiniMax H3 只接受 768P/2K；送出前固定使用這個檔位表。
H3_RESOLUTION_TIERS = {"768P": 768, "2K": 1440}
SEEDANCE_RESOLUTION_TIERS = {"480p": 480, "720p": 720, "1080p": 1080, "4k": 2160}

SEEDANCE_RATIOS = ["adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]


# ==================== 尺寸與長度換算 ====================
# 節點的輸入介面對齊 ComfyUI 官方範本（width / height / duration），但這些
# API 都只收固定的 ratio 字串與解析度檔位，不吃任意像素尺寸，因此在送出前
# 由下面三個函式換算。換算結果一律寫進 log，方便對照上游實際收到什麼。

def _ratio_value(name):
    left, right = name.split(":")
    return float(left) / float(right)


def ratio_from_size(width, height, options):
    """width / height → 最接近的長寬比字串。

    以對數距離比較，避免 21:9 這種極端比例因為數值大而被系統性偏袒。
    options 內的非比例值（Seedance 的 "adaptive"）只在寬高留空（0）時選用，
    代表「跟隨參考圖」。
    """
    numeric = [name for name in options if ":" in name]

    if not width or not height:
        return "adaptive" if "adaptive" in options else numeric[0]

    target = float(width) / float(height)
    return min(numeric, key=lambda name: abs(math.log(_ratio_value(name) / target)))


def resolution_from_size(width, height, tiers, default=None):
    """width / height → 最接近的解析度檔位名稱（比較短邊，取線性最近）。

    寬高留空時回傳 default，供 Seedance 的 adaptive 模式使用。
    """
    sides = [side for side in (width, height) if side and side > 0]
    if not sides:
        return default or next(iter(tiers))

    short = min(sides)
    return min(tiers, key=lambda name: abs(tiers[name] - short))


def duration_seconds(duration, minimum=4, maximum=15):
    """範本的 duration 是 FLOAT 秒數，但這些 API 只收整數秒，四捨五入後夾在合法區間。"""
    seconds = int(round(float(duration)))
    clamped = max(minimum, min(maximum, seconds))
    if clamped != seconds:
        logger.warning(
            "[DMXAPI] duration %.1f 秒超出允許範圍 %s~%s，已調整為 %s 秒。",
            float(duration), minimum, maximum, clamped,
        )
    return clamped


# ==================== 例外 ====================

class DMXAPIAuthError(RuntimeError):
    """認證或額度問題（401 / 429）。這類錯誤重試無意義，一律直接向上拋。"""


# ==================== API Key ====================

def resolve_api_key(api_key, *fallback_env):
    """解析 API Key，優先序：節點輸入 > DMXAPI_KEY > 模組專屬環境變數。"""
    key = (api_key or "").strip()
    if key:
        return key

    candidates = ("DMXAPI_KEY",) + fallback_env
    for name in candidates:
        value = os.environ.get(name, "").strip()
        if value:
            logger.info("[DMXAPI] 使用環境變數 %s 的 API Key。", name)
            return value

    raise DMXAPIAuthError(
        "[DMXAPI Error] 未提供 API Key！請在節點填入 api_key，"
        "或設定環境變數 " + " / ".join(candidates) + "。"
    )


def build_headers(token, style="bearer", content_type="application/json"):
    """組請求標頭。style 為 "bearer"（OpenAI 慣例）或 "raw"（裸 key）。

    content_type 傳 None 代表交給 requests 自己填——multipart 必須這樣，
    否則手寫的 Content-Type 少了 boundary，上游會解不出表單欄位。
    """
    headers = {"Authorization": ("Bearer " + token) if style == "bearer" else token}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


# DMXAPI 官方範例對 /v1/responses 用裸 key，但 /v1/images/generations 是
# OpenAI 相容介面、慣例用 Bearer。實測前無法斷定，因此兩種都試：
# 先送偏好的形式，收到 401 才換另一種，成功的形式記進快取供後續沿用。
_AUTH_STYLES = ("bearer", "raw")
_auth_style_cache = {}


def _auth_order(url):
    known = _auth_style_cache.get(url)
    if known:
        return (known,) + tuple(s for s in _AUTH_STYLES if s != known)
    if url == RESPONSES_URL:
        return ("raw", "bearer")
    return ("bearer", "raw")


def _post_with_auth_fallback(sender, url, token, timeout, request_kwargs):
    """送出請求；401 時自動改用另一種 Authorization 形式重試一次。

    request_kwargs 直接餵給 requests.post，因此 JSON 與 multipart 共用同一套
    認證探測與快取；帶 files 時不自行設 Content-Type（見 build_headers）。
    """
    content_type = None if "files" in request_kwargs else "application/json"
    response = None
    for style in _auth_order(url):
        response = sender.post(
            url,
            headers=build_headers(token, style, content_type),
            timeout=timeout,
            **request_kwargs
        )
        if response.status_code != 401:
            if _auth_style_cache.get(url) != style:
                logger.info("[DMXAPI] %s 採用 %s 認證形式。", url, style)
                _auth_style_cache[url] = style
            return response
        logger.debug("[DMXAPI] %s 認證形式被拒，改試下一種。", style)

    return response  # 兩種形式都 401，交由呼叫端判定為認證失敗


# ==================== HTTP ====================

DEFAULT_TIMEOUT = 90
DEFAULT_RETRIES = 3

# 請求送出「之後」才斷線（上游閘道約 60 秒的回應上限，會直接關閉連線、連 HTTP
# 狀態行都不回），代表上游已經收下請求並在跑，生成類請求多半也已經計費。
# 但這種失敗不能一律放棄：實測 2048x1152 第 1 次在 65 秒被斷、第 2 次 58 秒就成功，
# 生成時間剛好卡在上限附近，重試救得回來。真正沒救的是 3840x2160 那種必死局
# （六戰六敗），第 3 次只是再等 65 秒、再冒一次重複計費的風險。
# 折衷：這類失敗最多嘗試 2 次。連線建立前就失敗（DNS、拒絕連線、連線逾時）不可能
# 計費，不受此限，照 retries 完整重試；那類失敗必定很快發生，所以用耗時當判準。
POST_SEND_GUARD_SECONDS = 20
POST_SEND_MAX_ATTEMPTS = 2


def _already_delivered(error, elapsed):
    """判斷失敗當下請求是否已經送達上游（送達後重試可能重複計費）。"""
    if isinstance(error, requests.exceptions.ConnectTimeout):
        return False  # 連線根本沒建立起來，重試無害
    return elapsed >= POST_SEND_GUARD_SECONDS


def post_json(url, payload, token, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES, session=None):
    """POST JSON 並套用統一重試策略。

    401 / 429 立即拋出 DMXAPIAuthError；5xx 與「連上之前就失敗」的網路錯誤以
    指數退避重試；請求送出後才斷線則不重試（見 POST_SEND_GUARD_SECONDS）。
    """
    return _post(url, {"json": payload}, token, timeout, retries, session)


def post_multipart(url, fields, files, token, timeout=DEFAULT_TIMEOUT,
                   retries=DEFAULT_RETRIES, session=None):
    """POST multipart/form-data（/v1/images/edits 這類收檔案的端點）。

    重試策略與認證探測完全與 post_json 相同。files 的值一律用 bytes 而非
    已開啟的檔案物件——重試時 requests 會重新編碼表單，串流讀過就沒了。
    """
    return _post(url, {"data": fields, "files": files}, token, timeout, retries, session)


def _post(url, request_kwargs, token, timeout, retries, session):
    """post_json 與 post_multipart 共用的重試迴圈。"""
    sender = session or requests
    last_error = None
    delivered_failures = 0

    for attempt in range(1, retries + 1):
        started = time.time()
        try:
            response = _post_with_auth_fallback(sender, url, token, timeout, request_kwargs)

            status = response.status_code

            if status == 401:
                raise DMXAPIAuthError(
                    "[DMXAPI 401] 身份驗證失敗：API Key 無效或未授權"
                    "（Bearer 與裸 key 兩種認證形式皆被拒）。"
                )
            if status == 429:
                raise DMXAPIAuthError("[DMXAPI 429] 請求頻率超限或帳戶餘額不足。")
            if status >= 500:
                # 伺服器端問題，值得重試；明確拋出 HTTPError 交給下方 except 處理
                raise requests.exceptions.HTTPError(
                    "[DMXAPI " + str(status) + "] 伺服器錯誤：" + response.text[:300]
                )
            if not 200 <= status < 300:
                # 其他 4xx 屬請求本身有問題，重試無意義
                raise RuntimeError(
                    "[DMXAPI " + str(status) + "] 請求失敗：" + response.text[:500]
                )

            return response.json()

        except DMXAPIAuthError:
            raise
        except requests.exceptions.RequestException as e:
            elapsed = time.time() - started
            last_error = e
            logger.warning(
                "[DMXAPI] 請求失敗（第 %s/%s 次，耗時 %.0f 秒）：%s", attempt, retries, elapsed, e
            )

            if _already_delivered(e, elapsed):
                delivered_failures += 1
                logger.warning(
                    "[DMXAPI] 連線是在請求送出 %.0f 秒後才被上游切斷（閘道約 60 秒的回應上限），"
                    "這筆請求可能已經在上游生成並計費。", elapsed,
                )
                if delivered_failures >= POST_SEND_MAX_ATTEMPTS:
                    raise RuntimeError(
                        "[DMXAPI Error] 連線連續 " + str(delivered_failures)
                        + " 次在請求送出後被上游中斷（最後一次等了 {:.0f} 秒）".format(elapsed)
                        + "，未收到任何回應。這是上游閘道約 60 秒的回應上限：同步生成太慢"
                        + "就會被切斷，客戶端無法繞過。請改用較小的尺寸（例如 2048x1152、"
                        + "1024x1024）或較短的影片長度。注意這幾次請求可能已經計費。"
                        + "原始錯誤：" + str(e)
                    ) from e

        if attempt < retries:
            time.sleep(2 ** attempt)

    raise RuntimeError(
        "[DMXAPI Error] 請求失敗，已達最大重試次數 " + str(retries) + "：" + str(last_error)
    )


# ==================== 非同步任務輪詢 ====================

def poll_task(payload, parse, token, label="任務", poll_interval=8, max_wait=900,
              request_timeout=60, max_consecutive_errors=20, url=RESPONSES_URL):
    """統一的輪詢迴圈。

    H3 與 Seedance 的狀態欄位和回應結構不同（H3 用 succeeded，
    Seedance 還多包一層 JSON 字串），因此把解析交給 parse 回呼，這裡只負責
    計時、間隔、容錯與錯誤訊息格式。

    parse(data) 需回傳 (state, value)：
      "done"    -> value 是結果
      "pending" -> 繼續等（value 忽略）
      "failed"  -> value 是錯誤訊息
    """
    start = time.time()
    consecutive_errors = 0

    while True:
        elapsed = time.time() - start
        if elapsed > max_wait:
            raise TimeoutError(
                "[DMXAPI Timeout] " + label + " 輪詢超過 " + str(max_wait) + " 秒仍未完成。"
            )

        try:
            # 單次查詢不重試，交由外層迴圈的容錯計數處理
            data = post_json(url, payload, token, timeout=request_timeout, retries=1)
            consecutive_errors = 0
        except DMXAPIAuthError:
            raise
        except Exception as e:
            consecutive_errors += 1
            logger.warning(
                "[DMXAPI] %s 查詢失敗（連續 %s/%s 次）：%s",
                label, consecutive_errors, max_consecutive_errors, e,
            )
            if consecutive_errors >= max_consecutive_errors:
                raise RuntimeError(
                    "[DMXAPI Error] " + label + " 連續 "
                    + str(consecutive_errors) + " 次查詢失敗：" + str(e)
                )
            time.sleep(poll_interval)
            continue

        state, value = parse(data)
        logger.info("[DMXAPI] %s 狀態=%s（已等待 %.0f 秒）", label, state, elapsed)

        if state == "done":
            return value
        if state == "failed":
            raise RuntimeError("[DMXAPI Error] " + label + " 生成失敗：" + str(value))

        time.sleep(poll_interval)


# ==================== Tensor 與影像 ====================
# ComfyUI 的 IMAGE 型別：float32 tensor，形狀 [B, H, W, C]，值域 0.0~1.0，RGB。

def tensor_to_image_bytes(tensor, fmt="PNG", quality=95, max_side=0):
    """IMAGE tensor → (bytes, mime)（只取 batch 第一張）。

    multipart 端點要的是原始 bytes，data URI 端點再由 tensor_to_data_url 包一層。

    max_side > 0 時先等比縮到長邊不超過該值。上傳量直接吃掉閘道約 60 秒回應上限
    的一部分（實測 5 MB 的參考圖必被切斷），而參考圖的細節本來就不需要原尺寸。
    """
    if tensor is None:
        return None, None

    array = np.clip(tensor[0].cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
    image = Image.fromarray(array)
    if image.mode != "RGB":
        image = image.convert("RGB")

    if max_side and max(image.size) > max_side:
        scale = max_side / float(max(image.size))
        target = (max(1, int(round(image.width * scale))),
                  max(1, int(round(image.height * scale))))
        logger.info(
            "[DMXAPI] 參考圖 %sx%s 超過長邊上限 %s，縮到 %sx%s 再上傳。",
            image.width, image.height, max_side, target[0], target[1],
        )
        image = image.resize(target, Image.LANCZOS)

    buffer = io.BytesIO()
    if fmt.upper() == "JPEG":
        image.save(buffer, format="JPEG", quality=quality)
        mime = "image/jpeg"
    else:
        image.save(buffer, format="PNG")
        mime = "image/png"

    return buffer.getvalue(), mime


def tensor_to_data_url(tensor, fmt="PNG", quality=95):
    """IMAGE tensor → data URI（只取 batch 第一張）。

    影片節點傳首尾幀時用 fmt="JPEG"：base64 PNG 的 1080p 影格可達數 MB，
    容易撞上請求體積上限。
    """
    raw, mime = tensor_to_image_bytes(tensor, fmt=fmt, quality=quality)
    if raw is None:
        return None

    return "data:" + mime + ";base64," + base64.b64encode(raw).decode("utf-8")


def bytes_to_tensor(image_bytes):
    """圖片 bytes → IMAGE tensor [1, H, W, C]。"""
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    array = np.array(image).astype(np.float32) / 255.0
    return torch.from_numpy(array)[None, ...]


def pil_to_tensor_batch(pil_images):
    """多張 PIL → IMAGE tensor [B, H, W, C]。"""
    tensors = []
    for image in pil_images:
        if image.mode != "RGB":
            image = image.convert("RGB")
        tensors.append(torch.from_numpy(np.array(image).astype(np.float32) / 255.0))

    if not tensors:
        raise RuntimeError("[DMXAPI Error] 未能成功解析任何回傳影像。")

    return torch.stack(tensors, dim=0)


def empty_frame(size=64):
    """佔位用的空白影格，供「不下載影片」時填充 IMAGE 輸出槽。"""
    return torch.zeros((1, size, size, 3), dtype=torch.float32)


def fetch_image_item(item, session=None):
    """解析 /v1/images/generations 回傳項目中的 b64_json 或 url。"""
    getter = session or requests

    if item.get("b64_json"):
        return Image.open(io.BytesIO(base64.b64decode(item["b64_json"])))

    if item.get("url"):
        response = getter.get(item["url"], timeout=60)
        response.raise_for_status()
        return Image.open(io.BytesIO(response.content))

    raise RuntimeError("[DMXAPI Error] 無法識別的影像資料格式：" + str(item))


def download_image_tensor(url):
    """下載單張圖片為 IMAGE tensor；失敗回傳 None（供末幀預覽這類非關鍵路徑使用）。"""
    if not url:
        return None
    try:
        response = requests.get(url, timeout=(30, 60))
        response.raise_for_status()
        return bytes_to_tensor(response.content)
    except Exception as e:
        logger.warning("[DMXAPI] 下載影格失敗：%s", e)
        return None


# ==================== 影片 ====================

def get_output_dir():
    """取得 ComfyUI 的 output 目錄。"""
    try:
        import folder_paths
        return folder_paths.get_output_directory()
    except Exception:
        pass

    for candidate in [
        os.path.join(os.getcwd(), "output"),
        os.path.join(os.getcwd(), "ComfyUI", "output"),
    ]:
        if os.path.isdir(candidate):
            return candidate

    fallback = os.path.abspath("output")
    os.makedirs(fallback, exist_ok=True)
    return fallback


def _locate_under_comfy_dir(path):
    """回傳 (type, subfolder, filename)；檔案不在可服務目錄下則回傳 None。"""
    roots = []
    try:
        import folder_paths
        roots = [
            ("output", folder_paths.get_output_directory()),
            ("input", folder_paths.get_input_directory()),
            ("temp", folder_paths.get_temp_directory()),
        ]
    except Exception:
        roots = [("output", get_output_dir())]

    target = os.path.abspath(path)
    for type_name, root in roots:
        if not root:
            continue
        root = os.path.abspath(root)
        if target == root or target.startswith(root + os.sep):
            relative = os.path.relpath(target, root)
            return (type_name, os.path.dirname(relative), os.path.basename(relative))

    return None


def build_video_preview(path):
    """組出讓 ComfyUI 前端內嵌播放器播放影片的 ui 字典。

    前端是透過 /view?filename=&subfolder=&type= 取檔，所以影片必須位在
    output / input / temp 之下；存到別處就只能顯示路徑文字。
    這個格式與內建 SaveVideo 節點一致（images + animated）。
    """
    located = _locate_under_comfy_dir(path)
    if located is None:
        logger.warning(
            "[DMXAPI] %s 不在 ComfyUI 的 output/input/temp 目錄下，前端無法播放。"
            "把 save_dir 留空即可存進 output。", path,
        )
        return {"text": [path]}

    type_name, subfolder, filename = located
    return {
        "images": [{"filename": filename, "subfolder": subfolder, "type": type_name}],
        "animated": (True,),
    }


def to_video_output(path):
    """本地影片檔 → ComfyUI 的 VIDEO 物件，可直接接 SaveVideo / PreviewVideo。

    comfy_api 只有在 ComfyUI 進程內才 import 得到（冒煙測試是裸 Python 載入本套件，
    沒有 ComfyUI 在 sys.path 上），因此延遲 import，取不到就回傳 None 讓其餘輸出照常。
    """
    if not path:
        return None

    try:
        from comfy_api.input_impl import VideoFromFile
    except ImportError:
        try:
            from comfy_api.latest._input_impl import VideoFromFile
        except ImportError:
            logger.warning("[DMXAPI] 無法 import comfy_api，VIDEO 輸出留空，請改用 VIDEO_PATH。")
            return None

    return VideoFromFile(path)


def download_video_file(video_url, save_dir=None, filename_prefix="dmxapi"):
    """下載影片到本地。save_dir 留空時存到 ComfyUI 的 output 目錄。回傳檔案路徑。"""
    target_dir = (save_dir or "").strip() or get_output_dir()
    os.makedirs(target_dir, exist_ok=True)

    extension = ".mp4"
    stripped = video_url.lower().split("?")[0]
    for candidate in [".mp4", ".mov", ".webm", ".mkv"]:
        if stripped.endswith(candidate):
            extension = candidate
            break

    filename = filename_prefix + "_" + datetime.now().strftime("%Y%m%d_%H%M%S") + extension
    filepath = os.path.join(target_dir, filename)

    logger.info("[DMXAPI] 開始下載影片 → %s", filepath)
    with requests.get(video_url, stream=True, timeout=(30, 300)) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        downloaded = 0
        with open(filepath, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                if total > 0 and downloaded % (1024 * 1024 * 2) < 1024 * 256:
                    logger.info("[DMXAPI] 下載進度 %.1f%%", downloaded * 100 / total)

    logger.info(
        "[DMXAPI] 下載完成：%s（%.2f MB）", filepath, os.path.getsize(filepath) / (1024 * 1024)
    )
    return filepath


def download_video_to_temp(video_url):
    """下載影片到系統暫存目錄，供只需抽幀、不需保留檔案的情境使用。"""
    with requests.get(video_url, stream=True, timeout=(30, 300)) as response:
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    handle.write(chunk)
            return handle.name


def _decode_with_cv2(path, max_frames):
    try:
        import cv2
    except ImportError:
        return []

    frames = []
    capture = cv2.VideoCapture(path)
    try:
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frames.append(torch.from_numpy(rgb.astype(np.float32) / 255.0))
            if max_frames and len(frames) >= max_frames:
                break
    except Exception as e:
        logger.warning("[DMXAPI] OpenCV 解碼失敗：%s", e)
    finally:
        capture.release()

    return frames


def _decode_with_imageio(path, max_frames):
    try:
        import imageio.v2 as imageio
    except ImportError:
        return []

    frames = []
    try:
        reader = imageio.get_reader(path)
        for frame in reader:
            array = np.asarray(frame).astype(np.float32) / 255.0
            if array.ndim == 2:
                array = np.stack([array] * 3, axis=-1)
            if array.shape[-1] > 3:
                array = array[..., :3]
            frames.append(torch.from_numpy(array))
            if max_frames and len(frames) >= max_frames:
                break
        reader.close()
    except Exception as e:
        logger.warning("[DMXAPI] imageio 解碼失敗：%s", e)

    return frames


def _decode_first_frame_with_ffmpeg(path):
    import subprocess

    handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    handle.close()
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-vframes", "1", "-q:v", "2", handle.name],
            check=True, capture_output=True, timeout=60,
        )
        with open(handle.name, "rb") as f:
            return [bytes_to_tensor(f.read())[0]]
    except Exception as e:
        logger.warning("[DMXAPI] ffmpeg 抽幀失敗：%s", e)
        return []
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def video_to_frames(path, max_frames=0):
    """影片 → IMAGE tensor [F, H, W, C]，降級順序 cv2 → imageio →（僅首幀）ffmpeg。

    max_frames=0 表示不限。務必留意記憶體：float32 的 4K 影格單張約 100 MB，
    15 秒 24fps 全解會超過 30 GB，高解析度請務必設上限。

    全部後端都失敗時直接拋錯，不再靜默回傳黑畫面。
    """
    frames = _decode_with_cv2(path, max_frames)
    if not frames:
        frames = _decode_with_imageio(path, max_frames)
    if not frames and max_frames == 1:
        frames = _decode_first_frame_with_ffmpeg(path)

    if not frames:
        raise RuntimeError(
            "[DMXAPI Error] 無法解碼影片 " + str(path)
            + "：請確認已安裝 opencv-python 或 imageio，且檔案未損毀。"
        )

    return torch.stack(frames, dim=0)


def video_first_frame(path):
    """抽取影片首幀為 IMAGE tensor [1, H, W, C]。"""
    return video_to_frames(path, max_frames=1)


# 節點層 max_frames 的三態語意（所有影片節點一致）：
#   -1 = 全部解碼    0 = 完全不解碼    N > 0 = 最多 N 幀
# 有了內嵌影片預覽之後，多數情況其實不需要把影格拉進 IMAGE，0 最省記憶體。
MAX_FRAMES_ALL = -1
MAX_FRAMES_NONE = 0


def decode_frames(path, max_frames):
    """依節點層語意解碼影格；max_frames 為 0 時回傳 None，由呼叫端填空白影格。"""
    if max_frames == MAX_FRAMES_NONE:
        return None
    return video_to_frames(path, 0 if max_frames < 0 else max_frames)


# ==================== 影片節點基底 ====================

class DMXAPIVideoNodeBase:
    """所有影片節點的共同契約。

    統一輸出簽章，讓不同家族的節點在畫布上可以互換接線；第一槽的 VIDEO 與
    ComfyUI 官方範本一致，可直接接 SaveVideo / PreviewVideo。
    是否落地成檔案由 download_video 開關決定，關閉時只回傳 URL 與 task_id。
    """

    RETURN_TYPES = ("VIDEO", "IMAGE", "IMAGE", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("VIDEO", "IMAGE_FRAMES", "LAST_FRAME", "VIDEO_PATH", "VIDEO_URL", "TASK_ID")
    CATEGORY = "DMXAPI/Video"

    @staticmethod
    def size_inputs(width=1344, height=768):
        """對齊官方範本的 width / height 輸入。

        這些 API 不吃任意像素尺寸，寬高只用來換算成上游要的 ratio 與解析度檔位
        （見 ratio_from_size / resolution_from_size），所以接 GetImageSize 或
        ResolutionSelector 都可以，實際輸出仍是上游檔位的尺寸。
        """
        return {
            "width": ("INT", {
                "default": width, "min": 0, "max": 8192, "step": 16,
                "tooltip": "只用來換算長寬比與解析度檔位，不是實際輸出像素；0 = 跟隨參考圖（僅 Seedance）",
            }),
            "height": ("INT", {
                "default": height, "min": 0, "max": 8192, "step": 16,
                "tooltip": "只用來換算長寬比與解析度檔位，不是實際輸出像素；0 = 跟隨參考圖（僅 Seedance）",
            }),
        }

    @staticmethod
    def duration_input(default=5.0, minimum=4.0, maximum=15.0):
        """對齊範本的 FLOAT 秒數；送出前由 duration_seconds() 四捨五入成整數秒。"""
        return {
            "duration": ("FLOAT", {
                "default": default, "min": minimum, "max": maximum, "step": 0.5,
                "tooltip": "影片長度（秒）。上游只收整數秒，會四捨五入。",
            }),
        }

    @staticmethod
    def common_inputs(download_default=True, max_frames_default=MAX_FRAMES_NONE):
        """所有影片節點共用的輸入欄位。

        VIDEO 輸出需要本地檔案，因此 download_video 一律預設開啟；
        影格則預設不解碼——VIDEO 與內嵌預覽已能看片，要做後製再自行調 max_frames。
        """
        return {
            "download_video": ("BOOLEAN", {
                "default": download_default,
                "tooltip": "關閉時只回傳 VIDEO_URL 與 TASK_ID，VIDEO 與 VIDEO_PATH 會是空的",
            }),
            "max_frames": ("INT", {
                "default": max_frames_default, "min": -1, "max": 4096,
                "tooltip": "-1 = 全部解碼；0 = 不解碼（只留 VIDEO 與影片預覽，最省記憶體）；N = 最多 N 幀",
            }),
            "save_dir": ("STRING", {"default": "", "placeholder": "留空 = ComfyUI output"}),
            "poll_interval": ("INT", {"default": 8, "min": 3, "max": 30}),
            "max_wait": ("INT", {"default": 900, "min": 60, "max": 3600}),
        }

    def finish(self, video_url, task_id, download_video, max_frames, save_dir,
               prefix="dmxapi", last_frame_url=None):
        """把影片 URL 收斂成統一的六元組輸出。

        LAST_FRAME 的取得優先序：上游直接給的 last_frame_url > 從下載的影片取末幀 >
        空白影格。末幀常用來串接下一段影片，因此列為統一契約的一部分。
        """
        last_frame = download_image_tensor(last_frame_url)

        if not download_video:
            # VIDEO 物件需要本地檔案，沒下載就只能給空的，也無法內嵌播放
            logger.warning(
                "[DMXAPI] download_video=False，VIDEO 輸出為空。"
                "要接 SaveVideo 請打開 download_video，或事後用下載節點取件。"
            )
            return {
                "ui": {"text": [video_url]},
                "result": (None,
                           empty_frame(),
                           last_frame if last_frame is not None else empty_frame(),
                           "", video_url, task_id),
            }

        path = download_video_file(video_url, save_dir, prefix)
        frames = decode_frames(path, max_frames)
        if frames is None:
            frames = empty_frame()
        elif last_frame is None:
            last_frame = frames[-1:]
        if last_frame is None:
            last_frame = empty_frame()

        return {
            "ui": build_video_preview(path),
            "result": (to_video_output(path), frames, last_frame, path, video_url, task_id),
        }
