import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from app import (
    DRAFT_DEFAULTS,
    clear_browser_draft,
    draft_field_save_js,
    job_progress_percent,
    readable_job_message,
    render_job_card,
    restore_browser_draft,
    restore_selected_tab_js,
    sync_restored_job_outputs,
)
from video_studio import (
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

    def test_job_card_escapes_untrusted_text_and_bounds_progress(self):
        card = render_job_card(
            "job<1>",
            "<script>alert('prompt')</script>",
            Stage.GENERATING,
            1.5,
            "<img src=x onerror=alert(1)>",
        )
        self.assertIn("job&lt;1&gt;", card)
        self.assertIn("&lt;script&gt;", card)
        self.assertIn("&lt;img", card)
        self.assertNotIn("<script>", card)
        self.assertNotIn("<img", card)
        self.assertIn("is-indeterminate", card)

    def test_queued_job_uses_waiting_indicator_instead_of_zero_percent(self):
        job = JobStatus(
            job_id="queued",
            stage=Stage.QUEUED,
            progress=0.62,
            message="Queued...",
            settings=JobSettings(job_id="queued", prompt="A queued video"),
        )
        card = render_job_card(
            job.job_id,
            job.settings.prompt,
            job.stage,
            job.progress,
            job.message,
            wait_label="Queue position 2 · 1 ahead",
        )
        self.assertIn("Queue position 2 · 1 ahead", card)
        self.assertIn("is-indeterminate", card)
        self.assertIn("Waiting for a worker", card)
        self.assertNotIn("62%", card)
        self.assertNotIn("aria-valuenow", card)

    def test_queue_wait_label_explains_position_and_retry_time(self):
        active = JobStatus(
            job_id="active",
            stage=Stage.GENERATING,
            settings=JobSettings(job_id="active"),
        )
        delayed = JobStatus(
            job_id="delayed",
            stage=Stage.QUEUED,
            not_before=200,
            settings=JobSettings(job_id="delayed"),
        )
        queued = JobStatus(
            job_id="queued",
            stage=Stage.QUEUED,
            settings=JobSettings(job_id="queued"),
        )
        self.assertEqual(
            app.queue_wait_label(queued, [active, delayed, queued], now=100),
            "Queue position 2 · 1 ahead",
        )
        queued.not_before = 165
        self.assertEqual(
            app.queue_wait_label(queued, [active, delayed, queued], now=100),
            "Retry window in 1m 05s",
        )

    def test_active_job_shows_truthful_indeterminate_progress_and_step(self):
        card = render_job_card(
            "active",
            "A rendering video",
            Stage.GENERATING,
            0.42,
            "Generating AI clip 2/4...",
        )
        self.assertIn("Current step", card)
        self.assertIn("In progress", card)
        self.assertIn("Generating AI clip 2/4...", card)
        self.assertIn('aria-label="Generating Video in progress"', card)
        self.assertNotIn("aria-valuenow", card)
        self.assertNotIn("42%", card)
        self.assertIn("is-indeterminate", card)

    def test_browser_draft_restore_validates_values_and_never_includes_credentials(self):
        values = restore_browser_draft(
            json.dumps(
                {
                    "ai_prompt": "A saved creative draft",
                    "ai_quality": "not-a-quality",
                    "ai_duration": 500,
                    "ai_subtitles": "yes",
                    "story_scenes": 3.6,
                    "gemini_api_key": "must not be restored",
                }
            )
        )
        restored = dict(zip(DRAFT_DEFAULTS, values))
        self.assertEqual(restored["ai_prompt"], "A saved creative draft")
        self.assertEqual(restored["ai_quality"], DRAFT_DEFAULTS["ai_quality"])
        self.assertEqual(restored["ai_duration"], 60)
        self.assertIs(restored["ai_subtitles"], DRAFT_DEFAULTS["ai_subtitles"])
        self.assertEqual(restored["story_scenes"], 4)
        self.assertNotIn("gemini_api_key", restored)
        self.assertEqual(restore_browser_draft("invalid-json"), tuple(DRAFT_DEFAULTS.values()))

    def test_clear_draft_restores_form_defaults(self):
        cleared = clear_browser_draft()
        self.assertEqual(dict(zip(DRAFT_DEFAULTS, cleared)), DRAFT_DEFAULTS)
        self.assertIn("sessionStorage", restore_selected_tab_js())

    def test_draft_persistence_is_tab_session_scoped(self):
        js = draft_field_save_js("ai_prompt")
        self.assertIn("sessionStorage", js)
        self.assertNotIn("localStorage", js)
        self.assertIn("event.isTrusted", app.HEAD_JS)

    def test_failed_job_shows_readable_summary_and_redacted_error_details(self):
        job = JobStatus(
            job_id="failed",
            stage=Stage.FAILED,
            message="Failed after retries",
            error="Authentication failed with test-secret-token",
            settings=JobSettings(job_id="failed"),
        )
        previous = app.STUDIO.config.hf_token
        app.STUDIO.config.hf_token = "test-secret-token"
        try:
            self.assertIn("could not finish", readable_job_message(job))
            card = render_job_card(
                job.job_id,
                "A failed render",
                job.stage,
                job.progress,
                readable_job_message(job),
                error=job.error,
            )
            self.assertIn("Technical error details", card)
            self.assertIn("[redacted]", card)
            self.assertNotIn("test-secret-token", card)
        finally:
            app.STUDIO.config.hf_token = previous

    def test_restored_job_sync_reconnects_result_without_reloading_same_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "result.mp4"
            subtitles = Path(tmp) / "result.srt"
            video.write_bytes(b"video")
            subtitles.write_text("captions", encoding="utf-8")
            completed = JobStatus(
                job_id="ai-done",
                stage=Stage.DONE,
                progress=1,
                message="Completed",
                video_path=str(video),
                srt_path=str(subtitles),
                settings=JobSettings(
                    job_id="ai-done",
                    mode="ai_video",
                    prompt="Previous AI result",
                    created_at=1,
                ),
            )
            active_story = JobStatus(
                job_id="story-active",
                stage=Stage.GENERATING,
                progress=0.5,
                message="Rendering scene 2",
                settings=JobSettings(
                    job_id="story-active",
                    mode="script_story",
                    prompt="Current storyboard",
                    created_at=2,
                ),
            )
            with patch.object(app.STUDIO.queue, "all_jobs", return_value=[completed, active_story]):
                initial = sync_restored_job_outputs()
                self.assertIn("story-active", initial[0])
                self.assertIn("Previous AI result", initial[1])
                self.assertEqual(initial[2], str(video))
                self.assertEqual(initial[3], str(subtitles))
                self.assertEqual(initial[4], "")
                self.assertEqual(initial[7], "story-active")
                self.assertEqual(initial[8], str(video))
                self.assertEqual(initial[9], str(subtitles))

                repeated = sync_restored_job_outputs(str(video), str(subtitles), "")
                self.assertEqual(repeated[2], app.gr.skip())
                self.assertEqual(repeated[3], app.gr.skip())
                self.assertEqual(repeated[8], str(video))

    def test_invalid_progress_is_safe(self):
        self.assertEqual(job_progress_percent(float("nan")), 0)
        self.assertEqual(job_progress_percent("invalid"), 0)
        self.assertEqual(job_progress_percent(-0.5), 0)
        self.assertEqual(job_progress_percent(2), 100)
        self.assertEqual(Stage.from_str("FAILED"), Stage.FAILED)

    def test_view_only_queue_rejects_mutations(self):
        queue = app.STUDIO.queue
        is_worker = queue.is_worker
        queue.is_worker = False
        try:
            with self.assertRaisesRegex(RuntimeError, "view-only"):
                queue.submit(app.JobSettings(prompt="must not be enqueued"))
            self.assertFalse(queue.cancel("missing-job"))
            self.assertFalse(queue.retry("missing-job"))
            queue._auto_resume_interrupted_jobs()
        finally:
            queue.is_worker = is_worker

    def test_invalid_worker_lock_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = object.__new__(JobQueue)
            queue._lock_path = Path(tmp) / "worker.lock"
            queue._lock_path.write_text("not-a-process-id", encoding="utf-8")
            self.assertFalse(queue._acquire_worker_lock())
            self.assertEqual(queue._lock_path.read_text(encoding="utf-8"), "not-a-process-id")


if __name__ == "__main__":
    unittest.main()
