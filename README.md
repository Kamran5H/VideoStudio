# 🎬 VideoStudio Pro — 4K AI Video & Storyboard Studio
### 👑 Developer: Kamran Ashraf (Kami) · 💎 Gemini AI Pro Plan Connected · Zero-Billing Guaranteed

Welcome to **VideoStudio Pro**, the all-in-one cinematic AI Video Production Studio designed for effortless creation of ultra-realistic 4K AI diffusion videos, multi-scene storyboards, multilingual neural voiceovers, ambient music beds, and word-by-word karaoke subtitles with full crash-resilient auto-recovery!

---

## 📑 Table of Contents
1. [🚀 Quick Start: How to Run Step-by-Step](#-quick-start-how-to-run-step-by-step)
2. [🖥️ Studio Interface & Tab-by-Tab Guide](#-studio-interface--tab-by-tab-guide)
   - [Tab 1: 🌟 4K AI Video (Text-to-Video Diffusion)](#tab-1--4k-ai-video)
   - [Tab 2: 📜 Storyboard Director (Multi-Scene Movies)](#tab-2--storyboard-director)
   - [Tab 3: ⚙️ Settings & System (Credentials, Hardware & Queue)](#tab-3--settings--system)
3. [🛡️ Crash-Resilience & Auto-Resume Guide](#-crash-resilience--auto-resume-guide)
4. [💎 Gemini AI Pro & Zero-Billing Architecture](#-gemini-ai-pro--zero-billing-architecture)
5. [📁 Project Files & Output Folders](#-project-files--output-folders)
6. [❓ Troubleshooting & Frequently Asked Questions](#-troubleshooting--frequently-asked-questions)

---

## 🚀 Quick Start: How to Run Step-by-Step

You can launch VideoStudio Pro in any of these 3 easy ways:

### 🌟 Option 1: One-Click Desktop Icon (Easiest)
1. Go to your Windows **Desktop**.
2. Double-click the golden cinema icon **`VideoStudio Pro - Kamran Ashraf.lnk`** (or double-click **`VideoStudio/Start Video Studio.bat`**).
3. The server boots automatically and opens your web browser to:
   ```
   http://127.0.0.1:7860
   ```

---

### 🌐 Option 2: Master Cloud Suite Portal
1. In the main project folder, double-click **`Start Cloud Suite.bat`**.
2. Open your browser to **`http://127.0.0.1:8080`**.
3. In the Master Cloud Hub dashboard, click the glowing **"🎬 VideoStudio Pro"** card — it will automatically launch VideoStudio in the background and open the studio interface.

---

### 💻 Option 3: Command Line / Terminal
Open PowerShell or Command Prompt in the project folder and run:
```powershell
python VideoStudio/app.py
```
Then visit **`http://127.0.0.1:7860`** in Microsoft Edge or Google Chrome.

---

## 🖥️ Studio Interface & Tab-by-Tab Guide

```
+-----------------------------------------------------------------------------------------------+
|  🎬 VideoStudio Pro [4K Ultra HD]              [👑 Kami] [💎 Pro Plan Connected · Zero-Billing]  |
+-----------------------------------------------------------------------------------------------+
| [🌟 4K AI Video]           [📜 Storyboard Director]              [⚙️ Settings & System]         |
+-----------------------------------------------------------------------------------------------+
```

---

### Tab 1: 🌟 4K AI Video
*Generate ultra-crisp, high-definition AI diffusion video clips from text prompts.*

#### Step-by-Step Workflow:
1. **Template Presets (Optional)**: Choose from the `💡 Load Template Preset` dropdown (*Golden Eagle 4K, Cyberpunk Chase, Deep Space, Ocean Kingdom, اردو سینما*) to instantly populate settings.
2. **Enter Your Concept**: Type your scene description into **Prompt / Creative Vision**.
3. **`[✨ Enhance with AI]`**: Click this button! Gemini Pro rewrites your prompt with professional 35mm cinema lens optics, volumetric lighting, and color grading.
4. **Core Essentials**:
   - **Quality Tier**: `4K Ultra HD` (default), `1080p`, or `720p`.
   - **Aspect Ratio**: `16:9` (default YouTube/TV), `9:16` (TikTok/Reels/Shorts), `1:1` (Square/Instagram).
   - **Style Preset**: *Cinematic, Anime, Cyberpunk, Photorealistic, Hyper-realistic, Fantasy, 3D Render, Vintage Film, Horror Dark, Documentary*.
   - **Duration**: Choose from 2 to 60 seconds (chained with crossfading for long clips).
5. **Drawer 1: 🎙️ Voiceover, Music & Subtitles (Optional)**:
   - Voiceover narration script (neural actor speaks this text).
   - Language (*Urdu, English, Punjabi, Hindi, Arabic, Spanish, etc.*) and Voice (*Male / Female*).
   - Background Music Mood (*Ambient, Dramatic, Uplifting, Calm, Energetic, None*).
   - Burn Styled Subtitles (*Classic White, Neon Glow, Gold Luxury*).
6. **Drawer 2: ⚙️ Advanced Parameters (Optional)**:
   - Negative prompt, 60 FPS motion interpolation, Anti-fingerprint filter, Seed, and Branding watermark.
7. **Action & Viewport**:
   - Click **`[🚀 Generate 4K AI Video]`** to start rendering.
   - Watch live progress in the **4K Cinema Viewport** and click **`[📂 Open Videos Folder]`** when finished.

---

### Tab 2: 📜 Storyboard Director
*Turn an idea or script into a complete multi-scene documentary or story with automatic voiceovers, stock footage, AI artwork, and subtitles.*

#### Step-by-Step Workflow:
1. **Templates**: Pick a story preset (*Ancient Pyramids, Quantum AI, Deep Ocean, اردو سبق آموز کہانی*).
2. **Topic / Concept**: Enter a topic and scene count (2 to 10 scenes).
3. **`[✨ Direct Script with AI]`**: Gemini Pro automatically scripts the entire sequence with `Visual:` and `VO:` directions.
4. **Visual Style & Voice**: Set aesthetic, aspect ratio, narration language, and voice actor.
5. **Action**: Click **`[🎬 Build 4K Storyboard Movie]`** to assemble the final documentary with voiceovers, subtitles, and soundtrack.

---

### Tab 3: ⚙️ Settings & System
*Manage API credentials, check local and cloud hardware, and monitor queue history.*

- **Cloud GPU & AI Credentials**: Configure your Gemini Pro API key, Google Colab Worker URL, Pexels/Pixabay stock keys, and Hugging Face ZeroGPU token.
- **Hardware & Backend Status**: Live connection indicators for Colab T4, LTX-Video, CogVideoX, and Wan2.1.
- **Job Queue & Recovery**: One-click recovery of interrupted renders and real-time execution logs.

---

## 🛡️ Crash-Resilience & Auto-Resume Guide

### What happens if your laptop turns off, sleeps, or the browser closes?
1. **Atomic Checkpointing**:
   - Each individual video chunk (`chunk_00.mp4`, `chunk_01.mp4`), storyboard scene (`scene_01.mp4`), voiceover clip, and subtitle track is saved to disk as soon as it completes.
2. **Auto-Recovery on Startup**:
   - When you start VideoStudio Pro again, it automatically scans for any interrupted jobs.
   - It **resumes immediately from the exact scene or stage where it stopped**, reusing already-rendered clips without repeating them!
3. **Explicit User Termination**:
   - Jobs will **never disappear or terminate on their own**.
   - If a cloud GPU rate limit or network glitch occurs, the system automatically pauses and retries with exponential backoff.
   - A job will only stop when it reaches `Stage.DONE` (100% complete) or when you explicitly click **`[🛑 Cancel / Stop Job]`**.

---

## 💎 Gemini AI Pro & Zero-Billing Architecture

| Feature | How It Operates | Billing / Token Cost |
| :--- | :--- | :--- |
| **Gemini Pro Director** | Uses `gemini-2.5-flash` / `gemini-1.5-flash` via your connected Pro Plan | **$0.00 / Zero API Tokens** |
| **Nano Banana 4K Frames** | Generates photorealistic scenes via Pollinations Flux engine | **$0.00 / Unlimited Free** |
| **LTX / Wan2.1 Diffusion** | Hugging Face ZeroGPU + Google Colab T4 Worker | **$0.00 / Zero Cost** |
| **Multilingual Voiceover** | Microsoft Edge Neural TTS | **$0.00 / Unlimited Free** |
| **Whisper Karaoke Subtitles** | Local word-boundary subtitle engine | **$0.00 / Zero Cost** |

---

## 📁 Project Files & Output Folders

```
VideoStudio/
├── app.py                     # Modern Glassmorphic Web App (Gradio UI)
├── video_studio.py            # Master Consolidated Engine & Crash Recovery Queue
├── colab_worker.ipynb         # Google Colab T4 GPU Worker notebook
├── requirements.txt           # Python library dependencies
├── Start Video Studio.bat     # Windows batch launcher
├── run_videostudio.vbs        # Silent background VBS launcher
├── videostudio.ico            # 256x256 multi-resolution icon
├── README.md                  # This complete user manual
├── assets/                    # Watermarks, logos, and local test media
└── outputs/
    ├── videos/                # Master rendered .mp4 video files
    ├── jobs/<job_id>/         # Saved checkpoints, scene clips, VO files, subtitles (.srt)
    ├── queue/jobs.json        # Persistent crash-resilient queue database
    └── logs/studio.log        # System execution logs
```

---

## ❓ Troubleshooting & Frequently Asked Questions

#### Q: How do I find my finished videos?
> **Answer**: Go to Tab 4 (**Live Queue & Crash Recovery**) and click **`[📂 Open Videos Folder]`**, or navigate to `VideoStudio/outputs/videos/` in Windows Explorer.

#### Q: Can I run VideoStudio without an internet connection?
> **Answer**: Yes! Standalone TTS audio, synthetic ambient music pads, Ken Burns image animations, 60fps interpolation, 4K Lanczos upscaling, and test pattern rendering run 100% offline. Online access is only needed for cloud GPU diffusion and Pexels stock searches.

#### Q: How do I stop a running job?
> **Answer**: Click the red **`[🛑 Cancel / Stop Job]`** button in Tab 1 or Tab 2, or paste the Job ID into Tab 4 and click **`[🛑 Cancel Selected Job]`**.

---

### 👑 Credits
**Engineered with Precision by Kamran Ashraf (Kami)**
*VideoStudio Pro · Unified 4K AI Video & Storyboard Studio*
