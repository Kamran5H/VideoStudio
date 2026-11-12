"""
app.py — VideoStudio web interface (Gradio).

Launch:  python app.py      Opens at: http://127.0.0.1:7860

Two simple flows for beginners:
  * Quick clip   — describe a scene, get a short AI video.
  * Story video  — a topic or script becomes a narrated, subtitled video.
Everything optional lives under "More options"; keys live in Settings.
"""

from __future__ import annotations

import html
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, List, Optional

import gradio as gr
from gradio.themes import GoogleFont, Soft

from video_studio import (
    DEFAULT_OUTPUT_DIR,
    MUSIC_MOODS,
    STYLE_PRESETS,
    TTS_VOICES,
    FreeLLMPromptEnhancer,
    JobSettings,
    JobStatus,
    Stage,
    VideoStudio,
    log,
)

STUDIO = VideoStudio()

DEVELOPER_ATTRIBUTION = "Developer: Kamran Ashraf"

# ---------------------------------------------------------------------------
# Choices shown to the user (plain words -> internal values)
# ---------------------------------------------------------------------------

SHAPES = [
    ("▭  Wide — YouTube", "16:9"),
    ("▯  Tall — Reels / Shorts / TikTok", "9:16"),
    ("□  Square — Instagram", "1:1"),
]
SHAPE_VALUES = [v for _, v in SHAPES]
QUALITIES = ["720p", "1080p", "4K"]
LANGUAGES = list(TTS_VOICES.keys())
VOICES = ["Female", "Male"]
STYLES = list(STYLE_PRESETS.keys())
MUSIC = MUSIC_MOODS                        # includes "None"
SUBTITLE_STYLE = "Classic White"

CLIP_SECONDS = (3, 30)                     # slider range for quick clips
STORY_SCENES = (2, 6)
TEXT_LIMITS = {"prompt": 2000, "narration": 5000, "topic": 300, "script": 20000}

STAGE_ICONS = {
    Stage.QUEUED: "⏳", Stage.ENHANCING: "✨", Stage.GENERATING: "🎨", Stage.STITCHING: "🧵",
    Stage.INTERPOLATING: "🎞️", Stage.UPSCALING: "🔍", Stage.AUDIO: "🎵", Stage.SUBTITLES: "💬",
    Stage.DONE: "✅", Stage.FAILED: "❌", Stage.CANCELLED: "⏹️",
}
TERMINAL = (Stage.DONE, Stage.FAILED, Stage.CANCELLED)

# ---------------------------------------------------------------------------
# Look & feel
# ---------------------------------------------------------------------------

THEME = Soft(
    primary_hue="indigo",
    secondary_hue="sky",
    radius_size="lg",
    font=[GoogleFont("Inter"), "Segoe UI", "system-ui", "sans-serif"],
)

CSS = """
.gradio-container { max-width: 1120px !important; margin: 0 auto !important; }
#vs-hero { text-align: center; padding: 18px 8px 6px; }
#vs-hero h1 { font-size: 2rem; margin: 0; letter-spacing: -0.02em; }
#vs-hero p { margin: 6px 0 0; opacity: 0.75; font-size: 1.02rem; }
.vs-steps { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; margin-top: 12px; }
.vs-step { padding: 6px 12px; border-radius: 999px; font-size: 0.86rem;
           background: var(--color-accent-soft); color: var(--body-text-color); }
.vs-make { font-size: 1.1rem !important; min-height: 52px; }
.q-card { border: 1px solid var(--border-color-primary); border-radius: 14px; padding: 14px 16px;
          background: var(--block-background-fill); }
.q-head { display: flex; justify-content: space-between; gap: 10px; align-items: baseline; }
.q-prompt { opacity: 0.8; font-size: 0.9rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.q-stage { font-weight: 700; white-space: nowrap; }
.q-progress-meta { display: flex; justify-content: space-between; font-size: 0.8rem; opacity: 0.7; margin-top: 8px; }
.q-progress-track { height: 8px; border-radius: 999px; background: var(--border-color-primary);
                    overflow: hidden; margin-top: 6px; position: relative; }
.q-progress-fill { height: 100%; width: 0; background: var(--color-accent); border-radius: 999px; }
.q-progress-track.is-indeterminate .q-progress-fill { width: 35%; position: absolute;
    animation: vs-slide 1.4s ease-in-out infinite; }
.q-progress-track.is-terminal .q-progress-fill { width: 100%; background: #ef4444; }
@keyframes vs-slide { 0% { left: -35%; } 100% { left: 100%; } }
@media (prefers-reduced-motion: reduce) {
  .q-progress-track.is-indeterminate .q-progress-fill { animation: none; width: 100%; opacity: 0.5; }
}
.vs-hint { font-size: 0.86rem; opacity: 0.7; }
#vs-footer { text-align: center; font-size: 0.85rem; opacity: 0.65; padding: 10px 0 4px; }
@media (max-width: 640px) { #vs-hero h1 { font-size: 1.5rem; } }
"""

HERO_HTML = """
<div id="vs-hero">
  <h1>🎬 VideoStudio</h1>
  <p>Type an idea, press one button, get a video. Free cloud AI does the heavy work.</p>
  <div class="vs-steps">
    <span class="vs-step">1 · Describe it</span>
    <span class="vs-step">2 · Pick a shape</span>
    <span class="vs-step">3 · Press Make my video</span>
  </div>
</div>
"""

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _choice(value: Any, options: List[str], default: str) -> str:
    """Accept only known option values (the Gradio API can be called directly)."""
    return value if value in options else default


def _number(value: Any, default: float, lo: float, hi: float) -> float:
    """Finite number clamped to [lo, hi]; empty/garbage input -> default."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(lo, min(hi, number))


def _text(value: Any, limit: int) -> str:
    return (value if isinstance(value, str) else "").strip()[:limit]


def redact_sensitive_text(value: str) -> str:
    safe = str(value)
    for secret in (
        STUDIO.config.gemini_api_key,
        STUDIO.config.hf_token,
        STUDIO.config.pexels_api_key,
        STUDIO.config.pixabay_api_key,
    ):
        if secret:
            safe = safe.replace(secret, "[redacted]")
    return safe


def friendly_seconds(sec: int) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}m {sec % 60:02d}s" if sec >= 60 else f"{sec}s"


# ---------------------------------------------------------------------------
# Job cards
# ---------------------------------------------------------------------------


def queue_wait_label(job: JobStatus, jobs: List[JobStatus], now: Optional[float] = None) -> str:
    if job.stage != Stage.QUEUED:
        return ""
    current_time = time.time() if now is None else now
    if job.not_before > current_time:
        return f"Free GPUs are busy — trying again in {friendly_seconds(int(job.not_before - current_time))}"
    ahead = 0
    for previous in jobs:
        if previous.job_id == job.job_id:
            break
        if previous.stage in TERMINAL:
            continue
        if previous.stage != Stage.QUEUED or previous.not_before <= current_time:
            ahead += 1
    return "Starting next" if ahead == 0 else f"Waiting — {ahead} video(s) ahead"


def readable_job_message(job: JobStatus) -> str:
    if job.stage == Stage.FAILED:
        return "This video could not be finished. Open the details below to see why."
    if job.stage == Stage.QUEUED and job.error:
        return "A temporary problem happened. It will retry by itself."
    return job.message


def render_job_card(
    job_id: str,
    prompt: str,
    stage: Stage,
    progress: float,
    message: str,
    elapsed: Optional[int] = None,
    wait_label: str = "",
    error: Optional[str] = None,
) -> str:
    """Status card. Progress is shown as a moving bar, not an invented %."""
    icon = STAGE_ICONS.get(stage, "•")
    color = "#10B981" if stage == Stage.DONE else "#EF4444" if stage in (Stage.FAILED, Stage.CANCELLED) else "var(--color-accent)"
    is_queued = stage == Stage.QUEUED
    is_complete = stage == Stage.DONE
    is_terminal = stage in TERMINAL
    safe_error = html.escape(redact_sensitive_text(error or ""))

    if is_complete:
        progress_label, progress_value = "Done", "100%"
        progress_class, aria = "q-progress-track", (
            'role="progressbar" aria-label="Completed" aria-valuemin="0" aria-valuemax="100" aria-valuenow="100"')
    elif is_queued:
        progress_label, progress_value = (html.escape(wait_label) or "Waiting for the worker"), "—"
        progress_class, aria = "q-progress-track is-indeterminate", 'role="progressbar" aria-label="Waiting for a worker"'
    elif is_terminal:
        progress_label, progress_value = "Not finished", "—"
        progress_class = "q-progress-track is-terminal"
        aria = f'role="progressbar" aria-label="{html.escape(stage.value)} not completed"'
    else:
        progress_label, progress_value = "Working on it", "In progress"
        progress_class = "q-progress-track is-indeterminate"
        aria = f'role="progressbar" aria-label="{html.escape(stage.value)} in progress"'

    elapsed_html = f" · ⏱️ {friendly_seconds(elapsed)}" if elapsed is not None else ""
    error_html = (
        "<details style='margin-top:8px;'><summary>Technical details</summary>"
        f"<pre style='white-space:pre-wrap; overflow-wrap:anywhere;'>{safe_error}</pre></details>"
        if error and safe_error else ""
    )
    fill_style = ' style="width:100%;"' if is_complete else ""
    return f"""
    <div class="q-card">
      <div class="q-head">
        <span class="q-prompt">{html.escape(str(prompt)[:60])}</span>
        <span class="q-stage" style="color:{color};">{icon} {html.escape(stage.value)}</span>
      </div>
      <div class="q-progress-meta"><span>{progress_label}</span><span>{progress_value}</span></div>
      <div class="{progress_class}" {aria}><div class="q-progress-fill"{fill_style}></div></div>
      <div style="margin-top:9px; font-size:0.92rem;">
        {html.escape(redact_sensitive_text(message))}<span class="vs-hint">{elapsed_html}</span>
        <span class="vs-hint" style="float:right;">#{html.escape(str(job_id))}</span>
      </div>
      {error_html}
    </div>
    """


def idle_card(text: str) -> str:
    return f"<div class='q-card vs-hint'>{html.escape(text)}</div>"


def card_for_job(job: JobStatus) -> str:
    return render_job_card(
        job.job_id,
        job.settings.prompt if job.settings else "Video",
        job.stage,
        job.progress,
        readable_job_message(job),
        # Timer only while the job is live; a finished job's age is meaningless.
        max(0, int(time.time() - job.settings.created_at)) if job.settings and job.stage not in TERMINAL else None,
        queue_wait_label(job, STUDIO.queue.all_jobs()),
        job.error,
    )


def active_job(mode: str) -> Optional[JobStatus]:
    """Unfinished job of this mode (running first, then queued)."""
    jobs = [j for j in STUDIO.queue.all_jobs() if j.settings and j.settings.mode == mode]
    running = [j for j in jobs if j.stage not in TERMINAL and j.stage != Stage.QUEUED]
    queued = [j for j in jobs if j.stage == Stage.QUEUED]
    return (running or queued or [None])[0]


def latest_completed_job(mode: Optional[str] = None) -> Optional[JobStatus]:
    completed = [
        job for job in STUDIO.queue.all_jobs()
        if job.stage == Stage.DONE
        and (mode is None or (job.settings and job.settings.mode == mode))
        and job.video_path
        and Path(job.video_path).is_file()
    ]
    return max(completed, key=lambda job: job.settings.created_at if job.settings else 0, default=None)


def tab_state(mode: str) -> tuple:
    """(card_html, latest_video_path_or_None, active_job_id) for one tab."""
    job = active_job(mode)
    result = latest_completed_job(mode)
    if job is not None:
        card = card_for_job(job)
    elif result is not None:
        card = card_for_job(result)
    else:
        card = idle_card("Your video will appear here. It keeps rendering even if you refresh this page.")
    return card, (result.video_path if result else None), (job.job_id if job else "")


def sync_job_outputs(last_clip_video: str = "", last_story_video: str = "") -> tuple:
    """Timer/refresh hook: update both tabs; reload a video only when it changed."""
    clip_card, clip_video, clip_id = tab_state("ai_video")
    story_card, story_video, story_id = tab_state("script_story")
    clip_path, story_path = str(clip_video or ""), str(story_video or "")
    return (
        clip_card,
        clip_video if clip_path and clip_path != last_clip_video else gr.skip(),
        clip_id,
        story_card,
        story_video if story_path and story_path != last_story_video else gr.skip(),
        story_id,
        clip_path or last_clip_video,
        story_path or last_story_video,
    )


# ---------------------------------------------------------------------------
# Building jobs from (untrusted) form input
# ---------------------------------------------------------------------------


def build_ai_settings(prompt, shape, seconds, narration, language, voice, music, subtitles,
                      quality, style) -> JobSettings:
    return JobSettings(
        mode="ai_video",
        prompt=_text(prompt, TEXT_LIMITS["prompt"]),
        aspect_ratio=_choice(shape, SHAPE_VALUES, "16:9"),
        duration=_number(seconds, 5.0, *CLIP_SECONDS),
        voice_script=_text(narration, TEXT_LIMITS["narration"]),
        language=_choice(language, LANGUAGES, "English"),
        voice_gender=_choice(voice, VOICES, "Female"),
        music_mood=_choice(music, MUSIC, "Ambient"),
        subtitles_enabled=bool(subtitles),
        subtitle_style=SUBTITLE_STYLE,
        quality=_choice(quality, QUALITIES, "1080p"),
        style_preset=_choice(style, STYLES, "Cinematic"),
    )


def build_story_settings(topic, script, shape, language, voice, music, subtitles, style) -> JobSettings:
    return JobSettings(
        mode="script_story",
        prompt=_text(topic, TEXT_LIMITS["topic"]) or "Story video",
        script_text=_text(script, TEXT_LIMITS["script"]),
        aspect_ratio=_choice(shape, SHAPE_VALUES, "16:9"),
        language=_choice(language, LANGUAGES, "English"),
        voice_gender=_choice(voice, VOICES, "Female"),
        music_mood=_choice(music, MUSIC, "Calm"),
        subtitles_enabled=bool(subtitles),
        subtitle_style=SUBTITLE_STYLE,
        style_preset=_choice(style, STYLES, "Cinematic"),
    )


# ---------------------------------------------------------------------------
# Button handlers
# ---------------------------------------------------------------------------

VIEW_ONLY = "⚠️ Another VideoStudio window is already running and owns the queue. Use that one."


def _follow_job(job_id: str, done_text: str):
    """Yield (status_text, card_html, video_or_None, job_id) until the job ends."""
    while True:
        status = STUDIO.queue.get_status(job_id)
        if status is None:
            time.sleep(1.0)
            continue
        card = card_for_job(status)
        if status.stage == Stage.DONE:
            video = status.video_path if status.video_path and Path(status.video_path).exists() else None
            yield done_text, card, video, job_id
            return
        if status.stage == Stage.CANCELLED:
            yield "⏹️ Stopped.", card, None, job_id
            return
        if status.stage == Stage.FAILED:
            yield "❌ Sorry, this video failed. See the details in the card.", card, None, job_id
            return
        yield "⏳ Making your video… you can leave this page open or refresh it.", card, gr.skip(), job_id
        time.sleep(1.0)


def make_clip(prompt, shape, seconds, narration, language, voice, music, subtitles, quality, style):
    if not STUDIO.queue.is_worker:
        yield VIEW_ONLY, idle_card("This window is view-only."), None, ""
        return
    sett = build_ai_settings(prompt, shape, seconds, narration, language, voice, music, subtitles, quality, style)
    if not sett.prompt:
        yield "✏️ First, describe what the video should show.", idle_card("Waiting for your idea."), None, ""
        return
    job_id = STUDIO.queue.submit(sett)
    yield from _follow_job(job_id, "🎉 Your video is ready! Use the ⬇ button on the player to download it.")


def make_story(topic, script, shape, language, voice, music, subtitles, style):
    if not STUDIO.queue.is_worker:
        yield VIEW_ONLY, idle_card("This window is view-only."), None, ""
        return
    if not _text(topic, 1) and not _text(script, 1):
        yield "✏️ Type a topic (or paste a script) first.", idle_card("Waiting for your topic."), None, ""
        return
    sett = build_story_settings(topic, script, shape, language, voice, music, subtitles, style)
    job_id = STUDIO.queue.submit(sett)
    yield from _follow_job(job_id, "🎉 Your story video is ready! Use the ⬇ button on the player to download it.")


def improve_idea(prompt: str, style: str) -> str:
    text = _text(prompt, TEXT_LIMITS["prompt"])
    if not text:
        return prompt
    return FreeLLMPromptEnhancer.expand_with_ai(text, _choice(style, STYLES, "Cinematic"), STUDIO.config)


def write_script(topic: str, scenes, language: str) -> str:
    text = _text(topic, TEXT_LIMITS["topic"])
    if not text:
        return ""
    count = int(_number(scenes, 3, *STORY_SCENES))
    return FreeLLMPromptEnhancer.generate_script(
        text, count, _choice(language, LANGUAGES, "English"), STUDIO.config
    )


def stop_job(job_id: str) -> str:
    if not STUDIO.queue.is_worker:
        return VIEW_ONLY
    if not job_id:
        return "Nothing is running right now."
    return "⏹️ Stopping…" if STUDIO.queue.cancel(job_id) else "That video already finished."


def turn_off() -> str:
    """Stop the background server. The desktop shortcut starts it hidden, so
    this is the only visible way to end it."""
    def _exit() -> None:
        STUDIO.queue.shutdown()
        os._exit(0)

    import threading
    threading.Timer(1.0, _exit).start()
    return "👋 VideoStudio is turned off. Close this tab; use the desktop shortcut to start it again."


def open_videos_folder() -> None:
    v_dir = DEFAULT_OUTPUT_DIR / "videos"
    v_dir.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(str(v_dir))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(v_dir)])
        else:
            subprocess.Popen(["xdg-open", str(v_dir)])
    except OSError as exc:      # headless box / no file manager: don't crash the UI event
        log.warning("Could not open %s: %s", v_dir, exc)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

CLEAR_SECRET = "-"


def merge_secret(current: str, submitted: Optional[str]) -> str:
    """Secret fields are never pre-filled in the page, so an empty submission
    means "keep the saved value"; a single '-' clears it."""
    value = (submitted or "").strip()
    if not value:
        return current
    return "" if value == CLEAR_SECRET else value


def secret_placeholder(current: str) -> str:
    return f"✓ Saved — leave empty to keep, type {CLEAR_SECRET} to remove" if current else "Not set (optional)"


def save_api_credentials(colab_url: str, hf_tok: str, pexels_k: str, pixabay_k: str, gemini_k: str) -> str:
    cfg = STUDIO.config
    cfg.colab_url = (colab_url or "").strip()
    cfg.hf_token = merge_secret(cfg.hf_token, hf_tok)
    cfg.pexels_api_key = merge_secret(cfg.pexels_api_key, pexels_k)
    cfg.pixabay_api_key = merge_secret(cfg.pixabay_api_key, pixabay_k)
    cfg.gemini_api_key = merge_secret(cfg.gemini_api_key, gemini_k)
    cfg.save()
    return "✅ Saved. Keys stay on this computer (studio_config.json)."


def backend_status_html() -> str:
    labels = {
        "colab": "Your Colab GPU worker",
        "ltx23": "LTX-2.3 (free cloud)",
        "wan22": "Wan 2.2 (free cloud)",
        "ltx": "LTX-Video (free cloud)",
        "cogvideox": "CogVideoX (free cloud, wide and square only)",
    }
    has_token = bool(STUDIO.config.hf_token.strip())
    rows = []
    for name in STUDIO.config.backend_order:
        label = labels.get(name)
        if not label:
            continue
        be = STUDIO.backends.get(name)
        ok = bool(be and be.available())
        if name == "colab":
            state = "ready" if ok else "add the Colab URL to use"
        elif has_token:
            state = "ready"
            ok = True
        else:
            state = "needs the free Hugging Face token"
            ok = False
        rows.append(f"{'🟢' if ok else '⚪'} <b>{label}</b> — {state}")
    note = "" if has_token or STUDIO.config.colab_url.strip() else (
        "<div class='vs-hint' style='margin-top:8px;'>Without a Hugging Face token the free GPUs refuse "
        "video jobs, so Quick clip uses pictures with camera motion instead. The token is free: create an "
        "account at huggingface.co, open Settings → Access Tokens, make a <b>Read</b> token and paste it "
        "on the left.</div>")
    return "<div class='q-card'>" + "<br>".join(rows) + note + "</div>"


def render_queue_table() -> str:
    jobs = STUDIO.queue.all_jobs()
    if not jobs:
        return idle_card("No videos yet.")
    return "".join(card_for_job(js) for js in reversed(jobs[-6:]))


def read_system_logs() -> str:
    log_file = DEFAULT_OUTPUT_DIR / "logs" / "studio.log"
    if log_file.exists():
        try:
            tail = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]
            if tail:
                return redact_sensitive_text("\n".join(tail))
        except OSError:
            pass
    return "No log entries yet."


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def _more_options(default_music: str, with_quality: bool):
    """Shared 'More options' controls; returns them in a fixed order."""
    with gr.Accordion("More options (optional)", open=False):
        with gr.Row():
            language = gr.Dropdown(LANGUAGES, value="English", label="Voice language")
            voice = gr.Radio(VOICES, value="Female", label="Voice")
            music = gr.Dropdown(MUSIC, value=default_music, label="Background music")
        with gr.Row():
            style = gr.Dropdown(STYLES, value="Cinematic", label="Look / style")
            quality = gr.Dropdown(QUALITIES, value="1080p", label="Quality", visible=with_quality,
                                  info="Higher = bigger file, slower")
            subtitles = gr.Checkbox(value=True, label="Show subtitles on the video")
    return language, voice, music, style, quality, subtitles


def build_app() -> gr.Blocks:
    with gr.Blocks(title="VideoStudio") as app:
        gr.HTML(HERO_HTML)

        clip_job_id = gr.State("")
        story_job_id = gr.State("")
        last_clip_video = gr.State("")
        last_story_video = gr.State("")

        with gr.Tabs():
            # ---------------------------------------------------------- Quick clip
            with gr.Tab("🎬 Quick clip", id="clip"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=5):
                        clip_prompt = gr.Textbox(
                            label="What should the video show?",
                            placeholder="e.g. A paper boat drifting on a calm pond at sunrise",
                            lines=3, max_lines=8,
                        )
                        improve_btn = gr.Button("✨ Improve my idea with AI", size="sm", variant="secondary")
                        clip_shape = gr.Radio(SHAPES, value="16:9", label="Shape")
                        clip_seconds = gr.Slider(*CLIP_SECONDS, value=5, step=1, label="Length (seconds)")
                        clip_narration = gr.Textbox(
                            label="Narration (optional)",
                            placeholder="Words to be spoken over the video. Leave empty for music only.",
                            lines=2,
                        )
                        (clip_lang, clip_voice, clip_music, clip_style,
                         clip_quality, clip_subs) = _more_options("Ambient", with_quality=True)
                        with gr.Row():
                            clip_make = gr.Button("🎬 Make my video", variant="primary", scale=4, elem_classes=["vs-make"])
                            clip_stop = gr.Button("⏹ Stop", variant="stop", scale=1)
                        clip_status = gr.Markdown()
                    with gr.Column(scale=5):
                        clip_card = gr.HTML(idle_card("Your video will appear here."))
                        clip_video = gr.Video(label="Your video", interactive=False, height=360)
                        gr.Markdown("<span class='vs-hint'>A 5-second clip usually takes 1–3 minutes. "
                                    "Real AI video needs the free Hugging Face token (see Settings); "
                                    "if the free GPUs are busy, it waits and retries by itself.</span>")

                improve_btn.click(improve_idea, [clip_prompt, clip_style], clip_prompt)
                clip_make.click(
                    make_clip,
                    [clip_prompt, clip_shape, clip_seconds, clip_narration, clip_lang, clip_voice,
                     clip_music, clip_subs, clip_quality, clip_style],
                    [clip_status, clip_card, clip_video, clip_job_id],
                )
                clip_stop.click(stop_job, clip_job_id, clip_status)

            # ---------------------------------------------------------- Story video
            with gr.Tab("📖 Story video", id="story"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=5):
                        story_topic = gr.Textbox(
                            label="What is your video about?",
                            placeholder="e.g. Why the ocean is salty",
                        )
                        with gr.Row():
                            story_scenes = gr.Slider(*STORY_SCENES, value=3, step=1, label="Number of scenes", scale=3)
                            script_btn = gr.Button("✍️ Write the script for me", variant="secondary", scale=2)
                        story_script = gr.Textbox(
                            label="Script (optional — written for you if empty)",
                            placeholder=("Visual: waves crashing on a rocky shore\n"
                                         "VO: Every wave carries a pinch of salt from the land.\n"
                                         "Visual: rivers flowing into the sea\n"
                                         "VO: Rivers have been doing this for millions of years."),
                            lines=7,
                            info="Each scene = a 'Visual:' line (what to show) and a 'VO:' line (what to say).",
                        )
                        story_shape = gr.Radio(SHAPES, value="9:16", label="Shape")
                        (story_lang, story_voice, story_music, story_style,
                         _story_quality, story_subs) = _more_options("Calm", with_quality=False)
                        with gr.Row():
                            story_make = gr.Button("🎬 Make my video", variant="primary", scale=4, elem_classes=["vs-make"])
                            story_stop = gr.Button("⏹ Stop", variant="stop", scale=1)
                        story_status = gr.Markdown()
                    with gr.Column(scale=5):
                        story_card = gr.HTML(idle_card("Your video will appear here."))
                        story_video = gr.Video(label="Your video", interactive=False, height=420)
                        gr.Markdown("<span class='vs-hint'>Uses stock footage or AI pictures with gentle "
                                    "camera motion, a narrator voice, music and subtitles.</span>")

                script_btn.click(write_script, [story_topic, story_scenes, story_lang], story_script)
                story_make.click(
                    make_story,
                    [story_topic, story_script, story_shape, story_lang, story_voice,
                     story_music, story_subs, story_style],
                    [story_status, story_card, story_video, story_job_id],
                )
                story_stop.click(stop_job, story_job_id, story_status)

            # ---------------------------------------------------------- Settings
            with gr.Tab("⚙️ Settings", id="settings"):
                gr.Markdown(
                    "Story video works **without** any key. Real AI video in Quick clip needs the free "
                    "Hugging Face token; the other keys just give better results. "
                    "Keys are saved only on this computer and are never shown again in this page."
                )
                cfg = STUDIO.config
                with gr.Row():
                    with gr.Column():
                        cfg_gemini = gr.Textbox(label="Gemini API key — smarter prompts & scripts",
                                                placeholder=secret_placeholder(cfg.gemini_api_key), type="password",
                                                info="Free key: aistudio.google.com/apikey")
                        cfg_hf = gr.Textbox(label="Hugging Face token — more free GPU time",
                                            placeholder=secret_placeholder(cfg.hf_token), type="password",
                                            info="huggingface.co/settings/tokens (read access is enough)")
                        cfg_pexels = gr.Textbox(label="Pexels key — real stock video for stories",
                                                placeholder=secret_placeholder(cfg.pexels_api_key), type="password",
                                                info="pexels.com/api")
                        cfg_pixabay = gr.Textbox(label="Pixabay key — more stock video",
                                                 placeholder=secret_placeholder(cfg.pixabay_api_key), type="password",
                                                 info="pixabay.com/api/docs")
                        cfg_colab = gr.Textbox(label="Colab worker URL (advanced)", value=cfg.colab_url,
                                               placeholder="https://xxxx.gradio.live",
                                               info="From colab_worker.ipynb — your own free T4 GPU")
                        save_btn = gr.Button("💾 Save settings", variant="primary")
                        save_msg = gr.Markdown()
                    with gr.Column():
                        gr.Markdown("**Video engines**")
                        backend_box = gr.HTML(backend_status_html())
                        gr.Markdown("**Recent videos**")
                        queue_box = gr.HTML(render_queue_table())
                        folder_btn = gr.Button("📂 Open my videos folder", variant="secondary")
                        off_btn = gr.Button("⏻ Turn off VideoStudio", variant="secondary")
                        off_msg = gr.Markdown()
                        with gr.Accordion("Technical log", open=False):
                            log_box = gr.Textbox(lines=10, interactive=False, show_label=False)
                            gr.Button("Refresh log", size="sm").click(read_system_logs, None, log_box)
                        gr.Markdown(f"<span class='vs-hint'>{html.escape(STUDIO.probe.summary())}</span>")

                save_btn.click(
                    save_api_credentials, [cfg_colab, cfg_hf, cfg_pexels, cfg_pixabay, cfg_gemini], save_msg,
                ).then(backend_status_html, None, backend_box)
                folder_btn.click(open_videos_folder)
                off_btn.click(turn_off, None, off_msg)

        gr.HTML(f"<div id='vs-footer'>{DEVELOPER_ATTRIBUTION} · "
                "<a href='https://github.com/Kamran5H/VideoStudio' target='_blank'>GitHub</a></div>")

        # Reconnect to running/finished jobs after a refresh, then keep in sync.
        sync_outputs = [clip_card, clip_video, clip_job_id, story_card, story_video, story_job_id,
                        last_clip_video, last_story_video]
        sync_inputs = [last_clip_video, last_story_video]
        app.load(sync_job_outputs, sync_inputs, sync_outputs)
        gr.Timer(2.0).tick(sync_job_outputs, sync_inputs, sync_outputs)
        gr.Timer(5.0).tick(render_queue_table, None, queue_box)
        app.load(read_system_logs, None, log_box)

    return app


def launch_app(server_port: int = 7860):
    app = build_app()
    app.queue(default_concurrency_limit=10)
    managed = os.environ.get("VS_MANAGED_LAUNCH") == "1"
    app.launch(
        server_name="127.0.0.1",
        server_port=server_port,
        inbrowser=not managed,
        theme=THEME,
        css=CSS,
        show_error=True,
    )


if __name__ == "__main__":
    launch_app()
