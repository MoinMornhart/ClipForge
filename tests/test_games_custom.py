"""Spiel-Erkennung und eigene Layouts (Facecam frei platziert)."""

import subprocess

import pytest

from clipforge.core import ffmpeg
from clipforge.core.settings import Settings
from clipforge.pipeline import games, layouts
from clipforge.pipeline.clips import Clip
from clipforge.pipeline.facecam import Facecam
from clipforge.pipeline.project import Project
from clipforge.pipeline.render import RenderJob, plan
from clipforge.pipeline.runner import Pipeline, custom_border, custom_for, custom_stack, project_game


@pytest.mark.parametrize("text, key", [
    ("Fortnite", "fortnite"), ("Grand Theft Auto V", "gta"), ("nopixel V LOCK IN", "gta"),
    ("⛏️ CRAFTATTACK TAG 4", "minecraft"), ("Just Chatting", None), ("Fortnitely", None),
])
def test_match(text, key):
    assert games.match(text) == key


def test_detect_uses_platform_then_title(monkeypatch):
    monkeypatch.setattr(games, "_kick_game", lambda url: "Fortnite")
    assert games.detect("Kick", "https://kick.com/x/videos/1", "egal") == ("fortnite", "Fortnite")

    def offline(url):
        raise ConnectionError("offline")

    monkeypatch.setattr(games, "_twitch_game", offline)
    assert games.detect("Twitch", "https://twitch.tv/videos/1", "GTA RP heute") == ("gta", "GTA V / RP")
    assert games.detect("Datei", "", "urlaub") == (None, "")


def test_project_game_choice_overrides_detection():
    p = Project("t_game")
    p.state["game_detected"] = "fortnite"
    assert project_game(p) == "fortnite"
    p.state["game"] = "minecraft"
    assert project_game(p) == "minecraft"
    p.state["game"] = "none"
    assert project_game(p) == ""


def test_custom_layout_lookup():
    s = Settings()
    s.custom_layouts = {"Main": {"cam": [0.3, 0.05, 0.4], "border": False}}
    c = Clip(0, 20, 18, [], extras={"custom_cam": [0.1, 0.5, 0.3]})
    assert custom_for(c, s, "custom:Main") == (0.3, 0.05, 0.4) and not custom_border(c, s, "custom:Main")
    assert custom_for(c, s, "custom") == (0.1, 0.5, 0.3) and custom_border(c, s, "custom")
    assert custom_for(c, s, "custom:Gelöscht") is None and custom_for(c, s, "stack") is None
    assert layouts.resolve_layout("custom:Main", True, False) == "custom:Main"
    assert layouts.resolve_layout("custom:Main", False, False) == "blur"


def test_stack_split_runs_to_cam_bottom():
    assert layouts.stack_split(0.02, 0.2) == pytest.approx(0.22)  # bis zur Unterkante der Facecam
    assert layouts.stack_split(0.0, 0.03) == layouts.STACK_MIN  # winzige Cam: etwas Platz bleibt oben
    assert layouts.stack_split(0.6, 0.3) == layouts.STACK_MAX  # genug Gameplay bleibt unten
    cam = layouts.Rect(0, 0, 440, 260)
    li = layouts.LayoutInput(1920, 1080, cam=cam, custom=(0.2, 0.02, 0.6), custom_stack=True)
    cw = 648  # 60 % von 1080
    assert layouts.custom_top(li) == layouts._even(0.02 * 1920 + layouts._even(cw * 260 / 440) + 12)
    li.border = False
    assert layouts.custom_top(li) == layouts._even(0.02 * 1920 + layouts._even(cw * 260 / 440))
    li.custom_stack = False
    assert layouts.custom_top(li) == 0


def test_custom_stack_lookup():
    s = Settings()
    s.custom_layouts = {"Main": {"cam": [0.3, 0.05, 0.4], "stack": True}, "Alt": {"cam": [0.3, 0.05, 0.4]}}
    c = Clip(0, 20, 18, [], extras={"custom_cam": [0.1, 0.5, 0.3], "custom_stack": True})
    assert custom_stack(c, s, "custom:Main") and custom_stack(c, s, "custom")
    assert not custom_stack(c, s, "custom:Alt") and not custom_stack(c, s, "stack")


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    try:
        ff = ffmpeg.ffmpeg()
    except Exception:
        pytest.skip("FFmpeg nicht installiert")
    out = tmp_path_factory.mktemp("custom") / "q.mp4"
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=2",
                    "-c:v", "libx264", "-preset", "ultrafast", str(out)], check=True)
    return ff, out


@pytest.mark.parametrize("game", ["", "fortnite"])
@pytest.mark.parametrize("layout, custom", [("custom:Main", (0.3, 0.02, 0.4)), ("custom", (0.6, 0.7, 0.5)),
                                            ("top", None), ("stack", None)])
def test_custom_and_game_graphs_render(src, tmp_path, layout, custom, game):
    ff, video = src
    cam = Facecam("overlay", cam=[0.75, 0.70, 0.22, 0.27])
    job = RenderJob(video, Clip(0.2, 1.2, 1.0, []), tmp_path / "o.mp4", layout, facecam=cam, nvenc=False,
                    smart_crop=False, track_face=False, game=game, custom=custom, cam_border=False)
    resolved, graph = plan(job, {"width": 1920, "height": 1080, "duration": 2.0, "fps": 30.0}, tmp_path)
    assert resolved == layout
    if custom:
        assert f"overlay={int(custom[0] * 1080)}:{int(custom[1] * 1920)}" in graph and "pad=" not in graph
    if game == "fortnite":
        assert "boxblur" in graph  # breiterer Ausschnitt mit unscharfer Füllung
    r = subprocess.run([ff, "-v", "error", "-i", str(video), "-filter_complex", graph, "-map", "[vl]",
                        "-frames:v", "2", "-f", "null", "-"], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-400:]


@pytest.mark.parametrize("border", [True, False])
@pytest.mark.parametrize("custom", [(0.2, 0.02, 0.6), (0.35, 0.0, 0.3), (0.0, 0.0, 1.0), (0.1, 0.6, 0.8),
                                    (0.44, 0.02, 0.12)])
@pytest.mark.parametrize("game", ["", "fortnite"])
def test_custom_stack_graphs_render(src, tmp_path, custom, border, game):
    ff, video = src
    job = RenderJob(video, Clip(0.2, 1.2, 1.0, []), tmp_path / "o.mp4", "custom", nvenc=False,
                    facecam=Facecam("overlay", cam=[0.75, 0.70, 0.22, 0.27]), smart_crop=False, track_face=False,
                    custom=custom, cam_border=border, cam_stack=True, game=game)
    _, graph = plan(job, {"width": 1920, "height": 1080, "duration": 2.0, "fps": 30.0}, tmp_path)
    assert "vstack" in graph and "boxblur" in graph
    r = subprocess.run([ff, "-v", "error", "-i", str(video), "-filter_complex", graph, "-map", "[vl]",
                        "-frames:v", "2", "-f", "null", "-"], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-400:]


def _sharpness(img):
    import cv2

    return cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()


@pytest.fixture(scope="module")
def noisy(src, tmp_path_factory):
    """Quelle mit feinem Rauschen überall – so zeigt sich Weichzeichnen in jedem Bildteil."""
    ff, _ = src
    out = tmp_path_factory.mktemp("noisy") / "n.mp4"
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=1920x1080:rate=5:duration=2,noise=alls=60:allf=t+u",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", str(out)], check=True)
    return ff, out


def _frame(video, tmp_path, stack):
    cv2 = pytest.importorskip("cv2")
    from clipforge.pipeline.render import preview_frame

    out = preview_frame(video, 1.0, "custom", Facecam("overlay", cam=[0.75, 0.70, 0.22, 0.27]),
                        {"width": 1920, "height": 1080, "duration": 2.0, "fps": 5.0}, tmp_path / f"b_{stack}.png",
                        custom=(0.3, 0.05, 0.4), cam_border=False, cam_stack=stack)
    return cv2.imread(str(out))


def test_custom_stack_blurs_above_and_keeps_gameplay_below(noisy, tmp_path):
    """Neben der Facecam ist das Spiel unscharf, darunter beginnt das Gameplay scharf (wie „Facecam oben“)."""
    _, video = noisy
    ref, img = _frame(video, tmp_path, False), _frame(video, tmp_path, True)
    # Facecam liegt bei x 324–756, y 96–394: Bereich bis y 394 unscharf, darunter Gameplay
    for y0, y1, x0, x1 in [(110, 380, 20, 300), (0, 90, 20, 1060), (110, 380, 780, 1060)]:
        assert _sharpness(img[y0:y1, x0:x1]) < _sharpness(ref[y0:y1, x0:x1]) / 10, (y0, x0)
    below, ref_below = _sharpness(img[420:1900, 20:1060]), _sharpness(ref[420:1900, 20:1060])
    assert below > ref_below / 3  # scharf (Ausschnitt etwas breiter, daher nicht identisch)


def test_render_job_carries_stack():
    s = Settings()
    s.custom_layouts = {"Main": {"cam": [0.3, 0.05, 0.4], "border": False, "stack": True}}
    p = Project("t_stack")
    from clipforge.pipeline.importer import Source
    from pathlib import Path

    p.set_source(Source(path=Path("x.mp4"), title="t", streamer="", date=""), {"duration": 10.0, "fps": 30})
    c = Clip(0, 5, 4, [], layout="custom:Main")
    job = Pipeline(s).render_job(p, c, Path("o.mp4"), None, [])
    assert job.cam_stack and not job.cam_border


def test_missing_template_falls_back(src, tmp_path):
    ff, video = src
    job = RenderJob(video, Clip(0.2, 1.2, 1.0, []), tmp_path / "o.mp4", "custom:Weg",
                    facecam=Facecam("overlay", cam=[0.75, 0.7, 0.22, 0.27]), nvenc=False, smart_crop=False)
    resolved, _ = plan(job, {"width": 1920, "height": 1080, "duration": 2.0, "fps": 30.0}, tmp_path)
    assert resolved == "top"


def test_render_job_carries_game_and_custom():
    s = Settings()
    s.custom_layouts = {"Main": {"cam": [0.3, 0.05, 0.4], "border": True}}
    s.default_layout = "custom:Main"
    p = Project("t_job")
    from clipforge.pipeline.importer import Source
    from pathlib import Path

    p.set_source(Source(path=Path("x.mp4"), title="t", streamer="", date=""), {"duration": 10.0, "fps": 30})
    p.state["game_detected"] = "fortnite"
    job = Pipeline(s).render_job(p, Clip(0, 5, 4, []), Path("o.mp4"), None, [])
    assert job.game == "fortnite" and job.custom == (0.3, 0.05, 0.4) and job.layout == "custom:Main"
