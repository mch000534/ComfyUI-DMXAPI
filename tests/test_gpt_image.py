import importlib.util
import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

import torch


ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE_NAME = "ComfyUI_DMXAPI_gpt_image_test"


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
GPT_IMAGE = sys.modules[PACKAGE_NAME + ".dmxapi_gpt_image2_node"]


class GPTImageTests(unittest.TestCase):
    MODELS_25 = [
        "gpt-image-2.5-sunburst",
        "gpt-image-2.5-sunburst-cdx",
        "gpt-image-2.5-sunburst-ssvip",
        "gpt-image-2.5-flare",
        "gpt-image-2.5-flare-cdx",
        "gpt-image-2.5-flare-ssvip",
    ]
    LEGACY_MODELS = [
        "gpt-image-2-03",
        "gpt-image-2",
        "gpt-image-2-ssvip",
    ]

    def setUp(self):
        self.node = GPT_IMAGE.DMXAPI_GPT_Image2()

    def successful_output_patches(self):
        return (
            patch.object(GPT_IMAGE, "fetch_image_item", return_value="pil-image"),
            patch.object(GPT_IMAGE, "pil_to_tensor_batch", return_value="image-tensor"),
        )

    def test_registration_and_node_contract_are_stable(self):
        node_class = GPT_IMAGE.DMXAPI_GPT_Image2

        self.assertIs(PACKAGE.NODE_CLASS_MAPPINGS["DMXAPI_GPT_Image2"], node_class)
        self.assertEqual(
            PACKAGE.NODE_DISPLAY_NAME_MAPPINGS["DMXAPI_GPT_Image2"],
            "DMXAPI GPT Image",
        )
        self.assertEqual(node_class.RETURN_TYPES, ("IMAGE",))
        self.assertEqual(node_class.RETURN_NAMES, ("IMAGE",))
        self.assertEqual(node_class.FUNCTION, "generate_image")
        self.assertEqual(node_class.CATEGORY, "DMXAPI/Image")

    def test_widget_order_and_optional_input_are_exact(self):
        inputs = GPT_IMAGE.DMXAPI_GPT_Image2.INPUT_TYPES()

        self.assertEqual(
            list(inputs["required"]),
            ["prompt", "model", "api_key", "size", "batch_size", "quality"],
        )
        self.assertEqual(set(inputs["optional"]), {"image"})
        for removed_widget in (
            "output_format",
            "output_compression",
            "background",
            "moderation",
        ):
            self.assertNotIn(removed_widget, inputs["required"])
            self.assertNotIn(removed_widget, inputs["optional"])

    def test_model_constants_widget_order_and_default_are_exact(self):
        expected = self.MODELS_25 + self.LEGACY_MODELS
        model_spec = GPT_IMAGE.DMXAPI_GPT_Image2.INPUT_TYPES()["required"]["model"]

        self.assertEqual(GPT_IMAGE.GPT_IMAGE_25_MODELS, self.MODELS_25)
        self.assertEqual(GPT_IMAGE.LEGACY_MODELS, self.LEGACY_MODELS)
        self.assertEqual(GPT_IMAGE.SUPPORTED_MODELS, expected)
        self.assertEqual(model_spec[0], expected)
        self.assertEqual(model_spec[1]["default"], "gpt-image-2.5-flare")

    def test_quality_choices_and_default_are_exact(self):
        quality_spec = GPT_IMAGE.DMXAPI_GPT_Image2.INPUT_TYPES()["required"]["quality"]

        self.assertEqual(quality_spec[0], ["auto", "low", "medium", "high", "xhigh", "max"])
        self.assertEqual(quality_spec[1]["default"], "auto")

    def test_supported_sizes_and_validation_are_unchanged(self):
        expected = [
            "auto",
            "1024x1024",
            "1536x1024",
            "1024x1536",
            "2048x2048",
            "2048x1152",
            "3840x2160",
            "2160x3840",
        ]

        self.assertEqual(self.node.SUPPORTED_SIZES, expected)
        self.assertIsNone(self.node._validate_size("auto"))
        for size in expected[1:]:
            with self.subTest(size=size):
                width, height = (int(part) for part in size.split("x"))
                self.assertEqual(self.node._validate_size(size), (width, height))

    def test_legacy_models_reject_25_only_quality_before_resolving_key(self):
        for model in self.LEGACY_MODELS:
            for quality in ("xhigh", "max"):
                with self.subTest(model=model, quality=quality):
                    fetch_patch, tensor_patch = self.successful_output_patches()
                    with (
                        patch.object(
                            GPT_IMAGE, "resolve_api_key", return_value="token"
                        ) as resolve_key,
                        patch.object(
                            self.node,
                            "_submit_generation",
                            return_value={"data": [{"b64_json": "unused"}]},
                        ) as submit,
                        fetch_patch,
                        tensor_patch,
                    ):
                        try:
                            self.node.generate_image(
                                prompt="test prompt",
                                model=model,
                                api_key="unused",
                                size="1024x1024",
                                batch_size=1,
                                quality=quality,
                            )
                        except ValueError as error:
                            self.assertRegex(
                                str(error), r"\[DMXAPI Error\].*GPT Image 2\.5"
                            )
                        else:
                            self.fail("legacy model accepted a GPT Image 2.5-only quality")
                    resolve_key.assert_not_called()
                    submit.assert_not_called()

    def test_model_batch_limits_are_explicit(self):
        self.assertEqual(
            GPT_IMAGE.MODEL_BATCH_LIMITS,
            {
                "gpt-image-2-03": 1,
                "gpt-image-2.5-sunburst-cdx": 3,
                "gpt-image-2.5-flare-cdx": 3,
            },
        )

    def test_cdx_models_clamp_batch_once_before_submit(self):
        for model in (
            "gpt-image-2.5-sunburst-cdx",
            "gpt-image-2.5-flare-cdx",
        ):
            with self.subTest(model=model):
                session = object()
                submit = Mock(return_value={"data": [{"b64_json": "unused"}]})
                fetch_patch, tensor_patch = self.successful_output_patches()
                with (
                    patch.object(GPT_IMAGE, "resolve_api_key", return_value="token"),
                    patch.object(GPT_IMAGE.requests, "Session", return_value=session),
                    patch.object(self.node, "_submit_generation", submit),
                    fetch_patch,
                    tensor_patch,
                    self.assertLogs("DMXAPI", level="WARNING") as logs,
                ):
                    result = self.node.generate_image(
                        prompt="test prompt",
                        model=model,
                        api_key="provided",
                        size="1024x1024",
                        batch_size=4,
                        quality="high",
                    )

                self.assertEqual(result, ("image-tensor",))
                submit.assert_called_once_with(
                    "test prompt", model, "1024x1024", "high", 3, "token", session
                )
                self.assertEqual(len(logs.records), 1)
                warning = logs.output[0]
                self.assertIn(model, warning)
                self.assertIn("4", warning)
                self.assertIn("3", warning)

    def test_legacy_03_still_clamps_batch_to_one(self):
        session = object()
        submit = Mock(return_value={"data": [{"b64_json": "unused"}]})
        fetch_patch, tensor_patch = self.successful_output_patches()
        with (
            patch.object(GPT_IMAGE, "resolve_api_key", return_value="token"),
            patch.object(GPT_IMAGE.requests, "Session", return_value=session),
            patch.object(self.node, "_submit_generation", submit),
            fetch_patch,
            tensor_patch,
            self.assertLogs("DMXAPI", level="WARNING") as logs,
        ):
            self.node.generate_image(
                prompt="test prompt",
                model="gpt-image-2-03",
                api_key="provided",
                size="1024x1024",
                batch_size=4,
                quality="medium",
            )

        submit.assert_called_once_with(
            "test prompt", "gpt-image-2-03", "1024x1024", "medium", 1, "token", session
        )
        self.assertEqual(len(logs.records), 1)
        self.assertIn("gpt-image-2-03", logs.output[0])
        self.assertIn("4", logs.output[0])
        self.assertIn("1", logs.output[0])

    def test_25_base_and_ssvip_models_keep_batch_four(self):
        models = [
            "gpt-image-2.5-sunburst",
            "gpt-image-2.5-sunburst-ssvip",
            "gpt-image-2.5-flare",
            "gpt-image-2.5-flare-ssvip",
        ]
        for model in models:
            with self.subTest(model=model):
                submit = Mock(return_value={"data": [{"b64_json": "unused"}]})
                fetch_patch, tensor_patch = self.successful_output_patches()
                with (
                    patch.object(GPT_IMAGE, "resolve_api_key", return_value="token"),
                    patch.object(self.node, "_submit_generation", submit),
                    patch.object(GPT_IMAGE.logger, "warning") as warning,
                    fetch_patch,
                    tensor_patch,
                ):
                    self.node.generate_image(
                        prompt="test prompt",
                        model=model,
                        api_key="provided",
                        size="1024x1024",
                        batch_size=4,
                        quality="high",
                    )

                self.assertEqual(submit.call_args.args[4], 4)
                warning.assert_not_called()

    def test_25_text_generation_uses_json_without_response_format(self):
        session = object()
        response = {"data": [{"b64_json": "unused"}]}
        fetch_patch, tensor_patch = self.successful_output_patches()
        with (
            patch.object(GPT_IMAGE, "resolve_api_key", return_value="token"),
            patch.object(GPT_IMAGE.requests, "Session", return_value=session),
            patch.object(GPT_IMAGE, "post_json", return_value=response) as post_json,
            fetch_patch,
            tensor_patch,
        ):
            result = self.node.generate_image(
                prompt="test prompt",
                model="gpt-image-2.5-sunburst",
                api_key="provided",
                size="1536x1024",
                batch_size=2,
                quality="xhigh",
            )

        self.assertEqual(result, ("image-tensor",))
        post_json.assert_called_once()
        url, payload, token = post_json.call_args.args
        self.assertEqual(url, GPT_IMAGE.IMAGES_URL)
        self.assertEqual(
            payload,
            {
                "model": "gpt-image-2.5-sunburst",
                "prompt": "test prompt",
                "n": 2,
                "size": "1536x1024",
                "quality": "xhigh",
            },
        )
        self.assertIsInstance(payload["n"], int)
        self.assertEqual(token, "token")
        self.assertEqual(post_json.call_args.kwargs["timeout"], 120)
        self.assertIs(post_json.call_args.kwargs["session"], session)
        self.assertNotIn("response_format", payload)

    def test_legacy_text_generation_retains_b64_response_format(self):
        session = object()
        response = {"data": [{"b64_json": "unused"}]}
        fetch_patch, tensor_patch = self.successful_output_patches()
        with (
            patch.object(GPT_IMAGE, "resolve_api_key", return_value="token"),
            patch.object(GPT_IMAGE.requests, "Session", return_value=session),
            patch.object(GPT_IMAGE, "post_json", return_value=response) as post_json,
            fetch_patch,
            tensor_patch,
        ):
            self.node.generate_image(
                prompt="test prompt",
                model="gpt-image-2",
                api_key="provided",
                size="1024x1024",
                batch_size=2,
                quality="medium",
            )

        payload = post_json.call_args.args[1]
        self.assertEqual(payload["response_format"], "b64_json")
        self.assertEqual(payload["quality"], "medium")

    def test_25_edit_uses_multipart_jpeg_and_effective_batch_limit(self):
        session = object()
        response = {"data": [{"b64_json": "unused"}]}
        image = torch.zeros((1, 16, 16, 3))
        fetch_patch, tensor_patch = self.successful_output_patches()
        with (
            patch.object(GPT_IMAGE, "resolve_api_key", return_value="token"),
            patch.object(GPT_IMAGE.requests, "Session", return_value=session),
            patch.object(
                GPT_IMAGE,
                "tensor_to_image_bytes",
                return_value=(b"jpeg-bytes", "image/jpeg"),
            ) as encode,
            patch.object(GPT_IMAGE, "post_multipart", return_value=response) as post_multipart,
            fetch_patch,
            tensor_patch,
            self.assertLogs("DMXAPI", level="WARNING") as logs,
        ):
            result = self.node.generate_image(
                prompt="edit prompt",
                model="gpt-image-2.5-flare-cdx",
                api_key="provided",
                size="1024x1536",
                batch_size=4,
                quality="max",
                image=image,
            )

        self.assertEqual(result, ("image-tensor",))
        self.assertEqual(len(logs.records), 1)
        encode.assert_called_once_with(image, fmt="JPEG", quality=95, max_side=2048)
        post_multipart.assert_called_once()
        url, fields, files, token = post_multipart.call_args.args
        self.assertEqual(url, GPT_IMAGE.IMAGES_EDITS_URL)
        self.assertEqual(
            fields,
            {
                "model": "gpt-image-2.5-flare-cdx",
                "prompt": "edit prompt",
                "n": "3",
                "size": "1024x1536",
                "quality": "max",
            },
        )
        self.assertIsInstance(fields["n"], str)
        self.assertEqual(files, {"image": ("image.jpg", b"jpeg-bytes", "image/jpeg")})
        self.assertEqual(token, "token")
        self.assertEqual(post_multipart.call_args.kwargs["timeout"], 120)
        self.assertIs(post_multipart.call_args.kwargs["session"], session)

    def test_timeout_guidance_recommends_only_evidence_backed_flare_base(self):
        with (
            patch.object(GPT_IMAGE, "resolve_api_key", return_value="token"),
            patch.object(
                self.node,
                "_submit_generation",
                side_effect=RuntimeError("[DMXAPI Error] 連線被上游中斷"),
            ),
        ):
            with self.assertRaises(RuntimeError) as caught:
                self.node.generate_image(
                    prompt="test prompt",
                    model="gpt-image-2.5-sunburst",
                    api_key="provided",
                    size="1024x1024",
                    batch_size=1,
                    quality="high",
                )

        message = str(caught.exception)
        self.assertIn("gpt-image-2.5-flare", message)
        self.assertNotIn("gpt-image-2.5-flare-cdx", message)
        self.assertNotIn("gpt-image-2.5-flare-ssvip", message)


if __name__ == "__main__":
    unittest.main()
