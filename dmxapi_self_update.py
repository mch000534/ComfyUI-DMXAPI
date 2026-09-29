"""
DMXAPI 節點自我更新

在畫布上直接對比並套用套件最新版（來自 GitHub `main` 分支的 zip 快照），
取代手動執行 install_DMXAPI_node_NOgit.command／.bat。跟那兩支外部安裝腳本
不同的是：這裡的 apply 會保留 .env 與 .git，不會整包覆蓋洗掉。

兩段式設計：
  check_only（預設）：只比對本機與遠端版本，絕對不寫入任何檔案。
  apply：下載並套用更新，需要重新啟動 ComfyUI 才會生效。

刻意不定義 IS_CHANGED——沿用 ComfyUI 預設的輸入雜湊快取，同一組輸入不會重跑，
避免每次 Queue 都打 GitHub，甚至誤觸 apply。run_trigger 純粹是「改了才重跑」的
快取鍵，跟 dmxapi_minimax_h3_nodes.py 的 noise_seed 用途一樣。

全程只用 Python 標準庫操作檔案（zipfile／shutil／tempfile／os.replace），
不 shell out 到 unzip/curl，確保 macOS 與 Windows 都能一致運作。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone

import requests

from .dmxapi_common import logger

DEFAULT_REPO = "mch000534/ComfyUI-DMXAPI"
DEFAULT_BRANCH = "main"
STATE_FILENAME = ".dmxapi_update_state.json"

# 這些名字在「本機有、新版本沒有」的比對裡直接跳過（不警告、不搬移）：
# .env／.git 已經在保留步驟另外處理；__pycache__ 是可重新產生的位元組碼快取；
# .DS_Store 是 macOS Finder 的中繼檔；STATE_FILENAME 反正等一下會被覆寫。
PRESERVE_NAMES = {".env", ".git", STATE_FILENAME, "__pycache__", ".DS_Store"}


def _get_with_retry(url, headers, timeout_seconds, max_retries, stream=False):
    """通用 GET 重試迴圈，供 GitHub commits API 與 zip 下載共用。

    這是專門給 GitHub（不需認證的一般服務）用的重試邏輯，刻意不重用
    dmxapi_common 裡 DMXAPI 專用、POST-only 的 _post/_post_with_auth_fallback——
    那邊有一整套 401/429/60 秒重複計費的語意，這裡完全不適用。
    """
    last_response = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout_seconds, stream=stream)
        except requests.RequestException as error:
            logger.warning("[DMXAPI] 連線 %s 失敗（第 %s 次）：%s", url, attempt + 1, error)
            resp = None
        else:
            if resp.status_code == 200:
                return resp
            last_response = resp
            if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
                # GitHub 未登入 API 的速率限制（每小時 60 次，per IP）——同一分鐘內
                # 重試也不會恢復，直接放棄，不要浪費退避時間硬試滿 max_retries 次。
                logger.warning("[DMXAPI] %s 已達 GitHub API 速率限制，不再重試。", url)
                return resp
            logger.warning("[DMXAPI] %s 回應 %s，稍後重試", url, resp.status_code)
        if attempt < max_retries:
            time.sleep(2 ** attempt)
    return last_response


def _format_rate_limit_reset(reset_header_value):
    """把 X-RateLimit-Reset（Unix 秒數）換算成人看得懂的剩餘時間；解析失敗回傳 None。"""
    try:
        reset_epoch = int(reset_header_value)
    except (TypeError, ValueError):
        return None
    remaining_seconds = max(0, reset_epoch - time.time())
    remaining_minutes = int(remaining_seconds // 60) + (1 if remaining_seconds % 60 else 0)
    reset_clock = time.strftime("%H:%M UTC", time.gmtime(reset_epoch))
    return "約 {0} 分鐘後（{1}）恢復".format(remaining_minutes, reset_clock)


def _fetch_remote_sha(repo, branch, timeout_seconds, max_retries):
    """取得 GitHub 上最新 commit sha。不需要認證，也不需要本機裝 git。

    回傳 (sha, error_message)：成功時 error_message 是 None；失敗時 sha 是 None，
    error_message 會說明原因（連不上／GitHub API 額度用完／回應格式異常），
    供 report 顯示，不要只留在 log 裡讓使用者看不到。
    """
    url = "https://api.github.com/repos/{0}/commits/{1}".format(repo, branch)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ComfyUI-DMXAPI-SelfUpdate"}
    resp = _get_with_retry(url, headers, timeout_seconds, max_retries)

    if resp is None:
        message = "無法連線 GitHub，請檢查網路。"
        logger.warning("[DMXAPI] %s", message)
        return None, message

    if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
        message = "GitHub API 額度已用完（未登入每小時上限 60 次，同一網路的其他人共用同一額度）"
        reset_text = _format_rate_limit_reset(resp.headers.get("X-RateLimit-Reset"))
        if reset_text:
            message += "，" + reset_text
        logger.warning("[DMXAPI] %s", message)
        return None, message

    if resp.status_code != 200:
        message = "GitHub API 回應 {0}。".format(resp.status_code)
        logger.warning("[DMXAPI] %s", message)
        return None, message

    try:
        return resp.json()["sha"], None
    except (ValueError, KeyError) as error:
        message = "GitHub API 回傳格式異常：{0}".format(error)
        logger.warning("[DMXAPI] %s", message)
        return None, message


def _state_path(package_dir):
    return os.path.join(package_dir, STATE_FILENAME)


def _read_state(package_dir):
    path = _state_path(package_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as error:
        logger.warning("[DMXAPI] 讀取 %s 失敗：%s", path, error)
        return None


def _write_state(package_dir, sha, source):
    payload = {
        "last_applied_sha": sha,
        "applied_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
    }
    with open(_state_path(package_dir), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _read_local_sha(package_dir):
    """本機版本判斷順序：git HEAD（若是 git 安裝）> 本機標記檔 > 未知。"""
    git_dir = os.path.join(package_dir, ".git")
    if os.path.isdir(git_dir) and shutil.which("git"):
        try:
            result = subprocess.run(
                ["git", "-C", package_dir, "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                sha = result.stdout.strip()
                if sha:
                    return sha, "git"
        except (OSError, subprocess.SubprocessError) as error:
            logger.warning("[DMXAPI] 讀取 git HEAD 失敗：%s", error)

    state = _read_state(package_dir)
    if state and state.get("last_applied_sha"):
        return state["last_applied_sha"], "marker"

    return None, "unknown"


def _zip_urls(repo, branch):
    # 跟 install_DMXAPI_node_NOgit.command 的兩個來源一致：優先 codeload（較穩定的
    # 直接下載主機），失敗再 fallback 到 github.com 的 archive 連結。
    return [
        "https://codeload.github.com/{0}/zip/refs/heads/{1}".format(repo, branch),
        "https://github.com/{0}/archive/refs/heads/{1}.zip".format(repo, branch),
    ]


def _download_zip(repo, branch, dest_path, timeout_seconds, max_retries):
    headers = {"User-Agent": "ComfyUI-DMXAPI-SelfUpdate"}
    for url in _zip_urls(repo, branch):
        resp = _get_with_retry(url, headers, timeout_seconds, max_retries, stream=True)
        if resp is None or resp.status_code != 200:
            logger.warning("[DMXAPI] 下載 %s 失敗，改試下一個來源。", url)
            continue
        with open(dest_path, "wb") as handle:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if chunk:
                    handle.write(chunk)
        if os.path.getsize(dest_path) > 0 and zipfile.is_zipfile(dest_path):
            return True
        logger.warning("[DMXAPI] %s 下載內容不是有效 zip，改試下一個來源。", url)
    return False


def _extract_source_dir(zip_path, extract_dir):
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract_dir)

    top_level = [
        name for name in os.listdir(extract_dir)
        if os.path.isdir(os.path.join(extract_dir, name))
    ]
    if len(top_level) != 1:
        raise RuntimeError("解壓縮後找不到唯一的頂層資料夾，zip 可能已損毀。")

    source_dir = os.path.join(extract_dir, top_level[0])
    if not os.path.isfile(os.path.join(source_dir, "__init__.py")) or \
            not os.path.isfile(os.path.join(source_dir, "dmxapi_common.py")):
        raise RuntimeError("解壓縮後的內容缺少 __init__.py／dmxapi_common.py，zip 可能不完整。")

    return source_dir


def _move_with_retry(src, dst, attempts=5, base_delay=0.5):
    """包了重試的 shutil.move，用來扛過 Windows 上防毒軟體／索引服務造成的短暫檔案鎖定。

    macOS/Linux 幾乎不會遇到這個狀況，但寫成通用重試不需要分平台判斷。
    """
    last_error = None
    for attempt in range(attempts):
        try:
            shutil.move(src, dst)
            return
        except OSError as error:
            last_error = error
            logger.warning(
                "[DMXAPI] 搬移 %s → %s 失敗（第 %s 次）：%s", src, dst, attempt + 1, error
            )
            time.sleep(base_delay * (attempt + 1))
    raise last_error


def _populate_stage_dir(source_dir, stage_dir, package_dir):
    """把下載內容複製進 stage_dir，並保留現有的 .env／.git。

    這裡一律用複製（不是搬移）：source_dir 在系統暫存目錄，跟 stage_dir 所在的
    custom_nodes/ 不一定同一個檔案系統（Windows 常見 C:/D: 分屬不同磁碟）。
    """
    for entry in os.listdir(source_dir):
        src = os.path.join(source_dir, entry)
        dst = os.path.join(stage_dir, entry)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)

    env_path = os.path.join(package_dir, ".env")
    if os.path.isfile(env_path):
        shutil.copy2(env_path, os.path.join(stage_dir, ".env"))

    git_path = os.path.join(package_dir, ".git")
    if os.path.isdir(git_path):
        shutil.copytree(git_path, os.path.join(stage_dir, ".git"))


def _preserve_local_only_names(package_dir, stage_dir):
    """把「本機有、新版本沒有」的檔案原樣搬進 stage_dir，不砍使用者的本機自訂內容。"""
    existing_names = set(os.listdir(package_dir))
    staged_names = set(os.listdir(stage_dir))
    preserved = []
    for name in sorted(existing_names - staged_names):
        if name in PRESERVE_NAMES:
            continue
        src = os.path.join(package_dir, name)
        dst = os.path.join(stage_dir, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        preserved.append(name)
        logger.warning(
            "[DMXAPI] 本機檔案 %s 不在最新版本裡，已原樣保留（不會被更新，也不會被刪除）。",
            name,
        )
    return preserved


def _install_requirements(requirements_path):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", requirements_path],
            capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning("[DMXAPI] 安裝依賴失敗：%s", error)
        return "安裝依賴套件時發生錯誤，請手動執行 pip install -r requirements.txt。"

    if result.returncode != 0:
        logger.warning("[DMXAPI] pip install 失敗：%s", result.stderr)
        return "安裝依賴套件失敗，請手動執行 pip install -r requirements.txt（詳見日誌）。"

    logger.info("[DMXAPI] 已重新安裝依賴套件。")
    return "已重新安裝依賴套件，若版本有異動請務必重啟 ComfyUI。"


def _build_report(mode, local_sha, remote_sha, update_available, applied, extra=""):
    lines = [
        "[DMXAPI] 自我更新（{0}）".format(mode),
        "本機版本：{0}".format(local_sha or "未知"),
        "遠端版本：{0}".format(remote_sha or "無法取得"),
        "是否有更新：{0}".format("是" if update_available else "否"),
    ]
    if mode == "apply":
        lines.append("是否已套用：{0}".format("是" if applied else "否"))
    if extra:
        lines.append(extra)
    return "\n".join(lines)


def _apply_update(package_dir, repo, branch, local_sha, remote_sha,
                   install_requirements, timeout_seconds, max_retries):
    parent_dir = os.path.dirname(package_dir)
    package_name = os.path.basename(package_dir)

    download_dir = tempfile.mkdtemp(prefix="dmxapi-selfupdate-")
    stage_dir = None
    try:
        zip_path = os.path.join(download_dir, "repo.zip")
        if not _download_zip(repo, branch, zip_path, timeout_seconds, max_retries):
            return False, _build_report(
                "apply", local_sha, remote_sha, True, False,
                extra="無法從 GitHub 下載更新（zip 下載失敗），未做任何變更。",
            )

        extract_dir = os.path.join(download_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        source_dir = _extract_source_dir(zip_path, extract_dir)

        stage_dir = tempfile.mkdtemp(dir=parent_dir, prefix="." + package_name + ".new.")
        _populate_stage_dir(source_dir, stage_dir, package_dir)

        pip_note = ""
        if install_requirements:
            requirements_path = os.path.join(stage_dir, "requirements.txt")
            if os.path.isfile(requirements_path):
                pip_note = _install_requirements(requirements_path)

        warnings = _preserve_local_only_names(package_dir, stage_dir)
    except Exception as error:
        if stage_dir and os.path.isdir(stage_dir):
            shutil.rmtree(stage_dir, ignore_errors=True)
        shutil.rmtree(download_dir, ignore_errors=True)
        logger.warning("[DMXAPI] 準備更新內容時失敗：%s", error)
        return False, _build_report(
            "apply", local_sha, remote_sha, True, False,
            extra="準備更新內容時失敗，未做任何變更：{0}".format(error),
        )

    shutil.rmtree(download_dir, ignore_errors=True)

    backup_dir = tempfile.mkdtemp(dir=parent_dir, prefix="." + package_name + ".backup.")
    os.rmdir(backup_dir)

    try:
        _move_with_retry(package_dir, backup_dir)
    except OSError as error:
        shutil.rmtree(stage_dir, ignore_errors=True)
        return False, _build_report(
            "apply", local_sha, remote_sha, True, False,
            extra="無法備份現有版本，未做任何變更：{0}".format(error),
        )

    try:
        _move_with_retry(stage_dir, package_dir)
    except OSError as error:
        _move_with_retry(backup_dir, package_dir)
        return False, _build_report(
            "apply", local_sha, remote_sha, True, False,
            extra="套用更新時搬移檔案失敗，已還原成更新前的版本：{0}".format(error),
        )

    shutil.rmtree(backup_dir, ignore_errors=True)
    _write_state(
        package_dir, remote_sha,
        "git" if os.path.isdir(os.path.join(package_dir, ".git")) else "marker",
    )

    extra = "已套用更新，需要重新啟動 ComfyUI 才會生效。"
    if warnings:
        extra += " 有 {0} 個本機檔案不在新版本裡，已原樣保留：{1}".format(
            len(warnings), ", ".join(warnings)
        )
    if pip_note:
        extra += " " + pip_note

    return True, _build_report("apply", remote_sha, remote_sha, True, True, extra=extra)


class DMXAPI_SelfUpdate:
    """檢查／套用套件最新版（來自 GitHub main 分支），保留 .env 與 .git。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode": (["check_only", "apply"], {
                    "default": "check_only",
                    "tooltip": (
                        "check_only：只比對本機與遠端版本，絕不動任何檔案。"
                        "apply：下載並套用更新（保留 .env 與 .git），需要重啟 ComfyUI 才會生效。"
                    ),
                }),
                "run_trigger": ("INT", {
                    "default": 0, "min": 0, "max": 0xffffffffffffffff,
                    "tooltip": (
                        "純粹作為 ComfyUI 快取鍵，本身無意義。"
                        "同一組輸入不會重跑；要強制重新檢查／套用，請改動這個數字。"
                    ),
                }),
            },
            "optional": {
                "repo_owner_repo": ("STRING", {
                    "default": DEFAULT_REPO,
                    "tooltip": "格式 owner/repo，測試或 fork 情境可覆蓋，一般不需更動。",
                }),
                "branch": ("STRING", {"default": DEFAULT_BRANCH}),
                "install_requirements": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "apply 成功後是否對目前這個 ComfyUI 直譯器執行 pip install -r requirements.txt。",
                }),
                "timeout_seconds": ("INT", {"default": 20, "min": 5, "max": 120}),
                "max_retries": ("INT", {"default": 3, "min": 0, "max": 10}),
            },
        }

    RETURN_TYPES = ("STRING", "BOOLEAN", "BOOLEAN", "STRING", "STRING")
    RETURN_NAMES = ("REPORT", "UPDATE_AVAILABLE", "APPLIED", "LOCAL_SHA", "REMOTE_SHA")
    FUNCTION = "run"
    CATEGORY = "DMXAPI/Utility"
    OUTPUT_NODE = True

    def run(self, mode, run_trigger, repo_owner_repo=DEFAULT_REPO, branch=DEFAULT_BRANCH,
            install_requirements=True, timeout_seconds=20, max_retries=3):
        package_dir = os.path.dirname(os.path.abspath(__file__))
        local_sha, _source = _read_local_sha(package_dir)
        remote_sha, remote_error = _fetch_remote_sha(repo_owner_repo, branch, timeout_seconds, max_retries)
        update_available = bool(remote_sha) and remote_sha != local_sha

        if mode == "check_only" or not update_available:
            report = _build_report(
                mode, local_sha, remote_sha, update_available, False, extra=remote_error or "",
            )
            return {
                "ui": {"text": [report]},
                "result": (report, update_available, False, local_sha or "", remote_sha or ""),
            }

        applied, report = _apply_update(
            package_dir, repo_owner_repo, branch, local_sha, remote_sha,
            install_requirements, timeout_seconds, max_retries,
        )
        return {
            "ui": {"text": [report]},
            "result": (report, update_available, applied, local_sha or "", remote_sha or ""),
        }


NODE_CLASS_MAPPINGS = {
    "DMXAPI_SelfUpdate": DMXAPI_SelfUpdate,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DMXAPI_SelfUpdate": "DMXAPI 節點自我更新",
}
