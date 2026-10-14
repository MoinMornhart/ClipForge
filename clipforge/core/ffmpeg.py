"""FFmpeg/FFprobe finden und ausführen, mit Fortschritt und Abbruch."""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import threading
from functools import lru_cache
from pathlib import Path
from typing import Callable

from clipforge.core.errors import Cancelled, ClipForgeError
from clipforge.core.log import get_logger

log = get_logger("ffmpeg")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def which_safe(name: str) -> str | None:
    """Wie shutil.which, aber ohne den aktuellen Ordner: unter Windows sucht shutil.which dort zuerst,
    eine untergeschobene ffmpeg.exe/git.exe im Downloads-Ordner würde sonst gestartet."""
    cwd = os.path.normcase(os.path.abspath(os.getcwd()))
    exts = [""] + os.environ.get("PATHEXT", ".EXE;.CMD;.BAT").lower().split(";")
    for d in os.environ.get("PATH", "").split(os.pathsep):
        d = d.strip('"')
        if not d or os.path.normcase(os.path.abspath(d)) == cwd:
            continue
        for ext in exts:
            p = os.path.join(d, name + ext)
            if os.path.isfile(p):
                return p
    return None


def _candidates(name: str, configured: str = "") -> list[str]:
    out = []
    if configured:
        p = Path(configured)
        out.append(str(p / f"{name}.exe") if p.is_dir() else str(p.with_name(f"{name}.exe")))
    found = which_safe(name)
    if found:
        out.append(found)
    local = os.environ.get("LOCALAPPDATA", "")
    out.append(os.path.join(local, "Microsoft", "WinGet", "Links", f"{name}.exe"))
    pkgs = Path(local, "Microsoft", "WinGet", "Packages")
    if pkgs.is_dir():
        out += [str(p) for p in pkgs.glob(f"Gyan.FFmpeg*/**/bin/{name}.exe")]
    return out


@lru_cache(maxsize=4)
def find_tool(name: str, configured: str = "") -> str:
    for c in _candidates(name, configured):
        if c and Path(c).is_file():
            return c
    raise ClipForgeError(
        f"{name} wurde nicht gefunden.",
        "Installiere FFmpeg mit „winget install Gyan.FFmpeg“ oder trage den Pfad in den Einstellungen ein.",
    )


def ffmpeg(configured: str = "") -> str:
    return find_tool("ffmpeg", configured)


def ffprobe(configured: str = "") -> str:
    return find_tool("ffprobe", configured)


def probe(path: str | Path, configured: str = "") -> dict:
    cmd = [ffprobe(configured), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise ClipForgeError(
            "Die Videodatei konnte nicht gelesen werden.",
            "Prüfe, ob die Datei vollständig heruntergeladen und nicht beschädigt ist.",
            r.stderr,
        )
    data = json.loads(r.stdout)
    v = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
    fps = 30.0
    if v and v.get("avg_frame_rate", "0/0") != "0/0":
        n, d = v["avg_frame_rate"].split("/")
        fps = float(n) / float(d) if float(d) else 30.0
    return {
        "duration": float(data["format"].get("duration", 0.0)),
        "width": int(v["width"]) if v else 0,
        "height": int(v["height"]) if v else 0,
        "fps": fps,
        "has_audio": a is not None,
        "has_video": v is not None,
    }


@lru_cache(maxsize=1)
def nvenc_available(configured: str = "") -> bool:
    """Prüft, ob NVENC tatsächlich kodieren kann (nicht nur einkompiliert ist)."""
    cmd = [ffmpeg(configured), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.2",
           "-c:v", "h264_nvenc", "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=_NO_WINDOW)
        ok = r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    log.info("NVENC verfügbar: %s", ok)
    return ok


_PROCS: set[subprocess.Popen] = set()
_PROCS_LOCK = threading.Lock()


def kill_all() -> None:
    """Alle noch laufenden FFmpeg-Prozesse beenden (beim Schließen von ClipForge) – sonst rendern sie
    unsichtbar weiter."""
    with _PROCS_LOCK:
        procs = list(_PROCS)
    for p in procs:
        try:
            p.kill()
        except OSError:
            pass


atexit.register(kill_all)


def run(
    args: list[str],
    duration: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
    configured: str = "",
    error_message: str = "FFmpeg ist bei der Verarbeitung gescheitert.",
    cwd: str | Path | None = None,
) -> None:
    """Führt ffmpeg aus. on_progress bekommt Werte von 0.0 bis 1.0.
    cwd: Arbeitsordner, damit Filter (Untertitel, sendcmd) kurze relative Dateinamen nutzen können –
    Windows-Pfade mit Laufwerksdoppelpunkt müssten in Filtern sonst aufwendig maskiert werden."""
    cmd = [ffmpeg(configured), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", *args]
    log.debug("ffmpeg %s", subprocess.list2cmdline(cmd[1:]))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", creationflags=_NO_WINDOW, cwd=str(cwd) if cwd else None)
    with _PROCS_LOCK:
        _PROCS.add(proc)
    err_lines: list[str] = []
    t = threading.Thread(target=lambda: err_lines.extend(proc.stderr), daemon=True)
    t.start()
    assert proc.stdout
    try:
        for line in proc.stdout:
            if cancel and cancel.is_set():
                raise Cancelled()
            if on_progress and duration and line.startswith("out_time_us="):
                try:
                    us = int(line.split("=", 1)[1])
                    on_progress(min(1.0, us / 1e6 / duration))
                except ValueError:
                    pass
        proc.wait()
    finally:
        if proc.poll() is None:  # Abbruch oder Fehler im Fortschritt-Callback
            proc.kill()
            proc.wait()
        with _PROCS_LOCK:
            _PROCS.discard(proc)
    t.join(timeout=2)
    if proc.returncode != 0:
        stderr = "".join(err_lines[-30:])
        log.error("ffmpeg fehlgeschlagen (%s): %s", proc.returncode, stderr)
        raise ClipForgeError(error_message, "Details stehen in der Logdatei.", stderr)
    if on_progress:
        on_progress(1.0)
