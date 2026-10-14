"""Facecam ohne menschliches Gesicht: ein Bär als Avatar über wechselndem Spielbild."""
import numpy as np

from clipforge.pipeline.facecam import _overlay_without_face


def bear_frames(n=24, cam=(1480, 30, 400, 300), seed=3):
    import cv2

    rnd = np.random.default_rng(seed)
    x0, y0, w, h = cam
    frames = []
    for i in range(n):
        # Spielbild: jede Szene anders (Farbe, Formen, Rauschen)
        f = np.zeros((1080, 1920, 3), np.uint8)
        f[:] = rnd.integers(30, 220, 3)
        for _ in range(25):
            c = tuple(int(v) for v in rnd.integers(0, 255, 3))
            cv2.circle(f, (int(rnd.integers(0, 1920)), int(rnd.integers(0, 1080))), int(rnd.integers(20, 160)), c, -1)
        f = cv2.add(f, rnd.integers(0, 40, f.shape, dtype=np.uint8))
        # Bär-Facecam: ruhiger Hintergrund, Kopf wackelt leicht, Maul geht auf und zu
        f[y0:y0 + h, x0:x0 + w] = (60, 90, 120)
        cx, cy = x0 + w // 2 + int(rnd.integers(-12, 12)), y0 + h // 2 + int(rnd.integers(-8, 8))
        brown = (40, 80, 140)
        for ex in (-70, 70):
            cv2.circle(f, (cx + ex, cy - 80), 35, brown, -1)
        cv2.circle(f, (cx, cy), 100, brown, -1)
        cv2.circle(f, (cx - 35, cy - 20), 12, (20, 20, 20), -1)
        cv2.circle(f, (cx + 35, cy - 20), 12, (20, 20, 20), -1)
        cv2.ellipse(f, (cx, cy + 40), (30, 6 + 14 * (i % 3)), 0, 0, 360, (30, 30, 90), -1)
        cv2.rectangle(f, (x0, y0), (x0 + w, y0 + h), (255, 70, 145), 6)
        frames.append(f)
    return frames


def test_bear_facecam_found():
    fc = _overlay_without_face(bear_frames())
    assert fc.mode == "overlay"
    x, y, w, h = fc.cam
    # erwartet: 1480/1920=0.771, 30/1080=0.028, 400/1920=0.208, 300/1080=0.278 (±3 %)
    assert abs(x - 0.771) < 0.03 and abs(y - 0.028) < 0.03
    assert abs(w - 0.208) < 0.04 and abs(h - 0.278) < 0.04


def test_no_overlay_in_plain_gameplay():
    import cv2

    rnd = np.random.default_rng(5)
    frames = []
    for _ in range(24):
        f = np.zeros((1080, 1920, 3), np.uint8)
        f[:] = rnd.integers(30, 220, 3)
        for _ in range(25):
            c = tuple(int(v) for v in rnd.integers(0, 255, 3))
            cv2.circle(f, (int(rnd.integers(0, 1920)), int(rnd.integers(0, 1080))), int(rnd.integers(20, 160)), c, -1)
        frames.append(f)
    assert _overlay_without_face(frames).mode == "none"
