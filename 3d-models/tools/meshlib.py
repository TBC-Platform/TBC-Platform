# SPDX-License-Identifier: MIT
"""A very small solid-modelling library, standard library only.

Why this exists: the canonical, editable models in this repo are the OpenSCAD
files in ``3d-models/src``. But not everyone has OpenSCAD installed, and a
repository that promises "3D print files" should contain actual STLs. This
module generates them.

It is deliberately not a CSG engine. Instead of subtracting shapes, parts are
built as a **union of individually watertight solids**, and holes are modelled
directly into the geometry by extruding polygons that have holes in them. Every
slicer unions overlapping solids correctly, so this produces clean prints
without needing boolean operations.

Primitives:

* :meth:`Mesh.add_box`        - axis-aligned box
* :meth:`Mesh.add_cylinder`   - solid cylinder along Z
* :meth:`Mesh.add_tube`       - cylinder with a concentric hole (a real hole)
* :meth:`Mesh.add_extrusion`  - any 2D polygon, with any number of holes,
                                extruded along Z

The extruder triangulates with ear clipping after bridging each hole into the
outer contour, which is the standard approach and handles everything this
project needs (round bolt holes, rectangular display windows, belt loops).

:meth:`Mesh.check_manifold` verifies that every edge is shared by exactly two
triangles - the property a slicer needs - so a broken part fails here rather
than halfway through a four hour print.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

Point2 = tuple[float, float]
Point3 = tuple[float, float, float]
Triangle = tuple[Point3, Point3, Point3]


# ---------------------------------------------------------------------------
# 2D helpers
# ---------------------------------------------------------------------------

def signed_area(poly: list[Point2]) -> float:
    """Positive for counter-clockwise winding."""
    total = 0.0
    for i, (x0, y0) in enumerate(poly):
        x1, y1 = poly[(i + 1) % len(poly)]
        total += x0 * y1 - x1 * y0
    return total / 2.0


def dedupe(poly: list[Point2], eps: float = 1e-7) -> list[Point2]:
    """Drops consecutive duplicate points (including a repeated first/last).

    A fully-rounded rectangle - corner radius exactly half the height - has
    zero-length straight segments between its arcs, and those produce
    degenerate triangles that some slicers reject outright.
    """
    out: list[Point2] = []
    for point in poly:
        if not out or abs(point[0] - out[-1][0]) > eps or abs(point[1] - out[-1][1]) > eps:
            out.append(point)
    while len(out) > 1 and abs(out[0][0] - out[-1][0]) <= eps and abs(out[0][1] - out[-1][1]) <= eps:
        out.pop()
    return out


def ensure_ccw(poly: list[Point2]) -> list[Point2]:
    return poly if signed_area(poly) > 0 else poly[::-1]


def ensure_cw(poly: list[Point2]) -> list[Point2]:
    return poly if signed_area(poly) < 0 else poly[::-1]


def circle(cx: float, cy: float, r: float, segments: int = 32) -> list[Point2]:
    """A CCW circle. 32 segments is smooth enough that a 0.4 mm nozzle cannot
    tell, and keeps the STLs small."""
    return [
        (cx + r * math.cos(2 * math.pi * i / segments),
         cy + r * math.sin(2 * math.pi * i / segments))
        for i in range(segments)
    ]


def rect(x0: float, y0: float, x1: float, y1: float) -> list[Point2]:
    """A CCW rectangle from two opposite corners."""
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def rounded_rect(x0: float, y0: float, x1: float, y1: float, radius: float,
                 segments: int = 8) -> list[Point2]:
    """CCW rectangle with rounded corners - the outline most printed parts want,
    because sharp corners peel off the bed."""
    radius = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
    if radius <= 0:
        return rect(x0, y0, x1, y1)
    points: list[Point2] = []
    corners = [
        (x1 - radius, y0 + radius, -90.0),
        (x1 - radius, y1 - radius, 0.0),
        (x0 + radius, y1 - radius, 90.0),
        (x0 + radius, y0 + radius, 180.0),
    ]
    for cx, cy, start in corners:
        for i in range(segments + 1):
            angle = math.radians(start + 90.0 * i / segments)
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def stadium(length: float, width: float, radius: float, segments: int = 24) -> list[Point2]:
    """A closed racetrack outline, centred on the origin.

    Used for the TPU track belts: printed flat as a loop, then stretched over
    the drive and idler wheels.
    """
    half = max(length / 2 - radius, 0.0)
    points: list[Point2] = []
    for i in range(segments + 1):  # right cap
        angle = math.radians(-90 + 180 * i / segments)
        points.append((half + radius * math.cos(angle), radius * math.sin(angle)))
    for i in range(segments + 1):  # left cap
        angle = math.radians(90 + 180 * i / segments)
        points.append((-half + radius * math.cos(angle), radius * math.sin(angle)))
    del width  # kept in the signature for readability at call sites
    return points


_EPS = 1e-9


def _cross(o: Point2, u: Point2, v: Point2) -> float:
    return (u[0] - o[0]) * (v[1] - o[1]) - (u[1] - o[1]) * (v[0] - o[0])


def _same(p: Point2, q: Point2) -> bool:
    return abs(p[0] - q[0]) < 1e-9 and abs(p[1] - q[1]) < 1e-9


def _blocks_ear(p: Point2, a: Point2, b: Point2, c: Point2) -> bool:
    """True if vertex ``p`` lies inside or on the CCW triangle (a, b, c).

    A vertex that is *coincident with a corner* does not block: hole bridging
    deliberately duplicates two vertices, and treating those duplicates as
    blockers is exactly what made the first version of this triangulator stall
    and emit tubes with missing end caps.
    """
    if _same(p, a) or _same(p, b) or _same(p, c):
        return False
    return (
        _cross(a, b, p) >= -_EPS
        and _cross(b, c, p) >= -_EPS
        and _cross(c, a, p) >= -_EPS
    )


def _orient(a: Point2, b: Point2, c: Point2) -> int:
    value = _cross(a, b, c)
    if value > _EPS:
        return 1
    if value < -_EPS:
        return -1
    return 0


def _segments_cross(p1: Point2, p2: Point2, p3: Point2, p4: Point2) -> bool:
    """Proper intersection test: shared endpoints and touching do not count."""
    d1, d2 = _orient(p3, p4, p1), _orient(p3, p4, p2)
    d3, d4 = _orient(p1, p2, p3), _orient(p1, p2, p4)
    return d1 * d2 < 0 and d3 * d4 < 0


def _point_inside(point: Point2, poly: list[Point2]) -> bool:
    """Even-odd ray cast along +X."""
    x, y = point
    inside = False
    for i in range(len(poly)):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % len(poly)]
        if (ay > y) != (by > y):
            hit = ax + (y - ay) * (bx - ax) / (by - ay)
            if hit > x:
                inside = not inside
    return inside


def _bridge_is_clear(bridge_a: Point2, bridge_b: Point2,
                     poly: list[Point2], pending: list[list[Point2]]) -> bool:
    """True if the segment lies wholly inside the material.

    Three things have to hold: the bridge must not properly cross the contour
    built so far, it must not cross a hole that has not been merged yet, and
    its midpoint must actually be inside the part and outside every pending
    hole. The last check is what rejects a bridge that neatly threads between
    two edges but runs through fresh air.
    """
    for i in range(len(poly)):
        if _segments_cross(bridge_a, bridge_b, poly[i], poly[(i + 1) % len(poly)]):
            return False
    for ring in pending:
        for i in range(len(ring)):
            if _segments_cross(bridge_a, bridge_b, ring[i], ring[(i + 1) % len(ring)]):
                return False

    midpoint = ((bridge_a[0] + bridge_b[0]) / 2, (bridge_a[1] + bridge_b[1]) / 2)
    if not _point_inside(midpoint, poly):
        return False
    return all(not _point_inside(midpoint, ring) for ring in pending)


# Bridges are tried nearest-first; a valid one is almost always in the first
# handful, and the cap keeps a pathological layout from turning an O(n^3)
# search into a coffee break.
_MAX_BRIDGE_CANDIDATES = 400


def merge_holes(outer: list[Point2], holes: list[list[Point2]]) -> list[Point2]:
    """Bridges every hole into the outer contour, producing one simple polygon.

    For each hole, candidate bridges (every hole vertex to every contour
    vertex) are sorted by length and the first one that is verified clear is
    used. The obvious cheaper rule - "just bridge to the nearest vertex" -
    silently produces self-intersecting polygons as soon as holes are close
    together, which shows up not as an error but as a printed part with missing
    faces. Checking is worth the milliseconds.

    Holes are merged right to left so each bridge runs into geometry that is
    already settled.
    """
    poly = list(ensure_ccw(dedupe(outer)))
    pending = sorted(
        (ensure_cw(dedupe(h)) for h in holes),
        key=lambda h: max(p[0] for p in h),
        reverse=True,
    )

    while pending:
        ring = pending.pop(0)
        candidates = sorted(
            (
                ((poly[i][0] - ring[j][0]) ** 2 + (poly[i][1] - ring[j][1]) ** 2, i, j)
                for i in range(len(poly))
                for j in range(len(ring))
            ),
            key=lambda item: item[0],
        )

        chosen: tuple[int, int] | None = None
        for _, i, j in candidates[:_MAX_BRIDGE_CANDIDATES]:
            if _bridge_is_clear(poly[i], ring[j], poly, pending):
                chosen = (i, j)
                break
        if chosen is None:
            # Nothing verified clear. Take the shortest bridge rather than
            # dropping the hole; check_manifold() will flag the part.
            _, i, j = candidates[0]
            chosen = (i, j)

        i, j = chosen
        poly = poly[: i + 1] + ring[j:] + ring[: j + 1] + poly[i:]

    return poly


def earclip(poly: list[Point2]) -> list[tuple[int, int, int]]:
    """Triangulates a simple CCW polygon. Returns index triples."""
    indices = list(range(len(poly)))
    triangles: list[tuple[int, int, int]] = []
    stalled = 0
    while len(indices) > 3 and stalled < len(indices):
        clipped = False
        for k in range(len(indices)):
            i0 = indices[(k - 1) % len(indices)]
            i1 = indices[k]
            i2 = indices[(k + 1) % len(indices)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if _cross(a, b, c) <= _EPS:
                continue  # reflex or collinear: not an ear
            if any(
                _blocks_ear(poly[j], a, b, c)
                for j in indices
                if j not in (i0, i1, i2)
            ):
                continue
            triangles.append((i0, i1, i2))
            indices.pop(k)
            clipped = True
            stalled = 0
            break
        if not clipped:
            # Rotate and retry: a bad starting vertex can hide every ear.
            indices = indices[1:] + indices[:1]
            stalled += 1
    if len(indices) == 3:
        triangles.append((indices[0], indices[1], indices[2]))
    return triangles


# ---------------------------------------------------------------------------
# Mesh
# ---------------------------------------------------------------------------

@dataclass
class Mesh:
    """A triangle soup that knows how to write itself out as STL."""

    name: str = "part"
    triangles: list[Triangle] = field(default_factory=list)
    # (start, end) triangle index ranges, one per primitive. A part is a union
    # of these; each one must be individually watertight, but they are allowed
    # to overlap and to share faces - every slicer unions overlapping solids.
    solids: list[tuple[int, int]] = field(default_factory=list)

    # -------------------------- primitive builders -------------------------

    def add_triangle(self, a: Point3, b: Point3, c: Point3) -> None:
        self.triangles.append((a, b, c))

    def add_quad(self, a: Point3, b: Point3, c: Point3, d: Point3) -> None:
        """Adds a planar quad as two triangles, wound a-b-c-d."""
        self.add_triangle(a, b, c)
        self.add_triangle(a, c, d)

    def add_box(self, x0: float, y0: float, z0: float,
                x1: float, y1: float, z1: float) -> None:
        start = len(self.triangles)
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        z0, z1 = min(z0, z1), max(z0, z1)
        # Outward-facing winding on all six faces.
        self.add_quad((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0))  # -Z
        self.add_quad((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))  # +Z
        self.add_quad((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))  # -Y
        self.add_quad((x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1))  # +Y
        self.add_quad((x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1))  # -X
        self.add_quad((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1))  # +X
        self.solids.append((start, len(self.triangles)))

    def add_extrusion(self, outer: list[Point2], z0: float, z1: float,
                      holes: list[list[Point2]] | None = None) -> None:
        """Extrudes a polygon (with optional holes) between two Z planes."""
        start = len(self.triangles)
        holes = holes or []
        outer = ensure_ccw(dedupe(list(outer)))
        rings = [ensure_cw(dedupe(list(h))) for h in holes]

        merged = merge_holes(outer, rings) if rings else outer
        for i0, i1, i2 in earclip(merged):
            a, b, c = merged[i0], merged[i1], merged[i2]
            # Top cap faces +Z, bottom cap faces -Z (reversed winding).
            self.add_triangle((a[0], a[1], z1), (b[0], b[1], z1), (c[0], c[1], z1))
            self.add_triangle((a[0], a[1], z0), (c[0], c[1], z0), (b[0], b[1], z0))

        # Outer wall: normals point away from the solid.
        for i, (x0, y0) in enumerate(outer):
            x1, y1 = outer[(i + 1) % len(outer)]
            self.add_quad((x0, y0, z0), (x1, y1, z0), (x1, y1, z1), (x0, y0, z1))

        # Hole walls: CW rings, so the same winding faces inward - which is
        # outward with respect to the material.
        for ring in rings:
            for i, (x0, y0) in enumerate(ring):
                x1, y1 = ring[(i + 1) % len(ring)]
                self.add_quad((x0, y0, z0), (x1, y1, z0), (x1, y1, z1), (x0, y0, z1))

        self.solids.append((start, len(self.triangles)))

    def add_cylinder(self, cx: float, cy: float, z0: float, z1: float,
                     r: float, segments: int = 32) -> None:
        self.add_extrusion(circle(cx, cy, r, segments), z0, z1)

    def add_tube(self, cx: float, cy: float, z0: float, z1: float,
                 r_outer: float, r_inner: float, segments: int = 32) -> None:
        """A cylinder with a real through-hole - bolt bosses, wheels, bearings."""
        self.add_extrusion(
            circle(cx, cy, r_outer, segments), z0, z1,
            holes=[circle(cx, cy, r_inner, segments)],
        )

    # ------------------------------ analysis -------------------------------

    def check_manifold(self) -> list[str]:
        """Validates each solid independently. Empty list means the part is good.

        Checking the *whole part* at once would be wrong: a part is a union of
        touching solids, and two solids that share a face legitimately produce
        edges used four times. What actually matters is that every individual
        solid is closed, because that is what a slicer needs to union them.

        Coordinates are snapped to 1 micron before comparison so floating point
        noise in the trig does not read as a crack.
        """
        problems: list[str] = []

        def key(p: Point3) -> tuple:
            return (round(p[0], 3), round(p[1], 3), round(p[2], 3))

        ranges = self.solids or [(0, len(self.triangles))]
        for index, (start, end) in enumerate(ranges):
            edges: dict[tuple, int] = {}
            degenerate = 0
            for a, b, c in self.triangles[start:end]:
                ka, kb, kc = key(a), key(b), key(c)
                if ka == kb or kb == kc or ka == kc:
                    degenerate += 1
                    continue
                for u, v in ((ka, kb), (kb, kc), (kc, ka)):
                    edge = (u, v) if u < v else (v, u)
                    edges[edge] = edges.get(edge, 0) + 1

            if degenerate:
                problems.append(f"solid {index}: {degenerate} degenerate triangles")
            unshared = sum(1 for count in edges.values() if count != 2)
            if unshared:
                problems.append(
                    f"solid {index}: {unshared} of {len(edges)} edges are not shared by exactly 2 faces"
                )
        return problems

    def bounds(self) -> tuple[Point3, Point3]:
        xs = [p[0] for tri in self.triangles for p in tri]
        ys = [p[1] for tri in self.triangles for p in tri]
        zs = [p[2] for tri in self.triangles for p in tri]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def size(self) -> Point3:
        low, high = self.bounds()
        return (high[0] - low[0], high[1] - low[1], high[2] - low[2])

    def volume_cm3(self) -> float:
        """Signed volume via the divergence theorem, in cm^3.

        Useful as a filament estimate, and as a sanity check: a negative or
        wildly wrong volume means the winding is inverted somewhere.
        """
        total = 0.0
        for a, b, c in self.triangles:
            total += (
                a[0] * (b[1] * c[2] - c[1] * b[2])
                - a[1] * (b[0] * c[2] - c[0] * b[2])
                + a[2] * (b[0] * c[1] - c[0] * b[1])
            ) / 6.0
        return abs(total) / 1000.0

    # -------------------------------- output -------------------------------

    def write_stl(self, path: Path) -> None:
        """Writes a binary STL (about 6x smaller than ASCII, and universally read)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        header = f"Wall-E {self.name} - MIT licensed".encode()[:80]
        with path.open("wb") as handle:
            handle.write(header.ljust(80, b"\0"))
            handle.write(struct.pack("<I", len(self.triangles)))
            for a, b, c in self.triangles:
                nx, ny, nz = _normal(a, b, c)
                handle.write(struct.pack("<12fH", nx, ny, nz,
                                         *a, *b, *c, 0))


def _normal(a: Point3, b: Point3, c: Point3) -> Point3:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length == 0:
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)
