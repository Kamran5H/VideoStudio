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
Each render creates a separate folder in `outputs/editor/`, preserving source files. Preview output is capped at 720 pixels on the long edge; full edits are capped at 1920. Multi-clip assembly currently caps the long edge at 1280. Up to 20 clips and 20 minutes are accepted. Keep original downloads for higher-resolution workflows. These editor files are not currently included in the main job-retention cleanup; remove unwanted editor folders when finished.

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
