"""Cavité synthétique intégrée : puits d'entrée, galerie, salle, affluent en boucle,
second puits et galerie basse (≈ 78 m de dénivelé). Permet de tester l'application
dès le lancement, sans fichier externe.
"""
from __future__ import annotations

import numpy as np

from geometry.model import CaveModel, ModelBuilder


def build_sample_cave() -> CaveModel:
    b = ModelBuilder("Gouffre de démonstration")

    # Puits d'entrée (≈ 38 m), légèrement décalé
    z = np.linspace(0.0, -38.0, 14)
    shaft1 = np.column_stack((0.8 * np.sin(z / 7.0), 0.5 * np.cos(z / 11.0) - 0.5, z))
    b.add_polyline("puits", shaft1)
    s1 = shaft1[-1]

    # Galerie principale en méandres, légèrement descendante
    t = np.linspace(0.0, 1.0, 70)
    gallery = np.column_stack((s1[0] + 120.0 * t, s1[1] + 16.0 * np.sin(2 * np.pi * 1.4 * t), s1[2] - 9.0 * t))
    b.add_polyline("galerie", gallery)
    g_end = gallery[-1]

    # Salle : contour elliptique au milieu de la galerie
    centre = gallery[35]
    a = np.linspace(0.0, 2 * np.pi, 41)
    hall = np.column_stack((centre[0] + 13.0 * np.cos(a), centre[1] + 8.0 * np.sin(a), np.full_like(a, centre[2] - 0.5)))
    b.add_polyline("salle", hall)

    # Affluent : boucle nord qui descend puis rejoint la galerie
    s = np.linspace(0.0, 1.0, 40)
    start, end = gallery[20], gallery[48]
    loop = np.outer(1 - s, start) + np.outer(s, end)
    loop[:, 1] += 34.0 * np.sin(np.pi * s)
    loop[:, 2] -= 9.0 * np.sin(np.pi * s)
    loop[0], loop[-1] = start, end
    b.add_polyline("affluent", loop)

    # Second puits (≈ 27 m) au bout de la galerie
    u = np.linspace(0.0, 27.0, 10)
    shaft2 = np.column_stack((g_end[0] + 1.2 * np.sin(u / 5.0), g_end[1] + 0.8 * (1 - np.cos(u / 8.0)), g_end[2] - u))
    b.add_polyline("puits", shaft2)
    bottom = shaft2[-1]

    # Galerie basse vers l'ouest, jusqu'au point le plus profond
    t2 = np.linspace(0.0, 1.0, 50)
    lower = np.column_stack((bottom[0] - 70.0 * t2, bottom[1] - 28.0 * np.sin(np.pi * t2 * 1.2), bottom[2] - 3.5 * t2))
    b.add_polyline("galerie basse", lower)

    # Stations nommées
    b.add_station("puits", shaft1[0], "E0")
    b.add_station("puits", s1, "S1")
    for i in range(10, 70, 10):
        b.add_station("galerie", gallery[i], f"G{i // 10}")
    b.add_station("galerie", g_end, "G7")
    b.add_station("puits", bottom, "B1")
    b.add_station("galerie basse", lower[-1], "SIPHON")

    return b.build()
