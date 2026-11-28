"""Lecteur DXF (ezdxf) : points, lignes, polylignes 2D/3D, arcs, cercles, splines, blocs.

Les entités sont regroupées par calque (« layer »). Les unités du DXF ($INSUNITS) sont
converties en mètres quand elles sont renseignées. Les entités non supportées sont
comptées et signalées (jamais d'échec silencieux).
"""
from __future__ import annotations

import math
import os
from collections import Counter
from typing import Iterable, Iterator, Optional

import numpy as np

from geometry.model import CaveModel, ModelBuilder

from .errors import ParseError

MAX_INSERT_DEPTH = 4
FLATTEN_SAGITTA_M = 0.01  # écart maximal de l'approximation des courbes : 1 cm


def _xyz(vec) -> np.ndarray:
    return np.array([vec.x, vec.y, vec.z], dtype=float)


def _read_document(ezdxf, path: str):
    try:
        return ezdxf.readfile(path)
    except Exception as first:
        try:
            from ezdxf import recover

            document, _auditor = recover.readfile(path)
            return document
        except Exception as second:
            raise ParseError(f"Fichier DXF illisible : {first}") from second


def _unit_factor(ezdxf, document, builder: ModelBuilder) -> float:
    try:
        from ezdxf import units

        code = document.units
        if not code:
            return 1.0
        factor = float(units.conversion_factor(code, units.M))
        if not math.isfinite(factor) or factor <= 0:
            return 1.0
        if abs(factor - 1.0) > 1e-12:
            builder.warn(f"Unités du DXF converties en mètres (× {factor:g}).")
        return factor
    except Exception:
        return 1.0


def _iter_entities(entities: Iterable, depth: int, builder: ModelBuilder) -> Iterator:
    for entity in entities:
        try:
            kind = entity.dxftype()
        except Exception:
            continue
        if kind != "INSERT":
            yield entity
            continue
        if depth >= MAX_INSERT_DEPTH:
            builder.warn("Blocs imbriqués trop profonds : ignorés.")
            continue
        try:
            yield from _iter_entities(list(entity.virtual_entities()), depth + 1, builder)
        except Exception as exc:
            builder.warn(f"Bloc «{entity.dxf.get('name', '?')}» non développé : {exc}")


def _flatten_path(entity, distance: float) -> np.ndarray:
    from ezdxf import path as ezpath

    flat = ezpath.make_path(entity).flattening(distance=distance)
    return np.array([[v.x, v.y, v.z] for v in flat], dtype=float)


def _is_default_extrusion(entity) -> bool:
    try:
        ext = entity.dxf.get("extrusion", (0.0, 0.0, 1.0))
        return abs(ext[0]) < 1e-12 and abs(ext[1]) < 1e-12 and ext[2] > 0
    except Exception:
        return True


def _entity_points(entity, kind: str, distance: float) -> Optional[np.ndarray]:
    """Points (en unités du dessin) d'une entité, ou None si le type n'est pas géré."""
    if kind == "LINE":
        return np.array([_xyz(entity.dxf.start), _xyz(entity.dxf.end)])

    if kind == "LWPOLYLINE":
        raw = entity.get_points("xyb")
        has_bulge = any(abs(p[2]) > 1e-12 for p in raw)
        if has_bulge or not _is_default_extrusion(entity):
            return _flatten_path(entity, distance)
        elevation = float(entity.dxf.get("elevation", 0.0))
        pts = np.array([[p[0], p[1], elevation] for p in raw], dtype=float)
        if len(pts) and entity.closed:
            pts = np.vstack((pts, pts[:1]))
        return pts

    if kind == "POLYLINE":
        if entity.is_3d_polyline:
            pts = np.array([_xyz(v.dxf.location) for v in entity.vertices], dtype=float)
            if len(pts) and entity.is_closed:
                pts = np.vstack((pts, pts[:1]))
            return pts
        if entity.is_2d_polyline:
            return _flatten_path(entity, distance)
        return None  # maillages : non gérés

    if kind in ("ARC", "CIRCLE", "ELLIPSE", "SPLINE"):
        return _flatten_path(entity, distance)

    return None


def parse_dxf(path: str) -> CaveModel:
    try:
        import ezdxf
    except Exception as exc:
        raise ParseError(f"Le module ezdxf n'est pas disponible dans cette installation ({exc}).") from exc

    document = _read_document(ezdxf, path)
    builder = ModelBuilder(os.path.splitext(os.path.basename(path))[0] or "DXF")
    factor = _unit_factor(ezdxf, document, builder)
    distance = FLATTEN_SAGITTA_M / factor

    ignored: Counter = Counter()
    failed: Counter = Counter()
    first_error: dict = {}

    for entity in _iter_entities(document.modelspace(), 0, builder):
        kind = entity.dxftype()
        layer = str(entity.dxf.get("layer", "0"))
        try:
            if kind == "POINT":
                builder.add_station(layer, _xyz(entity.dxf.location) * factor, "")
                continue
            points = _entity_points(entity, kind, distance)
        except Exception as exc:
            failed[kind] += 1
            first_error.setdefault(kind, str(exc))
            continue
        if points is None:
            ignored["POLYLINE (maillage)" if kind == "POLYLINE" else kind] += 1
            continue
        builder.add_polyline(layer, points * factor)

    if ignored:
        detail = ", ".join(f"{k}×{n}" for k, n in ignored.most_common(6))
        builder.warn(f"Entités non supportées ignorées : {detail}.")
    for kind, count in failed.items():
        builder.warn(f"{count} entité(s) {kind} illisible(s) : {first_error[kind]}")

    model = builder.build()
    if model.is_empty:
        raise ParseError(
            "Aucune géométrie exploitable dans ce DXF (lus : POINT, LINE, LWPOLYLINE, POLYLINE 2D/3D, "
            "ARC, CIRCLE, ELLIPSE, SPLINE, blocs INSERT)."
        )
    return model
