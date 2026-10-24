# Stand: ClipForge (Übergabe 2026-10-24)

## Was es ist
Windows-Desktop-App (Python 3.11 + PySide6, NVIDIA-GPU), die lange Twitch-/Kick-VODs (auch YouTube/Datei) mit KI analysiert, die besten Momente findet und sie fertig als Hochkant-Clips (9:16) für TikTok, Shorts und Reels schneidet – mit Facecam-Layout, TikTok-Untertiteln, Titel und Hashtags.

## Aktueller Stand
- Aktuelle Version **0.4.2** (Release 21.10., Setup.exe auf GitHub). Weitgehend fertig und nutzbar.
- Meilensteine M0–M6 laut `docs/PLAN.md` erledigt: Import (Twitch, Kick, YouTube, Datei, nur Ausschnitt), lokale Transkription (faster-whisper `large-v3-turbo`, GPU), Trigger (Sprachbefehle, eigene Begriffe, Chat- und Lautstärkespitzen), KI-Bewertung mit 12 Anbietern (u. a. Claude, NVIDIA NIM, Ollama) inkl. Kostenschätzung + tatsächlicher Kosten + Ausgabenlimit, Facecam/Handcam/Objektverfolgung (auch ohne Gesicht, z. B. Bär), mehrere Layouts + eigene Layouts mit Facecam-Editor, Spiel-Erkennung mit passendem Ausschnitt, Untertitel, -14 LUFS, Oberfläche mit Dark Mode, Crash-Schutz, Update-Funktion.
- M7 „Auslieferung“ laut PLAN noch 🟡 (Stapel-Export, Kosten, Symbol, Verknüpfung, Gesamttest sind gebaut – was genau noch fehlt: **unklar**).
- 307 automatische Tests (`tests/`, letzter kompletter Lauf am 21.10. mit 0.4.2 grün; README-Badge nennt noch 232).

## Zuletzt gemacht
- 2026-10-24 Cloud-Start-Hook (Philips Skills automatisch in Cloud-Sitzungen); STAND.md als Übergabe; ClipForge hat jetzt eine feste Cloud-Sitzung.
- 2026-10-21 **0.4.2** (PR #16 gemergt, Release mit `ClipForge-Setup-0.4.2.exe`): Absturz „Internal C++ object (QLabel) already deleted“ behoben – bei schnellen Seitenwechseln (`ui/anim.py`, AnimatedStack) und im Layout-Editor (`ui/clips_page.py`), mit Regressionstest.
- 2026-10-20 **0.4.1** (PR #15 gemergt, Release mit `ClipForge-Setup-0.4.1.exe`):
  - Eigenes Layout „Facecam oben, Gameplay unten“: Facecam-Größe per Regler, Ecken oder Mausrad (10–75 % der Höhe), vom oberen Rand bis zur Facecam-Unterkante ist das Spiel unscharf, darunter das Gameplay. Eine Seitenauswahl (oben/unten/links/rechts) wollte Philip ausdrücklich **nicht**.
  - Fenstermodus: Knöpfe unter dem Player überlagern sich nicht mehr (FlowLayout bricht Zeilen um, Bedienbereich scrollt bei wenig Höhe).
  - Neuer GitHub-Workflow „Release“ (`.github/workflows/release.yml`): baut die Setup.exe auf einem Windows-Runner und veröffentlicht das Release.
- 2026-10-17 0.4.0: Spiel erkennen + passender Ausschnitt, eigene Layouts
- 2026-10-15 0.3.x: Clips nach viralen Kriterien, Ausgabenlimit, Facecam-Größe einstellbar, NVIDIA NIM als Anbieter

## Offen / Nächste Schritte
- **Bekannter Fehler:** seltener Absturz beim Beenden („pure virtual method called“), gab es schon in 0.4.0. Ursache noch nicht gesucht.
- **Test mit echtem Stream unter Windows** steht aus (0.4.1/0.4.2, auch die auf GitHub gebaute Setup.exe).
- Issue #1: 14 Abhängigkeiten aktualisieren (Liste von VibeWorks, Stand 23.10.; Major-Sprünge bei `anthropic` 2.x, `numpy` 2.x, `opencv-python` 5.x, `pytest` 9, `pytest-cov` 7) – vorsichtig einzeln testen.
- Restpunkte von M7 klären (PLAN.md).
- Laut README nicht getestet: KI-Bewertung mit Claude/bezahlten Anbietern an echten VODs (nur gegen nachgebauten Server) und ein komplettes 8-Stunden-VOD am Stück.
- Kleinkram: README nennt noch 232 statt 307 Tests; die Fortschrittsseite (Link in PLAN.md) endet bei 0.4.0.

## Wichtige Infos
- Technik: Python 3.11, PySide6, FFmpeg (NVENC), yt-dlp + curl_cffi (Kick/Cloudflare), faster-whisper/CTranslate2 (CUDA), MediaPipe/OpenCV, keyring für API-Keys.
- Start (Quellcode): `install.bat` (venv unter `%LOCALAPPDATA%\ClipForge\venv`), dann `start.bat`; `update.bat` für Git-Updates. CLI: `python -m clipforge run <Link|Datei>`.
- Tests: `pytest` im Projektordner.
- **Release-Ablauf:** Version in `clipforge/__init__.py` (`__version__`) und Notizen in `installer/RELEASE.md` anpassen (Titel muss die Version enthalten) → mergen → GitHub **Actions → Release → Run workflow** auf `main`. Baut die Setup.exe (PyInstaller + Inno Setup) und veröffentlicht `v<Version>` mit `ClipForge-Setup-<Version>.exe`; überschreibt nie ein bestehendes Release. Lokal: `installer\build.ps1`.
- Update in der App: Einstellungen → Über & Update → „Nach Updates suchen“ → „Jetzt aktualisieren“. Lädt nur veröffentlichte Releases von GitHub (auch ohne Git) – ein Branch allein kommt nie in der App an.
- Daten: Einstellungen/Projekte/Logs unter `%LOCALAPPDATA%\ClipForge\`, VODs/Exporte unter `Videos\ClipForge\`.
- Architektur und Entscheidungen: `docs/PLAN.md`. Live-Fortschrittsseite (privat) ist dort verlinkt.

## Philips Vorgaben
- **Updates immer gleich als Release rausbringen**, nicht nur als Branch: mergen, Release-Workflow starten, prüfen, dass die Setup.exe dranhängt.
- Bilder/Screenshots schicken, bevor etwas als fertig gilt oder gepusht wird.
- Nur bauen, was Philip will – eingegrenzten Umfang einhalten.
- `Verfasst mit Claude 🤖` steht in README-Fußzeile, App-Info und Release-Notizen; in Commits meist auch (nicht in allen) → in Git-Texten beibehalten.
- Update-Funktion: **ja**.
- Repo: **öffentlich** (MoinMornhart/ClipForge).
- Oberfläche und Fehlermeldungen auf Deutsch.

---
Diese Datei wird beim Arbeiten aktuell gehalten: nach jedem größeren Schritt „Aktueller Stand“ und „Offen“ anpassen.
