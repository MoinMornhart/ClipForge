"""ClipForge-Oberfläche: Hauptfenster mit Seitenleiste (Start, Clips, Einstellungen)."""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QStackedWidget, QVBoxLayout, QWidget)

from clipforge import __version__
import time

from clipforge.core import crashguard
from clipforge.core.log import get_logger, setup_logging
from clipforge.ui.worker import running_tasks
from clipforge.core.settings import Settings
from clipforge.ui import theme
from clipforge.ui.clips_page import ClipsPage
from clipforge.ui.import_page import ImportPage
from clipforge.ui.settings_page import SettingsPage

log = get_logger("ui")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"ClipForge {__version__}")
        self.resize(1440, 900)
        self.setMinimumSize(1100, 720)
        s = Settings.load()
        self._last_error_dialog = 0.0

        root = QWidget()
        root.setObjectName("root")
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        side = QWidget()
        side.setFixedWidth(210)
        side.setStyleSheet(f"background:{theme.PANEL}; border-right:1px solid {theme.LINE};")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 18, 0, 12)
        logo = QLabel(f"Clip<span style='color:{theme.ACCENT}'>Forge</span>")
        logo.setTextFormat(Qt.RichText)
        logo.setStyleSheet("font-size:19pt; font-weight:800; padding-left:20px; border:none;")
        sl.addWidget(logo)
        tag = QLabel("VOD → Kurzclips")
        tag.setStyleSheet(f"color:{theme.MUTED}; padding-left:21px; border:none;")
        sl.addWidget(tag)
        sl.addSpacing(16)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setStyleSheet("border:none;")
        for text in ("⬆  Start", "🎬  Clips", "⚙  Einstellungen"):
            it = QListWidgetItem(text)
            it.setSizeHint(QSize(190, 42))
            self.nav.addItem(it)
        sl.addWidget(self.nav, 1)
        ver = QLabel(f"Version {__version__}\nVerfasst mit Claude 🤖")
        ver.setStyleSheet(f"color:{theme.MUTED}; padding-left:20px; font-size:8pt; border:none;")
        sl.addWidget(ver)
        lay.addWidget(side)

        self.stack = QStackedWidget()
        self.import_page = ImportPage(s)
        self.clips_page = ClipsPage(s)
        self.settings_page = SettingsPage()
        for p in (self.import_page, self.clips_page, self.settings_page):
            self.stack.addWidget(p)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.import_page.project_ready.connect(self.open_project)
        for p in (self.clips_page, self.settings_page):
            p.status_message.connect(lambda m: self.statusBar().showMessage(m, 8000))
        self.settings_page.saved.connect(lambda: setattr(self.import_page, "s", Settings.load()))
        self.statusBar().showMessage("Bereit.")
        QTimer.singleShot(2500, lambda: self.settings_page.check_update(silent=True))

    def open_project(self, pid: str):
        self.nav.setCurrentRow(1)
        try:
            self.clips_page.open_project(pid)
        except Exception as e:
            log.exception("Projekt %s konnte nicht geöffnet werden", pid)
            QMessageBox.warning(self, "Öffnen fehlgeschlagen", f"Das Projekt konnte nicht geöffnet werden.\n\n{e}")
            self.nav.setCurrentRow(0)
            return
        crashguard.remember_project(pid)

    def show_error(self, message: str, details: str):
        """Abgefangener Fehler: Programm läuft weiter, Hinweis in der Statuszeile, höchstens alle 15 s ein Dialog."""
        self.statusBar().showMessage(f"Fehler abgefangen: {message} – Details in der Logdatei.", 15000)
        now = time.monotonic()
        if now - self._last_error_dialog > 15:
            self._last_error_dialog = now
            box = QMessageBox(QMessageBox.Warning, "Fehler abgefangen",
                              "Es ist ein Fehler aufgetreten. ClipForge läuft weiter.\n\n" + message, parent=self)
            box.setDetailedText(details[-4000:])
            box.setModal(False)
            box.show()

    def closeEvent(self, e):
        busy = running_tasks() > 0
        if busy and QMessageBox.question(self, "ClipForge arbeitet noch",
                                         "Eine Analyse oder ein Export läuft noch. Trotzdem beenden? Eine Analyse "
                                         "geht beim nächsten Start an derselben Stelle weiter.") != QMessageBox.Yes:
            e.ignore()
            return
        if busy:
            self.import_page.cancel()
            if self.import_page.task:
                self.import_page.task.wait(4000)
        crashguard.end_session()
        e.accept()


class _ErrorBridge(QObject):
    """Bringt abgefangene Fehler aus beliebigen Threads sicher in den Fenster-Thread."""

    error = Signal(str, str)


def main(initial: str | None = None) -> int:
    setup_logging(False)
    crashguard.install()
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("ClipForge")
    from pathlib import Path

    app.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "icon.ico")))
    try:  # eigenes Taskleisten-Symbol statt des Python-Symbols
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ClipForge.App")
    except (AttributeError, OSError):
        pass
    theme.apply(app)
    crashed = crashguard.begin_session()
    w = MainWindow()
    bridge = _ErrorBridge()
    bridge.error.connect(w.show_error)
    crashguard.set_reporter(lambda m, d: bridge.error.emit(m, d))
    w.show()
    if initial:  # auf ClipForge.exe gezogene Datei bzw. übergebener Link
        w.import_page.src.setText(initial)
    elif crashed and crashed.get("project"):
        pid = crashed["project"]
        QTimer.singleShot(300, lambda: (w.open_project(pid), w.statusBar().showMessage(
            "ClipForge wurde beim letzten Mal unerwartet beendet – das zuletzt geöffnete VOD ist wieder offen.", 12000)))
    code = app.exec()
    crashguard.end_session()
    return code


if __name__ == "__main__":
    sys.exit(main())
