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


def test_sloppy_model_answer_is_repaired():
    # Echte Antwort eines kleinen lokalen Modells: Hashtags als Text, Zahlen als Text
    s = Settings()
    lines = make_lines(speech(30), 0, 200)
    clip = Clip(start=0, end=10, trigger_time=50, reasons=["Chat-Spitze"])
    apply_result(clip, {"start_line": "L3", "end_line": "12", "score": "8/10", "title": "T", "description": "",
                        "hashtags": "#Twitch #Letzter Boss, gaming", "reason": ""}, lines, s, 200)
    assert clip.hashtags == ["#Twitch", "#Letzter", "#Boss", "#gaming"]
    assert clip.score == 8
    assert abs(clip.start - (lines[3].s - 0.25)) < 0.01


def test_voice_command_ends_clip():
    s = Settings(min_clip_seconds=10, max_clip_seconds=60)
    lines = make_lines(speech(30), 0, 200)
    trig = lines[10].e
    clip = Clip(start=0, end=10, trigger_time=trig, reasons=["Sprachbefehl: „clip das“"])
    apply_result(clip, {"start_line": 5, "end_line": 20, "score": 7, "title": "", "description": "",
                        "hashtags": [], "reason": "", "is_highlight": True}, lines, s, 200)
    assert clip.end <= trig + 6.5  # nicht bis Zeile 20 weiterlaufen


def test_overlaps_resolved():
    from clipforge.pipeline.clips import STATUS_REJECTED
    from clipforge.pipeline.llm import resolve_overlaps

    a = Clip(37.2, 68.5, 42, ["x"], score=8)
    b = Clip(56.1, 80.1, 78, ["y"], score=8)
    resolve_overlaps([a, b], 15)
    assert a.end == 56.1 and a.status != STATUS_REJECTED
    c = Clip(100, 130, 128, ["x"], score=5)
    d = Clip(105, 135, 133, ["y"], score=9)
    resolve_overlaps([c, d], 15)
    assert c.status == STATUS_REJECTED and d.status != STATUS_REJECTED


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


def test_nvidia_provider_is_free_and_openai_compatible():
    from clipforge.core.providers import LLM_PROVIDERS, llm_price

    p = LLM_PROVIDERS["nvidia"]
    assert p.kind == "openai_compat" and p.base_url == "https://integrate.api.nvidia.com/v1" and p.needs_key
    assert p.default_model in p.models
    assert llm_price(p.default_model, provider="nvidia").input_per_m == 0


def test_style_hint_and_new_fields():
    from clipforge.core.settings import Settings
    from clipforge.pipeline import llm
    from clipforge.pipeline.clips import Clip

    s = Settings()
    assert "Alle Arten" in llm.style_hint(s)
    s.clip_styles = ["ragebait", "rp"]
    h = llm.style_hint(s)
    assert "Ragebait" in h and "RP" in h and "Lustig" not in h
    assert {"hook", "category"} <= set(llm.RESULT_SCHEMA["required"])
    lines = [llm.Line(i, i * 5.0, i * 5.0 + 4.5, f"Satz {i}.") for i in range(10)]
    c = Clip(0, 50, 30, ["Lautstärkespitze"])
    llm.apply_result(c, {"start_line": 1, "end_line": 7, "score": 8, "title": "T", "hook": "  Er sah es  nicht kommen ",
                         "category": "RAGEBAIT", "hashtags": ["#a"]}, lines, s, 60)
    assert c.hook == "Er sah es nicht kommen" and c.category == "ragebait"
    llm.apply_result(c, {"start_line": 1, "end_line": 7, "score": 8, "category": "quatsch"}, lines, s, 60)
    assert c.category == "sonstiges" and c.hook == ""


def test_hook_ass_wraps_and_escapes():
    from clipforge.pipeline.subtitles import SubtitleStyle, build_hook_ass

    a = build_hook_ass("Er hat {es} nicht kommen sehen", SubtitleStyle(), "cover")
    line = [l for l in a.splitlines() if l.startswith("Dialogue")][0]
    assert r"\N" in line and "{ES}" not in line  # umbrochen, Klammern entschärft
    assert "fad" not in line  # Thumbnail ohne Einblenden
    assert "fad" in build_hook_ass("Kurz", SubtitleStyle(), "hook")
