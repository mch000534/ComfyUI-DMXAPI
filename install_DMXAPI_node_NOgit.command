#!/bin/zsh
#
# ComfyUI-DMXAPI 安裝檔（macOS，不需要 Git）
#
# 請不要雙擊本檔。傳到其他 Mac 時執行權限常會丟失，Finder 會顯示「沒有適當的取用權限」。
# 請改雙擊同一層的「install_DMXAPI_node_NOgit.app」（顯示名稱：安裝 ComfyUI-DMXAPI）。
#
# 若只有本 .command 檔，請打開「終端機」，把本檔拖進去，在路徑前面加上「zsh 」再按 Enter：
#           zsh ~/Downloads/install_DMXAPI_node_NOgit.command
# 或先補上執行權限後再雙擊：
#           chmod +x ~/Downloads/install_DMXAPI_node_NOgit.command
#           xattr -cr ~/Downloads/install_DMXAPI_node_NOgit.command
#
# 也可指定 ComfyUI 根目錄：
#   COMFYUI_ROOT="/你的/ComfyUI路徑" zsh ./install_DMXAPI_node_NOgit.command

# 被 bash / sh 啟動時改用 zsh（沒有 +x 時也能用「zsh 本檔」執行）
if [ -z "${ZSH_VERSION-}" ]; then
    exec /bin/zsh "$0" "$@"
fi

set -euo pipefail

readonly REPO_URL="https://github.com/mch000534/ComfyUI-DMXAPI/archive/refs/heads/main.zip"
readonly REPO_URL_FALLBACK="https://codeload.github.com/mch000534/ComfyUI-DMXAPI/zip/refs/heads/main"
readonly NODE_NAME="ComfyUI-DMXAPI"
readonly EXTRACTED_FOLDER_NAME="ComfyUI-DMXAPI-main"
readonly ZIP_NAME="ComfyUI-DMXAPI-main.zip"
readonly DESKTOP_CONFIG="$HOME/Library/Application Support/ComfyUI/config.json"
readonly DESKTOP_MODELS_CONFIG="$HOME/Library/Application Support/ComfyUI/extra_models_config.yaml"

WORK_DIR=""
STAGE_ROOT=""
BACKUP_DIR=""
PAUSED=0

is_interactive() {
    [[ -t 0 ]] && [[ -z "${DMXAPI_INSTALL_NONINTERACTIVE:-}" ]]
}

pause_if_needed() {
    if [[ "$PAUSED" -eq 1 ]]; then
        return 0
    fi
    if ! is_interactive; then
        return 0
    fi
    PAUSED=1
    print -- ""
    print -n -- "按 Enter 關閉視窗..."
    read -r _ < /dev/tty 2>/dev/null || read -r _ || true
}

cleanup() {
    if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
        rm -rf "$WORK_DIR" || true
    fi
    if [[ -n "$STAGE_ROOT" && -d "$STAGE_ROOT" ]]; then
        rm -rf "$STAGE_ROOT" || true
    fi
    pause_if_needed
}

interrupt() {
    exit 130
}

trap cleanup EXIT
trap interrupt INT TERM

die() {
    print -u2 -- "[錯誤] $*"
    exit 1
}

info() {
    print -- "[資訊] $*"
}

warn() {
    print -u2 -- "[警告] $*"
}

heal_self() {
    local self="${1:-$0}"
    case "$self" in
        /*) ;;
        *) self="$PWD/$self" ;;
    esac
    [[ -f "$self" ]] || return 0
    chmod +x "$self" 2>/dev/null || true
    if command -v xattr >/dev/null 2>&1; then
        xattr -d com.apple.quarantine "$self" 2>/dev/null || true
    fi
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "找不到必要指令：$1"
}

json_basepath() {
    local cfg="$1"
    local line
    [[ -f "$cfg" ]] || return 1
    line="$(grep -E '"basePath"' "$cfg" 2>/dev/null | head -n 1 || true)"
    [[ -n "$line" ]] || return 1
    line="${line#*\"basePath\"}"
    line="${line#*:}"
    line="${line#*\"}"
    line="${line%%\"*}"
    [[ -n "$line" ]] || return 1
    print -r -- "$line"
}

yaml_basepath() {
    local cfg="$1"
    local line
    [[ -f "$cfg" ]] || return 1
    line="$(grep -E '^[[:space:]]*base_path:' "$cfg" 2>/dev/null | head -n 1 || true)"
    [[ -n "$line" ]] || return 1
    line="${line#*base_path:}"
    line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    line="${line#[\'\"]}"
    line="${line%[\'\"]}"
    [[ -n "$line" ]] || return 1
    print -r -- "$line"
}

expand_user_path() {
    local path="$1"
    path="${path/#\~/$HOME}"
    print -r -- "$path"
}

looks_like_comfy_root() {
    local root="$1"
    [[ -d "$root" ]] || return 1
    [[ -d "$root/custom_nodes" ]] && return 0
    [[ -e "$root/main.py" ]] && return 0
    [[ -d "$root/models" && -d "$root/input" ]] && return 0
    [[ -d "$root/.venv" || -d "$root/venv" ]] && return 0
    [[ -d "$root/python_embeded" || -d "$root/python_embedded" ]] && return 0
    return 1
}

venv_is_blocked() {
    local root="$1"
    local venv="$root/.venv"
    [[ -e "$venv" ]] || return 1
    if ls "$venv/bin" >/dev/null 2>&1; then
        return 1
    fi
    return 0
}

find_comfy_python() {
    local comfy_root="$1"
    local candidate
    local -a candidates

    candidates=(
        "$comfy_root/.venv/bin/python"
        "$comfy_root/.venv/bin/python3"
        "$comfy_root/venv/bin/python"
        "$comfy_root/venv/bin/python3"
        "$comfy_root/python_embeded/python"
        "$comfy_root/python_embeded/bin/python"
        "$comfy_root/python_embedded/python"
        "$comfy_root/python_embedded/bin/python"
    )

    for candidate in "${candidates[@]}"; do
        if [[ -x "$candidate" ]]; then
            print -r -- "$candidate"
            return 0
        fi
    done

    return 1
}

print_venv_help() {
    warn "偵測到 ComfyUI 的 .venv，但 macOS 不允許「終端機」讀取（App 管理／沙盒）。"
    warn "請到：系統設定 → 隱私權與安全性 → App 管理（或完整磁碟取用權）"
    warn "開啟「終端機」權限後，再執行一次本安裝檔。"
}

pick_comfy_root() {
    local chosen
    chosen="$(osascript <<'APPLESCRIPT'
try
    POSIX path of (choose folder with prompt "請選取 ComfyUI 根目錄（裡面通常有 custom_nodes 資料夾）")
on error
    return ""
end try
APPLESCRIPT
    )" || true
    chosen="${chosen%%$'\r'}"
    chosen="${chosen%%$'\n'}"
    chosen="${chosen%/}"
    [[ -n "$chosen" && -d "$chosen" ]] || return 1
    print -r -- "$chosen"
}

collect_root_candidates() {
    local -a found
    local path

    found=()

    path="$(json_basepath "$DESKTOP_CONFIG" || true)"
    if [[ -n "$path" ]]; then
        found+=("$(expand_user_path "$path")")
    fi

    path="$(yaml_basepath "$DESKTOP_MODELS_CONFIG" || true)"
    if [[ -n "$path" ]]; then
        found+=("$(expand_user_path "$path")")
    fi

    found+=(
        "$HOME/Documents/ComfyUI"
        "$HOME/ComfyUI"
        "$HOME/Library/Application Support/ComfyUI"
        "$HOME/Desktop/ComfyUI"
    )

    print -r -- "${(F)found}"
}

choose_from_candidates() {
    local candidate
    local -A seen_map
    seen_map=()

    while IFS= read -r candidate; do
        [[ -n "$candidate" ]] || continue
        [[ -z "${seen_map[$candidate]:-}" ]] || continue
        seen_map[$candidate]=1
        if looks_like_comfy_root "$candidate" || find_comfy_python "$candidate" >/dev/null; then
            print -r -- "$candidate"
            return 0
        fi
    done

    return 1
}

validate_comfy_root() {
    local root="$1"
    find_comfy_python "$root" >/dev/null && return 0
    looks_like_comfy_root "$root" && return 0
    venv_is_blocked "$root" && return 0
    return 1
}

find_comfy_root() {
    local configured_root="${COMFYUI_ROOT:-}"
    local candidate

    if [[ -n "$configured_root" ]]; then
        configured_root="$(expand_user_path "$configured_root")"
        [[ -d "$configured_root" ]] || die "COMFYUI_ROOT 不存在：$configured_root"
        validate_comfy_root "$configured_root" \
            || die "COMFYUI_ROOT 沒有 ComfyUI Python，也不像 ComfyUI 目錄：$configured_root"
        print -r -- "$configured_root"
        return 0
    fi

    if candidate="$(collect_root_candidates | choose_from_candidates)"; then
        print -r -- "$candidate"
        return 0
    fi

    if is_interactive; then
        info "無法自動找到 ComfyUI，請手動選取根目錄。"
        if candidate="$(pick_comfy_root)"; then
            validate_comfy_root "$candidate" \
                || die "選取的目錄沒有 ComfyUI Python，也不像 ComfyUI 目錄：$candidate"
            print -r -- "$candidate"
            return 0
        fi
        die "已取消選取 ComfyUI 目錄。"
    fi

    die "找不到 ComfyUI。請設定 COMFYUI_ROOT 後再執行，例如：
  COMFYUI_ROOT=\"\$HOME/Documents/ComfyUI\" zsh \"$0\""
}

ask_replace() {
    local choice
    if ! is_interactive; then
        die "目標資料夾已存在，非互動模式無法確認是否覆蓋。請雙擊本檔或在終端機執行。"
    fi
    print -- "[資訊] 資料夾『$NODE_NAME』已經存在。"
    print -- "請選擇：1. 重新下載並覆蓋（更新）  2. 取消"
    print -n -- "請輸入 1 或 2："
    read -r choice < /dev/tty 2>/dev/null || read -r choice || true
    if [[ "$choice" != "1" ]]; then
        info "已取消安裝。"
        exit 0
    fi
    info "驗證新檔案後，會取代現有資料夾。"
}

download_zip() {
    local zip_path="$1"
    local url
    local -a urls
    urls=("$REPO_URL" "$REPO_URL_FALLBACK")

    for url in "${urls[@]}"; do
        info "正在下載：$url"
        if curl -fL --connect-timeout 20 --retry 3 --retry-delay 2 --silent --show-error "$url" -o "$zip_path"; then
            if unzip -t "$zip_path" >/dev/null 2>&1; then
                return 0
            fi
            warn "下載的檔案不是有效 ZIP，改試下一個來源。"
        else
            warn "下載失敗，改試下一個來源。"
        fi
    done

    die "無法從 GitHub 下載安裝包。請檢查網路後再試。"
}

SCRIPT_PATH="$0"
case "$SCRIPT_PATH" in
    /*) ;;
    *) SCRIPT_PATH="$PWD/$SCRIPT_PATH" ;;
esac
heal_self "$SCRIPT_PATH"
cd "$HOME"

print -- "=========================================="
print -- " 自動安裝 ComfyUI-DMXAPI（macOS，不需 Git）"
print -- "=========================================="
print -- ""
print -- "若剛才是被系統擋住才改用終端機執行，這是正常的。"
print -- ""

require_command curl
require_command unzip
require_command mktemp

COMFY_ROOT="$(find_comfy_root)"
TARGET_DIR="$COMFY_ROOT/custom_nodes"
NODE_DIR="$TARGET_DIR/$NODE_NAME"
COMFY_PYTHON="$(find_comfy_python "$COMFY_ROOT" || true)"

info "ComfyUI 根目錄：$COMFY_ROOT"
if [[ -n "$COMFY_PYTHON" ]]; then
    info "Python：$COMFY_PYTHON"
elif venv_is_blocked "$COMFY_ROOT"; then
    print_venv_help
else
    warn "找不到 ComfyUI 的 Python，將只複製節點檔案、不安裝 pip 依賴。"
    warn "請確認此目錄是 ComfyUI Desktop 的資料目錄（通常含 custom_nodes）。"
fi

mkdir -p "$TARGET_DIR"

if [[ -e "$NODE_DIR" || -L "$NODE_DIR" ]]; then
    ask_replace
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dmxapi-install.XXXXXX")"
EXTRACT_DIR="$WORK_DIR/extracted"
ZIP_PATH="$WORK_DIR/$ZIP_NAME"
mkdir -p "$EXTRACT_DIR"

download_zip "$ZIP_PATH"

info "正在解壓縮..."
unzip -q "$ZIP_PATH" -d "$EXTRACT_DIR" \
    || die "解壓縮失敗。下載的檔案可能已損毀。"

SOURCE_DIR="$EXTRACT_DIR/$EXTRACTED_FOLDER_NAME"
if [[ ! -d "$SOURCE_DIR" ]]; then
    # codeload 有時會解出相同資料夾名；否則取第一個含 __init__.py 的目錄
    SOURCE_DIR="$(find "$EXTRACT_DIR" -maxdepth 2 -type d -name "$EXTRACTED_FOLDER_NAME" | head -n 1 || true)"
    if [[ -z "$SOURCE_DIR" || ! -d "$SOURCE_DIR" ]]; then
        SOURCE_DIR="$(find "$EXTRACT_DIR" -maxdepth 2 -type f -name "__init__.py" -print \
            | head -n 1 \
            | sed 's|/__init__.py$||' || true)"
    fi
fi
[[ -n "$SOURCE_DIR" && -d "$SOURCE_DIR" ]] || die "解壓縮後找不到節點資料夾：$EXTRACTED_FOLDER_NAME"

STAGE_ROOT="$(mktemp -d "$TARGET_DIR/.${NODE_NAME}.new.XXXXXX")"
STAGED_NODE_DIR="$STAGE_ROOT/$NODE_NAME"
mv "$SOURCE_DIR" "$STAGED_NODE_DIR"

if [[ -f "$STAGED_NODE_DIR/requirements.txt" ]]; then
    if [[ -n "$COMFY_PYTHON" ]]; then
        info "正在安裝 Python 依賴..."
        if ! "$COMFY_PYTHON" -m pip install -r "$STAGED_NODE_DIR/requirements.txt"; then
            if venv_is_blocked "$COMFY_ROOT"; then
                print_venv_help
            fi
            die "依賴安裝失敗。"
        fi
    else
        warn "已跳過 pip。節點檔案仍會安裝；若啟動後缺少套件，請先開啟終端機的 App 管理權限再重跑本檔。"
    fi
else
    info "沒有 requirements.txt，略過依賴安裝。"
fi

if [[ -e "$NODE_DIR" || -L "$NODE_DIR" ]]; then
    BACKUP_DIR="$(mktemp -d "$TARGET_DIR/.${NODE_NAME}.backup.XXXXXX")"
    rmdir "$BACKUP_DIR"
    info "正在取代現有節點資料夾..."
    mv "$NODE_DIR" "$BACKUP_DIR" \
        || die "無法移走現有節點資料夾。"
fi

if ! mv "$STAGED_NODE_DIR" "$NODE_DIR"; then
    if [[ -n "$BACKUP_DIR" && -e "$BACKUP_DIR" ]]; then
        mv "$BACKUP_DIR" "$NODE_DIR" \
            || die "無法安裝新節點，也無法還原舊節點。"
        BACKUP_DIR=""
    fi
    die "無法把下載的節點放到目標位置。"
fi

rmdir "$STAGE_ROOT" 2>/dev/null || true
STAGE_ROOT=""

if [[ -n "$BACKUP_DIR" && -e "$BACKUP_DIR" ]]; then
    rm -rf "$BACKUP_DIR"
    BACKUP_DIR=""
fi

print -- ""
print -- "=========================================="
print -- " 安裝完成！"
print -- " 請重新啟動 ComfyUI。"
print -- "=========================================="
print -- ""
