"""Einstellungen als JSON unter %LOCALAPPDATA%\\ClipForge\\settings.json.

API-Keys stehen hier NICHT drin – die liegen in der Windows-Anmeldeinformationsverwaltung
(siehe clipforge.core.secrets).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from clipforge.core.log import get_logger
from clipforge.core.jsonio import write_json
from clipforge.core.paths import app_dir, default_download_dir, default_export_dir

log = get_logger("settings")

DEFAULT_VOICE_TRIGGERS = [
    "clip das",
    "clip it",
    "clippt das",
    "das muss ein clip werden",
    "das ist ein clip",
    "clip that",
]


@dataclass
class Trigger:
    phrase: str
    enabled: bool = True
    # Mindestähnlichkeit 0–100 für die unscharfe Erkennung
    threshold: int = 80
    # Sekunden vor/nach dem Trigger, falls die KI-Bewertung nicht aktiv ist
    pre: float = 30.0
    post: float = 4.0


@dataclass
class Settings:
    # Transkription
    transcription_backend: str = "local"  # Schlüssel aus providers.STT_PROVIDERS
    stt_api_model: str = ""  # leer = Standardmodell des Anbieters
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"  # "cuda" | "cpu"
    whisper_batched: bool = True  # Stapel-Modus auf der GPU (≈4× schneller)
    language: str = ""  # "" = automatisch erkennen (sonst z. B. "de", "en"); fest = etwas genauer
    chunk_minutes: float = 10.0

    # Trigger
    voice_triggers: list[Trigger] = field(default_factory=lambda: [Trigger(p) for p in DEFAULT_VOICE_TRIGGERS])
    custom_keywords: list[Trigger] = field(default_factory=list)
    use_chat: bool = True  # nur bei Twitch-/Kick-Links
    chat_reactions: list[str] = field(default_factory=lambda: [
        "KEKW", "LUL", "OMEGALUL", "LMAO", "LOL", "xD", "PogChamp", "Pog", "POGGERS", "!clip", "clip", "WTF",
        "monkaS", "😂", "💀", "🔥", "HAHAHA", "haha"])
    chat_sensitivity: float = 3.0  # kleiner = mehr Chat-Spitzen
    chat_min_messages: int = 8  # Mindestanzahl Nachrichten in 10 s
    use_audio_peaks: bool = True
    audio_above_db: float = 11.0  # so viel lauter als üblich (im Test: 10 dB zu empfindlich, 12 dB zu streng)
    audio_min_level_db: float = -22.0

    # Clips
    min_clip_seconds: float = 15.0
    max_clip_seconds: float = 60.0
    merge_gap_seconds: float = 10.0

    # KI-Bewertung
    ai_enabled: bool = True
    llm_provider: str = "anthropic"  # Schlüssel aus providers.LLM_PROVIDERS
    llm_model: str = "claude-opus-5-5"
    llm_base_url: str = ""  # nur für "custom" bzw. abweichende lokale Ports
    llm_effort: str = "medium"  # nur Claude: low | medium | high
    context_before: float = 120.0  # Sekunden Transkript vor dem Trigger, die die KI sieht
    context_after: float = 30.0
    min_score: int = 0  # Clips unter diesem Viral-Score automatisch verwerfen (0 = aus)
    # Eigene Preise je Modell: {"modell": [Eingabe $/1M, Ausgabe $/1M]}
    price_overrides: dict = field(default_factory=dict)
    # Höchstlänge je Plattform (Sekunden); der kleinste aktive Wert begrenzt die Clips
    platforms: dict = field(default_factory=lambda: {"TikTok": 180, "YouTube Shorts": 180, "Instagram Reels": 180})

    # Export
    download_dir: str = field(default_factory=lambda: str(default_download_dir()))
    export_dir: str = field(default_factory=lambda: str(default_export_dir()))
    codec: str = "h264"  # "h264" | "hevc"
    fps: int = 30  # alt, wird nicht mehr genutzt (siehe export_fps)
    cam_size: int = 40  # Facecam oben: höchstens so viel % der Bildhöhe (25–50), Rest ist Gameplay
    export_fps: int = 0  # 0 = wie die Quelle (höchstens 60), sonst 30/60
    use_nvenc: bool = True
    default_layout: str = "auto"  # auto | stack | corner | cam | blur | crop
    smart_crop: bool = True
    track_face: bool = True
    facecam_profiles: dict = field(default_factory=dict)  # Streamer → Facecam (normiert)

    # Untertitel und Feinschliff
    subtitles: dict = field(default_factory=dict)  # siehe pipeline.subtitles.SubtitleStyle
    loudnorm: bool = True  # -14 LUFS
    trim_silence: bool = True
    watermark: str = ""
    watermark_pos: str = "oben-rechts"
    watermark_opacity: float = 0.8
    intro: str = ""
    outro: str = ""
    filename_pattern: str = "{date}_{streamer}_{title}"
    ffmpeg_path: str = ""

    @staticmethod
    def path() -> Path:
        return app_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        p = cls.path()
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("kein Objekt")
            known = {f.name for f in fields(cls)}
            data = {k: v for k, v in raw.items() if k in known}
            tkeys = {f.name for f in fields(Trigger)}
            for key in ("voice_triggers", "custom_keywords"):
                if key in data:
                    data[key] = [Trigger(**{k: v for k, v in t.items() if k in tkeys}) for t in data[key]
                                 if isinstance(t, dict) and t.get("phrase")]
            s = cls(**data)
        except (OSError, ValueError, TypeError) as e:
            # kaputte Datei sichern statt beim nächsten Speichern Profile/Trigger endgültig zu verlieren
            bak = p.with_suffix(".json.bak")
            try:
                p.replace(bak)
            except OSError:
                pass
            log.warning("Einstellungen nicht lesbar (gesichert als %s), nutze Standardwerte: %s", bak.name, e)
            return cls()
        if not str(s.export_dir).strip():
            s.export_dir = str(default_export_dir())
        if not str(s.download_dir).strip():
            s.download_dir = str(default_download_dir())
        return s

    def save(self) -> None:
        if not str(self.export_dir).strip():  # leeres Feld: nie ins aktuelle Arbeitsverzeichnis exportieren
            self.export_dir = str(default_export_dir())
        if not str(self.download_dir).strip():
            self.download_dir = str(default_download_dir())
        write_json(self.path(), asdict(self), indent=2)

    @property
    def effective_max_seconds(self) -> float:
        caps = [v for v in self.platforms.values() if v]
        return min([self.max_clip_seconds, *caps])

    def all_triggers(self) -> list[tuple[str, Trigger]]:
        return [("Sprachbefehl", t) for t in self.voice_triggers if t.enabled] + [
            ("Schlüsselbegriff", t) for t in self.custom_keywords if t.enabled
        ]
