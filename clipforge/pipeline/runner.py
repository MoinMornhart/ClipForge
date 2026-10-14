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
from clipforge.core.providers import STT_PRICES_PER_MIN, STT_PROVIDERS
from clipforge.core.settings import Settings
from clipforge.pipeline import audio, chat, importer, render
from clipforge.pipeline.chat import load_chat
from clipforge.pipeline.facecam import Facecam, detect_facecam
from clipforge.pipeline.subtitles import SubtitleStyle
from clipforge.pipeline.triggers import Hit
from clipforge.pipeline.clips import STATUS_APPROVED, STATUS_EXPORTED, STATUS_REJECTED, Clip, candidates_from_hits
from clipforge.pipeline.llm import CostEstimate, Scorer, estimate_cost, resolve_overlaps
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
        proj.state["offset"] = src.offset if (section and importer.is_url(ref)) else 0.0
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
        backend = self.backend()
        try:
            words = transcribe_source(
                src.path, proj.media["duration"], proj.dir, backend, self.s.language or None,
                chunk_seconds=self.s.chunk_minutes * 60,
                on_progress=lambda f, m: self.progress("transcribe", f, m), cancel=self.cancel,
                ffmpeg_path=self.s.ffmpeg_path,
            )
        finally:
            # tatsächlich abgerechnete Audiominuten festhalten, auch bei Abbruch
            secs = getattr(backend, "billed_seconds", 0.0)
            if secs:
                prov = self.s.transcription_backend
                model = self.s.stt_api_model or STT_PROVIDERS[prov].default_model
                price = STT_PRICES_PER_MIN.get(f"{prov}:{model}")
                proj.add_cost("transkription", prov, model, None if price is None else price * secs / 60,
                              minutes=secs / 60, calls=1)
                backend.billed_seconds = 0.0
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
        try:
            for n, clip in enumerate(todo, 1):
                if self.cancel.is_set():
                    raise Cancelled()
                self.progress("score", (n - 1) / len(todo), f"KI bewertet Clip {n}/{len(todo)}")
                scorer.score_clip(clip, words, duration)
                if self.s.min_score and clip.score is not None and clip.score < self.s.min_score:
                    clip.status = STATUS_REJECTED
                proj.save_clips(clips)  # nach jedem Clip sichern: Abbruch verliert nichts
        finally:
            # tatsächlich verbrauchte Tokens festhalten, auch bei Abbruch oder Fehler
            u = scorer.usage
            if u.calls:
                proj.add_cost("ki", self.s.llm_provider, self.s.llm_model, scorer.cost_usd(),
                              u.input_tokens, u.output_tokens, calls=u.calls)
        resolve_overlaps(clips, self.s.min_clip_seconds)
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
        self.facecam(proj)
        clips = self.find_clips(proj)
        pending = [c for c in clips if c.score is None]
        if pending and self.ai_ready():
            if confirm and not confirm(self.estimate(proj, pending)):
                return proj, clips
            clips = self.score(proj, clips)
        return proj, clips

    # 5. Facecam
    def facecam(self, proj: Project, redetect: bool = False) -> Facecam | None:
        """Reihenfolge: manuell im Projekt gesetzt > Profil des Streamers > automatische Erkennung."""
        cur = Facecam.from_dict(proj.state.get("facecam"))
        if cur and cur.source == "manuell" and not redetect:
            return cur
        src = proj.source
        prof = Facecam.from_dict(self.s.facecam_profiles.get(src.streamer)) if src and src.streamer else None
        if prof and not redetect:
            prof.source = "profil"
            prof = self._relocate_by_subject(proj, prof)
            proj.state["facecam"] = prof.to_dict()
            proj.save()
            return prof
        if cur and not redetect:
            return cur
        self.progress("facecam", 0.0, "Suche Facecam …")
        try:
            found = detect_facecam(src.path, proj.media["duration"], cancel=self.cancel)
        except ClipForgeError as e:
            log.warning("Facecam-Erkennung übersprungen: %s", e)
            self.progress("facecam", 1.0, f"Facecam-Erkennung übersprungen: {e.message}")
            return None
        proj.state["facecam"] = found.to_dict()
        proj.save()
        txt = {"overlay": "Facecam gefunden", "fullcam": "Nur-Kamera-Stream erkannt",
               "none": "Keine Facecam gefunden"}[found.mode]
        self.progress("facecam", 1.0, f"{txt} ({found.frames_with_face}/{found.frames_total} Bilder)")
        return found

    def _relocate_by_subject(self, proj: Project, fcm: Facecam) -> Facecam:
        """Hat das Profil ein markiertes Objekt, wird es im neuen VOD gesucht. Sitzt es woanders
        (Cam-Fenster verschoben), wird der Cam-Rahmen mitverschoben."""
        if not (fcm.subject and fcm.subject_file and Path(fcm.subject_file).exists()):
            return fcm
        from clipforge.pipeline.facecam import Rect, SubjectFinder, locate_subject

        self.progress("facecam", 0.0, "Suche markiertes Objekt …")
        try:
            finder = SubjectFinder.from_file(fcm.subject_file)
            W, H = proj.media["width"], proj.media["height"]
            found = locate_subject(proj.source.path, proj.media["duration"], finder) if finder else None
        except Exception as e:
            log.warning("Objektsuche fehlgeschlagen: %s", e)
            return fcm
        if not found:
            self.progress("facecam", 1.0, "Markiertes Objekt nicht gefunden – nutze gespeicherten Rahmen")
            return fcm
        old = Rect.from_norm(fcm.subject, W, H)
        dx, dy = found.cx - old.cx, found.cy - old.cy
        cam = Rect.from_norm(fcm.cam, W, H) if fcm.cam else None
        # Der Bär bewegt sich auch innerhalb der Cam – verschoben wird nur, wenn er außerhalb des Rahmens sitzt
        inside = cam and (cam.x - 0.05 * cam.w <= found.cx <= cam.x + 1.05 * cam.w
                          and cam.y - 0.05 * cam.h <= found.cy <= cam.y + 1.05 * cam.h)
        if cam and fcm.mode == "overlay" and not inside:
            fcm.cam = Rect(cam.x + dx, cam.y + dy, cam.w, cam.h).clamp(W, H).norm(W, H)
            self.progress("facecam", 1.0, "Cam-Fenster verschoben – über das markierte Objekt wiedergefunden")
        else:
            self.progress("facecam", 1.0, "Markiertes Objekt gefunden")
        return fcm

    def set_facecam(self, proj: Project, cam_norm: list[float] | None, save_profile: bool = True,
                    subject_norm: list[float] | None = None, at: float | None = None,
                    subject_file: str | None = None, handcam_norm: list[float] | None = None) -> Facecam:
        """Facecam von Hand festlegen (normiert x,y,b,h) und als Profil des Streamers speichern.

        subject_norm: optional markiertes Objekt (z. B. Bär); sein Bildausschnitt bei Sekunde at wird
        gespeichert, damit ClipForge es in jedem Clip wiederfindet, ihm folgt und mitzoomt."""
        cur = Facecam.from_dict(proj.state.get("facecam"))
        fcm = Facecam("overlay" if cam_norm else ("fullcam" if subject_norm else "none"),
                      cam=cam_norm or ([0, 0, 1, 1] if subject_norm else None),
                      face=cur.face if cur else None, confidence=1.0, source="manuell",
                      content=cur.content if cur else None,
                      handcam=handcam_norm, hand_source="manuell" if handcam_norm else "")
        if subject_norm:
            # unverändertes Objekt aus dem Profil behalten, neu markiertes aus dem aktuellen Bild ausschneiden
            fcm.subject = subject_norm
            fcm.subject_file = (subject_file if subject_file and Path(subject_file).exists()
                                else self._save_subject(proj, subject_norm, at or 0.0))
        proj.state["facecam"] = fcm.to_dict()
        proj.state["facecam"] = fcm.to_dict()
        proj.save()
        src = proj.source
        if save_profile and src and src.streamer:
            self.s.facecam_profiles[src.streamer] = {**fcm.to_dict(), "source": "profil"}
            self.s.save()
        return fcm

    def _save_subject(self, proj: Project, subject_norm: list[float], at: float) -> str:
        import hashlib

        import cv2

        from clipforge.core.paths import sub
        from clipforge.pipeline.facecam import Rect, read_frames

        frames = read_frames(proj.source.path, [at])
        if not frames:
            raise ClipForgeError("Das Vorschaubild konnte nicht gelesen werden.", "Wähle eine andere Stelle im Clip.")
        f = frames[0][1]
        H, W = f.shape[:2]
        r = Rect.from_norm(subject_norm, W, H).clamp(W, H)
        crop = f[int(r.y):int(r.y + r.h), int(r.x):int(r.x + r.w)]
        if crop.size == 0 or min(crop.shape[:2]) < 16:
            raise ClipForgeError("Das markierte Objekt ist zu klein.", "Ziehe das Rechteck etwas größer.")
        key = hashlib.sha1(f"{proj.source.streamer}|{proj.id}|{at}".encode()).hexdigest()[:10]
        path = sub("profiles") / f"objekt_{key}.png"
        cv2.imwrite(str(path), crop)
        return str(path)

    # 6. Export
    def render_job(self, proj: Project, clip: Clip, out: Path, cam: Facecam | None = None,
                   words: list | None = None) -> render.RenderJob:
        s = self.s
        return render.RenderJob(
            proj.source.path, clip, out, clip.layout or s.default_layout, s.fps, s.codec, s.use_nvenc,
            facecam=cam, words=words if words is not None else proj.load_words(),
            subtitle_style=SubtitleStyle.from_dict(s.subtitles), loudnorm=s.loudnorm, trim_silence=s.trim_silence,
            watermark=s.watermark, watermark_pos=s.watermark_pos, watermark_opacity=s.watermark_opacity,
            intro=s.intro, outro=s.outro, track_face=s.track_face, smart_crop=s.smart_crop)

    def export(self, proj: Project, clips: list[Clip] | None = None, only_approved: bool = False) -> list[Path]:
        all_clips = proj.load_clips()
        todo = clips if clips is not None else [
            c for c in all_clips if c.status != STATUS_REJECTED and (not only_approved or c.status == STATUS_APPROVED)
        ]
        src = proj.source
        out_dir = Path(self.s.export_dir)
        cam = self.facecam(proj) if todo else None
        words = proj.load_words()
        results = []
        for n, clip in enumerate(todo, 1):
            if self.cancel.is_set():
                raise Cancelled()
            name = render.output_name(clip, self.s, src.date, src.streamer, n)
            out = render.unique_path(out_dir / f"{name}.mp4")
            job = self.render_job(proj, clip, out, cam, words)
            label = f"Clip {n}/{len(todo)}"
            render.render(job, on_progress=lambda f, l=label, k=n: self.progress(
                "export", ((k - 1) + f) / len(todo), f"Exportiere {l}"), cancel=self.cancel,
                ffmpeg_path=self.s.ffmpeg_path, media=proj.media)
            render.write_metadata(clip, out)
            clip.status, clip.export_path = STATUS_EXPORTED, str(out)
            results.append(out)
            by_id = {c.id: c for c in all_clips}
            by_id[clip.id] = clip
            proj.save_clips(list(by_id.values()))
        self.progress("export", 1.0, f"{len(results)} Clips exportiert nach {out_dir}")
        return results
