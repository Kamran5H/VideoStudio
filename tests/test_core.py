import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

# Importing app builds a real VideoStudio: without this, the test run would
# write to the user's outputs/, take the worker lock and auto-resume real jobs.
os.environ.setdefault("VIDEOSTUDIO_OUTPUT_DIR", tempfile.mkdtemp(prefix="vs_test_out_"))

import app  # noqa: E402
from app import readable_job_message, render_job_card, sync_job_outputs  # noqa: E402
from video_studio import (  # noqa: E402
    JobQueue,
    JobSettings,
    JobStatus,
    PromptEngine,
    Stage,
    SubtitleEngine,
    VideoStudio,
    frames_for,
)


class VideoStudioCoreTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        app.STUDIO.queue.shutdown()

    # ------------------------------------------------------------ pipeline basics
    def test_frames_are_snapped_to_model_requirement(self):
        self.assertEqual(frames_for(0), 17)
        self.assertEqual(frames_for(5), 81)
        self.assertEqual((frames_for(2.5) - 1) % 4, 0)

    def test_prompt_enhancement_uses_requested_style(self):
        enhanced = PromptEngine().enhance("A quiet forest", preset="Cyberpunk", seed=7)
        self.assertIn("cyberpunk", enhanced.lower())
        self.assertIn("A quiet forest.", enhanced)
        self.assertEqual(enhanced, PromptEngine().enhance("A quiet forest", preset="Cyberpunk", seed=7))

    def test_script_parser_extracts_visual_and_voiceover(self):
        studio = object.__new__(VideoStudio)
        self.assertEqual(
            studio._parse_script_scenes(
                "Visual: sunrise over the mountains\nVO: A new day begins.\n\n"
                "Visual: a river through the valley\nVO: Water gives life."
            ),
            [
                ("sunrise over the mountains", "A new day begins."),
                ("a river through the valley", "Water gives life."),
            ],
        )

    def test_scene_checkpoint_preserves_caption_offsets_for_resume(self):
        events = [{"text": "Hello", "offset": 0.25, "duration": 0.4}]
        with tempfile.TemporaryDirectory() as tmp:
            metadata = Path(tmp) / "scene_00.json"
            VideoStudio._write_scene_checkpoint(metadata, events, 1.75)
            self.assertEqual(VideoStudio._read_scene_checkpoint(metadata), (events, 1.75))
            metadata.write_text('{"events": [], "voiceover_duration": NaN}', encoding="utf-8")
            self.assertIsNone(VideoStudio._read_scene_checkpoint(metadata))

    def test_srt_generation_writes_timed_captions(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "captions.srt"
            SubtitleEngine.events_to_srt(
                [
                    {"text": "Hello", "offset": 0.25, "duration": 0.4},
                    {"text": "world.", "offset": 0.7, "duration": 0.5},
                ],
                output,
            )
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "1\n00:00:00,250 --> 00:00:01,200\nHello world.\n",
            )

    # ------------------------------------------------------------ status cards
    def test_job_card_escapes_untrusted_text(self):
        card = render_job_card("job<1>", "<script>alert('p')</script>", Stage.GENERATING, 1.5,
                               "<img src=x onerror=alert(1)>")
        self.assertIn("job&lt;1&gt;", card)
        self.assertNotIn("<script>", card)
        self.assertNotIn("<img", card)
        self.assertIn("is-indeterminate", card)

    def test_running_job_shows_no_invented_percentage(self):
        card = render_job_card("active", "A video", Stage.GENERATING, 0.42, "Generating AI clip 2/4...")
        self.assertIn("Working on it", card)
        self.assertIn('aria-label="Generating Video in progress"', card)
        self.assertNotIn("42%", card)
        self.assertNotIn("aria-valuenow", card)

    def test_queue_wait_label_in_plain_words(self):
        active = JobStatus(job_id="a", stage=Stage.GENERATING, settings=JobSettings(job_id="a"))
        delayed = JobStatus(job_id="d", stage=Stage.QUEUED, not_before=200, settings=JobSettings(job_id="d"))
        queued = JobStatus(job_id="q", stage=Stage.QUEUED, settings=JobSettings(job_id="q"))
        self.assertEqual(app.queue_wait_label(queued, [active, delayed, queued], now=100),
                         "Waiting — 1 video(s) ahead")
        queued.not_before = 165
        self.assertIn("trying again in 1m 05s", app.queue_wait_label(queued, [active, queued], now=100))

    def test_failed_job_shows_readable_summary_and_redacted_details(self):
        job = JobStatus(job_id="f", stage=Stage.FAILED, error="Auth failed with test-secret-token",
                        settings=JobSettings(job_id="f"))
        previous = app.STUDIO.config.hf_token
        app.STUDIO.config.hf_token = "test-secret-token"
        try:
            self.assertIn("could not be finished", readable_job_message(job))
            card = render_job_card(job.job_id, "x", job.stage, 0, readable_job_message(job), error=job.error)
            self.assertIn("Technical details", card)
            self.assertIn("[redacted]", card)
            self.assertNotIn("test-secret-token", card)
        finally:
            app.STUDIO.config.hf_token = previous

    # ------------------------------------------------------------ simple UI
    def test_ui_has_no_presaved_prompts_or_removed_extras(self):
        blocks = app.build_app()
        config = json.dumps(blocks.config, default=str)
        for gone in ("Golden Eagle", "Pyramids", "Template", "Watermark", "Anti-Fingerprint",
                     "Negative Prompt", "Translate Subtitles", "60 FPS"):
            self.assertNotIn(gone, config)
        self.assertIn("Make my video", config)
        self.assertIn("More options", config)
        values = [c.get("props", {}).get("value") for c in blocks.config["components"]]
        for v in values:
            if isinstance(v, str) and len(v) > 40 and "Visual:" in v:
                self.fail("a storyboard script is pre-filled")
        self.assertEqual(app.DEVELOPER_ATTRIBUTION, "Developer: Kamran Ashraf")
        self.assertIn(app.DEVELOPER_ATTRIBUTION, config)

    def test_restored_job_sync_reconnects_result_without_reloading_same_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "result.mp4"
            video.write_bytes(b"video")
            completed = JobStatus(
                job_id="ai-done", stage=Stage.DONE, progress=1, message="Completed", video_path=str(video),
                settings=JobSettings(job_id="ai-done", mode="ai_video", prompt="Previous clip", created_at=1),
            )
            active_story = JobStatus(
                job_id="story-active", stage=Stage.GENERATING, progress=0.5, message="Rendering scene 2",
                settings=JobSettings(job_id="story-active", mode="script_story", prompt="Story", created_at=2),
            )
            with patch.object(app.STUDIO.queue, "all_jobs", return_value=[completed, active_story]):
                first = sync_job_outputs()
                self.assertIn("Previous clip", first[0])
                self.assertEqual(first[1], str(video))
                self.assertEqual(first[2], "")
                self.assertIn("Rendering scene 2", first[3])
                self.assertEqual(first[5], "story-active")
                self.assertEqual(first[6], str(video))
                again = sync_job_outputs(str(video), "")
                self.assertEqual(again[1], app.gr.skip())

    def test_make_clip_requires_an_idea_and_streams_status(self):
        msg = next(app.make_clip("   ", "16:9", 5, "", "English", "Female", "Ambient", True, "1080p", "Cinematic"))
        self.assertIn("describe", msg[0])
        queue = app.STUDIO.queue
        status = JobStatus(job_id="active", stage=Stage.GENERATING, message="Generating AI clip 1/1",
                           settings=JobSettings(job_id="active", prompt="A boat"))
        with (
            patch.object(queue, "submit", return_value="active") as submit,
            patch.object(queue, "get_status", return_value=status),
            patch.object(queue, "all_jobs", return_value=[status]),
            patch.object(app.time, "sleep"),
        ):
            update = next(app.make_clip("A boat", "9:16", 5, "", "English", "Female", "Ambient",
                                        True, "1080p", "Cinematic"))
        self.assertIn("Making your video", update[0])
        self.assertNotIn("%", update[0])
        sett = submit.call_args.args[0]
        self.assertEqual((sett.aspect_ratio, sett.voice_script), ("9:16", ""))

    def test_job_settings_are_sanitized_from_raw_api_input(self):
        sett = app.build_ai_settings("  idea  ", "99:1", 10_000, None, "Klingon", "Robot", "Polka",
                                     1, "8K", "NotAStyle")
        self.assertEqual((sett.prompt, sett.aspect_ratio, sett.duration), ("idea", "16:9", 30))
        self.assertEqual((sett.language, sett.voice_gender, sett.music_mood), ("English", "Female", "Ambient"))
        self.assertEqual((sett.quality, sett.style_preset, sett.voice_script), ("1080p", "Cinematic", ""))
        self.assertFalse(sett.interpolate_60fps)
        self.assertEqual(app.build_ai_settings("p", "1:1", float("nan"), "Hi", "Urdu", "Male", "None",
                                               False, "4K", "Anime").duration, 5.0)
        story = app.build_story_settings(None, "Visual: x\nVO: y", "1:1", "Urdu", "?", "?", True, "?")
        self.assertEqual((story.prompt, story.aspect_ratio, story.language, story.music_mood),
                         ("Story video", "1:1", "Urdu", "Calm"))

    def test_my_voice_choice_sets_cloning_and_keeps_a_backup_voice(self):
        sett = app.build_ai_settings("boat", "16:9", 5, "Hi", "English", app.MY_VOICE, "Calm", True, "1080p", "Cinematic")
        self.assertEqual((sett.use_my_voice, sett.voice_gender), (True, "Female"))
        sett = app.build_story_settings("sea", "", "9:16", "English", "Male", "Calm", True, "Cinematic")
        self.assertEqual((sett.use_my_voice, sett.voice_gender), (False, "Male"))
        sett = app.build_story_settings("sea", "", "9:16", "English", "<script>", "Calm", True, "Cinematic")
        self.assertEqual((sett.use_my_voice, sett.voice_gender), (False, "Female"))
        self.assertIn("Record or upload", app.save_my_voice(None))

    def test_settings_never_prefill_saved_secrets(self):
        cfg = app.STUDIO.config
        saved = (cfg.gemini_api_key, cfg.hf_token, cfg.pexels_api_key, cfg.pixabay_api_key)
        cfg.gemini_api_key = "SECRET-GEMINI-KEY"
        try:
            with patch.object(cfg, "save"):
                config_json = json.dumps(app.build_app().config, default=str)
                self.assertNotIn("SECRET-GEMINI-KEY", config_json)
                self.assertEqual(app.merge_secret("old", ""), "old")
                self.assertEqual(app.merge_secret("old", "  new "), "new")
                self.assertEqual(app.merge_secret("old", app.CLEAR_SECRET), "")
                app.save_api_credentials("", "", "", "", "")
                self.assertEqual(cfg.gemini_api_key, "SECRET-GEMINI-KEY")
        finally:
            (cfg.gemini_api_key, cfg.hf_token, cfg.pexels_api_key, cfg.pixabay_api_key) = saved

    # ------------------------------------------------------------ queue
    def test_view_only_queue_rejects_mutations(self):
        queue = app.STUDIO.queue
        is_worker = queue.is_worker
        queue.is_worker = False
        try:
            with self.assertRaisesRegex(RuntimeError, "view-only"):
                queue.submit(JobSettings(prompt="must not be enqueued"))
            self.assertFalse(queue.cancel("missing-job"))
            self.assertFalse(queue.retry("missing-job"))
            queue._auto_resume_interrupted_jobs()
            self.assertIn("already running", next(app.make_clip("x", "16:9", 5, "", "", "", "", 1, "", ""))[0])
        finally:
            queue.is_worker = is_worker

    def test_invalid_worker_lock_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = object.__new__(JobQueue)
            queue._lock_path = Path(tmp) / "worker.lock"
            queue._lock_path.write_text("not-a-process-id", encoding="utf-8")
            self.assertFalse(queue._acquire_worker_lock())
            self.assertEqual(queue._lock_path.read_text(encoding="utf-8"), "not-a-process-id")

    def test_concurrent_queue_saves_serialize_shared_temp_file_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = object.__new__(JobQueue)
            queue.queue_file = Path(tmp) / "jobs.json"
            queue._lock = threading.RLock()
            queue._last_persist = 0.0
            status = JobStatus(job_id="saved", message="first snapshot", settings=JobSettings(job_id="saved"))
            queue._jobs = {"saved": status}

            temp_path = queue.queue_file.with_suffix(f".{os.getpid()}.tmp")
            first_write_started = threading.Event()
            second_call_started = threading.Event()
            second_write_started = threading.Event()
            release_first_write = threading.Event()
            writer_lock = threading.Lock()
            write_count = 0
            write_errors = []
            original_write_text = Path.write_text

            def controlled_write_text(path, data, *args, **kwargs):
                nonlocal write_count
                if path == temp_path:
                    with writer_lock:
                        write_count += 1
                        current_write = write_count
                    if current_write == 1:
                        first_write_started.set()
                        if not release_first_write.wait(timeout=5):
                            raise TimeoutError("Timed out waiting to release the first queue write.")
                    else:
                        second_write_started.set()
                return original_write_text(path, data, *args, **kwargs)

            def save():
                try:
                    queue._save(force=True)
                except Exception as exc:
                    write_errors.append(exc)

            def save_newer():
                second_call_started.set()
                try:
                    with queue._lock:
                        status.message = "second snapshot"
                        queue._save(force=True)
                except Exception as exc:
                    write_errors.append(exc)

            with patch.object(Path, "write_text", controlled_write_text):
                first = threading.Thread(target=save)
                first.start()
                self.assertTrue(first_write_started.wait(timeout=2))
                second = threading.Thread(target=save_newer)
                second.start()
                self.assertTrue(second_call_started.wait(timeout=2))
                self.assertFalse(second_write_started.wait(timeout=0.2))
                release_first_write.set()
                first.join(timeout=2)
                second.join(timeout=2)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(write_errors, [])
            self.assertTrue(second_write_started.is_set())
            self.assertEqual(
                json.loads(queue.queue_file.read_text(encoding="utf-8"))["saved"]["message"],
                "second snapshot",
            )


if __name__ == "__main__":
    unittest.main()
