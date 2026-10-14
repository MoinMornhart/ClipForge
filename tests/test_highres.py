"""Quellen größer als 1920 px (1440p/4K): Objektsuche liefert Quellpixel, nicht verkleinerte Koordinaten."""

import subprocess

import numpy as np
import pytest

from clipforge.core import ffmpeg
from clipforge.pipeline.facecam import SubjectFinder, locate_subject

try:
    FF = ffmpeg.ffmpeg()
except Exception:
    FF = None


@pytest.mark.skipif(not FF, reason="FFmpeg nicht installiert")
def test_locate_subject_in_1440p_source(tmp_path):
    import cv2

    rng = np.random.default_rng(1)
    frame = np.full((1440, 2560, 3), 60, np.uint8)
    obj = (rng.random((200, 160, 3)) * 255).astype(np.uint8)  # texturreiches „Objekt“
    x0, y0 = 2200, 1000
    frame[y0:y0 + 200, x0:x0 + 160] = obj
    img = tmp_path / "bild.png"
    cv2.imwrite(str(img), frame)
    video = tmp_path / "q.mp4"
    subprocess.run([FF, "-v", "error", "-loop", "1", "-i", str(img), "-t", "2", "-r", "10", "-c:v", "libx264",
                    "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv444p", str(video)], check=True)
    tpl = tmp_path / "objekt.png"
    cv2.imwrite(str(tpl), obj)
    r = locate_subject(video, 2.0, SubjectFinder.from_file(tpl), n=4)
    assert r is not None
    assert abs(r.cx - (x0 + 80)) < 20 and abs(r.cy - (y0 + 100)) < 20
