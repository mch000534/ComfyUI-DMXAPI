"""
DMXAPI GPT Image 2 圖像生成節點

走 OpenAI 相容的同步端點，提交後直接拿到影像：
純文生圖打 /v1/images/generations（JSON），帶參考圖時改打 /v1/images/edits（multipart）。
"""

import requests

from .dmxapi_common import (
    IMAGES_EDITS_URL,
    IMAGES_URL,
    fetch_image_item,
    logger,
    pil_to_tensor_batch,
    post_json,
    post_multipart,
    resolve_api_key,
    tensor_to_image_bytes,
)


class DMXAPI_GPT_Image2:
    """調用 DMXAPI GPT Image 2 圖像生成介面。"""

    # 每個選項都必須通過 _validate_size 的四項約束，新增前請先驗算
    SUPPORTED_SIZES = [
        "auto",
        "1024x1024",
        "1536x1024",
        "1024x1536",
        "2048x2048",
        "2048x1152",
        "3840x2160",
        "2160x3840",
    ]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "model": (
                    ["gpt-image-2-03", "gpt-image-2", "gpt-image-2-ssvip"],
                    {"default": "gpt-image-2-03"},
                ),
                "api_key": ("STRING", {"default": "", "multiline": False}),
                "size": (cls.SUPPORTED_SIZES, {"default": "auto"}),
                "batch_size": ("INT", {"default": 1, "min": 1, "max": 4, "step": 1}),
                # quality 是唯一能直接砍生成時間的旋鈕。同步端點約 60 秒就會被
                # 上游切斷，auto（等同偏高品質）常常來不及；被切就往 medium / low 降。
                # 擺在最後是為了不打亂既有 workflow 依位置存的 widgets_values。
                "quality": (["auto", "low", "medium", "high"], {"default": "auto"}),
            },
            "optional": {
                "image": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("IMAGE",)
    FUNCTION = "generate_image"
    CATEGORY = "DMXAPI/Image"

    # 這是同步端點，上游閘道約 60 秒就會切斷連線（實測 3840x2160 必定被斷）。
    # 像素越多越容易撞上，超過這個量就先示警，免得使用者等到重試跑完才知道。
    SLOW_SIZE_PIXELS = 4000000

    # 參考圖上傳前縮到的長邊上限。上傳耗時同樣算進那 60 秒，5 MB 的原尺寸 PNG
    # 光傳就吃掉大半（實測 66 / 92 秒兩次都被切）；2048 對參考用途已綽綽有餘。
    REFERENCE_MAX_SIDE = 2048

    # 官方文件註明 gpt-image-2-03 只支援 n=1（其餘變體 1~10）。節點的
    # batch_size 上限是 4，送超過會被上游擋，因此這裡先夾住並示警。
    SINGLE_IMAGE_ONLY_MODELS = ("gpt-image-2-03",)

    def _validate_size(self, size_str):
        """校驗 DMXAPI 的尺寸約束，回傳 (width, height)；auto 回傳 None。

        1. 最大邊長 <= 3840px
        2. 寬與高必須是 16 的倍數
        3. 長寬比 <= 3:1
        4. 總像素介於 655,360 與 8,294,400 之間
        """
        if size_str == "auto":
            return None

        try:
            width_str, height_str = size_str.lower().split("x")
            width = int(width_str)
            height = int(height_str)
        except Exception:
            raise ValueError(
                "[DMXAPI Error] 尺寸格式不正確：'" + size_str + "'，應為 'WIDTHxHEIGHT' 或 'auto'。"
            )

        if max(width, height) > 3840:
            raise ValueError(
                "[DMXAPI Error] 最大邊長不能超過 3840px，目前為 " + str(max(width, height)) + "px。"
            )

        if width % 16 != 0 or height % 16 != 0:
            raise ValueError(
                "[DMXAPI Error] 寬與高必須皆為 16 的倍數（當前 "
                + str(width) + "x" + str(height) + "）。"
            )

        aspect_ratio = max(width, height) / min(width, height)
        if aspect_ratio > 3.0:
            raise ValueError(
                "[DMXAPI Error] 長寬比不能超過 3:1（當前 {:.2f}:1）。".format(aspect_ratio)
            )

        total_pixels = width * height
        if not (655360 <= total_pixels <= 8294400):
            raise ValueError(
                "[DMXAPI Error] 總像素必須介於 655,360 到 8,294,400 之間"
                "（當前 {:,} 像素）。".format(total_pixels)
            )

        return (width, height)

    def _submit_generation(self, prompt, model, size, quality, batch_size, token, session):
        """文生圖：JSON 打 /v1/images/generations。"""
        payload = {
            "model": model,
            "prompt": prompt,
            "n": int(batch_size),
            "response_format": "b64_json",
        }
        if size != "auto":
            payload["size"] = size
        if quality != "auto":
            payload["quality"] = quality

        # 4K 生成耗時較長，逾時放寬到 120 秒
        return post_json(IMAGES_URL, payload, token, timeout=120, session=session)

    def _submit_edit(self, prompt, model, size, quality, batch_size, image, token, session):
        """圖生圖：multipart 打 /v1/images/edits。

        不能把圖塞進 /v1/images/generations 的 payload——那是純文生圖端點，
        會回 400 `Unknown parameter: 'image'`（實測確認）。編輯端點收的是
        multipart 檔案欄位，不是 base64 字串。

        這裡也不送 response_format：gpt-image 系列固定回 b64_json，多送這個
        欄位反而可能再吃一次 unknown_parameter，回傳格式交給 fetch_image_item
        自行判讀（b64_json 與 url 都吃）。
        """
        if image.shape[0] > 1:
            logger.warning(
                "[DMXAPI] image 收到 %s 張，只會送出第一張作為參考圖。", image.shape[0]
            )

        # 參考圖用 JPEG 且先縮到 REFERENCE_MAX_SIDE：上傳時間是算在閘道那 60 秒
        # 回應上限裡的，實測原尺寸 PNG（5 MB）連上傳帶生成必定超時被切。
        raw, mime = tensor_to_image_bytes(
            image, fmt="JPEG", quality=95, max_side=self.REFERENCE_MAX_SIDE
        )
        fields = {
            "model": model,
            "prompt": prompt,
            "n": str(int(batch_size)),
        }
        if size != "auto":
            fields["size"] = size
        if quality != "auto":
            fields["quality"] = quality

        files = {"image": ("image.jpg", raw, mime)}
        logger.info("[DMXAPI] 附帶參考圖，改走編輯端點（%.1f KB）", len(raw) / 1024.0)

        return post_multipart(
            IMAGES_EDITS_URL, fields, files, token, timeout=120, session=session
        )

    def generate_image(self, prompt, model, api_key, size, quality, batch_size, image=None):
        if not prompt.strip():
            raise ValueError("[DMXAPI Error] Prompt 不能為空！")

        token = resolve_api_key(api_key, "OPENAI_API_KEY")
        dimensions = self._validate_size(size)

        if model in self.SINGLE_IMAGE_ONLY_MODELS and int(batch_size) > 1:
            logger.warning(
                "[DMXAPI] %s 只支援 n=1，batch_size=%s 已自動夾成 1。"
                "要一次多張請改用 gpt-image-2 或 gpt-image-2-ssvip。", model, batch_size,
            )
            batch_size = 1

        logger.info(
            "[DMXAPI] 提交圖像%s model=%s size=%s quality=%s n=%s",
            "編輯" if image is not None else "生成", model, size, quality, batch_size,
        )

        if image is not None and size == "auto":
            logger.info(
                "[DMXAPI] size=auto 時輸出尺寸由上游決定，參考圖越大越可能產出大圖而變慢。"
                "若一直被上游切斷，改指定 1024x1024 這類明確尺寸。"
            )

        if dimensions and dimensions[0] * dimensions[1] > self.SLOW_SIZE_PIXELS:
            logger.warning(
                "[DMXAPI] size=%s 的像素量偏大，同步端點常來不及在上游約 60 秒的回應上限內"
                "回傳而被斷線。若失敗請改用 2048x1152 或 1024x1024。", size,
            )

        if quality == "auto":
            logger.info(
                "[DMXAPI] quality=auto 由上游決定（偏高品質）。同步端點只有約 60 秒，"
                "被切斷時降成 medium 或 low 是最有效的辦法。"
            )

        session = requests.Session()
        try:
            if image is not None:
                data = self._submit_edit(
                    prompt, model, size, quality, batch_size, image, token, session
                )
            else:
                data = self._submit_generation(
                    prompt, model, size, quality, batch_size, token, session
                )
        except RuntimeError as e:
            # common 的訊息是三個模組共用的（還提到影片長度），這裡補上圖像節點
            # 真正能調的旋鈕，免得使用者只看得到「改小尺寸」這一條路。
            if "被上游中斷" not in str(e):
                raise
            raise RuntimeError(
                str(e) + "\n[DMXAPI] 圖像節點可調的加速順序："
                "(1) quality 改 medium 或 low；"
                "(2) model 改 gpt-image-2-ssvip（官方標示回應較快）；"
                "(3) size 指定 1024x1024 而非 auto；"
                "(4) 縮短 prompt——多視角、多分鏈的描述會顯著拉長生成時間。"
            ) from e

        if not data.get("data"):
            message = data.get("error", {}).get("message", "未知錯誤")
            raise RuntimeError("[DMXAPI API Error] 回傳錯誤：" + message)

        pil_images = [fetch_image_item(item, session) for item in data["data"]]
        return (pil_to_tensor_batch(pil_images),)


NODE_CLASS_MAPPINGS = {
    "DMXAPI_GPT_Image2": DMXAPI_GPT_Image2,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DMXAPI_GPT_Image2": "DMXAPI GPT Image 2",
}
