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
    platform: str = "Datei"  # Twitch | Kick | YouTube | Web | Datei


PLATFORMS = {
    "twitch.tv": "Twitch",
    "kick.com": "Kick",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
}


def is_url(s: str) -> bool:
    return bool(_URL.match(s.strip()))


def platform_of(url: str) -> str:
    host = re.sub(r"^https?://(www\.|m\.)?", "", url.strip().lower()).split("/")[0]
    for domain, name in PLATFORMS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return "Web"


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
    section: tuple[float, float] | None = None,
) -> Source:
    """Lädt ein VOD von Twitch, Kick, YouTube (oder jeder anderen von yt-dlp unterstützten Seite).

    Bereits vorhandene Dateien werden wiederverwendet, abgebrochene Downloads setzt yt-dlp fort.
    section=(Start, Ende) in Sekunden lädt nur einen Ausschnitt.
    """
    platform = platform_of(url)
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
    if section:
        from yt_dlp.utils import download_range_func

        a, b = section
        opts["download_ranges"] = download_range_func(None, [(a, b)])
        opts["outtmpl"] = str(target / f"%(uploader)s_%(upload_date)s_%(id)s_{int(a)}-{int(b)}.%(ext)s")
    if platform == "Kick":
        try:
            import curl_cffi  # noqa: F401  Kick steht hinter Cloudflare, yt-dlp braucht dafür curl_cffi
        except ImportError as e:
            raise ClipForgeError("Für Kick-Links fehlt ein Zusatzpaket.", "Führe install.bat erneut aus.") from e
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
        low = msg.lower()
        hint = "Prüfe den Link und deine Internetverbindung."
        if "subscriber" in low or "sub-only" in low:
            hint = "Das VOD ist nur für Abonnenten. Lade es manuell herunter und importiere die Datei."
        elif "404" in msg or "does not exist" in low or "not found" in low:
            hint = {"Twitch": "Das VOD existiert nicht (mehr). Twitch löscht VODs je nach Konto nach 7–60 Tagen.",
                    "Kick": "Das VOD existiert nicht (mehr) oder der Link ist unvollständig "
                            "(Format: kick.com/<kanal>/videos/<id>)."}.get(platform, hint)
        elif "403" in msg or "cloudflare" in low or "impersonat" in low:
            hint = f"{platform} blockiert den Download gerade. Aktualisiere mit update.bat und versuche es erneut."
        elif "unsupported url" in low:
            hint = "Diese Seite wird nicht unterstützt. Unterstützt: Twitch, Kick, YouTube und viele weitere."
        raise ClipForgeError(f"Das VOD von {platform} konnte nicht heruntergeladen werden.", hint, msg) from e

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
                  date=date, url=url, vod_id=str(info.get("id") or ""), platform=platform)
