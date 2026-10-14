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
    subprocess.Popen([str(pyw), "-m", "clipforge", "--gui", *sys.argv[1:]], cwd=base,
                     creationflags=DETACHED | NO_WINDOW)
    return 0


if __name__ == "__main__":
    sys.exit(main())
