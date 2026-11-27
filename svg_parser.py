"""Lecteur SVG : échantillonne les tracés en polylignes 3D.

Utilise ``svgpathtools`` quand il est disponible. Si le module est absent ou échoue à
l'import (certaines versions exigent scipy, difficile à compiler pour Android), un lecteur
intégré (xml + regex) prend le relai : path (M L H V C S Q T A Z), line, polyline,
polygon, rect, circle, ellipse. Dans ce lecteur de secours les arcs « A » sont
approchés par des segments droits et les attributs ``transform`` sont ignorés.

Un SVG est plat : il est placé dans le plan X,Y (plan) ou X,Z (coupe) selon ``plane``.
L'axe vertical SVG (vers le bas) est inversé pour que le haut du dessin reste en haut.
"""
from __future__ import annotations

import math
import os
import re
import xml.etree.ElementTree as ET
from typing import List, Tuple

import numpy as np

from geometry.model import CaveModel, ModelBuilder

from .errors import ParseError

CURVE_SAMPLES = 12
DEFAULT_LAYER = "svg"

Item = Tuple[str, np.ndarray]  # (calque, tableau (n, 2))


# --------------------------------------------------------------------- utilitaires
def _layer_from_attrs(attrs: dict) -> str:
    stroke = attrs.get("stroke")
    if not stroke:
        match = re.search(r"stroke:\s*([^;]+)", attrs.get("style", "") or "")
        stroke = match.group(1).strip() if match else None
    if stroke and stroke.lower() != "none":
        return f"trait {stroke}"
    return attrs.get("class") or DEFAULT_LAYER


def _map_points(xy: np.ndarray, plane: str, scale: float) -> np.ndarray:
    x = xy[:, 0] * scale
    v = -xy[:, 1] * scale
    zeros = np.zeros(len(xy))
    if plane.upper() == "XZ":
        return np.column_stack((x, zeros, v))
    return np.column_stack((x, v, zeros))


# --------------------------------------------------------------------- svgpathtools
def _items_with_svgpathtools(path: str) -> List[Item]:
    from svgpathtools import svg2paths  # import tardif : peut lever ImportError

    paths, attributes = svg2paths(path)
    items: List[Item] = []
    for svg_path, attrs in zip(paths, attributes):
        layer = _layer_from_attrs(attrs)
        try:
            subpaths = svg_path.continuous_subpaths()
        except Exception:
            subpaths = [svg_path]
        for sub in subpaths:
            points: List[complex] = []
            for index, segment in enumerate(sub):
                if type(segment).__name__ == "Line":
                    samples = [segment.start, segment.end]
                else:
                    samples = [segment.point(float(t)) for t in np.linspace(0.0, 1.0, CURVE_SAMPLES + 1)]
                points.extend(samples if index == 0 else samples[1:])
            if len(points) >= 2:
                items.append((layer, np.array([[p.real, p.imag] for p in points], dtype=float)))
    return items


# --------------------------------------------------------------------- lecteur de secours
_TOKEN_RE = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}


def _cubic(p0, p1, p2, p3, n=CURVE_SAMPLES) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n + 1)[1:, None]
    return (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3


def _quad(p0, p1, p2, n=CURVE_SAMPLES) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n + 1)[1:, None]
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * p1 + t ** 2 * p2


def _parse_path_d(d: str, stats: dict) -> List[np.ndarray]:
    tokens = _TOKEN_RE.findall(d)
    subpaths: List[np.ndarray] = []
    points: List[np.ndarray] = []
    cur = np.zeros(2)
    start = np.zeros(2)
    last_c2 = None
    last_q = None
    cmd = None
    i = 0

    def flush():
        if len(points) >= 2:
            subpaths.append(np.array(points, dtype=float))

    try:
        while i < len(tokens):
            token = tokens[i]
            if token.isalpha():
                cmd = token
                i += 1
                if cmd in "Zz":
                    if points:
                        points.append(start.copy())
                    flush()
                    points = [start.copy()]
                    cur = start.copy()
                    last_c2 = last_q = None
                continue
            if cmd is None or cmd in "Zz":
                i += 1
                continue

            upper = cmd.upper()
            rel = cmd.islower()
            nargs = _ARGS[upper]
            args = [float(t) for t in tokens[i:i + nargs]]
            if len(args) < nargs:
                raise IndexError("chemin tronqué")
            i += nargs
            base = cur if rel else np.zeros(2)

            if upper == "M":
                flush()
                cur = base + np.array(args)
                start = cur.copy()
                points = [cur.copy()]
                cmd = "l" if rel else "L"
                last_c2 = last_q = None
            elif upper == "L":
                cur = base + np.array(args)
                points.append(cur.copy())
                last_c2 = last_q = None
            elif upper == "H":
                cur = np.array([(cur[0] if rel else 0.0) + args[0], cur[1]])
                points.append(cur.copy())
                last_c2 = last_q = None
            elif upper == "V":
                cur = np.array([cur[0], (cur[1] if rel else 0.0) + args[0]])
                points.append(cur.copy())
                last_c2 = last_q = None
            elif upper == "C":
                p1, p2, p3 = (base + np.array(args[k:k + 2]) for k in (0, 2, 4))
                points.extend(_cubic(cur, p1, p2, p3))
                cur, last_c2, last_q = p3, p2, None
            elif upper == "S":
                p1 = 2 * cur - last_c2 if last_c2 is not None else cur
                p2, p3 = (base + np.array(args[k:k + 2]) for k in (0, 2))
                points.extend(_cubic(cur, p1, p2, p3))
                cur, last_c2, last_q = p3, p2, None
            elif upper == "Q":
                p1, p2 = (base + np.array(args[k:k + 2]) for k in (0, 2))
                points.extend(_quad(cur, p1, p2))
                cur, last_q, last_c2 = p2, p1, None
            elif upper == "T":
                p1 = 2 * cur - last_q if last_q is not None else cur
                p2 = base + np.array(args)
                points.extend(_quad(cur, p1, p2))
                cur, last_q, last_c2 = p2, p1, None
            elif upper == "A":
                cur = base + np.array(args[5:7])
                points.append(cur.copy())
                stats["arcs"] = stats.get("arcs", 0) + 1
                last_c2 = last_q = None
    except (IndexError, ValueError):
        stats["truncated"] = stats.get("truncated", 0) + 1
    flush()
    return subpaths


def _items_fallback(path: str, warnings: List[str]) -> List[Item]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ParseError(f"Fichier SVG illisible : {exc}") from exc

    stats: dict = {}
    items: List[Item] = []
    has_transform = False

    def number(el, key, default=0.0) -> float:
        try:
            return float(el.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("path", "line", "polyline", "polygon", "rect", "circle", "ellipse"):
            continue
        if el.get("transform"):
            has_transform = True
        layer = _layer_from_attrs(el.attrib)
        subs: List[np.ndarray] = []
        if tag == "path":
            subs = _parse_path_d(el.get("d", ""), stats)
        elif tag == "line":
            subs = [np.array([[number(el, "x1"), number(el, "y1")], [number(el, "x2"), number(el, "y2")]])]
        elif tag in ("polyline", "polygon"):
            nums = [float(t) for t in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", el.get("points", ""))]
            pts = np.array(nums[: len(nums) // 2 * 2], dtype=float).reshape(-1, 2)
            if tag == "polygon" and len(pts) >= 2:
                pts = np.vstack((pts, pts[:1]))
            subs = [pts]
        elif tag == "rect":
            x, y, w, h = number(el, "x"), number(el, "y"), number(el, "width"), number(el, "height")
            subs = [np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]])]
        else:  # circle / ellipse
            cx, cy = number(el, "cx"), number(el, "cy")
            rx = number(el, "rx", number(el, "r"))
            ry = number(el, "ry", number(el, "r"))
            a = np.linspace(0.0, 2 * math.pi, 49)
            subs = [np.column_stack((cx + rx * np.cos(a), cy + ry * np.sin(a)))]
        for sub in subs:
            if len(sub) >= 2:
                items.append((layer, sub))

    if has_transform:
        warnings.append("Attributs « transform » SVG ignorés (lecteur intégré).")
    if stats.get("arcs"):
        warnings.append(f"{stats['arcs']} arc(s) SVG approché(s) par des segments droits (lecteur intégré).")
    if stats.get("truncated"):
        warnings.append(f"{stats['truncated']} chemin(s) SVG tronqué(s) ou mal formé(s).")
    return items


# --------------------------------------------------------------------- point d'entrée
def parse_svg(path: str, plane: str = "XY", scale: float = 1.0) -> CaveModel:
    """Lit un SVG. ``scale`` = mètres par unité SVG ; ``plane`` = « XY » (plan) ou « XZ » (coupe)."""
    if not (math.isfinite(scale) and scale > 0):
        raise ParseError("L'échelle du SVG doit être un nombre strictement positif.")
    builder = ModelBuilder(os.path.splitext(os.path.basename(path))[0] or "SVG")

    try:
        items = _items_with_svgpathtools(path)
    except Exception as exc:  # ImportError, scipy absent, XML invalide...
        fallback_warnings: List[str] = []
        builder.warn(f"svgpathtools indisponible ou en échec ({type(exc).__name__}) : lecteur SVG intégré utilisé.")
        items = _items_fallback(path, fallback_warnings)
        for message in fallback_warnings:
            builder.warn(message)

    for layer, xy in items:
        builder.add_polyline(layer, _map_points(xy, plane, scale))

    model = builder.build()
    if model.is_empty:
        raise ParseError(
            "Aucun tracé exploitable dans ce SVG (lus : path, line, polyline, polygon, rect, circle, ellipse)."
        )
    return model
