# 🎬 AI Video Generation Studio

A premium, single-window studio that turns a short text prompt into a finished
video — cinematic prompt enhancement, **free** cloud-GPU generation, long-video
stitching, AI voiceover, background music, and styled subtitles — with an endless
crash-safe job queue.

Everything runs locally **except the actual model inference**, which is delegated
to free GPUs because this machine has no CUDA GPU (see *Why cloud* below).

---

## 🚀 Launch (one command)

```bash
pip install -r requirements.txt
python app.py
```

The studio opens in your browser at `http://127.0.0.1:7860`. That's it —
**real AI video works out of the box** via the free LTX-Video backend
(no account, no token, no setup). Type a prompt and hit Generate.

---

## 🆓 The free GPU backends (tried in this order, automatic fallback)

Your laptop (Intel Iris Xe, no NVIDIA GPU, 8 GB RAM) **cannot** run video models
locally — generation runs on free cloud GPUs:

| Order | Backend | Setup needed | Notes |
|-------|---------|--------------|-------|
| 1 | **Colab worker (Wan2.1)** | run `colab_worker.ipynb`, paste URL | full Wan2.1 quality, most reliable |
| 2 | **LTX-Video** ⭐ default | **none** | works anonymously, ~40 s per clip, 30 fps |
| 3 | CogVideoX-5B | none | slower fallback, 720×480 |
| 4 | **Wan2.1-14B official** | none | true 720p, free public queue (5–20 min/clip) |
| 5 | Test pattern | — | **NOT AI** — disabled by default, heavily watermarked |

**Long videos are continuous now:** each 5 s chunk is conditioned on the last
frame of the previous chunk (image-to-video chaining), so a 30 s request is one
coherent scene, not disconnected clips.

**Quota handling — the queue never gives up and never fakes output.** Free
anonymous GPU quota (LTX/CogVideoX) is limited per rolling window. When it runs
out, the job **defers itself and auto-retries** when the window replenishes
(queue card shows "⏳ auto-retry at HH:MM"); already-generated clips are kept, so
a resume costs no extra quota. Meanwhile the Wan 14B official queue is unlimited
but slow. Add a **free** Hugging Face token
(<https://huggingface.co/settings/tokens> → **⚙️ Advanced → Backends**) to get
much more ZeroGPU quota, or use the Colab worker for the fastest sessions.

### Option A — Google Colab free T4 GPU  (full Wan2.1 quality)

1. Open **`colab_worker.ipynb`** in [Google Colab](https://colab.research.google.com/).
2. **Runtime → Change runtime type → T4 GPU**, then **Runtime → Run all**.
3. Copy the `https://xxxxx.gradio.live` URL the last cell prints.
4. In the studio: **⚙️ Advanced → 🔌 Backends & credentials → Colab worker URL** →
   paste → **Save & refresh backends**.
5. Keep the Colab tab open while generating. If it idles out, re-run the notebook
   for a fresh URL.

This runs the genuine **Wan2.1-T2V-1.3B** model on Colab's GPU — same quality as
running it on your own RTX card, at zero cost.

### Option B — Hugging Face ZeroGPU Space

1. Get a free token at <https://huggingface.co/settings/tokens>.
2. In the studio: **⚙️ Advanced → 🔌 Backends & credentials → Hugging Face token** →
   paste → **Save & refresh backends**.

The studio calls a public Wan Space on HF's free ZeroGPU pool. Availability and
queue times depend on HF; Colab is more reliable, so it's tried first.

---

## 🎛️ What every control does

**Prompt panel** — paste an idea. **✨ Enhance Prompt** rewrites it into a rich
cinematic prompt (camera, lens, lighting, color grade, motion, mood) using the
chosen style preset; the result is shown for you to edit. *Auto-enhance* does this
automatically at generate time. The **negative prompt** is added on top of smart
defaults that suppress blur, flicker, artifacts, deformed anatomy and watermarks.

**Format**
- **Quality** — 480p / 720p / 1080p / 4K. Above 480p the video is generated at the
  model's native size and **upscaled** (Lanczos locally, or higher quality on the
  Colab worker). A VRAM/backend note appears under the selector.
- **Aspect ratio** — 16:9 (YouTube), 9:16 (Reels/Shorts), 1:1 (Square),
  21:9 (Cinematic), 4:3 (Classic).
- **Duration** — 2–60 s. Above 5 s, **long-video stitching** turns on: the studio
  generates multiple clips and crossfades them into one continuous video. The panel
  tells you how many clips will be stitched.

**Audio**
- **AI voiceover** — free Microsoft neural voices in **English, Urdu, Hindi, Arabic,
  Spanish, French, Chinese**; male/female/neutral; adjustable speed and volume.
  Blank script = narrate the prompt. If narration is longer than the video, the last
  frame is held so nothing gets cut off.
- **Background music** — a mood bed (Ambient / Uplifting / Dramatic / Calm /
  Energetic) that **auto-ducks** under the voiceover via sidechain compression. Drop
  your own `music/<mood>.mp3` to override the built-in synth pad.

**Subtitles**
- Generate from the voiceover with **word-accurate timing**, or from the prompt.
- **Translate** to a second language (free Google translation).
- **Burn-in** styled captions (font, size, color, position, outline) **and** export
  an external `.srt`. Off = `.srt` only.

**⚙️ Advanced** — style preset, FPS, sampling steps, guidance scale, seed
(-1 = random), frame interpolation (2× FPS), upscaler toggle, and the backend
credentials panel.

**Queue & progress** — every job shows a live animated bar with stage labels
(Enhancing → Generating → Interpolating → Upscaling → Audio → Subtitles → Done),
ETA, a **Pause** button, and per-job **Cancel** (paste the job id). Jobs run one
after another forever and **survive restarts** — a crash mid-queue re-queues the
job and continues.

**Gallery** — finished videos as thumbnails; click to preview, **♻️ Regenerate**
re-runs with the exact same seed, **📂 Open folder** opens `outputs/videos`.

Every job also saves `outputs/jobs/<id>/settings.json` for exact reproduction.

---

## 📁 Where things land

```
D:\VideoStudio\
├─ app.py                 # the UI — launch this
├─ video_studio.py        # the whole backend (one file)
├─ colab_worker.ipynb     # free Colab T4 GPU worker
├─ requirements.txt       # pinned, verified
├─ music/                 # drop <mood>.mp3 files here (optional)
├─ outputs/
│  ├─ videos/             # finished .mp4 files
│  ├─ jobs/<id>/          # per-job settings.json, thumbnail, .srt
│  ├─ queue/jobs.json     # persistent queue (crash recovery)
│  └─ logs/studio.log     # rotating structured log
├─ Wan2.1-main/           # the original Wan2.1 model code (reference / local use)
└─ _TRASH_REVIEW/         # nothing deleted — see DELETION_LIST.md
```

---

## 🔧 Troubleshooting

| Symptom | Fix |
|---------|-----|
| **Video is a gradient with red "TEST CLIP" text** | No cloud GPU was reachable — check internet, add a free HF token, or start the Colab worker. The queue card names the backend used per job. |
| **First jobs work, later ones fall back to TEST CLIP** | Free anonymous GPU quota for the day is used up. Add a free HF token (raises quota) or use the Colab worker. |
| **"No generation backend available"** | Start `colab_worker.ipynb` and paste its URL, or add an HF token, or leave the test-pattern fallback on (Advanced). |
| **Colab URL stopped working** | Free Colab sessions idle-out. Re-run the notebook (`Runtime → Run all`) and paste the new `*.gradio.live` URL. |
| **Colab: `CUDA out of memory`** | Use a shorter duration or 480p; the worker already offloads to fit the T4. The studio also auto-retries once at lower resolution on OOM. |
| **Colab: model download is slow** | First run pulls ~17 GB of weights into the session — normal; it's cached for that session. |
| **No sound in the output** | Enable **AI voiceover** and/or **Background music** in the Audio panel. Without either, the video is silent by design. |
| **Subtitles not showing on the video** | Turn on **Burn into video**. Otherwise only the `.srt` is written (in `outputs/jobs/<id>/`). |
| **`ffmpeg not found`** | It's bundled via `imageio-ffmpeg`; just `pip install -r requirements.txt`. |
| **Non-English voice sounds wrong / silent** | Pick the matching **Language** in the Audio panel so the correct neural voice is used. |
| **Port 7860 in use** | Close the other Gradio app, or edit the `launch()` call at the bottom of `app.py` to add `server_port=7861`. |
| **Unicode/emoji crash in console** | Already handled — the app forces UTF-8 on stdout/stderr. |

---

## 🖥️ Run fully local (only if you get an NVIDIA GPU)

If you move this to a machine with ≥8 GB CUDA VRAM, you can skip the cloud:
install a CUDA build of PyTorch, download the weights into `Wan2.1-main/`, and the
`HardwareProbe` will report `Local Wan2.1 capable: True`. (A local backend adapter
can then be added to `video_studio.py` mirroring `ColabBackend`.) On this laptop
that path is not available, which is why cloud GPUs are the default.

---

## Why a local web UI (not PyQt6)

Generation already talks to cloud GPUs over HTTP, and video preview needs a real
media pipeline. A local Gradio app gives premium glass/gradient styling via CSS,
plays videos natively, runs cross-platform, and launches with one command — with
no heavy native GUI dependency. PyQt6 would add weight for no benefit here.
