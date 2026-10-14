"""Facecam finden, Gameplay-Ausschnitt wählen, Gesicht nachführen.

Ablauf der automatischen Erkennung:
1. Über das ganze VOD verteilt ~24 Bilder ziehen.
2. Gesichter mit MediaPipe suchen, in Kacheln, weil Facecam-Gesichter im Vollbild klein sind.
3. Das Gesicht, das in den meisten Bildern an derselben Stelle ist, gehört zur Facecam
   (Spielfiguren bewegen sich, die Facecam nicht).
4. Den Rand des Facecam-Rahmens über statische Kanten suchen (Spielinhalt wechselt und mittelt
   sich weg, der Rahmen bleibt). Ohne klare Kante gilt eine typische Größe um das Gesicht.
"""

from __future__ import annotations

import threading
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from clipforge.core.errors import Cancelled, ClipForgeError
from clipforge.core.log import get_logger
from clipforge.core.paths import models_dir

log = get_logger("facecam")

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/"
             "float16/latest/blaze_face_short_range.tflite")


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def clamp(self, W: float, H: float) -> "Rect":
        x, y = max(0.0, self.x), max(0.0, self.y)
        w, h = min(self.w, W - x), min(self.h, H - y)
        return Rect(x, y, max(2.0, w), max(2.0, h))

    def norm(self, W: float, H: float) -> list[float]:
        return [round(self.x / W, 4), round(self.y / H, 4), round(self.w / W, 4), round(self.h / H, 4)]

    @classmethod
    def from_norm(cls, v: list[float], W: float, H: float) -> "Rect":
        return cls(v[0] * W, v[1] * H, v[2] * W, v[3] * H)


@dataclass
class Facecam:
    mode: str  # "overlay" (Facecam-Fenster im Bild) | "fullcam" (nur Kamera) | "none"
    cam: list[float] | None = None  # normiert x,y,w,h
    face: list[float] | None = None  # typische Gesichtsposition, normiert
    confidence: float = 0.0
    source: str = "auto"  # "auto" | "profil" | "manuell"
    frames_with_face: int = 0
    frames_total: int = 0
    content: list[float] | None = None  # Bildbereich ohne schwarze Balken: [y0, y1] normiert

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Facecam | None":
        if not d:
            return None
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# --------------------------------------------------------------------------- Bilder + Detektor


def read_frames(path: Path, times: list[float], max_w: int = 1920) -> list[tuple[float, np.ndarray]]:
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ClipForgeError("Das Video konnte für die Bildanalyse nicht geöffnet werden.", "Details im Log.")
    out = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        if frame.shape[1] > max_w:
            s = max_w / frame.shape[1]
            frame = cv2.resize(frame, (max_w, int(frame.shape[0] * s)), interpolation=cv2.INTER_AREA)
        out.append((t, frame))
    cap.release()
    return out


class FaceDetector:
    _lock = threading.Lock()

    def __init__(self, min_confidence: float = 0.55):
        from mediapipe.tasks.python import BaseOptions, vision

        model = models_dir() / "blaze_face_short_range.tflite"
        if not model.exists():
            try:
                with self._lock:
                    tmp = model.with_suffix(".part")
                    urllib.request.urlretrieve(MODEL_URL, tmp)
                    tmp.replace(model)
            except OSError as e:
                raise ClipForgeError("Das Gesichtserkennungs-Modell konnte nicht geladen werden.",
                                     "Prüfe die Internetverbindung oder lege den Facecam-Bereich von Hand fest.",
                                     str(e)) from e
        opts = vision.FaceDetectorOptions(base_options=BaseOptions(model_asset_path=str(model)),
                                          min_detection_confidence=min_confidence)
        self._det = vision.FaceDetector.create_from_options(opts)

    def _run(self, rgb: np.ndarray) -> list[tuple[float, float, float, float, float]]:
        import mediapipe as mp

        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        res = self._det.detect(img)
        out = []
        for d in res.detections:
            b = d.bounding_box
            out.append((float(b.origin_x), float(b.origin_y), float(b.width), float(b.height),
                        float(d.categories[0].score if d.categories else 0.0)))
        return out

    def detect(self, bgr: np.ndarray, tiles: bool = True) -> list[tuple[Rect, float]]:
        """Gesichter im ganzen Bild und in überlappenden Kacheln (für kleine Facecam-Gesichter)."""
        rgb = bgr[:, :, ::-1]
        H, W = rgb.shape[:2]
        found = [(Rect(x, y, w, h), s) for x, y, w, h, s in self._run(rgb)]
        if tiles and W > 700:
            size = int(min(H, W) * 0.6)
            step = int(size * 0.66)
            for ty in range(0, max(1, H - size) + 1, step):
                for tx in range(0, max(1, W - size) + 1, step):
                    tile = rgb[ty:ty + size, tx:tx + size]
                    for x, y, w, h, s in self._run(tile):
                        found.append((Rect(x + tx, y + ty, w, h), s))
                if W - size > 0 and (W - size) % step:
                    tile = rgb[ty:ty + size, W - size:W]
                    for x, y, w, h, s in self._run(tile):
                        found.append((Rect(x + W - size, y + ty, w, h), s))
        return _nms(found)


def _iou(a: Rect, b: Rect) -> float:
    ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    return inter / (a.w * a.h + b.w * b.h - inter + 1e-6)


def _nms(found: list[tuple[Rect, float]], thr: float = 0.3) -> list[tuple[Rect, float]]:
    keep: list[tuple[Rect, float]] = []
    for r, s in sorted(found, key=lambda x: -x[1]):
        if all(_iou(r, k[0]) < thr for k in keep):
            keep.append((r, s))
    return keep


# --------------------------------------------------------------------------- Facecam-Erkennung


def _static_edges(frames: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Mittlere Kantenstärke über alle Bilder: wechselnder Spielinhalt mittelt sich weg,
    der Rahmen der Facecam bleibt. Liefert (vertikale Kanten, horizontale Kanten)."""
    import cv2

    acc_x = acc_y = None
    for f in frames:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3))
        gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
        acc_x = gx if acc_x is None else acc_x + gx
        acc_y = gy if acc_y is None else acc_y + gy
    n = max(1, len(frames))
    return acc_x / n, acc_y / n


def _walk(profile: np.ndarray, start: int, step: int, thr: float, limit: int) -> tuple[str, int] | None:
    """Vom Gesicht nach außen laufen, bis das Bild deutlich unruhiger wird (= Spiel statt Kamera).

    ("kante", i)  Übergang bei i gefunden
    ("rand", i)   Bildrand erreicht, ohne Übergang: der Bildrand ist der Rahmen
    None          Suchweite erschöpft, nichts gefunden
    """
    n = len(profile)
    i = min(max(start, 0), n - 1)
    for _ in range(limit):
        # drei Werte in Folge über der Schwelle, damit einzelne Ausreißer nicht zählen
        if all(0 <= i + step * k < n and profile[i + step * k] > thr for k in (0, 1, 2)):
            return ("kante", i)
        if not 0 <= i + step < n:
            return ("rand", 0 if step < 0 else n)
        i += step
    return None


def _snap(edge: np.ndarray, pos: int, radius: int = 10) -> int:
    """Auf die stärkste statische Kante in der Nähe einrasten (der eigentliche Rahmen)."""
    lo, hi = max(0, pos - radius), min(len(edge), pos + radius + 1)
    if hi <= lo:
        return pos
    return lo + int(np.argmax(edge[lo:hi]))


def _cam_from_variation(frames: list[np.ndarray], face: Rect) -> tuple[Rect, int]:
    """Facecam-Rand = Übergang vom ruhigen Kamerabild (wenig Veränderung über die Bilder)
    zum wechselnden Spielbild (viel Veränderung). Erreicht man den Bildrand, ist er der Rahmen."""
    import cv2

    H, W = frames[0].shape[:2]
    stack = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for f in frames])
    S = cv2.GaussianBlur(stack.std(axis=0), (0, 0), 3)
    ex, ey = _static_edges(frames)
    fx0, fx1 = int(face.x), int(face.x + face.w)
    fy0, fy1 = int(face.y), int(face.y + face.h)
    rows = slice(max(0, int(fy0 - face.h / 2)), min(H, int(fy1 + face.h * 1.5)))
    cols = slice(max(0, int(fx0 - face.w / 2)), min(W, int(fx1 + face.w / 2)))
    col_prof = S[rows, :].mean(axis=0)
    row_prof = S[:, cols].mean(axis=1)
    inside = float(np.median(S[max(0, fy0 - int(face.h)):min(H, fy1 + int(face.h)),
                               max(0, fx0 - int(face.w)):min(W, fx1 + int(face.w))]))
    outside = float(np.percentile(S, 75))
    fw, fh = face.w, face.h
    def_w = 3.9 * fw
    def_h = def_w * 3 / 4
    if outside - inside < 6:  # Spielbild zu ruhig (Menü, Pause): typische Größe annehmen
        return Rect(face.cx - def_w / 2, face.y - 0.9 * fh, def_w, def_h).clamp(W, H), 0
    thr = inside + 0.5 * (outside - inside)
    lim_x, lim_y = int(4.5 * fw), int(4.5 * fh)
    found = 0
    left = _walk(col_prof, int(fx0 - 0.4 * fw), -1, thr, lim_x)
    right = _walk(col_prof, int(fx1 + 0.4 * fw), 1, thr, lim_x)
    top = _walk(row_prof, int(fy0 - 0.4 * fh), -1, thr, lim_y)
    bottom = _walk(row_prof, int(fy1 + 0.8 * fh), 1, thr, lim_y)
    ex_col = ex[rows, :].mean(axis=0)
    ey_row = ey[:, cols].mean(axis=1)

    def resolve(hit, edge_prof):
        nonlocal found
        if hit is None:
            return None
        kind, pos = hit
        found += 1
        return pos if kind == "rand" else _snap(edge_prof, pos)

    x0 = resolve(left, ex_col)
    x1 = resolve(right, ex_col)
    y0 = resolve(top, ey_row)
    y1 = resolve(bottom, ey_row)
    if x0 is None:
        x0 = (x1 - def_w) if x1 is not None else face.cx - def_w / 2
    if x1 is None:
        x1 = x0 + def_w
    if y0 is None:
        y0 = (y1 - def_h) if y1 is not None else face.y - 0.9 * fh
    if y1 is None:
        y1 = y0 + def_h
    return Rect(x0, y0, x1 - x0, y1 - y0).clamp(W, H), found


def detect_letterbox(frames: list[np.ndarray], dark: float = 18.0) -> tuple[int, int]:
    """Schwarze Balken oben/unten (Kinoformat): Zeilen, die in allen Bildern fast schwarz sind."""
    H = frames[0].shape[0]
    # Median statt Maximum: einzelne Bilder ohne Balken (Szenenwechsel, Einblendungen) stören nicht
    rows = np.median(np.stack([f.mean(axis=(1, 2)) for f in frames]), axis=0)
    y0 = 0
    while y0 < H // 3 and rows[y0] < dark:
        y0 += 1
    y1 = H
    while y1 > H * 2 // 3 and rows[y1 - 1] < dark:
        y1 -= 1
    # nur echte Balken (mind. 2 % der Höhe) zählen
    if y0 < H * 0.02:
        y0 = 0
    if H - y1 < H * 0.02:
        y1 = H
    return y0, y1


def clip_letterbox(path: Path, start: float, end: float, n: int = 6) -> list[float] | None:
    """Schwarze Balken innerhalb eines Clips (Zwischensequenzen haben sie oft nur zeitweise)."""
    frames = read_frames(path, [start + (end - start) * (i + 0.5) / n for i in range(n)], max_w=480)
    if not frames:
        return None
    H = frames[0][1].shape[0]
    y0, y1 = detect_letterbox([f for _, f in frames])
    return [round(y0 / H, 4), round(y1 / H, 4)]


def detect_facecam(path: Path, duration: float, n_frames: int = 24, cancel: threading.Event | None = None,
                   detector: FaceDetector | None = None) -> Facecam:
    times = [duration * (i + 0.5) / n_frames for i in range(n_frames)]
    frames = read_frames(path, times)
    if not frames:
        return Facecam("none", frames_total=0)
    fc = _detect(frames, cancel, detector)
    H = frames[0][1].shape[0]
    y0, y1 = detect_letterbox([f for _, f in frames])
    fc.content = [round(y0 / H, 4), round(y1 / H, 4)]
    return fc


def _detect(frames: list[tuple[float, np.ndarray]], cancel, detector) -> Facecam:
    det = detector or FaceDetector()
    H, W = frames[0][1].shape[:2]
    per_frame: list[list[Rect]] = []
    for _, f in frames:
        if cancel and cancel.is_set():
            raise Cancelled()
        per_frame.append([r for r, s in det.detect(f)])

    # Gesichter über die Bilder hinweg nach Position gruppieren
    clusters: list[dict] = []
    radius = 0.06 * W
    for fi, faces in enumerate(per_frame):
        for r in faces:
            for c in clusters:
                if abs(c["cx"] - r.cx) < radius and abs(c["cy"] - r.cy) < radius:
                    c["rects"].append(r)
                    c["frames"].add(fi)
                    c["cx"] = float(np.mean([x.cx for x in c["rects"]]))
                    c["cy"] = float(np.mean([x.cy for x in c["rects"]]))
                    break
            else:
                clusters.append({"cx": r.cx, "cy": r.cy, "rects": [r], "frames": {fi}})
    if not clusters:
        return Facecam("none", frames_total=len(frames))
    best = max(clusters, key=lambda c: (len(c["frames"]), -np.std([x.cx for x in c["rects"]])))
    share = len(best["frames"]) / len(frames)
    if share < 0.3:
        log.info("Kein stabiles Gesicht gefunden (bester Anteil %.0f %%)", share * 100)
        return Facecam("none", frames_with_face=len(best["frames"]), frames_total=len(frames))
    face = Rect(float(np.median([r.x for r in best["rects"]])), float(np.median([r.y for r in best["rects"]])),
                float(np.median([r.w for r in best["rects"]])), float(np.median([r.h for r in best["rects"]])))

    if face.w > 0.16 * W:  # großes Gesicht: reine Kamera, kein Facecam-Fenster
        return Facecam("fullcam", cam=[0, 0, 1, 1], face=face.norm(W, H), confidence=round(share, 2),
                       frames_with_face=len(best["frames"]), frames_total=len(frames))

    cam, edges_found = _cam_from_variation([f for _, f in frames], face)
    conf = round(share * (0.6 + 0.1 * edges_found), 2)
    log.info("Facecam erkannt: %s (Gesicht in %d/%d Bildern, %d Kanten)", cam, len(best["frames"]), len(frames),
             edges_found)
    return Facecam("overlay", cam=cam.norm(W, H), face=face.norm(W, H), confidence=conf,
                   frames_with_face=len(best["frames"]), frames_total=len(frames))


# --------------------------------------------------------------------------- Gameplay-Ausschnitt


def smart_crop_x(path: Path, start: float, end: float, crop_w: int, src_w: int, src_h: int,
                 exclude: Rect | None = None, n: int = 10) -> int:
    """Wählt den horizontalen Ausschnitt mit der meisten Bewegung und Detail (statt stumpf mittig).
    Die Facecam wird dabei ausgeblendet; eine leichte Vorliebe für die Bildmitte verhindert Ausreißer."""
    import cv2

    if crop_w >= src_w:
        return 0
    times = [start + (end - start) * (i + 0.5) / n for i in range(n)]
    frames = read_frames(path, times, max_w=320)
    if len(frames) < 2:
        return (src_w - crop_w) // 2
    small = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for _, f in frames]
    sh, sw = small[0].shape
    motion = np.zeros_like(small[0])
    detail = np.zeros_like(small[0])
    for a, b in zip(small, small[1:]):
        motion += np.abs(b - a)
    for g in small:
        detail += np.abs(cv2.Laplacian(g, cv2.CV_32F))
    m = motion / (motion.mean() + 1e-3) + 0.5 * detail / (detail.mean() + 1e-3)
    if exclude is not None:
        # Facecam zählt negativ: Ausschnitte, die sie anschneiden, werden gemieden
        sx, sy = sw / src_w, sh / src_h
        m[int(exclude.y * sy):int((exclude.y + exclude.h) * sy) + 1,
          int(exclude.x * sx):int((exclude.x + exclude.w) * sx) + 1] = -6.0
    col = m.sum(axis=0)
    win = max(1, int(crop_w * sw / src_w))
    sums = np.convolve(col, np.ones(win), mode="valid")
    centers = np.arange(len(sums)) + win / 2
    prior = np.exp(-((centers - sw / 2) / (sw * 0.45)) ** 2)  # sanfte Vorliebe für die Mitte
    best = int(np.argmax(sums - np.abs(sums).mean() * 0.4 * (1 - prior)))
    x = int(best * src_w / sw)
    return int(min(max(0, x), src_w - crop_w)) // 2 * 2


# --------------------------------------------------------------------------- Nachführen


@dataclass
class Track:
    times: list[float] = field(default_factory=list)
    xs: list[float] = field(default_factory=list)  # linke obere Ecke des Fensters (px)
    ys: list[float] = field(default_factory=list)


def track_window(path: Path, start: float, end: float, region: Rect, win_w: float, win_h: float,
                 detector: FaceDetector | None = None, step: float = 0.5, smooth: float = 0.25,
                 dead_zone: float = 0.04) -> Track:
    """Führt ein Fenster (win_w × win_h) innerhalb von region sanft dem Gesicht nach.

    Glättung: exponentiell (smooth) plus Totzone, damit kleine Kopfbewegungen das Bild nicht wackeln lassen.
    """
    det = detector or FaceDetector()
    times = [start + i * step for i in range(int((end - start) / step) + 1)]
    frames = read_frames(path, times)
    tr = Track()
    cx, cy = region.cx, region.cy
    target_x, target_y = cx, cy
    first = True
    for t, f in frames:
        H, W = f.shape[:2]
        x0, y0 = int(region.x), int(region.y)
        sub = f[y0:int(region.y + region.h), x0:int(region.x + region.w)]
        faces = det.detect(sub, tiles=False) if sub.size else []
        if faces:
            r = min(faces, key=lambda fr: (fr[0].cx + x0 - target_x) ** 2 + (fr[0].cy + y0 - target_y) ** 2)[0]
            fx, fy = r.cx + x0, r.cy + y0 - 0.1 * r.h  # leicht über die Gesichtsmitte (Kopfraum)
            if first:
                cx, cy, first = fx, fy, False
            if abs(fx - target_x) > dead_zone * region.w or abs(fy - target_y) > dead_zone * region.h:
                target_x, target_y = fx, fy
        cx += (target_x - cx) * smooth
        cy += (target_y - cy) * smooth
        x = min(max(region.x, cx - win_w / 2), region.x + region.w - win_w)
        y = min(max(region.y, cy - win_h / 2), region.y + region.h - win_h)
        tr.times.append(t - start)
        tr.xs.append(x)
        tr.ys.append(y)
    return tr


def sendcmd_script(track: Track, filter_name: str = "cam", step: float = 0.1) -> str:
    """FFmpeg-sendcmd-Datei: Fensterposition alle 0,1 s, linear zwischen den Messpunkten."""
    if not track.times:
        return ""
    lines = []
    t_end = track.times[-1]
    t = 0.0
    while t <= t_end + 1e-6:
        x = float(np.interp(t, track.times, track.xs))
        y = float(np.interp(t, track.times, track.ys))
        lines.append(f"{t:.2f} crop@{filter_name} x {int(x) // 2 * 2}, crop@{filter_name} y {int(y) // 2 * 2};")
        t += step
    return "\n".join(lines) + "\n"
