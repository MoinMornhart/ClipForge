"""Rauchtest der Oberfläche (ohne Bildschirm): Fenster bauen, alle Seiten und Einstellungsbereiche
durchschalten, Einstellungen speichern, Facecam-Profil löschen."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

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
