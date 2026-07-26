"""
video_studio.py — Consolidated AI Video Generation Studio backend.

One file, every layer:

  1. Environment & hardware   — HardwareProbe
  2. Generation backends      — HFSpaceBackend (free ZeroGPU), ColabBackend (free T4),
                                TestPatternBackend (offline pipeline verification)
  3. Prompt engine            — PromptEngine (cinematic enhancement, style presets,
                                negative-prompt injection)
  4. Generation pipeline      — chunked long-video mode, crossfade stitching,
                                ffmpeg motion interpolation, upscaling
  5. Audio & subtitles        — edge-tts voiceover (7+ languages), music ducking,
                                word-boundary-accurate SRT + burned-in subtitles,
                                free translation
  6. Continuous batch queue   — persistent, crash-recovering JobQueue
  7. Engineering layer        — StudioConfig dataclass, structured logging,
                                progress callbacks, graceful shutdown

Hardware reality on this machine (Intel Iris Xe, 7.7 GB RAM, CPU-only torch):
local Wan2.1 inference is impossible — generation is delegated to free cloud
GPUs (Hugging Face ZeroGPU Spaces and/or a Google Colab worker started from
colab_worker.ipynb). Everything else runs locally.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import logging.handlers
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

# Windows consoles default to cp1252 and crash on emoji/unicode in logs and
# status text. Force UTF-8 on our streams (no-op where already UTF-8).
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                            # noqa: BLE001
            pass

# ----------------------------------------------------------------------------
# Paths & constants (no hard-coded absolute paths anywhere)
# ----------------------------------------------------------------------------

STUDIO_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = STUDIO_ROOT / "outputs"
CONFIG_PATH = STUDIO_ROOT / "studio_config.json"
MUSIC_DIR = STUDIO_ROOT / "music"          # drop .mp3 files named <mood>.mp3 here

WAN_NATIVE_FPS = 16                        # Wan2.1 generates at 16 fps
CHUNK_SECONDS = 5.0                        # native clip length per generation call
CROSSFADE_SECONDS = 0.5

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

def _build_logger() -> logging.Logger:
    logger = logging.getLogger("video_studio")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%H:%M:%S"
    )
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)
    log_dir = DEFAULT_OUTPUT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fileh = logging.handlers.RotatingFileHandler(
        log_dir / "studio.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    fileh.setLevel(logging.DEBUG)
    fileh.setFormatter(fmt)
    logger.addHandler(fileh)
    return logger


log = _build_logger()

# ----------------------------------------------------------------------------
# ffmpeg helper
# ----------------------------------------------------------------------------

def ffmpeg_exe() -> str:
    """Locate an ffmpeg binary: system PATH first, then imageio-ffmpeg's bundled one."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args: list[str], timeout: int = 1800) -> None:
    """Run ffmpeg with args (excluding the binary itself); raise on failure."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {proc.stderr[-2000:]}")


def ffprobe_duration(path: Path) -> float:
    """Video/audio duration in seconds via ffprobe (falls back to ffmpeg parse)."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        proc = subprocess.run(
            [ffprobe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True,
        )
        try:
            return float(proc.stdout.strip())
        except ValueError:
            pass
    # Fallback: parse "Duration: HH:MM:SS.xx" from ffmpeg stderr
    proc = subprocess.run([ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", proc.stderr)
    if not m:
        return 0.0
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

@dataclass
class StudioConfig:
    """Single source of configuration for the whole studio."""

    output_dir: str = str(DEFAULT_OUTPUT_DIR)
    hf_token: str = ""                       # free huggingface.co token → ZeroGPU quota
    hf_spaces: list[str] = field(default_factory=lambda: [
        "Wan-AI/Wan2.1",                     # official Wan space
    ])
    colab_url: str = ""                      # *.gradio.live URL printed by colab_worker.ipynb
    backend_order: list[str] = field(default_factory=lambda: [
        "colab", "ltx", "cogvideox", "wan_official", "test_pattern",
    ])
    quota_wait_cap_min: int = 45             # max minutes a deferred job waits per round
    max_quota_waits_per_job: int = 2         # short in-process waits before deferring
    max_deferrals_per_job: int = 48          # deferrals are cheap (progress is kept);
    # counter resets whenever a clip lands, so this only stops truly-stuck jobs
    wan_cooldown_hours: float = 3.0          # after a Wan queue timeout, don't ride
    # the 40-min queue again for this long — quick LTX/Cog checks + defer instead
    allow_test_pattern_fallback: bool = False  # never fake AI output by default;
    # quota-blocked jobs defer and auto-resume instead
    default_steps: int = 30
    default_guidance: float = 6.0
    max_retries: int = 1

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.write_text(json.dumps(dataclasses.asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "StudioConfig":
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                known = {f.name for f in dataclasses.fields(cls)}
                cfg = cls(**{k: v for k, v in data.items() if k in known})
                # Migration: configs saved before new backends existed must
                # still try them — insert any missing backend before the
                # test-pattern fallback.
                for be in ("colab", "ltx", "cogvideox", "wan_official"):
                    if be not in cfg.backend_order:
                        idx = (cfg.backend_order.index("test_pattern")
                               if "test_pattern" in cfg.backend_order
                               else len(cfg.backend_order))
                        cfg.backend_order.insert(idx, be)
                return cfg
            except Exception as exc:                                  # noqa: BLE001
                log.warning("Config load failed (%s); using defaults", exc)
        return cls()


# ----------------------------------------------------------------------------
# 1. Environment & hardware layer
# ----------------------------------------------------------------------------

class HardwareProbe:
    """Detects GPU/CPU/RAM/disk and answers what this machine can do locally."""

    def __init__(self) -> None:
        self.has_cuda = False
        self.vram_gb = 0.0
        self.gpu_name = "none"
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
        except Exception:                                             # noqa: BLE001
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
        except Exception:                                             # noqa: BLE001
            pass
        try:
            self.free_disk_gb = shutil.disk_usage(STUDIO_ROOT).free / 1e9
        except Exception:                                             # noqa: BLE001
            pass

    @property
    def can_run_wan_locally(self) -> bool:
        """Wan2.1-T2V-1.3B needs ~8 GB CUDA VRAM and ~20 GB disk for weights."""
        return self.has_cuda and self.vram_gb >= 8 and self.free_disk_gb >= 25

    def dtype_choice(self) -> str:
        if not self.has_cuda:
            return "float32"
        try:
            import torch
            return "bfloat16" if torch.cuda.is_bf16_supported() else "float16"
        except Exception:                                             # noqa: BLE001
            return "float16"

    def vram_warning(self, quality: str) -> str:
        """Human warning for a requested quality tier."""
        needs = {"480p": 8, "720p": 16, "1080p": 16, "4K": 24}
        if self.can_run_wan_locally and self.vram_gb < needs.get(quality, 8):
            return (f"⚠️ Local GPU has {self.vram_gb:.0f} GB VRAM — {quality} native is "
                    f"unlikely; will be generated at 480p and upscaled.")
        if not self.can_run_wan_locally:
            return (f"☁️ No local CUDA GPU ({self.gpu_name}) — generation runs on free "
                    f"cloud GPUs; {quality} above 480p/720p is achieved by upscaling.")
        return ""

    def summary(self) -> str:
        return (f"GPU: {self.gpu_name} ({self.vram_gb:.1f} GB VRAM) | CUDA: {self.has_cuda} | "
                f"RAM: {self.ram_gb:.1f} GB | Free disk: {self.free_disk_gb:.1f} GB | "
                f"Local Wan2.1 capable: {self.can_run_wan_locally}")


# ----------------------------------------------------------------------------
# 3. Prompt engine
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
        "prefix": "Cinematic film still in motion,",
        "camera": "slow dolly-in on a 35mm anamorphic lens, shallow depth of field",
        "light": "dramatic golden-hour key light with soft rim lighting, volumetric haze",
        "grade": "teal-and-orange color grade, filmic contrast, subtle 35mm grain",
        "mood": "epic, emotionally charged atmosphere",
    },
    "Documentary": {
        "prefix": "Documentary footage,",
        "camera": "handheld tracking shot on a 24mm lens, natural framing",
        "light": "available natural light, true-to-life exposure",
        "grade": "neutral realistic color grade, high dynamic range",
        "mood": "authentic, observational tone",
    },
    "Anime": {
        "prefix": "High-quality anime scene,",
        "camera": "dynamic sweeping camera pan with dramatic perspective lines",
        "light": "vibrant cel-shaded lighting, glowing highlights",
        "grade": "rich saturated palette, crisp line art, studio-quality animation",
        "mood": "expressive, energetic atmosphere",
    },
    "Hyper-realistic": {
        "prefix": "Ultra photorealistic footage,",
        "camera": "locked-off tripod shot on an 85mm prime lens, razor-sharp focus",
        "light": "physically accurate global illumination, soft studio lighting",
        "grade": "true-to-life color science, 8K-detail textures, no stylization",
        "mood": "lifelike, tangible realism",
    },
    "Commercial/Ad": {
        "prefix": "Premium commercial advertisement shot,",
        "camera": "smooth gimbal orbit with macro detail inserts, product-hero framing",
        "light": "polished high-key studio lighting, specular highlights",
        "grade": "clean vibrant grade, glossy finish, immaculate styling",
        "mood": "aspirational, premium brand feel",
    },
    "Drone/Aerial": {
        "prefix": "Breathtaking aerial drone footage,",
        "camera": "high-altitude forward flight with a slow gimbal tilt-down, wide 14mm lens",
        "light": "crisp daylight with long shadows, atmospheric depth",
        "grade": "vivid landscape grade, deep blue skies, HDR clarity",
        "mood": "vast, awe-inspiring scale",
    },
    "Slow-motion": {
        "prefix": "Ultra slow-motion footage,",
        "camera": "1000fps high-speed camera feel, macro-level detail on motion",
        "light": "strong directional lighting freezing every particle and droplet",
        "grade": "high-contrast dramatic grade, crystal-clear motion detail",
        "mood": "mesmerizing, suspended-in-time feeling",
    },
    "Vlog": {
        "prefix": "Casual vlog-style footage,",
        "camera": "chest-height handheld selfie-stick framing, wide 18mm lens",
        "light": "bright soft daylight, flattering skin tones",
        "grade": "warm lifestyle grade, light film emulation",
        "mood": "friendly, personal, upbeat energy",
    },
}

_MOTION_HINTS = [
    "fluid natural motion", "smooth coherent movement", "consistent object permanence",
    "physically plausible dynamics", "seamless continuous action",
]


class PromptEngine:
    """Deterministic rule/template prompt enhancer (no paid LLM calls).

    Expands a short user prompt into a rich cinematic prompt with camera,
    lens, lighting, grade, motion and mood language — the same axes premium
    T2V frontends inject. Seeded randomness keeps variety reproducible.
    """

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
            "Highly detailed, coherent scene geometry, professional composition.",
        ]
        return " ".join(parts)

    @staticmethod
    def negative(user_negative: str = "") -> str:
        extra = user_negative.strip()
        return f"{DEFAULT_NEGATIVE}, {extra}" if extra else DEFAULT_NEGATIVE


# ----------------------------------------------------------------------------
# Generation request/result plumbing
# ----------------------------------------------------------------------------

ASPECT_SIZES_480 = {   # Wan2.1 1.3B native buckets at 480p tier
    "16:9": (832, 480),
    "9:16": (480, 832),
    "1:1": (624, 624),
    "21:9": (960, 416),
    "4:3": (704, 544),
}

QUALITY_TIERS = ["480p", "720p", "1080p", "4K"]


@dataclass
class GenerationRequest:
    prompt: str
    negative_prompt: str = DEFAULT_NEGATIVE
    width: int = 832
    height: int = 480
    num_frames: int = 81                    # 4n+1, 81 ≈ 5s @ 16fps
    steps: int = 30
    guidance: float = 6.0
    seed: int = -1                          # -1 → random
    fps: int = WAN_NATIVE_FPS
    init_image: Optional[str] = None        # last frame of previous chunk →
                                            # image-conditioned continuation


def quota_wait_seconds(error_text: str) -> Optional[int]:
    """If the error is a ZeroGPU quota limit, return seconds until retry.

    ZeroGPU errors look like: 'You have exceeded your ZeroGPU quota
    (120s requested vs. -60s left). Try again in 0:14:56.' The 'Try again'
    hint is often 0:00:00 while the balance is still negative, so we also
    derive the wait from the deficit (requested − left) — the rolling window
    must replenish at least that much. Returns None for non-quota errors.
    """
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


class QuotaExhausted(RuntimeError):
    """Backends temporarily unreachable (quota/network); defer and resume."""

    def __init__(self, wait_seconds: int, reason: str = "Free GPU busy") -> None:
        super().__init__(f"{reason} — retry in {wait_seconds}s")
        self.wait_seconds = wait_seconds
        self.reason = reason


_TRANSIENT_MARKERS = (
    "getaddrinfo failed",        # DNS / internet down (WinError 11001)
    "connection", "reset by peer", "temporarily unavailable",
    "502", "503", "504", "read timed out", "network is unreachable",
)


def is_transient_error(error_text: str) -> bool:
    """True for network-ish failures that deserve a defer-and-retry, not a fail."""
    low = error_text.lower()
    return any(m in low for m in _TRANSIENT_MARKERS)


class GenerationBackend:
    """Abstract backend: turn a GenerationRequest into a short mp4 clip."""

    name = "abstract"
    description = ""
    supports_image_conditioning = False     # can chain chunks via init_image

    def available(self) -> bool:
        raise NotImplementedError

    def generate(self, req: GenerationRequest, out_path: Path,
                 progress: Callable[[str], None] = lambda m: None) -> Path:
        raise NotImplementedError


class ColabBackend(GenerationBackend):
    """Free Google Colab T4 worker (started via colab_worker.ipynb).

    The notebook launches a tiny gradio app with share=True and prints a
    *.gradio.live URL; paste it into Settings. Both sides are authored here,
    so the API contract (/generate) is fixed and reliable.
    """

    name = "colab"
    description = "Free Colab T4 GPU worker (colab_worker.ipynb)"

    def __init__(self, config: StudioConfig) -> None:
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

    def generate(self, req: GenerationRequest, out_path: Path,
                 progress: Callable[[str], None] = lambda m: None) -> Path:
        progress(f"Colab worker: generating {req.width}x{req.height}, "
                 f"{req.num_frames} frames, {req.steps} steps…")
        client = self._get_client()
        result = client.predict(
            req.prompt, req.negative_prompt, req.width, req.height,
            req.num_frames, req.steps, req.guidance, req.seed,
            api_name="/generate",
        )
        video_path = result["video"] if isinstance(result, dict) else result
        if isinstance(video_path, dict):      # gradio VideoData
            video_path = video_path.get("video") or video_path.get("path")
        shutil.copyfile(str(video_path), out_path)
        return out_path


class LTXSpaceBackend(GenerationBackend):
    """LTX-Video (Lightricks) on a free HF ZeroGPU Space.

    Verified to work ANONYMOUSLY — no account, no token, no setup. This is the
    default real-AI backend. A free HF token (Settings) raises the daily quota.
    Distilled model: guidance is fixed at 1.0 (higher values degrade output),
    clips up to ~8 s, 30 fps output.
    """

    name = "ltx"
    description = "Free LTX-Video AI (no signup needed) — HF ZeroGPU"
    SPACE = "Lightricks/ltx-video-distilled"
    MAX_SECONDS = 8.0
    supports_image_conditioning = True      # /image_to_video endpoint

    def __init__(self, config: StudioConfig) -> None:
        self.config = config
        self._client: Any = None

    def available(self) -> bool:
        return True

    def _get_client(self) -> Any:
        from gradio_client import Client
        if self._client is None:
            token = self.config.hf_token.strip() or None
            try:
                self._client = Client(self.SPACE, hf_token=token, verbose=False)
            except TypeError:
                self._client = Client(self.SPACE, verbose=False)
        return self._client

    @staticmethod
    def _snap32(v: int) -> int:
        return max((v // 32) * 32, 256)

    def generate(self, req: GenerationRequest, out_path: Path,
                 progress: Callable[[str], None] = lambda m: None) -> Path:
        w, h = self._snap32(req.width), self._snap32(req.height)
        seconds = min(max(req.num_frames / req.fps, 1.0), self.MAX_SECONDS)
        client = self._get_client()
        common = dict(
            prompt=req.prompt, negative_prompt=req.negative_prompt,
            input_video_filepath=None,
            height_ui=h, width_ui=w,
            duration_ui=round(seconds, 1), ui_frames_to_use=9,
            seed_ui=req.seed if req.seed >= 0 else 42,
            randomize_seed=(req.seed < 0),
            ui_guidance_scale=1.0,            # distilled model requirement
            improve_texture_flag=True,
        )
        if req.init_image:
            # Continuation chunk: condition on the previous chunk's last frame
            # for true scene continuity across a long video.
            from gradio_client import handle_file
            progress(f"LTX-Video: continuing scene {w}x{h}, {seconds:.1f}s…")
            result = client.predict(
                input_image_filepath=handle_file(req.init_image),
                mode="image-to-video",
                api_name="/image_to_video", **common,
            )
        else:
            progress(f"LTX-Video: generating {w}x{h}, {seconds:.1f}s…")
            result = client.predict(
                input_image_filepath=None,
                mode="text-to-video",
                api_name="/text_to_video", **common,
            )
        path = HFSpaceBackend._extract_video(result)
        if not path:
            raise RuntimeError(f"LTX returned no video: {str(result)[:200]}")
        shutil.copyfile(path, out_path)
        return out_path


class CogVideoXBackend(GenerationBackend):
    """CogVideoX-5B on a free HF ZeroGPU Space (fallback — slower, 720x480).

    Fixed output size; aspect is handled downstream by the studio's
    post-processing. RIFE interpolation on the space is enabled for smoother
    motion.
    """

    name = "cogvideox"
    description = "Free CogVideoX-5B AI (fallback) — HF ZeroGPU"
    SPACE = "THUDM/CogVideoX-5B-Space"

    def __init__(self, config: StudioConfig) -> None:
        self.config = config
        self._client: Any = None

    def available(self) -> bool:
        return True

    def _get_client(self) -> Any:
        from gradio_client import Client
        if self._client is None:
            token = self.config.hf_token.strip() or None
            try:
                self._client = Client(self.SPACE, hf_token=token, verbose=False)
            except TypeError:
                self._client = Client(self.SPACE, verbose=False)
        return self._client

    def generate(self, req: GenerationRequest, out_path: Path,
                 progress: Callable[[str], None] = lambda m: None) -> Path:
        progress("CogVideoX-5B: generating (720x480, ~6s, can take minutes)…")
        client = self._get_client()
        result = client.predict(
            prompt=req.prompt, image_input=None, video_input=None,
            video_strength=0.8,
            seed_value=req.seed if req.seed >= 0 else -1,
            scale_status=False, rife_status=True,
            api_name="/generate",
        )
        path = HFSpaceBackend._extract_video(result)
        if not path:
            raise RuntimeError(f"CogVideoX returned no video: {str(result)[:200]}")
        shutil.copyfile(path, out_path)
        return out_path


class WanOfficialBackend(GenerationBackend):
    """Official Wan2.1-14B Space (Alibaba-hosted free queue, 720p).

    Independent of ZeroGPU quota — works anonymously. Uses the space's async
    protocol: submit via /t2v_generation_async, then poll /status_refresh on
    the SAME client session until the video appears. Slow (public queue,
    5-20 min) but the highest-quality free source. Verified live 2026-07-03.
    """

    name = "wan_official"
    description = "Free official Wan2.1-14B 720p (slow public queue)"
    SPACE = "Wan-AI/Wan2.1"
    SIZES = ["1280*720", "960*960", "720*1280", "1088*832", "832*1088"]
    POLL_SECONDS = 12
    MAX_WAIT = 2400          # public queue ETA oscillates; 25 min was too tight

    def __init__(self, config: StudioConfig) -> None:
        self.config = config
        self.cooldown_until = 0.0

    def available(self) -> bool:
        return True

    def _pick_size(self, req: GenerationRequest) -> str:
        ratio = req.width / max(req.height, 1)
        def size_ratio(s: str) -> float:
            sw, sh = s.split("*")
            return int(sw) / int(sh)
        return min(self.SIZES, key=lambda s: abs(size_ratio(s) - ratio))

    def generate(self, req: GenerationRequest, out_path: Path,
                 progress: Callable[[str], None] = lambda m: None) -> Path:
        from gradio_client import Client
        if time.time() < self.cooldown_until:
            # Empirically the public queue times out for long stretches of the
            # day; riding it for 40 min per retry cycle starves the whole
            # queue. Fail fast while cooling down — deferral handles patience.
            raise RuntimeError(
                f"Wan official cooling down until "
                f"{time.strftime('%H:%M', time.localtime(self.cooldown_until))} "
                f"after a queue timeout")
        size = self._pick_size(req)
        progress(f"Wan2.1-14B official: submitting to free queue ({size})…")
        client = Client(self.SPACE, verbose=False)   # fresh session per job —
        # status_refresh is session-scoped
        client.predict(req.prompt, size, False,
                       float(req.seed) if req.seed >= 0 else -1.0,
                       api_name="/t2v_generation_async")
        deadline = time.time() + self.MAX_WAIT
        poll_failures = 0
        while time.time() < deadline:
            time.sleep(self.POLL_SECONDS)
            try:
                status = client.predict(api_name="/status_refresh")
                poll_failures = 0
            except Exception as exc:                                  # noqa: BLE001
                # One network hiccup must not throw away our queue position —
                # keep polling unless the connection stays down for ~2 minutes.
                poll_failures += 1
                if poll_failures >= 10:
                    raise RuntimeError(
                        f"Wan official: connection lost while polling: {exc}")
                progress(f"Wan2.1-14B official: connection hiccup "
                         f"({poll_failures}/10), retrying…")
                continue
            video = HFSpaceBackend._extract_video(status)
            if video:
                shutil.copyfile(video, out_path)
                return out_path
            eta = ""
            try:
                if (isinstance(status, (list, tuple)) and len(status) >= 3
                        and isinstance(status[2], (int, float)) and status[2] > 0):
                    e = int(status[2])
                    eta = f", ~{e // 60}m{e % 60:02d}s left"
            except Exception:                                         # noqa: BLE001
                pass
            progress(f"Wan2.1-14B official: in free queue{eta}…")
        self.cooldown_until = time.time() + self.config.wan_cooldown_hours * 3600
        raise RuntimeError("Wan official queue timed out (40 min)")


class HFSpaceBackend(GenerationBackend):
    """Free Hugging Face ZeroGPU Spaces running Wan models.

    Space APIs vary and change; this adapter introspects the space API and
    maps our request onto the closest matching parameters. A free HF token
    (Settings) grants ZeroGPU quota; anonymous calls usually get queued or
    rejected. Failure of one space falls through to the next.
    """

    name = "hf_space"
    description = "Free Hugging Face ZeroGPU Space (needs free HF token)"

    _PARAM_MAP = {
        "prompt": ["prompt", "text", "positive_prompt"],
        "negative": ["negative_prompt", "n_prompt", "negative"],
        "steps": ["steps", "num_inference_steps", "sample_steps", "sampling_steps"],
        "guidance": ["guidance_scale", "guide_scale", "cfg_scale", "cfg"],
        "seed": ["seed"],
        "size": ["size", "resolution"],
        "width": ["width"],
        "height": ["height"],
        "frames": ["num_frames", "frame_num", "frames", "video_length"],
        "duration": ["duration", "duration_seconds"],
    }

    def __init__(self, config: StudioConfig) -> None:
        self.config = config

    def available(self) -> bool:
        return bool(self.config.hf_spaces)

    def _call_space(self, space: str, req: GenerationRequest,
                    progress: Callable[[str], None]) -> Optional[str]:
        from gradio_client import Client
        token = self.config.hf_token.strip() or None
        # gradio_client renamed/removed this kwarg across versions; try the
        # token-aware form, then fall back to a plain client.
        try:
            client = Client(space, hf_token=token, verbose=False)
        except TypeError:
            os.environ.setdefault("HF_TOKEN", token or "")
            client = Client(space, verbose=False)
        api = client.view_api(return_format="dict", print_info=False)
        endpoints = {**api.get("named_endpoints", {})}
        for ep_name, ep in endpoints.items():
            returns = json.dumps(ep.get("returns", [])).lower()
            if "video" not in returns:
                continue
            kwargs: dict[str, Any] = {}
            for p in ep.get("parameters", []):
                pname = (p.get("parameter_name") or "").lower()
                default = p.get("parameter_default")
                matched = False
                for key, aliases in self._PARAM_MAP.items():
                    if pname in aliases:
                        matched = True
                        if key == "prompt":
                            kwargs[pname] = req.prompt
                        elif key == "negative":
                            kwargs[pname] = req.negative_prompt
                        elif key == "steps":
                            kwargs[pname] = req.steps
                        elif key == "guidance":
                            kwargs[pname] = req.guidance
                        elif key == "seed":
                            kwargs[pname] = req.seed if req.seed >= 0 else 0
                        elif key == "size":
                            kwargs[pname] = f"{req.width}*{req.height}"
                        elif key == "width":
                            kwargs[pname] = req.width
                        elif key == "height":
                            kwargs[pname] = req.height
                        elif key == "frames":
                            kwargs[pname] = req.num_frames
                        elif key == "duration":
                            kwargs[pname] = req.num_frames / req.fps
                        break
                if not matched and default is not None:
                    kwargs[pname] = default
            if not any(k in kwargs for k in self._PARAM_MAP["prompt"]):
                continue
            progress(f"HF Space {space}: calling {ep_name}…")
            result = client.predict(api_name=ep_name, **kwargs)
            path = self._extract_video(result)
            if path:
                return path
        return None

    @staticmethod
    def _extract_video(result: Any) -> Optional[str]:
        """Recursively hunt for a video file path in any nested result shape."""
        if isinstance(result, str):
            return (result if result.lower().endswith((".mp4", ".webm", ".mov"))
                    else None)
        if isinstance(result, dict):
            for key in ("video", "path", "name", "value"):
                found = HFSpaceBackend._extract_video(result.get(key))
                if found:
                    return found
            return None
        if isinstance(result, (list, tuple)):
            for item in result:
                found = HFSpaceBackend._extract_video(item)
                if found:
                    return found
        return None

    def generate(self, req: GenerationRequest, out_path: Path,
                 progress: Callable[[str], None] = lambda m: None) -> Path:
        last_error = "no space produced a video"
        for space in self.config.hf_spaces:
            try:
                path = self._call_space(space, req, progress)
                if path:
                    shutil.copyfile(path, out_path)
                    return out_path
                last_error = f"{space}: no compatible video endpoint / empty result"
            except Exception as exc:                                  # noqa: BLE001
                last_error = f"{space}: {type(exc).__name__}: {str(exc)[:160]}"
                log.warning("HF space %s failed: %s", space, exc)
        raise RuntimeError(f"All HF spaces failed ({last_error})")


class TestPatternBackend(GenerationBackend):
    """Offline synthetic backend — animated gradient + prompt text via ffmpeg.

    Exists so the ENTIRE pipeline (queue, stitching, interpolation, upscale,
    audio, subtitles, gallery) can be exercised and verified with zero GPU and
    zero network. Clearly labeled; never pretends to be AI output.
    """

    name = "test_pattern"
    description = "Offline synthetic test clips (pipeline verification only)"

    def available(self) -> bool:
        return True

    def generate(self, req: GenerationRequest, out_path: Path,
                 progress: Callable[[str], None] = lambda m: None) -> Path:
        progress("Test pattern: rendering synthetic clip…")
        seconds = req.num_frames / req.fps
        seed = req.seed if req.seed >= 0 else random.randint(0, 9999)
        label = re.sub(r"[^a-zA-Z0-9 .\-]", "", req.prompt)[:60] or "test"
        base_args = [
            "-f", "lavfi",
            "-i", f"gradients=size={req.width}x{req.height}:rate={req.fps}:"
                  f"speed=0.05:seed={seed}",
            "-t", f"{seconds:.2f}",
        ]
        tail = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                str(out_path)]
        font = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "Fonts" / "arial.ttf"
        if font.exists():
            font_esc = str(font).replace("\\", "/").replace(":", "\\:")
            # Loud, unmistakable watermark: this clip is NOT AI output.
            warn = "TEST CLIP - NO AI BACKEND REACHED"
            hint = "connect Colab or check internet - see Queue message"
            vf = (
                f"drawtext=fontfile='{font_esc}':text='{warn}':fontcolor=red:"
                f"fontsize={max(req.width // 22, 18)}:x=(w-text_w)/2:"
                f"y=(h-text_h)/2:box=1:boxcolor=black@0.6:boxborderw=12,"
                f"drawtext=fontfile='{font_esc}':text='{hint}':fontcolor=yellow:"
                f"fontsize={max(req.width // 40, 12)}:x=(w-text_w)/2:"
                f"y=(h+text_h)/2+30:box=1:boxcolor=black@0.6:boxborderw=8,"
                f"drawtext=fontfile='{font_esc}':text='{label}':fontcolor=white:"
                f"fontsize=18:x=(w-text_w)/2:y=h-50:box=1:boxcolor=black@0.5:"
                f"boxborderw=8"
            )
            try:
                run_ffmpeg([*base_args, "-vf", vf, *tail])
                return out_path
            except RuntimeError as exc:
                log.warning("drawtext failed, rendering without label: %s", exc)
        run_ffmpeg([*base_args, *tail])
        return out_path


# ----------------------------------------------------------------------------
# 4. Post-processing (stitch / interpolate / upscale / thumbnail)
# ----------------------------------------------------------------------------

class PostProcessor:
    """ffmpeg-based long-video stitching and quality boosters.

    NOTE on quality boosters (honest labeling): on this CPU-only machine,
    frame interpolation uses ffmpeg's motion-compensated `minterpolate`
    (RIFE-class neural interpolation needs a GPU) and upscaling uses Lanczos
    (Real-ESRGAN needs a GPU). The Colab worker can do RIFE/ESRGAN-quality
    passes when attached.
    """

    @staticmethod
    def stitch(clips: list[Path], out_path: Path,
               crossfade: float = CROSSFADE_SECONDS) -> Path:
        if len(clips) == 1:
            shutil.copyfile(clips[0], out_path)
            return out_path
        inputs: list[str] = []
        for c in clips:
            inputs += ["-i", str(c)]
        durations = [ffprobe_duration(c) for c in clips]
        # Chain xfade filters: each transition offset is cumulative play time
        # minus the crossfade overlap accumulated so far.
        filters = []
        prev = "[0:v]"
        offset = 0.0
        for i in range(1, len(clips)):
            offset += durations[i - 1] - crossfade
            outlbl = f"[vx{i}]" if i < len(clips) - 1 else "[vout]"
            filters.append(
                f"{prev}[{i}:v]xfade=transition=fade:duration={crossfade}:"
                f"offset={offset:.3f}{outlbl}"
            )
            prev = f"[vx{i}]"
        run_ffmpeg([
            *inputs, "-filter_complex", ";".join(filters), "-map", "[vout]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium",
            "-crf", "17", str(out_path),
        ])
        return out_path

    @staticmethod
    def interpolate(src: Path, out_path: Path, target_fps: int = 32) -> Path:
        run_ffmpeg([
            "-i", str(src),
            "-vf", f"minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:"
                   f"vsbmc=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium",
            "-crf", "17", str(out_path),
        ])
        return out_path

    @staticmethod
    def upscale(src: Path, out_path: Path, quality: str) -> Path:
        heights = {"720p": 720, "1080p": 1080, "4K": 2160}
        target_h = heights.get(quality)
        if not target_h:
            shutil.copyfile(src, out_path)
            return out_path
        run_ffmpeg([
            "-i", str(src),
            "-vf", f"scale=-2:{target_h}:flags=lanczos,unsharp=5:5:0.4:5:5:0.0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium",
            "-crf", "17", str(out_path),
        ])
        return out_path

    @staticmethod
    def thumbnail(src: Path, out_path: Path) -> Path:
        run_ffmpeg(["-i", str(src), "-vf", "thumbnail,scale=320:-2",
                    "-frames:v", "1", str(out_path)])
        return out_path

    @staticmethod
    def last_frame(src: Path, out_path: Path) -> Path:
        """Extract the final frame (PNG) — used to condition the next chunk."""
        run_ffmpeg(["-sseof", "-0.25", "-i", str(src),
                    "-update", "1", "-frames:v", "1", str(out_path)])
        if not out_path.exists():
            raise RuntimeError(f"last_frame produced nothing for {src}")
        return out_path

    @staticmethod
    def probe_video(path: Path) -> tuple[int, int, float]:
        """Return (width, height, fps) of the first video stream."""
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            proc = subprocess.run(
                [ffprobe, "-v", "quiet", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,r_frame_rate",
                 "-of", "csv=p=0", str(path)],
                capture_output=True, text=True)
            parts = proc.stdout.strip().split(",")
            if len(parts) >= 3:
                try:
                    num, _, den = parts[2].partition("/")
                    fps = float(num) / float(den or 1)
                    return int(parts[0]), int(parts[1]), fps
                except (ValueError, ZeroDivisionError):
                    pass
        # Fallback: parse ffmpeg stderr
        proc = subprocess.run([ffmpeg_exe(), "-i", str(path)],
                              capture_output=True, text=True)
        m = re.search(r"(\d{3,5})x(\d{3,5})", proc.stderr)
        f = re.search(r"(\d+(?:\.\d+)?) fps", proc.stderr)
        return (int(m.group(1)) if m else 0, int(m.group(2)) if m else 0,
                float(f.group(1)) if f else 30.0)

    @classmethod
    def normalize_clips(cls, clips: list[Path], tmp: Path) -> list[Path]:
        """Re-encode any clip whose size/fps differs from the first clip.

        xfade hard-fails on mismatched inputs; mismatches happen when backend
        fallback switches mid-job (e.g. LTX 832x480@30 → CogVideoX 720x480@16).
        """
        if len(clips) < 2:
            return clips
        ref_w, ref_h, ref_fps = cls.probe_video(clips[0])
        out: list[Path] = [clips[0]]
        for i, clip in enumerate(clips[1:], 1):
            w, h, fps = cls.probe_video(clip)
            if (w, h) == (ref_w, ref_h) and abs(fps - ref_fps) < 0.5:
                out.append(clip)
                continue
            norm = tmp / f"norm_{i:02d}.mp4"
            run_ffmpeg([
                "-i", str(clip),
                "-vf", f"scale={ref_w}:{ref_h}:flags=lanczos:"
                       f"force_original_aspect_ratio=decrease,"
                       f"pad={ref_w}:{ref_h}:(ow-iw)/2:(oh-ih)/2,"
                       f"fps={ref_fps:.3f}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
                "-crf", "17", str(norm),
            ])
            out.append(norm)
        return out


# ----------------------------------------------------------------------------
# 5. Audio & subtitles layer
# ----------------------------------------------------------------------------

TTS_VOICES: dict[str, dict[str, str]] = {
    "English":  {"male": "en-US-GuyNeural",     "female": "en-US-JennyNeural",   "neutral": "en-US-AriaNeural"},
    "Urdu":     {"male": "ur-PK-AsadNeural",    "female": "ur-PK-UzmaNeural",    "neutral": "ur-PK-UzmaNeural"},
    "Hindi":    {"male": "hi-IN-MadhurNeural",  "female": "hi-IN-SwaraNeural",   "neutral": "hi-IN-SwaraNeural"},
    "Arabic":   {"male": "ar-SA-HamedNeural",   "female": "ar-SA-ZariyahNeural", "neutral": "ar-SA-ZariyahNeural"},
    "Spanish":  {"male": "es-ES-AlvaroNeural",  "female": "es-ES-ElviraNeural",  "neutral": "es-ES-ElviraNeural"},
    "French":   {"male": "fr-FR-HenriNeural",   "female": "fr-FR-DeniseNeural",  "neutral": "fr-FR-DeniseNeural"},
    "Chinese":  {"male": "zh-CN-YunxiNeural",   "female": "zh-CN-XiaoxiaoNeural","neutral": "zh-CN-XiaoxiaoNeural"},
}

LANG_CODES = {"English": "en", "Urdu": "ur", "Hindi": "hi", "Arabic": "ar",
              "Spanish": "es", "French": "fr", "Chinese": "zh-CN"}

MUSIC_MOODS = ["Ambient", "Uplifting", "Dramatic", "Calm", "Energetic"]


@dataclass
class WordStamp:
    word: str
    start: float   # seconds
    end: float


class AudioEngine:
    """edge-tts voiceover (free Microsoft neural voices), music bed, ducking."""

    @staticmethod
    def synthesize(text: str, language: str, gender: str, speed_pct: int,
                   out_path: Path) -> list[WordStamp]:
        """Generate TTS mp3; return word-boundary timestamps for subtitles."""
        import edge_tts

        voice = TTS_VOICES.get(language, TTS_VOICES["English"]).get(
            gender, "en-US-JennyNeural")
        rate = f"{'+' if speed_pct >= 0 else ''}{speed_pct}%"
        stamps: list[WordStamp] = []

        async def _run() -> None:
            tts = edge_tts.Communicate(text, voice, rate=rate)
            with open(out_path, "wb") as fh:
                async for chunk in tts.stream():
                    if chunk["type"] == "audio":
                        fh.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        start = chunk["offset"] / 1e7
                        dur = chunk["duration"] / 1e7
                        stamps.append(WordStamp(chunk["text"], start, start + dur))

        asyncio.run(_run())
        return stamps

    @staticmethod
    def _music_source(mood: str, duration: float, tmp: Path) -> Path:
        """User-provided music/<mood>.mp3 if present, else a synthesized ambient pad."""
        for ext in (".mp3", ".wav", ".m4a", ".ogg"):
            candidate = MUSIC_DIR / f"{mood.lower()}{ext}"
            if candidate.exists():
                return candidate
        pad = tmp / f"pad_{mood.lower()}.wav"
        chords = {
            "Ambient":   (110.0, 164.81, 220.0),
            "Uplifting": (130.81, 196.0, 261.63),
            "Dramatic":  (98.0, 146.83, 185.0),
            "Calm":      (87.31, 130.81, 174.61),
            "Energetic": (146.83, 220.0, 293.66),
        }.get(mood, (110.0, 164.81, 220.0))
        expr = "+".join(f"0.12*sin(2*PI*{f}*t)" for f in chords)
        run_ffmpeg([
            "-f", "lavfi",
            "-i", f"aevalsrc={expr}:s=44100:d={duration:.2f}",
            "-af", "tremolo=f=0.15:d=0.4,lowpass=f=900,afade=t=in:d=2,"
                   f"afade=t=out:st={max(duration-2,0):.2f}:d=2",
            str(pad),
        ])
        return pad

    @classmethod
    def mix_onto_video(cls, video: Path, out_path: Path, tmp: Path,
                       voice_mp3: Optional[Path],
                       music_mood: Optional[str],
                       voice_vol: float = 1.0, music_vol: float = 0.35) -> Path:
        """Attach voiceover and/or ducked music to a (silent) video.

        If the voiceover is longer than the video, the last frame is frozen
        (tpad) so narration never gets cut off.
        """
        vid_dur = ffprobe_duration(video)
        total = vid_dur
        src_video = video
        if voice_mp3 is not None:
            voice_dur = ffprobe_duration(voice_mp3)
            if voice_dur > vid_dur + 0.2:
                total = voice_dur + 0.5
                padded = tmp / "padded.mp4"
                run_ffmpeg([
                    "-i", str(video),
                    "-vf", f"tpad=stop_mode=clone:stop_duration={total - vid_dur:.2f}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
                    "-crf", "18", str(padded),
                ])
                src_video = padded

        inputs: list[str] = ["-i", str(src_video)]
        filters: list[str] = []
        if voice_mp3 is not None and music_mood:
            music = cls._music_source(music_mood, total, tmp)
            inputs += ["-i", str(voice_mp3), "-stream_loop", "-1", "-i", str(music)]
            # asplit the voice so it can BOTH key the sidechain compressor
            # (ducking the music) and be mixed back in — a label consumed by
            # one filter cannot be reused by another.
            filters.append(
                f"[1:a]volume={voice_vol},aresample=44100,asplit=2[vo1][vo2];"
                f"[2:a]volume={music_vol},aresample=44100,atrim=0:{total:.2f}[mu];"
                f"[mu][vo1]sidechaincompress=threshold=0.04:ratio=8:attack=80:"
                f"release=600[duck];"
                f"[duck][vo2]amix=inputs=2:duration=longest:normalize=0[aout]"
            )
        elif voice_mp3 is not None:
            inputs += ["-i", str(voice_mp3)]
            filters.append(f"[1:a]volume={voice_vol},aresample=44100[aout]")
        elif music_mood:
            music = cls._music_source(music_mood, total, tmp)
            inputs += ["-stream_loop", "-1", "-i", str(music)]
            filters.append(
                f"[1:a]volume={music_vol},aresample=44100,atrim=0:{total:.2f}[aout]")
        else:
            shutil.copyfile(video, out_path)
            return out_path

        run_ffmpeg([
            *inputs, "-filter_complex", ";".join(filters),
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
            str(out_path),
        ])
        return out_path


class SubtitleEngine:
    """Word-boundary-accurate SRT generation, free translation, burn-in."""

    MAX_CHARS_PER_CUE = 42
    POSITIONS = {"Bottom": 2, "Middle": 5, "Top": 8}   # ASS alignment codes

    @classmethod
    def build_cues(cls, stamps: list[WordStamp]) -> list[tuple[float, float, str]]:
        """Group word stamps into readable cues (~42 chars, natural breaks)."""
        cues: list[tuple[float, float, str]] = []
        cur_words: list[WordStamp] = []
        cur_len = 0
        for ws in stamps:
            add = len(ws.word) + (1 if cur_words else 0)
            if cur_words and (cur_len + add > cls.MAX_CHARS_PER_CUE
                              or ws.start - cur_words[-1].end > 1.2):
                cues.append((cur_words[0].start, cur_words[-1].end,
                             " ".join(w.word for w in cur_words)))
                cur_words, cur_len = [], 0
                add = len(ws.word)
            cur_words.append(ws)
            cur_len += add
        if cur_words:
            cues.append((cur_words[0].start, cur_words[-1].end,
                         " ".join(w.word for w in cur_words)))
        return cues

    @staticmethod
    def fallback_cues(text: str, total: float) -> list[tuple[float, float, str]]:
        """Even-timing cues when no word boundaries exist (e.g. no voiceover)."""
        words = text.split()
        if not words:
            return []
        cues, chunk = [], []
        for w in words:
            chunk.append(w)
            if len(" ".join(chunk)) > SubtitleEngine.MAX_CHARS_PER_CUE:
                cues.append(" ".join(chunk))
                chunk = []
        if chunk:
            cues.append(" ".join(chunk))
        per = total / len(cues)
        return [(i * per, (i + 1) * per - 0.05, c) for i, c in enumerate(cues)]

    @staticmethod
    def translate_cues(cues: list[tuple[float, float, str]],
                       target_lang: str) -> list[tuple[float, float, str]]:
        from deep_translator import GoogleTranslator
        code = LANG_CODES.get(target_lang, target_lang)
        tr = GoogleTranslator(source="auto", target=code)
        out = []
        for start, end, text in cues:
            try:
                out.append((start, end, tr.translate(text) or text))
            except Exception as exc:                                  # noqa: BLE001
                log.warning("Translate failed: %s", exc)
                out.append((start, end, text))
        return out

    @staticmethod
    def _srt_time(t: float) -> str:
        ms = int(round(t * 1000))
        h, rem = divmod(ms, 3600_000)
        m, rem = divmod(rem, 60_000)
        s, ms = divmod(rem, 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    @classmethod
    def write_srt(cls, cues: list[tuple[float, float, str]], out_path: Path) -> Path:
        lines = []
        for i, (start, end, text) in enumerate(cues, 1):
            lines += [str(i), f"{cls._srt_time(start)} --> {cls._srt_time(end)}",
                      text, ""]
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path

    @classmethod
    def burn_in(cls, video: Path, srt: Path, out_path: Path, *,
                font: str = "Arial", size: int = 24, color: str = "#FFFFFF",
                position: str = "Bottom", outline: int = 2) -> Path:
        rgb = color.lstrip("#")
        # ASS colors are &HBBGGRR
        ass_color = f"&H00{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}".upper()
        align = cls.POSITIONS.get(position, 2)
        style = (f"FontName={font},FontSize={size},PrimaryColour={ass_color},"
                 f"OutlineColour=&H00000000,BorderStyle=1,Outline={outline},"
                 f"Shadow=1,Alignment={align},MarginV=30")
        # ffmpeg filter path escaping (Windows drive colon)
        srt_esc = str(srt).replace("\\", "/").replace(":", "\\:")
        run_ffmpeg([
            "-i", str(video),
            "-vf", f"subtitles='{srt_esc}':force_style='{style}'",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium",
            "-crf", "18", "-c:a", "copy", str(out_path),
        ])
        return out_path


# ----------------------------------------------------------------------------
# 6. Continuous batch queue
# ----------------------------------------------------------------------------

class Stage(str, Enum):
    QUEUED = "Queued"
    ENHANCING = "Enhancing"
    GENERATING = "Generating"
    STITCHING = "Stitching"
    INTERPOLATING = "Interpolating"
    UPSCALING = "Upscaling"
    AUDIO = "Audio"
    SUBTITLES = "Subtitles"
    DONE = "Done"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


@dataclass
class JobSettings:
    """Everything needed to reproduce a job exactly."""
    prompt: str
    enhanced_prompt: str = ""
    negative_prompt: str = ""
    style_preset: str = "Cinematic"
    auto_enhance: bool = True
    quality: str = "480p"
    aspect: str = "16:9"
    duration: float = 5.0
    fps: int = WAN_NATIVE_FPS
    steps: int = 30
    guidance: float = 6.0
    seed: int = -1
    interpolate: bool = False
    upscale: bool = True
    voiceover: bool = False
    voice_text: str = ""
    voice_language: str = "English"
    voice_gender: str = "female"
    voice_speed: int = 0
    voice_volume: float = 1.0
    music: bool = False
    music_mood: str = "Ambient"
    music_volume: float = 0.35
    subtitles: bool = False
    subtitle_language: str = "English"
    subtitle_translate_to: str = ""
    subtitle_burn_in: bool = True
    subtitle_font: str = "Arial"
    subtitle_size: int = 24
    subtitle_color: str = "#FFFFFF"
    subtitle_position: str = "Bottom"
    subtitle_outline: int = 2


@dataclass
class Job:
    settings: JobSettings
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    created: float = field(default_factory=time.time)
    stage: Stage = Stage.QUEUED
    progress: float = 0.0
    message: str = "Waiting in queue"
    backend_used: str = ""
    actual_seed: int = -1
    output_video: str = ""
    thumbnail: str = ""
    srt_file: str = ""
    error: str = ""
    started: float = 0.0
    finished: float = 0.0
    not_before: float = 0.0                 # deferred-until timestamp (quota)
    deferrals: int = 0

    @property
    def eta_seconds(self) -> float:
        if self.stage in (Stage.DONE, Stage.FAILED, Stage.CANCELLED) or not self.started:
            return 0.0
        elapsed = time.time() - self.started
        if self.progress <= 0.02:
            return 0.0
        return max(elapsed / self.progress - elapsed, 0.0)


class JobQueue:
    """Persistent, crash-recovering, forever-running job queue.

    Jobs survive restarts via jobs.json; a failed job is logged and skipped;
    an OOM-flavored failure retries once at lower resolution. The worker
    thread never dies with the queue.
    """

    def __init__(self, studio: "VideoStudio") -> None:
        self.studio = studio
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._cancel_flags: set[str] = set()
        self._paused = False
        self._state_path = Path(studio.config.output_dir) / "queue" / "jobs.json"
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._state_path.parent / "worker.lock"
        self._restore()
        # Single-worker guard: if another live studio process owns the queue,
        # do NOT start a second worker — it would re-run the job the first
        # instance is generating right now (duplicate GPU spend, file races).
        self.is_worker = self._acquire_worker_lock()
        if self.is_worker:
            self._worker = threading.Thread(target=self._run, daemon=True,
                                            name="studio-queue")
            self._worker.start()
        else:
            log.warning("Another studio instance owns the queue — this one is "
                        "VIEW-ONLY (its submissions run when the owner restarts)")

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
            self._lock_path.write_text(str(os.getpid()), encoding="utf-8")
            return True
        except Exception as exc:                                      # noqa: BLE001
            log.warning("Worker lock error (%s) — assuming ownership", exc)
            return True

    # -- public API ----------------------------------------------------------

    def submit(self, settings: JobSettings) -> Job:
        job = Job(settings=settings)
        with self._lock:
            self.jobs[job.id] = job
            self.order.append(job.id)
            self._persist()
        self._wake.set()
        log.info("Job %s queued: %.40s…", job.id, settings.prompt)
        return job

    def cancel(self, job_id: str) -> None:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            self._cancel_flags.add(job_id)
            if job.stage == Stage.QUEUED:
                job.stage = Stage.CANCELLED
                job.message = "Cancelled before start"
            self._persist()

    def pause(self, paused: bool) -> None:
        self._paused = paused
        if not paused:
            self._wake.set()

    def snapshot(self) -> list[Job]:
        with self._lock:
            return [self.jobs[jid] for jid in self.order if jid in self.jobs]

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        try:
            if (self.is_worker and self._lock_path.exists()
                    and self._lock_path.read_text().strip() == str(os.getpid())):
                self._lock_path.unlink()
        except Exception:                                             # noqa: BLE001
            pass

    # -- persistence ---------------------------------------------------------

    def _persist(self) -> None:
        """Write queue state. MUST never raise — a failed persist (e.g. a
        Windows file-lock collision with another instance) must not kill the
        worker thread or a running generation."""
        try:
            data = []
            for jid in self.order:
                job = self.jobs.get(jid)
                if not job:
                    continue
                d = dataclasses.asdict(job)
                d["stage"] = job.stage.value
                data.append(d)
            payload = json.dumps(data, indent=1)
        except Exception as exc:                                      # noqa: BLE001
            log.warning("persist serialization failed: %s", exc)
            return
        tmp = self._state_path.with_name(f"jobs.{os.getpid()}.tmp")
        for attempt in range(5):
            try:
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(self._state_path)
                return
            except (PermissionError, OSError) as exc:
                time.sleep(0.25 * (attempt + 1))
                last = exc
        log.warning("persist failed after retries: %s", last)

    @staticmethod
    def _job_from_dict(d: dict) -> Job:
        d = dict(d)
        st = d.pop("settings", {})
        known_s = {f.name for f in dataclasses.fields(JobSettings)}
        known_j = {f.name for f in dataclasses.fields(Job)}
        stage = d.get("stage", "Queued")
        job = Job(settings=JobSettings(**{k: v for k, v in st.items()
                                          if k in known_s}),
                  **{k: v for k, v in d.items()
                     if k in known_j and k not in ("settings", "stage")})
        job.stage = (Stage(stage) if stage in Stage._value2member_map_
                     else Stage.QUEUED)
        return job

    def _restore(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            for d in data:
                job = self._job_from_dict(d)
                if job.stage in (Stage.ENHANCING, Stage.GENERATING, Stage.STITCHING,
                                 Stage.INTERPOLATING, Stage.UPSCALING, Stage.AUDIO,
                                 Stage.SUBTITLES):
                    job.stage = Stage.QUEUED       # was mid-flight during a crash → re-run
                    job.message = "Recovered after restart — re-queued"
                self.jobs[job.id] = job
                self.order.append(job.id)
            log.info("Queue restored: %d job(s)", len(self.order))
        except Exception as exc:                                      # noqa: BLE001
            log.warning("Queue restore failed: %s", exc)

    def _merge_external(self) -> None:
        """Pick up jobs submitted by another (view-only) studio instance.

        If the user accidentally opens two copies of the app, the second one
        can still accept prompts — they land in jobs.json. The worker instance
        merges any unknown queued job so nothing silently sits forever."""
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:                                             # noqa: BLE001
            return
        with self._lock:
            for d in data:
                jid = d.get("id")
                if jid and jid not in self.jobs and d.get("stage") == "Queued":
                    try:
                        job = self._job_from_dict(d)
                    except Exception as exc:                          # noqa: BLE001
                        log.warning("merge of external job %s failed: %s", jid, exc)
                        continue
                    self.jobs[job.id] = job
                    self.order.append(job.id)
                    log.info("Merged externally-submitted job %s", job.id)

    # -- worker --------------------------------------------------------------

    def _next_pending(self) -> Optional[Job]:
        now = time.time()
        with self._lock:
            for jid in self.order:
                job = self.jobs.get(jid)
                if (job and job.stage == Stage.QUEUED
                        and jid not in self._cancel_flags
                        and job.not_before <= now):
                    return job
        return None

    def _run(self) -> None:
        last_merge = 0.0
        while not self._stop.is_set():
            try:
                self._run_once(time.time() - last_merge > 10)
                if time.time() - last_merge > 10:
                    last_merge = time.time()
            except Exception as exc:                                  # noqa: BLE001
                # The worker must be unkillable — log and keep serving.
                log.error("Worker loop error (recovered): %s", exc, exc_info=True)
                time.sleep(2)

    def _run_once(self, do_merge: bool) -> None:
            if do_merge:
                self._merge_external()
            job = None if self._paused else self._next_pending()
            if job is None:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                return
            try:
                self.studio.process_job(job, self._make_progress(job),
                                        cancelled=lambda: job.id in self._cancel_flags)
            except QuotaExhausted as quota:
                job.deferrals += 1
                if job.deferrals > self.studio.config.max_deferrals_per_job:
                    job.stage = Stage.FAILED
                    job.error = "Free GPU quota never recovered"
                    job.message = (f"Failed after {job.deferrals} quota waits — "
                                   f"add a free HF token or use the Colab worker")
                    job.finished = time.time()
                else:
                    job.stage = Stage.QUEUED
                    job.not_before = time.time() + quota.wait_seconds
                    at = time.strftime("%H:%M", time.localtime(job.not_before))
                    job.message = (f"⏳ {quota.reason} — auto-retry {job.deferrals}/"
                                   f"{self.studio.config.max_deferrals_per_job} "
                                   f"at {at} (progress is saved)")
                    job.progress = 0.0
                    log.info("Job %s deferred until %s", job.id, at)
            except Exception as exc:                                  # noqa: BLE001
                job.stage = Stage.FAILED
                job.error = str(exc)[:500]
                job.message = f"Failed: {exc}"
                job.finished = time.time()
                log.error("Job %s failed: %s", job.id, exc, exc_info=True)
            finally:
                with self._lock:
                    self._persist()

    def _make_progress(self, job: Job) -> Callable[[Stage, float, str], None]:
        def cb(stage: Stage, fraction: float, message: str) -> None:
            job.stage = stage
            job.progress = min(max(fraction, 0.0), 1.0)
            job.message = message
            with self._lock:
                self._persist()
        return cb


# ----------------------------------------------------------------------------
# 7. The studio facade
# ----------------------------------------------------------------------------

STAGE_WEIGHTS = {   # rough share of total job time, for the progress bar
    Stage.ENHANCING: 0.02, Stage.GENERATING: 0.70, Stage.STITCHING: 0.05,
    Stage.INTERPOLATING: 0.08, Stage.UPSCALING: 0.05, Stage.AUDIO: 0.05,
    Stage.SUBTITLES: 0.05,
}


class VideoStudio:
    """Facade wiring hardware, prompts, backends, post, audio, subs, queue."""

    def __init__(self, config: Optional[StudioConfig] = None) -> None:
        self.config = config or StudioConfig.load()
        self.hardware = HardwareProbe()
        self.prompts = PromptEngine()
        self.post = PostProcessor()
        self.backends: dict[str, GenerationBackend] = {
            "colab": ColabBackend(self.config),
            "ltx": LTXSpaceBackend(self.config),
            "cogvideox": CogVideoXBackend(self.config),
            "wan_official": WanOfficialBackend(self.config),
            "hf_space": HFSpaceBackend(self.config),
            "test_pattern": TestPatternBackend(),
        }
        out = Path(self.config.output_dir)
        (out / "jobs").mkdir(parents=True, exist_ok=True)
        (out / "videos").mkdir(parents=True, exist_ok=True)
        MUSIC_DIR.mkdir(exist_ok=True)
        self.queue = JobQueue(self)
        log.info("Studio up. %s", self.hardware.summary())

    # -- helpers -------------------------------------------------------------

    def active_backends(self) -> list[GenerationBackend]:
        chain = []
        for name in self.config.backend_order:
            be = self.backends.get(name)
            if not be or not be.available():
                continue
            if name == "test_pattern" and not self.config.allow_test_pattern_fallback:
                continue
            chain.append(be)
        return chain

    def backend_status(self) -> str:
        rows = []
        for name in ("colab", "ltx", "cogvideox", "wan_official", "test_pattern"):
            be = self.backends[name]
            ok = be.available()
            if name == "test_pattern" and not self.config.allow_test_pattern_fallback:
                ok = False
            rows.append(f"{'🟢' if ok else '⚪'} {be.description}")
        return "\n".join(rows)

    @staticmethod
    def _frames_for(seconds: float, fps: int = WAN_NATIVE_FPS) -> int:
        n = max(int(round(seconds * fps)), fps)
        return (n // 4) * 4 + 1            # Wan requires 4n+1 frames

    # -- the pipeline --------------------------------------------------------

    def process_job(self, job: Job, progress: Callable[[Stage, float, str], None],
                    cancelled: Callable[[], bool] = lambda: False) -> Job:
        s = job.settings
        job.started = time.time()
        job_dir = Path(self.config.output_dir) / "jobs" / job.id
        tmp = job_dir / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        done_weight = 0.0

        def bump(stage: Stage, inner: float, msg: str) -> None:
            w = STAGE_WEIGHTS.get(stage, 0.05)
            progress(stage, done_weight + w * inner, msg)

        # 1. Enhance -----------------------------------------------------------
        bump(Stage.ENHANCING, 0.2, "Enhancing prompt…")
        job.actual_seed = s.seed if s.seed >= 0 else random.randint(0, 2**31 - 1)
        if s.auto_enhance and not s.enhanced_prompt:
            s.enhanced_prompt = self.prompts.enhance(s.prompt, s.style_preset,
                                                     job.actual_seed)
        final_prompt = s.enhanced_prompt or s.prompt
        negative = self.prompts.negative(s.negative_prompt)
        done_weight += STAGE_WEIGHTS[Stage.ENHANCING]

        # 2. Generate (chunked long-video mode) ---------------------------------
        w, h = ASPECT_SIZES_480.get(s.aspect, ASPECT_SIZES_480["16:9"])
        n_chunks = max(int(-(-s.duration // CHUNK_SECONDS)), 1)   # ceil
        clips: list[Path] = []
        chain = self.active_backends()
        if not chain:
            raise RuntimeError(
                "No generation backend available. Start the Colab worker "
                "(colab_worker.ipynb) or add a free HF token in Settings.")
        partial_note = ""
        prev_frame: Optional[Path] = None
        quota_waits = 0
        sticky: Optional[GenerationBackend] = None   # backend that made clip 0
        for i in range(n_chunks):
            if cancelled():
                job.stage = Stage.CANCELLED
                job.message = "Cancelled"
                job.finished = time.time()
                return job
            chunk_secs = min(CHUNK_SECONDS, s.duration - i * CHUNK_SECONDS)
            if n_chunks > 1 and i > 0:
                chunk_secs = min(chunk_secs + CROSSFADE_SECONDS, CHUNK_SECONDS)
            req = GenerationRequest(
                prompt=final_prompt,
                negative_prompt=negative,
                width=w, height=h,
                num_frames=self._frames_for(chunk_secs),
                steps=s.steps, guidance=s.guidance,
                seed=job.actual_seed + i, fps=WAN_NATIVE_FPS,
            )
            # After the first clip, stick to the backend that produced it and
            # never mix in the test pattern mid-video. Condition on the last
            # frame when the backend supports it — true scene continuity.
            if sticky is None:
                candidates = chain
            else:
                candidates = [sticky] + [
                    be for be in chain
                    if be is not sticky and be.name != "test_pattern"
                ] if sticky.name != "test_pattern" else [sticky]
            clip_path = tmp / f"clip_{i:02d}.mp4"
            # Resume support: a deferred/restarted job keeps its finished clips
            # on disk — never re-spend GPU quota on a clip we already have.
            if clip_path.exists() and ffprobe_duration(clip_path) > 0.5:
                log.info("Job %s: reusing existing clip %d", job.id, i)
                clips.append(clip_path)
                if sticky is None and job.backend_used:
                    sticky = self.backends.get(job.backend_used)
                if (i < n_chunks - 1 and sticky
                        and sticky.supports_image_conditioning):
                    try:
                        prev_frame = self.post.last_frame(
                            clip_path, tmp / f"frame_{i:02d}.png")
                    except Exception:                                  # noqa: BLE001
                        prev_frame = None
                bump(Stage.GENERATING, (i + 1) / n_chunks,
                     f"Clip {i+1}/{n_chunks} restored from previous run")
                continue
            generated = False
            clip_quota_hint = 0
            clip_transient = False
            for be in candidates:
                req.init_image = (str(prev_frame)
                                  if prev_frame and be.supports_image_conditioning
                                  else None)
                attempt = 0
                while attempt <= self.config.max_retries:
                    try:
                        bump(Stage.GENERATING, (i + 0.1) / n_chunks,
                             f"Generating clip {i+1}/{n_chunks} via {be.name}"
                             f"{' (retry)' if attempt else ''}…")
                        be.generate(req, clip_path,
                                    lambda m: bump(Stage.GENERATING,
                                                   (i + 0.5) / n_chunks, m))
                        job.backend_used = be.name
                        generated = True
                        break
                    except Exception as exc:                          # noqa: BLE001
                        log.warning("Backend %s clip %d attempt %d failed: %s",
                                    be.name, i, attempt, exc)
                        wait = quota_wait_seconds(str(exc))
                        if wait is not None:
                            clip_quota_hint = max(clip_quota_hint, wait)
                        if (wait is not None and wait <= 180
                                and quota_waits < self.config.max_quota_waits_per_job):
                            # Free-GPU quota window: wait it out and resume the
                            # SAME backend — keeps continuity, costs only time.
                            quota_waits += 1
                            deadline = time.time() + wait
                            while time.time() < deadline:
                                if cancelled():
                                    job.stage = Stage.CANCELLED
                                    job.message = "Cancelled while waiting for quota"
                                    job.finished = time.time()
                                    return job
                                left = int(deadline - time.time())
                                bump(Stage.GENERATING, (i + 0.1) / n_chunks,
                                     f"Free GPU quota — auto-resuming clip "
                                     f"{i+1}/{n_chunks} in {left//60}m{left%60:02d}s "
                                     f"(wait {quota_waits}/"
                                     f"{self.config.max_quota_waits_per_job})")
                                time.sleep(min(10, max(left, 1)))
                            continue          # retry same backend, attempt unchanged
                        if "queue timed out" in str(exc):
                            # Re-submitting after a queue timeout means starting
                            # at the BACK of the public queue — never retry the
                            # same slow backend; move on (or defer).
                            attempt = self.config.max_retries + 1
                            continue
                        if is_transient_error(str(exc)):
                            # Internet/DNS blip — flag it so total failure
                            # defers the job instead of failing it.
                            clip_transient = True
                        if "out of memory" in str(exc).lower() and req.width > 480:
                            req.width, req.height = 480, 832 if h > w else 480
                            log.info("OOM → retrying at lower resolution")
                        attempt += 1
                if generated:
                    break
            if not generated:
                if clip_quota_hint:
                    # Quota-blocked: defer the whole job — finished clips stay
                    # on disk, so the resume costs nothing. The queue re-runs
                    # it automatically when the free window replenishes.
                    raise QuotaExhausted(min(
                        clip_quota_hint, self.config.quota_wait_cap_min * 60))
                if clip_transient:
                    # Internet/DNS outage: defer and retry — connections come back.
                    raise QuotaExhausted(300, reason="Network unavailable")
                if clips:
                    # Hard (non-quota) failure mid-job: deliver what we have
                    # honestly instead of splicing test clips into AI video.
                    got = sum(ffprobe_duration(c) for c in clips)
                    partial_note = (f"⚠ Backend failed after clip {i}/{n_chunks} — "
                                    f"delivered first {got:.0f}s of {s.duration:.0f}s.")
                    log.warning("Job %s partial: %s", job.id, partial_note)
                    break
                raise RuntimeError(f"All backends failed on clip {i+1}/{n_chunks}")
            clips.append(clip_path)
            job.deferrals = 0        # progress made — reset the patience budget
            if sticky is None:
                sticky = self.backends.get(job.backend_used)
            if i < n_chunks - 1 and sticky and sticky.supports_image_conditioning:
                try:
                    prev_frame = self.post.last_frame(
                        clip_path, tmp / f"frame_{i:02d}.png")
                except Exception as exc:                              # noqa: BLE001
                    log.warning("last_frame failed (%s) — next chunk unconditioned", exc)
                    prev_frame = None
            bump(Stage.GENERATING, (i + 1) / n_chunks,
                 f"Clip {i+1}/{n_chunks} done")
        done_weight += STAGE_WEIGHTS[Stage.GENERATING]

        # 3. Stitch -------------------------------------------------------------
        bump(Stage.STITCHING, 0.3, "Stitching clips with crossfade…")
        stitched = tmp / "stitched.mp4"
        self.post.stitch(self.post.normalize_clips(clips, tmp), stitched)
        current = stitched
        done_weight += STAGE_WEIGHTS[Stage.STITCHING]

        # 4. Interpolate / upscale ----------------------------------------------
        if s.interpolate:
            _, _, src_fps = self.post.probe_video(current)
            target_fps = min(int(round(src_fps * 2)), 60)
            bump(Stage.INTERPOLATING, 0.3,
                 f"Motion-interpolating {src_fps:.0f} → {target_fps} fps…")
            interp = tmp / "interp.mp4"
            self.post.interpolate(current, interp, target_fps=target_fps)
            current = interp
        done_weight += STAGE_WEIGHTS[Stage.INTERPOLATING]

        if s.upscale and s.quality in ("720p", "1080p", "4K"):
            bump(Stage.UPSCALING, 0.3, f"Upscaling to {s.quality} (Lanczos+sharpen)…")
            up = tmp / "upscaled.mp4"
            self.post.upscale(current, up, s.quality)
            current = up
        done_weight += STAGE_WEIGHTS[Stage.UPSCALING]

        # 5. Audio ---------------------------------------------------------------
        voice_mp3: Optional[Path] = None
        stamps: list[WordStamp] = []
        if s.voiceover and (s.voice_text.strip() or s.prompt.strip()):
            bump(Stage.AUDIO, 0.2, "Synthesizing voiceover…")
            voice_mp3 = tmp / "voice.mp3"
            stamps = AudioEngine.synthesize(
                s.voice_text.strip() or s.prompt.strip(),
                s.voice_language, s.voice_gender, s.voice_speed, voice_mp3)
        if voice_mp3 or s.music:
            bump(Stage.AUDIO, 0.6, "Mixing audio (music ducked under voice)…")
            mixed = tmp / "mixed.mp4"
            AudioEngine.mix_onto_video(
                current, mixed, tmp, voice_mp3,
                s.music_mood if s.music else None,
                voice_vol=s.voice_volume, music_vol=s.music_volume)
            current = mixed
        done_weight += STAGE_WEIGHTS[Stage.AUDIO]

        # 6. Subtitles -------------------------------------------------------------
        if s.subtitles:
            bump(Stage.SUBTITLES, 0.3, "Building subtitles…")
            script = s.voice_text.strip() or s.prompt.strip()
            if stamps:
                cues = SubtitleEngine.build_cues(stamps)
            else:
                cues = SubtitleEngine.fallback_cues(script, ffprobe_duration(current))
            if s.subtitle_translate_to and s.subtitle_translate_to != "None":
                cues = SubtitleEngine.translate_cues(cues, s.subtitle_translate_to)
            srt = job_dir / f"{job.id}.srt"
            SubtitleEngine.write_srt(cues, srt)
            job.srt_file = str(srt)
            if s.subtitle_burn_in and cues:
                bump(Stage.SUBTITLES, 0.7, "Burning in styled subtitles…")
                burned = tmp / "subtitled.mp4"
                SubtitleEngine.burn_in(
                    current, srt, burned,
                    font=s.subtitle_font, size=s.subtitle_size,
                    color=s.subtitle_color, position=s.subtitle_position,
                    outline=s.subtitle_outline)
                current = burned
        done_weight += STAGE_WEIGHTS[Stage.SUBTITLES]

        # 7. Finalize ---------------------------------------------------------------
        videos_dir = Path(self.config.output_dir) / "videos"
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(job.created))
        final = videos_dir / f"{stamp}_{job.id}.mp4"
        shutil.copyfile(current, final)
        thumb = job_dir / "thumb.jpg"
        try:
            self.post.thumbnail(final, thumb)
            job.thumbnail = str(thumb)
        except Exception as exc:                                      # noqa: BLE001
            log.warning("Thumbnail failed: %s", exc)
        job.output_video = str(final)
        (job_dir / "settings.json").write_text(
            json.dumps(dataclasses.asdict(s) | {"actual_seed": job.actual_seed,
                                                "backend": job.backend_used},
                       indent=2), encoding="utf-8")
        shutil.rmtree(tmp, ignore_errors=True)
        job.stage = Stage.DONE
        job.progress = 1.0
        job.finished = time.time()
        pretty = {"colab": "Colab GPU (Wan2.1)", "ltx": "LTX-Video AI",
                  "hf_space": "HF Space (Wan)", "cogvideox": "CogVideoX-5B AI",
                  "test_pattern": "⚠ TEST PATTERN — not AI! No backend reached"}
        job.message = (f"Done in {job.finished - job.started:.0f}s via "
                       f"{pretty.get(job.backend_used, job.backend_used)}")
        if partial_note:
            job.message = f"{job.message} | {partial_note}"
        log.info("Job %s complete → %s", job.id, final.name)
        return job

    # -- gallery ----------------------------------------------------------------

    def gallery(self) -> list[dict[str, str]]:
        items = []
        for job in reversed(self.queue.snapshot()):
            if job.stage == Stage.DONE and job.output_video and \
                    Path(job.output_video).exists():
                items.append({
                    "id": job.id, "video": job.output_video,
                    "thumb": job.thumbnail, "prompt": job.settings.prompt,
                    "seed": str(job.actual_seed),
                })
        return items

    def shutdown(self) -> None:
        self.queue.shutdown()
        log.info("Studio shut down gracefully.")


# ----------------------------------------------------------------------------
# Smoke test:  python video_studio.py --smoke
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    if "--smoke" in sys.argv:
        studio = VideoStudio()
        print(studio.hardware.summary())
        print(studio.backend_status())
        settings = JobSettings(
            prompt="A red fox running through a snowy forest at dawn",
            duration=4, quality="480p", voiceover=True,
            voice_text="A red fox races through fresh snow as the sun rises.",
            subtitles=True, music=True, music_mood="Calm",
        )
        job = studio.queue.submit(settings)
        while job.stage not in (Stage.DONE, Stage.FAILED):
            print(f"  [{job.stage.value:14s}] {job.progress*100:5.1f}% {job.message}")
            time.sleep(2)
        print(f"FINAL: {job.stage.value} → {job.output_video or job.error}")
        studio.shutdown()
