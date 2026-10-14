"""JSON sicher speichern: nie eine halb geschriebene Datei, auch wenn mehrere Threads gleichzeitig schreiben
oder der Virenscanner die Datei kurz sperrt."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

LOCK = threading.RLock()  # alle Schreibzugriffe auf Projekt- und Einstellungsdateien laufen hierüber


def write_json(path: Path, data, indent: int | None = 1) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=indent)
    with LOCK:
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            for attempt in range(20):  # Windows: Ziel kurz von Virenscanner/Indexer gesperrt
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.05)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
