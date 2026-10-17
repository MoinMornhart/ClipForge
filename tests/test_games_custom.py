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
from clipforge.pipeline.runner import Pipeline, custom_border, custom_for, project_game


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
