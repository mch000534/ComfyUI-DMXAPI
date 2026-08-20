# MiniMax H3 Simplification Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce MiniMax support to one registered H3-only video generation node while removing obsolete standalone and Hailuo implementations.

**Architecture:** Keep the existing `DMXAPIVideoNodeBase` output and download flow, but make the MiniMax module H3-specific. Preserve the public `model` widget position with one allowed value, keep the reference node class unregistered for later redesign, and cover registration and pre-submit validation with offline unit tests.

**Tech Stack:** Python 3.12, ComfyUI custom-node APIs, `unittest`, PyTorch tensors, DMXAPI `/v1/responses` client helpers.

---

## File Map

- Create `tests/test_minimax_simplification.py`: offline regression tests for registration, model restrictions, payload construction, and validation before network submission.
- Modify `dmxapi_minimax_h3_nodes.py`: retain only H3 base logic, the integrated public node, and the unregistered reference class.
- Modify `dmxapi_common.py`: remove the unused Hailuo resolution tier constant and Hailuo-specific comments.
- Modify `README.md`: describe the 10-node, H3-only public surface and remove obsolete MiniMax nodes.
- Modify `CLAUDE.md`: synchronize architecture, protocol, node-count, sizing, and download guidance.

## Chunk 1: H3-only implementation and verification

### Task 1: Add failing offline regression tests

**Files:**

- Create: `tests/test_minimax_simplification.py`
- Reference: `docs/superpowers/specs/2026-08-20-minimax-h3-simplification-design.md`

- [ ] **Step 1: Create the test module with a package loader**

Use the repository's `importlib.util.spec_from_file_location` pattern so the hyphenated directory can be imported without ComfyUI on `sys.path`:

```python
import importlib.util
import inspect
import pathlib
import sys
import unittest
from unittest.mock import Mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE_NAME = "ComfyUI_DMXAPI_test"


def load_package():
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PACKAGE = load_package()
MINIMAX = sys.modules[PACKAGE_NAME + ".dmxapi_minimax_h3_nodes"]
```

- [ ] **Step 2: Add registration and model-surface tests**

```python
class MiniMaxSimplificationTests(unittest.TestCase):
    def test_only_integrated_minimax_node_is_registered(self):
        minimax_ids = [
            name for name in PACKAGE.NODE_CLASS_MAPPINGS
            if name.startswith("DMXAPI_MiniMax_")
        ]
        self.assertEqual(minimax_ids, ["DMXAPI_MiniMax_Video"])
        minimax_display_ids = [
            name for name in PACKAGE.NODE_DISPLAY_NAME_MAPPINGS
            if name.startswith("DMXAPI_MiniMax_")
        ]
        self.assertEqual(minimax_display_ids, ["DMXAPI_MiniMax_Video"])
        self.assertEqual(len(PACKAGE.NODE_CLASS_MAPPINGS), 10)

    def test_integrated_model_widget_only_offers_h3(self):
        model_spec = MINIMAX.DMXAPI_MiniMax_Video.INPUT_TYPES()["required"]["model"]
        self.assertEqual(model_spec[0], ["MiniMax-H3"])
        self.assertEqual(model_spec[1]["default"], "MiniMax-H3")

    def test_reference_node_is_unregistered_and_h3_only(self):
        self.assertNotIn("DMXAPI_MiniMax_Reference2V", PACKAGE.NODE_CLASS_MAPPINGS)
        model_spec = MINIMAX.DMXAPI_MiniMax_Reference2V.INPUT_TYPES()["required"]["model"]
        self.assertEqual(model_spec[0], ["MiniMax-H3"])

    def test_removed_node_classes_are_absent(self):
        for name in (
            "DMXAPI_MiniMax_T2V",
            "DMXAPI_MiniMax_I2V",
            "DMXAPI_MiniMax_DownloadVideo",
        ):
            self.assertFalse(hasattr(MINIMAX, name), name)
```

- [ ] **Step 3: Add offline payload and pre-submit validation tests**

```python
    def test_h3_payload_model_is_fixed(self):
        payload = MINIMAX.MiniMaxVideoBase().build_h3_payload(
            prompt="test",
            ratio="16:9",
            resolution="768P",
            duration=5.0,
            prompt_optimizer=True,
        )
        self.assertEqual(payload["model"], "MiniMax-H3")

    def _generate_without_frames(self, node, model="MiniMax-H3", last_frame=None):
        return node.generate(
            prompt="test",
            width=1344,
            height=768,
            duration=5.0,
            noise_seed=0,
            model=model,
            api_key="unused",
            prompt_optimizer=True,
            download_video=False,
            max_frames=0,
            save_dir="",
            poll_interval=8,
            max_wait=60,
            first_frame=None,
            last_frame=last_frame,
        )

    def test_last_frame_without_first_frame_fails_before_submit(self):
        node = MINIMAX.DMXAPI_MiniMax_Video()
        node.run_task = Mock(side_effect=AssertionError("API submission reached"))
        with self.assertRaisesRegex(ValueError, "last_frame|尾幀"):
            self._generate_without_frames(node, last_frame=object())
        node.run_task.assert_not_called()

    def test_removed_model_value_fails_before_submit(self):
        node = MINIMAX.DMXAPI_MiniMax_Video()
        node.run_task = Mock(side_effect=AssertionError("API submission reached"))
        with self.assertRaisesRegex(ValueError, "MiniMax-H3"):
            self._generate_without_frames(node, model="MiniMax-Hailuo-02")
        node.run_task.assert_not_called()

    def test_unregistered_reference_path_uses_h3_signatures_offline(self):
        node = MINIMAX.DMXAPI_MiniMax_Reference2V()
        node.resolve_key = Mock(return_value="token")
        node.run_task = Mock(return_value=("https://example.invalid/video.mp4", "task"))
        node.finish = Mock(return_value="finished")

        result = node.generate(
            prompt="test",
            width=1344,
            height=768,
            duration=5.0,
            noise_seed=0,
            model="MiniMax-H3",
            api_key="unused",
            prompt_optimizer=True,
            download_video=False,
            max_frames=0,
            save_dir="",
            poll_interval=8,
            max_wait=60,
        )

        self.assertEqual(result, "finished")
        payload = node.run_task.call_args.args[0]
        self.assertEqual(payload["model"], "MiniMax-H3")
        node.run_task.assert_called_once_with(payload, "token", 8, 60)
```

- [ ] **Step 4: Run the new tests and verify they fail for the expected old behavior**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/barry/Documents/ComfyUI/.venv/bin/python -m unittest tests.test_minimax_simplification -v
```

Expected: failures show 14 rather than 10 registrations, multiple MiniMax model choices, registered/defined old classes, non-fixed payload API, and missing pre-submit validation. Import must succeed; an import error is not an acceptable expected failure.

### Task 2: Simplify MiniMax to H3-only

**Files:**

- Modify: `dmxapi_minimax_h3_nodes.py:1-437`
- Modify: `dmxapi_common.py:104-121`
- Test: `tests/test_minimax_simplification.py`

- [ ] **Step 1: Reduce shared constants and imports**

In `dmxapi_common.py`, delete `HAILUO_RESOLUTION_TIERS` and rewrite the surrounding comments so MiniMax documents only `H3_RESOLUTION_TIERS`.

In `dmxapi_minimax_h3_nodes.py`:

- Remove imports used only by the download node or Hailuo: `HAILUO_RESOLUTION_TIERS`, `build_video_preview`, `decode_frames`, `download_video_file`, `empty_frame`, and `to_video_output`.
- Replace `H3_MODELS` with `MINIMAX_MODEL = "MiniMax-H3"`.
- Rewrite the module docstring to describe only H3 submit and polling behavior.

- [ ] **Step 2: Make `MiniMaxVideoBase` H3-specific**

Delete `_parse_hailuo`, `fetch_hailuo_url`, `detect_series`, `build_hailuo_payload`, and every Hailuo branch.

Use these H3-only method boundaries:

```python
def resolve_size(self, width, height):
    ratio = ratio_from_size(width, height, MINIMAX_RATIOS)
    resolution = resolution_from_size(
        width, height, H3_RESOLUTION_TIERS, default="768P"
    )
    logger.info(
        "[DMXAPI] %s %sx%s → ratio=%s resolution=%s",
        MINIMAX_MODEL, width, height, ratio, resolution,
    )
    return (ratio, resolution)

def wait_for_url(self, task_id, token, poll_interval, max_wait):
    return poll_task(
        {"model": "MiniMax-H3-get", "input": task_id},
        _parse_h3,
        token,
        label="H3",
        poll_interval=poll_interval,
        max_wait=max_wait,
    )

def run_task(self, payload, token, poll_interval, max_wait):
    task_id = self.submit(payload, token, "H3")
    video_url = self.wait_for_url(task_id, token, poll_interval, max_wait)
    return (video_url, task_id)
```

Remove the `model` argument from `build_h3_payload` and set its payload field internally:

```python
payload = {
    "model": MINIMAX_MODEL,
    "input": items,
    "resolution": resolution,
    "duration": duration_seconds(duration),
    "ratio": ratio,
    "prompt_optimizer": prompt_optimizer,
    "aigc_watermark": False,
}
```

- [ ] **Step 3: Preserve the model widget position with one valid value**

Change `_minimax_inputs` to build this exact model spec in the same dictionary position:

```python
"model": ([MINIMAX_MODEL], {"default": MINIMAX_MODEL}),
```

Remove its `models` argument and update both remaining callers.

- [ ] **Step 4: Make the integrated node validate before any API work**

At the start of `DMXAPI_MiniMax_Video.generate`, before `resolve_size` or `resolve_key`, enforce:

```python
if model != MINIMAX_MODEL:
    raise ValueError(
        "[DMXAPI Error] MiniMax 目前只支援 MiniMax-H3，請更新舊 workflow 的 model。"
    )
if last_frame is not None and first_frame is None:
    raise ValueError("[DMXAPI Error] 使用 last_frame 時必須同時提供 first_frame。")
if first_frame is None and not prompt.strip():
    raise ValueError("[DMXAPI Error] 文生影片模式下 Prompt 不能為空。")
```

Then always build and run the H3 payload; there must be no model-family branch:

```python
ratio, resolution = self.resolve_size(width, height)
token = self.resolve_key(api_key)
payload = self.build_h3_payload(
    prompt,
    ratio,
    resolution,
    duration,
    prompt_optimizer,
    noise_seed,
    images=(("first_frame", first_frame), ("last_frame", last_frame)),
)
video_url, task_id = self.run_task(payload, token, poll_interval, max_wait)
```

- [ ] **Step 5: Delete obsolete classes and unregister the reference class**

- Delete `DMXAPI_MiniMax_T2V`, `DMXAPI_MiniMax_I2V`, and `DMXAPI_MiniMax_DownloadVideo` completely.
- Keep `DMXAPI_MiniMax_Reference2V`, update it for the H3-only method signatures, and place this comment directly above it:

```python
# 非公開待重構：刻意不加入 NODE_CLASS_MAPPINGS / NODE_DISPLAY_NAME_MAPPINGS。
```

Its `generate` method must use the new signatures exactly:

```python
ratio, resolution = self.resolve_size(width, height)
token = self.resolve_key(api_key)
payload = self.build_h3_payload(
    prompt,
    ratio,
    resolution,
    duration,
    prompt_optimizer,
    noise_seed,
    images=(("character", character_image), ("style", style_image)),
)
if audio_url and audio_url.strip():
    payload["audio_url"] = audio_url.strip()

video_url, task_id = self.run_task(payload, token, poll_interval, max_wait)
return self.finish(video_url, task_id, download_video, max_frames, save_dir, "minimax_ref")
```

- Set both mappings to contain only `DMXAPI_MiniMax_Video`.

- [ ] **Step 6: Run the focused tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/barry/Documents/ComfyUI/.venv/bin/python -m unittest tests.test_minimax_simplification -v
```

Expected: all tests pass and no network request is made.

- [ ] **Step 7: Run syntax compilation**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/comfyui-dmxapi-pycache /Users/barry/Documents/ComfyUI/.venv/bin/python -m py_compile __init__.py dmxapi_common.py dmxapi_agnes_image.py dmxapi_gpt_image2_node.py dmxapi_minimax_h3_nodes.py dmxapi_seedance2.py
```

Expected: exit code 0 with no syntax errors, and no repository `__pycache__` files are written or changed.

- [ ] **Step 8: Commit the H3-only implementation and tests**

```bash
git add dmxapi_common.py dmxapi_minimax_h3_nodes.py tests/test_minimax_simplification.py
git commit -m "refactor: simplify MiniMax nodes to H3"
```

### Task 3: Synchronize user and maintainer documentation

**Files:**

- Modify: `README.md:1-260`
- Modify: `CLAUDE.md:5-227`

- [ ] **Step 1: Update `README.md` public behavior**

Make these outcomes explicit:

- Project total is 10 registered nodes.
- MiniMax supports only `MiniMax-H3` through `DMXAPI MiniMax 影片生成`.
- The integrated node supports text-only, first-frame, and first-plus-last-frame input.
- Remove public entries for MiniMax T2V, I2V, reference, and download nodes.
- Remove Hailuo, H3-01, MiniMax download-series detection, and cross-workflow MiniMax download claims.
- Keep Seedance download instructions unchanged.
- Describe `dmxapi_minimax_h3_nodes.py` as one registered H3 node plus an unregistered reference implementation awaiting redesign.
- Change the registration smoke-test expectation from 14 to 10.

- [ ] **Step 2: Update `CLAUDE.md` architecture facts**

Synchronize every affected section:

- Project total: 10 nodes; video total: 8 nodes.
- MiniMax module: one registered H3 node; reference class is intentionally unregistered.
- Async protocols: two current protocols, MiniMax H3 and Seedance 2.0.
- Remove Hailuo protocol, status-case, resolution-tier, ratio, download-node, and series-detection guidance.
- MiniMax sizing uses only `H3_RESOLUTION_TIERS`.
- Download-node guidance applies only to Seedance.
- Preview wording must no longer claim two download nodes.
- Preserve the H3 payload and official-template naming guidance.

- [ ] **Step 3: Verify obsolete public claims are gone**

Run:

```bash
rg -n "MiniMax-H3-01|MiniMax-Hailuo|Hailuo-02|DMXAPI_MiniMax_(T2V|I2V|DownloadVideo)|DMXAPI MiniMax (文生影片|圖生影片|下載影片)" dmxapi_common.py dmxapi_minimax_h3_nodes.py README.md CLAUDE.md
```

Expected: no matches. The tracked design document is excluded because it intentionally records the removed surface.

- [ ] **Step 4: Commit synchronized documentation**

```bash
git add README.md CLAUDE.md
git commit -m "docs: document H3-only MiniMax node"
```

### Task 4: Run full offline acceptance verification

**Files:**

- Verify: `__init__.py`
- Verify: `dmxapi_common.py`
- Verify: `dmxapi_minimax_h3_nodes.py`
- Verify: `tests/test_minimax_simplification.py`
- Verify: `README.md`
- Verify: `CLAUDE.md`

- [ ] **Step 1: Run all repository unit tests**

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/barry/Documents/ComfyUI/.venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Run registration and signature integrity smoke test**

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/barry/Documents/ComfyUI/.venv/bin/python - <<'PY'
import importlib.util
import inspect
import sys

p = "/Users/barry/Documents/ComfyUI/custom_nodes/ComfyUI-DMXAPI"
spec = importlib.util.spec_from_file_location(
    "ComfyUI_DMXAPI", p + "/__init__.py", submodule_search_locations=[p]
)
module = importlib.util.module_from_spec(spec)
sys.modules["ComfyUI_DMXAPI"] = module
spec.loader.exec_module(module)

assert len(module.NODE_CLASS_MAPPINGS) == 10
assert [name for name in module.NODE_CLASS_MAPPINGS if name.startswith("DMXAPI_MiniMax_")] == [
    "DMXAPI_MiniMax_Video"
]

for name, cls in module.NODE_CLASS_MAPPINGS.items():
    inputs = cls.INPUT_TYPES()
    declared = set(inputs.get("required", {})) | set(inputs.get("optional", {}))
    signature = inspect.signature(getattr(cls, cls.FUNCTION))
    parameters = set(signature.parameters) - {"self"}
    required = {
        key for key, value in signature.parameters.items()
        if key != "self" and value.default is inspect.Parameter.empty
    }
    assert not (required - declared), (name, required - declared)
    assert not (declared - parameters), (name, declared - parameters)
    assert len(cls.RETURN_TYPES) == len(cls.RETURN_NAMES), name

print("OK: 10 nodes; MiniMax is H3-only; signatures are consistent")
PY
```

Expected: `OK: 10 nodes; MiniMax is H3-only; signatures are consistent`.

- [ ] **Step 3: Confirm only intended files changed**

```bash
git status --short
git diff --check
git diff --stat HEAD~2..HEAD
```

Expected: no unrelated source changes. Ignore the pre-existing tracked `__pycache__/dmxapi_common.cpython-312.pyc` modification; do not stage or overwrite it.

- [ ] **Step 4: Restart ComfyUI for manual acceptance**

After implementation, fully restart ComfyUI and verify the node search shows only `DMXAPI MiniMax 影片生成` for MiniMax. Confirm its model widget contains only `MiniMax-H3`. Do not queue a paid generation unless the user explicitly authorizes it.
