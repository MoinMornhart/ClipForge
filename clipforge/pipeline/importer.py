"""Import: Twitch-VOD per yt-dlp herunterladen oder lokale Datei übernehmen."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from clipforge.core.errors import Cancelled, ClipForgeError
from clipforge.core.log import get_logger

log = get_logger("import")

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".flv", ".ts"}
_URL = re.compile(r"^https?://", re.I)


@dataclass
class Source:
    path: Path
    title: str
    streamer: str
    date: str  # JJJJ-MM-TT
    url: str = ""
    vod_id: str = ""


def is_url(s: str) -> bool:
    return bool(_URL.match(s.strip()))


def from_local(path: str | Path) -> Source:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ClipForgeError(f"Die Datei „{p}“ existiert nicht.", "Prüfe den Pfad oder ziehe die Datei erneut ins Fenster.")
    if p.suffix.lower() not in VIDEO_EXTS:
        raise ClipForgeError(
            f"„{p.suffix}“ wird nicht unterstützt.", "Unterstützt werden MP4, MKV, MOV, WEBM, FLV und TS."
        )
    import datetime as dt

    date = dt.date.fromtimestamp(p.stat().st_mtime).isoformat()
    return Source(path=p, title=p.stem, streamer="", date=date)


def download(
    url: str,
    target_dir: str | Path,
    on_progress: Callable[[float, str], None] | None = None,
    cancel: threading.Event | None = None,
    max_height: int = 1080,
) -> Source:
    """Lädt ein VOD herunter. Bereits vorhandene Dateien werden wiederverwendet,
    abgebrochene Downloads setzt yt-dlp fort (.part)."""
    try:
        import yt_dlp
    except ImportError as e:
        raise ClipForgeError("yt-dlp ist nicht installiert.", "Führe install.bat erneut aus.") from e

    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    def hook(d: dict) -> None:
        if cancel and cancel.is_set():
            raise Cancelled()
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes", 0)
            speed = d.get("speed") or 0
            msg = f"{done / 1e9:.2f} GB" + (f" von {total / 1e9:.2f} GB" if total else "")
            if speed:
                msg += f" · {speed / 1e6:.1f} MB/s"
            on_progress(done / total if total else 0.0, msg)

    opts = {
        "format": f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best",
        "outtmpl": str(target / "%(uploader)s_%(upload_date)s_%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "continuedl": True,
        "noprogress": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "concurrent_fragment_downloads": 8,
        "retries": 10,
        "fragment_retries": 10,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
    except Cancelled:
        raise
    except Exception as e:  # yt-dlp wirft viele eigene Typen
        msg = str(e)
        if "Cancelled" in type(e).__name__ or (cancel and cancel.is_set()):
            raise Cancelled() from e
        hint = "Prüfe den Link und deine Internetverbindung."
        if "subscriber" in msg.lower() or "sub-only" in msg.lower():
            hint = "Das VOD ist nur für Abonnenten. Lade es manuell herunter und importiere die Datei."
        elif "404" in msg or "does not exist" in msg.lower():
            hint = "Das VOD existiert nicht (mehr). Twitch löscht VODs je nach Konto nach 7–60 Tagen."
        raise ClipForgeError("Das VOD konnte nicht heruntergeladen werden.", hint, msg) from e

    if not path.exists():
        cands = sorted(target.glob(f"*{info.get('id', '')}*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
        cands = [c for c in cands if c.suffix.lower() in VIDEO_EXTS]
        if not cands:
            raise ClipForgeError("Der Download ist fertig, aber die Datei fehlt.", "Details stehen in der Logdatei.")
        path = cands[0]

    d = info.get("upload_date") or ""
    date = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else ""
    log.info("VOD geladen: %s", path)
    return Source(path=path, title=info.get("title") or path.stem, streamer=info.get("uploader") or "",
                  date=date, url=url, vod_id=str(info.get("id") or ""))
