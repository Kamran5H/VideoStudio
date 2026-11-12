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

1. Double-click **VideoStudio** on your desktop (or run `python app.py`). Your browser opens the studio.
   No desktop icon yet? Double-click **`Install Desktop Shortcut.vbs`** in the VideoStudio folder once.
2. **🎬 Quick clip:** describe what the video should show, pick a shape (Wide, Tall or Square) and a length, then press **Make my video**.
   **📖 Story video:** type a topic, press **Write the script for me** (or paste your own), then press **Make my video**.
3. Watch the status card. When it says ✅ Completed, use the ⬇ button on the player to download.

Voice language, music, subtitles, quality and visual style are under **More options**. API keys are optional and go in **⚙️ Settings**. To stop the studio, use **⏻ Turn off VideoStudio** in Settings.

> **Everything here is free, but real AI video needs one free thing:** a Hugging Face account token. Hugging Face refuses GPU jobs from visitors who are not signed in, so without a token **Quick clip** makes its video from AI pictures with camera motion instead (and tells you so). Create a free account at huggingface.co, open *Settings → Access Tokens*, make a **Read** token and paste it into **⚙️ Settings**. A free account gets a few minutes of GPU time per day, which is a handful of 5-second clips.

## What happens behind the button

VideoStudio runs on your computer, but the heavy diffusion work is sent to free cloud GPUs. FFmpeg then assembles the finished video on your machine.

**🎬 Quick clip** (text-to-video):
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

**📖 Story video** (script-to-video). You write `Visual:` / `VO:` lines, or have them drafted for you (Gemini if you set a key, otherwise a free Pollinations text model, otherwise an offline template). Each scene gets:
- a stock clip from Pexels or Pixabay, or a still image from Wikimedia or Pollinations, animated with Ken Burns motion
- its own narration

Narration and subtitles are timed to each scene's on-screen slot, so they stay in sync across crossfades.

> **Honest resolution note:** the free models generate at about 480p–720p. Higher quality tiers are **upscaled**, not native 4K.

## Requirements

- Python **3.10+**
- **FFmpeg**: the system `ffmpeg` is used if it's on PATH; otherwise the binary bundled with `imageio-ffmpeg` (installed from `requirements.txt`). Burned-in subtitles need an FFmpeg build with libass.
- Optional keys, all set in `.env` (see [`.env.example`](.env.example)) or in the **⚙️ Settings & System** tab:

| Variable | Used for |
|---|---|
| `GEMINI_API_KEY` | Prompt enhancement and storyboard scripts (`GEMINI_MODELS` sets the model order) |
| `HF_TOKEN` | **Needed for real AI video.** Free Hugging Face token (Read); anonymous visitors get no usable ZeroGPU quota |
| `PEXELS_API_KEY`, `PIXABAY_API_KEY` | Stock footage and photos |
| `VIDEOSTUDIO_OUTPUT_DIR` | Where renders go (keep it **outside OneDrive**: sync locks files mid-encode) |

Keys saved in the Settings tab go to `studio_config.json`, which is git-ignored. They are never sent back to the page.

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

### Optional: free Colab GPU worker
1. Open [`colab_worker.ipynb`](colab_worker.ipynb) in Google Colab.
2. Select a T4 GPU and run all cells.
3. Paste the printed `*.gradio.live` URL into **⚙️ Settings & System → Google Colab Worker URL**.

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
app.py                 Gradio UI: Quick clip, Story video, Settings
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
