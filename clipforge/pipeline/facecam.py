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
        return [round(float(self.x) / W, 4), round(float(self.y) / H, 4), round(float(self.w) / W, 4),
                round(float(self.h) / H, 4)]

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
    subject: list[float] | None = None  # markiertes Objekt (z. B. Bär) beim Markieren, normiert
    subject_file: str = ""  # Bildausschnitt des Objekts (PNG) zum Wiederfinden
    handcam: list[float] | None = None  # zweites Kamerafenster mit den Händen, normiert x,y,w,h
    hand_source: str = ""  # "auto" | "manuell" | "profil"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Facecam | None":
        if not d:
            return None
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# --------------------------------------------------------------------------- Bilder + Detektor


FULL_RES = 1 << 20  # max_w für Bilder in Originalgröße (Koordinaten = Quellpixel)


def read_frames(path: Path, times: list[float], max_w: int = 1920) -> list[tuple[float, np.ndarray]]:
    """Einzelbilder lesen. Achtung: Breitere Bilder werden auf max_w verkleinert – wer mit Quellpixel-
    Koordinaten (Rect aus media width/height) rechnet, muss max_w=FULL_RES übergeben."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ClipForgeError("Das Video konnte für die Bildanalyse nicht geöffnet werden.", "Details im Log.")
    out = []
    try:
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            if frame.shape[1] > max_w:
                s = max_w / frame.shape[1]
                frame = cv2.resize(frame, (max_w, int(frame.shape[0] * s)), interpolation=cv2.INTER_AREA)
            out.append((t, frame))
    finally:
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


HAND_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/"
                  "hand_landmarker.task")


class HandDetector:
    """Hände finden (MediaPipe Hand Landmarker). Liefert pro Hand ein Rechteck um alle Gelenkpunkte."""

    def __init__(self, min_confidence: float = 0.4):
        from mediapipe.tasks.python import BaseOptions, vision

        model = models_dir() / "hand_landmarker.task"
        if not model.exists():
            try:
                tmp = model.with_suffix(".part")
                urllib.request.urlretrieve(HAND_MODEL_URL, tmp)  # noqa: S310 – feste https-Adresse
                tmp.replace(model)
            except OSError as e:
                raise ClipForgeError("Das Hand-Erkennungsmodell konnte nicht geladen werden.",
                                     "Prüfe die Internetverbindung oder markiere die Handcam von Hand.", str(e)) from e
        opts = vision.HandLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(model)), num_hands=2,
                                            min_hand_detection_confidence=min_confidence,
                                            min_hand_presence_confidence=min_confidence)
        self._det = vision.HandLandmarker.create_from_options(opts)

    def _run(self, rgb: np.ndarray) -> list[Rect]:
        import mediapipe as mp

        h, w = rgb.shape[:2]
        res = self._det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
        out = []
        for lm in res.hand_landmarks or []:
            xs, ys = [p.x * w for p in lm], [p.y * h for p in lm]
            out.append(Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
        return out

    def detect(self, bgr: np.ndarray) -> list[Rect]:
        rgb = bgr[:, :, ::-1]
        H, W = rgb.shape[:2]
        found = self._run(rgb)
        size = int(min(H, W) * 0.6)
        step = int(size * 0.66)
        for ty in range(0, max(1, H - size) + 1, step):
            for tx in list(range(0, max(1, W - size) + 1, step)) + ([W - size] if W > size else []):
                for r in self._run(rgb[ty:ty + size, tx:tx + size]):
                    found.append(Rect(r.x + tx, r.y + ty, r.w, r.h))
        return [r for r, _ in _nms([(r, 1.0) for r in found], 0.3)]


def _snap_box(ex: np.ndarray, ey: np.ndarray, box: Rect, W: int, H: int) -> Rect:
    """Rand eines Overlay-Fensters an die stärksten statischen Kanten in der Nähe von box legen."""
    def strongest(profile, lo, hi, default):
        lo, hi = max(0, int(lo)), min(len(profile), int(hi))
        if hi - lo < 4:
            return default
        seg = profile[lo:hi]
        i = int(np.argmax(seg))
        return lo + i if seg[i] > 2.0 * (np.median(seg) + 1e-3) else default

    ys = slice(int(max(0, box.y)), int(min(H, box.y + box.h)))
    xs = slice(int(max(0, box.x)), int(min(W, box.x + box.w)))
    col, row = ex[ys, :].mean(axis=0), ey[:, xs].mean(axis=1)
    mx, my = 0.35 * box.w, 0.35 * box.h
    x0 = strongest(col, box.x - mx, box.x + 0.15 * box.w, box.x)
    x1 = strongest(col, box.x + 0.85 * box.w, box.x + box.w + mx, box.x + box.w)
    y0 = strongest(row, box.y - my, box.y + 0.15 * box.h, box.y)
    y1 = strongest(row, box.y + 0.85 * box.h, box.y + box.h + my, box.y + box.h)
    return Rect(x0, y0, max(8, x1 - x0), max(8, y1 - y0)).clamp(W, H)


def handcam_from_boxes(per_frame: list[list[Rect]], W: int, H: int, exclude: Rect | None = None,
                       min_share: float = 0.3) -> Rect | None:
    """Bereich, in dem über die Bilder hinweg immer wieder Hände auftauchen (= Handcam).

    Hände innerhalb der Facecam zählen nicht (Streamer an der Tastatur ist keine Handcam)."""
    centers = []
    for fi, boxes in enumerate(per_frame):
        boxes = [b for b in boxes if not (exclude and exclude.x <= b.cx <= exclude.x + exclude.w
                                          and exclude.y <= b.cy <= exclude.y + exclude.h)]
        if boxes:
            x0, y0 = min(b.x for b in boxes), min(b.y for b in boxes)
            x1, y1 = max(b.x + b.w for b in boxes), max(b.y + b.h for b in boxes)
            centers.append((fi, Rect(x0, y0, x1 - x0, y1 - y0)))
    if len(centers) < max(2, min_share * len(per_frame)):
        return None
    # größte Gruppe von Bildern, deren Hand-Bereiche nahe beieinander liegen
    radius = 0.2 * W
    best = []
    for _, ref in centers:
        group = [r for _, r in centers if abs(r.cx - ref.cx) < radius and abs(r.cy - ref.cy) < radius]
        if len(group) > len(best):
            best = group
    if len(best) < max(2, min_share * len(per_frame)):
        return None
    x0 = float(np.percentile([r.x for r in best], 10))
    y0 = float(np.percentile([r.y for r in best], 10))
    x1 = float(np.percentile([r.x + r.w for r in best], 90))
    y1 = float(np.percentile([r.y + r.h for r in best], 90))
    pad_x, pad_y = 0.25 * (x1 - x0), 0.25 * (y1 - y0)
    return Rect(x0 - pad_x, y0 - pad_y, (x1 - x0) + 2 * pad_x, (y1 - y0) + 2 * pad_y).clamp(W, H)


def detect_handcam(frames: list[np.ndarray], facecam: Rect | None = None,
                   detector: HandDetector | None = None) -> Rect | None:
    if not frames:
        return None
    det = detector or HandDetector()
    H, W = frames[0].shape[:2]
    box = handcam_from_boxes([det.detect(f) for f in frames], W, H, exclude=facecam)
    if box is None:
        return None
    if box.w * box.h > 0.55 * W * H:  # Hände übers ganze Bild: reiner Handcam-Stream
        return Rect(0, 0, W, H)
    ex, ey = _static_edges(frames)
    return _snap_box(ex, ey, box, W, H)


def clip_letterbox(path: Path, start: float, end: float, n: int = 6) -> list[float] | None:
    """Schwarze Balken innerhalb eines Clips (Zwischensequenzen haben sie oft nur zeitweise)."""
    frames = read_frames(path, [start + (end - start) * (i + 0.5) / n for i in range(n)], max_w=480)
    if not frames:
        return None
    H = frames[0][1].shape[0]
    y0, y1 = detect_letterbox([f for _, f in frames])
    return [round(y0 / H, 4), round(y1 / H, 4)]


def detect_facecam(path: Path, duration: float, n_frames: int = 24, cancel: threading.Event | None = None,
                   detector: FaceDetector | None = None, hands: bool = True) -> Facecam:
    times = [duration * (i + 0.5) / n_frames for i in range(n_frames)]
    frames = read_frames(path, times)
    if not frames:
        return Facecam("none", frames_total=0)
    fc = _detect(frames, cancel, detector)
    H, W = frames[0][1].shape[:2]
    y0, y1 = detect_letterbox([f for _, f in frames])
    fc.content = [round(y0 / H, 4), round(y1 / H, 4)]
    if hands:
        try:
            cam = Rect.from_norm(fc.cam, W, H) if fc.mode == "overlay" and fc.cam else None
            hc = detect_handcam([f for _, f in frames], facecam=cam)
            if hc:
                fc.handcam, fc.hand_source = hc.norm(W, H), "auto"
                log.info("Handcam erkannt: %s", hc)
        except Exception as e:  # Handerkennung ist Zusatz: Fehler dürfen die Facecam nicht kosten
            log.warning("Handcam-Erkennung übersprungen: %s", e)
    return fc


def _overlay_without_face(frames: list[np.ndarray]) -> Facecam:
    """Facecam ohne menschliches Gesicht (Tier-Maskottchen, VTuber-, PNGtuber-Avatar).

    Gesucht wird ein rechteckiges Fenster, dessen Inhalt über das ganze VOD ruhig bleibt, sich aber leicht
    bewegt (der Avatar), während das Spielbild ringsum ständig wechselt. Völlig starre Flächen (Logos,
    schwarze Balken) und schmale Streifen zählen nicht.
    """
    import cv2

    n = len(frames)
    H, W = frames[0].shape[:2]
    if n < 6:
        return Facecam("none", frames_total=n)
    s = 0.25  # verkleinert rechnen
    small = np.stack([cv2.cvtColor(cv2.resize(f, (int(W * s), int(H * s)), interpolation=cv2.INTER_AREA),
                                   cv2.COLOR_BGR2GRAY).astype(np.float32) for f in frames])
    S = cv2.GaussianBlur(small.std(axis=0), (0, 0), 2)
    hi = float(np.percentile(S, 75))
    if hi < 8:  # Spielbild selbst zu ruhig (Menü, Standbild): keine Aussage möglich
        return Facecam("none", frames_total=n)
    calm = (S < 0.4 * hi).astype(np.uint8)
    calm = cv2.morphologyEx(calm, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(calm, 8)
    sh, sw = calm.shape
    best, best_score = None, 0.0
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        frac = (w * h) / (sw * sh)
        if not 0.015 <= frac <= 0.3 or not 0.5 <= w / max(h, 1) <= 2.6:
            continue
        fill = area / float(w * h)
        if fill < 0.6:
            continue
        inside = S[y:y + h, x:x + w]
        activity = float(np.percentile(inside, 90))  # der Avatar bewegt sich irgendwo im Fenster
        if activity < 0.06 * hi:  # völlig starr: Logo, Rahmen, Balken
            continue
        edge = min(x, y, sw - (x + w), sh - (y + h)) / max(sw, sh)
        corner_bonus = 1.3 if edge < 0.06 else 1.0  # Facecams sitzen meist am Rand
        score = fill * min(activity / hi, 1.0) * corner_bonus * min(frac / 0.05, 1.0)
        if score > best_score:
            best, best_score = (x, y, w, h), score
    if not best:
        log.info("Keine Facecam gefunden (weder Gesicht noch ruhiges Fenster)")
        return Facecam("none", frames_total=n)
    x, y, w, h = best
    cam = Rect(x / s, y / s, w / s, h / s).clamp(W, H)
    log.info("Facecam ohne Gesicht erkannt (Avatar/Maskottchen): %s, Wert %.2f", cam, best_score)
    return Facecam("overlay", cam=cam.norm(W, H), face=None, confidence=round(min(0.8, 0.3 + best_score), 2),
                   frames_with_face=0, frames_total=n)


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
        return _overlay_without_face([f for _, f in frames])
    best = max(clusters, key=lambda c: (len(c["frames"]), -np.std([x.cx for x in c["rects"]])))
    share = len(best["frames"]) / len(frames)
    if share < 0.3:
        log.info("Kein stabiles Gesicht gefunden (bester Anteil %.0f %%), suche Fenster ohne Gesicht", share * 100)
        return _overlay_without_face([f for _, f in frames])
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
                 exclude: "Rect | list[Rect] | None" = None, n: int = 10) -> int:
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
    # Facecam/Handcam zählen negativ: Ausschnitte, die sie anschneiden, werden gemieden
    sx, sy = sw / src_w, sh / src_h
    for ex_r in ([exclude] if isinstance(exclude, Rect) else (exclude or [])):
        m[int(ex_r.y * sy):int((ex_r.y + ex_r.h) * sy) + 1,
          int(ex_r.x * sx):int((ex_r.x + ex_r.w) * sx) + 1] = -6.0
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
    ws: list[float] | None = None  # Fenstergröße, wenn mitgezoomt wird
    hs: list[float] | None = None


def track_window(path: Path, start: float, end: float, region: Rect, win_w: float, win_h: float,
                 detector: FaceDetector | None = None, step: float = 0.5, smooth: float = 0.25,
                 dead_zone: float = 0.04) -> Track:
    """Führt ein Fenster (win_w × win_h) innerhalb von region sanft dem Gesicht nach.

    Glättung: exponentiell (smooth) plus Totzone, damit kleine Kopfbewegungen das Bild nicht wackeln lassen.
    """
    det = detector or FaceDetector()
    times = [start + i * step for i in range(int((end - start) / step) + 1)]
    frames = read_frames(path, times, max_w=FULL_RES)  # region ist in Quellpixeln
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
    """FFmpeg-sendcmd-Datei: Fensterposition (und ggf. -größe) alle 0,1 s, linear zwischen den Messpunkten."""
    if not track.times:
        return ""
    lines = []
    t_end = track.times[-1]
    t = 0.0
    zoom = bool(track.ws)
    while t <= t_end + 1e-6:
        x = int(float(np.interp(t, track.times, track.xs))) // 2 * 2
        y = int(float(np.interp(t, track.times, track.ys))) // 2 * 2
        cmd = f"{t:.2f} crop@{filter_name} x {x}, crop@{filter_name} y {y}"
        if zoom:
            w = int(float(np.interp(t, track.times, track.ws))) // 2 * 2
            h = int(float(np.interp(t, track.times, track.hs))) // 2 * 2
            cmd += f", crop@{filter_name} w {w}, crop@{filter_name} h {h}"
        lines.append(cmd + ";")
        t += step
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- Markiertes Objekt (z. B. Bär)


class SubjectFinder:
    """Findet ein einmal markiertes Objekt (Maskottchen, Avatar, Gesicht) wieder – auch wenn es sich
    bewegt, größer/kleiner wird oder leicht dreht.

    1. Merkmalsabgleich (SIFT + RANSAC): robust gegen Größe und Drehung, braucht etwas Struktur im Bild.
    2. Rückfall für glatte Comic-Flächen: Bildvergleich in mehreren Größen (Template-Matching).
    """

    def __init__(self, template_bgr: np.ndarray):
        import cv2

        self.cv2 = cv2
        self.tpl = template_bgr
        self.tpl_gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
        self.th, self.tw = self.tpl_gray.shape
        self.sift = cv2.SIFT_create(nfeatures=600)
        self.kp, self.des = self.sift.detectAndCompute(self.tpl_gray, None)
        self.matcher = cv2.BFMatcher(cv2.NORM_L2)

    @classmethod
    def from_file(cls, path: str | Path) -> "SubjectFinder | None":
        import cv2

        img = cv2.imread(str(path))
        return cls(img) if img is not None and img.size else None

    def _sift(self, gray: np.ndarray) -> tuple[Rect | None, float]:
        cv2 = self.cv2
        if self.des is None or len(self.kp) < 8:
            return None, 0.0
        kp, des = self.sift.detectAndCompute(gray, None)
        if des is None or len(kp) < 8:
            return None, 0.0
        good = [m for m, n in (p for p in self.matcher.knnMatch(self.des, des, k=2) if len(p) == 2)
                if m.distance < 0.75 * n.distance]
        if len(good) < 8:
            return None, 0.0
        src = np.float32([self.kp[m.queryIdx].pt for m in good])
        dst = np.float32([kp[m.trainIdx].pt for m in good])
        M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0)
        if M is None or inl is None or int(inl.sum()) < 6:
            return None, 0.0
        scale = float(np.hypot(M[0, 0], M[1, 0]))
        if not 0.25 <= scale <= 4.0:
            return None, 0.0
        corners = np.float32([[0, 0], [self.tw, 0], [self.tw, self.th], [0, self.th]]).reshape(-1, 1, 2)
        box = cv2.transform(corners, M).reshape(-1, 2)
        x0, y0 = box.min(axis=0)
        x1, y1 = box.max(axis=0)
        return Rect(float(x0), float(y0), float(x1 - x0), float(y1 - y0)), float(inl.sum()) / len(good)

    def _template(self, gray: np.ndarray, size_hint: float | None) -> tuple[Rect | None, float]:
        cv2 = self.cv2
        H, W = gray.shape
        base = size_hint or 1.0
        best = (None, 0.0)
        for f in (0.7, 0.8, 0.9, 1.0, 1.12, 1.25, 1.4, 1.6):
            s = base * f
            tw, th = int(self.tw * s), int(self.th * s)
            if tw < 12 or th < 12 or tw >= W or th >= H:
                continue
            tpl = cv2.resize(self.tpl_gray, (tw, th), interpolation=cv2.INTER_AREA)
            res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(res)
            if mx > best[1]:
                best = (Rect(float(loc[0]), float(loc[1]), float(tw), float(th)), float(mx))
        return best if best[1] >= 0.55 else (None, best[1])

    def find(self, frame_bgr: np.ndarray, search: Rect | None = None,
             prev: Rect | None = None) -> tuple[Rect | None, float]:
        cv2 = self.cv2
        H, W = frame_bgr.shape[:2]
        if search is None:
            search = Rect(0, 0, W, H)
        # etwas Rand um den Suchbereich, damit ein Objekt am Rand nicht verloren geht
        pad = 0.1 * max(search.w, search.h)
        sr = Rect(search.x - pad, search.y - pad, search.w + 2 * pad, search.h + 2 * pad).clamp(W, H)
        sub = frame_bgr[int(sr.y):int(sr.y + sr.h), int(sr.x):int(sr.x + sr.w)]
        if sub.size == 0:
            return None, 0.0
        gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
        r, score = self._sift(gray)
        if r is None:
            hint = (prev.w / self.tw) if prev is not None else None
            r, score = self._template(gray, hint)
        if r is None:
            return None, score
        return Rect(r.x + sr.x, r.y + sr.y, r.w, r.h), score


def read_frames_seq(path: Path, start: float, end: float, fps: float = 5.0) -> list[tuple[float, np.ndarray]]:
    """Bilder in festem Takt der Reihe nach lesen (schneller als für jedes Bild neu zu springen)."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return []
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    every = max(1, int(round(src_fps / fps)))
    out, i, t = [], 0, start
    try:
        while t <= end:
            ok = cap.grab()
            if not ok:
                break
            if i % every == 0:
                ok, frame = cap.retrieve()
                if ok:
                    out.append((t, frame))
            i += 1
            t = start + i / src_fps
    finally:
        cap.release()
    return out


def track_subject(path: Path, start: float, end: float, finder: SubjectFinder, region: Rect, aspect: float,
                  margin: float = 2.0, min_frac: float = 0.35, fps: float = 5.0, smooth: float = 0.3,
                  dead_zone: float = 0.03) -> Track:
    """Fenster mit Seitenverhältnis aspect, das dem markierten Objekt folgt und mitzoomt.

    Fenstergröße = Objektgröße × margin (das Objekt füllt etwa die Hälfte), begrenzt auf den Bereich und
    mindestens min_frac davon. Position und Größe werden geglättet; kurz verlorene Objekte werden gehalten.
    """
    frames = read_frames_seq(path, start, end, fps)
    max_w, max_h = layouts_window(region, aspect)
    min_w = max_w * min_frac
    tr = Track(ws=[], hs=[])
    cx, cy, cw = region.cx, region.cy, max_w
    target = None
    prev = None
    lost = 0
    for t, f in frames:
        r, score = finder.find(f, region, prev)
        if r is not None:
            prev, lost = r, 0
            want_w = min(max(max(r.w, r.h * aspect) * margin, min_w), max_w)
            new = (r.cx, r.cy, want_w)
            if target is None:
                cx, cy, cw = new
                target = new
            elif (abs(new[0] - target[0]) > dead_zone * region.w or abs(new[1] - target[1]) > dead_zone * region.h
                  or abs(new[2] - target[2]) > 0.08 * target[2]):
                target = new
        else:
            lost += 1
            if lost > 2 * fps:  # länger als 2 s weg: langsam zurück zum ganzen Bereich
                target = (region.cx, region.cy, max_w)
        if target:
            cx += (target[0] - cx) * smooth
            cy += (target[1] - cy) * smooth
            cw += (target[2] - cw) * smooth
        w = cw
        h = w / aspect
        x = min(max(region.x, cx - w / 2), region.x + region.w - w)
        y = min(max(region.y, cy - h / 2), region.y + region.h - h)
        tr.times.append(t - start)
        tr.xs.append(x)
        tr.ys.append(y)
        tr.ws.append(w)
        tr.hs.append(h)
    return tr


def layouts_window(region: Rect, aspect: float) -> tuple[float, float]:
    """Größtes Fenster mit Seitenverhältnis aspect in region (wie layouts.cam_window_size, ohne Rundung)."""
    if region.w / region.h > aspect:
        return region.h * aspect, region.h
    return region.w, region.w / aspect


def locate_subject(path: Path, duration: float, finder: SubjectFinder, n: int = 12) -> Rect | None:
    """Sucht das markierte Objekt im ganzen Bild (z. B. wenn das Cam-Fenster verschoben wurde).
    Liefert die mittlere Position, wenn es in mindestens 40 % der Bilder gefunden wurde."""
    # Originalgröße: Ergebnis in Quellpixeln und im Maßstab der gespeicherten Vorlage
    frames = read_frames(path, [duration * (i + 0.5) / n for i in range(n)], max_w=FULL_RES)
    hits = [r for _, f in frames for r, s in [finder.find(f)] if r is not None]
    if len(hits) < max(2, 0.4 * len(frames)):
        return None
    return Rect(float(np.median([r.x for r in hits])), float(np.median([r.y for r in hits])),
                float(np.median([r.w for r in hits])), float(np.median([r.h for r in hits])))
