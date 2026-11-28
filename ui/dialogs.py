"""Boîtes de dialogue : messages, calques, sélecteur de fichier de secours, options SVG."""
from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional, Sequence, Set

from kivy.graphics import Color, Rectangle
from kivy.metrics import dp, sp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton
from kivy.uix.widget import Widget
from kivy.utils import platform

from parsers.loader import SUPPORTED_EXTENSIONS

_OPEN_POPUPS: List[Popup] = []


def _track(popup: Popup) -> Popup:
    _OPEN_POPUPS.append(popup)
    popup.bind(on_dismiss=lambda p: _OPEN_POPUPS.remove(p) if p in _OPEN_POPUPS else None)
    popup.open()
    return popup


def close_top_popup() -> bool:
    """Ferme la boîte de dialogue la plus récente (bouton Retour Android). True si fermée."""
    if _OPEN_POPUPS:
        _OPEN_POPUPS[-1].dismiss()
        return True
    return False


def show_message(title: str, text: str) -> Popup:
    content = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
    scroll = ScrollView()
    label = Label(text=text, size_hint_y=None, halign="left", valign="top", font_size=sp(14))
    label.bind(width=lambda inst, w: setattr(inst, "text_size", (w, None)))
    label.bind(texture_size=lambda inst, ts: setattr(inst, "height", ts[1]))
    scroll.add_widget(label)
    close = Button(text="Fermer", size_hint_y=None, height=dp(44))
    content.add_widget(scroll)
    content.add_widget(close)
    popup = Popup(title=title, content=content, size_hint=(0.92, 0.6))
    close.bind(on_release=popup.dismiss)
    return _track(popup)


class _Swatch(AnchorLayout):
    """Pastille de couleur d'un calque."""

    def __init__(self, rgba, **kwargs):
        super().__init__(size_hint_x=None, width=dp(30), **kwargs)
        dot = Widget(size_hint=(None, None), size=(dp(20), dp(20)))
        with dot.canvas:
            Color(*rgba)
            rect = Rectangle(pos=dot.pos, size=dot.size)
        dot.bind(pos=lambda inst, v: setattr(rect, "pos", v), size=lambda inst, v: setattr(rect, "size", v))
        self.add_widget(dot)


def show_layers(layers: Sequence[str], visible: Set[str], colors: Dict[str, tuple],
                on_change: Callable[[Set[str]], None]) -> Popup:
    state = set(visible)
    checks: Dict[str, CheckBox] = {}

    content = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))
    grid = GridLayout(cols=1, size_hint_y=None, spacing=dp(4))
    grid.bind(minimum_height=grid.setter("height"))

    for name in layers:
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        box = CheckBox(active=name in state, size_hint_x=None, width=dp(44))

        def toggled(inst, value, layer=name):
            if value:
                state.add(layer)
            else:
                state.discard(layer)
            on_change(set(state))

        box.bind(active=toggled)
        checks[name] = box
        row.add_widget(box)
        row.add_widget(_Swatch(colors.get(name, (1, 1, 1, 1))))
        row.add_widget(Label(text=name, halign="left", valign="middle", shorten=True,
                             text_size=(None, None), font_size=sp(15)))
        grid.add_widget(row)

    scroll = ScrollView()
    scroll.add_widget(grid)

    buttons = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
    all_btn = Button(text="Tout")
    none_btn = Button(text="Aucun")
    close_btn = Button(text="Fermer")
    all_btn.bind(on_release=lambda *_: [setattr(c, "active", True) for c in checks.values()])
    none_btn.bind(on_release=lambda *_: [setattr(c, "active", False) for c in checks.values()])
    for button in (all_btn, none_btn, close_btn):
        buttons.add_widget(button)

    content.add_widget(scroll)
    content.add_widget(buttons)
    popup = Popup(title="Calques", content=content, size_hint=(0.85, 0.75))
    close_btn.bind(on_release=popup.dismiss)
    return _track(popup)


def _start_directory() -> str:
    candidates = ["/storage/emulated/0", "/sdcard"] if platform == "android" else []
    candidates.append(os.path.expanduser("~"))
    for path in candidates:
        if os.path.isdir(path):
            return path
    return os.getcwd()


def show_file_chooser(on_select: Callable[[str], None]) -> Popup:
    """Sélecteur de fichier interne (bureau, ou secours si le sélecteur Android échoue)."""
    extensions = SUPPORTED_EXTENSIONS

    def accept(folder, filename):
        return filename.lower().endswith(extensions)

    content = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
    chooser = FileChooserListView(path=_start_directory(), filters=[accept])
    buttons = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
    open_btn = Button(text="Ouvrir")
    cancel_btn = Button(text="Annuler")
    buttons.add_widget(open_btn)
    buttons.add_widget(cancel_btn)
    content.add_widget(chooser)
    content.add_widget(buttons)
    popup = Popup(title="Choisir un fichier (DXF, SVG, texte)", content=content, size_hint=(0.95, 0.9))

    def submit(*_):
        if chooser.selection:
            path = chooser.selection[0]
            if os.path.isfile(path):
                popup.dismiss()
                on_select(path)

    open_btn.bind(on_release=submit)
    chooser.bind(on_submit=submit)
    cancel_btn.bind(on_release=popup.dismiss)
    return _track(popup)


def show_svg_options(on_ok: Callable[[str, float], None]) -> Popup:
    content = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
    content.add_widget(Label(text="Un SVG est plat : où le placer ?", size_hint_y=None, height=dp(28)))

    modes = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
    plan = ToggleButton(text="Plan (X, Y)", group="svg_plane", state="down", allow_no_selection=False)
    section = ToggleButton(text="Coupe (X, Z)", group="svg_plane", allow_no_selection=False)
    modes.add_widget(plan)
    modes.add_widget(section)
    content.add_widget(modes)

    content.add_widget(Label(text="Échelle : mètres par unité SVG", size_hint_y=None, height=dp(28)))
    scale_input = TextInput(text="1.0", multiline=False, input_filter="float",
                            size_hint_y=None, height=dp(44))
    content.add_widget(scale_input)

    buttons = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
    ok_btn = Button(text="Charger")
    cancel_btn = Button(text="Annuler")
    buttons.add_widget(ok_btn)
    buttons.add_widget(cancel_btn)
    content.add_widget(buttons)

    popup = Popup(title="Options SVG", content=content, size_hint=(0.9, None), height=dp(310))

    def confirm(*_):
        try:
            scale = float(scale_input.text.replace(",", "."))
        except ValueError:
            scale = 1.0
        if scale <= 0:
            scale = 1.0
        popup.dismiss()
        on_ok("XZ" if section.state == "down" else "XY", scale)

    ok_btn.bind(on_release=confirm)
    cancel_btn.bind(on_release=popup.dismiss)
    return _track(popup)
