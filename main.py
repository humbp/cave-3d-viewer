import datetime
import logging
import math
import os
from pathlib import Path
import re
import struct
import zlib
import numpy as np

from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Line, Rectangle, Triangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

try:
    import ezdxf
except ImportError:
    ezdxf = None

try:
    from svgpathtools import svg2paths
except ImportError:
    svg2paths = None


# ==============================================================================
# 1. CONFIGURATION LOGS & DEBUG
# ==============================================================================

APP_VERSION = os.environ.get("CAVE_APP_VERSION", "0.1.0-dev")


def setup_logger() -> tuple[logging.Logger, Path]:
    log_dir = Path("/storage/emulated/0/Documents/cave 3D viewer")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "cave_3d_debug.log"
    except OSError:
        log_path = Path.cwd() / "cave_3d_debug.log"

    logger = logging.getLogger("Cave3DTopo")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    sh = logging.StreamHandler()
    sh.setLevel(logging.DEBUG)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    try:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass

    logger.info(f"Démarrage Cave 3D Topo — version={APP_VERSION}, log={log_path}")
    return logger, log_path


logger, LOG_FILE_PATH = setup_logger()


# ==============================================================================
# 2. PARSEURS 2D (DXF / SVG / PDF VECTORIEL)
# ==============================================================================

def _sanitize_polylines(lines: list[np.ndarray], source_name: str) -> list[np.ndarray]:
    clean: list[np.ndarray] = []
    for idx, pts in enumerate(lines):
        arr = np.asarray(pts, dtype=float)
        if arr.ndim != 2 or arr.shape[1] != 2 or len(arr) < 2:
            logger.debug(f"[{source_name}] Polyligne #{idx} ignorée (forme={arr.shape})")
            continue
        if not np.all(np.isfinite(arr)):
            logger.warning(f"[{source_name}] Polyligne #{idx} contient NaN/Inf, ignorée")
            continue
        diffs = np.diff(arr, axis=0)
        length = float(np.sum(np.hypot(diffs[:, 0], diffs[:, 1])))
        if length <= 1e-6:
            logger.debug(f"[{source_name}] Polyligne #{idx} de longueur nulle ignorée")
            continue
        clean.append(arr)
    logger.info(f"[{source_name}] {len(clean)} polylignes valides retenues (sur {len(lines)} brutes)")
    return clean


def load_dxf_polylines(filepath: Path, layer: str | None = None) -> list[np.ndarray]:
    if ezdxf is None:
        raise RuntimeError("ezdxf non disponible dans cet environnement")
    doc = ezdxf.readfile(str(filepath))
    msp = doc.modelspace()
    polylines: list[np.ndarray] = []
    for entity in msp:
        if layer and entity.dxf.layer != layer:
            continue
        etype = entity.dxftype()
        if etype == "LINE":
            polylines.append(np.array([
                [entity.dxf.start.x, entity.dxf.start.y],
                [entity.dxf.end.x, entity.dxf.end.y],
            ], dtype=float))
        elif etype == "LWPOLYLINE":
            pts = np.array([[p[0], p[1]] for p in entity.get_points()], dtype=float)
            polylines.append(pts)
        elif etype == "POLYLINE":
            pts = np.array([[v.dxf.location.x, v.dxf.location.y] for v in entity.vertices], dtype=float)
            polylines.append(pts)
    return _sanitize_polylines(polylines, f"DXF:{filepath.name}")


def load_svg_polylines(filepath: Path, samples_per_path: int = 80) -> list[np.ndarray]:
    if svg2paths is None:
        raise RuntimeError("svgpathtools non disponible dans cet environnement")
    paths, _ = svg2paths(str(filepath))
    polylines: list[np.ndarray] = []
    for path in paths:
        if len(path) == 0:
            continue
        ts = np.linspace(0.0, 1.0, samples_per_path)
        pts = np.array([[path.point(t).real, -path.point(t).imag] for t in ts], dtype=float)
        polylines.append(pts)
    return _sanitize_polylines(polylines, f"SVG:{filepath.name}")


def _cubic_bezier(p0, c1, c2, p1, n=24) -> np.ndarray:
    ts = np.linspace(0.0, 1.0, n)[:, None]
    return (
        (1.0 - ts) ** 3 * p0
        + 3.0 * (1.0 - ts) ** 2 * ts * c1
        + 3.0 * (1.0 - ts) * ts ** 2 * c2
        + ts ** 3 * p1
    )


def load_pdf_polylines(filepath: Path) -> list[np.ndarray]:
    """Extracteur vectoriel PDF léger (décompresse streams FlateDecode et lit m/l/c/re)."""
    raw = filepath.read_bytes()
    streams: list[bytes] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, flags=re.DOTALL):
        data = match.group(1)
        try:
            streams.append(zlib.decompress(data))
        except zlib.error:
            streams.append(data)

    logger.debug(f"[PDF:{filepath.name}] {len(streams)} flux extraits")
    polylines: list[np.ndarray] = []
    token_re = re.compile(rb"([+-]?(?:\d+\.?\d*|\.\d+)|m|l|c|re|h|S|s|B|b|f|F)")

    for s_idx, content in enumerate(streams):
        nums: list[float] = []
        current: list[list[float]] = []

        def flush_current():
            nonlocal current
            if len(current) >= 2:
                arr = np.array(current, dtype=float)
                arr[:, 1] = -arr[:, 1]
                polylines.append(arr)
            current = []

        for tok_m in token_re.finditer(content):
            tok = tok_m.group(1)
            if tok in (b"m", b"l", b"c", b"re", b"h", b"S", b"s", b"B", b"b", b"f", b"F"):
                op = tok.decode("ascii")
                try:
                    if op == "m" and len(nums) >= 2:
                        flush_current()
                        current.append([nums[-2], nums[-1]])
                    elif op == "l" and len(nums) >= 2:
                        current.append([nums[-2], nums[-1]])
                    elif op == "c" and len(nums) >= 6:
                        p0 = np.array(current[-1] if current else [nums[-6], nums[-5]], dtype=float)
                        c1 = np.array([nums[-6], nums[-5]], dtype=float)
                        c2 = np.array([nums[-4], nums[-3]], dtype=float)
                        p1 = np.array([nums[-2], nums[-1]], dtype=float)
                        seg = _cubic_bezier(p0, c1, c2, p1, n=20)
                        if not current:
                            current.extend(seg.tolist())
                        else:
                            current.extend(seg[1:].tolist())
                    elif op == "re" and len(nums) >= 4:
                        flush_current()
                        x, y, w, h = nums[-4], nums[-3], nums[-2], nums[-1]
                        current = [
                            [x, y],
                            [x + w, y],
                            [x + w, y + h],
                            [x, y + h],
                            [x, y],
                        ]
                        flush_current()
                    elif op in ("h", "S", "s", "B", "b", "f", "F"):
                        if op == "h" and len(current) >= 2:
                            current.append(current[0][:])
                        flush_current()
                except Exception as exc:
                    logger.debug(f"[PDF stream {s_idx} op {op}] ignoré: {exc}")
                nums.clear()
            else:
                try:
                    nums.append(float(tok))
                except ValueError:
                    pass
        flush_current()

    return _sanitize_polylines(polylines, f"PDF:{filepath.name}")


def load_topo_polylines(filepath: str | Path, scale: float = 1.0) -> list[np.ndarray]:
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {path}")
    ext = path.suffix.lower()
    logger.info(f"Chargement topo : {path} (ext={ext}, scale={scale})")

    if ext == ".dxf":
        lines = load_dxf_polylines(path)
    elif ext == ".svg":
        lines = load_svg_polylines(path)
    elif ext == ".pdf":
        lines = load_pdf_polylines(path)
    else:
        raise ValueError(f"Format non supporté : {ext}. Formats acceptés : .pdf, .dxf, .svg")

    if not lines:
        raise ValueError("Aucune polyligne exploitable trouvée dans le fichier.")

    return [pts * scale for pts in lines]


# ==============================================================================
# 3. RECONSTRUCTION 3D & EXPORT STL
# ==============================================================================

def recenter_lines(polylines: list[np.ndarray], indices: list[int]) -> list[np.ndarray]:
    if not indices:
        raise ValueError("Aucun index sélectionné.")
    max_idx = len(polylines) - 1
    for i in indices:
        if i < 0 or i > max_idx:
            raise IndexError(f"Index {i} hors limites (0..{max_idx})")

    selected = [polylines[i].copy() for i in indices]
    all_pts = np.vstack(selected)
    min_x, min_y = float(np.min(all_pts[:, 0])), float(np.min(all_pts[:, 1]))
    logger.debug(f"Recalage de {len(selected)} polylignes sur ({min_x:.2f}, {min_y:.2f})")
    return [pts - np.array([min_x, min_y]) for pts in selected]


def compute_curvilinear_abscissa(xy: np.ndarray) -> np.ndarray:
    diffs = np.diff(xy, axis=0)
    lengths = np.hypot(diffs[:, 0], diffs[:, 1])
    return np.concatenate([[0.0], np.cumsum(lengths)])


def build_cave_mesh(
    plan_lines: list[np.ndarray],
    profile_lines: list[np.ndarray],
    width: float = 2.5,
    height: float = 3.0,
    n_radial: int = 18,
    use_curvilinear: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    axis_xy = plan_lines[0]
    s_axis = compute_curvilinear_abscissa(axis_xy)

    prof = profile_lines[0]
    order = np.argsort(prof[:, 0])
    px = prof[order, 0]
    pz = prof[order, 1]

    if use_curvilinear:
        span = max(float(px[-1] - px[0]), 1e-9)
        s_prof = (px - px[0]) / span * max(float(s_axis[-1]), 1e-9)
        z_axis = np.interp(s_axis, s_prof, pz)
    else:
        z_axis = np.interp(axis_xy[:, 0], px, pz)

    center_3d = np.column_stack([axis_xy[:, 0], axis_xy[:, 1], z_axis])
    n_pts = len(center_3d)
    logger.info(f"Construction 3D : {n_pts} stations, largeur={width}m, hauteur={height}m, n_radial={n_radial}")

    vertices_list: list[np.ndarray] = []
    rx = width * 0.5
    rz = height * 0.5
    thetas = np.linspace(0.0, 2.0 * math.pi, n_radial, endpoint=False)
    cos_t = np.cos(thetas)[:, None]
    sin_t = np.sin(thetas)[:, None]

    for i in range(n_pts):
        p = center_3d[i]
        if i == 0:
            tangent = center_3d[1] - center_3d[0]
        elif i == n_pts - 1:
            tangent = center_3d[-1] - center_3d[-2]
        else:
            tangent = center_3d[i + 1] - center_3d[i - 1]

        t_norm = float(np.linalg.norm(tangent))
        tangent = tangent / t_norm if t_norm > 1e-9 else np.array([1.0, 0.0, 0.0])

        up_ref = np.array([0.0, 0.0, 1.0])
        if abs(float(np.dot(tangent, up_ref))) > 0.95:
            up_ref = np.array([0.0, 1.0, 0.0])

        right = np.cross(tangent, up_ref)
        r_norm = float(np.linalg.norm(right))
        right = right / r_norm if r_norm > 1e-9 else np.array([0.0, 1.0, 0.0])

        up = np.cross(right, tangent)
        u_norm = float(np.linalg.norm(up))
        up = up / u_norm if u_norm > 1e-9 else np.array([0.0, 0.0, 1.0])

        ring = p[None, :] + (cos_t * rx) * right[None, :] + (sin_t * rz) * up[None, :]
        vertices_list.append(ring)

    vertices = np.vstack(vertices_list).astype(np.float32)

    faces_list: list[list[int]] = []
    for i in range(n_pts - 1):
        a0 = i * n_radial
        b0 = (i + 1) * n_radial
        for k in range(n_radial):
            kn = (k + 1) % n_radial
            faces_list.append([a0 + k, b0 + k, b0 + kn])
            faces_list.append([a0 + k, b0 + kn, a0 + kn])

    faces = np.asarray(faces_list, dtype=np.int32)
    logger.info(f"Maillage généré : {len(vertices)} sommets, {len(faces)} triangles")
    return vertices, faces


def export_binary_stl(filepath: Path, vertices: np.ndarray, faces: np.ndarray) -> None:
    tris = vertices[faces]
    v1 = tris[:, 1] - tris[:, 0]
    v2 = tris[:, 2] - tris[:, 0]
    normals = np.cross(v1, v2)
    norms = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.where(norms > 1e-9, normals / np.maximum(norms, 1e-9), np.array([0.0, 0.0, 1.0]))

    header = f"Cave 3D Topo {APP_VERSION} {datetime.datetime.now().isoformat()}".encode("ascii", errors="ignore")[:80].ljust(80, b" ")
    with open(filepath, "wb") as f:
        f.write(header)
        f.write(struct.pack("<I", len(faces)))
        for idx in range(len(faces)):
            n = normals[idx]
            p0, p1, p2 = tris[idx, 0], tris[idx, 1], tris[idx, 2]
            f.write(struct.pack(
                "<12fH",
                float(n[0]), float(n[1]), float(n[2]),
                float(p0[0]), float(p0[1]), float(p0[2]),
                float(p1[0]), float(p1[1]), float(p1[2]),
                float(p2[0]), float(p2[1]), float(p2[2]),
                0,
            ))
    logger.info(f"Export STL réussi : {filepath} ({filepath.stat().st_size} octets)")


# ==============================================================================
# 4. WIDGET 2D INTERACTIF (SÉLECTION PAR TAP / CLIC)
# ==============================================================================

class TopoSelectCanvas(Widget):
    def __init__(self, on_selection_change=None, **kwargs):
        super().__init__(**kwargs)
        self.polylines: list[np.ndarray] = []
        self.selected: list[int] = []
        self.on_selection_change = on_selection_change
        self._bounds = (0.0, 1.0, 0.0, 1.0)
        self._transform = (1.0, 0.0, 0.0)
        self.bind(pos=self._redraw, size=self._redraw)

    def set_polylines(self, polylines: list[np.ndarray], selected: list[int] | None = None):
        self.polylines = polylines
        self.selected = list(selected) if selected else []
        if not polylines:
            self._bounds = (0.0, 1.0, 0.0, 1.0)
            self._redraw()
            return
        all_pts = np.vstack(polylines)
        xmin, ymin = float(all_pts[:, 0].min()), float(all_pts[:, 1].min())
        xmax, ymax = float(all_pts[:, 0].max()), float(all_pts[:, 1].max())
        self._bounds = (xmin, max(xmax, xmin + 1e-6), ymin, max(ymax, ymin + 1e-6))
        self._redraw()

    def _to_screen(self, pts: np.ndarray) -> np.ndarray:
        xmin, xmax, ymin, ymax = self._bounds
        pad = dp(16)
        avail_w = max(self.width - 2 * pad, 1.0)
        avail_h = max(self.height - 2 * pad, 1.0)
        scale = min(avail_w / (xmax - xmin), avail_h / (ymax - ymin))
        ox = self.x + pad + (avail_w - (xmax - xmin) * scale) * 0.5 - xmin * scale
        oy = self.y + pad + (avail_h - (ymax - ymin) * scale) * 0.5 - ymin * scale
        self._transform = (scale, ox, oy)
        sx = pts[:, 0] * scale + ox
        sy = pts[:, 1] * scale + oy
        return np.column_stack([sx, sy]).reshape(-1)

    def _redraw(self, *args):
        self.canvas.clear()
        with self.canvas:
            Color(0.10, 0.11, 0.14, 1)
            Rectangle(pos=self.pos, size=self.size)

            if not self.polylines:
                return

            sel_set = set(self.selected)
            for idx, pts in enumerate(self.polylines):
                coords = self._to_screen(pts)
                if idx in sel_set:
                    Color(0.95, 0.25, 0.25, 1)
                    Line(points=coords.tolist(), width=dp(2.6))
                else:
                    Color(0.45, 0.72, 0.98, 0.75)
                    Line(points=coords.tolist(), width=dp(1.2))

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos) or not self.polylines:
            return super().on_touch_down(touch)

        if getattr(touch, "button", None) == "right":
            if self.selected:
                removed = self.selected.pop()
                logger.debug(f"Annulation clic droit index #{removed}")
                self._redraw()
                if self.on_selection_change:
                    self.on_selection_change(list(self.selected))
            return True

        scale, ox, oy = self._transform
        tx, ty = touch.x, touch.y
        best_idx = None
        best_dist = dp(14)

        for idx, pts in enumerate(self.polylines):
            spts = np.column_stack([pts[:, 0] * scale + ox, pts[:, 1] * scale + oy])
            if len(spts) < 2:
                continue
            a = spts[:-1]
            b = spts[1:]
            ab = b - a
            ab_len2 = np.sum(ab ** 2, axis=1)
            valid = ab_len2 > 1e-9
            if not np.any(valid):
                continue
            a, b, ab, ab_len2 = a[valid], b[valid], ab[valid], ab_len2[valid]
            ap = np.array([tx, ty]) - a
            t = np.clip(np.sum(ap * ab, axis=1) / ab_len2, 0.0, 1.0)[:, None]
            proj = a + t * ab
            dists = np.hypot(proj[:, 0] - tx, proj[:, 1] - ty)
            d_min = float(dists.min())
            if d_min < best_dist:
                best_dist = d_min
                best_idx = idx

        if best_idx is not None:
            if best_idx in self.selected:
                self.selected.remove(best_idx)
                logger.info(f"Désélection index #{best_idx}")
            else:
                self.selected.append(best_idx)
                logger.info(f"Sélection index #{best_idx}")
            self._redraw()
            if self.on_selection_change:
                self.on_selection_change(list(self.selected))
            return True

        return super().on_touch_down(touch)


# ==============================================================================
# 5. WIDGET 3D INTERACTIF (ROTATION / ZOOM TACTILE)
# ==============================================================================

class Cave3DViewport(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rot_x = -55.0
        self.rot_z = 25.0
        self.zoom = 1.0
        self.vertices: np.ndarray | None = None
        self.faces: np.ndarray | None = None
        self.bind(pos=self._redraw, size=self._redraw)

    def set_mesh(self, vertices: np.ndarray, faces: np.ndarray):
        center = vertices.mean(axis=0)
        centered = vertices - center
        scale = max(float(np.max(np.abs(centered))), 1e-6)
        self.vertices = centered / scale
        self.faces = faces
        self._redraw()

    def _project(self) -> tuple[np.ndarray, np.ndarray]:
        assert self.vertices is not None and self.faces is not None
        rx = math.radians(self.rot_x)
        rz = math.radians(self.rot_z)

        mx = np.array([
            [1.0, 0.0, 0.0],
            [0.0, math.cos(rx), -math.sin(rx)],
            [0.0, math.sin(rx), math.cos(rx)],
        ])
        mz = np.array([
            [math.cos(rz), -math.sin(rz), 0.0],
            [math.sin(rz), math.cos(rz), 0.0],
            [0.0, 0.0, 1.0],
        ])

        rot_pts = self.vertices @ (mz @ mx).T
        tris = rot_pts[self.faces]
        depths = tris[:, :, 2].mean(axis=1)
        order = np.argsort(depths)
        tris_sorted = tris[order]
        depths_sorted = depths[order]

        pad = dp(24)
        radius = min(max(self.width - 2 * import datetime
import logging
import math
import os
from pathlib import Path
import re
import struct
import zlib
import numpy as np

from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Line, Rectangle, Triangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

try:
    import ezdxf
except ImportError:
    ezdxf = None

try:
    from svgpathtools import svg2paths
except ImportError:
    svg2paths = None


# ==============================================================================
# 1. CONFIGURATION LOGS & DEBUG
# ==============================================================================

APP_VERSION = os.environ.get("CAVE_APP_VERSION", "0.1.0-dev")


def setup_logger() -> tuple[logging.Logger, Path]:
    log_dir = Path("/storage/emulated/0/Documents/cave 3D viewer")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "cave_3d_debug.log"
    except OSError:
        log_path = Path.cwd() / "cave_3d_debug.log"

    logger = logging.getLogger("Cave3DTopo")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    sh = logging.StreamHandler()
    sh.setLevel(logging.DEBUG)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    try:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass

    logger.info(f"Démarrage Cave 3D Topo — version={APP_VERSION}, log={log_path}")
    return logger, log_path


logger, LOG_FILE_PATH = setup_logger()


# ==============================================================================
# 2. PARSEURS 2D (DXF / SVG / PDF VECTORIEL)
# ==============================================================================

def _sanitize_polylines(lines: list[np.ndarray], source_name: str) -> list[np.ndarray]:
    clean: list[np.ndarray] = []
    for idx, pts in enumerate(lines):
        arr = np.asarray(pts, dtype=float)
        if arr.ndim != 2 or arr.shape[1] != 2 or len(arr) < 2:
            logger.debug(f"[{source_name}] Polyligne #{idx} ignorée (forme={arr.shape})")
            continue
        if not np.all(np.isfinite(arr)):
            logger.warning(f"[{source_name}] Polyligne #{idx} contient NaN/Inf, ignorée")
            continue
        diffs = np.diff(arr, axis=0)
        length = float(np.sum(np.hypot(diffs[:, 0], diffs[:, 1])))
        if length <= 1e-6:
            logger.debug(f"[{source_name}] Polyligne #{idx} de longueur nulle ignorée")
            continue
        clean.append(arr)
    logger.info(f"[{source_name}] {len(clean)} polylignes valides retenues (sur {len(lines)} brutes)")
    return clean


def load_dxf_polylines(filepath: Path, layer: str | None = None) -> list[np.ndarray]:
    if ezdxf is None:
        raise RuntimeError("ezdxf non disponible dans cet environnement")
    doc = ezdxf.readfile(str(filepath))
    msp = doc.modelspace()
    polylines: list[np.ndarray] = []
    for entity in msp:
        if layer and entity.dxf.layer != layer:
            continue
        etype = entity.dxftype()
        if etype == "LINE":
            polylines.append(np.array([
                [entity.dxf.start.x, entity.dxf.start.y],
                [entity.dxf.end.x, entity.dxf.end.y],
            ], dtype=float))
        elif etype == "LWPOLYLINE":
            pts = np.array([[p[0], p[1]] for p in entity.get_points()], dtype=float)
            polylines.append(pts)
        elif etype == "POLYLINE":
            pts = np.array([[v.dxf.location.x, v.dxf.location.y] for v in entity.vertices], dtype=float)
            polylines.append(pts)
    return _sanitize_polylines(polylines, f"DXF:{filepath.name}")


def load_svg_polylines(filepath: Path, samples_per_path: int = 80) -> list[np.ndarray]:
    if svg2paths is None:
        raise RuntimeError("svgpathtools non disponible dans cet environnement")
    paths, _ = svg2paths(str(filepath))
    polylines: list[np.ndarray] = []
    for path in paths:
        if len(path) == 0:
            continue
        ts = np.linspace(0.0, 1.0, samples_per_path)
        pts = np.array([[path.point(t).real, -path.point(t).imag] for t in ts], dtype=float)
        polylines.append(pts)
    return _sanitize_polylines(polylines, f"SVG:{filepath.name}")


def _cubic_bezier(p0, c1, c2, p1, n=24) -> np.ndarray:
    ts = np.linspace(0.0, 1.0, n)[:, None]
    return (
        (1.0 - ts) ** 3 * p0
        + 3.0 * (1.0 - ts) ** 2 * ts * c1
        + 3.0 * (1.0 - ts) * ts ** 2 * c2
        + ts ** 3 * p1
    )


def load_pdf_polylines(filepath: Path) -> list[np.ndarray]:
    """Extracteur vectoriel PDF léger (décompresse streams FlateDecode et lit m/l/c/re)."""
    raw = filepath.read_bytes()
    streams: list[bytes] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, flags=re.DOTALL):
        data = match.group(1)
        try:
            streams.append(zlib.decompress(data))
        except zlib.error:
            streams.append(data)

    logger.debug(f"[PDF:{filepath.name}] {len(streams)} flux extraits")
    polylines: list[np.ndarray] = []
    token_re = re.compile(rb"([+-]?(?:\d+\.?\d*|\.\d+)|m|l|c|re|h|S|s|B|b|f|F)")

    for s_idx, content in enumerate(streams):
        nums: list[float] = []
        current: list[list[float]] = []

        def flush_current():
            nonlocal current
            if len(current) >= 2:
                arr = np.array(current, dtype=float)
                arr[:, 1] = -arr[:, 1]
                polylines.append(arr)
            current = []

        for tok_m in token_re.finditer(content):
            tok = tok_m.group(1)
            if tok in (b"m", b"l", b"c", b"re", b"h", b"S", b"s", b"B", b"b", b"f", b"F"):
                op = tok.decode("ascii")
                try:
                    if op == "m" and len(nums) >= 2:
                        flush_current()
                        current.append([nums[-2], nums[-1]])
                    elif op == "l" and len(nums) >= 2:
                        current.append([nums[-2], nums[-1]])
                    elif op == "c" and len(nums) >= 6:
                        p0 = np.array(current[-1] if current else [nums[-6], nums[-5]], dtype=float)
                        c1 = np.array([nums[-6], nums[-5]], dtype=float)
                        c2 = np.array([nums[-4], nums[-3]], dtype=float)
                        p1 = np.array([nums[-2], nums[-1]], dtype=float)
                        seg = _cubic_bezier(p0, c1, c2, p1, n=20)
                        if not current:
                            current.extend(seg.tolist())
                        else:
                            current.extend(seg[1:].tolist())
                    elif op == "re" and len(nums) >= 4:
                        flush_current()
                        x, y, w, h = nums[-4], nums[-3], nums[-2], nums[-1]
                        current = [
                            [x, y],
                            [x + w, y],
                            [x + w, y + h],
                            [x, y + h],
                            [x, y],
                        ]
                        flush_current()
                    elif op in ("h", "S", "s", "B", "b", "f", "F"):
                        if op == "h" and len(current) >= 2:
                            current.append(current[0][:])
                        flush_current()
                except Exception as exc:
                    logger.debug(f"[PDF stream {s_idx} op {op}] ignoré: {exc}")
                nums.clear()
            else:
                try:
                    nums.append(float(tok))
                except ValueError:
                    pass
        flush_current()

    return _sanitize_polylines(polylines, f"PDF:{filepath.name}")


def load_topo_polylines(filepath: str | Path, scale: float = 1.0) -> list[np.ndarray]:
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {path}")
    ext = path.suffix.lower()
    logger.info(f"Chargement topo : {path} (ext={ext}, scale={scale})")

    if ext == ".dxf":
        lines = load_dxf_polylines(path)
    elif ext == ".svg":
        lines = load_svg_polylines(path)
    elif ext == ".pdf":
        lines = load_pdf_polylines(path)
    else:
        raise ValueError(f"Format non supporté : {ext}. Formats acceptés : .pdf, .dxf, .svg")

    if not lines:
        raise ValueError("Aucune polyligne exploitable trouvée dans le fichier.")

    return [pts * scale for pts in lines]


# ==============================================================================
# 3. RECONSTRUCTION 3D & EXPORT STL
# ==============================================================================

def recenter_lines(polylines: list[np.ndarray], indices: list[int]) -> list[np.ndarray]:
    if not indices:
        raise ValueError("Aucun index sélectionné.")
    max_idx = len(polylines) - 1
    for i in indices:
        if i < 0 or i > max_idx:
            raise IndexError(f"Index {i} hors limites (0..{max_idx})")

    selected = [polylines[i].copy() for i in indices]
    all_pts = np.vstack(selected)
    min_x, min_y = float(np.min(all_pts[:, 0])), float(np.min(all_pts[:, 1]))
    logger.debug(f"Recalage de {len(selected)} polylignes sur ({min_x:.2f}, {min_y:.2f})")
    return [pts - np.array([min_x, min_y]) for pts in selected]


def compute_curvilinear_abscissa(xy: np.ndarray) -> np.ndarray:
    diffs = np.diff(xy, axis=0)
    lengths = np.hypot(diffs[:, 0], diffs[:, 1])
    return np.concatenate([[0.0], np.cumsum(lengths)])


def build_cave_mesh(
    plan_lines: list[np.ndarray],
    profile_lines: list[np.ndarray],
    width: float = 2.5,
    height: float = 3.0,
    n_radial: int = 18,
    use_curvilinear: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    axis_xy = plan_lines[0]
    s_axis = compute_curvilinear_abscissa(axis_xy)

    prof = profile_lines[0]
    order = np.argsort(prof[:, 0])
    px = prof[order, 0]
    pz = prof[order, 1]

    if use_curvilinear:
        span = max(float(px[-1] - px[0]), 1e-9)
        s_prof = (px - px[0]) / span * max(float(s_axis[-1]), 1e-9)
        z_axis = np.interp(s_axis, s_prof, pz)
    else:
        z_axis = np.interp(axis_xy[:, 0], px, pz)

    center_3d = np.column_stack([axis_xy[:, 0], axis_xy[:, 1], z_axis])
    n_pts = len(center_3d)
    logger.info(f"Construction 3D : {n_pts} stations, largeur={width}m, hauteur={height}m, n_radial={n_radial}")

    vertices_list: list[np.ndarray] = []
    rx = width * 0.5
    rz = height * 0.5
    thetas = np.linspace(0.0, 2.0 * math.pi, n_radial, endpoint=False)
    cos_t = np.cos(thetas)[:, None]
    sin_t = np.sin(thetas)[:, None]

    for i in range(n_pts):
        p = center_3d[i]
        if i == 0:
            tangent = center_3d[1] - center_3d[0]
        elif i == n_pts - 1:
            tangent = center_3d[-1] - center_3d[-2]
        else:
            tangent = center_3d[i + 1] - center_3d[i - 1]

        t_norm = float(np.linalg.norm(tangent))
        tangent = tangent / t_norm if t_norm > 1e-9 else np.array([1.0, 0.0, 0.0])

        up_ref = np.array([0.0, 0.0, 1.0])
        if abs(float(np.dot(tangent, up_ref))) > 0.95:
            up_ref = np.array([0.0, 1.0, 0.0])

        right = np.cross(tangent, up_ref)
        r_norm = float(np.linalg.norm(right))
        right = right / r_norm if r_norm > 1e-9 else np.array([0.0, 1.0, 0.0])

        up = np.cross(right, tangent)
        u_norm = float(np.linalg.norm(up))
        up = up / u_norm if u_norm > 1e-9 else np.array([0.0, 0.0, 1.0])

        ring = p[None, :] + (cos_t * rx) * right[None, :] + (sin_t * rz) * up[None, :]
        vertices_list.append(ring)

    vertices = np.vstack(vertices_list).astype(np.float32)

    faces_list: list[list[int]] = []
    for i in range(n_pts - 1):
        a0 = i * n_radial
        b0 = (i + 1) * n_radial
        for k in range(n_radial):
            kn = (k + 1) % n_radial
            faces_list.append([a0 + k, b0 + k, b0 + kn])
            faces_list.append([a0 + k, b0 + kn, a0 + kn])

    faces = np.asarray(faces_list, dtype=np.int32)
    logger.info(f"Maillage généré : {len(vertices)} sommets, {len(faces)} triangles")
    return vertices, faces


def export_binary_stl(filepath: Path, vertices: np.ndarray, faces: np.ndarray) -> None:
    tris = vertices[faces]
    v1 = tris[:, 1] - tris[:, 0]
    v2 = tris[:, 2] - tris[:, 0]
    normals = np.cross(v1, v2)
    norms = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.where(norms > 1e-9, normals / np.maximum(norms, 1e-9), np.array([0.0, 0.0, 1.0]))

    header = f"Cave 3D Topo {APP_VERSION} {datetime.datetime.now().isoformat()}".encode("ascii", errors="ignore")[:80].ljust(80, b" ")
    with open(filepath, "wb") as f:
        f.write(header)
        f.write(struct.pack("<I", len(faces)))
        for idx in range(len(faces)):
            n = normals[idx]
            p0, p1, p2 = tris[idx, 0], tris[idx, 1], tris[idx, 2]
            f.write(struct.pack(
                "<12fH",
                float(n[0]), float(n[1]), float(n[2]),
                float(p0[0]), float(p0[1]), float(p0[2]),
                float(p1[0]), float(p1[1]), float(p1[2]),
                float(p2[0]), float(p2[1]), float(p2[2]),
                0,
            ))
    logger.info(f"Export STL réussi : {filepath} ({filepath.stat().st_size} octets)")


# ==============================================================================
# 4. WIDGET 2D INTERACTIF (SÉLECTION PAR TAP / CLIC)
# ==============================================================================

class TopoSelectCanvas(Widget):
    def __init__(self, on_selection_change=None, **kwargs):
        super().__init__(**kwargs)
        self.polylines: list[np.ndarray] = []
        self.selected: list[int] = []
        self.on_selection_change = on_selection_change
        self._bounds = (0.0, 1.0, 0.0, 1.0)
        self._transform = (1.0, 0.0, 0.0)
        self.bind(pos=self._redraw, size=self._redraw)

    def set_polylines(self, polylines: list[np.ndarray], selected: list[int] | None = None):
        self.polylines = polylines
        self.selected = list(selected) if selected else []
        if not polylines:
            self._bounds = (0.0, 1.0, 0.0, 1.0)
            self._redraw()
            return
        all_pts = np.vstack(polylines)
        xmin, ymin = float(all_pts[:, 0].min()), float(all_pts[:, 1].min())
        xmax, ymax = float(all_pts[:, 0].max()), float(all_pts[:, 1].max())
        self._bounds = (xmin, max(xmax, xmin + 1e-6), ymin, max(ymax, ymin + 1e-6))
        self._redraw()

    def _to_screen(self, pts: np.ndarray) -> np.ndarray:
        xmin, xmax, ymin, ymax = self._bounds
        pad = dp(16)
        avail_w = max(self.width - 2 * pad, 1.0)
        avail_h = max(self.height - 2 * pad, 1.0)
        scale = min(avail_w / (xmax - xmin), avail_h / (ymax - ymin))
        ox = self.x + pad + (avail_w - (xmax - xmin) * scale) * 0.5 - xmin * scale
        oy = self.y + pad + (avail_h - (ymax - ymin) * scale) * 0.5 - ymin * scale
        self._transform = (scale, ox, oy)
        sx = pts[:, 0] * scale + ox
        sy = pts[:, 1] * scale + oy
        return np.column_stack([sx, sy]).reshape(-1)

    def _redraw(self, *args):
        self.canvas.clear()
        with self.canvas:
            Color(0.10, 0.11, 0.14, 1)
            Rectangle(pos=self.pos, size=self.size)

            if not self.polylines:
                return

            sel_set = set(self.selected)
            for idx, pts in enumerate(self.polylines):
                coords = self._to_screen(pts)
                if idx in sel_set:
                    Color(0.95, 0.25, 0.25, 1)
                    Line(points=coords.tolist(), width=dp(2.6))
                else:
                    Color(0.45, 0.72, 0.98, 0.75)
                    Line(points=coords.tolist(), width=dp(1.2))

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos) or not self.polylines:
            return super().on_touch_down(touch)

        if getattr(touch, "button", None) == "right":
            if self.selected:
                removed = self.selected.pop()
                logger.debug(f"Annulation clic droit index #{removed}")
                self._redraw()
                if self.on_selection_change:
                    self.on_selection_change(list(self.selected))
            return True

        scale, ox, oy = self._transform
        tx, ty = touch.x, touch.y
        best_idx = None
        best_dist = dp(14)

        for idx, pts in enumerate(self.polylines):
            spts = np.column_stack([pts[:, 0] * scale + ox, pts[:, 1] * scale + oy])
            if len(spts) < 2:
                continue
            a = spts[:-1]
            b = spts[1:]
            ab = b - a
            ab_len2 = np.sum(ab ** 2, axis=1)
            valid = ab_len2 > 1e-9
            if not np.any(valid):
                continue
            a, b, ab, ab_len2 = a[valid], b[valid], ab[valid], ab_len2[valid]
            ap = np.array([tx, ty]) - a
            t = np.clip(np.sum(ap * ab, axis=1) / ab_len2, 0.0, 1.0)[:, None]
            proj = a + t * ab
            dists = np.hypot(proj[:, 0] - tx, proj[:, 1] - ty)
            d_min = float(dists.min())
            if d_min < best_dist:
                best_dist = d_min
                best_idx = idx

        if best_idx is not None:
            if best_idx in self.selected:
                self.selected.remove(best_idx)
                logger.info(f"Désélection index #{best_idx}")
            else:
                self.selected.append(best_idx)
                logger.info(f"Sélection index #{best_idx}")
            self._redraw()
            if self.on_selection_change:
                self.on_selection_change(list(self.selected))
            return True

        return super().on_touch_down(touch)


# ==============================================================================
# 5. WIDGET 3D INTERACTIF (ROTATION / ZOOM TACTILE)
# ==============================================================================

class Cave3DViewport(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rot_x = -55.0
        self.rot_z = 25.0
        self.zoom = 1.0
        self.vertices: np.ndarray | None = None
        self.faces: np.ndarray | None = None
        self.bind(pos=self._redraw, size=self._redraw)

    def set_mesh(self, vertices: np.ndarray, faces: np.ndarray):
        center = vertices.mean(axis=0)
        centered = vertices - center
        scale = max(float(np.max(np.abs(centered))), 1e-6)
        self.vertices = centered / scale
        self.faces = faces
        self._redraw()

    def _project(self) -> tuple[np.ndarray, np.ndarray]:
        assert self.vertices is not None and self.faces is not None
        rx = math.radians(self.rot_x)
        rz = math.radians(self.rot_z)

        mx = np.array([
            [1.0, 0.0, 0.0],
            [0.0, math.cos(rx), -math.sin(rx)],
            [0.0, math.sin(rx), math.cos(rx)],
        ])
        mz = np.array([
            [math.cos(rz), -math.sin(rz), 0.0],
            [math.sin(rz), math.cos(rz), 0.0],
            [0.0, 0.0, 1.0],
        ])

        rot_pts = self.vertices @ (mz @ mx).T
        tris = rot_pts[self.faces]
        depths = tris[:, :, 2].mean(axis=1)
        order = np.argsort(depths)
        tris_sorted = tris[order]
        depths_sorted = depths[order]

        pad = dp(24)
        radius = min(max(self.width - 2 * 
