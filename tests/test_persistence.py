"""Speichern: Bearbeitungen überleben eine Neu-Analyse, gleichzeitiges Schreiben verliert nichts."""

import threading

from clipforge.core.jsonio import write_json
from clipforge.pipeline.clips import STATUS_APPROVED, STATUS_EXPORTED, Clip
from clipforge.pipeline.project import Project
from clipforge.pipeline.runner import carry_over


def test_carry_over_keeps_user_edits_when_trigger_moves():
    old = Clip(start=80, end=101, trigger_time=100.0, reasons=["Sprachbefehl"], title="Mein Titel",
               status=STATUS_APPROVED, edited_words=[["hallo", 90.0, 90.5]], user_bounds=True, layout="cam")
    # neuer Begriff 5 s vorher: Trigger wird zusammengefasst, Zeitpunkt verschiebt sich
    new = [Clip(start=70, end=104.7, trigger_time=95.5, reasons=["Sprachbefehl", "Begriff"])]
    out = carry_over([old], new)
    assert len(out) == 1
    c = out[0]
    assert (c.id, c.title, c.status, c.layout) == (old.id, "Mein Titel", STATUS_APPROVED, "cam")
    assert c.edited_words == old.edited_words
    assert (c.start, c.end) == (80, 101)  # von Hand gesetzte Grenzen
    assert c.reasons == ["Sprachbefehl", "Begriff"]  # Gründe kommen aus der neuen Analyse


def test_carry_over_bounds_follow_analysis_when_untouched():
    old = Clip(start=10, end=40, trigger_time=35, reasons=["x"])
    out = carry_over([old], [Clip(start=12, end=41, trigger_time=36, reasons=["x"])])
    assert (out[0].start, out[0].end, out[0].id) == (12, 41, old.id)


def test_carry_over_keeps_edited_clip_without_match():
    edited = Clip(start=500, end=530, trigger_time=520, reasons=["alt"], status=STATUS_APPROVED)
    untouched = Clip(start=600, end=630, trigger_time=620, reasons=["alt"])
    out = carry_over([edited, untouched], [Clip(start=10, end=40, trigger_time=35, reasons=["neu"])])
    ids = [c.id for c in out]
    assert edited.id in ids and untouched.id not in ids and len(out) == 2


def test_ui_save_does_not_undo_export():
    p = Project("t_persist")
    a, b = Clip(0, 20, 18, ["x"], status=STATUS_APPROVED), Clip(30, 50, 48, ["y"], status=STATUS_APPROVED)
    p.save_clips([a, b])
    ui_copy = p.load_clips()  # Oberfläche hält ihren Stand
    p.update_clip(a.id, status=STATUS_EXPORTED, export_path="C:/out/a.mp4")  # Export im Hintergrund
    ui_copy[1].title = "Neuer Titel"  # Nutzer tippt während des Exports
    p.save_clips(ui_copy)
    got = {c.id: c for c in p.load_clips()}
    assert got[a.id].status == STATUS_EXPORTED and got[a.id].export_path == "C:/out/a.mp4"
    assert got[b.id].title == "Neuer Titel"
    # erneutes Freigeben eines exportierten Clips durch den Nutzer bleibt möglich
    again = p.load_clips()
    again[0].status = STATUS_APPROVED
    p.save_clips(again)
    assert p.load_clips()[0].status == STATUS_APPROVED


def test_parallel_writes_never_fail(tmp_path):
    target = tmp_path / "x.json"
    errors = []

    def worker(n):
        try:
            for i in range(150):
                write_json(target, {"n": n, "i": i})
        except Exception as e:  # pragma: no cover - genau das darf nicht passieren
            errors.append(e)

    ts = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    assert not list(tmp_path.glob("*.tmp"))


def test_empty_vod_and_delete(tmp_path):
    from clipforge.pipeline.clips import STATUS_REJECTED
    from clipforge.pipeline.importer import Source

    video = tmp_path / "vod.mp4"
    video.write_bytes(b"x")
    p = Project("t_empty")
    p.set_source(Source(path=video, title="VOD", streamer="Bär", date="2026-10-15", url="https://kick.com/b/videos/1",
                        platform="Kick"), {"duration": 60.0})
    p.save_clips([Clip(0, 20, 18, ["x"], status=STATUS_REJECTED)])
    p.mark("triggers")
    assert p.is_empty and p.downloaded_video() == video
    p.delete(remove_video=True)
    assert not p.dir.exists() and not video.exists()


def test_own_local_file_is_never_deleted(tmp_path):
    from clipforge.pipeline.importer import Source

    video = tmp_path / "meins.mp4"
    video.write_bytes(b"x")
    p = Project("t_local")
    p.set_source(Source(path=video, title="Meins", streamer="", date="2026-10-15"), {"duration": 60.0})
    assert p.downloaded_video() is None
    p.delete(remove_video=True)
    assert video.exists()


def test_clip_extras_override_settings():
    from clipforge.core.settings import Settings
    from clipforge.pipeline.runner import extra_on

    s = Settings()
    s.watermark = "logo.png"
    c = Clip(0, 20, 18, [])
    assert extra_on(c, s, "subtitles") and extra_on(c, s, "watermark") and not extra_on(c, s, "intro_outro")
    c.extras = {"subtitles": False, "watermark": False}
    assert not extra_on(c, s, "subtitles") and not extra_on(c, s, "watermark")
    # Neu-Analyse behält die Schalter
    new = carry_over([c], [Clip(1, 21, 18.5, ["neu"])])
    assert new[0].extras == {"subtitles": False, "watermark": False}
