from clipforge.pipeline import layouts
from clipforge.pipeline.facecam import Rect, handcam_from_boxes


def test_hand_cluster_becomes_handcam():
    # Hände tauchen in 18 von 24 Bildern unten links auf, leicht wechselnd
    frames = []
    for i in range(24):
        if i % 4 == 3:
            frames.append([])
        else:
            frames.append([Rect(120 + 10 * (i % 3), 700, 160, 120), Rect(330, 710 + 5 * (i % 2), 150, 110)])
    r = handcam_from_boxes(frames, 1920, 1080)
    assert r is not None
    assert r.x < 120 and r.x + r.w > 480 and r.y < 700 and r.y + r.h > 820


def test_hands_inside_facecam_are_not_a_handcam():
    face = Rect(1500, 0, 420, 320)
    frames = [[Rect(1600, 230, 120, 80)] for _ in range(24)]  # Hände an der Tastatur in der Facecam
    assert handcam_from_boxes(frames, 1920, 1080, exclude=face) is None


def test_rare_hands_ignored():
    frames = [[] for _ in range(24)]
    frames[3] = [Rect(900, 500, 100, 100)]
    assert handcam_from_boxes(frames, 1920, 1080) is None


def test_handcam_layouts_and_auto_choice():
    assert layouts.resolve_layout("auto", True, False, has_hand=True) == "face_hand"
    assert layouts.resolve_layout("auto", False, False, has_hand=True) == "hand"
    assert layouts.resolve_layout("face_hand", False, False, has_hand=True) == "hand"
    assert layouts.resolve_layout("hand", True, False, has_hand=False) == "blur"
    li = layouts.LayoutInput(1920, 1080, 30, cam=Rect(1500, 0, 420, 318), face=Rect(1660, 70, 100, 100),
                             hand=Rect(0, 640, 640, 440), gameplay_x=500)
    for name in ("hand", "face_hand", "hand_only"):
        g = layouts.build(name, li)
        assert g.endswith("fps=30,format=yuv420p[v]"), name
        assert g.count("[") == g.count("]"), name
    assert "vstack=inputs=3" in layouts.build("face_hand", li)
