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
    offset: float = 0.0  # Start der Datei im Original-VOD (bei Ausschnitten, auf Segmentgrenze genau)


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


MAX_FETCH = 64 * 1024 * 1024  # ein HLS-Segment/eine Playlist ist nie größer


def check_public_url(url: str) -> None:
    """Nur http/https zu öffentlichen Adressen – eine präparierte Playlist darf weder lokale Dateien (file://)
    noch Geräte im Heimnetz (192.168.x.x, localhost) ansprechen."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ClipForgeError("Der Stream enthält eine ungültige Adresse.", "Nur http/https ist erlaubt.", url[:200])
    try:
        infos = socket.getaddrinfo(u.hostname, None)
    except OSError as e:
        raise ClipForgeError("Der Videoserver ist nicht erreichbar.", "Prüfe die Internetverbindung.", str(e)) from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ClipForgeError("Der Stream verweist auf eine Adresse im lokalen Netz.",
                                 "Aus Sicherheitsgründen wird das nicht geladen.", f"{u.hostname} → {ip}")


def _opener():
    """urllib-Opener, der JEDE Weiterleitung vor dem Folgen prüft – sonst ginge die Anfrage an eine interne
    Adresse schon raus, bevor die Antwort verworfen wird."""
    import urllib.request

    class _SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            check_public_url(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(_SafeRedirect)


def _fetch(url: str, headers: dict, timeout: int = 30, retries: int = 5) -> bytes:
    import time
    import urllib.request

    # bei jeder Anfrage neu prüfen (kein Merken pro Host: ein Name kann später auf eine andere IP zeigen)
    check_public_url(url)
    opener = _opener()
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with opener.open(req, timeout=timeout) as r:  # noqa: S310 – Schema/Host und Weiterleitungen geprüft
                data = r.read(MAX_FETCH + 1)
                if len(data) > MAX_FETCH:
                    raise ClipForgeError("Ein Videostück ist unerwartet groß.", "Der Download wurde abgebrochen.")
                return data
        except ClipForgeError:
            raise  # Weiterleitung ins lokale Netz: nicht erneut versuchen
        except OSError as e:
            last = e
            time.sleep(1 + i)
    raise ClipForgeError("Ein Videostück konnte nicht geladen werden.", "Prüfe die Internetverbindung.", str(last))


def parse_m3u8(text: str, base: str) -> tuple[list[tuple[float, float, str]], str | None, bool]:
    """Liefert ([(start, dauer, url)], init_url, verschlüsselt)."""
    from urllib.parse import urljoin

    segs, t, dur, init, enc = [], 0.0, None, None, False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            dur = float(line[8:].split(",")[0])
        elif line.startswith("#EXT-X-MAP:"):
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                init = urljoin(base, m.group(1))
        elif line.startswith("#EXT-X-KEY:") and "METHOD=NONE" not in line:
            enc = True
        elif line and not line.startswith("#") and dur is not None:
            segs.append((t, dur, urljoin(base, line)))
            t += dur
            dur = None
    return segs, init, enc


def hls_section(playlist_url: str, headers: dict, start: float, end: float, out: Path,
                on_progress: Callable[[float, str], None] | None = None, cancel: threading.Event | None = None,
                workers: int = 8, ffmpeg_path: str = "") -> float:
    """Lädt nur die HLS-Segmente zwischen start und end, parallel, und fügt sie ohne Neukodierung zusammen.

    FFmpeg würde den Stream sonst bis zur Startstelle durchlesen – bei Stunde 5 eines VODs sind das Gigabytes.
    Liefert den tatsächlichen Start (Segmentgrenze) im Original-VOD.
    """
    import shutil
    import tempfile
    from concurrent.futures import ThreadPoolExecutor

    from clipforge.core import ffmpeg as ff

    segs, init, enc = parse_m3u8(_fetch(playlist_url, headers).decode("utf-8", "replace"), playlist_url)
    if enc:
        raise ClipForgeError("Der Stream ist verschlüsselt.", "Lade das ganze VOD ohne Ausschnitt.")
    pick = [s for s in segs if s[0] + s[1] > start and s[0] < end]
    if not pick:
        raise ClipForgeError("Der gewählte Ausschnitt liegt außerhalb des VODs.",
                             f"Das VOD ist {segs[-1][0] + segs[-1][1]:.0f} s lang." if segs else "")
    tmp = Path(tempfile.mkdtemp(prefix="cf_hls_", dir=out.parent))
    done = [0]
    lock = threading.Lock()

    def get(i_seg):
        i, (_, _, url) = i_seg
        if cancel and cancel.is_set():
            raise Cancelled()
        data = _fetch(url, headers)
        (tmp / f"{i:06d}.seg").write_bytes(data)
        with lock:
            done[0] += 1
            if on_progress:
                on_progress(done[0] / len(pick) * 0.95, f"Lade Ausschnitt: {done[0]}/{len(pick)} Stücke")

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(get, enumerate(pick)))
        joined = tmp / ("joined.mp4" if init else "joined.ts")
        with open(joined, "wb") as fo:
            if init:
                fo.write(_fetch(init, headers))
            for i in range(len(pick)):
                fo.write((tmp / f"{i:06d}.seg").read_bytes())
        # erst unter .part schreiben: ein Abbruch hinterlässt nie eine halbe Datei, die später als fertig gilt
        part = out.with_name(out.stem + ".part.mp4")
        ff.run(["-y", "-i", str(joined), "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
                *(["-bsf:a", "aac_adtstoasc"] if not init else []), "-movflags", "+faststart", str(part)],
               cancel=cancel, configured=ffmpeg_path, error_message="Der Ausschnitt konnte nicht zusammengefügt werden.")
        part.replace(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        out.with_name(out.stem + ".part.mp4").unlink(missing_ok=True)
    if on_progress:
        on_progress(1.0, "Ausschnitt geladen")
    log.info("HLS-Ausschnitt %s–%s s: %d Stücke, Start %.2f s", start, end, len(pick), pick[0][0])
    return pick[0][0]


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/131.0 Safari/537.36")


def _download_kick(url: str, target: Path, on_progress, cancel, max_height: int | None,
                   section: tuple[float, float] | None) -> Source:
    """Kick über den eigenen Weg: Videoseite → HLS-Stream → nur die nötigen Segmente.
    Funktioniert auch mit dem neuen Kick-Link-Format, das yt-dlp nicht kennt."""
    from clipforge.pipeline import kick

    if on_progress:
        on_progress(0.0, "Suche das Kick-VOD …")
    v = kick.resolve(url)
    headers = {"User-Agent": _UA, "Referer": "https://kick.com/", "Origin": "https://kick.com"}
    master = _fetch(v.m3u8, headers).decode("utf-8", "replace")
    variant = kick.best_variant(v.m3u8, master, max_height) if "#EXT-X-STREAM-INF" in master else v.m3u8
    segs, _, _ = parse_m3u8(_fetch(variant, headers).decode("utf-8", "replace"), variant)
    total = segs[-1][0] + segs[-1][1] if segs else v.duration
    start, end = section if section else (0.0, total)
    tag = f"_{int(start)}-{int(end)}" if section else ""
    name = re.sub(r"[^\w.-]+", "_", f"{v.streamer or v.slug}_{(v.date or '').replace('-', '')}_{v.vod_id[:8]}{tag}")
    path = target / f"{name}.mp4"
    if path.exists():
        offset = next((s[0] for s in segs if s[0] + s[1] > start), start) if section else 0.0
    else:
        offset = hls_section(variant, headers, start, end, path, on_progress, cancel)
    log.info("Kick-VOD geladen: %s (%s)", path, v.title)
    return Source(path=path, title=v.title or path.stem, streamer=v.streamer or v.slug, date=v.date, url=url,
                  vod_id=v.vod_id, platform="Kick", offset=offset if section else 0.0)


def download(
    url: str,
    target_dir: str | Path,
    on_progress: Callable[[float, str], None] | None = None,
    cancel: threading.Event | None = None,
    max_height: int | None = None,  # None = beste verfügbare Qualität
    section: tuple[float, float] | None = None,
) -> Source:
    """Lädt ein VOD von Twitch, Kick, YouTube (oder jeder anderen von yt-dlp unterstützten Seite).

    Bereits vorhandene Dateien werden wiederverwendet, abgebrochene Downloads setzt yt-dlp fort.
    section=(Start, Ende) in Sekunden lädt nur einen Ausschnitt.
    """
    platform = platform_of(url)
    if platform == "Kick" and "/videos/" in url:
        Path(target_dir).mkdir(parents=True, exist_ok=True)
        return _download_kick(url, Path(target_dir), on_progress, cancel, max_height, section)
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
        # beste Qualität: höchste Auflösung, dann Bildrate, dann Bitrate
        "format": (f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best" if max_height
                   else "bestvideo*+bestaudio/best"),
        "format_sort": ["res", "fps", "tbr"],
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
    offset = section[0] if section else 0.0
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            if section:
                info = ydl.extract_info(url, download=False)
                fmts = info.get("requested_formats") or [info]
                path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
                if len(fmts) == 1 and str(fmts[0].get("protocol", "")).startswith("m3u8") and not path.exists():
                    # schneller Weg: nur die Segmente des Ausschnitts laden
                    offset = hls_section(fmts[0]["url"], fmts[0].get("http_headers") or {}, section[0], section[1],
                                         path, on_progress, cancel)
                elif not path.exists():
                    info = ydl.extract_info(url, download=True)
            else:
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
                  date=date, url=url, vod_id=str(info.get("id") or ""), platform=platform, offset=offset)
