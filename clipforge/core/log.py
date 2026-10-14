"""Logdatei unter %LOCALAPPDATA%\\ClipForge\\logs\\clipforge.log (rotierend)."""

import logging
import sys
from logging.handlers import RotatingFileHandler

from clipforge.core.paths import logs_dir

_configured = False


def setup_logging(verbose: bool = False) -> logging.Logger:
    global _configured
    root = logging.getLogger("clipforge")
    if _configured:
        return root
    root.setLevel(logging.DEBUG)
    fh = RotatingFileHandler(logs_dir() / "clipforge.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    fh.setLevel(logging.DEBUG)
    root.addHandler(fh)
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(logging.Formatter("%(message)s"))
    ch.setLevel(logging.DEBUG if verbose else logging.WARNING)
    root.addHandler(ch)
    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"clipforge.{name}")
