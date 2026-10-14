"""Ein Projekt = ein VOD mit seinem Analysezustand. Alles wird laufend gespeichert,
damit abgebrochene Analysen fortgesetzt werden können."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from clipforge.core.paths import projects_dir
from clipforge.pipeline.clips import Clip
from clipforge.pipeline.importer import Source
from clipforge.pipeline.transcribe import Word


def project_id(source_ref: str) -> str:
    return hashlib.sha1(source_ref.strip().lower().encode()).hexdigest()[:12]


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


class Project:
    def __init__(self, pid: str):
        self.id = pid
        self.dir = projects_dir() / pid
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state: dict = {}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))

    @classmethod
    def for_source(cls, ref: str) -> "Project":
        p = cls(project_id(ref))
        p.state.setdefault("ref", ref)
        p.state.setdefault("created", dt.datetime.now().isoformat(timespec="seconds"))
        p.state.setdefault("steps", {})
        p.save()
        return p

    @classmethod
    def all(cls) -> list["Project"]:
        out = [cls(d.name) for d in projects_dir().iterdir() if (d / "state.json").exists()]
        return sorted(out, key=lambda p: p.state.get("created", ""), reverse=True)

    # Pfade
    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    @property
    def transcript_path(self) -> Path:
        return self.dir / "transcript.json"

    @property
    def clips_path(self) -> Path:
        return self.dir / "clips.json"

    def save(self) -> None:
        _write_json(self.state_path, self.state)

    # Schritte
    def done(self, step: str) -> bool:
        return bool(self.state.get("steps", {}).get(step))

    def mark(self, step: str, value=True) -> None:
        self.state.setdefault("steps", {})[step] = value
        self.save()

    # Daten
    @property
    def source(self) -> Source | None:
        s = self.state.get("source")
        if not s:
            return None
        return Source(**{**s, "path": Path(s["path"])})

    def set_source(self, src: Source, media: dict) -> None:
        self.state["source"] = {**asdict(src), "path": str(src.path)}
        self.state["media"] = media
        self.save()

    @property
    def media(self) -> dict:
        return self.state.get("media", {})

    def save_words(self, words: list[Word]) -> None:
        _write_json(self.transcript_path, [asdict(w) for w in words])

    def load_words(self) -> list[Word]:
        if not self.transcript_path.exists():
            return []
        return [Word(**w) for w in json.loads(self.transcript_path.read_text(encoding="utf-8"))]

    def save_clips(self, clips: list[Clip]) -> None:
        _write_json(self.clips_path, [c.to_dict() for c in clips])

    def load_clips(self) -> list[Clip]:
        if not self.clips_path.exists():
            return []
        return [Clip.from_dict(c) for c in json.loads(self.clips_path.read_text(encoding="utf-8"))]
