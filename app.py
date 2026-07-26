"""
app.py — AI Video Generation Studio (glassmorphism popup UI).

Launch:  python app.py

A single-window Gradio app wired end-to-end to video_studio.py. Design is dark
glassmorphism with gradient accents; every control drives a real backend
function — no dead buttons, no placeholders.

Why Gradio (not PyQt6): the backend already delegates generation to cloud GPUs
over HTTP, video playback needs a real media pipeline, and a local web UI gives
premium glass/gradient styling for free via CSS while staying cross-platform and
launchable with one command. PyQt6 would add a heavy native dependency for no
gain here.
"""

from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path

import gradio as gr

from video_studio import (
    LANG_CODES, MUSIC_MOODS, QUALITY_TIERS, STYLE_PRESETS, TTS_VOICES,
    JobSettings, Stage, VideoStudio,
)

STUDIO = VideoStudio()
ASPECTS = ["16:9", "9:16", "1:1", "21:9", "4:3"]
ASPECT_LABELS = {
    "16:9": "16:9 · YouTube", "9:16": "9:16 · Reels/Shorts", "1:1": "1:1 · Square",
    "21:9": "21:9 · Cinematic", "4:3": "4:3 · Classic",
}
LANGUAGES = list(TTS_VOICES.keys())
SUBTITLE_LANGS = ["None"] + list(LANG_CODES.keys())

STAGE_ORDER = [Stage.ENHANCING, Stage.GENERATING, Stage.INTERPOLATING,
               Stage.UPSCALING, Stage.AUDIO, Stage.SUBTITLES, Stage.DONE]
STAGE_ICON = {
    Stage.QUEUED: "◔", Stage.ENHANCING: "✦", Stage.GENERATING: "▣",
    Stage.STITCHING: "⛓", Stage.INTERPOLATING: "⇄", Stage.UPSCALING: "⤢",
    Stage.AUDIO: "♪", Stage.SUBTITLES: "☰", Stage.DONE: "✓",
    Stage.FAILED: "✕", Stage.CANCELLED: "⊘",
}

# ---------------------------------------------------------------------------
# CSS — dark glassmorphism, gradient accents, glow, animation
# ---------------------------------------------------------------------------

CSS = """
:root {
  --bg0:#0b0d17; --bg1:#12152b; --glass:rgba(255,255,255,0.05);
  --glass-brd:rgba(255,255,255,0.12); --accent:#6366F1; --accent2:#a855f7;
  --accent3:#22d3ee; --txt:#e6e8f2; --muted:#8b90b5; --ok:#34d399;
  --warn:#fbbf24; --bad:#f87171;
}
.gradio-container {
  background:
    radial-gradient(1200px 600px at 15% -10%, rgba(99,102,241,0.25), transparent 60%),
    radial-gradient(1000px 500px at 100% 0%, rgba(168,85,247,0.20), transparent 55%),
    radial-gradient(900px 700px at 50% 120%, rgba(34,211,238,0.12), transparent 55%),
    linear-gradient(160deg, var(--bg0), var(--bg1)) !important;
  color: var(--txt) !important; font-family: 'Inter','Segoe UI',system-ui,sans-serif !important;
  max-width: 1400px !important; margin: 0 auto !important;
}
.glass, .gr-group, .gr-box {
  background: var(--glass) !important; border: 1px solid var(--glass-brd) !important;
  border-radius: 18px !important; backdrop-filter: blur(16px) saturate(140%) !important;
  -webkit-backdrop-filter: blur(16px) saturate(140%) !important;
  box-shadow: 0 8px 32px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.06) !important;
}
#hero {
  text-align:center; padding: 26px 0 8px 0;
}
#hero h1 {
  font-size: 2.5rem; font-weight: 800; letter-spacing:-0.02em; margin:0;
  background: linear-gradient(100deg,#c7d2fe,#a855f7 45%,#22d3ee);
  -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent;
  filter: drop-shadow(0 4px 24px rgba(99,102,241,0.5));
}
#hero p { color: var(--muted); margin: 6px 0 0 0; font-size: 0.95rem; }
.panel-title {
  font-weight:700; font-size:1.02rem; color:#c7d2fe; margin:2px 0 10px 0;
  display:flex; align-items:center; gap:8px;
}
.gr-button-primary, button.primary {
  background: linear-gradient(100deg,var(--accent),var(--accent2)) !important;
  border:none !important; color:white !important; font-weight:700 !important;
  border-radius:14px !important; transition: all .2s ease !important;
  box-shadow: 0 6px 20px rgba(99,102,241,0.45) !important;
}
.gr-button-primary:hover, button.primary:hover {
  transform: translateY(-2px) !important;
  box-shadow: 0 10px 30px rgba(168,85,247,0.6) !important; filter:brightness(1.08) !important;
}
.gr-button-secondary, button.secondary {
  background: rgba(255,255,255,0.06) !important; border:1px solid var(--glass-brd) !important;
  color: var(--txt) !important; border-radius:14px !important; transition: all .2s ease !important;
}
.gr-button-secondary:hover { border-color: var(--accent) !important;
  box-shadow: 0 0 18px rgba(99,102,241,0.4) !important; transform: translateY(-1px) !important; }
input, textarea, .gr-input, .gr-text-input {
  background: rgba(10,12,26,0.6) !important; border:1px solid var(--glass-brd) !important;
  color: var(--txt) !important; border-radius:12px !important;
}
textarea:focus, input:focus { border-color: var(--accent) !important;
  box-shadow: 0 0 0 3px rgba(99,102,241,0.25) !important; }
.q-card {
  padding:12px 14px; margin:9px 0; border-radius:14px;
  background: rgba(255,255,255,0.045); border:1px solid var(--glass-brd);
  backdrop-filter: blur(10px);
}
.q-head { display:flex; justify-content:space-between; align-items:center; gap:10px;
  font-size:0.86rem; }
.q-prompt { color:var(--txt); font-weight:600; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; max-width:62%; }
.q-stage { color:var(--accent3); font-weight:700; font-size:0.8rem; }
.q-bar { height:8px; border-radius:6px; background:rgba(255,255,255,0.08); margin-top:9px;
  overflow:hidden; position:relative; }
.q-fill { height:100%; border-radius:6px;
  background: linear-gradient(100deg,var(--accent),var(--accent2),var(--accent3));
  background-size:200% 100%; animation: flow 2s linear infinite;
  box-shadow: 0 0 12px rgba(99,102,241,0.7); transition: width .5s ease; }
@keyframes flow { 0%{background-position:0% 0} 100%{background-position:200% 0} }
.q-msg { color:var(--muted); font-size:0.76rem; margin-top:6px; }
.q-steps { display:flex; gap:5px; margin-top:9px; flex-wrap:wrap; }
.q-step { font-size:0.68rem; padding:2px 8px; border-radius:20px;
  border:1px solid var(--glass-brd); color:var(--muted); }
.q-step.on { color:white; border-color:transparent;
  background:linear-gradient(100deg,var(--accent),var(--accent2)); }
.q-step.done { color:var(--ok); border-color:rgba(52,211,153,0.4); }
.q-done { color:var(--ok)!important; } .q-fail { color:var(--bad)!important; }
.warn-box { color:var(--warn); font-size:0.82rem; padding:8px 12px; border-radius:12px;
  background:rgba(251,191,36,0.08); border:1px solid rgba(251,191,36,0.25); }
.hw-box { color:var(--muted); font-size:0.78rem; font-family:ui-monospace,monospace;
  padding:8px 12px; }
.char-count { color:var(--muted); font-size:0.75rem; text-align:right; }
footer { display:none !important; }
"""


# ---------------------------------------------------------------------------
# UI callback logic
# ---------------------------------------------------------------------------

def count_chars(text: str) -> str:
    return f"{len(text or '')} characters"


def do_enhance(prompt: str, preset: str, seed: float) -> str:
    if not (prompt or "").strip():
        return ""
    return STUDIO.prompts.enhance(prompt.strip(), preset,
                                  int(seed) if seed and seed >= 0 else None)


def quality_warning(quality: str) -> str:
    w = STUDIO.hardware.vram_warning(quality)
    return f'<div class="warn-box">{w}</div>' if w else ""


def duration_note(seconds: float) -> str:
    if seconds > 5:
        n = int(-(-seconds // 5))
        return (f'<div class="warn-box">🎞️ Long-video stitching ON — {seconds:.0f}s '
                f'will be built from {n} clips crossfaded into one continuous video.</div>')
    return '<div class="hw-box">Single-clip generation (≤5s).</div>'


def backend_status_html() -> str:
    return f'<div class="hw-box">{STUDIO.backend_status().replace(chr(10), "<br>")}</div>'


def submit_job(prompt, enhanced, negative, preset, auto_enhance, quality, aspect,
               duration, voiceover, voice_text, voice_lang, voice_gender, voice_speed,
               voice_vol, music, music_mood, music_vol, subtitles, sub_lang,
               sub_translate, sub_burn, sub_font, sub_size, sub_color, sub_pos,
               sub_outline, fps, steps, guidance, seed, interpolate, upscale):
    if not (prompt or "").strip():
        return gr.update(value="⚠️ Enter a prompt first."), gr.update()
    settings = JobSettings(
        prompt=prompt.strip(),
        enhanced_prompt=(enhanced or "").strip() if not auto_enhance else "",
        negative_prompt=negative or "",
        style_preset=preset, auto_enhance=bool(auto_enhance),
        quality=quality, aspect=aspect, duration=float(duration),
        fps=int(fps), steps=int(steps), guidance=float(guidance), seed=int(seed),
        interpolate=bool(interpolate), upscale=bool(upscale),
        voiceover=bool(voiceover), voice_text=voice_text or "",
        voice_language=voice_lang, voice_gender=voice_gender,
        voice_speed=int(voice_speed), voice_volume=float(voice_vol),
        music=bool(music), music_mood=music_mood, music_volume=float(music_vol),
        subtitles=bool(subtitles), subtitle_language=sub_lang,
        subtitle_translate_to=("" if sub_translate == "None" else sub_translate),
        subtitle_burn_in=bool(sub_burn), subtitle_font=sub_font,
        subtitle_size=int(sub_size), subtitle_color=sub_color,
        subtitle_position=sub_pos, subtitle_outline=int(sub_outline),
    )
    job = STUDIO.queue.submit(settings)
    return (gr.update(value=f"✅ Job {job.id} queued."), render_queue())


def _stage_steps_html(job) -> str:
    chips = []
    active_idx = STAGE_ORDER.index(job.stage) if job.stage in STAGE_ORDER else -1
    for i, st in enumerate(STAGE_ORDER):
        cls = "q-step"
        if job.stage == Stage.DONE and st == Stage.DONE:
            cls += " done"
        elif active_idx >= 0 and i < active_idx:
            cls += " done"
        elif i == active_idx:
            cls += " on"
        chips.append(f'<span class="{cls}">{STAGE_ICON.get(st,"")} {st.value}</span>')
    return "".join(chips)


def render_queue() -> str:
    jobs = STUDIO.queue.snapshot()
    if not jobs:
        return ('<div class="hw-box">Queue is empty. Paste a prompt and hit '
                '<b>Generate</b> — jobs run one after another, forever.</div>')
    cards = []
    for job in reversed(jobs[-12:]):
        pct = int(job.progress * 100)
        stage_cls = ("q-done" if job.stage == Stage.DONE else
                     "q-fail" if job.stage in (Stage.FAILED, Stage.CANCELLED) else "")
        eta = job.eta_seconds
        eta_txt = f" · ETA {eta:.0f}s" if eta > 1 else ""
        prompt = (job.settings.prompt[:70] + "…") if len(job.settings.prompt) > 70 \
            else job.settings.prompt
        cards.append(f"""
        <div class="q-card">
          <div class="q-head">
            <span class="q-prompt">{STAGE_ICON.get(job.stage,'')} {prompt}</span>
            <span class="q-stage {stage_cls}">{job.stage.value} · {pct}%{eta_txt}</span>
          </div>
          <div class="q-bar"><div class="q-fill" style="width:{max(pct,3)}%"></div></div>
          <div class="q-msg">#{job.id} · {job.message}</div>
          <div class="q-steps">{_stage_steps_html(job)}</div>
        </div>""")
    return "".join(cards)


def render_gallery():
    items = STUDIO.gallery()
    return [(it["thumb"] or it["video"], f"#{it['id']} · seed {it['seed']}")
            for it in items if it.get("thumb") or it.get("video")]


def gallery_meta() -> list[dict]:
    return STUDIO.gallery()


def on_gallery_select(evt: gr.SelectData, meta):
    if not meta or evt.index is None or evt.index >= len(meta):
        return gr.update(), gr.update(value="")
    item = meta[evt.index]
    return (gr.update(value=item["video"]),
            gr.update(value=f"**#{item['id']}** · seed `{item['seed']}` · {item['prompt']}"))


def regenerate_selected(video_path):
    """Re-submit the job whose output is currently loaded, with its exact seed."""
    if not video_path:
        return gr.update(value="Select a gallery video first."), render_queue()
    for job in STUDIO.queue.snapshot():
        if job.output_video == video_path or Path(job.output_video or "").name == \
                Path(video_path).name:
            s = job.settings
            s.seed = job.actual_seed          # lock the seed for reproduction
            STUDIO.queue.submit(s)
            return gr.update(value=f"♻️ Regenerating #{job.id} at seed {job.actual_seed}."), \
                render_queue()
    return gr.update(value="Could not find source job for that video."), render_queue()


def open_output_folder():
    folder = str(Path(STUDIO.config.output_dir) / "videos")
    try:
        if sys.platform == "win32":
            os.startfile(folder)                                     # noqa: S606
        elif sys.platform == "darwin":
            os.system(f'open "{folder}"')
        else:
            os.system(f'xdg-open "{folder}"')
    except Exception:                                                # noqa: BLE001
        webbrowser.open(Path(folder).as_uri())
    return f"Opened {folder}"


def toggle_pause(current_label):
    paused = current_label.startswith("▶")
    STUDIO.queue.pause(paused)
    return "⏸ Pause queue" if paused else "▶ Resume queue"


def save_settings(hf_token, colab_url):
    STUDIO.config.hf_token = hf_token or ""
    STUDIO.config.colab_url = colab_url or ""
    STUDIO.config.save()
    return backend_status_html(), gr.update(value="✅ Saved. Backends refreshed.")


def cancel_job(job_id):
    if job_id:
        STUDIO.queue.cancel(job_id.strip())
    return render_queue()


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def build_ui() -> gr.Blocks:
    with gr.Blocks(title="AI Video Studio") as demo:
        gallery_state = gr.State([])

        gr.HTML(
            '<div id="hero"><h1>✨ AI Video Generation Studio</h1>'
            '<p>Cinematic text-to-video · free cloud GPUs · voiceover · subtitles · '
            'endless queue</p></div>'
        )

        with gr.Row():
            # ============================ LEFT COLUMN =========================
            with gr.Column(scale=5):
                # ---- Prompt panel ----
                with gr.Group(elem_classes="glass"):
                    gr.HTML('<div class="panel-title">📝 Prompt</div>')
                    prompt = gr.Textbox(
                        label="", placeholder="Paste your idea… e.g. 'a lone lighthouse "
                        "in a storm at night, waves crashing'",
                        lines=4, show_label=False)
                    char_count = gr.HTML('<div class="char-count">0 characters</div>')
                    with gr.Row():
                        enhance_btn = gr.Button("✨ Enhance Prompt", variant="primary",
                                                scale=2)
                        auto_enhance = gr.Checkbox(label="Auto-enhance on generate",
                                                   value=True, scale=1)
                    enhanced = gr.Textbox(label="Enhanced prompt (editable — used when "
                                          "auto-enhance is off)", lines=3, visible=True)
                    negative = gr.Textbox(
                        label="Negative prompt (added to smart defaults)",
                        placeholder="things to avoid…", lines=2)

                # ---- Quality / aspect / duration ----
                with gr.Group(elem_classes="glass"):
                    gr.HTML('<div class="panel-title">🎬 Format</div>')
                    quality = gr.Radio(QUALITY_TIERS, value="480p",
                                       label="Video quality (higher tiers upscaled)")
                    quality_warn = gr.HTML(quality_warning("480p"))
                    aspect = gr.Radio([(ASPECT_LABELS[a], a) for a in ASPECTS],
                                      value="16:9", label="Aspect ratio")
                    with gr.Row():
                        duration = gr.Slider(2, 60, value=5, step=1,
                                             label="Duration (seconds)", scale=3)
                        duration_num = gr.Number(value=5, label="s", scale=1,
                                                 minimum=2, maximum=60)
                    duration_info = gr.HTML(duration_note(5))

                # ---- Audio panel ----
                with gr.Group(elem_classes="glass"):
                    gr.HTML('<div class="panel-title">🔊 Audio</div>')
                    voiceover = gr.Checkbox(label="AI voiceover", value=False)
                    voice_text = gr.Textbox(
                        label="Voiceover script (blank = use prompt)", lines=2,
                        visible=False)
                    with gr.Row(visible=False) as voice_row:
                        voice_lang = gr.Dropdown(LANGUAGES, value="English",
                                                 label="Language")
                        voice_gender = gr.Dropdown(["female", "male", "neutral"],
                                                   value="female", label="Voice")
                        voice_speed = gr.Slider(-40, 40, value=0, step=5,
                                                label="Speed %")
                    voice_vol = gr.Slider(0, 1.5, value=1.0, step=0.05,
                                          label="Voiceover volume", visible=False)
                    music = gr.Checkbox(label="Background music", value=False)
                    with gr.Row(visible=False) as music_row:
                        music_mood = gr.Dropdown(MUSIC_MOODS, value="Ambient",
                                                 label="Mood")
                        music_vol = gr.Slider(0, 1.0, value=0.35, step=0.05,
                                              label="Music volume (ducks under voice)")

                # ---- Subtitle panel ----
                with gr.Group(elem_classes="glass"):
                    gr.HTML('<div class="panel-title">☰ Subtitles</div>')
                    subtitles = gr.Checkbox(label="Generate subtitles", value=False)
                    with gr.Column(visible=False) as sub_col:
                        with gr.Row():
                            sub_lang = gr.Dropdown(LANGUAGES, value="English",
                                                   label="Subtitle language")
                            sub_translate = gr.Dropdown(
                                SUBTITLE_LANGS, value="None",
                                label="Translate to (2nd language)")
                        with gr.Row():
                            sub_burn = gr.Checkbox(label="Burn into video", value=True)
                            sub_font = gr.Dropdown(
                                ["Arial", "Verdana", "Georgia", "Impact",
                                 "Courier New", "Trebuchet MS"],
                                value="Arial", label="Font")
                        with gr.Row():
                            sub_size = gr.Slider(12, 48, value=24, step=1, label="Size")
                            sub_color = gr.ColorPicker(value="#FFFFFF", label="Color")
                        with gr.Row():
                            sub_pos = gr.Radio(["Bottom", "Middle", "Top"],
                                               value="Bottom", label="Position")
                            sub_outline = gr.Slider(0, 5, value=2, step=1,
                                                    label="Outline")
                        gr.HTML('<div class="hw-box">Off = external .srt only · '
                                'On = styled burned-in captions + .srt export.</div>')

                # ---- Advanced drawer ----
                with gr.Accordion("⚙️ Advanced", open=False,
                                  elem_classes="glass"):
                    style_preset = gr.Dropdown(
                        list(STYLE_PRESETS.keys()), value="Cinematic",
                        label="Style preset")
                    with gr.Row():
                        fps = gr.Slider(8, 32, value=16, step=1, label="FPS (base 16)")
                        steps = gr.Slider(10, 60, value=30, step=1,
                                          label="Sampling steps")
                    with gr.Row():
                        guidance = gr.Slider(1, 12, value=6, step=0.5,
                                             label="Guidance scale")
                        seed = gr.Number(value=-1, label="Seed (-1 = random)")
                    with gr.Row():
                        interpolate = gr.Checkbox(
                            label="Frame interpolation (2× FPS)", value=False)
                        upscale = gr.Checkbox(
                            label="Upscale to target quality", value=True)
                    with gr.Accordion("🔌 Backends & credentials", open=False):
                        gr.HTML('<div class="hw-box">Free GPUs. Start the Colab worker '
                                '(colab_worker.ipynb) and paste its gradio.live URL, '
                                'and/or add a free huggingface.co token for ZeroGPU '
                                'quota.</div>')
                        hf_token = gr.Textbox(label="Hugging Face token (free)",
                                              type="password",
                                              value=STUDIO.config.hf_token)
                        colab_url = gr.Textbox(label="Colab worker URL (*.gradio.live)",
                                               value=STUDIO.config.colab_url)
                        save_cfg_btn = gr.Button("💾 Save & refresh backends",
                                                 variant="secondary")

                generate_btn = gr.Button("🚀 Generate Video", variant="primary",
                                         size="lg")
                submit_status = gr.Markdown("")

            # ============================ RIGHT COLUMN ========================
            with gr.Column(scale=4):
                with gr.Group(elem_classes="glass"):
                    gr.HTML('<div class="panel-title">🖥️ System</div>')
                    gr.HTML(f'<div class="hw-box">{STUDIO.hardware.summary()}</div>')
                    backend_html = gr.HTML(backend_status_html())

                with gr.Group(elem_classes="glass"):
                    gr.HTML('<div class="panel-title">📋 Queue &amp; progress</div>')
                    with gr.Row():
                        pause_btn = gr.Button("⏸ Pause queue", variant="secondary",
                                              scale=2)
                        cancel_id = gr.Textbox(label="", placeholder="job id to cancel",
                                               scale=2, show_label=False)
                        cancel_btn = gr.Button("Cancel", variant="secondary", scale=1)
                    queue_html = gr.HTML(render_queue())

                with gr.Group(elem_classes="glass"):
                    gr.HTML('<div class="panel-title">🎞️ Gallery</div>')
                    gallery = gr.Gallery(value=render_gallery(), columns=3, height=240,
                                         object_fit="cover", label="", show_label=False)
                    player = gr.Video(label="Preview", height=280)
                    gallery_caption = gr.Markdown("")
                    with gr.Row():
                        regen_btn = gr.Button("♻️ Regenerate (same seed)",
                                              variant="secondary")
                        open_btn = gr.Button("📂 Open folder", variant="secondary")
                    gallery_status = gr.Markdown("")

        # ---------------- wiring ----------------
        prompt.change(count_chars, prompt, char_count)
        enhance_btn.click(do_enhance, [prompt, style_preset, seed], enhanced)
        quality.change(quality_warning, quality, quality_warn)
        duration.change(duration_note, duration, duration_info)
        duration.change(lambda v: v, duration, duration_num)
        duration_num.change(lambda v: v, duration_num, duration)
        duration_num.change(duration_note, duration_num, duration_info)

        voiceover.change(lambda v: [gr.update(visible=v)] * 3,
                         voiceover, [voice_text, voice_row, voice_vol])
        music.change(lambda v: gr.update(visible=v), music, music_row)
        subtitles.change(lambda v: gr.update(visible=v), subtitles, sub_col)

        save_cfg_btn.click(save_settings, [hf_token, colab_url],
                           [backend_html, submit_status])

        gen_inputs = [prompt, enhanced, negative, style_preset, auto_enhance, quality,
                      aspect, duration, voiceover, voice_text, voice_lang, voice_gender,
                      voice_speed, voice_vol, music, music_mood, music_vol, subtitles,
                      sub_lang, sub_translate, sub_burn, sub_font, sub_size, sub_color,
                      sub_pos, sub_outline, fps, steps, guidance, seed, interpolate,
                      upscale]
        generate_btn.click(submit_job, gen_inputs, [submit_status, queue_html])

        pause_btn.click(toggle_pause, pause_btn, pause_btn)
        cancel_btn.click(cancel_job, cancel_id, queue_html)
        regen_btn.click(regenerate_selected, player, [gallery_status, queue_html])
        open_btn.click(open_output_folder, None, gallery_status)
        gallery.select(on_gallery_select, gallery_state, [player, gallery_caption])

        # live refresh loop (queue + gallery) every 1.5s
        timer = gr.Timer(1.5)
        timer.tick(render_queue, None, queue_html)
        timer.tick(lambda: render_gallery(), None, gallery)
        timer.tick(gallery_meta, None, gallery_state)

    return demo


if __name__ == "__main__":
    ui = build_ui()
    ui.queue(default_concurrency_limit=8).launch(
        css=CSS, theme=gr.themes.Base(), inbrowser=True, show_error=True)
