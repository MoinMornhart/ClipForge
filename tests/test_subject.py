"""Markiertes Objekt (Bär) wiederfinden, auch wenn es wandert, wächst oder das Cam-Fenster verschoben wird."""
import numpy as np

from clipforge.pipeline.facecam import Rect, SubjectFinder, Track, sendcmd_script


def draw_bear(f, cx, cy, s):
    import cv2

    brown, dark = (40, 80, 140), (25, 50, 95)
    for ex in (-0.7, 0.7):
        cv2.circle(f, (int(cx + ex * 100 * s), int(cy - 80 * s)), int(35 * s), brown, -1)
        cv2.circle(f, (int(cx + ex * 100 * s), int(cy - 80 * s)), int(18 * s), dark, -1)
    cv2.circle(f, (int(cx), int(cy)), int(100 * s), brown, -1)
    cv2.ellipse(f, (int(cx), int(cy + 30 * s)), (int(45 * s), int(32 * s)), 0, 0, 360, (90, 140, 190), -1)
    cv2.circle(f, (int(cx - 35 * s), int(cy - 25 * s)), int(12 * s), (20, 20, 20), -1)
    cv2.circle(f, (int(cx + 35 * s), int(cy - 25 * s)), int(12 * s), (20, 20, 20), -1)
    cv2.circle(f, (int(cx), int(cy + 15 * s)), int(14 * s), (15, 15, 15), -1)
    cv2.putText(f, "B", (int(cx - 20 * s), int(cy + 70 * s)), cv2.FONT_HERSHEY_SIMPLEX, 1.2 * s, (230, 230, 230), 2)


def frame(cam, bear, seed):
    import cv2

    rnd = np.random.default_rng(seed)
    f = np.zeros((1080, 1920, 3), np.uint8)
    f[:] = rnd.integers(30, 220, 3)
    for _ in range(20):
        cv2.circle(f, (int(rnd.integers(0, 1920)), int(rnd.integers(0, 1080))), int(rnd.integers(20, 150)),
                   tuple(int(v) for v in rnd.integers(0, 255, 3)), -1)
    x0, y0, w, h = cam
    f[y0:y0 + h, x0:x0 + w] = (70, 100, 125)
    cv2.rectangle(f, (x0 + 20, y0 + 20), (x0 + 120, y0 + 60), (200, 200, 200), -1)  # Regal im Hintergrund
    draw_bear(f, *bear)
    return f


def test_finds_moving_growing_bear():
    cam = (1300, 20, 600, 450)
    f0 = frame(cam, (1550, 230, 0.8), 0)
    # Markierung wie im Dialog: Rechteck um den Bären (Kopf + Ohren)
    tpl = f0[230 - 125:230 + 85, 1550 - 115:1550 + 115]
    finder = SubjectFinder(tpl)
    region = Rect(*cam)
    prev = None
    for i, (cx, cy, s) in enumerate([(1500, 220, 0.8), (1620, 250, 0.95), (1700, 280, 1.1), (1560, 300, 1.3)]):
        r, score = finder.find(frame(cam, (cx, cy, s), i + 1), region, prev)
        assert r is not None, f"Bild {i}: Bär nicht gefunden"
        prev = r
        assert abs(r.cx - cx) < 30 and abs(r.cy - (cy - 20 * s)) < 40, (i, r, cx, cy)
        assert 0.75 < r.w / (230 * s / 0.8 * 0.8) < 1.35, (i, r.w, s)  # Größe wächst mit


def test_relocates_moved_cam():
    f0 = frame((1300, 20, 600, 450), (1550, 230, 0.8), 0)
    finder = SubjectFinder(f0[105:315, 1435:1665])
    # Streamer hat das Cam-Fenster nach links unten verschoben
    r, _ = finder.find(frame((60, 560, 600, 450), (310, 770, 0.8), 9))
    assert r is not None and abs(r.cx - 310) < 30 and abs(r.cy - 754) < 40


def test_sendcmd_with_zoom():
    s = sendcmd_script(Track([0, 1], [100, 120], [10, 20], ws=[400, 300], hs=[300, 226]))
    first = s.splitlines()[0]
    assert "crop@cam w 400" in first and "crop@cam h 300" in first
