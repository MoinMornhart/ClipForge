"""Robustheit: Überlappungen, Dateinamen-Muster, unlesbare KI-Antworten, kein halber Export."""

import pytest

from clipforge.core.settings import Settings
from clipforge.pipeline import llm
from clipforge.pipeline.clips import STATUS_REJECTED, Clip
from clipforge.pipeline.render import output_name


def test_overlaps_checked_across_rejected_clip():
    a = Clip(0, 60, 50, ["x"], score=9)
    b = Clip(5, 20, 15, ["x"], score=2)
    c = Clip(30, 50, 45, ["x"], score=8)
    llm.resolve_overlaps([a, b, c], 15)
    live = sorted((x for x in (a, b, c) if x.status != STATUS_REJECTED), key=lambda x: x.start)
    for p, q in zip(live, live[1:]):
        assert p.end <= q.start, "nach dem Entzerren darf sich nichts mehr überschneiden"
    assert b.status == STATUS_REJECTED


@pytest.mark.parametrize("pattern", ["{datum}_{titel}", "{date:%Y}", "{0}", "{unbekannt}_{title}", "{title"])
def test_filename_pattern_never_crashes(pattern):
    s = Settings()
    s.filename_pattern = pattern
    name = output_name(Clip(0, 20, 18, [], title="Mega Fail"), s, "2026-10-14", "Morni", 1)
    assert name and "/" not in name


def test_german_placeholders():
    s = Settings()
    s.filename_pattern = "{datum}_{titel}"
    assert output_name(Clip(0, 20, 18, [], title="Mega Fail"), s, "2026-10-14", "M", 1) == "2026-10-14_Mega_Fail"


@pytest.mark.parametrize("text, title", [
    ('Hier: {"score": 7, "title": "A"} und {"x": 1}', "A"),
    ('```json\n{"score": 5, "title": "B"}\n```', "B"),
    ('{"score": 3, "title": null}', ""),
])
def test_parse_json_tolerant(text, title):
    r = llm._parse_json(text)
    assert isinstance(r, dict) and str(r.get("title") or "") == title


@pytest.mark.parametrize("text", ['{"score": 7, "title": "abgeschn', '[1, 2]', "kein json"])
def test_parse_json_rejects_garbage(text):
    with pytest.raises(ValueError):
        llm._parse_json(text)


def test_unreadable_answer_skips_clip_instead_of_aborting():
    from clipforge.pipeline.transcribe import Word

    sc = llm.Scorer.__new__(llm.Scorer)
    sc.s = Settings()
    calls = []

    def ask(prompt):
        calls.append(1)
        return llm._parse_json('[9, "toll"]')  # Liste statt Objekt

    sc.ask = ask
    words = [Word(w, i * 0.5, i * 0.5 + 0.4) for i, w in enumerate("oh nein clip das bitte jetzt".split() * 20)]
    c = sc.score_clip(Clip(10, 40, 30, ["Sprachbefehl"]), words, 60)
    assert len(calls) == 2 and c.score is None and "unlesbar" in c.ai_reason
