# macOS No-Git Installer Design

## Goal

Add a double-clickable macOS installer for ComfyUI-DMXAPI that mirrors the existing Windows no-Git installer without requiring Git or `sudo`.

## Scope

- Add `install_DMXAPI_node_NOgit.command`.
- Keep `install_DMXAPI_node_NOgit.bat` unchanged.
- Do not change the Python package or add a test framework.
- Do not update README documentation in this change.

## Behaviour

1. Run under `zsh` and resolve the repository's GitHub ZIP URL.
2. Prefer `COMFYUI_ROOT` when supplied; otherwise detect common ComfyUI roots, with `~/Documents/ComfyUI` as the primary macOS Desktop location.
3. Resolve a ComfyUI-managed Python interpreter from known virtual-environment locations. If none is found, stop with an actionable error instead of silently using system Python.
4. Create `custom_nodes` when needed.
5. If `ComfyUI-DMXAPI` already exists, ask whether to replace it or cancel.
6. Download and extract the repository ZIP into a temporary directory, then install it as `custom_nodes/ComfyUI-DMXAPI`.
7. Install `requirements.txt` with the resolved ComfyUI Python.
8. Remove temporary files on both success and failure where possible, print clear status messages, and tell the user to restart ComfyUI after success.

## Safety and compatibility

- Never use `sudo`.
- Never remove an existing node directory without explicit confirmation.
- Use a temporary working directory created by `mktemp -d` rather than a fixed path.
- Use `curl -fL` and `unzip`, both normally available on macOS.
- Keep the script self-contained and executable as a `.command` file.

## Verification

- `zsh -n install_DMXAPI_node_NOgit.command` must pass.
- The file must have an executable bit.
- An isolated dry run with stubbed external commands must exercise path detection and the missing-Python failure path without touching the real ComfyUI installation.
