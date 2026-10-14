"""Startseite: Link oder Datei angeben, Analyse starten, Fortschritt verfolgen."""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

from clipforge.core.settings import Settings
from clipforge.pipeline.importer import is_url, platform_of
from clipforge.pipeline.project import Project
from clipforge.pipeline.runner import Pipeline
from clipforge.ui import theme
from clipforge.ui.widgets import DropZone, fmt_time
from clipforge.ui.worker import Task

STEPS = [
    ("import", "Import"), ("transcribe", "Transkript"), ("facecam", "Facecam"), ("chat", "Chat"),
    ("audio", "Lautstärke"), ("triggers", "Trigger"), ("score", "KI-Bewertung"),
]


def _parse_time(s: str) -> float:
    t = 0.0
    for part in s.strip().split(":"):
        t = t * 60 + float(part.replace(",", "."))
    return t


class StepPill(QLabel):
    def __init__(self, text: str):
        super().__init__(text)
        self.set_state("todo")

    def set_state(self, state: str):
        colors = {"todo": (theme.PANEL_2, theme.MUTED), "active": ("#3a2c16", theme.ACCENT),
                  "done": ("#173426", theme.GOOD), "skip": (theme.PANEL_2, "#5d6878")}
        bg, fg = colors[state]
        mark = {"todo": "○", "active": "◐", "done": "●", "skip": "–"}[state]
        self.setText(f"{mark}  {self.text().split('  ', 1)[-1]}")
        self.setStyleSheet(f"background:{bg}; color:{fg}; border-radius:12px; padding:4px 10px; font-weight:600;")


class ProjectCard(QFrame):
    """Karte für ein bearbeitetes VOD: Vorschaubild, Titel, Eckdaten."""

    def __init__(self, p: Project, cover):
        super().__init__()
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 10)
        lay.setSpacing(4)
        img = QLabel()
        img.setFixedSize(266, 150)
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet("background:#0b0d11; border-top-left-radius:12px; border-top-right-radius:12px;")
        if cover.exists():
            img.setPixmap(QPixmap(str(cover)).scaled(266, 150, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        else:
            img.setText("🎬")
        lay.addWidget(img)
        src = p.source
        t = QLabel(src.title)
        t.setTextFormat(Qt.PlainText)
        t.setStyleSheet("font-weight:700; padding:0 12px;")
        t.setMaximumWidth(266)
        t.setText(src.title if len(src.title) <= 34 else src.title[:33].rstrip() + "…")
        who = f"{src.streamer} · {src.platform}" if src.streamer else "Lokale Datei"
        meta = QLabel(f"{who} · {fmt_time(p.media.get('duration', 0))}")
        meta.setTextFormat(Qt.PlainText)
        meta.setObjectName("muted")
        meta.setStyleSheet("padding:0 12px;")
        n = len(p.load_clips())
        stats = QLabel(f"{n} Clip{'s' if n != 1 else ''} · {p.cost_text().split(' (')[0]}")
        stats.setObjectName("muted")
        stats.setStyleSheet("padding:0 12px; font-size:8.5pt;")
        for w in (t, meta, stats):
            lay.addWidget(w)


class ImportPage(QWidget):
    project_ready = Signal(str)  # Projekt-ID

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.task: Task | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        h = QLabel("Neues VOD analysieren")
        h.setObjectName("h1")
        root.addWidget(h)
        sub = QLabel("Twitch- oder Kick-Link einfügen oder eine Videodatei hineinziehen. "
                     "ClipForge findet die Momente, du entscheidest, was exportiert wird.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        root.addWidget(sub)

        row = QHBoxLayout()
        self.src = QLineEdit()
        self.src.setPlaceholderText("https://www.twitch.tv/videos/…   ·   https://kick.com/kanal/videos/…   ·   D:\\VODs\\stream.mp4")
        self.src.setMinimumHeight(40)
        self.src.textChanged.connect(self._src_changed)
        self.src.returnPressed.connect(self.start)
        browse = QPushButton("Datei wählen …")
        browse.clicked.connect(self._browse)
        self.go = QPushButton("Analysieren")
        self.go.setObjectName("primary")
        self.go.clicked.connect(self.start)
        row.addWidget(self.src, 1)
        row.addWidget(browse)
        row.addWidget(self.go)
        root.addLayout(row)

        opts = QHBoxLayout()
        self.platform = QLabel("")
        self.platform.setObjectName("badge")
        self.platform.hide()
        self.section = QCheckBox("Nur Ausschnitt laden")
        self.sec_from = QLineEdit("0:00:00")
        self.sec_to = QLineEdit("1:00:00")
        for w in (self.sec_from, self.sec_to):
            w.setMaximumWidth(90)
            w.setEnabled(False)
        self.section.toggled.connect(lambda on: (self.sec_from.setEnabled(on), self.sec_to.setEnabled(on)))
        self.ai = QCheckBox("KI-Bewertung")
        self.ai.setChecked(settings.ai_enabled)
        self.chat = QCheckBox("Chat auswerten")
        self.chat.setChecked(settings.use_chat)
        self.loud = QCheckBox("Lautstärkespitzen")
        self.loud.setChecked(settings.use_audio_peaks)
        opts.addWidget(self.platform)
        opts.addWidget(self.section)
        opts.addWidget(self.sec_from)
        opts.addWidget(QLabel("bis"))
        opts.addWidget(self.sec_to)
        opts.addSpacing(18)
        for w in (self.ai, self.chat, self.loud):
            opts.addWidget(w)
        opts.addStretch(1)
        root.addLayout(opts)

        self.drop = DropZone()
        self.drop.dropped.connect(self._dropped)
        root.addWidget(self.drop)

        # Fortschritt
        self.card = QFrame()
        self.card.setObjectName("card")
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(18, 14, 18, 14)
        top = QHBoxLayout()
        self.status = QLabel("Bereit.")
        self.status.setObjectName("h2")
        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.setObjectName("bad")
        self.cancel_btn.clicked.connect(self.cancel)
        self.cancel_btn.hide()
        top.addWidget(self.status, 1)
        top.addWidget(self.cancel_btn)
        cl.addLayout(top)
        pills = QHBoxLayout()
        self.pills = {}
        for key, label in STEPS:
            p = StepPill(label)
            self.pills[key] = p
            pills.addWidget(p)
        pills.addStretch(1)
        cl.addLayout(pills)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        cl.addWidget(self.bar)
        self.detail = QLabel("")
        self.detail.setObjectName("muted")
        cl.addWidget(self.detail)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(90)
        cl.addWidget(self.log)
        root.addWidget(self.card)
        self.card.hide()  # erscheint erst, wenn eine Analyse läuft – mehr Platz für „Zuletzt bearbeitet“

        # Zuletzt
        rh = QHBoxLayout()
        lbl = QLabel("Zuletzt bearbeitet")
        lbl.setObjectName("h2")
        self.total_cost = QLabel("")
        self.total_cost.setObjectName("muted")
        rh.addWidget(lbl)
        rh.addStretch(1)
        rh.addWidget(self.total_cost)
        root.addLayout(rh)
        # Zuletzt bearbeitet: Karten mit Vorschaubild, Klick öffnet das VOD
        self.recent = QListWidget()
        self.recent.setObjectName("cards")
        self.recent.setViewMode(QListWidget.IconMode)
        self.recent.setResizeMode(QListWidget.Adjust)
        self.recent.setMovement(QListWidget.Static)
        self.recent.setSpacing(8)
        self.recent.setUniformItemSizes(True)
        self.recent.setSelectionMode(QListWidget.NoSelection)
        self.recent.itemClicked.connect(lambda it: self.project_ready.emit(it.data(Qt.UserRole)))
        root.addWidget(self.recent, 1)
        self._cover_task = None
        self.refresh_recent()

    # ------------------------------------------------------------------ Eingaben
    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "Videodatei wählen", "", "Videos (*.mp4 *.mkv *.mov *.webm *.flv *.ts)")
        if p:
            self.src.setText(p)

    def _dropped(self, s: str):
        self.src.setText(s)
        self.start()

    def _src_changed(self, text: str):
        if is_url(text):
            self.platform.setText(platform_of(text))
            self.platform.show()
        else:
            self.platform.hide()

    def refresh_recent(self):
        self.recent.clear()
        total, n = Project.total_cost_all()
        self.total_cost.setText(f"API-Kosten gesamt: {total:.2f} $ für {n} VOD(s)".replace(".", ","))
        missing = []
        projects = [p for p in Project.all()[:24] if p.source]
        if not projects:
            it = QListWidgetItem("Noch keine VODs. Füge oben einen Link ein oder ziehe eine Datei hinein.")
            it.setFlags(Qt.NoItemFlags)
            self.recent.addItem(it)
            return
        for p in projects:
            cover = p.dir / "cover.jpg"
            if not cover.exists():
                missing.append(p)
            it = QListWidgetItem()
            it.setData(Qt.UserRole, p.id)
            it.setSizeHint(QSize(268, 236))
            it.setToolTip(p.source.title)
            self.recent.addItem(it)
            self.recent.setItemWidget(it, ProjectCard(p, cover))
        if missing and self._cover_task is None:
            self._make_covers(missing)

    def _make_covers(self, projects):
        """Vorschaubilder für die Karten im Hintergrund erzeugen (ein Bild aus dem ersten Drittel)."""
        def job(progress, cancel, confirm):
            import cv2

            from clipforge.pipeline.facecam import read_frames

            for p in projects:
                if cancel.is_set():
                    break
                try:
                    fr = read_frames(p.source.path, [p.media.get("duration", 60) * 0.3], max_w=480)
                    if fr:
                        cv2.imwrite(str(p.dir / "cover.jpg"), fr[0][1])
                except Exception:  # fehlende Datei o. Ä.: Karte ohne Bild
                    pass
            return True

        self._cover_task = Task(job)
        self._cover_task.finished.connect(lambda _: (setattr(self, "_cover_task", None), self.refresh_recent()))
        self._cover_task.failed.connect(lambda *_: setattr(self, "_cover_task", None))
        self._cover_task.start()

    # ------------------------------------------------------------------ Analyse
    def start(self):
        ref = self.src.text().strip().strip('"')
        if not ref or self.task:
            return
        section = None
        if self.section.isChecked() and is_url(ref):
            try:
                section = (_parse_time(self.sec_from.text()), _parse_time(self.sec_to.text()))
            except ValueError:
                QMessageBox.warning(self, "Ausschnitt", "Bitte Zeiten im Format 1:20:00 eingeben.")
                return
        s = Settings.load()
        s.ai_enabled, s.use_chat, s.use_audio_peaks = self.ai.isChecked(), self.chat.isChecked(), self.loud.isChecked()

        def job(progress, cancel, confirm):
            pipe = Pipeline(s, progress, cancel)
            proj, clips = pipe.analyze(ref, confirm=lambda est: confirm(est.text()), section=section)
            return proj.id

        for p in self.pills.values():
            p.set_state("todo")
        self.log.clear()
        self.bar.setValue(0)
        self.status.setText("Analyse läuft …")
        self.card.show()
        self.go.setEnabled(False)
        self.cancel_btn.show()
        self.task = Task(job)
        self.task.progress.connect(self._progress)
        self.task.finished.connect(self._done)
        self.task.failed.connect(self._failed)
        self.task.cancelled.connect(self._cancelled)
        self.task.confirm_request.connect(self._confirm)
        self.task.start()

    def cancel(self):
        if self.task:
            self.task.cancel.set()
            self.status.setText("Breche ab …")

    def _confirm(self, text: str):
        r = QMessageBox.question(self, "Kosten bestätigen", f"Geschätzte Kosten:\n\n{text}\n\nFortfahren?",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        self.task.answer(r == QMessageBox.Yes)

    def _progress(self, step: str, frac: float, msg: str):
        order = [k for k, _ in STEPS]
        if step in self.pills:
            idx = order.index(step)
            for k in order[:idx]:
                if self.pills[k].text().startswith(("○", "◐")):
                    self.pills[k].set_state("done" if self.pills[k].text().startswith("◐") else "skip")
            self.pills[step].set_state("done" if frac >= 1.0 else "active")
            self.bar.setValue(int((idx + min(frac, 1.0)) / len(order) * 1000))
        self.detail.setText(msg)
        if frac >= 1.0 or frac == 0.0:
            self.log.appendPlainText(f"{dt.datetime.now():%H:%M:%S}  {msg}")

    def _finish_ui(self):
        self.task = None
        self.go.setEnabled(True)
        self.cancel_btn.hide()
        self.refresh_recent()

    def _done(self, pid: str):
        self.bar.setValue(1000)
        self.status.setText("Fertig – Clips prüfen")
        self._finish_ui()
        self.project_ready.emit(pid)

    def _failed(self, msg: str, hint: str):
        self.status.setText("Fehler")
        self.log.appendPlainText(f"FEHLER: {msg} {hint}")
        self._finish_ui()
        QMessageBox.warning(self, "Analyse fehlgeschlagen", f"{msg}\n\n{hint}")

    def _cancelled(self):
        self.status.setText("Abgebrochen – beim nächsten Start geht es an derselben Stelle weiter.")
        self._finish_ui()
