"""
DMXAPI 設定 API Key

在畫布上直接把 DMXAPI_KEY 寫進與 dmxapi_common.py 同層的 .env，並立即同步到
目前這個 ComfyUI 進程的環境變數，不需要重啟就能讓其他節點用上新 Key。

畫面上的輸入框由 web/dmxapi_set_api_key.js 遮蔽成密碼樣式並在執行後自動清空，
但這只防止「看畫面的人」偷看到明碼——ComfyUI 節點欄位值本來就會存進 workflow
JSON、預設也會嵌進輸出圖片的 metadata，請勿分享填了真實 Key 的 workflow 檔或圖片。

只支援 DMXAPI_KEY（通用 fallback）；OPENAI_API_KEY／AGNES_API_KEY／MINIMAX_API_KEY
這幾個模組專屬 fallback 目前仍須手動編輯 .env。
"""

import os

from .dmxapi_common import logger, mask_secret, write_env_var

_ENV_VAR_NAME = "DMXAPI_KEY"


class DMXAPI_SetAPIKey:
    """把 DMXAPI_KEY 寫進 .env，並立即套用到目前的 ComfyUI 進程。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "api_key": ("STRING", {
                    "default": "", "multiline": False,
                    "tooltip": (
                        "寫入 .env 的 DMXAPI_KEY。畫面上會被前端遮蔽成密碼欄位，"
                        "但仍會存進 workflow JSON／輸出圖片 metadata；"
                        "請勿分享含有真實 Key 的 workflow 檔或圖片。"
                    ),
                }),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("REPORT",)
    FUNCTION = "set_key"
    CATEGORY = "DMXAPI/Utility"
    OUTPUT_NODE = True

    def set_key(self, api_key):
        if not api_key.strip():
            raise ValueError(
                "[DMXAPI Error] API Key 不能是空字串，若要清空請直接編輯 .env 檔案。"
            )

        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        write_env_var(env_path, _ENV_VAR_NAME, api_key)
        os.environ[_ENV_VAR_NAME] = api_key

        report = "[DMXAPI] 已寫入 .env：{0}={1}（已同步套用到目前 ComfyUI 進程，不需重啟）".format(
            _ENV_VAR_NAME, mask_secret(api_key)
        )
        logger.info(report)
        return {"ui": {"text": [report]}, "result": (report,)}


NODE_CLASS_MAPPINGS = {
    "DMXAPI_SetAPIKey": DMXAPI_SetAPIKey,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DMXAPI_SetAPIKey": "DMXAPI 設定 API Key",
}
