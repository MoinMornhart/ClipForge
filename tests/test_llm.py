from clipforge.core.settings import Settings
from clipforge.pipeline.clips import Clip
from clipforge.pipeline.llm import apply_result, build_prompt, estimate_cost, make_lines
from clipforge.pipeline.transcribe import Word


def speech(n_sentences: int = 30, start: float = 0.0) -> list[Word]:
    """Sätze à 5 Wörter, je 0,4 s pro Wort, 1 s Pause zwischen Sätzen."""
    words, t = [], start
    for i in range(n_sentences):
        for j in range(5):
            w = f"wort{i}_{j}" + ("." if j == 4 else "")
            words.append(Word(w, t, t + 0.3))
            t += 0.4
        t += 1.0
    return words


def test_make_lines_splits_on_sentences():
    lines = make_lines(speech(4), 0, 100)
    assert len(lines) == 4
    assert lines[0].text.endswith(".") and lines[1].idx == 1


def test_apply_result_respects_min_and_max():
    s = Settings(min_clip_seconds=15, max_clip_seconds=30)
    lines = make_lines(speech(30), 0, 200)
    clip = Clip(start=0, end=10, trigger_time=50, reasons=["test"])
    # KI wählt fast alles -> muss auf 30 s gekürzt werden, Ende bleibt
    apply_result(clip, {"start_line": 0, "end_line": 20, "score": 12, "title": "T", "description": "D",
                        "hashtags": ["gaming", "#fail", "zwei wörter"], "reason": "R", "is_highlight": True},
                 lines, s, 200)
    assert clip.duration <= 30 + 0.01
    assert abs(clip.end - (lines[20].e + 0.5)) < 0.01
    assert clip.score == 10
    assert clip.hashtags == ["#gaming", "#fail", "#zweiwörter"]
    # KI wählt eine Zeile -> auf mindestens 15 s verlängert
    apply_result(clip, {"start_line": 10, "end_line": 10, "score": 5, "title": "", "description": "",
                        "hashtags": [], "reason": "", "is_highlight": True}, lines, s, 200)
    assert clip.duration >= 15


def test_not_highlight_caps_score():
    s = Settings()
    lines = make_lines(speech(30), 0, 200)
    clip = Clip(start=0, end=10, trigger_time=50, reasons=["test"])
    apply_result(clip, {"start_line": 2, "end_line": 9, "score": 8, "title": "", "description": "",
                        "hashtags": [], "reason": "", "is_highlight": False}, lines, s, 200)
    assert clip.score <= 3


def test_prompt_contains_reason_and_lines():
    s = Settings()
    lines = make_lines(speech(3), 0, 100)
    p = build_prompt(Clip(0, 10, 5, ["Sprachbefehl: „clip das“"]), lines, s)
    assert "clip das" in p and "L0 [" in p


def test_estimate_known_and_unknown_price():
    s = Settings(llm_provider="anthropic", llm_model="claude-opus-5-5")
    est = estimate_cost(s, duration=8 * 3600)
    assert est.n_clips == 32 and est.llm_price_known and 0 < est.llm_low_usd < est.llm_high_usd
    s2 = Settings(llm_provider="mistral", llm_model="irgendein-modell")
    assert not estimate_cost(s2, 3600).llm_price_known
    s3 = Settings(llm_provider="ollama", llm_model="llama")
    assert estimate_cost(s3, 3600).llm_high_usd == 0
    s4 = Settings(transcription_backend="openai")
    assert abs(estimate_cost(s4, 3600).stt_usd - 0.36) < 1e-9
