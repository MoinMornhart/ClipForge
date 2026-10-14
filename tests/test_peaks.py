import random

from clipforge.pipeline import audio
from clipforge.pipeline.chat import ChatMessage, find_peaks, kick_video_uuid, twitch_vod_id


def test_url_parsing():
    assert twitch_vod_id("https://www.twitch.tv/videos/2899619133") == "2899619133"
    assert twitch_vod_id("https://www.twitch.tv/papaplatte/video/123") == "123"
    assert kick_video_uuid("https://kick.com/xqc/videos/a187a188-1fe3-4bdd-bead-7f172b911bba") == \
        "a187a188-1fe3-4bdd-bead-7f172b911bba"
    assert kick_video_uuid("https://kick.com/xqc") is None


def test_chat_peak_found_with_reactions():
    rnd = random.Random(1)
    msgs = [ChatMessage(rnd.uniform(0, 1200), "hallo") for _ in range(600)]  # ~0,5 pro Sekunde
    msgs += [ChatMessage(700 + rnd.uniform(0, 8), rnd.choice(["KEKW", "LUL KEKW", "!clip", "OMEGALUL"]))
             for _ in range(70)]
    peaks = find_peaks(sorted(msgs, key=lambda m: m.t), 1200)
    assert len(peaks) == 1
    p = peaks[0]
    assert 690 <= p.time <= 712
    assert "KEKW" in p.reason


def test_steady_chat_no_peak():
    rnd = random.Random(2)
    msgs = sorted((ChatMessage(rnd.uniform(0, 1200), "hi") for _ in range(2400)), key=lambda m: m.t)
    assert find_peaks(msgs, 1200) == []


def test_audio_peak_relative_to_surroundings():
    levels = [-35.0] * 2400  # 20 min leise (0,5 s pro Wert)
    for i in range(1000, 1006):
        levels[i] = -12.0  # 3 s Schrei
    peaks = audio.find_peaks(levels)
    assert len(peaks) == 1 and 498 <= peaks[0].time <= 504
    # Lautes Spiel durchgehend: keine Spitze
    assert audio.find_peaks([-15.0] * 2400) == []
