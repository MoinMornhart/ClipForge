"""Gemeinsame Test-Umgebung: eigener Datenordner und ein Schlüsselbund im Speicher –
Tests berühren nie die echten Projekte, Einstellungen oder API-Keys."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import keyring  # noqa: E402
import pytest  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        if self.store.pop((service, username), None) is None:
            raise keyring.errors.PasswordDeleteError(username)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("CLIPFORGE_HOME", str(home))
    old = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    yield home
    keyring.set_keyring(old)
