"""9:16-Export (1080×1920) über FFmpeg, mit NVENC wenn verfügbar."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from clipforge.core import ffmpeg
from clipforge.core.log import get_logger
from clipforge.core.settings import Settings
from clipforge.pipeline.clips import Clip

log = get_logger("render")

W, H = 1080, 1920

LAYOUTS = {
    "blur": "Gameplay mittig, unscharfer Hintergrund",
    "crop": "Mittig zugeschnitten (Vollbild)",
}


@dataclass
class RenderJob:
    src: Path
    clip: Clip
    out: Path
    layout: str
    fps: int = 30
    codec: str = "h264"
    nvenc: bool = True


def layout_filter(layout: str, fps: int) -> str:
    tail = f"fps={fps},format=yuv420p[v]"
    if layout == "crop":
        return f"[0:v]crop='min(iw,ih*9/16)':ih,scale={W}:{H}:flags=lanczos,setsar=1,{tail}"
    # blur (Standard): Hintergrund klein rechnen, weichzeichnen, hochskalieren – spart viel Rechenzeit
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,"
        f"scale={W}:{H},setsar=1[bgb];"
        f"[fg]scale={W}:-2:flags=lanczos,setsar=1[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,{tail}"
    )


def encoder_args(codec: str, nvenc: bool) -> list[str]:
    if nvenc:
        enc = "hevc_nvenc" if codec == "hevc" else "h264_nvenc"
        args = ["-c:v", enc, "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "0",
                "-maxrate", "16M", "-bufsize", "32M"]
    else:
        enc = "libx265" if codec == "hevc" else "libx264"
        args = ["-c:v", enc, "-preset", "medium", "-crf", "20"]
    if codec == "hevc":
        args += ["-tag:v", "hvc1"]
    return args


def render(job: RenderJob, on_progress: Callable[[float], None] | None = None,
           cancel: threading.Event | None = None, ffmpeg_path: str = "") -> Path:
    job.out.parent.mkdir(parents=True, exist_ok=True)
    nvenc = job.nvenc and ffmpeg.nvenc_available(ffmpeg_path)
    dur = job.clip.duration
    args = [
        "-ss", f"{job.clip.start:.3f}", "-t", f"{dur:.3f}", "-i", str(job.src),
        "-filter_complex", layout_filter(job.layout, job.fps), "-map", "[v]", "-map", "0:a?",
        *encoder_args(job.codec, nvenc),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(job.out),
    ]
    ffmpeg.run(args, duration=dur, on_progress=on_progress, cancel=cancel, configured=ffmpeg_path,
               error_message=f"Clip „{job.clip.title or job.clip.id}“ konnte nicht exportiert werden.")
    log.info("Exportiert: %s (%s, %s)", job.out, job.layout, "NVENC" if nvenc else "CPU")
    return job.out


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def safe_name(s: str, max_len: int = 80) -> str:
    s = _BAD.sub("", s).strip().replace(" ", "_")
    s = re.sub(r"_+", "_", s).strip("._")
    return s[:max_len] or "clip"


def output_name(clip: Clip, s: Settings, date: str, streamer: str, index: int) -> str:
    title = clip.title or f"Clip_{index:02d}"
    name = s.filename_pattern.format(date=date or "ohne-Datum", streamer=streamer or "Streamer", title=title,
                                     index=f"{index:02d}", score=int(clip.score or 0))
    return safe_name(name, 120)


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    i = 2
    while (q := p.with_name(f"{p.stem}_{i}{p.suffix}")).exists():
        i += 1
    return q


def write_metadata(clip: Clip, video_path: Path) -> Path:
    txt = video_path.with_suffix(".txt")
    tags = " ".join(t if t.startswith("#") else f"#{t}" for t in clip.hashtags)
    lines = [
        f"Titel: {clip.title}",
        "",
        "Beschreibung:",
        clip.description,
        "",
        f"Hashtags: {tags}",
        "",
        f"Viral-Score: {clip.score if clip.score is not None else '–'}/10",
        f"Grund: {'; '.join(clip.reasons)}",
    ]
    txt.write_text("\n".join(lines), encoding="utf-8")
    return txt
