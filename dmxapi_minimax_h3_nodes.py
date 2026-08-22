"""DMXAPI MiniMax H3 影片生成節點。

使用 POST /v1/responses 兩段式協定：以 ``MiniMax-H3`` 提交任務，
再以 ``MiniMax-H3-get`` 輪詢，直到 ``task.content.url`` 提供影片網址。
"""

from .dmxapi_common import (
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
                         noise_seed=0, images=()):
        """images 為 (role, tensor) 序列，例如 ("first_frame", t)。

        ``ratio`` 只在**文生影片**（input 只有 text）時送出；一旦帶了參考圖，
        上游的比例恆為 ``adaptive``（跟隨圖片），送任何值都會被忽略，因此直接省略。
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

        payload = {
            "model": MINIMAX_MODEL,
            "input": items,
            "resolution": resolution,
            "duration": duration_seconds(duration),
            "prompt_optimizer": prompt_optimizer,
            "aigc_watermark": False,
        }

        # 帶參考圖時上游比例恆為 adaptive，送 ratio 只會被忽略，索性不送。
        if len(items) > 1:
            logger.info(
                "[DMXAPI] %s resolution=%s（帶參考圖，比例跟隨圖片，不送 ratio）",
                MINIMAX_MODEL, resolution,
            )
        else:
            payload["ratio"] = ratio
            logger.info(
                "[DMXAPI] %s resolution=%s ratio=%s",
                MINIMAX_MODEL, resolution, ratio,
            )
        if noise_seed and noise_seed > 0:
            payload["seed"] = int(noise_seed)
        return payload

# ==================== 節點 ====================

def _minimax_inputs():
    """組出 H3 的輸入：prompt / resolution / ratio / duration / noise_seed。

    上游只收 ``resolution`` 與 ``ratio`` 兩個列舉欄位，不接受任意像素尺寸，
    因此這裡**不提供 width / height**——收像素尺寸只會讓人誤以為能指定輸出解析度。
    範本的 unet/clip/vae 是本地推論用的，API 版換成 model 與 api_key。
    """
    required = {
        "prompt": ("STRING", {"multiline": True, "default": ""}),
        "resolution": (H3_RESOLUTIONS, {
            "default": H3_RESOLUTIONS[0],
            "tooltip": "上游解析度檔位；2K 較貴也較慢",
        }),
        "ratio": (MINIMAX_RATIOS, {
            "default": MINIMAX_RATIOS[0],
            "tooltip": "畫面比例。只在文生影片時生效；帶 first_frame / last_frame 時比例跟隨圖片",
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


# 非公開待重構：刻意不加入 NODE_CLASS_MAPPINGS / NODE_DISPLAY_NAME_MAPPINGS。
class DMXAPI_MiniMax_Reference2V(MiniMaxVideoBase):
    """參考圖生影片（H3），可另帶角色圖、風格圖與音訊 URL。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": _minimax_inputs(),
            "optional": {
                "character_image": ("IMAGE",),
                "style_image": ("IMAGE",),
                "audio_url": ("STRING", {"default": "", "multiline": False}),
            },
        }

    FUNCTION = "generate"

    def generate(self, prompt, resolution, ratio, duration, noise_seed, model, api_key,
                 prompt_optimizer, download_video, max_frames, save_dir, poll_interval, max_wait,
                 character_image=None, style_image=None, audio_url=""):
        self.validate_model(model)
        token = self.resolve_key(api_key)
        payload = self.build_h3_payload(
            prompt, ratio, resolution, duration, prompt_optimizer, noise_seed,
            images=(("character", character_image), ("style", style_image)),
        )
        if audio_url and audio_url.strip():
            payload["audio_url"] = audio_url.strip()

        video_url, task_id = self.run_task(payload, token, poll_interval, max_wait)
        return self.finish(video_url, task_id, download_video, max_frames, save_dir, "minimax_ref")


NODE_CLASS_MAPPINGS = {
    "DMXAPI_MiniMax_Video": DMXAPI_MiniMax_Video,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DMXAPI_MiniMax_Video": "DMXAPI MiniMax 影片生成",
}
