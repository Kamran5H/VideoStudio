import tempfile
import unittest
from pathlib import Path

import app
from app import job_progress_percent, render_job_card
from video_studio import (
    JobQueue,
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
        self.assertIn("width:100%;", card)

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
