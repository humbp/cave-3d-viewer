"""Tests des modules sans Kivy : géométrie, parseurs, caméra."""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from geometry.colors import run_lengths, segment_bins
from geometry.graph import edges_to_chains
from geometry.model import ModelBuilder
from geometry.scene import build_scene
from geometry.transforms import Camera
from parsers import ParseError, load_model
from parsers.sample import build_sample_cave
from parsers.svg_parser import _items_fallback, parse_svg
from parsers.topo_text import parse_topo_text


# ---------------------------------------------------------------- exemple intégré
def test_sample_cave():
    model = build_sample_cave()
    s = model.stats()
    assert s["denivele"] > 60
    assert s["n_segments"] > 100
    assert {"puits", "galerie", "salle", "affluent", "galerie basse"} <= set(model.layer_names())
    scene = build_scene(model)
    assert np.abs(scene.vertices).max() <= 1.0 + 1e-9


# ---------------------------------------------------------------- topo texte
def test_topo_coordinates_connect_in_order():
    m = parse_topo_text("A 0 0 0\nB 10 0 -2\nC 10 10 -5\n")
    assert len(m.polylines) == 1 and len(m.polylines[0].points) == 3
    assert m.stats()["denivele"] == pytest.approx(5.0)


def test_topo_shots():
    # 10 m plein Est, puis 10 m plein Nord descendant de 90° -> verticale
    m = parse_topo_text("@unit m\nS0 S1 10 90 0\nS1 S2 10 0 -90\n")
    pts = m.polylines[0].points
    assert pts[1] - pts[0] == pytest.approx([10, 0, 0], abs=1e-9)
    assert pts[2] - pts[1] == pytest.approx([0, 0, -10], abs=1e-9)


def test_topo_shot_in_reverse_order_and_orphan():
    m = parse_topo_text("A B 5 0 0\nC A 5 90 0\nX Y 3 0 0\n")
    assert any("non rattachée" in w for w in m.warnings) or len(m.station_xyz) >= 3


def test_topo_comma_decimal_and_junk():
    m = parse_topo_text("# test\nA 0,5 0 0\nB 1,5 0 0\nbidon\nC x y z\n")
    assert m.polylines[0].points[1][0] == pytest.approx(1.5)
    assert m.warnings


def test_topo_empty_raises():
    with pytest.raises(ParseError):
        parse_topo_text("# rien\n\n")


def test_topo_unit_feet():
    m = parse_topo_text("@unit ft\nA 0 0 0\nB 10 0 0\n")
    assert m.polylines[0].points[1][0] == pytest.approx(3.048)


def test_topo_loop_closure_does_not_crash():
    m = parse_topo_text("A B 10 90 0\nB C 10 0 0\nC D 10 270 0\nD A 10 180 0\n")
    assert m.stats()["n_segments"] >= 4


# ---------------------------------------------------------------- graphe
def test_edges_to_chains_branch():
    chains = edges_to_chains([("a", "b"), ("b", "c"), ("b", "d"), ("a", "b")])
    assert sum(len(c) - 1 for c in chains) == 3


# ---------------------------------------------------------------- builder
def test_builder_filters_nan():
    b = ModelBuilder()
    b.add_polyline("x", [[0, 0, 0], [float("nan"), 1, 1], [2, 2, 2]])
    b.add_station("x", [float("inf"), 0, 0])
    m = b.build()
    assert len(m.polylines[0].points) == 2
    assert m.warnings


# ---------------------------------------------------------------- SVG (lecteur intégré)
SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
<path d="M 0 0 L 10 0 L 10 10 Z" stroke="red"/>
<path d="M 0 0 C 10 0 10 10 20 10" stroke="blue"/>
<rect x="0" y="0" width="5" height="5"/>
<circle cx="50" cy="50" r="10"/>
<path d="M 0 0 L bogus"/>
</svg>"""


def test_svg_fallback(tmp_path):
    f = tmp_path / "t.svg"
    f.write_text(SVG)
    items = _items_fallback(str(f), [])
    layers = {layer for layer, _ in items}
    assert "trait red" in layers and "trait blue" in layers
    assert len(items) >= 4


def test_svg_full_and_planes(tmp_path):
    f = tmp_path / "t.svg"
    f.write_text(SVG)
    m_xy = parse_svg(str(f), "XY", 2.0)
    m_xz = parse_svg(str(f), "XZ", 2.0)
    assert np.allclose(m_xy.all_points()[:, 2], 0)
    assert np.allclose(m_xz.all_points()[:, 1], 0)
    assert m_xy.all_points()[:, 0].max() >= 20


def test_svg_empty_raises(tmp_path):
    f = tmp_path / "e.svg"
    f.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    with pytest.raises(ParseError):
        parse_svg(str(f))


def test_svg_bad_scale(tmp_path):
    f = tmp_path / "t.svg"
    f.write_text(SVG)
    with pytest.raises(ParseError):
        parse_svg(str(f), scale=0)


# ---------------------------------------------------------------- DXF
try:
    import ezdxf
except ImportError:  # ezdxf absent : seuls les tests DXF sont sautés
    ezdxf = None

needs_ezdxf = pytest.mark.skipif(ezdxf is None, reason="ezdxf non installé")


@needs_ezdxf
def test_dxf_roundtrip(tmp_path):
    doc = ezdxf.new("R2010")
    doc.layers.add("GALERIE")
    doc.layers.add("STATIONS")
    msp = doc.modelspace()
    msp.add_polyline3d([(0, 0, 0), (10, 0, -2), (20, 5, -6)], dxfattribs={"layer": "GALERIE"})
    msp.add_line((0, 0, 0), (0, 0, -30), dxfattribs={"layer": "PUITS"})
    msp.add_lwpolyline([(0, 0), (5, 0), (5, 5)], dxfattribs={"layer": "GALERIE", "elevation": -1})
    msp.add_point((1, 2, 3), dxfattribs={"layer": "STATIONS"})
    msp.add_circle((0, 0, -3), 4, dxfattribs={"layer": "SALLE"})
    msp.add_text("hello")  # non supporté
    f = tmp_path / "t.dxf"
    doc.saveas(f)

    m = load_model(str(f))
    assert {"GALERIE", "PUITS", "SALLE", "STATIONS"} <= set(m.layer_names())
    assert m.stats()["denivele"] == pytest.approx(30.0)
    assert any("TEXT" in w for w in m.warnings)
    assert len(m.station_xyz) == 1


@needs_ezdxf
def test_dxf_garbage_raises(tmp_path):
    f = tmp_path / "bad.dxf"
    f.write_text("ceci n'est pas un DXF\n")
    with pytest.raises(ParseError):
        load_model(str(f))


@needs_ezdxf
def test_dxf_empty_modelspace_raises(tmp_path):
    doc = ezdxf.new()
    f = tmp_path / "empty.dxf"
    doc.saveas(f)
    with pytest.raises(ParseError):
        load_model(str(f))


# ---------------------------------------------------------------- chargeur
def test_loader_errors(tmp_path):
    with pytest.raises(ParseError):
        load_model(str(tmp_path / "absent.dxf"))
    empty = tmp_path / "vide.txt"
    empty.write_text("")
    with pytest.raises(ParseError):
        load_model(str(empty))
    dwg = tmp_path / "x.dwg"
    dwg.write_bytes(b"AC1018")
    with pytest.raises(ParseError):
        load_model(str(dwg))


def test_loader_txt(tmp_path):
    f = tmp_path / "cave.txt"
    f.write_text("@name Test\nA 0 0 0\nB 5 5 -5\n")
    m = load_model(str(f))
    assert m.name == "Test" and m.source == "cave.txt"


# ---------------------------------------------------------------- couleurs / caméra
def test_color_bins_and_runs():
    z = np.array([0.0, -1.0, -2.0, -3.0, -4.0])
    bins = segment_bins(z[:-1], z[1:], -4.0, 0.0, 4)
    assert list(bins) == [0, 1, 2, 3]
    runs = run_lengths(np.array([0, 0, 1, 1, 1, 2]))
    assert runs == [(0, 2, 0), (2, 5, 1), (5, 6, 2)]
    assert segment_bins(np.array([1.0]), np.array([1.0]), 1.0, 1.0)[0] == 0


def test_camera_plan_and_section():
    cam = Camera()
    p = np.array([[1.0, 2.0, 3.0]])
    cam.set_plan()
    xy, _ = cam.project(p, 0, 0, 1000, 1000)
    s = cam.scale(1000, 1000)
    assert xy[0] == pytest.approx([s * 1.0, s * 2.0])  # x = Est, y = Nord
    cam.set_section()
    xy, _ = cam.project(p, 0, 0, 1000, 1000)
    assert xy[0] == pytest.approx([s * 1.0, s * 3.0])  # y écran = Z


def test_camera_zoom_anchor_is_fixed():
    cam = Camera()
    cam.reset()
    p = np.array([[0.3, 0.1, 0.2]])
    before, _ = cam.project(p, 0, 0, 800, 600)
    cam.zoom_about(2.0, before[0][0], before[0][1])
    after, _ = cam.project(p, 0, 0, 800, 600)
    assert after[0] == pytest.approx(before[0], abs=1e-6)


def test_camera_limits():
    cam = Camera()
    cam.zoom_about(1e9, 0, 0)
    assert cam.zoom == Camera.MAX_ZOOM
    cam.zoom_about(float("nan"), 0, 0)
    assert cam.zoom == Camera.MAX_ZOOM
    cam.orbit(0, -1e6)
    assert abs(cam.tilt) <= math.pi / 2 + 1e-12
