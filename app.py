"""
app.py — 4K Ultra HD Streamlined VideoStudio Pro.
Designed with precision for Kamran Ashraf (Kami).

Launch:  python app.py
Opens at: http://127.0.0.1:7860
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

import gradio as gr

from video_studio import (
    ASPECT_SIZES, DEFAULT_OUTPUT_DIR, LANG_CODES, MUSIC_MOODS, QUALITY_TIERS,
    STYLE_PRESETS, SUBTITLE_STYLES, TTS_VOICES, AudioEngine, FreeLLMPromptEnhancer,
    HardwareProbe, JobSettings, PromptEngine, Stage, StudioConfig, SubtitleEngine,
    VideoEngine, VideoStudio, log, log_queue,
)

STUDIO = VideoStudio()

ASPECT_LABELS = {
    "16:9": "16:9 · YouTube / Cinema",
    "9:16": "9:16 · Reels / Shorts / TikTok",
    "1:1": "1:1 · Square / Instagram",
    "21:9": "21:9 · Ultrawide 4K",
    "4:3": "4:3 · Classic TV / Vintage",
}

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

# ---------------------------------------------------------------------------
# CSS — Ultra-Clean 4K HDR Obsidian Studio Aesthetics
# ---------------------------------------------------------------------------

CSS = """
:root, .dark, body, .gradio-container {
  --bg-dark: #05070E;
  --bg-card: rgba(12, 17, 32, 0.88);
  --bg-card-sub: rgba(18, 25, 46, 0.70);
  --glass-border: rgba(99, 102, 241, 0.24);
  --glass-border-focus: rgba(56, 189, 248, 0.70);
  --accent-blue: #38BDF8;
  --accent-indigo: #6366F1;
  --accent-purple: #A855F7;
  --gold: #F59E0B;
  --txt-main: #F8FAFC;
  --txt-muted: #94A3B8;
  --txt-dim: #64748B;
  
  --body-background-fill: #05070E !important;
  --background-fill-primary: #0A0E1A !important;
  --background-fill-secondary: #10162A !important;
  --border-color-primary: rgba(99, 102, 241, 0.25) !important;
  --block-background-fill: rgba(12, 17, 32, 0.88) !important;
  --block-label-text-color: #E2E8F0 !important;
  --block-title-text-color: #FFFFFF !important;
  --body-text-color: #F8FAFC !important;
  --body-text-color-subdued: #94A3B8 !important;
  --input-background-fill: #080C18 !important;
  --input-border-color: rgba(99, 102, 241, 0.28) !important;
  --input-placeholder-color: #475569 !important;
}

body, .gradio-container {
  background:
    radial-gradient(1200px 500px at 15% -5%, rgba(99, 102, 241, 0.18), transparent 60%),
    radial-gradient(1000px 450px at 85% -5%, rgba(168, 85, 247, 0.14), transparent 55%),
    linear-gradient(175deg, #04060C 0%, #070B16 100%) !important;
  color: #F8FAFC !important;
  font-family: 'Outfit', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
  max-width: 1560px !important;
  margin: 0 auto !important;
  padding: 10px 16px !important;
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* Slim Studio Top Bar */
#studio-navbar {
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  padding: 10px 20px !important;
  margin-bottom: 14px !important;
  background: rgba(11, 16, 30, 0.85) !important;
  border: 1px solid rgba(99, 102, 241, 0.25) !important;
  border-radius: 16px !important;
  backdrop-filter: blur(20px) saturate(180%) !important;
  -webkit-backdrop-filter: blur(20px) saturate(180%) !important;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.08) !important;
}

.studio-logo {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-size: 1.35rem;
  font-weight: 900;
  letter-spacing: -0.02em;
  background: linear-gradient(110deg, #FFFFFF 10%, #C7D2FE 50%, #38BDF8 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.badge-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 0.82rem;
  font-weight: 700;
  letter-spacing: 0.3px;
  border: 1px solid transparent;
  transition: all 0.2s ease;
}

.badge-kami {
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.15), rgba(168, 85, 247, 0.15));
  border-color: rgba(245, 158, 11, 0.45);
  color: #FDE047;
  box-shadow: 0 0 12px rgba(245, 158, 11, 0.25);
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

/* Master Glass Panels — NO backdrop-filter here so position:fixed dropdowns anchor to viewport */
.glass-panel, .gr-group, .gr-box, .gr-panel, .tabitem, .block {
  background: rgba(11, 16, 30, 0.92) !important;
  border: 1px solid rgba(99, 102, 241, 0.22) !important;
  border-radius: 16px !important;
}

.gr-form, .gr-row, .gr-column, .block, .form, .tabitem {
  overflow: visible !important;
}

/* Modern Tab Bar */
.tab-nav, div[role="tablist"] {
  border-bottom: 1.5px solid rgba(99, 102, 241, 0.25) !important;
  gap: 10px !important;
  margin-bottom: 14px !important;
  padding: 4px 0 !important;
}

.tab-nav button, button[role="tab"] {
  background: rgba(255, 255, 255, 0.04) !important;
  border: 1px solid rgba(255, 255, 255, 0.08) !important;
  color: #CBD5E1 !important;
  font-size: 0.96rem !important;
  font-weight: 700 !important;
  padding: 9px 20px !important;
  border-radius: 12px !important;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

.tab-nav button:hover, button[role="tab"]:hover {
  background: rgba(99, 102, 241, 0.18) !important;
  border-color: rgba(56, 189, 248, 0.5) !important;
  color: #FFFFFF !important;
  transform: translateY(-1px) !important;
}

.tab-nav button.selected, button[role="tab"][aria-selected="true"], button[role="tab"].selected {
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.35), rgba(168, 85, 247, 0.30)) !important;
  border: 1.5px solid #38BDF8 !important;
  color: #FFFFFF !important;
  font-weight: 800 !important;
  box-shadow: 0 0 16px rgba(56, 189, 248, 0.35) !important;
}

/* Inputs, Textareas & Form Controls */
textarea, select, .gr-text-input {
  background: #080C18 !important;
  border: 1.5px solid rgba(99, 102, 241, 0.28) !important;
  color: #F8FAFC !important;
  font-size: 0.94rem !important;
  border-radius: 12px !important;
  padding: 9px 12px !important;
  transition: all 0.2s ease !important;
}

textarea:focus, select:focus {
  border-color: #38BDF8 !important;
  box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.25) !important;
  background: #0B1020 !important;
}

/* Gradio 6 Dropdown & Popup Fixes */
.wrap, .wrap-default, .secondary-wrap {
  background: #080C18 !important;
  border: 1.5px solid rgba(99, 102, 241, 0.28) !important;
  border-radius: 12px !important;
  cursor: pointer !important;
}

.wrap input, .wrap-default input, .secondary-wrap input {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  outline: none !important;
  color: #FFFFFF !important;
  font-size: 0.94rem !important;
  cursor: pointer !important;
}

.options, ul.options, [role="listbox"] {
  background: #0F162A !important;
  border: 1.5px solid #38BDF8 !important;
  border-radius: 12px !important;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.9) !important;
  max-height: 280px !important;
  z-index: 999999 !important;
}

.options li, ul.options li, [role="listbox"] li, .item.svelte-1ou0lab {
  color: #F8FAFC !important;
  padding: 10px 14px !important;
  font-size: 0.92rem !important;
  font-weight: 600 !important;
  cursor: pointer !important;
  transition: background 0.15s ease !important;
}

.options li:hover, ul.options li:hover, [role="listbox"] li:hover, .item.svelte-1ou0lab:hover, .active.svelte-1ou0lab {
  background: rgba(99, 102, 241, 0.45) !important;
  color: #38BDF8 !important;
}

label, span.label-text, .block-title, label span {
  color: #E2E8F0 !important;
  font-weight: 700 !important;
  font-size: 0.88rem !important;
  margin-bottom: 3px !important;
}

/* Clean Compact Accordions */
.gr-accordion, .accordion {
  border: 1px solid rgba(99, 102, 241, 0.22) !important;
  border-radius: 14px !important;
  background: rgba(14, 20, 36, 0.55) !important;
  margin: 8px 0 !important;
  overflow: hidden !important;
}

.gr-accordion-header, .accordion-header {
  color: #F1F5F9 !important;
  font-weight: 700 !important;
  font-size: 0.92rem !important;
  padding: 10px 14px !important;
}

/* Action Buttons */
.gr-button-primary, button.primary {
  background: linear-gradient(135deg, #4F46E5 0%, #7C3AED 50%, #0284C7 100%) !important;
  border: none !important;
  color: #FFFFFF !important;
  font-weight: 800 !important;
  font-size: 1.02rem !important;
  padding: 11px 22px !important;
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
  background: rgba(26, 35, 58, 0.75) !important;
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
  background: rgba(239, 68, 68, 0.14) !important;
  border: 1px solid rgba(239, 68, 68, 0.38) !important;
  color: #FCA5A5 !important;
  font-weight: 700 !important;
  border-radius: 12px !important;
  padding: 10px 18px !important;
  transition: all 0.18s ease !important;
}

.gr-button-stop:hover {
  background: rgba(239, 68, 68, 0.28) !important;
  border-color: #EF4444 !important;
  color: #FFFFFF !important;
}

/* Cinema Viewport Container */
.viewport-box {
  background: #020409 !important;
  border: 1.5px solid rgba(99, 102, 241, 0.30) !important;
  border-radius: 16px !important;
  overflow: hidden !important;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.7) !important;
}

/* Live Progress Cards */
.q-card {
  padding: 12px 16px;
  margin: 6px 0 10px 0;
  border-radius: 14px;
  background: linear-gradient(135deg, rgba(16, 22, 40, 0.90), rgba(10, 14, 26, 0.95));
  border: 1px solid rgba(99, 102, 241, 0.30);
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

/* Hardware & System Status Badges */
.hw-badge {
  display: block;
  background: rgba(14, 20, 36, 0.65);
  border: 1px solid rgba(99, 102, 241, 0.25);
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 0.86rem;
  color: #CBD5E1;
  line-height: 1.5;
}

footer { display: none !important; }
"""

# ---------------------------------------------------------------------------
# Demo Presets Dictionary (4K Quality Preset)
# ---------------------------------------------------------------------------

DEMO_AI_PRESETS = {
    "cyberpunk": (
        "A sleek futuristic neon hypercar drifting through rain-slicked Tokyo streets at night, vibrant cyan and magenta reflections, ultra-detailed 35mm cinema lens, 4k ultra hd.",
        "Cyberpunk", "4K", "16:9", 8, "English", "Male", "Night city beats with neon speed.", "Dramatic", "Neon Glow"
    ),
    "eagle": (
        "A majestic golden eagle soaring gracefully above snow-capped Himalayan peaks in dramatic sunrise volumetric light, 8k nature documentary masterpiece.",
        "Documentary", "4K", "16:9", 6, "English", "Female", "High above the world, freedom finds its true horizon.", "Ambient", "Classic White"
    ),
    "space": (
        "An astronaut floating gently past a glowing bioluminescent alien nebula with shimmering stardust and colossal ringed planets in deep cosmos, 4k resolution.",
        "Cyberpunk", "4K", "16:9", 8, "English", "Male", "Beyond the boundaries of our solar system lies the infinite unknown.", "Ambient", "Neon Glow"
    ),
    "ocean": (
        "Sunlight piercing crystal clear turquoise ocean waters revealing a vibrant coral reef kingdom with sea turtles and glowing exotic marine life, 4k hdr.",
        "Hyper-realistic", "4K", "16:9", 6, "English", "Female", "Beneath the surface lies a tranquil universe untouched by time.", "Calm", "Neon Glow"
    ),
    "urdu": (
        "شہر کی خوبصورت بارش میں چلتی ہوئی پرانی گاڑی اور شام کے سائے، خوبصورت سینما فوٹیج",
        "Cinematic", "4K", "16:9", 6, "Urdu", "Male", "ہر سفر کی اپنی ایک کہانی ہوتی ہے، جو دل سے شروع ہو کر منزل تک پہنچتی ہے۔", "Dramatic", "Gold Luxury"
    )
}

DEMO_STORY_PRESETS = {
    "pyramids": (
        "The Lost Secrets of Ancient Pyramids",
        "Visual: aerial pyramids golden hour sunrise\nVO: For five thousand years, the ancient pyramids have guarded the deepest secrets of human history.\nVisual: close up hieroglyphs inside tomb torches\nVO: Carved in stone, ancient architects encoded knowledge of the stars.\nVisual: desert night sky milky way over pyramids\nVO: Aligning perfectly with the cosmos, they remain an eternal monument to wonder.",
        "Cinematic", "16:9", "English", "Male", "Dramatic"
    ),
    "ai_future": (
        "The Quantum AI Revolution",
        "Visual: glowing quantum neural network circuits\nVO: We stand on the precipice of the greatest technological revolution in human history.\nVisual: robot hand shaking human hand in modern lab\nVO: Artificial intelligence is no longer science fiction—it is reshaping our world.\nVisual: futuristic glowing smart city skyline\nVO: Empowering human creativity to reach heights never before imagined.",
        "Cyberpunk", "16:9", "English", "Female", "Uplifting"
    ),
    "ocean_giants": (
        "Mysteries of the Deep Ocean",
        "Visual: deep ocean sunlight fading into blue abyss\nVO: Covering seventy percent of our planet, the deep sea remains less explored than the moon.\nVisual: bioluminescent glowing jellyfish in deep darkness\nVO: In complete darkness, mysterious creatures generate their own living light.\nVisual: giant blue whale gliding gracefully through ocean\nVO: The silent giants of the deep remind us of the majesty of Earth.",
        "Hyper-realistic", "16:9", "English", "Male", "Ambient"
    ),
    "urdu_motivation": (
        "کامیابی اور ہمت کا راستہ",
        "Visual: high mountain climber reaching summit sunrise\nVO: زندگی میں کامیابی صرف خواب دیکھنے سے نہیں، بلکہ ہر مشکل کا ڈٹ کر مقابلہ کرنے سے ملتی ہے۔\nVisual: sunrise over golden valley landscape\nVO: ہر نئی صبح ایک نیا موقع لے کر آتی ہے کہ آپ اپنی تقدیر خود لکھیں۔\nVisual: eagle flying across clouds into sun\nVO: جب ارادے پختہ ہوں تو آسمان کی بلندی بھی قدم چومتی ہے۔",
        "Cinematic", "16:9", "Urdu", "Male", "Dramatic"
    )
}

AI_TEMPLATE_CHOICES = [
    "💡 Select a 4K Template...",
    "🦅 Golden Eagle — Himalayan Sunrise 4K",
    "🏎️ Cyberpunk Chase — Neon Tokyo 4K",
    "🌌 Deep Space — Alien Nebula 4K",
    "🌊 Ocean Kingdom — Coral Reef 4K",
    "🇵🇰 اردو سینما — Classic City 4K",
]

AI_TEMPLATE_MAP = {
    "🦅 Golden Eagle — Himalayan Sunrise 4K": "eagle",
    "🏎️ Cyberpunk Chase — Neon Tokyo 4K": "cyberpunk",
    "🌌 Deep Space — Alien Nebula 4K": "space",
    "🌊 Ocean Kingdom — Coral Reef 4K": "ocean",
    "🇵🇰 اردو سینما — Classic City 4K": "urdu",
}

STORY_TEMPLATE_CHOICES = [
    "💡 Select a Storyboard Template...",
    "📜 Ancient Pyramids — Lost Secrets",
    "📜 Quantum AI — Future Revolution",
    "📜 Deep Ocean — Giants of the Deep",
    "🇵🇰 اردو سبق آموز کہانی — ہمت کا راستہ",
]

STORY_TEMPLATE_MAP = {
    "📜 Ancient Pyramids — Lost Secrets": "pyramids",
    "📜 Quantum AI — Future Revolution": "ai_future",
    "📜 Deep Ocean — Giants of the Deep": "ocean_giants",
    "🇵🇰 اردو سبق آموز کہانی — ہمت کا راستہ": "urdu_motivation",
}

def load_ai_template(selected_label: str):
    key = AI_TEMPLATE_MAP.get(selected_label)
    if not key:
        return [gr.skip()] * 10
    preset = DEMO_AI_PRESETS.get(key)
    return list(preset)

def load_story_template(selected_label: str):
    key = STORY_TEMPLATE_MAP.get(selected_label)
    if not key:
        return [gr.skip()] * 7
    preset = DEMO_STORY_PRESETS.get(key)
    return list(preset)

# ---------------------------------------------------------------------------
# Studio Callback Functions
# ---------------------------------------------------------------------------

def enhance_prompt_btn(prompt: str, preset: str) -> str:
    if not prompt.strip():
        return ""
    return FreeLLMPromptEnhancer.expand_with_ai(prompt, preset, STUDIO.config)

def generate_script_btn(topic: str, scenes: int, language: str) -> str:
    if not topic.strip():
        return ""
    return FreeLLMPromptEnhancer.generate_script(topic, int(scenes), language)

def duration_hint(sec) -> str:
    n = max(1, int(-(-float(sec) // 5)))
    if n == 1:
        return "<span style='color:#64748B;font-size:0.82rem;'>⚡ Single 4K AI shot (≤5s)</span>"
    return (f"<span style='color:#38BDF8;font-size:0.82rem;'>🎞️ {int(float(sec))}s 4K · "
            f"{n} AI shots chained & crossfaded</span>")

def generate_ai_video_live(
    prompt, negative, preset, quality, aspect, duration, language, gender,
    custom_script, music, burn_subs, sub_style, tr_lang, interp_60, anti_fp, watermark, seed
):
    if not prompt.strip():
        yield "⚠️ Please enter a prompt first.", "<div class='q-card'><b>Enter a prompt</b> to generate 4K video.</div>", None, None, ""
        return

    sett = JobSettings(
        mode="ai_video",
        prompt=prompt.strip(),
        negative_prompt=negative.strip() if negative else "",
        style_preset=preset,
        quality=quality or "4K",
        aspect_ratio=aspect or "16:9",
        duration=float(duration),
        language=language,
        voice_gender=gender,
        voice_script=custom_script.strip() if custom_script else "",
        music_mood=music,
        subtitles_enabled=burn_subs,
        subtitle_style=sub_style,
        translate_lang=tr_lang,
        interpolate_60fps=interp_60,
        anti_fingerprint=anti_fp,
        watermark_logo=watermark,
        seed=int(seed),
    )
    job_id = STUDIO.queue.submit(sett)

    start_t = time.time()
    while True:
        status = STUDIO.queue.get_status(job_id)
        if not status:
            time.sleep(1.0)
            continue

        pct = int(status.progress * 100)
        icon = STAGE_ICONS.get(status.stage, "•")
        color = "#10B981" if status.stage == Stage.DONE else "#EF4444" if status.stage in (Stage.FAILED, Stage.CANCELLED) else "#38BDF8"
        elapsed = int(time.time() - start_t)

        card_html = f"""
        <div class="q-card">
          <div class="q-head">
            <span class="q-prompt"><b>[{job_id}]</b> {status.settings.prompt[:40] if status.settings else 'Job'}</span>
            <span class="q-stage" style="color:{color}; font-weight:800;">{icon} {status.stage.value} ({pct}%)</span>
          </div>
          <div class="q-bar"><div class="q-fill" style="width:{pct}%;"></div></div>
          <div style="margin-top:6px; font-size:0.84rem; color:#CBD5E1;">
            {status.message} <span style="float:right; opacity:0.7;">⏱️ {elapsed}s</span>
          </div>
        </div>
        """

        if status.stage == Stage.DONE:
            v_out = status.video_path if (status.video_path and Path(status.video_path).exists()) else None
            srt_out = status.srt_path if (status.srt_path and Path(status.srt_path).exists()) else None
            yield f"🎉 4K Video Completed: {job_id}", card_html, v_out, srt_out, job_id
            break
        elif status.stage == Stage.CANCELLED:
            yield f"🛑 Job Cancelled: {job_id}", card_html, None, None, job_id
            break
        elif status.stage == Stage.FAILED:
            yield f"❌ Job Error: {status.error or 'Failed'}", card_html, None, None, job_id
            break

        yield f"⏳ Rendering 4K ({pct}%)...", card_html, None, None, job_id
        time.sleep(1.0)

def generate_storyboard_video_live(
    topic, script_text, preset, aspect, language, gender, music, burn_subs, sub_style, watermark, anti_fp
):
    if not script_text.strip() and not topic.strip():
        yield "⚠️ Please enter a topic or script.", "<div class='q-card'><b>Enter a script</b> to build video.</div>", None, ""
        return

    sett = JobSettings(
        mode="script_story",
        prompt=topic.strip() or "Storyboard Scene",
        script_text=script_text.strip(),
        style_preset=preset,
        aspect_ratio=aspect or "16:9",
        language=language,
        voice_gender=gender,
        music_mood=music,
        subtitles_enabled=burn_subs,
        subtitle_style=sub_style,
        watermark_logo=watermark,
        anti_fingerprint=anti_fp,
    )
    job_id = STUDIO.queue.submit(sett)

    start_t = time.time()
    while True:
        status = STUDIO.queue.get_status(job_id)
        if not status:
            time.sleep(1.0)
            continue

        pct = int(status.progress * 100)
        icon = STAGE_ICONS.get(status.stage, "•")
        color = "#10B981" if status.stage == Stage.DONE else "#EF4444" if status.stage in (Stage.FAILED, Stage.CANCELLED) else "#38BDF8"
        elapsed = int(time.time() - start_t)

        card_html = f"""
        <div class="q-card">
          <div class="q-head">
            <span class="q-prompt"><b>[{job_id}]</b> {status.settings.prompt[:40] if status.settings else 'Job'}</span>
            <span class="q-stage" style="color:{color}; font-weight:800;">{icon} {status.stage.value} ({pct}%)</span>
          </div>
          <div class="q-bar"><div class="q-fill" style="width:{pct}%;"></div></div>
          <div style="margin-top:6px; font-size:0.84rem; color:#CBD5E1;">
            {status.message} <span style="float:right; opacity:0.7;">⏱️ {elapsed}s</span>
          </div>
        </div>
        """

        if status.stage == Stage.DONE:
            v_out = status.video_path if (status.video_path and Path(status.video_path).exists()) else None
            yield f"🎉 Storyboard Movie Completed: {job_id}", card_html, v_out, job_id
            break
        elif status.stage == Stage.CANCELLED:
            yield f"🛑 Storyboard Cancelled: {job_id}", card_html, None, job_id
            break
        elif status.stage == Stage.FAILED:
            yield f"❌ Storyboard Error: {status.error or 'Failed'}", card_html, None, job_id
            break

        yield f"⏳ Building Storyboard ({pct}%)...", card_html, None, job_id
        time.sleep(1.0)

def cancel_active_job_btn(job_id: str) -> str:
    if not job_id:
        return "⚠️ No active job selected."
    success = STUDIO.queue.cancel(job_id)
    return f"🛑 Job [{job_id}] stopped." if success else f"Job [{job_id}] not found."

def resume_all_interrupted_jobs_btn() -> str:
    STUDIO.queue._auto_resume_interrupted_jobs()
    return "🔄 Interrupted jobs restored & resumed!"

def save_api_credentials(colab_url: str, hf_tok: str, pexels_k: str, pixabay_k: str, gemini_k: str) -> str:
    STUDIO.config.colab_url = colab_url.strip()
    STUDIO.config.hf_token = hf_tok.strip()
    STUDIO.config.pexels_api_key = pexels_k.strip()
    STUDIO.config.pixabay_api_key = pixabay_k.strip()
    STUDIO.config.gemini_api_key = gemini_k.strip()
    STUDIO.config.save()
    return "✅ Configuration & API Keys saved successfully!"

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
            tail = content.splitlines()[-50:]
            if tail:
                return "\n".join(tail)
        except Exception:
            pass
    return "Studio ready. Logs streaming..."

def backend_status_html() -> str:
    labels = {
        "colab": "Colab T4 GPU Worker (Wan2.1)",
        "ltx": "LTX-Video (HF ZeroGPU)",
        "cogvideox": "CogVideoX-5B (ZeroGPU)",
        "wan_official": "Wan2.1-14B (Public Queue)",
    }
    rows = []
    for name in ("colab", "ltx", "cogvideox", "wan_official"):
        be = STUDIO.backends.get(name)
        ok = bool(be and be.available())
        rows.append(f"{'🟢' if ok else '⚪'} <b>{labels.get(name, name)}</b>: {'Connected' if ok else 'Offline/Standby'}")
    return "<div class='hw-badge'>" + "<br>".join(rows) + "</div>"

def render_queue_table() -> str:
    jobs = STUDIO.queue.all_jobs()
    if not jobs:
        return "<div class='q-card' style='font-size:0.86rem; color:#94A3B8;'>No jobs in queue. Submit a 4K Video or Storyboard to begin.</div>"
    cards = []
    for js in reversed(jobs[-8:]):
        pct = int(js.progress * 100)
        icon = STAGE_ICONS.get(js.stage, "•")
        color = "#10B981" if js.stage == Stage.DONE else "#EF4444" if js.stage == Stage.CANCELLED else "#F59E0B" if js.stage == Stage.FAILED else "#38BDF8"
        cards.append(f"""
        <div class="q-card">
          <div class="q-head">
            <span class="q-prompt"><b>[{js.job_id}]</b> {js.settings.prompt[:40] if js.settings else 'Job'}</span>
            <span class="q-stage" style="color:{color};">{icon} {js.stage.value} ({pct}%)</span>
          </div>
          <div class="q-bar"><div class="q-fill" style="width:{pct}%;"></div></div>
          <div style="margin-top:6px; font-size:0.80rem; color:#94A3B8;">{js.message}</div>
        </div>
        """)
    return "".join(cards)

# ---------------------------------------------------------------------------
# Gradio Modern App Layout Definition
# ---------------------------------------------------------------------------

def build_app() -> gr.Blocks:
    with gr.Blocks(title="VideoStudio Pro — 4K Ultra HD AI Studio") as app:
        has_gemini = bool(STUDIO.config.gemini_api_key or os.environ.get("GEMINI_API_KEY"))
        gemini_chip = (
            "<span class='badge-chip badge-pro'>💎 Pro Plan Connected · Zero-Billing</span>"
            if has_gemini else
            "<span class='badge-chip' style='background:rgba(255,255,255,0.06); color:#94A3B8;'>⚡ Cloud Multi-Provider Mode</span>"
        )

        # Slim High-End Studio Top Bar
        with gr.Group(elem_id="studio-navbar"):
            gr.HTML(f"""
            <div style="display:flex; align-items:center; justify-content:space-between; width:100%; flex-wrap:wrap; gap:10px;">
              <div style="display:flex; align-items:center; gap:12px;">
                <span class="studio-logo">🎬 VideoStudio Pro</span>
                <span class="badge-chip badge-4k">4K Ultra HD</span>
              </div>
              <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
                <span class="badge-chip badge-kami">👑 Kami</span>
                {gemini_chip}
              </div>
            </div>
            """)

        active_job_id = gr.State("")
        story_active_job_id = gr.State("")

        with gr.Tabs():
            # =================================================================
            # TAB 1: 🌟 4K AI Video
            # =================================================================
            with gr.Tab("🌟 4K AI Video"):
                with gr.Row():
                    # Left Column: Creation Controls
                    with gr.Column(scale=6):
                        with gr.Row():
                            ai_template_pick = gr.Dropdown(
                                label="💡 Load Template Preset",
                                choices=AI_TEMPLATE_CHOICES,
                                value=AI_TEMPLATE_CHOICES[0],
                                scale=4
                            )
                            enhance_btn = gr.Button("✨ Enhance with AI", variant="secondary", scale=2)

                        ai_prompt = gr.Textbox(
                            label="Prompt / Creative Vision",
                            placeholder="Describe your scene in detail (lighting, motion, optics)... or select a template above.",
                            lines=3,
                            value=DEMO_AI_PRESETS["eagle"][0]
                        )

                        # Core Essentials
                        with gr.Row():
                            ai_quality = gr.Dropdown(label="Quality Tier", choices=QUALITY_TIERS, value="4K")
                            ai_aspect = gr.Dropdown(label="Aspect Ratio", choices=list(ASPECT_SIZES.keys()), value="16:9")
                            ai_style = gr.Dropdown(label="Style Preset", choices=STYLES_LIST, value="Cinematic")
                            ai_dur = gr.Slider(label="Duration (s)", minimum=2, maximum=60, value=6, step=1)

                        ai_dur_hint = gr.HTML(duration_hint(6))

                        # Drawer 1: Voiceover & Subtitles (Collapsed by default)
                        with gr.Accordion("🎙️ Voiceover, Music & Subtitles (Optional)", open=False):
                            ai_custom_script = gr.Textbox(
                                label="Narration Script (Spoken Voiceover)",
                                placeholder="Text spoken by the neural voice actor...",
                                lines=2,
                                value=DEMO_AI_PRESETS["eagle"][6]
                            )
                            with gr.Row():
                                ai_lang = gr.Dropdown(label="Language", choices=LANGUAGES, value="English")
                                ai_gender = gr.Radio(label="Voice", choices=["Male", "Female"], value="Female")
                                ai_music = gr.Dropdown(label="Music Mood", choices=MUSIC_MOODS, value="Ambient")
                            with gr.Row():
                                ai_subs = gr.Checkbox(label="Burn Styled Subtitles", value=True)
                                ai_sub_style = gr.Dropdown(label="Subtitle Style", choices=SUB_STYLES_LIST, value="Classic White")
                                ai_tr_lang = gr.Dropdown(label="Translate Subtitles To", choices=SUBTITLE_LANGS, value="None")

                        # Drawer 2: Advanced Parameters (Collapsed by default)
                        with gr.Accordion("⚙️ Advanced Parameters (Optional)", open=False):
                            ai_negative = gr.Textbox(
                                label="Negative Prompt (Artifacts to avoid)",
                                placeholder="blurry, distorted, low quality...",
                                lines=1,
                                value="",
                            )
                            with gr.Row():
                                ai_interp = gr.Checkbox(label="60 FPS Motion Interpolation", value=False)
                                ai_anti_fp = gr.Checkbox(label="Anti-Fingerprint Filter", value=True)
                                ai_watermark = gr.Checkbox(label="Apply Watermark", value=False)
                            ai_seed = gr.Number(label="Seed (-1 for Random)", value=-1, precision=0)

                        # Primary Actions
                        with gr.Row():
                            ai_gen_btn = gr.Button("🚀 Generate 4K AI Video", variant="primary", size="lg", scale=4)
                            ai_cancel_btn = gr.Button("🛑 Stop", variant="stop", size="lg", scale=1)

                        ai_status_msg = gr.Markdown("")

                    # Right Column: High-Definition Cinema Viewport
                    with gr.Column(scale=5):
                        gr.Markdown("### 🎬 4K Cinema Viewport")
                        ai_live_card = gr.HTML("<div class='q-card'><b>Studio Ready</b>. Select a preset or type a prompt, then hit <b>Generate 4K Video</b>.</div>")
                        with gr.Group(elem_classes=["viewport-box"]):
                            ai_video_player = gr.Video(label="4K Rendered Output", interactive=False)
                        
                        # Compact Export Toolbar
                        with gr.Row():
                            ai_open_folder_btn = gr.Button("📂 Open Videos Folder", variant="secondary", scale=3)
                            ai_srt_download = gr.File(label="Subtitles (.srt)", interactive=False, scale=3)

                # Event Handlers for Tab 1
                ai_template_pick.change(
                    load_ai_template,
                    inputs=[ai_template_pick],
                    outputs=[ai_prompt, ai_style, ai_quality, ai_aspect, ai_dur, ai_lang, ai_gender, ai_custom_script, ai_music, ai_sub_style]
                )
                ai_dur.change(duration_hint, inputs=[ai_dur], outputs=[ai_dur_hint])
                enhance_btn.click(enhance_prompt_btn, inputs=[ai_prompt, ai_style], outputs=[ai_prompt])
                ai_gen_btn.click(
                    generate_ai_video_live,
                    inputs=[
                        ai_prompt, ai_negative, ai_style, ai_quality, ai_aspect, ai_dur,
                        ai_lang, ai_gender, ai_custom_script, ai_music, ai_subs, ai_sub_style,
                        ai_tr_lang, ai_interp, ai_anti_fp, ai_watermark, ai_seed,
                    ],
                    outputs=[ai_status_msg, ai_live_card, ai_video_player, ai_srt_download, active_job_id],
                )
                ai_cancel_btn.click(cancel_active_job_btn, inputs=[active_job_id], outputs=[ai_status_msg])
                ai_open_folder_btn.click(open_videos_folder)

            # =================================================================
            # TAB 2: 📜 Storyboard Director
            # =================================================================
            with gr.Tab("📜 Storyboard Director"):
                with gr.Row():
                    # Left Column: Storyboard Creation
                    with gr.Column(scale=6):
                        with gr.Row():
                            story_template_pick = gr.Dropdown(
                                label="💡 Load Storyboard Template",
                                choices=STORY_TEMPLATE_CHOICES,
                                value=STORY_TEMPLATE_CHOICES[0],
                                scale=4
                            )
                            story_script_btn = gr.Button("✨ Direct Script with AI", variant="secondary", scale=2)

                        with gr.Row():
                            story_topic = gr.Textbox(
                                label="Topic / Story Concept",
                                placeholder="e.g., The Secret History of the Ancient Pyramids...",
                                lines=1,
                                value=DEMO_STORY_PRESETS["pyramids"][0],
                                scale=4
                            )
                            story_scenes_cnt = gr.Slider(label="Scenes", minimum=2, maximum=10, value=3, step=1, scale=2)

                        story_script = gr.Textbox(
                            label="Multi-Scene Script (Structured Visual: and VO: lines)",
                            placeholder="Visual: aerial pyramids golden hour\nVO: The ancient sands hold secrets...",
                            lines=7,
                            value=DEMO_STORY_PRESETS["pyramids"][1]
                        )

                        # Core Essentials
                        with gr.Row():
                            story_style = gr.Dropdown(label="Visual Aesthetic", choices=STYLES_LIST, value="Cinematic")
                            story_aspect = gr.Dropdown(label="Aspect Ratio", choices=list(ASPECT_SIZES.keys()), value="16:9")
                            story_lang = gr.Dropdown(label="Language", choices=LANGUAGES, value="English")
                            story_gender = gr.Radio(label="Voice Actor", choices=["Male", "Female"], value="Male")

                        # Collapsible Options
                        with gr.Accordion("🎵 Audio, Subtitles & Filters (Optional)", open=False):
                            with gr.Row():
                                story_music = gr.Dropdown(label="Music Bed", choices=MUSIC_MOODS, value="Dramatic")
                                story_subs = gr.Checkbox(label="Burn Karaoke Subtitles", value=True)
                                story_sub_style = gr.Dropdown(label="Subtitle Style", choices=SUB_STYLES_LIST, value="Neon Glow")
                            with gr.Row():
                                story_anti_fp = gr.Checkbox(label="Anti-Fingerprint Filter", value=True)
                                story_watermark = gr.Checkbox(label="Branding Watermark", value=False)

                        # Action Buttons
                        with gr.Row():
                            story_gen_btn = gr.Button("🎬 Build 4K Storyboard Movie", variant="primary", size="lg", scale=4)
                            story_cancel_btn = gr.Button("🛑 Stop", variant="stop", size="lg", scale=1)

                        story_status_msg = gr.Markdown("")

                    # Right Column: Storyboard Viewport
                    with gr.Column(scale=5):
                        gr.Markdown("### 🎬 Storyboard Movie Viewport")
                        story_live_card = gr.HTML("<div class='q-card'><b>Storyboard Ready</b>. Direct a script or pick a template to build.</div>")
                        with gr.Group(elem_classes=["viewport-box"]):
                            story_video_player = gr.Video(label="Rendered Storyboard Movie", interactive=False)
                        
                        with gr.Row():
                            story_open_folder_btn = gr.Button("📂 Open Videos Folder", variant="secondary")

                # Event Handlers for Tab 2
                story_template_pick.change(
                    load_story_template,
                    inputs=[story_template_pick],
                    outputs=[story_topic, story_script, story_style, story_aspect, story_lang, story_gender, story_music]
                )
                story_script_btn.click(generate_script_btn, inputs=[story_topic, story_scenes_cnt, story_lang], outputs=[story_script])
                story_gen_btn.click(
                    generate_storyboard_video_live,
                    inputs=[
                        story_topic, story_script, story_style, story_aspect, story_lang,
                        story_gender, story_music, story_subs, story_sub_style,
                        story_watermark, story_anti_fp,
                    ],
                    outputs=[story_status_msg, story_live_card, story_video_player, story_active_job_id],
                )
                story_cancel_btn.click(cancel_active_job_btn, inputs=[story_active_job_id], outputs=[story_status_msg])
                story_open_folder_btn.click(open_videos_folder)

            # =================================================================
            # TAB 3: ⚙️ Settings & System
            # =================================================================
            with gr.Tab("⚙️ Settings & System"):
                with gr.Row():
                    # Column 1: API Credentials
                    with gr.Column():
                        gr.Markdown("### 🔌 Cloud GPU & AI Credentials")
                        cfg_gemini = gr.Textbox(label="Gemini API Key (Pro Plan Active / Zero-Billing)", value=STUDIO.config.gemini_api_key, type="password")
                        cfg_colab = gr.Textbox(label="Google Colab Worker URL (*.gradio.live)", value=STUDIO.config.colab_url)
                        cfg_pexels = gr.Textbox(label="Pexels API Key (HD Stock Footage)", value=STUDIO.config.pexels_api_key, type="password")
                        cfg_pixabay = gr.Textbox(label="Pixabay API Key (Stock Media)", value=STUDIO.config.pixabay_api_key, type="password")
                        cfg_hf = gr.Textbox(label="Hugging Face Token (ZeroGPU)", value=STUDIO.config.hf_token, type="password")
                        save_cfg_btn = gr.Button("💾 Save API Keys & Settings", variant="primary")
                        save_msg = gr.Markdown("")

                        gr.HTML("""
                        <div class='hw-badge' style='margin-top:12px; border-color:rgba(56,189,248,0.3); color:#38BDF8;'>
                          <b>Zero-Billing & Continuous Execution:</b><br>
                          • Model: Gemini 2.5 Flash / 1.5 Flash (Pro Plan Included)<br>
                          • Crash Recovery: Atomic per-scene checkpoints saved to disk<br>
                          • Cost to user: <b>$0.00 / Zero token billing</b>
                        </div>
                        """)

                    # Column 2: System Health, Queue & Logs
                    with gr.Column():
                        gr.Markdown("### 🖥️ Local Hardware & Backend Status")
                        gr.HTML(f"<div class='hw-badge'>{STUDIO.probe.summary()}</div>")
                        backend_status_box = gr.HTML(backend_status_html())

                        gr.Markdown("### 📊 Job Queue & Recovery")
                        with gr.Row():
                            q_resume_btn = gr.Button("🔄 Resume Interrupted", variant="primary", size="sm")
                            q_refresh_btn = gr.Button("⚡ Refresh Queue", variant="secondary", size="sm")
                            open_folder_btn = gr.Button("📂 Open Folder", variant="secondary", size="sm")
                        queue_card = gr.HTML(render_queue_table())

                        gr.Markdown("### 📋 Studio Logs")
                        log_box = gr.Textbox(label="Live Log Console", lines=8, interactive=False)
                        log_refresh = gr.Button("🔄 Refresh Logs", variant="secondary", size="sm")
                        log_refresh.click(read_system_logs, outputs=[log_box])

                save_cfg_btn.click(
                    save_api_credentials,
                    inputs=[cfg_colab, cfg_hf, cfg_pexels, cfg_pixabay, cfg_gemini],
                    outputs=[save_msg],
                ).then(backend_status_html, outputs=[backend_status_box])

                q_refresh_btn.click(render_queue_table, outputs=[queue_card])
                q_resume_btn.click(resume_all_interrupted_jobs_btn, outputs=[]).then(render_queue_table, outputs=[queue_card])
                open_folder_btn.click(open_videos_folder)

        # Background Timers for smooth live updates
        queue_timer = gr.Timer(3.0)
        queue_timer.tick(render_queue_table, None, queue_card)
        log_timer = gr.Timer(5.0)
        log_timer.tick(read_system_logs, None, log_box)

        app.load(read_system_logs, outputs=[log_box])
        app.load(render_queue_table, outputs=[queue_card])

    return app

def launch_app(server_port: int = 7860):
    app = build_app()
    app.queue(default_concurrency_limit=10)
    managed = os.environ.get("VS_MANAGED_LAUNCH") == "1"
    app.launch(
        server_name="127.0.0.1",
        server_port=server_port,
        inbrowser=not managed,
        css=CSS,
        show_error=True,
    )

if __name__ == "__main__":
    launch_app()
