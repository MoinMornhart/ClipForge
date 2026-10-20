"""Eigene Bedienelemente: Ablagefläche, Doppel-Schieberegler, Rechteck-Auswahl, umbrechende Knopfzeile."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QLabel, QLayout, QLayoutItem, QScrollArea, QSizePolicy, QSpacerItem,
                               QVBoxLayout, QWidget)

from clipforge.ui import theme

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".flv", ".ts"}


class DropZone(QFrame):
    """Videodatei hineinziehen oder Link einfügen."""

    dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setMinimumHeight(120)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        icon = QLabel("⬇")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(f"font-size: 30pt; color: {theme.ACCENT};")
        title = QLabel("Videodatei hierher ziehen")
        title.setObjectName("h2")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("MP4, MKV, MOV, WEBM · oder oben einen Twitch-, Kick- oder YouTube-Link einfügen")
        sub.setObjectName("muted")
        sub.setAlignment(Qt.AlignCenter)
        for w in (icon, title, sub):
            lay.addWidget(w)

    def _set_hover(self, on: bool) -> None:
        self.setProperty("hover", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasUrls() and any(Path(u.toLocalFile()).suffix.lower() in VIDEO_EXTS for u in md.urls()):
            e.acceptProposedAction()
            self._set_hover(True)
        elif md.hasText():
            e.acceptProposedAction()
            self._set_hover(True)

    def dragLeaveEvent(self, e):
        self._set_hover(False)

    def dropEvent(self, e):
        self._set_hover(False)
        md = e.mimeData()
        for u in md.urls():
            p = u.toLocalFile()
            if p and Path(p).suffix.lower() in VIDEO_EXTS:
                self.dropped.emit(p)
                return
            if u.scheme() in ("http", "https"):
                self.dropped.emit(u.toString())
                return
        if md.hasText():
            self.dropped.emit(md.text().strip())


class FlowLayout(QLayout):
    """Knopfzeile, die bei Platzmangel in die nächste Zeile umbricht, statt Knöpfe übereinanderzuschieben
    (kleines Fenster). addStretch() schiebt die folgenden Elemente nach rechts – wie bei QHBoxLayout, solange
    sie in derselben Zeile landen."""

    def __init__(self, parent=None, spacing: int = 8, line_spacing: int = 6):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h, self._v = spacing, line_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem):
        self._items.append(item)

    def addStretch(self):
        self.addItem(QSpacerItem(0, 0, QSizePolicy.Expanding, QSizePolicy.Minimum))

    def addSpacing(self, w: int):
        self.addItem(QSpacerItem(w, 0, QSizePolicy.Fixed, QSizePolicy.Minimum))

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return self._arrange(QRect(0, 0, w, 0), apply=False)

    def setGeometry(self, rect: QRect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:
        """Alles in einer Zeile."""
        items = self._visible()
        m = self.contentsMargins()
        w = sum(it.sizeHint().width() for it in items) + self._h * max(0, len(items) - 1)
        h = max((it.sizeHint().height() for it in items), default=0)
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self) -> QSize:
        """Das größte einzelne Element – die Höhe bei gegebener Breite liefert heightForWidth."""
        items = self._visible()
        m = self.contentsMargins()
        w = max((it.minimumSize().width() for it in items), default=0)
        h = max((it.minimumSize().height() for it in items), default=0)
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def _visible(self) -> list[QLayoutItem]:
        return [it for it in self._items if it.spacerItem() is not None or not it.isEmpty()]

    def _arrange(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        lines: list[list[QLayoutItem]] = []
        line: list[QLayoutItem] = []
        x = 0
        for it in self._visible():
            w = it.sizeHint().width()
            if line and x + self._h + w > r.width():
                lines.append(line)
                line, x = [], 0
            x += (self._h if line else 0) + w
            line.append(it)
        if line:
            lines.append(line)
        y = r.y()
        for line in lines:
            widths = [min(it.sizeHint().width(), r.width()) for it in line]
            stretch = [i for i, it in enumerate(line) if it.spacerItem() is not None and it.expandingDirections()
                       & Qt.Horizontal]
            free = max(0, r.width() - sum(widths) - self._h * (len(line) - 1))
            for i in stretch:
                widths[i] += free // len(stretch)
            lh = max(it.sizeHint().height() for it in line)
            x = r.x()
            for it, w in zip(line, widths):
                h = min(lh, it.sizeHint().height())
                if apply:
                    it.setGeometry(QRect(x, y + (lh - h) // 2, w, h))
                x += w + self._h
            y += lh + self._v
        return max(0, y - self._v - r.y()) + m.top() + m.bottom() if lines else m.top() + m.bottom()


class FitScrollArea(QScrollArea):
    """Scrollbereich, der so hoch sein möchte wie sein Inhalt: Bei genug Platz gibt es keine Bildlaufleiste,
    im kleinen Fenster wird gescrollt, statt Bedienelemente übereinanderzuschieben."""

    def __init__(self, content: QWidget, parent=None):
        super().__init__(parent)
        self.setWidget(content)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { background: transparent; }")
        self.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)
        self._w = -1

    def sizeHint(self) -> QSize:
        c = self.widget()
        w = self.viewport().width() if self._w > 0 else c.sizeHint().width()
        h = c.heightForWidth(w) if c.hasHeightForWidth() else -1
        return QSize(c.sizeHint().width(), h if h >= 0 else c.sizeHint().height())

    def minimumSizeHint(self) -> QSize:
        return QSize(super().minimumSizeHint().width(), 90)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if e.size().width() != self._w:  # Inhalt bricht anders um: gewünschte Höhe neu melden
            self._w = e.size().width()
            self.updateGeometry()


def fmt_time(t: float) -> str:
    t = max(0.0, t)
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}"


class RangeSlider(QWidget):
    """Zeitachse mit zwei Griffen (Start/Ende) und Abspielposition."""

    changed = Signal(float, float)  # start, end (Sekunden)
    seek = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(54)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.lo, self.hi = 0.0, 60.0  # sichtbarer Bereich
        self.start, self.end = 10.0, 40.0
        self.pos = None
        self.marks: list[float] = []  # Trigger-Zeitpunkte
        self.min_len = 3.0
        self._drag = None
        self.setMouseTracking(True)

    def set_range(self, lo: float, hi: float, start: float, end: float, marks: list[float] | None = None):
        self.lo, self.hi = lo, max(hi, lo + 1)
        self.start, self.end = start, end
        self.marks = marks or []
        self.update()

    def set_pos(self, t: float | None):
        self.pos = t
        self.update()

    def _x(self, t: float) -> float:
        m = 10
        return m + (t - self.lo) / (self.hi - self.lo) * (self.width() - 2 * m)

    def _t(self, x: float) -> float:
        m = 10
        return self.lo + (x - m) / max(1, self.width() - 2 * m) * (self.hi - self.lo)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        y = 18
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.PANEL_2))
        p.drawRoundedRect(QRectF(10, y, self.width() - 20, 14), 7, 7)
        xs, xe = self._x(self.start), self._x(self.end)
        p.setBrush(QColor(theme.ACCENT))
        p.setOpacity(0.35)
        p.drawRect(QRectF(xs, y, xe - xs, 14))
        p.setOpacity(1.0)
        p.setBrush(QColor(theme.TWITCH))
        for t in self.marks:
            if self.lo <= t <= self.hi:
                p.drawRect(QRectF(self._x(t) - 1.5, y - 4, 3, 22))
        p.setBrush(QColor(theme.ACCENT))
        for x in (xs, xe):
            p.drawRoundedRect(QRectF(x - 5, y - 6, 10, 26), 4, 4)
        if self.pos is not None and self.lo <= self.pos <= self.hi:
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawLine(QPointF(self._x(self.pos), y - 8), QPointF(self._x(self.pos), y + 22))
        p.setPen(QColor(theme.MUTED))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(QRectF(0, 40, self.width(), 14), Qt.AlignLeft, fmt_time(self.lo))
        p.drawText(QRectF(0, 40, self.width(), 14), Qt.AlignRight, fmt_time(self.hi))
        p.setPen(QColor(theme.FG))
        p.drawText(QRectF(0, 40, self.width(), 14), Qt.AlignCenter,
                   f"{fmt_time(self.start)} – {fmt_time(self.end)}  ({self.end - self.start:.1f} s)")

    def mousePressEvent(self, e: QMouseEvent):
        x = e.position().x()
        ds, de = abs(x - self._x(self.start)), abs(x - self._x(self.end))
        if min(ds, de) < 12:
            self._drag = "start" if ds <= de else "end"
        else:
            self._drag = None
            self.seek.emit(self._t(x))

    def mouseMoveEvent(self, e: QMouseEvent):
        if not self._drag:
            x = e.position().x()
            near = min(abs(x - self._x(self.start)), abs(x - self._x(self.end))) < 12
            self.setCursor(Qt.SizeHorCursor if near else Qt.PointingHandCursor)
            return
        t = min(max(self._t(e.position().x()), self.lo), self.hi)
        if self._drag == "start":
            self.start = min(t, self.end - self.min_len)
        else:
            self.end = max(t, self.start + self.min_len)
        self.update()
        self.changed.emit(round(self.start, 2), round(self.end, 2))

    def mouseReleaseEvent(self, e):
        self._drag = None


class RectSelector(QLabel):
    """Zeigt ein Vorschaubild; mit der Maus Rechtecke aufziehen.

    mode "cam": Facecam-Rahmen (orange), mode "subject": Objekt in der Cam, z. B. ein Bär (grün).
    """

    selected = Signal(str, list)  # Modus, normiert [x, y, w, h]
    STYLES = {"cam": ("Facecam", theme.ACCENT, (242, 169, 59, 40)), "subject": ("Objekt", theme.GOOD, (76, 201, 138, 40)),
              "hand": ("Handcam", "#4aa3ff", (74, 163, 255, 40))}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(640, 360)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(f"background: #000; border-radius: 8px;")
        self._img: QImage | None = None
        self.rect_norm: list[float] | None = None
        self.subject_norm: list[float] | None = None
        self.hand_norm: list[float] | None = None
        self.mode = "cam"
        self._origin = None
        self._cur = None

    def set_image(self, img: QImage, rect_norm: list[float] | None = None):
        self._img = img
        self.rect_norm = rect_norm
        self.update()

    def _target(self) -> QRect:
        if not self._img:
            return QRect()
        s = min(self.width() / self._img.width(), self.height() / self._img.height())
        w, h = int(self._img.width() * s), int(self._img.height() * s)
        return QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000"))
        if not self._img:
            return
        t = self._target()
        p.drawImage(t, self._img)
        for mode, norm in (("cam", self.rect_norm), ("hand", self.hand_norm), ("subject", self.subject_norm)):
            r = None
            if self._origin and self._cur and mode == self.mode:
                r = QRectF(self._origin, self._cur).normalized()
            elif norm:
                x, y, w, h = norm
                r = QRectF(t.x() + x * t.width(), t.y() + y * t.height(), w * t.width(), h * t.height())
            if not r:
                continue
            label, color, fill = self.STYLES[mode]
            p.setPen(QPen(QColor(color), 3))
            p.setBrush(QColor(*fill))
            p.drawRect(r)
            p.setPen(QColor("#111"))
            p.setBrush(QColor(color))
            tag = QRectF(r.x(), max(t.y(), r.y() - 22), 82, 20)
            p.drawRect(tag)
            p.drawText(tag, Qt.AlignCenter, label)

    def mousePressEvent(self, e):
        self._origin = e.position()
        self._cur = e.position()

    def mouseMoveEvent(self, e):
        if self._origin:
            self._cur = e.position()
            self.update()

    def mouseReleaseEvent(self, e):
        if not self._origin:
            return
        t = self._target()
        r = QRectF(self._origin, e.position()).normalized().intersected(QRectF(t))
        self._origin = self._cur = None
        if r.width() > 10 and r.height() > 10 and t.width() > 0:
            norm = [round((r.x() - t.x()) / t.width(), 4), round((r.y() - t.y()) / t.height(), 4),
                    round(r.width() / t.width(), 4), round(r.height() / t.height(), 4)]
            if self.mode == "subject":
                self.subject_norm = norm
            elif self.mode == "hand":
                self.hand_norm = norm
            else:
                self.rect_norm = norm
            self.selected.emit(self.mode, norm)
        self.update()


def bgr_to_qimage(bgr) -> QImage:
    import numpy as np

    rgb = np.ascontiguousarray(bgr[:, :, ::-1])
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def pixmap_from_file(path: Path, w: int, h: int) -> QPixmap:
    pm = QPixmap(str(path))
    return pm.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation) if not pm.isNull() else pm
