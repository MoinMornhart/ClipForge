"""Update-Funktion: prüft GitHub auf eine neuere Version und aktualisiert per git pull.

Lokale Daten (Einstellungen, Projekte, Keys) liegen außerhalb des Programmordners und bleiben erhalten.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from clipforge.core.errors import ClipForgeError
from clipforge.core.log import get_logger

log = get_logger("updater")

ROOT = Path(__file__).resolve().parents[2]
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class UpdateInfo:
    available: bool
    behind: int = 0
    current: str = ""
    latest: str = ""
    changes: list[str] | None = None
    message: str = ""


def _git(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    git = shutil.which("git")
    if not git:
        raise ClipForgeError("Git ist nicht installiert.", "Installiere Git mit „winget install Git.Git“.")
    return subprocess.run([git, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=_NO_WINDOW)


def check() -> UpdateInfo:
    if not (ROOT / ".git").exists():
        return UpdateInfo(False, message="Kein Git-Ordner – Updates nur über einen neuen Download.")
    try:
        r = _git("fetch", "--quiet", "origin")
    except subprocess.TimeoutExpired:
        return UpdateInfo(False, message="GitHub antwortet nicht. Später erneut versuchen.")
    if r.returncode != 0:
        log.warning("git fetch fehlgeschlagen: %s", r.stderr)
        return UpdateInfo(False, message="GitHub ist nicht erreichbar. Internetverbindung prüfen.")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    behind = _git("rev-list", "--count", f"HEAD..origin/{branch}").stdout.strip()
    current = _git("rev-parse", "--short", "HEAD").stdout.strip()
    latest = _git("rev-parse", "--short", f"origin/{branch}").stdout.strip()
    n = int(behind or 0)
    changes = _git("log", "--format=%s", f"HEAD..origin/{branch}").stdout.strip().splitlines()[:10] if n else []
    return UpdateInfo(n > 0, n, current, latest, changes,
                      f"{n} neue Änderung(en) verfügbar." if n else "ClipForge ist aktuell.")


def apply() -> str:
    """git pull --ff-only + Pakete aktualisieren. Liefert eine Erfolgsmeldung."""
    dirty = _git("status", "--porcelain", "--untracked-files=no").stdout.strip()
    if dirty:
        raise ClipForgeError("Im Programmordner gibt es eigene Änderungen.",
                             "Sichere sie oder verwirf sie mit „git stash“, dann erneut aktualisieren.")
    r = _git("pull", "--ff-only", timeout=180)
    if r.returncode != 0:
        raise ClipForgeError("Das Update konnte nicht geladen werden.", "Details stehen in der Logdatei.", r.stderr)
    pip = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")],
                         capture_output=True, text=True, timeout=900, creationflags=_NO_WINDOW)
    if pip.returncode != 0:
        log.warning("pip nach Update: %s", pip.stderr[-2000:])
        return "Update geladen, aber einige Pakete konnten nicht aktualisiert werden. Siehe Logdatei."
    head = _git("rev-parse", "--short", "HEAD").stdout.strip()
    return f"Update installiert (Version {head}). Bitte ClipForge neu starten."
