#!/bin/zsh

set -euo pipefail

readonly REPO_URL="https://github.com/mch000534/ComfyUI-DMXAPI/archive/refs/heads/main.zip"
readonly NODE_NAME="ComfyUI-DMXAPI"
readonly EXTRACTED_FOLDER_NAME="ComfyUI-DMXAPI-main"
readonly ZIP_NAME="ComfyUI-DMXAPI-main.zip"

WORK_DIR=""
STAGE_ROOT=""
BACKUP_DIR=""

cleanup() {
    if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
        rm -rf "$WORK_DIR"
    fi
    if [[ -n "$STAGE_ROOT" && -d "$STAGE_ROOT" ]]; then
        rm -rf "$STAGE_ROOT"
    fi
}

interrupt() {
    exit 130
}

trap cleanup EXIT
trap interrupt INT TERM

die() {
    print -u2 -- "[ERROR] $*"
    exit 1
}

info() {
    print -- "[INFO] $*"
}

find_comfy_root() {
    local configured_root="${COMFYUI_ROOT:-}"
    local candidate
    local -a candidates

    if [[ -n "$configured_root" ]]; then
        configured_root="${configured_root/#\~/$HOME}"
        [[ -d "$configured_root" ]] || die "COMFYUI_ROOT does not exist: $configured_root"
        find_comfy_python "$configured_root" >/dev/null \
            || die "COMFYUI_ROOT has no supported ComfyUI Python environment: $configured_root"
        print -r -- "$configured_root"
        return
    fi

    candidates=(
        "$HOME/Documents/ComfyUI"
        "$HOME/ComfyUI"
        "$HOME/Library/Application Support/ComfyUI"
    )

    for candidate in "${candidates[@]}"; do
        if [[ -d "$candidate" ]] && find_comfy_python "$candidate" >/dev/null; then
            print -r -- "$candidate"
            return
        fi
    done

    die "Could not find a ComfyUI installation. Set COMFYUI_ROOT and run this file again."
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
    )

    for candidate in "${candidates[@]}"; do
        if [[ -x "$candidate" ]]; then
            print -r -- "$candidate"
            return
        fi
    done

    return 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

print -- "=========================================="
print -- " Auto-Install ComfyUI-DMXAPI (macOS, No Git)"
print -- "=========================================="
print -- ""

require_command curl
require_command unzip
require_command mktemp

COMFY_ROOT="$(find_comfy_root)"
TARGET_DIR="$COMFY_ROOT/custom_nodes"
COMFY_PYTHON="$(find_comfy_python "$COMFY_ROOT")"
NODE_DIR="$TARGET_DIR/$NODE_NAME"

info "ComfyUI root: $COMFY_ROOT"
info "Python: $COMFY_PYTHON"

mkdir -p "$TARGET_DIR"

if [[ -e "$NODE_DIR" || -L "$NODE_DIR" ]]; then
    print -- "[INFO] Folder '$NODE_NAME' already exists."
    print -- "Choose action: 1. Re-download & Replace (Update)  2. Cancel"
    print -n -- "Enter 1 or 2: "
    read -r choice
    if [[ "$choice" != "1" ]]; then
        print -- "[INFO] Installation cancelled."
        exit 0
    fi
    info "The existing folder will be replaced after the new archive is verified."
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dmxapi-install.XXXXXX")"
EXTRACT_DIR="$WORK_DIR/extracted"
ZIP_PATH="$WORK_DIR/$ZIP_NAME"
mkdir -p "$EXTRACT_DIR"

info "Downloading repository (ZIP)..."
curl -fL --retry 3 --retry-delay 2 --silent --show-error "$REPO_URL" -o "$ZIP_PATH" \
    || die "Download failed. Check your network connection and try again."

info "Extracting repository..."
unzip -q "$ZIP_PATH" -d "$EXTRACT_DIR" \
    || die "Extraction failed. The downloaded archive may be invalid."

SOURCE_DIR="$EXTRACT_DIR/$EXTRACTED_FOLDER_NAME"
[[ -d "$SOURCE_DIR" ]] || die "Extracted folder not found: $EXTRACTED_FOLDER_NAME"

STAGE_ROOT="$(mktemp -d "$TARGET_DIR/.${NODE_NAME}.new.XXXXXX")"
STAGED_NODE_DIR="$STAGE_ROOT/$NODE_NAME"
mv "$SOURCE_DIR" "$STAGED_NODE_DIR"

if [[ -f "$STAGED_NODE_DIR/requirements.txt" ]]; then
    info "Installing Python dependencies in the downloaded copy..."
    "$COMFY_PYTHON" -m pip install -r "$STAGED_NODE_DIR/requirements.txt" \
        || die "Dependency installation failed."
else
    info "No requirements.txt found, skipping dependencies."
fi

if [[ -e "$NODE_DIR" || -L "$NODE_DIR" ]]; then
    BACKUP_DIR="$(mktemp -d "$TARGET_DIR/.${NODE_NAME}.backup.XXXXXX")"
    rmdir "$BACKUP_DIR"
    info "Replacing the existing node folder..."
    mv "$NODE_DIR" "$BACKUP_DIR" \
        || die "Could not stage the existing node folder for replacement."
fi

if ! mv "$STAGED_NODE_DIR" "$NODE_DIR"; then
    if [[ -n "$BACKUP_DIR" && -e "$BACKUP_DIR" ]]; then
        mv "$BACKUP_DIR" "$NODE_DIR" \
            || die "Could not install the new node or restore the existing node."
        BACKUP_DIR=""
    fi
    die "Could not move the downloaded node into place."
fi

rmdir "$STAGE_ROOT" 2>/dev/null || true
STAGE_ROOT=""

if [[ -n "$BACKUP_DIR" && -e "$BACKUP_DIR" ]]; then
    rm -rf "$BACKUP_DIR"
    BACKUP_DIR=""
fi

print -- ""
print -- "=========================================="
print -- " Installation Complete!"
print -- " Please restart ComfyUI."
print -- "=========================================="
print -- ""
print -n -- "Press Enter to close this window..."
read -r || true
