"""
DMXAPI 設定畫面用的後端 HTTP 路由

讓 ComfyUI「設定」畫面（web/dmxapi_set_api_key.js、web/dmxapi_self_update.js 註冊的
settings 項目）能讀取／寫入 DMXAPI_KEY、檢查／套用套件更新，分別跟
DMXAPI_SetAPIKey／DMXAPI_SelfUpdate 兩個節點共用同一套底層邏輯（
dmxapi_common.write_env_var()、dmxapi_self_update 的 check/apply 函式）——
不管使用者從節點還是設定畫面操作，效果完全一致。

跟設定畫面比，節點的 widget 值會被存進 workflow JSON、預設也會嵌進輸出圖片的
metadata；這裡的值只會送到本機 .env／套件目錄，不會有這個外洩管道。

server/aiohttp 都只在真正跑在 ComfyUI 進程裡才 import 得到——冒煙測試與離線
unittest 是用 importlib 在裸 Python 下載入 __init__.py，這時候 server 模組
根本不存在。這裡延遲＋包 try/except import，失敗就整個略過路由註冊，理由跟
dmxapi_common.py 裡 comfy_api 的延遲 import 慣例一樣：絕不能讓套件在裸 Python
環境下 import 失敗。
"""

import os

try:
    from server import PromptServer
except ImportError:
    PromptServer = None

try:
    from aiohttp import web
except ImportError:
    web = None

from .dmxapi_common import logger, mask_secret, write_env_var
from .dmxapi_self_update import (
    DEFAULT_BRANCH,
    DEFAULT_REPO,
    _apply_update,
    _build_report,
    _fetch_remote_sha,
    _read_local_sha,
)

_ENV_VAR_NAME = "DMXAPI_KEY"


def _package_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _env_path():
    return os.path.join(_package_dir(), ".env")


async def _get_api_key(request):
    current = os.environ.get(_ENV_VAR_NAME, "")
    return web.json_response({"masked": mask_secret(current) if current else None})


async def _set_api_key(request):
    body = await request.json()
    value = (body or {}).get("value", "")
    if not isinstance(value, str) or not value.strip():
        return web.json_response(
            {"error": "[DMXAPI Error] API Key 不能是空字串。"}, status=400,
        )

    write_env_var(_env_path(), _ENV_VAR_NAME, value)
    os.environ[_ENV_VAR_NAME] = value
    logger.info(
        "[DMXAPI] 已透過設定畫面寫入 .env：%s=%s", _ENV_VAR_NAME, mask_secret(value)
    )
    return web.json_response({"masked": mask_secret(value)})


async def _check_for_update(request):
    """對應設定畫面的「檢查更新」開關；GET，絕不寫入任何檔案。"""
    package_dir = _package_dir()
    local_sha, _source = _read_local_sha(package_dir)
    remote_sha, remote_error = _fetch_remote_sha(DEFAULT_REPO, DEFAULT_BRANCH, timeout_seconds=20, max_retries=3)
    update_available = bool(remote_sha) and remote_sha != local_sha
    report = _build_report(
        "check_only", local_sha, remote_sha, update_available, False, extra=remote_error or "",
    )
    return web.json_response({
        "report": report,
        "update_available": update_available,
        "local_sha": local_sha or "",
        "remote_sha": remote_sha or "",
    })


async def _apply_update_route(request):
    """對應設定畫面的「套用更新」開關；POST，會下載並覆蓋套件程式碼（保留 .env／.git）。

    前端在呼叫這裡之前應該已經跳出確認對話框——這裡不再另外要求任何確認參數，
    照樣直接執行，跟 DMXAPI_SelfUpdate 節點的 mode="apply" 是同一套邏輯。
    """
    package_dir = _package_dir()
    local_sha, _source = _read_local_sha(package_dir)
    remote_sha, remote_error = _fetch_remote_sha(DEFAULT_REPO, DEFAULT_BRANCH, timeout_seconds=20, max_retries=3)
    update_available = bool(remote_sha) and remote_sha != local_sha

    if not update_available:
        report = _build_report(
            "apply", local_sha, remote_sha, update_available, False, extra=remote_error or "",
        )
        return web.json_response({"applied": False, "report": report})

    applied, report = _apply_update(
        package_dir, DEFAULT_REPO, DEFAULT_BRANCH, local_sha, remote_sha,
        install_requirements=True, timeout_seconds=20, max_retries=3,
    )
    return web.json_response({"applied": applied, "report": report})


def register_routes():
    """在 ComfyUI 進程內註冊設定畫面用的 HTTP 路由；不在 ComfyUI 內就略過。"""
    if PromptServer is None or web is None or getattr(PromptServer, "instance", None) is None:
        logger.info(
            "[DMXAPI] 不在 ComfyUI 進程內（或 PromptServer 尚未就緒），"
            "略過設定畫面用的 HTTP 路由。"
        )
        return

    routes = PromptServer.instance.routes
    routes.get("/dmxapi/api_key")(_get_api_key)
    routes.post("/dmxapi/api_key")(_set_api_key)
    routes.get("/dmxapi/self_update/check")(_check_for_update)
    routes.post("/dmxapi/self_update/apply")(_apply_update_route)
