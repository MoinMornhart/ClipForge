"""API-Keys in der Windows-Anmeldeinformationsverwaltung (über keyring).

Keys werden nie in Dateien, Logs oder im Repo gespeichert.
"""

import keyring
from keyring.errors import KeyringError

from clipforge.core.errors import ClipForgeError
from clipforge.core.providers import KEY_PROVIDERS

SERVICE = "ClipForge"
PROVIDERS = KEY_PROVIDERS


def get_key(provider: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, provider)
    except KeyringError as e:
        raise ClipForgeError(
            "Die Windows-Anmeldeinformationsverwaltung ist nicht erreichbar.",
            "Starte ClipForge neu. Hilft das nicht, prüfe unter Systemsteuerung → Anmeldeinformationsverwaltung.",
            str(e),
        ) from e


def set_key(provider: str, key: str) -> None:
    key = key.strip()
    if not key:
        delete_key(provider)
        return
    keyring.set_password(SERVICE, provider, key)


def delete_key(provider: str) -> None:
    try:
        keyring.delete_password(SERVICE, provider)
    except keyring.errors.PasswordDeleteError:
        pass


def has_key(provider: str) -> bool:
    return bool(get_key(provider))


def mask(key: str | None) -> str:
    if not key:
        return "nicht gesetzt"
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "gesetzt"
