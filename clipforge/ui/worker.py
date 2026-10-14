"""Hintergrundarbeit für die Oberfläche: Analyse und Export laufen in eigenen Threads,
das Fenster bleibt bedienbar.

Wichtig für die Stabilität (Qt stürzt sonst ab):
- Das Task-Objekt lebt im Fenster-Thread; nur die Arbeit läuft in einem Python-Thread. Signale, die aus
  dem Arbeits-Thread kommen, stellt Qt dadurch immer im Fenster-Thread zu – auch an Lambdas.
- Laufende Tasks werden in _RUNNING festgehalten, bis ihr letztes Signal zugestellt ist. So wird nie ein
  Task freigegeben, während er noch arbeitet.
"""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from clipforge.core.errors import Cancelled, ClipForgeError
from clipforge.core.log import get_logger

log = get_logger("ui.worker")

_RUNNING: set["Task"] = set()


class Task(QObject):
    """Führt fn(progress, cancel, confirm) in einem Thread aus.

    progress(step, anteil, text) → Signal progress
    confirm(text) → fragt in der Oberfläche nach (blockiert den Arbeits-Thread, nicht das Fenster)
    """

    progress = Signal(str, float, str)
    finished = Signal(object)
    failed = Signal(str, str)  # Meldung, Hinweis
    cancelled = Signal()
    confirm_request = Signal(str)
    _ended = Signal()

    def __init__(self, fn: Callable[..., Any]):
        super().__init__()
        self.fn = fn
        self.cancel = threading.Event()
        self._answer = False
        self._answered = threading.Event()
        self._thread: threading.Thread | None = None
        self._ended.connect(self._release)

    def start(self) -> None:
        _RUNNING.add(self)
        self._thread = threading.Thread(target=self._run, name="ClipForge-Task", daemon=True)
        self._thread.start()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def answer(self, yes: bool) -> None:
        self._answer = yes
        self._answered.set()

    def _confirm(self, text: str) -> bool:
        self._answered.clear()
        self.confirm_request.emit(text)
        while not self._answered.wait(0.2):
            if self.cancel.is_set():
                return False
        return self._answer

    def _run(self) -> None:
        try:
            result = self.fn(lambda s, f, m: self.progress.emit(s, float(f), m), self.cancel, self._confirm)
            self.finished.emit(result)
        except Cancelled:
            self.cancelled.emit()
        except ClipForgeError as e:
            log.error("%s | %s", e.message, e.detail or "")
            self.failed.emit(e.message, e.hint or "")
        except Exception as e:  # unerwartet: ins Log, verständlich anzeigen
            log.error("Unerwarteter Fehler: %s\n%s", e, traceback.format_exc())
            self.failed.emit("Ein unerwarteter Fehler ist aufgetreten.",
                             f"{type(e).__name__}: {e}. Details stehen in der Logdatei.")
        finally:
            self._ended.emit()  # kommt nach den anderen Signalen im Fenster-Thread an

    def _release(self) -> None:
        _RUNNING.discard(self)

    def wait(self, ms: int = 5000) -> None:
        if self._thread:
            self._thread.join(ms / 1000)


def running_tasks() -> int:
    return sum(1 for t in _RUNNING if t.is_running())
