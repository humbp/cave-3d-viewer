"""Widget Kivy de visualisation 3D.

Principe : la projection orthographique est calculée avec numpy, puis les points 2D
résultants sont écrits dans des instructions ``Line`` créées une seule fois (la structure
n'est reconstruite que si le modèle, les calques ou le mode de couleur changent). Cette
approche évite les shaders personnalisés et reste fluide sur mobile.

Gestes :
  - 1 doigt : rotation orbitale (ou panoramique si ``one_finger_pan``)
  - 2 doigts : pincement = zoom, déplacement = panoramique
  - double tape : réinitialise la vue ; molette de souris : zoom (bureau)
"""
from __future__ import annotations

import math
from typing import Iterable, Optional, Set

import numpy as np
from kivy.clock import Clock
from kivy.graphics import Color, InstructionGroup, Line, Point, Rectangle
from kivy.graphics.texture import Texture
from kivy.metrics import dp, sp
from kivy.properties import BooleanProperty, OptionProperty
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from geometry.colors import NBINS, UNIFORM_COLOR, layer_colors, ramp, run_lengths, segment_bins
from geometry.scene import Scene
from geometry.transforms import Camera

BACKGROUND = (0.055, 0.065, 0.085, 1.0)
STATION_COLOR = (0.95, 0.95, 0.95, 1.0)
AXIS_COLORS = ((0.95, 0.35, 0.35, 1.0), (0.35, 0.85, 0.45, 1.0), (0.45, 0.60, 1.0, 1.0))
AXIS_NAMES = ("E", "N", "Z")
HUD_TEXT = (0.88, 0.90, 0.93, 1.0)


def _nice_length(x: float) -> float:
    """Plus grande longueur « ronde » (1, 2, 5 × 10^k) inférieure ou égale à x."""
    if not math.isfinite(x) or x <= 0:
        return 1.0
    base = 10.0 ** math.floor(math.log10(x))
    for m in (5.0, 2.0, 1.0):
        if m * base <= x:
            return m * base
    return base


def _format_length(meters: float) -> str:
    if meters >= 1000:
        return f"{meters / 1000:g} km"
    if meters >= 1:
        return f"{meters:g} m"
    if meters >= 0.01:
        return f"{meters * 100:g} cm"
    return f"{meters * 1000:g} mm"


class CaveView(Widget):
    one_finger_pan = BooleanProperty(False)
    show_stations = BooleanProperty(True)
    show_axes = BooleanProperty(True)
    color_mode = OptionProperty("depth", options=("depth", "altitude", "layer", "uniform"))

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.camera = Camera()
        self.scene: Optional[Scene] = None
        self.visible_layers: Set[str] = set()

        self._layer_colors = {}
        self._runs = []  # (Line, premier sommet, dernier sommet + 1)
        self._station_pt: Optional[Point] = None
        self._station_idx = np.zeros(0, dtype=int)
        self._touches = {}
        self._prev_mid = None
        self._prev_dist = None
        self._trigger = Clock.create_trigger(self._redraw, -1)

        with self.canvas.before:
            Color(*BACKGROUND)
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self._geo = InstructionGroup()
        self.canvas.add(self._geo)
        self._hud = InstructionGroup()
        self.canvas.after.add(self._hud)

        # Texture du dégradé de la légende (ligne 0 = bas = point le plus profond)
        self._legend_tex = Texture.create(size=(1, 64), colorfmt="rgb")
        rows = (ramp(np.linspace(1.0, 0.0, 64)) * 255).astype(np.uint8)
        self._legend_tex.blit_buffer(rows.tobytes(), colorfmt="rgb", bufferfmt="ubyte")
        self._legend_tex.mag_filter = "linear"
        self._legend_tex.min_filter = "linear"

        self._axis_labels = [self._make_label(name, AXIS_COLORS[i]) for i, name in enumerate(AXIS_NAMES)]
        self._scale_label = self._make_label("", HUD_TEXT)
        self._legend_top = self._make_label("", HUD_TEXT)
        self._legend_bottom = self._make_label("", HUD_TEXT)

        self.bind(
            pos=self._on_geometry,
            size=self._on_geometry,
            color_mode=lambda *_: self._rebuild(),
            show_stations=lambda *_: self._rebuild(),
            show_axes=lambda *_: self._trigger(),
        )

    # ------------------------------------------------------------------ utilitaires
    def _make_label(self, text: str, color) -> Label:
        label = Label(text=text, size_hint=(None, None), color=color, font_size=sp(11), opacity=0)
        label.texture_update()
        label.size = label.texture_size
        self.add_widget(label)
        return label

    @staticmethod
    def _set_text(label: Label, text: str) -> None:
        if label.text != text:
            label.text = text
            label.texture_update()
            label.size = label.texture_size

    def _on_geometry(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._trigger()

    # ------------------------------------------------------------------ API publique
    def set_scene(self, scene: Optional[Scene]) -> None:
        self.scene = scene
        self.visible_layers = set(scene.layers) if scene else set()
        self._layer_colors = layer_colors(scene.layers) if scene else {}
        self.camera.reset()
        self._rebuild()

    def layer_color_map(self):
        return dict(self._layer_colors)

    def set_visible_layers(self, layers: Iterable[str]) -> None:
        self.visible_layers = set(layers)
        self._rebuild()

    def reset_view(self) -> None:
        self.camera.reset()
        self._trigger()

    def view_plan(self) -> None:
        self.camera.set_plan()
        self._trigger()

    def view_section(self) -> None:
        self.camera.set_section()
        self._trigger()

    # ------------------------------------------------------------------ construction
    def _rebuild(self) -> None:
        """Recrée les instructions graphiques (structure), puis redessine."""
        self._geo.clear()
        self._runs = []
        self._station_pt = None
        self._station_idx = np.zeros(0, dtype=int)
        scene = self.scene
        if scene is None:
            self._trigger()
            return

        mode = self.color_mode
        gradient = mode in ("depth", "altitude")
        bin_colors = ramp((np.arange(NBINS) + 0.5) / NBINS) if gradient else None
        width = dp(1.5)

        for layer, start, count in scene.chains:
            if layer not in self.visible_layers or count < 2:
                continue
            if gradient:
                z = scene.z_world[start:start + count]
                bins = segment_bins(z[:-1], z[1:], scene.zmin, scene.zmax, NBINS)
                for first, last, tint in run_lengths(bins):
                    r, g, b = bin_colors[tint]
                    self._geo.add(Color(float(r), float(g), float(b), 1.0))
                    line = Line(points=[0, 0, 0, 0], width=width, joint="none", cap="none")
                    self._geo.add(line)
                    self._runs.append((line, start + first, start + last + 1))
            else:
                rgba = UNIFORM_COLOR if mode == "uniform" else self._layer_colors.get(layer, UNIFORM_COLOR)
                self._geo.add(Color(*rgba))
                line = Line(points=[0, 0, 0, 0], width=width, joint="none", cap="none")
                self._geo.add(line)
                self._runs.append((line, start, start + count))

        if self.show_stations and len(scene.station_vertices):
            idx = [i for i, layer in enumerate(scene.station_layers) if layer in self.visible_layers]
            if idx:
                self._station_idx = np.array(idx, dtype=int)
                self._geo.add(Color(*STATION_COLOR))
                self._station_pt = Point(points=[0, 0], pointsize=dp(2.2))
                self._geo.add(self._station_pt)

        self._trigger()

    # ------------------------------------------------------------------ dessin
    def _redraw(self, *_):
        scene = self.scene
        width, height = self.size
        if scene is None or width < 2 or height < 2:
            self._update_hud()
            return
        cx, cy = self.center_x, self.center_y
        xy, _depth = self.camera.project(scene.vertices, cx, cy, width, height)
        for line, first, last in self._runs:
            line.points = xy[first:last].ravel().tolist()
        if self._station_pt is not None and len(self._station_idx):
            sxy, _d = self.camera.project(scene.station_vertices[self._station_idx], cx, cy, width, height)
            self._station_pt.points = sxy.ravel().tolist()
        self._update_hud()

    def _update_hud(self) -> None:
        group = self._hud
        group.clear()
        scene = self.scene
        labels = [*self._axis_labels, self._scale_label, self._legend_top, self._legend_bottom]
        if scene is None or self.width < 2 or self.height < 2:
            for label in labels:
                label.opacity = 0
            return

        width, height = self.size

        # Axes E / N / Z en bas à gauche
        for label in self._axis_labels:
            label.opacity = 1 if self.show_axes else 0
        if self.show_axes:
            ox, oy = self.x + dp(44), self.y + dp(44)
            length = dp(30)
            matrix = self.camera.view_matrix()
            for k, label in enumerate(self._axis_labels):
                ex = ox + length * matrix[0, k]
                ey = oy + length * matrix[2, k]
                group.add(Color(*AXIS_COLORS[k]))
                group.add(Line(points=[ox, oy, ex, ey], width=dp(1.4)))
                label.center = (ex + 0.35 * (ex - ox), ey + 0.35 * (ey - oy))

        # Barre d'échelle (dans le plan de l'écran) en bas à droite
        pixels_per_meter = self.camera.scale(width, height) / scene.radius
        nice = _nice_length(dp(90) / pixels_per_meter)
        bar = nice * pixels_per_meter
        x1 = self.right - dp(16)
        x0 = x1 - bar
        y = self.y + dp(26)
        group.add(Color(*HUD_TEXT))
        group.add(Line(points=[x0, y, x1, y], width=dp(1.2)))
        group.add(Line(points=[x0, y - dp(4), x0, y + dp(4)], width=dp(1.2)))
        group.add(Line(points=[x1, y - dp(4), x1, y + dp(4)], width=dp(1.2)))
        self._set_text(self._scale_label, _format_length(nice))
        self._scale_label.opacity = 1
        self._scale_label.center = ((x0 + x1) / 2.0, y + dp(14))

        # Légende du dégradé profondeur / altitude
        if self.color_mode in ("depth", "altitude"):
            strip_h = min(height * 0.35, dp(150))
            sx = self.right - dp(22)
            sy = self.center_y - strip_h / 2.0
            group.add(Color(1, 1, 1, 1))
            group.add(Rectangle(texture=self._legend_tex, pos=(sx, sy), size=(dp(10), strip_h)))
            if self.color_mode == "depth":
                top_text, bottom_text = "0 m", f"{scene.zmax - scene.zmin:.0f} m"
            else:
                top_text, bottom_text = f"{scene.zmax:.0f} m", f"{scene.zmin:.0f} m"
            self._set_text(self._legend_top, top_text)
            self._set_text(self._legend_bottom, bottom_text)
            self._legend_top.opacity = 1
            self._legend_bottom.opacity = 1
            self._legend_top.right = sx - dp(4)
            self._legend_top.top = sy + strip_h + dp(2)
            self._legend_bottom.right = sx - dp(4)
            self._legend_bottom.y = sy - dp(2)
        else:
            self._legend_top.opacity = 0
            self._legend_bottom.opacity = 0

    # ------------------------------------------------------------------ gestes
    def _snapshot(self) -> None:
        touches = list(self._touches.values())
        if len(touches) >= 2:
            (x1, y1), (x2, y2) = touches[0].pos, touches[1].pos
            self._prev_mid = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
            self._prev_dist = max(math.hypot(x2 - x1, y2 - y1), 1.0)
        else:
            self._prev_mid = None
            self._prev_dist = None

    def _two_finger_update(self) -> None:
        if self._prev_mid is None:
            self._snapshot()
            return
        touches = list(self._touches.values())[:2]
        (x1, y1), (x2, y2) = touches[0].pos, touches[1].pos
        mid = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
        dist = max(math.hypot(x2 - x1, y2 - y1), 1.0)
        self.camera.pan_by(mid[0] - self._prev_mid[0], mid[1] - self._prev_mid[1])
        self.camera.zoom_about(dist / self._prev_dist, mid[0] - self.center_x, mid[1] - self.center_y)
        self._prev_mid, self._prev_dist = mid, dist

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return False
        if touch.is_mouse_scrolling:
            factor = 1.15 if touch.button == "scrollup" else 1 / 1.15
            self.camera.zoom_about(factor, touch.x - self.center_x, touch.y - self.center_y)
            self._trigger()
            return True
        touch.grab(self)
        self._touches[touch.uid] = touch
        if touch.is_double_tap and len(self._touches) == 1:
            self.reset_view()
        self._snapshot()
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is not self or touch.uid not in self._touches:
            return False
        if len(self._touches) == 1:
            if self.one_finger_pan:
                self.camera.pan_by(touch.dx, touch.dy)
            else:
                self.camera.orbit(touch.dx, touch.dy)
        else:
            self._two_finger_update()
        self._trigger()
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self._touches.pop(touch.uid, None)
            self._snapshot()
            return True
        return False
