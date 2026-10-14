"""Clip-Übersicht und Editor: prüfen, anpassen, freigeben, exportieren."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

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
        title = QLabel(clip.title or "Ohne Titel")
        title.setStyleSheet("font-weight:700;")
        meta = QLabel(f"{fmt_time(clip.start)} – {fmt_time(clip.end)} · {clip.duration:.0f} s")
        meta.setObjectName("muted")
        reason = QLabel("; ".join(clip.reasons)[:90])
        reason.setObjectName("muted")
        reason.setStyleSheet("font-size:8.5pt;")
        for w in (title, meta, reason):
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
        self.resize(1000, 680)
        self.pipe, self.proj = pipe, proj
        lay = QVBoxLayout(self)
        info = QLabel("Ziehe ein Rechteck um die Facecam. Wird als Profil für diesen Streamer gespeichert "
                      "und bei künftigen VODs automatisch benutzt.")
        info.setWordWrap(True)
        info.setObjectName("muted")
        lay.addWidget(info)
        self.sel = RectSelector()
        lay.addWidget(self.sel, 1)
        frames = read_frames(proj.source.path, [at])
        cur = Facecam.from_dict(proj.state.get("facecam"))
        if frames:
            self.sel.set_image(bgr_to_qimage(frames[0][1]), cur.cam if cur and cur.mode == "overlay" else None)
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
            f" · {src.get(f.source, f.source)} · Sicherheit {f.confidence:.0%}"

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
        self.proj.state["facecam"] = f.to_dict()
        self.proj.save()
        self.sel.rect_norm = f.cam if f.mode == "overlay" else None
        self.sel.update()
        self.state.setText(self._describe(f))

    def _none(self):
        self.sel.rect_norm = None
        self._none_chosen = True
        self.sel.update()

    def _accept(self):
        self.pipe.set_facecam(self.proj, self.sel.rect_norm, save_profile=self.profile.isChecked())
        self.accept()


class ClipsPage(QWidget):
    status_message = Signal(str)

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.proj: Project | None = None
        self.clips: list[Clip] = []
        self.words: list[Word] = []
        self.cur: Clip | None = None
        self.tasks: list[Task] = []
        self._loading = False
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self._save)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 14)
        head = QHBoxLayout()
        self.title = QLabel("Clips")
        self.title.setObjectName("h1")
        self.title.setMinimumWidth(10)
        from PySide6.QtWidgets import QSizePolicy

        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.sub = QLabel("Öffne links unter „Start“ ein VOD.")
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
        rl.addWidget(self.video, 3)
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
        self.preview_btn = QPushButton("9:16-Vorschau")
        self.preview_btn.clicked.connect(self._preview)
        ctl.addWidget(self.play_btn)
        for b in nudge:
            ctl.addWidget(b)
        ctl.addStretch(1)
        ctl.addWidget(QLabel("Layout"))
        ctl.addWidget(self.layout_box)
        ctl.addWidget(self.preview_btn)
        rl.addLayout(ctl)

        form_card = QFrame()
        form_card.setObjectName("card")
        fl = QHBoxLayout(form_card)
        left_form = QFormLayout()
        self.title_edit = QLineEdit()
        self.title_edit.textEdited.connect(self._edited)
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setMaximumHeight(64)
        self.desc_edit.textChanged.connect(self._edited)
        self.tags_edit = QLineEdit()
        self.tags_edit.textEdited.connect(self._edited)
        self.reason = QLabel("")
        self.reason.setWordWrap(True)
        self.reason.setObjectName("muted")
        left_form.addRow("Titel", self.title_edit)
        left_form.addRow("Beschreibung", self.desc_edit)
        left_form.addRow("Hashtags", self.tags_edit)
        left_form.addRow("Grund", self.reason)
        fl.addLayout(left_form, 3)
        tr = QVBoxLayout()
        tr.addWidget(QLabel("Untertitel-Text (Fehler hier korrigieren)"))
        self.transcript = QPlainTextEdit()
        self.transcript.setMaximumHeight(110)
        tr.addWidget(self.transcript)
        apply_tr = QPushButton("Text übernehmen")
        apply_tr.clicked.connect(self._apply_transcript)
        tr.addWidget(apply_tr, 0, Qt.AlignRight)
        fl.addLayout(tr, 2)
        rl.addWidget(form_card)

        act = QHBoxLayout()
        self.approve_btn = QPushButton("✓ Freigeben")
        self.approve_btn.setObjectName("good")
        self.approve_btn.clicked.connect(lambda: self._set_status(STATUS_APPROVED))
        self.reject_btn = QPushButton("✕ Verwerfen")
        self.reject_btn.setObjectName("bad")
        self.reject_btn.clicked.connect(lambda: self._set_status(STATUS_REJECTED))
        act.addWidget(self.approve_btn)
        act.addWidget(self.reject_btn)
        act.addStretch(1)
        rl.addLayout(act)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

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
        self.tasks.append(t)
        t.start()

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
        self.export_btn.setEnabled(approved > 0)
        if self.list.count():
            self.list.setCurrentRow(row_sel)
            self._select(row_sel)
        else:
            self._enable_editor(False)

    def _enable_editor(self, on: bool):
        for w in (self.title_edit, self.desc_edit, self.tags_edit, self.transcript, self.approve_btn, self.reject_btn,
                  self.layout_box, self.preview_btn, self.play_btn, self.range):
            w.setEnabled(on)

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
        self.title_edit.setText(c.title)
        self.desc_edit.setPlainText(c.description)
        self.tags_edit.setText(" ".join(c.hashtags))
        self.reason.setText("; ".join(c.reasons) + (f"\nKI: {c.ai_reason}" if c.ai_reason else ""))
        idx = self.layout_box.findData(c.layout or self.s.default_layout)
        self.layout_box.setCurrentIndex(max(0, idx))
        dur = self.proj.media.get("duration", c.end + 60)
        lo, hi = max(0.0, c.start - 45), min(dur, c.end + 30)
        self.range.set_range(lo, hi, c.start, c.end, [c.trigger_time])
        self._show_transcript()
        self._loading = False
        self.player.setPosition(int(c.start * 1000))
        self.player.pause()

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

    def _range_changed(self, start: float, end: float):
        if self._loading or not self.cur:
            return
        self.cur.start, self.cur.end = start, end
        if self.cur.edited_words:  # Korrektur gilt für den alten Bereich: neu ausrichten lassen
            self.cur.edited_words = []
        self._show_transcript()
        self._save_timer.start()

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
        dlg = FacecamDialog(Pipeline(self.s), self.proj, at, self)
        if dlg.exec():
            self.open_project(self.proj.id)
            self.status_message.emit("Facecam gespeichert.")

    def _preview(self):
        if not self.cur:
            return
        proj, clip = self.proj, Clip.from_dict(self.cur.to_dict())
        out = Path(tempfile.gettempdir()) / f"clipforge_vorschau_{clip.id}.mp4"
        self.preview_btn.setEnabled(False)
        self.preview_btn.setText("Rendere …")
        s = Settings.load()

        def job(progress, cancel, confirm):
            pipe = Pipeline(s, progress, cancel)
            render.render(pipe.render_job(proj, clip, out, pipe.facecam(proj)), cancel=cancel, media=proj.media)
            return out

        t = Task(job)
        t.finished.connect(self._preview_done)
        t.failed.connect(lambda m, h: (QMessageBox.warning(self, "Vorschau", f"{m}\n\n{h}"), self._preview_reset()))
        self.tasks.append(t)
        t.start()

    def _preview_reset(self):
        self.preview_btn.setEnabled(True)
        self.preview_btn.setText("9:16-Vorschau")

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
        if not self.proj:
            return
        self._save()
        proj = self.proj
        s = Settings.load()

        def job(progress, cancel, confirm):
            return Pipeline(s, progress, cancel).export(proj, only_approved=True)

        self.export_btn.setEnabled(False)
        self.export_bar.show()
        t = Task(job)
        t.progress.connect(lambda st, f, m: (self.export_bar.setValue(int(f * 1000)), self.status_message.emit(m)))
        t.finished.connect(self._export_done)
        t.failed.connect(lambda m, h: (QMessageBox.warning(self, "Export", f"{m}\n\n{h}"), self._export_reset()))
        self.tasks.append(t)
        t.start()

    def _export_reset(self):
        self.export_bar.hide()
        self.export_btn.setEnabled(True)

    def _export_done(self, paths):
        self._export_reset()
        self.open_project(self.proj.id)
        r = QMessageBox.information(self, "Export fertig", f"{len(paths)} Clips exportiert nach\n{self.s.export_dir}",
                                    QMessageBox.Open | QMessageBox.Ok)
        if r == QMessageBox.Open:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.s.export_dir))
