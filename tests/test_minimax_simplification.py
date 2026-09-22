import base64
import importlib.util
import inspect
import io
import pathlib
import sys
import types
import unittest
import wave
from unittest.mock import Mock, patch

import torch


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
COMMON = sys.modules[PACKAGE_NAME + ".dmxapi_common"]
MINIMAX = sys.modules[PACKAGE_NAME + ".dmxapi_minimax_h3_nodes"]


class MiniMaxSimplificationTests(unittest.TestCase):
    def test_audio_to_wav_data_url_encodes_first_batch_as_pcm16(self):
        audio = {
            "waveform": torch.zeros((2, 2, 32000)),
            "sample_rate": 16000,
        }

        data_url, duration, raw_size = COMMON.audio_to_wav_data_url(audio)

        self.assertTrue(data_url.startswith("data:audio/wav;base64,"))
        raw = base64.b64decode(data_url.split(",", 1)[1])
        with wave.open(io.BytesIO(raw), "rb") as wav_file:
            self.assertEqual(wav_file.getnchannels(), 2)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getframerate(), 16000)
            self.assertEqual(wav_file.getnframes(), 32000)
        self.assertEqual(duration, 2.0)
        self.assertEqual(raw_size, len(raw))

    def test_video_to_data_url_reads_comfy_video_stream(self):
        class FakeVideo:
            def get_duration(self):
                return 4.0

            def get_container_format(self):
                return "mov,mp4,m4a,3gp,3g2,mj2"

            def get_stream_source(self):
                return io.BytesIO(b"fake-mp4")

        data_url, duration, raw_size = COMMON.video_to_data_url(FakeVideo())

        self.assertEqual(data_url, "data:video/mp4;base64,ZmFrZS1tcDQ=")
        self.assertEqual(duration, 4.0)
        self.assertEqual(raw_size, 8)

    def test_video_to_data_url_transcodes_unsupported_container_lazily(self):
        class FakeVideo:
            def __init__(self):
                self.saved_with = None

            def get_duration(self):
                return 3.0

            def get_container_format(self):
                return "webm"

            def save_to(self, output, format, codec):
                self.saved_with = (format, codec)
                output.write(b"fake-transcoded-mp4")

        class FakeVideoContainer:
            MP4 = object()

        class FakeVideoCodec:
            H264 = object()

        comfy_api = types.ModuleType("comfy_api")
        comfy_api.__path__ = []
        latest = types.ModuleType("comfy_api.latest")
        latest.VideoContainer = FakeVideoContainer
        latest.VideoCodec = FakeVideoCodec
        video = FakeVideo()

        with patch.dict(
            sys.modules,
            {"comfy_api": comfy_api, "comfy_api.latest": latest},
        ):
            data_url, duration, raw_size = COMMON.video_to_data_url(video)

        self.assertTrue(data_url.startswith("data:video/mp4;base64,"))
        self.assertEqual(video.saved_with, (FakeVideoContainer.MP4, FakeVideoCodec.H264))
        self.assertEqual(duration, 3.0)
        self.assertEqual(raw_size, len(b"fake-transcoded-mp4"))

    def test_video_to_data_url_materializes_trimmed_mp4(self):
        class FakeVideo:
            def get_duration(self):
                return 3.0

            def get_container_format(self):
                return "mov,mp4,m4a,3gp,3g2,mj2"

            def get_active_trim_window(self):
                return (1.0, 3.0)

            def get_stream_source(self):
                raise AssertionError("trimmed VIDEO must not reuse source bytes")

            def save_to(self, output, format, codec):
                output.write(b"trimmed-mp4")

        class FakeVideoContainer:
            MP4 = object()

        class FakeVideoCodec:
            H264 = object()

        comfy_api = types.ModuleType("comfy_api")
        comfy_api.__path__ = []
        latest = types.ModuleType("comfy_api.latest")
        latest.VideoContainer = FakeVideoContainer
        latest.VideoCodec = FakeVideoCodec

        with patch.dict(
            sys.modules,
            {"comfy_api": comfy_api, "comfy_api.latest": latest},
        ):
            data_url, _, _ = COMMON.video_to_data_url(FakeVideo())

        self.assertEqual(
            base64.b64decode(data_url.split(",", 1)[1]),
            b"trimmed-mp4",
        )

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

    def test_reference_node_exposes_three_video_and_audio_inputs(self):
        optional = MINIMAX.DMXAPI_MiniMax_Reference2V.INPUT_TYPES()["optional"]
        for index in range(1, 4):
            self.assertEqual(optional[f"reference_video_{index}"], ("VIDEO",))
            self.assertEqual(optional[f"reference_audio_{index}"], ("AUDIO",))

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

    def test_local_video_and_audio_precede_url_media(self):
        node = self._mocked_reference_node()
        with patch.object(
            MINIMAX,
            "video_to_data_url",
            return_value=("data:video/mp4;base64,VjE=", 3.0, 2),
        ), patch.object(
            MINIMAX,
            "audio_to_wav_data_url",
            return_value=("data:audio/wav;base64,QTE=", 3.0, 2),
        ):
            self._reference_generate(
                node,
                reference_image_urls="",
                reference_video_1=object(),
                reference_audio_1={"waveform": object()},
            )

        items = node.run_task.call_args.args[0]["input"][1:]
        self.assertEqual(
            [item["role"] for item in items],
            ["reference_video", "reference_video", "reference_audio"],
        )
        self.assertEqual(items[0]["video_url"]["url"], "data:video/mp4;base64,VjE=")
        self.assertEqual(
            items[1]["video_url"]["url"],
            "https://example.invalid/v.mp4",
        )
        self.assertEqual(items[2]["audio_url"]["url"], "data:audio/wav;base64,QTE=")

    def test_local_video_inputs_take_the_three_video_slots_before_urls(self):
        node = self._mocked_reference_node()
        local_urls = [
            "data:video/mp4;base64,VjE=",
            "data:video/mp4;base64,VjI=",
        ]
        with patch.object(
            MINIMAX,
            "video_to_data_url",
            side_effect=[(url, 3.0, 2) for url in local_urls],
        ):
            self._reference_generate(
                node,
                reference_image_urls="",
                reference_video_urls="\n".join(
                    f"https://example.invalid/{index}.mp4" for index in range(3)
                ),
                reference_video_1=object(),
                reference_video_2=object(),
            )

        videos = [
            item["video_url"]["url"]
            for item in node.run_task.call_args.args[0]["input"]
            if item["type"] == "video_url"
        ]
        self.assertEqual(
            videos,
            local_urls + ["https://example.invalid/0.mp4"],
        )

    def test_local_images_take_the_nine_image_slots_before_urls(self):
        node = self._mocked_reference_node()
        local_urls = [f"data:image/jpeg;base64,{index}" for index in range(9)]
        node._encode_images = Mock(return_value=local_urls)

        self._reference_generate(
            node,
            reference_images=object(),
            reference_image_urls="https://example.invalid/url.png",
            reference_video_urls="",
        )

        images = [
            item["image_url"]["url"]
            for item in node.run_task.call_args.args[0]["input"]
            if item["type"] == "image_url"
        ]
        self.assertEqual(images, local_urls)

    def _assert_local_media_rejected(self, kind, helper_results, message_pattern,
                                     max_bytes=None):
        node = self._mocked_reference_node()
        kwargs = {
            "reference_image_urls": (
                "https://example.invalid/a.png" if kind == "audio" else ""
            ),
            "reference_video_urls": "",
        }
        for index in range(len(helper_results)):
            kwargs[f"reference_{kind}_{index + 1}"] = object()

        helper_name = (
            "video_to_data_url" if kind == "video" else "audio_to_wav_data_url"
        )
        patches = [patch.object(MINIMAX, helper_name, side_effect=helper_results)]
        if max_bytes is not None:
            constant_name = (
                "MAX_REFERENCE_VIDEO_BYTES"
                if kind == "video"
                else "MAX_REFERENCE_AUDIO_BYTES"
            )
            patches.append(patch.object(MINIMAX, constant_name, max_bytes))

        with patches[0]:
            if len(patches) == 2:
                with patches[1]:
                    with self.assertRaisesRegex(ValueError, message_pattern):
                        self._reference_generate(node, **kwargs)
            else:
                with self.assertRaisesRegex(ValueError, message_pattern):
                    self._reference_generate(node, **kwargs)

        node.resolve_key.assert_not_called()
        node.run_task.assert_not_called()

    def test_local_video_duration_limits_are_checked_before_api_work(self):
        for name, results, pattern in (
            ("too short", [("data:video/mp4;base64,AA==", 1.9, 1)], "影片.*2.*15"),
            ("too long", [("data:video/mp4;base64,AA==", 15.1, 1)], "影片.*2.*15"),
            (
                "total too long",
                [
                    ("data:video/mp4;base64,AA==", 8.0, 1),
                    ("data:video/mp4;base64,AQ==", 7.1, 1),
                ],
                "影片.*合計.*15",
            ),
        ):
            with self.subTest(name=name):
                self._assert_local_media_rejected("video", results, pattern)

    def test_local_audio_duration_limits_are_checked_before_api_work(self):
        for name, results, pattern in (
            ("too short", [("data:audio/wav;base64,AA==", 1.9, 1)], "音訊.*2.*15"),
            ("too long", [("data:audio/wav;base64,AA==", 15.1, 1)], "音訊.*2.*15"),
            (
                "total too long",
                [
                    ("data:audio/wav;base64,AA==", 8.0, 1),
                    ("data:audio/wav;base64,AQ==", 7.1, 1),
                ],
                "音訊.*合計.*15",
            ),
        ):
            with self.subTest(name=name):
                self._assert_local_media_rejected("audio", results, pattern)

    def test_local_media_size_limits_are_checked_before_api_work(self):
        self._assert_local_media_rejected(
            "video",
            [("data:video/mp4;base64,AA==", 3.0, 11)],
            "影片.*大小.*公網 URL",
            max_bytes=10,
        )
        self._assert_local_media_rejected(
            "audio",
            [("data:audio/wav;base64,AA==", 3.0, 11)],
            "音訊.*大小.*公網 URL",
            max_bytes=10,
        )

    def test_complete_json_body_limit_is_checked_before_api_key(self):
        node = self._mocked_reference_node()
        oversized_data_url = "data:video/mp4;base64," + ("A" * 256)

        with patch.object(
            MINIMAX,
            "video_to_data_url",
            return_value=(oversized_data_url, 3.0, 1),
        ), patch.object(MINIMAX, "MAX_BODY_BYTES", 128):
            with self.assertRaisesRegex(ValueError, "請求體.*公網 URL"):
                self._reference_generate(
                    node,
                    reference_image_urls="",
                    reference_video_urls="",
                    reference_video_1=object(),
                )

        node.resolve_key.assert_not_called()
        node.run_task.assert_not_called()

    def test_reference_requires_some_reference_material(self):
        node = self._mocked_reference_node()
        with self.assertRaisesRegex(ValueError, "參考素材"):
            self._reference_generate(
                node, reference_image_urls="", reference_video_urls="",
            )
        node.run_task.assert_not_called()

    def test_reference_images_work_without_video(self):
        """官方 H3 規格允許只用 reference_image，不應強制附加影片。"""
        node = self._mocked_reference_node()
        result = self._reference_generate(node, reference_video_urls="")

        self.assertEqual(result, "finished")
        items = node.run_task.call_args.args[0]["input"]
        self.assertEqual(
            [item.get("role") for item in items[1:]],
            ["reference_image"],
        )

    def test_reference_audio_cannot_be_the_only_material(self):
        """reference_audio 必須搭配至少一張參考圖或一段參考影片。"""
        node = self._mocked_reference_node()
        with self.assertRaisesRegex(ValueError, "音訊.*圖片.*影片"):
            self._reference_generate(
                node,
                reference_image_urls="",
                reference_video_urls="",
                reference_audio_urls="https://example.invalid/a.mp3",
            )
        node.run_task.assert_not_called()

    def test_local_reference_audio_cannot_be_the_only_material(self):
        node = self._mocked_reference_node()
        with patch.object(
            MINIMAX,
            "audio_to_wav_data_url",
            return_value=("data:audio/wav;base64,QTE=", 3.0, 2),
        ):
            with self.assertRaisesRegex(ValueError, "音訊.*圖片.*影片"):
                self._reference_generate(
                    node,
                    reference_image_urls="",
                    reference_video_urls="",
                    reference_audio_1=object(),
                )
        node.resolve_key.assert_not_called()
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
