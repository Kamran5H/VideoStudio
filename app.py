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
    CLONE_LANG_CODES,
    DEFAULT_OUTPUT_DIR,
    MUSIC_MOODS,
    STYLE_PRESETS,
    TTS_VOICES,
    FreeLLMPromptEnhancer,
    JobSettings,
    JobStatus,
    QualityError,
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
MY_VOICE = "My voice"
VOICES = ["Female", "Male", MY_VOICE]
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
.gradio-container { max-width: 1180px !important; margin: 0 auto !important; }
#vs-hero { text-align: left; padding: 28px 8px 20px; }
.vs-brand-dot { color: #6366f1; }
.vs-welcome-card { padding: 24px !important; border: 1px solid var(--border-color-primary); border-radius: 20px; background: var(--block-background-fill); }
.vs-panel { padding: 18px !important; }
#vs-navigation { margin-bottom: 18px; }
#vs-workspaces .tab-wrapper { display: none; }
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
  <h1>VideoStudio<span class="vs-brand-dot">.</span></h1>
  <p>Your idea. Your voice. Your next video.</p>

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


def files_for_video(video_path: Optional[str]) -> List[str]:
    """Download companions for the currently displayed completed render."""
    if not video_path:
        return []
    for job in STUDIO.queue.all_jobs():
        if job.video_path and Path(job.video_path) == Path(video_path):
            candidates = [Path(job.srt_path)] if job.srt_path else []
            candidates.append(DEFAULT_OUTPUT_DIR / "jobs" / job.job_id / "render_report.json")
            return [str(path) for path in candidates if path.is_file()]
    return []


# ---------------------------------------------------------------------------
# Building jobs from (untrusted) form input
# ---------------------------------------------------------------------------


def _standard_voice(voice: Any) -> str:
    """The standard voice to use (also the backup when 'My voice' is chosen)."""
    return voice if voice in ("Female", "Male") else "Female"


def build_ai_settings(prompt, shape, seconds, narration, language, voice, music, subtitles,
                      quality, style, delivery="Engaging") -> JobSettings:
    return JobSettings(
        mode="ai_video",
        prompt=_text(prompt, TEXT_LIMITS["prompt"]),
        aspect_ratio=_choice(shape, SHAPE_VALUES, "16:9"),
        duration=_number(seconds, 5.0, *CLIP_SECONDS),
        voice_script=_text(narration, TEXT_LIMITS["narration"]),
        language=_choice(language, LANGUAGES, "English"),
        voice_gender=_standard_voice(voice),
        use_my_voice=(voice == MY_VOICE),
        narration_delivery=_choice(delivery, ["Natural", "Engaging", "Calm", "Energetic"], "Engaging"),
        music_mood=_choice(music, MUSIC, "Ambient"),
        subtitles_enabled=bool(subtitles),
        subtitle_style=SUBTITLE_STYLE,
        quality=_choice(quality, QUALITIES, "1080p"),
        style_preset=_choice(style, STYLES, "Cinematic"),
    )


def build_story_settings(topic, script, shape, language, voice, music, subtitles, style, scenes=4, delivery="Engaging") -> JobSettings:
    return JobSettings(
        mode="script_story",
        prompt=_text(topic, TEXT_LIMITS["topic"]) or "Story video",
        script_text=_text(script, TEXT_LIMITS["script"]),
        scene_count=int(_number(scenes, 4, *STORY_SCENES)),
        aspect_ratio=_choice(shape, SHAPE_VALUES, "16:9"),
        language=_choice(language, LANGUAGES, "English"),
        voice_gender=_standard_voice(voice),
        use_my_voice=(voice == MY_VOICE),
        narration_delivery=_choice(delivery, ["Natural", "Engaging", "Calm", "Energetic"], "Engaging"),
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


def make_clip(prompt, shape, seconds, narration, language, voice, music, subtitles, quality, style, delivery="Engaging"):
    if not STUDIO.queue.is_worker:
        yield VIEW_ONLY, idle_card("This window is view-only."), None, ""
        return
    sett = build_ai_settings(prompt, shape, seconds, narration, language, voice, music, subtitles, quality, style, delivery)
    if not sett.prompt:
        yield "✏️ First, describe what the video should show.", idle_card("Waiting for your idea."), None, ""
        return
    job_id = STUDIO.queue.submit(sett)
    yield from _follow_job(job_id, "🎉 Your video is ready! Use the ⬇ button on the player to download it.")


def make_story(topic, script, shape, language, voice, music, subtitles, style, scenes=4, delivery="Engaging"):
    if not STUDIO.queue.is_worker:
        yield VIEW_ONLY, idle_card("This window is view-only."), None, ""
        return
    if not _text(topic, 1) and not _text(script, 1):
        yield "✏️ Type a topic (or paste a script) first.", idle_card("Waiting for your topic."), None, ""
        return
    sett = build_story_settings(topic, script, shape, language, voice, music, subtitles, style, scenes, delivery)
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
    try:
        return FreeLLMPromptEnhancer.generate_script(
            text, count, _choice(language, LANGUAGES, "English"), STUDIO.config
        )
    except QualityError as exc:
        raise gr.Error(str(exc)) from exc


def stop_job(job_id: str) -> str:
    if not STUDIO.queue.is_worker:
        return VIEW_ONLY
    if not job_id:
        return "Nothing is running right now."
    return "⏹️ Stopping…" if STUDIO.queue.cancel(job_id) else "That video already finished."


# ---------------------------------------------------------------------------
# My voice (voice cloning)
# ---------------------------------------------------------------------------

VOICE_TEST_LINE = "Hello! This is my own voice, speaking from VideoStudio."


def my_voice_status() -> str:
    cloner = STUDIO.audio_engine.voice_cloner
    if not cloner.has_sample():
        return "⚪ No voice saved yet."
    lines = [f"🟢 Your voice is saved. Choose **{MY_VOICE}** under *More options → Voice* when you make a video."]
    if not STUDIO.config.hf_token.strip():
        lines.append("⚠️ One more step: add the free Hugging Face token in **Settings** (voice cloning runs on the free GPUs).")
    return "\n\n".join(lines)


def save_my_voice(recording: Optional[str]) -> str:
    if not recording:
        return "✏️ Record or upload your voice first."
    try:
        seconds = STUDIO.audio_engine.voice_cloner.save_sample(Path(recording))
    except ValueError as exc:
        return f"❌ {exc}"
    return f"✅ Saved {seconds:.0f} seconds of your voice.\n\n{my_voice_status()}"


def delete_my_voice() -> str:
    STUDIO.audio_engine.voice_cloner.delete_sample()
    return "🗑️ Your voice sample was deleted from this computer."


def test_my_voice(language: str):
    cloner = STUDIO.audio_engine.voice_cloner
    lang = _choice(language, list(CLONE_LANG_CODES), "English")
    out = DEFAULT_OUTPUT_DIR / "audio_temp" / "my_voice_test.mp3"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        cloner.synthesize(VOICE_TEST_LINE, lang, out)
    except Exception as exc:
        reason = redact_sensitive_text(str(exc))
        if "ZeroGPU" in reason:
            reason = "today's free GPU time is used up. Try again later"
        return None, f"❌ Could not test: {reason[:200]}."
    return str(out), "✅ This is how your videos will sound."


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
            voice = gr.Radio(VOICES, value=MY_VOICE if STUDIO.audio_engine.voice_cloner.has_sample() else "Female", label="Voice")
            music = gr.Dropdown(MUSIC, value=default_music, label="Background music")
        with gr.Row():
            style = gr.Dropdown(STYLES, value="Cinematic", label="Look / style")
            quality = gr.Dropdown(QUALITIES, value="1080p", label="Quality", visible=with_quality,
                                  info="Export size. Higher sizes are upscaled from the source, with larger files and slower renders.")
            subtitles = gr.Checkbox(value=True, label="Show subtitles on the video")
        delivery = gr.Dropdown(["Natural", "Engaging", "Calm", "Energetic"], value="Engaging", label="Narrator delivery", info="Controls standard voice pacing. Cloned voices retain the model’s delivery.")
    return language, voice, music, style, quality, subtitles, delivery


def editor_render(path, start, end, shape, muted, caption, srt, position, size, narration=None, preview=True):
    from local_editor import render_edit
    if not path:
        return None, "Import a video to begin."
    try:
        return render_edit(path, start, end, shape, muted, caption or "", srt, position, size, preview, narration)
    except (ValueError, RuntimeError, OSError) as exc:
        raise gr.Error(str(exc)) from exc


def editor_join(paths, order):
    from local_editor import join_clips
    try:
        paths = list(paths or [])
        if str(order or "").strip():
            indices = [int(value.strip()) - 1 for value in order.split(",")]
            if sorted(indices) != list(range(len(paths))):
                raise ValueError("Order must list every clip number once, for example 2,1,3.")
            paths = [paths[i] for i in indices]
        result = join_clips(paths)
        return result, "Imported in this order: " + " → ".join(Path(p).name for p in paths)
    except (ValueError, RuntimeError, OSError) as exc:
        raise gr.Error(str(exc)) from exc


def build_flow_workspace():
    with gr.Tab("Edit a video", id="flow_editor"):
        gr.Markdown("## Make your video yours\nAdd a clip, make a few changes, then download. Your original stays safe.")
        with gr.Accordion("Need a video? Use your Google plan", open=False) as google_help:
            gr.Markdown("**1.** Open Flow and create a clip. **2.** Download it. **3.** Add it below. Your Google plan's limits apply; no API key is needed here.")
            with gr.Row():
                gr.Button("Open Google Flow ↗", link="https://flow.google.com/", variant="primary")
                gr.Button("Open Gemini ↗", link="https://gemini.google.com/app")
            with gr.Accordion("Help me write a voiceover", open=False):
                brief_topic = gr.Textbox(label="What is your video about?", lines=2)
                brief_language = gr.Dropdown(LANGUAGES, value="Urdu", label="Language")
                brief_count = gr.Slider(2, 6, value=4, step=1, label="Scenes")
                brief_button = gr.Button("Prepare my writing prompt")
                brief_output = gr.Textbox(label="Copy this into Gemini, then review its reply", lines=6)
                brief_button.click(browser_script_brief, [brief_topic, brief_language, brief_count], brief_output)
        with gr.Row(equal_height=False):
            with gr.Column(scale=4, elem_classes=["vs-panel"]):
                gr.Markdown("### 1 · Add your video")
                source = gr.File(label="Choose a video", file_types=[".mp4", ".mov", ".webm", ".mkv"])
                with gr.Accordion("Join several videos", open=False):
                    uploads = gr.File(label="Add your clips", file_count="multiple", file_types=[".mp4", ".mov", ".webm", ".mkv"])
                    order = gr.Textbox(label="Change the order (optional)", placeholder="Example: 2,1,3")
                    join = gr.Button("Join my clips")
                    join_status = gr.Markdown()
                gr.Markdown("### 2 · Make it yours")
                caption = gr.Textbox(label="Add a caption (optional)", placeholder="Write a short line… / اردو میں لکھیں", lines=2)
                shape = gr.Radio([("Keep original", "Original"), ("Wide", "16:9"), ("Tall", "9:16"), ("Square", "1:1")], value="Original", label="Video shape")
                with gr.Accordion("Trim the video", open=False):
                    gr.Markdown("Keep the whole clip, or enter the part you want. End at 0 keeps everything after the start.")
                    with gr.Row():
                        start = gr.Number(value=0, minimum=0, label="Start at (seconds)")
                        end = gr.Number(value=0, minimum=0, label="End at (0 = end)")
                with gr.Accordion("Voice & sound", open=False):
                    muted = gr.Checkbox(value=False, label="Mute the original sound")
                    narration = gr.Audio(label="Record or add your narration", sources=["upload", "microphone"], type="filepath")
                    gr.Markdown("Your recording replaces the original sound and starts with your selected clip. Urdu is welcome.")
                with gr.Accordion("Caption appearance & subtitle files", open=False):
                    position = gr.Radio([("Choose for me", "Auto: less clutter"), ("Top", "Top"), ("Bottom", "Bottom")], value="Auto: less clutter", label="Put captions")
                    size = gr.Slider(.7, 1.5, value=1, step=.1, label="Text size")
                    srt = gr.File(label="Add timed subtitles (.srt)", file_types=[".srt"])
                    gr.Markdown("Automatic placement estimates the quieter area. You can move captions above. Subtitle times refer to the original clip.")
            with gr.Column(scale=5, elem_classes=["vs-panel"]):
                gr.Markdown("### 3 · Preview & download")
                preview_video = gr.Video(label="Your preview", interactive=False, height=360)
                status = gr.Markdown("Your preview will appear after you add a video.")
                refresh = gr.Button("↻ Update preview", size="sm")
                export = gr.Button("Download my finished video", variant="primary", interactive=False, elem_classes=["vs-make"])
                download = gr.File(label="Your finished video", interactive=False)
                gr.Markdown("Preview shows the first 8 seconds. Download creates your full selected video.", elem_classes=["vs-hint"])
        inputs = [source, start, end, shape, muted, caption, srt, position, size, narration]
        source.change(lambda path: gr.Button(interactive=bool(path)), source, export, queue=False)
        join.click(editor_join, [uploads, order], [source, join_status], concurrency_id="local_editor", concurrency_limit=1)
        for control in (source, start, end, shape, muted, srt, position, narration):
            control.change(editor_render, inputs, [preview_video, status], concurrency_id="local_editor", concurrency_limit=1, trigger_mode="always_last")
        caption.blur(editor_render, inputs, [preview_video, status], concurrency_id="local_editor", concurrency_limit=1)
        size.release(editor_render, inputs, [preview_video, status], concurrency_id="local_editor", concurrency_limit=1)
        refresh.click(editor_render, inputs, [preview_video, status], concurrency_id="local_editor", concurrency_limit=1)
        export.click(lambda *args: editor_render(*args, preview=False), inputs, [download, status], concurrency_id="local_editor", concurrency_limit=1)
    return google_help


def browser_script_brief(topic, language, scenes):
    from video_studio import narration_brief
    topic = _text(topic, 5000)
    if not topic:
        raise gr.Error("Add your topic and any facts you want included.")
    language = _choice(language, LANGUAGES, "Urdu")
    count = int(_number(scenes, 4, 2, 6))
    return (
        f"Write exactly {count} distinct video scenes in {language}. Topic and supplied facts: {topic}\n\n"
        + narration_brief(language)
        + "\nDo not invent claims, statistics or quotes. Identify any uncertain claims separately for review. "
        "For each scene output Visual: followed by concrete English visual keywords, then VO: followed by "
        f"spoken {language} narration. Keep the narration aligned to the visual."
    )


def build_app() -> gr.Blocks:
    with gr.Blocks(title="VideoStudio") as app:
        gr.HTML(HERO_HTML)

        clip_job_id = gr.State("")
        story_job_id = gr.State("")
        last_clip_video = gr.State("")
        last_story_video = gr.State("")

        with gr.Row(elem_id="vs-navigation"):
            home_button = gr.Button("⌂ Start", size="sm")
            edit_button = gr.Button("Edit a video", size="sm")
            create_button = gr.Button("Make a story", size="sm")
            with gr.Accordion("More tools", open=False):
                quick_button = gr.Button("Make an AI clip", size="sm")
                voice_button = gr.Button("My voice", size="sm")
                settings_button = gr.Button("Settings", size="sm")
        with gr.Tabs(selected="home", elem_id="vs-workspaces") as workspaces:
            with gr.Tab("Start", id="home"):
                gr.Markdown("## What would you like to do?\nPick one. We’ll guide you from there.")
                with gr.Row(equal_height=True):
                    with gr.Column(elem_classes=["vs-welcome-card"]):
                        gr.Markdown("### Use my Google plan\nCreate a clip in Flow, then add your voice, captions and finishing touches here.")
                        home_google = gr.Button("Start with Google →", variant="primary")
                    with gr.Column(elem_classes=["vs-welcome-card"]):
                        gr.Markdown("### Edit a video I have\nTrim it, add a caption, record your voice, and download your finished video.")
                        home_edit = gr.Button("Choose my video →")
                    with gr.Column(elem_classes=["vs-welcome-card"]):
                        gr.Markdown("### Turn an idea into a story\nWrite a topic and make a narrated video with pictures or stock clips.")
                        home_story = gr.Button("Make my story →")
                gr.Markdown("**New here?** Try editing one short clip first. You can leave every optional setting as it is.")
            google_help = build_flow_workspace()
            # ---------------------------------------------------------- Quick clip
            with gr.Tab("AI clip", id="clip"):
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
                         clip_quality, clip_subs, clip_delivery) = _more_options("Ambient", with_quality=True)
                        with gr.Row():
                            clip_make = gr.Button("🎬 Make my video", variant="primary", scale=4, elem_classes=["vs-make"])
                            clip_stop = gr.Button("⏹ Stop", variant="stop", scale=1)
                        clip_status = gr.Markdown()
                    with gr.Column(scale=5):
                        clip_card = gr.HTML(idle_card("Your video will appear here."))
                        clip_video = gr.Video(label="Your video", interactive=False, height=360)
                        clip_files = gr.File(label="Captions and render report", file_count="multiple", interactive=False)
                        gr.Markdown("<span class='vs-hint'>A 5-second clip usually takes 1–3 minutes. "
                                    "Real AI video needs the free Hugging Face token (see Settings); "
                                    "if the free GPUs are busy, it waits and retries by itself.</span>")

                improve_btn.click(improve_idea, [clip_prompt, clip_style], clip_prompt)
                clip_make.click(
                    make_clip,
                    [clip_prompt, clip_shape, clip_seconds, clip_narration, clip_lang, clip_voice,
                     clip_music, clip_subs, clip_quality, clip_style, clip_delivery],
                    [clip_status, clip_card, clip_video, clip_job_id],
                )
                clip_stop.click(stop_job, clip_job_id, clip_status)
                clip_video.change(files_for_video, clip_video, clip_files)

            # ---------------------------------------------------------- Story video
            with gr.Tab("Make a story", id="story"):
                gr.Markdown("## Tell your story\nStart with a topic. Preview the script if you like, then make your video.")
                with gr.Row(equal_height=False):
                    with gr.Column(scale=5):
                        story_topic = gr.Textbox(
                            label="What is your video about?",
                            placeholder="e.g. Why the ocean is salty",
                        )
                        with gr.Accordion("Review or write my script (optional)", open=False):
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
                                info="Each scene needs a 'Visual:' line and a 'VO:' line. Review AI drafts and verify factual claims before rendering.",
                            )
                        story_shape = gr.Radio(SHAPES, value="9:16", label="Shape")
                        (story_lang, story_voice, story_music, story_style,
                         _story_quality, story_subs, story_delivery) = _more_options("Calm", with_quality=False)
                        with gr.Row():
                            story_make = gr.Button("🎬 Make my video", variant="primary", scale=4, elem_classes=["vs-make"])
                            story_stop = gr.Button("⏹ Stop", variant="stop", scale=1)
                        story_status = gr.Markdown()
                    with gr.Column(scale=5):
                        story_card = gr.HTML(idle_card("Your video will appear here."))
                        story_video = gr.Video(label="Your video", interactive=False, height=420)
                        story_files = gr.File(label="Captions and render report", file_count="multiple", interactive=False)
                        gr.Markdown("<span class='vs-hint'>Uses stock footage or AI pictures with gentle "
                                    "camera motion, a narrator voice, music and subtitles.</span>")

                script_btn.click(write_script, [story_topic, story_scenes, story_lang], story_script)
                story_make.click(
                    make_story,
                    [story_topic, story_script, story_shape, story_lang, story_voice,
                     story_music, story_subs, story_style, story_scenes, story_delivery],
                    [story_status, story_card, story_video, story_job_id],
                )
                story_stop.click(stop_job, story_job_id, story_status)
                story_video.change(files_for_video, story_video, story_files)

            # ---------------------------------------------------------- My voice
            with gr.Tab("🎙️ My voice", id="voice"):
                gr.Markdown(
                    "Make the narrator sound like **you**. Free, and your recording stays on this computer "
                    "(it is only sent to the free voice service while a video is being made).\n\n"
                    "**1.** Record yourself reading anything for **10 to 20 seconds** in a quiet room "
                    "(or upload a recording). **2.** Press **Save my voice**. "
                    f"**3.** When making a video, open *More options* and choose **Voice → {MY_VOICE}**."
                )
                with gr.Row(equal_height=False):
                    with gr.Column(scale=5):
                        voice_rec = gr.Audio(sources=["microphone", "upload"], type="filepath",
                                             label="Your voice (10 to 20 seconds)")
                        with gr.Row():
                            voice_save = gr.Button("💾 Save my voice", variant="primary", scale=3)
                            voice_delete = gr.Button("🗑️ Delete", variant="secondary", scale=1)
                        voice_msg = gr.Markdown(my_voice_status())
                    with gr.Column(scale=5):
                        voice_test_lang = gr.Dropdown(list(CLONE_LANG_CODES), value="English", label="Test language")
                        voice_test = gr.Button("▶ Test my voice", variant="secondary")
                        voice_test_audio = gr.Audio(label="Test result", interactive=False)
                        voice_test_msg = gr.Markdown()
                        gr.Markdown(
                            "<span class='vs-hint'>Works in " + ", ".join(CLONE_LANG_CODES) + ". "
                            "Urdu and Punjabi are not supported by the free cloning model yet, so those use the "
                            "standard voices. The result is a close likeness, not a perfect copy. Each few "
                            "sentences use about a minute of your free daily GPU time. "
                            "Only use your own voice, or one you have permission to use.</span>")
                voice_save.click(save_my_voice, voice_rec, voice_msg)
                voice_delete.click(delete_my_voice, None, voice_msg)
                voice_test.click(test_my_voice, voice_test_lang, [voice_test_audio, voice_test_msg])

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

        for button, destination in ((home_button, "home"), (edit_button, "flow_editor"),
                                    (create_button, "story"), (quick_button, "clip"),
                                    (voice_button, "voice"), (settings_button, "settings"),
                                    (home_edit, "flow_editor"), (home_story, "story")):
            button.click(lambda target=destination: gr.Tabs(selected=target), outputs=workspaces)
        home_google.click(lambda: (gr.Tabs(selected="flow_editor"), gr.Accordion(open=True)),
                          outputs=[workspaces, google_help])

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
