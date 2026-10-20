"""Rauchtest der Oberfläche (ohne Bildschirm): Fenster bauen, alle Seiten und Einstellungsbereiche
durchschalten, Einstellungen speichern, Facecam-Profil löschen."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from pathlib import Path  # noqa: E402

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from clipforge.core import secrets  # noqa: E402
from clipforge.core.settings import Settings  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from clipforge.ui import theme

    a = QApplication.instance() or QApplication([])
    theme.apply(a)
    return a


def _spin(app, n=20):
    for _ in range(n):
        app.processEvents()


def test_main_window_pages(app):
    from clipforge.ui.app import MainWindow

    w = MainWindow()
    w.show()
    for i in (1, 2, 0):
        w.nav.setCurrentRow(i)
        _spin(app)
        assert w.stack.currentIndex() == i
    sp = w.settings_page
    for i in range(sp.section_nav.count()):
        sp.tabs.setCurrentIndex(i)
        _spin(app, 5)
    assert sp.section_stack.currentIndex() == sp.section_nav.count() - 1
    w.notify("Einstellungen gespeichert.")
    w.close()


def test_settings_save_and_profiles(app):
    s = Settings()
    s.facecam_profiles = {"Bär": {"mode": "overlay", "cam": [0, 0, 0.2, 0.2], "subject_file": "x.png"}}
    s.save()
    from clipforge.ui.settings_page import SettingsPage

    sp = SettingsPage()
    sp.show()
    _spin(app)
    assert sp.profiles.rowCount() == 1 and sp.profiles.item(0, 0).text() == "Bär"
    assert sp.profiles.item(0, 2).text() == "✓"
    sp._delete_profile("Bär")
    assert Settings.load().facecam_profiles == {}
    sp.min_score.setValue(5)
    sp.save()
    assert Settings.load().min_score == 5
    assert not secrets.has_key("anthropic")  # Speichern legt nie von selbst Keys an


def test_clips_page_starts_with_vod_library(app):
    from clipforge.core.paths import projects_dir  # noqa: F401
    from clipforge.pipeline.clips import Clip
    from clipforge.pipeline.importer import Source
    from clipforge.pipeline.project import Project
    from clipforge.ui.clips_page import ClipsPage

    for name, streamer in (("a", "Bär"), ("b", "Bär"), ("c", "")):
        p = Project.for_source(f"test://{name}")
        p.set_source(Source(path=Path(f"C:/gibt/es/nicht/{name}.mp4"), title=f"VOD {name}", streamer=streamer,
                            date="2026-10-15", platform="Kick" if streamer else "Datei"),
                     {"duration": 60.0, "width": 1280, "height": 720, "fps": 30.0, "has_audio": True})
        p.save_clips([Clip(0, 20, 18, ["x"]), Clip(30, 50, 48, ["y"])])
    cp = ClipsPage(Settings())
    cp.show()
    _spin(app)
    lib = cp.library
    assert cp.body.currentIndex() == 0 and not cp.head_w.isVisible()
    titles = [g.findChildren(QLabel)[0].text() for g in lib.groups]
    assert titles == ["Bär · Kick", "Lokale Dateien"] or titles == ["Lokale Dateien", "Bär · Kick"]
    lib.search.setText("vod c")
    assert sum(c.isVisible() for c, _ in lib.cards) == 1
    lib.search.clear()
    card = lib.cards[0][0]
    card.clicked.emit(card.pid)  # VOD anklicken: Clips erscheinen
    _spin(app)
    assert cp.body.currentIndex() == 1 and cp.proj.id == card.pid and len(cp.clips) == 2
    cp.back_btn.click()
    assert cp.body.currentIndex() == 0


def test_layout_editor_stack(app):
    """Eigenes Layout „Facecam oben, Gameplay unten“: an/aus, Aufteilung folgt der Facecam, Ergebnis kommt zurück."""
    from PySide6.QtGui import QColor, QPixmap

    from clipforge.ui.layout_editor import LayoutEditor

    cam = QPixmap(160, 90)
    cam.fill(QColor("#3a7"))
    dlg = LayoutEditor(cam, 16 / 9, (0.2, 0.02, 0.6), stack=True)
    assert dlg.stack_cb.isChecked() and "Facecam-Bereich" in dlg.stack_hint.text()
    src = QPixmap(1920, 1080)
    src.fill(QColor("#246"))
    dlg.set_source(src)
    assert dlg.canvas.src_blur is not None and not dlg.canvas.grab().isNull()  # zeichnet Bereich + Gameplay
    before = dlg.canvas.split()
    dlg.size.setValue(40)  # kleinere Facecam → kleinerer Facecam-Bereich, mehr Gameplay
    assert dlg.canvas.split() < before
    dlg.border_cb.setChecked(False)
    assert not dlg.canvas.border
    dlg._finish("once")
    assert dlg.mode == "once" and dlg.stack and not dlg.border
    dlg.stack_cb.setChecked(False)
    assert dlg.canvas.split() == 0 and dlg.stack_hint.text() == ""


@pytest.mark.parametrize("size", [(890, 690), (1000, 760), (1230, 870), (1700, 1050)])
def test_clip_editor_controls_never_overlap(app, size):
    """Fenstermodus: Knöpfe und Schalter unter dem Player brechen um statt sich zu überlagern."""
    import time

    from PySide6.QtCore import QPoint, QRect

    from clipforge.pipeline.clips import Clip
    from clipforge.pipeline.importer import Source
    from clipforge.pipeline.project import Project
    from clipforge.ui.clips_page import ClipsPage

    p = Project.for_source("test://fenster")
    p.set_source(Source(path=Path("C:/gibt/es/nicht/f.mp4"), title="Ein ziemlich langer Streamtitel", streamer="Bär",
                        date="2026-10-20", platform="Kick"),
                 {"duration": 60.0, "width": 1920, "height": 1080, "fps": 30.0, "has_audio": True})
    p.save_clips([Clip(0, 20, 18, ["Sprachbefehl: clip das"], title="Ein Titel", score=8.0)])
    cp = ClipsPage(Settings())
    cp.resize(*size)
    cp.show()
    cp.open_project(p.id)
    end = time.time() + 0.5
    while time.time() < end:
        _spin(app, 5)
    assert cp.right_stack.currentIndex() == 1
    content = cp.lower.widget()
    widgets = [cp.play_btn, *cp.nudge_btns, cp.layout_box.parentWidget(), cp.preview_btn,
               *cp.extra_boxes.values(), cp.cam_slider.parentWidget(), cp.extras_all, cp.state_lbl,
               cp.approve_btn, cp.reject_btn]
    rects = [QRect(w.mapTo(content, QPoint(0, 0)), w.size()) for w in widgets]
    width = cp.lower.viewport().width()
    for w, r in zip(widgets, rects):
        assert r.left() >= 0 and r.right() < width + 1, (type(w).__name__, r, width)
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            assert not rects[i].intersects(rects[j]), (widgets[i], widgets[j], rects[i], rects[j])
    # Player und 9:16-Vorschau liegen über dem Zeitregler, nicht darauf
    assert cp.pv916.mapTo(cp, QPoint(0, cp.pv916.height())).y() <= cp.range.mapTo(cp, QPoint(0, 0)).y()
    assert cp.lower.widget().width() <= cp.lower.viewport().width()  # nichts ragt rechts aus dem Bereich
