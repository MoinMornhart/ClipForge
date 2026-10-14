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


def _parse_time(s: str) -> float:
    parts = [float(p) for p in s.strip().split(":")]
    t = 0.0
    for p in parts:
        t = t * 60 + p
    return t


def _parse_section(s: str) -> tuple[float, float]:
    try:
        a, b = s.split("-", 1)
        start, end = _parse_time(a), _parse_time(b)
    except ValueError as e:
        raise ClipForgeError(f"„{s}“ ist kein gültiger Ausschnitt.", "Beispiel: --section 1:00:00-1:20:00") from e
    if end <= start:
        raise ClipForgeError("Das Ende des Ausschnitts liegt vor dem Anfang.", "Beispiel: --section 1:00:00-1:20:00")
    return start, end


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
    from clipforge.core.providers import LLM_PROVIDERS

    print(f"KI-Anbieter: {LLM_PROVIDERS[s.llm_provider].name if s.llm_provider in LLM_PROVIDERS else s.llm_provider}"
          f" · Modell: {s.llm_model or '–'}")
    for p in secrets.PROVIDERS:
        print(f"API-Key {LLM_PROVIDERS[p].name:<36}", secrets.mask(secrets.get_key(p)))
    return 0


def cmd_key(args) -> int:
    import getpass

    from clipforge.core import secrets
    from clipforge.core.providers import LLM_PROVIDERS

    if args.action == "list" or not args.provider:
        for pid, p in LLM_PROVIDERS.items():
            state = secrets.mask(secrets.get_key(pid)) if p.needs_key or pid == "custom" else "kein Key nötig"
            print(f"  {pid:<11} {p.name:<36} {state}")
        return 0
    if args.provider not in LLM_PROVIDERS:
        print(f"Unbekannter Anbieter. Möglich: {', '.join(LLM_PROVIDERS)}", file=sys.stderr)
        return 2
    if args.action == "delete":
        secrets.delete_key(args.provider)
        print("Key gelöscht.")
        return 0
    key = getpass.getpass(f"API-Key für {LLM_PROVIDERS[args.provider].name} (Eingabe bleibt unsichtbar): ")
    secrets.set_key(args.provider, key)
    print("Gespeichert in der Windows-Anmeldeinformationsverwaltung." if key.strip() else "Leer – Key entfernt.")
    return 0


def cmd_keyword(args) -> int:
    from clipforge.core.settings import Trigger

    s = Settings.load()
    if args.action == "list":
        print("Sprachbefehle:")
        for t in s.voice_triggers:
            print(f"  {'✓' if t.enabled else '–'} {t.phrase:<32} Ähnlichkeit ≥ {t.threshold} %")
        print("Eigene Schlüsselbegriffe:")
        for t in s.custom_keywords or []:
            print(f"  {'✓' if t.enabled else '–'} {t.phrase:<32} Ähnlichkeit ≥ {t.threshold} %, "
                  f"{t.pre:.0f} s davor, {t.post:.0f} s danach")
        if not s.custom_keywords:
            print("  (keine)  →  clipforge keyword add \"was geht ab\"")
        return 0
    if not args.phrase:
        print("Bitte einen Begriff angeben.", file=sys.stderr)
        return 2
    phrase = args.phrase.strip()
    s.custom_keywords = [t for t in s.custom_keywords if t.phrase.lower() != phrase.lower()]
    if args.action == "add":
        s.custom_keywords.append(Trigger(phrase, threshold=args.threshold, pre=args.pre, post=args.post))
        print(f"„{phrase}“ hinzugefügt.")
    else:
        print(f"„{phrase}“ gelöscht.")
    s.save()
    return 0


def cmd_costs(_args) -> int:
    from clipforge.pipeline.project import Project

    rows = [p for p in Project.all() if p.source]
    if not rows:
        print("Noch keine VODs analysiert.")
        return 0
    print(f"{'Datum':<11} {'Streamer':<16} {'Kosten':>10}  Details")
    for p in rows:
        src = p.source
        c = p.cost_summary
        usd = f"{c['usd']:.2f} $" + ("" if c["complete"] else "+?")
        detail = p.cost_text().split("(", 1)[-1].rstrip(")") if c["items"] else "nur lokal"
        print(f"{src.date or '–':<11} {(src.streamer or 'Datei')[:16]:<16} {usd:>10}  {detail}  · {src.title[:40]}")
    total, n = Project.total_cost_all()
    print(f"\nGesamt: {total:.2f} $ für {n} VOD(s) mit API-Nutzung")
    return 0


def cmd_models(args) -> int:
    from clipforge.pipeline.llm import list_models

    for m in list_models(Settings.load(), args.provider):
        print(" ", m)
    return 0


def _confirm_factory(auto_yes: bool):
    def confirm(est) -> bool:
        print(f"\nGeschätzte Kosten: {est.text()}")
        if auto_yes:
            return True
        try:
            return input("Fortfahren? [J/n] ").strip().lower() in ("", "j", "ja", "y", "yes")
        except EOFError:
            return False

    return confirm


def _utf8_console() -> None:
    """Windows-Konsolen mit alter Codepage (cp1252/cp850) stürzen sonst bei „→“, Emojis in VOD-Titeln usw. ab."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    ap = argparse.ArgumentParser(prog="clipforge", description="Clips aus Twitch-VODs finden und im Hochformat exportieren.")
    ap.add_argument("-v", "--verbose", action="store_true", help="ausführliche Ausgabe")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("analyze", "export", "run"):
        p = sub.add_parser(name)
        p.add_argument("source", help="Twitch-VOD-URL oder Pfad zu einer Videodatei")
        p.add_argument("--layout", choices=["auto", "stack", "corner", "cam", "hand", "face_hand", "hand_only", "blur", "crop"], help="Layout für den Export")
        p.add_argument("--approved", action="store_true", help="nur freigegebene Clips exportieren")
        p.add_argument("-y", "--yes", action="store_true", help="Kostenabfrage automatisch bestätigen")
        p.add_argument("--no-ai", action="store_true", help="ohne KI-Bewertung")
        p.add_argument("--section", help="nur Ausschnitt laden, z. B. 1:00:00-1:20:00 (nur bei Links)")
    sub.add_parser("check")
    k = sub.add_parser("key", help="API-Keys verwalten")
    k.add_argument("action", choices=["set", "delete", "list"])
    k.add_argument("provider", nargs="?")
    m = sub.add_parser("models", help="verfügbare Modelle eines Anbieters anzeigen")
    m.add_argument("provider", nargs="?")
    sub.add_parser("costs", help="tatsächliche API-Kosten pro VOD anzeigen")
    kw = sub.add_parser("keyword", help="eigene Schlüsselbegriffe verwalten")
    kw.add_argument("action", choices=["list", "add", "delete"])
    kw.add_argument("phrase", nargs="?")
    kw.add_argument("--threshold", type=int, default=80, help="Mindestähnlichkeit in %% (Standard 80)")
    kw.add_argument("--pre", type=float, default=30.0, help="Sekunden vor dem Begriff")
    kw.add_argument("--post", type=float, default=4.0, help="Sekunden nach dem Begriff")
    args = ap.parse_args(argv)
    setup_logging(args.verbose)

    try:
        if args.cmd == "check":
            return cmd_check(args)
        if args.cmd == "key":
            return cmd_key(args)
        if args.cmd == "models":
            return cmd_models(args)
        if args.cmd == "keyword":
            return cmd_keyword(args)
        if args.cmd == "costs":
            return cmd_costs(args)
    except ClipForgeError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1

    from clipforge.pipeline.runner import Pipeline

    s = Settings.load()
    if args.layout:
        s.default_layout = args.layout
    if args.no_ai:
        s.ai_enabled = False
    cancel = threading.Event()
    pipe = Pipeline(s, _progress_printer(), cancel)
    try:
        section = _parse_section(args.section) if args.section else None
        proj, clips = pipe.analyze(args.source, confirm=_confirm_factory(args.yes), section=section)
        print(f"\n{len(clips)} Clip(s) gefunden:")
        for i, c in enumerate(clips, 1):
            score = f"[{c.score:.0f}/10] " if c.score is not None else ""
            print(f"  {i:2d}. {score}{_fmt(c.start)}–{_fmt(c.end)} ({c.duration:.0f} s)  {c.title or ''}")
            print(f"      Grund: {'; '.join(c.reasons)}")
            if c.ai_reason:
                print(f"      KI: {c.ai_reason}")
            if c.hashtags:
                print(f"      {' '.join(c.hashtags)}")
        print(f"\nTatsächliche API-Kosten für dieses VOD: {proj.cost_text()}")
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
