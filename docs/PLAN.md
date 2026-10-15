# ClipForge – Plan

Live-Fortschritt (privat): https://claude.ai/artifact/SnkPaTEaAyFcUNNtkPsCXU

| # | Meilenstein | Inhalt | Stand |
|---|---|---|:---:|
| M0 | Projekt-Setup | Repo, Struktur, Python-Umgebung, FFmpeg, CUDA-Check | ✅ |
| M1 | Grundgerüst | VOD laden (Twitch, Kick, YouTube, Datei) → Transkript → Trigger „clip das“ → 9:16-Export | ✅ |
| M2 | KI-Bewertung | 11 Anbieter, Start/Ende per Zeilenwahl, Score, Titel, Hashtags, Kostenschätzung | ✅ |
| M3 | Weitere Trigger | Eigene Begriffe (unscharf), Twitch- und Kick-Chat, Chat- und Lautstärkespitzen | ✅ |
| M4 | Facecam & Layouts | Facecam-Erkennung, Profile, 5 Layouts, intelligenter Ausschnitt, Balken entfernen, Nachführung | ✅ |
| M5 | Untertitel & Feinschliff | TikTok-Untertitel, Stil, Korrektur, -14 LUFS, Stille kürzen, Intro/Outro, Wasserzeichen | ✅ |
| M6 | Oberfläche | PySide6, Dark Mode, Clip-Editor, Facecam-Dialog, Einstellungen, Update-Knopf | ✅ |
| M7 | Auslieferung | Stapel-Export, tatsächliche API-Kosten pro VOD, Symbol, Desktop-Verknüpfung, Gesamttest | 🟡 |

## Architektur

```
clipforge/
  core/       Einstellungen, Logs, Fehler (deutsch), API-Keys (keyring), FFmpeg, CUDA, Anbieter, Updates
  pipeline/   importer → transcribe → facecam → chat/audio/triggers → clips → llm → layouts/subtitles → render
              project.py speichert den Zustand je VOD (Fortsetzen nach Abbruch, tatsächliche Kosten)
              runner.py steuert den Ablauf für CLI und Oberfläche
  ui/         PySide6-Oberfläche: app, import_page, clips_page, settings_page, widgets, worker, theme
  cli.py      Kommandozeile
```

## Entscheidungen

- **Oberfläche PySide6:** ein Python-Prozess, keine Brücke zu Node, eingebauter Videoplayer, am stabilsten.
- **GPU:** RTX 5060 Ti (Blackwell). CTranslate2 4.8 + cuBLAS/cuDNN aus pip laufen darauf.
- **Whisper `large-v3-turbo`:** fast so genau wie `large-v3`, deutlich schneller.
- **Lange VODs:** 10-Minuten-Abschnitte mit 3 s Überlappung, jeder Abschnitt wird sofort gespeichert.
- **KI schneidet über Zeilennummern:** Die KI wählt Start- und Endzeile, die Zeiten kommen aus den Wortzeitstempeln.
- **Twitch-Chat:** eigene Abfrage über die Zeitposition (`chat-downloader` ist mit der aktuellen Twitch-Schnittstelle kaputt).
- **Kick:** yt-dlp + curl_cffi gegen Cloudflare, Chat parallel in Abschnitten.
- **Facecam-Rahmen:** Übergang vom ruhigen Kamerabild zum wechselnden Spielbild, nicht stärkste Kante (der Raum hinter dem Streamer ist auch ruhig).
