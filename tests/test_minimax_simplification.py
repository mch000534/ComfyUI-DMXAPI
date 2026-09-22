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
    def test_total_registered_node_count_is_eleven(self):
        self.assertEqual(len(PACKAGE.NODE_CLASS_MAPPINGS), 11)

    def test_registered_minimax_nodes(self):
        expected = ["DMXAPI_MiniMax_Video", "DMXAPI_MiniMax_Reference2V"]
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
        self.assertEqual(
            PACKAGE.NODE_DISPLAY_NAME_MAPPINGS["DMXAPI_MiniMax_Reference2V"],
            "DMXAPI MiniMax 多模態參考生影片",
        )

    def test_integrated_model_widget_only_offers_and_defaults_to_h3(self):
        model_spec = MINIMAX.DMXAPI_MiniMax_Video.INPUT_TYPES()["required"]["model"]
        self.assertEqual(model_spec[0], ["MiniMax-H3"])
        self.assertEqual(model_spec[1]["default"], "MiniMax-H3")

    def test_reference_node_is_h3_only(self):
        model_spec = MINIMAX.DMXAPI_MiniMax_Reference2V.INPUT_TYPES()["required"][
            "model"
        ]
        self.assertEqual(model_spec[0], ["MiniMax-H3"])
        self.assertEqual(model_spec[1]["default"], "MiniMax-H3")

    def test_reference_node_offers_adaptive_ratio_by_default(self):
        """多模態參考是唯一能指定 adaptive 的情境，且上游預設就是它。"""
        ratio_spec = MINIMAX.DMXAPI_MiniMax_Reference2V.INPUT_TYPES()["required"]["ratio"]
        self.assertIn("adaptive", ratio_spec[0])
        self.assertEqual(ratio_spec[1]["default"], "adaptive")

    def test_frame_node_ratio_has_no_adaptive(self):
        """文生影片不接受 adaptive，首尾幀則不送 ratio，兩者都不該出現這個選項。"""
        ratio_spec = MINIMAX.DMXAPI_MiniMax_Video.INPUT_TYPES()["required"]["ratio"]
        self.assertNotIn("adaptive", ratio_spec[0])

    def test_reference_node_has_no_frame_inputs(self):
        """官方明文：出現 reference_* role 就不能再出現 first_frame / last_frame。"""
        optional = MINIMAX.DMXAPI_MiniMax_Reference2V.INPUT_TYPES()["optional"]
        self.assertNotIn("first_frame", optional)
        self.assertNotIn("last_frame", optional)

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
            resolution="768P",
            ratio="16:9",
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

    def test_last_frame_alone_is_submitted_as_tail_frame(self):
        """上游支援「只給尾幀」，節點不得再擋下這個組合。"""
        node = MINIMAX.DMXAPI_MiniMax_Video()
        node.resolve_key = Mock(return_value="token")
        node.encode_image = Mock(return_value="data:image/jpeg;base64,AAAA")
        node.run_task = Mock(
            return_value=("https://example.invalid/video.mp4", "task")
        )
        node.finish = Mock(return_value="finished")

        result = self._generate_without_frames(node, last_frame=object())

        self.assertEqual(result, "finished")
        payload = node.run_task.call_args.args[0]
        roles = [item.get("role") for item in payload["input"]]
        self.assertEqual(roles, [None, "last_frame"])
        # 帶參考圖時比例由圖片決定，ratio 不應出現在 payload 裡
        self.assertNotIn("ratio", payload)

    def test_text_only_still_requires_prompt(self):
        node = MINIMAX.DMXAPI_MiniMax_Video()
        node.resolve_key = Mock(side_effect=AssertionError("API key resolution reached"))
        node.run_task = Mock(side_effect=AssertionError("API submission reached"))
        with self.assertRaisesRegex(ValueError, "Prompt"):
            node.generate(
                prompt="   ",
                resolution="768P",
                ratio="16:9",
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
        node.resolve_key.assert_not_called()
        node.run_task.assert_not_called()
    def test_removed_model_value_fails_before_submit(self):
        node = MINIMAX.DMXAPI_MiniMax_Video()
        node.run_task = Mock(side_effect=AssertionError("API submission reached"))
        with self.assertRaisesRegex(ValueError, "MiniMax-H3"):
            self._generate_without_frames(node, model="MiniMax-Hailuo-02")
        node.run_task.assert_not_called()

    def _reference_generate(self, node, model="MiniMax-H3", **overrides):
        kwargs = dict(
            prompt="test",
            resolution="768P",
            ratio="adaptive",
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
            reference_image_urls="https://example.invalid/a.png",
            reference_video_urls="https://example.invalid/v.mp4",
        )
        kwargs.update(overrides)
        return node.generate(**kwargs)

    def _mocked_reference_node(self):
        node = MINIMAX.DMXAPI_MiniMax_Reference2V()
        node.resolve_key = Mock(return_value="token")
        node.run_task = Mock(
            return_value=("https://example.invalid/video.mp4", "task")
        )
        node.finish = Mock(return_value="finished")
        return node

    def test_reference_payload_uses_documented_roles_and_sends_ratio(self):
        node = self._mocked_reference_node()

        result = self._reference_generate(
            node,
            reference_image_urls="https://example.invalid/a.png\nhttps://example.invalid/b.png",
            reference_video_urls="https://example.invalid/v.mp4",
            reference_audio_urls="https://example.invalid/a.mp3",
        )

        self.assertEqual(result, "finished")
        payload = node.run_task.call_args.args[0]
        self.assertEqual(payload["model"], "MiniMax-H3")
        node.run_task.assert_called_once_with(payload, "token", 8, 60)

        items = payload["input"]
        self.assertEqual(items[0], {"type": "text", "text": "test"})
        self.assertEqual(
            [item["role"] for item in items[1:]],
            ["reference_image", "reference_image", "reference_video", "reference_audio"],
        )
        self.assertEqual(
            [item["type"] for item in items[1:]],
            ["image_url", "image_url", "video_url", "audio_url"],
        )
        self.assertEqual(items[3]["video_url"], {"url": "https://example.invalid/v.mp4"})
        # 首尾幀情境會省略 ratio，多模態參考則必須照送（上游文件明文接受）
        self.assertEqual(payload["ratio"], "adaptive")

    def test_reference_requires_some_reference_material(self):
        node = self._mocked_reference_node()
        with self.assertRaisesRegex(ValueError, "參考素材"):
            self._reference_generate(
                node, reference_image_urls="", reference_video_urls="",
            )
        node.run_task.assert_not_called()

    def test_reference_requires_at_least_one_video(self):
        """DMXAPI 中轉的計費規則：只給參考圖會被上游 400 擋下，節點要先攔。"""
        node = self._mocked_reference_node()
        with self.assertRaisesRegex(ValueError, "參考影片"):
            self._reference_generate(node, reference_video_urls="")
        node.run_task.assert_not_called()

    def test_reference_requires_prompt(self):
        node = self._mocked_reference_node()
        with self.assertRaisesRegex(ValueError, "Prompt"):
            self._reference_generate(node, prompt="   ")
        node.run_task.assert_not_called()

    def test_reference_url_counts_are_capped_at_upstream_limits(self):
        node = self._mocked_reference_node()

        self._reference_generate(
            node,
            reference_image_urls="\n".join("https://example.invalid/%d.png" % i for i in range(12)),
            reference_video_urls="\n".join("https://example.invalid/%d.mp4" % i for i in range(5)),
            reference_audio_urls="\n".join("https://example.invalid/%d.mp3" % i for i in range(5)),
        )

        roles = [item.get("role") for item in node.run_task.call_args.args[0]["input"][1:]]
        self.assertEqual(roles.count("reference_image"), MINIMAX.MAX_REFERENCE_IMAGES)
        self.assertEqual(roles.count("reference_video"), MINIMAX.MAX_REFERENCE_VIDEOS)
        self.assertEqual(roles.count("reference_audio"), MINIMAX.MAX_REFERENCE_AUDIOS)

    def test_reference_removed_model_fails_before_api_work(self):
        node = MINIMAX.DMXAPI_MiniMax_Reference2V()
        node.resolve_key = Mock(side_effect=AssertionError("API key resolution reached"))
        node.run_task = Mock(side_effect=AssertionError("API submission reached"))

        with self.assertRaisesRegex(ValueError, "MiniMax-H3"):
            self._reference_generate(node, model="MiniMax-Hailuo-02")

        node.resolve_key.assert_not_called()
        node.run_task.assert_not_called()


if __name__ == "__main__":
    unittest.main()
