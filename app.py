"""
app.py — VideoStudio Pro (Obsidian Cinema Edition)
Unified 4K Ultra HD AI Video & Storyboard Studio.
Crafted for Kamran Ashraf (Kami).

Launch:   python app.py
Access:   http://127.0.0.1:7860
"""

from __future__ import annotations

import dataclasses
import html
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import gradio as gr

from video_studio import (
    ASPECT_SIZES,
    DEFAULT_OUTPUT_DIR,
    LANG_CODES,
    MUSIC_MOODS,
    QUALITY_TIERS,
    STYLE_PRESETS,
    SUBTITLE_STYLES,
    TTS_VOICES,
    AudioEngine,
    FreeLLMPromptEnhancer,
    HardwareProbe,
    JobQueue,
    JobSettings,
    JobStatus,
    PromptEngine,
    Stage,
    StudioConfig,
    SubtitleEngine,
    VideoEngine,
    VideoStudio,
    frames_for,
    log,
    log_queue,
)

# Global VideoStudio Instance
STUDIO = VideoStudio()

# Mapping constants
ASPECT_OPTIONS = {
    "16:9 · YouTube / Cinema": "16:9",
    "9:16 · Reels / Shorts / TikTok": "9:16",
    "1:1 · Square / Instagram": "1:1",
    "21:9 · Ultrawide Cinema": "21:9",
    "4:3 · Classic TV": "4:3",
}
ASPECT_CHOICES = list(ASPECT_OPTIONS.keys())

LANGUAGES = list(TTS_VOICES.keys())
SUBTITLE_LANGS = ["None"] + list(LANG_CODES.keys())
STYLES_LIST = list(STYLE_PRESETS.keys())
SUB_STYLES_LIST = list(SUBTITLE_STYLES.keys())

STAGE_ICONS = {
    Stage.QUEUED: "◔",
    Stage.ENHANCING: "✦",
    Stage.GENERATING: "▣",
    Stage.STITCHING: "⛓",
    Stage.INTERPOLATING: "⇄",
    Stage.UPSCALING: "⤢",
    Stage.AUDIO: "♪",
    Stage.SUBTITLES: "☰",
    Stage.DONE: "✓",
    Stage.FAILED: "✕",
    Stage.CANCELLED: "⊘",
}

STAGE_ORDER = [
    Stage.QUEUED,
    Stage.ENHANCING,
    Stage.GENERATING,
    Stage.STITCHING,
    Stage.INTERPOLATING,
    Stage.UPSCALING,
    Stage.AUDIO,
    Stage.SUBTITLES,
    Stage.DONE,
]

# Curated Prompt Inspirations
PROMPT_PRESETS = {
    "🦅 Himalayan Eagle": (
        "A majestic golden eagle soaring gracefully above snow-capped Himalayan peaks in dramatic golden hour sunrise light, 8k nature documentary cinematic masterpiece.",
        "Documentary", "1080p", "16:9 · YouTube / Cinema", 6, "English", "Female",
        "High above the world, freedom finds its true horizon.", "Ambient", "Classic White"
    ),
    "🏎️ Cyberpunk Tokyo": (
        "A sleek futuristic neon hypercar drifting through rain-slicked Tokyo streets at night, vibrant cyan and magenta reflections, ultra-detailed 35mm anamorphic cinema lens, 4k ultra hd.",
        "Cyberpunk", "1080p", "16:9 · YouTube / Cinema", 6, "English", "Male",
        "Night city beats with neon speed and electric dreams.", "Dramatic", "Neon Glow"
    ),
    "🌌 Deep Space Nebula": (
        "An astronaut floating gently past a glowing bioluminescent alien nebula with shimmering cosmic dust and colossal ringed planets in deep space, volumetric lighting.",
        "Cinematic", "1080p", "16:9 · YouTube / Cinema", 8, "English", "Male",
        "Beyond the boundaries of our solar system lies the infinite unknown.", "Ambient", "Neon Glow"
    ),
    "🌊 Tropical Coral Reef": (
        "Sunlight piercing crystal clear turquoise ocean waters revealing a vibrant coral reef kingdom with sea turtles and glowing exotic marine life, 4k hdr.",
        "Hyper-realistic", "1080p", "16:9 · YouTube / Cinema", 6, "English", "Female",
        "Beneath the surface lies a tranquil universe untouched by time.", "Calm", "Classic White"
    ),
    "🇵🇰 اردو سینما - پرانی گاڑی": (
        "شہر کی خوبصورت بارش میں چلتی ہوئی پرانی گاڑی اور شام کے سائے، خوبصورت سینما فوٹیج",
        "Cinematic", "1080p", "16:9 · YouTube / Cinema", 6, "Urdu", "Male",
        "ہر سفر کی اپنی ایک کہانی ہوتی ہے، جو دل سے شروع ہو کر منزل تک پہنچتی ہے۔", "Dramatic", "Gold Luxury"
    ),
    "🌸 Anime Cherry Blossom": (
        "Gentle spring breeze blowing pink cherry blossom petals across a traditional Japanese temple garden at sunset, Makoto Shinkai anime aesthetic, ethereal glow.",
        "Anime", "1080p", "16:9 · YouTube / Cinema", 5, "Japanese", "Female",
        "Spring returns, bringing memories of the past.", "Calm", "Neon Glow"
    ),
}
PRESET_CHOICES = ["💡 Select an Inspiration Preset..."] + list(PROMPT_PRESETS.keys())

# ---------------------------------------------------------------------------
# CSS — Ultra-Clean Obsidian Cinema Aesthetics (Gradio 6 Optimized)
# ---------------------------------------------------------------------------

CSS = """
:root, .dark, body, .gradio-container {
  --bg-deep: #05070E;
  --bg-card: rgba(13, 18, 36, 0.85);
  --bg-card-sub: rgba(18, 25, 48, 0.65);
  --border-glass: rgba(99, 102, 241, 0.22);
  --border-focus: #38BDF8;
  --accent-cyan: #38BDF8;
  --accent-indigo: #6366F1;
  --accent-purple: #A855F7;
  --accent-gold: #F59E0B;
  --txt-bright: #F8FAFC;
  --txt-dim: #94A3B8;
  --txt-muted: #64748B;
  
  --body-background-fill: #05070E !important;
  --background-fill-primary: #0A0F1D !important;
  --background-fill-secondary: #0F1629 !important;
  --border-color-primary: rgba(99, 102, 241, 0.20) !important;
  --block-background-fill: rgba(13, 18, 36, 0.85) !important;
  --block-label-text-color: #E2E8F0 !important;
  --block-title-text-color: #FFFFFF !important;
  --body-text-color: #F8FAFC !important;
  --input-background-fill: #080D1A !important;
  --input-border-color: rgba(99, 102, 241, 0.28) !important;
}

body, .gradio-container {
  background:
    radial-gradient(1000px 450px at 15% -5%, rgba(99, 102, 241, 0.16), transparent 60%),
    radial-gradient(900px 400px at 85% -5%, rgba(168, 85, 247, 0.12), transparent 55%),
    linear-gradient(175deg, #03050A 0%, #070B16 100%) !important;
  color: #F8FAFC !important;
  font-family: 'Outfit', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
  max-width: 1580px !important;
  margin: 0 auto !important;
  padding: 12px 18px !important;
}

/* Studio Top Bar */
#studio-navbar {
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  padding: 12px 22px !important;
  margin-bottom: 16px !important;
  background: rgba(12, 17, 34, 0.88) !important;
  border: 1px solid rgba(99, 102, 241, 0.25) !important;
  border-radius: 16px !important;
  backdrop-filter: blur(16px) !important;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.55), inset 0 1px 0 rgba(255, 255, 255, 0.08) !important;
}

.studio-logo {
  font-size: 1.40rem;
  font-weight: 900;
  letter-spacing: -0.02em;
  background: linear-gradient(110deg, #FFFFFF 15%, #C7D2FE 50%, #38BDF8 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.badge-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 0.80rem;
  font-weight: 700;
  letter-spacing: 0.3px;
  border: 1px solid transparent;
}

.badge-kami {
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.16), rgba(168, 85, 247, 0.16));
  border-color: rgba(245, 158, 11, 0.45);
  color: #FDE047;
  box-shadow: 0 0 12px rgba(245, 158, 11, 0.20);
}

.badge-pro {
  background: rgba(56, 189, 248, 0.12);
  border-color: rgba(56, 189, 248, 0.40);
  color: #38BDF8;
}

.badge-4k {
  background: rgba(99, 102, 241, 0.15);
  border-color: rgba(99, 102, 241, 0.40);
  color: #A5B4FC;
}

/* Glass Panels */
.glass-panel, .gr-group, .gr-box, .gr-panel, .block {
  background: rgba(12, 17, 34, 0.88) !important;
  border: 1px solid rgba(99, 102, 241, 0.20) !important;
  border-radius: 14px !important;
}

/* Inputs & Form Controls */
textarea, select, input[type="text"], input[type="number"], .gr-text-input {
  background: #080D1A !important;
  border: 1.5px solid rgba(99, 102, 241, 0.28) !important;
  color: #F8FAFC !important;
  font-size: 0.94rem !important;
  border-radius: 12px !important;
  transition: all 0.2s ease !important;
}

textarea:focus, select:focus, input:focus {
  border-color: #38BDF8 !important;
  box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.22) !important;
  background: #0B1122 !important;
}

/* Primary Action Buttons */
.gr-button-primary, button.primary {
  background: linear-gradient(135deg, #4F46E5 0%, #7C3AED 50%, #0284C7 100%) !important;
  border: none !important;
  color: #FFFFFF !important;
  font-weight: 800 !important;
  font-size: 1.02rem !important;
  padding: 12px 24px !important;
  border-radius: 12px !important;
  box-shadow: 0 6px 20px rgba(79, 70, 229, 0.45) !important;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
  cursor: pointer !important;
}

.gr-button-primary:hover, button.primary:hover {
  transform: translateY(-2px) !important;
  box-shadow: 0 8px 28px rgba(124, 58, 237, 0.65) !important;
  filter: brightness(1.1) !important;
}

.gr-button-secondary, button.secondary {
  background: rgba(22, 30, 52, 0.75) !important;
  border: 1px solid rgba(99, 102, 241, 0.35) !important;
  color: #F8FAFC !important;
  font-weight: 700 !important;
  font-size: 0.90rem !important;
  border-radius: 12px !important;
  padding: 8px 16px !important;
  transition: all 0.18s ease !important;
}

.gr-button-secondary:hover {
  background: rgba(99, 102, 241, 0.25) !important;
  border-color: #38BDF8 !important;
  color: #FFFFFF !important;
  transform: translateY(-1px) !important;
}

.gr-button-stop, button.stop {
  background: rgba(239, 68, 68, 0.15) !important;
  border: 1px solid rgba(239, 68, 68, 0.40) !important;
  color: #FCA5A5 !important;
  font-weight: 700 !important;
  border-radius: 12px !important;
  padding: 10px 18px !important;
  transition: all 0.18s ease !important;
}

.gr-button-stop:hover {
  background: rgba(239, 68, 68, 0.28) !important;
  color: #FFFFFF !important;
}

/* Accordions */
.gr-accordion, .accordion {
  border: 1px solid rgba(99, 102, 241, 0.20) !important;
  border-radius: 12px !important;
  background: rgba(14, 20, 38, 0.50) !important;
  margin: 6px 0 !important;
}

/* Queue Cards */
.q-card {
  padding: 12px 16px;
  margin: 6px 0 10px 0;
  border-radius: 14px;
  background: linear-gradient(135deg, rgba(16, 22, 42, 0.92), rgba(10, 14, 28, 0.95));
  border: 1px solid rgba(99, 102, 241, 0.28);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.4);
}

.q-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.q-prompt {
  font-size: 0.90rem;
  font-weight: 700;
  color: #FFFFFF;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 65%;
}

.q-stage {
  font-size: 0.80rem;
  font-weight: 800;
  padding: 3px 10px;
  border-radius: 16px;
  background: rgba(99, 102, 241, 0.20);
  border: 1px solid rgba(99, 102, 241, 0.40);
}

.q-bar {
  height: 6px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 3px;
  overflow: hidden;
  margin: 4px 0;
}

.q-fill {
  height: 100%;
  background: linear-gradient(90deg, #6366F1, #A855F7, #38BDF8);
  border-radius: 3px;
  transition: width 0.35s ease;
  box-shadow: 0 0 10px rgba(56, 189, 248, 0.6);
}

.hw-badge {
  display: block;
  background: rgba(14, 20, 38, 0.60);
  border: 1px solid rgba(99, 102, 241, 0.22);
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 0.84rem;
  color: #CBD5E1;
  line-height: 1.5;
}

footer { display: none !important; }
"""

# ---------------------------------------------------------------------------
# UI Callback Logic (Non-blocking & Asynchronous)
# ---------------------------------------------------------------------------

def job_progress_percent(progress: float) -> int:
    try:
        return max(0, min(100, int(float(progress) * 100)))
    except (TypeError, ValueError, OverflowError):
        return 0


def render_job_card(
    job_id: str,
    prompt: str,
    stage: Stage,
    progress: float,
    message: str,
    elapsed: Optional[int] = None,
) -> str:
    pct = job_progress_percent(progress)
    icon = STAGE_ICONS.get(stage, "•")
    color = (
        "#10B981"
        if stage == Stage.DONE
        else "#EF4444"
        if stage in (Stage.FAILED, Stage.CANCELLED)
        else "#38BDF8"
    )
    elapsed_html = (
        f"<span style='float:right; opacity:0.75;'>⏱️ {max(0, elapsed)}s</span>"
        if elapsed is not None
        else ""
    )
    safe_job_id = html.escape(str(job_id))
    safe_prompt = html.escape(str(prompt)[:55])
    safe_stage = html.escape(stage.value if hasattr(stage, "value") else str(stage))
    safe_message = html.escape(str(message))

    return f"""
    <div class="q-card">
      <div class="q-head">
        <span class="q-prompt"><b>[{safe_job_id}]</b> {safe_prompt}</span>
        <span class="q-stage" style="color:{color}; font-weight:800;">{icon} {safe_stage} ({pct}%)</span>
      </div>
      <div class="q-bar"><div class="q-fill" style="width:{pct}%;"></div></div>
      <div style="margin-top:6px; font-size:0.84rem; color:#CBD5E1;">
        {safe_message} {elapsed_html}
      </div>
    </div>
    """


def render_queue() -> str:
    jobs = STUDIO.queue.all_jobs()
    if not jobs:
        return (
            "<div class='q-card' style='font-size:0.86rem; color:#94A3B8; text-align:center; padding:18px;'>"
            "🚀 <b>Studio Idle</b>. Enter a creative prompt and click <b>Generate Video</b> to start."
            "</div>"
        )
    cards = []
    for js in reversed(jobs[-6:]):
        cards.append(
            render_job_card(
                js.job_id,
                js.settings.prompt if js.settings else "Job",
                js.stage,
                js.progress,
                js.message,
            )
        )
    return "".join(cards)


_gallery_cache_sig: tuple = ()


def render_gallery():
    items = STUDIO.gallery()
    return [
        (
            it["thumb"] or it["video"],
            f"#{it['id']} • {it['prompt'][:32]}",
        )
        for it in items
        if it.get("video")
    ]


def render_gallery_if_changed():
    global _gallery_cache_sig
    items = STUDIO.gallery()
    sig = tuple((it["id"], it.get("video"), it.get("thumb")) for it in items)
    if sig == _gallery_cache_sig:
        return gr.update()
    _gallery_cache_sig = sig
    return render_gallery()


def gallery_metadata() -> List[Dict[str, Any]]:
    return STUDIO.gallery()


def on_gallery_select(evt: gr.SelectData, meta: List[Dict[str, Any]]) -> Tuple[Any, Any, Any, str]:
    if not meta or evt.index is None or evt.index >= len(meta):
        return gr.update(), gr.update(value=""), gr.update(), ""
    item = meta[evt.index]
    vid_path = item.get("video")
    srt_path = item.get("srt")
    info_md = (
        f"### 🎬 #{item['id']} · {item['prompt']}\n"
        f"**Engine**: `{item.get('backend', 'AI Studio')}` · **Seed**: `{item.get('seed', 'random')}`"
    )
    return (
        vid_path if vid_path and Path(vid_path).exists() else None,
        info_md,
        srt_path if srt_path and Path(srt_path).exists() else None,
        item["id"],
    )


def duration_hint(sec: float) -> str:
    n = max(1, int(-(-float(sec) // 5)))
    if n == 1:
        return "<span style='color:#64748B; font-size:0.82rem;'>⚡ Single continuous shot (≤5s)</span>"
    return (
        f"<span style='color:#38BDF8; font-size:0.82rem;'>🎞️ {int(float(sec))}s video · "
        f"{n} cinematic shots crossfaded & chained</span>"
    )


def load_prompt_preset(selected_label: str):
    if not selected_label or selected_label not in PROMPT_PRESETS:
        return [gr.skip()] * 10
    preset = PROMPT_PRESETS[selected_label]
    return list(preset)


def enhance_prompt_btn(prompt: str, preset: str) -> str:
    if not prompt.strip():
        return ""
    return FreeLLMPromptEnhancer.expand_with_ai(prompt, preset, STUDIO.config)


def submit_video_job(
    prompt: str,
    enhanced_prompt: str,
    negative: str,
    preset: str,
    quality: str,
    aspect_label: str,
    duration: float,
    voiceover_enabled: bool,
    voice_script: str,
    language: str,
    voice_gender: str,
    music_mood: str,
    subtitles_enabled: bool,
    sub_style: str,
    translate_lang: str,
    interpolate_60fps: bool,
    allow_motion_fallback: bool,
    anti_fingerprint: bool,
    watermark_logo: bool,
    seed: float,
) -> Tuple[str, str, str]:
    if not prompt.strip():
        return "⚠️ Please enter a creative prompt first.", render_queue(), ""

    aspect = ASPECT_OPTIONS.get(aspect_label, "16:9")
    final_prompt = (
        enhanced_prompt.strip()
        if (enhanced_prompt and enhanced_prompt.strip())
        else prompt.strip()
    )

    settings = JobSettings(
        mode="ai_video",
        prompt=final_prompt,
        negative_prompt=negative.strip() if negative else "",
        style_preset=preset,
        quality=quality or "1080p",
        aspect_ratio=aspect,
        duration=float(duration),
        language=language,
        voice_gender=voice_gender,
        voice_script=voice_script.strip() if voiceover_enabled else "",
        music_mood=music_mood,
        subtitles_enabled=subtitles_enabled,
        subtitle_style=sub_style,
        translate_lang=translate_lang,
        interpolate_60fps=interpolate_60fps,
        allow_motion_fallback=allow_motion_fallback,
        anti_fingerprint=anti_fingerprint,
        watermark_logo=watermark_logo,
        seed=int(seed) if seed is not None else -1,
    )

    try:
        job_id = STUDIO.queue.submit(settings)
        msg = f"🚀 **Job [{job_id}] submitted to queue!** Running asynchronously in background..."
        return msg, render_queue(), job_id
    except Exception as exc:
        return f"⚠️ Submission error: {html.escape(str(exc))}", render_queue(), ""


def stop_job_btn(job_id: str) -> Tuple[str, str]:
    if not job_id:
        # Cancel most recent running job if no specific ID given
        jobs = STUDIO.queue.all_jobs()
        active = [j for j in jobs if j.stage not in (Stage.DONE, Stage.FAILED, Stage.CANCELLED)]
        if active:
            job_id = active[-1].job_id
        else:
            return "⚠️ No active jobs running.", render_queue()
    success = STUDIO.queue.cancel(job_id)
    msg = f"🛑 Job [{job_id}] cancelled." if success else f"Job [{job_id}] not found."
    return msg, render_queue()


def resume_queue_action() -> Tuple[str, str]:
    STUDIO.queue._auto_resume_interrupted_jobs()
    return "🔄 Queue resumed! Processing pending jobs...", render_queue()


def regenerate_video(loaded_job_id: str) -> Tuple[str, str, str]:
    if not loaded_job_id:
        return "⚠️ Select a video from the Gallery below to regenerate.", render_queue(), ""
    for js in STUDIO.queue.all_jobs():
        if js.job_id == loaded_job_id and js.settings:
            new_settings = dataclasses.replace(
                js.settings,
                job_id=STUDIO.queue.studio.config.colab_url and "" or None,  # new id
            )
            new_settings.job_id = None or (hex(int(time.time() * 1000))[-8:])
            new_id = STUDIO.queue.submit(new_settings)
            return f"♻️ Re-submitting #{loaded_job_id} as new Job [{new_id}]...", render_queue(), new_id
    return f"Could not find settings for #{loaded_job_id}.", render_queue(), ""


def save_api_credentials(colab_url: str, hf_tok: str, gemini_k: str, pexels_k: str, pixabay_k: str) -> Tuple[str, str]:
    STUDIO.config.colab_url = colab_url.strip()
    STUDIO.config.hf_token = hf_tok.strip()
    STUDIO.config.gemini_api_key = gemini_k.strip()
    STUDIO.config.pexels_api_key = pexels_k.strip()
    STUDIO.config.pixabay_api_key = pixabay_k.strip()
    STUDIO.config.save()
    return "✅ API Keys & Settings saved successfully!", STUDIO.backend_status_html()


def open_videos_folder() -> None:
    v_dir = DEFAULT_OUTPUT_DIR / "videos"
    v_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(v_dir))
    else:
        subprocess.Popen(["xdg-open", str(v_dir)])


def read_system_logs() -> str:
    log_file = DEFAULT_OUTPUT_DIR / "logs" / "studio.log"
    if log_file.exists():
        try:
            content = log_file.read_text(encoding="utf-8", errors="replace")
            tail = content.splitlines()[-40:]
            if tail:
                return "\n".join(tail)
        except Exception:
            pass
    return "VideoStudio engine initialized. Logs streaming..."


# ---------------------------------------------------------------------------
# UI Construction (Obsidian Cinema Unified Layout)
# ---------------------------------------------------------------------------

def build_app() -> gr.Blocks:
    with gr.Blocks(title="VideoStudio Pro — 4K AI Video Studio") as app:
        # High-End Studio Top Bar
        with gr.Group(elem_id="studio-navbar"):
            gr.HTML("""
            <div style="display:flex; align-items:center; justify-content:space-between; width:100%; flex-wrap:wrap; gap:12px;">
              <div style="display:flex; align-items:center; gap:14px;">
                <span class="studio-logo">🎬 VideoStudio Pro</span>
                <span class="badge-chip badge-4k">4K Cinema Engine</span>
                <span class="badge-chip badge-pro">⚡ Multi-GPU Cloud Active</span>
              </div>
              <div style="display:flex; align-items:center; gap:10px;">
                <span class="badge-chip badge-kami">👑 Kamran Ashraf (Kami)</span>
              </div>
            </div>
            """)

        gallery_meta_state = gr.State([])
        active_video_id_state = gr.State("")

        with gr.Row():
            # =================================================================
            # LEFT COLUMN: Creative Controls & Studio Parameters (scale=6)
            # =================================================================
            with gr.Column(scale=6):
                # Prompt & Inspiration
                with gr.Row():
                    preset_pick = gr.Dropdown(
                        choices=PRESET_CHOICES,
                        value=PRESET_CHOICES[0],
                        label="💡 Load Creative Inspiration",
                        scale=4,
                    )
                    ai_enhance_btn = gr.Button("✨ Enhance with AI", variant="secondary", scale=2)

                prompt_input = gr.Textbox(
                    label="Creative Prompt / Scene Vision",
                    placeholder="Describe your scene in cinematic detail (optics, lighting, environment, camera motion)...",
                    lines=3,
                    value=PROMPT_PRESETS["🦅 Himalayan Eagle"][0],
                )

                with gr.Accordion("📝 Fine-Tune Enhanced Prompt (Optional)", open=False):
                    enhanced_input = gr.Textbox(
                        label="Expanded AI Prompt (leave blank to auto-use main prompt)",
                        placeholder="Click '✨ Enhance with AI' above to generate cinematography details...",
                        lines=2,
                        value="",
                    )

                # Format, Resolution & Duration
                with gr.Row():
                    quality_pick = gr.Dropdown(
                        label="Quality Tier",
                        choices=QUALITY_TIERS,
                        value="1080p",
                    )
                    aspect_pick = gr.Dropdown(
                        label="Aspect Ratio",
                        choices=ASPECT_CHOICES,
                        value="16:9 · YouTube / Cinema",
                    )
                    style_pick = gr.Dropdown(
                        label="Visual Style",
                        choices=STYLES_LIST,
                        value="Cinematic",
                    )
                    duration_slider = gr.Slider(
                        label="Duration (seconds)",
                        minimum=2,
                        maximum=60,
                        value=6,
                        step=1,
                    )

                dur_hint_html = gr.HTML(duration_hint(6))

                # Drawer 1: Neural Voiceover & Background Music
                with gr.Accordion("🎙️ AI Voiceover & Background Music (Optional)", open=True):
                    with gr.Row():
                        vo_enable = gr.Checkbox(label="Enable Neural Voiceover", value=True)
                        vo_gender = gr.Radio(label="Voice Actor", choices=["Female", "Male"], value="Female")
                        vo_lang = gr.Dropdown(label="Language", choices=LANGUAGES, value="English")
                    vo_script_input = gr.Textbox(
                        label="Spoken Script / Narration (Blank = reads scene prompt)",
                        placeholder="Type narration spoken by the voice actor...",
                        lines=2,
                        value=PROMPT_PRESETS["🦅 Himalayan Eagle"][6],
                    )
                    with gr.Row():
                        music_pick = gr.Dropdown(label="Music Mood Bed", choices=MUSIC_MOODS, value="Ambient")

                # Drawer 2: Styled Subtitles & Typography
                with gr.Accordion("☰ Styled Karaoke Subtitles (Optional)", open=False):
                    with gr.Row():
                        subs_enable = gr.Checkbox(label="Burn Styled Subtitles into Video", value=True)
                        sub_style_pick = gr.Dropdown(label="Subtitle Style Preset", choices=SUB_STYLES_LIST, value="Classic White")
                        sub_translate_pick = gr.Dropdown(label="Translate Subtitles To", choices=SUBTITLE_LANGS, value="None")

                # Drawer 3: Advanced Studio Parameters & Cloud Backends
                with gr.Accordion("⚙️ Advanced AI Parameters & GPU Credentials", open=False):
                    neg_prompt_input = gr.Textbox(
                        label="Negative Prompt (Artifacts to suppress)",
                        placeholder="blurry, distorted, low resolution, watermark...",
                        lines=1,
                        value="",
                    )
                    with gr.Row():
                        motion_fallback_chk = gr.Checkbox(
                            label="Allow Motion Synthesis Fallback when Cloud GPU is busy",
                            value=True,
                        )
                        interp_60_chk = gr.Checkbox(label="60 FPS Motion Interpolation", value=False)
                        anti_fp_chk = gr.Checkbox(label="Anti-Fingerprint Filter", value=False)
                        watermark_chk = gr.Checkbox(label="Branded Watermark", value=False)
                    seed_input = gr.Number(label="Seed (-1 for Random)", value=-1, precision=0)

                    gr.Markdown("#### 🔌 Cloud GPU & AI Keys")
                    with gr.Row():
                        cfg_colab = gr.Textbox(
                            label="Colab Worker URL (*.gradio.live)",
                            value=STUDIO.config.colab_url,
                        )
                        cfg_hf = gr.Textbox(
                            label="Hugging Face Token (ZeroGPU)",
                            value=STUDIO.config.hf_token,
                            type="password",
                        )
                    with gr.Row():
                        cfg_gemini = gr.Textbox(
                            label="Gemini API Key (Pro Plan)",
                            value=STUDIO.config.gemini_api_key,
                            type="password",
                        )
                        cfg_pexels = gr.Textbox(
                            label="Pexels Key",
                            value=STUDIO.config.pexels_api_key,
                            type="password",
                        )
                        cfg_pixabay = gr.Textbox(
                            label="Pixabay Key",
                            value=STUDIO.config.pixabay_api_key,
                            type="password",
                        )
                    save_cfg_btn = gr.Button("💾 Save API Credentials", variant="secondary")
                    cfg_status_msg = gr.Markdown("")

                # Primary Action Buttons
                with gr.Row():
                    generate_btn = gr.Button("🚀 Generate Video", variant="primary", size="lg", scale=4)
                    stop_btn = gr.Button("🛑 Cancel Job", variant="stop", size="lg", scale=1)

                status_msg_box = gr.Markdown("")

            # =================================================================
            # RIGHT COLUMN: Cinema Viewport, Gallery & Live Queue (scale=5)
            # =================================================================
            with gr.Column(scale=5):
                # System Status Header
                with gr.Group():
                    gr.HTML(f"""
                    <div class="hw-badge">
                      <b>🖥️ Hardware:</b> {STUDIO.probe.summary()}<br>
                      <div id="backend-status-container" style="margin-top:4px;">
                        {STUDIO.backend_status_html()}
                      </div>
                    </div>
                    """)

                # 4K Cinema Viewport & Player
                gr.Markdown("### 🎬 Cinema Viewport & Player")
                video_player = gr.Video(
                    label="4K Video Preview",
                    interactive=False,
                    height=320,
                )
                video_info_md = gr.Markdown("**Select a video from the Gallery below or generate a new scene.**")

                # Action Toolbar
                with gr.Row():
                    srt_download = gr.File(label="Subtitles (.srt)", interactive=False, scale=2)
                    regen_btn = gr.Button("♻️ Regenerate (Same Seed)", variant="secondary", scale=2)
                    open_dir_btn = gr.Button("📂 Open Videos Folder", variant="secondary", scale=2)

                # Production Gallery
                gr.Markdown("### 🎞️ Finished Videos Gallery")
                gallery_view = gr.Gallery(
                    value=render_gallery(),
                    columns=3,
                    height=250,
                    object_fit="cover",
                    label="Completed Videos",
                    show_label=False,
                )

                # Live Queue & Progress
                with gr.Row():
                    gr.Markdown("### 📋 Live Queue & Progress")
                    resume_q_btn = gr.Button("▶️ Resume Queue", variant="primary", size="sm", scale=1)
                    refresh_q_btn = gr.Button("⚡ Refresh", variant="secondary", size="sm", scale=1)

                queue_box_html = gr.HTML(render_queue())

                # Live Logs Drawer
                with gr.Accordion("📋 Live Studio Logs", open=False):
                    log_console = gr.Textbox(label="", lines=6, interactive=False, show_label=False)
                    refresh_log_btn = gr.Button("🔄 Refresh Logs", variant="secondary", size="sm")

        # ---------------------------------------------------------------------
        # Event Wiring & Dynamic Callbacks
        # ---------------------------------------------------------------------

        # Preset selection
        preset_pick.change(
            load_prompt_preset,
            inputs=[preset_pick],
            outputs=[
                prompt_input, style_pick, quality_pick, aspect_pick, duration_slider,
                vo_lang, vo_gender, vo_script_input, music_pick, sub_style_pick,
            ],
        )

        # Duration hint
        duration_slider.change(duration_hint, inputs=[duration_slider], outputs=[dur_hint_html])

        # Prompt AI expansion
        ai_enhance_btn.click(
            enhance_prompt_btn,
            inputs=[prompt_input, style_pick],
            outputs=[enhanced_input],
        )

        # Submit generation (Non-blocking asynchronous submission)
        generate_btn.click(
            submit_video_job,
            inputs=[
                prompt_input, enhanced_input, neg_prompt_input, style_pick,
                quality_pick, aspect_pick, duration_slider, vo_enable,
                vo_script_input, vo_lang, vo_gender, music_pick,
                subs_enable, sub_style_pick, sub_translate_pick,
                interp_60_chk, motion_fallback_chk, anti_fp_chk, watermark_chk, seed_input,
            ],
            outputs=[status_msg_box, queue_box_html, active_video_id_state],
        )

        # Stop / Cancel Job
        stop_btn.click(stop_job_btn, inputs=[active_video_id_state], outputs=[status_msg_box, queue_box_html])

        # Resume Queue
        resume_q_btn.click(resume_queue_action, outputs=[status_msg_box, queue_box_html])

        # Refresh Queue manually
        refresh_q_btn.click(render_queue, outputs=[queue_box_html])

        # Gallery Card Click -> Load video and metadata
        gallery_view.select(
            on_gallery_select,
            inputs=[gallery_meta_state],
            outputs=[video_player, video_info_md, srt_download, active_video_id_state],
        )

        # Regenerate loaded video
        regen_btn.click(regenerate_video, inputs=[active_video_id_state], outputs=[status_msg_box, queue_box_html, active_video_id_state])

        # Open Output Folder
        open_dir_btn.click(open_videos_folder)

        # Save Credentials
        save_cfg_btn.click(
            save_api_credentials,
            inputs=[cfg_colab, cfg_hf, cfg_gemini, cfg_pexels, cfg_pixabay],
            outputs=[cfg_status_msg, queue_box_html],
        )

        # Refresh Logs
        refresh_log_btn.click(read_system_logs, outputs=[log_console])

        # Periodic Live Background Refreshers (Queue & Gallery sync every 2.0s without UI reload)
        refresh_timer = gr.Timer(2.0)
        refresh_timer.tick(render_queue, None, queue_box_html)
        refresh_timer.tick(render_gallery_if_changed, None, gallery_view)
        refresh_timer.tick(gallery_metadata, None, gallery_meta_state)

        # Initial Page Load
        app.load(read_system_logs, outputs=[log_console])
        app.load(gallery_metadata, outputs=[gallery_meta_state])
        app.load(render_queue, outputs=[queue_box_html])

    return app


def launch_app(server_port: int = 7860):
    app = build_app()
    app.queue(default_concurrency_limit=10)
    managed = os.environ.get("VS_MANAGED_LAUNCH") == "1"

    # Auto-find free port if 7860 is taken
    target_port = server_port
    import socket

    def _port_free(p: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("127.0.0.1", p)) != 0

    if not _port_free(target_port):
        for candidate in range(7860, 7880):
            if _port_free(candidate):
                target_port = candidate
                break

    log.info("Launching VideoStudio Pro on http://127.0.0.1:%d", target_port)
    app.launch(
        server_name="127.0.0.1",
        server_port=target_port,
        inbrowser=not managed,
        css=CSS,
        show_error=True,
    )


if __name__ == "__main__":
    launch_app()
