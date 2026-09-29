"""Opt-in end-to-end smoke checks for external services and media generation.

Run explicitly with ``python test_verify.py``. Automated test discovery only
runs the offline suite in ``tests/``.
"""

from pathlib import Path
import sys


def main() -> None:
    vs_dir = Path(__file__).resolve().parent
    if str(vs_dir) not in sys.path:
        sys.path.insert(0, str(vs_dir))

    import app
    import video_studio

    print("1. Testing VideoStudio Core Imports...", flush=True)
    print("   ✔ Imports succeeded cleanly.", flush=True)

    print("2. Testing Hardware Probe...", flush=True)
    probe = video_studio.HardwareProbe()
    print(f"   ✔ Hardware: {probe.summary()}", flush=True)

    print("3. Testing Prompt Enhancer...", flush=True)
    prompt = "Futuristic neon cyber city at night"
    enhanced = video_studio.PromptEngine().enhance(prompt, preset="Cyberpunk")
    assert enhanced and prompt in enhanced
    print(f"   ✔ Enhanced prompt: {enhanced[:80]}...", flush=True)

    print("4. Testing Multi-Scene Script Generator...", flush=True)
    script = video_studio.FreeLLMPromptEnhancer.generate_script("Deep Sea Wonders", scene_count=2)
    assert "Visual:" in script and "VO:" in script
    print("   ✔ Generated script preview:")
    for line in script.splitlines()[:4]:
        print(f"     {line}")

    print("5. Testing Neural Audio Voiceover Synthesis...", flush=True)
    vo_out, events = video_studio.AudioEngine().generate_voiceover(
        "Welcome to VideoStudio Pro.", language="English", gender="Male"
    )
    assert vo_out.exists() and vo_out.stat().st_size > 0
    print(f"   ✔ Voiceover generated: {vo_out} (Events: {len(events)})", flush=True)

    print("6. Testing Ambient Synth Pad Generator...", flush=True)
    bgm_out = video_studio.DEFAULT_OUTPUT_DIR / "audio_temp" / "test_synth_bgm.mp3"
    video_studio.AudioEngine().generate_synth_music("Ambient", 3.0, bgm_out)
    assert bgm_out.exists() and bgm_out.stat().st_size > 0
    print(f"   ✔ Synth BGM generated: {bgm_out}", flush=True)

    print("7. Testing SRT Subtitle Generation...", flush=True)
    srt_out = video_studio.DEFAULT_OUTPUT_DIR / "audio_temp" / "test_subs.srt"
    video_studio.SubtitleEngine.events_to_srt(events, srt_out)
    assert srt_out.exists() and srt_out.stat().st_size > 0
    print(f"   ✔ SRT generated: {srt_out}", flush=True)

    print("8. Testing Media Fetcher Fallbacks...", flush=True)
    fetcher = video_studio.MediaFetcher()
    visual_path, media_type = fetcher.fetch_scene_visual(
        "deep ocean glowing jellyfish", prefer_video=False
    )
    assert Path(visual_path).exists()
    print(f"   ✔ Visual asset fetched: {visual_path} (Type: {media_type})", flush=True)

    print("9. Testing Ken Burns 3D Video Motion...", flush=True)
    kb_video = video_studio.DEFAULT_OUTPUT_DIR / "test_kb_out.mp4"
    video_studio.VideoEngine.image_to_video_ken_burns(
        Path(visual_path), 2.5, 640, 360, fps=24, out_path=kb_video
    )
    assert kb_video.exists() and kb_video.stat().st_size > 0
    print(f"   ✔ Ken Burns video created: {kb_video}", flush=True)

    print("\n🎉 ALL 9 VERIFICATION CHECKS PASSED!\n", flush=True)
    app.STUDIO.queue.shutdown()


if __name__ == "__main__":
    main()
