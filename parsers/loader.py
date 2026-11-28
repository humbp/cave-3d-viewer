"""Détection du format et chargement d'un fichier de cavité."""
from __future__ import annotations

import os

from geometry.model import CaveModel

from .errors import ParseError

_BY_EXTENSION = {
    ".dxf": "dxf",
    ".svg": "svg",
    ".txt": "topo",
    ".top": "topo",
    ".dat": "topo",
    ".xyz": "topo",
    ".tab": "topo",
}

SUPPORTED_EXTENSIONS = tuple(_BY_EXTENSION)


def detect_format(path: str) -> str:
    """Renvoie « dxf », « svg » ou « topo » (extension d'abord, contenu ensuite)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _BY_EXTENSION:
        return _BY_EXTENSION[ext]
    if ext == ".dwg":
        raise ParseError("Le format DWG n'est pas pris en charge : exportez le plan en DXF.")
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096).lower()
    except OSError as exc:
        raise ParseError(f"Impossible de lire le fichier : {exc}") from exc
    if b"<svg" in head:
        return "svg"
    if b"section" in head and (b"header" in head or b"entities" in head or b"tables" in head):
        return "dxf"
    return "topo"


def load_model(path: str, svg_plane: str = "XY", svg_scale: float = 1.0) -> CaveModel:
    """Charge un fichier et renvoie un ``CaveModel`` (lève ``ParseError`` si inexploitable)."""
    if not os.path.isfile(path):
        raise ParseError(f"Fichier introuvable : {path}")
    if os.path.getsize(path) == 0:
        raise ParseError("Le fichier est vide.")

    fmt = detect_format(path)
    if fmt == "dxf":
        from .dxf_parser import parse_dxf

        model = parse_dxf(path)
    elif fmt == "svg":
        from .svg_parser import parse_svg

        model = parse_svg(path, plane=svg_plane, scale=svg_scale)
    else:
        from .topo_text import parse_topo_file

        model = parse_topo_file(path)

    model.source = os.path.basename(path)
    return model
