# SPDX-License-Identifier: MIT
"""Tests for the mesh generator.

The point of these is simple: a broken triangulator does not crash, it quietly
produces a part with a hole in the wrong place, and you find out four hours
into a print. So every part is checked for watertightness, and the primitives
are checked against their analytic volumes.
"""

from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gen_stl import PARTS  # noqa: E402
from meshlib import (  # noqa: E402
    Mesh,
    circle,
    dedupe,
    earclip,
    ensure_ccw,
    ensure_cw,
    merge_holes,
    rect,
    rounded_rect,
    signed_area,
    stadium,
)


# --------------------------------------------------------------------------
# 2D helpers
# --------------------------------------------------------------------------

def test_winding_helpers():
    square = rect(0, 0, 10, 10)
    assert signed_area(square) > 0
    assert ensure_ccw(square) == square
    assert signed_area(ensure_cw(square)) < 0


def test_dedupe_removes_repeats_and_the_closing_point():
    assert dedupe([(0, 0), (0, 0), (1, 0), (1, 1), (0, 0)]) == [(0, 0), (1, 0), (1, 1)]


def test_fully_rounded_rect_has_no_zero_length_segments():
    """radius == half the height leaves zero-length straights between arcs."""
    poly = dedupe(rounded_rect(0, 0, 60, 14, 7))
    for i, point in enumerate(poly):
        nxt = poly[(i + 1) % len(poly)]
        assert math.dist(point, nxt) > 1e-9


def test_earclip_triangle_count():
    poly = rect(0, 0, 4, 4)
    assert len(earclip(poly)) == len(poly) - 2


def test_merge_holes_produces_a_closed_ring():
    outer = rect(0, 0, 40, 40)
    holes = [circle(10, 10, 3, 16), circle(30, 30, 3, 16)]
    merged = merge_holes(outer, holes)
    # 4 outer + each hole contributes its ring plus two duplicated bridge ends.
    assert len(merged) == 4 + 2 * (16 + 1) + 2


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------

def test_box_volume_and_manifold():
    mesh = Mesh()
    mesh.add_box(0, 0, 0, 10, 20, 3)
    assert mesh.check_manifold() == []
    assert mesh.volume_cm3() == pytest.approx(0.6)
    assert mesh.size() == pytest.approx((10, 20, 3))


def test_tube_volume_matches_the_analytic_value():
    mesh = Mesh()
    mesh.add_tube(0, 0, 0, 10, 15, 5, segments=128)
    ideal = math.pi * (15**2 - 5**2) * 10 / 1000
    assert mesh.check_manifold() == []
    assert mesh.volume_cm3() == pytest.approx(ideal, rel=0.002)


def test_rectangular_window_volume_is_exact():
    mesh = Mesh()
    mesh.add_extrusion(rect(0, 0, 60, 40), 0, 2.4, holes=[rect(10, 10, 50, 30)])
    assert mesh.check_manifold() == []
    assert mesh.volume_cm3() == pytest.approx((60 * 40 - 40 * 20) * 2.4 / 1000)


def test_dense_hole_grid_stays_watertight():
    """The case that broke nearest-vertex bridging: 19 closely packed holes."""
    holes = [circle(6 + c * 6, 6 + r * 6, 2.0, 24) for r in range(3) for c in range(5)]
    holes += [circle(x, y, 1.2, 24) for x in (3, 33) for y in (3, 21)]
    mesh = Mesh()
    mesh.add_extrusion(rounded_rect(0, 0, 36, 24, 2), 0, 2.0, holes=holes)
    assert mesh.check_manifold() == []
    ideal = (36 * 24 - 15 * math.pi * 4 - 4 * math.pi * 1.44) * 2.0 / 1000
    assert mesh.volume_cm3() == pytest.approx(ideal, rel=0.01)


def test_belt_loop_is_watertight():
    mesh = Mesh()
    mesh.add_extrusion(stadium(90, 20, 16, 32), 0, 1.6, holes=[stadium(84, 20, 13, 32)])
    assert mesh.check_manifold() == []


def test_touching_solids_are_validated_separately():
    """Two boxes sharing a face is legal input for a slicer, not an error."""
    mesh = Mesh()
    mesh.add_box(0, 0, 0, 10, 10, 5)
    mesh.add_box(0, 0, 5, 10, 10, 10)  # sits exactly on top
    assert mesh.check_manifold() == []
    assert len(mesh.solids) == 2


# --------------------------------------------------------------------------
# The actual robot parts
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(PARTS))
def test_every_part_is_watertight(name):
    mesh = PARTS[name][0]()
    assert mesh.check_manifold() == [], f"{name} is not printable"


@pytest.mark.parametrize("name", sorted(PARTS))
def test_every_part_fits_a_180mm_bed(name):
    """A part that does not fit a common 180 x 180 bed is a design bug."""
    width, depth, height = PARTS[name][0]().size()
    assert width <= 180 and depth <= 180, f"{name} is {width:.0f} x {depth:.0f} mm"
    assert height <= 180


@pytest.mark.parametrize("name", sorted(PARTS))
def test_every_part_has_positive_volume(name):
    assert PARTS[name][0]().volume_cm3() > 0.1


def test_stl_is_written_in_valid_binary_format(tmp_path):
    mesh = PARTS["eye_pod"][0]()
    path = tmp_path / "eye_pod.stl"
    mesh.write_stl(path)

    data = path.read_bytes()
    assert len(data) == 84 + 50 * len(mesh.triangles)
    (count,) = struct.unpack("<I", data[80:84])
    assert count == len(mesh.triangles)
    # First facet normal must be a unit vector.
    nx, ny, nz = struct.unpack("<3f", data[84:96])
    assert math.sqrt(nx * nx + ny * ny + nz * nz) == pytest.approx(1.0, abs=1e-5)
