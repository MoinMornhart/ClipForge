"""Eigenes Layout: Facecam frei auf dem 9:16-Bild verschieben und skalieren, mit Hilfslinien wie in Word.

Ergebnis: (x, y, Breite) als Anteil von 1080×1920. Speichern als Vorlage (eigener Name, optional als Standard)
oder nur für den aktuellen Clip.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

from clipforge.ui import theme

SNAP = 8  # Pixel im Editor, ab denen die Facecam an Mitte/Rand einrastet
MARGIN = 0.035  # Sicherheitsabstand zum Rand (Anteil der Breite)


class CamCanvas(QWidget):
    """9:16-Fläche mit Gameplay-Hintergrund und verschiebbarer, skalierbarer Facecam."""

    changed = Signal()

    def __init__(self, cam_aspect: float, value: tuple[float, float, float], parent=None):
        super().__init__(parent)
        self.setFixedSize(405, 720)
        self.setMouseTracking(True)
        self.bg: QPixmap | None = None
        self.cam: QPixmap | None = None
        self.aspect = cam_aspect  # Breite / Höhe der Facecam
        self.x, self.y, self.w = value
        self.guides: list[tuple[str, float]] = []
        self._drag = None  # ("move"|"resize", Startpunkt, Startwerte)

    def cam_height_frac(self) -> float:
        """Höhe der Facecam als Anteil der Bildhöhe (1080×1920: Breite·1080/aspect/1920)."""
        return self.w * 1080 / self.aspect / 1920

    def _px_rect(self) -> QRectF:
        W, H = self.width(), self.height()
        return QRectF(self.x * W, self.y * H, self.w * W, self.cam_height_frac() * H)

    def value(self) -> tuple[float, float, float]:
        return round(self.x, 4), round(self.y, 4), round(self.w, 4)

    def set_width(self, w: float, keep_center: bool = True):
        r = self._px_rect()
        cx, cy = r.center().x() / self.width(), r.center().y() / self.height()
        self.w = min(1.0, max(0.12, w))
        if keep_center:
            self.x = cx - self.w / 2
            self.y = cy - self.cam_height_frac() / 2
        self._clamp()
        self._snap()
        self.update()
        self.changed.emit()

    def _clamp(self):
        h = self.cam_height_frac()
        self.x = min(max(0.0, self.x), 1.0 - self.w)
        self.y = min(max(0.0, self.y), max(0.0, 1.0 - h))

    def _snap(self):
        """An Bildmitte und Sicherheitsrand einrasten und die passenden Hilfslinien merken."""
        W, H = self.width(), self.height()
        h = self.cam_height_frac()
        self.guides = []
        tol_x, tol_y = SNAP / W, SNAP / H
        cx, cy = self.x + self.w / 2, self.y + h / 2
        if abs(cx - 0.5) < tol_x:
            self.x = 0.5 - self.w / 2
            self.guides.append(("v", 0.5))
        elif abs(self.x - MARGIN) < tol_x:
            self.x = MARGIN
            self.guides.append(("v", MARGIN))
        elif abs(self.x + self.w - (1 - MARGIN)) < tol_x:
            self.x = 1 - MARGIN - self.w
            self.guides.append(("v", 1 - MARGIN))
        my = MARGIN * 1080 / 1920
        if abs(cy - 0.5) < tol_y:
            self.y = 0.5 - h / 2
            self.guides.append(("h", 0.5))
        elif abs(self.y - my) < tol_y:
            self.y = my
            self.guides.append(("h", my))

    # Zeichnen
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        if self.bg and not self.bg.isNull():
            p.drawPixmap(self.rect(), self.bg)
        else:
            p.fillRect(self.rect(), QColor("#0b0d11"))
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Lade Spielbild …")
        r = self._px_rect()
        if self.cam and not self.cam.isNull():
            p.drawPixmap(r.toRect(), self.cam)
        p.setPen(QPen(QColor(theme.ACCENT), 2))
        p.drawRect(r)
        # Anfasser an den Ecken
        p.setBrush(QColor(theme.ACCENT))
        for pt in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
            p.drawRect(QRectF(pt.x() - 5, pt.y() - 5, 10, 10))
        # Hilfslinien
        p.setPen(QPen(QColor("#ff3dcf"), 1, Qt.DashLine))
        for kind, v in self.guides:
            if kind == "v":
                p.drawLine(QPointF(v * W, 0), QPointF(v * W, H))
            else:
                p.drawLine(QPointF(0, v * H), QPointF(W, v * H))
        if any(v == 0.5 for _, v in self.guides):
            p.setPen(QColor("#ff3dcf"))
            p.drawText(QRectF(0, 6, W, 20), Qt.AlignHCenter, "Mitte")
        p.end()

    # Maus
    def _corner_at(self, pos) -> str | None:
        r = self._px_rect()
        for name, pt in (("tl", r.topLeft()), ("tr", r.topRight()), ("bl", r.bottomLeft()), ("br", r.bottomRight())):
            if abs(pos.x() - pt.x()) <= 9 and abs(pos.y() - pt.y()) <= 9:
                return name
        return None

    def mousePressEvent(self, e):
        pos = e.position()
        corner = self._corner_at(pos)
        if corner:
            self._drag = ("resize", corner, pos, (self.x, self.y, self.w))
        elif self._px_rect().contains(pos):
            self._drag = ("move", None, pos, (self.x, self.y, self.w))

    def mouseMoveEvent(self, e):
        pos = e.position()
        if not self._drag:
            corner = self._corner_at(pos)
            self.setCursor(Qt.SizeFDiagCursor if corner in ("tl", "br") else Qt.SizeBDiagCursor if corner
                           else Qt.SizeAllCursor if self._px_rect().contains(pos) else Qt.ArrowCursor)
            return
        kind, corner, start, (x0, y0, w0) = self._drag
        W, H = self.width(), self.height()
        dx, dy = (pos.x() - start.x()) / W, (pos.y() - start.y()) / H
        if kind == "move":
            self.x, self.y = x0 + dx, y0 + dy
        else:
            # Ecke ziehen: gegenüberliegende Ecke bleibt stehen, Seitenverhältnis bleibt
            sign = 1 if corner in ("tr", "br") else -1
            w = min(1.0, max(0.12, w0 + sign * dx))
            h0, h = w0 * 1080 / self.aspect / 1920, w * 1080 / self.aspect / 1920
            self.w = w
            if corner in ("tl", "bl"):
                self.x = x0 + (w0 - w)
            if corner in ("tl", "tr"):
                self.y = y0 + (h0 - h)
        self._clamp()
        self._snap()
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.guides = []
        self.update()

    def wheelEvent(self, e):
        step = 0.02 if e.angleDelta().y() > 0 else -0.02
        self.set_width(self.w + step)


class LayoutEditor(QDialog):
    """Ergebnis nach exec(): self.mode = "once" | "template" | "default", self.name, self.value, self.border."""

    def __init__(self, cam_pix: QPixmap, cam_aspect: float, value: tuple[float, float, float], name: str = "",
                 border: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Eigenes Layout")
        self.mode, self.name, self.value, self.border = "", name, value, border
        lay = QHBoxLayout(self)
        self.canvas = CamCanvas(cam_aspect, value)
        self.canvas.cam = cam_pix
        lay.addWidget(self.canvas)
        side = QVBoxLayout()
        h = QLabel("Facecam frei platzieren")
        h.setObjectName("h2")
        info = QLabel("Ziehen zum Verschieben, an den Ecken ziehen oder Mausrad zum Vergrößern/Verkleinern. "
                      "Rosa Linien zeigen, wenn die Facecam mittig oder am Rand sitzt. Das Gameplay füllt das "
                      "ganze Bild.")
        info.setWordWrap(True)
        info.setObjectName("muted")
        side.addWidget(h)
        side.addWidget(info)
        side.addSpacing(8)
        self.size_lbl = QLabel("")
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(12, 100)
        self.size.setValue(int(value[2] * 100))
        self.size.valueChanged.connect(lambda v: self.canvas.set_width(v / 100))
        side.addWidget(self.size_lbl)
        side.addWidget(self.size)
        quick = QHBoxLayout()
        for label, fn in (("Oben mittig", self._top_center), ("Mitte", self._center), ("Ecke links", self._corner)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            quick.addWidget(b)
        side.addLayout(quick)
        self.border_cb = QCheckBox("Rahmen um die Facecam")
        self.border_cb.setChecked(border)
        side.addWidget(self.border_cb)
        side.addStretch(1)
        once = QPushButton("Nur für diesen Clip")
        once.setToolTip("Gilt nur für den aktuellen Clip, wird nicht als Vorlage gespeichert")
        once.clicked.connect(lambda: self._finish("once"))
        tpl = QPushButton("Als Vorlage speichern …")
        tpl.setToolTip("Unter eigenem Namen speichern – erscheint dann in der Layout-Auswahl")
        tpl.clicked.connect(lambda: self._finish("template"))
        default = QPushButton("Als Standard festlegen …")
        default.setObjectName("primary")
        default.setToolTip("Speichern und für alle Clips als Standard-Layout verwenden")
        default.clicked.connect(lambda: self._finish("default"))
        cancel = QPushButton("Abbrechen")
        cancel.clicked.connect(self.reject)
        for b in (once, tpl, default, cancel):
            side.addWidget(b)
        lay.addLayout(side)
        self.canvas.changed.connect(self._sync)
        self._sync()

    def set_background(self, pix: QPixmap):
        self.canvas.bg = pix
        self.canvas.update()

    def _sync(self):
        w = self.canvas.w
        self.size_lbl.setText(f"Größe: {w * 100:.0f} % der Breite")
        self.size.blockSignals(True)
        self.size.setValue(int(round(w * 100)))
        self.size.blockSignals(False)

    def _place(self, x: float, y: float):
        c = self.canvas
        c.x, c.y = x, y
        c._clamp()
        c.update()
        c.changed.emit()

    def _top_center(self):
        self._place(0.5 - self.canvas.w / 2, MARGIN * 1080 / 1920)

    def _center(self):
        self._place(0.5 - self.canvas.w / 2, 0.5 - self.canvas.cam_height_frac() / 2)

    def _corner(self):
        self._place(MARGIN, 0.09)

    def _finish(self, mode: str):
        if mode in ("template", "default"):
            name, ok = QInputDialog.getText(self, "Name des Layouts", "Name (z. B. Main):", text=self.name or "Main")
            name = (name or "").strip()[:30]
            if not ok or not name:
                return
            if ":" in name:
                QMessageBox.warning(self, "Name", "Bitte ohne Doppelpunkt.")
                return
            self.name = name
        self.mode, self.value, self.border = mode, self.canvas.value(), self.border_cb.isChecked()
        self.accept()
