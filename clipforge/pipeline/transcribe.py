"""Transkription mit Wortzeitstempeln.

Lange VODs werden in Abschnitte (Standard 10 min) zerlegt. Jeder fertige Abschnitt wird
sofort gespeichert, sodass eine abgebrochene Analyse dort weitermacht, wo sie aufgehört hat.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol

from clipforge.core import ffmpeg
from clipforge.core.errors import Cancelled, ClipForgeError
from clipforge.core.log import get_logger
from clipforge.core.paths import models_dir

log = get_logger("transcribe")

OVERLAP = 3.0  # Sekunden Überlappung, damit an Abschnittsgrenzen keine Wörter verloren gehen


@dataclass
class Word:
    w: str
    s: float
    e: float
    p: float = 1.0


class Backend(Protocol):
    def transcribe(self, wav: Path, language: str | None) -> list[Word]: ...


class LocalWhisper:
    """faster-whisper auf der GPU (CUDA), Rückfall auf CPU."""

    def __init__(self, model: str = "large-v3-turbo", device: str = "cuda"):
        self.model_name = model
        self.device = device
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        from clipforge.core.cuda import register_cuda_dlls

        register_cuda_dlls()
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise ClipForgeError("faster-whisper ist nicht installiert.", "Führe install.bat erneut aus.") from e
        root = str(models_dir() / "whisper")
        if self.device == "cuda":
            try:
                self._model = WhisperModel(self.model_name, device="cuda", compute_type="float16", download_root=root)
                log.info("Whisper-Modell %s auf GPU geladen", self.model_name)
                return self._model
            except Exception as e:  # CUDA/cuDNN fehlt oder Karte nicht unterstützt
                log.warning("GPU-Transkription nicht möglich, wechsle auf CPU: %s", e)
        self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8", download_root=root)
        log.info("Whisper-Modell %s auf CPU geladen", self.model_name)
        return self._model

    @property
    def on_gpu(self) -> bool:
        m = self._load()
        return getattr(m.model, "device", "cpu") == "cuda"

    def transcribe(self, wav: Path, language: str | None) -> list[Word]:
        model = self._load()
        segments, _info = model.transcribe(
            str(wav), language=language or None, word_timestamps=True, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500}, beam_size=5, condition_on_previous_text=False,
        )
        words: list[Word] = []
        for seg in segments:
            for w in seg.words or []:
                words.append(Word(w.word.strip(), round(w.start, 3), round(w.end, 3), round(w.probability, 3)))
        return words


class ApiWhisper:
    """Transkriptions-API mit Wortzeitstempeln (OpenAI whisper-1, Groq whisper-large-v3[-turbo])."""

    audio_ext = ".mp3"  # kleiner Upload; 10 min ≈ 5 MB (Grenze bei OpenAI: 25 MB)

    def __init__(self, provider_name: str, base_url: str, api_key: str, model: str):
        self.billed_seconds = 0.0  # abgerechnete Audiodauer (für die tatsächlichen Kosten)
        self.provider_name = provider_name
        self.base_url = base_url
        self.api_key = api_key
        self.model = model

    def transcribe(self, wav: Path, language: str | None) -> list[Word]:
        import openai

        client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
        extra = {"language": language} if language else {}
        try:
            with open(wav, "rb") as f:
                r = client.audio.transcriptions.create(
                    model=self.model, file=f, response_format="verbose_json",
                    timestamp_granularities=["word"], **extra,
                )
        except openai.AuthenticationError as e:
            raise ClipForgeError(f"{self.provider_name} lehnt den API-Key ab.",
                                 "Prüfe den Key in den Einstellungen.", str(e)) from e
        except openai.RateLimitError as e:
            raise ClipForgeError(f"{self.provider_name}: Limit erreicht oder kein Guthaben.",
                                 "Warte kurz oder lade Guthaben auf. Die Analyse setzt beim nächsten Start fort.",
                                 str(e)) from e
        except openai.APIError as e:
            raise ClipForgeError(f"Die Transkription bei {self.provider_name} ist fehlgeschlagen.",
                                 "Details stehen in der Logdatei.", str(e)) from e
        words = getattr(r, "words", None) or []
        return [Word(w.word.strip(), round(w.start, 3), round(w.end, 3)) for w in words]


def chunk_plan(duration: float, chunk_seconds: float) -> list[tuple[float, float]]:
    """Liefert (Start, Ende) je Abschnitt, ohne Überlappung."""
    n = max(1, math.ceil(duration / chunk_seconds))
    return [(i * chunk_seconds, min(duration, (i + 1) * chunk_seconds)) for i in range(n)]


def extract_audio(src: Path, dst: Path, start: float, length: float, cancel=None, ffmpeg_path: str = "") -> None:
    codec = ["-c:a", "libmp3lame", "-b:a", "64k"] if dst.suffix == ".mp3" else ["-c:a", "pcm_s16le"]
    ffmpeg.run(
        ["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         *codec, str(dst)],
        cancel=cancel, configured=ffmpeg_path, error_message="Die Tonspur konnte nicht gelesen werden.",
    )


def transcribe_source(
    src: Path,
    duration: float,
    work_dir: Path,
    backend: Backend,
    language: str | None,
    chunk_seconds: float = 600.0,
    on_progress: Callable[[float, str], None] | None = None,
    cancel: threading.Event | None = None,
    ffmpeg_path: str = "",
) -> list[Word]:
    chunks_dir = work_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    plan = chunk_plan(duration, chunk_seconds)
    all_words: list[Word] = []
    for i, (cs, ce) in enumerate(plan):
        if cancel and cancel.is_set():
            raise Cancelled()
        # Abschnittslänge im Namen, damit geänderte Einstellungen keine alten Stücke vermischen
        out = chunks_dir / f"{int(chunk_seconds)}s_{i:04d}.json"
        label = f"Abschnitt {i + 1}/{len(plan)} ({_hms(cs)}–{_hms(ce)})"
        if out.exists():
            words = [Word(**w) for w in json.loads(out.read_text(encoding="utf-8"))]
            log.debug("%s schon transkribiert, übersprungen", label)
        else:
            if on_progress:
                on_progress(i / len(plan), f"Transkribiere {label}")
            a_start = max(0.0, cs - OVERLAP)
            a_len = (ce - a_start) + (OVERLAP if ce < duration else 0.0)
            wav = out.with_suffix(getattr(backend, "audio_ext", ".wav"))
            extract_audio(src, wav, a_start, a_len, cancel, ffmpeg_path)
            try:
                raw = backend.transcribe(wav, language)
                if hasattr(backend, "billed_seconds"):
                    backend.billed_seconds += a_len
            finally:
                wav.unlink(missing_ok=True)
            # Zeitstempel auf das VOD beziehen und nur Wörter behalten, die in diesem Abschnitt beginnen
            words = []
            for w in raw:
                s, e = w.s + a_start, w.e + a_start
                if cs <= s < ce or (i == len(plan) - 1 and s >= cs):
                    words.append(Word(w.w, round(s, 3), round(e, 3), w.p))
            tmp = out.with_suffix(".tmp")
            tmp.write_text(json.dumps([asdict(w) for w in words], ensure_ascii=False), encoding="utf-8")
            tmp.replace(out)
        all_words.extend(words)
    if on_progress:
        on_progress(1.0, "Transkription fertig")
    return all_words


def _hms(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"
