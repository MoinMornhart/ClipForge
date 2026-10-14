import numpy as np

from clipforge.pipeline import layouts
from clipforge.pipeline.facecam import Rect, Track, detect_letterbox, sendcmd_script
from clipforge.pipeline.subtitles import SubtitleStyle, _ass_color, build_ass, group_words
from clipforge.pipeline.transcribe import Word


def test_resolve_layout_falls_back_without_cam():
    assert layouts.resolve_layout("auto", True, False) == "stack"
    assert layouts.resolve_layout("auto", False, True) == "cam"
    assert layouts.resolve_layout("auto", False, False) == "blur"
    assert layouts.resolve_layout("corner", False, False) == "blur"


def test_all_layouts_build_valid_graph():
    li = layouts.LayoutInput(1920, 1080, 30, cam=Rect(1500, 0, 420, 318), face=Rect(1660, 70, 100, 100),
                             gameplay_x=500, cam_window=(420, 248), cam_xy=(1500, 20))
    for name in ("stack", "corner", "cam", "blur", "crop"):
        g = layouts.build(name, li)
        assert g.endswith("fps=30,format=yuv420p[v]"), name
        assert g.count("[") == g.count("]")
    assert "vstack" in layouts.build("stack", li)
    assert "overlay=36:170" in layouts.build("corner", li)


def test_cam_window_keeps_aspect_inside_region():
    w, h = layouts.cam_window_size(Rect(0, 0, 420, 318), 1080 / 640)
    assert w <= 420 and h <= 318 and abs(w / h - 1080 / 640) < 0.03


def test_letterbox_detected_only_for_real_bars():
    f = np.full((1080, 1920, 3), 90, np.uint8)
    f[:108] = 0
    f[972:] = 0
    assert detect_letterbox([f] * 5) == (108, 972)
    assert detect_letterbox([np.full((1080, 1920, 3), 90, np.uint8)]) == (0, 1080)


def test_sendcmd_interpolates():
    s = sendcmd_script(Track([0, 1], [100, 200], [10, 10]))
    assert s.splitlines()[0].startswith("0.00 crop@cam x 100")
    assert "crop@cam x 150" in s  # Mitte interpoliert


def test_subtitle_groups_and_highlight():
    words = [Word(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate("das war echt der wahnsinn heute.".split())]
    assert [len(g) for g in group_words(words, max_words=3, max_chars=30)] == [3, 3]
    # Zeichengrenze bricht früher um: „der wahnsinn heute.“ hat 19 Zeichen
    assert [len(g) for g in group_words(words, max_words=3, max_chars=18)] == [3, 2, 1]
    ass = build_ass(words, SubtitleStyle(), 3.0)
    assert ass.count("Dialogue:") == 6  # ein Eintrag pro Wort
    assert _ass_color("#FFD400") == "&H0000D4FF"
    assert "WAHNSINN" in ass
