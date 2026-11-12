import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('VIDEOSTUDIO_OUTPUT_DIR', tempfile.mkdtemp(prefix='vs_editor_tests_'))

import local_editor as editor
import video_studio as vs


class LocalEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def clip(self, name, audio=False):
        out = self.folder / name
        args = ['-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=30:duration=1.5']
        if audio:
            args += ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.5', '-c:a', 'aac']
        vs.run_ffmpeg([*args, *vs.VIDEO_ENCODE_ARGS, str(out)])
        return str(out)

    def test_join_preserves_duration_with_missing_audio(self):
        result = editor.join_clips([self.clip('a.mp4'), self.clip('b.mp4', True)])
        self.assertAlmostEqual(vs.ffprobe_duration(Path(result)), 3, delta=.15)
        self.assertIn('Audio:', vs._ffmpeg_info(Path(result)))

    def test_captions_are_trimmed_to_source_time(self):
        srt = self.folder / 'captions.srt'
        srt.write_text('1\n00:00:01,000 --> 00:00:03,000\nHello\n\n2\n00:00:04,000 --> 00:00:06,000\nWorld\n', encoding='utf-8')
        self.assertEqual(editor.caption_cues('', str(srt), 2, 3), [(0, 1, 'Hello'), (2, 3, 'World')])

    def test_render_urdu_caption_and_portrait_frame(self):
        result, status = editor.render_edit(self.clip('source.mp4', True), .2, 1.2, '9:16', True,
                                            'یہ ایک خوبصورت منظر ہے', None, 'Auto: less clutter', 1)
        w, h = vs.probe_video_size(Path(result))
        self.assertLess(w, h)
        self.assertAlmostEqual(vs.ffprobe_duration(Path(result)), 1, delta=.1)
        self.assertNotIn('Audio:', vs._ffmpeg_info(Path(result)))
        self.assertIn('captions:', status)

    def test_invalid_trim_is_rejected(self):
        with self.assertRaises(ValueError):
            editor.render_edit(self.clip('source.mp4'), 1, .5, 'Original', False, '', None, 'Bottom', 1)

    def test_recorded_narration_replaces_source_sound_and_is_padded(self):
        voice = self.folder / 'narration.wav'
        vs.run_ffmpeg(['-f', 'lavfi', '-i', 'sine=frequency=220:duration=0.5', str(voice)])
        result, _ = editor.render_edit(self.clip('source.mp4'), 0, 1.3, 'Original', True,
                                       '', None, 'Bottom', 1, False, str(voice))
        self.assertIn('Audio:', vs._ffmpeg_info(Path(result)))
        self.assertAlmostEqual(vs.ffprobe_duration(Path(result)), 1.3, delta=.1)


if __name__ == '__main__':
    unittest.main()
