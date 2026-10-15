"""KI-Highlight-Bewertung.

Die KI bekommt das Transkript rund um einen Trigger als nummerierte Zeilen mit Zeitstempeln
und wählt Start- und Endzeile. Die genauen Schnittpunkte rechnet ClipForge danach aus den
Wortzeitstempeln aus. So kann das Modell keine Zeiten erfinden, und die Clips beginnen und
enden immer an Satzgrenzen.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from clipforge.core import secrets
from clipforge.core.errors import ClipForgeError
from clipforge.core.log import get_logger
from clipforge.core.providers import LLM_PROVIDERS, STT_PRICES_PER_MIN, STT_PROVIDERS, Provider, llm_price
from clipforge.core.settings import Settings
from clipforge.pipeline.clips import Clip
from clipforge.pipeline.transcribe import Word

log = get_logger("llm")

# Modelle mit serverseitigem Ausweichmodell bei Ablehnung (Claude API)
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_EFFORT_MODELS = re.compile(r"^claude-(opus|sonnet|haiku|fable|mythos)-(5|4-[678])")

# Clip-Arten, die der Nutzer bevorzugen kann (Schlüssel → Anzeigename, Beschreibung für die KI)
CLIP_STYLES: dict[str, tuple[str, str]] = {
    "lustig": ("Lustig", "Pointe, Situationskomik, unerwartete oder absurde Reaktion"),
    "ragebait": ("Ragebait", "Momente, die zum Kommentieren reizen: steile These, Ausraster/Rage, unfaire "
                             "Situation, offensichtlicher Fehler, Streit oder Trashtalk – polarisierend, aber ohne "
                             "Menschen oder Gruppen herabzuwürdigen"),
    "rp": ("RP / Story", "Rollenspiel-Szenen (z. B. GTA RP): In-Character-Dialoge, Improvisation, absurde "
                         "Situationen und ihre Folgen; brauchen oft ein, zwei Sätze Vorlauf, damit man sie versteht"),
    "skill": ("Skill / Clutch", "Clutch, knapper Sieg, spektakulärer Spielzug"),
    "fail": ("Fails", "Missgeschick, Pech, peinlicher Fehler, Plan geht schief"),
    "emotional": ("Emotional", "echte Freude, Schock, Wut oder Rührung"),
}

SYSTEM_PROMPT = """Du bist erfahrener Cutter für Kurzvideos (TikTok, YouTube Shorts, Instagram Reels) und \
wählst Momente aus Twitch-Streams aus.

Du bekommst einen Ausschnitt aus dem Transkript eines Streams als nummerierte Zeilen mit Zeitstempeln \
(Sekunden im VOD) und den Grund, warum diese Stelle auffiel, etwa weil der Streamer „clip das“ gesagt hat. \
Ein Sprachbefehl wie „clip das“ bezieht sich fast immer auf das, was kurz VORHER passiert ist.

Deine Aufgaben:
1. Entscheide, ob hier ein Moment steckt, der als eigenständiger Kurzclip funktioniert.
2. Wähle Start- und Endzeile. Ein Kurzclip muss in den ersten 1–2 Sekunden fesseln, sonst wird \
weggewischt: Beginne so spät wie möglich und so früh wie nötig – mitten in der Spannung, nicht beim Aufbau, kein \
Begrüßen, kein Leerlauf. Der Clip muss trotzdem ohne Vorwissen verständlich sein und endet kurz nach der Pointe \
bzw. Reaktion (ideal 15–40 Sekunden; länger nur, wenn die Geschichte es braucht). Den Sprachbefehl selbst \
(„clip das“) lässt du am Ende weg, wenn danach nichts Wichtiges mehr kommt.
3. Bewerte das Viralpotenzial von 1 bis 10. Sei streng und nutze die ganze Skala: Die meisten Stellen in \
einem Stream sind 3–5. 1–2 = nichts passiert, 3–4 = normales Gespräch, 5–6 = ganz nett, 7 = gut, \
8–9 = sehr stark (überraschend, lustig, emotional oder spektakulär, auch ohne Vorwissen), 10 = außergewöhnlich. \
Viele Chat-Nachrichten oder Lautstärke allein machen noch keinen guten Clip – entscheidend ist, was passiert. \
Bewerte vor allem: Wie stark ist der Einstieg? Versteht man es ohne Vorwissen? Gibt es eine klare Pointe? Würde man \
es nochmal ansehen, teilen oder kommentieren?
4. Schreibe einen kurzen, neugierig machenden Titel (max. 60 Zeichen) über den INHALT des Moments, konkret \
statt allgemein, höchstens ein Emoji. Nenne nie den Auslöser (keine „Chat-Spitze“, keine Nachrichtenzahlen, kein \
„Clip“). Dazu eine Beschreibung (1–2 Sätze) und 5–10 Hashtags als Liste, jeder ohne Leerzeichen: gemischt aus \
speziellen (Spiel, Situation) und allgemeinen (z. B. gaming, fyp, streamer). Alles in der Sprache des Streams.
5. Schreibe einen Hook-Text mit 2–6 Wörtern, der in den ersten Sekunden groß eingeblendet wird und auf dem \
Thumbnail steht. Er macht neugierig, verrät die Pointe nicht und verspricht nichts, was der Clip nicht hält.
6. Ordne den Clip einer Art zu (category).
7. Begründe deine Bewertung in einem Satz.

Das Transkript ist automatisch erstellt und kann Fehler enthalten. Schreibe Titel und Beschreibung trotzdem \
korrekt."""

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_highlight": {"type": "boolean"},
        "start_line": {"type": "integer"},
        "end_line": {"type": "integer"},
        "score": {"type": "integer"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "hook": {"type": "string"},
        "category": {"type": "string", "enum": [*CLIP_STYLES, "sonstiges"]},
        "reason": {"type": "string"},
    },
    "required": ["is_highlight", "start_line", "end_line", "score", "title", "description", "hashtags", "hook",
                 "category", "reason"],
    "additionalProperties": False,
}


@dataclass
class Line:
    idx: int
    s: float
    e: float
    text: str


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, i: int | None, o: int | None) -> None:
        self.input_tokens += i or 0
        self.output_tokens += o or 0
        self.calls += 1


def make_lines(words: list[Word], t0: float, t1: float, max_words: int = 18, gap: float = 0.7) -> list[Line]:
    """Wörter zu kurzen Zeilen bündeln: neue Zeile bei Satzende, Pause oder zu vielen Wörtern."""
    sel = [w for w in words if t0 <= w.s <= t1]
    lines: list[Line] = []
    cur: list[Word] = []

    def flush():
        if cur:
            lines.append(Line(len(lines), cur[0].s, cur[-1].e, " ".join(w.w for w in cur)))
            cur.clear()

    for i, w in enumerate(sel):
        if cur and (w.s - cur[-1].e > gap or len(cur) >= max_words):
            flush()
        cur.append(w)
        if w.w.endswith((".", "!", "?")):
            flush()
    flush()
    return lines


def format_lines(lines: list[Line]) -> str:
    return "\n".join(f"L{l.idx} [{l.s:.1f}–{l.e:.1f}] {l.text}" for l in lines)


def style_hint(s: Settings) -> str:
    """Welche Clip-Arten der Nutzer bevorzugt – als Hinweis für die Bewertung."""
    chosen = [k for k in s.clip_styles if k in CLIP_STYLES]
    if not chosen or len(chosen) == len(CLIP_STYLES):
        return "Alle Arten von Momenten sind willkommen."
    want = "; ".join(f"{CLIP_STYLES[k][0]}: {CLIP_STYLES[k][1]}" for k in chosen)
    return (f"Bevorzugte Arten von Momenten (bei vergleichbarer Stärke höher bewerten): {want}. "
            "Andere Arten nur hoch bewerten, wenn sie herausragend sind.")


def build_prompt(clip: Clip, lines: list[Line], s: Settings) -> str:
    return (
        f"Grund für diese Stelle: {'; '.join(clip.reasons)}\n"
        f"{style_hint(s)}\n"
        f"Zeitpunkt des Triggers: {clip.trigger_time:.1f} s\n"
        f"Ziel-Länge des Clips: {s.min_clip_seconds:.0f}–{s.effective_max_seconds:.0f} Sekunden.\n\n"
        f"Transkript:\n{format_lines(lines)}\n\n"
        "Antworte ausschließlich mit dem JSON-Objekt (start_line/end_line sind die Zeilennummern ohne „L“)."
    )


def budget_note(s: Settings, est: "CostEstimate") -> str:
    """Hinweis zur Kostenschätzung, wie viel vom Ausgabenlimit übrig ist."""
    from clipforge.core import budget

    rem = budget.remaining_usd(s)
    if rem is None:
        return ""
    eur = s.usd_to_eur
    if rem <= 0:
        return "Ausgabenlimit erreicht – bezahlte Schritte werden übersprungen."
    if est.total_high > rem:
        return (f"Achtung: nur noch {rem * eur:.2f} € im Ausgabenlimit – die Bewertung stoppt, sobald es "
                "aufgebraucht ist.").replace(".", ",", 1)
    return f"Im Ausgabenlimit übrig: {rem * eur:.2f} €".replace(".", ",")


def estimate_tokens(text: str) -> int:
    # Deutsch: grob 3,5 Zeichen pro Token
    return int(len(text) / 3.5) + 1


@dataclass
class CostEstimate:
    stt_usd: float = 0.0
    llm_low_usd: float = 0.0
    llm_high_usd: float = 0.0
    n_clips: int = 0
    llm_price_known: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def total_low(self) -> float:
        return self.stt_usd + self.llm_low_usd

    @property
    def total_high(self) -> float:
        return self.stt_usd + self.llm_high_usd

    def text(self) -> str:
        parts = []
        if self.stt_usd:
            parts.append(f"Transkription ca. {self.stt_usd:.2f} $")
        else:
            parts.append("Transkription lokal (kostenlos)")
        if self.n_clips:
            if self.llm_price_known:
                parts.append(f"KI-Bewertung von {self.n_clips} Clips ca. {self.llm_low_usd:.2f}–{self.llm_high_usd:.2f} $")
            else:
                parts.append(f"KI-Bewertung von {self.n_clips} Clips: Preis des Modells unbekannt")
        return " · ".join(parts + self.notes)


def stt_cost(s: Settings, duration: float) -> float:
    if s.transcription_backend == "local":
        return 0.0
    prov = STT_PROVIDERS.get(s.transcription_backend)
    model = s.stt_api_model or (prov.default_model if prov else "")
    return STT_PRICES_PER_MIN.get(f"{s.transcription_backend}:{model}", 0.0) * duration / 60


def estimate_cost(s: Settings, duration: float, clips: list[Clip] | None = None, words: list[Word] | None = None,
                  clips_per_hour: float = 4.0) -> CostEstimate:
    """Vor der Analyse (nur Dauer bekannt) grob, nach der Trigger-Suche genau pro Clip."""
    est = CostEstimate(stt_usd=stt_cost(s, duration))
    if not s.ai_enabled or s.llm_provider == "none":
        return est
    system_tokens = estimate_tokens(SYSTEM_PROMPT)
    if clips is not None and words is not None:
        est.n_clips = len(clips)
        inputs = [system_tokens + estimate_tokens(build_prompt(c, make_lines(
            words, c.trigger_time - s.context_before, c.trigger_time + s.context_after), s)) for c in clips]
    else:
        est.n_clips = max(1, round(duration / 3600 * clips_per_hour))
        est.notes.append(f"Annahme: ca. {clips_per_hour:.0f} Trigger pro Stunde")
        per = system_tokens + int((s.context_before + s.context_after) * 2.5 * 1.6) + 150
        inputs = [per] * est.n_clips
    price = llm_price(s.llm_model, s.price_overrides, s.llm_provider)
    if price is None:
        est.llm_price_known = False
        return est
    # Ausgabe: JSON ~250 Tokens; dazu Denk-Tokens je nach Modell (untere/obere Schätzung)
    out_low, out_high = 300, 3000
    total_in = sum(inputs)
    est.llm_low_usd = (total_in * price.input_per_m + out_low * len(inputs) * price.output_per_m) / 1e6
    est.llm_high_usd = (total_in * price.input_per_m + out_high * len(inputs) * price.output_per_m) / 1e6
    return est


# --------------------------------------------------------------------------- Anbieter-Clients


def _provider(s: Settings) -> Provider:
    p = LLM_PROVIDERS.get(s.llm_provider)
    if not p:
        raise ClipForgeError(f"Unbekannter KI-Anbieter „{s.llm_provider}“.", "Wähle einen Anbieter in den Einstellungen.")
    return p


def _key(p: Provider) -> str:
    key = secrets.get_key(p.id) or ""
    if p.needs_key and not key:
        raise ClipForgeError(f"Für {p.name} ist kein API-Key hinterlegt.",
                             f"Trage ihn in den Einstellungen ein{f' (Key erstellen: {p.key_url})' if p.key_url else ''}.")
    return key


# Nur bei diesen Anbietern darf eine eigene Server-Adresse gelten. Bei Cloud-Anbietern immer die offizielle
# Adresse – sonst ginge z. B. der OpenAI-Key an eine früher für Ollama eingetragene Adresse.
CUSTOM_URL_PROVIDERS = {"custom", "ollama", "lmstudio"}


def server_url(p: Provider, configured: str) -> str:
    if p.id in CUSTOM_URL_PROVIDERS and configured.strip():
        url = configured.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ClipForgeError("Die Server-Adresse muss mit http:// oder https:// beginnen.",
                                 "Beispiel: http://localhost:11434/v1")
        return url
    return p.base_url


def _parse_json(text: str) -> dict:
    """Erstes gültiges JSON-Objekt aus der Antwort (kleine Modelle schreiben gern Text drumherum oder
    mehrere Objekte). Wirft ValueError, wenn keines da ist."""
    text = text or ""
    try:
        r = json.loads(text)
        if isinstance(r, dict):
            return r
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            r, _ = dec.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict):
            return r
    raise ValueError("Die KI-Antwort enthält kein lesbares JSON-Objekt.")


class Scorer:
    def __init__(self, s: Settings):
        self.s = s
        self.p = _provider(s)
        self.key = _key(self.p)
        self.usage = Usage()
        self._client = None
        if not s.llm_model:
            raise ClipForgeError(f"Für {self.p.name} ist kein Modell ausgewählt.", "Wähle ein Modell in den Einstellungen.")

    # Anthropic über das offizielle SDK
    def _ask_anthropic(self, prompt: str) -> dict:
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self.key, max_retries=4)
        output_config: dict = {"format": {"type": "json_schema", "schema": RESULT_SCHEMA}}
        if _EFFORT_MODELS.match(self.s.llm_model) and self.s.llm_effort:
            output_config["effort"] = self.s.llm_effort
        kwargs = dict(model=self.s.llm_model, max_tokens=16000, system=SYSTEM_PROMPT,
                      messages=[{"role": "user", "content": prompt}], output_config=output_config)
        try:
            if self.s.llm_model in _FALLBACK_MODELS:
                resp = self._client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            else:
                resp = self._client.messages.create(**kwargs)
        except anthropic.AuthenticationError as e:
            raise ClipForgeError(
                "Anthropic lehnt den API-Key ab.",
                "Erstelle einen API-Key unter console.anthropic.com → API Keys (er beginnt mit „sk-ant-api…“) und "
                "füge ihn vollständig ein. Ein Claude-Abo (claude.ai) enthält keinen API-Key.", str(e)) from e
        except anthropic.PermissionDeniedError as e:
            raise ClipForgeError("Der Anthropic-Key hat keine Berechtigung für dieses Modell.",
                                 "Wähle ein anderes Modell oder prüfe dein Konto.", str(e)) from e
        except anthropic.NotFoundError as e:
            raise ClipForgeError(f"Das Modell „{self.s.llm_model}“ gibt es bei Anthropic nicht.",
                                 "Wähle ein Modell aus der Liste in den Einstellungen.", str(e)) from e
        except anthropic.RateLimitError as e:
            raise ClipForgeError("Anthropic: Limit erreicht oder kein Guthaben.",
                                 "Warte kurz oder lade Guthaben auf. Bereits bewertete Clips bleiben erhalten.",
                                 str(e)) from e
        except anthropic.BadRequestError as e:
            raise ClipForgeError("Anthropic hat die Anfrage abgelehnt.", "Details stehen in der Logdatei.", str(e)) from e
        except anthropic.APIConnectionError as e:
            raise ClipForgeError("Anthropic ist nicht erreichbar.", "Prüfe deine Internetverbindung.", str(e)) from e
        except anthropic.APIStatusError as e:
            raise ClipForgeError(f"Anthropic-Fehler ({e.status_code}).", "Später erneut versuchen.", str(e)) from e
        self.usage.add(resp.usage.input_tokens, resp.usage.output_tokens)
        if resp.stop_reason == "refusal":
            return {"is_highlight": False, "start_line": 0, "end_line": 0, "score": 0, "title": "",
                    "description": "", "hashtags": [], "reason": "Die KI hat die Bewertung abgelehnt."}
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return _parse_json(text)

    # Alle anderen über die OpenAI-kompatible Schnittstelle
    def _ask_openai_compat(self, prompt: str) -> dict:
        import openai

        base = server_url(self.p, self.s.llm_base_url)
        if not base:
            raise ClipForgeError("Für den eigenen Server fehlt die Adresse.", "Trage die Basis-URL in den Einstellungen ein.")
        if self._client is None:
            self._client = openai.OpenAI(api_key=self.key or "lokal", base_url=base, max_retries=4)
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt + "\n\nFelder: " + ", ".join(RESULT_SCHEMA["required"])}]
        try:
            try:
                resp = self._client.chat.completions.create(model=self.s.llm_model, messages=messages,
                                                            response_format={"type": "json_object"})
            except openai.BadRequestError:
                # Manche Modelle kennen keinen JSON-Modus
                resp = self._client.chat.completions.create(model=self.s.llm_model, messages=messages)
        except openai.AuthenticationError as e:
            raise ClipForgeError(f"{self.p.name} lehnt den API-Key ab.", "Prüfe den Key in den Einstellungen.", str(e)) from e
        except openai.PermissionDeniedError as e:  # z. B. NVIDIA antwortet bei falschem Key mit 403
            raise ClipForgeError(f"{self.p.name} lehnt den API-Key ab oder erlaubt dieses Modell nicht.",
                                 "Prüfe den Key in den Einstellungen oder wähle ein anderes Modell.", str(e)) from e
        except openai.NotFoundError as e:
            raise ClipForgeError(f"Das Modell „{self.s.llm_model}“ gibt es bei {self.p.name} nicht.",
                                 "Wähle ein Modell aus der Liste in den Einstellungen.", str(e)) from e
        except openai.RateLimitError as e:
            raise ClipForgeError(f"{self.p.name}: Limit erreicht oder kein Guthaben.",
                                 "Warte kurz oder lade Guthaben auf. Bereits bewertete Clips bleiben erhalten.",
                                 str(e)) from e
        except openai.APIConnectionError as e:
            hint = (f"Läuft {self.p.name}? Starte den Server und lade das Modell." if self.p.local
                    else "Prüfe deine Internetverbindung.")
            raise ClipForgeError(f"{self.p.name} ist nicht erreichbar.", hint, str(e)) from e
        except openai.APIError as e:
            raise ClipForgeError(f"Fehler bei {self.p.name}.", "Details stehen in der Logdatei.", str(e)) from e
        if resp.usage:
            self.usage.add(resp.usage.prompt_tokens or 0, resp.usage.completion_tokens or 0)
        return _parse_json(resp.choices[0].message.content or "")

    def ask(self, prompt: str) -> dict:
        if self.p.kind == "anthropic":
            return self._ask_anthropic(prompt)
        return self._ask_openai_compat(prompt)

    def cost_usd(self) -> float | None:
        price = llm_price(self.s.llm_model, self.s.price_overrides, self.s.llm_provider)
        if price is None:
            return None
        return (self.usage.input_tokens * price.input_per_m + self.usage.output_tokens * price.output_per_m) / 1e6

    # ----------------------------------------------------------------------- Bewertung eines Clips

    def score_clip(self, clip: Clip, words: list[Word], duration: float) -> Clip:
        s = self.s
        lines = make_lines(words, max(0.0, clip.trigger_time - s.context_before),
                           min(duration, clip.trigger_time + s.context_after))
        if not lines:
            clip.ai_reason = "Kein gesprochener Text an dieser Stelle."
            return clip
        prompt = build_prompt(clip, lines, s)
        for attempt in range(2):  # unlesbare Antwort: einmal neu fragen, dann ohne Bewertung weiter
            try:
                apply_result(clip, self.ask(prompt), lines, s, duration)
                return clip
            except (ValueError, AttributeError, TypeError) as e:
                log.warning("KI-Antwort unlesbar (Versuch %d): %s", attempt + 1, e)
        clip.ai_reason = "KI-Antwort unlesbar – Clip ohne Bewertung."
        return clip


def _int(v, default: int) -> int:
    """Kleine Modelle liefern Zahlen gern als Text („8“, „8/10“, „L12“)."""
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"-?\d+", str(v or ""))
    return int(m.group(0)) if m else default


def _hashtags(raw) -> list[str]:
    """Liste oder Text („#gaming #fail“ / „gaming, fail“) → ['#gaming', '#fail']."""
    if isinstance(raw, str):
        raw = re.split(r"[\s,;]+", raw)
    tags = []
    for t in raw or []:
        t = re.sub(r"\s+", "", str(t)).lstrip("#").strip(",.;")
        if len(t) >= 2 and f"#{t}" not in tags:
            tags.append(f"#{t}")
    return tags[:10]


def resolve_overlaps(clips: list[Clip], min_len: float) -> None:
    """Überlappende Clips entzerren: der frühere endet, wo der spätere beginnt. Bleibt dann zu wenig übrig,
    wird der schwächere verworfen (Grund wird vermerkt)."""
    from clipforge.pipeline.clips import STATUS_REJECTED

    live = sorted((c for c in clips if c.status != STATUS_REJECTED), key=lambda c: c.start)
    if not live:
        return
    prev = live[0]  # letzter noch gültiger Clip – nicht nur Nachbarpaare, sonst bleiben nach einem
    for b in live[1:]:  # Verwerfen Überschneidungen über den verworfenen hinweg bestehen
        a = prev
        if a.end <= b.start:
            prev = b
            continue
        if b.start - a.start >= min_len:
            a.end = round(b.start, 2)
            prev = b
            continue
        weaker = a if (a.score or 0) < (b.score or 0) else b
        weaker.status = STATUS_REJECTED
        weaker.ai_reason = (weaker.ai_reason + " " if weaker.ai_reason else "") + \
            "(Automatisch verworfen: überschneidet sich mit einem stärkeren Clip.)"
        prev = b if weaker is a else a


def apply_result(clip: Clip, r: dict, lines: list[Line], s: Settings, duration: float) -> None:
    """Überträgt die KI-Antwort auf den Clip und erzwingt die Längengrenzen."""
    n = len(lines)
    a = min(max(_int(r.get("start_line"), 0), 0), n - 1)
    b = min(max(_int(r.get("end_line"), n - 1), a), n - 1)
    max_len, min_len = s.effective_max_seconds, s.min_clip_seconds
    # Ein Sprachbefehl („clip das“) beendet den Moment: der Clip endet kurz danach, nicht Minuten später
    if clip.reasons and all(x.startswith(("Sprachbefehl", "Schlüsselbegriff")) for x in clip.reasons):
        limit = clip.trigger_time + 4.0
        while b > a and lines[b].e > limit:
            b -= 1
    # Zu lang: vorne kürzen, die Pointe am Ende bleibt
    while a < b and lines[b].e - lines[a].s > max_len:
        a += 1
    # Zu kurz: erst nach vorne, dann nach hinten Kontext ergänzen
    while lines[b].e - lines[a].s < min_len and (a > 0 or b < n - 1):
        if a > 0:
            a -= 1
        else:
            b += 1
    start = max(0.0, lines[a].s - 0.25)
    end = min(duration, lines[b].e + 0.5)
    if end - start > max_len:
        start = end - max_len
    if end - start < min_len:
        start = max(0.0, end - min_len)
        end = min(duration, max(end, start + min_len))
    clip.start, clip.end = round(start, 2), round(end, 2)
    clip.score = float(min(10, max(1, _int(r.get("score"), 1))))
    clip.title = str(r.get("title") or "").strip()[:100]
    clip.description = str(r.get("description") or "").strip()
    clip.hashtags = _hashtags(r.get("hashtags") or [])
    clip.ai_reason = str(r.get("reason") or "").strip()
    clip.hook = " ".join(str(r.get("hook") or "").split()[:8])[:60]
    cat = str(r.get("category") or "").strip().lower()
    clip.category = cat if cat in CLIP_STYLES else ("sonstiges" if cat else "")
    if not r.get("is_highlight", True):
        clip.score = min(clip.score, 3.0)


def list_models(s: Settings, provider_id: str | None = None) -> list[str]:
    """Fragt die verfügbaren Modelle beim Anbieter ab (für die Auswahl in den Einstellungen)."""
    p = LLM_PROVIDERS[provider_id or s.llm_provider]
    key = secrets.get_key(p.id) or ""
    try:
        if p.kind == "anthropic":
            import anthropic

            return [m.id for m in anthropic.Anthropic(api_key=key).models.list()] if key else list(p.models)
        import openai

        base = server_url(p, s.llm_base_url if p.id == s.llm_provider else "")
        if not base or (p.needs_key and not key):
            return list(p.models)
        ids = sorted(m.id for m in openai.OpenAI(api_key=key or "lokal", base_url=base).models.list())
        if p.id == "nvidia":  # NVIDIA listet auch Embedding-, Sicherheits- und Bildmodelle: nur Textmodelle zeigen
            skip = ("embed", "guard", "safety", "reward", "parse", "clip", "vision", "-vl", "vila", "neva", "kosmos",
                    "deplot", "fuyu", "detector", "translate", "retriever", "code", "starcoder", "calibration")
            ids = [i for i in ids if not any(k in i.lower() for k in skip)]
        return ids
    except Exception as e:
        log.warning("Modellliste von %s nicht abrufbar: %s", p.name, e)
        return list(p.models)
