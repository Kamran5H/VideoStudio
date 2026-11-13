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

    def test_story_scene_count_survives_submission_settings(self):
        settings = app.build_story_settings("Oceans", "", "9:16", "English", "Female",
                                            "None", True, "Documentary", scenes=6)
        self.assertEqual(settings.scene_count, 6)

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

    def test_make_clip_requires_an_idea_and_queues_without_blocking(self):
        msg = app.make_clip("   ", "16:9", 5, "", "English", "Female", "Ambient", True, "1080p", "Cinematic")
        self.assertIn("describe", msg[0])
        queue = app.STUDIO.queue
        status = JobStatus(job_id="active", stage=Stage.GENERATING, message="Generating AI clip 1/1",
                           settings=JobSettings(job_id="active", prompt="A boat"))
        with (
            patch.object(queue, "submit", return_value="active") as submit,
            patch.object(queue, "get_status", return_value=status),
            patch.object(queue, "all_jobs", return_value=[status]),
        ):
            update = app.make_clip("A boat", "9:16", 5, "", "English", "Female", "Ambient",
                                   True, "1080p", "Cinematic")
        self.assertIn("Started", update[0])
        self.assertNotIn("%", update[0])
        self.assertIn("Generating AI clip 1/1", update[1])
        self.assertEqual(update[2], "active")
        sett = submit.call_args.args[0]
        self.assertEqual((sett.aspect_ratio, sett.voice_script), ("9:16", ""))

    def test_failed_job_stays_visible_with_try_again(self):
        done = JobStatus(job_id="old", stage=Stage.DONE, video_path=__file__,
                         settings=JobSettings(job_id="old", mode="script_story", prompt="Old story", created_at=1))
        failed = JobStatus(job_id="bad", stage=Stage.FAILED, error="boom",
                           settings=JobSettings(job_id="bad", mode="script_story", prompt="New story", created_at=2))
        queue = app.STUDIO.queue
        with patch.object(queue, "all_jobs", return_value=[done, failed]):
            card, video, job_id, can_stop, can_retry = app.tab_state("script_story")
        self.assertIn("New story", card)
        self.assertEqual((video, job_id, can_stop, can_retry), (__file__, "bad", False, True))
        with (patch.object(queue, "get_status", return_value=failed),
              patch.object(queue, "retry", return_value=True) as retry):
            self.assertIn("Trying again", app.retry_job("bad"))
        retry.assert_called_once_with("bad")
        with patch.object(queue, "get_status", return_value=done):
            self.assertIn("nothing to try again", app.retry_job("old"))
        self.assertEqual(app.stop_job(""), "Nothing is running right now.")

    def test_my_voice_is_checked_before_a_job_is_queued(self):
        queue = app.STUDIO.queue
        cloner = app.STUDIO.audio_engine.voice_cloner
        with (patch.object(cloner, "unavailable_reason", return_value="no voice sample is saved yet"),
              patch.object(queue, "submit") as submit):
            message = app.make_story("Oceans", "", "9:16", "English", app.MY_VOICE, "Calm", True, "Cinematic")[0]
            self.assertIn("My voice cannot be used", message)
            submit.assert_not_called()
            # A clip without narration never needs the voice, so it is not blocked.
            app.make_clip("A boat", "16:9", 5, "", "English", app.MY_VOICE, "Calm", True, "1080p", "Cinematic")
            submit.assert_called_once()

    def test_library_lists_and_deletes_finished_videos(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "result.mp4"
            video.write_bytes(b"video")
            done = JobStatus(job_id="lib-done", stage=Stage.DONE, video_path=str(video),
                             settings=JobSettings(job_id="lib-done", mode="script_story", prompt="Salty ocean"))
            running = JobStatus(job_id="lib-run", stage=Stage.GENERATING,
                                settings=JobSettings(job_id="lib-run", prompt="Busy"))
            queue = app.STUDIO.queue
            with patch.dict(queue._jobs, {"lib-done": done, "lib-run": running}, clear=True), patch.object(queue, "_save"):
                (label, job_id), = app.library_choices()
                self.assertIn("· Story · Salty ocean", label)
                self.assertEqual(app.library_show(job_id)[0], str(video))
                self.assertFalse(queue.remove("lib-run"))          # still rendering: must be stopped first
                self.assertTrue(queue.remove("lib-done"))
                self.assertFalse(video.exists())
                self.assertEqual(app.library_choices(), [])
                self.assertIn("lib-run", queue._jobs)

    def test_narration_must_be_written_in_the_chosen_language(self):
        from video_studio import QualityError, check_narration_length, language_mismatch, spoken_lines
        urdu = "کیا آپ نے کبھی سوچا ہے کہ سمندر نمکین کیوں ہے؟"
        self.assertEqual(language_mismatch(urdu, "Urdu"), "")
        self.assertIn("Urdu", language_mismatch(urdu, "English"))
        self.assertEqual(language_mismatch("Why is the ocean salty? Rivers carry salt.", "English"), "")
        self.assertIn("English letters", language_mismatch("Samandar namkeen kyun hai, socha kabhi?", "Urdu"))
        self.assertEqual(language_mismatch("NASA", "Urdu"), "")           # too short to judge
        self.assertEqual(language_mismatch(urdu + " NASA", "Urdu"), "")   # a foreign name is fine
        script = f"Visual: ocean waves at sunrise\nVO: {urdu}"
        self.assertEqual(spoken_lines(script), urdu)                      # English keywords are not narration
        queue = app.STUDIO.queue
        with patch.object(queue, "submit") as submit:
            message = app.make_story("Ocean", script, "9:16", "English", "Female", "Calm", True, "Cinematic")[0]
            self.assertIn("Change Language", message)
            message = app.make_clip("A boat", "16:9", 5, urdu, "English", "Female", "Calm", True, "1080p", "Cinematic")[0]
            self.assertIn("Change Language", message)
            submit.assert_not_called()
        check_narration_length("A calm sentence of six words.", 2.4, "English")
        with self.assertRaisesRegex(QualityError, "could not read this text"):
            check_narration_length(urdu, 0.1, "English")

    def test_script_writer_rejects_a_reply_in_the_wrong_language(self):
        from video_studio import FreeLLMPromptEnhancer, QualityError, StudioConfig
        urdu_reply = ("Visual: ocean waves\nVO: کیا آپ نے کبھی سوچا ہے کہ سمندر نمکین کیوں ہے؟\n"
                      "Visual: river stones\nVO: دریا پہاڑوں سے نمک بہا کر سمندر تک لاتے ہیں۔")
        english_reply = ("Visual: ocean waves\nVO: Have you ever wondered why the sea is salty?\n"
                         "Visual: river stones\nVO: Rivers carry salt from the mountains to the sea.")

        class Reply:
            status_code = 200

            def __init__(self, text):
                self.text = text

        config = StudioConfig()
        config.gemini_api_key = ""
        with (patch.dict("os.environ", {"GEMINI_API_KEY": ""}), patch("video_studio.time.sleep"),
              patch("requests.get", side_effect=[Reply(urdu_reply), Reply(english_reply)]) as get):
            self.assertEqual(FreeLLMPromptEnhancer.generate_script("Ocean", 2, "English", config), english_reply)
            self.assertEqual(get.call_count, 2)
        with (patch.dict("os.environ", {"GEMINI_API_KEY": ""}), patch("video_studio.time.sleep"),
              patch("requests.get", return_value=Reply(urdu_reply))):
            self.assertEqual(FreeLLMPromptEnhancer.generate_script("Ocean", 2, "Urdu", config), urdu_reply)
            with self.assertRaises(QualityError):
                FreeLLMPromptEnhancer.generate_script("Ocean", 2, "English", config)

    def test_improve_idea_keeps_the_text_when_no_ai_answers(self):
        from video_studio import FreeLLMPromptEnhancer, StudioConfig
        config = StudioConfig()
        config.gemini_api_key = ""
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}), patch("requests.get", side_effect=OSError("offline")):
            self.assertEqual(FreeLLMPromptEnhancer.expand_with_ai("a boat", "Cinematic", config, offline_fallback=False), "")
            self.assertIn("boat", FreeLLMPromptEnhancer.expand_with_ai("a boat", "Cinematic", config))
        with (patch.object(FreeLLMPromptEnhancer, "expand_with_ai", return_value=""), patch.object(app.gr, "Warning") as warn):
            self.assertEqual(app.improve_idea("a boat", "Cinematic"), "a boat")
            warn.assert_called_once()

    def test_finished_video_brings_its_caption_and_report_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            video, srt = Path(tmp) / "result.mp4", Path(tmp) / "result.srt"
            video.write_bytes(b"video")
            srt.write_text("1", encoding="utf-8")
            done = JobStatus(job_id="files-done", stage=Stage.DONE, video_path=str(video), srt_path=str(srt),
                             settings=JobSettings(job_id="files-done", mode="script_story", prompt="Story"))
            with patch.object(app.STUDIO.queue, "all_jobs", return_value=[done]):
                first = sync_job_outputs()
                self.assertEqual(first[13]["value"], [str(srt)])
                self.assertTrue(first[13]["visible"])
                self.assertEqual(sync_job_outputs("", str(video))[13], app.gr.skip())

    def test_ai_pictures_are_requested_at_a_size_the_model_handles(self):
        from video_studio import MediaFetcher
        self.assertEqual(MediaFetcher.image_request_size(1080, 1920), (768, 1024))   # tall: no stacked pictures
        self.assertEqual(MediaFetcher.image_request_size(1920, 1080), (1024, 576))
        self.assertEqual(MediaFetcher.image_request_size(1080, 1080), (1024, 1024))
        self.assertEqual(MediaFetcher.image_request_size(832, 480), (832, 448))

    def test_settings_reject_a_bad_worker_url_without_saving(self):
        cfg = app.STUDIO.config
        with patch.object(cfg, "save") as save:
            self.assertIn("must start with https://", app.save_api_credentials("my-worker", "", "", "", ""))
            save.assert_not_called()

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
            self.assertIn("already running", app.make_clip("x", "16:9", 5, "", "", "", "", 1, "", "")[0])
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
