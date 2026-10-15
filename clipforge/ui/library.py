"""VOD-Übersicht der Clip-Seite: jedes VOD als Karte, nach Kanal gruppiert. Ein Klick öffnet seine Clips."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from clipforge.pipeline.clips import STATUS_APPROVED, STATUS_EXPORTED, STATUS_REJECTED
from clipforge.pipeline.project import Project
from clipforge.ui import theme
from clipforge.ui.widgets import fmt_time
from clipforge.ui.worker import Task

CARD_W, IMG_H = 266, 150


class FlowLayout(QLayout):
    """Ordnet Karten zeilenweise an und bricht um, wenn der Platz endet."""

    def __init__(self, parent=None, spacing: int = 12):
        super().__init__(parent)
        self._items = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._place(QRect(0, 0, w, 0), dry=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._place(rect, dry=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _place(self, rect: QRect, dry: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x, y, line = rect.x(), y + line + self._sp, 0
            if not dry:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._sp
            line = max(line, hint.height())
        return y + line - rect.y()


class VodCard(QFrame):
    clicked = Signal(str)

    def __init__(self, p: Project):
        super().__init__()
        self.pid = p.id
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(CARD_W)
        src = p.source
        self.setToolTip(src.title)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 12)
        lay.setSpacing(4)
        img = QLabel()
        img.setFixedSize(CARD_W, IMG_H)
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet("background:#0b0d11; border-top-left-radius:12px; border-top-right-radius:12px;")
        cover = p.dir / "cover.jpg"
        if cover.exists():
            img.setPixmap(QPixmap(str(cover)).scaled(CARD_W, IMG_H, Qt.KeepAspectRatioByExpanding,
                                                     Qt.SmoothTransformation))
        else:
            img.setText("🎬")
        lay.addWidget(img)
        t = QLabel(src.title if len(src.title) <= 34 else src.title[:33].rstrip() + "…")
        t.setTextFormat(Qt.PlainText)
        t.setStyleSheet("font-weight:700; padding:0 12px;")
        meta = QLabel(f"{src.date or ''} · {fmt_time(p.media.get('duration', 0))}".strip(" ·"))
        meta.setTextFormat(Qt.PlainText)
        meta.setObjectName("muted")
        meta.setStyleSheet("padding:0 12px;")
        clips = p.load_clips()
        live = [c for c in clips if c.status != STATUS_REJECTED]
        ok = sum(c.status == STATUS_APPROVED for c in clips)
        done = sum(c.status == STATUS_EXPORTED for c in clips)
        best = max((c.score or 0 for c in live), default=0)
        stats = QLabel(f"{len(live)} Clips" + (f" · {ok} freigegeben" if ok else "")
                       + (f" · {done} exportiert" if done else "") + (f" · bester Score {best:.0f}" if best else ""))
        stats.setTextFormat(Qt.PlainText)
        stats.setObjectName("muted")
        stats.setStyleSheet("padding:0 12px; font-size:8.5pt;")
        for w in (t, meta, stats):
            lay.addWidget(w)
        if live and done == len(live):
            badge, color = "fertig", theme.GOOD
        elif ok or done:
            badge, color = "in Arbeit", theme.ACCENT
        else:
            badge, color = "neu", theme.MUTED
        b = QLabel(badge, img)
        b.setStyleSheet(f"background:rgba(10,12,16,190); color:{color}; border-radius:9px; padding:2px 9px;"
                        "font-size:8.5pt; font-weight:700;")
        b.adjustSize()
        b.move(CARD_W - b.width() - 8, 8)
        self.search_text = f"{src.title} {src.streamer} {src.date}".lower()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.pid)
        super().mouseReleaseEvent(e)


class VodLibrary(QWidget):
    """Alle analysierten VODs, gruppiert nach Kanal (lokale Dateien eigene Gruppe)."""

    open_project = Signal(str)
    go_start = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cover_task: Task | None = None
        self._cover_tried: set[str] = set()  # nicht endlos für fehlende Videodateien versuchen
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        head = QHBoxLayout()
        tv = QVBoxLayout()
        h = QLabel("Deine VODs")
        h.setObjectName("h1")
        self.count = QLabel("")
        self.count.setObjectName("muted")
        tv.addWidget(h)
        tv.addWidget(self.count)
        head.addLayout(tv, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen: Titel, Kanal, Datum …")
        self.search.setFixedWidth(300)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        head.addWidget(self.search, 0, Qt.AlignBottom)
        root.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.content = QWidget()
        self.vbox = QVBoxLayout(self.content)
        self.vbox.setContentsMargins(0, 0, 8, 0)
        self.vbox.setSpacing(10)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)

        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setAlignment(Qt.AlignCenter)
        icon = QLabel("🎬")
        icon.setStyleSheet("font-size:42pt;")
        et = QLabel("Noch keine VODs")
        et.setObjectName("h1")
        es = QLabel("Analysiere unter „Start“ ein VOD – es erscheint dann hier mit seinen Clips.")
        es.setObjectName("muted")
        go = QPushButton("Zur Startseite")
        go.setObjectName("primary")
        go.clicked.connect(self.go_start.emit)
        for w in (icon, et, es):
            w.setAlignment(Qt.AlignCenter)
            el.addWidget(w)
        el.addWidget(go, 0, Qt.AlignCenter)
        root.addWidget(self.empty, 1)
        self.cards: list[tuple[VodCard, QWidget]] = []
        self.groups: list[QWidget] = []

    # ------------------------------------------------------------------ Aufbau
    def refresh(self):
        while self.vbox.count():
            it = self.vbox.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.cards, self.groups = [], []
        projects = [p for p in Project.all() if p.source]
        self.empty.setVisible(not projects)
        self.scroll.setVisible(bool(projects))
        self.search.setVisible(bool(projects))
        n_streams = len({p.source.streamer or "" for p in projects})
        self.count.setText(f"{len(projects)} VOD{'s' if len(projects) != 1 else ''} · "
                           f"{n_streams} {'Kanal' if n_streams == 1 else 'Kanäle'} · zum Öffnen anklicken"
                           if projects else "")
        groups: dict[str, list[Project]] = {}
        for p in projects:  # Project.all() ist neueste zuerst: Gruppen erscheinen nach letzter Aktivität
            key = f"{p.source.streamer} · {p.source.platform}" if p.source.streamer else "Lokale Dateien"
            groups.setdefault(key, []).append(p)
        missing = []
        for name, items in groups.items():
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 6, 0, 6)
            bl.setSpacing(8)
            n_clips = sum(len([c for c in p.load_clips() if c.status != STATUS_REJECTED]) for p in items)
            hl = QLabel(f"{name}")
            hl.setObjectName("h2")
            hl.setTextFormat(Qt.PlainText)
            sub = QLabel(f"{len(items)} VOD{'s' if len(items) != 1 else ''} · {n_clips} Clips")
            sub.setObjectName("muted")
            row = QHBoxLayout()
            row.addWidget(hl)
            row.addWidget(sub)
            row.addStretch(1)
            bl.addLayout(row)
            grid = QWidget()
            grid.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            flow = FlowLayout(grid)
            for p in items:
                card = VodCard(p)
                card.clicked.connect(self.open_project.emit)
                flow.addWidget(card)
                self.cards.append((card, box))
                if not (p.dir / "cover.jpg").exists() and p.id not in self._cover_tried:
                    missing.append(p)
            bl.addWidget(grid)
            self.vbox.addWidget(box)
            self.groups.append(box)
        self.vbox.addStretch(1)
        self._filter(self.search.text())
        if missing and self._cover_task is None:
            self._make_covers(missing)

    def _filter(self, text: str):
        q = text.strip().lower()
        visible: dict[int, bool] = {}
        for card, box in self.cards:
            show = not q or all(w in card.search_text for w in q.split())
            card.setVisible(show)
            visible[id(box)] = visible.get(id(box), False) or show
        for box in self.groups:
            box.setVisible(visible.get(id(box), False))

    def _make_covers(self, projects):
        """Vorschaubilder für die Karten im Hintergrund erzeugen."""

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

        self._cover_tried.update(p.id for p in projects)
        self._cover_task = Task(job)
        self._cover_task.finished.connect(lambda _: (setattr(self, "_cover_task", None), self.refresh()))
        self._cover_task.failed.connect(lambda *_: setattr(self, "_cover_task", None))
        self._cover_task.start()
