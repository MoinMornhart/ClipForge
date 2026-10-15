"""ClipForge.exe – startet die Oberfläche mit der mitgelieferten Python-Laufzeit.

Wird mit PyInstaller zu einer kleinen .exe gebaut (installer/build.ps1). Eine Videodatei oder ein Link,
der auf die .exe gezogen bzw. übergeben wird, landet direkt im Eingabefeld.
"""

import ctypes
import subprocess
import sys
from pathlib import Path

DETACHED = 0x00000008
NO_WINDOW = 0x08000000


def message(text: str, title: str = "ClipForge", flags: int = 0x10) -> int:
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags)


def main() -> int:
    base = Path(sys.executable).resolve().parent
    pyw = base / "runtime" / "pythonw.exe"
    if not pyw.exists():
        message("Die Python-Laufzeit von ClipForge fehlt.\n\nBitte ClipForge neu installieren.")
        return 1
    if not (base / "runtime" / ".ready").exists():
        # Einrichtung ist beim Installieren nicht durchgelaufen (z. B. Internet weg): jetzt nachholen
        if message("ClipForge ist noch nicht fertig eingerichtet.\n\nJetzt die nötigen Bibliotheken laden "
                   "(einmalig ca. 3 GB)?", flags=0x24) != 6:  # Ja = 6
            return 1
        subprocess.call(["cmd", "/c", str(base / "setup_runtime.bat")], cwd=base)
        if not (base / "runtime" / ".ready").exists():
            message("Die Einrichtung ist fehlgeschlagen. Details stehen im Einrichtungsfenster.\n\n"
                    "Prüfe die Internetverbindung und starte ClipForge erneut.")
            return 1
    # Wächter: stürzt ClipForge ab, Neustart anbieten (das zuletzt geöffnete VOD wird wieder geöffnet).
    # Höchstens 3 Neustarts in 5 Minuten, damit ein dauerhafter Fehler nicht endlos neu startet.
    import time

    args = sys.argv[1:]
    restarts: list[float] = []
    while True:
        started = time.time()
        code = subprocess.call([str(pyw), "-m", "clipforge", "--gui", *args], cwd=base, creationflags=NO_WINDOW)
        if code == 0:
            return 0
        restarts = [t for t in restarts if started - t < 300] + [started]
        log = Path.home() / "AppData" / "Local" / "ClipForge" / "logs"
        if len(restarts) > 3:
            message("ClipForge ist mehrfach kurz hintereinander abgestürzt.\n\nDetails stehen in\n"
                    f"{log}\\crash.log und clipforge.log.\n\nBitte diese Dateien an den Entwickler schicken.")
            return code
        if message(f"ClipForge wurde unerwartet beendet (Code {code & 0xFFFFFFFF:#x}).\n\n"
                   "Jetzt neu starten? Das zuletzt geöffnete VOD wird wieder geöffnet, eine laufende Analyse "
                   "geht an derselben Stelle weiter.", flags=0x34) != 6:  # Ja = 6
            return code
        args = []  # Datei/Link nicht erneut übergeben – die Sitzung wird wiederhergestellt


if __name__ == "__main__":
    sys.exit(main())
