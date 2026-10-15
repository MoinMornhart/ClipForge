"""Clip-Übersicht und Editor: prüfen, anpassen, freigeben, exportieren."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QTabWidget, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from clipforge.core.log import get_logger
from clipforge.core.settings import Settings
from clipforge.pipeline import render
from clipforge.pipeline.clips import (STATUS_APPROVED, STATUS_EXPORTED, STATUS_NEW, STATUS_REJECTED, Clip,
                                      align_edit)
from clipforge.pipeline.facecam import Facecam, detect_facecam, read_frames
from clipforge.pipeline.layouts import LAYOUTS
from clipforge.pipeline.project import Project
from clipforge.pipeline.runner import Pipeline
from clipforge.pipeline.transcribe import Word
from clipforge.ui import theme
from clipforge.ui.widgets import RangeSlider, RectSelector, bgr_to_qimage, fmt_time
from clipforge.ui.worker import Task

log = get_logger("ui.clips")

STATUS_STYLE = {
    STATUS_NEW: ("neu", theme.MUTED, theme.PANEL_2),
    STATUS_APPROVED: ("freigegeben", theme.GOOD, "#173426"),
    STATUS_REJECTED: ("verworfen", theme.BAD, "#3a1a1d"),
    STATUS_EXPORTED: ("exportiert", theme.ACCENT, "#3a2c16"),
}


def score_color(score: float | None) -> str:
    if score is None:
        return theme.MUTED
    return theme.GOOD if score >= 7 else (theme.ACCENT if score >= 5 else theme.BAD)


class ClipRow(QWidget):
    def __init__(self, clip: Clip, thumb: Path | None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(10)
        self.thumb = QLabel()
        self.thumb.setFixedSize(128, 72)
        self.thumb.setStyleSheet("background:#000; border-radius:6px;")
        if thumb and thumb.exists():
            self.thumb.setPixmap(QPixmap(str(thumb)).scaled(128, 72, Qt.KeepAspectRatioByExpanding,
                                                            Qt.SmoothTransformation))
        lay.addWidget(self.thumb)
        mid = QVBoxLayout()
        mid.setSpacing(2)
        # noch ohne KI-Titel: Auslöser und Zeitpunkt statt „Ohne Titel“
        kind = clip.reasons[0].split(":")[0].strip() if clip.reasons else "Clip"
        t = int(clip.trigger_time)
        at = f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"
        title = QLabel(clip.title or f"{kind} bei {at}")
        title.setStyleSheet("font-weight:700;")
        meta = QLabel(f"{fmt_time(clip.start)} – {fmt_time(clip.end)} · {clip.duration:.0f} s")
        meta.setObjectName("muted")
        full = "; ".join(clip.reasons)
        if not clip.title and clip.reasons and ":" in clip.reasons[0]:  # Auslöser steht schon im Titel
            full = "; ".join([clip.reasons[0].split(":", 1)[1].strip(), *clip.reasons[1:]])
        reason = QLabel(full if len(full) <= 42 else full[:41] + "…")
        reason.setToolTip(full)
        reason.setObjectName("muted")
        reason.setStyleSheet("font-size:8.5pt;")
        for w in (title, meta, reason):
            w.setTextFormat(Qt.PlainText)  # KI-/Streamtitel nie als HTML deuten
            mid.addWidget(w)
        lay.addLayout(mid, 1)
        right = QVBoxLayout()
        sc = QLabel("–" if clip.score is None else f"{clip.score:.0f}")
        sc.setAlignment(Qt.AlignCenter)
        sc.setFixedSize(40, 40)
        sc.setStyleSheet(f"border:2px solid {score_color(clip.score)}; border-radius:20px; "
                         f"color:{score_color(clip.score)}; font-weight:800; font-size:13pt;")
        label, fg, bg = STATUS_STYLE.get(clip.status, STATUS_STYLE[STATUS_NEW])
        st = QLabel(label)
        st.setAlignment(Qt.AlignCenter)
        st.setStyleSheet(f"background:{bg}; color:{fg}; border-radius:8px; padding:1px 6px; font-size:8pt;")
        right.addWidget(sc, 0, Qt.AlignCenter)
        right.addWidget(st)
        lay.addLayout(right)


class FacecamDialog(QDialog):
    def __init__(self, pipe: Pipeline, proj: Project, at: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Facecam festlegen")
        self.resize(1280, 760)
        self.pipe, self.proj = pipe, proj
        lay = QVBoxLayout(self)
        info = QLabel("1. Ziehe ein Rechteck um die Facecam (orange).  2. Optional: Markiere das Objekt in der Cam "
                      "(grün), z. B. dein Maskottchen. ClipForge erkennt es dann in jedem Clip wieder, folgt ihm und "
                      "zoomt mit, wenn es größer oder kleiner wird – und findet die Cam auch, wenn du sie verschiebst. "
                      "3. Optional: Handcam-Rahmen (blau), falls du eine Kamera auf deine Hände hast. "
                      "Wird als Profil für diesen Streamer gespeichert.")
        info.setWordWrap(True)
        info.setObjectName("muted")
        lay.addWidget(info)
        modes = QHBoxLayout()
        self.mode_cam = QPushButton("① Facecam-Rahmen ziehen")
        self.mode_obj = QPushButton("② Objekt markieren (z. B. Bär)")
        self.mode_hand = QPushButton("③ Handcam-Rahmen ziehen")
        for b, m in ((self.mode_cam, "cam"), (self.mode_obj, "subject"), (self.mode_hand, "hand")):
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, mm=m: self._set_mode(mm))
            modes.addWidget(b)
        clear_obj = QPushButton("Objekt entfernen")
        clear_obj.clicked.connect(self._clear_subject)
        modes.addWidget(clear_obj)
        clear_hand = QPushButton("Handcam entfernen")
        clear_hand.clicked.connect(self._clear_hand)
        modes.addWidget(clear_hand)
        modes.addStretch(1)
        lay.addLayout(modes)
        self.sel = RectSelector()
        body = QHBoxLayout()
        body.addWidget(self.sel, 1)
        # Live-Vorschau: so sieht der Clip mit den gezogenen Rahmen aus
        side = QVBoxLayout()
        pv_title = QLabel("Vorschau 9:16")
        pv_title.setObjectName("h2")
        self.pv_layout = QComboBox()
        for k, v in LAYOUTS.items():
            self.pv_layout.addItem(v, k)
        self.preview = QLabel("Rahmen ziehen …")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(234, 416)
        self.preview.setStyleSheet("background:#000; border-radius:10px; color:#8d96a5;")
        side.addWidget(pv_title)
        side.addWidget(self.pv_layout)
        side.addWidget(self.preview)
        side.addStretch(1)
        body.addLayout(side)
        lay.addLayout(body, 1)
        self._pv_timer = QTimer(self, singleShot=True, interval=350, timeout=self._update_preview)
        self._pv_task = None
        self.sel.selected.connect(lambda *_: self._pv_timer.start())
        self.pv_layout.currentIndexChanged.connect(lambda *_: self._pv_timer.start())
        self.at = at
        frames = read_frames(proj.source.path, [at])
        cur = Facecam.from_dict(proj.state.get("facecam"))
        self.cur = cur
        if frames:
            self.sel.set_image(bgr_to_qimage(frames[0][1]), cur.cam if cur and cur.mode == "overlay" else None)
        if cur and cur.subject and cur.subject_file:
            self.sel.subject_norm = cur.subject
        if cur and cur.handcam:
            self.sel.hand_norm = cur.handcam
        self._set_mode("cam")
        QTimer.singleShot(200, self._update_preview)
        self.state = QLabel(self._describe(cur))
        self.state.setObjectName("muted")
        lay.addWidget(self.state)
        btns = QHBoxLayout()
        auto = QPushButton("Automatisch erkennen")
        auto.clicked.connect(self._auto)
        none = QPushButton("Keine Facecam")
        none.clicked.connect(self._none)
        self.profile = QCheckBox(f"Als Profil für „{proj.source.streamer or 'diese Quelle'}“ speichern")
        self.profile.setChecked(bool(proj.source.streamer))
        ok = QPushButton("Übernehmen")
        ok.setObjectName("primary")
        ok.clicked.connect(self._accept)
        btns.addWidget(auto)
        btns.addWidget(none)
        btns.addStretch(1)
        btns.addWidget(self.profile)
        btns.addWidget(ok)
        lay.addLayout(btns)
        self._task = None
        self._none_chosen = False

    @staticmethod
    def _describe(f: Facecam | None) -> str:
        if not f:
            return "Noch keine Facecam bestimmt."
        src = {"auto": "automatisch erkannt", "profil": "aus Streamer-Profil", "manuell": "von Hand festgelegt"}
        return {"overlay": "Facecam-Fenster", "fullcam": "Nur-Kamera-Stream", "none": "Keine Facecam"}[f.mode] + \
            f" · {src.get(f.source, f.source)} · Sicherheit {f.confidence:.0%}" + \
            (" · Objekt markiert: wird verfolgt und gezoomt" if f.subject and f.subject_file else "") +             (" · Handcam " + ("erkannt" if f.hand_source == "auto" else "festgelegt") if f.handcam else "")

    def _auto(self):
        self.state.setText("Erkenne Facecam …")
        proj = self.proj

        def job(progress, cancel, confirm):
            return detect_facecam(proj.source.path, proj.media["duration"], cancel=cancel)

        self._task = Task(job)
        self._task.finished.connect(self._auto_done)
        self._task.failed.connect(lambda m, h: self.state.setText(f"{m} {h}"))
        self._task.start()

    def _auto_done(self, f: Facecam):
        if not self.isVisible():  # Dialog inzwischen geschlossen: Auswahl des Nutzers nicht überschreiben
            return
        self.cur = f  # gespeichert wird erst mit „Übernehmen“
        self.sel.rect_norm = f.cam if f.mode == "overlay" else None
        self.sel.update()
        self.state.setText(self._describe(f))

    def _none(self):
        self.sel.rect_norm = None
        self._none_chosen = True
        self.sel.update()

    def _current_facecam(self) -> Facecam:
        cam, hand = self.sel.rect_norm, self.sel.hand_norm
        base = self.cur
        return Facecam("overlay" if cam else "none", cam=cam, face=base.face if base else None,
                       content=base.content if base else None, handcam=hand)

    def _update_preview(self):
        if self._pv_task is not None:  # läuft noch: gleich danach erneut
            self._pv_timer.start()
            return
        import tempfile

        out = Path(tempfile.gettempdir()) / f"clipforge_fc_preview_{self.proj.id}.png"
        fcm, layout, proj, at = self._current_facecam(), self.pv_layout.currentData(), self.proj, self.at

        def job(progress, cancel, confirm):
            return render.preview_frame(proj.source.path, at, layout, fcm, proj.media, out)

        self._pv_task = Task(job)
        self._pv_task.finished.connect(self._preview_ready)
        self._pv_task.failed.connect(lambda m, h: (self.preview.setText(m), setattr(self, "_pv_task", None)))
        self._pv_task.start()

    def _preview_ready(self, path):
        self._pv_task = None
        pm = QPixmap(str(path))
        if not pm.isNull():
            self.preview.setPixmap(pm.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _set_mode(self, mode: str):
        self.sel.mode = mode
        self.mode_cam.setChecked(mode == "cam")
        self.mode_obj.setChecked(mode == "subject")
        self.mode_hand.setChecked(mode == "hand")

    def _clear_hand(self):
        self.sel.hand_norm = None
        self.sel.update()
        self._pv_timer.start()

    def _clear_subject(self):
        self.sel.subject_norm = None
        self.sel.update()

    def _accept(self):
        subj = self.sel.subject_norm
        keep = self.cur.subject_file if (self.cur and subj and subj == self.cur.subject) else None
        try:
            self.pipe.set_facecam(self.proj, self.sel.rect_norm, save_profile=self.profile.isChecked(),
                                  subject_norm=subj, at=self.at, subject_file=keep,
                                  handcam_norm=self.sel.hand_norm)
        except Exception as e:
            QMessageBox.warning(self, "Facecam", str(e))
            return
        self.accept()

    def done(self, r):
        for t in (self._task, self._pv_task):  # laufende Erkennung/Vorschau beenden
            if t is not None:
                t.cancel.set()
        super().done(r)


class ClipsPage(QWidget):
    status_message = Signal(str)
    go_start = Signal()

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.proj: Project | None = None
        self.clips: list[Clip] = []
        self.words: list[Word] = []
        self.cur: Clip | None = None
        self.tasks: list[Task] = []
        self._loading = False
        self._exporting = False
        self._previewing = False
        self.export_task: Task | None = None
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self._save)
        # 9:16-Livebild neben dem Player: neu rendern, sobald sich Clip, Layout, Grenzen oder Facecam ändern
        self._pv916_timer = QTimer(self, singleShot=True, interval=300, timeout=self._render_916)
        self._pv916_task: Task | None = None
        self._pv916_key = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 14)
        head = QHBoxLayout()
        self.title = QLabel("Clips")
        self.title.setTextFormat(Qt.PlainText)
        self.title.setObjectName("h1")
        self.title.setMinimumWidth(10)
        from PySide6.QtWidgets import QSizePolicy

        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.sub = QLabel("Öffne links unter „Start“ ein VOD.")
        self.sub.setTextFormat(Qt.PlainText)
        self.sub.setObjectName("muted")
        tv = QVBoxLayout()
        tv.addWidget(self.title)
        tv.addWidget(self.sub)
        head.addLayout(tv, 1)
        self.sort = QComboBox()
        self.sort.addItems(["Nach Score", "Nach Zeit"])
        self.sort.currentIndexChanged.connect(self._fill_list)
        self.show_rejected = QCheckBox("Verworfene zeigen")
        self.show_rejected.toggled.connect(self._fill_list)
        self.facecam_btn = QPushButton("Facecam …")
        self.facecam_btn.clicked.connect(self._facecam)
        head.addWidget(self.sort)
        head.addWidget(self.show_rejected)
        head.addWidget(self.facecam_btn)
        root.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setMinimumWidth(380)
        self.list.currentRowChanged.connect(self._select)
        split.addWidget(self.list)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 0, 0, 0)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(300)
        self.video.setStyleSheet("background:#000;")
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._pos)
        # Standbild des Clipstarts, bis abgespielt wird (Video zeichnet Windows erst beim Abspielen)
        from PySide6.QtWidgets import QStackedWidget

        self.poster = QLabel()
        self.poster.setAlignment(Qt.AlignCenter)
        self.poster.setStyleSheet("background:#000;")
        self.poster.setMinimumHeight(300)
        self.view = QStackedWidget()
        self.view.addWidget(self.poster)
        self.view.addWidget(self.video)
        viewrow = QHBoxLayout()
        viewrow.setSpacing(10)
        viewrow.addWidget(self.view, 1)
        side = QVBoxLayout()
        side.setSpacing(4)
        cap = QLabel("So sieht der Clip aus (9:16)")
        cap.setObjectName("muted")
        cap.setAlignment(Qt.AlignCenter)
        self.pv916 = QLabel("Rendere Vorschau …")
        self.pv916.setObjectName("muted")
        self.pv916.setAlignment(Qt.AlignCenter)
        self.pv916.setFixedWidth(200)
        self.pv916.setMinimumHeight(300)
        self.pv916.setStyleSheet(f"background:#000; border:1px solid {theme.LINE}; border-radius:8px;")
        self.pv916.setToolTip("Mittleres Bild des Clips im gewählten Layout – genau so wird exportiert")
        side.addWidget(cap)
        side.addWidget(self.pv916, 1)
        viewrow.addLayout(side)
        rl.addLayout(viewrow, 3)
        self.range = RangeSlider()
        self.range.changed.connect(self._range_changed)
        self.range.seek.connect(lambda t: self.player.setPosition(int(t * 1000)))
        rl.addWidget(self.range)
        ctl = QHBoxLayout()
        self.play_btn = QPushButton("▶ Abspielen")
        self.play_btn.clicked.connect(self._toggle_play)
        nudge = []
        for label, which, d, tip in (("◀ Start", "s", -1, "Start 1 s früher"), ("Start ▶", "s", 1, "Start 1 s später"),
                                     ("◀ Ende", "e", -1, "Ende 1 s früher"), ("Ende ▶", "e", 1, "Ende 1 s später")):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, w=which, dd=d: self._nudge(w, dd))
            nudge.append(b)
        self.layout_box = QComboBox()
        for k, v in LAYOUTS.items():
            self.layout_box.addItem(v, k)
        self.layout_box.setMinimumWidth(240)
        self.layout_box.currentIndexChanged.connect(self._edited)
        self.preview_btn = QPushButton("Als Video ansehen")
        self.preview_btn.setToolTip("Den ganzen Clip in 9:16 rendern und im Player öffnen")
        self.preview_btn.clicked.connect(self._preview)
        ctl.addWidget(self.play_btn)
        self.nudge_btns = nudge
        for b in nudge:
            ctl.addWidget(b)
        ctl.addStretch(1)
        ctl.addWidget(QLabel("Layout"))
        ctl.addWidget(self.layout_box)
        ctl.addWidget(self.preview_btn)
        rl.addLayout(ctl)

        # Status-Leiste: Freigeben/Verwerfen immer sichtbar
        act = QHBoxLayout()
        self.state_lbl = QLabel("")
        self.state_lbl.setTextFormat(Qt.PlainText)
        self.state_lbl.setObjectName("h2")
        self.approve_btn = QPushButton("✓ Freigeben")
        self.approve_btn.setObjectName("good")
        self.approve_btn.setToolTip("Strg+Enter")
        self.approve_btn.clicked.connect(lambda: self._set_status(STATUS_APPROVED))
        self.reject_btn = QPushButton("✕ Verwerfen")
        self.reject_btn.setObjectName("bad")
        self.reject_btn.setToolTip("Strg+Entf")
        self.reject_btn.clicked.connect(lambda: self._set_status(STATUS_REJECTED))
        act.addWidget(self.state_lbl, 1)
        act.addWidget(self.approve_btn)
        act.addWidget(self.reject_btn)
        rl.addLayout(act)

        # Bearbeiten in Reitern statt alles auf einmal
        self.edit_tabs = QTabWidget()
        details = QWidget()
        left_form = QFormLayout(details)
        left_form.setContentsMargins(14, 12, 14, 12)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Titel für TikTok, Shorts, Reels")
        self.title_edit.textEdited.connect(self._edited)
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setMaximumHeight(64)
        self.desc_edit.textChanged.connect(self._edited)
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("#gaming #fail …")
        self.tags_edit.textEdited.connect(self._edited)
        self.reason = QLabel("")
        self.reason.setTextFormat(Qt.PlainText)
        self.reason.setWordWrap(True)
        self.reason.setObjectName("muted")
        left_form.addRow("Titel", self.title_edit)
        left_form.addRow("Beschreibung", self.desc_edit)
        left_form.addRow("Hashtags", self.tags_edit)
        left_form.addRow("Grund", self.reason)
        self.edit_tabs.addTab(details, "Titel && Hashtags")
        subs = QWidget()
        tr = QVBoxLayout(subs)
        tr.setContentsMargins(14, 12, 14, 12)
        hint = QLabel("Das Transkript dieses Clips. Fehler hier korrigieren – gleiche Wortzahl behält die exakten Zeiten.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        tr.addWidget(hint)
        self.transcript = QPlainTextEdit()
        self.transcript.setMaximumHeight(120)
        tr.addWidget(self.transcript)
        apply_tr = QPushButton("Text übernehmen")
        apply_tr.clicked.connect(self._apply_transcript)
        tr.addWidget(apply_tr, 0, Qt.AlignRight)
        self.edit_tabs.addTab(subs, "Untertitel")
        rl.addWidget(self.edit_tabs)

        from PySide6.QtGui import QKeySequence, QShortcut

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=lambda: self._set_status(STATUS_APPROVED))
        QShortcut(QKeySequence("Ctrl+Delete"), self, activated=lambda: self._set_status(STATUS_REJECTED))
        QShortcut(QKeySequence("Ctrl+Space"), self, activated=self._toggle_play)
        # ohne ausgewählten Clip: Hinweis statt leerem Player und ausgegrauten Feldern
        self.right_stack = QStackedWidget()
        none = QWidget()
        nl = QVBoxLayout(none)
        nl.setAlignment(Qt.AlignCenter)
        ni = QLabel("✂")
        ni.setStyleSheet(f"font-size:40pt; color:{theme.ACCENT};")
        self.none_title = QLabel("Keine Clips in dieser Ansicht")
        self.none_title.setObjectName("h2")
        self.none_text = QLabel("")
        self.none_text.setObjectName("muted")
        self.none_text.setWordWrap(True)
        self.none_text.setMaximumWidth(460)
        for w in (ni, self.none_title, self.none_text):
            w.setAlignment(Qt.AlignCenter)
            nl.addWidget(w, 0, Qt.AlignHCenter)
        self.right_stack.addWidget(none)
        self.right_stack.addWidget(right)
        split.addWidget(self.right_stack)
        split.setStretchFactor(1, 1)
        from PySide6.QtWidgets import QStackedWidget as _Stack

        self.body = _Stack()
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.setAlignment(Qt.AlignCenter)
        icon = QLabel("🎬")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size:42pt;")
        et = QLabel("Noch kein VOD geöffnet")
        et.setObjectName("h1")
        et.setAlignment(Qt.AlignCenter)
        es = QLabel("Analysiere unter „Start“ ein VOD oder öffne eines aus „Zuletzt bearbeitet“.")
        es.setObjectName("muted")
        es.setAlignment(Qt.AlignCenter)
        go = QPushButton("Zur Startseite")
        go.setObjectName("primary")
        go.clicked.connect(self.go_start.emit)
        for w in (icon, et, es):
            el.addWidget(w)
        el.addWidget(go, 0, Qt.AlignCenter)
        self.body.addWidget(empty)
        self.body.addWidget(split)
        root.addWidget(self.body, 1)

        bottom = QFrame()
        bottom.setObjectName("card")
        bl = QHBoxLayout(bottom)
        self.export_info = QLabel("")
        self.folder = QLabel(settings.export_dir)
        self.folder.setObjectName("muted")
        change = QPushButton("Ordner …")
        change.clicked.connect(self._choose_folder)
        open_folder = QPushButton("Ordner öffnen")
        open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.s.export_dir)))
        self.export_bar = QProgressBar()
        self.export_bar.setRange(0, 1000)
        self.export_bar.setMaximumWidth(220)
        self.export_bar.hide()
        self.export_btn = QPushButton("Freigegebene exportieren")
        self.export_btn.setObjectName("primary")
        self.export_btn.clicked.connect(self._export)
        bl.addWidget(self.export_info)
        bl.addSpacing(12)
        bl.addWidget(self.folder, 1)
        bl.addWidget(change)
        bl.addWidget(open_folder)
        bl.addWidget(self.export_bar)
        bl.addWidget(self.export_btn)
        root.addWidget(bottom)
        self._enable_editor(False)

    def showEvent(self, e):
        # Listeneinträge, die gefüllt wurden, während die Seite verdeckt war, neu zeichnen
        super().showEvent(e)
        if self.proj:
            self._fill_list()

    # ------------------------------------------------------------------ Laden
    def open_project(self, pid: str):
        self.s = Settings.load()
        self.proj = Project(pid)
        self.cur = None  # Editor neu laden
        self.clips = self.proj.load_clips()
        self.words = self.proj.load_words()
        src = self.proj.source
        full = src.title if src else "Clips"
        self.title.setToolTip(full)
        self.title.setText(full if len(full) <= 60 else full[:58].rstrip() + " …")
        fcm = Facecam.from_dict(self.proj.state.get("facecam"))
        cam_txt = {"overlay": "Facecam erkannt", "fullcam": "Nur-Kamera", "none": "keine Facecam"}.get(
            fcm.mode if fcm else "", "Facecam unbekannt")
        self.sub.setText(f"{src.streamer or 'Datei'} · {src.platform} · {fmt_time(self.proj.media.get('duration', 0))}"
                         f" · {len(self.clips)} Clips · {cam_txt} · API-Kosten: {self.proj.cost_text()}")
        self.player.setSource(QUrl.fromLocalFile(str(src.path)))
        self.body.setCurrentIndex(1)
        self._fill_list()
        self._make_thumbs()

    def _thumb(self, c: Clip) -> Path:
        return self.proj.dir / "thumbs" / f"{c.id}.jpg"

    def _make_thumbs(self):
        proj, clips = self.proj, list(self.clips)
        missing = [c for c in clips if not self._thumb(c).exists()]
        if not missing:
            return

        def job(progress, cancel, confirm):
            import cv2

            (proj.dir / "thumbs").mkdir(exist_ok=True)
            for c in missing:
                fr = read_frames(proj.source.path, [(c.start + c.end) / 2], max_w=320)
                if fr:
                    cv2.imwrite(str(proj.dir / "thumbs" / f"{c.id}.jpg"), fr[0][1])
            return True

        t = Task(job)
        t.finished.connect(lambda _: self._fill_list())
        self._track(t)
        t.start()

    def _track(self, t: Task) -> None:
        """Task festhalten, solange er läuft, danach freigeben."""
        self.tasks.append(t)
        t._ended.connect(lambda t=t: self.tasks.remove(t) if t in self.tasks else None)

    def _visible(self) -> list[Clip]:
        cl = [c for c in self.clips if self.show_rejected.isChecked() or c.status != STATUS_REJECTED]
        if self.sort.currentIndex() == 0:
            return sorted(cl, key=lambda c: (-(c.score or 0), c.start))
        return sorted(cl, key=lambda c: c.start)

    def _fill_list(self):
        if not self.proj:
            return
        keep = self.cur.id if self.cur else None
        self.list.blockSignals(True)
        self.list.clear()
        row_sel = 0
        for i, c in enumerate(self._visible()):
            it = QListWidgetItem()
            it.setData(Qt.UserRole, c.id)
            it.setSizeHint(QSize(360, 92))
            self.list.addItem(it)
            self.list.setItemWidget(it, ClipRow(c, self._thumb(c)))
            if c.id == keep:
                row_sel = i
        self.list.blockSignals(False)
        approved = sum(c.status == STATUS_APPROVED for c in self.clips)
        self.export_info.setText(f"{approved} freigegeben · {len(self.clips)} gesamt")
        self.export_btn.setEnabled(approved > 0 or self._exporting)
        if self.list.count():
            self.list.blockSignals(True)
            self.list.setCurrentRow(row_sel)
            self.list.blockSignals(False)
            if keep and self.cur and self.list.item(row_sel).data(Qt.UserRole) == keep:
                # derselbe Clip bleibt ausgewählt: ungespeicherte Eingaben im Editor nicht überschreiben
                self.state_lbl.setText(self._state_text(self.cur, row_sel))
            else:
                self._select(row_sel)
        else:
            hidden = sum(c.status == STATUS_REJECTED for c in self.clips)
            it = QListWidgetItem("Keine Clips in dieser Ansicht.")
            it.setFlags(Qt.NoItemFlags)
            it.setSizeHint(QSize(360, 56))
            self.list.addItem(it)
            if hidden and not self.show_rejected.isChecked():
                self.none_title.setText(f"{hidden} verworfene Clips ausgeblendet")
                self.none_text.setText("Oben „Verworfene zeigen“ anhaken, um sie wieder zu sehen.")
            else:
                self.none_title.setText("Keine Clips in dieser Ansicht")
                self.none_text.setText("In den Einstellungen unter „Trigger“ eigene Begriffe ergänzen oder die "
                                       "Chat-Empfindlichkeit senken, dann das VOD neu analysieren.")
            self.cur = None
            self.state_lbl.setText("Kein Clip ausgewählt")
            self._enable_editor(False)

    def _enable_editor(self, on: bool):
        for w in (self.title_edit, self.desc_edit, self.tags_edit, self.transcript, self.approve_btn, self.reject_btn,
                  self.layout_box, self.play_btn, self.range, *self.nudge_btns):
            w.setEnabled(on)
        self.preview_btn.setEnabled(on and not self._previewing)
        self.right_stack.setCurrentIndex(1 if on else 0)
        if not on:
            self.player.pause()

    # ------------------------------------------------------------------ Auswahl + Bearbeiten
    def _select(self, row: int):
        if row < 0 or not self.proj:
            return
        cid = self.list.item(row).data(Qt.UserRole)
        c = next((x for x in self.clips if x.id == cid), None)
        if not c:
            return
        self._loading = True
        self.cur = c
        self._enable_editor(True)
        self.state_lbl.setText(self._state_text(c, row))
        self.title_edit.setText(c.title)
        self.desc_edit.setPlainText(c.description)
        self.tags_edit.setText(" ".join(c.hashtags))
        self.title_edit.setCursorPosition(0)
        self.tags_edit.setCursorPosition(0)
        self.reason.setText("; ".join(c.reasons) + (f"\nKI: {c.ai_reason}" if c.ai_reason else ""))
        idx = self.layout_box.findData(c.layout or self.s.default_layout)
        self.layout_box.setCurrentIndex(max(0, idx))
        dur = self.proj.media.get("duration", c.end + 60)
        lo, hi = max(0.0, c.start - 45), min(dur, c.end + 30)
        self.range.set_range(lo, hi, c.start, c.end, [c.trigger_time])
        self._show_transcript()
        self._loading = False
        self.player.setPosition(int(c.start * 1000))
        self._show_still()
        self._pv916_timer.start()

    def _render_916(self):
        """Ein Bild aus der Clipmitte im gewählten Layout rendern (unter 1 s) und neben dem Player zeigen."""
        c, proj = self.cur, self.proj
        if not c or not proj or not proj.source or not proj.source.path.exists():
            return
        if self._pv916_task is not None:  # läuft noch: danach erneut
            self._pv916_timer.start()
            return
        import hashlib
        import json

        cam = Facecam.from_dict(proj.state.get("facecam"))
        layout = c.layout or self.s.default_layout
        at = round((c.start + c.end) / 2, 1)
        key = hashlib.sha1(json.dumps([layout, at, proj.state.get("facecam")], sort_keys=True,
                                      default=str).encode()).hexdigest()[:12]
        out = proj.dir / "pv916" / f"{key}.png"
        self._pv916_key = key
        if out.exists():
            self._show_916(out, key)
            return
        out.parent.mkdir(exist_ok=True)
        media = proj.media

        def job(progress, cancel, confirm):
            return render.preview_frame(proj.source.path, at, layout, cam, media, out)

        t = Task(job)
        self._pv916_task = t
        t.finished.connect(lambda p, k=key: (setattr(self, "_pv916_task", None), self._show_916(p, k)))
        t.failed.connect(lambda m, h: (setattr(self, "_pv916_task", None), self.pv916.setText(m)))
        self._track(t)
        t.start()

    def _show_916(self, path, key):
        if key != self._pv916_key:  # inzwischen anderer Clip/anderes Layout gewählt
            self._pv916_timer.start()
            return
        pm = QPixmap(str(path))
        if pm.isNull():
            return
        self._pv916_pm = pm
        self.pv916.setPixmap(pm.scaled(self.pv916.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _state_text(self, c: Clip, row: int) -> str:
        label = STATUS_STYLE.get(c.status, STATUS_STYLE[STATUS_NEW])[0]
        score = "–" if c.score is None else f"{c.score:.0f}/10"
        return f"Clip {row + 1} von {self.list.count()} · Score {score} · {label}"

    def _show_still(self):
        """Standbild am Clipstart anzeigen, bis der Nutzer abspielt."""
        c = self.cur
        if not c or not self.proj:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        self.play_btn.setText("▶ Abspielen")
        self.view.setCurrentWidget(self.poster)
        try:
            fr = read_frames(self.proj.source.path, [c.start + 0.5], max_w=1280)
        except Exception as e:  # Videodatei verschoben/gelöscht: ohne Standbild weiter
            log.warning("Standbild nicht lesbar: %s", e)
            fr = None
            self._poster_pm = None
            self.poster.setText("Videodatei nicht gefunden")
        if fr:
            pm = QPixmap.fromImage(bgr_to_qimage(fr[0][1]))
            self._poster_pm = pm
            self.poster.setPixmap(pm.scaled(self.poster.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_pv916_pm", None):
            self.pv916.setPixmap(self._pv916_pm.scaled(self.pv916.size(), Qt.KeepAspectRatio,
                                                       Qt.SmoothTransformation))
        if getattr(self, "_poster_pm", None):
            self.poster.setPixmap(self._poster_pm.scaled(self.poster.size(), Qt.KeepAspectRatio,
                                                         Qt.SmoothTransformation))

    def _show_transcript(self):
        c = self.cur
        if c.edited_words:
            text = " ".join(w for w, _, _ in c.edited_words)
        else:
            text = " ".join(w.w for w in self.words if c.start <= w.s < c.end)
        self.transcript.setPlainText(text)

    def _edited(self, *_):
        if self._loading or not self.cur:
            return
        c = self.cur
        c.title = self.title_edit.text().strip()
        c.description = self.desc_edit.toPlainText().strip()
        c.hashtags = [t if t.startswith("#") else f"#{t}" for t in self.tags_edit.text().split() if t.strip("#")]
        c.layout = self.layout_box.currentData()
        self._save_timer.start()
        self._pv916_timer.start()

    def _range_changed(self, start: float, end: float):
        if self._loading or not self.cur:
            return
        self.cur.start, self.cur.end = start, end
        self.cur.user_bounds = True
        if self.cur.edited_words:  # Korrektur gilt für den alten Bereich: neu ausrichten lassen
            self.cur.edited_words = []
        self._show_transcript()
        self._save_timer.start()
        self._pv916_timer.start()

    def _nudge(self, which: str, d: float):
        if not self.cur:
            return
        s, e = self.cur.start, self.cur.end
        if which == "s":
            s = max(0.0, min(s + d, e - 3))
        else:
            e = max(s + 3, e + d)
        self.range.set_range(self.range.lo, max(self.range.hi, e), s, e, self.range.marks)
        self._range_changed(s, e)
        self.player.setPosition(int((s if which == "s" else max(s, e - 3)) * 1000))

    def _apply_transcript(self):
        c = self.cur
        if not c:
            return
        orig = [w for w in self.words if c.start <= w.s < c.end]
        c.edited_words = align_edit(orig, self.transcript.toPlainText())
        self._save()
        self.status_message.emit("Untertitel-Text übernommen.")

    def _set_status(self, st: str):
        if not self.cur:
            return
        self.cur.status = st
        self._save()
        rows = self.list.count()
        cur = self.list.currentRow()
        self._fill_list()
        if st == STATUS_REJECTED and not self.show_rejected.isChecked():
            self.list.setCurrentRow(min(cur, self.list.count() - 1))
        elif cur + 1 < rows:
            self.list.setCurrentRow(cur + 1)

    def _save(self):
        if self.proj:
            self.proj.save_clips(self.clips)

    # ------------------------------------------------------------------ Player
    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_btn.setText("▶ Abspielen")
        else:
            if self.cur and not (self.cur.start <= self.player.position() / 1000 < self.cur.end):
                self.player.setPosition(int(self.cur.start * 1000))
            self.view.setCurrentWidget(self.video)
            self.player.play()
            self.play_btn.setText("⏸ Pause")

    def _pos(self, ms: int):
        t = ms / 1000
        self.range.set_pos(t)
        if self.cur and self.player.playbackState() == QMediaPlayer.PlayingState and t >= self.cur.end:
            self.player.setPosition(int(self.cur.start * 1000))  # Schleife über den Clip

    # ------------------------------------------------------------------ Facecam, Vorschau, Export
    def _facecam(self):
        if not self.proj:
            return
        at = (self.cur.start + self.cur.end) / 2 if self.cur else self.proj.media.get("duration", 60) / 2
        dlg = FacecamDialog(Pipeline(Settings.load()), self.proj, at, self)
        ok = dlg.exec()
        dlg.deleteLater()
        if ok:
            self.open_project(self.proj.id)
            self.status_message.emit("Facecam gespeichert.")

    def _preview(self):
        if not self.cur:
            return
        proj, clip = self.proj, Clip.from_dict(self.cur.to_dict())
        out = Path(tempfile.gettempdir()) / f"clipforge_vorschau_{clip.id}.mp4"
        if self._previewing:
            return
        self._previewing = True
        self.preview_btn.setEnabled(False)
        self.preview_btn.setText("Rendere …")
        s = Settings.load()

        def job(progress, cancel, confirm):
            pipe = Pipeline(s, progress, cancel)
            render.render(pipe.render_job(proj, clip, out, pipe.facecam(proj)), cancel=cancel, media=proj.media)
            return out

        t = Task(job)
        t.finished.connect(self._preview_done)
        t.failed.connect(lambda m, h: (self._preview_reset(), QMessageBox.warning(self, "Vorschau", f"{m}\n\n{h}")))
        t.cancelled.connect(self._preview_reset)
        self._track(t)
        t.start()

    def _preview_reset(self):
        self._previewing = False
        self.preview_btn.setEnabled(self.cur is not None)
        self.preview_btn.setText("Als Video ansehen")

    def _preview_done(self, out: Path):
        self._preview_reset()
        os.startfile(str(out))  # im Standard-Player öffnen

    def _choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Exportordner wählen", self.s.export_dir)
        if d:
            s = Settings.load()
            s.export_dir = d
            s.save()
            self.s = s
            self.folder.setText(d)

    def _export(self):
        if self._exporting:  # Knopf ist während des Exports „Abbrechen“
            if self.export_task:
                self.export_task.cancel.set()
                self.export_btn.setEnabled(False)
                self.export_btn.setText("Breche ab …")
            return
        if not self.proj:
            return
        self._save()
        proj = self.proj
        s = Settings.load()

        def job(progress, cancel, confirm):
            return Pipeline(s, progress, cancel).export(proj, only_approved=True)

        self._exporting = True
        self.export_btn.setText("Export abbrechen")
        self.export_btn.setObjectName("bad")
        self.export_btn.style().polish(self.export_btn)
        self.export_bar.show()
        t = Task(job)
        self.export_task = t
        t.progress.connect(lambda st, f, m: (self.export_bar.setValue(int(f * 1000)), self.status_message.emit(m)))
        t.finished.connect(self._export_done)
        t.failed.connect(lambda m, h: (self._export_reset(), QMessageBox.warning(self, "Export", f"{m}\n\n{h}")))
        t.cancelled.connect(lambda: (self._export_reset(), self.open_project(self.proj.id),
                                     self.status_message.emit("Export abgebrochen.")))
        self._track(t)
        t.start()

    def _export_reset(self):
        self._exporting = False
        self.export_task = None
        self.export_bar.hide()
        self.export_btn.setText("Freigegebene exportieren")
        self.export_btn.setObjectName("primary")
        self.export_btn.style().polish(self.export_btn)
        self.export_btn.setEnabled(any(c.status == STATUS_APPROVED for c in self.clips))

    def _export_done(self, paths):
        self._export_reset()
        self.open_project(self.proj.id)
        r = QMessageBox.information(self, "Export fertig", f"{len(paths)} Clips exportiert nach\n{self.s.export_dir}",
                                    QMessageBox.Open | QMessageBox.Ok)
        if r == QMessageBox.Open:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.s.export_dir))
