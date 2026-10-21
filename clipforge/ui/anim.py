"""Bewegung in der Oberfläche: sanfte Seitenwechsel und kurze Benachrichtigungen („Toasts“).

Der Seitenwechsel animiert ein Standbild der neuen Seite statt der echten Seite. So bleiben Videoplayer
und andere native Elemente unberührt (Grafikeffekte auf Videofenstern führen unter Windows zu Fehlern).
"""

from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, QSequentialAnimationGroup,
                            Qt, QTimer)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QStackedWidget, QWidget

from clipforge.ui import theme


class AnimatedStack(QStackedWidget):
    """QStackedWidget mit Gleiten + Einblenden beim Seitenwechsel."""

    DURATION = 260

    def __init__(self, parent=None):
        super().__init__(parent)
        self._overlay: QLabel | None = None
        self._cover: QLabel | None = None
        self._anim = None
        self.animate = True

    def _end_transition(self) -> None:
        """Laufenden Übergang sofort beenden und seine Standbilder entfernen – etwa bei schnellem Weiterklicken.
        Sonst löscht der neue Übergang das Standbild des alten, und dessen Ende greift auf das gelöschte zu."""
        if self._anim is not None:
            self._anim.stop()  # stop() löst kein finished aus
            self._anim.deleteLater()
        for w in (self._overlay, self._cover):
            if w is not None and _alive(w):
                w.deleteLater()
        self._anim = self._overlay = self._cover = None

    def slide_to(self, index: int) -> None:
        if index == self.currentIndex():
            return
        self._end_transition()
        if not self.animate or not self.isVisible():
            self.setCurrentIndex(index)
            return
        forward = index > self.currentIndex()
        target = self.widget(index)
        target.resize(self.size())
        target.ensurePolished()
        pix = target.grab()  # Standbild der neuen Seite
        self.setCurrentIndex(index)
        ov = QLabel(self)
        ov.setPixmap(pix)
        ov.setGeometry(self.rect())
        ov.setAttribute(Qt.WA_TransparentForMouseEvents)
        eff = QGraphicsOpacityEffect(ov)
        eff.setOpacity(0.0)
        ov.setGraphicsEffect(eff)
        ov.show()
        ov.raise_()
        self._overlay = ov
        dx = 36 if forward else -36
        move = QPropertyAnimation(ov, b"pos", self)
        move.setDuration(self.DURATION)
        move.setStartValue(QPoint(dx, 0))
        move.setEndValue(QPoint(0, 0))
        move.setEasingCurve(QEasingCurve.OutCubic)
        fade = QPropertyAnimation(eff, b"opacity", self)
        fade.setDuration(self.DURATION)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        # Die echte Seite liegt schon darunter; das Standbild deckt sie ab, bis es voll da ist.
        target_cover = QLabel(self)
        target_cover.setStyleSheet(f"background:{theme.BG};")
        target_cover.setGeometry(self.rect())
        target_cover.setAttribute(Qt.WA_TransparentForMouseEvents)
        target_cover.show()
        ov.raise_()
        self._cover = target_cover
        group = QParallelAnimationGroup(self)
        group.addAnimation(move)
        group.addAnimation(fade)

        def done():
            if self._anim is group:  # sonst hat ein neuer Übergang schon aufgeräumt
                self._end_transition()

        group.finished.connect(done)
        group.start()
        self._anim = group


class Toast(QLabel):
    """Kurze Meldung unten rechts, blendet ein und nach ein paar Sekunden wieder aus."""

    def __init__(self, parent: QWidget, text: str, kind: str = "info", ms: int = 3500):
        super().__init__(text, parent)
        colors = {"info": theme.ACCENT, "good": theme.GOOD, "bad": theme.BAD}
        self.setTextFormat(Qt.PlainText)
        self.setWordWrap(True)
        self.setMaximumWidth(420)
        self.setStyleSheet(f"background:{theme.PANEL_2}; color:{theme.FG}; border:1px solid {theme.LINE};"
                           f"border-left:4px solid {colors.get(kind, theme.ACCENT)}; border-radius:10px;"
                           "padding:10px 14px; font-weight:600;")
        self.adjustSize()
        eff = QGraphicsOpacityEffect(self)
        eff.setOpacity(0.0)
        self.setGraphicsEffect(eff)
        self._eff = eff
        self._ms = ms

    def popup(self, index: int = 0) -> None:
        p = self.parentWidget()
        self.adjustSize()
        end = QPoint(p.width() - self.width() - 24, p.height() - self.height() - 48 - index * (self.height() + 10))
        self.move(end + QPoint(0, 16))
        self.show()
        self.raise_()
        fade_in = QPropertyAnimation(self._eff, b"opacity", self)
        fade_in.setDuration(180)
        fade_in.setEndValue(1.0)
        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(220)
        slide.setEndValue(end)
        slide.setEasingCurve(QEasingCurve.OutCubic)
        appear = QParallelAnimationGroup(self)
        appear.addAnimation(fade_in)
        appear.addAnimation(slide)
        fade_out = QPropertyAnimation(self._eff, b"opacity", self)
        fade_out.setDuration(400)
        fade_out.setEndValue(0.0)
        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(appear)
        seq.addPause(self._ms)
        seq.addAnimation(fade_out)
        seq.finished.connect(self.deleteLater)
        seq.start()
        self._seq = seq


class ToastHost:
    """Verwaltet mehrere Toasts übereinander im Fenster."""

    def __init__(self, window: QWidget):
        self.window = window
        self.live: list[Toast] = []

    def show(self, text: str, kind: str = "info", ms: int = 3500) -> None:
        self.live = [t for t in self.live if _alive(t)]
        t = Toast(self.window, text, kind, ms)
        t.destroyed.connect(lambda *_: QTimer.singleShot(0, self._prune))
        t.popup(len(self.live))
        self.live.append(t)

    def _prune(self):
        self.live = [t for t in self.live if _alive(t)]


def _alive(w) -> bool:
    try:
        w.isVisible()
        return True
    except RuntimeError:  # bereits gelöscht
        return False
