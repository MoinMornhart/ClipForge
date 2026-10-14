"""Tests zu den Funden der Sicherheitsprüfung (Issues #2–#6)."""
import os

import pytest

from clipforge.core import updater
from clipforge.core.errors import ClipForgeError
from clipforge.core.ffmpeg import which_safe
from clipforge.core.providers import LLM_PROVIDERS
from clipforge.pipeline.importer import check_public_url
from clipforge.pipeline.llm import server_url


@pytest.mark.parametrize("url", ["file:///C:/Windows/win.ini", "http://127.0.0.1/x.ts", "http://192.168.0.1/reboot",
                                 "http://10.0.0.5/a", "http://169.254.169.254/latest", "ftp://8.8.8.8/x", "http:///x"])
def test_playlist_cannot_reach_local_targets(url):
    with pytest.raises(ClipForgeError):
        check_public_url(url)


def test_public_ip_allowed():
    check_public_url("https://8.8.8.8/segment.ts")  # kein Fehler


def test_cloud_key_never_goes_to_custom_address():
    evil = "http://192.168.1.50:8080/v1"
    for pid in ("openai", "groq", "mistral", "xai", "openrouter", "gemini", "deepseek"):
        assert server_url(LLM_PROVIDERS[pid], evil) == LLM_PROVIDERS[pid].base_url
    assert server_url(LLM_PROVIDERS["ollama"], "http://localhost:11500/v1") == "http://localhost:11500/v1"
    with pytest.raises(ClipForgeError):
        server_url(LLM_PROVIDERS["custom"], "file:///etc/passwd")


def test_which_ignores_current_folder(tmp_path, monkeypatch):
    fake = tmp_path / "ffmpeg.exe"
    fake.write_bytes(b"MZ")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    found = which_safe("ffmpeg")
    assert found is None or os.path.dirname(os.path.abspath(found)) != str(tmp_path)


def test_version_only_upwards(monkeypatch):
    assert updater.parse_version("v0.2.0") > updater.parse_version("0.1.9")
    monkeypatch.setattr(updater, "installed_version", lambda: "0.2.0")
    monkeypatch.setattr(updater, "_latest_release", lambda: {"tag": "v0.1.5", "version": "0.1.5", "notes": "", "name": ""})
    info = updater._check_zip()
    assert not info.available  # älteres Release wird nie angeboten
    monkeypatch.setattr(updater, "_latest_release", lambda: {"tag": "v0.3.0", "version": "0.3.0",
                                                            "notes": "- Neu: X\n- Fix: Y", "name": ""})
    info = updater._check_zip()
    assert info.available and info.changes == ["Neu: X", "Fix: Y"]


def test_swap_rolls_back_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    (tmp_path / "clipforge").mkdir()
    (tmp_path / "clipforge" / "alt.py").write_text("alt")
    src = tmp_path / "neu_src"
    src.mkdir()
    (src / "neu.py").write_text("neu")
    real_rename = type(tmp_path).rename

    def failing_rename(self, target):
        if self.name == "clipforge.new":
            raise OSError("Datei gesperrt")
        return real_rename(self, target)

    monkeypatch.setattr(type(tmp_path), "rename", failing_rename)
    with pytest.raises(OSError):
        updater._swap_code(src)
    assert (tmp_path / "clipforge" / "alt.py").exists()  # alter Code ist wieder da
