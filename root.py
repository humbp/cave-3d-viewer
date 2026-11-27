"""Écran principal : barre d'outils, vue 3D, panneau d'informations, barre d'état."""
from __future__ import annotations

import os
import threading
from typing import Optional

from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.logger import Logger
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from geometry.model import CaveModel
from geometry.scene import Scene, build_scene
from parsers import ParseError, detect_format, load_model
from parsers.sample import build_sample_cave
from rendering.cave_view import CaveView

from . import dialogs
from .file_picker import is_android, pick_file_android

COLOR_MODES = ("depth", "altitude", "layer", "uniform")
COLOR_LABELS = {"depth": "Profondeur", "altitude": "Altitude", "layer": "Calques", "uniform": "Uniforme"}
MAX_WARNINGS_SHOWN = 3


class InfoPanel(Label):
    """Petit panneau semi-transparent affiché au-dessus de la vue 3D."""

    def __init__(self, **kwargs):
        super().__init__(size_hint=(None, None), halign="left", valign="top",
                         font_size=sp(12), text_size=(dp(330), None), **kwargs)
        with self.canvas.before:
            Color(0, 0, 0, 0.55)
            self._rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync,
                  texture_size=lambda inst, ts: setattr(inst, "size", (ts[0] + dp(16), ts[1] + dp(12))))

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size


class RootLayout(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", **kwargs)
        self.model: Optional[CaveModel] = None
        self.scene: Optional[Scene] = None

        self._build_toolbar()

        body = FloatLayout()
        self.view = CaveView(size_hint=(1, 1))
        body.add_widget(self.view)
        self.info = InfoPanel(text="", pos_hint={"x": 0.01, "top": 0.99})
        body.add_widget(self.info)
        self.add_widget(body)

        self.status = Label(text="Prêt", size_hint_y=None, height=dp(26), font_size=sp(12),
                            halign="left", valign="middle", shorten=True, shorten_from="right")
        self.status.bind(size=lambda inst, size: setattr(inst, "text_size", (size[0] - dp(12), size[1])))
        self.add_widget(self.status)
        self._update_button_labels()  # la vue existe maintenant : libellés des boutons à jour

    # ------------------------------------------------------------------ barre d'outils
    def _build_toolbar(self) -> None:
        scroll = ScrollView(size_hint=(1, None), height=dp(54), do_scroll_x=True, do_scroll_y=False, bar_width=0)
        row = BoxLayout(size_hint=(None, 1), spacing=dp(4), padding=dp(4))

        def button(text, callback, width=dp(92)):
            btn = Button(text=text, size_hint=(None, 1), width=width, font_size=sp(12),
                         halign="center", valign="middle")
            btn.bind(size=lambda inst, size: setattr(inst, "text_size", size))
            btn.bind(on_release=callback)
            row.add_widget(btn)
            return btn

        button("Ouvrir", self.open_file)
        button("Exemple", self.load_sample)
        button("Calques", self.show_layers)
        self.color_btn = button("", self.cycle_color_mode)
        button("Plan", lambda *_: self.view.view_plan())
        button("Coupe", lambda *_: self.view.view_section())
        button("Vue 3D", lambda *_: self.view.reset_view())
        self.pan_btn = button("", self.toggle_pan_mode)
        self.stations_btn = button("", self.toggle_stations)
        button("Infos", self.toggle_info)

        count = len(row.children)
        row.width = count * dp(92) + (count - 1) * dp(4) + dp(8)
        scroll.add_widget(row)
        self.add_widget(scroll)
        self._update_button_labels()

    def _update_button_labels(self) -> None:
        self.color_btn.text = f"Couleur\n{COLOR_LABELS[self.view.color_mode]}" if hasattr(self, "view") else "Couleur"
        if hasattr(self, "view"):
            self.pan_btn.text = "1 doigt\n" + ("panoramique" if self.view.one_finger_pan else "rotation")
            self.stations_btn.text = "Stations\n" + ("oui" if self.view.show_stations else "non")

    # ------------------------------------------------------------------ actions
    def toggle_info(self, *_):
        self.info.opacity = 0 if self.info.opacity else 1

    def toggle_pan_mode(self, *_):
        self.view.one_finger_pan = not self.view.one_finger_pan
        self._update_button_labels()

    def toggle_stations(self, *_):
        self.view.show_stations = not self.view.show_stations
        self._update_button_labels()

    def cycle_color_mode(self, *_):
        index = COLOR_MODES.index(self.view.color_mode)
        self.view.color_mode = COLOR_MODES[(index + 1) % len(COLOR_MODES)]
        self._update_button_labels()

    def show_layers(self, *_):
        if self.scene is None:
            dialogs.show_message("Calques", "Aucune cavité n'est chargée.")
            return
        dialogs.show_layers(self.scene.layers, self.view.visible_layers, self.view.layer_color_map(),
                            self._on_layers_changed)

    def _on_layers_changed(self, visible) -> None:
        self.view.set_visible_layers(visible)
        self._refresh_info()

    def close_popups(self) -> bool:
        return dialogs.close_top_popup()

    # ------------------------------------------------------------------ chargement
    def load_sample(self, *_):
        try:
            model = build_sample_cave()
            model.source = "exemple intégré"
            self._loaded(model, build_scene(model))
        except Exception as exc:
            Logger.exception("CaveViewer: exemple intégré en échec")
            dialogs.show_message("Erreur", f"Impossible de construire l'exemple : {exc}")

    def open_file(self, *_):
        if is_android():
            cache_dir = os.path.join(App.get_running_app().user_data_dir, "imports")
            pick_file_android(self._on_picked, self._on_picker_failed, cache_dir)
        else:
            dialogs.show_file_chooser(self.load_path)

    def _on_picked(self, path: Optional[str]) -> None:
        if path:
            self.load_path(path)

    def _on_picker_failed(self, message: str) -> None:
        # Secours : explorateur interne (utile si le sélecteur système est absent)
        dialogs.show_message("Sélecteur système indisponible", message)
        dialogs.show_file_chooser(self.load_path)

    def load_path(self, path: str) -> None:
        try:
            fmt = detect_format(path)
        except ParseError as exc:
            dialogs.show_message("Format non supporté", str(exc))
            return
        if fmt == "svg":
            dialogs.show_svg_options(lambda plane, scale: self._load_async(path, plane, scale))
        else:
            self._load_async(path)

    def _load_async(self, path: str, plane: str = "XY", scale: float = 1.0) -> None:
        self.status.text = f"Chargement de {os.path.basename(path)}…"

        def work():
            try:
                model = load_model(path, svg_plane=plane, svg_scale=scale)
                scene = build_scene(model)
            except ParseError as exc:
                message = str(exc)
                Clock.schedule_once(lambda dt, m=message: self._load_failed(m), 0)
                return
            except Exception as exc:
                Logger.exception("CaveViewer: erreur de chargement")
                message = f"Erreur inattendue pendant la lecture : {type(exc).__name__}: {exc}"
                Clock.schedule_once(lambda dt, m=message: self._load_failed(m), 0)
                return
            Clock.schedule_once(lambda dt: self._loaded(model, scene), 0)

        threading.Thread(target=work, daemon=True).start()

    def _load_failed(self, message: str) -> None:
        self.status.text = "Échec du chargement"
        dialogs.show_message("Chargement impossible", message)

    def _loaded(self, model: CaveModel, scene: Scene) -> None:
        self.model = model
        self.scene = scene
        self.view.set_scene(scene)
        self._refresh_info()
        extra = f" · {len(model.warnings)} avertissement(s)" if model.warnings else ""
        self.status.text = f"{model.source or model.name}{extra}"

    # ------------------------------------------------------------------ informations
    def _refresh_info(self) -> None:
        model, scene = self.model, self.scene
        if model is None or scene is None:
            self.info.text = ""
            return
        s = model.stats()
        lines = [
            model.name,
            f"Étendue  X {s['extent_x']:.1f} m · Y {s['extent_y']:.1f} m · Z {s['extent_z']:.1f} m",
            f"Dénivelé total {s['denivele']:.1f} m (z {s['zmax']:.1f} à {s['zmin']:.1f})",
            f"Développement {s['development']:.0f} m",
            f"Sommets {s['n_vertices']} · Segments {s['n_segments']} · Stations {s['n_stations']}",
            f"Calques {len(self.view.visible_layers)}/{len(scene.layers)} visibles",
        ]
        if model.warnings:
            lines.append(f"Avertissements ({len(model.warnings)}) :")
            lines.extend("- " + w for w in model.warnings[:MAX_WARNINGS_SHOWN])
            if len(model.warnings) > MAX_WARNINGS_SHOWN:
                lines.append(f"- … et {len(model.warnings) - MAX_WARNINGS_SHOWN} autre(s)")
        self.info.text = "\n".join(lines)
