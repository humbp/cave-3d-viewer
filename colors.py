"""Couleurs : dégradé profondeur/altitude et palette de calques."""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import numpy as np

NBINS = 16  # nombre de teintes du dégradé (une instruction graphique par plage)

_STOPS = np.array([0.0, 0.33, 0.66, 1.0])
_R = np.array([1.00, 0.95, 0.72, 0.30])
_G = np.array([0.88, 0.50, 0.22, 0.45])
_B = np.array([0.25, 0.18, 0.60, 0.98])

UNIFORM_COLOR = (0.35, 0.85, 0.95, 1.0)

LAYER_PALETTE = [
    (0.35, 0.85, 0.95, 1.0),
    (0.98, 0.72, 0.25, 1.0),
    (0.55, 0.90, 0.45, 1.0),
    (0.95, 0.45, 0.55, 1.0),
    (0.70, 0.55, 0.98, 1.0),
    (0.95, 0.90, 0.40, 1.0),
    (0.40, 0.75, 0.70, 1.0),
    (0.90, 0.60, 0.85, 1.0),
    (0.75, 0.80, 0.85, 1.0),
    (0.95, 0.60, 0.30, 1.0),
]


def ramp(t) -> np.ndarray:
    """t in [0, 1] (0 = point le plus haut, 1 = point le plus bas) -> RGB (N, 3)."""
    t = np.clip(np.asarray(t, dtype=float), 0.0, 1.0)
    return np.column_stack((np.interp(t, _STOPS, _R), np.interp(t, _STOPS, _G), np.interp(t, _STOPS, _B)))


def layer_colors(layers: Sequence[str]) -> Dict[str, Tuple[float, float, float, float]]:
    return {name: LAYER_PALETTE[i % len(LAYER_PALETTE)] for i, name in enumerate(layers)}


def segment_bins(z0: np.ndarray, z1: np.ndarray, zmin: float, zmax: float, nbins: int = NBINS) -> np.ndarray:
    """Teinte (0..nbins-1) de chaque segment selon l'altitude de son milieu."""
    z0 = np.asarray(z0, dtype=float)
    z1 = np.asarray(z1, dtype=float)
    span = zmax - zmin
    if span <= 1e-12:
        return np.zeros(len(z0), dtype=int)
    t = (zmax - (z0 + z1) / 2.0) / span
    return np.clip(np.floor(t * nbins).astype(int), 0, nbins - 1)


def run_lengths(bins: np.ndarray) -> List[Tuple[int, int, int]]:
    """Regroupe les segments consécutifs de même teinte.

    Renvoie des tuples (premier segment, dernier segment + 1, teinte). Un groupe qui
    couvre les segments a..b-1 correspond aux sommets a..b (inclus).
    """
    n = len(bins)
    if n == 0:
        return []
    change = np.flatnonzero(np.diff(bins)) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [n]))
    return [(int(s), int(e), int(bins[s])) for s, e in zip(starts, ends)]
