"""9:16-Layouts als FFmpeg-Filtergraphen (Ausgabe 1080×1920).

Jedes Layout bekommt: Quellgröße, Facecam (falls bekannt), gewählten Gameplay-Ausschnitt und
optional eine Nachführung (sendcmd-Datei), und liefert einen Filter, der in [v] endet.
"""

from __future__ import annotations

from dataclasses import dataclass

from clipforge.pipeline.facecam import Rect

W, H = 1080, 1920

LAYOUTS = {
    "auto": "Automatisch (nach Facecam)",
    "stack": "Facecam oben, Gameplay unten",
    "corner": "Gameplay mit Facecam-Ecke",
    "cam": "Nur Facecam",
    "hand": "Handcam oben, Gameplay unten",
    "face_hand": "Facecam + Handcam + Gameplay",
    "hand_only": "Nur Handcam",
    "blur": "Gameplay mittig, unscharfer Hintergrund",
    "crop": "Gameplay zugeschnitten (Vollbild)",
}
NEEDS_CAM = {"stack", "corner", "cam"}
NEEDS_HAND = {"hand", "face_hand", "hand_only"}


@dataclass
class LayoutInput:
    src_w: int
    src_h: int
    fps: int = 30
    cam: Rect | None = None  # Facecam-Fenster im Quellbild (px)
    fullcam: bool = False  # ganze Quelle ist Kamerabild
    face: Rect | None = None  # typische Gesichtsposition (px)
    gameplay_x: int | None = None  # linker Rand des Gameplay-Ausschnitts (px)
    cam_window: tuple[int, int] | None = None  # Größe des nachgeführten Fensters
    cam_xy: tuple[int, int] | None = None  # Startposition des Fensters
    tracked: bool = False  # sendcmd-Datei "track.txt" im Arbeitsordner vorhanden
    stack_top: int = 640  # Höhe der Facecam oben (≈ 1/3)
    hand: Rect | None = None  # Handcam-Fenster im Quellbild (px)
    face_hand_rows: tuple[int, int] = (560, 640)  # Höhen Facecam / Handcam im Layout face_hand
    content_y: int = 0  # Bildbereich ohne schwarze Balken (Kinoformat)
    content_h: int | None = None
    corner_width: int = 440
    accent: str = "0x9146FF"


def resolve_layout(layout: str, has_cam: bool, fullcam: bool, has_hand: bool = False) -> str:
    if layout in ("", "auto"):
        if has_hand:
            return "face_hand" if has_cam else "hand"
        return "cam" if fullcam else ("stack" if has_cam else "blur")
    if layout in NEEDS_HAND and not has_hand:
        layout = "stack" if layout == "face_hand" else "blur"
    if layout == "face_hand" and not has_cam:
        return "hand"
    if layout in NEEDS_CAM and not has_cam:
        # ohne Facecam-Fenster: bei reinem Kamerabild (Vollbild-Kamera) groß die Kamera, sonst Gameplay mittig
        return "cam" if fullcam else "blur"
    return layout


def fit_window(region: Rect, aspect: float, center: tuple[float, float] | None = None) -> tuple[int, int, int, int]:
    """Größtes Fenster mit Seitenverhältnis aspect in region, um center gelegt (x, y, b, h)."""
    w, h = cam_window_size(region, aspect)
    cx, cy = center or (region.cx, region.cy)
    x = min(max(region.x, cx - w / 2), region.x + region.w - w)
    y = min(max(region.y, cy - h / 2), region.y + region.h - h)
    return _even(x), _even(y), w, h


def _crop(win: tuple[int, int, int, int]) -> str:
    x, y, w, h = win
    return f"crop={w}:{h}:{x}:{y}"


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def cam_window_size(region: Rect, aspect: float) -> tuple[int, int]:
    """Größtes Fenster mit Seitenverhältnis aspect (b/h), das in region passt."""
    if region.w / region.h > aspect:
        h = region.h
        return _even(h * aspect), _even(h)
    w = region.w
    return _even(w), _even(w / aspect)


def gameplay_width(src_h: int, out_w: int, out_h: int) -> int:
    return _even(src_h * out_w / out_h)


def _cam_crop(li: LayoutInput, name: str = "cam") -> str:
    ww, wh = li.cam_window
    x, y = li.cam_xy
    prefix = "sendcmd=f=track.txt," if li.tracked else ""
    return f"{prefix}crop@{name}=w={ww}:h={wh}:x={_even(x)}:y={_even(y)}"


PANEL_MIN, PANEL_MAX = 560, 900  # Höhe des Kamerabereichs oben (von 1920)


def panel_height(r: Rect, lo: int = PANEL_MIN, hi: int = PANEL_MAX) -> int:
    """Höhe, in der das Kamerafenster r in voller Breite ganz zu sehen ist – begrenzt, damit genug Gameplay
    bleibt. Breite Cams bekommen wenig, hochkant-Cams (z. B. ein Avatar am Rand) viel Platz."""
    return _even(min(hi, max(lo, W * r.h / max(r.w, 1))))


def _fit_panel(r: Rect, w: int, h: int, tag: str) -> str:
    """Kamerafenster r KOMPLETT in w×h zeigen (nichts abgeschnitten): so groß wie möglich, die freien Ränder
    füllt eine unscharfe Kopie derselben Cam. Liefert eine Filterkette ohne Ein-/Ausgangslabel."""
    crop = f"crop={_even(r.w)}:{_even(r.h)}:{_even(r.x) if r.x >= 2 else 0}:{_even(r.y) if r.y >= 2 else 0}"
    if abs((r.w / r.h) - (w / h)) / (w / h) < 0.03:  # passt praktisch genau
        return f"{crop},scale={w}:{h}:flags=lanczos,setsar=1"
    bw, bh = _even(w / 4), _even(h / 4)
    return (f"{crop},split=2[{tag}b][{tag}f];"
            f"[{tag}b]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},boxblur=8:2,"
            f"scale={w}:{h},setsar=1[{tag}bb];"
            f"[{tag}f]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,"
            f"setsar=1[{tag}ff];"
            f"[{tag}bb][{tag}ff]overlay=(W-w)/2:(H-h)/2")


def _gameplay_crop(sw: int, sh: int, cy: int, out_h: int, gx: int | None) -> str:
    """Gameplay-Ausschnitt mit Seitenverhältnis W:out_h. Bei schmalen Quellen (Hochformat) wäre er breiter
    als das Bild: dann volle Breite und passend weniger Höhe (mittig)."""
    gw = gameplay_width(sh, W, out_h)
    gh = sh
    if gw > sw:
        gw = _even(sw) if sw % 2 == 0 else _even(sw - 1)
        gh = min(sh, _even(gw * out_h / W))
        cy = cy + (sh - gh) // 2
    x = gx if gx is not None else (sw - gw) // 2
    x = min(max(0, x), sw - gw)
    return f"crop={gw}:{gh}:{_even(x) if x >= 2 else 0}:{_even(cy) if cy >= 2 else 0}"


def build(layout: str, li: LayoutInput) -> str:
    tail = f"fps={li.fps},format=yuv420p[v]"
    sw = li.src_w
    sh = _even(li.content_h or li.src_h)  # Gameplay ohne Balken
    cy = _even(li.content_y) if li.content_y >= 2 else 0
    gx = li.gameplay_x

    if layout == "crop":
        return f"[0:v]{_gameplay_crop(sw, sh, cy, H, gx)},scale={W}:{H}:flags=lanczos,setsar=1,{tail}"

    if layout == "stack":
        top, bottom = li.stack_top, H - li.stack_top
        gcrop = _gameplay_crop(sw, sh, cy, bottom, gx)
        cam = _fit_panel(li.cam, W, top, "t") if li.cam else f"{_cam_crop(li)},scale={W}:{top}:flags=lanczos,setsar=1"
        return (
            f"[0:v]split=2[a][b];"
            f"[a]{cam}[top];"
            f"[b]{gcrop},scale={W}:{bottom}:flags=lanczos,setsar=1[bot];"
            f"[top][bot]vstack=inputs=2,{tail}"
        )

    if layout == "hand":
        top, bottom = li.stack_top, H - li.stack_top
        gcrop = _gameplay_crop(sw, sh, cy, bottom, gx)
        return (
            f"[0:v]split=2[a][b];"
            f"[a]{_fit_panel(li.hand, W, top, 't')}[top];"
            f"[b]{gcrop},scale={W}:{bottom}:flags=lanczos,setsar=1[bot];"
            f"[top][bot]vstack=inputs=2,{tail}"
        )

    if layout == "face_hand":
        fh_, hh_ = li.face_hand_rows
        gh = H - fh_ - hh_
        gcrop = _gameplay_crop(sw, sh, cy, gh, gx)
        return (
            f"[0:v]split=3[a][b][c];"
            f"[a]{_fit_panel(li.cam, W, fh_, 'f')}[f];"
            f"[b]{_fit_panel(li.hand, W, hh_, 'h')}[h];"
            f"[c]{gcrop},scale={W}:{gh}:flags=lanczos,setsar=1[g];"
            f"[f][h][g]vstack=inputs=3,{tail}"
        )

    if layout == "hand_only":
        c = li.hand
        fh = _even(W * c.h / c.w)
        crop = f"crop={_even(c.w)}:{_even(c.h)}:{_even(c.x)}:{_even(c.y)}"
        if fh >= H:  # Handcam im Hochformat: auf 9:16 zuschneiden
            return f"[0:v]{_crop(fit_window(c, W / H))},scale={W}:{H}:flags=lanczos,setsar=1,{tail}"
        return (
            f"[0:v]{crop},split=2[a][b];"
            f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:2,"
            f"scale={W}:{H},setsar=1[bg];"
            f"[b]scale={W}:{fh}:flags=lanczos,setsar=1[fg];"
            f"[bg][fg]overlay=0:(H-h)/2,{tail}"
        )

    if layout == "corner":
        gcrop = _gameplay_crop(sw, sh, cy, H, gx)
        c = li.cam
        cw = li.corner_width
        ch = _even(cw * c.h / c.w)
        return (
            f"[0:v]split=2[a][b];"
            f"[a]{gcrop},scale={W}:{H}:flags=lanczos,setsar=1[bg];"
            f"[b]crop={_even(c.w)}:{_even(c.h)}:{_even(c.x)}:{_even(c.y)},scale={cw}:{ch}:flags=lanczos,"
            f"pad={cw + 12}:{ch + 12}:6:6:color={li.accent},setsar=1[cam];"
            f"[bg][cam]overlay=36:170,{tail}"
        )

    if layout == "cam":
        if li.fullcam:
            # ganzes Bild ist Kamera: 9:16-Fenster folgt dem Gesicht
            return f"[0:v]{_cam_crop(li)},scale={W}:{H}:flags=lanczos,setsar=1,{tail}"
        # Facecam-Fenster groß in die Mitte, unscharfe Kopie als Hintergrund
        c = li.cam
        if not li.cam_window:  # ganzes Fenster, eingepasst
            return f"[0:v]{_fit_panel(c, W, H, 'c')},{tail}"
        # mit markiertem Objekt: nachgeführter, mitzoomender Ausschnitt, ebenfalls ganz eingepasst
        return (
            f"[0:v]{_cam_crop(li)},split=2[a][b];"
            f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:2,"
            f"scale={W}:{H},setsar=1[bg];"
            f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{tail}"
        )

    # blur (Standard): Hintergrund klein rechnen, weichzeichnen, hochskalieren – spart viel Rechenzeit
    return (
        f"[0:v]crop={_even(sw)}:{sh}:0:{cy},split=2[bg][fg];"
        f"[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,"
        f"scale={W}:{H},setsar=1[bgb];"
        f"[fg]scale={W}:-2:flags=lanczos,setsar=1[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,{tail}"
    )
