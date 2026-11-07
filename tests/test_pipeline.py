"""Regression tests for media-pipeline bugs (no network, no Gradio).

FFmpeg-backed tests run only when an ffmpeg binary is available
(system PATH or the imageio-ffmpeg wheel from requirements.txt).
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("VIDEOSTUDIO_OUTPUT_DIR", tempfile.mkdtemp(prefix="vs_test_out_"))

import video_studio as vs  # noqa: E402  (env var must be set before import)

TEMPLATE_SCRIPT = (
    "Visual: aerial pyramids golden hour sunrise\n"
    "VO: For five thousand years, the pyramids guarded secrets.\n"
    "Visual: close up hieroglyphs inside tomb torches\n"
    "VO: Carved in stone, architects encoded knowledge of the stars.\n"
    "Visual: desert night sky milky way over pyramids\n"
    "VO: They remain an eternal monument to wonder."
)


def _ffmpeg_available() -> bool:
    try:
        subprocess.run([vs.ffmpeg_exe(), "-version"], capture_output=True, timeout=30, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


HAS_FFMPEG = _ffmpeg_available()


class ScriptParserTests(unittest.TestCase):
    def setUp(self):
        self.studio = object.__new__(vs.VideoStudio)

    def test_consecutive_scenes_without_blank_lines_are_split(self):
        scenes = self.studio._parse_script_scenes(TEMPLATE_SCRIPT)
        self.assertEqual(len(scenes), 3)
        for visual, vo in scenes:
            self.assertNotIn("Visual:", vo)
            self.assertNotIn("VO:", visual)
        self.assertEqual(scenes[2][0], "desert night sky milky way over pyramids")

    def test_markdown_labels_and_continuation_lines(self):
        scenes = self.studio._parse_script_scenes(
            "1. **Visual:** forest at dawn\n**VO:** Light returns\nto the valley.\n\nVisual: river"
        )
        self.assertEqual(scenes, [("forest at dawn", "Light returns to the valley."), ("river", "river")])

    def test_builtin_story_templates_have_multiple_scenes(self):
        try:
            import app  # noqa: F401
        except ImportError:
            self.skipTest("gradio not installed")
        import app as app_mod
        for key, preset in app_mod.DEMO_STORY_PRESETS.items():
            with self.subTest(template=key):
                self.assertEqual(len(self.studio._parse_script_scenes(preset[1])), 3)


class PureHelperTests(unittest.TestCase):
    def test_output_size_keeps_aspect_and_even_dims(self):
        self.assertEqual(vs.output_size("16:9"), (1872, 1080))   # from the 832x480 bucket
        w, h = vs.output_size("9:16")
        self.assertGreater(h, w)
        self.assertEqual(w, 1080)
        w, h = vs.output_size("21:9")
        self.assertEqual(h, 1080)
        self.assertGreater(w / h, 2.2)
        for ar in vs.ASPECT_SIZES:
            w, h = vs.output_size(ar)
            self.assertEqual((w % 2, h % 2), (0, 0))

    def test_scene_slots_account_for_crossfade(self):
        slots = [vs.scene_slot_seconds(d, i, 3) for i, d in enumerate([2.0, 4.0, 1.0])]
        self.assertAlmostEqual(slots[0], 3.5 - 0.4)
        self.assertAlmostEqual(slots[1], 4.5 - 0.4)
        self.assertAlmostEqual(slots[2], 3.5)          # last scene keeps its tail
        # Narration must always fit inside its slot (never truncated).
        for vo in (0.0, 1.0, 3.0, 10.0):
            self.assertGreater(vs.scene_slot_seconds(vo, 0, 2), vo)

    def test_vo_timeline_filter_pads_and_trims_each_clip(self):
        f = vs.vo_timeline_filter([3.1, 3.5])
        self.assertIn("[0:a]aresample=44100", f)
        self.assertIn("apad=whole_dur=3.100,atrim=0:3.100", f)
        self.assertIn("[a0][a1]concat=n=2:v=0:a=1[outa]", f)

    def test_upscale_filter_preserves_aspect(self):
        self.assertIsNone(vs.VideoEngine.upscale_filter("480p"))
        f = vs.VideoEngine.upscale_filter("4K")
        self.assertIn("2160", f)
        self.assertIn("-2", f)

    def test_offline_prompt_enhancer_is_stable_across_processes(self):
        code = "import video_studio as v; print(v.PromptEngine().enhance('A quiet forest'))"
        env = dict(os.environ)
        outs = {
            subprocess.run(
                [os.sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                cwd=str(vs.STUDIO_ROOT), env={**env, "PYTHONHASHSEED": seed}, timeout=120,
            ).stdout.strip().splitlines()[-1]
            for seed in ("1", "2")
        }
        self.assertEqual(len(outs), 1)

    def test_run_ffmpeg_missing_binary_raises_ffmpeg_error(self):
        with patch.object(vs, "ffmpeg_exe", return_value="definitely-not-ffmpeg-xyz"):
            with self.assertRaises(vs.FFmpegError):
                vs.run_ffmpeg(["-version"])

    def test_gemini_script_prompt_includes_language(self):
        try:
            import google.genai  # noqa: F401
        except ImportError:
            self.skipTest("google-genai not installed")
        captured = {}

        class FakeModels:
            def generate_content(self, model, contents):
                captured["contents"] = contents
                raise RuntimeError("stop")

        class FakeClient:
            def __init__(self, api_key):
                self.models = FakeModels()

        cfg = vs.StudioConfig(gemini_api_key="k")
        with patch("google.genai.Client", FakeClient, create=True):
            try:
                vs.FreeLLMPromptEnhancer.generate_script("Oceans", 2, "Urdu", cfg)
            except Exception:
                pass
        if "contents" not in captured:
            self.skipTest("google-genai not installed")
        self.assertNotIn("{language}", captured["contents"])
        self.assertIn("in Urdu", captured["contents"])


class NetworkHardeningTests(unittest.TestCase):
    def test_gemini_models_env_override(self):
        with patch.dict(os.environ, {"GEMINI_MODELS": " a , b ,"}):
            self.assertEqual(vs.gemini_models(), ["a", "b"])
        with patch.dict(os.environ, {"GEMINI_MODELS": ""}):
            self.assertNotIn("gemini-1.5-flash", vs.gemini_models())

    def test_failed_or_tiny_download_leaves_no_file(self):
        class Resp:
            def __init__(self, status, body, ctype="video/mp4"):
                self.status, self.body, self.headers = status, body, {"Content-Type": ctype}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def raise_for_status(self):
                if self.status >= 400:
                    raise RuntimeError(f"HTTP {self.status}")

            def iter_content(self, chunk_size):
                yield self.body

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "clip.mp4"
            for resp in (Resp(500, b"x" * 5000), Resp(200, b"x" * 10), Resp(200, b"<html>" * 999, "text/html")):
                with patch("requests.get", return_value=resp):
                    self.assertIsNone(vs.MediaFetcher._download("https://e/x?key=SECRET", out, min_bytes=100))
                self.assertEqual(list(Path(tmp).iterdir()), [])
            with patch("requests.get", return_value=Resp(200, b"x" * 500)):
                self.assertEqual(vs.MediaFetcher._download("https://e/x", out, min_bytes=100), str(out))
            self.assertEqual(out.stat().st_size, 500)

    def test_pixabay_key_is_not_built_into_url(self):
        cfg = vs.StudioConfig(pixabay_api_key="PIXKEY")
        fetcher = vs.MediaFetcher(cfg)
        calls = []

        class R:
            status_code = 200

            def json(self):
                return {"hits": []}

        def fake_get(url, **kw):
            calls.append((url, kw))
            return R()

        with patch("requests.get", fake_get):
            fetcher._search_pixabay_video("ocean waves")
        url, kw = calls[0]
        self.assertNotIn("PIXKEY", url)
        self.assertEqual(kw["params"]["key"], "PIXKEY")


class BackendLoopTests(unittest.TestCase):
    def test_failed_backend_does_not_corrupt_next_chunk_width(self):
        """Regression: `w = quota_wait_seconds(...)` overwrote the clip width."""
        studio = object.__new__(vs.VideoStudio)
        studio.config = vs.StudioConfig(backend_order=["a", "b"], allow_test_pattern_fallback=False)
        widths = []

        class Failing(vs.GenerationBackend):
            name = "a"

            def generate(self, req, out_path, progress=lambda m: None):
                widths.append(req.width)
                raise RuntimeError("model exploded")

        class Ok(vs.GenerationBackend):
            name = "b"

            def generate(self, req, out_path, progress=lambda m: None):
                widths.append(req.width)
                Path(out_path).write_bytes(b"x")
                return out_path

        studio.backends = {"a": Failing(), "b": Ok()}

        class Q:
            def is_cancelled(self, _):
                return False

        studio.queue = Q()
        studio.video_engine = vs.VideoEngine()
        sett = vs.JobSettings(prompt="p", duration=10.0, aspect_ratio="16:9")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(vs.FreeLLMPromptEnhancer, "expand_with_ai", return_value="p"), \
                patch.object(vs, "ffprobe_duration", return_value=0.0), \
                patch.object(vs, "run_ffmpeg"), \
                patch.object(vs.VideoEngine, "crossfade_concat", side_effect=RuntimeError("stop here")):
            with self.assertRaisesRegex(RuntimeError, "stop here"):
                studio._execute_ai_diffusion_mode(sett, Path(tmp), Path(tmp) / "o.mp4", lambda *a, **k: None)
        self.assertEqual(widths, [832, 832, 832, 832])


class EdgeTTSTests(unittest.TestCase):
    def test_word_boundaries_are_requested(self):
        try:
            import edge_tts
        except ImportError:
            self.skipTest("edge-tts not installed")
        seen = {}

        class FakeCommunicate:
            def __init__(self, text, voice, rate="+0%", pitch="+0Hz", boundary="SentenceBoundary"):
                seen["boundary"] = boundary

            async def stream(self):
                yield {"type": "audio", "data": b"\0" * 400}
                yield {"type": "WordBoundary", "text": "Hi", "offset": 0, "duration": 5_000_000}
                yield {"type": "SentenceBoundary", "text": "Hi", "offset": 0, "duration": 5_000_000}

        with tempfile.TemporaryDirectory() as tmp, patch.object(edge_tts, "Communicate", FakeCommunicate):
            out, events = vs.AudioEngine().generate_voiceover("Hi", out_path=Path(tmp) / "vo.mp3")
        self.assertEqual(seen["boundary"], "WordBoundary")
        self.assertEqual(events, [{"text": "Hi", "offset": 0.0, "duration": 0.5}])


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not available")
class FFmpegPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vs_ff_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _clip(self, name, w, h, dur=1.2, audio=False):
        out = self.tmp / name
        args = ["-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=16:duration={dur}"]
        if audio:
            args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}", "-c:a", "aac"]
        vs.run_ffmpeg([*args, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(out)])
        return out

    def test_crossfade_accepts_clips_of_different_sizes(self):
        a = self._clip("a.mp4", 832, 480)
        b = self._clip("b.mp4", 720, 480)
        out = self.tmp / "joined.mp4"
        vs.VideoEngine.crossfade_concat([a, b], out, crossfade=0.3)
        self.assertEqual(vs.probe_video_size(out), (832, 480))
        self.assertGreater(vs.ffprobe_duration(out), 1.8)

    def test_upscale_keeps_portrait_orientation(self):
        src = self._clip("p.mp4", 480, 832, dur=0.5)
        out = self.tmp / "up.mp4"
        vs.VideoEngine.upscale_video(src, "720p", out)
        w, h = vs.probe_video_size(out)
        self.assertEqual(w, 720)
        self.assertGreater(h, w)

    def test_watermark_keeps_audio(self):
        src = self._clip("v.mp4", 320, 240, audio=True)
        logo = self.tmp / "logo.png"
        vs.run_ffmpeg(["-f", "lavfi", "-i", "color=c=red:size=64x64", "-frames:v", "1", str(logo)])
        out = self.tmp / "wm.mp4"
        vs.VideoEngine.apply_watermark(src, logo, out)
        self.assertIn("Audio:", vs._ffmpeg_info(out))

    def test_burn_subtitles_handles_awkward_folder_names(self):
        folder = self.tmp / "Kami's videos, v2"
        folder.mkdir()
        src = self._clip("v.mp4", 320, 240, audio=True)
        srt = folder / "subs.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
        out = folder / "burned.mp4"
        try:
            vs.SubtitleEngine.burn_subtitles(src, srt, out)
        except vs.FFmpegError as exc:
            if "No such filter" in str(exc) or "subtitles" in str(exc) and "not found" in str(exc).lower():
                self.skipTest("this ffmpeg build lacks libass")
            raise
        self.assertGreater(vs.ffprobe_duration(out), 0.5)

    def test_vo_timeline_concat_mixes_sample_formats(self):
        a = self.tmp / "a.mp3"
        b = self.tmp / "b.mp3"
        vs.run_ffmpeg(["-f", "lavfi", "-i", "sine=duration=1:sample_rate=24000", "-ac", "1", str(a)])
        vs.run_ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "0.5", str(b)])
        out = self.tmp / "full.mp3"
        vs.run_ffmpeg(["-i", str(a), "-i", str(b), "-filter_complex",
                       vs.vo_timeline_filter([2.0, 1.5]), "-map", "[outa]", str(out)])
        self.assertAlmostEqual(vs.ffprobe_duration(out), 3.5, delta=0.15)


if __name__ == "__main__":
    unittest.main()
