"""Hintergrundarbeit für die Oberfläche: Analyse und Export laufen in eigenen Threads,
das Fenster bleibt bedienbar. Fortschritt und Fehler kommen als Qt-Signale zurück."""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from clipforge.core.errors import Cancelled, ClipForgeError
from clipforge.core.log import get_logger

log = get_logger("ui.worker")


class Task(QObject):
    """Führt fn(progress, cancel, confirm) in einem Thread aus.

    progress(step, anteil, text) → Signal progress
    confirm(text) → fragt in der Oberfläche nach (blockiert den Thread, nicht das Fenster)
    """

    progress = Signal(str, float, str)
    finished = Signal(object)
    failed = Signal(str, str)  # Meldung, Hinweis
    cancelled = Signal()
    confirm_request = Signal(str)

    def __init__(self, fn: Callable[..., Any]):
        super().__init__()
        self.fn = fn
        self.cancel = threading.Event()
        self._answer = False
        self._answered = threading.Event()
        self._thread = QThread()
        self.moveToThread(self._thread)
        self._thread.started.connect(self._run)

    def start(self) -> None:
        self._thread.start()

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
            self._thread.quit()

    def wait(self, ms: int = 5000) -> None:
        self._thread.wait(ms)
