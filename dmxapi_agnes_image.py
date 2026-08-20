"""
DMXAPI Agnes Image 2.1 Flash 圖像生成節點

Sapiens AI 出品，走 OpenAI 相容的同步端點 /v1/images/generations，
提交後直接拿到影像。文生圖與圖生圖是**同一個端點**，差別只在有沒有帶
extra_body.image——這點跟 gpt-image-2 不同（那邊帶圖要改打 /v1/images/edits）。

官方文件：
  文生圖 https://doc.dmxapi.cn/agnes-image-21-flash-t2i.html
  圖生圖 https://doc.dmxapi.cn/agnes-image-21-flash-i2i.html
"""

import base64

import requests

from .dmxapi_common import (
    IMAGES_URL,
    fetch_image_item,
    logger,
    pil_to_tensor_batch,
    post_json,
    resolve_api_key,
    tensor_to_image_bytes,
)


class DMXAPI_Agnes_Image21Flash:
    """調用 DMXAPI Agnes Image 2.1 Flash 圖像生成介面。"""

    # 這個模型不收任意像素尺寸，只收「解析度檔位 + 寬高比」兩個下拉的組合，
    # 實際輸出尺寸由下面的對照表決定（值取自官方文件）。
    SIZE_TIERS = ["1K", "2K", "3K", "4K"]
    RATIOS = ["1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9"]

    # ratio → {檔位: (width, height)}，只用於在 log 中告知實際輸出尺寸，
    # 不參與請求組裝——真正決定尺寸的是上游。
    DIMENSIONS = {
        "1:1":  {"1K": (1024, 1024), "2K": (2048, 2048), "3K": (3072, 3072), "4K": (4096, 4096)},
        "3:4":  {"1K": (864, 1152),  "2K": (1728, 2304), "3K": (2592, 3456), "4K": (3456, 4608)},
        "4:3":  {"1K": (1152, 864),  "2K": (2304, 1728), "3K": (3456, 2592), "4K": (4608, 3456)},
        "16:9": {"1K": (1312, 736),  "2K": (2624, 1472), "3K": (3936, 2208), "4K": (5248, 2944)},
        "9:16": {"1K": (736, 1312),  "2K": (1472, 2624), "3K": (2208, 3936), "4K": (2944, 5248)},
        "2:3":  {"1K": (832, 1248),  "2K": (1664, 2496), "3K": (2496, 3744), "4K": (3328, 4992)},
        "3:2":  {"1K": (1248, 832),  "2K": (2496, 1664), "3K": (3744, 2496), "4K": (4992, 3328)},
        "21:9": {"1K": (1568, 672),  "2K": (3136, 1344), "3K": (4704, 2016), "4K": (6272, 2688)},
    }

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "model": (["agnes-image-2.1-flash"], {"default": "agnes-image-2.1-flash"}),
                "api_key": ("STRING", {"default": "", "multiline": False}),
                "size": (cls.SIZE_TIERS, {"default": "1K"}),
                "ratio": (cls.RATIOS, {"default": "1:1"}),
                # url 是上游預設；b64_json 省掉一次下載往返，但回應體積大得多。
                # 兩種格式 fetch_image_item() 都吃得下，維持上游預設即可。
                "response_format": (["url", "b64_json"], {"default": "url"}),
            },
            "optional": {
                "image": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("IMAGE",)
    FUNCTION = "generate_image"
    CATEGORY = "DMXAPI/Image"

    # 參考圖上傳前縮到的長邊上限。理由同 gpt-image-2 節點：上傳耗時是算在
    # 同步端點的回應上限裡的，原尺寸 PNG 光傳就可能把時間吃光。
    REFERENCE_MAX_SIDE = 2048

    # image 是陣列，多張即「多圖合成」。ComfyUI 的 IMAGE batch 有幾張就送幾張，
    # 但超過這個數量只會把請求體撐大又拖慢上傳，因此截斷並示警。
    MAX_REFERENCE_IMAGES = 5

    # 官方建議客戶端逾時抓 60~360 秒。3K / 4K 明顯較慢，分兩段給。
    TIMEOUT_BY_SIZE = {"1K": 180, "2K": 180, "3K": 300, "4K": 300}

    def _encode_references(self, image):
        """IMAGE tensor batch → data URI 陣列（供 extra_body.image 使用）。

        tensor_to_image_bytes() 只取 batch 第一張，所以這裡逐張切片後再呼叫。
        一律 JPEG：同內容下 base64 體積遠小於 PNG，而參考用途不需要無損。
        """
        count = int(image.shape[0])
        if count > self.MAX_REFERENCE_IMAGES:
            logger.warning(
                "[DMXAPI] image 收到 %s 張，只會送出前 %s 張作為參考圖。",
                count, self.MAX_REFERENCE_IMAGES,
            )
            count = self.MAX_REFERENCE_IMAGES

        data_urls = []
        total = 0
        for index in range(count):
            raw, mime = tensor_to_image_bytes(
                image[index:index + 1], fmt="JPEG", quality=95,
                max_side=self.REFERENCE_MAX_SIDE,
            )
            total += len(raw)
            data_urls.append("data:" + mime + ";base64," + base64.b64encode(raw).decode("utf-8"))

        logger.info(
            "[DMXAPI] 附帶 %s 張參考圖，走圖生圖模式（共 %.1f KB）",
            len(data_urls), total / 1024.0,
        )
        return data_urls

    def generate_image(self, prompt, model, api_key, size, ratio, response_format, image=None):
        if not prompt.strip():
            raise ValueError("[DMXAPI Error] Prompt 不能為空！")

        token = resolve_api_key(api_key, "AGNES_API_KEY")

        # image 與 response_format 都必須包在 extra_body 內，放到頂層會被上游拒絕
        # （官方文件明文標注）。
        extra_body = {"response_format": response_format}
        payload = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "ratio": ratio,
            "extra_body": extra_body,
        }

        if image is not None:
            extra_body["image"] = self._encode_references(image)

        width, height = self.DIMENSIONS[ratio][size]
        logger.info(
            "[DMXAPI] 提交 Agnes %s model=%s size=%s ratio=%s（約 %sx%s）format=%s",
            "圖生圖" if image is not None else "文生圖",
            model, size, ratio, width, height, response_format,
        )

        session = requests.Session()
        try:
            data = post_json(
                IMAGES_URL, payload, token,
                timeout=self.TIMEOUT_BY_SIZE.get(size, 180), session=session,
            )
        except RuntimeError as e:
            # common 的斷線訊息是三個模組共用的，這裡補上本節點真正能調的旋鈕。
            if "被上游中斷" not in str(e):
                raise
            raise RuntimeError(
                str(e) + "\n[DMXAPI] Agnes 節點可調的加速順序："
                "(1) size 降一個檔位（4K → 2K → 1K）；"
                "(2) 減少或縮小參考圖；"
                "(3) 縮短 prompt。"
            ) from e

        if not data.get("data"):
            message = data.get("error", {}).get("message", "未知錯誤")
            raise RuntimeError("[DMXAPI API Error] 回傳錯誤：" + message)

        pil_images = [fetch_image_item(item, session) for item in data["data"]]
        return (pil_to_tensor_batch(pil_images),)


NODE_CLASS_MAPPINGS = {
    "DMXAPI_Agnes_Image21Flash": DMXAPI_Agnes_Image21Flash,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DMXAPI_Agnes_Image21Flash": "DMXAPI Agnes Image 2.1 Flash",
}
