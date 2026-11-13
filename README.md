# 🎥 VideoStudio

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://github.com/Kamran5H/VideoStudio)
[![UI: Gradio 6](https://img.shields.io/badge/UI-Gradio%206-FF7C00?style=for-the-badge&logo=gradio&logoColor=white)](https://gradio.app/)
[![CI](https://github.com/Kamran5H/VideoStudio/actions/workflows/ci.yml/badge.svg)](https://github.com/Kamran5H/VideoStudio/actions/workflows/ci.yml)

**A local Gradio studio that turns a prompt into an AI video clip, or a script into a narrated, subtitled storyboard video. GPU work runs on free cloud backends.**

</div>

---

## Using it (no experience needed)

1. Double-click **VideoStudio** on your desktop (or run `python app.py`). Your browser opens the studio on the **🏠 Start** page.
   No desktop icon yet? Double-click **`Install Desktop Shortcut.vbs`** in the VideoStudio folder once.
2. Pick one of the three cards, or use the bar at the top:
   - **📖 Make a story:** type a topic and press **Make my video**. The script is written for you; open *Review or write my script* to see or change it first.
   - **✂️ Edit a video:** add a clip, optionally add a caption, trim it or record your voice, then press **Make my finished video**.
   - **🎬 AI clip:** describe one scene, pick a shape and a length, then press **Make my video**.
3. Watch the status card. **⏹ Stop this video** appears only while a video is rendering. If a video fails or you stop it, the card stays with the reason and a **🔁 Try again** button that continues from the last finished part.
4. **📁 My videos** lists every finished video with its captions and render report. You can play, download or delete them there, or open the folder.

**🎙️ My voice (optional):** record 10 to 20 seconds of yourself, press **Save my voice**, and the Voice choice under *More options* switches to **My voice**. It is a close likeness, not a perfect copy. It works in English, Hindi, Arabic, Spanish, French, German, Chinese and Japanese (the free cloning model has no Urdu or Punjabi yet), and it needs the same free Hugging Face token as AI video. If My voice cannot be used for a video, you are told why when you press **Make my video**, before anything is queued.

**Language** is on the main form: the script is written and spoken in it. If your script or narration is written in a different language's script (for example Urdu text with Language set to English), you are told before the video starts, because the narrator could not read it. Voice, music, subtitles, quality and visual style are under **More options**. **⚙️ Settings** shows one key up front, the free Hugging Face token; the other keys are under *More keys (optional)*. To stop the studio, use **⏻ Turn off VideoStudio** in Settings.

> **The studio uses local processing and hosted services; provider availability and account limits apply. Real AI video needs:** a Hugging Face account token. Hugging Face refuses GPU jobs from visitors who are not signed in, so without a token **AI clip** makes its video from AI pictures with camera motion instead (and tells you so). Create a free account at huggingface.co, open *Settings → Access Tokens*, make a **Read** token and paste it into **⚙️ Settings**. A free account gets a few minutes of GPU time per day, which is a handful of 5-second clips.

## What happens behind the button

VideoStudio runs on your computer, but the heavy diffusion work is sent to free cloud GPUs. FFmpeg then assembles the finished video on your machine.

**🎬 AI clip** (text-to-video):
1. **Prompt enhancement.** Gemini (if you set a key) rewrites the prompt. If not, a free Pollinations text model does it, and an offline style template is the last fallback.
2. **Generation in 5-second chunks.** Each chunk is chained to the next through its last frame. Backends are tried in order:
   - your own **Colab T4 worker** running Wan2.1-T2V-1.3B ([`colab_worker.ipynb`](colab_worker.ipynb))
   - **LTX-2.3** (official `Lightricks/LTX-2-3` Space; continues each chunk from the last frame)
   - **Wan 2.2 14B** with the 4-step Lightning LoRA (`Upsampler/wan-2-2-14b-text-to-video`; cheapest in GPU time)
   - **LTX-Video distilled** (`Lightricks/ltx-video-distilled`)
   - **CogVideoX-5B** (`zai-org/CogVideoX-5B-Space`; wide and square only, it always renders 720x480)

   Every call is built from the signature each Space publishes, and each returned clip is cropped to the requested shape and cut to the requested length, because the models return their own native sizes and durations.
3. **When free GPU quota runs out** and a token is saved, the job waits and resumes by itself. Finished chunks are checkpointed to disk, so nothing is re-generated. With no token (waiting would never help), or if every backend is genuinely down, the studio uses stock footage or an AI still image with **Ken Burns** motion instead.
4. **Post-processing with FFmpeg:** crossfades and a Lanczos **upscale** to the exact standard frame: 1920x1080, 1080x1920 or 1080x1080 (and the 720p / 4K equivalents).
5. **Audio:** if you typed narration, **Edge-TTS** speaks it (gTTS as fallback) in 10 languages with word-timed subtitles. Background music ducks under the voice. With no narration, the clip gets music only.

**Voice cloning** uses the free `ResembleAI/Chatterbox-Multilingual-TTS` Space. Narration is sent in pieces of up to 280 characters (the Space cuts anything past 300) and joined with FFmpeg. Each piece costs about a minute of the free daily GPU time. Your recording is stored only in `outputs/voices/` and is uploaded to that Space while a video is being made.

**📖 Make a story** (script-to-video). You write `Visual:` / `VO:` lines, or have them drafted for you (Gemini if you set a key, otherwise a free Pollinations text model, otherwise a clear error asking you to paste a script). Each scene gets:
- a stock clip from Pexels or Pixabay, or a still image from Pexels, Pixabay, Wikimedia or Pollinations, animated with Ken Burns motion. Searches use the first few concrete words of the visual line (Pixabay rejects queries over 100 characters); if they find nothing, the first three words are tried. A file near the output size is chosen, not a 4K download.
- its own narration

Narration and subtitles are timed to each scene's on-screen slot, so they stay in sync across crossfades.

> **Honest resolution note:** the free models generate at about 480p–720p. Higher quality tiers are **upscaled**, not native 4K.

## Output quality and service failures

- Prompts preserve the described subject, setting, action and time of day. Style presets apply a visual treatment without inserting unrelated scenery.
- Stock selection prefers fresh shots and suitable framing. Downloads are cached by source and rendition to avoid fetching the same file again.
- Still images use gentle pans and zooms spread over the scene duration. Crossfade failures retain the scene timeline so later narration stays aligned.
- H.264 exports use CRF 18 and fast-start MP4 packaging. Upscaling uses Lanczos without aggressive sharpening; it does not create native detail.
- A narration that comes back far too short for its text stops the job with a clear reason instead of finishing as a near-silent video.
- AI pictures for tall videos are requested in a 3:4 frame and cropped, because narrower requests often return two pictures stacked. The free picture service may add its own small logo.
- A missing visual never becomes a text placeholder. If both speech services fail, narration does not become silence. The job stops with a useful error and can be retried after the cause is resolved.
- Script generation requires complete, distinct scene pairs in the requested count. When services are unavailable, paste your own script; the studio does not replace it with generic narration or an English template for another language.
- AI scripts are drafts, and generated imagery is illustrative. Review narration and verify factual claims before publishing. Stock search results also need editorial review for relevance.
- The render report is saved beside job checkpoints as `outputs/jobs/<job_id>/render_report.json`. It records source files, scene/chunk types, export dimensions and duration. Older checkpoints may have incomplete source records.

## Requirements

- Python **3.10+**
- **FFmpeg**: the system `ffmpeg` is used if it's on PATH; otherwise the binary bundled with `imageio-ffmpeg` (installed from `requirements.txt`). Burned-in subtitles need an FFmpeg build with libass.
- Optional keys, all set in `.env` (see [`.env.example`](.env.example)) or in the **⚙️ Settings** tab:

| Variable | Used for |
|---|---|
| `GEMINI_API_KEY` | Prompt enhancement and storyboard scripts (`GEMINI_MODELS` sets the model order) |
| `HF_TOKEN` | **Needed for real AI video.** Free Hugging Face token (Read); anonymous visitors get no usable ZeroGPU quota |
| `PEXELS_API_KEY`, `PIXABAY_API_KEY` | Stock footage and photos |
| `VIDEOSTUDIO_OUTPUT_DIR` | Where renders go (keep it **outside OneDrive**: sync locks files mid-encode) |

Keys saved in the Settings tab go to `studio_config.json`, which is git-ignored. They are never sent back to the page.

Surrounding spaces and quotes in pasted keys are removed. A key the provider rejects (HTTP 401/403), an exhausted limit (429) or an unavailable Gemini model is written once to the technical log (**⚙️ Settings → Technical log**) instead of failing silently.

## Install and run

```bash
git clone https://github.com/Kamran5H/VideoStudio.git
cd VideoStudio
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # optional; macOS/Linux: cp
python app.py                   # opens http://127.0.0.1:7860
```

On Windows, `run_videostudio.vbs` starts the studio silently in the background (no console window) and opens the browser. `Install Desktop Shortcut.vbs` creates the **VideoStudio** desktop icon for it and repairs older VideoStudio shortcuts after the folder is moved; the launcher also recreates the icon if it is missing. `Start Video Studio.bat` does the same in a visible console window, which is handy for seeing errors. `python video_studio.py --probe` prints hardware and backend diagnostics.

### Optional: your own Colab GPU worker
1. Open [`colab_worker.ipynb`](colab_worker.ipynb) in Google Colab.
2. **Runtime → Change runtime type**: with Colab Pro pick **L4** or **A100** (several times faster); on a **T4**, switch **High-RAM** on. On a T4 with standard RAM the 11 GB text encoder does not fit, and the first cell says so instead of crashing the session.
3. **Run all**, then paste the printed `*.gradio.live` URL into **⚙️ Settings → More keys → Colab worker URL**, press **💾 Save settings**, then **🔄 Check connection**.

The notebook adapts to the GPU: on L4/A100 the text encoder runs on the GPU, and A100 keeps every model loaded. A T4 renders about one step a minute, so steps are capped at 25 there. The job card shows the worker's step progress; the studio keeps waiting while steps advance and gives up only after 45 minutes without progress. If the same chunk is sent again (after a restart or retry), the worker returns the clip it already made. Wan's own requirements are installed without its `numpy<2` and `flash_attn` pins, which would otherwise break Colab's NumPy or spend half an hour compiling.

## Refresh and job recovery

Jobs run in the local worker thread, not in the browser, so refreshing or closing the page doesn't stop a video. On reload, each tab shows its running job or its latest finished video. Interrupted jobs resume from their last checkpoint when the studio restarts. Only one studio process at a time owns the queue; a second instance opens view-only.

## Development

```bash
pip install -r requirements-dev.txt
ruff check .                                  # lint
pyright app.py video_studio.py                # type check
python -m unittest discover -s tests -v       # offline tests (run real FFmpeg, no network)
```

Tests write to a temporary output folder, never your `outputs/`. `test_verify.py` and `test_gemini_features.py` are opt-in **live** smoke checks: they use the network and API quota, so CI does not run them.

## Repository layout

```text
app.py                 Gradio UI: Start, Edit a video, Make a story, AI clip, My videos, My voice, Settings
local_editor.py        Local trim / caption / voiceover / join editor (FFmpeg only, no network)
video_studio.py        Pipeline: backends, media fetch, TTS, subtitles, FFmpeg engine, job queue
colab_worker.ipynb     Free Colab GPU worker exposing /generate for Wan2.1-T2V-1.3B
tests/                 Offline unit and FFmpeg regression tests
Wan2.1-main/           Unmodified upstream Wan2.1 reference copy (Apache-2.0); not imported by the app
Start Video Studio.bat / run_videostudio.vbs   Windows launchers (visible console / silent)
Install Desktop Shortcut.vbs                   Creates or repairs the desktop icon
```

---

## 📜 License

This project is released under the [MIT License](LICENSE).
Copyright (c) 2024-2026 **Kamran Ashraf**. `Wan2.1-main/` keeps its own Apache-2.0 license.

---

<div align="center">

<a href="https://github.com/Kamran5H">
  <img src="https://raw.githubusercontent.com/Kamran5H/Kamran5H/main/assets/kamran-developer-badge.png" width="85" height="85" style="border-radius: 50%; border: 2px solid #00F2FE; box-shadow: 0 4px 14px rgba(0,0,0,0.18);" alt="Developer: Kamran Ashraf" />
</a>

<br/>

### 👨‍💻 **Developer:** [Kamran Ashraf](https://github.com/Kamran5H)
*AI Systems Architect • Computational Physical Chemist • Automation Pioneer*

[![Developer Profile](https://img.shields.io/badge/GitHub-Kamran5H-181717?style=flat-square&logo=github&logoColor=white)](https://github.com/Kamran5H)
[![Developer Portfolio](https://img.shields.io/badge/Portfolio-Visit%20Website-00F2FE?style=flat-square&logo=vercel&logoColor=black)](https://kamran5h.github.io/Portfolio/)
[![Contact Email](https://img.shields.io/badge/Email-chkamran32b%40gmail.com-EA4335?style=flat-square&logo=gmail&logoColor=white)](mailto:chkamran32b@gmail.com)

</div>
