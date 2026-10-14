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
    if layout in NEEDS_CAM and not has_cam and not fullcam:
        return "blur"  # ohne Facecam keine Facecam-Layouts
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


def build(layout: str, li: LayoutInput) -> str:
    tail = f"fps={li.fps},format=yuv420p[v]"
    sw = li.src_w
    sh = _even(li.content_h or li.src_h)  # Gameplay ohne Balken
    cy = _even(li.content_y)
    gx = li.gameplay_x

    if layout == "crop":
        gw = gameplay_width(sh, W, H)
        x = gx if gx is not None else (sw - gw) // 2
        return f"[0:v]crop={gw}:{sh}:{_even(x)}:{cy},scale={W}:{H}:flags=lanczos,setsar=1,{tail}"

    if layout == "stack":
        top, bottom = li.stack_top, H - li.stack_top
        gw = gameplay_width(sh, W, bottom)
        x = gx if gx is not None else (sw - gw) // 2
        return (
            f"[0:v]split=2[a][b];"
            f"[a]{_cam_crop(li)},scale={W}:{top}:flags=lanczos,setsar=1[top];"
            f"[b]crop={gw}:{sh}:{_even(x)}:{cy},scale={W}:{bottom}:flags=lanczos,setsar=1[bot];"
            f"[top][bot]vstack=inputs=2,{tail}"
        )

    if layout == "hand":
        top, bottom = li.stack_top, H - li.stack_top
        gw = gameplay_width(sh, W, bottom)
        x = gx if gx is not None else (sw - gw) // 2
        hw = fit_window(li.hand, W / top)
        return (
            f"[0:v]split=2[a][b];"
            f"[a]{_crop(hw)},scale={W}:{top}:flags=lanczos,setsar=1[top];"
            f"[b]crop={gw}:{sh}:{_even(x)}:{cy},scale={W}:{bottom}:flags=lanczos,setsar=1[bot];"
            f"[top][bot]vstack=inputs=2,{tail}"
        )

    if layout == "face_hand":
        fh_, hh_ = li.face_hand_rows
        gh = H - fh_ - hh_
        gw = gameplay_width(sh, W, gh)
        x = gx if gx is not None else (sw - gw) // 2
        face_c = (li.face.cx, li.face.cy) if li.face else None
        fwin = fit_window(li.cam, W / fh_, face_c)
        hwin = fit_window(li.hand, W / hh_)
        return (
            f"[0:v]split=3[a][b][c];"
            f"[a]{_crop(fwin)},scale={W}:{fh_}:flags=lanczos,setsar=1[f];"
            f"[b]{_crop(hwin)},scale={W}:{hh_}:flags=lanczos,setsar=1[h];"
            f"[c]crop={gw}:{sh}:{_even(x)}:{cy},scale={W}:{gh}:flags=lanczos,setsar=1[g];"
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
        gw = gameplay_width(sh, W, H)
        x = gx if gx is not None else (sw - gw) // 2
        c = li.cam
        cw = li.corner_width
        ch = _even(cw * c.h / c.w)
        return (
            f"[0:v]split=2[a][b];"
            f"[a]crop={gw}:{sh}:{_even(x)}:{cy},scale={W}:{H}:flags=lanczos,setsar=1[bg];"
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
        fh = _even(W * c.h / c.w)
        # mit markiertem Objekt: nachgeführter, mitzoomender Ausschnitt; sonst das ganze Fenster
        crop = _cam_crop(li) if li.cam_window else f"crop={_even(c.w)}:{_even(c.h)}:{_even(c.x)}:{_even(c.y)}"
        return (
            f"[0:v]{crop},split=2[a][b];"
            f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:2,"
            f"scale={W}:{H},setsar=1[bg];"
            f"[b]scale={W}:{fh}:flags=lanczos,setsar=1[fg];"
            f"[bg][fg]overlay=0:(H-h)*0.42,{tail}"
        )

    # blur (Standard): Hintergrund klein rechnen, weichzeichnen, hochskalieren – spart viel Rechenzeit
    return (
        f"[0:v]crop={_even(sw)}:{sh}:0:{cy},split=2[bg][fg];"
        f"[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,"
        f"scale={W}:{H},setsar=1[bgb];"
        f"[fg]scale={W}:-2:flags=lanczos,setsar=1[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,{tail}"
    )
