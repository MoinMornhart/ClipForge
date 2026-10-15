import pytest

from clipforge.core.errors import ClipForgeError
from clipforge.pipeline import kick

VID = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"
URL = f"https://kick.com/testkanal/videos/{VID}"
M3U8 = "https://stream.kick.com/ivs/v1/123/abc/2026/10/14/master.m3u8"


class Resp:
    def __init__(self, status=200, json=None, text=""):
        self.status_code, self._json, self.text = status, json, text

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, timeout=30):
        self.calls.append(url)
        for key, r in self.routes.items():
            if key in url:
                if isinstance(r, Exception):
                    raise r
                return r
        return Resp(404)


def use(monkeypatch, routes):
    s = FakeSession(routes)
    monkeypatch.setattr(kick, "_session", lambda: s)
    return s


ENTRY = {"source": M3U8, "session_title": "Bärenjagd", "start_time": "2026-10-14 18:00:00", "channel_id": 42,
         "duration": 3_600_000, "channel": {"slug": "testkanal", "user": {"username": "TestKanal"}},
         "video": {"uuid": VID}}


def test_parse_url():
    assert kick.parse_url(URL) == ("testkanal", VID)
    assert kick.parse_url(URL.replace(VID, VID.upper())) == ("testkanal", VID)
    assert kick.parse_url("https://kick.com/testkanal") is None


def test_old_api_format(monkeypatch):
    use(monkeypatch, {"api/v1/video": Resp(json={"source": M3U8, "livestream": ENTRY})})
    v = kick.resolve(URL)
    assert v.m3u8 == M3U8 and v.title == "Bärenjagd" and v.streamer == "TestKanal"
    assert v.channel_id == 42 and v.duration == 3600 and v.date == "2026-10-14"
    assert v.start_time.tzinfo is not None


def test_new_format_via_channel_list(monkeypatch):
    page = f'<html><title>x</title><script>{{"url":"{M3U8}"}}</script></html>'
    s = use(monkeypatch, {"api/v1/video": Resp(404), f"/videos/{VID}": Resp(text=page),
                          "api/v2/channels/testkanal/videos": Resp(json=[{"source": "andere"}, ENTRY])})
    v = kick.resolve(URL)
    assert v.title == "Bärenjagd" and v.channel_id == 42
    assert any("api/v2/channels" in c for c in s.calls)


def test_page_only_fallback(monkeypatch):
    page = f'<title>Mein VOD</title> "{M3U8}"'
    use(monkeypatch, {"api/v1/video": ConnectionError("cloudflare"), f"/videos/{VID}": Resp(text=page),
                      "api/v2/channels": Resp(500)})
    v = kick.resolve(URL)
    assert v.m3u8 == M3U8 and v.title == "Mein VOD" and v.channel_id is None


def test_errors(monkeypatch):
    with pytest.raises(ClipForgeError, match="kein gültiger"):
        kick.resolve("https://kick.com/testkanal")
    use(monkeypatch, {})
    with pytest.raises(ClipForgeError, match="nicht gefunden"):
        kick.resolve(URL)
    use(monkeypatch, {f"/videos/{VID}": Resp(text="<html>nur Text</html>")})
    with pytest.raises(ClipForgeError, match="kein Video"):
        kick.resolve(URL)


def test_best_variant_picks_highest():
    master = "\n".join([
        "#EXTM3U",
        "#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=852x480", "480p30/playlist.m3u8",
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080", "1080p60/playlist.m3u8",
        "#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080", "1080p30/playlist.m3u8",
        "#EXT-X-STREAM-INF:BANDWIDTH=12000000,RESOLUTION=2560x1440", "1440p60/playlist.m3u8",
    ])
    assert kick.best_variant(M3U8, master).endswith("/1440p60/playlist.m3u8")  # immer die beste
    assert kick.best_variant(M3U8, master, max_height=1080).endswith("/1080p60/playlist.m3u8")
    assert kick.best_variant(M3U8, master, max_height=480).endswith("/480p30/playlist.m3u8")
    assert kick.best_variant(M3U8, "#EXTM3U\n") == M3U8
