"""Speicherorte. Arbeitsdaten liegen bewusst außerhalb von iCloud/OneDrive,
damit große Videos und Modelle nicht synchronisiert werden."""

import os
from pathlib import Path

from clipforge import APP_NAME


def app_dir() -> Path:
    base = os.environ.get("CLIPFORGE_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")), APP_NAME
    )
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def sub(name: str) -> Path:
    p = app_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def projects_dir() -> Path:
    return sub("projects")


def logs_dir() -> Path:
    return sub("logs")


def models_dir() -> Path:
    return sub("models")


def videos_dir() -> Path:
    p = Path.home() / "Videos" / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_download_dir() -> Path:
    p = videos_dir() / "VODs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_export_dir() -> Path:
    p = videos_dir() / "Export"
    p.mkdir(parents=True, exist_ok=True)
    return p
