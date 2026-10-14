"""Chat-Laden: in Chat-Spitzen dürfen keine ganzen Seiten verloren gehen; Lücken werden nicht zwischengespeichert."""

import json

from clipforge.pipeline import chat

# 1 Nachricht/s, zwischen 100 und 110 s 30 Nachrichten/s; Twitch liefert ab einer ganzen Sekunde max. 20
MSGS = [(t + k / n, f"id{t}_{k}") for t in range(200) for n in [30 if 100 <= t < 110 else 1] for k in range(n)]
PAGE = 20


class _R:
    def __init__(self, d):
        self.d = d

    def json(self):
        return self.d


class _Twitch:
    def __init__(self, fail_after=None):
        self.calls, self.fail_after = 0, fail_after

    def post(self, url, headers, data, timeout):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise ConnectionError("Netz weg")
        off = json.loads(data)["variables"]["off"]
        sel = [m for m in MSGS if m[0] >= off][:PAGE]
        return _R({"data": {"video": {"comments": {"edges": [
            {"node": {"id": i, "contentOffsetSeconds": int(t), "commenter": {"login": "u"},
                      "message": {"fragments": [{"text": "KEKW"}]}}} for t, i in sel]}}}})


def test_twitch_spike_keeps_a_full_page_per_second(monkeypatch):
    monkeypatch.setattr(chat, "_session", lambda: _Twitch())
    monkeypatch.setattr(chat.time, "sleep", lambda s: None)
    out = chat.fetch_twitch("1", 0, 199)
    spike = [m for m in out if 100 <= m.t < 110]
    assert len(spike) == 200  # mehr gibt die API pro Sekunde nicht her (vorher: 20)
    assert len(out) - len(spike) == 190  # außerhalb der Spitze alles
    assert len({(m.t, m.text, m.user) for m in out}) <= len(out) and out.complete


def test_incomplete_chat_is_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(chat, "_session", lambda: _Twitch(fail_after=3))
    monkeypatch.setattr(chat.time, "sleep", lambda s: None)
    msgs = chat.load_chat("https://www.twitch.tv/videos/123", 199, tmp_path)
    assert msgs and not (tmp_path / "chat.json").exists()
    monkeypatch.setattr(chat, "_session", lambda: _Twitch())
    chat.load_chat("https://www.twitch.tv/videos/123", 199, tmp_path)
    assert (tmp_path / "chat.json").exists()
