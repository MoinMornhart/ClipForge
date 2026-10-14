from clipforge.core.settings import Settings, Trigger
from clipforge.pipeline.clips import candidates_from_hits
from clipforge.pipeline.transcribe import Word, chunk_plan
from clipforge.pipeline.triggers import find_hits, normalize


def words(text: str, start: float = 0.0, step: float = 0.4) -> list[Word]:
    return [Word(w, start + i * step, start + i * step + 0.3) for i, w in enumerate(text.split())]


def test_normalize_phonetic():
    assert normalize("Klipp das!") == normalize("clip das")
    assert normalize("Übel") == "uebel"


def test_fuzzy_voice_command():
    s = Settings()
    hits = find_hits(words("ja oh mein gott klipp des bitte"), s.all_triggers())
    assert len(hits) == 1
    assert hits[0].phrase in ("clip das", "clippt das")


def test_no_false_positive():
    s = Settings()
    assert find_hits(words("wir gehen jetzt in den keller und holen das licht"), s.all_triggers()) == []


def test_custom_keyword_short_needs_high_similarity():
    s = Settings(voice_triggers=[], custom_keywords=[Trigger("boom", threshold=75)])
    assert len(find_hits(words("und dann boom einfach weg"), s.all_triggers())) == 1
    assert find_hits(words("und dann room einfach weg"), s.all_triggers()) == []


def test_candidates_respect_limits_and_merge():
    s = Settings(min_clip_seconds=15, max_clip_seconds=60)
    w = words("clip das", start=100) + words("clip das", start=112)
    hits = find_hits(w, s.all_triggers())
    clips = candidates_from_hits(hits, duration=500, s=s)
    assert len(clips) == 1
    c = clips[0]
    assert 15 <= c.duration <= 60
    assert c.end > 112


def test_chunk_plan_covers_duration():
    plan = chunk_plan(1250, 600)
    assert plan[0] == (0, 600) and plan[-1][1] == 1250 and len(plan) == 3
