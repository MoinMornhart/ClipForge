"""Ablaufsteuerung: Import → Transkript → Trigger → Clips → Export.

Wird von CLI und Oberfläche gleichermaßen benutzt. Fortschritt läuft über einen Callback
(Schritt, Anteil 0–1, Text), Abbruch über ein threading.Event.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from clipforge.core import ffmpeg, secrets
from clipforge.core.errors import Cancelled, ClipForgeError
from clipforge.core.log import get_logger
from clipforge.core.providers import STT_PROVIDERS
from clipforge.core.settings import Settings
from clipforge.pipeline import audio, chat, importer, render
from clipforge.pipeline.chat import load_chat
from clipforge.pipeline.triggers import Hit
from clipforge.pipeline.clips import STATUS_APPROVED, STATUS_EXPORTED, STATUS_REJECTED, Clip, candidates_from_hits
from clipforge.pipeline.llm import CostEstimate, Scorer, estimate_cost
from clipforge.pipeline.project import Project
from clipforge.pipeline.transcribe import ApiWhisper, LocalWhisper, transcribe_source
from clipforge.pipeline.triggers import find_hits

log = get_logger("runner")

Progress = Callable[[str, float, str], None]


def _noop(step: str, frac: float, msg: str) -> None:
    pass


class Pipeline:
    def __init__(self, settings: Settings | None = None, progress: Progress | None = None,
                 cancel: threading.Event | None = None):
        self.s = settings or Settings.load()
        self.progress = progress or _noop
        self.cancel = cancel or threading.Event()
        self._backend = None

    # 1. Import
    def open(self, ref: str, section: tuple[float, float] | None = None) -> Project:
        ref = ref.strip().strip('"')
        key = ref if importer.is_url(ref) else str(Path(ref).resolve())
        if section and importer.is_url(ref):
            key += f"#{int(section[0])}-{int(section[1])}"
        proj = Project.for_source(key)
        src = proj.source
        if src and src.path.exists() and proj.done("import"):
            return proj
        if importer.is_url(ref):
            platform = importer.platform_of(ref)
            self.progress("import", 0.0, f"Lade VOD von {platform} herunter …")
            src = importer.download(ref, self.s.download_dir, section=section,
                                    on_progress=lambda f, m: self.progress("import", f, m), cancel=self.cancel)
        else:
            src = importer.from_local(ref)
        media = ffmpeg.probe(src.path, self.s.ffmpeg_path)
        if not media["has_audio"]:
            raise ClipForgeError("Das Video hat keine Tonspur.", "Ohne Ton kann kein Transkript erstellt werden.")
        proj.set_source(src, media)
        proj.state["offset"] = section[0] if (section and importer.is_url(ref)) else 0.0
        proj.mark("import")
        self.progress("import", 1.0, f"Importiert: {src.title}")
        return proj

    # 2. Transkript
    def backend(self):
        if self._backend is None:
            prov = STT_PROVIDERS.get(self.s.transcription_backend)
            if prov is None or prov.id == "local":
                self._backend = LocalWhisper(self.s.whisper_model, self.s.whisper_device)
            else:
                key = secrets.get_key(prov.id)
                if not key:
                    raise ClipForgeError(f"Für die Transkription über {prov.name} fehlt der API-Key.",
                                         "Trage ihn in den Einstellungen ein oder wähle die lokale Transkription.")
                self._backend = ApiWhisper(prov.name, prov.base_url, key, self.s.stt_api_model or prov.default_model)
        return self._backend

    def transcribe(self, proj: Project) -> None:
        if proj.done("transcribe") and proj.transcript_path.exists():
            return
        src = proj.source
        self.progress("transcribe", 0.0, "Lade Sprachmodell …")
        words = transcribe_source(
            src.path, proj.media["duration"], proj.dir, self.backend(), self.s.language or None,
            chunk_seconds=self.s.chunk_minutes * 60,
            on_progress=lambda f, m: self.progress("transcribe", f, m), cancel=self.cancel,
            ffmpeg_path=self.s.ffmpeg_path,
        )
        proj.save_words(words)
        proj.mark("transcribe")

    # 3. Trigger + Kandidaten
    def chat_hits(self, proj: Project) -> list[Hit]:
        src = proj.source
        if not self.s.use_chat or not src or not src.url:
            return []
        self.progress("chat", 0.0, f"Lade {src.platform}-Chat …")
        try:
            msgs = load_chat(src.url, proj.media["duration"], proj.dir, offset=proj.offset,
                             on_progress=lambda f, m: self.progress("chat", f, m), cancel=self.cancel)
        except ClipForgeError as e:
            log.warning("Chat übersprungen: %s | %s", e, e.detail)
            self.progress("chat", 1.0, f"Chat übersprungen: {e.message}")
            return []
        if msgs is None:
            return []
        peaks = chat.find_peaks(msgs, proj.media["duration"], self.s.chat_reactions,
                                sensitivity=self.s.chat_sensitivity, min_count=self.s.chat_min_messages)
        self.progress("chat", 1.0, f"Chat: {len(msgs)} Nachrichten, {len(peaks)} Spitzen")
        # Der Chat reagiert einige Sekunden nach dem Ereignis
        return [Hit("Chat", "Chat-Spitze", "", p.time, p.start, 100.0, pre=35.0, post=3.0, text=p.reason)
                for p in peaks]

    def audio_hits(self, proj: Project) -> list[Hit]:
        if not self.s.use_audio_peaks:
            return []
        levels = audio.measure_levels(proj.source.path, proj.media["duration"], proj.dir,
                                      on_progress=lambda f, m: self.progress("audio", f, m), cancel=self.cancel,
                                      ffmpeg_path=self.s.ffmpeg_path)
        peaks = audio.find_peaks(levels, above_db=self.s.audio_above_db, min_level_db=self.s.audio_min_level_db)
        self.progress("audio", 1.0, f"Lautstärke: {len(peaks)} Spitzen")
        return [Hit("Lautstärke", "Lautstärkespitze", "", p.time + 2.0, p.start, 100.0, pre=25.0, post=6.0,
                    text=p.reason) for p in peaks]

    def find_clips(self, proj: Project) -> list[Clip]:
        self.progress("triggers", 0.0, "Suche Trigger …")
        words = proj.load_words()
        hits = find_hits(words, self.s.all_triggers()) + self.chat_hits(proj) + self.audio_hits(proj)
        hits.sort(key=lambda h: h.time)
        clips = candidates_from_hits(hits, proj.media["duration"], self.s)
        # Bereits bearbeitete Clips (Status, Titel, Layout) behalten
        old = {round(c.trigger_time, 1): c for c in proj.load_clips()}
        for c in clips:
            prev = old.get(round(c.trigger_time, 1))
            if prev:
                c.id, c.status, c.title, c.layout = prev.id, prev.status, prev.title, prev.layout
                c.score, c.description, c.hashtags = prev.score, prev.description, prev.hashtags
                if prev.score is not None:  # von der KI oder von Hand gesetzte Grenzen behalten
                    c.start, c.end, c.ai_reason = prev.start, prev.end, prev.ai_reason
        proj.save_clips(clips)
        proj.mark("triggers")
        self.progress("triggers", 1.0, f"{len(hits)} Trigger, {len(clips)} Clips gefunden")
        return clips

    # 4. KI-Bewertung
    def ai_ready(self) -> bool:
        from clipforge.core.providers import LLM_PROVIDERS

        p = LLM_PROVIDERS.get(self.s.llm_provider)
        if not self.s.ai_enabled or p is None:
            return False
        if p.needs_key and not secrets.get_key(p.id):
            self.progress("score", 1.0, f"KI-Bewertung übersprungen: kein API-Key für {p.name}")
            return False
        return True

    def estimate(self, proj: Project, clips: list[Clip] | None = None) -> CostEstimate:
        words = proj.load_words() if clips is not None else None
        return estimate_cost(self.s, proj.media.get("duration", 0.0), clips, words)

    def score(self, proj: Project, clips: list[Clip]) -> list[Clip]:
        if not self.s.ai_enabled or self.s.llm_provider == "none":
            return clips
        todo = [c for c in clips if c.score is None]
        if not todo:
            return clips
        try:
            scorer = Scorer(self.s)
        except ClipForgeError as e:
            # Ohne Key/Modell läuft alles weiter, nur ohne KI-Bewertung
            log.warning("KI-Bewertung übersprungen: %s", e)
            self.progress("score", 1.0, f"KI-Bewertung übersprungen: {e.message}")
            return clips
        words = proj.load_words()
        duration = proj.media["duration"]
        for n, clip in enumerate(todo, 1):
            if self.cancel.is_set():
                raise Cancelled()
            self.progress("score", (n - 1) / len(todo), f"KI bewertet Clip {n}/{len(todo)}")
            scorer.score_clip(clip, words, duration)
            if self.s.min_score and clip.score is not None and clip.score < self.s.min_score:
                clip.status = STATUS_REJECTED
            proj.save_clips(clips)  # nach jedem Clip sichern: Abbruch verliert nichts
        clips.sort(key=lambda c: -(c.score or 0))
        proj.save_clips(clips)
        cost = scorer.cost_usd()
        used = f"{scorer.usage.input_tokens + scorer.usage.output_tokens:,} Tokens".replace(",", ".")
        self.progress("score", 1.0, f"{len(todo)} Clips bewertet · {used}"
                      + (f" · {cost:.3f} $" if cost is not None else ""))
        return clips

    def analyze(self, ref: str, confirm: Callable[[CostEstimate], bool] | None = None,
                section: tuple[float, float] | None = None) -> tuple[Project, list[Clip]]:
        """confirm wird vor kostenpflichtigen Schritten mit der Schätzung aufgerufen; False bricht ab."""
        proj = self.open(ref, section)
        if confirm and self.s.transcription_backend != "local" and not proj.done("transcribe"):
            if not confirm(estimate_cost(self.s, proj.media["duration"])):
                raise Cancelled()
        self.transcribe(proj)
        clips = self.find_clips(proj)
        pending = [c for c in clips if c.score is None]
        if pending and self.ai_ready():
            if confirm and not confirm(self.estimate(proj, pending)):
                return proj, clips
            clips = self.score(proj, clips)
        return proj, clips

    # 4. Export
    def export(self, proj: Project, clips: list[Clip] | None = None, only_approved: bool = False) -> list[Path]:
        all_clips = proj.load_clips()
        todo = clips if clips is not None else [
            c for c in all_clips if c.status != STATUS_REJECTED and (not only_approved or c.status == STATUS_APPROVED)
        ]
        src = proj.source
        out_dir = Path(self.s.export_dir)
        results = []
        for n, clip in enumerate(todo, 1):
            name = render.output_name(clip, self.s, src.date, src.streamer, n)
            out = render.unique_path(out_dir / f"{name}.mp4")
            job = render.RenderJob(src.path, clip, out, clip.layout or self.s.default_layout, self.s.fps,
                                   self.s.codec, self.s.use_nvenc)
            label = f"Clip {n}/{len(todo)}"
            render.render(job, on_progress=lambda f, l=label, k=n: self.progress(
                "export", ((k - 1) + f) / len(todo), f"Exportiere {l}"), cancel=self.cancel,
                ffmpeg_path=self.s.ffmpeg_path)
            render.write_metadata(clip, out)
            clip.status, clip.export_path = STATUS_EXPORTED, str(out)
            results.append(out)
            by_id = {c.id: c for c in all_clips}
            by_id[clip.id] = clip
            proj.save_clips(list(by_id.values()))
        self.progress("export", 1.0, f"{len(results)} Clips exportiert nach {out_dir}")
        return results
