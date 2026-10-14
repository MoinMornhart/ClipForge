"""ClipForge-Oberfläche: Hauptfenster mit Seitenleiste (Start, Clips, Einstellungen)."""

from __future__ import annotations

import sys

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QStackedWidget, QVBoxLayout, QWidget)

from clipforge import __version__
from clipforge.core.log import get_logger, setup_logging
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
        try:
            self.clips_page.open_project(pid)
        except Exception as e:
            log.exception("Projekt %s konnte nicht geöffnet werden", pid)
            QMessageBox.warning(self, "Öffnen fehlgeschlagen", f"Das Projekt konnte nicht geöffnet werden.\n\n{e}")
            return
        self.nav.setCurrentRow(1)

    def closeEvent(self, e):
        busy = self.import_page.task is not None
        if busy and QMessageBox.question(self, "Analyse läuft",
                                         "Eine Analyse läuft noch. Trotzdem beenden? Beim nächsten Start geht sie "
                                         "an derselben Stelle weiter.") != QMessageBox.Yes:
            e.ignore()
            return
        if busy:
            self.import_page.cancel()
            self.import_page.task.wait(4000)
        e.accept()


def main(initial: str | None = None) -> int:
    setup_logging(False)
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
    w = MainWindow()
    w.show()
    if initial:  # auf ClipForge.exe gezogene Datei bzw. übergebener Link
        w.import_page.src.setText(initial)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
