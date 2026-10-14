"""Chat eines VODs laden (Twitch, Kick) und Chat-Spitzen finden.

Twitch: öffentliche Chat-Wiedergabe über gql.twitch.tv (wie der Web-Player).
Kick:   Chat-Wiedergabe über die Kick-API (hinter Cloudflare, daher curl_cffi).
Der geladene Chat wird im Projekt gespeichert; ein Abbruch setzt an der letzten Stelle fort.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import statistics
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from clipforge.core.errors import Cancelled, ClipForgeError
from clipforge.core.log import get_logger

log = get_logger("chat")

TWITCH_GQL = "https://gql.twitch.tv/gql"
TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # öffentliche Client-ID des Twitch-Web-Players
_TWITCH_Q = """query($id: ID!, $off: Int, $cursor: Cursor) { video(id: $id) {
  comments(contentOffsetSeconds: $off, after: $cursor) {
    edges { node { id contentOffsetSeconds commenter { login } message { fragments { text } } } }
    pageInfo { hasNextPage } } } }"""

DEFAULT_REACTIONS = ["KEKW", "LUL", "OMEGALUL", "LMAO", "LOL", "xD", "PogChamp", "Pog", "POGGERS", "W", "!clip",
                     "clip", "WTF", "monkaS", "Kappa", "😂", "💀", "🔥", "HAHAHA", "haha", "Clip it"]


@dataclass
class ChatMessage:
    t: float  # Sekunden im VOD
    text: str
    user: str = ""


def _session():
    try:
        from curl_cffi import requests
    except ImportError as e:
        raise ClipForgeError("Zum Laden des Chats fehlt ein Zusatzpaket.", "Führe install.bat erneut aus.") from e
    return requests.Session(impersonate="chrome")


# --------------------------------------------------------------------------- Twitch


def twitch_vod_id(url: str) -> str | None:
    m = re.search(r"twitch\.tv/(?:\w+/)?videos?/(\d+)", url)
    return m.group(1) if m else None


def fetch_twitch(vod_id: str, start: float, end: float, on_progress=None, cancel=None) -> list[ChatMessage]:
    """Blättert über die Zeitposition (das Weiterblättern per Cursor verlangt bei Twitch inzwischen
    eine Integritätsprüfung). Jede Antwort enthält die Nachrichten ab der angefragten Sekunde."""
    s = _session()
    out: list[ChatMessage] = []
    seen: set[str] = set()
    off = int(start)
    pages = errors = 0
    while off <= end:
        if cancel and cancel.is_set():
            raise Cancelled()
        try:
            r = s.post(TWITCH_GQL, headers={"Client-Id": TWITCH_CLIENT_ID},
                       data=json.dumps({"query": _TWITCH_Q, "variables": {"id": vod_id, "off": off}}), timeout=30)
            data = r.json()["data"]["video"]["comments"]
            edges = (data or {}).get("edges") or []
            errors = 0
        except Exception as e:
            errors += 1
            if pages == 0 and errors > 3:
                raise ClipForgeError("Der Twitch-Chat konnte nicht geladen werden.",
                                     "Das VOD hat evtl. keinen Chat mehr. Die Analyse läuft ohne Chat weiter.",
                                     str(e)) from e
            if errors > 5:
                log.warning("Twitch-Chat abgebrochen bei %s s: %s", off, e)
                break
            time.sleep(errors)
            continue
        newest = off
        for ed in edges:
            n = ed["node"]
            t = float(n["contentOffsetSeconds"])
            key = n.get("id") or f"{t}:{n.get('message')}"
            if key in seen:
                continue
            seen.add(key)
            newest = max(newest, int(t))
            if start <= t <= end:
                text = "".join(f.get("text", "") for f in (n.get("message") or {}).get("fragments") or [])
                out.append(ChatMessage(t, text, ((n.get("commenter") or {}).get("login") or "")))
        pages += 1
        if on_progress and end > start:
            on_progress(min(1.0, (newest - start) / (end - start)), f"Twitch-Chat: {len(out)} Nachrichten")
        # Weiter ab der neuesten Sekunde; ohne neue Nachrichten 10 s springen
        off = newest + 1 if newest > off else off + 10
        time.sleep(0.05)
    out.sort(key=lambda m: m.t)
    return out


# --------------------------------------------------------------------------- Kick


def kick_video_uuid(url: str) -> str | None:
    m = re.search(r"kick\.com/[^/]+/videos/([0-9a-f-]{36})", url)
    return m.group(1) if m else None


def fetch_kick(uuid: str, start: float, end: float, on_progress=None, cancel=None,
               step_empty: float = 5.0, workers: int = 16) -> list[ChatMessage]:
    """Die Kick-API liefert pro Abfrage nur wenige Sekunden Chat. Darum wird die Zeitachse in
    Abschnitte geteilt, die parallel geladen werden."""
    from concurrent.futures import ThreadPoolExecutor

    s = _session()
    try:
        v = s.get(f"https://kick.com/api/v1/video/{uuid}", timeout=30).json()
        ls = v["livestream"]
        channel_id = ls["channel_id"]
        t0 = dt.datetime.fromisoformat(ls["start_time"].replace(" ", "T"))
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=dt.timezone.utc)
    except Exception as e:
        raise ClipForgeError("Der Kick-Chat konnte nicht geladen werden.",
                             "Die Analyse läuft ohne Chat weiter.", str(e)) from e
    lock = threading.Lock()
    found: dict[str, ChatMessage] = {}
    done_secs = [0.0]

    def crawl(a: float, b: float) -> None:
        sess = _session()
        t, errors = a, 0
        while t < b:
            if cancel and cancel.is_set():
                return
            when = (t0 + dt.timedelta(seconds=t)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            try:
                msgs = sess.get(f"https://kick.com/api/v2/channels/{channel_id}/messages?start_time={when}",
                                timeout=30).json()["data"]["messages"]
                errors = 0
            except Exception as e:
                errors += 1
                if errors > 5:
                    log.warning("Kick-Chat-Abschnitt abgebrochen bei %.0f s: %s", t, e)
                    return
                time.sleep(2 * errors)
                continue
            newest = t
            with lock:
                for m in msgs:
                    mt = (dt.datetime.fromisoformat(m["created_at"].replace("Z", "+00:00")) - t0).total_seconds()
                    newest = max(newest, mt)
                    if start <= mt <= end and m.get("id") not in found:
                        found[m.get("id")] = ChatMessage(mt, m.get("content", ""),
                                                         (m.get("sender") or {}).get("username", ""))
            nxt = newest + 1.0 if newest > t else t + step_empty
            with lock:
                done_secs[0] += min(nxt, b) - t
                if on_progress:
                    on_progress(min(1.0, done_secs[0] / max(1.0, end - start)), f"Kick-Chat: {len(found)} Nachrichten")
            t = nxt

    n = max(1, min(workers, int((end - start) // 60) or 1))
    bounds = [start + (end - start) * i / n for i in range(n + 1)]
    with ThreadPoolExecutor(max_workers=n) as ex:
        list(ex.map(lambda i: crawl(bounds[i], bounds[i + 1]), range(n)))
    if cancel and cancel.is_set():
        raise Cancelled()
    return sorted(found.values(), key=lambda m: m.t)


# --------------------------------------------------------------------------- Laden mit Zwischenspeicher


def load_chat(url: str, duration: float, work_dir: Path, offset: float = 0.0,
              on_progress: Callable[[float, str], None] | None = None,
              cancel: threading.Event | None = None) -> list[ChatMessage] | None:
    """Lädt den Chat für [offset, offset+duration] des Original-VODs. Zeiten im Ergebnis sind relativ
    zur lokalen Datei (also minus offset). None = Plattform ohne Chat-Unterstützung."""
    cache = work_dir / "chat.json"
    if cache.exists():
        return [ChatMessage(**m) for m in json.loads(cache.read_text(encoding="utf-8"))]
    start, end = offset, offset + duration
    if vid := twitch_vod_id(url):
        msgs = fetch_twitch(vid, start, end, on_progress, cancel)
    elif uuid := kick_video_uuid(url):
        msgs = fetch_kick(uuid, start, end, on_progress, cancel)
    else:
        return None
    msgs = [ChatMessage(round(m.t - offset, 2), m.text, m.user) for m in msgs]
    cache.write_text(json.dumps([asdict(m) for m in msgs], ensure_ascii=False), encoding="utf-8")
    log.info("Chat geladen: %d Nachrichten", len(msgs))
    return msgs


# --------------------------------------------------------------------------- Spitzen


@dataclass
class ChatPeak:
    time: float  # Mitte des stärksten Fensters
    start: float
    count: int
    baseline: float
    reactions: Counter

    @property
    def reason(self) -> str:
        top = ", ".join(f"{k} ×{v}" for k, v in self.reactions.most_common(3))
        return f"Chat-Spitze: {self.count} Nachrichten in 10 s (sonst ~{self.baseline:.0f})" + (f", {top}" if top else "")


def _reaction_hits(text: str, words: list[str]) -> list[str]:
    low = text.lower()
    hits = []
    for w in words:
        if w.startswith("!") or not w.isalnum():
            if w.lower() in low:
                hits.append(w)
        elif re.search(rf"(?<![\w]){re.escape(w.lower())}(?![\w])", low):
            hits.append(w)
    return hits


def find_peaks(msgs: list[ChatMessage], duration: float, reactions: list[str] | None = None,
               window: float = 10.0, sensitivity: float = 3.0, min_count: int = 8,
               min_gap: float = 45.0) -> list[ChatPeak]:
    """Fenster mit deutlich mehr Nachrichten (und Reaktionen) als üblich.

    Bewertet wird pro Sekunde: Nachrichten + doppelt gewichtete Reaktionen (KEKW, !clip …) im 10-s-Fenster,
    verglichen mit dem Median der umliegenden 5 Minuten.
    """
    if not msgs or duration <= 0:
        return []
    reactions = reactions or DEFAULT_REACTIONS
    n = int(duration) + 1
    per_sec = [0] * n
    react_sec = [0] * n
    react_words: list[list[str]] = [[] for _ in range(n)]
    for m in msgs:
        i = int(m.t)
        if 0 <= i < n:
            per_sec[i] += 1
            r = _reaction_hits(m.text, reactions)
            react_sec[i] += len(r)
            react_words[i] += r
    w = int(window)
    # gleitende Summe über das Fenster
    score, count = [0.0] * n, [0] * n
    s_cnt = s_react = 0
    for i in range(n):
        s_cnt += per_sec[i]
        s_react += react_sec[i]
        if i >= w:
            s_cnt -= per_sec[i - w]
            s_react -= react_sec[i - w]
        count[i] = s_cnt
        score[i] = s_cnt + 2 * s_react
    # Grundrauschen: Median der Fenster-Werte in ±150 s (alle 15 s gestützt, dann übertragen)
    step = 15
    stats = []  # je 15-s-Stützstelle: (Median Score, Streuung Score, Median Nachrichten)
    for a in range(0, n, step):
        lo, hi = max(0, a - 150), min(n, a + 150)
        vals = score[lo:hi:3] or [0.0]
        stats.append((statistics.median(vals), statistics.pstdev(vals) if len(vals) > 1 else 0.0,
                      statistics.median(count[lo:hi:3] or [0])))
    peaks: list[ChatPeak] = []
    i = 0
    while i < n:
        med, sd, med_count = stats[i // step]
        thr = max(med + sensitivity * max(sd, 1.0), med * 2.5, float(min_count))
        if score[i] < thr or count[i] < min_count:
            i += 1
            continue
        # Höhepunkt dieses Ausschlags suchen
        j = i
        while j + 1 < n and score[j + 1] >= thr:
            j += 1
        k = max(range(i, j + 1), key=lambda x: score[x])
        t = k - w / 2
        if not peaks or t - peaks[-1].time >= min_gap:
            words = Counter(x for sec in range(max(0, k - w + 1), k + 1) for x in react_words[sec])
            peaks.append(ChatPeak(time=float(t), start=float(max(0, k - w + 1)), count=count[k],
                                  baseline=float(med_count), reactions=words))
        i = j + 1
    return peaks
