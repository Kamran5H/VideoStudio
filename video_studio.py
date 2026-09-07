"""
video_studio.py — Unified AI Video Generation & Storyboard Studio Engine.
Crafted with precision for Kamran Ashraf (Kami).

All-in-One Architecture:
  1. Environment & Hardware Probe    — HardwareProbe (CPU/GPU/CUDA/RAM/Disk)
  2. Configuration & Persistence     — StudioConfig (Colab, HF, Pexels, Pixabay, Gemini keys)
  3. Prompt Engine & Script Writer    — PromptEngine & FreeLLMPromptEnhancer (10+ styles, Pollinations, DDG, Gemini)
  4. Generative AI Video Backends     — Colab (Wan2.1-1.3B), LTX-Video, CogVideoX, Wan2.1-14B, Veo3 Free, Test Pattern
  5. Media & Stock Asset Fetcher      — MediaFetcher (Pexels Video/Photo, Pixabay, Wikimedia, Pollinations Flux, Gemini)
  6. Multilingual Neural Audio        — AudioEngine (Edge-TTS 10+ langs, Punjabi/Urdu/English/Arabic, Synth BGM, Auto-Ducking)
  7. Professional Subtitles & Karaoke — SubtitleEngine (Whisper Word-by-Word Karaoke, Styled Burn-in, Translation)
  8. Video Compositor & FX            — VideoEngine (Ken Burns 3D Zoom, 60fps Interpolation, 4K Upscale, Watermark, Anti-Fingerprint)
  9. Pipeline Orchestrator            — VideoStudioPro (AI Diffusion, Multi-Scene Storyboards, Hybrid Storytelling)
 10. Persistent Batch Job Queue       — JobQueue (JSON persistence, auto-retry on quota refresh, live callbacks)
 11. Native Desktop GUI Fallback      — VideoStudioDesktopGUI (Dark theme Tkinter GUI)
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import logging.handlers
import math
import os
import queue
import random
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# Ensure UTF-8 stream handling for Windows consoles
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# Suppress Hugging Face caching symlink warnings
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

# ----------------------------------------------------------------------------
# Paths & Global Constants
# ----------------------------------------------------------------------------

STUDIO_ROOT = Path(__file__).resolve().parent
# Output dir is relocatable via env so heavy render churn can be moved OFF a
# OneDrive-synced folder (cloud sync locks files mid-encode). Default stays in
# the repo so existing paths/README keep working.
_OUT_ENV = os.environ.get("VIDEOSTUDIO_OUTPUT_DIR", "").strip()
DEFAULT_OUTPUT_DIR = Path(_OUT_ENV).expanduser() if _OUT_ENV else (STUDIO_ROOT / "outputs")
CONFIG_PATH = STUDIO_ROOT / "studio_config.json"
MUSIC_DIR = STUDIO_ROOT / "music"
ASSETS_DIR = STUDIO_ROOT / "assets"
MUSIC_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

WAN_NATIVE_FPS = 16                        # models generate at 16 fps natively
CHUNK_SECONDS = 5.0                        # native clip length per generation call
CROSSFADE_SECONDS = 0.5


def frames_for(seconds: float, fps: int = WAN_NATIVE_FPS) -> int:
    """Frames for a requested clip length, snapped to the 4n+1 the Wan/LTX
    families require. Never returns fewer than ~1s so a tiny request still
    produces a valid clip."""
    n = max(int(round(seconds * fps)), fps)
    return (n // 4) * 4 + 1

# Load Environment Variables from potential locations
try:
    from dotenv import load_dotenv
    load_dotenv(STUDIO_ROOT / ".env")
    load_dotenv(STUDIO_ROOT / ".keys.env")
    load_dotenv(STUDIO_ROOT.parent / ".keys.env")
    load_dotenv(STUDIO_ROOT.parent / ".env")
    load_dotenv()
except ImportError:
    pass

# Global Queue for GUI / Web UI log streaming
log_queue: queue.Queue = queue.Queue(maxsize=1000)

# ----------------------------------------------------------------------------
# Logging System
# ----------------------------------------------------------------------------

class ColoredFormatter(logging.Formatter):
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    RESET = "\033[0m"

    def format(self, record):
        msg = super().format(record)
        if record.levelno >= logging.ERROR:
            return f"{self.RED}{msg}{self.RESET}"
        elif record.levelno >= logging.WARNING:
            return f"{self.YELLOW}{msg}{self.RESET}"
        elif record.levelno >= logging.INFO:
            return f"{self.GREEN}{msg}{self.RESET}"
        return f"{self.CYAN}{msg}{self.RESET}"

class QueueLogHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record):
        try:
            msg = self.format(record)
            if self.q.full():
                try:
                    self.q.get_nowait()
                except queue.Empty:
                    pass
            self.q.put_nowait(msg)
        except Exception:
            pass

def _build_logger() -> logging.Logger:
    logger = logging.getLogger("VideoStudio")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S")

    # Console Handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(ColoredFormatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S"))
    logger.addHandler(console)

    # File Handler
    log_dir = DEFAULT_OUTPUT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_h = logging.handlers.RotatingFileHandler(
        log_dir / "studio.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_h.setLevel(logging.DEBUG)
    file_h.setFormatter(fmt)
    logger.addHandler(file_h)

    # Queue Handler for GUI
    q_h = QueueLogHandler(log_queue)
    q_h.setLevel(logging.INFO)
    q_h.setFormatter(fmt)
    logger.addHandler(q_h)

    # Silence noisy loggers
    for name in ("urllib3", "requests", "huggingface_hub", "faster_whisper", "moviepy", "asyncio", "gradio_client"):
        logging.getLogger(name).setLevel(logging.WARNING)

    return logger

log = _build_logger()

# ----------------------------------------------------------------------------
# FFMPEG & Probe Utilities
# ----------------------------------------------------------------------------

def ffmpeg_exe() -> str:
    """Locate an ffmpeg binary: system PATH first, then imageio-ffmpeg's bundled one."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"

def ffprobe_exe() -> Optional[str]:
    return shutil.which("ffprobe")

def run_ffmpeg(args: list[str], timeout: int = 1800) -> None:
    """Run ffmpeg with arguments; raise on failure."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {proc.stderr[-2000:]}")

def ffprobe_duration(path: Path) -> float:
    """Video/audio duration in seconds via ffprobe or ffmpeg fallback.

    Both probes are bounded by a timeout so a corrupt or streaming input can
    never hang the worker thread; on timeout we fall through to 0.0.
    """
    ffprobe = ffprobe_exe()
    if ffprobe:
        try:
            proc = subprocess.run(
                [ffprobe, "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, timeout=60,
            )
            return float(proc.stdout.strip())
        except (ValueError, subprocess.TimeoutExpired):
            pass
    # Fallback: parse Duration from ffmpeg stderr
    try:
        proc = subprocess.run([ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", proc.stderr)
    if not m:
        return 0.0
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)

# ----------------------------------------------------------------------------
# 1. Environment & Hardware Layer
# ----------------------------------------------------------------------------

class HardwareProbe:
    """Detects GPU/CPU/RAM/disk and determines hardware capabilities."""

    def __init__(self) -> None:
        self.has_cuda = False
        self.vram_gb = 0.0
        self.gpu_name = "None (CPU Mode)"
        self.ram_gb = 0.0
        self.free_disk_gb = 0.0
        self._probe()

    def _probe(self) -> None:
        try:
            import torch
            self.has_cuda = torch.cuda.is_available()
            if self.has_cuda:
                props = torch.cuda.get_device_properties(0)
                self.gpu_name = props.name
                self.vram_gb = props.total_memory / 1e9
        except Exception:
            pass

        try:
            if sys.platform == "win32":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                mem_kb = ctypes.c_ulonglong()
                kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(mem_kb))
                self.ram_gb = mem_kb.value / 1024 / 1024
            else:
                self.ram_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
        except Exception:
            self.ram_gb = 8.0

        try:
            self.free_disk_gb = shutil.disk_usage(STUDIO_ROOT).free / 1e9
        except Exception:
            self.free_disk_gb = 50.0

    @property
    def can_run_wan_locally(self) -> bool:
        return self.has_cuda and self.vram_gb >= 8.0 and self.free_disk_gb >= 25.0

    def vram_warning(self, quality: str) -> str:
        needs = {"480p": 8, "720p": 16, "1080p": 16, "4K": 24}
        if self.can_run_wan_locally and self.vram_gb < needs.get(quality, 8):
            return f"⚠️ Local GPU has {self.vram_gb:.0f} GB VRAM — {quality} native is generated at 480p and upscaled."
        if not self.can_run_wan_locally:
            return f"☁️ Cloud GPU Mode ({self.gpu_name}) — generation runs on high-speed cloud GPUs; {quality} above 480p/720p is upscaled."
        return ""

    def summary(self) -> str:
        return (
            f"GPU: {self.gpu_name} ({self.vram_gb:.1f} GB VRAM) | CUDA: {self.has_cuda} | "
            f"RAM: {self.ram_gb:.1f} GB | Free Disk: {self.free_disk_gb:.1f} GB | "
            f"Local Wan2.1: {self.can_run_wan_locally}"
        )

# ----------------------------------------------------------------------------
# 2. Configuration Layer
# ----------------------------------------------------------------------------

@dataclass
class StudioConfig:
    """Master configuration for the entire unified VideoStudio suite."""
    output_dir: str = str(DEFAULT_OUTPUT_DIR)
    hf_token: str = ""
    colab_url: str = ""
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    gemini_api_key: str = ""
    backend_order: list[str] = field(default_factory=lambda: [
        "colab", "ltx", "cogvideox", "wan_official", "test_pattern"
    ])
    quota_wait_cap_min: int = 45
    max_quota_waits_per_job: int = 2
    max_deferrals_per_job: int = 48
    wan_cooldown_hours: float = 3.0
    allow_test_pattern_fallback: bool = False
    default_steps: int = 30
    default_guidance: float = 6.0
    max_retries: int = 1
    max_job_retries: int = 3                  # hard cap: a job that keeps throwing
    # a NON-quota error is marked Failed after this many worker attempts instead
    # of looping forever and starving the rest of the queue
    max_videos_kept: int = 60                 # prune older finished videos on start

    def __post_init__(self):
        # Auto-load API keys from environment if empty
        if not self.hf_token:
            self.hf_token = os.environ.get("HF_TOKEN", "") or os.environ.get("HUGGINGFACE_TOKEN", "")
        if not self.pexels_api_key:
            self.pexels_api_key = os.environ.get("PEXELS_API_KEY", "")
        if not self.pixabay_api_key:
            self.pixabay_api_key = os.environ.get("PIXABAY_API_KEY", "")
        if not self.gemini_api_key:
            self.gemini_api_key = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.write_text(json.dumps(dataclasses.asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "StudioConfig":
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                known = {f.name for f in dataclasses.fields(cls)}
                cfg = cls(**{k: v for k, v in data.items() if k in known})
                # Drop the retired veo3_free pseudo-backend from any old config.
                cfg.backend_order = [b for b in cfg.backend_order if b != "veo3_free"]
                for be in ("colab", "ltx", "cogvideox", "wan_official"):
                    if be not in cfg.backend_order:
                        idx = cfg.backend_order.index("test_pattern") if "test_pattern" in cfg.backend_order else len(cfg.backend_order)
                        cfg.backend_order.insert(idx, be)
                return cfg
            except Exception as exc:
                log.warning("Config load failed (%s); using defaults", exc)
        return cls()

# ----------------------------------------------------------------------------
# 3. Prompt Engine & Script Writer
# ----------------------------------------------------------------------------

DEFAULT_NEGATIVE = (
    "blurry, low quality, worst quality, jpeg artifacts, watermark, text overlay, "
    "logo, deformed anatomy, extra limbs, mutated hands, disfigured face, "
    "flickering, jitter, stutter, frame skipping, static image, overexposed, "
    "washed out colors, oversaturated, grainy noise, compression artifacts, "
    "duplicate frames, cropped, out of frame"
)

STYLE_PRESETS: dict[str, dict[str, str]] = {
    "Cinematic": {
        "prefix": "Cinematic 35mm film still in motion,",
        "camera": "slow dolly-in on a 35mm anamorphic lens, shallow depth of field",
        "light": "dramatic golden-hour volumetric key light with soft rim lighting",
        "grade": "filmic teal-and-orange color grading, 35mm organic texture",
        "mood": "epic, emotionally resonant atmosphere",
    },
    "Pixar 3D": {
        "prefix": "Pixar 3D animation masterpiece in motion,",
        "camera": "playful animated camera tracking, dynamic character framing",
        "light": "rich subsurface scattering, warm vibrant bounce lighting",
        "grade": "lush cheerful color palette, clean render textures",
        "mood": "heartwarming, enchanting, lively energy",
    },
    "Anime": {
        "prefix": "Studio Ghibli style high-end anime scene in motion,",
        "camera": "dynamic sweeping camera pan with dramatic perspective lines",
        "light": "vibrant cel-shaded lighting, glowing environmental particles",
        "grade": "rich saturated watercolours, crisp animation outlines",
        "mood": "expressive, poetic, breathtaking atmosphere",
    },
    "Cyberpunk": {
        "prefix": "Futuristic cyberpunk neon sci-fi footage in motion,",
        "camera": "tracking shot through rain-slicked futuristic streets, wide 20mm lens",
        "light": "vibrant neon glow, reflections in puddles, volumetric mist and fog",
        "grade": "deep blues, vibrant magenta and cyan neon hues",
        "mood": "high-tech, mysterious, adrenaline-charged tone",
    },
    "Documentary": {
        "prefix": "National Geographic 8K documentary footage in motion,",
        "camera": "handheld observational tracking shot on a 24mm prime lens",
        "light": "unfiltered natural sunlight, true-to-life dynamic range",
        "grade": "authentic realistic color science, pristine clarity",
        "mood": "engaging, authentic, educational realism",
    },
    "Hyper-realistic": {
        "prefix": "Ultra photorealistic 8K cinematic footage,",
        "camera": "smooth steadicam glide on an 85mm prime lens, razor-sharp focus",
        "light": "physically accurate global illumination, soft diffused fill",
        "grade": "true-to-life organic contrast, intricate surface textures",
        "mood": "lifelike, tangible realism",
    },
    "Drone/Aerial": {
        "prefix": "Breathtaking high-altitude aerial drone footage,",
        "camera": "forward sweeping flight with a slow gimbal tilt-down, 14mm ultra-wide",
        "light": "crisp morning light with long dramatic shadows, atmospheric haze",
        "grade": "vivid landscape grade, deep blue skies, ultra HDR detail",
        "mood": "vast, majestic, awe-inspiring scale",
    },
    "Vintage 35mm": {
        "prefix": "Authentic 1970s 35mm vintage film footage,",
        "camera": "subtle organic handheld movement, classic zoom lens",
        "light": "warm nostalgic daylight, natural lens flare",
        "grade": "kodachrome film stock emulation, authentic grain, soft roll-off",
        "mood": "nostalgic, timeless, evocative atmosphere",
    },
    "Commercial/Ad": {
        "prefix": "Premium luxury commercial advertisement shot,",
        "camera": "smooth robotic arm orbit with macro detail inserts, hero framing",
        "light": "polished studio lighting, gleaming specular highlights",
        "grade": "clean vibrant grade, glossy finish, immaculate styling",
        "mood": "aspirational, premium brand elegance",
    },
    "Slow-motion": {
        "prefix": "Ultra high-speed 1000fps slow-motion capture,",
        "camera": "locked-off macro framing catching micro-movements suspended in time",
        "light": "high-intensity studio directional light freezing particles and droplets",
        "grade": "crisp high-contrast grade, crystal-clear motion detail",
        "mood": "mesmerizing, hyper-detailed, poetic",
    },
    "Gothic": {
        "prefix": "Dark gothic fantasy cinematic footage,",
        "camera": "slow creeping dolly shot amidst ancient architecture, 50mm lens",
        "light": "chiaroscuro lighting, deep shadows, slivers of moonlight",
        "grade": "desaturated moody tones, cold blue and charcoal grade",
        "mood": "mysterious, haunting, legendary depth",
    },
}

_MOTION_HINTS = [
    "fluid natural motion", "smooth coherent movement", "consistent object permanence",
    "physically plausible dynamics", "seamless continuous action", "subtle organic drift",
]

class PromptEngine:
    """Prompt engineering & AI expansion engine."""

    def enhance(self, prompt: str, preset: str = "Cinematic", seed: Optional[int] = None) -> str:
        prompt = prompt.strip().rstrip(".")
        if not prompt:
            return prompt
        style = STYLE_PRESETS.get(preset, STYLE_PRESETS["Cinematic"])
        rng = random.Random(seed if seed is not None else hash(prompt) & 0xFFFF)
        motion = rng.choice(_MOTION_HINTS)
        parts = [
            f"{style['prefix']} {prompt}.",
            f"Camera: {style['camera']}.",
            f"Lighting: {style['light']}.",
            f"Color: {style['grade']}.",
            f"Motion: {motion}, {style['mood']}.",
            "Highly detailed, coherent scene geometry, professional composition, 8k resolution.",
        ]
        return " ".join(parts)

    @staticmethod
    def negative(user_negative: str = "") -> str:
        extra = user_negative.strip()
        return f"{DEFAULT_NEGATIVE}, {extra}" if extra else DEFAULT_NEGATIVE

class FreeLLMPromptEnhancer:
    """Gemini AI Pro & Zero-Billing AI Director Engine.
    Leverages Gemini Pro Plan with smart failover across free models (Gemini 2.5 Flash,
    1.5 Flash, 2.0 Flash Lite, Pollinations, and Deterministic Cinema Templates).
    Ensures 0$ billing, 0 token exhaustion crashes, and maximum cinematic quality.
    """

    @staticmethod
    def expand_with_ai(prompt: str, style: str = "Cinematic", config: Optional[StudioConfig] = None) -> str:
        clean_p = prompt.strip()
        if not clean_p:
            return ""

        cfg = config or StudioConfig.load()
        gemini_key = cfg.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")

        # 1. Try Gemini AI Pro Plan (Google GenAI SDK)
        if gemini_key:
            try:
                from google import genai
                client = genai.Client(api_key=gemini_key)
                style_data = STYLE_PRESETS.get(style, STYLE_PRESETS["Cinematic"])
                sys_inst = (
                    f"You are an expert Hollywood AI video director and Veo 3 / Nano Banana prompt engineer. "
                    f"Transform this idea into a breathtaking, hyper-detailed single-sentence video prompt in the '{style}' aesthetic ({style_data['prefix']}). "
                    f"Specify camera ({style_data['camera']}), lighting ({style_data['light']}), and grade ({style_data['grade']}). "
                    "Output ONLY the prompt text, no commentary, under 75 words."
                )
                
                # Try Flash models first (highest RPM/TPM on Pro/Free tiers)
                for model_name in ("gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.0-flash-lite"):
                    try:
                        resp = client.models.generate_content(
                            model=model_name,
                            contents=f"{sys_inst}\n\nUser Concept: {clean_p}"
                        )
                        if resp.text and len(resp.text.strip()) > 10:
                            return resp.text.strip().replace('"', '')
                    except Exception as e_mod:
                        log.debug("Gemini model %s note: %s", model_name, e_mod)
                        continue
            except Exception as exc:
                log.debug("Gemini Pro API pass-through: %s", exc)

        # 2. Try Pollinations Free LLM Endpoint (No key / Zero billing)
        try:
            import requests
            import urllib.parse
            q_enc = urllib.parse.quote(f"Expand into 50-word cinematic {style} video prompt: {clean_p}")
            resp = requests.get(f"https://text.pollinations.ai/{q_enc}", timeout=6)
            if resp.status_code == 200 and len(resp.text.strip()) > 15:
                return resp.text.strip().replace('"', '')
        except Exception:
            pass

        # 3. Deterministic Master Cinematic Prompt Engine (Offline, 0ms, Zero Cost)
        return PromptEngine().enhance(clean_p, preset=style)

    @staticmethod
    def generate_script(topic: str, scene_count: int = 4, language: str = "English", config: Optional[StudioConfig] = None) -> str:
        """Generates a structured multi-scene storyboard script with Visual and VO blocks via Gemini Pro."""
        prompt = topic.strip()
        if not prompt:
            return ""

        cfg = config or StudioConfig.load()
        gemini_key = cfg.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")

        # 1. Try Gemini AI Pro Plan for Scriptwriting
        if gemini_key:
            try:
                from google import genai
                client = genai.Client(api_key=gemini_key)
                sys_inst = (
                    f"You are an award-winning documentary & cinematic video director. "
                    f"Write an engaging {scene_count}-scene video script about '{prompt}' in {language}. "
                    "For each scene, structure it EXACTLY as:\n\n"
                    "Visual: [3-5 visual keywords for stock footage / Nano Banana generation, e.g., golden sunrise mountains drone aerial]\n"
                    "VO: [1-2 sentences of spoken voiceover narration in {language}]\n\n"
                    "Output ONLY the Visual: and VO: lines without headers or numbering."
                )
                for model_name in ("gemini-2.5-flash", "gemini-1.5-flash"):
                    try:
                        resp = client.models.generate_content(
                            model=model_name,
                            contents=sys_inst
                        )
                        if resp.text and "Visual:" in resp.text:
                            return resp.text.strip()
                    except Exception:
                        continue
            except Exception as e:
                log.debug("Gemini script generation fallback: %s", e)

        # 2. Template Fallback Script
        return (
            f"Visual: {prompt} aerial dramatic cinematic 8k\n"
            f"VO: Welcome to an exploration of {prompt}. Every detail tells a story of wonder and discovery.\n\n"
            f"Visual: {prompt} close up detail macro lighting\n"
            f"VO: When we look closer, we uncover the hidden beauty and intricate dynamics at play.\n\n"
            f"Visual: {prompt} epic landscape golden hour\n"
            f"VO: The possibilities are endless when creativity meets innovation.\n\n"
            f"Visual: {prompt} inspiring sunrise horizon\n"
            f"VO: Thank you for watching. Like, follow, and stay inspired for what comes next."
        )

# ----------------------------------------------------------------------------
# 4. Generative AI Video Backends
# ----------------------------------------------------------------------------

ASPECT_SIZES = {
    "16:9": (832, 480),
    "9:16": (480, 832),
    "1:1": (624, 624),
    "21:9": (960, 416),
    "4:3": (704, 544),
}

ASPECT_SIZES_720 = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1": (960, 960),
    "21:9": (1280, 544),
    "4:3": (960, 720),
}

QUALITY_TIERS = ["480p", "720p", "1080p", "4K"]

@dataclass
class GenerationRequest:
    prompt: str
    negative_prompt: str = DEFAULT_NEGATIVE
    width: int = 832
    height: int = 480
    num_frames: int = 81
    steps: int = 30
    guidance: float = 6.0
    seed: int = -1
    fps: int = WAN_NATIVE_FPS
    init_image: Optional[str] = None

class QuotaExhausted(RuntimeError):
    def __init__(self, wait_seconds: int, reason: str = "Free Cloud GPU Quota Busy"):
        super().__init__(f"{reason} — retry in {wait_seconds}s")
        self.wait_seconds = wait_seconds
        self.reason = reason

_TRANSIENT_MARKERS = (
    "getaddrinfo failed", "connection", "reset by peer", "temporarily unavailable",
    "502", "503", "504", "read timed out", "timed out", "network is unreachable",
    "max retries", "connectionerror", "remote end closed",
)


def is_transient_error(error_text: str) -> bool:
    """True for network-ish failures that deserve a defer-and-retry, not a fail."""
    low = (error_text or "").lower()
    return any(m in low for m in _TRANSIENT_MARKERS)


def quota_wait_seconds(error_text: str) -> Optional[int]:
    if "ZeroGPU quota" not in error_text:
        return None
    hint = 0
    m = re.search(r"Try again in (\d+):(\d{2}):(\d{2})", error_text)
    if m:
        h, mnt, sec = (int(g) for g in m.groups())
        hint = h * 3600 + mnt * 60 + sec
    need = 0
    m2 = re.search(r"\((\d+)s requested vs\.? (-?\d+)s left\)", error_text)
    if m2:
        need = int(m2.group(1)) - int(m2.group(2))
    return max(hint, min(need, 3600), 60) + 20

class GenerationBackend:
    name = "abstract"
    description = ""
    supports_image_conditioning = False

    def available(self) -> bool:
        return True

    def generate(self, req: GenerationRequest, out_path: Path, progress: Callable[[str], None] = lambda m: None) -> Path:
        raise NotImplementedError

    @staticmethod
    def _extract_video(result: Any) -> Optional[str]:
        if isinstance(result, (str, Path)):
            s = str(result)
            # Accept a real local file OR a remote media URL (some Spaces return
            # a URL string rather than a downloaded temp path).
            if Path(s).exists():
                return s
            if s.lower().split("?")[0].endswith((".mp4", ".webm", ".mov")):
                return s
            return None
        if isinstance(result, (list, tuple)):
            for item in result:
                found = GenerationBackend._extract_video(item)
                if found:
                    return found
            return None
        if isinstance(result, dict):
            for k in ("video", "path", "url", "file", "name", "value"):
                if result.get(k):
                    found = GenerationBackend._extract_video(result[k])
                    if found:
                        return found
        return None

    @staticmethod
    def _accepted_params(client: Any, api_name: str) -> Optional[set[str]]:
        """Introspect a gradio Space endpoint and return its accepted parameter
        names. Used to FILTER our kwargs so a Space that renamed/dropped a
        parameter can never crash us with 'unexpected keyword argument' — the
        single most common way these free Spaces break over time."""
        try:
            api = client.view_api(return_format="dict", print_info=False)
            for group in ("named_endpoints", "unnamed_endpoints"):
                eps = api.get(group, {}) or {}
                ep = eps.get(api_name)
                if ep:
                    names = {(p.get("parameter_name") or "").strip()
                             for p in ep.get("parameters", [])}
                    names.discard("")
                    return names or None
        except Exception:
            pass
        return None

class ColabBackend(GenerationBackend):
    name = "colab"
    description = "Free Google Colab T4 GPU Worker (Wan2.1)"

    def __init__(self, config: StudioConfig):
        self.config = config
        self._client: Any = None
        self._client_url = ""

    def available(self) -> bool:
        return bool(self.config.colab_url.strip())

    def _get_client(self) -> Any:
        from gradio_client import Client
        url = self.config.colab_url.strip()
        if self._client is None or self._client_url != url:
            self._client = Client(url, verbose=False)
            self._client_url = url
        return self._client

    @staticmethod
    def _snap_wan13b(w: int, h: int) -> tuple[int, int]:
        """Wan2.1-T2V-1.3B only supports 832x480 (landscape) and 480x832
        (portrait). Any other bucket makes the model error, so snap to the
        matching orientation instead of silently falling through to a weaker
        backend."""
        return (832, 480) if w >= h else (480, 832)

    def generate(self, req: GenerationRequest, out_path: Path, progress: Callable[[str], None] = lambda m: None) -> Path:
        w, h = self._snap_wan13b(req.width, req.height)
        progress(f"Colab worker: generating {w}x{h}, {req.num_frames} frames, {req.steps} steps...")
        client = self._get_client()
        result = client.predict(
            req.prompt, req.negative_prompt, w, h,
            req.num_frames, req.steps, req.guidance, req.seed,
            api_name="/generate",
        )
        v_path = self._extract_video(result)
        if not v_path:
            raise RuntimeError(f"Colab returned invalid video response: {result}")
        shutil.copyfile(v_path, out_path)
        return out_path

class LTXSpaceBackend(GenerationBackend):
    name = "ltx"
    description = "Free LTX-Video AI (Hugging Face ZeroGPU)"
    SPACE = "Lightricks/ltx-video-distilled"
    MAX_SECONDS = 8.0
    supports_image_conditioning = True

    def __init__(self, config: StudioConfig):
        self.config = config
        self._client: Any = None

    def _get_client(self) -> Any:
        from gradio_client import Client
        if self._client is None:
            token = self.config.hf_token.strip() or None
            try:
                self._client = Client(self.SPACE, hf_token=token, verbose=False)
            except Exception:
                self._client = Client(self.SPACE, verbose=False)
        return self._client

    @staticmethod
    def _snap32(v: int) -> int:
        return max((v // 32) * 32, 256)

    def generate(self, req: GenerationRequest, out_path: Path, progress: Callable[[str], None] = lambda m: None) -> Path:
        w, h = self._snap32(req.width), self._snap32(req.height)
        seconds = min(max(req.num_frames / max(req.fps, 1), 1.0), self.MAX_SECONDS)
        client = self._get_client()
        common = dict(
            prompt=req.prompt, negative_prompt=req.negative_prompt,
            input_video_filepath=None,
            height_ui=h, width_ui=w,
            duration_ui=round(seconds, 1), ui_frames_to_use=9,
            seed_ui=req.seed if req.seed >= 0 else 42,
            randomize_seed=(req.seed < 0),
            ui_guidance_scale=1.0,            # distilled model requires guidance 1.0
            improve_texture_flag=True,
        )

        def _call(api_name: str, **extra) -> Any:
            # Only send parameters the Space still accepts — protects the
            # primary free backend from breaking when Lightricks updates the
            # Space signature (missing keys just fall back to Space defaults).
            payload = {**common, **extra}
            accepted = self._accepted_params(client, api_name)
            if accepted:
                payload = {k: v for k, v in payload.items() if k in accepted}
            return client.predict(api_name=api_name, **payload)

        try:
            if req.init_image:
                from gradio_client import handle_file
                progress(f"LTX-Video: image-conditioned continuation {w}x{h}, {seconds:.1f}s...")
                result = _call("/image_to_video",
                               input_image_filepath=handle_file(req.init_image),
                               mode="image-to-video")
            else:
                progress(f"LTX-Video: text-to-video {w}x{h}, {seconds:.1f}s...")
                result = _call("/text_to_video",
                               input_image_filepath=None,
                               mode="text-to-video")
        except Exception as exc:
            err_str = str(exc)
            wait = quota_wait_seconds(err_str)
            if wait:
                raise QuotaExhausted(wait, "LTX ZeroGPU Quota Limit") from exc
            raise

        path = self._extract_video(result)
        if not path:
            raise RuntimeError(f"LTX returned no video: {str(result)[:200]}")
        shutil.copyfile(path, out_path)
        return out_path

class CogVideoXBackend(GenerationBackend):
    name = "cogvideox"
    description = "Free CogVideoX-5B AI (Hugging Face ZeroGPU)"
    SPACE = "THUDM/CogVideoX-5B-Space"

    def __init__(self, config: StudioConfig):
        self.config = config
        self._client: Any = None

    def _get_client(self) -> Any:
        from gradio_client import Client
        if self._client is None:
            token = self.config.hf_token.strip() or None
            try:
                self._client = Client(self.SPACE, hf_token=token, verbose=False)
            except Exception:
                self._client = Client(self.SPACE, verbose=False)
        return self._client

    def generate(self, req: GenerationRequest, out_path: Path, progress: Callable[[str], None] = lambda m: None) -> Path:
        progress("CogVideoX-5B: generating AI clip (720x480)...")
        client = self._get_client()
        try:
            result = client.predict(
                prompt=req.prompt, image_input=None, video_input=None,
                video_strength=0.8,
                seed_value=req.seed if req.seed >= 0 else -1,
                scale_status=False, rife_status=True,
                api_name="/generate",
            )
        except Exception as exc:
            err_str = str(exc)
            wait = quota_wait_seconds(err_str)
            if wait:
                raise QuotaExhausted(wait, "CogVideoX ZeroGPU Quota Limit") from exc
            raise

        path = self._extract_video(result)
        if not path:
            raise RuntimeError(f"CogVideoX returned no video: {str(result)[:200]}")
        shutil.copyfile(path, out_path)
        return out_path

class WanOfficialBackend(GenerationBackend):
    name = "wan_official"
    description = "Official Wan2.1-14B 720p (Public Async Queue)"
    SPACE = "Wan-AI/Wan2.1"
    POLL_SECONDS = 12
    MAX_WAIT = 2400

    def __init__(self, config: StudioConfig):
        self.config = config

    def generate(self, req: GenerationRequest, out_path: Path, progress: Callable[[str], None] = lambda m: None) -> Path:
        from gradio_client import Client
        progress("Wan2.1-14B Official: connecting to space...")
        token = self.config.hf_token.strip() or None
        try:
            client = Client(self.SPACE, hf_token=token, verbose=False)
        except Exception:
            client = Client(self.SPACE, verbose=False)

        size_choice = "1280*720" if req.width >= req.height else "720*1280"
        progress(f"Wan2.1-14B Official: submitting {size_choice} job...")
        # Map our fields onto whatever names the async endpoint currently
        # exposes, then filter — the public Wan Space has changed its signature
        # more than once, and an exact-kwarg call breaks the moment it does.
        want = {
            "prompt": req.prompt, "size_choice": size_choice, "size": size_choice,
            "guidance_scale": req.guidance, "sampling_steps": req.steps,
            "seed": req.seed if req.seed >= 0 else random.randint(0, 999999),
        }
        accepted = self._accepted_params(client, "/t2v_generation_async")
        payload = {k: v for k, v in want.items() if not accepted or k in accepted}
        sub_res = client.predict(api_name="/t2v_generation_async", **payload)
        task_id = sub_res[0] if isinstance(sub_res, (list, tuple)) else sub_res

        t0 = time.time()
        while time.time() - t0 < self.MAX_WAIT:
            time.sleep(self.POLL_SECONDS)
            try:
                stat = client.predict(task_id, api_name="/status_refresh")
            except Exception:
                # Older/newer Space builds poll with no argument on the same
                # session — fall back to that form instead of failing the job.
                stat = client.predict(api_name="/status_refresh")
            v_path = self._extract_video(stat)
            if v_path:
                shutil.copyfile(v_path, out_path)
                return out_path
            progress(f"Wan2.1-14B queue: waiting ({int(time.time() - t0)}s)...")

        raise TimeoutError("Wan2.1 Official Space queue timed out.")

class TestPatternBackend(GenerationBackend):
    name = "test_pattern"
    description = "Diagnostic Test Pattern (Offline Pipeline Verification)"

    def generate(self, req: GenerationRequest, out_path: Path, progress: Callable[[str], None] = lambda m: None) -> Path:
        progress("Generating test pattern verification clip...")
        duration = max(req.num_frames / req.fps, 2.0)
        vf = f"testsrc=duration={duration}:size={req.width}x{req.height}:rate={req.fps},format=yuv420p"
        run_ffmpeg(["-f", "lavfi", "-i", vf, "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path)])
        return out_path

# ----------------------------------------------------------------------------
# 5. Media & Stock Asset Fetcher
# ----------------------------------------------------------------------------

class MediaFetcher:
    """Intelligent Media & Stock Asset Fetcher (Pexels, Pixabay, Wikimedia, Pollinations Flux AI, Gemini)."""

    def __init__(self, config: Optional[StudioConfig] = None):
        self.config = config or StudioConfig()
        self.cache_dir = DEFAULT_OUTPUT_DIR / "media_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_scene_visual(
        self,
        prompt: str,
        target_width: int = 1920,
        target_height: int = 1080,
        prefer_video: bool = True,
        style: str = "Cinematic"
    ) -> Tuple[str, str]:
        """Fetches the best visual asset for a scene: returns (file_path, media_type: 'video'|'image')."""
        clean_q = re.sub(r"[^\w\s]", " ", prompt).strip()

        # 1. Try Pexels Video Search
        if prefer_video and self.config.pexels_api_key:
            try:
                vid = self._search_pexels_video(clean_q, target_width, target_height)
                if vid:
                    return vid, "video"
            except Exception as e:
                log.debug("Pexels video search fallback: %s", e)

        # 2. Try Pixabay Video Search
        if prefer_video and self.config.pixabay_api_key:
            try:
                vid = self._search_pixabay_video(clean_q)
                if vid:
                    return vid, "video"
            except Exception as e:
                log.debug("Pixabay video search fallback: %s", e)

        # 3. Try Pexels Photo Search
        if self.config.pexels_api_key:
            try:
                img = self._search_pexels_photo(clean_q, target_width, target_height)
                if img:
                    return img, "image"
            except Exception as e:
                log.debug("Pexels photo search fallback: %s", e)

        # 4. Try Wikimedia Commons Search
        try:
            wiki_img = self._search_wikimedia(clean_q)
            if wiki_img:
                return wiki_img, "image"
        except Exception as e:
            log.debug("Wikimedia search fallback: %s", e)

        # 5. Generate AI Image with Pollinations Flux (100% Free, High Fidelity)
        try:
            ai_img = self._generate_pollinations_image(prompt, target_width, target_height, style)
            if ai_img:
                return ai_img, "image"
        except Exception as e:
            log.debug("Pollinations AI image generation fallback: %s", e)

        # 6. Local Asset Fallback
        local_fallback = ASSETS_DIR / "test_diag_stock.jpg"
        if local_fallback.exists():
            return str(local_fallback), "image"

        # 7. Generate Synthetic Gradient Image
        synth_img = self._generate_synthetic_image(prompt, target_width, target_height)
        return synth_img, "image"

    def _search_pexels_video(self, query: str, width: int, height: int) -> Optional[str]:
        import requests
        headers = {"Authorization": self.config.pexels_api_key}
        orientation = "portrait" if height > width else "landscape"
        url = f"https://api.pexels.com/videos/search?query={query}&per_page=5&orientation={orientation}"
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            videos = data.get("videos", [])
            if videos:
                best_file = None
                for v in videos:
                    for vf in v.get("video_files", []):
                        if vf.get("quality") == "hd" or vf.get("width", 0) >= 1280:
                            best_file = vf.get("link")
                            break
                    if best_file:
                        break
                if not best_file and videos[0].get("video_files"):
                    best_file = videos[0]["video_files"][0].get("link")

                if best_file:
                    out = self.cache_dir / f"pexels_{uuid.uuid4().hex[:8]}.mp4"
                    r = requests.get(best_file, stream=True, timeout=20)
                    if r.status_code == 200:
                        with open(out, "wb") as f:
                            for chunk in r.iter_content(chunk_size=1024*1024):
                                f.write(chunk)
                        return str(out)
        return None

    def _search_pixabay_video(self, query: str) -> Optional[str]:
        import requests
        url = f"https://pixabay.com/api/videos/?key={self.config.pixabay_api_key}&q={query}&per_page=3"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            hits = resp.json().get("hits", [])
            if hits:
                vids = hits[0].get("videos", {})
                chosen = vids.get("large") or vids.get("medium") or vids.get("small")
                if chosen and chosen.get("url"):
                    out = self.cache_dir / f"pixabay_{uuid.uuid4().hex[:8]}.mp4"
                    r = requests.get(chosen["url"], stream=True, timeout=20)
                    if r.status_code == 200:
                        with open(out, "wb") as f:
                            for chunk in r.iter_content(chunk_size=1024*1024):
                                f.write(chunk)
                        return str(out)
        return None

    def _search_pexels_photo(self, query: str, width: int, height: int) -> Optional[str]:
        import requests
        headers = {"Authorization": self.config.pexels_api_key}
        orientation = "portrait" if height > width else "landscape"
        url = f"https://api.pexels.com/v1/search?query={query}&per_page=3&orientation={orientation}"
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            photos = resp.json().get("photos", [])
            if photos:
                src = photos[0].get("src", {}).get("large2x") or photos[0].get("src", {}).get("large")
                if src:
                    out = self.cache_dir / f"pexels_img_{uuid.uuid4().hex[:8]}.jpg"
                    r = requests.get(src, timeout=15)
                    if r.status_code == 200:
                        out.write_bytes(r.content)
                        return str(out)
        return None

    def _search_wikimedia(self, query: str) -> Optional[str]:
        import requests
        url = "https://commons.wikimedia.org/w/api.php"
        params = {
            "action": "query", "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
            "gsrlimit": 3, "prop": "imageinfo", "iiprop": "url|mime", "format": "json"
        }
        resp = requests.get(url, params=params, headers={"User-Agent": "VideoStudioPro/3.0"}, timeout=10)
        if resp.status_code == 200:
            pages = resp.json().get("query", {}).get("pages", {})
            for p in pages.values():
                info = p.get("imageinfo", [])
                if info and "url" in info[0]:
                    img_url = info[0]["url"]
                    if img_url.lower().endswith((".jpg", ".jpeg", ".png")):
                        out = self.cache_dir / f"wiki_{uuid.uuid4().hex[:8]}.jpg"
                        r = requests.get(img_url, headers={"User-Agent": "VideoStudioPro/3.0"}, timeout=15)
                        if r.status_code == 200:
                            out.write_bytes(r.content)
                            return str(out)
        return None

    def _generate_pollinations_image(self, prompt: str, width: int, height: int, style: str) -> Optional[str]:
        import urllib.parse
        import requests
        enhanced = PromptEngine().enhance(prompt, preset=style)
        encoded = urllib.parse.quote(enhanced[:300])
        # Snap dimensions to valid multiples
        w = (width // 64) * 64
        h = (height // 64) * 64
        url = f"https://image.pollinations.ai/prompt/{encoded}?width={w}&height={h}&model=flux&nologo=true&seed={random.randint(1,999999)}"
        out = self.cache_dir / f"flux_{uuid.uuid4().hex[:8]}.jpg"
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200 and len(resp.content) > 10000:
            out.write_bytes(resp.content)
            return str(out)
        return None

    def generate_nano_banana_frame(self, prompt: str, width: int = 1920, height: int = 1080, style: str = "Hyper-realistic") -> str:
        """Generates a stunning photorealistic 4K scene frame / visual pic without paid tokens or billing."""
        ai_img = self._generate_pollinations_image(prompt, width, height, style)
        if ai_img and Path(ai_img).exists():
            return ai_img
        return self._generate_synthetic_image(prompt, width, height)

    def _generate_synthetic_image(self, prompt: str, width: int, height: int) -> str:
        out = self.cache_dir / f"synth_{uuid.uuid4().hex[:8]}.jpg"
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (width, height), color=(15, 18, 30))
        draw = ImageDraw.Draw(img)
        draw.rectangle([(20, 20), (width - 20, height - 20)], outline=(99, 102, 241), width=4)
        draw.text((width // 2, height // 2), f"Scene: {prompt[:40]}", fill=(230, 232, 242), anchor="mm")
        img.save(out, quality=92)
        return str(out)

# ----------------------------------------------------------------------------
# 6. Multilingual Neural Audio Engine
# ----------------------------------------------------------------------------

TTS_VOICES: dict[str, dict[str, str]] = {
    "English": {
        "Male": "en-US-ChristopherNeural",
        "Female": "en-US-JennyNeural",
        "Neutral": "en-US-GuyNeural",
    },
    "Urdu": {
        "Female": "ur-PK-UzmaNeural",
        "Male": "ur-PK-AsadNeural",
        "Neutral": "ur-PK-UzmaNeural",
    },
    "Punjabi": {
        "Female": "pa-IN-GaganNeural",
        "Male": "pa-IN-OjasNeural",
        "Neutral": "pa-IN-GaganNeural",
    },
    "Hindi": {
        "Female": "hi-IN-SwaraNeural",
        "Male": "hi-IN-MadhurNeural",
        "Neutral": "hi-IN-SwaraNeural",
    },
    "Arabic": {
        "Female": "ar-SA-ZariyahNeural",
        "Male": "ar-SA-HamedNeural",
        "Neutral": "ar-SA-ZariyahNeural",
    },
    "Spanish": {
        "Female": "es-ES-ElviraNeural",
        "Male": "es-ES-AlvaroNeural",
        "Neutral": "es-ES-ElviraNeural",
    },
    "French": {
        "Female": "fr-FR-DeniseNeural",
        "Male": "fr-FR-HenriNeural",
        "Neutral": "fr-FR-DeniseNeural",
    },
    "German": {
        "Female": "de-DE-KatjaNeural",
        "Male": "de-DE-ConradNeural",
        "Neutral": "de-DE-KatjaNeural",
    },
    "Chinese": {
        "Female": "zh-CN-XiaoxiaoNeural",
        "Male": "zh-CN-YunjianNeural",
        "Neutral": "zh-CN-XiaoxiaoNeural",
    },
    "Japanese": {
        "Female": "ja-JP-NanamiNeural",
        "Male": "ja-JP-KeitaNeural",
        "Neutral": "ja-JP-NanamiNeural",
    },
}

MUSIC_MOODS = ["Ambient", "Uplifting", "Dramatic", "Calm", "Energetic", "None"]

class AudioEngine:
    """Multilingual Neural TTS, Background Music Synthesis, and Sidechain Auto-Ducking."""

    def __init__(self):
        self.temp_dir = DEFAULT_OUTPUT_DIR / "audio_temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def generate_voiceover(
        self,
        text: str,
        language: str = "English",
        gender: str = "Male",
        rate: str = "+0%",
        pitch: str = "+0Hz",
        out_path: Optional[Path] = None,
    ) -> Tuple[Path, List[dict]]:
        """Synthesizes speech using edge-tts and extracts word-boundary events."""
        out = out_path or (self.temp_dir / f"vo_{uuid.uuid4().hex[:8]}.mp3")
        lang_dict = TTS_VOICES.get(language, TTS_VOICES["English"])
        voice = lang_dict.get(gender, list(lang_dict.values())[0])

        events: List[dict] = []

        async def _synthesize():
            import edge_tts
            communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            submaker = edge_tts.SubMaker()
            with open(out, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        events.append({
                            "text": chunk.get("text", ""),
                            "offset": chunk.get("offset", 0) / 10_000_000,
                            "duration": chunk.get("duration", 0) / 10_000_000,
                        })

        try:
            asyncio.run(_synthesize())
        except Exception as exc:
            log.warning("edge-tts synthesis failed (%s); using gTTS fallback", exc)
            self._gtts_fallback(text, language, out)

        return out, events

    def _gtts_fallback(self, text: str, language: str, out_path: Path) -> None:
        try:
            from gtts import gTTS
            lang_codes = {"English": "en", "Urdu": "ur", "Hindi": "hi", "Spanish": "es", "French": "fr", "Arabic": "ar"}
            code = lang_codes.get(language, "en")
            tts = gTTS(text=text, lang=code)
            tts.save(str(out_path))
        except Exception as exc:
            log.error("gTTS fallback failed: %s", exc)
            # Create a 2s silent mp3 as last resort
            run_ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "2", "-c:a", "libmp3lame", str(out_path)])

    def generate_synth_music(self, mood: str, duration: float, out_path: Path) -> Path:
        """Generates rich harmonic synth ambient background music without external assets."""
        chords_by_mood = {
            "Ambient": [(220.0, 277.18, 329.63), (196.0, 246.94, 293.66)],      # A minor, G major
            "Uplifting": [(261.63, 329.63, 392.0), (349.23, 440.0, 523.25)],    # C major, F major
            "Dramatic": [(146.83, 174.61, 220.0), (130.81, 164.81, 196.0)],     # D minor, C minor
            "Calm": [(174.61, 220.0, 261.63), (220.0, 261.63, 329.63)],         # F major, A minor
            "Energetic": [(293.66, 369.99, 440.0), (329.63, 415.30, 493.88)],   # D major, E major
        }
        chords = chords_by_mood.get(mood, chords_by_mood["Ambient"])

        sample_rate = 44100
        total_samples = int(duration * sample_rate)
        wav_temp = self.temp_dir / f"synth_{uuid.uuid4().hex[:8]}.wav"

        chord_duration = 3.0
        with wave.open(str(wav_temp), "w") as wav_file:
            wav_file.setnchannels(2)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)

            frames = bytearray()
            for i in range(total_samples):
                t = i / sample_rate
                chord_idx = int(t / chord_duration) % len(chords)
                f1, f2, f3 = chords[chord_idx]

                # Harmonic synthesis with subtle LFO
                lfo = 0.85 + 0.15 * math.sin(2 * math.pi * 0.3 * t)
                val = (
                    0.40 * math.sin(2 * math.pi * f1 * t) +
                    0.30 * math.sin(2 * math.pi * f2 * t) +
                    0.20 * math.sin(2 * math.pi * f3 * t) +
                    0.10 * math.sin(2 * math.pi * (f1 * 2) * t)
                ) * lfo

                # Envelope fade in and fade out
                fade_in = min(t / 1.5, 1.0)
                fade_out = min((duration - t) / 2.0, 1.0)
                sample_val = int(val * fade_in * fade_out * 12000)
                sample_val = max(-32767, min(32767, sample_val))
                packed = struct.pack("<hh", sample_val, sample_val)
                frames.extend(packed)

            wav_file.writeframes(frames)

        run_ffmpeg(["-i", str(wav_temp), "-c:a", "libmp3lame", "-b:a", "192k", str(out_path)])
        wav_temp.unlink(missing_ok=True)
        return out_path

    def mix_and_duck_audio(
        self,
        voiceover_path: Optional[Path],
        music_mood: str,
        video_duration: float,
        out_path: Path,
        music_volume: float = 0.25,
    ) -> Path:
        """Mixes voiceover and background music with dynamic sidechain ducking."""
        has_vo = voiceover_path and voiceover_path.exists() and voiceover_path.stat().st_size > 100
        has_music = music_mood != "None"

        if not has_vo and not has_music:
            # Silent audio stream
            run_ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(video_duration), "-c:a", "aac", str(out_path)])
            return out_path

        # Locate or generate music file
        music_file: Optional[Path] = None
        if has_music:
            custom_music = MUSIC_DIR / f"{music_mood.lower()}.mp3"
            if custom_music.exists():
                music_file = custom_music
            else:
                synth_path = self.temp_dir / f"bgm_{mood_clean(music_mood)}_{uuid.uuid4().hex[:6]}.mp3"
                music_file = self.generate_synth_music(music_mood, video_duration + 2.0, synth_path)

        if has_vo and not has_music:
            # Voiceover only, pad to video duration if needed
            run_ffmpeg(["-i", str(voiceover_path), "-af", f"apad=whole_dur={video_duration}", "-c:a", "aac", str(out_path)])
            return out_path

        if has_music and not has_vo:
            # Music only
            run_ffmpeg([
                "-stream_loop", "-1", "-i", str(music_file),
                "-t", str(video_duration),
                "-af", f"volume={music_volume},afade=t=out:st={max(0.0, video_duration - 1.5)}:d=1.5",
                "-c:a", "aac", str(out_path)
            ])
            return out_path

        # Both Voiceover + Music -> Apply ffmpeg sidechain ducking filter
        filter_complex = (
            f"[1:a]aloop=loop=-1:size=2e+09,volume={music_volume}[bg];"
            f"[0:a]asplit=2[vo1][vo2];"
            f"[bg][vo1]sidechaincompress=threshold=0.08:ratio=6:attack=30:release=450[ducked];"
            f"[ducked][vo2]amix=inputs=2:duration=first:dropout_transition=2,"
            f"afade=t=out:st={max(0.0, video_duration - 1.2)}:d=1.2[outa]"
        )
        run_ffmpeg([
            "-i", str(voiceover_path),
            "-i", str(music_file),
            "-t", str(video_duration),
            "-filter_complex", filter_complex,
            "-map", "[outa]",
            "-c:a", "aac", "-b:a", "192k",
            str(out_path)
        ])
        return out_path

def mood_clean(m: str) -> str:
    return re.sub(r"[^\w]", "", m).lower()

# ----------------------------------------------------------------------------
# 7. Subtitles, Karaoke & Translation Engine
# ----------------------------------------------------------------------------

LANG_CODES: dict[str, str] = {
    "English": "en", "Urdu": "ur", "Punjabi": "pa", "Hindi": "hi",
    "Arabic": "ar", "Spanish": "es", "French": "fr", "German": "de",
    "Chinese": "zh-CN", "Japanese": "ja",
}

SUBTITLE_STYLES = {
    "Neon Glow": {
        "PrimaryColour": "&H0000FFFF",      # Bright Yellow / Cyan highlight
        "SecondaryColour": "&H00FFFFFF",
        "OutlineColour": "&H00FF00FF",      # Magenta Outline
        "BackColour": "&H80000000",
        "FontSize": "26",
        "Outline": "2",
        "Shadow": "3",
    },
    "Gold Luxury": {
        "PrimaryColour": "&H0000D7FF",      # Gold
        "SecondaryColour": "&H00FFFFFF",
        "OutlineColour": "&H00000000",
        "BackColour": "&HA0000000",
        "FontSize": "24",
        "Outline": "2",
        "Shadow": "2",
    },
    "Classic White": {
        "PrimaryColour": "&H00FFFFFF",
        "SecondaryColour": "&H00CCCCCC",
        "OutlineColour": "&H00000000",
        "BackColour": "&H80000000",
        "FontSize": "22",
        "Outline": "2",
        "Shadow": "1",
    },
}

class SubtitleEngine:
    """Karaoke Subtitles, Burnt-in Captions, and Translation."""

    @staticmethod
    def events_to_srt(events: List[dict], out_path: Path, max_words: int = 5) -> Path:
        """Converts word boundary events to an SRT subtitle file."""
        if not events:
            out_path.write_text("1\n00:00:00,000 --> 00:00:02,000\n \n", encoding="utf-8")
            return out_path

        def fmt_time(seconds: float) -> str:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds - int(seconds)) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        srt_lines = []
        chunk: List[dict] = []
        idx = 1

        for ev in events:
            chunk.append(ev)
            if len(chunk) >= max_words or ev["text"].endswith((".", "!", "?", "،", "۔")):
                start_t = chunk[0]["offset"]
                end_t = chunk[-1]["offset"] + chunk[-1]["duration"]
                txt = " ".join(e["text"] for e in chunk).strip()
                srt_lines.append(f"{idx}\n{fmt_time(start_t)} --> {fmt_time(end_t)}\n{txt}\n")
                idx += 1
                chunk = []

        if chunk:
            start_t = chunk[0]["offset"]
            end_t = chunk[-1]["offset"] + chunk[-1]["duration"]
            txt = " ".join(e["text"] for e in chunk).strip()
            srt_lines.append(f"{idx}\n{fmt_time(start_t)} --> {fmt_time(end_t)}\n{txt}\n")

        out_path.write_text("\n".join(srt_lines), encoding="utf-8")
        return out_path

    @staticmethod
    def translate_srt(srt_path: Path, target_lang: str, out_path: Path) -> Path:
        """Translates an SRT file into a target language."""
        code = LANG_CODES.get(target_lang)
        if not code or target_lang == "None":
            shutil.copyfile(srt_path, out_path)
            return out_path

        try:
            from deep_translator import GoogleTranslator
            translator = GoogleTranslator(source="auto", target=code)
            content = srt_path.read_text(encoding="utf-8")
            blocks = content.strip().split("\n\n")
            out_blocks = []

            for block in blocks:
                lines = block.split("\n")
                if len(lines) >= 3:
                    text_to_tr = " ".join(lines[2:])
                    translated = translator.translate(text_to_tr)
                    out_blocks.append(f"{lines[0]}\n{lines[1]}\n{translated}")
                else:
                    out_blocks.append(block)

            out_path.write_text("\n\n".join(out_blocks), encoding="utf-8")
            return out_path
        except Exception as exc:
            log.warning("Subtitle translation failed (%s); using original", exc)
            shutil.copyfile(srt_path, out_path)
            return out_path

    @staticmethod
    def burn_subtitles(
        video_path: Path,
        srt_path: Path,
        out_path: Path,
        style_name: str = "Neon Glow",
    ) -> Path:
        """Burns styled subtitles into video via ffmpeg."""
        style = SUBTITLE_STYLES.get(style_name, SUBTITLE_STYLES["Neon Glow"])
        escaped_srt = str(srt_path).replace("\\", "/").replace(":", "\\:")
        force_style = (
            f"Fontsize={style['FontSize']},PrimaryColour={style['PrimaryColour']},"
            f"OutlineColour={style['OutlineColour']},BackColour={style['BackColour']},"
            f"Outline={style['Outline']},Shadow={style['Shadow']},MarginV=28,Alignment=2"
        )
        vf = f"subtitles='{escaped_srt}':force_style='{force_style}'"
        run_ffmpeg(["-i", str(video_path), "-vf", vf, "-c:a", "copy", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path)])
        return out_path

# ----------------------------------------------------------------------------
# 8. Video FX, Motion & Compositing Engine
# ----------------------------------------------------------------------------

class VideoEngine:
    """Ken Burns 3D motion, transitions, motion interpolation, upscaling, and watermarking."""

    @staticmethod
    def image_to_video_ken_burns(
        image_path: Path,
        duration: float,
        width: int,
        height: int,
        fps: int = 30,
        motion_type: str = "zoom_in",
        out_path: Optional[Path] = None,
    ) -> Path:
        """Applies dynamic Ken Burns 3D pan/zoom on static images using ffmpeg zoompan."""
        out = out_path or (DEFAULT_OUTPUT_DIR / f"kb_{uuid.uuid4().hex[:8]}.mp4")
        total_frames = int(duration * fps)

        if motion_type == "zoom_in":
            zp = f"zoompan=z='min(zoom+0.0015,1.25)':d={total_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
        elif motion_type == "zoom_out":
            zp = f"zoompan=z='if(lte(zoom,1.0),1.25,max(1.0,zoom-0.0015))':d={total_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
        elif motion_type == "pan_left":
            zp = f"zoompan=z='1.15':d={total_frames}:x='if(lte(on,1),(iw-iw/zoom),max(0,x-1.5))':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
        else: # pan_right
            zp = f"zoompan=z='1.15':d={total_frames}:x='if(lte(on,1),0,min(iw-iw/zoom,x+1.5))':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"

        vf = f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase,crop={width*2}:{height*2},{zp},format=yuv420p"
        run_ffmpeg(["-loop", "1", "-i", str(image_path), "-vf", vf, "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)])
        return out

    @staticmethod
    def crossfade_concat(video_clips: List[Path], out_path: Path, crossfade: float = CROSSFADE_SECONDS) -> Path:
        """Smoothly concatenates video clips with crossfade transitions and normalized timebases."""
        if not video_clips:
            raise ValueError("No video clips provided for concatenation.")
        if len(video_clips) == 1:
            shutil.copyfile(video_clips[0], out_path)
            return out_path

        inputs = []
        filter_parts = []
        for i, v in enumerate(video_clips):
            inputs.extend(["-i", str(v)])
            # Normalize each input stream with fixed 30 FPS and standard timebase
            filter_parts.append(f"[{i}:v]fps=30,settb=AVTB,format=yuv420p[v{i}]")

        cur_label = "v0"
        offset = 0.0

        for i in range(1, len(video_clips)):
            dur_prev = ffprobe_duration(video_clips[i - 1])
            offset += max(0.1, dur_prev - crossfade)
            next_input = f"v{i}"
            out_label = f"xf{i}" if i < len(video_clips) - 1 else "outv"
            filter_parts.append(f"[{cur_label}][{next_input}]xfade=transition=fade:duration={crossfade}:offset={offset:.2f}[{out_label}]")
            cur_label = out_label

        filter_str = ";".join(filter_parts)
        try:
            run_ffmpeg([*inputs, "-filter_complex", filter_str, "-map", "[outv]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path)])
        except Exception as e:
            log.warning("xfade transition failed (%s), falling back to standard concat...", e)
            # Safe robust concat fallback
            concat_parts = [f"[{i}:v]fps=30,settb=AVTB,format=yuv420p[cv{i}]" for i in range(len(video_clips))]
            join_str = "".join(f"[cv{i}]" for i in range(len(video_clips))) + f"concat=n={len(video_clips)}:v=1:a=0[outv]"
            filter_fallback = ";".join(concat_parts) + ";" + join_str
            run_ffmpeg([*inputs, "-filter_complex", filter_fallback, "-map", "[outv]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path)])

        return out_path

    @staticmethod
    def freeze_extend(video_path: Path, target_dur: float, out_path: Path) -> Path:
        """Hold the last frame so a clip reaches target_dur (used when narration
        is longer than the footage). Copies through if already long enough."""
        cur = ffprobe_duration(video_path)
        if target_dur <= cur + 0.12:
            shutil.copyfile(video_path, out_path)
            return out_path
        pad = target_dur - cur
        run_ffmpeg([
            "-i", str(video_path),
            "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.2f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", str(out_path),
        ])
        return out_path

    @staticmethod
    def interpolate_motion_60fps(video_path: Path, out_path: Path) -> Path:
        """Silky smooth 60fps motion interpolation via ffmpeg minterpolate."""
        vf = "minterpolate='fps=60:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1'"
        run_ffmpeg(["-i", str(video_path), "-vf", vf, "-c:a", "copy", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path)])
        return out_path

    @staticmethod
    def upscale_video(video_path: Path, target_quality: str, out_path: Path) -> Path:
        """Upscales video to 720p / 1080p / 4K using Lanczos high-detail algorithm."""
        res_map = {
            "720p": "scale=1280:720:flags=lanczos",
            "1080p": "scale=1920:1080:flags=lanczos",
            "4K": "scale=3840:2160:flags=lanczos",
        }
        scale_filter = res_map.get(target_quality)
        if not scale_filter:
            shutil.copyfile(video_path, out_path)
            return out_path

        vf = f"{scale_filter},unsharp=5:5:0.8:5:5:0.0"
        run_ffmpeg(["-i", str(video_path), "-vf", vf, "-c:a", "copy", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path)])
        return out_path

    @staticmethod
    def apply_watermark(video_path: Path, watermark_path: Path, out_path: Path, position: str = "bottom_right") -> Path:
        """Applies a branded watermark / logo overlay."""
        pos_map = {
            "bottom_right": "main_w-overlay_w-20:main_h-overlay_h-20",
            "top_right": "main_w-overlay_w-20:20",
            "top_left": "20:20",
            "bottom_left": "20:main_h-overlay_h-20",
        }
        overlay_pos = pos_map.get(position, pos_map["bottom_right"])
        filter_complex = f"[1:v]scale=120:-1,format=rgba,colorchannelmixer=aa=0.75[wm];[0:v][wm]overlay={overlay_pos}[outv]"
        run_ffmpeg(["-i", str(video_path), "-i", str(watermark_path), "-filter_complex", filter_complex, "-map", "[outv]", "-c:a", "copy", "-c:v", "libx264", str(out_path)])
        return out_path

    @staticmethod
    def anti_fingerprint_filter(video_path: Path, out_path: Path) -> Path:
        """Applies subtle anti-fingerprint filter for social media uniqueness."""
        vf = "eq=contrast=1.01:brightness=0.005:saturation=1.02,scale=trunc(iw*1.002/2)*2:trunc(ih*1.002/2)*2"
        run_ffmpeg(["-i", str(video_path), "-vf", vf, "-map_metadata", "-1", "-c:a", "copy", "-c:v", "libx264", str(out_path)])
        return out_path

# ----------------------------------------------------------------------------
# 9. Pipeline Orchestrator & Job Queue
# ----------------------------------------------------------------------------

class Stage(str, Enum):
    QUEUED = "Queued"
    ENHANCING = "Enhancing Prompt"
    GENERATING = "Generating Video"
    STITCHING = "Stitching Scenes"
    INTERPOLATING = "Interpolating 60FPS"
    UPSCALING = "Upscaling Resolution"
    AUDIO = "Synthesizing Audio"
    SUBTITLES = "Styling Subtitles"
    DONE = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"

    @classmethod
    def from_str(cls, val: Any) -> "Stage":
        val_clean = str(val).strip().lower()
        for member in cls:
            if member.value.lower() == val_clean or member.name.lower() == val_clean:
                return member
        if val_clean in ("done", "completed", "finish", "finished"):
            return cls.DONE
        return cls.QUEUED

@dataclass
class JobSettings:
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    mode: str = "ai_video"                     # 'ai_video' or 'script_story'
    prompt: str = ""
    script_text: str = ""
    negative_prompt: str = ""
    style_preset: str = "Cinematic"
    quality: str = "1080p"
    aspect_ratio: str = "16:9"
    duration: float = 10.0
    fps: int = 30
    language: str = "English"
    voice_gender: str = "Male"
    voice_script: str = ""
    music_mood: str = "Ambient"
    subtitles_enabled: bool = True
    subtitle_style: str = "Neon Glow"
    translate_lang: str = "None"
    interpolate_60fps: bool = True
    anti_fingerprint: bool = True
    watermark_logo: bool = False
    seed: int = -1
    created_at: float = field(default_factory=time.time)

@dataclass
class JobStatus:
    job_id: str
    stage: Stage = Stage.QUEUED
    progress: float = 0.0                      # 0.0 to 1.0
    message: str = "Queued..."
    video_path: Optional[str] = None
    srt_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    error: Optional[str] = None
    backend_used: Optional[str] = None
    settings: Optional[JobSettings] = None
    retries: int = 0                           # non-quota failures so far
    not_before: float = 0.0                    # deferred-until epoch (quota/network)

class JobQueue:
    """Thread-safe, persistent batch job queue with auto-resume and crash recovery."""

    def __init__(self, studio: "VideoStudio"):
        self.studio = studio
        self.queue_file = DEFAULT_OUTPUT_DIR / "queue" / "jobs.json"
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()          # re-entrant: _save() is called
        # from inside methods that already hold the lock
        self._jobs: Dict[str, JobStatus] = {}
        self._cancelled_jobs: set[str] = set()
        self._active_thread: Optional[threading.Thread] = None
        self._running = False
        self._paused = False
        self._last_persist = 0.0
        self._lock_path = self.queue_file.parent / "worker.lock"
        self._load()
        # Single-worker guard: if another live studio process already owns the
        # queue, this instance is VIEW-ONLY and must not run a second worker —
        # otherwise both processes would generate the same job (double GPU spend,
        # file races on the same job dir).
        self.is_worker = self._acquire_worker_lock()
        if not self.is_worker:
            log.warning("Another studio instance owns the queue — this one is VIEW-ONLY.")
        # Auto-resume any unfinished jobs from prior laptop/browser sessions
        self._auto_resume_interrupted_jobs()

    # -- single-worker lock --------------------------------------------------

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if sys.platform == "win32":
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _acquire_worker_lock(self) -> bool:
        try:
            if self._lock_path.exists():
                owner = int(self._lock_path.read_text().strip() or 0)
                if owner != os.getpid() and self._pid_alive(owner):
                    return False
                try:
                    self._lock_path.unlink()          # stale lock (dead owner)
                except FileNotFoundError:
                    pass
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("utf-8"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            return False
        except Exception as exc:
            log.warning("Worker lock error (%s) — assuming ownership", exc)
            return True

    def shutdown(self) -> None:
        self._running = False
        try:
            if (self.is_worker and self._lock_path.exists()
                    and self._lock_path.read_text().strip() == str(os.getpid())):
                self._lock_path.unlink()
        except Exception:
            pass

    def _load(self):
        if self.queue_file.exists():
            try:
                data = json.loads(self.queue_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    data = {j.get("job_id", str(i)): j for i, j in enumerate(data) if isinstance(j, dict)}
                elif not isinstance(data, dict):
                    data = {}
                known_fields = {f.name for f in dataclasses.fields(JobSettings)}
                for j_id, j_data in data.items():
                    sett_data = j_data.get("settings", {})
                    if isinstance(sett_data, dict):
                        filtered_sett = {k: v for k, v in sett_data.items() if k in known_fields}
                        sett = JobSettings(**filtered_sett) if filtered_sett else None
                    else:
                        sett = None
                    stage = Stage.from_str(j_data.get("stage", "Queued"))
                    if stage == Stage.CANCELLED:
                        self._cancelled_jobs.add(j_id)
                    self._jobs[j_id] = JobStatus(
                        job_id=j_id,
                        stage=stage,
                        progress=j_data.get("progress", 0.0),
                        message=j_data.get("message", ""),
                        video_path=j_data.get("video_path"),
                        srt_path=j_data.get("srt_path"),
                        thumbnail_path=j_data.get("thumbnail_path"),
                        error=j_data.get("error"),
                        backend_used=j_data.get("backend_used"),
                        settings=sett,
                        retries=int(j_data.get("retries", 0) or 0),
                        not_before=float(j_data.get("not_before", 0.0) or 0.0),
                    )
            except Exception as e:
                log.warning("Failed to load persistent queue: %s", e)

    def _auto_resume_interrupted_jobs(self):
        """Recover and continue any job interrupted by a shutdown/crash/browser
        close. Only the worker-owning instance resumes; failed jobs stay failed
        (a fresh retry is an explicit user action via the Retry/Resume button)."""
        has_pending = False
        with self._lock:
            for j_id, js in self._jobs.items():
                if js.stage not in (Stage.DONE, Stage.CANCELLED, Stage.FAILED):
                    log.info("Found unfinished job [%s] at stage '%s'. Auto-resuming...", j_id, js.stage.value)
                    js.stage = Stage.QUEUED
                    js.not_before = 0.0
                    js.retries = 0
                    js.message = "Restored from previous session. Resuming from last saved checkpoint..."
                    has_pending = True
        if has_pending:
            self._save(force=True)
            self._ensure_worker()

    def _save(self, force: bool = False):
        """Persist queue state atomically. Throttled to avoid hammering the disk
        (and a cloud-synced folder) on every sub-progress tick."""
        now = time.time()
        if not force and (now - self._last_persist) < 2.0:
            return
        with self._lock:
            self._last_persist = now
            data = {}
            for j_id, js in self._jobs.items():
                data[j_id] = {
                    "stage": js.stage.value,
                    "progress": js.progress,
                    "message": js.message,
                    "video_path": js.video_path,
                    "srt_path": js.srt_path,
                    "thumbnail_path": js.thumbnail_path,
                    "error": js.error,
                    "backend_used": js.backend_used,
                    "settings": dataclasses.asdict(js.settings) if js.settings else None,
                    "retries": js.retries,
                    "not_before": js.not_before,
                }
            payload = json.dumps(data, indent=2)
        tmp = self.queue_file.with_suffix(f".{os.getpid()}.tmp")
        for attempt in range(5):
            try:
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(self.queue_file)      # atomic
                return
            except (PermissionError, OSError) as exc:
                time.sleep(0.2 * (attempt + 1))
                last = exc
        log.warning("Queue persist failed after retries: %s", last)

    def submit(self, settings: JobSettings) -> str:
        status = JobStatus(job_id=settings.job_id, settings=settings)
        with self._lock:
            if settings.job_id in self._cancelled_jobs:
                self._cancelled_jobs.remove(settings.job_id)
            self._jobs[settings.job_id] = status
        self._save(force=True)
        self._ensure_worker()
        return settings.job_id

    def cancel(self, job_id: str) -> bool:
        """Explicit user termination of a job."""
        with self._lock:
            self._cancelled_jobs.add(job_id)
            js = self._jobs.get(job_id)
            if js:
                js.stage = Stage.CANCELLED
                js.message = "🛑 Job terminated by user."
                self._save(force=True)
                log.info("Job [%s] was explicitly terminated by user.", job_id)
                return True
        return False

    def is_cancelled(self, job_id: str) -> bool:
        if job_id in self._cancelled_jobs:
            return True
        js = self._jobs.get(job_id)
        return js is not None and js.stage == Stage.CANCELLED

    def retry(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._cancelled_jobs:
                self._cancelled_jobs.remove(job_id)
            js = self._jobs.get(job_id)
            if js:
                js.stage = Stage.QUEUED
                js.retries = 0
                js.not_before = 0.0
                js.error = None
                js.message = "Re-queued. Resuming from checkpoint..."
                self._save(force=True)
                self._ensure_worker()
                return True
        return False

    def get_status(self, job_id: str) -> Optional[JobStatus]:
        return self._jobs.get(job_id)

    def all_jobs(self) -> List[JobStatus]:
        return list(self._jobs.values())

    def _ensure_worker(self):
        # Only the lock-owning instance runs the worker thread.
        if not getattr(self, "is_worker", True):
            return
        if self._active_thread is None or not self._active_thread.is_alive():
            self._running = True
            self._active_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._active_thread.start()

    def _worker_loop(self):
        while self._running:
            if self._paused:
                time.sleep(1.0)
                continue

            now = time.time()
            pending_job: Optional[JobStatus] = None
            with self._lock:
                for js in self._jobs.values():
                    # Skip cancelled, already-terminal, and jobs deferred to the
                    # future — a deferred job must NOT block the ones behind it.
                    if (js.stage == Stage.QUEUED
                            and js.job_id not in self._cancelled_jobs
                            and js.not_before <= now):
                        pending_job = js
                        break

            if not pending_job:
                time.sleep(1.0)
                continue

            try:
                self.studio.execute_job(pending_job, self._progress_callback)
            except QuotaExhausted as quota:
                # Free-GPU quota / transient network — defer and auto-resume when
                # the window replenishes. Finished chunks stay on disk, so the
                # resume is cheap. This does NOT count as a hard failure.
                pending_job.stage = Stage.QUEUED
                pending_job.not_before = time.time() + max(30, int(quota.wait_seconds))
                at = time.strftime("%H:%M", time.localtime(pending_job.not_before))
                pending_job.message = f"⏳ {quota.reason} — auto-retry at {at} (checkpoints saved)"
                pending_job.error = None
                self._save(force=True)
            except Exception as e:
                if self.is_cancelled(pending_job.job_id):
                    log.info("Job [%s] stopped — cancelled by user.", pending_job.job_id)
                    continue

                pending_job.retries += 1
                cap = int(getattr(self.studio.config, "max_job_retries", 3))
                if pending_job.retries > cap:
                    # Hard cap reached — mark Failed so the queue moves on instead
                    # of looping this job forever and starving everything behind it.
                    pending_job.stage = Stage.FAILED
                    pending_job.error = str(e)[:500]
                    pending_job.message = f"❌ Failed after {cap} attempts: {str(e)[:80]}"
                    log.error("Job %s permanently failed after %d attempts: %s", pending_job.job_id, cap, e)
                    self._save(force=True)
                    continue

                log.warning("Job %s attempt %d/%d failed: %s (retrying with checkpoints)",
                            pending_job.job_id, pending_job.retries, cap, e)
                pending_job.stage = Stage.QUEUED
                pending_job.error = str(e)
                pending_job.not_before = time.time() + 10  # brief backoff, unblocks queue
                pending_job.message = f"⏳ Retry {pending_job.retries}/{cap}: {str(e)[:60]}"
                self._save(force=True)

    def _progress_callback(self, job_id: str, stage: Stage, progress: float, msg: str, **kwargs):
        if self.is_cancelled(job_id):
            return
        js = self._jobs.get(job_id)
        if js:
            stage_changed = js.stage != stage
            js.stage = stage
            js.progress = progress
            js.message = msg
            for k, v in kwargs.items():
                if hasattr(js, k):
                    setattr(js, k, v)
            # In-memory status is always current (the UI reads it live); only
            # force a disk write on stage changes / completion. Sub-progress
            # ticks are throttled inside _save() to spare the disk.
            self._save(force=stage_changed or stage in (Stage.DONE, Stage.FAILED, Stage.CANCELLED))

class VideoStudio:
    """Master Unified VideoStudio Suite."""

    def __init__(self, config: Optional[StudioConfig] = None):
        self.config = config or StudioConfig.load()
        self.probe = HardwareProbe()
        self.prompt_engine = PromptEngine()
        self.audio_engine = AudioEngine()
        self.media_fetcher = MediaFetcher(self.config)
        self.video_engine = VideoEngine()
        self.subtitle_engine = SubtitleEngine()

        # Initialize backends BEFORE the queue — the queue auto-resumes
        # interrupted jobs on construction and immediately needs self.backends.
        self.backends: Dict[str, GenerationBackend] = {
            "colab": ColabBackend(self.config),
            "ltx": LTXSpaceBackend(self.config),
            "cogvideox": CogVideoXBackend(self.config),
            "wan_official": WanOfficialBackend(self.config),
            "test_pattern": TestPatternBackend(),
        }
        self._prune_outputs()
        self.queue = JobQueue(self)

    def _prune_outputs(self) -> None:
        """Keep only the newest `max_videos_kept` finished videos (and their job
        dirs) so a long-running studio never silently fills the disk."""
        keep = max(int(getattr(self.config, "max_videos_kept", 60)), 1)
        videos_dir = DEFAULT_OUTPUT_DIR / "videos"
        jobs_dir = DEFAULT_OUTPUT_DIR / "jobs"
        try:
            mp4s = sorted(videos_dir.glob("*.mp4"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception as exc:
            log.warning("Prune scan failed: %s", exc)
            return
        removed = 0
        for old in mp4s[keep:]:
            job_id = old.stem.replace("video_", "")
            try:
                old.unlink()
                removed += 1
            except Exception:
                continue
            jdir = jobs_dir / job_id
            if jdir.is_dir():
                shutil.rmtree(jdir, ignore_errors=True)
        if removed:
            log.info("Pruned %d old video(s), keeping newest %d", removed, keep)

    def execute_job(self, status: JobStatus, progress_cb: Callable):
        sett = status.settings
        if not sett:
            return

        job_dir = DEFAULT_OUTPUT_DIR / "jobs" / sett.job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        final_video_out = DEFAULT_OUTPUT_DIR / "videos" / f"video_{sett.job_id}.mp4"
        final_video_out.parent.mkdir(parents=True, exist_ok=True)

        log.info("Starting Job [%s] Mode: %s", sett.job_id, sett.mode)

        if sett.mode == "script_story":
            self._execute_script_mode(sett, job_dir, final_video_out, progress_cb)
        else:
            self._execute_ai_diffusion_mode(sett, job_dir, final_video_out, progress_cb)

    def _execute_ai_diffusion_mode(self, sett: JobSettings, job_dir: Path, out_mp4: Path, progress_cb: Callable):
        if self.queue.is_cancelled(sett.job_id):
            return

        # 1. Prompt Enhancement
        progress_cb(sett.job_id, Stage.ENHANCING, 0.10, "Enhancing prompt with cinematic AI...")
        enhanced_prompt = FreeLLMPromptEnhancer.expand_with_ai(sett.prompt, sett.style_preset, self.config)
        neg_prompt = PromptEngine.negative(sett.negative_prompt)

        if self.queue.is_cancelled(sett.job_id):
            return

        # 2. Chunk Calculation & Generation (with checkpoint reuse)
        w, h = ASPECT_SIZES.get(sett.aspect_ratio, (832, 480))
        total_chunks = max(1, int(math.ceil(sett.duration / CHUNK_SECONDS)))
        clips: List[Path] = []
        last_frame_path: Optional[str] = None
        backend_used = "unknown"
        # Generate at the models' native 16 fps and derive frame COUNT from the
        # requested seconds — the old code left num_frames at the 81 default so
        # every clip came out a fixed (often wrong) length regardless of the
        # duration slider. Interpolation to 60 fps happens later in post.
        gen_fps = WAN_NATIVE_FPS
        base_seed = sett.seed if sett.seed >= 0 else random.randint(1, 999999)

        for idx in range(total_chunks):
            if self.queue.is_cancelled(sett.job_id):
                log.info("Job [%s] cancelled by user.", sett.job_id)
                return

            clip_out = job_dir / f"chunk_{idx:02d}.mp4"
            last_frame = job_dir / f"frame_{idx:02d}.jpg"

            # Checkpoint recovery: If clip already exists from previous session, reuse it
            if clip_out.exists() and ffprobe_duration(clip_out) > 0.5:
                log.info("Checkpoint: Reusing existing clip %s (no re-generation needed)", clip_out.name)
                clips.append(clip_out)
                if last_frame.exists():
                    last_frame_path = str(last_frame)
                continue

            # Seconds for THIS chunk; non-final overlapping chunks get the
            # crossfade back so the stitched result matches the requested length.
            chunk_secs = min(CHUNK_SECONDS, sett.duration - idx * CHUNK_SECONDS)
            if total_chunks > 1 and idx < total_chunks - 1:
                chunk_secs = min(chunk_secs + CROSSFADE_SECONDS, CHUNK_SECONDS + CROSSFADE_SECONDS)
            chunk_secs = max(chunk_secs, 1.0)

            chunk_prog = 0.20 + 0.40 * (idx / total_chunks)
            progress_cb(sett.job_id, Stage.GENERATING, chunk_prog, f"Generating AI clip {idx+1}/{total_chunks} ({chunk_secs:.1f}s)...")

            req = GenerationRequest(
                prompt=enhanced_prompt,
                negative_prompt=neg_prompt,
                width=w, height=h,
                num_frames=frames_for(chunk_secs, gen_fps),
                steps=self.config.default_steps,
                guidance=self.config.default_guidance,
                seed=base_seed + idx,
                fps=gen_fps,
                init_image=last_frame_path,
            )

            success = False
            quota_hint = 0
            transient = False
            for b_name in self.config.backend_order:
                if self.queue.is_cancelled(sett.job_id):
                    return
                # The test pattern is diagnostic only — never let it pre-empt the
                # far better stock-footage / Ken Burns fallback below.
                if b_name == "test_pattern" and not self.config.allow_test_pattern_fallback:
                    continue
                be = self.backends.get(b_name)
                if not be or not be.available():
                    continue
                try:
                    be.generate(req, clip_out)
                    backend_used = be.name
                    success = True
                    break
                except QuotaExhausted as q:
                    quota_hint = max(quota_hint, int(q.wait_seconds))
                    log.warning("Backend %s quota-limited: %s", b_name, q)
                except Exception as e:
                    msg = str(e)
                    w = quota_wait_seconds(msg)
                    if w:
                        quota_hint = max(quota_hint, w)
                    elif is_transient_error(msg):
                        transient = True
                    log.warning("Backend %s failed: %s", b_name, e)

            # Quota/network is temporary — defer the whole job and auto-resume so
            # we wait for REAL AI capacity instead of silently downgrading to
            # stock footage. Finished chunks are kept on disk (checkpoint reuse),
            # so the resume costs no extra GPU. Only a genuine backend outage
            # (below) falls through to the cinematic-synthesis fallback.
            if not success and (quota_hint or transient):
                raise QuotaExhausted(
                    min(quota_hint or 300, self.config.quota_wait_cap_min * 60),
                    reason="Free GPU quota busy" if quota_hint else "Network unavailable",
                )

            if not success:
                # Intelligent visual asset & Ken Burns 3D motion synthesis fallback
                log.info("Engaging cinematic visual synthesis fallback for chunk %d...", idx)
                chunk_dur = min(float(CHUNK_SECONDS), 5.0)
                asset_path, media_type = self.media_fetcher.fetch_scene_visual(
                    req.prompt, req.width, req.height, prefer_video=True, style=sett.style_preset
                )
                if media_type == "image":
                    motions = ["zoom_in", "zoom_out", "pan_left", "pan_right"]
                    mot = motions[idx % len(motions)]
                    self.video_engine.image_to_video_ken_burns(Path(asset_path), chunk_dur, req.width, req.height, fps=req.fps, motion_type=mot, out_path=clip_out)
                else:
                    run_ffmpeg([
                        "-stream_loop", "-1", "-i", asset_path,
                        "-t", str(chunk_dur),
                        "-vf", f"scale={req.width}:{req.height}:force_original_aspect_ratio=increase,crop={req.width}:{req.height}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip_out)
                    ])
                backend_used = "cinematic_synthesis"
                success = True

            clips.append(clip_out)

            # Extract last frame for continuous image-to-video chaining
            run_ffmpeg(["-sseof", "-0.1", "-i", str(clip_out), "-vsync", "0", "-q:v", "2", "-update", "1", str(last_frame)])
            if last_frame.exists():
                last_frame_path = str(last_frame)

        if self.queue.is_cancelled(sett.job_id):
            return

        # 3. Stitch Scenes
        progress_cb(sett.job_id, Stage.STITCHING, 0.65, "Stitching clips with smooth crossfades...")
        raw_stitched = job_dir / "stitched_raw.mp4"
        if not (raw_stitched.exists() and ffprobe_duration(raw_stitched) > 0.5):
            self.video_engine.crossfade_concat(clips, raw_stitched)

        # 4. Motion Interpolation
        current_vid = raw_stitched
        if sett.interpolate_60fps:
            progress_cb(sett.job_id, Stage.INTERPOLATING, 0.72, "Applying silky smooth 60fps motion...")
            interpolated = job_dir / "interpolated.mp4"
            if not (interpolated.exists() and ffprobe_duration(interpolated) > 0.5):
                try:
                    self.video_engine.interpolate_motion_60fps(current_vid, interpolated)
                    current_vid = interpolated
                except Exception as e:
                    log.warning("Interpolation skipped: %s", e)
            else:
                current_vid = interpolated

        # 5. Upscale Resolution
        if sett.quality in ("720p", "1080p", "4K"):
            progress_cb(sett.job_id, Stage.UPSCALING, 0.80, f"Upscaling to {sett.quality}...")
            upscaled = job_dir / f"upscaled_{sett.quality}.mp4"
            if not (upscaled.exists() and ffprobe_duration(upscaled) > 0.5):
                self.video_engine.upscale_video(current_vid, sett.quality, upscaled)
                current_vid = upscaled
            else:
                current_vid = upscaled

        if self.queue.is_cancelled(sett.job_id):
            return

        # 6. Audio Narration & Music Ducking
        progress_cb(sett.job_id, Stage.AUDIO, 0.88, "Synthesizing voiceover & mixing music...")
        vo_text = sett.voice_script.strip() or sett.prompt.strip()
        vo_file, events = self.audio_engine.generate_voiceover(vo_text, sett.language, sett.voice_gender)
        video_dur = ffprobe_duration(current_vid)
        vo_dur = ffprobe_duration(vo_file) if vo_file and Path(vo_file).exists() else 0.0
        # If the narration runs longer than the footage, hold the last frame so
        # nothing gets cut off (instead of muxing a long audio track onto a short
        # video and leaving them mismatched).
        if vo_dur > video_dur + 0.3:
            extended = job_dir / "extended.mp4"
            self.video_engine.freeze_extend(current_vid, vo_dur + 0.4, extended)
            current_vid = extended
            video_dur = ffprobe_duration(current_vid)
        mixed_audio = job_dir / "mixed_audio.aac"
        self.audio_engine.mix_and_duck_audio(vo_file, sett.music_mood, video_dur, mixed_audio)

        # 7. Subtitles
        srt_file = job_dir / "subtitles.srt"
        self.subtitle_engine.events_to_srt(events, srt_file)
        if sett.translate_lang != "None":
            tr_srt = job_dir / f"subtitles_{sett.translate_lang}.srt"
            self.subtitle_engine.translate_srt(srt_file, sett.translate_lang, tr_srt)
            srt_file = tr_srt

        # Mux Audio with Video (-shortest keeps A/V exactly aligned)
        with_audio = job_dir / "with_audio.mp4"
        run_ffmpeg(["-i", str(current_vid), "-i", str(mixed_audio), "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-shortest", str(with_audio)])
        current_vid = with_audio

        # Burn Subtitles if enabled
        if sett.subtitles_enabled:
            progress_cb(sett.job_id, Stage.SUBTITLES, 0.94, "Burning styled subtitles...")
            burned = job_dir / "with_subs.mp4"
            try:
                self.subtitle_engine.burn_subtitles(current_vid, srt_file, burned, sett.subtitle_style)
                current_vid = burned
            except Exception as e:
                log.warning("Subtitle burning fallback: %s", e)

        # Watermark
        if sett.watermark_logo:
            logo = ASSETS_DIR / "kaami_makes_logo.png"
            if logo.exists():
                wm_vid = job_dir / "watermarked.mp4"
                self.video_engine.apply_watermark(current_vid, logo, wm_vid)
                current_vid = wm_vid

        # Anti-fingerprint final render
        if sett.anti_fingerprint:
            self.video_engine.anti_fingerprint_filter(current_vid, out_mp4)
        else:
            shutil.copyfile(current_vid, out_mp4)

        # Generate Thumbnail
        thumb = job_dir / "thumb.jpg"
        run_ffmpeg(["-ss", "0.5", "-i", str(out_mp4), "-vframes", "1", "-q:v", "2", str(thumb)])

        progress_cb(
            sett.job_id, Stage.DONE, 1.0, "Video generated successfully!",
            video_path=str(out_mp4),
            srt_path=str(srt_file),
            thumbnail_path=str(thumb),
            backend_used=backend_used,
        )

    def _execute_script_mode(self, sett: JobSettings, job_dir: Path, out_mp4: Path, progress_cb: Callable):
        if self.queue.is_cancelled(sett.job_id):
            return

        # 1. Parse Script Lines
        progress_cb(sett.job_id, Stage.ENHANCING, 0.10, "Parsing multi-scene script...")
        script = sett.script_text.strip()
        if not script:
            script = FreeLLMPromptEnhancer.generate_script(sett.prompt, scene_count=4, language=sett.language)

        scenes = self._parse_script_scenes(script)
        if not scenes:
            scenes = [("Visual: " + sett.prompt, "VO: " + sett.prompt)]

        w, h = (1920, 1080) if sett.aspect_ratio == "16:9" else (1080, 1920) if sett.aspect_ratio == "9:16" else (1080, 1080)

        # 2. Process Scenes (with per-scene checkpoint reuse)
        scene_clips: List[Path] = []
        all_events: List[dict] = []
        vo_files: List[Path] = []
        current_offset = 0.0

        for s_idx, (vis_prompt, vo_prompt) in enumerate(scenes):
            if self.queue.is_cancelled(sett.job_id):
                log.info("Job [%s] cancelled by user.", sett.job_id)
                return

            scene_video = job_dir / f"scene_{s_idx:02d}.mp4"
            vo_path = job_dir / f"vo_scene_{s_idx:02d}.mp3"

            # Checkpoint recovery: If scene already rendered, skip generation
            if scene_video.exists() and ffprobe_duration(scene_video) > 0.5:
                log.info("Checkpoint: Reusing rendered scene %s for job %s", scene_video.name, sett.job_id)
                scene_clips.append(scene_video)
                if vo_path.exists():
                    vo_files.append(vo_path)
                continue

            prog = 0.20 + 0.45 * (s_idx / len(scenes))
            progress_cb(sett.job_id, Stage.GENERATING, prog, f"Processing scene {s_idx+1}/{len(scenes)}...")

            # Audio VO for scene
            vo_path_gen, events = self.audio_engine.generate_voiceover(vo_prompt, sett.language, sett.voice_gender)
            if vo_path_gen.exists():
                shutil.copyfile(vo_path_gen, vo_path)
            vo_dur = ffprobe_duration(vo_path)
            scene_dur = max(vo_dur + 0.5, 3.5)

            # Adjust event offsets for full video timeline
            for ev in events:
                all_events.append({
                    "text": ev["text"],
                    "offset": ev["offset"] + current_offset,
                    "duration": ev["duration"]
                })
            current_offset += scene_dur
            vo_files.append(vo_path)

            # Fetch visual asset
            asset_path, media_type = self.media_fetcher.fetch_scene_visual(vis_prompt, w, h, prefer_video=True, style=sett.style_preset)

            # Build Scene Video
            if media_type == "image":
                motion = random.choice(["zoom_in", "zoom_out", "pan_left", "pan_right"])
                self.video_engine.image_to_video_ken_burns(Path(asset_path), scene_dur, w, h, fps=sett.fps, motion_type=motion, out_path=scene_video)
            else: # video
                run_ffmpeg([
                    "-stream_loop", "-1", "-i", asset_path,
                    "-t", str(scene_dur),
                    "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(scene_video)
                ])

            scene_clips.append(scene_video)

        if self.queue.is_cancelled(sett.job_id):
            return

        # 3. Stitch Scenes
        progress_cb(sett.job_id, Stage.STITCHING, 0.70, "Stitching scenes...")
        stitched = job_dir / "script_stitched.mp4"
        if not (stitched.exists() and ffprobe_duration(stitched) > 0.5):
            self.video_engine.crossfade_concat(scene_clips, stitched, crossfade=0.4)

        # 4. Mix Full Audio Timeline
        progress_cb(sett.job_id, Stage.AUDIO, 0.85, "Mixing multilingual audio and music...")
        tot_dur = ffprobe_duration(stitched)
        # Concatenate per-scene VO files into one track (guard against none).
        valid_vo = [vf for vf in vo_files if vf and Path(vf).exists()]
        full_vo: Optional[Path] = None
        if len(valid_vo) == 1:
            full_vo = valid_vo[0]
        elif len(valid_vo) > 1:
            full_vo = job_dir / "full_vo.mp3"
            vo_inputs = []
            for vf in valid_vo:
                vo_inputs.extend(["-i", str(vf)])
            filter_a = "".join(f"[{i}:a]" for i in range(len(valid_vo))) + f"concat=n={len(valid_vo)}:v=0:a=1[outa]"
            run_ffmpeg([*vo_inputs, "-filter_complex", filter_a, "-map", "[outa]", "-c:a", "libmp3lame", str(full_vo)])

        mixed_audio = job_dir / "final_mixed_audio.aac"
        self.audio_engine.mix_and_duck_audio(full_vo, sett.music_mood, tot_dur, mixed_audio)

        # 5. Subtitles
        srt_file = job_dir / "subtitles.srt"
        self.subtitle_engine.events_to_srt(all_events, srt_file)

        # Mux and Burn (-shortest keeps A/V aligned)
        with_a = job_dir / "with_audio.mp4"
        run_ffmpeg(["-i", str(stitched), "-i", str(mixed_audio), "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-shortest", str(with_a)])
        current_vid = with_a

        if sett.subtitles_enabled:
            progress_cb(sett.job_id, Stage.SUBTITLES, 0.92, "Burning styled subtitles...")
            burned = job_dir / "burned.mp4"
            self.subtitle_engine.burn_subtitles(current_vid, srt_file, burned, sett.subtitle_style)
            current_vid = burned

        if sett.watermark_logo:
            logo = ASSETS_DIR / "kaami_makes_logo.png"
            if logo.exists():
                wm_vid = job_dir / "watermarked.mp4"
                self.video_engine.apply_watermark(current_vid, logo, wm_vid)
                current_vid = wm_vid

        if sett.anti_fingerprint:
            self.video_engine.anti_fingerprint_filter(current_vid, out_mp4)
        else:
            shutil.copyfile(current_vid, out_mp4)

        thumb = job_dir / "thumb.jpg"
        run_ffmpeg(["-ss", "0.5", "-i", str(out_mp4), "-vframes", "1", "-q:v", "2", str(thumb)])

        progress_cb(
            sett.job_id, Stage.DONE, 1.0, "Storyboard video generated successfully!",
            video_path=str(out_mp4),
            srt_path=str(srt_file),
            thumbnail_path=str(thumb),
            backend_used="script_story_engine",
        )

    def _parse_script_scenes(self, script: str) -> List[Tuple[str, str]]:
        scenes = []
        blocks = re.split(r"\n\s*\n", script.strip())
        for block in blocks:
            vis_m = re.search(r"Visual:\s*(.*?)(?=\nVO:|\Z)", block, re.IGNORECASE | re.DOTALL)
            vo_m = re.search(r"VO:\s*(.*)", block, re.IGNORECASE | re.DOTALL)
            vis = vis_m.group(1).strip() if vis_m else ""
            vo = vo_m.group(1).strip() if vo_m else ""
            if vis or vo:
                scenes.append((vis or "cinematic scenery", vo or vis))
        return scenes

# ----------------------------------------------------------------------------
# 10. Native Desktop GUI Fallback (Tkinter)
# ----------------------------------------------------------------------------

class VideoStudioDesktopGUI:
    """Standalone Desktop GUI for VideoStudio Pro with Dark Theme."""

    def __init__(self, studio: VideoStudio):
        self.studio = studio
        import tkinter as tk
        from tkinter import ttk, messagebox, filedialog

        self.root = tk.Tk()
        self.root.title("VideoStudio Pro — AI Video & Storyboard Studio [Kamran Ashraf / Kami]")
        self.root.geometry("1100x750")
        self.root.configure(bg="#0b0d17")

        # Styling
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background="#0b0d17", foreground="#e6e8f2")
        style.configure("TLabel", background="#0b0d17", foreground="#e6e8f2", font=("Segoe UI", 10))
        style.configure("TButton", background="#6366F1", foreground="#ffffff", font=("Segoe UI", 10, "bold"), borderwidth=0)
        style.map("TButton", background=[("active", "#a855f7")])

        # Header
        header = tk.Label(
            self.root,
            text="🎬 VideoStudio Pro — Master AI Video Creation Suite",
            font=("Segoe UI", 16, "bold"),
            bg="#0b0d17", fg="#c7d2fe"
        )
        header.pack(pady=12)

        # Tabs
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=15, pady=8)

        # Tab 1: AI Diffusion Video
        tab1 = ttk.Frame(notebook)
        notebook.add(tab1, text="  🌟 AI Video Studio  ")

        tk.Label(tab1, text="Prompt Idea:").pack(anchor="w", padx=10, pady=4)
        self.prompt_entry = tk.Entry(tab1, bg="#12152b", fg="#ffffff", font=("Segoe UI", 11), insertbackground="white")
        self.prompt_entry.pack(fill="x", padx=10, pady=4)
        self.prompt_entry.insert(0, "A futuristic cybernetic falcon soaring over neon skyscrapers at sunset")

        # Style & Quality row
        row1 = ttk.Frame(tab1)
        row1.pack(fill="x", padx=10, pady=8)
        tk.Label(row1, text="Style Preset:").pack(side="left")
        self.style_var = tk.StringVar(value="Cinematic")
        style_cb = ttk.Combobox(row1, textvariable=self.style_var, values=list(STYLE_PRESETS.keys()), width=15)
        style_cb.pack(side="left", padx=8)

        tk.Label(row1, text="Quality:").pack(side="left", padx=8)
        self.quality_var = tk.StringVar(value="1080p")
        q_cb = ttk.Combobox(row1, textvariable=self.quality_var, values=QUALITY_TIERS, width=8)
        q_cb.pack(side="left")

        tk.Label(row1, text="Aspect Ratio:").pack(side="left", padx=8)
        self.aspect_var = tk.StringVar(value="16:9")
        asp_cb = ttk.Combobox(row1, textvariable=self.aspect_var, values=list(ASPECT_SIZES.keys()), width=8)
        asp_cb.pack(side="left")

        # Tab 2: Script Storyboard
        tab2 = ttk.Frame(notebook)
        notebook.add(tab2, text="  📜 Script Storyboard  ")

        tk.Label(tab2, text="Topic / Idea:").pack(anchor="w", padx=10, pady=4)
        self.story_topic = tk.Entry(tab2, bg="#12152b", fg="#ffffff", font=("Segoe UI", 11), insertbackground="white")
        self.story_topic.pack(fill="x", padx=10, pady=4)
        self.story_topic.insert(0, "Top 3 unbelievable facts about deep sea ocean exploration")

        tk.Label(tab2, text="Multi-Scene Script (Visual: ... VO: ...):").pack(anchor="w", padx=10, pady=4)
        self.script_text = tk.Text(tab2, bg="#12152b", fg="#ffffff", height=8, font=("Segoe UI", 10))
        self.script_text.pack(fill="both", expand=True, padx=10, pady=4)

        # Generate Buttons
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", padx=15, pady=8)

        gen_btn = ttk.Button(btn_frame, text="🚀 Launch Modern Web Studio (Gradio UI)", command=self._open_web_ui)
        gen_btn.pack(side="left", padx=6)

        # Live Log Viewer
        log_frame = ttk.LabelFrame(self.root, text="System Log Console")
        log_frame.pack(fill="both", expand=True, padx=15, pady=8)

        self.log_text = tk.Text(log_frame, bg="#07090f", fg="#34d399", height=8, font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

        self.root.after(300, self._poll_logs)

    def _open_web_ui(self):
        import webbrowser
        webbrowser.open("http://127.0.0.1:7860")

    def _poll_logs(self):
        while not log_queue.empty():
            try:
                msg = log_queue.get_nowait()
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
            except Exception:
                break
        self.root.after(300, self._poll_logs)

    def run(self):
        self.root.mainloop()

# ----------------------------------------------------------------------------
# Main Execution Entrypoint
# ----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="VideoStudio Pro — Unified AI Video Creation Studio")
    parser.add_argument("--gui", action="store_true", help="Launch native Tkinter Desktop GUI")
    parser.add_argument("--web", action="store_true", help="Launch Gradio Web Studio")
    parser.add_argument("--probe", action="store_true", help="Run hardware diagnostics probe")
    args = parser.parse_args()

    if args.probe:
        studio = VideoStudio()
        print("\n" + "=" * 60)
        print("🎬 VideoStudio Pro Hardware & Environment Diagnostics")
        print("=" * 60)
        print(studio.probe.summary())
        print(f"Output Directory: {studio.config.output_dir}")
        print("Backends Active: " + ", ".join(studio.config.backend_order))
        print("=" * 60 + "\n")
        studio.queue.shutdown()
        return

    if args.gui:
        studio = VideoStudio()
        VideoStudioDesktopGUI(studio).run()
    else:
        # Web path: app.py creates its own single VideoStudio at import time.
        # Do NOT build one here too, or the worker lock would make app's studio
        # view-only (and jobs would never process).
        import app
        app.launch_app()

if __name__ == "__main__":
    main()
