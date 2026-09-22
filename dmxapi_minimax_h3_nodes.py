"""DMXAPI MiniMax H3 影片生成節點。

使用 POST /v1/responses 兩段式協定：以 ``MiniMax-H3`` 提交任務，
再以 ``MiniMax-H3-get`` 輪詢，直到 ``task.content.url`` 提供影片網址。

官方文件：
  文生視頻   https://doc.dmxapi.cn/MiniMax-H3-text-to-video.html
  圖生視頻   https://doc.dmxapi.cn/MiniMax-H3-image-to-video.html
  多模態參考 https://doc.dmxapi.cn/MiniMax-H3-multimodal-reference-to-video.html
"""

import base64

from .dmxapi_common import (
    H3_REFERENCE_RATIOS,
    H3_RESOLUTIONS,
    MINIMAX_RATIOS,
    RESPONSES_URL,
    DMXAPIVideoNodeBase,
    duration_seconds,
    logger,
    poll_task,
    post_json,
    resolve_api_key,
    tensor_to_data_url,
    tensor_to_image_bytes,
)

MINIMAX_MODEL = "MiniMax-H3"


# ==================== 狀態解析 ====================

def _parse_h3(data):
    task = data.get("task", {})
    status = task.get("status", "")

    if status == "succeeded":
        url = task.get("content", {}).get("url")
        if not url:
            return ("failed", "狀態為 succeeded 但缺少 url：" + str(data))
        return ("done", url)

    if status in ("failed", "cancelled"):
        return ("failed", task.get("error", {}).get("message", "未知錯誤"))

    return ("pending", status)


# ==================== 共用基底 ====================

class MiniMaxVideoBase(DMXAPIVideoNodeBase):
    """MiniMax H3 的提交與輪詢流程。"""

    def validate_model(self, model):
        if model != MINIMAX_MODEL:
            raise ValueError(
                "[DMXAPI Error] MiniMax 目前只支援 MiniMax-H3，"
                "請更新舊 workflow 的 model。"
            )

    def resolve_key(self, api_key):
        return resolve_api_key(api_key, "MINIMAX_API_KEY")

    def encode_image(self, tensor):
        # 影片首尾幀用 JPEG，避免 base64 PNG 撐爆請求體積
        return tensor_to_data_url(tensor, fmt="JPEG", quality=95)

    def submit(self, payload, token, label):
        data = post_json(RESPONSES_URL, payload, token, timeout=60)
        task_id = str(data.get("task_id") or data.get("id") or "")
        if not task_id:
            raise RuntimeError("[DMXAPI Error] " + label + " 提交失敗，未取得 task_id：" + str(data))
        logger.info("[DMXAPI] %s 已提交 task_id=%s", label, task_id)
        return task_id

    def wait_for_url(self, task_id, token, poll_interval, max_wait):
        """輪詢 H3 task_id 直到取得影片網址。"""
        return poll_task(
            {"model": "MiniMax-H3-get", "input": task_id},
            _parse_h3, token, label="H3",
            poll_interval=poll_interval, max_wait=max_wait,
        )

    def run_task(self, payload, token, poll_interval, max_wait):
        """提交並等待完成，回傳 (video_url, task_id)。"""
        task_id = self.submit(payload, token, "H3")
        video_url = self.wait_for_url(task_id, token, poll_interval, max_wait)
        return (video_url, task_id)

    def build_h3_payload(self, prompt, ratio, resolution, duration, prompt_optimizer,
                         noise_seed=0, images=(), extra_items=(), send_ratio=None):
        """組出 /v1/responses 的提交 payload。

        ``images`` 為 (role, tensor) 序列，例如 ("first_frame", t)，會就地編碼成
        data URI；``extra_items`` 則是呼叫端已組好的 input 項目（多模態參考
        節點的圖／影／音 URL 走這條）。

        ``send_ratio`` 三態，因為 ratio 的規則按情境而異：

        * ``None``（預設）＝首尾幀情境。只有純文字時才送 ratio——一旦帶了影格，
          上游比例恆為 ``adaptive``（跟隨圖片），送任何值都會被忽略。
        * ``True`` ＝多模態參考情境。上游文件明文接受 ratio（含 ``adaptive``），
          必須照送，否則使用者選的比例會無聲失效。
        """
        items = [{"type": "text", "text": prompt}]
        for role, tensor in images:
            if tensor is None:
                continue
            items.append({
                "type": "image_url",
                "role": role,
                "image_url": {"url": self.encode_image(tensor)},
            })
        items.extend(extra_items)

        payload = {
            "model": MINIMAX_MODEL,
            "input": items,
            "resolution": resolution,
            "duration": duration_seconds(duration),
            "prompt_optimizer": prompt_optimizer,
            "aigc_watermark": False,
        }

        if send_ratio is None:
            send_ratio = len(items) == 1

        if send_ratio:
            payload["ratio"] = ratio
            logger.info(
                "[DMXAPI] %s resolution=%s ratio=%s",
                MINIMAX_MODEL, resolution, ratio,
            )
        else:
            logger.info(
                "[DMXAPI] %s resolution=%s（帶參考圖，比例跟隨圖片，不送 ratio）",
                MINIMAX_MODEL, resolution,
            )
        if noise_seed and noise_seed > 0:
            payload["seed"] = int(noise_seed)
        return payload

# ==================== 節點 ====================

_FRAME_RATIO_TIP = "畫面比例。只在文生影片時生效；帶 first_frame / last_frame 時比例跟隨圖片"


def _minimax_inputs(ratios=MINIMAX_RATIOS, ratio_tooltip=_FRAME_RATIO_TIP):
    """組出 H3 的輸入：prompt / resolution / ratio / duration / noise_seed。

    上游只收 ``resolution`` 與 ``ratio`` 兩個列舉欄位，不接受任意像素尺寸，
    因此這裡**不提供 width / height**——收像素尺寸只會讓人誤以為能指定輸出解析度。
    範本的 unet/clip/vae 是本地推論用的，API 版換成 model 與 api_key。

    ``ratios`` 依情境不同：首尾幀節點用 MINIMAX_RATIOS，多模態參考節點另外
    多一個 ``adaptive``（見 H3_REFERENCE_RATIOS）。
    """
    required = {
        "prompt": ("STRING", {"multiline": True, "default": ""}),
        "resolution": (H3_RESOLUTIONS, {
            "default": H3_RESOLUTIONS[0],
            "tooltip": "上游解析度檔位；2K 較貴也較慢",
        }),
        "ratio": (ratios, {
            "default": ratios[0],
            "tooltip": ratio_tooltip,
        }),
    }
    required.update(DMXAPIVideoNodeBase.duration_input())
    required.update({
        "noise_seed": ("INT", {
            "default": 0, "min": 0, "max": 0xffffffffffffffff,
            "tooltip": "實測 H3 不保證可重現：同一組 prompt 與 seed 仍會得到不同影片。"
                       "此欄位的實際用途是當成 ComfyUI 的快取鍵，改值才會重跑節點",
        }),
        "model": ([MINIMAX_MODEL], {"default": MINIMAX_MODEL}),
        "api_key": ("STRING", {"default": "", "multiline": False}),
        "prompt_optimizer": ("BOOLEAN", {
            "default": True,
            "tooltip": "開啟時上游會先改寫、擴寫 prompt 再生成（短 prompt 效果較好）；"
                       "關閉則嚴格照原文，適合已寫細的長 prompt 或要求結果可重現",
        }),
    })
    required.update(DMXAPIVideoNodeBase.common_inputs(download_default=True))
    return required


class DMXAPI_MiniMax_Video(MiniMaxVideoBase):
    """H3 整合節點：支援文生、首幀、尾幀與首尾幀生成。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": _minimax_inputs(),
            "optional": {
                "first_frame": ("IMAGE",),
                "last_frame": ("IMAGE",),
            },
        }

    FUNCTION = "generate"

    def generate(self, prompt, resolution, ratio, duration, noise_seed, model, api_key,
                 prompt_optimizer, download_video, max_frames, save_dir, poll_interval, max_wait,
                 first_frame=None, last_frame=None):
        self.validate_model(model)
        # 上游支援四種組合：純文字、first_frame、last_frame、first_frame + last_frame。
        # 只接 last_frame 是合法的「尾幀生成」，不要再擋。
        if first_frame is None and last_frame is None and not prompt.strip():
            raise ValueError("[DMXAPI Error] 文生影片模式下 Prompt 不能為空。")

        token = self.resolve_key(api_key)
        payload = self.build_h3_payload(
            prompt, ratio, resolution, duration, prompt_optimizer, noise_seed,
            images=(("first_frame", first_frame), ("last_frame", last_frame)),
        )
        video_url, task_id = self.run_task(payload, token, poll_interval, max_wait)
        return self.finish(video_url, task_id, download_video, max_frames, save_dir, "minimax")


# ==================== 多模態參考生影片 ====================
# 以下常數全部照抄官方文件的硬性限制，不要憑印象調整。

MAX_REFERENCE_IMAGES = 9      # role=reference_image 最多 9 張
MAX_REFERENCE_VIDEOS = 3      # role=reference_video 最多 3 段，總長 <= 15 秒
MAX_REFERENCE_AUDIOS = 3      # role=reference_audio 最多 3 段，總長 <= 15 秒
PROMPT_MAX_CHARS = 7000       # text 項上限
MAX_BODY_BYTES = 64 * 1024 * 1024   # 請求體總大小上限
IMAGE_MIN_SIDE = 256          # 參考圖寬高需落在 [256, 5760]，上限由 REFERENCE_MAX_SIDE 保證
IMAGE_RATIO_RANGE = (0.4, 2.5)      # 參考圖寬高比允許區間

# DMXAPI 中轉對「多模態參考」這個計費項目要求 input 內必須含至少一段
# role=reference_video；只給參考圖會被上游以
#   400 {"code": "dmxapi_billing_error", "message": "billing requires at least one reference video"}
# 擋下（官方文件把三種素材都寫成選填，但中轉的計費規則不是，實測確認）。
# 這條規則屬於中轉而非 MiniMax 本身，日後若放寬，改掉這個旗標即可。
REQUIRE_REFERENCE_VIDEO = True

# 內嵌圖片縮到的長邊上限。上游允許到 5760，但 base64 是算進那 64 MB 請求體的，
# 而參考用途不需要原尺寸——9 張原尺寸 2K 照片很容易就把體積推爆。
REFERENCE_MAX_SIDE = 2048


def _url_lines(text, limit, label):
    """多行文字 → URL 清單，超過上游數量上限就截斷並示警。"""
    urls = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(urls) > limit:
        logger.warning(
            "[DMXAPI] %s 收到 %s 筆，超過上游上限 %s 筆，只送出前 %s 筆。",
            label, len(urls), limit, limit,
        )
        urls = urls[:limit]
    return urls


def _media_items(kind, urls):
    """URL 清單 → input 項目。kind 為 "image" / "video" / "audio"。"""
    key = kind + "_url"
    return [{"type": key, "role": "reference_" + kind, key: {"url": url}} for url in urls]


class DMXAPI_MiniMax_Reference2V(MiniMaxVideoBase):
    """多模態參考生影片（H3）：用參考圖、參考影片與參考音訊指定主體、動作與音色。

    與圖生視頻**互斥**（官方明文）：input 裡出現任一 ``reference_*`` role，就不能
    再出現 ``first_frame`` / ``last_frame``。因此這個節點刻意不提供影格輸入，
    首尾幀請改用「DMXAPI MiniMax 影片生成」。

    參考素材可以是 ComfyUI 的 IMAGE（節點自行編成 data URI），也可以是公網 URL；
    影片與音訊上游只收 URL，無法從畫布傳入。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": _minimax_inputs(
                ratios=H3_REFERENCE_RATIOS,
                ratio_tooltip="畫面比例。adaptive = 由上游依參考素材自動決定"
                              "（實際比例可在查詢結果的 ratio 欄位看到）",
            ),
            "optional": {
                "reference_images": ("IMAGE",),
                "reference_image_urls": ("STRING", {
                    "multiline": True, "default": "",
                    "placeholder": "每行一個公網圖片 URL；與 reference_images 合計最多 9 張",
                }),
                "reference_video_urls": ("STRING", {
                    "multiline": True, "default": "",
                    "placeholder": "必填：每行一個公網影片 URL（MP4/MOV），最多 3 段、總長 15 秒",
                }),
                "reference_audio_urls": ("STRING", {
                    "multiline": True, "default": "",
                    "placeholder": "每行一個公網音訊 URL（WAV/MP3），最多 3 段、總長 15 秒",
                }),
            },
        }

    FUNCTION = "generate"

    @staticmethod
    def _warn_image_limits(frame, index):
        """檢查上游會擋、而我們的縮圖救不了的兩件事：短邊過小與比例過偏。"""
        height, width = int(frame.shape[1]), int(frame.shape[2])
        if min(width, height) < IMAGE_MIN_SIDE:
            logger.warning(
                "[DMXAPI] 第 %s 張參考圖 %sx%s，短邊小於上游下限 %s px，可能被拒。",
                index + 1, width, height, IMAGE_MIN_SIDE,
            )
        aspect = width / float(height) if height else 0.0
        low, high = IMAGE_RATIO_RANGE
        if not low <= aspect <= high:
            logger.warning(
                "[DMXAPI] 第 %s 張參考圖比例 %.2f 超出上游允許的 %s~%s，可能被拒。",
                index + 1, aspect, low, high,
            )

    def _encode_images(self, images, budget):
        """IMAGE batch → data URI 陣列。

        tensor_to_image_bytes() 只取 batch 第一張，所以逐張切片後再呼叫；
        一律 JPEG，理由同其他節點（同內容下 base64 體積遠小於 PNG）。
        """
        count = int(images.shape[0])
        if budget <= 0:
            logger.warning(
                "[DMXAPI] 參考圖 URL 已佔滿上游的 %s 張額度，reference_images 全數略過。",
                MAX_REFERENCE_IMAGES,
            )
            return []

        if count > budget:
            logger.warning(
                "[DMXAPI] reference_images 收到 %s 張，連同 URL 已達上限 %s 張，只送出前 %s 張。",
                count, MAX_REFERENCE_IMAGES, budget,
            )
            count = budget

        data_urls = []
        for index in range(count):
            frame = images[index:index + 1]
            self._warn_image_limits(frame, index)
            raw, mime = tensor_to_image_bytes(
                frame, fmt="JPEG", quality=95, max_side=REFERENCE_MAX_SIDE,
            )
            data_urls.append("data:" + mime + ";base64," + base64.b64encode(raw).decode("utf-8"))
        return data_urls

    def build_reference_items(self, reference_images, image_urls, video_urls, audio_urls):
        """把四種來源收斂成 input 陣列裡的參考項目（不含 text）。"""
        urls = _url_lines(image_urls, MAX_REFERENCE_IMAGES, "reference_image_urls")
        embedded = []
        if reference_images is not None:
            embedded = self._encode_images(reference_images, MAX_REFERENCE_IMAGES - len(urls))

        videos = _url_lines(video_urls, MAX_REFERENCE_VIDEOS, "reference_video_urls")
        audios = _url_lines(audio_urls, MAX_REFERENCE_AUDIOS, "reference_audio_urls")

        items = (_media_items("image", embedded + urls)
                 + _media_items("video", videos)
                 + _media_items("audio", audios))

        embedded_bytes = sum(len(url) for url in embedded)
        if embedded_bytes > MAX_BODY_BYTES:
            raise ValueError(
                "[DMXAPI Error] 內嵌參考圖共 %.1f MB，超過上游 64 MB 的請求體上限。"
                "請減少張數，或改用公網 URL。" % (embedded_bytes / 1048576.0)
            )

        logger.info(
            "[DMXAPI] 多模態參考：圖片 %s 張（內嵌 %s、URL %s，內嵌共 %.1f KB）、影片 %s 段、音訊 %s 段",
            len(embedded) + len(urls), len(embedded), len(urls),
            embedded_bytes / 1024.0, len(videos), len(audios),
        )
        return items

    def generate(self, prompt, resolution, ratio, duration, noise_seed, model, api_key,
                 prompt_optimizer, download_video, max_frames, save_dir, poll_interval, max_wait,
                 reference_images=None, reference_image_urls="", reference_video_urls="",
                 reference_audio_urls=""):
        self.validate_model(model)

        text = prompt.strip()
        if not text:
            raise ValueError(
                "[DMXAPI Error] 多模態參考生影片必須提供非空 Prompt（上游明文要求）。"
            )
        if len(text) > PROMPT_MAX_CHARS:
            raise ValueError(
                "[DMXAPI Error] Prompt 共 %s 字，超過上游上限 %s 字。"
                % (len(text), PROMPT_MAX_CHARS)
            )

        items = self.build_reference_items(
            reference_images, reference_image_urls, reference_video_urls, reference_audio_urls,
        )
        if not items:
            raise ValueError(
                "[DMXAPI Error] 沒有任何參考素材。請接上 reference_images，"
                "或填入參考圖／影片／音訊的公網 URL；"
                "純文生影片與首尾幀請改用「DMXAPI MiniMax 影片生成」節點。"
            )

        if REQUIRE_REFERENCE_VIDEO and not any(item["type"] == "video_url" for item in items):
            raise ValueError(
                "[DMXAPI Error] 多模態參考生影片至少需要一段參考影片。"
                "DMXAPI 中轉對這個計費項目要求 input 內含 role=reference_video，"
                "只給參考圖會被上游以 400 dmxapi_billing_error 拒絕。"
                "請在 reference_video_urls 填入至少一段公網影片 URL；"
                "若只想用圖片生成，請改用「DMXAPI MiniMax 影片生成」節點的 first_frame。"
            )

        token = self.resolve_key(api_key)
        payload = self.build_h3_payload(
            text, ratio, resolution, duration, prompt_optimizer, noise_seed,
            extra_items=items, send_ratio=True,
        )
        video_url, task_id = self.run_task(payload, token, poll_interval, max_wait)
        return self.finish(video_url, task_id, download_video, max_frames, save_dir, "minimax_ref")


NODE_CLASS_MAPPINGS = {
    "DMXAPI_MiniMax_Video": DMXAPI_MiniMax_Video,
    "DMXAPI_MiniMax_Reference2V": DMXAPI_MiniMax_Reference2V,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DMXAPI_MiniMax_Video": "DMXAPI MiniMax 影片生成",
    "DMXAPI_MiniMax_Reference2V": "DMXAPI MiniMax 多模態參考生影片",
}
