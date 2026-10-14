import io
import zipfile
from pathlib import Path

import pytest

from clipforge.core import updater
from clipforge.core.errors import ClipForgeError


def test_parse_version():
    assert updater.parse_version("v0.2.0") == (0, 2, 0)
    assert updater.parse_version("0.10.1") > updater.parse_version("0.9.9")
    assert updater.parse_version("") == (0,)


def _release(monkeypatch, version, cur="0.2.0"):
    monkeypatch.setattr(updater, "installed_version", lambda: cur)
    monkeypatch.setattr(updater, "_latest_release", lambda: {
        "tag": f"v{version}", "version": version, "notes": "- Neu A\n- Neu B\nText", "name": ""})


def test_check_only_upwards(monkeypatch):
    _release(monkeypatch, "0.2.0")
    assert not updater._check_zip().available
    _release(monkeypatch, "0.1.9")
    assert not updater._check_zip().available  # nie ein Downgrade
    _release(monkeypatch, "0.3.0")
    info = updater._check_zip()
    assert info.available and info.latest == "0.3.0" and info.changes == ["Neu A", "Neu B"]


def test_check_offline(monkeypatch):
    monkeypatch.setattr(updater, "installed_version", lambda: "0.2.0")

    def boom():
        raise OSError("offline")

    monkeypatch.setattr(updater, "_latest_release", boom)
    assert "nicht erreichbar" in updater._check_zip().message


def test_latest_release_ignores_prereleases(monkeypatch):
    monkeypatch.setattr(updater, "_api", lambda p: {"tag_name": "v9.0.0", "prerelease": True})
    with pytest.raises(OSError):
        updater._latest_release()


def _zip(version: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ClipForge-abc/clipforge/__init__.py", '__version__ = "%s"\n' % version)
    return buf.getvalue()


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, tmp_path, pkg_version):
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    monkeypatch.setattr(updater, "_api", lambda p: {"sha": "abcdef1234567890"})
    monkeypatch.setattr(updater, "_pip_update", lambda: "")
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda req, timeout=0: _Resp(_zip(pkg_version)))


def test_apply_rejects_package_with_wrong_version(monkeypatch, tmp_path):
    _release(monkeypatch, "0.3.0")
    _serve(monkeypatch, tmp_path, "6.6.6")
    with pytest.raises(ClipForgeError, match="passt nicht"):
        updater._apply_zip()
    assert not (tmp_path / "BUILD").exists()


def test_apply_installs_matching_package(monkeypatch, tmp_path):
    (tmp_path / "clipforge").mkdir()
    (tmp_path / "clipforge" / "__init__.py").write_text('__version__ = "0.2.0"\n', encoding="utf-8")
    _release(monkeypatch, "0.3.0")
    _serve(monkeypatch, tmp_path, "0.3.0")
    assert "0.3.0" in updater._apply_zip()
    assert '"0.3.0"' in (tmp_path / "clipforge" / "__init__.py").read_text(encoding="utf-8")
    assert (tmp_path / "BUILD").read_text(encoding="utf-8") == "abcdef123456"
    assert not (tmp_path / "clipforge.old").exists()


def test_swap_code_rolls_back(monkeypatch, tmp_path):
    cur = tmp_path / "clipforge"
    cur.mkdir()
    (cur / "alt.py").write_text("alt", encoding="utf-8")
    new_src = tmp_path / "src"
    new_src.mkdir()
    (new_src / "neu.py").write_text("neu", encoding="utf-8")
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    real = Path.rename

    def flaky(self, target):
        if self.name == "clipforge.new":
            raise OSError("Virenscanner sperrt")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(OSError):
        updater._swap_code(new_src)
    assert (cur / "alt.py").exists()  # alter Code ist wieder da
