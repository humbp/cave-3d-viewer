"""Caméra orbitale orthographique : matrices de rotation, zoom, panoramique, projection.

Repère monde : X = Est, Y = Nord, Z = haut.
Vue : on tourne d'abord autour de Z (``yaw``), puis on bascule autour de X (``tilt``).
  - tilt = 0      -> coupe développée vue de face (écran x = X, écran y = Z)
  - tilt = 90°    -> vue en plan, Nord en haut (écran x = X, écran y = Y)
La coordonnée « profondeur » renvoyée est la composante vue-axe (utile pour trier).
"""
from __future__ import annotations

import math

import numpy as np


def rot_z(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def rot_x(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


class Camera:
    MIN_ZOOM = 0.2
    MAX_ZOOM = 400.0
    ORBIT_SENSITIVITY = 0.008  # radians par pixel

    def __init__(self) -> None:
        self.yaw = 0.0
        self.tilt = 0.0
        self.zoom = 1.0
        self.pan = np.zeros(2)
        self.reset()

    # ------------------------------------------------------------- préréglages
    def reset(self) -> None:
        self.yaw = math.radians(-30.0)
        self.tilt = math.radians(55.0)
        self.zoom = 1.0
        self.pan = np.zeros(2)

    def set_plan(self) -> None:
        """Vue de dessus, Nord en haut."""
        self.yaw = 0.0
        self.tilt = math.pi / 2
        self.pan = np.zeros(2)

    def set_section(self) -> None:
        """Vue de face (coupe développée)."""
        self.yaw = 0.0
        self.tilt = 0.0
        self.pan = np.zeros(2)

    # ------------------------------------------------------------- matrices
    def view_matrix(self) -> np.ndarray:
        return rot_x(self.tilt) @ rot_z(self.yaw)

    def scale(self, width: float, height: float) -> float:
        """Pixels par unité de scène (la scène a un rayon de 1)."""
        return 0.45 * min(width, height) * self.zoom

    # ------------------------------------------------------------- gestes
    def orbit(self, dx: float, dy: float) -> None:
        self.yaw += dx * self.ORBIT_SENSITIVITY
        self.tilt = float(np.clip(self.tilt - dy * self.ORBIT_SENSITIVITY, -math.pi / 2, math.pi / 2))

    def pan_by(self, dx: float, dy: float) -> None:
        self.pan = self.pan + np.array([dx, dy], dtype=float)

    def zoom_about(self, factor: float, anchor_x: float, anchor_y: float) -> None:
        """Zoom en gardant fixe le point d'écran (anchor_x, anchor_y), relatif au centre."""
        if not math.isfinite(factor) or factor <= 0:
            return
        new_zoom = float(np.clip(self.zoom * factor, self.MIN_ZOOM, self.MAX_ZOOM))
        f = new_zoom / self.zoom
        anchor = np.array([anchor_x, anchor_y], dtype=float)
        self.pan = anchor - (anchor - self.pan) * f
        self.zoom = new_zoom

    # ------------------------------------------------------------- projection
    def project(self, points: np.ndarray, cx: float, cy: float, width: float, height: float):
        """Projette des points (N, 3) -> (coordonnées écran (N, 2), profondeur (N,))."""
        view = points @ self.view_matrix().T
        s = self.scale(width, height)
        xy = np.empty((len(points), 2), dtype=float)
        xy[:, 0] = cx + self.pan[0] + s * view[:, 0]
        xy[:, 1] = cy + self.pan[1] + s * view[:, 2]
        return xy, view[:, 1]
