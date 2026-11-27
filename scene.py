"""Normalisation et centrage du modèle pour le rendu.

Les coordonnées sont centrées sur le centre de la boîte englobante et mises à l'échelle
pour que la scène tienne dans une sphère de rayon 1. Le calcul reste en float64 (les
coordonnées géographiques absolues, ex. 600000 / 200000, perdraient en précision en
float32) ; seules les valeurs projetées en pixels sont ensuite transmises à Kivy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from .model import CaveModel


@dataclass
class Scene:
    vertices: np.ndarray            # (V, 3) centrés, rayon <= 1
    z_world: np.ndarray             # (V,) altitude réelle en mètres
    chains: List[Tuple[str, int, int]]  # (calque, indice de départ, nombre de sommets)
    station_vertices: np.ndarray    # (S, 3) centrés
    station_layers: List[str]
    center: np.ndarray              # centre réel (m)
    radius: float                   # rayon réel de la sphère englobante (m)
    zmin: float
    zmax: float
    layers: List[str]


def build_scene(model: CaveModel) -> Scene:
    if model.is_empty:
        raise ValueError("Le modèle est vide.")
    mn, mx = model.bounds()
    center = (mn + mx) / 2.0
    radius = float(np.linalg.norm(mx - mn) / 2.0)
    if not np.isfinite(radius) or radius < 1e-9:
        radius = 1.0

    chunks = []
    chains: List[Tuple[str, int, int]] = []
    start = 0
    for poly in model.polylines:
        n = len(poly.points)
        chunks.append(poly.points)
        chains.append((poly.layer, start, n))
        start += n
    points = np.vstack(chunks) if chunks else np.zeros((0, 3))

    return Scene(
        vertices=(points - center) / radius,
        z_world=points[:, 2].copy(),
        chains=chains,
        station_vertices=(model.station_xyz - center) / radius if len(model.station_xyz) else np.zeros((0, 3)),
        station_layers=list(model.station_layers),
        center=center,
        radius=radius,
        zmin=float(mn[2]),
        zmax=float(mx[2]),
        layers=model.layer_names(),
    )
