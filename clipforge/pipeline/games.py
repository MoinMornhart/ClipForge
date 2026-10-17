"""Spiele erkennen und je Spiel den besten Gameplay-Ausschnitt wählen.

Third-Person-Spiele (Fortnite, GTA): die eigene Spielfigur steht in der Bildmitte – der Ausschnitt bleibt
mittig und zeigt etwas mehr Breite, damit der Skin samt Umgebung zu sehen ist. Erkannt wird das Spiel aus den
Daten der Plattform (Kick-Kategorie, Twitch-Spiel), sonst aus dem Titel; im Zweifel wählt es der Nutzer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from clipforge.core.log import get_logger

log = get_logger("games")


@dataclass(frozen=True)
class Game:
    name: str
    focus: str = "motion"  # "center": Ausschnitt mittig (Spielfigur/Fadenkreuz) | "motion": nach Bewegung
    zoom: float = 1.0  # < 1: breiterer Ausschnitt (mehr vom Spiel), Rest unscharf aufgefüllt
    aliases: tuple[str, ...] = ()


GAMES: dict[str, Game] = {
    "fortnite": Game("Fortnite", "center", 0.84, ("fortnite",)),
    "gta": Game("GTA V / RP", "center", 0.9, ("grand theft auto", "gta v", "gta 5", "gta rp", "gtarp", "nopixel",
                                               "fivem")),
    "minecraft": Game("Minecraft", "center", 1.0, ("minecraft", "craftattack")),
    "valorant": Game("Valorant", "center", 1.0, ("valorant",)),
    "cs2": Game("Counter-Strike", "center", 1.0, ("counter-strike", "cs2", "csgo", "cs:go")),
    "apex": Game("Apex Legends", "center", 0.92, ("apex legends", "apex")),
    "cod": Game("Call of Duty / Warzone", "center", 1.0, ("call of duty", "warzone")),
    "rocketleague": Game("Rocket League", "center", 0.86, ("rocket league",)),
    "lol": Game("League of Legends", "motion", 1.0, ("league of legends",)),
}
DEFAULT = Game("Anderes Spiel")


def profile(key: str | None) -> Game:
    return GAMES.get(key or "", DEFAULT)


def match(text: str) -> str | None:
    """Spiel-Schlüssel aus einem Kategorie- oder Titeltext."""
    low = (text or "").lower()
    for key, g in GAMES.items():
        for a in g.aliases:
            if re.search(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])", low):
                return key
    return None


def _twitch_game(url: str) -> str:
    from clipforge.pipeline import chat

    vid = chat.twitch_vod_id(url)
    if not vid:
        return ""
    q = 'query{video(id:"%s"){game{name}}}' % vid
    r = chat._session().post(chat.TWITCH_GQL, headers={"Client-Id": chat.TWITCH_CLIENT_ID},
                             data=json.dumps({"query": q}), timeout=20)
    return (((r.json().get("data") or {}).get("video") or {}).get("game") or {}).get("name") or ""


def _kick_game(url: str) -> str:
    from clipforge.pipeline import kick

    return kick.resolve(url).game


def detect(platform: str, url: str, title: str) -> tuple[str | None, str]:
    """(Schlüssel oder None, erkannter Spielname). Netzfehler sind kein Problem: dann zählt der Titel."""
    name = ""
    try:
        if url and platform == "Twitch":
            name = _twitch_game(url)
        elif url and platform == "Kick":
            name = _kick_game(url)
    except Exception as e:  # Erkennung ist Zugabe
        log.info("Spiel nicht über %s ermittelbar: %s", platform, e)
    key = match(name) or match(title)
    return key, name or (GAMES[key].name if key else "")
