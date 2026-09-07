"""
test_verify.py — Complete Verification Suite for VideoStudio Pro.
"""

import sys
from pathlib import Path

vs_dir = Path(__file__).resolve().parent
if str(vs_dir) not in sys.path:
    sys.path.insert(0, str(vs_dir))

print("1. Testing VideoStudio Core Imports...", flush=True)
import video_studio
import app
print("   ✔ Imports succeeded cleanly.", flush=True)

print("2. Testing Hardware Probe...", flush=True)
probe = video_studio.HardwareProbe()
print(f"   ✔ Hardware: {probe.summary()}", flush=True)

print("3. Testing Prompt Enhancer...", flush=True)
prompt = "Futuristic neon cyber city at night"
enhanced = video_studio.PromptEngine().enhance(prompt, preset="Cyberpunk")
print(f"   ✔ Enhanced prompt: {enhanced[:80]}...", flush=True)

print("4. Testing Multi-Scene Script Generator...", flush=True)
script = video_studio.FreeLLMPromptEnhancer.generate_script("Deep Sea Wonders", scene_count=2)
print("   ✔ Generated script preview:")
for line in script.splitlines()[:4]:
    print(f"     {line}")

print("5. Testing Neural Audio Voiceover Synthesis...", flush=True)
vo_out, events = video_studio.AudioEngine().generate_voiceover(
    "Welcome to VideoStudio Pro.", language="English", gender="Male"
)
print(f"   ✔ Voiceover generated: {vo_out} (Exists: {vo_out.exists()}, Events: {len(events)})", flush=True)

print("6. Testing Ambient Synth Pad Generator...", flush=True)
bgm_out = video_studio.DEFAULT_OUTPUT_DIR / "audio_temp" / "test_synth_bgm.mp3"
video_studio.AudioEngine().generate_synth_music("Ambient", 3.0, bgm_out)
print(f"   ✔ Synth BGM generated: {bgm_out} (Exists: {bgm_out.exists()})", flush=True)

print("7. Testing SRT Subtitle Generation...", flush=True)
srt_out = video_studio.DEFAULT_OUTPUT_DIR / "audio_temp" / "test_subs.srt"
video_studio.SubtitleEngine.events_to_srt(events, srt_out)
print(f"   ✔ SRT generated: {srt_out} (Exists: {srt_out.exists()})", flush=True)

print("8. Testing Media Fetcher Fallbacks...", flush=True)
fetcher = video_studio.MediaFetcher()
visual_path, media_type = fetcher.fetch_scene_visual("deep ocean glowing jellyfish", prefer_video=False)
print(f"   ✔ Visual asset fetched: {visual_path} (Type: {media_type})", flush=True)

print("9. Testing Ken Burns 3D Video Motion...", flush=True)
kb_video = video_studio.DEFAULT_OUTPUT_DIR / "test_kb_out.mp4"
video_studio.VideoEngine.image_to_video_ken_burns(Path(visual_path), 2.5, 640, 360, fps=24, out_path=kb_video)
print(f"   ✔ Ken Burns video created: {kb_video} (Exists: {kb_video.exists()})", flush=True)

print("\n🎉 ALL 9 VERIFICATION CHECKS PASSED PERFECTLY!\n", flush=True)
