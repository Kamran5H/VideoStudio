"""Local editing for clips downloaded from Google Flow; no remote requests."""
from __future__ import annotations

import math
import re
import uuid
from pathlib import Path

from video_studio import (
    DEFAULT_OUTPUT_DIR,
    VIDEO_ENCODE_ARGS,
    _ffmpeg_info,
    ffprobe_duration,
    output_size,
    probe_video_size,
    run_ffmpeg,
)

EDIT_DIR = DEFAULT_OUTPUT_DIR / "editor"


def work_dir() -> Path:
    folder = EDIT_DIR / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def checked_video(path: str) -> tuple[Path, float, tuple[int, int]]:
    source = Path(path).resolve()
    if not source.is_file():
        raise ValueError("Choose a video file first.")
    duration, size = ffprobe_duration(source), probe_video_size(source)
    if not size or not math.isfinite(duration) or duration <= 0:
        raise ValueError("This file has no readable video stream.")
    if duration > 1200:
        raise ValueError("Please edit videos shorter than 20 minutes.")
    return source, duration, size


def join_clips(paths: list[str]) -> str:
    if not paths or len(paths) > 20:
        raise ValueError("Select between 1 and 20 clips in playback order.")
    clips = [checked_video(p) for p in paths]
    if sum(c[1] for c in clips) > 1200:
        raise ValueError("The combined video must be shorter than 20 minutes.")
    if len(clips) == 1:
        return str(clips[0][0])
    folder = work_dir()
    w, h = clips[0][2]
    factor = min(1, 1280 / max(w, h))
    w, h = max(2, int(w * factor) // 2 * 2), max(2, int(h * factor) // 2 * 2)
    inputs, filters, labels = [], [], []
    for i, (source, duration, _) in enumerate(clips):
        inputs += ["-i", str(source)]
        filters.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,setpts=PTS-STARTPTS[v{i}]")
        if re.search(r"Stream #\d+:\d+.*Audio:", _ffmpeg_info(source)):
            filters.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[a{i}]")
        else:
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[a{i}]")
        labels += [f"[v{i}][a{i}]"]
    filters.append("".join(labels) + f"concat=n={len(clips)}:v=1:a=1[v][a]")
    out = folder / "assembled.mp4"
    run_ffmpeg([*inputs, "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
                "-r", "30", *VIDEO_ENCODE_ARGS, "-c:a", "aac", str(out)])
    return str(out)


def quiet_position(source: Path, start: float, duration: float, folder: Path) -> str:
    """Estimate a less cluttered band. This is not face or subject detection."""
    from PIL import Image, ImageFilter, ImageStat
    scores = {"Top": 0.0, "Bottom": 0.0}
    for i, fraction in enumerate((0.15, 0.5, 0.85)):
        frame = folder / f"sample_{i}.png"
        run_ffmpeg(["-ss", str(start + duration * fraction), "-i", str(source), "-frames:v", "1",
                    "-vf", "scale=320:-2", str(frame)], timeout=60)
        with Image.open(frame) as image:
            gray = image.convert("L")
            w, h = gray.size
            for label, y0, y1 in (("Top", .06, .25), ("Bottom", .75, .94)):
                band = gray.crop((int(w * .08), int(h * y0), int(w * .92), int(h * y1)))
                scores[label] += ImageStat.Stat(band.filter(ImageFilter.FIND_EDGES)).mean[0]
    return min(scores, key=lambda label: scores[label])


def caption_cues(text: str, srt_path: str | None, start: float, duration: float) -> list[tuple[float, float, str]]:
    if not srt_path:
        return [(0, duration, text.strip())] if text.strip() else []
    content = Path(srt_path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    if len(content) > 500_000:
        raise ValueError("Caption file is too large.")
    pattern = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*\n(.*?)(?=\n\s*\n|\Z)", re.S)
    cues = []
    matches = list(pattern.finditer(content.strip()))
    if not matches:
        raise ValueError("No readable timed captions were found in the SRT file.")
    for match in matches:
        values = [int(v) for v in match.groups()[:8]]
        a = values[0]*3600 + values[1]*60 + values[2] + values[3]/1000 - start
        b = values[4]*3600 + values[5]*60 + values[6] + values[7]/1000 - start
        if b > 0 and a < duration and b > a:
            cues.append((max(0, a), min(duration, b), match.group(9).strip()))
    return cues


def ass_time(seconds: float) -> str:
    ticks = round(seconds * 100)
    return f"{ticks // 360000}:{ticks // 6000 % 60:02d}:{ticks // 100 % 60:02d}.{ticks % 100:02d}"


def render_edit(path: str, start: float, end: float, shape: str, muted: bool,
                caption: str, srt_path: str | None, position: str, size: float,
                preview: bool = True, narration_path: str | None = None) -> tuple[str, str]:
    source, total, (sw, sh) = checked_video(path)
    start, end, size = float(start), float(end), float(size)
    if not all(math.isfinite(v) for v in (start, end, size)):
        raise ValueError("Trim and caption size must be finite numbers.")
    end = total if end == 0 else min(end, total)
    if start < 0 or end - start < .1 or start >= total:
        raise ValueError("End must be after start, with at least 0.1 seconds selected.")
    if shape not in ("Original", "16:9", "9:16", "1:1"):
        raise ValueError("Choose a supported frame shape.")
    if position not in ("Auto: less clutter", "Top", "Bottom"):
        raise ValueError("Choose a supported caption position.")
    if len(caption) > 1000:
        raise ValueError("Use an SRT file for long narration; the caption field is limited to 1,000 characters.")
    folder = work_dir()
    duration = min(end - start, 8) if preview else end - start
    w, h = (sw, sh) if shape == "Original" else output_size(shape, min(sw, sh))
    factor = min(1, (720 if preview else 1920) / max(w, h))
    w, h = max(2, int(w * factor) // 2 * 2), max(2, int(h * factor) // 2 * 2)
    vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    cues = caption_cues(caption, srt_path, start, duration)
    chosen = position
    if cues:
        if position.startswith("Auto"):
            chosen = quiet_position(source, start, end - start, folder)
        font_size = max(12, round(min(w * .052, h * .042) * max(.7, min(size, 1.5))))
        margin = max(12, round(h * .07))
        ass = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\n"
               "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
               f"Style: Caption,Segoe UI,{font_size},&H00FFFFFF,&H70000000,&H70000000,0,3,2,0,{8 if chosen == 'Top' else 2},{int(w*.08)},{int(w*.08)},{margin}\n"
               "[Events]\nFormat: Layer, Start, End, Style, Text\n")
        for a, b, words in cues:
            safe = words.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")
            ass += f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Caption,{safe}\n"
        (folder / "captions.ass").write_text(ass, encoding="utf-8")
        vf += ",ass=captions.ass"
    out = folder / ("preview.mp4" if preview else "edited.mp4")
    audio_input = []
    audio = ["-an"] if muted else ["-map", "0:a?", "-c:a", "aac"]
    if narration_path:
        narration = Path(narration_path).resolve()
        length = ffprobe_duration(narration)
        if not narration.is_file() or length <= 0:
            raise ValueError("The narration recording could not be read.")
        if length > end - start + .2:
            raise ValueError("The narration is longer than the selected video. Extend the selection or shorten the recording.")
        audio_input = ["-i", str(narration)]
        audio = ["-map", "1:a:0", "-af", f"apad,atrim=duration={duration:.6f}", "-c:a", "aac"]
    run_ffmpeg(["-ss", str(start), "-i", str(source), *audio_input, "-t", str(duration), "-map", "0:v:0",
                *audio, "-vf", vf, *VIDEO_ENCODE_ARGS, str(out)], cwd=folder)
    return str(out), f"{'Preview' if preview else 'Export'}: {duration:.1f}s · {w} × {h}" + (f" · captions: {chosen.lower()}" if cues else "")
