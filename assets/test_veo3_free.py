#!/usr/bin/env python3
"""
test_veo3_free.py — Standalone Free VEO 3 AI Video Generator Test Script

Generates high-definition AI Videos directly from natural language prompts using 
100% free open AI video backends (Wan 2.1, LTX-Video, Pollinations AI, Optical-Flow Keyframe Engine).

Usage:
  python test_veo3_free.py "A golden retriever puppy playing in falling golden leaves, 4k cinematic"
  python test_veo3_free.py "Cyberpunk neon city with flying sports car" --aspect 9:16 --camera pan_left
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import argparse
from pathlib import Path

# Add current directory to path
script_dir = Path(__file__).resolve().parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

import warnings
warnings.filterwarnings("ignore", message=".*numpy.*")
warnings.filterwarnings("ignore", category=UserWarning)

from video_generator import Veo3FreeVideoEngine, FreeLLMPromptEnhancer

def main():
    parser = argparse.ArgumentParser(description="Free VEO 3 AI Video Generator Test")
    parser.add_argument("prompt", type=str, nargs="?", default="A glowing futuristic starship sailing through a purple nebula, 8k cinematic masterpiece", help="Text prompt for video generation")
    parser.add_argument("--aspect", type=str, default="16:9", choices=["16:9", "9:16", "1:1"], help="Aspect Ratio")
    parser.add_argument("--duration", type=float, default=4.0, help="Clip duration in seconds")
    parser.add_argument("--model", type=str, default="auto", choices=["auto", "wan2.1", "ltx-video", "pollinations_video", "morph"], help="AI Video Model backend")
    parser.add_argument("--camera", type=str, default="zoom_in", choices=["zoom_in", "pan_left", "pan_right", "tilt_up", "tilt_down", "handheld"], help="Camera motion preset")
    parser.add_argument("--style", type=str, default="cinematic", choices=["cinematic", "pixar", "anime", "cyberpunk", "vintage", "documentary", "gothic"], help="Visual aesthetic style preset")
    parser.add_argument("--output", type=str, default="./out/veo3_test.mp4", help="Output MP4 file path")

    args = parser.parse_args()

    print("=" * 60)
    print("      🎬 VEO 3 FREE AI VIDEO GENERATOR ENGINE TEST       ")
    print("      Cost: $0.00 | Free Open Weights AI Video Backend    ")
    print("=" * 60)

    prompt = args.prompt.strip()
    print(f"\n[1/3] Input Prompt: '{prompt}'")
    print(f"      Style Preset: {args.style.upper()}")

    # Step 1: VEO 3 Prompt Enhancer
    enhanced_prompt = FreeLLMPromptEnhancer.expand_prompt_veo3(prompt, aspect_ratio=args.aspect, style=args.style)
    print(f"\n[2/3] VEO 3 Enhanced Prompt: '{enhanced_prompt}'")

    # Step 2: VEO 3 Video Generation
    out_path = Path(args.output).resolve()
    print(f"\n[3/3] Generating Video (Aspect: {args.aspect}, Duration: {args.duration}s, Model: {args.model}, Style: {args.style})...")

    engine = Veo3FreeVideoEngine()
    result = engine.generate_veo3_video(
        prompt=prompt,
        output_path=out_path,
        aspect_ratio=args.aspect,
        duration=args.duration,
        model_type=args.model,
        camera_motion=args.camera,
        style=args.style
    )

    if result and result.exists() and result.stat().st_size > 0:
        file_size_mb = result.stat().st_size / (1024 * 1024)
        print(f"\n✨ SUCCESS! VEO 3 Video created successfully:")
        print(f"   📁 Output Path: {result}")
        print(f"   📦 File Size  : {file_size_mb:.2f} MB")
    else:
        print("\n❌ Failed to generate VEO 3 Video.")
        sys.exit(1)

if __name__ == "__main__":
    main()
