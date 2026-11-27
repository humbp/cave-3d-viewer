"""Lecteur de topographie spéléo au format texte simple.

Une instruction par ligne ; champs séparés par espaces, tabulations ou « ; ».

    # commentaire   (ou  // commentaire)
    @name  Nom de la cavité
    @unit  m | cm | mm | ft | km          (défaut : m)
    @layer nom_du_calque                  (valable pour les lignes suivantes)

    X Y Z                        point isolé
    NOM X Y Z                    station avec coordonnées
    NOM_A NOM_B                  liaison entre deux stations connues
    DE VERS LONG AZIMUT PENTE    visée : azimut en degrés depuis le Nord (sens horaire),
                                 pente en degrés (+ vers le haut)

Les colonnes supplémentaires d'une visée (gauche, droite, haut, bas...) sont ignorées.
Sans visée ni liaison, les stations consécutives d'un même calque sont reliées dans
l'ordre du fichier. La virgule décimale est acceptée.

Les visées sont calculées sans compensation des fermetures de boucle : la première
position calculée d'une station est conservée.
"""
from __future__ import annotations

import math
import os
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np

from geometry.graph import edges_to_chains
from geometry.model import CaveModel, ModelBuilder

from .errors import ParseError

DEFAULT_LAYER = "topo"
UNITS = {"m": 1.0, "cm": 0.01, "mm": 0.001, "km": 1000.0, "ft": 0.3048}
_COMMENT_RE = re.compile(r"(#|//).*$")


def _num(token: str) -> Optional[float]:
    try:
        value = float(token.replace(",", "."))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _shot_vector(length: float, azimuth_deg: float, inclination_deg: float) -> np.ndarray:
    az = math.radians(azimuth_deg)
    inc = math.radians(inclination_deg)
    horizontal = length * math.cos(inc)
    return np.array([horizontal * math.sin(az), horizontal * math.cos(az), length * math.sin(inc)])


def parse_topo_text(text: str, name: str = "Topographie") -> CaveModel:
    builder = ModelBuilder(name)
    unit = 1.0
    layer = DEFAULT_LAYER
    text = text.lstrip("﻿")

    pos: Dict[str, np.ndarray] = {}
    st_layer: Dict[str, str] = {}
    coord_order: List[Tuple[str, str]] = []
    shots: List[Tuple[int, str, str, np.ndarray, str]] = []
    links: List[Tuple[int, str, str, str]] = []
    free_points: List[Tuple[np.ndarray, str]] = []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = _COMMENT_RE.sub("", raw).strip()
        if not line:
            continue

        if line.startswith("@"):
            parts = line[1:].split(None, 1)
            key = parts[0].lower() if parts else ""
            arg = parts[1].strip() if len(parts) > 1 else ""
            if key in ("name", "nom", "titre", "title"):
                builder.name = arg or builder.name
            elif key in ("layer", "calque"):
                layer = arg or layer
            elif key in ("unit", "unite", "unité"):
                factor = UNITS.get(arg.lower())
                if factor is None:
                    builder.warn(f"Ligne {lineno} : unité «{arg}» inconnue (m, cm, mm, km, ft).")
                else:
                    unit = factor
            else:
                builder.warn(f"Ligne {lineno} : directive «@{key}» inconnue, ignorée.")
            continue

        tokens = line.replace(";", " ").split()
        count = len(tokens)
        if count == 2:
            links.append((lineno, tokens[0], tokens[1], layer))
        elif count == 3:
            values = [_num(t) for t in tokens]
            if None in values:
                builder.warn(f"Ligne {lineno} ignorée : 3 champs dont certains ne sont pas numériques.")
            else:
                free_points.append((np.array(values, dtype=float) * unit, layer))
        elif count == 4:
            values = [_num(t) for t in tokens[1:]]
            if None in values:
                builder.warn(f"Ligne {lineno} ignorée : coordonnées non numériques pour «{tokens[0]}».")
            else:
                pos[tokens[0]] = np.array(values, dtype=float) * unit
                st_layer[tokens[0]] = layer
                coord_order.append((tokens[0], layer))
        elif count >= 5:
            values = [_num(t) for t in tokens[2:5]]
            if None in values:
                builder.warn(f"Ligne {lineno} ignorée : visée non numérique ({tokens[0]} → {tokens[1]}).")
            else:
                length, azimuth, inclination = values
                shots.append((lineno, tokens[0], tokens[1], _shot_vector(length * unit, azimuth, inclination), layer))
        else:
            builder.warn(f"Ligne {lineno} ignorée : un seul champ.")

    # ---- calcul des positions des stations à partir des visées -------------
    edges: List[Tuple[str, str, str]] = []

    def resolve(pending):
        progress = True
        while pending and progress:
            progress = False
            rest = []
            for shot in pending:
                _ln, a, b, vec, lay = shot
                if a in pos:
                    if b not in pos:
                        pos[b] = pos[a] + vec
                        st_layer[b] = lay
                    edges.append((a, b, lay))
                    progress = True
                elif b in pos:
                    pos[a] = pos[b] - vec
                    st_layer[a] = lay
                    edges.append((a, b, lay))
                    progress = True
                else:
                    rest.append(shot)
            pending = rest
        return pending

    pending = resolve(shots)
    while pending and not pos:
        _ln, start, _b, _vec, lay = pending[0]
        pos[start] = np.zeros(3)
        st_layer[start] = lay
        builder.warn(f"Aucune station de départ : «{start}» placée à l'origine (0, 0, 0).")
        pending = resolve(pending)
    for ln, a, b, _vec, _lay in pending:
        builder.warn(f"Ligne {ln} : visée {a} → {b} non rattachée au réseau, ignorée.")

    for ln, a, b, lay in links:
        if a in pos and b in pos:
            edges.append((a, b, lay))
        else:
            builder.warn(f"Ligne {ln} : liaison {a} – {b} ignorée (station inconnue).")

    # ---- construction du modèle --------------------------------------------
    if not shots and not links and len(coord_order) >= 2:
        groups: Dict[str, List[np.ndarray]] = defaultdict(list)
        for station, lay in coord_order:
            groups[lay].append(pos[station])
        for lay, pts in groups.items():
            if len(pts) >= 2:
                builder.add_polyline(lay, pts)
    else:
        by_layer: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        for a, b, lay in edges:
            by_layer[lay].append((a, b))
        for lay, layer_edges in by_layer.items():
            for chain in edges_to_chains(layer_edges):
                builder.add_polyline(lay, [pos[n] for n in chain])

    for station, xyz in pos.items():
        builder.add_station(st_layer.get(station, DEFAULT_LAYER), xyz, station)
    for xyz, lay in free_points:
        builder.add_station(lay, xyz, "")

    model = builder.build()
    if model.is_empty:
        raise ParseError(
            "Aucune donnée exploitable dans ce fichier texte. Formats attendus : "
            "«NOM X Y Z», «DE VERS LONG AZIMUT PENTE» ou «X Y Z»."
        )
    return model


def parse_topo_file(path: str) -> CaveModel:
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ParseError(f"Impossible de lire le fichier : {exc}") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    if not text.strip():
        raise ParseError("Le fichier est vide.")
    default_name = os.path.splitext(os.path.basename(path))[0] or "Topographie"
    return parse_topo_text(text, default_name)
