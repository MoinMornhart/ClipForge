# Stand: ClipForge (Übergabe 2026-10-24)

## Was es ist
Windows-Desktop-App (Python + PySide6), die komplette Twitch-/Kick-VODs (auch YouTube/Datei) mit KI analysiert, die besten Momente findet und sie fertig im 9:16-Hochformat für TikTok, Shorts und Reels schneidet – mit Facecam-Layout, TikTok-Untertiteln, Titel und Hashtags.

## Aktueller Stand
- Aktuelle Version **0.4.2** (Release 21.10., Setup.exe auf GitHub). Weitgehend fertig und nutzbar.
- Meilensteine M0–M6 laut `docs/PLAN.md` erledigt: Import (Twitch, Kick, YouTube, Datei, nur Ausschnitt), lokale Transkription (faster-whisper `large-v3-turbo`, GPU), Trigger (Sprachbefehle, eigene Begriffe, Chat- und Lautstärkespitzen), KI-Bewertung mit 12 Anbietern (u. a. Claude, NVIDIA NIM, Ollama) inkl. Kostenschätzung + tatsächlicher Kosten + Ausgabenlimit, Facecam/Handcam/Objektverfolgung (auch ohne Gesicht, z. B. Bär), mehrere Layouts + eigene Layouts mit Facecam-Editor, Spiel-Erkennung mit passendem Ausschnitt, Untertitel, -14 LUFS, Oberfläche mit Dark Mode, Crash-Schutz, Update-Funktion.
- M7 „Auslieferung“ laut PLAN noch 🟡 (Stapel-Export, Kosten, Symbol, Verknüpfung, Gesamttest sind gebaut – was genau noch fehlt: **unklar**).
- Rund 232 automatische Tests (`tests/`).

## Zuletzt gemacht
- 2026-10-24 Cloud-Start-Hook (Philips Skills automatisch in Cloud-Sitzungen)
- 2026-10-21 0.4.2: Fehler „Internal C++ object (QLabel) already deleted“ beim schnellen Seitenwechsel behoben
- 2026-10-20 Release-Workflow (Setup.exe auf GitHub bauen + veröffentlichen); Clip-Editor im Fenstermodus überlappt nicht mehr; 0.4.1 eigenes Layout „Facecam oben, Gameplay unten“ mit freier Facecam-Größe
- 2026-10-17 0.4.0: Spiel erkennen + passender Ausschnitt, eigene Layouts
- 2026-10-15 0.3.x: Clips nach viralen Kriterien, Ausgabenlimit, Facecam-Größe einstellbar, NVIDIA NIM als Anbieter

## Offen / Nächste Schritte
- Issue #1: 14 Abhängigkeiten aktualisieren (u. a. Major-Sprünge bei `anthropic` 2.x, `numpy` 2.x, `opencv-python` 5.x, `pytest` 9) – vorsichtig einzeln testen.
- Restpunkte von M7 klären (PLAN.md).
- Laut README nicht getestet: KI-Bewertung mit Claude/bezahlten Anbietern an echten VODs (nur gegen nachgebauten Server) und ein komplettes 8-Stunden-VOD am Stück.
- Keine bekannten offenen Fehler im Repo vermerkt.

## Wichtige Infos
- Technik: Python 3.11, PySide6, FFmpeg (NVENC), yt-dlp + curl_cffi (Kick/Cloudflare), faster-whisper/CTranslate2 (CUDA), MediaPipe/OpenCV, keyring für API-Keys.
- Start (Quellcode): `install.bat` (venv unter `%LOCALAPPDATA%\ClipForge\venv`), dann `start.bat`; `update.bat` für Git-Updates. CLI: `python -m clipforge run <Link|Datei>`.
- Tests: `pytest` im Projektordner.
- Version steht in `clipforge/__init__.py` (`__version__`); Release-Notizen in `installer/RELEASE.md`. Release: GitHub **Actions → Release → Run workflow** (baut Setup.exe mit PyInstaller + Inno Setup, überschreibt nie ein bestehendes Release). Lokal: `installer\build.ps1`.
- Update in der App: Einstellungen → Über & Update (lädt geprüfte Releases von GitHub, auch ohne Git).
- Daten: Einstellungen/Projekte/Logs unter `%LOCALAPPDATA%\ClipForge\`, VODs/Exporte unter `Videos\ClipForge\`.
- Architektur und Entscheidungen: `docs/PLAN.md`. Live-Fortschrittsseite (privat) ist dort verlinkt.

## Philips Vorgaben
- `Verfasst mit Claude 🤖` steht in README-Fußzeile, App-Info und Release-Notizen; in Commits meist auch (nicht in allen) → in Git-Texten beibehalten.
- Update-Funktion: **ja**.
- Repo: **öffentlich** (MoinMornhart/ClipForge).
- Oberfläche und Fehlermeldungen auf Deutsch.

---
Diese Datei wird beim Arbeiten aktuell gehalten: nach jedem größeren Schritt „Aktueller Stand“ und „Offen“ anpassen.
