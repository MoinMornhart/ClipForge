# ClipForge

Windows-Desktop-App, die komplette Twitch-VODs mit KI analysiert, die besten Momente findet und sie fertig im Hochformat (9:16) für TikTok, YouTube Shorts und Instagram Reels schneidet.

> **Stand: Meilenstein 1 (Grundgerüst).** VOD laden → Transkript → Trigger „clip das“ → 9:16-Export funktioniert über die Kommandozeile bzw. `start.bat`. KI-Bewertung, Facecam-Layouts, Untertitel und Oberfläche folgen. Plan: [docs/PLAN.md](docs/PLAN.md)

## Was schon geht

- **Import:** Twitch-VOD per Link (yt-dlp, abgebrochene Downloads werden fortgesetzt) oder lokale MP4/MKV-Datei
- **Transkription:** lokal mit faster-whisper auf der NVIDIA-GPU (CUDA), Wortzeitstempel, Verarbeitung in 10-Minuten-Abschnitten. Wird die Analyse abgebrochen, geht sie beim nächsten Start an derselben Stelle weiter. Alternativ: OpenAI-Whisper-API
- **Trigger:** Sprachbefehle wie „clip das“, „clip it“, „das muss ein Clip werden“ mit unscharfer Erkennung („Klipp des“ wird auch gefunden)
- **Export:** 1080×1920, H.264/H.265, 30/60 fps, GPU-beschleunigt per NVENC (Rückfall auf CPU), dazu je eine `.txt` mit Titel, Beschreibung und Hashtags

## Installation

Voraussetzungen: Windows 10/11, Python 3.11, NVIDIA-Grafikkarte empfohlen (läuft auch ohne, dann langsamer).

1. Repo herunterladen: `git clone https://github.com/<user>/ClipForge.git`
2. **`install.bat`** doppelklicken. Das richtet alles ein:
   - Python-Umgebung unter `%LOCALAPPDATA%\ClipForge\venv`
   - alle Pakete inkl. CUDA-Bibliotheken
   - FFmpeg über winget, falls es fehlt
3. **`start.bat`** doppelklicken, VOD-Link oder Datei eingeben bzw. hineinziehen. Fertig.

Beim ersten Start lädt ClipForge das Whisper-Modell (`large-v3-turbo`, ca. 1,6 GB) herunter.

## Bedienung über die Kommandozeile

```bat
start.bat "https://www.twitch.tv/videos/123456789"
```

oder direkt:

```bat
%LOCALAPPDATA%\ClipForge\venv\Scripts\python.exe -m clipforge analyze "D:\VODs\stream.mp4"
%LOCALAPPDATA%\ClipForge\venv\Scripts\python.exe -m clipforge run "D:\VODs\stream.mp4" --layout crop
%LOCALAPPDATA%\ClipForge\venv\Scripts\python.exe -m clipforge check
```

## Speicherorte

| Was | Wo |
|---|---|
| Programm | dieser Ordner |
| Python-Umgebung, Whisper-Modelle | `%LOCALAPPDATA%\ClipForge\` |
| Einstellungen | `%LOCALAPPDATA%\ClipForge\settings.json` |
| Analysezustand je VOD | `%LOCALAPPDATA%\ClipForge\projects\` |
| Logdatei | `%LOCALAPPDATA%\ClipForge\logs\clipforge.log` |
| Heruntergeladene VODs | `Videos\ClipForge\VODs` |
| Exportierte Clips | `Videos\ClipForge\Export` (einstellbar) |

Große Dateien liegen absichtlich nicht im Programmordner, damit iCloud/OneDrive keine Gigabytes synchronisieren.

## API-Keys

API-Keys (Anthropic, OpenAI) werden ausschließlich in der **Windows-Anmeldeinformationsverwaltung** gespeichert (über `keyring`), nie in Dateien oder im Repo. Für die lokale Transkription braucht ClipForge keinen Key, nur die KI-Bewertung braucht später einen.

## Aktualisieren

`update.bat` doppelklicken. Holt die neueste Version von GitHub und aktualisiert die Pakete. Später gibt es dafür auch einen Knopf in der App.

## Technik

- **Backend:** Python 3.11, FFmpeg, yt-dlp, faster-whisper (CTranslate2/CUDA), rapidfuzz, MediaPipe/OpenCV
- **Oberfläche:** PySide6 (Qt 6) mit Dark Mode. Gewählt, weil es am stabilsten ist: ein einziger Python-Prozess ohne Brücke zwischen Node und Python, eingebauter Videoplayer (QtMultimedia), direkter Zugriff auf alle Analysebibliotheken.

## Lizenz

MIT, siehe [LICENSE](LICENSE).

---

Verfasst mit Claude 🤖
