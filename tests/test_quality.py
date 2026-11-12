"""Quality regressions: unusable assets, scene variety and fallback timing."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("VIDEOSTUDIO_OUTPUT_DIR", tempfile.mkdtemp(prefix="vs_quality_"))

import video_studio as vs


class QualityTests(unittest.TestCase):
    def test_selected_clone_can_fail_without_changing_the_speaker(self):
        engine = vs.AudioEngine()
        with patch.object(engine.voice_cloner, "synthesize", side_effect=RuntimeError("voice service offline")), \
             patch.object(engine, "_gtts_fallback") as fallback:
            with self.assertRaisesRegex(vs.QualityError, "saved voice could not be cloned"):
                engine.generate_voiceover("Hello", my_voice=True, strict_my_voice=True)
        fallback.assert_not_called()

    def test_prompt_preserves_night_scene_without_inserting_preset_setting(self):
        result = vs.PromptEngine().enhance("A fox sleeping in a forest at midnight", "Cyberpunk")
        self.assertTrue(result.startswith("A fox sleeping in a forest at midnight."))
        self.assertNotIn("streets", result)
        self.assertNotIn("8k", result.lower())

    def test_scripts_require_complete_distinct_scene_pairs(self):
        for script in (
            "Visual: ocean\nVO:",
            "Visual: ocean\nVisual: river\nVO: Water.",
            "Visual: ocean\nVO: Water.\nVisual: ocean\nVO: More water.",
        ):
            self.assertFalse(vs.looks_like_script(script), script)
        good = "Visual: ocean\nVO: Water.\nVisual: river\nVO: Moving water."
        self.assertTrue(vs.looks_like_script(good, 2))
        self.assertFalse(vs.looks_like_script(good, 3))

    def test_no_media_does_not_become_placeholder_slide(self):
        fetcher = vs.MediaFetcher(vs.StudioConfig())
        with patch.object(fetcher, "_search_wikimedia", return_value=None), \
             patch.object(fetcher, "_generate_pollinations_image", return_value=None), \
             patch.object(fetcher, "_generate_synthetic_image") as placeholder:
            with self.assertRaisesRegex(vs.QualityError, "No usable visual"):
                fetcher.fetch_scene_visual("ocean waves")
        placeholder.assert_not_called()

    def test_failed_narration_does_not_become_silence(self):
        engine = vs.AudioEngine()
        with tempfile.TemporaryDirectory() as tmp, \
             patch("gtts.gTTS", side_effect=OSError("offline")), \
             patch.object(engine, "_silence") as silence:
            out = Path(tmp) / "voice.mp3"
            out.write_bytes(b"partial audio")
            with self.assertRaisesRegex(vs.QualityError, "Narration could not"):
                engine._gtts_fallback("Hello", "English", out)
            self.assertFalse(out.exists())
        silence.assert_not_called()

    def test_stock_selection_varies_shots_and_reuses_cached_downloads(self):
        class Response:
            status_code = 200

            def json(self):
                return {"videos": [
                    {"id": i, "video_files": [
                        {"width": 1920, "height": 1080, "link": f"https://example.com/{i}.mp4"}
                    ]} for i in (1, 2)
                ]}

        fetcher = vs.MediaFetcher(vs.StudioConfig(pexels_api_key="test"))
        with tempfile.TemporaryDirectory() as tmp:
            fetcher.cache_dir = Path(tmp)

            def download(url, out, **kwargs):
                out.write_bytes(b"x" * 50000)
                return str(out)

            with patch("requests.get", return_value=Response()), \
                 patch.object(fetcher, "_download", side_effect=download) as downloader:
                first = fetcher._search_pexels_video("ocean", 1920, 1080)
                second = fetcher._search_pexels_video("ocean", 1920, 1080)
                third = fetcher._search_pexels_video("ocean", 1920, 1080)
            self.assertNotEqual(first, second)
            self.assertEqual(first, third)
            self.assertEqual(downloader.call_count, 2)

    def test_stock_ranking_accounts_for_portrait_crop(self):
        score = vs.MediaFetcher._framing_score
        portrait = score({"width": 1080, "height": 1920}, 1080, 1920)
        landscape = score({"width": 1920, "height": 1080}, 1080, 1920)
        self.assertLess(portrait, landscape)

    def test_motion_progress_spans_scene_duration(self):
        with patch.object(vs, "run_ffmpeg") as ffmpeg:
            vs.VideoEngine.image_to_video_ken_burns(Path("still.jpg"), 20, 320, 180, fps=30)
        args = ffmpeg.call_args.args[0]
        self.assertIn("on/599", args[args.index("-vf") + 1])
        self.assertIn("setsar=1", args[args.index("-vf") + 1])

    def test_hard_cut_fallback_preserves_crossfade_timeline(self):
        calls = []

        def run(args):
            calls.append(args)
            if len(calls) == 1:
                raise vs.FFmpegError("xfade unavailable")

        with patch.object(vs, "run_ffmpeg", side_effect=run), \
             patch.object(vs, "ffprobe_duration", return_value=4.0):
            vs.VideoEngine.crossfade_concat([Path("a.mp4"), Path("b.mp4")],
                                            Path("out.mp4"), crossfade=0.4, size=(320, 180))
        graph = calls[1][calls[1].index("-filter_complex") + 1]
        self.assertIn("trim=duration=3.600000", graph)
        self.assertIn("setpts=PTS-STARTPTS", graph)


if __name__ == "__main__":
    unittest.main()
