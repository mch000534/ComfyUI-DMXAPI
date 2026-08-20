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


class MiniMaxSimplificationTests(unittest.TestCase):
    def test_total_registered_node_count_is_ten(self):
        self.assertEqual(len(PACKAGE.NODE_CLASS_MAPPINGS), 10)

    def test_only_integrated_minimax_node_is_registered(self):
        expected = ["DMXAPI_MiniMax_Video"]
        minimax_class_ids = [
            name
            for name in PACKAGE.NODE_CLASS_MAPPINGS
            if name.startswith("DMXAPI_MiniMax_")
        ]
        minimax_display_ids = [
            name
            for name in PACKAGE.NODE_DISPLAY_NAME_MAPPINGS
            if name.startswith("DMXAPI_MiniMax_")
        ]
        self.assertEqual(minimax_class_ids, expected)
        self.assertEqual(minimax_display_ids, expected)
        self.assertEqual(
            PACKAGE.NODE_DISPLAY_NAME_MAPPINGS["DMXAPI_MiniMax_Video"],
            "DMXAPI MiniMax 影片生成",
        )

    def test_integrated_model_widget_only_offers_and_defaults_to_h3(self):
        model_spec = MINIMAX.DMXAPI_MiniMax_Video.INPUT_TYPES()["required"]["model"]
        self.assertEqual(model_spec[0], ["MiniMax-H3"])
        self.assertEqual(model_spec[1]["default"], "MiniMax-H3")

    def test_reference_node_is_unregistered_and_h3_only(self):
        self.assertNotIn("DMXAPI_MiniMax_Reference2V", PACKAGE.NODE_CLASS_MAPPINGS)
        self.assertNotIn(
            "DMXAPI_MiniMax_Reference2V", PACKAGE.NODE_DISPLAY_NAME_MAPPINGS
        )
        model_spec = MINIMAX.DMXAPI_MiniMax_Reference2V.INPUT_TYPES()["required"][
            "model"
        ]
        self.assertEqual(model_spec[0], ["MiniMax-H3"])
        self.assertEqual(model_spec[1]["default"], "MiniMax-H3")

    def test_removed_node_classes_are_absent(self):
        for name in (
            "DMXAPI_MiniMax_T2V",
            "DMXAPI_MiniMax_I2V",
            "DMXAPI_MiniMax_DownloadVideo",
        ):
            self.assertFalse(hasattr(MINIMAX, name), name)

    def test_h3_payload_signature_has_no_model_and_model_is_fixed(self):
        signature = inspect.signature(MINIMAX.MiniMaxVideoBase.build_h3_payload)
        self.assertNotIn("model", signature.parameters)
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

    def test_reference_removed_model_fails_before_api_work(self):
        node = MINIMAX.DMXAPI_MiniMax_Reference2V()
        node.resolve_key = Mock(side_effect=AssertionError("API key resolution reached"))
        node.run_task = Mock(side_effect=AssertionError("API submission reached"))

        with self.assertRaisesRegex(ValueError, "MiniMax-H3"):
            node.generate(
                prompt="test",
                width=1344,
                height=768,
                duration=5.0,
                noise_seed=0,
                model="MiniMax-Hailuo-02",
                api_key="unused",
                prompt_optimizer=True,
                download_video=False,
                max_frames=0,
                save_dir="",
                poll_interval=8,
                max_wait=60,
            )

        node.resolve_key.assert_not_called()
        node.run_task.assert_not_called()

    def test_unregistered_reference_path_uses_h3_signatures_offline(self):
        node = MINIMAX.DMXAPI_MiniMax_Reference2V()
        node.resolve_key = Mock(return_value="token")
        node.run_task = Mock(
            return_value=("https://example.invalid/video.mp4", "task")
        )
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


if __name__ == "__main__":
    unittest.main()
