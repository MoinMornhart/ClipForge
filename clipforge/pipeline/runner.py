"""Ablaufsteuerung: Import → Transkript → Trigger → Clips → Export.

Wird von CLI und Oberfläche gleichermaßen benutzt. Fortschritt läuft über einen Callback
(Schritt, Anteil 0–1, Text), Abbruch über ein threading.Event.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from clipforge.core import ffmpeg, secrets
from clipforge.core.errors import ClipForgeError
from clipforge.core.log import get_logger
from clipforge.core.settings import Settings
from clipforge.pipeline import importer, render
from clipforge.pipeline.clips import STATUS_APPROVED, STATUS_EXPORTED, STATUS_REJECTED, Clip, candidates_from_hits
from clipforge.pipeline.project import Project
from clipforge.pipeline.transcribe import LocalWhisper, OpenAIWhisper, transcribe_source
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
    def open(self, ref: str) -> Project:
        ref = ref.strip().strip('"')
        proj = Project.for_source(ref if importer.is_url(ref) else str(Path(ref).resolve()))
        src = proj.source
        if src and src.path.exists() and proj.done("import"):
            return proj
        if importer.is_url(ref):
            self.progress("import", 0.0, "Lade VOD herunter …")
            src = importer.download(ref, self.s.download_dir,
                                    on_progress=lambda f, m: self.progress("import", f, m), cancel=self.cancel)
        else:
            src = importer.from_local(ref)
        media = ffmpeg.probe(src.path, self.s.ffmpeg_path)
        if not media["has_audio"]:
            raise ClipForgeError("Das Video hat keine Tonspur.", "Ohne Ton kann kein Transkript erstellt werden.")
        proj.set_source(src, media)
        proj.mark("import")
        self.progress("import", 1.0, f"Importiert: {src.title}")
        return proj

    # 2. Transkript
    def backend(self):
        if self._backend is None:
            if self.s.transcription_backend == "openai":
                key = secrets.get_key("openai")
                if not key:
                    raise ClipForgeError("Für die OpenAI-Transkription fehlt der API-Key.",
                                         "Trage ihn in den Einstellungen ein oder wähle die lokale Transkription.")
                self._backend = OpenAIWhisper(key)
            else:
                self._backend = LocalWhisper(self.s.whisper_model, self.s.whisper_device)
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
    def find_clips(self, proj: Project) -> list[Clip]:
        self.progress("triggers", 0.0, "Suche Trigger …")
        words = proj.load_words()
        hits = find_hits(words, self.s.all_triggers())
        clips = candidates_from_hits(hits, proj.media["duration"], self.s)
        # Bereits bearbeitete Clips (Status, Titel, Layout) behalten
        old = {round(c.trigger_time, 1): c for c in proj.load_clips()}
        for c in clips:
            prev = old.get(round(c.trigger_time, 1))
            if prev:
                c.id, c.status, c.title, c.layout = prev.id, prev.status, prev.title, prev.layout
                c.score, c.description, c.hashtags = prev.score, prev.description, prev.hashtags
        proj.save_clips(clips)
        proj.mark("triggers")
        self.progress("triggers", 1.0, f"{len(hits)} Trigger, {len(clips)} Clips gefunden")
        return clips

    def analyze(self, ref: str) -> tuple[Project, list[Clip]]:
        proj = self.open(ref)
        self.transcribe(proj)
        return proj, self.find_clips(proj)

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
