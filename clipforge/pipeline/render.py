"""9:16-Export (1080×1920) über FFmpeg, mit NVENC wenn verfügbar.

Ein Export besteht aus: Layout (inkl. Facecam, Gameplay-Ausschnitt, Nachführung), Untertiteln,
Wasserzeichen, Lautheit (-14 LUFS) und optional Intro/Outro.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from clipforge.core import ffmpeg
from clipforge.core.errors import ClipForgeError
from clipforge.core.log import get_logger
from clipforge.core.settings import Settings
from clipforge.pipeline import facecam as fc
from clipforge.pipeline import layouts
from clipforge.pipeline.clips import Clip
from clipforge.pipeline.subtitles import SubtitleStyle, build_ass, clip_words, prepare_fonts
from clipforge.pipeline.transcribe import Word

log = get_logger("render")

W, H = layouts.W, layouts.H
LAYOUTS = layouts.LAYOUTS


@dataclass
class RenderJob:
    src: Path
    clip: Clip
    out: Path
    layout: str = "auto"
    fps: int = 30
    codec: str = "h264"
    nvenc: bool = True
    facecam: fc.Facecam | None = None
    words: list[Word] = field(default_factory=list)  # Wörter des ganzen VODs (für Untertitel)
    subtitle_style: SubtitleStyle | None = None
    loudnorm: bool = True
    trim_silence: bool = True
    watermark: str = ""  # Pfad zu PNG
    watermark_pos: str = "oben-rechts"
    watermark_opacity: float = 0.8
    intro: str = ""
    outro: str = ""
    track_face: bool = True
    smart_crop: bool = True


def encoder_args(codec: str, nvenc: bool) -> list[str]:
    if nvenc:
        enc = "hevc_nvenc" if codec == "hevc" else "h264_nvenc"
        # p6 + Lookahead + räumliche AQ: sichtbar sauberer bei schnellen Spielszenen; Plattformen kodieren
        # ohnehin neu, je besser das Ausgangsmaterial, desto weniger Matsch danach
        args = ["-c:v", enc, "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", "18", "-b:v", "0",
                "-maxrate", "24M", "-bufsize", "48M", "-spatial-aq", "1", "-rc-lookahead", "32", "-bf", "3"]
    else:
        enc = "libx265" if codec == "hevc" else "libx264"
        args = ["-c:v", enc, "-preset", "slow", "-crf", "18"]
    if codec == "hevc":
        args += ["-tag:v", "hvc1"]
    return args


def layout_filter(layout: str, fps: int, src_w: int = 1920, src_h: int = 1080) -> str:
    """Einfache Variante ohne Facecam (für Tests und Vorschau)."""
    return layouts.build(layouts.resolve_layout(layout, False, False), layouts.LayoutInput(src_w, src_h, fps))


def speech_bounds(words: list[Word], start: float, end: float, pad_in: float = 0.15,
                  pad_out: float = 0.5) -> tuple[float, float]:
    """Stille am Anfang und Ende abschneiden: Clip beginnt kurz vor dem ersten und endet kurz
    nach dem letzten Wort."""
    inside = [w for w in words if start <= w.s < end]
    if not inside:
        return start, end
    return max(start, inside[0].s - pad_in), min(end, inside[-1].e + pad_out)


def plan(job: RenderJob, media: dict, work: Path) -> tuple[str, str]:
    """Liefert (aufgelöstes Layout, Filtergraph bis [vl])."""
    sw, sh = media["width"], media["height"]
    cam_info = job.facecam
    has_cam = bool(cam_info and cam_info.mode == "overlay" and cam_info.cam)
    fullcam = bool(cam_info and cam_info.mode == "fullcam")
    has_hand = bool(cam_info and cam_info.handcam)
    layout = layouts.resolve_layout(job.layout, has_cam, fullcam, has_hand)
    li = layouts.LayoutInput(sw, sh, job.fps, fullcam=fullcam and not has_cam)
    clip = job.clip
    if has_cam:
        li.cam = fc.Rect.from_norm(cam_info.cam, sw, sh)
    if cam_info and cam_info.face:
        li.face = fc.Rect.from_norm(cam_info.face, sw, sh)
    if has_hand:
        li.hand = fc.Rect.from_norm(cam_info.handcam, sw, sh)
    # Kamerabereich oben so hoch, dass die ganze Cam zu sehen ist (statt eines festen, breiten Streifens)
    if layout == "stack" and li.cam:
        li.stack_top = layouts.panel_height(li.cam)
    elif layout == "hand" and li.hand:
        li.stack_top = layouts.panel_height(li.hand)
    content = cam_info.content if cam_info else None
    if job.smart_crop and layout in ("crop", "stack", "corner", "blur", "hand", "face_hand"):
        try:
            content = fc.clip_letterbox(job.src, clip.start, clip.end) or content
        except Exception as e:
            log.warning("Balkenerkennung fehlgeschlagen: %s", e)
    if content and (content[0] > 0 or content[1] < 1):
        li.content_y = int(content[0] * sh)
        li.content_h = int((content[1] - content[0]) * sh)
    ch = li.content_h or sh

    if layout in ("crop", "stack", "corner", "hand", "face_hand") and job.smart_crop:
        out_h = {"stack": H - li.stack_top, "hand": H - li.stack_top,
                 "face_hand": H - sum(li.face_hand_rows)}.get(layout, H)
        gw = layouts.gameplay_width(ch, W, out_h)
        try:
            li.gameplay_x = fc.smart_crop_x(job.src, clip.start, clip.end, gw, sw, sh,
                                            exclude=[r for r in (li.cam, li.hand) if r is not None])
        except Exception as e:  # Bildanalyse ist Kür: im Zweifel mittig
            log.warning("Gameplay-Ausschnitt nicht bestimmbar, nehme Mitte: %s", e)

    # markiertes Objekt (z. B. Bär): ihm folgen und mitzoomen statt einem Gesicht nachzuführen
    finder = None
    if job.track_face and cam_info and cam_info.subject_file and Path(cam_info.subject_file).exists():
        try:
            finder = fc.SubjectFinder.from_file(cam_info.subject_file)
        except Exception as e:
            log.warning("Markiertes Objekt nicht ladbar: %s", e)

    if layout == "cam" and (has_cam or fullcam):
        region = li.cam if has_cam else fc.Rect(0, 0, sw, sh)
        aspect = W / H
        if has_cam:
            # ohne Objekt: Facecam-Fenster als Ganzes; mit Objekt: im Seitenverhältnis des Fensters mitzoomen
            aspect = region.w / region.h if finder else None
        if aspect and finder:
            ww, wh = layouts.cam_window_size(region, aspect)
            li.cam_window = (ww, wh)
            li.cam_xy = (int(region.x), int(region.y))
            try:
                tr = fc.track_subject(job.src, clip.start, clip.end, finder, region, aspect)
                script = fc.sendcmd_script(tr, "cam")
                if script:
                    (work / "track.txt").write_text(script, encoding="utf-8")
                    li.tracked = True
                    li.cam_window = (layouts._even(tr.ws[0]), layouts._even(tr.hs[0]))
                    li.cam_xy = (int(tr.xs[0]), int(tr.ys[0]))
            except Exception as e:
                log.warning("Objekt-Verfolgung nicht möglich, nutze festen Ausschnitt: %s", e)
        elif aspect:
            ww, wh = layouts.cam_window_size(region, aspect)
            li.cam_window = (ww, wh)
            face = li.face or region
            li.cam_xy = (int(min(max(region.x, face.cx - ww / 2), region.x + region.w - ww)),
                         int(min(max(region.y, face.cy - 0.1 * face.h - wh / 2), region.y + region.h - wh)))
            # Nachführen lohnt nur, wenn das Fenster kleiner als der Bereich ist
            if job.track_face and (ww < region.w - 8 or wh < region.h - 8):
                try:
                    tr = fc.track_window(job.src, clip.start, clip.end, region, ww, wh)
                    script = fc.sendcmd_script(tr, "cam")
                    if script:
                        (work / "track.txt").write_text(script, encoding="utf-8")
                        li.tracked = True
                        li.cam_xy = (int(tr.xs[0]), int(tr.ys[0]))
                except Exception as e:
                    log.warning("Nachführen nicht möglich, nutze festen Ausschnitt: %s", e)
    graph = layouts.build(layout, li)
    return layout, graph.replace("[v]", "[vl]")


def preview_frame(src: Path, at: float, layout: str, cam: fc.Facecam | None, media: dict, out_png: Path,
                  ffmpeg_path: str = "") -> Path:
    """Ein einzelnes 9:16-Bild mit dem gewählten Layout – für die Live-Vorschau im Facecam-Dialog.
    Ohne Nachführung und ohne Bildanalyse, damit es in unter einer Sekunde fertig ist."""
    work = Path(tempfile.mkdtemp(prefix="cf_preview_"))
    try:
        job = RenderJob(src, Clip(at, at + 1.0, at, []), out_png, layout, facecam=cam, smart_crop=False,
                        track_face=False)
        _, graph = plan(job, media, work)
        graph = graph.replace("[vl]", "[v]")
        ffmpeg.run(["-ss", f"{at:.3f}", "-i", str(src), "-filter_complex", graph, "-map", "[v]", "-frames:v", "1",
                    "-update", "1", str(out_png.resolve())], configured=ffmpeg_path, cwd=work,
                   error_message="Die Vorschau konnte nicht erstellt werden.")
        return out_png
    finally:
        shutil.rmtree(work, ignore_errors=True)


_WM_POS = {
    "oben-rechts": "W-w-40:60", "oben-links": "40:60", "unten-rechts": "W-w-40:H-h-220",
    "unten-links": "40:H-h-220", "mitte": "(W-w)/2:(H-h)/2",
}


def render(job: RenderJob, on_progress: Callable[[float], None] | None = None,
           cancel: threading.Event | None = None, ffmpeg_path: str = "", media: dict | None = None) -> Path:
    job.out.parent.mkdir(parents=True, exist_ok=True)
    media = media or ffmpeg.probe(job.src, ffmpeg_path)
    nvenc = job.nvenc and ffmpeg.nvenc_available(ffmpeg_path)
    clip = job.clip
    if clip.edited_words:  # im Editor korrigiertes Transkript hat Vorrang
        job.words = [Word(w, s, e) for w, s, e in clip.edited_words]
    start, end = clip.start, clip.end
    if job.trim_silence and job.words:
        start, end = speech_bounds(job.words, start, end)
    trimmed = Clip(**{**clip.to_dict(), "start": start, "end": end})
    dur = end - start
    work = Path(tempfile.mkdtemp(prefix="cf_render_"))
    try:
        layout, graph = plan(RenderJob(**{**job.__dict__, "clip": trimmed}), media, work)
        chain = [graph]
        last = "vl"
        style = job.subtitle_style
        if style and style.enabled and job.words:
            words = clip_words(job.words, start, end)
            if words:
                (work / "subs.ass").write_text(build_ass(words, style, dur), encoding="utf-8")
                prepare_fonts(style, work)
                chain.append(f"[{last}]subtitles=subs.ass:fontsdir=fonts[vs]")
                last = "vs"
        inputs = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(job.src)]
        if job.watermark and Path(job.watermark).exists():
            inputs += ["-loop", "1", "-i", str(job.watermark)]
            pos = _WM_POS.get(job.watermark_pos, _WM_POS["oben-rechts"])
            chain.append(f"[1:v]format=rgba,scale={int(W * 0.28)}:-1,colorchannelmixer=aa={job.watermark_opacity:.2f}[wm];"
                         f"[{last}][wm]overlay={pos}:shortest=1[vw]")
            last = "vw"
        chain.append(f"[{last}]null[v]")
        afilter = ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"] if job.loudnorm else []
        # im Arbeitsordner rendern, erst fertig in den Exportordner: Abbruch hinterlässt keine halbe Datei
        final = work / "final.mp4"
        main_out = final if not (job.intro or job.outro) else work / "main.mp4"
        args = [*inputs, "-filter_complex", ";".join(chain), "-map", "[v]", "-map", "0:a?", *afilter,
                *encoder_args(job.codec, nvenc), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-movflags", "+faststart", str(main_out.resolve())]
        share = 0.85 if main_out != final else 1.0
        ffmpeg.run(args, duration=dur, on_progress=(lambda f: on_progress(f * share)) if on_progress else None,
                   cancel=cancel, configured=ffmpeg_path, cwd=work,
                   error_message=f"Clip „{clip.title or clip.id}“ konnte nicht exportiert werden.")
        if main_out != final:
            _concat_intro_outro(job, main_out, final, nvenc, ffmpeg_path, cancel)
        shutil.move(str(final), str(job.out))
        log.info("Exportiert: %s (%s, %s)", job.out, layout, "NVENC" if nvenc else "CPU")
        if on_progress:
            on_progress(1.0)
        return job.out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _concat_intro_outro(job: RenderJob, main: Path, out: Path, nvenc: bool, ffmpeg_path: str, cancel) -> None:
    parts = [p for p in (job.intro, str(main), job.outro) if p]
    for p in parts:
        if not Path(p).exists():
            raise ClipForgeError(f"Intro/Outro „{p}“ nicht gefunden.", "Prüfe den Pfad in den Einstellungen.")
    inputs, filters, labels = [], [], []
    total = 0.0
    for i, p in enumerate(parts):
        info = ffmpeg.probe(p, ffmpeg_path)
        total += info["duration"]
        inputs += ["-i", p]
        filters.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
                       f"setsar=1,fps={job.fps},format=yuv420p[v{i}]")
        if info["has_audio"]:
            filters.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        else:
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{info['duration']:.3f}[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    filters.append(f"{''.join(labels)}concat=n={len(parts)}:v=1:a=1[v][a]")
    ffmpeg.run([*inputs, "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
                *encoder_args(job.codec, nvenc), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                str(out)], duration=total, cancel=cancel, configured=ffmpeg_path,
               error_message="Intro/Outro konnten nicht angefügt werden.")


# --------------------------------------------------------------------------- Dateinamen und Metadaten

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def safe_name(s: str, max_len: int = 80) -> str:
    s = _BAD.sub("", s).strip().replace(" ", "_")
    s = re.sub(r"_+", "_", s).strip("._")
    return s[:max_len] or "clip"


def output_name(clip: Clip, s: Settings, date: str, streamer: str, index: int) -> str:
    title = clip.title or f"Clip_{index:02d}"
    values = {"date": date or "ohne-Datum", "streamer": streamer or "Streamer", "title": title,
              "index": f"{index:02d}", "score": int(clip.score or 0)}
    # deutsche Platzhalter sind naheliegend – mit erlauben
    values.update(datum=values["date"], titel=title, nummer=values["index"])
    try:
        name = s.filename_pattern.format_map(_Keep(values))
    except (ValueError, IndexError, AttributeError, TypeError):  # kaputtes Muster: Standard statt Absturz
        log.warning("Dateinamen-Muster ungültig: %r – nutze Standard", s.filename_pattern)
        name = "{date}_{streamer}_{title}".format_map(values)
    return safe_name(name, 120)


class _Keep(dict):
    """Unbekannte Platzhalter bleiben als Text stehen statt den Export abstürzen zu lassen."""

    def __missing__(self, key):
        return key


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
    if clip.ai_reason:
        lines.append(f"KI: {clip.ai_reason}")
    txt.write_text("\n".join(lines), encoding="utf-8")
    return txt
