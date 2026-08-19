"""
DMXAPI 豆包 Seedance 2.0 影片生成節點

端點：POST /v1/responses（與 MiniMax 共用，靠 payload 的 model 欄位區分動作）

流程兩段式，但與 MiniMax 有兩個關鍵差異：
  1. 提交回傳的 key 是 id，不是 task_id
  2. 輪詢結果多包一層 JSON 字串——真正的狀態在
     data["output"][0]["content"][0]["text"]，必須再 json.loads() 一次

節點：文生影片、首幀生影片、首尾幀生影片、多模態參考、影片延長、影片編輯，
以及一個可用 task_id 或 video_url 事後取回影片的下載節點。
"""

import json

from .dmxapi_common import (
    RESPONSES_URL,
    SEEDANCE_RATIOS,
    SEEDANCE_RESOLUTION_TIERS,
    DMXAPIVideoNodeBase,
    download_image_tensor,
    build_video_preview,
    decode_frames,
    download_video_file,
    duration_seconds,
    empty_frame,
    logger,
    poll_task,
    post_json,
    ratio_from_size,
    resolution_from_size,
    resolve_api_key,
    tensor_to_data_url,
    to_video_output,
)

SEEDANCE_MODEL = "doubao-seedance-2-0-260128"
SEEDANCE_QUERY_MODEL = "seedance-2-0-get"


def _parse_seedance(data):
    """Seedance 的狀態藏在巢狀 JSON 字串裡；解析不出來代表任務還在排隊。"""
    try:
        inner = json.loads(data["output"][0]["content"][0]["text"])
    except (KeyError, IndexError, TypeError, ValueError):
        return ("pending", "排隊中")

    status = inner.get("status", "unknown")

    if status == "succeeded":
        content = inner.get("content", {})
        video_url = content.get("video_url")
        if not video_url:
            return ("failed", "狀態為 succeeded 但缺少 video_url：" + str(inner))
        last_frame_url = content.get("last_frame_url") or content.get("last_frame")
        return ("done", (video_url, last_frame_url))

    if status in ("failed", "expired"):
        return ("failed", str(inner))

    return ("pending", status)


def poll_seedance(token, task_id, poll_interval=8, max_wait=900):
    """輪詢 Seedance 任務，回傳 (video_url, last_frame_url)。"""
    return poll_task(
        {"model": SEEDANCE_QUERY_MODEL, "input": task_id},
        _parse_seedance, token, label="Seedance",
        poll_interval=poll_interval, max_wait=max_wait,
    )


class SeedanceVideoBase(DMXAPIVideoNodeBase):
    """Seedance 各節點共用的輸入組裝、提交與輪詢。"""

    @classmethod
    def base_inputs(cls, default_width=0, default_height=0, default_duration=5.0, min_duration=4.0):
        """與官方影片範本同名的 prompt / width / height / duration / noise_seed。

        以圖或影片為輸入的節點預設寬高為 0，換算成 Seedance 的 ratio="adaptive"，
        維持原本「畫面比例跟隨參考素材」的行為。
        """
        required = {"prompt": ("STRING", {"default": "", "multiline": True})}
        required.update(cls.size_inputs(default_width, default_height))
        required.update(cls.duration_input(default_duration, min_duration))
        required.update({
            "noise_seed": ("INT", {
                "default": -1, "min": -1, "max": 2147483647,
                "tooltip": "-1 = 交由上游隨機",
            }),
            "api_key": ("STRING", {"default": "", "multiline": False}),
            "generate_audio": ("BOOLEAN", {"default": True}),
            "watermark": ("BOOLEAN", {"default": False}),
            "return_last_frame": ("BOOLEAN", {"default": False}),
            "web_search": ("BOOLEAN", {"default": False}),
        })
        required.update(cls.common_inputs(download_default=True))
        return required

    def resolve_key(self, api_key):
        return resolve_api_key(api_key, "SEEDANCE_API_KEY", "ARK_API_KEY")

    def resolve_size(self, width, height):
        """width / height → Seedance 的 (ratio, resolution)；寬高為 0 時走 adaptive。"""
        ratio = ratio_from_size(width, height, SEEDANCE_RATIOS)
        resolution = resolution_from_size(width, height, SEEDANCE_RESOLUTION_TIERS, default="720p")
        logger.info(
            "[DMXAPI] Seedance %sx%s → ratio=%s resolution=%s", width, height, ratio, resolution
        )
        return (ratio, resolution)

    def build_payload(self, inputs, ratio, resolution, duration, generate_audio,
                      watermark, noise_seed, return_last_frame, web_search):
        payload = {
            "model": SEEDANCE_MODEL,
            "input": inputs,
            "ratio": ratio,
            "resolution": resolution,
            "duration": duration_seconds(duration),
            "generate_audio": generate_audio,
            "watermark": watermark,
            "seed": noise_seed,
            "return_last_frame": return_last_frame,
        }
        if web_search:
            payload["tools"] = [{"type": "web_search"}]
        return payload

    def text_item(self, prompt):
        return {"type": "text", "text": prompt.strip()}

    def image_item(self, tensor, role):
        return {
            "type": "image_url",
            "image_url": {"url": tensor_to_data_url(tensor, fmt="PNG")},
            "role": role,
        }

    def video_item(self, url, role="reference_video"):
        return {"type": "video_url", "video_url": {"url": url.strip()}, "role": role}

    def submit(self, payload, token):
        data = post_json(RESPONSES_URL, payload, token, timeout=120)
        # Seedance 用 id 而非 task_id
        task_id = data.get("id")
        if not task_id:
            raise RuntimeError("[DMXAPI Error] Seedance 提交失敗，未取得 task_id：" + str(data))
        logger.info("[DMXAPI] Seedance 已提交 task_id=%s", task_id)
        return str(task_id)

    def run(self, label, inputs, api_key, width, height, duration, generate_audio,
            watermark, noise_seed, return_last_frame, web_search,
            download_video, max_frames, save_dir, poll_interval, max_wait, prefix):
        token = self.resolve_key(api_key)
        ratio, resolution = self.resolve_size(width, height)
        payload = self.build_payload(
            inputs, ratio, resolution, duration, generate_audio,
            watermark, noise_seed, return_last_frame, web_search,
        )
        logger.info(
            "[DMXAPI] %s 提交中 ratio=%s res=%s dur=%ss",
            label, ratio, resolution, payload["duration"],
        )

        task_id = self.submit(payload, token)
        video_url, last_frame_url = poll_seedance(token, task_id, poll_interval, max_wait)

        return self.finish(
            video_url, task_id, download_video, max_frames, save_dir, prefix,
            last_frame_url=last_frame_url if return_last_frame else None,
        )


# ==================== 1. 文生影片 ====================

class DMXAPI_Seedance2_Text2Video(SeedanceVideoBase):
    @classmethod
    def INPUT_TYPES(cls):
        # 沒有參考素材可跟隨，寬高要給明確值（1280x720 → 16:9 / 720p）
        return {"required": cls.base_inputs(default_width=1280, default_height=720)}

    FUNCTION = "generate"

    def generate(self, prompt, width, height, duration, noise_seed, api_key, generate_audio,
                 watermark, return_last_frame, web_search, download_video, max_frames, save_dir,
                 poll_interval, max_wait):
        if not prompt.strip():
            raise ValueError("[DMXAPI Error] Prompt 不能為空。")

        return self.run(
            "Seedance 文生影片", [self.text_item(prompt)],
            api_key, width, height, duration, generate_audio, watermark, noise_seed,
            return_last_frame, web_search, download_video, max_frames, save_dir,
            poll_interval, max_wait, "seedance_t2v",
        )


# ==================== 2. 首幀生影片 ====================

class DMXAPI_Seedance2_FirstFrame2Video(SeedanceVideoBase):
    @classmethod
    def INPUT_TYPES(cls):
        required = cls.base_inputs()
        required["first_frame"] = ("IMAGE",)
        return {"required": required}

    FUNCTION = "generate"

    def generate(self, prompt, first_frame, width, height, duration, noise_seed, api_key,
                 generate_audio, watermark, return_last_frame, web_search, download_video,
                 max_frames, save_dir, poll_interval, max_wait):
        if not prompt.strip():
            raise ValueError("[DMXAPI Error] Prompt 不能為空。")

        inputs = [self.text_item(prompt), self.image_item(first_frame, "first_frame")]
        return self.run(
            "Seedance 首幀生影片", inputs,
            api_key, width, height, duration, generate_audio, watermark, noise_seed,
            return_last_frame, web_search, download_video, max_frames, save_dir,
            poll_interval, max_wait, "seedance_i2v",
        )


# ==================== 3. 首尾幀生影片 ====================

class DMXAPI_Seedance2_FirstLastFrame2Video(SeedanceVideoBase):
    @classmethod
    def INPUT_TYPES(cls):
        required = cls.base_inputs()
        required["first_frame"] = ("IMAGE",)
        required["last_frame"] = ("IMAGE",)
        return {"required": required}

    FUNCTION = "generate"

    def generate(self, prompt, first_frame, last_frame, width, height, duration, noise_seed,
                 api_key, generate_audio, watermark, return_last_frame, web_search,
                 download_video, max_frames, save_dir, poll_interval, max_wait):
        if not prompt.strip():
            raise ValueError("[DMXAPI Error] Prompt 不能為空。")

        inputs = [
            self.text_item(prompt),
            self.image_item(first_frame, "first_frame"),
            self.image_item(last_frame, "last_frame"),
        ]
        return self.run(
            "Seedance 首尾幀生影片", inputs,
            api_key, width, height, duration, generate_audio, watermark, noise_seed,
            return_last_frame, web_search, download_video, max_frames, save_dir,
            poll_interval, max_wait, "seedance_flf2v",
        )


# ==================== 4. 多模態參考 ====================

class DMXAPI_Seedance2_MultimodalRef(SeedanceVideoBase):
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": cls.base_inputs(default_duration=8.0),
            "optional": {
                "image1": ("IMAGE",),
                "image2": ("IMAGE",),
                "image3": ("IMAGE",),
                "image4": ("IMAGE",),
                "video_url_1": ("STRING", {"default": ""}),
            },
        }

    FUNCTION = "generate"

    def generate(self, prompt, width, height, duration, noise_seed, api_key, generate_audio,
                 watermark, return_last_frame, web_search, download_video, max_frames, save_dir,
                 poll_interval, max_wait,
                 image1=None, image2=None, image3=None, image4=None, video_url_1=""):
        if not prompt.strip():
            raise ValueError("[DMXAPI Error] Prompt 不能為空。")

        inputs = [self.text_item(prompt)]
        for image in (image1, image2, image3, image4):
            if image is not None:
                inputs.append(self.image_item(image, "reference_image"))
        if video_url_1 and video_url_1.strip():
            inputs.append(self.video_item(video_url_1))

        if len(inputs) <= 1:
            raise ValueError("[DMXAPI Error] 請至少提供一張參考圖或一段參考影片 URL。")

        return self.run(
            "Seedance 多模態參考", inputs,
            api_key, width, height, duration, generate_audio, watermark, noise_seed,
            return_last_frame, web_search, download_video, max_frames, save_dir,
            poll_interval, max_wait, "seedance_ref",
        )


# ==================== 5. 影片延長 ====================

class DMXAPI_Seedance2_VideoExtend(SeedanceVideoBase):
    @classmethod
    def INPUT_TYPES(cls):
        required = cls.base_inputs(default_duration=8.0, min_duration=8.0)
        required["source_video_url"] = ("STRING", {"default": ""})
        return {"required": required}

    FUNCTION = "generate"

    def generate(self, prompt, source_video_url, width, height, duration, noise_seed, api_key,
                 generate_audio, watermark, return_last_frame, web_search, download_video,
                 max_frames, save_dir, poll_interval, max_wait):
        if not prompt.strip():
            raise ValueError("[DMXAPI Error] Prompt 不能為空。")
        if not source_video_url.strip():
            raise ValueError("[DMXAPI Error] 請提供來源影片 URL。")

        inputs = [self.text_item(prompt), self.video_item(source_video_url)]
        return self.run(
            "Seedance 影片延長", inputs,
            api_key, width, height, duration, generate_audio, watermark, noise_seed,
            return_last_frame, web_search, download_video, max_frames, save_dir,
            poll_interval, max_wait, "seedance_extend",
        )


# ==================== 6. 影片編輯 ====================

class DMXAPI_Seedance2_VideoEdit(SeedanceVideoBase):
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": cls.base_inputs(default_duration=8.0),
            "optional": {
                "image1": ("IMAGE",),
                "image2": ("IMAGE",),
                "image3": ("IMAGE",),
                "video_url_1": ("STRING", {"default": ""}),
                "video_url_2": ("STRING", {"default": ""}),
            },
        }

    FUNCTION = "generate"

    def generate(self, prompt, width, height, duration, noise_seed, api_key, generate_audio,
                 watermark, return_last_frame, web_search, download_video, max_frames, save_dir,
                 poll_interval, max_wait,
                 image1=None, image2=None, image3=None, video_url_1="", video_url_2=""):
        if not prompt.strip():
            raise ValueError("[DMXAPI Error] Prompt 不能為空。")

        inputs = [self.text_item(prompt)]
        for image in (image1, image2, image3):
            if image is not None:
                inputs.append(self.image_item(image, "reference_image"))
        for url in (video_url_1, video_url_2):
            if url and url.strip():
                inputs.append(self.video_item(url))

        if len(inputs) <= 1:
            raise ValueError("[DMXAPI Error] 請至少提供一張圖或一段影片 URL。")

        return self.run(
            "Seedance 影片編輯", inputs,
            api_key, width, height, duration, generate_audio, watermark, noise_seed,
            return_last_frame, web_search, download_video, max_frames, save_dir,
            poll_interval, max_wait, "seedance_edit",
        )


# ==================== 7. 下載影片 ====================

class DMXAPI_Seedance2_DownloadVideo(DMXAPIVideoNodeBase):
    """以 task_id 或 video_url 取回影片，供事後補下載或跨工作流取件。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "filename_prefix": ("STRING", {"default": "seedance"}),
                "save_dir": ("STRING", {"default": "", "placeholder": "留空 = ComfyUI output"}),
            },
            "optional": {
                "api_key": ("STRING", {"default": ""}),
                "task_id": ("STRING", {"default": ""}),
                "video_url": ("STRING", {"default": ""}),
                "max_frames": ("INT", {"default": 1, "min": -1, "max": 4096, "tooltip": "-1 = 全部解碼；0 = 不解碼（只留影片預覽與檔案）；N = 最多 N 幀"}),
                "poll_interval": ("INT", {"default": 8, "min": 3, "max": 30}),
                "max_wait": ("INT", {"default": 300, "min": 30, "max": 3600}),
            },
        }

    FUNCTION = "download"
    OUTPUT_NODE = True

    def download(self, filename_prefix, save_dir, api_key="", task_id="", video_url="",
                 max_frames=1, poll_interval=8, max_wait=300):
        task_id = (task_id or "").strip()
        video_url = (video_url or "").strip()
        api_key = (api_key or "").strip()

        if not video_url and not task_id:
            raise ValueError("[DMXAPI Error] 請至少填入 task_id 或 video_url。")

        last_frame_url = None
        if task_id and not video_url:
            token = resolve_api_key(api_key, "SEEDANCE_API_KEY", "ARK_API_KEY")
            logger.info("[DMXAPI] 以 task_id=%s 查詢影片網址", task_id)
            video_url, last_frame_url = poll_seedance(token, task_id, poll_interval, max_wait)

        path = download_video_file(video_url, save_dir, filename_prefix or "seedance")

        frames = decode_frames(path, max_frames)
        last_frame = empty_frame() if frames is None else frames[-1:]
        if frames is None:
            frames = empty_frame()

        if last_frame_url:
            api_last_frame = download_image_tensor(last_frame_url)
            if api_last_frame is not None:
                last_frame = api_last_frame

        return {
            "ui": build_video_preview(path),
            "result": (to_video_output(path), frames, last_frame, path, video_url, task_id),
        }


NODE_CLASS_MAPPINGS = {
    "DMXAPI_Seedance2_Text2Video": DMXAPI_Seedance2_Text2Video,
    "DMXAPI_Seedance2_FirstFrame2Video": DMXAPI_Seedance2_FirstFrame2Video,
    "DMXAPI_Seedance2_FirstLastFrame2Video": DMXAPI_Seedance2_FirstLastFrame2Video,
    "DMXAPI_Seedance2_MultimodalRef": DMXAPI_Seedance2_MultimodalRef,
    "DMXAPI_Seedance2_VideoExtend": DMXAPI_Seedance2_VideoExtend,
    "DMXAPI_Seedance2_VideoEdit": DMXAPI_Seedance2_VideoEdit,
    "DMXAPI_Seedance2_DownloadVideo": DMXAPI_Seedance2_DownloadVideo,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DMXAPI_Seedance2_Text2Video": "DMXAPI Seedance2 文生影片",
    "DMXAPI_Seedance2_FirstFrame2Video": "DMXAPI Seedance2 首幀生影片",
    "DMXAPI_Seedance2_FirstLastFrame2Video": "DMXAPI Seedance2 首尾幀生影片",
    "DMXAPI_Seedance2_MultimodalRef": "DMXAPI Seedance2 多模態參考生影片",
    "DMXAPI_Seedance2_VideoExtend": "DMXAPI Seedance2 影片延長",
    "DMXAPI_Seedance2_VideoEdit": "DMXAPI Seedance2 影片編輯",
    "DMXAPI_Seedance2_DownloadVideo": "DMXAPI Seedance2 下載影片",
}
