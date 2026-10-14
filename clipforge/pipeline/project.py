"""Ein Projekt = ein VOD mit seinem Analysezustand. Alles wird laufend gespeichert,
damit abgebrochene Analysen fortgesetzt werden können."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
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

    # Tatsächliche API-Kosten (über alle Läufe dieses VODs aufsummiert)
    def add_cost(self, kind: str, provider: str, model: str, usd: float | None, input_tokens: int = 0,
                 output_tokens: int = 0, minutes: float = 0.0, calls: int = 0) -> None:
        """kind: "ki" (Bewertung) oder "transkription". usd=None, wenn der Preis des Modells unbekannt ist."""
        costs = self.state.setdefault("costs", {"items": []})
        costs["items"].append({
            "kind": kind, "provider": provider, "model": model, "usd": None if usd is None else round(usd, 5),
            "input_tokens": input_tokens, "output_tokens": output_tokens, "minutes": round(minutes, 2),
            "calls": calls, "at": dt.datetime.now().isoformat(timespec="seconds"),
        })
        self.save()

    @property
    def cost_summary(self) -> dict:
        items = self.state.get("costs", {}).get("items", [])
        known = [i["usd"] for i in items if i["usd"] is not None]
        return {
            "usd": round(sum(known), 4),
            "complete": all(i["usd"] is not None for i in items),  # False: ein Modell ohne bekannten Preis
            "input_tokens": sum(i["input_tokens"] for i in items),
            "output_tokens": sum(i["output_tokens"] for i in items),
            "minutes": round(sum(i["minutes"] for i in items), 1),
            "by_kind": {k: round(sum(i["usd"] or 0 for i in items if i["kind"] == k), 4)
                        for k in ("transkription", "ki")},
            "items": items,
        }

    def cost_text(self) -> str:
        c = self.cost_summary
        if not c["items"]:
            return "0,00 $ (nur lokal)"
        txt = f"{c['usd']:.2f} $".replace(".", ",")
        if not c["complete"]:
            txt += " + unbekannt"
        parts = []
        if c["minutes"]:
            parts.append(f"{c['minutes']:.0f} Audiominuten")
        if c["input_tokens"] or c["output_tokens"]:
            parts.append(f"{(c['input_tokens'] + c['output_tokens']) / 1000:.1f}k Tokens")
        return txt + (f" ({', '.join(parts)})" if parts else "")

    @staticmethod
    def total_cost_all() -> tuple[float, int]:
        """Summe über alle Projekte: (US-$, Anzahl VODs mit Kosten)."""
        total, n = 0.0, 0
        for p in Project.all():
            c = p.cost_summary
            if c["items"]:
                total += c["usd"]
                n += 1
        return round(total, 4), n

    @property
    def offset(self) -> float:
        """Start des geladenen Ausschnitts im Original-VOD (0 bei vollständigen VODs)."""
        if "offset" in self.state:
            return float(self.state["offset"])
        m = re.search(r"#(\d+)-(\d+)$", self.state.get("ref", ""))
        return float(m.group(1)) if m else 0.0

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
