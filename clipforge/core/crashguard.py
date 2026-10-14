"""Crash-Schutz.

- Unerwartete Python-Fehler (auch in Threads und Qt-Slots) beenden das Programm nicht, sondern landen
  im Log und als verständliche Meldung im Fenster.
- Abstürze tief in Qt/Bibliotheken werden mit Stacktrace nach logs/crash.log geschrieben (faulthandler).
- Unter pythonw (ClipForge.exe) gibt es keine Konsole: Ausgaben von Bibliotheken (Download-Balken usw.)
  gehen in eine Datei statt ins Leere – sonst brechen manche Bibliotheken mit Fehlern ab.
- Sitzungs-Markierung: Wurde ClipForge beim letzten Mal nicht sauber beendet, wird das zuletzt geöffnete
  VOD beim nächsten Start wieder geöffnet.
"""

from __future__ import annotations

import faulthandler
import json
import sys
import threading
import traceback
from pathlib import Path
from typing import Callable

from clipforge.core.log import get_logger
from clipforge.core.paths import app_dir, logs_dir

log = get_logger("crashguard")

_files: list = []  # offene Dateien am Leben halten
_reporter: Callable[[str, str], None] | None = None


def _session_file() -> Path:
    return app_dir() / "session.json"


def install() -> None:
    logs = logs_dir()
    # 1) fehlende Konsole (pythonw): Ausgaben in eine Datei
    if sys.stdout is None or sys.stderr is None:
        out = open(logs / "ausgabe.log", "a", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
        _files.append(out)
        if sys.stdout is None:
            sys.stdout = out
        if sys.stderr is None:
            sys.stderr = out
    # 2) harte Abstürze mit Stacktrace protokollieren
    crash = open(logs / "crash.log", "a", encoding="utf-8", errors="replace")  # noqa: SIM115
    _files.append(crash)
    faulthandler.enable(file=crash, all_threads=True)
    # 3) Python-Fehler abfangen statt abstürzen
    sys.excepthook = _excepthook
    threading.excepthook = lambda a: _excepthook(a.exc_type, a.exc_value, a.exc_traceback)


def set_reporter(fn: Callable[[str, str], None]) -> None:
    """fn(meldung, details) zeigt den Fehler im Fenster an (wird threadsicher aufgerufen)."""
    global _reporter
    _reporter = fn


def _excepthook(exc_type, exc, tb) -> None:
    if exc_type is KeyboardInterrupt:
        return
    details = "".join(traceback.format_exception(exc_type, exc, tb))
    log.error("Abgefangener Fehler (Programm läuft weiter):\n%s", details)
    if _reporter:
        try:
            _reporter(f"{exc_type.__name__}: {exc}", details)
        except Exception:  # Meldung darf selbst nie abstürzen
            pass


# --------------------------------------------------------------------------- Sitzung


def begin_session() -> dict | None:
    """Markiert die Sitzung als laufend. Liefert die vorige Sitzung, falls sie abgestürzt ist."""
    f = _session_file()
    prev = None
    if f.exists():
        try:
            prev = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = {}
    _write({"running": True, "project": (prev or {}).get("project")})
    return prev if prev and prev.get("running") else None


def remember_project(pid: str) -> None:
    _write({"running": True, "project": pid})


def end_session() -> None:
    try:
        data = json.loads(_session_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    data["running"] = False
    _write(data)


def _write(data: dict) -> None:
    try:
        _session_file().write_text(json.dumps(data), encoding="utf-8")
    except OSError as e:
        log.debug("Sitzungsdatei: %s", e)
