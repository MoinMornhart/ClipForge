"""Trigger-Erkennung im Transkript mit unscharfem Abgleich.

Damit „klipp das“, „clipt das“ oder „clip des“ trotzdem als „clip das“ erkannt werden,
werden Transkript und Suchbegriff gleich normalisiert (Kleinschreibung, Umlaute, k→c,
doppelte Buchstaben) und dann per Levenshtein-Ähnlichkeit (rapidfuzz) verglichen.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

from clipforge.core.settings import Trigger
from clipforge.pipeline.transcribe import Word

_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_REPEAT = re.compile(r"(.)\1+")


def normalize(text: str) -> str:
    t = text.lower().translate(_UMLAUT)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    t = _NON_ALNUM.sub(" ", t)
    t = t.replace("ck", "k").replace("k", "c").replace("ph", "f")
    t = _REPEAT.sub(r"\1", t)
    return " ".join(t.split())


# Füllwörter zählen nicht als Kern eines Begriffs: „das ist ein clip“ muss ein Wort wie „clip“ enthalten
_STOP = {normalize(w) for w in (
    "das des dis der die den dem ein eine einen einer ist es is it that this the a an muss mus werden wird "
    "und and jetzt mal bitte doch so ja oh"
).split()}
ANCHOR_MIN = 75  # Mindestähnlichkeit je Kernwort


def anchors(phrase_norm: str) -> list[str]:
    toks = phrase_norm.split()
    core = [t for t in toks if t not in _STOP]
    return core or toks


def has_anchors(cand: str, core: list[str]) -> bool:
    toks = cand.split()
    return all(any(fuzz.ratio(a, t) >= ANCHOR_MIN for t in toks) for a in core)


@dataclass
class Hit:
    kind: str  # "Sprachbefehl" | "Schlüsselbegriff" | "Chat" | "Lautstärke"
    phrase: str
    matched: str
    time: float  # Ende des erkannten Ausdrucks im VOD (Sekunden)
    start: float
    score: float  # Ähnlichkeit 0–100
    trigger: Trigger | None = None

    @property
    def reason(self) -> str:
        if self.matched and normalize(self.matched) != normalize(self.phrase):
            return f"{self.kind}: „{self.matched}“ (≈ {self.phrase}, {self.score:.0f} %)"
        return f"{self.kind}: „{self.phrase}“"


def find_hits(words: list[Word], triggers: list[tuple[str, Trigger]], dedupe_seconds: float = 8.0) -> list[Hit]:
    norm = [normalize(w.w) for w in words]
    hits: list[Hit] = []
    for kind, trig in triggers:
        target = normalize(trig.phrase)
        if not target:
            continue
        n = len(target.split())
        core = anchors(target)
        # Kurze Begriffe brauchen eine höhere Schwelle, sonst passt fast alles
        threshold = trig.threshold if len(target) >= 6 else max(trig.threshold, 92)
        lengths = sorted({max(1, n - 1), n, n + 1})
        found: list[Hit] = []
        for i in range(len(words)):
            if not norm[i]:
                continue
            best = None
            for L in lengths:
                if i + L > len(words):
                    continue
                cand = " ".join(x for x in norm[i:i + L] if x)
                if not has_anchors(cand, core):
                    continue
                score = fuzz.ratio(cand, target)
                if best is None or score > best[0]:
                    best = (score, L)
            if best and best[0] >= threshold:
                score, L = best
                matched = " ".join(w.w for w in words[i:i + L])
                found.append(Hit(kind, trig.phrase, matched, words[i + L - 1].e, words[i].s, score, trig))
        # Mehrfachtreffer desselben Begriffs kurz hintereinander zusammenfassen (bester bleibt)
        for h in sorted(found, key=lambda h: h.time):
            if hits and hits[-1].phrase == h.phrase and h.time - hits[-1].time < dedupe_seconds:
                if h.score > hits[-1].score:
                    hits[-1] = h
                continue
            hits.append(h)
    # Verschiedene Begriffe, die dieselbe Stelle treffen („clip das“ und „clippt das“), nur einmal zählen
    hits.sort(key=lambda h: (h.start, -h.score))
    unique: list[Hit] = []
    for h in hits:
        if unique and h.start <= unique[-1].time and h.time >= unique[-1].start:
            if h.score > unique[-1].score:
                unique[-1] = h
            continue
        unique.append(h)
    return unique
