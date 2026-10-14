"""Update-Funktion.

Zwei Wege:
- Git-Ordner (Entwickler, git clone): git fetch + git pull --ff-only
- Installierte Version (ClipForge-Setup.exe, kein Git): neueste Fassung als ZIP von GitHub laden und
  den Programmcode austauschen. Die Version steht in der Datei BUILD (Commit-Kennung).

Lokale Daten (Einstellungen, Projekte, Keys) liegen außerhalb des Programmordners und bleiben erhalten.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from clipforge.core.errors import ClipForgeError
from clipforge.core.log import get_logger

log = get_logger("updater")

ROOT = Path(__file__).resolve().parents[2]
REPO = "MoinMornhart/ClipForge"
BRANCH = "main"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_UA = {"User-Agent": "ClipForge-Updater", "Accept": "application/vnd.github+json"}


@dataclass
class UpdateInfo:
    available: bool
    behind: int = 0
    current: str = ""
    latest: str = ""
    changes: list[str] | None = None
    message: str = ""


def is_git() -> bool:
    return (ROOT / ".git").exists()


def _git(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    git = shutil.which("git")
    if not git:
        raise ClipForgeError("Git ist nicht installiert.", "Installiere Git mit „winget install Git.Git“.")
    return subprocess.run([git, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=_NO_WINDOW)


def _api(path: str, timeout: int = 20):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}", headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 – feste https-Adresse
        return json.loads(r.read().decode("utf-8"))


def installed_build() -> str:
    f = ROOT / "BUILD"
    return f.read_text(encoding="utf-8").strip() if f.exists() else ""


def check() -> UpdateInfo:
    return _check_git() if is_git() else _check_zip()


def apply() -> str:
    return _apply_git() if is_git() else _apply_zip()


# --------------------------------------------------------------------------- Git


def _check_git() -> UpdateInfo:
    try:
        r = _git("fetch", "--quiet", "origin")
    except subprocess.TimeoutExpired:
        return UpdateInfo(False, message="GitHub antwortet nicht. Später erneut versuchen.")
    if r.returncode != 0:
        log.warning("git fetch fehlgeschlagen: %s", r.stderr)
        return UpdateInfo(False, message="GitHub ist nicht erreichbar. Internetverbindung prüfen.")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or BRANCH
    behind = _git("rev-list", "--count", f"HEAD..origin/{branch}").stdout.strip()
    current = _git("rev-parse", "--short", "HEAD").stdout.strip()
    latest = _git("rev-parse", "--short", f"origin/{branch}").stdout.strip()
    n = int(behind or 0)
    changes = _git("log", "--format=%s", f"HEAD..origin/{branch}").stdout.strip().splitlines()[:10] if n else []
    return UpdateInfo(n > 0, n, current, latest, changes,
                      f"{n} neue Änderung(en) verfügbar." if n else "ClipForge ist aktuell.")


def _apply_git() -> str:
    dirty = _git("status", "--porcelain", "--untracked-files=no").stdout.strip()
    if dirty:
        raise ClipForgeError("Im Programmordner gibt es eigene Änderungen.",
                             "Sichere sie oder verwirf sie mit „git stash“, dann erneut aktualisieren.")
    r = _git("pull", "--ff-only", timeout=180)
    if r.returncode != 0:
        raise ClipForgeError("Das Update konnte nicht geladen werden.", "Details stehen in der Logdatei.", r.stderr)
    msg = _pip_update()
    head = _git("rev-parse", "--short", "HEAD").stdout.strip()
    return msg or f"Update installiert (Version {head}). Bitte ClipForge neu starten."


# --------------------------------------------------------------------------- ZIP (installierte Version)


def _check_zip() -> UpdateInfo:
    cur = installed_build()
    try:
        latest = _api(f"commits/{BRANCH}")["sha"]
    except OSError as e:
        log.warning("GitHub-Abfrage fehlgeschlagen: %s", e)
        return UpdateInfo(False, message="GitHub ist nicht erreichbar. Internetverbindung prüfen.")
    if cur and latest.startswith(cur):
        return UpdateInfo(False, 0, cur[:7], latest[:7], [], "ClipForge ist aktuell.")
    changes, n = [], 1
    if cur:
        try:
            cmp = _api(f"compare/{cur}...{latest}")
            n = int(cmp.get("ahead_by", 1))
            changes = [c["commit"]["message"].splitlines()[0] for c in cmp.get("commits", [])][-10:]
        except OSError:
            pass
    return UpdateInfo(n > 0, n, cur[:7] or "unbekannt", latest[:7], changes,
                      f"{n} neue Änderung(en) verfügbar." if n else "ClipForge ist aktuell.")


def _apply_zip() -> str:
    try:
        sha = _api(f"commits/{BRANCH}")["sha"]
        req = urllib.request.Request(f"https://codeload.github.com/{REPO}/zip/{sha}", headers=_UA)
        with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 – feste https-Adresse
            data = r.read()
    except OSError as e:
        raise ClipForgeError("Das Update konnte nicht geladen werden.", "Internetverbindung prüfen.", str(e)) from e
    with tempfile.TemporaryDirectory(prefix="cf_update_") as tmp:
        zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
        src = next(Path(tmp).iterdir())  # ClipForge-<sha>/
        if not (src / "clipforge" / "__init__.py").exists():
            raise ClipForgeError("Das Update-Paket ist unvollständig.", "Später erneut versuchen.")
        # Programmcode austauschen (Python lädt die neuen Dateien beim nächsten Start)
        shutil.rmtree(ROOT / "clipforge.old", ignore_errors=True)
        if (ROOT / "clipforge").exists():
            (ROOT / "clipforge").rename(ROOT / "clipforge.old")
        shutil.copytree(src / "clipforge", ROOT / "clipforge")
        for name in ("requirements.txt", "README.md", "LICENSE"):
            if (src / name).exists():
                shutil.copy2(src / name, ROOT / name)
        if (src / "installer" / "setup_runtime.bat").exists():
            shutil.copy2(src / "installer" / "setup_runtime.bat", ROOT / "setup_runtime.bat")
    (ROOT / "BUILD").write_text(sha[:12], encoding="utf-8")
    shutil.rmtree(ROOT / "clipforge.old", ignore_errors=True)
    msg = _pip_update()
    return msg or f"Update installiert (Version {sha[:7]}). Bitte ClipForge neu starten."


def _pip_update() -> str:
    """Pakete nach dem Update aktualisieren. Leere Rückgabe = alles gut."""
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe" and (py.parent / "python.exe").exists():
        py = py.parent / "python.exe"
    pip = subprocess.run([str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                          "--no-warn-script-location", "-r", str(ROOT / "requirements.txt")],
                         capture_output=True, text=True, timeout=1800, creationflags=_NO_WINDOW)
    if pip.returncode != 0:
        log.warning("pip nach Update: %s", pip.stderr[-2000:])
        return "Update geladen, aber einige Pakete konnten nicht aktualisiert werden. Siehe Logdatei."
    return ""
