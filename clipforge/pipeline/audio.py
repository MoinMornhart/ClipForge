"""Lautstärkespitzen (Schreien, Lachen, Jubel) in der Tonspur finden.

FFmpeg misst den RMS-Pegel pro halber Sekunde (nur Ton dekodieren, auch bei 8 h schnell).
Eine Spitze ist ein Abschnitt, der deutlich lauter ist als das, was in den Minuten davor und
danach üblich war, damit laute Spiele nicht ständig auslösen.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from clipforge.core import ffmpeg
from clipforge.core.errors import Cancelled
from clipforge.core.jsonio import write_json
from clipforge.core.log import get_logger

log = get_logger("audio")

HOP = 0.5  # Sekunden pro Messwert


@dataclass
class LoudPeak:
    time: float
    start: float
    level_db: float
    baseline_db: float

    @property
    def reason(self) -> str:
        return f"Lautstärkespitze: {self.level_db - self.baseline_db:+.0f} dB über normal"


def measure_levels(src: Path, duration: float, work_dir: Path, on_progress: Callable[[float, str], None] | None = None,
                   cancel: threading.Event | None = None, ffmpeg_path: str = "") -> list[float]:
    cache = work_dir / "levels.json"
    if cache.exists():
        return json.loads(cache.read_text())
    n = int(16000 * HOP)
    cmd = [ffmpeg.ffmpeg(ffmpeg_path), "-hide_banner", "-nostdin", "-i", str(src), "-vn", "-map", "0:a:0",
           "-af", f"aresample=16000,asetnsamples=n={n}:p=0,astats=metadata=1:reset=1,"
                  "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
           "-f", "null", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    levels: list[float] = []
    rx = re.compile(r"RMS_level=(-?[\d.]+|-inf)")
    assert proc.stdout
    try:
        for line in proc.stdout:
            m = rx.search(line)
            if not m:
                continue
            v = m.group(1)
            levels.append(-90.0 if v == "-inf" else max(-90.0, float(v)))
            if len(levels) % 600 == 0:
                if cancel and cancel.is_set():
                    raise Cancelled()
                if on_progress and duration:
                    on_progress(min(1.0, len(levels) * HOP / duration), "Messe Lautstärke …")
        proc.wait()
    finally:
        if proc.poll() is None:  # Abbruch oder Fehler: FFmpeg nicht weiterlaufen lassen
            proc.kill()
            proc.wait()
    if proc.returncode == 0 and levels:  # nur vollständige Messungen zwischenspeichern
        write_json(cache, [round(x, 1) for x in levels], indent=None)
    else:
        log.warning("Lautstärkemessung unvollständig (Code %s) – nicht zwischengespeichert", proc.returncode)
    return levels


def find_peaks(levels: list[float], above_db: float = 11.0, min_level_db: float = -22.0,
               min_len: float = 1.0, min_gap: float = 45.0, context: float = 180.0) -> list[LoudPeak]:
    if not levels:
        return []
    k = max(1, int(min_len / HOP))
    # geglättet über min_len, damit einzelne Knackser nicht zählen
    smooth = [statistics.fmean(levels[max(0, i - k + 1):i + 1]) for i in range(len(levels))]
    span = int(context / HOP)
    step = int(15 / HOP)
    base = []
    for a in range(0, len(levels), step):
        vals = [v for v in levels[max(0, a - span):a + span:2] if v > -70] or [-60.0]
        base.append(statistics.median(vals))
    peaks: list[LoudPeak] = []
    i = 0
    while i < len(smooth):
        b = base[i // step]
        if smooth[i] >= max(b + above_db, min_level_db):
            j = i
            while j + 1 < len(smooth) and smooth[j + 1] >= b + above_db - 3:
                j += 1
            top = max(range(i, j + 1), key=lambda x: smooth[x])
            t = top * HOP
            if not peaks or t - peaks[-1].time >= min_gap:
                peaks.append(LoudPeak(time=t, start=max(0.0, (i - k + 1) * HOP), level_db=smooth[top], baseline_db=b))
            i = j + 1
        else:
            i += 1
    return peaks
