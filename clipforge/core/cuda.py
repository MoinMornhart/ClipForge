"""Macht die per pip installierten CUDA-Bibliotheken (cuBLAS, cuDNN) für CTranslate2 auffindbar."""

import os
import sys
from pathlib import Path

_done = False


def register_cuda_dlls() -> list[str]:
    global _done
    if _done or sys.platform != "win32":
        return []
    _done = True
    added = []
    for sp in map(Path, sys.path):
        nv = sp / "nvidia"
        if not nv.is_dir():
            continue
        for bin_dir in nv.glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            added.append(str(bin_dir))
    return added
