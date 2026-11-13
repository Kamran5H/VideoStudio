"""Stock-media, Gemini and Colab-worker regression tests (no network)."""

import json
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("VIDEOSTUDIO_OUTPUT_DIR", tempfile.mkdtemp(prefix="vs_test_out_"))

import video_studio as vs  # noqa: E402  (env var must be set before import)

REPO = Path(__file__).resolve().parent.parent


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data or {}

    def json(self):
        return self._data


class KeyCleaningTests(unittest.TestCase):
    def test_pasted_keys_lose_quotes_and_spaces(self):
        self.assertEqual(vs.clean_secret('  "abc123" '), "abc123")
        self.assertEqual(vs.clean_secret("'abc'"), "abc")
        self.assertEqual(vs.clean_secret(None), "")
        cfg = vs.StudioConfig(pexels_api_key=' "PX" ', pixabay_api_key="PB\n",
                              colab_url=" 'https://x.gradio.live/' ")
        self.assertEqual((cfg.pexels_api_key, cfg.pixabay_api_key, cfg.colab_url),
                         ("PX", "PB", "https://x.gradio.live"))

    def test_keys_from_the_environment_are_cleaned_too(self):
        with patch.dict(os.environ, {"PEXELS_API_KEY": '"from-env"'}):
            self.assertEqual(vs.StudioConfig().pexels_api_key, "from-env")


class StockQueryTests(unittest.TestCase):
    def test_long_cinematic_prompt_becomes_a_short_keyword_query(self):
        prompt = ("Cinematic 35mm film still in motion, a red fox running through fresh snow in a pine "
                  "forest at dawn, slow dolly-in on a 35mm anamorphic lens, shallow depth of field. "
                  "Visual treatment: filmic teal-and-orange color grading")
        query = vs.stock_query(prompt)
        self.assertLessEqual(len(query), 90)            # Pixabay rejects q over 100 characters
        self.assertTrue(query.startswith("red fox running"), query)
        self.assertNotIn("cinematic", query)
        self.assertNotIn("teal", query)

    def test_short_queries_and_other_scripts_are_kept(self):
        self.assertEqual(vs.stock_query("ocean waves"), "ocean waves")
        self.assertEqual(vs.stock_query("سمندر کی لہریں"), "سمندر کی لہریں")
        self.assertEqual(vs.stock_query(""), "")


class RenditionChoiceTests(unittest.TestCase):
    def test_output_size_beats_4k_and_720p(self):
        score = vs.MediaFetcher._framing_score
        full_hd = score({"width": 1920, "height": 1080}, 1920, 1080)
        uhd = score({"width": 3840, "height": 2160}, 1920, 1080)
        hd = score({"width": 1280, "height": 720}, 1920, 1080)
        self.assertLess(full_hd, uhd)        # 4K costs a 100+ MB download for nothing
        self.assertLess(uhd, hd)             # but upscaling 720p loses more
        self.assertEqual(score({"width": None, "height": 0}, 1920, 1080), float("inf"))

    def test_pixabay_video_picks_the_right_shape_and_size(self):
        fetcher = vs.MediaFetcher(vs.StudioConfig(pixabay_api_key="PB"))
        data = {"hits": [
            {"id": 1, "videos": {"large": {"url": "https://p/1-4k.mp4", "width": 3840, "height": 2160},
                                 "medium": {"url": "https://p/1-hd.mp4", "width": 1920, "height": 1080}}},
            {"id": 2, "videos": {"large": {"url": "https://p/2-tall.mp4", "width": 1080, "height": 1920}}},
        ]}
        sent = {}

        def fake_get(url, **kw):
            sent.update(kw["params"])
            return Resp(200, data)

        with patch("requests.get", fake_get), \
                patch.object(fetcher, "_stock_asset", side_effect=lambda s, i, url, *a: url):
            self.assertEqual(fetcher._search_pixabay_video("x" * 300, 1920, 1080), "https://p/1-hd.mp4")
            self.assertLessEqual(len(sent["q"]), 100)
            self.assertEqual(fetcher._search_pixabay_video("waves", 1080, 1920), "https://p/2-tall.mp4")

    def test_pixabay_photo_uses_orientation_and_large_image(self):
        fetcher = vs.MediaFetcher(vs.StudioConfig(pixabay_api_key="PB"))
        sent = {}

        def fake_get(url, **kw):
            sent.update(url=url, **kw["params"])
            return Resp(200, {"hits": [{"id": 9, "imageWidth": 1080, "imageHeight": 1920,
                                        "largeImageURL": "https://p/big.jpg"}]})

        with patch("requests.get", fake_get), \
                patch.object(fetcher, "_stock_asset", side_effect=lambda s, i, url, *a: url):
            self.assertEqual(fetcher._search_pixabay_photo("tower", 1080, 1920), "https://p/big.jpg")
        self.assertEqual((sent["url"], sent["orientation"], sent["image_type"]),
                         ("https://pixabay.com/api/", "vertical", "photo"))


class StockFallbackTests(unittest.TestCase):
    def test_rejected_key_is_logged_once_as_a_warning(self):
        fetcher = vs.MediaFetcher(vs.StudioConfig(pexels_api_key="bad"))
        with patch("requests.get", return_value=Resp(401)), self.assertLogs("VideoStudio", "WARNING") as logs:
            fetcher._search_pexels_video("ocean", 1920, 1080)
            fetcher._search_pexels_photo("ocean", 1920, 1080)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("API key was rejected", logs.output[0])

    def test_pixabay_photo_and_shorter_query_are_tried_before_free_sources(self):
        fetcher = vs.MediaFetcher(vs.StudioConfig(pixabay_api_key="PB"))
        tried = []

        def photo(query, w, h):
            tried.append(query)
            return "/tmp/photo.jpg" if len(query.split()) <= 3 else None

        with patch.object(fetcher, "_search_pixabay_video", return_value=None), \
                patch.object(fetcher, "_search_pixabay_photo", side_effect=photo), \
                patch.object(fetcher, "_search_wikimedia") as wiki:
            found = fetcher.fetch_scene_visual("old stone bridge over a misty river valley at dawn")
        self.assertEqual(found, ("/tmp/photo.jpg", "image"))
        self.assertEqual(tried, ["old stone bridge misty river valley", "old stone bridge"])
        wiki.assert_not_called()


class WikimediaTests(unittest.TestCase):
    def test_request_follows_wikimedia_rules_and_uses_scaled_files(self):
        fetcher = vs.MediaFetcher(vs.StudioConfig())
        sent = {}
        data = {"query": {"pages": [
            {"imageinfo": [{"mime": "image/tiff", "url": "https://u/scan.tif", "width": 1920, "height": 1080}]},
            {"imageinfo": [{"mime": "image/jpeg", "url": "https://u/orig.jpg",
                            "thumburl": "https://u/1920px-orig.jpg", "width": 4000, "height": 2250}]},
        ]}}

        def fake_get(url, params=None, headers=None, timeout=None):
            sent.update(params=params, headers=headers)
            return Resp(200, data)

        with tempfile.TemporaryDirectory() as tmp, patch("requests.get", fake_get), \
                patch.object(vs.MediaFetcher, "_download", side_effect=lambda url, out, **k: url):
            fetcher.cache_dir = Path(tmp)
            self.assertEqual(fetcher._search_wikimedia("stone bridge"), "https://u/1920px-orig.jpg")
        self.assertIn("github.com/Kamran5H/VideoStudio", sent["headers"]["User-Agent"])
        self.assertEqual(sent["params"]["gsrnamespace"], 6)
        self.assertGreaterEqual(sent["params"]["iiurlwidth"], 1280)

    def test_rate_limit_is_reported_not_crashed(self):
        fetcher = vs.MediaFetcher(vs.StudioConfig())
        with patch("requests.get", return_value=Resp(429)), self.assertLogs("VideoStudio", "WARNING") as logs:
            self.assertIsNone(fetcher._search_wikimedia("bridge"))
        self.assertIn("request limit", logs.output[0])


class FakeGenai:
    """Stands in for google.genai with scripted per-model answers."""

    def __init__(self, answers):
        self.answers, self.asked = answers, []
        outer = self

        class Models:
            def generate_content(self, model, contents):
                outer.asked.append(model)
                answer = outer.answers.get(model, RuntimeError("500 internal"))
                if isinstance(answer, Exception):
                    raise answer
                return types.SimpleNamespace(text=answer)

        class Client:
            def __init__(self, api_key=None, http_options=None):
                self.models = Models()

        self.module = types.SimpleNamespace(Client=Client)
        self.types = types.SimpleNamespace(HttpOptions=lambda timeout=None: timeout)
        self.module.types = self.types

    def install(self):
        google = types.SimpleNamespace(genai=self.module)
        return patch.dict(sys.modules, {"google": google, "google.genai": self.module,
                                        "google.genai.types": self.types})


class GeminiTests(unittest.TestCase):
    def setUp(self):
        vs._GEMINI_UNAVAILABLE_MODELS.clear()
        vs._GEMINI_WARNED.clear()

    def test_unknown_model_is_skipped_and_remembered(self):
        fake = FakeGenai({"m1": RuntimeError("404 NOT_FOUND. models/m1 is not found"), "m2": "good reply"})
        with fake.install(), patch.dict(os.environ, {"GEMINI_MODELS": "m1,m2"}):
            self.assertEqual(vs.gemini_generate("k", "hi", lambda t: True), "good reply")
            self.assertEqual(vs.gemini_generate("k", "hi", lambda t: True), "good reply")
        self.assertEqual(fake.asked, ["m1", "m2", "m2"])

    def test_rejected_key_stops_with_one_warning(self):
        fake = FakeGenai({"m1": RuntimeError("400 INVALID_ARGUMENT. API key not valid. API_KEY_INVALID")})
        with fake.install(), patch.dict(os.environ, {"GEMINI_MODELS": "m1,m2"}), \
                self.assertLogs("VideoStudio", "WARNING") as logs:
            self.assertIsNone(vs.gemini_generate("bad", "hi", lambda t: True))
            self.assertIsNone(vs.gemini_generate("bad", "hi", lambda t: True))
        self.assertEqual(fake.asked, ["m1", "m1"])
        self.assertEqual(len(logs.output), 1)
        self.assertIn("rejected the API key", logs.output[0])

    def test_unusable_reply_moves_to_the_next_model(self):
        fake = FakeGenai({"m1": "Error", "m2": "Visual: a\nVO: b"})
        with fake.install(), patch.dict(os.environ, {"GEMINI_MODELS": "m1,m2"}):
            self.assertEqual(vs.gemini_generate("k", "hi", lambda t: "VO:" in t), "Visual: a\nVO: b")


def _status(code, index=None, rank=None, length=25):
    units = [types.SimpleNamespace(index=index, length=length)] if index is not None else []
    return types.SimpleNamespace(code=types.SimpleNamespace(name=code), rank=rank, progress_data=units)


class PendingJob:
    """A Colab job submitted once; reports progress until it is finished."""

    def __init__(self, statuses, finish_after):
        self.statuses, self.finish_after, self.polls, self.finished = statuses, finish_after, 0, False

    def done(self):
        return self.finished

    def result(self, timeout=None):
        if self.polls >= self.finish_after:
            self.finished = True
            return self.path
        self.polls += 1
        raise TimeoutError()

    def status(self):
        return self.statuses[min(self.polls, len(self.statuses)) - 1]


class ColabPendingTests(unittest.TestCase):
    """From the user's log: a slow T4 render was abandoned and re-sent."""

    def test_progress_is_shown_and_the_same_request_is_polled_not_resent(self):
        tmp = Path(tempfile.mkdtemp())
        clip = tmp / "remote.mp4"
        clip.write_bytes(b"video")
        job = PendingJob([_status("IN_QUEUE", rank=0), _status("PROGRESS", 7, length=20)], finish_after=2)
        job.path = str(clip)
        submits = []
        client = types.SimpleNamespace(submit=lambda *a, **k: submits.append(a) or job)
        be = vs.ColabBackend(vs.StudioConfig(colab_url="https://x.gradio.live"))
        req = vs.GenerationRequest(prompt="lions", width=480, height=832, steps=30, seed=4)
        messages = []
        with patch.object(be, "_get_client", return_value=client):
            with self.assertRaises(vs.ColabRenderPending) as first:
                be.generate(req, tmp / "out.mp4", messages.append)
            self.assertIn("yours is next", first.exception.reason)
            with self.assertRaises(vs.ColabRenderPending) as second:
                be.generate(req, tmp / "out.mp4", messages.append)
            self.assertEqual(second.exception.reason, "Colab: rendering, step 7/20")
            out = be.generate(req, tmp / "out.mp4", messages.append)
        self.assertEqual(out.read_bytes(), b"video")
        self.assertEqual(len(submits), 1)                 # never re-sent while pending
        self.assertIn("Colab worker: rendering, step 7/20", messages)

    def test_status_messages(self):
        describe = vs.ColabBackend._describe_status
        self.assertEqual(describe(_status("IN_QUEUE", rank=2))[1],
                         "Colab worker: finishing an earlier render; 2 more request(s) before yours")
        self.assertEqual(describe(_status("PROCESSING"))[1], "Colab worker: rendering...")
        self.assertEqual(describe(None), (None, ""))


class PendingDeferralTests(unittest.TestCase):
    def test_a_pending_colab_render_is_checked_soon_and_not_counted_as_a_quota_wait(self):
        import time as real_time

        studio = types.SimpleNamespace(config=vs.StudioConfig())
        queue = object.__new__(vs.JobQueue)
        job = vs.JobStatus(job_id="j1", settings=vs.JobSettings(prompt="p"))
        queue.studio, queue._jobs, queue._cancelled_jobs, queue._paused = studio, {"j1": job}, set(), False
        queue._lock = __import__("threading").RLock()
        queue._running = True
        saved = []

        def execute(status, cb):
            queue._running = False                    # one pass of the loop
            raise vs.ColabRenderPending(15, reason="Colab: rendering, step 3/20")

        studio.execute_job = execute
        queue._save = lambda force=False: saved.append(force)
        before = real_time.time()
        queue._worker_loop()
        self.assertEqual(job.stage, vs.Stage.QUEUED)
        self.assertEqual(job.deferrals, 0)
        self.assertLess(job.not_before - before, 60)      # not the 2-45 min quota back-off
        self.assertEqual(job.message, "⏳ Colab: rendering, step 3/20")


class QuotaBackoffTests(unittest.TestCase):
    def test_daily_quota_is_not_retried_every_minute_all_day(self):
        waits = [vs.deferral_wait(80, n) for n in range(1, 9)]
        self.assertEqual(waits[:5], [120, 240, 480, 960, 1920])
        self.assertEqual(set(waits[5:]), {2700})
        self.assertEqual(vs.deferral_wait(600, 1), 600)          # the provider's own longer hint wins
        self.assertEqual(vs.deferral_wait(99_999, 1, 2700), 2700)


QUOTA_TEXT = ("You have exceeded your ZeroGPU quota (60s requested vs. 0s left). Try again in 0:00:00. "
              "Subscribe to Hugging Face PRO to get 40 min of ZeroGPU quota a day")


class VoiceQuotaTests(unittest.TestCase):
    """From the user's log: a story failed because the voice-cloning quota ran out."""

    def _engine(self):
        engine = vs.AudioEngine()
        self.std_calls = 0

        def standard(text, language, out_path):
            self.std_calls += 1
            Path(out_path).write_bytes(b"\0" * 500)

        return engine, standard

    def test_quota_before_any_cloned_scene_uses_the_standard_voice_for_the_whole_video(self):
        engine, standard = self._engine()
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(engine.voice_cloner, "synthesize", side_effect=RuntimeError(QUOTA_TEXT)) as clone, \
                patch("asyncio.run", side_effect=RuntimeError("offline")), \
                patch.object(engine, "_gtts_fallback", side_effect=standard):
            for i in range(2):
                engine.generate_voiceover("Hello", out_path=Path(tmp) / f"{i}.mp3",
                                          my_voice=True, strict_my_voice=True)
        self.assertEqual(clone.call_count, 1)        # not retried for scene 2: one voice per video
        self.assertEqual(self.std_calls, 2)
        self.assertEqual(engine.voice_note, "today's free GPU time is used up")

    def test_quota_after_a_cloned_scene_waits_instead_of_mixing_voices(self):
        engine, standard = self._engine()
        engine.job_voice = "cloned"
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(engine.voice_cloner, "synthesize", side_effect=RuntimeError(QUOTA_TEXT)), \
                patch.object(engine, "_gtts_fallback", side_effect=standard):
            with self.assertRaises(vs.QuotaExhausted) as ctx:
                engine.generate_voiceover("Hello", out_path=Path(tmp) / "a.mp3", my_voice=True, strict_my_voice=True)
        self.assertGreaterEqual(ctx.exception.wait_seconds, 900)
        self.assertEqual(self.std_calls, 0)

    def test_scene_checkpoint_remembers_the_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scene_00.json"
            vs.VideoStudio._write_scene_checkpoint(path, [], 2.0, voice="cloned")
            self.assertEqual(vs.VideoStudio._scene_voice(path), "cloned")
            self.assertIsNotNone(vs.VideoStudio._read_scene_checkpoint(path))
            path.write_text('{"events": [], "voiceover_duration": 1}', encoding="utf-8")
            self.assertEqual(vs.VideoStudio._scene_voice(path), "")


class ColabNotebookTests(unittest.TestCase):
    def setUp(self):
        self.nb = json.loads((REPO / "colab_worker.ipynb").read_text(encoding="utf-8"))
        self.code = ["".join(c["source"]) for c in self.nb["cells"] if c["cell_type"] == "code"]

    def test_install_does_not_downgrade_colab_numpy_or_build_flash_attn(self):
        install = next(c for c in self.code if "requirements.txt" in c)
        found = re.search(r"grep -viE '([^']+)'", install)
        assert found, "install cell must filter Wan's requirements"
        pattern = found.group(1)
        skip = re.compile(pattern, re.IGNORECASE)
        for line in ("numpy>=1.23.5,<2", "torch>=2.4.0", "torchvision>=0.19.0", "flash_attn", "gradio>=5.0.0"):
            self.assertTrue(skip.search(line), line)
        for line in ("diffusers>=0.31.0", "tokenizers>=0.20.3", "easydict", "torchsde"):
            self.assertFalse(skip.search(line), line)

    def test_worker_matches_the_studio_call_and_gpu_profile(self):
        serve = self.code[-1]
        self.assertIn("def generate(prompt, negative_prompt, width, height, num_frames,\n"
                      "             steps, guidance, seed, progress=", serve)
        self.assertIn("[p, np_, w, h, nf, st, g, sd]", serve)     # the 8 inputs ColabBackend sends
        self.assertIn("api_name='generate'", serve)
        for name in ("offload_model=OFFLOAD", "shift=SAMPLE_SHIFT", "MAX_STEPS",
                     "gr.Progress(track_tqdm=True)", "FINISHED[key] = out", "same request as before"):
            self.assertIn(name, serve)
        check = self.code[0]
        for name in ("T5_ON_GPU", "OFFLOAD", "MAX_STEPS", "SAMPLE_SHIFT", "High-RAM"):
            self.assertIn(name, check)
        load = next(c for c in self.code if "wan.WanT2V(" in c)
        self.assertIn("t5_cpu=not T5_ON_GPU", load)
        self.assertIn("mmap=True", load)

    def test_studio_keeps_a_render_longer_than_a_capped_t4_render(self):
        self.assertGreaterEqual(vs.ColabBackend.MAX_PENDING_SECONDS, 2700)


if __name__ == "__main__":
    unittest.main()
