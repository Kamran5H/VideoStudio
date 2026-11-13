"""
video_studio.py — Unified AI Video Generation & Storyboard Studio Engine.
Crafted with precision for Kamran Ashraf (Kami).

All-in-One Architecture:
  1. Environment & Hardware Probe    — HardwareProbe (CPU/GPU/CUDA/RAM/Disk)
  2. Configuration & Persistence     — StudioConfig (Colab, HF, Pexels, Pixabay, Gemini keys)
  3. Prompt Engine & Script Writer    — PromptEngine & FreeLLMPromptEnhancer (11 styles, Gemini, Pollinations, offline)
  4. Generative AI Video Backends     — Colab (Wan2.1-1.3B), LTX-2.3, Wan2.2-14B, LTX-Video, CogVideoX-5B, Test Pattern
  5. Media & Stock Asset Fetcher      — MediaFetcher (Pexels Video/Photo, Pixabay, Wikimedia, Pollinations Flux)
  6. Multilingual Neural Audio        — AudioEngine (Edge-TTS 10 languages, Synth BGM, Auto-Ducking)
  7. Subtitles                        — SubtitleEngine (word-timed captions, styled burn-in, translation)
  8. Video Compositor & FX            — VideoEngine (Ken Burns, crossfades, 60fps interpolation, upscale, clip conform)
  9. Pipeline Orchestrator            — VideoStudio (AI diffusion clips, multi-scene storyboards)
 10. Persistent Batch Job Queue       — JobQueue (JSON persistence, auto-retry on quota refresh, live callbacks)
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
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
import subprocess
import sys
import threading
import time
import uuid
import zlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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
# Repeated intermediate encodes need enough headroom to retain texture and avoid
# banding; faststart also makes exported MP4s begin playing before download ends.
VIDEO_ENCODE_ARGS = ("-c:v", "libx264", "-preset", "fast", "-crf", "18",
                     "-pix_fmt", "yuv420p", "-movflags", "+faststart")


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

class QualityError(RuntimeError):
    """A required creative asset is missing; the user can correct it or retry."""


class FFmpegError(RuntimeError):
    """ffmpeg is missing, timed out, or exited non-zero."""


def run_ffmpeg(args: list[str], timeout: int = 1800, cwd: Optional[Path] = None) -> None:
    """Run ffmpeg with arguments; raise FFmpegError on any failure.

    Missing binaries and timeouts are converted to FFmpegError too, so callers
    (and the job queue's retry logic) see one exception type with a readable
    message instead of a bare FileNotFoundError / TimeoutExpired.
    """
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg: %s", subprocess.list2cmdline(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as exc:
        raise FFmpegError(
            "ffmpeg was not found. Install it (or `pip install imageio-ffmpeg`) and restart."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"ffmpeg timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}): {(proc.stderr or '')[-2000:]}")


def _ffmpeg_info(path: Path) -> str:
    """stderr of ``ffmpeg -i path`` (stream/duration info), or '' on failure."""
    try:
        proc = subprocess.run(
            [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stderr or ""


def ffprobe_duration(path: Path) -> float:
    """Video/audio duration in seconds via ffprobe or ffmpeg fallback.

    Both probes are bounded by a timeout so a corrupt or streaming input can
    never hang the worker thread; any failure returns 0.0.
    """
    ffprobe = ffprobe_exe()
    if ffprobe:
        try:
            proc = subprocess.run(
                [ffprobe, "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, timeout=60,
            )
            value = float(proc.stdout.strip())
            if math.isfinite(value) and value >= 0:
                return value
        except (ValueError, OSError, subprocess.TimeoutExpired):
            pass
    # Fallback: parse Duration from ffmpeg stderr (imageio-ffmpeg ships no ffprobe)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", _ffmpeg_info(path))
    if not m:
        return 0.0
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def probe_video_size(path: Path) -> Optional[Tuple[int, int]]:
    """(width, height) of the first video stream, or None if unknown."""
    m = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})\b", _ffmpeg_info(path))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))

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
            import torch  # pyright: ignore[reportMissingImports]  # optional, local-GPU only
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
    """Master configuration for the entire unified VideoStudio suite.

    The output folder is NOT a config field: it comes from the
    VIDEOSTUDIO_OUTPUT_DIR environment variable (see DEFAULT_OUTPUT_DIR).
    Unknown keys in an older studio_config.json are ignored by load().
    """
    hf_token: str = ""
    colab_url: str = ""
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    gemini_api_key: str = ""
    backend_order: list[str] = field(default_factory=lambda: [
        "colab", "ltx23", "wan22", "ltx", "cogvideox", "test_pattern"
    ])
    quota_wait_cap_min: int = 45
    max_deferrals_per_job: int = 48           # quota/network waits before a job is Failed
    allow_test_pattern_fallback: bool = False
    default_steps: int = 30
    default_guidance: float = 6.0
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
        # Atomic: a crash mid-write must not leave a truncated config (which
        # load() would then discard, silently resetting every saved key).
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(dataclasses.asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> StudioConfig:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                known = {f.name for f in dataclasses.fields(cls)}
                cfg = cls(**{k: v for k, v in data.items() if k in known})
                # The backend list changes as free Spaces die or appear (the
                # Wan-AI/Wan2.1 Space is permanently down; veo3_free was never
                # real). The order is not user-editable in the UI, so a saved
                # list that names a different set is simply replaced.
                default_order = cls().backend_order
                if (not isinstance(cfg.backend_order, list)
                        or set(cfg.backend_order) != set(default_order)):
                    cfg.backend_order = default_order
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
        # crc32, not hash(): str hashes are randomized per process, so the
        # "deterministic" offline enhancer gave different text on every launch.
        rng = random.Random(seed if seed is not None else zlib.crc32(prompt.encode("utf-8")))
        motion = rng.choice(_MOTION_HINTS)
        # Put the actual subject first. Presets are visual treatment, not permission
        # to replace a night scene with golden hour or move a subject to neon streets.
        parts = [
            f"{prompt}.",
            f"Visual treatment: {preset if preset in STYLE_PRESETS else 'Cinematic'}; {style['grade']}.",
            "Preserve the specified subjects, setting, time of day and action.",
            "One continuous shot with restrained camera movement and scene-appropriate lighting.",
            f"Motion: {motion}; stable identities, natural proportions and coherent geometry.",
        ]
        return " ".join(parts)

    @staticmethod
    def negative(user_negative: str = "") -> str:
        extra = user_negative.strip()
        return f"{DEFAULT_NEGATIVE}, {extra}" if extra else DEFAULT_NEGATIVE

def looks_like_prompt(text: str) -> bool:
    """Reject free-endpoint replies that are error pages, JSON, rate-limit
    notices or essays rather than a single prompt."""
    t = (text or "").strip()
    if not (15 < len(t) <= 1500):
        return False
    if t[0] in "<{[":
        return False
    head = t[:120].lower()
    return not any(m in head for m in ("error", "rate limit", "too many requests", "queue full", "unauthorized"))


SCRIPT_RETRY_PAUSE = 6.0      # seconds between attempts at the free script writer


def looks_like_script(text: str, scene_count: Optional[int] = None) -> bool:
    """A usable storyboard reply: labelled Visual:/VO: lines, not an error page."""
    t = (text or "").strip()
    if not t or t[0] in "<{[" or len(t) > 20000:
        return False
    labels = list(re.finditer(r"(?im)^\W*(visual|vo|voiceover|narration)\W*:\s*", t))
    if not labels or len(labels) % 2:
        return False
    if scene_count is not None and len(labels) != scene_count * 2:
        return False
    visuals = []
    for i, label in enumerate(labels):
        is_visual = label.group(1).lower() == "visual"
        if is_visual != (i % 2 == 0):
            return False
        body = t[label.end():labels[i + 1].start() if i + 1 < len(labels) else len(t)].strip()
        if not body:
            return False
        if is_visual:
            visuals.append(body.casefold())
    return len(visuals) == len(set(visuals))


DEFAULT_GEMINI_MODELS = ("gemini-2.5-flash", "gemini-2.5-flash-lite")


def gemini_models() -> List[str]:
    """Gemini models to try in order. Override with GEMINI_MODELS (comma list)
    when Google retires/renames models — gemini-1.5-flash, which this file used
    to hard-code, has already been shut down."""
    raw = os.environ.get("GEMINI_MODELS", "")
    models = [m.strip() for m in raw.split(",") if m.strip()]
    return models or list(DEFAULT_GEMINI_MODELS)


def narration_brief(language: str) -> str:
    brief = (
        "VOICEOVER DIRECTION: Speak to one curious viewer in a warm, conversational voice. "
        "Open with a specific intriguing detail or an honest question; avoid generic introductions. "
        "Build curiosity, explain using a concrete example, and resolve the opening question at the end. "
        "Use varied, short spoken sentences with natural punctuation and breathing room. "
        "Invite the viewer to notice or imagine something once when it fits; do not interrogate them repeatedly. "
        "Give each scene one new idea in 12–30 spoken words. Match the visual to that exact idea. "
        "Avoid clickbait, exaggerated promises, filler, repeated hooks and forced subscribe requests. "
        "Write only words that should be spoken, without stage directions, emojis or performance labels. "
    )
    if language == "Urdu":
        brief += (
            "Use Urdu script and idiomatic Pakistani Urdu: conversational آپ، ذرا سوچیے، دیکھیے. "
            "Prefer پانی، زندگی، وجہ، سوال، حقیقت، مثال over Sanskritized vocabulary. "
            "Do not translate literally from English or output Devanagari. Preserve proper names and factual meaning. "
        )
    return brief


class FreeLLMPromptEnhancer:
    """Prompt and storyboard drafting with configured providers and offline fallback."""

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
                    f"Rewrite the idea as one achievable continuous video shot in the '{style}' aesthetic. "
                    "Preserve every specified subject, action, location, time of day and exclusion. "
                    "Add only compatible camera and lighting details; do not invent events or extra subjects. "
                    f"Suggested color treatment: {style_data['grade']}. "
                    "Output ONLY the prompt text, no commentary, under 75 words."
                )

                for model_name in gemini_models():
                    try:
                        resp = client.models.generate_content(
                            model=model_name,
                            contents=f"{sys_inst}\n\nUser Concept: {clean_p}"
                        )
                        if resp.text and looks_like_prompt(resp.text):
                            return resp.text.strip().replace('"', '')
                    except Exception as e_mod:
                        log.debug("Gemini model %s note: %s", model_name, e_mod)
                        continue
            except Exception as exc:
                log.debug("Gemini Pro API pass-through: %s", exc)

        # 2. Try Pollinations Free LLM Endpoint (No key / Zero billing)
        try:
            import urllib.parse

            import requests
            q_enc = urllib.parse.quote(
                f"Rewrite as a single 50-word {style} video shot. Preserve the subject, action, "
                f"setting and time of day exactly. No extra subjects or commentary. Idea: {clean_p}"
            )
            resp = requests.get(f"https://text.pollinations.ai/{q_enc}", timeout=6)
            if resp.status_code == 200 and looks_like_prompt(resp.text):
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
        scene_count = max(1, min(int(scene_count), 6))

        cfg = config or StudioConfig.load()
        gemini_key = cfg.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")

        # 1. Try Gemini AI Pro Plan for Scriptwriting
        if gemini_key:
            try:
                from google import genai
                client = genai.Client(api_key=gemini_key)
                sys_inst = (
                    f"You are an award-winning documentary & cinematic video director. "
                    f"For Urdu, use natural Pakistani Urdu vocabulary in Urdu script, avoiding Sanskritized Hindi wording. "
                    f"Write an engaging {scene_count}-scene video script about '{prompt}' in {language}. "
                    "For each scene, structure it EXACTLY as:\n\n"
                    "Visual: [3-5 concrete visual keywords for footage or image generation, e.g., golden sunrise mountains drone aerial]\n"
                    f"VO: [1-2 sentences of spoken voiceover narration in {language}]\n\n"
                    "Give each scene a distinct purpose: hook, explanation, concrete detail, then payoff. "
                    "Keep visuals specific to the narration and vary shot scale without changing the subject. "
                    "Use cautious factual language. Do not invent statistics, quotations or historical claims. "
                    "Output ONLY the Visual: and VO: lines without headers or numbering."
                )
                sys_inst += "\n" + narration_brief(language)
                for model_name in gemini_models():
                    try:
                        resp = client.models.generate_content(
                            model=model_name,
                            contents=sys_inst
                        )
                        if resp.text and looks_like_script(resp.text, scene_count):
                            return resp.text.strip()
                    except Exception:
                        continue
            except Exception as e:
                log.debug("Gemini script generation fallback: %s", e)

        # 2. Free Pollinations text model (no key, no billing)
        try:
            import urllib.parse

            import requests
            ask = (
                f"Write a {scene_count}-scene video script about '{prompt}'. For each scene output exactly "
                "two lines. Visual: 3-6 English keywords for stock footage. "
                f"VO: 1-2 sentences of spoken narration in {language}. "
                "For Urdu use Urdu script and natural Pakistani Urdu vocabulary, not Sanskritized Hindi. "
                "Use distinct, topic-specific scenes with a hook, explanation and payoff. "
                "Do not invent statistics, quotes or factual claims. "
                "Output only the Visual: and VO: lines, no titles, no numbering."
            )
            ask += "\n" + narration_brief(language)
            url = f"https://text.pollinations.ai/{urllib.parse.quote(ask)}"
            # The free tier queues behind image requests from the same address,
            # and sometimes answers 402/429 for a few seconds, so allow a slow
            # answer and two retries before reporting that the service is unavailable.
            for attempt in range(3):
                try:
                    resp = requests.get(url, timeout=60)
                    text = resp.text.strip() if resp.status_code == 200 else ""
                    if looks_like_script(text, scene_count):
                        return text
                    log.info("Free script writer gave no usable script (HTTP %s)", resp.status_code)
                except requests.RequestException as exc:
                    log.info("Free script writer unavailable: %s", type(exc).__name__)
                if attempt < 2:
                    time.sleep(SCRIPT_RETRY_PAUSE * (attempt + 1))
        except Exception as exc:
            log.debug("Pollinations script fallback: %s", exc)

        raise QualityError(
            f"The script services are unavailable. Paste a script in {language} or try again later. "
            "A Gemini API key can be configured in Settings."
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


def output_size(aspect_ratio: str, short_edge: int = 1080) -> Tuple[int, int]:
    """Even (width, height) for an aspect ratio with the given short edge:
    16:9 -> 1920x1080, 9:16 -> 1080x1920, 1:1 -> 1080x1080. The ratio comes
    from the name itself (the 832x480 generation bucket is only ~16:9 and used
    to give 1872x1080). Unknown ratios fall back to 16:9."""
    m = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", aspect_ratio or "")
    bw, bh = (int(m.group(1)), int(m.group(2))) if m and int(m.group(1)) and int(m.group(2)) else (16, 9)
    if bw >= bh:
        w, h = short_edge * bw / bh, short_edge
    else:
        w, h = short_edge, short_edge * bh / bw
    return int(round(w / 2)) * 2, int(round(h / 2)) * 2


SCRIPT_CROSSFADE_SECONDS = 0.4
MIN_SCENE_SECONDS = 3.5


def scene_video_seconds(vo_duration: float) -> float:
    """On-screen length of a storyboard scene: narration + 0.5s breathing room."""
    return max(vo_duration + 0.5, MIN_SCENE_SECONDS)


def scene_slot_seconds(vo_duration: float, index: int, count: int,
                       crossfade: float = SCRIPT_CROSSFADE_SECONDS) -> float:
    """Timeline time a scene owns in the stitched video. Every scene except
    the last loses `crossfade` seconds to the overlap with the next one, so
    the narration track must advance by exactly this much per scene."""
    dur = scene_video_seconds(vo_duration)
    return dur if index >= count - 1 else dur - crossfade


def vo_timeline_filter(slots: List[float]) -> str:
    """ffmpeg filter_complex that resamples every narration clip to one format
    (edge-tts, gTTS and silence differ) and pads/trims each to its scene slot
    before concatenating, producing a track aligned with the stitched video."""
    parts = [
        f"[{i}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"apad=whole_dur={s:.3f},atrim=0:{s:.3f},asetpts=N/SR/TB[a{i}]"
        for i, s in enumerate(slots)
    ]
    joined = "".join(f"[a{i}]" for i in range(len(slots)))
    return ";".join(parts) + f";{joined}concat=n={len(slots)}:v=0:a=1[outa]"

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


_ZEROGPU_LIMIT = re.compile(r"ZeroGPU (quota|runs limit)", re.IGNORECASE)


def quota_wait_seconds(error_text: str) -> Optional[int]:
    """Seconds to wait for a ZeroGPU limit error, or None for any other error.

    Hugging Face words it two ways (both seen live): "You have exceeded your
    ZeroGPU quota (120s requested vs. -60s left). Try again in 0:00:00." and
    "You have exceeded your ZeroGPU runs limit."
    """
    if not _ZEROGPU_LIMIT.search(error_text or ""):
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

def quota_needs_token(error_text: str) -> bool:
    """True when Hugging Face itself says the caller should sign in for quota.

    Anonymous ZeroGPU quota is smaller than a single video generation, so the
    Space answers "Try again in 0:00:00" forever. Waiting never helps there;
    only a (free) Hugging Face token does.
    """
    text = error_text or ""
    return bool(_ZEROGPU_LIMIT.search(text)) and "Authenticate with a Hugging Face token" in text


def hf_space_client(space: str, hf_token: str = "") -> Any:
    """gradio_client.Client for a HF Space, authenticated when a token is set.

    gradio_client 2.x renamed ``hf_token=`` to ``token=``. The old code passed
    ``hf_token=`` inside a bare ``except`` that retried anonymously, so the
    user's token was silently never sent (lower ZeroGPU quota). Pick the
    keyword the installed client actually accepts.
    """
    import inspect

    from gradio_client import Client
    token = (hf_token or "").strip() or None
    if not token:
        return Client(space, verbose=False)
    if "token" in inspect.signature(Client.__init__).parameters:
        return Client(space, verbose=False, token=token)
    return Client(space, verbose=False, hf_token=token)  # pyright: ignore[reportCallIssue]  # gradio_client < 2


class GenerationBackend:
    name = "abstract"
    description = ""
    supports_image_conditioning = False
    # Upper bound for one remote generation call. gradio_client.predict() has
    # no timeout, so a dead Colab tunnel or stuck Space used to block the
    # single worker thread — and every job behind it — forever.
    PREDICT_TIMEOUT = 900

    @classmethod
    def _predict(cls, client: Any, *args: Any, timeout: Optional[float] = None, **kwargs: Any) -> Any:
        limit = timeout or cls.PREDICT_TIMEOUT
        job = client.submit(*args, **kwargs)
        try:
            return job.result(timeout=limit)
        except (TimeoutError, concurrent.futures.TimeoutError) as exc:   # distinct classes on 3.10
            try:
                job.cancel()
            except Exception:
                pass
            # "timed out" makes the queue treat it as transient (defer + retry).
            raise TimeoutError(f"{cls.name}: no response within {int(limit)}s (timed out)") from exc

    def available(self) -> bool:
        return True

    def supports(self, req: GenerationRequest) -> bool:
        """False when this model cannot produce the requested shape at all."""
        return True

    def generate(self, req: GenerationRequest, out_path: Path, progress: Callable[[str], None] = lambda m: None) -> Path:
        raise NotImplementedError

    @staticmethod
    def _aspect_key(req: GenerationRequest) -> str:
        """Closest of the three shapes every Space offers."""
        ratio = req.width / max(req.height, 1)
        if ratio > 1.2:
            return "16:9"
        if ratio < 0.84:
            return "9:16"
        return "1:1"

    @staticmethod
    def _param_choices(client: Any, api_name: str, parameter: str) -> List[str]:
        """Dropdown choices a Space endpoint accepts for one parameter."""
        try:
            api = client.view_api(return_format="dict", print_info=False)
            ep = (api.get("named_endpoints", {}) or {}).get(api_name) or {}
            for p in ep.get("parameters", []):
                if p.get("parameter_name") == parameter:
                    return re.findall(r"'([^']+)'", str((p.get("python_type") or {}).get("type", "")))
        except Exception:
            pass
        return []

    def _raise_quota(self, exc: Exception, label: str) -> None:
        """Re-raise a ZeroGPU quota error as QuotaExhausted; otherwise return."""
        text = str(exc)
        wait = quota_wait_seconds(text)
        if wait:
            raise QuotaExhausted(wait, f"{label} ZeroGPU Quota Limit: {text[:300]}") from exc

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
        result = self._predict(
            client,
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
    MAX_SECONDS = 7.0      # the Space allows 8.5 s, but above 7 s it bills 25% more GPU time
    MAX_SIDE = 1280        # Space slider maximum; sides must be multiples of 32
    supports_image_conditioning = True

    def __init__(self, config: StudioConfig):
        self.config = config
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = hf_space_client(self.SPACE, self.config.hf_token)
        return self._client

    @classmethod
    def _snap32(cls, v: int) -> int:
        return min(max((v // 32) * 32, 256), cls.MAX_SIDE)

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
            return self._predict(client, api_name=api_name, **payload)

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
            self._raise_quota(exc, "LTX")
            raise

        path = self._extract_video(result)
        if not path:
            raise RuntimeError(f"LTX returned no video: {str(result)[:200]}")
        shutil.copyfile(path, out_path)
        return out_path

class LTX23Backend(GenerationBackend):
    """Official Lightricks LTX-2.3 Space (22B distilled, 24 fps, text or image start).

    Signature and limits are taken from the Space's own app.py:
    generate_video(input_image, prompt, duration, enhance_prompt, seed,
    randomize_seed, height, width); duration 1-10 s; fixed resolutions per
    shape; 75 s of ZeroGPU time per call.
    """
    name = "ltx23"
    description = "Free LTX-2.3 AI (official Lightricks Space, ZeroGPU)"
    SPACE = "Lightricks/LTX-2-3"
    API = "/generate_video"
    MAX_SECONDS = 10.0
    RESOLUTIONS = {"16:9": (1536, 1024), "9:16": (1024, 1536), "1:1": (1024, 1024)}
    supports_image_conditioning = True

    def __init__(self, config: StudioConfig):
        self.config = config
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = hf_space_client(self.SPACE, self.config.hf_token)
        return self._client

    def generate(self, req: GenerationRequest, out_path: Path, progress: Callable[[str], None] = lambda m: None) -> Path:
        w, h = self.RESOLUTIONS[self._aspect_key(req)]
        seconds = round(min(max(req.num_frames / max(req.fps, 1), 1.0), self.MAX_SECONDS), 1)
        client = self._get_client()
        image = None
        if req.init_image:
            from gradio_client import handle_file
            image = handle_file(req.init_image)
        payload: Dict[str, Any] = dict(
            input_image=image, prompt=req.prompt, duration=seconds,
            enhance_prompt=False,             # the studio already enhanced the prompt
            seed=req.seed if req.seed >= 0 else 42, randomize_seed=(req.seed < 0),
            height=h, width=w,
        )
        accepted = self._accepted_params(client, self.API)
        if accepted:
            payload = {k: v for k, v in payload.items() if k in accepted}
        progress(f"LTX-2.3: {'continuing from last frame' if image else 'text-to-video'} {w}x{h}, {seconds:.1f}s...")
        try:
            result = self._predict(client, api_name=self.API, **payload)
        except Exception as exc:
            self._raise_quota(exc, "LTX-2.3")
            raise
        path = self._extract_video(result)
        if not path:
            raise RuntimeError(f"LTX-2.3 returned no video: {str(result)[:200]}")
        shutil.copyfile(path, out_path)
        return out_path


class Wan22Backend(GenerationBackend):
    """Wan 2.2 T2V-A14B with the 4-step Lightning LoRA (free ZeroGPU Space).

    From the Space's app.py: generate_video(prompt, aspect_ratio, steps,
    negative_prompt, duration_seconds, guidance_scale, guidance_scale_2, seed,
    randomize_seed); 16 fps, at most 80 frames (5 s); 4-8 steps, guidance 1.
    It asks for the least GPU time of all backends (about 50 s for 5 s).
    """
    name = "wan22"
    description = "Free Wan 2.2 14B AI (4-step Lightning, ZeroGPU)"
    SPACE = "Upsampler/wan-2-2-14b-text-to-video"
    API = "/generate_video"
    MAX_SECONDS = 5.0
    STEPS = 4
    ASPECT_CHOICES = {"16:9": "16:9 (832x480)", "9:16": "9:16 (480x832)", "1:1": "1:1 (640x640)"}

    def __init__(self, config: StudioConfig):
        self.config = config
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = hf_space_client(self.SPACE, self.config.hf_token)
        return self._client

    def _aspect_choice(self, client: Any, key: str) -> str:
        # Prefer the label the Space currently publishes for this shape.
        for choice in self._param_choices(client, self.API, "aspect_ratio"):
            if choice.startswith(key):
                return choice
        return self.ASPECT_CHOICES[key]

    def generate(self, req: GenerationRequest, out_path: Path, progress: Callable[[str], None] = lambda m: None) -> Path:
        seconds = round(min(max(req.num_frames / max(req.fps, 1), 1.0), self.MAX_SECONDS), 1)
        client = self._get_client()
        choice = self._aspect_choice(client, self._aspect_key(req))
        payload: Dict[str, Any] = dict(
            prompt=req.prompt, aspect_ratio=choice, steps=self.STEPS,
            negative_prompt=req.negative_prompt, duration_seconds=seconds,
            guidance_scale=1, guidance_scale_2=1,     # Lightning LoRA: CFG off
            seed=req.seed if req.seed >= 0 else 42, randomize_seed=(req.seed < 0),
        )
        accepted = self._accepted_params(client, self.API)
        if accepted:
            payload = {k: v for k, v in payload.items() if k in accepted}
        progress(f"Wan 2.2: text-to-video {choice}, {seconds:.1f}s...")
        try:
            result = self._predict(client, api_name=self.API, **payload)
        except Exception as exc:
            self._raise_quota(exc, "Wan 2.2")
            raise
        path = self._extract_video(result)
        if not path:
            raise RuntimeError(f"Wan 2.2 returned no video: {str(result)[:200]}")
        shutil.copyfile(path, out_path)
        return out_path


class CogVideoXBackend(GenerationBackend):
    """CogVideoX-5B Space. Per its app.py it always renders 49 frames at
    720x480 (6 s at 8 fps) and reserves 300 s of ZeroGPU time, so it is the
    last resort and is skipped for tall videos (a 720x480 frame cropped to
    9:16 keeps only 270 pixels of width)."""
    name = "cogvideox"
    description = "Free CogVideoX-5B AI (Hugging Face ZeroGPU)"
    SPACE = "zai-org/CogVideoX-5B-Space"      # THUDM was renamed to zai-org

    def __init__(self, config: StudioConfig):
        self.config = config
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = hf_space_client(self.SPACE, self.config.hf_token)
        return self._client

    def supports(self, req: GenerationRequest) -> bool:
        return req.width >= req.height

    def generate(self, req: GenerationRequest, out_path: Path, progress: Callable[[str], None] = lambda m: None) -> Path:
        progress("CogVideoX-5B: generating AI clip (720x480)...")
        client = self._get_client()
        try:
            result = self._predict(
                client,
                prompt=req.prompt, image_input=None, video_input=None,
                video_strength=0.8,
                seed_value=req.seed if req.seed >= 0 else -1,
                scale_status=False, rife_status=True,
                api_name="/generate",
            )
        except Exception as exc:
            self._raise_quota(exc, "CogVideoX")
            raise

        path = self._extract_video(result)
        if not path:
            raise RuntimeError(f"CogVideoX returned no video: {str(result)[:200]}")
        shutil.copyfile(path, out_path)
        return out_path

class TestPatternBackend(GenerationBackend):
    name = "test_pattern"
    description = "Diagnostic Test Pattern (Offline Pipeline Verification)"

    def generate(self, req: GenerationRequest, out_path: Path, progress: Callable[[str], None] = lambda m: None) -> Path:
        progress("Generating test pattern verification clip...")
        duration = max(req.num_frames / req.fps, 2.0)
        vf = f"testsrc=duration={duration}:size={req.width}x{req.height}:rate={req.fps},format=yuv420p"
        run_ffmpeg(["-f", "lavfi", "-i", vf, "-t", str(duration), *VIDEO_ENCODE_ARGS, str(out_path)])
        return out_path

# ----------------------------------------------------------------------------
# 5. Media & Stock Asset Fetcher
# ----------------------------------------------------------------------------

class MediaFetcher:
    """Intelligent Media & Stock Asset Fetcher (Pexels, Pixabay, Wikimedia, Pollinations Flux AI, Gemini)."""

    POLLINATIONS_RETRY_PAUSES = (0, 8, 20)      # seconds before each attempt

    def __init__(self, config: Optional[StudioConfig] = None):
        self.config = config or StudioConfig()
        self.cache_dir = DEFAULT_OUTPUT_DIR / "media_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._recent_assets: List[str] = []

    def _stock_asset(self, source: str, asset_id: Any, url: str, suffix: str,
                     min_bytes: int) -> Optional[str]:
        """Reuse downloaded files and remember recent selections for shot variety."""
        key = f"{source}_{zlib.crc32(str(asset_id).encode()):08x}_{zlib.crc32(url.encode()):08x}"
        out = self.cache_dir / f"{key}{suffix}"
        got = str(out) if out.exists() and out.stat().st_size >= min_bytes else self._download(
            url, out, min_bytes=min_bytes, timeout=60
        )
        if got:
            self._recent_assets.append(f"{source}:{asset_id}")
            self._recent_assets = self._recent_assets[-100:]
        return got

    @staticmethod
    def _framing_score(item: dict, width: int, height: int) -> float:
        """Prefer files that retain detail after the requested aspect-ratio crop."""
        w, h = item.get("width", 0), item.get("height", 0)
        if not w or not h:
            return float("inf")
        return abs(math.log((w / h) / (width / height))) * 4 + max(width / w, height / h)

    def fetch_scene_visual(
        self,
        prompt: str,
        target_width: int = 1920,
        target_height: int = 1080,
        prefer_video: bool = True,
        style: str = "Cinematic",
        previous: Optional[Tuple[str, str]] = None,
    ) -> Tuple[str, str]:
        """Fetches the best visual asset for a scene: returns (file_path, media_type: 'video'|'image').

        ``previous`` may be supplied for another chunk of the same scene. It is
        reused only if every provider fails. A new storyboard scene should not
        use this option: the previous visual may not illustrate its narration.
        """
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

        # 6. Stay on topic: reuse the previous scene's visual
        if previous and Path(previous[0]).exists():
            log.warning("No new visual found for %r; reusing the previous scene's.", clean_q[:40])
            return previous

        raise QualityError(
            f"No usable visual was found for '{prompt[:100]}'. "
            "Try a more specific visual description, configure a stock media key in Settings, or retry later."
        )

    @staticmethod
    def _download(url: str, out: Path, min_bytes: int = 1024, timeout: int = 30,
                  headers: Optional[Dict[str, str]] = None) -> Optional[str]:
        """Stream a URL to `out` atomically.

        Writes to a .part file and renames only after a complete, non-trivial
        download, so a dropped connection or an HTML error page can never be
        cached and later fed to ffmpeg as a "video"/"image".
        """
        import requests
        tmp = out.with_name(out.name + ".part")
        try:
            with requests.get(url, stream=True, timeout=timeout, headers=headers) as r:
                r.raise_for_status()
                ctype = r.headers.get("Content-Type", "")
                if ctype.startswith(("text/", "application/json")):
                    raise ValueError(f"unexpected content type {ctype!r}")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
            if tmp.stat().st_size < min_bytes:
                raise ValueError(f"download too small ({tmp.stat().st_size} bytes)")
            tmp.replace(out)
            return str(out)
        except Exception as exc:
            log.debug("Download failed for %s: %s", url.split("?")[0], exc)
            tmp.unlink(missing_ok=True)
            return None

    def _search_pexels_video(self, query: str, width: int, height: int) -> Optional[str]:
        import requests
        headers = {"Authorization": self.config.pexels_api_key}
        orientation = "portrait" if height > width else "landscape"
        resp = requests.get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "per_page": 12, "orientation": orientation},
            headers=headers, timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            videos = data.get("videos", [])
            videos.sort(key=lambda v: f"pexels:{v.get('id')}" in self._recent_assets)
            for v in videos:
                files = [f for f in v.get("video_files", [])
                         if f.get("link") and f.get("file_type", "video/mp4") == "video/mp4"]
                files.sort(key=lambda f: self._framing_score(f, width, height))
                for vf in files:
                    got = self._stock_asset("pexels", v.get("id", vf["link"]), vf["link"], ".mp4", 50_000)
                    if got:
                        return got
        return None

    def _search_pixabay_video(self, query: str) -> Optional[str]:
        import requests
        # Key goes in `params`, never hand-built into a URL string that can be
        # echoed into logs / exception messages.
        resp = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": self.config.pixabay_api_key, "q": query, "per_page": 12},
            timeout=10,
        )
        if resp.status_code == 200:
            hits = resp.json().get("hits", [])
            hits.sort(key=lambda v: f"pixabay:{v.get('id')}" in self._recent_assets)
            for hit in hits:
                vids = hit.get("videos", {})
                chosen = vids.get("large") or vids.get("medium") or vids.get("small")
                if chosen and chosen.get("url"):
                    got = self._stock_asset("pixabay", hit.get("id", chosen["url"]), chosen["url"], ".mp4", 50_000)
                    if got:
                        return got
        return None

    def _search_pexels_photo(self, query: str, width: int, height: int) -> Optional[str]:
        import requests
        headers = {"Authorization": self.config.pexels_api_key}
        orientation = "portrait" if height > width else "landscape"
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "per_page": 12, "orientation": orientation},
            headers=headers, timeout=10,
        )
        if resp.status_code == 200:
            photos = resp.json().get("photos", [])
            photos.sort(key=lambda p: (f"pexels_photo:{p.get('id')}" in self._recent_assets,
                                       self._framing_score(p, width, height)))
            for photo in photos:
                src = photo.get("src", {}).get("large2x") or photo.get("src", {}).get("large")
                if src:
                    got = self._stock_asset("pexels_photo", photo.get("id", src), src, ".jpg", 5_000)
                    if got:
                        return got
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
                        ext = ".png" if img_url.lower().endswith(".png") else ".jpg"
                        out = self.cache_dir / f"wiki_{uuid.uuid4().hex[:8]}{ext}"
                        got = self._download(img_url, out, min_bytes=5_000,
                                             headers={"User-Agent": "VideoStudioPro/3.0"})
                        if got:
                            return got
        return None

    def _generate_pollinations_image(self, prompt: str, width: int, height: int, style: str) -> Optional[str]:
        import urllib.parse
        # Callers may pass an already-styled prompt (the AI-clip fallback does).
        styled = "Visual treatment:" in prompt or any(prompt.startswith(p["prefix"]) for p in STYLE_PRESETS.values())
        enhanced = prompt if styled else PromptEngine().enhance(prompt, preset=style)
        encoded = urllib.parse.quote(enhanced[:1500])
        # Snap dimensions to valid multiples
        w = (width // 64) * 64
        h = (height // 64) * 64
        out = self.cache_dir / f"flux_{uuid.uuid4().hex[:8]}.jpg"
        # The free tier serves one image at a time per address and answers
        # 429 to anything faster, so back off and try again.
        for attempt, pause in enumerate(self.POLLINATIONS_RETRY_PAUSES):
            if pause:
                time.sleep(pause)
            url = (f"https://image.pollinations.ai/prompt/{encoded}?width={w}&height={h}"
                   f"&model=flux&nologo=true&seed={random.randint(1, 999999)}")
            got = self._download(url, out, min_bytes=10_000, timeout=90)
            if got:
                return got
            log.debug("Pollinations image attempt %d failed", attempt + 1)
        return None

    def generate_nano_banana_frame(self, prompt: str, width: int = 1920, height: int = 1080, style: str = "Hyper-realistic") -> str:
        """Compatibility entry point for generating a still through the image provider."""
        ai_img = self._generate_pollinations_image(prompt, width, height, style)
        if ai_img and Path(ai_img).exists():
            return ai_img
        raise QualityError("The image service returned no usable image. Retry later.")

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

VOICES_DIR = DEFAULT_OUTPUT_DIR / "voices"
MY_VOICE_PATH = VOICES_DIR / "my_voice.wav"

# Languages the free cloning model speaks (Chatterbox Multilingual, 23 in all).
# Urdu and Punjabi are not among them, so those keep the standard voices.
CLONE_LANG_CODES: dict[str, str] = {
    "English": "en", "Hindi": "hi", "Arabic": "ar", "Spanish": "es",
    "French": "fr", "German": "de", "Chinese": "zh", "Japanese": "ja",
}


def split_for_cloning(text: str, limit: int = 280) -> List[str]:
    """Split narration into pieces the cloning Space accepts (it silently cuts
    anything past 300 characters), breaking at sentence ends where possible."""
    pieces: List[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?。！？۔،,;:])\s+", " ".join((text or "").split())):
        while len(sentence) > limit:                      # one very long sentence
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


class VoiceCloner:
    """Speak narration in the user's own voice from a short recording.

    Uses the free ResembleAI Chatterbox Multilingual Space (zero-shot cloning).
    Per its app.py: generate_tts_audio(text_input, language_id,
    audio_prompt_path_input, exaggeration_input, temperature_input,
    seed_num_input, cfgw_input), at most 300 characters and about one minute
    of ZeroGPU time per call. It needs the same free Hugging Face token as the
    video engines.
    """
    SPACE = "ResembleAI/Chatterbox-Multilingual-TTS"
    API = "/generate_tts_audio"
    MIN_SAMPLE_SECONDS = 5.0
    MAX_SAMPLE_SECONDS = 30.0

    def __init__(self, config: Optional[StudioConfig] = None, sample_path: Path = MY_VOICE_PATH):
        self.config = config or StudioConfig()
        self.sample_path = Path(sample_path)
        self._client: Any = None

    def has_sample(self) -> bool:
        return self.sample_path.is_file() and self.sample_path.stat().st_size > 1000

    def save_sample(self, source: Path) -> float:
        """Store a recording as the voice sample (mono WAV, quiet edges cut,
        at most 30 s). Returns its length; raises ValueError if unusable."""
        source = Path(source)
        if not source.is_file():
            raise ValueError("No recording was received.")
        self.sample_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.sample_path.with_name("my_voice_new.wav")
        try:
            run_ffmpeg([
                "-i", str(source), "-vn", "-ac", "1", "-ar", "24000",
                # dynaudnorm, not loudnorm: silenceremove feeding loudnorm
                # deadlocks ffmpeg on short clean recordings.
                "-af", "silenceremove=start_periods=1:start_threshold=-45dB,dynaudnorm",
                "-t", f"{self.MAX_SAMPLE_SECONDS}", str(tmp),
            ], timeout=60)
        except FFmpegError as exc:
            tmp.unlink(missing_ok=True)
            raise ValueError("That file could not be read as audio.") from exc
        seconds = ffprobe_duration(tmp)
        if seconds < self.MIN_SAMPLE_SECONDS:
            tmp.unlink(missing_ok=True)
            raise ValueError(f"The recording is too short ({seconds:.0f}s). Speak for 10 to 20 seconds.")
        tmp.replace(self.sample_path)
        return seconds

    def delete_sample(self) -> None:
        self.sample_path.unlink(missing_ok=True)

    def unavailable_reason(self, language: str) -> str:
        """Why cloning cannot be used right now ('' when it can)."""
        if not self.has_sample():
            return "no voice sample is saved yet (see the My voice tab)"
        if language not in CLONE_LANG_CODES:
            return f"voice cloning does not support {language} yet"
        if not self.config.hf_token.strip():
            return "voice cloning needs the free Hugging Face token (see Settings)"
        return ""

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = hf_space_client(self.SPACE, self.config.hf_token)
        return self._client

    def synthesize(self, text: str, language: str, out_path: Path) -> Path:
        """Narrate `text` in the saved voice. Raises on any failure so the
        caller can fall back to a standard voice."""
        reason = self.unavailable_reason(language)
        if reason:
            raise RuntimeError(reason)
        from gradio_client import handle_file
        client = self._get_client()
        parts: List[Path] = []
        work = out_path.with_suffix("")
        try:
            for i, piece in enumerate(split_for_cloning(text)):
                result = GenerationBackend._predict(
                    client,
                    text_input=piece, language_id=CLONE_LANG_CODES[language],
                    audio_prompt_path_input=handle_file(str(self.sample_path)),
                    exaggeration_input=0.5, temperature_input=0.8, seed_num_input=0, cfgw_input=0.5,
                    api_name=self.API, timeout=300,
                )
                produced = result[0] if isinstance(result, (list, tuple)) else result
                if isinstance(produced, dict):
                    produced = produced.get("path") or produced.get("url")
                if not produced or not Path(str(produced)).is_file():
                    raise RuntimeError(f"the voice service returned no audio: {str(result)[:120]}")
                part = Path(f"{work}_part{i:02d}.wav")
                shutil.copyfile(str(produced), part)
                parts.append(part)
            if not parts:
                raise RuntimeError("there was no text to speak")
            inputs: List[str] = []
            for part in parts:
                inputs.extend(["-i", str(part)])
            joined = "".join(f"[{i}:a]" for i in range(len(parts)))
            run_ffmpeg([*inputs, "-filter_complex", f"{joined}concat=n={len(parts)}:v=0:a=1[a]",
                        "-map", "[a]", "-c:a", "libmp3lame", "-b:a", "192k", str(out_path)])
            return out_path
        finally:
            for part in parts:
                part.unlink(missing_ok=True)


class AudioEngine:
    """Multilingual Neural TTS, Background Music Synthesis, and Sidechain Auto-Ducking."""

    def __init__(self, config: Optional[StudioConfig] = None):
        self.temp_dir = DEFAULT_OUTPUT_DIR / "audio_temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.voice_cloner = VoiceCloner(config)
        self.voice_note = ""        # why the cloned voice was not used, if it was asked for

    def generate_voiceover(
        self,
        text: str,
        language: str = "English",
        gender: str = "Male",
        rate: str = "+0%",
        pitch: str = "+0Hz",
        out_path: Optional[Path] = None,
        my_voice: bool = False,
        strict_my_voice: bool = False,
        delivery: str = "Natural",
    ) -> Tuple[Path, List[dict]]:
        """Synthesizes speech and returns (audio, word-boundary events).

        With ``my_voice`` the saved voice sample is cloned; that path returns
        no word events (captions are then timed by estimate_word_events). If
        cloning is not possible the standard voice is used and the reason is
        kept in ``self.voice_note``.
        """
        out = out_path or (self.temp_dir / f"vo_{uuid.uuid4().hex[:8]}.mp3")
        if my_voice and text.strip():
            try:
                self.voice_cloner.synthesize(text, language, out)
                if out.exists() and out.stat().st_size > 100:
                    return out, []
                raise RuntimeError("the voice service returned no audio")
            except Exception as exc:
                reason = str(exc)
                if quota_wait_seconds(reason):
                    reason = "today's free GPU time is used up"
                self.voice_note = reason[:160]
                if strict_my_voice:
                    out.unlink(missing_ok=True)
                    raise QualityError(
                        f"Your saved voice could not be cloned: {reason[:160]}. "
                        "Retry, select a standard voice explicitly, or record the complete narration in the live editor."
                    ) from exc
                log.warning("Cloned voice not used (%s); using the standard voice", reason[:200])
        lang_dict = TTS_VOICES.get(language, TTS_VOICES["English"])
        voice = lang_dict.get(gender, list(lang_dict.values())[0])
        if rate == "+0%":
            rate = {"Natural": "+0%", "Engaging": "+4%", "Calm": "-6%", "Energetic": "+10%"}.get(delivery, "+0%")

        events: List[dict] = []

        async def _synthesize():
            import inspect

            import edge_tts
            kwargs: Dict[str, Any] = {"rate": rate, "pitch": pitch}
            # edge-tts >= 7 only emits SentenceBoundary events unless asked;
            # without word boundaries the karaoke subtitles come out empty.
            if "boundary" in inspect.signature(edge_tts.Communicate).parameters:
                kwargs["boundary"] = "WordBoundary"
            communicate = edge_tts.Communicate(text, voice, **kwargs)
            words: List[dict] = []
            sentences: List[dict] = []
            with open(out, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk.get("data", b""))
                    elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                        target = words if chunk["type"] == "WordBoundary" else sentences
                        target.append({
                            "text": chunk.get("text", ""),
                            "offset": chunk.get("offset", 0) / 10_000_000,
                            "duration": chunk.get("duration", 0) / 10_000_000,
                        })
            events.extend(words or sentences)

        if not text.strip():
            events.clear()
            self._silence(out, 1.0)
            return out, events

        try:
            asyncio.run(_synthesize())
            if not out.exists() or out.stat().st_size < 100:
                raise RuntimeError("edge-tts returned no audio")
        except Exception as exc:
            log.warning("edge-tts synthesis failed (%s); using gTTS fallback", exc)
            events.clear()          # partial boundaries would not match new audio
            self._gtts_fallback(text, language, out)

        return out, events

    @staticmethod
    def _silence(out_path: Path, seconds: float) -> None:
        run_ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{seconds:.2f}",
                    "-c:a", "libmp3lame", str(out_path)])

    def _gtts_fallback(self, text: str, language: str, out_path: Path) -> None:
        try:
            from gtts import gTTS
            code = LANG_CODES.get(language, "en")
            tts = gTTS(text=text, lang=code)
            tts.save(str(out_path))
        except Exception as exc:
            log.error("gTTS fallback failed: %s", exc)
            out_path.unlink(missing_ok=True)
            raise QualityError("Narration could not be generated. Check your connection and retry.") from exc

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
        duration = max(float(duration), 0.5)
        # Rendered by ffmpeg's aevalsrc (C, per-sample) instead of a Python
        # loop: same chords/LFO/envelope, ~100x faster (60 s took 12.6 s).
        run_ffmpeg([
            "-f", "lavfi", "-i", self.synth_expression(chords, duration),
            "-af", f"afade=t=in:d=1.5,afade=t=out:st={max(0.0, duration - 2.0):.3f}:d=2",
            "-ac", "2", "-c:a", "libmp3lame", "-b:a", "192k", str(out_path),
        ])
        return out_path

    @staticmethod
    def synth_expression(chords: List[Tuple[float, float, float]], duration: float,
                         chord_seconds: float = 3.0) -> str:
        """aevalsrc source: chord pads cycling every `chord_seconds`, four
        harmonics, 0.3 Hz amplitude LFO, peak about 0.37 full scale."""
        def pad(f1: float, f2: float, f3: float) -> str:
            return (f"0.40*sin(2*PI*{f1}*t)+0.30*sin(2*PI*{f2}*t)"
                    f"+0.20*sin(2*PI*{f3}*t)+0.10*sin(2*PI*{f1 * 2}*t)")

        cycle = chord_seconds * len(chords)
        expr = pad(*chords[-1])
        for i in range(len(chords) - 2, -1, -1):
            expr = f"if(lt(mod(t\\,{cycle})\\,{chord_seconds * (i + 1)})\\,{pad(*chords[i])}\\,{expr})"
        expr = f"0.366*(0.85+0.15*sin(2*PI*0.3*t))*({expr})"
        return f"aevalsrc=exprs={expr}:s=44100:d={duration:.3f}"

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
            # Voiceover only: pad short narration AND cut long narration to the video
            run_ffmpeg(["-i", str(voiceover_path), "-af", f"apad=whole_dur={video_duration}",
                        "-t", f"{video_duration:.3f}", "-c:a", "aac", str(out_path)])
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
            # apad: sidechaincompress ends with its sidechain, so without
            # padding the music (and, via the -shortest mux, the VIDEO) was
            # cut off the moment narration stopped. -t bounds the result.
            f"[0:a]apad,asplit=2[vo1][vo2];"
            f"[bg][vo1]sidechaincompress=threshold=0.08:ratio=6:attack=30:release=450[ducked];"
            # normalize=0: amix otherwise divides every input by 2, halving
            # the narration; the limiter catches the (rare) summed peaks.
            f"[ducked][vo2]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,"
            f"alimiter=limit=0.95,"
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

def estimate_word_events(text: str, duration: float) -> List[dict]:
    """Word timings spread across a narration clip in proportion to word length.

    Edge-TTS reports exact word boundaries; the gTTS backup voice reports none,
    which used to leave the video with no subtitles at all. An even spread is
    close enough for caption lines of a few words.
    """
    words = (text or "").split()
    if not words or duration <= 0:
        return []
    weights = [len(w) + 2 for w in words]          # +2: every word costs a small pause
    total = float(sum(weights))
    usable = max(duration - 0.15, duration * 0.9)  # speech rarely fills the very end
    events, t = [], 0.0
    for word, weight in zip(words, weights, strict=True):
        span = usable * weight / total
        events.append({"text": word, "offset": round(t, 3), "duration": round(span, 3)})
        t += span
    return events


class SubtitleEngine:
    """Karaoke Subtitles, Burnt-in Captions, and Translation."""

    @staticmethod
    def events_to_srt(events: List[dict], out_path: Path, max_words: int = 5) -> Path:
        """Converts word boundary events to an SRT subtitle file."""
        if not events:
            out_path.write_text("1\n00:00:00,000 --> 00:00:02,000\n \n", encoding="utf-8")
            return out_path

        def fmt_time(seconds: float) -> str:
            total_ms = max(0, round(seconds * 1000))
            h, remainder = divmod(total_ms, 3_600_000)
            m, remainder = divmod(remainder, 60_000)
            s, ms = divmod(remainder, 1000)
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
        """Burns styled subtitles into video via ffmpeg.

        The subtitles filter takes a filter-graph-escaped path, which breaks on
        Windows drive letters and on apostrophes/commas in folder names. We run
        ffmpeg *inside* the SRT's folder and reference a plain file name, so no
        escaping is needed; the video paths are made absolute.
        """
        style = SUBTITLE_STYLES.get(style_name, SUBTITLE_STYLES["Neon Glow"])
        force_style = (
            f"Fontname=Segoe UI,Fontsize=17,PrimaryColour={style['PrimaryColour']},"
            f"OutlineColour={style['OutlineColour']},BackColour={style['BackColour']},"
            "Outline=1.5,Shadow=0.5,MarginL=24,MarginR=24,MarginV=18,Alignment=2,WrapStyle=0"
        )
        srt_path = Path(srt_path).resolve()
        safe_name = srt_path.name
        if not re.fullmatch(r"[\w.\-]+", safe_name):
            safe_name = "subs_burn.srt"
            shutil.copyfile(srt_path, srt_path.parent / safe_name)
        vf = f"subtitles={safe_name}:charenc=UTF-8:force_style='{force_style}'"
        run_ffmpeg(
            ["-i", str(Path(video_path).resolve()), "-vf", vf, "-map", "0:v:0", "-map", "0:a?",
             "-c:a", "copy", *VIDEO_ENCODE_ARGS, str(Path(out_path).resolve())],
            cwd=srt_path.parent,
        )
        return out_path

# ----------------------------------------------------------------------------
# 8. Video FX, Motion & Compositing Engine
# ----------------------------------------------------------------------------

class VideoEngine:
    """Ken Burns motion, crossfades, motion interpolation and upscaling."""

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
        """Animate a still with restrained motion over the full scene duration."""
        out = out_path or (DEFAULT_OUTPUT_DIR / f"kb_{uuid.uuid4().hex[:8]}.mp4")
        if duration <= 0 or fps <= 0 or width < 2 or height < 2:
            raise ValueError("Scene duration, frame rate and dimensions must be positive.")
        total_frames = max(1, round(duration * fps))
        # Use normalized time instead of per-frame increments: a long narration
        # must not finish its zoom early and then hold still for the remaining scene.
        phase = f"on/{max(1, total_frames - 1)}"
        centered = "x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"

        if motion_type == "zoom_in":
            motion = f"z='1+0.08*{phase}':{centered}"
        elif motion_type == "zoom_out":
            motion = f"z='1.08-0.08*{phase}':{centered}"
        elif motion_type == "pan_left":
            motion = f"z='1.08':x='(iw-iw/zoom)*(1-{phase})':y='(ih-ih/zoom)/2'"
        elif motion_type == "pan_right":
            motion = f"z='1.08':x='(iw-iw/zoom)*{phase}':y='(ih-ih/zoom)/2'"
        else:
            raise ValueError(f"Unknown camera motion: {motion_type}")
        zp = f"zoompan={motion}:d={total_frames}:s={width}x{height}:fps={fps}"

        # Reset pixel aspect ratio after the crop; otherwise portrait sources can
        # produce stretched output despite the correct pixel dimensions.
        vf = f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase,crop={width*2}:{height*2},setsar=1,{zp},setsar=1,format=yuv420p"
        # One still frame in, `d` animated frames out. (With `-loop 1` the
        # image was re-fed at 25 fps and zoompan restarted per input frame;
        # only `-t` hid it, at the cost of decoding/scaling every extra frame.)
        run_ffmpeg(["-i", str(image_path), "-vf", vf, "-frames:v", str(max(total_frames, 1)),
                    *VIDEO_ENCODE_ARGS, str(out)])
        return out

    @staticmethod
    def normalize_filter(width: int, height: int, fps: int) -> str:
        """Letterbox any clip into width x height at a fixed fps/timebase so
        clips from different backends can be xfaded or concatenated."""
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"fps={fps},settb=AVTB,format=yuv420p"
        )

    @staticmethod
    def crossfade_concat(
        video_clips: List[Path],
        out_path: Path,
        crossfade: float = CROSSFADE_SECONDS,
        size: Optional[Tuple[int, int]] = None,
        fps: int = 30,
    ) -> Path:
        """Smoothly concatenates video clips with crossfade transitions.

        Every input is normalized to one frame size (``size`` or the first
        clip's size), fps and timebase first: xfade and concat both reject
        inputs whose dimensions differ, which happens whenever the backend
        fallback chain switches models mid-job.
        """
        if not video_clips:
            raise ValueError("No video clips provided for concatenation.")
        if len(video_clips) == 1:
            shutil.copyfile(video_clips[0], out_path)
            return out_path

        tw, th = size or probe_video_size(video_clips[0]) or (1280, 720)
        tw, th = tw - tw % 2, th - th % 2
        norm = VideoEngine.normalize_filter(tw, th, fps)

        inputs = []
        filter_parts = []
        for i, v in enumerate(video_clips):
            inputs.extend(["-i", str(v)])
            filter_parts.append(f"[{i}:v]{norm}[v{i}]")

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
            run_ffmpeg([*inputs, "-filter_complex", filter_str, "-map", "[outv]", "-r", str(fps),
                        *VIDEO_ENCODE_ARGS, str(out_path)])
        except Exception as e:
            log.warning("xfade transition failed (%s), falling back to standard concat...", e)
            # Safe robust concat fallback
            # Preserve scene start times even when xfade is unavailable. Dropping
            # overlap without trimming would drift every later narration/caption.
            concat_parts = []
            for i, clip in enumerate(video_clips):
                trim = (f",trim=duration={max(0.1, ffprobe_duration(clip) - crossfade):.6f}"
                        if i < len(video_clips) - 1 else "")
                concat_parts.append(f"[{i}:v]{norm}{trim},setpts=PTS-STARTPTS[cv{i}]")
            join_str = "".join(f"[cv{i}]" for i in range(len(video_clips))) + f"concat=n={len(video_clips)}:v=1:a=0[outv]"
            filter_fallback = ";".join(concat_parts) + ";" + join_str
            run_ffmpeg([*inputs, "-filter_complex", filter_fallback, "-map", "[outv]", "-r", str(fps),
                        *VIDEO_ENCODE_ARGS, str(out_path)])

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
            *VIDEO_ENCODE_ARGS, "-preset", "veryfast", str(out_path),
        ])
        return out_path

    @staticmethod
    def conform_clip(video_path: Path, width: int, height: int, seconds: float) -> bool:
        """Make a backend's clip match what was asked for, in place.

        Free models return whatever they natively make: LTX-2.3 is 3:2 with a
        sound track, CogVideoX is always 720x480 for 6 s, Wan 2.2 stops at
        5 s. The clip is centre-cropped to the requested aspect ratio (at its
        own resolution, so nothing is downscaled), cut or frozen to the
        requested length, and its audio is dropped. Returns True if rewritten.
        """
        size = probe_video_size(video_path)
        duration = ffprobe_duration(video_path)
        if not size or duration <= 0:
            return False
        cw, ch = size
        target = width / max(height, 1)
        if cw / ch > target:
            nw, nh = int(round(ch * target / 2)) * 2, ch - ch % 2
        else:
            nw, nh = cw - cw % 2, int(round(cw / target / 2)) * 2
        nw, nh = max(2, min(nw, cw)), max(2, min(nh, ch))
        needs_crop = abs(cw / ch - target) / target > 0.01
        too_long = duration > seconds + 0.15
        too_short = duration < seconds - 0.15
        has_audio = bool(re.search(r"Stream #\d+:\d+.*Audio:", _ffmpeg_info(video_path)))
        if not (needs_crop or too_long or too_short or has_audio):
            return False
        filters = []
        if needs_crop:
            filters.append(f"crop={nw}:{nh}")
        if too_short:
            filters.append(f"tpad=stop_mode=clone:stop_duration={seconds - duration:.3f}")
        tmp = video_path.with_name(video_path.stem + "_conform.mp4")
        args = ["-i", str(video_path)]
        if filters:
            args += ["-vf", ",".join(filters)]
        args += ["-t", f"{seconds:.3f}", "-an", "-c:v", "libx264", "-crf", "17",
                 "-preset", "veryfast", "-pix_fmt", "yuv420p", str(tmp)]
        run_ffmpeg(args)
        tmp.replace(video_path)
        return True

    @staticmethod
    def interpolate_motion_60fps(video_path: Path, out_path: Path) -> Path:
        """Silky smooth 60fps motion interpolation via ffmpeg minterpolate."""
        vf = "minterpolate='fps=60:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1'"
        run_ffmpeg(["-i", str(video_path), "-vf", vf, "-c:a", "copy", *VIDEO_ENCODE_ARGS, str(out_path)])
        return out_path

    UPSCALE_SHORT_EDGE = {"720p": 720, "1080p": 1080, "4K": 2160}

    @staticmethod
    def upscale_filter(target_quality: str) -> Optional[str]:
        """Aspect-preserving Lanczos scale: the SHORT edge becomes the tier
        height, so 9:16 / 1:1 / 21:9 clips are no longer squashed into a
        fixed 16:9 frame. Dimensions stay even for yuv420p/libx264."""
        short = VideoEngine.UPSCALE_SHORT_EDGE.get(target_quality)
        if not short:
            return None
        return (
            f"scale='if(gte(iw,ih),-2,{short})':'if(gte(iw,ih),{short},-2)':flags=lanczos"
        )

    @staticmethod
    def upscale_video(video_path: Path, target_quality: str, out_path: Path,
                      aspect_ratio: Optional[str] = None) -> Path:
        """Upscales video to 720p / 1080p / 4K using Lanczos.

        With ``aspect_ratio`` the result is the exact standard frame for that
        shape (e.g. 1920x1080 or 1080x1920), filling and centre-cropping the
        few pixels by which model buckets differ from the true ratio.
        """
        scale_filter = VideoEngine.upscale_filter(target_quality)
        if not scale_filter:
            shutil.copyfile(video_path, out_path)
            return out_path
        if aspect_ratio:
            tw, th = output_size(aspect_ratio, VideoEngine.UPSCALE_SHORT_EDGE[target_quality])
            scale_filter = (f"scale={tw}:{th}:force_original_aspect_ratio=increase:flags=lanczos,"
                            f"crop={tw}:{th},setsar=1")

        vf = scale_filter  # Preserve source texture; aggressive sharpening creates halos.
        run_ffmpeg(["-i", str(video_path), "-vf", vf, "-map", "0:v:0", "-map", "0:a?",
                    "-c:a", "copy", *VIDEO_ENCODE_ARGS, str(out_path)])
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
    def from_str(cls, val: Any) -> Stage:
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
    scene_count: int = 4
    negative_prompt: str = ""
    style_preset: str = "Cinematic"
    quality: str = "1080p"
    aspect_ratio: str = "16:9"
    duration: float = 10.0
    fps: int = 30
    language: str = "English"
    voice_gender: str = "Male"
    use_my_voice: bool = False                 # narrate with the saved voice sample
    voice_script: str = ""
    narration_delivery: str = "Engaging"
    music_mood: str = "Ambient"
    subtitles_enabled: bool = True
    subtitle_style: str = "Neon Glow"
    translate_lang: str = "None"
    interpolate_60fps: bool = False
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
    deferrals: int = 0                         # quota/network deferrals so far

class JobQueue:
    """Thread-safe, persistent batch job queue with auto-resume and crash recovery."""

    def __init__(self, studio: VideoStudio):
        self.studio = studio
        self.queue_file = DEFAULT_OUTPUT_DIR / "queue" / "jobs.json"
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()          # re-entrant: _save() is called
        # from inside methods that already hold the lock. Queue writes stay
        # under this lock too, so concurrent snapshots cannot share a temp file.
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
            log.warning("Worker lock error (%s) — queue will remain view-only", exc)
            return False

    def shutdown(self) -> None:
        self._running = False
        worker = self._active_thread
        if worker and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=5)
            if worker.is_alive():
                log.warning("Queue worker is still active; keeping its lock until process exit.")
                return
        try:
            if (self.is_worker and self._lock_path.exists()
                    and self._lock_path.read_text().strip() == str(os.getpid())):
                self._lock_path.unlink()
        except Exception:
            pass

    def _load(self):
        """Load the persisted queue, one job at a time.

        A single malformed entry is skipped instead of aborting the whole load
        (the next save would otherwise overwrite every job with an empty
        queue). If the file itself is unreadable it is moved aside to
        ``jobs.json.corrupt-<time>`` so nothing is silently destroyed.
        """
        if not self.queue_file.exists():
            return
        try:
            data = json.loads(self.queue_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            backup = self.queue_file.with_name(f"{self.queue_file.name}.corrupt-{int(time.time())}")
            try:
                self.queue_file.replace(backup)
                log.warning("Queue file unreadable (%s); moved to %s", e, backup.name)
            except OSError:
                log.warning("Queue file unreadable (%s)", e)
            return
        if isinstance(data, list):
            data = {j.get("job_id", str(i)): j for i, j in enumerate(data) if isinstance(j, dict)}
        elif not isinstance(data, dict):
            data = {}
        for j_id, j_data in data.items():
            try:
                self._jobs[str(j_id)] = self._job_from_dict(str(j_id), j_data)
                if self._jobs[str(j_id)].stage == Stage.CANCELLED:
                    self._cancelled_jobs.add(str(j_id))
            except Exception as e:
                log.warning("Skipping unreadable queue entry %s: %s", j_id, e)

    @staticmethod
    def _job_from_dict(j_id: str, j_data: Any) -> JobStatus:
        if not isinstance(j_data, dict):
            raise TypeError("entry is not an object")
        known_fields = {f.name for f in dataclasses.fields(JobSettings)}
        sett_data = j_data.get("settings")
        sett = None
        if isinstance(sett_data, dict):
            filtered = {k: v for k, v in sett_data.items() if k in known_fields}
            sett = JobSettings(**filtered) if filtered else None
        return JobStatus(
            job_id=j_id,
            stage=Stage.from_str(j_data.get("stage", "Queued")),
            progress=float(j_data.get("progress", 0.0) or 0.0),
            message=str(j_data.get("message", "") or ""),
            video_path=j_data.get("video_path"),
            srt_path=j_data.get("srt_path"),
            thumbnail_path=j_data.get("thumbnail_path"),
            error=j_data.get("error"),
            backend_used=j_data.get("backend_used"),
            settings=sett,
            retries=int(j_data.get("retries", 0) or 0),
            not_before=float(j_data.get("not_before", 0.0) or 0.0),
            deferrals=int(j_data.get("deferrals", 0) or 0),
        )

    def _auto_resume_interrupted_jobs(self):
        """Recover and continue any job interrupted by a shutdown/crash/browser
        close. Only the worker-owning instance resumes; failed jobs stay failed
        (a fresh retry is an explicit user action via the Retry/Resume button)."""
        if not self.is_worker:
            return
        has_pending = False
        with self._lock:
            for j_id, js in self._jobs.items():
                if js.stage not in (Stage.DONE, Stage.CANCELLED, Stage.FAILED):
                    log.info("Found unfinished job [%s] at stage '%s'. Auto-resuming...", j_id, js.stage.value)
                    js.stage = Stage.QUEUED
                    js.not_before = 0.0
                    js.retries = 0
                    js.deferrals = 0
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
                    "deferrals": js.deferrals,
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
        if not self.is_worker:
            raise RuntimeError(
                "This studio is view-only because another instance owns the job queue."
            )
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
        if not self.is_worker:
            return False
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
        if not self.is_worker:
            return False
        with self._lock:
            if job_id in self._cancelled_jobs:
                self._cancelled_jobs.remove(job_id)
            js = self._jobs.get(job_id)
            if js:
                js.stage = Stage.QUEUED
                js.retries = 0
                js.deferrals = 0
                js.not_before = 0.0
                js.error = None
                js.message = "Re-queued. Resuming from checkpoint..."
                self._save(force=True)
                self._ensure_worker()
                return True
        return False

    def remove(self, job_id: str) -> bool:
        """Delete a finished, failed or stopped job together with its files.
        A job that is still queued or rendering must be stopped first."""
        if not self.is_worker:
            return False
        with self._lock:
            js = self._jobs.get(job_id)
            if js is None or js.stage not in (Stage.DONE, Stage.FAILED, Stage.CANCELLED):
                return False
            del self._jobs[job_id]
            self._cancelled_jobs.discard(job_id)
            self._save(force=True)
        for path in (js.video_path, js.srt_path, js.thumbnail_path):
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError as exc:
                    log.warning("Could not delete %s: %s", path, exc)
        shutil.rmtree(DEFAULT_OUTPUT_DIR / "jobs" / job_id, ignore_errors=True)
        return True

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

            if pending_job.settings is None:
                # Nothing to run (e.g. a hand-edited/legacy queue entry). Without
                # this it would stay QUEUED and be re-picked in a tight loop.
                self._fail(pending_job, "Job has no saved settings and cannot be run.")
                continue

            try:
                self.studio.execute_job(pending_job, self._progress_callback)
                if (pending_job.stage not in (Stage.DONE, Stage.FAILED, Stage.CANCELLED)
                        and not self.is_cancelled(pending_job.job_id)):
                    # Pipeline returned without a result: never leave it QUEUED,
                    # or the worker would spin on it forever.
                    self._fail(pending_job, "Pipeline finished without producing a video.")
            except QuotaExhausted as quota:
                # Free-GPU quota / transient network — defer and auto-resume when
                # the window replenishes. Finished chunks stay on disk, so the
                # resume is cheap. Capped so a job cannot wait forever.
                pending_job.deferrals += 1
                cap = int(getattr(self.studio.config, "max_deferrals_per_job", 48))
                if pending_job.deferrals > cap:
                    self._fail(pending_job, f"Gave up after {cap} quota/network waits: {quota.reason}")
                    continue
                pending_job.stage = Stage.QUEUED
                pending_job.not_before = time.time() + max(30, int(quota.wait_seconds))
                at = time.strftime("%H:%M", time.localtime(pending_job.not_before))
                pending_job.message = f"⏳ {quota.reason} — auto-retry at {at} (checkpoints saved)"
                pending_job.error = None
                self._save(force=True)
            except QualityError as exc:
                self._fail(pending_job, str(exc))
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

    def _fail(self, job: JobStatus, reason: str) -> None:
        job.stage = Stage.FAILED
        job.error = reason[:500]
        job.message = f"❌ {reason[:120]}"
        log.error("Job %s failed: %s", job.job_id, reason)
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

    BACKEND_NAMES = ("colab", "ltx23", "wan22", "ltx", "cogvideox", "test_pattern")

    def __init__(self, config: Optional[StudioConfig] = None):
        self.config = config or StudioConfig.load()
        self.probe = HardwareProbe()
        self.prompt_engine = PromptEngine()
        self.audio_engine = AudioEngine(self.config)
        self.media_fetcher = MediaFetcher(self.config)
        self.video_engine = VideoEngine()
        self.subtitle_engine = SubtitleEngine()

        # Initialize backends BEFORE the queue — the queue auto-resumes
        # interrupted jobs on construction and immediately needs self.backends.
        self.backends: Dict[str, GenerationBackend] = {
            "colab": ColabBackend(self.config),
            "ltx23": LTX23Backend(self.config),
            "wan22": Wan22Backend(self.config),
            "ltx": LTXSpaceBackend(self.config),
            "cogvideox": CogVideoXBackend(self.config),
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
        self.audio_engine.voice_note = ""

        if sett.mode == "script_story":
            self._execute_script_mode(sett, job_dir, final_video_out, progress_cb)
        else:
            self._execute_ai_diffusion_mode(sett, job_dir, final_video_out, progress_cb)

    def _voice_note(self, sett: JobSettings) -> str:
        """Sentence for the result card when 'My voice' was asked for but a
        standard voice had to be used."""
        note = self.audio_engine.voice_note if sett.use_my_voice else ""
        return f" Your own voice was not used: {note}." if note else ""

    @staticmethod
    def _publish_video(source: Path, destination: Path, settings: JobSettings,
                       job_dir: Path, voice_note: str = "") -> None:
        """Validate the export, write its report, and publish an atomic, seekable MP4."""
        duration = ffprobe_duration(source)
        size = probe_video_size(source)
        if duration <= 0.5 or not size:
            raise QualityError("The final video could not be validated. Retry the render.")
        sources = []
        for metadata in sorted(job_dir.glob("*.asset.json")):
            try:
                sources.append(json.loads(metadata.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                sources.append({"scene": metadata.stem, "source": "unknown (older checkpoint)"})
        report = {
            "job_id": settings.job_id,
            "mode": settings.mode,
            "prompt": settings.prompt,
            "duration_seconds": duration,
            "export_width": size[0],
            "export_height": size[1],
            "style": settings.style_preset,
            "language": settings.language,
            "voice_note": voice_note,
            "subtitles_requested": settings.subtitles_enabled,
            "subtitles_burned_in": source.name in ("with_subs.mp4", "burned.mp4"),
            "sources": sources,
            "notes": [
                "Export size is not native source resolution; scaling cannot recover missing detail.",
                "AI visuals are illustrations. Generated narration requires factual review.",
            ],
        }
        if settings.subtitles_enabled and not report["subtitles_burned_in"]:
            report["notes"].append("Subtitles were not burned in. Use the companion SRT file.")
        report_path = job_dir / "render_report.json"
        report_tmp = report_path.with_suffix(".json.part")
        report_tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report_tmp.replace(report_path)
        temp_video = destination.with_name(destination.stem + ".publishing.mp4")
        try:
            run_ffmpeg(["-i", str(source), "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
                        "-movflags", "+faststart", str(temp_video)])
            temp_video.replace(destination)
        finally:
            temp_video.unlink(missing_ok=True)

    def _execute_ai_diffusion_mode(self, sett: JobSettings, job_dir: Path, out_mp4: Path, progress_cb: Callable):
        if self.queue.is_cancelled(sett.job_id):
            return

        # 1. Prompt Enhancement
        progress_cb(sett.job_id, Stage.ENHANCING, 0.10, "Enhancing prompt with cinematic AI...")
        plan_path = job_dir / "generation_plan.json"
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            enhanced_prompt = plan["enhanced_prompt"]
            base_seed = int(plan["seed"])
            if not isinstance(enhanced_prompt, str) or not enhanced_prompt.strip():
                raise ValueError("Empty saved prompt")
        except (OSError, ValueError, KeyError, TypeError):
            enhanced_prompt = FreeLLMPromptEnhancer.expand_with_ai(sett.prompt, sett.style_preset, self.config)
            base_seed = sett.seed if sett.seed >= 0 else random.randint(1, 999999)
            plan_tmp = plan_path.with_suffix(".json.part")
            plan_tmp.write_text(json.dumps({"enhanced_prompt": enhanced_prompt, "seed": base_seed},
                                           ensure_ascii=False, indent=2), encoding="utf-8")
            plan_tmp.replace(plan_path)
        neg_prompt = PromptEngine.negative(sett.negative_prompt)

        if self.queue.is_cancelled(sett.job_id):
            return

        # 2. Chunk Calculation & Generation (with checkpoint reuse)
        w, h = ASPECT_SIZES.get(sett.aspect_ratio, (832, 480))
        total_chunks = max(1, int(math.ceil(sett.duration / CHUNK_SECONDS)))
        clips: List[Path] = []
        last_frame_path: Optional[str] = None
        backend_used = "unknown"
        used_backends: set[str] = set()
        used_token_fallback = False
        fallback_asset: Optional[Tuple[str, str]] = None
        # Generate at the models' native 16 fps and derive frame COUNT from the
        # requested seconds — the old code left num_frames at the 81 default so
        # every clip came out a fixed (often wrong) length regardless of the
        # duration slider. Interpolation to 60 fps happens later in post.
        gen_fps = WAN_NATIVE_FPS

        for idx in range(total_chunks):
            media_type = "AI video"
            if self.queue.is_cancelled(sett.job_id):
                log.info("Job [%s] cancelled by user.", sett.job_id)
                return

            clip_out = job_dir / f"chunk_{idx:02d}.mp4"
            last_frame = job_dir / f"frame_{idx:02d}.jpg"

            # Checkpoint recovery: If clip already exists from previous session, reuse it
            if clip_out.exists() and ffprobe_duration(clip_out) > 0.5:
                log.info("Checkpoint: Reusing existing clip %s (no re-generation needed)", clip_out.name)
                clips.append(clip_out)
                try:
                    metadata = json.loads((job_dir / f"chunk_{idx:02d}.asset.json").read_text(encoding="utf-8"))
                    used_backends.add(metadata["backend"])
                except (OSError, ValueError, KeyError, TypeError):
                    used_backends.add("unknown (older checkpoint)")
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
            needs_token = False
            for b_name in self.config.backend_order:
                if self.queue.is_cancelled(sett.job_id):
                    return
                # The test pattern is diagnostic only — never let it pre-empt the
                # far better stock-footage / Ken Burns fallback below.
                if b_name == "test_pattern" and not self.config.allow_test_pattern_fallback:
                    continue
                be = self.backends.get(b_name)
                if not be or not be.available() or not be.supports(req):
                    continue
                try:
                    be.generate(req, clip_out, lambda m, _p=chunk_prog: progress_cb(sett.job_id, Stage.GENERATING, _p, m))
                    backend_used = be.name
                    success = True
                    break
                except QuotaExhausted as q:
                    quota_hint = max(quota_hint, int(q.wait_seconds))
                    needs_token = needs_token or quota_needs_token(str(q))
                    log.warning("Backend %s quota-limited: %s", b_name, q)
                except Exception as e:
                    msg = str(e)
                    # NOTE: must not reuse `w` here — it holds the clip width
                    # for the next chunk's GenerationRequest.
                    wait_s = quota_wait_seconds(msg)
                    if wait_s:
                        quota_hint = max(quota_hint, wait_s)
                        needs_token = needs_token or quota_needs_token(msg)
                    elif is_transient_error(msg):
                        transient = True
                    log.warning("Backend %s failed: %s", b_name, e)

            # Quota/network is temporary — defer the whole job and auto-resume so
            # we wait for REAL AI capacity instead of silently downgrading to
            # stock footage. Finished chunks are kept on disk (checkpoint reuse),
            # so the resume costs no extra GPU. Only a genuine backend outage
            # (below) falls through to the cinematic-synthesis fallback.
            #
            # Exception: with no Hugging Face token saved, the anonymous quota
            # is smaller than one generation, so the Spaces answer "sign in"
            # forever. Waiting hours for that cannot work; make the picture-
            # based video now and tell the user how to unlock real AI video.
            no_token_dead_end = not success and bool(quota_hint) and needs_token and not self.config.hf_token.strip()
            if no_token_dead_end and not transient:
                log.warning("Free GPU quota needs a Hugging Face token; using the picture-based fallback.")
                used_token_fallback = True
            elif not success and (quota_hint or transient):
                raise QuotaExhausted(
                    min(quota_hint or 300, self.config.quota_wait_cap_min * 60),
                    reason="Free GPU quota busy" if quota_hint else "Network unavailable",
                )

            if not success:
                # Stock footage or animated still fallback
                log.info("Engaging cinematic visual synthesis fallback for chunk %d...", idx)
                chunk_dur = chunk_secs     # match the AI chunk length so total duration is right
                # Search with the user's own words: stock sites and Wikimedia
                # find nothing for a 60-word styled prompt.
                asset_path, media_type = self.media_fetcher.fetch_scene_visual(
                    sett.prompt, req.width, req.height, prefer_video=True, style=sett.style_preset,
                    previous=fallback_asset,
                )
                fallback_asset = (asset_path, media_type)
                if media_type == "image":
                    motions = ["zoom_in", "zoom_out", "pan_left", "pan_right"]
                    mot = motions[idx % len(motions)]
                    self.video_engine.image_to_video_ken_burns(Path(asset_path), chunk_dur, req.width, req.height, fps=req.fps, motion_type=mot, out_path=clip_out)
                else:
                    run_ffmpeg([
                        "-stream_loop", "-1", "-i", asset_path,
                        "-t", str(chunk_dur),
                        "-vf", f"scale={req.width}:{req.height}:force_original_aspect_ratio=increase,crop={req.width}:{req.height}",
                        *VIDEO_ENCODE_ARGS, str(clip_out)
                    ])
                backend_used = "cinematic_synthesis"
                success = True
            else:
                # AI clips come back in each model's native shape/length.
                try:
                    self.video_engine.conform_clip(clip_out, req.width, req.height, chunk_secs)
                except FFmpegError as exc:
                    log.warning("Clip conform skipped for chunk %d: %s", idx, exc)

            used_backends.add(backend_used)
            asset_record = {
                "chunk": idx + 1, "backend": backend_used, "seed": req.seed,
                "visual_type": media_type if backend_used == "cinematic_synthesis" else "AI video",
                "source_file": fallback_asset[0] if backend_used == "cinematic_synthesis" and fallback_asset else None,
            }
            (job_dir / f"chunk_{idx:02d}.asset.json").write_text(
                json.dumps(asset_record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            clips.append(clip_out)

            # Extract last frame for continuous image-to-video chaining. Optional:
            # a failure here must not fail a job whose clip rendered fine.
            try:
                run_ffmpeg(["-sseof", "-0.5", "-i", str(clip_out), "-q:v", "2", "-update", "1", str(last_frame)])
            except FFmpegError as exc:
                log.warning("Last-frame extraction skipped for chunk %d: %s", idx, exc)
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
                self.video_engine.upscale_video(current_vid, sett.quality, upscaled, sett.aspect_ratio)
                current_vid = upscaled
            else:
                current_vid = upscaled

        if self.queue.is_cancelled(sett.job_id):
            return

        # 6. Audio Narration & Music Ducking
        progress_cb(sett.job_id, Stage.AUDIO, 0.88, "Synthesizing voiceover & mixing music...")
        vo_text = sett.voice_script.strip()
        vo_file: Optional[Path] = None
        events: List[dict] = []
        if vo_text:     # no narration typed -> music only (the prompt is not read aloud)
            vo_file, events = self.audio_engine.generate_voiceover(
                vo_text, sett.language, sett.voice_gender, my_voice=sett.use_my_voice,
                strict_my_voice=True, delivery=sett.narration_delivery)
        video_dur = ffprobe_duration(current_vid)
        vo_dur = ffprobe_duration(vo_file) if vo_file and Path(vo_file).exists() else 0.0
        if vo_text and vo_dur <= 0:
            raise QualityError("The narration service returned unusable audio. Retry the render.")
        if vo_text and not events:
            events = estimate_word_events(vo_text, vo_dur)
        # If the narration runs longer than the footage, hold the last frame so
        # nothing gets cut off (instead of muxing a long audio track onto a short
        # video and leaving them mismatched).
        if vo_dur > video_dur + 0.3:
            extended = job_dir / "extended.mp4"
            self.video_engine.freeze_extend(current_vid, vo_dur + 0.4, extended)
            current_vid = extended
            video_dur = ffprobe_duration(current_vid)
        mixed_audio = job_dir / "mixed_audio.m4a"   # MP4 container: exact duration (raw .aac is estimated)
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
        if sett.subtitles_enabled and events:
            progress_cb(sett.job_id, Stage.SUBTITLES, 0.94, "Burning styled subtitles...")
            burned = job_dir / "with_subs.mp4"
            try:
                self.subtitle_engine.burn_subtitles(current_vid, srt_file, burned, sett.subtitle_style)
                current_vid = burned
            except Exception as e:
                log.warning("Subtitle burning fallback: %s", e)

        self._publish_video(current_vid, out_mp4, sett, job_dir, self._voice_note(sett))

        # Generate Thumbnail
        thumb = job_dir / "thumb.jpg"
        run_ffmpeg(["-ss", "0.5", "-i", str(out_mp4), "-vframes", "1", "-q:v", "2", str(thumb)])

        if used_token_fallback:
            done_msg = ("Includes stock footage or animated stills, because the free AI video quota "
                        "needs a free Hugging Face token. Add one in Settings for real AI video.")
        elif "cinematic_synthesis" in used_backends:
            done_msg = "Includes stock footage or animated stills because AI video services were unavailable. See the render report."
        else:
            done_msg = "Video generated successfully!"
        done_msg += self._voice_note(sett)
        if sett.subtitles_enabled and events and current_vid.name != "with_subs.mp4":
            done_msg += " Captions could not be burned in; download the SRT file."
        progress_cb(
            sett.job_id, Stage.DONE, 1.0, done_msg,
            video_path=str(out_mp4),
            srt_path=str(srt_file),
            thumbnail_path=str(thumb),
            backend_used=", ".join(sorted(used_backends)),
        )

    def _execute_script_mode(self, sett: JobSettings, job_dir: Path, out_mp4: Path, progress_cb: Callable):
        if self.queue.is_cancelled(sett.job_id):
            return

        # 1. Parse Script Lines
        progress_cb(sett.job_id, Stage.ENHANCING, 0.10, "Parsing multi-scene script...")
        script = sett.script_text.strip()
        if not script:
            script = FreeLLMPromptEnhancer.generate_script(sett.prompt, sett.scene_count, sett.language, self.config)

        scenes = self._parse_script_scenes(script)
        if not scenes:
            scenes = [("Visual: " + sett.prompt, "VO: " + sett.prompt)]

        w, h = output_size(sett.aspect_ratio, 1080)
        xfade = SCRIPT_CROSSFADE_SECONDS

        # 2. Process Scenes (with per-scene checkpoint reuse)
        scene_clips: List[Path] = []
        all_events: List[dict] = []
        vo_files: List[Path] = []
        # Audio slot per scene = the time that scene is on screen after the
        # crossfade overlap. Each VO is padded/trimmed to its slot so narration
        # and captions stay in sync with the visuals across the whole video.
        slots: List[float] = []
        current_offset = 0.0
        n_scenes = len(scenes)
        previous_asset: Optional[Tuple[str, str]] = None

        for s_idx, (vis_prompt, vo_prompt) in enumerate(scenes):
            if self.queue.is_cancelled(sett.job_id):
                log.info("Job [%s] cancelled by user.", sett.job_id)
                return

            scene_video = job_dir / f"scene_{s_idx:02d}.mp4"
            vo_path = job_dir / f"vo_scene_{s_idx:02d}.mp3"
            metadata_path = job_dir / f"scene_{s_idx:02d}.json"

            # Checkpoint recovery: If scene already rendered, skip generation
            if scene_video.exists() and ffprobe_duration(scene_video) > 0.5 and vo_path.exists():
                checkpoint = self._read_scene_checkpoint(metadata_path)
                if checkpoint is not None:
                    events, vo_dur = checkpoint
                    log.info("Checkpoint: Reusing rendered scene %s for job %s", scene_video.name, sett.job_id)
                    scene_clips.append(scene_video)
                    vo_files.append(vo_path)
                    all_events.extend(
                        {
                            "text": ev["text"],
                            "offset": ev["offset"] + current_offset,
                            "duration": ev["duration"],
                        }
                        for ev in events
                    )
                    slot = scene_slot_seconds(vo_dur, s_idx, n_scenes, xfade)
                    slots.append(slot)
                    current_offset += slot
                    continue
                log.warning(
                    "Scene checkpoint metadata missing or invalid for %s; regenerating scene",
                    scene_video.name,
                )

            prog = 0.20 + 0.45 * (s_idx / len(scenes))
            progress_cb(sett.job_id, Stage.GENERATING, prog, f"Processing scene {s_idx+1}/{len(scenes)}...")

            # Audio VO for scene
            vo_path_gen, events = self.audio_engine.generate_voiceover(
                vo_prompt, sett.language, sett.voice_gender, my_voice=sett.use_my_voice,
                strict_my_voice=True, delivery=sett.narration_delivery)
            if vo_path_gen.exists():
                shutil.copyfile(vo_path_gen, vo_path)
            vo_dur = ffprobe_duration(vo_path)
            if vo_dur <= 0:
                raise QualityError(f"Scene {s_idx + 1} has no usable narration. Retry the render.")
            scene_dur = scene_video_seconds(vo_dur)
            if not events:
                events = estimate_word_events(vo_prompt, vo_dur)

            # Adjust event offsets for full video timeline
            for ev in events:
                all_events.append({
                    "text": ev["text"],
                    "offset": ev["offset"] + current_offset,
                    "duration": ev["duration"]
                })
            vo_files.append(vo_path)

            # Fetch visual asset
            asset_path, media_type = self.media_fetcher.fetch_scene_visual(
                vis_prompt, w, h, prefer_video=True, style=sett.style_preset)
            reused_previous = previous_asset == (asset_path, media_type)
            previous_asset = (asset_path, media_type)
            (job_dir / f"scene_{s_idx:02d}.asset.json").write_text(json.dumps({
                "scene": s_idx + 1, "visual_prompt": vis_prompt,
                "visual_type": media_type, "source_file": asset_path, "reused_previous": reused_previous,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

            # Build Scene Video
            if media_type == "image":
                motion = ["zoom_in", "pan_right", "zoom_out", "pan_left"][s_idx % 4]
                self.video_engine.image_to_video_ken_burns(Path(asset_path), scene_dur, w, h, fps=sett.fps, motion_type=motion, out_path=scene_video)
            else: # video
                run_ffmpeg([
                    "-stream_loop", "-1", "-i", asset_path,
                    "-t", str(scene_dur),
                    "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
                    *VIDEO_ENCODE_ARGS, str(scene_video)
                ])

            actual_scene_duration = ffprobe_duration(scene_video)
            if actual_scene_duration <= 0.5:
                raise RuntimeError(f"Scene render produced an invalid video: {scene_video}")
            slot = scene_slot_seconds(vo_dur, s_idx, n_scenes, xfade)
            slots.append(slot)
            current_offset += slot
            self._write_scene_checkpoint(
                metadata_path,
                events,
                vo_dur,
            )
            scene_clips.append(scene_video)

        if self.queue.is_cancelled(sett.job_id):
            return

        # 3. Stitch Scenes
        progress_cb(sett.job_id, Stage.STITCHING, 0.70, "Stitching scenes...")
        stitched = job_dir / "script_stitched.mp4"
        if not (stitched.exists() and ffprobe_duration(stitched) > 0.5):
            self.video_engine.crossfade_concat(scene_clips, stitched, crossfade=xfade, size=(w, h), fps=sett.fps)

        # 4. Mix Full Audio Timeline
        progress_cb(sett.job_id, Stage.AUDIO, 0.85, "Mixing multilingual audio and music...")
        tot_dur = ffprobe_duration(stitched)
        # Concatenate per-scene VO files into one track (guard against none).
        full_vo: Optional[Path] = None
        if vo_files and all(Path(vf).exists() for vf in vo_files):
            full_vo = job_dir / "full_vo.mp3"
            vo_inputs: List[str] = []
            for vf in vo_files:
                vo_inputs.extend(["-i", str(vf)])
            run_ffmpeg([*vo_inputs, "-filter_complex", vo_timeline_filter(slots), "-map", "[outa]",
                        "-c:a", "libmp3lame", str(full_vo)])

        mixed_audio = job_dir / "final_mixed_audio.m4a"
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
            try:
                self.subtitle_engine.burn_subtitles(current_vid, srt_file, burned, sett.subtitle_style)
                current_vid = burned
            except FFmpegError as e:
                # Same policy as AI mode: keep the narrated video, ship the .srt.
                log.warning("Subtitle burning skipped: %s", e)

        self._publish_video(current_vid, out_mp4, sett, job_dir, self._voice_note(sett))

        thumb = job_dir / "thumb.jpg"
        run_ffmpeg(["-ss", "0.5", "-i", str(out_mp4), "-vframes", "1", "-q:v", "2", str(thumb)])

        subtitle_note = (" Captions could not be burned in; download the SRT file."
                         if sett.subtitles_enabled and current_vid.name != "burned.mp4" else "")
        progress_cb(
            sett.job_id, Stage.DONE, 1.0, "Storyboard video generated successfully!" + self._voice_note(sett) + subtitle_note,
            video_path=str(out_mp4),
            srt_path=str(srt_file),
            thumbnail_path=str(thumb),
            backend_used="script_story_engine",
        )

    @staticmethod
    def _read_scene_checkpoint(path: Path) -> Optional[Tuple[List[dict], float]]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            events = data["events"]
            duration = float(data["voiceover_duration"])
            if not isinstance(events, list) or not math.isfinite(duration) or duration < 0:
                return None
            validated_events = []
            for event in events:
                if not isinstance(event, dict) or not isinstance(event.get("text"), str):
                    return None
                offset = float(event["offset"])
                event_duration = float(event["duration"])
                if (
                    not math.isfinite(offset)
                    or not math.isfinite(event_duration)
                    or offset < 0
                    or event_duration < 0
                ):
                    return None
                validated_events.append(
                    {
                        "text": event["text"],
                        "offset": offset,
                        "duration": event_duration,
                    }
                )
            return validated_events, duration
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            return None

    @staticmethod
    def _write_scene_checkpoint(path: Path, events: List[dict], voiceover_duration: float) -> None:
        payload = json.dumps(
            {"events": events, "voiceover_duration": voiceover_duration},
            ensure_ascii=False,
        )
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(payload, encoding="utf-8")
        temp_path.replace(path)

    def _parse_script_scenes(self, script: str) -> List[Tuple[str, str]]:
        """Parse ``Visual:`` / ``VO:`` lines into (visual, voiceover) scenes.

        Line-based so scenes do not need blank lines between them (the built-in
        templates and most LLM output put them on consecutive lines). A new
        ``Visual:`` line, a second ``VO:`` line or a blank line closes the
        current scene; unlabeled lines continue the previous field.
        """
        scenes: List[Tuple[str, str]] = []
        vis: List[str] = []
        vo: List[str] = []
        current: Optional[List[str]] = None

        def flush() -> None:
            v, n = " ".join(vis).strip(), " ".join(vo).strip()
            if v or n:
                scenes.append((v or "cinematic scenery", n or v))
            vis.clear()
            vo.clear()

        # Accepts "Visual:", "**Visual:**", "**Visual**:", "1. VO:", "- Narration:" ...
        label = re.compile(
            r"^\s*(?:[-*\d.)\s]*)?(?:\*\*)?(visual|vo|voiceover|narration)(?:\*\*)?\s*:\s*(?:\*\*)?\s*(.*)$",
            re.IGNORECASE,
        )
        for raw in script.strip().splitlines():
            line = raw.strip()
            if not line:
                flush()
                current = None
                continue
            m = label.match(line)
            if m:
                kind, text = m.group(1).lower(), m.group(2).strip()
                if kind == "visual":
                    if vis or vo:
                        flush()
                    current = vis
                else:
                    if vo:
                        flush()
                    current = vo
                if text:
                    current.append(text)
            elif current is not None:
                current.append(line)
        flush()
        return scenes


# ----------------------------------------------------------------------------
# Main Execution Entrypoint
# ----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="VideoStudio Pro — Unified AI Video Creation Studio")
    parser.add_argument("--web", action="store_true", help="Launch Gradio Web Studio")
    parser.add_argument("--probe", action="store_true", help="Run hardware diagnostics probe")
    args = parser.parse_args()

    if args.probe:
        studio = VideoStudio()
        print("\n" + "=" * 60)
        print("🎬 VideoStudio Pro Hardware & Environment Diagnostics")
        print("=" * 60)
        print(studio.probe.summary())
        print(f"Output Directory: {DEFAULT_OUTPUT_DIR}")
        print("Backends Active: " + ", ".join(studio.config.backend_order))
        print("=" * 60 + "\n")
        studio.queue.shutdown()
        return

    # Web path: app.py creates its own single VideoStudio at import time.
    # Do NOT build one here too, or the worker lock would make app's studio
    # view-only (and jobs would never process).
    import app
    app.launch_app()

if __name__ == "__main__":
    main()
