"""Einstellungen als JSON unter %LOCALAPPDATA%\\ClipForge\\settings.json.

API-Keys stehen hier NICHT drin – die liegen in der Windows-Anmeldeinformationsverwaltung
(siehe clipforge.core.secrets).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from clipforge.core.log import get_logger
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
    transcription_backend: str = "local"  # "local" (faster-whisper) | "openai"
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"  # "cuda" | "cpu"
    language: str = "de"  # "" = automatisch
    chunk_minutes: float = 10.0

    # Trigger
    voice_triggers: list[Trigger] = field(default_factory=lambda: [Trigger(p) for p in DEFAULT_VOICE_TRIGGERS])
    custom_keywords: list[Trigger] = field(default_factory=list)
    use_chat: bool = False
    use_audio_peaks: bool = False

    # Clips
    min_clip_seconds: float = 15.0
    max_clip_seconds: float = 60.0
    merge_gap_seconds: float = 10.0

    # KI
    llm_provider: str = "anthropic"  # "anthropic" | "openai" | "none"
    llm_model: str = "claude-sonnet-5-5"

    # Export
    download_dir: str = field(default_factory=lambda: str(default_download_dir()))
    export_dir: str = field(default_factory=lambda: str(default_export_dir()))
    codec: str = "h264"  # "h264" | "hevc"
    fps: int = 30
    use_nvenc: bool = True
    default_layout: str = "blur"
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
        except (OSError, json.JSONDecodeError) as e:
            log.warning("Einstellungen konnten nicht gelesen werden, nutze Standardwerte: %s", e)
            return cls()
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in raw.items() if k in known}
        for key in ("voice_triggers", "custom_keywords"):
            if key in data:
                data[key] = [Trigger(**t) for t in data[key]]
        return cls(**data)

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")

    def all_triggers(self) -> list[tuple[str, Trigger]]:
        return [("Sprachbefehl", t) for t in self.voice_triggers if t.enabled] + [
            ("Schlüsselbegriff", t) for t in self.custom_keywords if t.enabled
        ]
