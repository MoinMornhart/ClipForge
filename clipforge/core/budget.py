"""Ausgabenlimit für bezahlte KI-/Transkriptions-Anbieter.

Alle tatsächlich angefallenen Kosten landen zusätzlich in einem eigenen Kassenbuch (budget.json), damit das
Limit auch dann stimmt, wenn ein VOD samt Projekt gelöscht wird. Beim ersten Start wird es aus den
vorhandenen Projekten befüllt.
"""

from __future__ import annotations

import datetime as dt
import json

from clipforge.core.jsonio import LOCK, write_json
from clipforge.core.log import get_logger
from clipforge.core.paths import app_dir

log = get_logger("budget")


def _path():
    return app_dir() / "budget.json"


def _load() -> list[dict]:
    p = _path()
    if not p.exists():
        return _seed()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _seed() -> list[dict]:
    """Kassenbuch aus den Kosten der vorhandenen Projekte anlegen (einmalig)."""
    from clipforge.pipeline.project import Project

    items = []
    for p in Project.all():
        for i in p.cost_summary["items"]:
            if i.get("usd"):
                items.append({"at": i.get("at", ""), "usd": i["usd"], "kind": i.get("kind", ""),
                              "provider": i.get("provider", ""), "project": p.id})
    write_json(_path(), items, indent=None)
    return items


def record(usd: float | None, kind: str, provider: str, project: str) -> None:
    if not usd:
        return
    with LOCK:
        if not _path().exists():  # erstes Mal: aus den Projekten anlegen – die gerade gespeicherte Ausgabe
            _seed()               # steht dort schon drin, also nicht doppelt zählen
            return
        items = _load()
        items.append({"at": dt.datetime.now().isoformat(timespec="seconds"), "usd": round(usd, 5), "kind": kind,
                      "provider": provider, "project": project})
        write_json(_path(), items, indent=None)


def spent_usd(period: str = "monat", now: dt.datetime | None = None) -> float:
    """Ausgaben im laufenden Kalendermonat ("monat") oder insgesamt ("gesamt")."""
    now = now or dt.datetime.now()
    month = now.strftime("%Y-%m")
    return round(sum(i["usd"] for i in _load() if period == "gesamt" or str(i.get("at", "")).startswith(month)), 5)


def limit_usd(s) -> float | None:
    """Limit in US-$ (Preise der Anbieter sind in $), None = kein Limit."""
    if not s.budget_eur or s.budget_eur <= 0:
        return None
    return s.budget_eur / max(0.01, s.usd_to_eur)


def remaining_usd(s) -> float | None:
    lim = limit_usd(s)
    return None if lim is None else lim - spent_usd(s.budget_period)


def status_text(s) -> str:
    """Kurztext für die Oberfläche, z. B. „Diesen Monat 3,40 € von 80 €“."""
    spent = spent_usd(s.budget_period) * s.usd_to_eur
    when = "Diesen Monat" if s.budget_period == "monat" else "Insgesamt"
    if not s.budget_eur:
        return f"{when} ausgegeben: {spent:.2f} € (kein Limit)".replace(".", ",")
    return f"{when}: {spent:.2f} € von {s.budget_eur:.0f} €".replace(".", ",")


class BudgetExceeded(Exception):
    pass
