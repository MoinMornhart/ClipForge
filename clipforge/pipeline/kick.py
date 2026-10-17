"""Kick-VODs auflösen, auch mit dem neuen Link-Format.

Kick verwendet in neuen Links (kick.com/<kanal>/videos/<id>) Kennungen, die die öffentliche Video-API
und yt-dlp nicht kennen (HTTP 404). Die Videoseite selbst enthält aber den HLS-Stream; über ihn wird der
passende Eintrag in der Videoliste des Kanals gefunden (Titel, Startzeit, Kanal-ID für den Chat).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from clipforge.core.errors import ClipForgeError
from clipforge.core.log import get_logger

log = get_logger("kick")

_URL = re.compile(r"kick\.com/([^/?#]+)/videos/([0-9a-f-]{36})", re.I)
_M3U8 = re.compile(r'https://stream\.kick\.com/[^"\\\s]+?/master\.m3u8')


@dataclass
class KickVod:
    slug: str  # Kanalname in der URL
    vod_id: str  # Kennung aus dem Link
    m3u8: str  # master.m3u8 des VODs
    title: str = ""
    streamer: str = ""
    start_time: dt.datetime | None = None  # Streamstart (UTC) für die Chat-Wiedergabe
    channel_id: int | None = None
    duration: float = 0.0
    game: str = ""  # Kategorie des Streams, z. B. „Fortnite“

    @property
    def date(self) -> str:
        return self.start_time.date().isoformat() if self.start_time else ""


def parse_url(url: str) -> tuple[str, str] | None:
    m = _URL.search(url)
    return (m.group(1), m.group(2).lower()) if m else None


def _session():
    try:
        from curl_cffi import requests
    except ImportError as e:
        raise ClipForgeError("Für Kick-Links fehlt ein Zusatzpaket.", "ClipForge neu installieren bzw. install.bat.") from e
    return requests.Session(impersonate="chrome")


def _ts(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    t = dt.datetime.fromisoformat(s.replace(" ", "T").replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _from_entry(slug: str, vod_id: str, e: dict) -> KickVod:
    ls = e.get("livestream") or e  # api/v1/video liefert {livestream: …}, die Kanalliste den Eintrag direkt
    ch = ls.get("channel") or {}
    return KickVod(
        slug=slug, vod_id=vod_id, m3u8=e.get("source") or ls.get("source") or "",
        title=ls.get("session_title") or "", streamer=(ch.get("user") or {}).get("username") or ch.get("slug") or slug,
        start_time=_ts(ls.get("start_time")), channel_id=ls.get("channel_id"),
        duration=float(ls.get("duration") or 0) / 1000.0,
        game=((e.get("categories") or ls.get("categories") or [{}])[0] or {}).get("name") or "",
    )


def resolve(url: str) -> KickVod:
    """Liefert Stream und Metadaten eines Kick-VODs (altes und neues Link-Format)."""
    p = parse_url(url)
    if not p:
        raise ClipForgeError("Das ist kein gültiger Kick-VOD-Link.", "Format: kick.com/<kanal>/videos/<id>")
    slug, vod_id = p
    s = _session()
    # 1) altes Format: direkte Video-API
    try:
        r = s.get(f"https://kick.com/api/v1/video/{vod_id}", timeout=30)
        if r.status_code == 200:
            v = _from_entry(slug, vod_id, r.json())
            if v.m3u8:
                return v
    except Exception as e:  # Netz/Cloudflare: weiter mit der Seite
        log.debug("Kick-Video-API: %s", e)
    # 2) neues Format: Stream aus der Videoseite, Metadaten aus der Videoliste des Kanals
    try:
        page = s.get(f"https://kick.com/{slug}/videos/{vod_id}", timeout=30)
    except Exception as e:
        raise ClipForgeError("Kick ist nicht erreichbar.", "Prüfe die Internetverbindung.", str(e)) from e
    if page.status_code != 200:
        raise ClipForgeError("Das Kick-VOD wurde nicht gefunden.",
                             "Prüfe den Link. VODs löscht Kick je nach Kanal nach einiger Zeit.",
                             f"HTTP {page.status_code}")
    streams = list(dict.fromkeys(_M3U8.findall(page.text)))
    entries: list[dict] = []
    try:
        lr = s.get(f"https://kick.com/api/v2/channels/{slug}/videos", timeout=30)
        if lr.status_code == 200 and isinstance(lr.json(), list):
            entries = lr.json()
    except Exception as e:
        log.debug("Kick-Videoliste: %s", e)
    for e in entries:
        if (e.get("video") or {}).get("uuid") == vod_id or (e.get("source") and e["source"] in streams):
            v = _from_entry(slug, vod_id, e)
            log.info("Kick-VOD aufgelöst über Videoliste: %s", v.title)
            return v
    if streams:
        t = re.search(r"<title>([^<]+)</title>", page.text)
        log.info("Kick-VOD nur über die Seite aufgelöst (ohne Chat-Daten)")
        return KickVod(slug, vod_id, streams[0], title=(t.group(1).strip() if t else vod_id), streamer=slug)
    raise ClipForgeError("Auf der Kick-Seite wurde kein Video gefunden.",
                         "Ist das VOD privat oder nur für Abonnenten? Dann die Datei herunterladen und hineinziehen.")


def best_variant(master_url: str, master_text: str, max_height: int | None = None) -> str:
    """Wählt aus der master.m3u8 die beste Qualität (optional höchstens max_height)."""
    from urllib.parse import urljoin

    best, best_key = None, (-1, -1)
    lines = master_text.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        res = re.search(r"RESOLUTION=\d+x(\d+)", line)
        bw = re.search(r"BANDWIDTH=(\d+)", line)
        h = int(res.group(1)) if res else 0
        key = (h if max_height is None or h <= max_height else -h, int(bw.group(1)) if bw else 0)
        nxt = next((x for x in lines[i + 1:] if x and not x.startswith("#")), None)
        if nxt and key > best_key:
            best, best_key = urljoin(master_url, nxt.strip()), key
    return best or master_url
