# VideoStudio improvement and setup guide

## Current implementation
VideoStudio supports Quick clip and narrated Story video, stock media, hosted AI generation, voice recording, Urdu standard narration, Hindi cloning, captions, checkpoint recovery and export reports. Existing backends and features are retained.

## Changes in this pass
- Smaller captions, restrained outline/shadow and horizontal safe margins reduce obstruction.
- A saved voice becomes the default selection when the interface is rebuilt.
- Script-writing instructions request Urdu script and natural Pakistani Urdu vocabulary rather than Sanskritized Hindi.

## Using the studio
1. Run `python app.py`.
2. For the no-API workflow, select Google Flow & live editor. Existing cloud backends still have optional credentials in Settings.
3. Save a clean 10–20 second voice recording in My voice.
4. Select My voice in the video options. Restart/reload the application after saving to refresh default controls.
5. Draft a script, review its wording, choose aspect ratio and render.
6. Download the video, SRT captions and render report.

## Voice limitations and remaining work
The current Chatterbox integration supports Hindi but not Urdu. Urdu currently uses standard Urdu narration. Selecting Hindi does not provide an Urdu translation or guarantee Urdu vocabulary. Exact 100% reproduction cannot be promised. The saved-voice default fixes selection only; live cloning reliability still needs investigation and testing. Video jobs now stop with a clear error when My voice cannot be cloned. They do not automatically switch the selected speaker. The low-level audio helper retains optional fallback for compatibility. Select a standard voice explicitly if desired.
Reference: https://github.com/resemble-ai/chatterbox

## Subtitle limitations and remaining work
The changes reduce caption size and improve margins. Captions still use a fixed lower-screen position. Scene-aware placement in empty space, face avoidance, per-shot positioning and Urdu font rendering require further implementation and visual testing.

## Google Flow and local live editing — no API keys

The Google Flow browser session was verified as signed in with a PRO badge, and an editing project was opened. This is a browser session, not a permanent account connection in the Python application. No API credentials or new billing were configured.

1. Open **Google Flow & live editor** in VideoStudio.
2. Choose **Open Google Flow** or **Open Gemini** and use the Google account with your existing plan. If the new browser tab is not signed in, sign in there.
3. Generate or edit scenes in Google's interface using the allowance included in your plan. Do not buy additional credits if you want to avoid extra charges.
4. Download the finished clips and upload a single clip under **Source video**, or select several under **Assemble several clips**.
5. The clip list is numbered from 1. Enter an optional order such as `2,1,3`, then choose **Assemble clips**. Clips are fitted to the first clip's frame shape, keeping their original image proportions. Missing audio is filled with silence.
6. Set trim start/end. An end value of 0 uses the end of the source.
7. Choose Original, Wide, Tall or Square framing. The editor fits the full image with padding rather than cropping subjects out.
8. Keep or mute source audio. Add a short caption, or upload a UTF-8 SRT for timed subtitles. SRT timestamps refer to the untrimmed source; trimming shifts them automatically.
9. Choose Auto, Top or Bottom caption placement and adjust caption size. Urdu script is accepted. Auto estimates the less cluttered band from three sampled frames; it is not face recognition or per-person tracking.
10. A preview refreshes after control changes (or leaving the caption field). It shows up to eight seconds and uses a lower resolution for speed. Choose **Export full selection** for the complete edit and download the MP4.

The local editor makes no remote generation requests. Clip transfer currently uses download/import; automatic monitoring of Google downloads, unattended browser generation and two-way synchronization have not been implemented. Google remains responsible for its account limits. The editor does not create unlimited subscription access.

### Local editing files and limits
Each render creates a separate folder in `outputs/editor/`, preserving source files. Preview output is capped at 720 pixels on the long edge; full edits are capped at 1920. Multi-clip assembly currently caps the long edge at 1280. Up to 20 clips and 20 minutes are accepted. Keep original downloads for higher-resolution workflows. Editor folders older than seven days are removed automatically.

### Source layout
- `app.py`: browser links, import controls, assembly ordering and live preview events.
- `local_editor.py`: input validation, assembly, silence handling, trims, aspect fitting, caption layout and export.
- `tests/test_local_editor.py`: actual FFmpeg regression checks for assembly, Urdu caption rendering, trim alignment and validation.

## Google API background (not required for this workflow)
Google API access has separate project billing requirements. A consumer AI Pro subscription should not be assumed to supply paid API access.
The current official documentation says the Veo 3.1 preview Gemini API endpoints were retired on October 22, 2026; it directs users to Gemini Omni Flash or Veo GA on Google's enterprise platform. No retired endpoint has been added to this application. Google video integration is still pending; no paid generation has been performed.
References:
- https://ai.google.dev/gemini-api/docs/video
- https://ai.google.dev/gemini-api/docs/billing

## Media quality
Pexels and Pixabay remain available. Existing AI image generation remains in place. Google-generated clips can now be imported and edited locally; automatic generation inside the Python pipeline remains separate work. Generated visuals require review for subject identity, geometry, continuity and relevance.

## Verification
Local editor regression tests exercise real FFmpeg assembly, sound handling, Urdu caption rendering, caption trim offsets and invalid trims. Live voice cloning, Urdu lexical quality and Google video generation have not been validated end to end. Auto caption placement is a visual-clutter heuristic with manual override.

## Remaining delivery checklist
- Implement and visually validate adaptive subtitle placement.
- Diagnose the live cloning request and make voice fallback controllable.
- Add a tested Urdu vocabulary/transliteration workflow without mislabeling Hindi as Urdu.
- Improve the browser-to-local handoff without requiring API keys or adding billing.
- Run end-to-end narration and multi-scene generation checks.

## Engaging voiceover writing and delivery

Generated scripts now share a spoken-story brief: one specific hook, a conversational explanation, a concrete example, occasional viewer involvement and a resolved payoff. Each scene targets 12–30 spoken words. Generic introductions, exaggerated claims, repetitive questions and forced subscription requests are discouraged. Urdu direction uses Urdu script and everyday Pakistani vocabulary. This is drafting guidance, not a factual or linguistic guarantee.

The video options include Natural, Engaging, Calm and Energetic narrator delivery. Standard voices receive rate adjustments of 0%, +4%, -6% and +10%. Pitch remains unchanged for a natural timbre. Cloning retains the model's delivery; a rate control is not a guarantee of emotion or speaker similarity. User-supplied narration is spoken as written.

For a workflow without API keys, open the voiceover planning accordion in Google Flow & live editor. Enter the topic and verified facts, choose Urdu or another language and scene count, then prepare the request. Copy it into the signed-in Gemini app and review its response. This does not silently call a paid API.

For the exact sound of your recorded voice, record or upload the complete narration in the live editor. This is a recording, not generated cloning. It replaces source sound, begins at the selected video's start, and is padded with silence if shorter. If longer, extend the selection or shorten the recording. Source mute does not mute this separately selected narration. SRT captions are provided separately and are not automatically transcribed from the recording.

Automatic Flow transfer, production-grade Urdu voice cloning and face-aware subtitle tracking remain unfinished. Existing browser controls and the import workflow do not constitute a permanent account API connection.

## Beginner interface

The app opens on Start with three choices: Turn an idea into a story, Edit a video I have, and Use my Google plan. The bar at the top is the navigation: Start, Edit a video, Make a story, AI clip, My videos, My voice and Settings. The selected screen is highlighted.

The editor follows Add your video → Make it yours → Preview & download. Add one file, optionally write a caption and choose a shape. Trim, voice/sound, subtitle appearance and joining several clips are collapsed optional sections. Update preview and Make my finished video are enabled after a video is added. The finished file appears under the buttons once it is made.

The story screen shows the topic, shape and make button. The script editor and scene count are inside Review or write my script. Quality, voice, music and delivery remain inside More options.

### What each button does
- **Make my video** queues the job and returns immediately. The page timer follows the job, so a refresh never loses it and several videos can be queued.
- **Stop this video** is shown only while a job is queued or rendering. **Try again** is shown when the newest job failed or was stopped; it resumes from the saved checkpoint.
- A failed or stopped job keeps its card and reason on screen until a new video is made.
- **My voice** is checked when Make my video is pressed. If there is no saved sample, no token, or the language is unsupported, the message says so and nothing is queued.
- **My videos** plays any finished video, offers its captions and render report, deletes a video with its files, and opens the videos folder. If the folder cannot be opened, its path is shown.
- **Save settings** empties the key boxes and marks saved keys. A Colab worker URL that does not start with http(s):// is rejected.
- Empty **Improve my idea** and **Write the script for me** presses show a message.
- Editor previews and exports older than seven days are removed from `outputs/editor/`.

### Fixes found by rendering real videos
- The script writer was told about Urdu in every request, so an English story could come back in Urdu. That line is now sent only for Urdu, and a reply in the wrong writing system is rejected and retried.
- Urdu text read by the English voice produced about 0.1 s of speech, broken caption timings, and a job marked Completed. Language is now on the main form, a mismatch is reported before queueing, and the pipeline fails with a reason if narration is far too short for its text.
- The captions and render report list under the player never appeared, because it was looked up by the browser's cached copy of the video. It now comes from the job.
- Improve my idea waited 6 s for a service that needs 10 to 20 s, then pasted the offline style template into the box. It now waits up to 30 s and leaves the text unchanged, with a message, if no AI answers.
- A failed video's card shows the reason in plain view.
- Tall AI pictures were often two pictures stacked. They are now requested at 3:4 and cropped.

### Colab GPU worker and API keys
- Installing Wan's requirements on Colab downgraded NumPy to 1.x (`numpy<2` pin), which breaks Colab's preinstalled packages until a restart, and tried to compile `flash_attn`. Those pins, and torch/torchvision/gradio, are now skipped.
- On a T4 with standard RAM, loading the 11 GB text encoder on the CPU crashed the session. Checkpoints are now memory-mapped, and the first cell stops with clear steps (turn on High-RAM, or pick L4/A100) instead of crashing.
- Colab Pro GPUs are used properly: on L4/A100 the text encoder runs on the GPU; A100 keeps all models loaded. T4 renders are capped at 25 steps, the studio waits up to 45 minutes, and the 1.3B model's recommended `sample_shift` of 8 is used.
- Pixabay rejected any query over 100 characters (HTTP 400), and long cinematic prompts found nothing on Pexels. Searches now use a short keyword query, then its first three words.
- Pexels and Pixabay ranked 4K files first, so a 1080p video downloaded 100+ MB files that often timed out. The file nearest the output size is now chosen. Pixabay photos are searched too.
- A rejected key, an exhausted limit or a missing Gemini model was logged only at debug level. Each now shows once in the technical log. Pasted keys lose stray spaces and quotes.
- Wikimedia requests now send a descriptive User-Agent, search only files and download scaled copies instead of full-size originals.

### Fixes from a real error log
- **`colab: no response within 1800s`**: the worker was busy, not dead. A T4 chunk takes 20–30 minutes, and every retry or restart sent the same chunk again, which queued behind the render still running. The studio now follows the worker's progress ("step 7/25" on the card). It waits as long as steps advance or the request is waiting its turn, and gives up only after 45 minutes with no progress or 3 hours in total. The worker returns a finished clip at once when the same chunk is sent again.
- **All free Hugging Face backends: `exceeded your ZeroGPU runs limit`**: this is the daily free quota, but the studio retried every 1–5 minutes. Retries now back off: 2, 4, 8, 16, 32 minutes, then every 45 minutes.
- **`Your saved voice could not be cloned: today's free GPU time is used up`** failed a whole story. If no scene has used the cloned voice yet, the video now uses the standard voice and the result card says why. If some scenes already used it, the job waits for quota and keeps the finished scenes, so one video never mixes two voices. Each scene's checkpoint records its voice, so this holds after a restart too.
