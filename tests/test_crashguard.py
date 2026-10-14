from clipforge.core import crashguard


def test_session_restores_project_after_crash():
    assert crashguard.begin_session() is None  # erster Start
    crashguard.remember_project("p123")
    # kein end_session: Absturz
    prev = crashguard.begin_session()
    assert prev and prev["project"] == "p123"
    crashguard.end_session()
    assert crashguard.begin_session() is None  # sauber beendet


def test_corrupt_session_file_is_ignored():
    crashguard._session_file().write_text("{kaputt", encoding="utf-8")
    assert crashguard.begin_session() is None


def test_excepthook_reports_and_survives(monkeypatch):
    got = []
    monkeypatch.setattr(crashguard, "_reporter", lambda m, d: got.append((m, d)))
    try:
        raise ValueError("Testfehler")
    except ValueError as e:
        crashguard._excepthook(type(e), e, e.__traceback__)
    assert got and got[0][0] == "ValueError: Testfehler" and "Traceback" in got[0][1]
    crashguard._excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert len(got) == 1

    def broken(m, d):
        raise RuntimeError("Anzeige kaputt")

    monkeypatch.setattr(crashguard, "_reporter", broken)
    crashguard._excepthook(ValueError, ValueError("x"), None)  # darf nicht werfen
