"""Modèle de données d'une cavité : polylignes 3D et stations, regroupées par calque.

Conventions : X = Est, Y = Nord, Z = altitude (vers le haut), en mètres.
Ce module ne dépend que de numpy (aucune dépendance Kivy) afin de rester testable
sur un ordinateur de bureau.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

import numpy as np

# Nombre maximal de sommets conservés : au-delà, les polylignes sont décimées
# pour garder une interface fluide sur mobile.
MAX_VERTICES = 150_000


@dataclass
class Polyline:
    """Suite de points 3D reliés entre eux, appartenant à un calque."""

    layer: str
    points: np.ndarray  # forme (n, 3), n >= 2


@dataclass
class CaveModel:
    """Ensemble des polylignes et stations d'une cavité."""

    name: str = "Sans titre"
    polylines: List[Polyline] = field(default_factory=list)
    station_xyz: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    station_layers: List[str] = field(default_factory=list)
    station_names: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    source: str = ""

    # ------------------------------------------------------------------ état
    @property
    def is_empty(self) -> bool:
        return not self.polylines and len(self.station_xyz) == 0

    def layer_names(self) -> List[str]:
        """Calques dans l'ordre de première apparition."""
        seen: Dict[str, None] = {}
        for poly in self.polylines:
            seen.setdefault(poly.layer, None)
        for layer in self.station_layers:
            seen.setdefault(layer, None)
        return list(seen)

    # ------------------------------------------------------------ géométrie
    def all_points(self) -> np.ndarray:
        parts = [p.points for p in self.polylines]
        if len(self.station_xyz):
            parts.append(self.station_xyz)
        if not parts:
            return np.zeros((0, 3))
        return np.vstack(parts)

    def bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        """Boîte englobante (min, max) sur X, Y, Z."""
        pts = self.all_points()
        if len(pts) == 0:
            raise ValueError("Le modèle est vide.")
        return pts.min(axis=0), pts.max(axis=0)

    def stats(self) -> Dict[str, float]:
        """Statistiques affichées dans le panneau d'informations."""
        mn, mx = self.bounds()
        ext = mx - mn
        n_vertices = sum(len(p.points) for p in self.polylines)
        n_segments = sum(len(p.points) - 1 for p in self.polylines)
        development = 0.0
        for poly in self.polylines:
            d = np.diff(poly.points, axis=0)
            development += float(np.sqrt((d * d).sum(axis=1)).sum())
        return {
            "extent_x": float(ext[0]),
            "extent_y": float(ext[1]),
            "extent_z": float(ext[2]),
            "zmin": float(mn[2]),
            "zmax": float(mx[2]),
            "denivele": float(ext[2]),
            "development": development,
            "n_vertices": n_vertices,
            "n_segments": n_segments,
            "n_stations": int(len(self.station_xyz)),
            "n_layers": len(self.layer_names()),
        }


def decimate(model: CaveModel, max_vertices: int = MAX_VERTICES) -> CaveModel:
    """Réduit le nombre de sommets si nécessaire (garde toujours les extrémités)."""
    total = sum(len(p.points) for p in model.polylines)
    if total <= max_vertices:
        return model
    stride = int(np.ceil(total / max_vertices))
    reduced: List[Polyline] = []
    for poly in model.polylines:
        pts = poly.points
        if len(pts) > 3:
            idx = np.unique(np.concatenate((np.arange(0, len(pts), stride), [len(pts) - 1])))
            pts = pts[idx]
        reduced.append(Polyline(poly.layer, pts))
    model.polylines = reduced
    model.warnings.append(
        f"Modèle très dense ({total} sommets) : décimé (1 sommet sur {stride}) pour rester fluide."
    )
    return model


class ModelBuilder:
    """Accumule polylignes et stations en filtrant les données invalides."""

    MAX_WARNINGS = 50

    def __init__(self, name: str = "Sans titre") -> None:
        self.name = name
        self.warnings: List[str] = []
        self._polylines: List[Polyline] = []
        self._st_xyz: List[np.ndarray] = []
        self._st_layers: List[str] = []
        self._st_names: List[str] = []
        self._skipped = 0

    def warn(self, message: str) -> None:
        if message not in self.warnings and len(self.warnings) < self.MAX_WARNINGS:
            self.warnings.append(message)

    def add_polyline(self, layer: str, points: Sequence[Sequence[float]]) -> None:
        try:
            arr = np.asarray(points, dtype=float).reshape(-1, 3)
        except (ValueError, TypeError):
            self._skipped += 1
            return
        arr = arr[np.isfinite(arr).all(axis=1)]
        if len(arr) >= 2:
            self._polylines.append(Polyline(layer, arr))
        elif len(arr) == 1:
            self.add_station(layer, arr[0], "")
        else:
            self._skipped += 1

    def add_station(self, layer: str, xyz: Sequence[float], name: str = "") -> None:
        arr = np.asarray(xyz, dtype=float).reshape(-1)
        if arr.shape != (3,) or not np.isfinite(arr).all():
            self._skipped += 1
            return
        self._st_xyz.append(arr)
        self._st_layers.append(layer)
        self._st_names.append(name)

    def build(self) -> CaveModel:
        if self._skipped:
            self.warn(f"{self._skipped} élément(s) ignoré(s) : coordonnées invalides ou incomplètes.")
        xyz = np.array(self._st_xyz, dtype=float).reshape(-1, 3)
        model = CaveModel(
            name=self.name,
            polylines=list(self._polylines),
            station_xyz=xyz,
            station_layers=list(self._st_layers),
            station_names=list(self._st_names),
            warnings=list(self.warnings),
        )
        return decimate(model)
