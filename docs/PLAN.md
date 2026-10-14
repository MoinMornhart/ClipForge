# ClipForge – Plan

Live-Fortschritt: https://claude.ai/artifact/SnkPaTEaAyFcUNNtkPsCXU

| # | Meilenstein | Inhalt |
|---|---|---|
| M0 | Projekt-Setup | Repo, Struktur, Python-Umgebung, FFmpeg, CUDA-Check |
| M1 | Grundgerüst | VOD laden → Transkript → Trigger „clip das“ → einfacher 9:16-Export, getestet |
| M2 | KI-Bewertung | LLM (Anthropic/OpenAI) bestimmt Start/Ende mit Kontext, Viral-Score 1–10, Titel, Beschreibung, Hashtags; Kostenschätzung vor der Analyse |
| M3 | Weitere Trigger | Eigene Schlüsselbegriffe (unscharf), Twitch-Chat (KEKW, LUL, !clip), Chat- und Lautstärkespitzen |
| M4 | Facecam & Layouts | MediaPipe-Gesichtserkennung, manuelles Rechteck + Profil pro Streamer, 4 Layouts, intelligenter Gameplay-Ausschnitt, sanfte Nachführung |
| M5 | Untertitel & Feinschliff | Wortgenaue TikTok-Untertitel mit aktivem Wort, Stil einstellbar, Transkript korrigierbar, -14 LUFS, Stille kürzen, Intro/Outro, Wasserzeichen |
| M6 | Oberfläche | PySide6, Dark Mode, Drag & Drop, Clip-Liste mit Vorschau, Schieberegler, Layoutwahl, Freigeben/Verwerfen, Einstellungen + API-Keys |
| M7 | Export & Auslieferung | Stapel-Export, Dateinamen-Muster, Update-Knopf in der App, README, Ein-Klick-Start |

## Architektur

```
clipforge/
  core/       Einstellungen, Logs, Fehler (deutsch), API-Keys (keyring), FFmpeg, CUDA
  pipeline/   importer → transcribe → triggers → clips → (llm) → render
              project.py speichert den Zustand je VOD (Fortsetzen nach Abbruch)
              runner.py steuert den Ablauf für CLI und Oberfläche
  ui/         PySide6-Oberfläche (M6)
  cli.py      Kommandozeile
```

## Bekannte Entscheidungen

- GPU ist eine RTX 5060 Ti (Blackwell). CTranslate2 4.8 + cuBLAS/cuDNN aus pip laufen darauf, getestet.
- Whisper-Modell `large-v3-turbo`: fast so genau wie `large-v3`, deutlich schneller.
- Lange VODs werden in 10-Minuten-Abschnitten mit 3 s Überlappung transkribiert; jeder Abschnitt wird sofort gespeichert.
