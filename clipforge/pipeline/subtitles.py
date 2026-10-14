"""TikTok-Untertitel: wenige Wörter pro Einblendung, das gesprochene Wort hervorgehoben.

Erzeugt eine ASS-Datei (Advanced SubStation), die FFmpeg/libass ins Video brennt.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from clipforge.core.log import get_logger
from clipforge.pipeline.transcribe import Word

log = get_logger("subtitles")


@dataclass
class SubtitleStyle:
    enabled: bool = True
    font: str = "Arial Black"
    size: int = 84
    color: str = "#FFFFFF"
    highlight: str = "#FFD400"
    outline_color: str = "#000000"
    outline: int = 7
    shadow: int = 3
    position: float = 0.68  # vertikale Position der Textmitte, 0 = oben, 1 = unten
    uppercase: bool = True
    max_words: int = 3
    max_chars: int = 18
    pop: bool = True  # aktives Wort kurz vergrößern

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "SubtitleStyle":
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    """#RRGGBB → &HAABBGGRR (ASS-Reihenfolge)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _clean(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\\", "/").strip()


def group_words(words: list[Word], max_words: int = 3, max_chars: int = 18, gap: float = 0.6) -> list[list[Word]]:
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if not w.w.strip():
            continue
        length = sum(len(x.w) + 1 for x in cur) + len(w.w)
        if cur and (len(cur) >= max_words or length > max_chars or w.s - cur[-1].e > gap):
            groups.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?…]$", w.w):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def clip_words(words: list[Word], start: float, end: float) -> list[Word]:
    """Wörter im Clip, Zeiten relativ zum Clipstart."""
    return [Word(w.w, max(0.0, w.s - start), min(end, w.e) - start, w.p) for w in words if start <= w.s < end]


def build_ass(words: list[Word], style: SubtitleStyle, duration: float) -> str:
    st = style
    margin_v = int(1920 * (1 - st.position)) - st.size // 2
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st.font},{st.size},{_ass_color(st.color)},{_ass_color(st.highlight)},{_ass_color(st.outline_color)},{_ass_color('#000000', 0x60)},-1,0,0,0,100,100,0,0,1,{st.outline},{st.shadow},2,60,60,{max(0, margin_v)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    hi = _ass_color(st.highlight)
    base = _ass_color(st.color)
    for g in group_words(words, st.max_words, st.max_chars):
        texts = [_clean(w.w.upper() if st.uppercase else w.w) for w in g]
        for i, w in enumerate(g):
            t0 = w.s
            t1 = g[i + 1].s if i + 1 < len(g) else max(w.e, w.s + 0.25)
            if i == len(g) - 1:
                t1 = min(t1 + 0.15, duration)
            parts = []
            for j, tx in enumerate(texts):
                if j == i:
                    pop = r"\t(0,80,\fscx112\fscy112)\t(80,160,\fscx100\fscy100)" if st.pop else ""
                    parts.append(f"{{\\c{hi}{pop}}}{tx}{{\\c{base}\\fscx100\\fscy100}}")
                else:
                    parts.append(tx)
            lines.append(f"Dialogue: 0,{_ts(t0)},{_ts(t1)},Cap,,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- Schriften


def find_font_file(name: str) -> Path | None:
    """Sucht die Datei einer installierten Windows-Schrift (System und Benutzer)."""
    p = Path(name)
    if p.suffix.lower() in (".ttf", ".otf") and p.exists():
        return p
    try:
        import winreg
    except ImportError:
        return None
    want = name.lower()
    hits: list[Path] = []
    for root, folder in ((winreg.HKEY_LOCAL_MACHINE, Path("C:/Windows/Fonts")),
                         (winreg.HKEY_CURRENT_USER, None)):
        try:
            key = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
        except OSError:
            continue
        i = 0
        while True:
            try:
                label, file, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            i += 1
            lab = re.sub(r"\s*\((TrueType|OpenType)\)\s*$", "", label, flags=re.I).lower()
            if lab == want or lab.split(" & ")[0] == want:
                fp = Path(file) if Path(file).is_absolute() else (folder or Path()) / file
                if fp.exists():
                    hits.append(fp)
    return hits[0] if hits else None


def prepare_fonts(style: SubtitleStyle, work_dir: Path) -> None:
    """Kopiert die gewählte Schrift in den Arbeitsordner (libass findet sie dort sicher)."""
    fonts = work_dir / "fonts"
    fonts.mkdir(exist_ok=True)
    f = find_font_file(style.font)
    if f:
        shutil.copy2(f, fonts / f.name)
    else:
        log.warning("Schrift „%s“ nicht gefunden, nutze Arial Black/Arial", style.font)
        for fallback in ("ariblk.ttf", "arialbd.ttf", "arial.ttf"):
            fp = Path("C:/Windows/Fonts") / fallback
            if fp.exists():
                shutil.copy2(fp, fonts / fp.name)
                break
