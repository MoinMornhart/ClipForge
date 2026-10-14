"""Einstellungen und Aufräumen: nie Datenverlust durch kaputte Dateien, keine verwaisten Prozesse/Projekte."""

import json
import threading

import pytest

from clipforge.core import ffmpeg
from clipforge.core.errors import Cancelled, ClipForgeError
from clipforge.core.settings import Settings
from clipforge.pipeline.project import Project
from clipforge.pipeline.runner import Pipeline


def test_corrupt_settings_are_backed_up_not_lost():
    s = Settings()
    s.facecam_profiles = {"Bär": {"mode": "overlay"}}
    s.save()
    p = Settings.path()
    broken = p.read_text(encoding="utf-8")[:-40]  # Stromausfall beim Speichern: Ende fehlt
    p.write_text(broken, encoding="utf-8")
    assert Settings.load().facecam_profiles == {}
    bak = p.with_suffix(".json.bak")
    assert bak.exists() and bak.read_text(encoding="utf-8") == broken  # zum Retten aufbewahrt


def test_unknown_trigger_fields_do_not_block_start():
    raw = json.loads(json.dumps({"custom_keywords": [{"phrase": "boss", "neu_in_v9": 1}, {"kaputt": True}]}))
    Settings.path().parent.mkdir(parents=True, exist_ok=True)
    Settings.path().write_text(json.dumps(raw), encoding="utf-8")
    s = Settings.load()
    assert [t.phrase for t in s.custom_keywords] == ["boss"]


def test_empty_folders_fall_back_to_defaults():
    s = Settings()
    s.export_dir = "  "
    s.download_dir = ""
    s.save()
    t = Settings.load()
    assert t.export_dir.strip() and t.download_dir.strip()


def test_failed_first_import_leaves_no_empty_project(tmp_path):
    with pytest.raises(ClipForgeError):
        Pipeline(Settings()).open(str(tmp_path / "gibt_es_nicht.mp4"))
    assert [p for p in Project.all() if not p.source] == []


def test_cancel_kills_ffmpeg():
    try:
        ffmpeg.ffmpeg()
    except ClipForgeError:
        pytest.skip("FFmpeg nicht installiert")
    cancel = threading.Event()
    seen = []

    def prog(f):
        seen.append(f)
        cancel.set()

    with pytest.raises(Cancelled):
        ffmpeg.run(["-f", "lavfi", "-i", "testsrc2=duration=600", "-f", "null", "-"], duration=600,
                   on_progress=prog, cancel=cancel)
    assert not ffmpeg._PROCS  # kein verwaister Prozess
