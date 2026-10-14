import random

from clipforge.pipeline import audio
from clipforge.pipeline.chat import ChatMessage, find_peaks, kick_video_uuid, twitch_vod_id


def test_url_parsing():
    assert twitch_vod_id("https://www.twitch.tv/videos/2899619133") == "2899619133"
    assert twitch_vod_id("https://www.twitch.tv/papaplatte/video/123") == "123"
    assert kick_video_uuid("https://kick.com/xqc/videos/a187a188-1fe3-4bdd-bead-7f172b911bba") == \
        "a187a188-1fe3-4bdd-bead-7f172b911bba"
    assert kick_video_uuid("https://kick.com/xqc") is None


def test_parse_m3u8_segments_and_offsets():
    from clipforge.pipeline.importer import parse_m3u8

    text = "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10.000,\n0.ts\n#EXTINF:10.000,\n1.ts\n#EXTINF:4.5,\n2.ts\n"
    segs, init, enc = parse_m3u8(text, "https://cdn.example/vod/index.m3u8")
    assert [s[0] for s in segs] == [0.0, 10.0, 20.0] and segs[2][1] == 4.5
    assert segs[1][2] == "https://cdn.example/vod/1.ts" and init is None and not enc
    _, _, enc2 = parse_m3u8('#EXT-X-KEY:METHOD=AES-128,URI="k"\n#EXTINF:10,\na.ts\n', "https://x/")
    assert enc2


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
