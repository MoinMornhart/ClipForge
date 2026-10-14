"""Clip-Kandidaten aus Triggern bilden."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

from clipforge.core.settings import Settings
from clipforge.pipeline.transcribe import Word
from clipforge.pipeline.triggers import Hit

STATUS_NEW, STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPORTED = "neu", "freigegeben", "verworfen", "exportiert"


@dataclass
class Clip:
    start: float
    end: float
    trigger_time: float
    reasons: list[str]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    score: float | None = None  # Viral-Score 1–10 (von der KI)
    title: str = ""
    description: str = ""
    hashtags: list[str] = field(default_factory=list)
    layout: str = ""
    status: str = STATUS_NEW
    ai_reason: str = ""
    export_path: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Clip":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def text_between(words: list[Word], start: float, end: float) -> str:
    return " ".join(w.w for w in words if start <= w.s < end)


def candidates_from_hits(hits: list[Hit], duration: float, s: Settings) -> list[Clip]:
    """Einfache Grenzen: ein Sprachbefehl bezieht sich auf das, was VORHER passiert ist.
    Die KI-Bewertung (Meilenstein 2) verfeinert die Grenzen danach inhaltlich."""
    raw: list[Clip] = []
    for h in hits:
        pre = h.trigger.pre if h.trigger else 30.0
        post = h.trigger.post if h.trigger else 4.0
        start = max(0.0, h.start - pre)
        end = min(duration, h.time + post)
        if end - start < s.min_clip_seconds:
            missing = s.min_clip_seconds - (end - start)
            start = max(0.0, start - missing)
        if end - start > s.max_clip_seconds:
            start = end - s.max_clip_seconds
        raw.append(Clip(start=round(start, 2), end=round(end, 2), trigger_time=h.time, reasons=[h.reason]))

    merged: list[Clip] = []
    for c in sorted(raw, key=lambda c: c.start):
        if merged:
            last = merged[-1]
            if c.start - last.end <= s.merge_gap_seconds and max(c.end, last.end) - last.start <= s.max_clip_seconds:
                last.end = max(last.end, c.end)
                last.reasons += [r for r in c.reasons if r not in last.reasons]
                continue
        merged.append(c)
    return merged
