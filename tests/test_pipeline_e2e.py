"""Ganzer Ablauf mit einem erzeugten Testvideo: Import → Transkript → Trigger → Clips → Export (9:16 + .txt).

Die Spracherkennung wird durch ein festes Transkript ersetzt; alles andere (FFmpeg, Facecam-Suche,
Lautstärke, Untertitel, Rendern) läuft echt.
"""

import json
import subprocess

import pytest

from clipforge.core import ffmpeg
from clipforge.core.settings import Settings
from clipforge.pipeline.clips import STATUS_EXPORTED
from clipforge.pipeline.runner import Pipeline
from clipforge.pipeline.transcribe import Word

try:
    FF, FP = ffmpeg.ffmpeg(), ffmpeg.ffprobe()
except Exception:
    FF = FP = None
pytestmark = pytest.mark.skipif(not FF, reason="FFmpeg nicht installiert")


class FakeWhisper:
    """Liefert für den einzigen Abschnitt einen Satz mit Sprachbefehl bei 20 s."""

    billed_seconds = 0.0

    def transcribe(self, wav, language):
        text = "okay jetzt kommt der boss oh nein was war das denn clip das bitte"
        return [Word(w, 12.0 + i * 0.6, 12.0 + i * 0.6 + 0.5) for i, w in enumerate(text.split())]


@pytest.fixture
def video(tmp_path):
    out = tmp_path / "test_stream.mp4"
    subprocess.run([FF, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=40",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=40", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


def test_full_pipeline(video, tmp_path):
    s = Settings()
    s.ai_enabled = False
    s.use_chat = False
    s.use_nvenc = False
    s.export_dir = str(tmp_path / "export")
    steps = []
    p = Pipeline(s, progress=lambda st, f, m: steps.append(st))
    p._backend = FakeWhisper()

    proj, clips = p.analyze(str(video))
    assert proj.done("transcribe") and proj.media["duration"] == pytest.approx(40, abs=0.5)
    assert clips, "der Sprachbefehl muss einen Clip ergeben"
    c = clips[0]
    assert c.start < c.trigger_time <= c.end and s.min_clip_seconds <= c.end - c.start <= s.max_clip_seconds
    assert {"import", "transcribe", "triggers"} <= set(steps)

    # zweiter Durchlauf: nichts wird neu transkribiert, Clips bleiben erhalten
    class NoWhisper:
        def transcribe(self, wav, language):
            raise AssertionError("darf nicht erneut transkribieren")

    p2 = Pipeline(s)
    p2._backend = NoWhisper()
    _, clips2 = p2.analyze(str(video))
    assert [x.id for x in clips2] == [x.id for x in clips]

    outs = p.export(proj, clips[:1])
    assert len(outs) == 1 and outs[0].exists()
    info = json.loads(subprocess.run([FP, "-v", "error", "-show_entries",
                                      "stream=width,height,codec_type", "-of", "json", str(outs[0])],
                                     capture_output=True, text=True, check=True).stdout)
    v = next(st for st in info["streams"] if st["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert any(st["codec_type"] == "audio" for st in info["streams"])
    assert outs[0].with_suffix(".txt").exists()
    thumb = outs[0].with_name(outs[0].stem + "_thumbnail.jpg")
    assert thumb.exists() and thumb.stat().st_size > 10_000  # Thumbnail je Clip
    assert any(x.status == STATUS_EXPORTED and x.export_path == str(outs[0]) for x in proj.load_clips())


def test_video_without_audio_is_rejected(tmp_path):
    out = tmp_path / "stumm.mp4"
    subprocess.run([FF, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=2",
                    "-c:v", "libx264", "-preset", "ultrafast", str(out)], check=True)
    from clipforge.core.errors import ClipForgeError

    with pytest.raises(ClipForgeError, match="keine Tonspur"):
        Pipeline(Settings()).open(str(out))
