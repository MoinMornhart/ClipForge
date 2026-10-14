"""Fehler mit verständlichen deutschen Meldungen für Oberfläche und Konsole."""


class ClipForgeError(Exception):
    """Fehler, dessen Text direkt dem Nutzer gezeigt werden kann.

    message: was schiefging (deutsch, ohne Fachjargon)
    hint:    was der Nutzer tun kann
    detail:  technische Ursache, landet nur im Log
    """

    def __init__(self, message: str, hint: str | None = None, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.message}\n→ {self.hint}" if self.hint else self.message


class Cancelled(Exception):
    """Der Nutzer hat den Vorgang abgebrochen."""
