"""Alle Layouts × alle Kamera-Situationen: Layout-Wahl ist sinnvoll und FFmpeg rendert jeden Filtergraphen."""

import subprocess

import pytest

from clipforge.core import ffmpeg
from clipforge.pipeline import layouts
from clipforge.pipeline.clips import Clip
from clipforge.pipeline.facecam import Facecam
from clipforge.pipeline.render import RenderJob, plan

try:
    FF = ffmpeg.ffmpeg()
except Exception:
    FF = None

CAMS = {
    "ohne": None,
    "fenster": Facecam("overlay", cam=[0.75, 0.70, 0.22, 0.27], face=[0.82, 0.74, 0.08, 0.12]),
    "vollbild": Facecam("fullcam", face=[0.45, 0.2, 0.1, 0.18]),
}
HAND = [0.05, 0.62, 0.25, 0.33]


def _cam(kind: str, hand: bool) -> Facecam | None:
    c = CAMS[kind]
    if c is None:
        return Facecam("none", handcam=HAND) if hand else None
    return Facecam(**{**c.__dict__, "handcam": HAND if hand else None})


@pytest.mark.parametrize("hand", [False, True])
@pytest.mark.parametrize("kind", list(CAMS))
def test_auto_layout_choice(kind, hand):
    cam = _cam(kind, hand)
    has_cam = bool(cam and cam.mode == "overlay" and cam.cam)
    full = bool(cam and cam.mode == "fullcam")
    got = layouts.resolve_layout("auto", has_cam, full, hand)
    expected = {("ohne", False): "blur", ("fenster", False): "stack", ("vollbild", False): "cam",
                ("ohne", True): "hand", ("fenster", True): "face_hand", ("vollbild", True): "hand"}[(kind, hand)]
    assert got == expected


@pytest.mark.parametrize("hand", [False, True])
@pytest.mark.parametrize("kind", list(CAMS))
@pytest.mark.parametrize("layout", list(layouts.LAYOUTS))
def test_resolved_layout_is_usable(layout, kind, hand):
    """Ein gewähltes Layout, das zur Situation nicht passt, fällt auf etwas Sinnvolles zurück."""
    cam = _cam(kind, hand)
    has_cam = bool(cam and cam.mode == "overlay" and cam.cam)
    full = bool(cam and cam.mode == "fullcam")
    got = layouts.resolve_layout(layout, has_cam, full, hand)
    assert got in layouts.LAYOUTS and got != "auto"
    if got in ("stack", "corner"):
        assert has_cam, f"{layout}/{kind} → {got} braucht ein Facecam-Fenster"
    if got == "cam":
        assert has_cam or full
    if got in layouts.NEEDS_HAND:
        assert hand
    if got == "face_hand":
        assert has_cam


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    if not FF:
        pytest.skip("FFmpeg nicht installiert")
    out = tmp_path_factory.mktemp("matrix") / "quelle.mp4"
    subprocess.run([FF, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=3",
                    "-f", "lavfi", "-i", "sine=duration=3", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


@pytest.mark.parametrize("hand", [False, True])
@pytest.mark.parametrize("kind", list(CAMS))
@pytest.mark.parametrize("layout", [x for x in layouts.LAYOUTS if x != "auto"])
def test_graph_renders(src, tmp_path, layout, kind, hand):
    job = RenderJob(src, Clip(start=0.5, end=1.5, trigger_time=1.0, reasons=[]), tmp_path / "o.mp4", layout,
                    facecam=_cam(kind, hand), nvenc=False, track_face=True, smart_crop=True)
    media = {"width": 1920, "height": 1080, "duration": 3.0, "fps": 30.0, "has_audio": True}
    resolved, graph = plan(job, media, tmp_path)
    r = subprocess.run([FF, "-v", "error", "-ss", "0.5", "-t", "0.3", "-i", str(src), "-filter_complex", graph,
                        "-map", "[vl]", "-frames:v", "3", "-f", "null", "-"], cwd=tmp_path,
                       capture_output=True, text=True)
    assert r.returncode == 0, f"{layout}/{kind}/{'hand' if hand else '-'} → {resolved}: {r.stderr[-600:]}"


@pytest.mark.parametrize("layout", ["stack", "cam"])
def test_graph_renders_with_tracked_object(src, tmp_path, layout):
    """Markiertes Objekt (z. B. Bär als Facecam): wird gesucht, verfolgt und der Graph rendert."""
    import cv2

    from clipforge.pipeline.facecam import read_frames

    (t, frame), = read_frames(src, [1.0])
    h, w = frame.shape[:2]
    cam = [0.55, 0.45, 0.4, 0.5]
    x0, y0 = int((cam[0] + 0.1) * w), int((cam[1] + 0.1) * h)
    obj = tmp_path / "objekt.png"
    cv2.imwrite(str(obj), frame[y0:y0 + int(0.2 * h), x0:x0 + int(0.15 * w)])
    fcm = Facecam("overlay", cam=cam, subject=[cam[0] + 0.1, cam[1] + 0.1, 0.15, 0.2], subject_file=str(obj))
    job = RenderJob(src, Clip(start=0.5, end=2.0, trigger_time=1.0, reasons=[]), tmp_path / "o.mp4", layout,
                    facecam=fcm, nvenc=False)
    media = {"width": 1920, "height": 1080, "duration": 3.0, "fps": 30.0, "has_audio": True}
    _, graph = plan(job, media, tmp_path)
    r = subprocess.run([FF, "-v", "error", "-ss", "0.5", "-t", "0.5", "-i", str(src), "-filter_complex", graph,
                        "-map", "[vl]", "-f", "null", "-"], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-600:]


@pytest.fixture(scope="module")
def portrait(tmp_path_factory):
    if not FF:
        pytest.skip("FFmpeg nicht installiert")
    out = tmp_path_factory.mktemp("hoch") / "handy.mp4"
    subprocess.run([FF, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=2",
                    "-c:v", "libx264", "-preset", "ultrafast", str(out)], check=True)
    return out


@pytest.mark.parametrize("kind", ["ohne", "fenster"])
@pytest.mark.parametrize("layout", [x for x in layouts.LAYOUTS if x != "auto"])
def test_portrait_source_renders(portrait, tmp_path, layout, kind):
    """Handyvideo im Hochformat: Gameplay-Ausschnitt darf nie breiter als das Bild werden."""
    cam = _cam(kind, layout in layouts.NEEDS_HAND)
    job = RenderJob(portrait, Clip(start=0.2, end=1.2, trigger_time=1.0, reasons=[]), tmp_path / "o.mp4", layout,
                    facecam=cam, nvenc=False, track_face=False, smart_crop=False)
    media = {"width": 1080, "height": 1920, "duration": 2.0, "fps": 30.0, "has_audio": False}
    resolved, graph = plan(job, media, tmp_path)
    r = subprocess.run([FF, "-v", "error", "-i", str(portrait), "-filter_complex", graph, "-map", "[vl]",
                        "-frames:v", "2", "-f", "null", "-"], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, f"{layout}/{kind} → {resolved}: {r.stderr[-400:]}"
