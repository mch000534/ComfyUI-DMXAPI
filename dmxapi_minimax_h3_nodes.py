"""
DMXAPI MiniMax 影片生成節點

端點：POST /v1/responses（單一端點多工，靠 payload 的 model 欄位區分動作）

H3 系列（MiniMax-H3 / MiniMax-H3-01）兩段式：
  提交 model=MiniMax-H3，input=[{type,text}, ...]，附 resolution / duration / ratio
    -> 取得 task_id
  輪詢 model=MiniMax-H3-get，input=<task_id>
    -> task.status == "succeeded" 時，影片位於 task.content.url

Hailuo-02 系列（MiniMax-Hailuo-02）三段式：
  提交 model=MiniMax-Hailuo-02，input=<prompt>，附 first_frame_image 等
    -> 取得 task_id
  輪詢 model=MiniMax-Hailuo-query，input={task_id}
    -> status == "Success" 時取得 file_id
  取檔 model=MiniMax-Hailuo-get，input={file_id}
    -> file.download_url 才是影片網址
"""

from .dmxapi_common import (
    H3_RESOLUTION_TIERS,
    HAILUO_RESOLUTION_TIERS,
    MINIMAX_RATIOS,
    RESPONSES_URL,
    DMXAPIVideoNodeBase,
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

H3_MODELS = {"MiniMax-H3", "MiniMax-H3-01"}


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


def _parse_hailuo(data):
    status = data.get("status", "")

    if status == "Success":
        file_id = data.get("file_id")
        if not file_id:
            return ("failed", "狀態為 Success 但缺少 file_id：" + str(data))
        return ("done", str(file_id))

    if status in ("Fail", "Failed", "FAILED"):
        return ("failed", data.get("message") or data.get("error_msg", "未知錯誤"))

    return ("pending", status)


# ==================== 共用基底 ====================

class MiniMaxVideoBase(DMXAPIVideoNodeBase):
    """MiniMax 兩種協定的提交與輪詢流程。"""

    def resolve_key(self, api_key):
        return resolve_api_key(api_key, "MINIMAX_API_KEY")

    def encode_image(self, tensor):
        # 影片首尾幀用 JPEG，避免 base64 PNG 撐爆請求體積
        return tensor_to_data_url(tensor, fmt="JPEG", quality=95)

    def resolve_size(self, model, width, height):
        """width / height → 該 model 所屬系列的 (ratio, resolution)。

        兩系列的解析度檔位不同（H3 只有 768P/2K，Hailuo-02 只有 512P/768P/1080P），
        各自查自己的檔位表就不可能組出上游會以 400 擋下的組合。
        """
        tiers = H3_RESOLUTION_TIERS if model in H3_MODELS else HAILUO_RESOLUTION_TIERS
        ratio = ratio_from_size(width, height, MINIMAX_RATIOS)
        resolution = resolution_from_size(width, height, tiers, default="768P")
        logger.info(
            "[DMXAPI] %s %sx%s → ratio=%s resolution=%s", model, width, height, ratio, resolution
        )
        return (ratio, resolution)

    def submit(self, payload, token, label):
        data = post_json(RESPONSES_URL, payload, token, timeout=60)
        task_id = str(data.get("task_id") or data.get("id") or "")
        if not task_id:
            raise RuntimeError("[DMXAPI Error] " + label + " 提交失敗，未取得 task_id：" + str(data))
        logger.info("[DMXAPI] %s 已提交 task_id=%s", label, task_id)
        return task_id

    def fetch_hailuo_url(self, file_id, token):
        """Hailuo 比 H3 多一段：拿 file_id 再換真正的下載網址。"""
        data = post_json(
            RESPONSES_URL, {"model": "MiniMax-Hailuo-get", "input": {"file_id": file_id}}, token
        )
        video_url = data.get("file", {}).get("download_url")
        if not video_url:
            raise RuntimeError("[DMXAPI Error] 無法取得 Hailuo download_url：" + str(data))
        return video_url

    def wait_for_url(self, series, task_id, token, poll_interval, max_wait):
        """輪詢既有的 task_id 直到取得影片網址。series 為 "H3" 或 "Hailuo-02"。"""
        if series == "H3":
            return poll_task(
                {"model": "MiniMax-H3-get", "input": task_id},
                _parse_h3, token, label="H3",
                poll_interval=poll_interval, max_wait=max_wait,
            )

        file_id = poll_task(
            {"model": "MiniMax-Hailuo-query", "input": {"task_id": task_id}},
            _parse_hailuo, token, label="Hailuo",
            poll_interval=poll_interval, max_wait=max_wait,
        )
        return self.fetch_hailuo_url(file_id, token)

    def detect_series(self, task_id, token):
        """探測 task_id 屬於哪個系列：H3 的回應帶 task 物件，Hailuo 帶 status 字串。"""
        try:
            data = post_json(
                RESPONSES_URL, {"model": "MiniMax-H3-get", "input": task_id},
                token, timeout=30, retries=1,
            )
            if isinstance(data.get("task"), dict):
                return "H3"
        except Exception as e:
            logger.debug("[DMXAPI] H3 探測未命中：%s", e)

        try:
            data = post_json(
                RESPONSES_URL, {"model": "MiniMax-Hailuo-query", "input": {"task_id": task_id}},
                token, timeout=30, retries=1,
            )
            if data.get("status"):
                return "Hailuo-02"
        except Exception as e:
            logger.debug("[DMXAPI] Hailuo 探測未命中：%s", e)

        raise RuntimeError(
            "[DMXAPI Error] 無法判斷 task_id=" + str(task_id)
            + " 屬於哪個系列，請在節點手動指定 series。"
        )

    def run_task(self, model, payload, token, poll_interval, max_wait):
        """提交並等待完成，回傳 (video_url, task_id)。"""
        series = "H3" if model in H3_MODELS else "Hailuo-02"
        task_id = self.submit(payload, token, series)
        video_url = self.wait_for_url(series, task_id, token, poll_interval, max_wait)
        return (video_url, task_id)

    def build_h3_payload(self, prompt, model, ratio, resolution, duration, prompt_optimizer,
                         noise_seed=0, images=()):
        """images 為 (role, tensor) 序列，例如 ("first_frame", t)。"""
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
            "model": model,
            "input": items,
            "resolution": resolution,
            "duration": duration_seconds(duration),
            "ratio": ratio,
            "prompt_optimizer": prompt_optimizer,
            "aigc_watermark": False,
        }
        if noise_seed and noise_seed > 0:
            payload["seed"] = int(noise_seed)
        return payload

    def build_hailuo_payload(self, prompt, model, resolution, duration, prompt_optimizer,
                             noise_seed=0, first_frame_image=None, last_frame_image=None):
        payload = {
            "model": model,
            "input": prompt,
            "prompt_optimizer": prompt_optimizer,
            "duration": duration_seconds(duration),
            "resolution": resolution,
            "aigc_watermark": False,
        }
        if first_frame_image is not None:
            payload["first_frame_image"] = self.encode_image(first_frame_image)
        if last_frame_image is not None:
            payload["last_frame_image"] = self.encode_image(last_frame_image)
        if noise_seed and noise_seed > 0:
            payload["seed"] = int(noise_seed)
        return payload


# ==================== 節點 ====================

def _minimax_inputs(models, width=1344, height=768):
    """組出與官方 H3 範本同名同序的輸入：prompt / width / height / duration / noise_seed。

    範本的 unet/clip/vae 是本地推論用的，API 版換成 model 與 api_key。
    """
    required = {
        "prompt": ("STRING", {"multiline": True, "default": ""}),
    }
    required.update(DMXAPIVideoNodeBase.size_inputs(width, height))
    required.update(DMXAPIVideoNodeBase.duration_input())
    required.update({
        "noise_seed": ("INT", {
            "default": 0, "min": 0, "max": 0xffffffffffffffff,
            "tooltip": "0 = 不指定 seed，交由上游隨機",
        }),
        "model": (models, {"default": models[0]}),
        "api_key": ("STRING", {"default": "", "multiline": False}),
        "prompt_optimizer": ("BOOLEAN", {"default": True}),
    })
    required.update(DMXAPIVideoNodeBase.common_inputs(download_default=True))
    return required


class DMXAPI_MiniMax_Video(MiniMaxVideoBase):
    """整合節點：無首幀為文生影片（建議 H3），有首幀為圖生影片（建議 Hailuo-02）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": _minimax_inputs(["MiniMax-H3", "MiniMax-H3-01", "MiniMax-Hailuo-02"]),
            "optional": {
                "first_frame": ("IMAGE",),
                "last_frame": ("IMAGE",),
            },
        }

    FUNCTION = "generate"

    def generate(self, prompt, width, height, duration, noise_seed, model, api_key,
                 prompt_optimizer, download_video, max_frames, save_dir, poll_interval, max_wait,
                 first_frame=None, last_frame=None):
        if first_frame is None and not prompt.strip():
            raise ValueError("[DMXAPI Error] 文生影片模式下 Prompt 不能為空。")

        # 兩系列的解析度檔位不同，換算時各查各的表，組不出上游不接受的值
        ratio, resolution = self.resolve_size(model, width, height)
        token = self.resolve_key(api_key)

        if model in H3_MODELS:
            payload = self.build_h3_payload(
                prompt, model, ratio, resolution, duration, prompt_optimizer, noise_seed,
                images=(("first_frame", first_frame), ("last_frame", last_frame)),
            )
        else:
            payload = self.build_hailuo_payload(
                prompt, model, resolution, duration, prompt_optimizer, noise_seed,
                first_frame, last_frame,
            )

        video_url, task_id = self.run_task(model, payload, token, poll_interval, max_wait)
        return self.finish(video_url, task_id, download_video, max_frames, save_dir, "minimax")


class DMXAPI_MiniMax_T2V(MiniMaxVideoBase):
    """文生影片（H3）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": _minimax_inputs(["MiniMax-H3", "MiniMax-H3-01"])}

    FUNCTION = "generate"

    def generate(self, prompt, width, height, duration, noise_seed, model, api_key,
                 prompt_optimizer, download_video, max_frames, save_dir, poll_interval, max_wait):
        if not prompt.strip():
            raise ValueError("[DMXAPI Error] Prompt 不能為空。")

        ratio, resolution = self.resolve_size(model, width, height)
        token = self.resolve_key(api_key)
        payload = self.build_h3_payload(
            prompt, model, ratio, resolution, duration, prompt_optimizer, noise_seed
        )
        video_url, task_id = self.run_task(model, payload, token, poll_interval, max_wait)
        return self.finish(video_url, task_id, download_video, max_frames, save_dir, "minimax_t2v")


class DMXAPI_MiniMax_I2V(MiniMaxVideoBase):
    """圖生影片 / 首尾幀生影片（Hailuo-02）。"""

    @classmethod
    def INPUT_TYPES(cls):
        required = {"first_frame": ("IMAGE",)}
        required.update(_minimax_inputs(["MiniMax-Hailuo-02"]))
        return {
            "required": required,
            "optional": {"last_frame": ("IMAGE",)},
        }

    FUNCTION = "generate"

    def generate(self, first_frame, prompt, width, height, duration, noise_seed, model, api_key,
                 prompt_optimizer, download_video, max_frames, save_dir, poll_interval, max_wait,
                 last_frame=None):
        # Hailuo payload 不帶 ratio，畫面比例跟隨首幀，width/height 只決定解析度檔位
        _, resolution = self.resolve_size(model, width, height)
        token = self.resolve_key(api_key)
        payload = self.build_hailuo_payload(
            prompt, model, resolution, duration, prompt_optimizer, noise_seed,
            first_frame, last_frame,
        )
        video_url, task_id = self.run_task(model, payload, token, poll_interval, max_wait)
        return self.finish(video_url, task_id, download_video, max_frames, save_dir, "minimax_i2v")


class DMXAPI_MiniMax_Reference2V(MiniMaxVideoBase):
    """參考圖生影片（H3），可另帶角色圖、風格圖與音訊 URL。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": _minimax_inputs(["MiniMax-H3", "MiniMax-H3-01"]),
            "optional": {
                "character_image": ("IMAGE",),
                "style_image": ("IMAGE",),
                "audio_url": ("STRING", {"default": "", "multiline": False}),
            },
        }

    FUNCTION = "generate"

    def generate(self, prompt, width, height, duration, noise_seed, model, api_key,
                 prompt_optimizer, download_video, max_frames, save_dir, poll_interval, max_wait,
                 character_image=None, style_image=None, audio_url=""):
        ratio, resolution = self.resolve_size(model, width, height)
        token = self.resolve_key(api_key)
        payload = self.build_h3_payload(
            prompt, model, ratio, resolution, duration, prompt_optimizer, noise_seed,
            images=(("character", character_image), ("style", style_image)),
        )
        if audio_url and audio_url.strip():
            payload["audio_url"] = audio_url.strip()

        video_url, task_id = self.run_task(model, payload, token, poll_interval, max_wait)
        return self.finish(video_url, task_id, download_video, max_frames, save_dir, "minimax_ref")


# ==================== 下載影片 ====================

class DMXAPI_MiniMax_DownloadVideo(MiniMaxVideoBase):
    """以 task_id 或 video_url 取回 MiniMax 影片，供事後補下載或跨工作流取件。

    task_id 可從任一 MiniMax 生成節點的 TASK_ID 輸出取得；即使當時關掉了
    download_video，或 ComfyUI 中途重啟，只要任務還在上游保留期內就能補抓。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "filename_prefix": ("STRING", {"default": "minimax"}),
                "save_dir": ("STRING", {"default": "", "placeholder": "留空 = ComfyUI output"}),
            },
            "optional": {
                "api_key": ("STRING", {"default": ""}),
                "task_id": ("STRING", {"default": ""}),
                "video_url": ("STRING", {"default": ""}),
                "series": (["auto", "H3", "Hailuo-02"], {"default": "auto"}),
                "max_frames": ("INT", {"default": 1, "min": -1, "max": 4096, "tooltip": "-1 = 全部解碼；0 = 不解碼（只留影片預覽與檔案）；N = 最多 N 幀"}),
                "poll_interval": ("INT", {"default": 8, "min": 3, "max": 30}),
                "max_wait": ("INT", {"default": 300, "min": 30, "max": 3600}),
            },
        }

    FUNCTION = "download"
    OUTPUT_NODE = True

    def download(self, filename_prefix, save_dir, api_key="", task_id="", video_url="",
                 series="auto", max_frames=1, poll_interval=8, max_wait=300):
        task_id = (task_id or "").strip()
        video_url = (video_url or "").strip()

        if not video_url and not task_id:
            raise ValueError("[DMXAPI Error] 請至少填入 task_id 或 video_url。")

        if not video_url:
            token = self.resolve_key(api_key)
            if series == "auto":
                series = self.detect_series(task_id, token)
                logger.info("[DMXAPI] 自動判定 task_id=%s 屬於 %s 系列", task_id, series)
            logger.info("[DMXAPI] 以 task_id=%s 查詢影片網址（%s）", task_id, series)
            video_url = self.wait_for_url(series, task_id, token, poll_interval, max_wait)

        path = download_video_file(video_url, save_dir, filename_prefix or "minimax")

        frames = decode_frames(path, max_frames)
        last_frame = empty_frame() if frames is None else frames[-1:]
        if frames is None:
            frames = empty_frame()

        return {
            "ui": build_video_preview(path),
            "result": (to_video_output(path), frames, last_frame, path, video_url, task_id),
        }


NODE_CLASS_MAPPINGS = {
    "DMXAPI_MiniMax_Video": DMXAPI_MiniMax_Video,
    "DMXAPI_MiniMax_T2V": DMXAPI_MiniMax_T2V,
    "DMXAPI_MiniMax_I2V": DMXAPI_MiniMax_I2V,
    "DMXAPI_MiniMax_Reference2V": DMXAPI_MiniMax_Reference2V,
    "DMXAPI_MiniMax_DownloadVideo": DMXAPI_MiniMax_DownloadVideo,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DMXAPI_MiniMax_Video": "DMXAPI MiniMax 影片生成",
    "DMXAPI_MiniMax_T2V": "DMXAPI MiniMax 文生影片",
    "DMXAPI_MiniMax_I2V": "DMXAPI MiniMax 圖生影片",
    "DMXAPI_MiniMax_Reference2V": "DMXAPI MiniMax 參考圖生影片",
    "DMXAPI_MiniMax_DownloadVideo": "DMXAPI MiniMax 下載影片",
}
