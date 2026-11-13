"""Regression tests for media-pipeline bugs (no network, no Gradio).

FFmpeg-backed tests run only when an ffmpeg binary is available
(system PATH or the imageio-ffmpeg wheel from requirements.txt).
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("VIDEOSTUDIO_OUTPUT_DIR", tempfile.mkdtemp(prefix="vs_test_out_"))

import video_studio as vs  # noqa: E402  (env var must be set before import)

vs.SCRIPT_RETRY_PAUSE = 0.0      # no real waiting between retries in tests

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

    def test_offline_script_writer_does_not_substitute_generic_filler(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}), patch("requests.get", side_effect=OSError("offline")):
            with self.assertRaisesRegex(vs.QualityError, "Paste a script in English"):
                vs.FreeLLMPromptEnhancer.generate_script("Oceans", 4, "English",
                                                        vs.StudioConfig(gemini_api_key=""))


class PureHelperTests(unittest.TestCase):
    def test_output_size_keeps_aspect_and_even_dims(self):
        self.assertEqual(vs.output_size("16:9"), (1920, 1080))
        self.assertEqual(vs.output_size("9:16"), (1080, 1920))
        self.assertEqual(vs.output_size("1:1"), (1080, 1080))
        self.assertEqual(vs.output_size("16:9", 2160), (3840, 2160))
        self.assertEqual(vs.output_size("nonsense"), (1920, 1080))
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
        with patch("google.genai.Client", FakeClient, create=True), \
                patch("requests.get", side_effect=OSError("offline")):
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
            models = vs.gemini_models()
            self.assertNotIn("gemini-1.5-flash", models)
            self.assertNotIn("gemini-2.5-flash", models)
            self.assertNotIn("gemini-2.5-flash-lite", models)
            self.assertIn("gemini-3.5-flash", models)

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


def _bare_queue(tmp: str, studio=None) -> "vs.JobQueue":
    import threading
    q = object.__new__(vs.JobQueue)
    q.queue_file = Path(tmp) / "jobs.json"
    q._lock = threading.RLock()
    q._jobs, q._cancelled_jobs = {}, set()
    q._last_persist, q._paused, q._running = 0.0, False, True
    q.studio = studio
    return q


class JobQueueRobustnessTests(unittest.TestCase):
    def _run_once(self, q):
        # Stop the worker loop the first time it would idle.
        def stop(_):
            q._running = False
        with patch.object(vs.time, "sleep", side_effect=stop):
            q._worker_loop()

    def test_job_without_settings_fails_instead_of_spinning(self):
        class Studio:
            config = vs.StudioConfig()

            def execute_job(self, *a):
                raise AssertionError("must not run")

        with tempfile.TemporaryDirectory() as tmp:
            q = _bare_queue(tmp, Studio())
            q._jobs["x"] = vs.JobStatus(job_id="x", settings=None)
            self._run_once(q)
            self.assertEqual(q._jobs["x"].stage, vs.Stage.FAILED)

    def test_missing_required_asset_fails_without_automatic_retry(self):
        class Studio:
            config = vs.StudioConfig()

            def execute_job(self, *a):
                raise vs.QualityError("No usable visual was found")

        with tempfile.TemporaryDirectory() as tmp:
            q = _bare_queue(tmp, Studio())
            q._jobs["x"] = vs.JobStatus(job_id="x", settings=vs.JobSettings(job_id="x"))
            self._run_once(q)
            self.assertEqual(q._jobs["x"].stage, vs.Stage.FAILED)
            self.assertEqual(q._jobs["x"].retries, 0)

    def test_pipeline_returning_without_result_is_failed(self):
        class Studio:
            config = vs.StudioConfig()

            def execute_job(self, *a):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            q = _bare_queue(tmp, Studio())
            q._jobs["x"] = vs.JobStatus(job_id="x", settings=vs.JobSettings(job_id="x"))
            self._run_once(q)
            self.assertEqual(q._jobs["x"].stage, vs.Stage.FAILED)

    def test_quota_deferrals_are_capped(self):
        class Studio:
            config = vs.StudioConfig(max_deferrals_per_job=2)

            def execute_job(self, *a):
                raise vs.QuotaExhausted(60)

        with tempfile.TemporaryDirectory() as tmp:
            q = _bare_queue(tmp, Studio())
            job = vs.JobStatus(job_id="x", settings=vs.JobSettings(job_id="x"))
            q._jobs["x"] = job
            self._run_once(q)
            self.assertEqual((job.stage, job.deferrals), (vs.Stage.QUEUED, 1))
            job.not_before = 0
            job.deferrals = 2
            q._running = True
            self._run_once(q)
            self.assertEqual(job.stage, vs.Stage.FAILED)

    def test_one_bad_entry_does_not_drop_the_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = _bare_queue(tmp)
            q.queue_file.write_text(
                '{"good": {"stage": "Completed", "settings": {"prompt": "p"}, "deferrals": 3},'
                ' "bad": "not-an-object", "worse": {"progress": "NaN-ish"}}',
                encoding="utf-8",
            )
            q._load()
            self.assertIn("good", q._jobs)
            self.assertEqual(q._jobs["good"].deferrals, 3)
            self.assertNotIn("bad", q._jobs)

    def test_corrupt_queue_file_is_kept_aside(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = _bare_queue(tmp)
            q.queue_file.write_text("{truncated", encoding="utf-8")
            q._load()
            self.assertEqual(q._jobs, {})
            self.assertEqual(len(list(Path(tmp).glob("jobs.json.corrupt-*"))), 1)


class BackendTimeoutTests(unittest.TestCase):
    def test_hung_remote_call_times_out_as_transient(self):
        import concurrent.futures

        class Job:
            cancelled = False

            def result(self, timeout=None):
                raise concurrent.futures.TimeoutError()

            def cancel(self):
                Job.cancelled = True

        class Client:
            def submit(self, *a, **k):
                return Job()

        with self.assertRaises(TimeoutError) as ctx:
            vs.ColabBackend._predict(Client(), "p", api_name="/generate", timeout=1)
        self.assertTrue(Job.cancelled)
        self.assertTrue(vs.is_transient_error(str(ctx.exception)))

    def test_colab_backend_normalizes_url_and_resets_client_on_error(self):
        cfg = vs.StudioConfig(colab_url="  https://demo.gradio.live/ ")
        be = vs.ColabBackend(cfg)
        self.assertTrue(be.available())
        self.assertEqual(be._normalized_url(), "https://demo.gradio.live")
        class BrokenClient:
            def submit(self, *a, **k):
                raise RuntimeError("boom")

        be._client = BrokenClient()
        be._client_url = "https://demo.gradio.live"
        with patch.object(be, "_get_client", return_value=be._client):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                be.generate(vs.GenerationRequest(prompt="test"), Path("out.mp4"))
        self.assertIsNone(be._client)
        self.assertEqual(be._client_url, "")

    def test_colab_check_connection_statuses(self):
        import requests

        be = vs.ColabBackend(vs.StudioConfig())
        self.assertEqual(be.check_connection(), (False, "not configured"))

        be.config.colab_url = "https://demo.gradio.live/"

        class FakeResp:
            def __init__(self, code, payload=None):
                self.status_code = code
                self._payload = payload

            def json(self):
                if isinstance(self._payload, Exception):
                    raise self._payload
                return self._payload

        with patch("requests.get", return_value=FakeResp(404)):
            self.assertEqual(be.check_connection(), (False, "offline or expired (HTTP 404)"))

        with patch(
            "requests.get",
            return_value=FakeResp(200, {"named_endpoints": {"/generate": {}}}),
        ):
            self.assertEqual(be.check_connection(), (True, "connected"))

        with patch("requests.get", return_value=FakeResp(200, {"named_endpoints": {}})):
            self.assertEqual(be.check_connection(), (False, "connected, but /generate endpoint is missing"))

        with patch("requests.get", side_effect=requests.ConnectionError("down")):
            self.assertEqual(be.check_connection(), (False, "unreachable (ConnectionError)"))

        # Polled in short slices; the render itself may take up to an hour.
        self.assertGreaterEqual(vs.ColabBackend.MAX_PENDING_SECONDS, 1800)

    def test_colab_worker_notebook_and_t4_sdpa_fallback(self):
        import ast
        import sys
        import types

        nb_path = Path(__file__).resolve().parent.parent / "colab_worker.ipynb"
        nb = json.loads(nb_path.read_text(encoding="utf-8"))
        sources = ["".join(c.get("source", [])) for c in nb["cells"] if c.get("cell_type") == "code"]
        joined = "\n".join(sources)
        self.assertIn("show_error=True", joined)
        self.assertIn("PYTORCH_CUDA_ALLOC_CONF", joined)
        for src in sources:
            cleaned = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith(("%", "!")))
            ast.parse(cleaned)

        # Compile only the SDPA fallback setup block (up to CKPT_DIR) against stub torch/wan
        # to verify SM 7.5 (T4) bfloat16 -> float16 casting and chunked fallback offline.
        cell4 = next(s for s in sources if "def _sdpa_fallback" in s)
        head = cell4.split("CKPT_DIR =")[0]
        calls = []
        fail_first = [False]

        class FakeTensor:
            def __init__(self, shape, dtype="bf16", is_cuda=True):
                self.shape = tuple(shape)
                self.dtype = dtype
                self.is_cuda = is_cuda
                self.device = "cuda:0"
                self.last_cast = None

            def size(self, dim):
                return self.shape[dim]

            def type(self, dt):
                return FakeTensor(self.shape, dt, self.is_cuda)

            def __getitem__(self, item):
                if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], slice):
                    start, stop, _ = item[1].indices(self.shape[1])
                    return FakeTensor((self.shape[0], max(0, stop - start), *self.shape[2:]), self.dtype, self.is_cuda)
                return self

        class FakeLens:
            def __init__(self, vals):
                self._vals = list(vals)

            def numel(self):
                return len(self._vals)

            def __getitem__(self, idx):
                return self._vals[idx]

        class FakeSDP:
            def __enter__(self):
                return None

            def __exit__(self, *args):
                return False

        fake_attn = types.ModuleType("wan.modules.attention")
        fake_attn.FLASH_ATTN_2_AVAILABLE = False
        fake_attn.FLASH_ATTN_3_AVAILABLE = False

        def fake_attention(q, k, v, **kwargs):
            calls.append((q.shape, dict(kwargs)))
            if fail_first[0]:
                fail_first[0] = False
                raise RuntimeError("No available kernel")
            return FakeTensor(q.shape, kwargs["dtype"], q.is_cuda)

        fake_attn.attention = fake_attention
        fake_model = types.ModuleType("wan.modules.model")
        fake_wan = types.ModuleType("wan")
        fake_wan_cfg = types.ModuleType("wan.configs")
        fake_wan_cfg.WAN_CONFIGS = {}
        fake_modules = types.ModuleType("wan.modules")

        fake_torch = types.ModuleType("torch")
        fake_torch.bfloat16 = "bf16"
        fake_torch.float16 = "fp16"
        fake_torch.cat = lambda seq, dim=0: FakeTensor(
            (seq[0].shape[0], sum(t.shape[1] for t in seq), *seq[0].shape[2:]),
            seq[0].dtype,
            seq[0].is_cuda,
        )
        fake_torch.cuda = types.SimpleNamespace(get_device_capability=lambda dev: (7, 5))
        fake_torch.nn = types.ModuleType("torch.nn")
        fake_torch_attn = types.ModuleType("torch.nn.attention")
        fake_torch_attn.SDPBackend = types.SimpleNamespace(FLASH_ATTENTION=1, EFFICIENT_ATTENTION=2)
        fake_torch_attn.sdpa_kernel = lambda backends: FakeSDP()
        fake_torch.nn.attention = fake_torch_attn

        stubs = {
            "torch": fake_torch,
            "torch.nn": fake_torch.nn,
            "torch.nn.attention": fake_torch_attn,
            "wan": fake_wan,
            "wan.modules": fake_modules,
            "wan.modules.attention": fake_attn,
            "wan.modules.model": fake_model,
            "wan.configs": fake_wan_cfg,
        }
        orig_sys_path = list(sys.path)
        try:
            with patch.dict(sys.modules, stubs):
                exec(compile(head, "<colab_cell4>", "exec"), {})
            q = FakeTensor((1, 35880, 12, 128), "bf16")
            out = fake_attn.flash_attention(q, q, q, k_lens=FakeLens([35880]), q_lens=FakeLens([35880]))
            self.assertEqual(out.dtype, "bf16")
            self.assertEqual(out.shape, (1, 35880, 12, 128))
            self.assertEqual(calls[0][1]["dtype"], "fp16")
            self.assertIsNone(calls[0][1]["k_lens"])
            self.assertIsNone(calls[0][1]["q_lens"])

            calls.clear()
            fail_first[0] = True
            q2 = FakeTensor((1, 2500, 12, 128), "bf16")
            out2 = fake_attn.flash_attention(q2, q2, q2)
            self.assertEqual(out2.shape, (1, 2500, 12, 128))
            self.assertGreater(len(calls), 1)
        finally:
            sys.path[:] = orig_sys_path

    def test_free_text_reply_validation(self):
        self.assertTrue(vs.looks_like_prompt("A slow aerial shot over misty mountains at dawn."))
        for bad in ("", "short", "<html>502 Bad Gateway</html>" * 3, '{"error": "rate limited"}',
                    "Error: too many requests, please slow down and retry later", "x" * 5000):
            self.assertFalse(vs.looks_like_prompt(bad), bad[:30])


class ConfigCompatTests(unittest.TestCase):
    def test_old_config_with_retired_fields_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "studio_config.json"
            path.write_text(
                '{"output_dir": "C:/old", "wan_cooldown_hours": 3, "max_retries": 1,'
                ' "max_quota_waits_per_job": 2, "colab_url": "https://x.gradio.live",'
                ' "max_deferrals_per_job": 5}',
                encoding="utf-8",
            )
            cfg = vs.StudioConfig.load(path)
            self.assertEqual((cfg.colab_url, cfg.max_deferrals_per_job), ("https://x.gradio.live", 5))
            cfg.save(path)
            self.assertNotIn("wan_cooldown_hours", path.read_text(encoding="utf-8"))


    def test_saved_backend_order_from_an_older_version_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "studio_config.json"
            path.write_text(
                '{"backend_order": ["colab", "ltx", "cogvideox", "wan_official", "veo3_free", "test_pattern"]}',
                encoding="utf-8",
            )
            order = vs.StudioConfig.load(path).backend_order
        self.assertEqual(order, ["colab", "ltx23", "wan22", "ltx", "cogvideox", "test_pattern"])
        self.assertEqual(set(order), set(object.__new__(vs.VideoStudio).__class__.BACKEND_NAMES))


LIVE_QUOTA_ERRORS = (
    # Both copied verbatim from the live Spaces.
    "You have exceeded your ZeroGPU quota (120s requested vs. -60s left). Try again in 0:00:00. "
    "Authenticate with a Hugging Face token for more quota - https://huggingface.co/settings/tokens",
    "You have exceeded your ZeroGPU runs limit. Authenticate with a Hugging Face token for more quota "
    "- https://huggingface.co/settings/tokens",
)


class QuotaTests(unittest.TestCase):
    def test_both_live_quota_wordings_are_recognised(self):
        for text in LIVE_QUOTA_ERRORS:
            self.assertGreaterEqual(vs.quota_wait_seconds(text) or 0, 60, text)
            self.assertTrue(vs.quota_needs_token(text), text)
        self.assertIsNone(vs.quota_wait_seconds("CUDA out of memory"))
        signed_in = "You have exceeded your ZeroGPU quota (75s requested vs. 10s left). Try again in 1:30:00."
        self.assertEqual(vs.quota_wait_seconds(signed_in), 5420)
        self.assertFalse(vs.quota_needs_token(signed_in))

    def _run(self, hf_token, error):
        studio = object.__new__(vs.VideoStudio)
        studio.config = vs.StudioConfig(backend_order=["a"], hf_token=hf_token)

        class Limited(vs.GenerationBackend):
            name = "a"

            def generate(self, req, out_path, progress=lambda m: None):
                raise RuntimeError(error)

        studio.backends = {"a": Limited()}

        class Q:
            def is_cancelled(self, _):
                return False

        class Fetcher:
            calls = 0

            def fetch_scene_visual(self, *a, **k):
                Fetcher.calls += 1
                raise RuntimeError("fallback reached")

        studio.queue = Q()
        studio.video_engine = vs.VideoEngine()
        studio.media_fetcher = Fetcher()
        sett = vs.JobSettings(prompt="p", duration=5.0)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(vs.FreeLLMPromptEnhancer, "expand_with_ai", return_value="p"), \
                patch.object(vs, "ffprobe_duration", return_value=0.0):
            studio._execute_ai_diffusion_mode(sett, Path(tmp), Path(tmp) / "o.mp4", lambda *a, **k: None)

    def test_without_a_token_the_job_does_not_wait_for_quota_that_never_comes(self):
        with patch.dict(os.environ, {"HF_TOKEN": "", "HUGGINGFACE_TOKEN": ""}):
            with self.assertRaisesRegex(RuntimeError, "fallback reached"):
                self._run("", LIVE_QUOTA_ERRORS[0])

    def test_with_a_token_a_quota_error_defers_the_job(self):
        with self.assertRaises(vs.QuotaExhausted):
            self._run("hf_x", LIVE_QUOTA_ERRORS[0])


class FakeSpaceClient:
    """Stands in for gradio_client.Client with a Space's real signature."""

    def __init__(self, api_name, params, result="/tmp/does-not-matter.mp4", error=None):
        self.api_name, self.params, self.result, self.error = api_name, params, result, error
        self.sent = None

    def view_api(self, return_format="dict", print_info=False):
        return {"named_endpoints": {self.api_name: {"parameters": [
            {"parameter_name": n, "python_type": {"type": t}} for n, t in self.params]}}}

    def submit(self, *args, api_name=None, **kwargs):
        self.sent = (api_name, kwargs)
        client = self

        class Job:
            def result(self, timeout=None):
                if client.error:
                    raise RuntimeError(client.error)
                return (client.result, 7)

        return Job()


class SpaceBackendTests(unittest.TestCase):
    """Payloads must match the signatures published by each Space's app.py."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vs_space_"))
        self.clip = self.tmp / "remote.mp4"
        self.clip.write_bytes(b"video")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ltx23_payload_matches_the_space(self):
        be = vs.LTX23Backend(vs.StudioConfig())
        be._client = FakeSpaceClient("/generate_video", [
            ("input_image", "dict"), ("prompt", "str"), ("duration", "float"), ("enhance_prompt", "bool"),
            ("seed", "float"), ("randomize_seed", "bool"), ("height", "int"), ("width", "int")], str(self.clip))
        req = vs.GenerationRequest(prompt="boat", width=480, height=832, num_frames=vs.frames_for(5.0), seed=3)
        out = be.generate(req, self.tmp / "out.mp4")
        api, sent = be._client.sent
        self.assertEqual(api, "/generate_video")
        self.assertEqual((sent["width"], sent["height"]), (1024, 1536))
        self.assertEqual((sent["prompt"], sent["seed"], sent["randomize_seed"], sent["input_image"]),
                         ("boat", 3, False, None))
        self.assertAlmostEqual(sent["duration"], 5.1, places=1)
        self.assertEqual(out.read_bytes(), b"video")

    def test_wan22_uses_the_aspect_label_the_space_publishes(self):
        be = vs.Wan22Backend(vs.StudioConfig())
        be._client = FakeSpaceClient("/generate_video", [
            ("prompt", "str"), ("aspect_ratio", "Literal['16:9 (832x480)', '9:16 (480x832) HD', '1:1 (640x640)']"),
            ("steps", "float"), ("negative_prompt", "str"), ("duration_seconds", "float"),
            ("guidance_scale", "float"), ("seed", "float"), ("randomize_seed", "bool")], str(self.clip))
        req = vs.GenerationRequest(prompt="boat", width=480, height=832, num_frames=vs.frames_for(9.0), seed=-1)
        be.generate(req, self.tmp / "out.mp4")
        _, sent = be._client.sent
        self.assertEqual(sent["aspect_ratio"], "9:16 (480x832) HD")
        self.assertEqual((sent["duration_seconds"], sent["steps"], sent["randomize_seed"]), (5.0, 4, True))
        self.assertNotIn("guidance_scale_2", sent)       # dropped: this Space build does not accept it

    def test_quota_error_becomes_quota_exhausted_that_asks_for_a_token(self):
        be = vs.LTX23Backend(vs.StudioConfig())
        be._client = FakeSpaceClient("/generate_video", [("prompt", "str")], error=LIVE_QUOTA_ERRORS[1])
        with self.assertRaises(vs.QuotaExhausted) as ctx:
            be.generate(vs.GenerationRequest(prompt="boat"), self.tmp / "out.mp4")
        self.assertTrue(vs.quota_needs_token(str(ctx.exception)))

    def test_cogvideox_is_skipped_for_tall_videos(self):
        be = vs.CogVideoXBackend(vs.StudioConfig())
        self.assertEqual(be.SPACE, "zai-org/CogVideoX-5B-Space")
        self.assertTrue(be.supports(vs.GenerationRequest(prompt="p", width=832, height=480)))
        self.assertFalse(be.supports(vs.GenerationRequest(prompt="p", width=480, height=832)))


class CaptionAndVisualFallbackTests(unittest.TestCase):
    def test_backup_voice_still_gets_timed_captions(self):
        events = vs.estimate_word_events("A small paper boat begins its journey.", 4.0)
        self.assertEqual([e["text"] for e in events][:3], ["A", "small", "paper"])
        self.assertEqual(events[0]["offset"], 0.0)
        ends = [e["offset"] + e["duration"] for e in events]
        self.assertTrue(all(b >= a for a, b in zip(ends, ends[1:], strict=False)))
        self.assertLessEqual(ends[-1], 4.0)
        self.assertEqual(vs.estimate_word_events("", 4.0), [])
        self.assertEqual(vs.estimate_word_events("hello", 0.0), [])

    def test_failed_fetch_reuses_previous_visual_not_an_unrelated_placeholder(self):
        fetcher = vs.MediaFetcher(vs.StudioConfig(pexels_api_key="", pixabay_api_key=""))
        with tempfile.TemporaryDirectory() as tmp:
            prev = Path(tmp) / "ocean.jpg"
            prev.write_bytes(b"x")
            with patch.object(fetcher, "_search_wikimedia", return_value=None), \
                    patch.object(fetcher, "_generate_pollinations_image", return_value=None):
                self.assertEqual(fetcher.fetch_scene_visual("salt crystals", previous=(str(prev), "image")),
                                 (str(prev), "image"))
                with self.assertRaises(vs.QualityError):
                    fetcher.fetch_scene_visual("salt crystals")

    def test_pollinations_retries_and_does_not_style_a_styled_prompt_twice(self):
        fetcher = vs.MediaFetcher(vs.StudioConfig())
        urls = []

        def fake_download(url, out, **kwargs):
            urls.append(url)
            return str(out) if len(urls) == 2 else None

        styled = vs.PromptEngine().enhance("a boat", preset="Cinematic")
        with patch.object(vs.MediaFetcher, "POLLINATIONS_RETRY_PAUSES", (0, 0, 0)), \
                patch.object(vs.MediaFetcher, "_download", side_effect=fake_download):
            self.assertTrue(fetcher._generate_pollinations_image(styled, 832, 480, "Cinematic"))
        self.assertEqual(len(urls), 2)
        self.assertEqual(urls[0].count("Visual%20treatment%3A"), 1)


class VoiceCloningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vs_voice_"))
        self.cloner = vs.VoiceCloner(vs.StudioConfig(hf_token="hf_x"), self.tmp / "voices" / "my_voice.wav")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tone(self, name, seconds):
        out = self.tmp / name
        vs.run_ffmpeg(["-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}", str(out)])
        return out

    def test_long_narration_is_split_under_the_space_limit_without_losing_words(self):
        text = " ".join(f"Sentence number {i} talks about the salty sea." for i in range(30))
        pieces = vs.split_for_cloning(text)
        self.assertGreater(len(pieces), 3)
        self.assertTrue(all(0 < len(p) <= 280 for p in pieces))
        self.assertEqual(" ".join(pieces).split(), text.split())
        self.assertEqual(vs.split_for_cloning("x" * 700)[0], "x" * 280)
        self.assertEqual(vs.split_for_cloning("  "), [])

    def test_reasons_cloning_cannot_be_used_are_plain(self):
        self.assertIn("no voice sample", self.cloner.unavailable_reason("English"))
        self.cloner.sample_path.parent.mkdir(parents=True)
        self.cloner.sample_path.write_bytes(b"x" * 2000)
        self.assertEqual(self.cloner.unavailable_reason("English"), "")
        self.assertIn("Urdu", self.cloner.unavailable_reason("Urdu"))
        self.cloner.config.hf_token = ""
        self.assertIn("Hugging Face token", self.cloner.unavailable_reason("English"))

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg not available")
    def test_sample_is_saved_trimmed_and_short_ones_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "too short"):
            self.cloner.save_sample(self._tone("short.wav", 2))
        self.assertFalse(self.cloner.has_sample())
        seconds = self.cloner.save_sample(self._tone("long.mp3", 40))
        self.assertTrue(self.cloner.has_sample())
        self.assertAlmostEqual(seconds, 30.0, delta=0.5)
        junk = self.tmp / "junk.wav"
        junk.write_bytes(b"not audio")
        with self.assertRaisesRegex(ValueError, "could not be read"):
            self.cloner.save_sample(junk)
        self.assertTrue(self.cloner.has_sample())          # a bad upload keeps the old sample
        self.cloner.delete_sample()
        self.assertFalse(self.cloner.has_sample())

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg not available")
    def test_synthesize_sends_the_space_signature_and_joins_the_pieces(self):
        self.cloner.save_sample(self._tone("me.wav", 8))
        piece = self._tone("piece.wav", 1.0)
        sent = []

        class Client(FakeSpaceClient):
            def submit(self, *args, api_name=None, **kwargs):
                sent.append((api_name, kwargs))
                return super().submit(*args, api_name=api_name, **kwargs)

        self.cloner._client = Client("/generate_tts_audio", [], str(piece))
        self.cloner._client.result = str(piece)
        text = " ".join(f"Sentence number {i} talks about the salty sea." for i in range(12))
        out = self.cloner.synthesize(text, "Hindi", self.tmp / "vo.mp3")
        self.assertEqual(len(sent), len(vs.split_for_cloning(text)))
        api, kwargs = sent[0]
        self.assertEqual(api, "/generate_tts_audio")
        self.assertEqual(set(kwargs), {"text_input", "language_id", "audio_prompt_path_input", "exaggeration_input",
                                       "temperature_input", "seed_num_input", "cfgw_input"})
        self.assertEqual(kwargs["language_id"], "hi")
        self.assertAlmostEqual(vs.ffprobe_duration(out), 1.0 * len(sent), delta=0.4)
        self.assertEqual(list(self.tmp.glob("vo_part*")), [])

    def test_voiceover_falls_back_to_the_standard_voice_and_says_why(self):
        engine = vs.AudioEngine(vs.StudioConfig(hf_token=""))
        engine.voice_cloner = self.cloner               # no sample saved
        out = self.tmp / "vo.mp3"

        def fake_gtts(text, language, out_path):
            Path(out_path).write_bytes(b"\0" * 500)

        with patch("asyncio.run", side_effect=RuntimeError("offline")), \
                patch.object(engine, "_gtts_fallback", side_effect=fake_gtts):
            got, events = engine.generate_voiceover("Hello there.", "English", "Female", out_path=out, my_voice=True)
        self.assertEqual((got, events), (out, []))
        self.assertIn("no voice sample", engine.voice_note)
        studio = object.__new__(vs.VideoStudio)
        studio.audio_engine = engine
        self.assertIn("Your own voice was not used", studio._voice_note(vs.JobSettings(use_my_voice=True)))
        self.assertEqual(studio._voice_note(vs.JobSettings(use_my_voice=False)), "")


class ScriptWriterTests(unittest.TestCase):
    def test_offline_writer_reports_the_requested_language(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}), \
                patch("requests.get", side_effect=OSError("offline")):
            for language in ("English", "Urdu", "Arabic"):
                with self.assertRaisesRegex(vs.QualityError, f"Paste a script in {language}"):
                    vs.FreeLLMPromptEnhancer.generate_script("Oceans", 3, language,
                                                            vs.StudioConfig(gemini_api_key=""))

    def test_free_text_model_writes_the_script_when_there_is_no_gemini_key(self):
        class Resp:
            status_code = 200
            text = "Visual: waves rocks\nVO: سمندر نمکین ہے۔\n\nVisual: river sea\nVO: دریا نمک لاتے ہیں۔"

        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}), patch("requests.get", return_value=Resp()) as get:
            script = vs.FreeLLMPromptEnhancer.generate_script("Oceans", 2, "Urdu", vs.StudioConfig(gemini_api_key=""))
        self.assertEqual(script, Resp.text)
        self.assertIn("Urdu", get.call_args[0][0].replace("%20", " "))

    def test_error_pages_are_not_accepted_as_scripts(self):
        self.assertFalse(vs.looks_like_script("<html>502 Bad Gateway</html>"))
        self.assertFalse(vs.looks_like_script("Sorry, rate limit reached."))
        self.assertTrue(vs.looks_like_script("**Visual:** sea\n**VO:** Salt."))


class HFClientTests(unittest.TestCase):
    def test_token_is_passed_with_the_keyword_the_client_accepts(self):
        try:
            import gradio_client
        except ImportError:
            self.skipTest("gradio_client not installed")
        seen = {}

        class FakeClient:
            def __init__(self, src, token=None, verbose=True):
                seen.update(src=src, token=token)

        with patch.object(gradio_client, "Client", FakeClient):
            vs.hf_space_client("org/space", "  hf_abc ")
        self.assertEqual(seen, {"src": "org/space", "token": "hf_abc"})


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

    def test_failed_crossfade_keeps_original_scene_timing(self):
        a = self._clip("a.mp4", 320, 180, dur=1.5)
        b = self._clip("b.mp4", 320, 180, dur=1.5)
        out = self.tmp / "fallback.mp4"
        real_run = vs.run_ffmpeg

        def fail_xfade(args, **kwargs):
            if any("xfade=" in str(arg) for arg in args):
                raise vs.FFmpegError("forced xfade failure")
            real_run(args, **kwargs)

        with patch.object(vs, "run_ffmpeg", side_effect=fail_xfade):
            vs.VideoEngine.crossfade_concat([a, b], out, crossfade=0.4, fps=30)
        self.assertAlmostEqual(vs.ffprobe_duration(out), 2.6, delta=0.08)

    def test_publication_writes_playable_export_and_source_report(self):
        source = self._clip("source.mp4", 320, 180)
        (self.tmp / "scene_00.asset.json").write_text(
            json.dumps({"visual_type": "video", "source_file": str(source)}), encoding="utf-8"
        )
        output = self.tmp / "published.mp4"
        vs.VideoStudio._publish_video(source, output, vs.JobSettings(prompt="Ocean"), self.tmp)
        report = json.loads((self.tmp / "render_report.json").read_text(encoding="utf-8"))
        self.assertEqual((report["export_width"], report["export_height"]), (320, 180))
        self.assertEqual(report["sources"][0]["source_file"], str(source))
        self.assertGreater(vs.ffprobe_duration(output), 1)
        self.assertFalse((self.tmp / "published.publishing.mp4").exists())

    def test_upscale_gives_exact_standard_frames(self):
        for (w, h), aspect, want in (((832, 480), "16:9", (1920, 1080)), ((480, 832), "9:16", (1080, 1920)),
                                     ((1536, 1024), "16:9", (1920, 1080)), ((624, 624), "1:1", (1080, 1080))):
            src = self._clip(f"s{w}x{h}.mp4", w, h, dur=0.5)
            out = self.tmp / f"up{w}x{h}.mp4"
            vs.VideoEngine.upscale_video(src, "1080p", out, aspect)
            self.assertEqual(vs.probe_video_size(out), want)

    def test_conform_crops_trims_and_drops_audio(self):
        # CogVideoX-like clip (720x480, too long, with sound) asked to be square 2 s.
        clip = self._clip("cog.mp4", 720, 480, dur=3.0, audio=True)
        self.assertTrue(vs.VideoEngine.conform_clip(clip, 624, 624, 2.0))
        self.assertEqual(vs.probe_video_size(clip), (480, 480))
        self.assertAlmostEqual(vs.ffprobe_duration(clip), 2.0, delta=0.15)
        self.assertNotIn("Audio:", vs._ffmpeg_info(clip))

    def test_conform_extends_a_short_clip_and_leaves_a_matching_one_alone(self):
        short = self._clip("wan.mp4", 832, 480, dur=1.0)
        self.assertTrue(vs.VideoEngine.conform_clip(short, 832, 480, 1.5))
        self.assertAlmostEqual(vs.ffprobe_duration(short), 1.5, delta=0.15)
        ok = self._clip("ok.mp4", 832, 480, dur=1.5)
        before = ok.read_bytes()
        self.assertFalse(vs.VideoEngine.conform_clip(ok, 832, 480, 1.5))
        self.assertEqual(ok.read_bytes(), before)

    def test_upscale_keeps_portrait_orientation(self):
        src = self._clip("p.mp4", 480, 832, dur=0.5)
        out = self.tmp / "up.mp4"
        vs.VideoEngine.upscale_video(src, "720p", out)
        w, h = vs.probe_video_size(out)
        self.assertEqual(w, 720)
        self.assertGreater(h, w)

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

    def test_synth_music_is_fast_and_full_length(self):
        import time as _t
        out = self.tmp / "bgm.mp3"
        start = _t.time()
        vs.AudioEngine().generate_synth_music("Dramatic", 20.0, out)
        self.assertLess(_t.time() - start, 10.0)
        self.assertAlmostEqual(vs.ffprobe_duration(out), 20.0, delta=0.3)

    def test_ken_burns_renders_exact_duration_from_one_frame(self):
        img = self.tmp / "still.jpg"
        vs.run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=640x360", "-frames:v", "1", str(img)])
        for motion in ("zoom_in", "zoom_out", "pan_left", "pan_right"):
            with self.subTest(motion=motion):
                out = self.tmp / f"kb_{motion}.mp4"
                vs.VideoEngine.image_to_video_ken_burns(img, 2.0, 320, 180, fps=24, motion_type=motion, out_path=out)
                self.assertAlmostEqual(vs.ffprobe_duration(out), 2.0, delta=0.1)
                self.assertEqual(vs.probe_video_size(out), (320, 180))

    def test_voice_and_music_mix_keeps_narration_level(self):
        vo = self.tmp / "vo.mp3"
        vs.run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=300:duration=3", str(vo)])
        out = self.tmp / "mix.m4a"     # raw .aac (ADTS) durations are only bitrate estimates
        vs.AudioEngine().mix_and_duck_audio(vo, "Calm", 4.0, out)
        self.assertAlmostEqual(vs.ffprobe_duration(out), 4.0, delta=0.3)
        proc = subprocess.run([vs.ffmpeg_exe(), "-hide_banner", "-i", str(out), "-af", "volumedetect",
                               "-f", "null", "-"], capture_output=True, text=True, timeout=60)
        import re as _re
        peak = float(_re.search(r"max_volume:\s*(-?[\d.]+) dB", proc.stderr).group(1))
        # sine source peaks at about -18 dBFS; amix normalization would push it to about -24.
        self.assertGreater(peak, -21.0)

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
