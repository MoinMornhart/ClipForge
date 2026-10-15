"""Ausgabenlimit: zählt echte Kosten pro Monat, stoppt bezahlte Schritte, übersteht gelöschte VODs."""

import datetime as dt

import pytest

from clipforge.core import budget
from clipforge.core.errors import ClipForgeError
from clipforge.core.settings import Settings
from clipforge.pipeline.clips import Clip
from clipforge.pipeline.project import Project
from clipforge.pipeline.runner import Pipeline


def test_ledger_counts_month_and_survives_deleted_vod():
    p = Project("t_budget")
    p.add_cost("ki", "anthropic", "claude-opus-5-5", 2.5, calls=1)
    p.add_cost("ki", "ollama", "qwen", 0.0, calls=1)  # kostenlos zählt nicht
    assert budget.spent_usd("monat") == pytest.approx(2.5)
    p.delete()
    assert budget.spent_usd("monat") == pytest.approx(2.5)  # gelöschtes VOD: Ausgabe bleibt
    next_month = dt.datetime.now() + dt.timedelta(days=40)
    assert budget.spent_usd("monat", now=next_month) == 0
    assert budget.spent_usd("gesamt", now=next_month) == pytest.approx(2.5)


def test_limit_and_status_in_euro():
    s = Settings()
    assert budget.limit_usd(s) is None and "kein Limit" in budget.status_text(s)
    s.budget_eur, s.usd_to_eur = 80, 0.8
    assert budget.limit_usd(s) == pytest.approx(100)
    Project("t_b2").add_cost("ki", "anthropic", "m", 10.0)
    assert budget.remaining_usd(s) == pytest.approx(90)
    assert budget.status_text(s) == "Diesen Monat: 8,00 € von 80 €"


class _FakeScorer:
    """Jeder Clip kostet 1 $."""

    def __init__(self, s):
        from clipforge.pipeline.llm import Usage

        self.usage = Usage()
        self._cost = 0.0

    def score_clip(self, clip, words, duration):
        self._cost += 1.0
        self.usage.add(100, 10)
        clip.score, clip.title = 7.0, "T"
        return clip

    def cost_usd(self):
        return self._cost


def test_scoring_stops_at_limit(monkeypatch):
    from clipforge.pipeline import runner

    monkeypatch.setattr(runner, "Scorer", _FakeScorer)
    s = Settings()
    s.ai_enabled, s.llm_provider, s.budget_eur, s.usd_to_eur = True, "anthropic", 3, 1.0
    p = Project("t_b3")
    p.state["media"] = {"duration": 600.0}
    clips = [Clip(i * 60, i * 60 + 20, i * 60 + 18, ["x"]) for i in range(6)]
    msgs = []
    out = Pipeline(s, progress=lambda st, f, m: msgs.append(m)).score(p, clips)
    assert sum(c.score is not None for c in out) == 3  # genau bis zum Limit von 3 $
    assert any("Ausgabenlimit erreicht" in m for m in msgs)
    assert budget.spent_usd() == pytest.approx(3.0)
    # Limit aufgebraucht: nächste Bewertung startet gar nicht erst
    msgs.clear()
    Pipeline(s, progress=lambda st, f, m: msgs.append(m)).score(p, [Clip(0, 20, 18, ["x"])])
    assert any("übersprungen" in m for m in msgs)


def test_paid_transcription_blocked_when_limit_too_small():
    s = Settings()
    s.transcription_backend, s.budget_eur, s.usd_to_eur = "openai", 0.05, 1.0
    p = Project("t_b4")
    p.state["media"] = {"duration": 3600.0}  # 60 min × 0,006 $ = 0,36 $ > 0,05 $
    with pytest.raises(ClipForgeError, match="Ausgabenlimit"):
        Pipeline(s)._check_budget_stt(p)
