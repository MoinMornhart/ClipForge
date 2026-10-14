"""Kommandozeile – praktisch zum Testen und für Stapelverarbeitung.

  python -m clipforge analyze <URL oder Datei>
  python -m clipforge export <URL oder Datei> [--approved]
  python -m clipforge run <URL oder Datei>        (analysieren + exportieren)
  python -m clipforge check                       (Systemprüfung)
"""

from __future__ import annotations

import argparse
import sys
import threading

from clipforge import __version__
from clipforge.core.errors import Cancelled, ClipForgeError
from clipforge.core.log import get_logger, setup_logging
from clipforge.core.settings import Settings

log = get_logger("cli")


def _progress_printer():
    last = {"step": None}

    def cb(step: str, frac: float, msg: str) -> None:
        bar = "█" * int(frac * 20) + "░" * (20 - int(frac * 20))
        sys.stdout.write(f"\r[{bar}] {frac * 100:5.1f} %  {msg[:70]:<70}")
        sys.stdout.flush()
        if frac >= 1.0 or step != last["step"]:
            if frac >= 1.0:
                sys.stdout.write("\n")
            last["step"] = step

    return cb


def _fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def cmd_check(_args) -> int:
    from clipforge.core import ffmpeg, secrets
    from clipforge.core.cuda import register_cuda_dlls

    s = Settings.load()
    print(f"ClipForge {__version__}")
    try:
        print("FFmpeg:  ", ffmpeg.ffmpeg(s.ffmpeg_path))
        print("NVENC:   ", "ja" if ffmpeg.nvenc_available(s.ffmpeg_path) else "nein (CPU-Kodierung)")
    except ClipForgeError as e:
        print("FFmpeg:   FEHLT –", e)
    register_cuda_dlls()
    try:
        import ctranslate2

        print("CUDA-GPU:", "ja" if ctranslate2.get_cuda_device_count() > 0 else "nein (CPU-Transkription)")
    except Exception as e:
        print("CUDA-GPU: nein –", e)
    for p in secrets.PROVIDERS:
        print(f"API-Key {p}:", secrets.mask(secrets.get_key(p)))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="clipforge", description="Clips aus Twitch-VODs finden und im Hochformat exportieren.")
    ap.add_argument("-v", "--verbose", action="store_true", help="ausführliche Ausgabe")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("analyze", "export", "run"):
        p = sub.add_parser(name)
        p.add_argument("source", help="Twitch-VOD-URL oder Pfad zu einer Videodatei")
        p.add_argument("--layout", choices=["blur", "crop"], help="Layout für den Export")
        p.add_argument("--approved", action="store_true", help="nur freigegebene Clips exportieren")
    sub.add_parser("check")
    args = ap.parse_args(argv)
    setup_logging(args.verbose)

    if args.cmd == "check":
        return cmd_check(args)

    from clipforge.pipeline.runner import Pipeline

    s = Settings.load()
    if args.layout:
        s.default_layout = args.layout
    cancel = threading.Event()
    pipe = Pipeline(s, _progress_printer(), cancel)
    try:
        proj, clips = pipe.analyze(args.source)
        print(f"\n{len(clips)} Clip(s) gefunden:")
        for i, c in enumerate(clips, 1):
            print(f"  {i:2d}. {_fmt(c.start)}–{_fmt(c.end)} ({c.duration:.0f} s)  {'; '.join(c.reasons)}")
        if args.cmd in ("export", "run") and clips:
            paths = pipe.export(proj, only_approved=args.approved)
            for p in paths:
                print("  →", p)
        return 0
    except KeyboardInterrupt:
        cancel.set()
        print("\nAbgebrochen. Beim nächsten Start geht es an derselben Stelle weiter.")
        return 130
    except Cancelled:
        print("\nAbgebrochen.")
        return 130
    except ClipForgeError as e:
        log.error("%s | %s", e.message, e.detail or "")
        print(f"\nFehler: {e}", file=sys.stderr)
        return 1
