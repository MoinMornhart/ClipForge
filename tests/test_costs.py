from clipforge.core.settings import Settings
from clipforge.pipeline.clips import align_edit
from clipforge.pipeline.llm import Scorer, Usage
from clipforge.pipeline.project import Project
from clipforge.pipeline.transcribe import Word


def test_project_cost_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_HOME", str(tmp_path))
    p = Project.for_source("https://kick.com/test/videos/x")
    assert p.cost_text().startswith("0,00 $")
    p.add_cost("transkription", "groq", "whisper-large-v3-turbo", 0.32, minutes=480)
    p.add_cost("ki", "anthropic", "claude-opus-5-5", 1.25, 300_000, 40_000, calls=32)
    c = Project(p.id).cost_summary  # neu geladen = gespeichert
    assert c["usd"] == 1.57 and c["complete"] and c["minutes"] == 480
    assert c["by_kind"] == {"transkription": 0.32, "ki": 1.25}
    p.add_cost("ki", "mistral", "unbekannt", None, 1000, 100, calls=1)
    assert "unbekannt" in Project(p.id).cost_text()
    total, n = Project.total_cost_all()
    assert total == 1.57 and n == 1


def test_scorer_cost_from_tokens(monkeypatch):
    s = Settings(llm_provider="anthropic", llm_model="claude-opus-5-5")
    sc = Scorer.__new__(Scorer)
    sc.s, sc.usage = s, Usage(1_000_000, 100_000, 10)
    assert abs(sc.cost_usd() - (4.0 + 2.0)) < 1e-9  # 1M Eingabe × 4 $ + 0,1M Ausgabe × 20 $


def test_align_edit_same_and_different_count():
    orig = [Word("hallo", 1.0, 1.4), Word("welt", 1.5, 1.9)]
    assert align_edit(orig, "Hallo Welt!") == [["Hallo", 1.0, 1.4], ["Welt!", 1.5, 1.9]]
    out = align_edit(orig, "Hallo schöne Welt")
    assert len(out) == 3 and out[0][1] == 1.0 and out[-1][2] <= 1.9
