#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generates the printable STLs in ``3d-models/stl``.

    python3 3d-models/tools/gen_stl.py

Every part is validated for watertightness before it is written, and the script
prints each part's bounding box and volume so you can sanity check a part
against your printer's bed and your filament budget before slicing.

The parametric OpenSCAD sources in ``3d-models/src`` are the canonical models -
they use real boolean subtraction and are the nicer thing to edit. This script
exists so the repository ships STLs even for people who do not have OpenSCAD,
and it deliberately keeps the same dimension names so the two stay comparable.

Design notes worth knowing before you print:

* Every part is oriented for printing with **no support material**. Openings
  face up or sideways, and there are no overhangs past 45 degrees.
* Holes are modelled 0.2 mm oversize, because FDM printers shrink holes.
* The chassis is a bolt-together design rather than a single print, so it fits
  a 180 mm bed and a failed print costs you one part instead of the robot.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from meshlib import Mesh, circle, rect, rounded_rect, stadium  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "stl"

# ---------------------------------------------------------------------------
# Shared dimensions. Change these and every part follows.
# ---------------------------------------------------------------------------
WALL = 2.4               # general wall thickness: 6 perimeters at 0.4 mm
FLOOR = 2.4

BODY_W = 86.0            # left-right, between the track frames
BODY_D = 76.0            # front-back
BODY_H = 38.0            # floor to the open top

HEAD_W = 70.0
HEAD_D = 45.0
HEAD_H = 40.0

# Fastener holes, already oversized for FDM shrinkage.
M2 = 1.2                 # radius for a self-tapping M2 screw
M25 = 1.45
M3 = 1.7
AXLE = 3.2               # 6 mm axle / M6 bolt shank

WHEEL_R = 16.0
WHEEL_H = 9.0
WHEEL_SPACING = 58.0     # centre-to-centre, drive to idler

OLED_WINDOW_W = 27.0     # visible area of a 0.96" 128x64 SSD1306 module
OLED_WINDOW_H = 15.0

SEG = 32                 # circle facets
SEG_FINE = 24


# ---------------------------------------------------------------------------
# Parts
# ---------------------------------------------------------------------------

def chassis_base() -> Mesh:
    """The main tub: holds the ESP32, the battery and the two drive servos.

    Printed open side up. The front wall has a rectangular opening for the
    speaker grille, made by composing three boxes rather than subtracting -
    which is also why there is no unsupported bridge over it.
    """
    m = Mesh("chassis_base")
    w, d = BODY_W, BODY_D

    # ESP32-S3 board: 4 mounting holes on a 60 x 28 pattern, centred.
    board_holes = [
        circle(w / 2 + dx, d / 2 + dy, M25, SEG_FINE)
        for dx in (-30.0, 30.0)
        for dy in (-14.0, 14.0)
    ]
    # Cable pass-throughs between the electronics bay and the servo bay.
    slots = [
        rounded_rect(w / 2 - 22, 8, w / 2 - 8, 16, 3.5),
        rounded_rect(w / 2 + 8, 8, w / 2 + 22, 16, 3.5),
    ]
    # Bolt holes for the two side track frames.
    frame_holes = [
        circle(x, y, M3, SEG_FINE)
        for x in (WALL + 4, w - WALL - 4)
        for y in (18.0, d - 18.0)
    ]

    m.add_extrusion(rounded_rect(0, 0, w, d, 4), 0, FLOOR,
                    holes=board_holes + slots + frame_holes)

    top = FLOOR + BODY_H
    # Side and back walls.
    m.add_box(0, 0, FLOOR, WALL, d, top)
    m.add_box(w - WALL, 0, FLOOR, w, d, top)
    m.add_box(0, d - WALL, FLOOR, w, d, top)

    # Front wall in three pieces, leaving a 36 x 24 speaker opening.
    open_w, open_h = 36.0, 24.0
    x0 = (w - open_w) / 2
    m.add_box(0, 0, FLOOR, x0, WALL, top)
    m.add_box(x0 + open_w, 0, FLOOR, w, WALL, top)
    m.add_box(x0, 0, FLOOR + open_h, x0 + open_w, WALL, top)

    # Corner bosses for the lid screws.
    for cx, cy in ((6.0, 6.0), (w - 6.0, 6.0), (6.0, d - 6.0), (w - 6.0, d - 6.0)):
        m.add_tube(cx, cy, FLOOR, top, 4.2, M3, SEG_FINE)

    # Neck mount: a raised pad on the back half of the floor.
    m.add_extrusion(rounded_rect(w / 2 - 20, d - 30, w / 2 + 20, d - 8, 3),
                    FLOOR, FLOOR + 3.0,
                    holes=[circle(w / 2 + dx, d - 19.0, M3, SEG_FINE)
                           for dx in (-15.0, 15.0)])
    return m


def track_frame() -> Mesh:
    """Side plate carrying the drive and idler axles. Print two."""
    m = Mesh("track_frame")
    length = WHEEL_SPACING + 2 * WHEEL_R + 8
    height = 2 * WHEEL_R + 8
    plate = 3.2

    axle_y = height / 2
    holes = [
        circle(WHEEL_R + 4, axle_y, AXLE, SEG),
        circle(WHEEL_R + 4 + WHEEL_SPACING, axle_y, AXLE, SEG),
    ]
    # Bolt pattern matching chassis_base's frame_holes.
    holes += [circle(x, y, M3, SEG_FINE)
              for x in (length / 2 - 14, length / 2 + 14)
              for y in (6.0, height - 6.0)]

    m.add_extrusion(rounded_rect(0, 0, length, height, 6), 0, plate, holes=holes)

    # Axle bosses: more thread engagement without thickening the whole plate.
    for cx in (WHEEL_R + 4, WHEEL_R + 4 + WHEEL_SPACING):
        m.add_tube(cx, axle_y, plate, plate + 3.0, AXLE + 3.0, AXLE, SEG)
    return m


def drive_wheel() -> Mesh:
    """Sprocket driven directly by a continuous-rotation servo. Print two.

    The rim nubs are what actually grip a TPU belt; a fine-toothed sprocket
    looks better in CAD and strips in practice at this scale.
    """
    m = Mesh("drive_wheel")
    holes = [circle(0, 0, AXLE, SEG)]
    # Two screws into the servo horn, on the standard 5 mm pitch.
    holes += [circle(dx, 0.0, M2, SEG_FINE) for dx in (-5.0, 5.0)]
    m.add_extrusion(circle(0, 0, WHEEL_R - 2.0, SEG), 0, WHEEL_H, holes=holes)

    # Rim nubs.
    import math

    for i in range(10):
        angle = 2 * math.pi * i / 10
        m.add_cylinder(
            (WHEEL_R - 2.0) * math.cos(angle),
            (WHEEL_R - 2.0) * math.sin(angle),
            0, WHEEL_H, 2.2, 16,
        )
    # Flanges keep the belt from walking off.
    m.add_tube(0, 0, -1.6, 0, WHEEL_R + 1.5, AXLE, SEG)
    m.add_tube(0, 0, WHEEL_H, WHEEL_H + 1.6, WHEEL_R + 1.5, AXLE, SEG)
    return m


def idler_wheel() -> Mesh:
    """Free-running front wheel. Print two."""
    m = Mesh("idler_wheel")
    m.add_tube(0, 0, 0, WHEEL_H, WHEEL_R - 2.0, AXLE, SEG)
    m.add_tube(0, 0, -1.6, 0, WHEEL_R + 1.5, AXLE, SEG)
    m.add_tube(0, 0, WHEEL_H, WHEEL_H + 1.6, WHEEL_R + 1.5, AXLE, SEG)
    return m


def track_belt() -> Mesh:
    """A flexible track, printed flat as a closed loop in TPU. Print two.

    Printed lying down, then stretched over the drive and idler wheels. This is
    far more reliable at desk-robot scale than thirty individual printed links
    and thirty pins, and it is quiet on a wooden desk.
    """
    m = Mesh("track_belt")
    thickness = 1.6
    belt_w = 12.0
    # The loop's centreline has to be shorter than the wheels' circumference
    # path so the TPU is under slight tension.
    outer = stadium(WHEEL_SPACING + 2 * (WHEEL_R - 1.0), belt_w, WHEEL_R - 1.0, 28)
    inner = stadium(
        WHEEL_SPACING + 2 * (WHEEL_R - 1.0 - belt_w),
        belt_w, WHEEL_R - 1.0 - belt_w, 28,
    )
    m.add_extrusion(outer, 0, thickness, holes=[inner])
    return m


def speaker_grille() -> Mesh:
    """Front panel over the speaker. Also Wall-E's chest detail."""
    m = Mesh("speaker_grille")
    w, h = 36.0, 24.0
    holes = []
    for row in range(3):
        for col in range(5):
            holes.append(circle(6.0 + col * 6.0, 6.0 + row * 6.0, 2.0, SEG_FINE))
    holes += [circle(x, y, M2, SEG_FINE) for x in (3.0, w - 3.0) for y in (3.0, h - 3.0)]
    m.add_extrusion(rounded_rect(0, 0, w, h, 2), 0, 2.0, holes=holes)
    return m


def neck_bracket() -> Mesh:
    """Holds the head-pan servo, bolts to the pad in the chassis floor."""
    m = Mesh("neck_bracket")
    w, d, plate = 40.0, 30.0, 3.2

    # SG90 body pocket, straight through: 23 x 12.5 is the standard body.
    servo = rect(w / 2 - 11.5, d / 2 - 6.25, w / 2 + 11.5, d / 2 + 6.25)
    holes = [servo]
    # Servo flange screws sit 27.5 mm apart.
    holes += [circle(w / 2 + dx, d / 2, M2, SEG_FINE) for dx in (-13.75, 13.75)]
    # Bolts down to the chassis pad.
    holes += [circle(w / 2 + dx, 4.0, M3, SEG_FINE) for dx in (-15.0, 15.0)]

    m.add_extrusion(rounded_rect(0, 0, w, d, 3), 0, plate, holes=holes)
    # Uprights that lift the servo clear of the floor.
    m.add_box(0, d - 4.0, plate, 5.0, d, plate + 14.0)
    m.add_box(w - 5.0, d - 4.0, plate, w, d, plate + 14.0)
    return m


def head_shell() -> Mesh:
    """Wall-E's head: open bottom, OLED window in the front face.

    Print with the open face down. The window is composed from four boxes, so
    nothing bridges and no support is needed.
    """
    m = Mesh("head_shell")
    w, d, h = HEAD_W, HEAD_D, HEAD_H

    # Roof, with two holes for the eye pods and one for the camera.
    roof_holes = [circle(w / 2 + dx, d / 2 + 6.0, 10.5, SEG) for dx in (-17.0, 17.0)]
    roof_holes.append(circle(w / 2, d / 2 - 12.0, 4.5, SEG_FINE))
    m.add_extrusion(rounded_rect(0, 0, w, d, 5), h - WALL, h, holes=roof_holes)

    # Side and back walls.
    m.add_box(0, 0, 0, WALL, d, h - WALL)
    m.add_box(w - WALL, 0, 0, w, d, h - WALL)
    m.add_box(0, d - WALL, 0, w, d, h - WALL)

    # Front wall with the display window.
    win_x0 = (w - OLED_WINDOW_W) / 2
    win_z0 = (h - WALL - OLED_WINDOW_H) / 2
    m.add_box(0, 0, 0, win_x0, WALL, h - WALL)
    m.add_box(win_x0 + OLED_WINDOW_W, 0, 0, w, WALL, h - WALL)
    m.add_box(win_x0, 0, 0, win_x0 + OLED_WINDOW_W, WALL, win_z0)
    m.add_box(win_x0, 0, win_z0 + OLED_WINDOW_H, win_x0 + OLED_WINDOW_W, WALL, h - WALL)

    # Bosses to screw the OLED module in from behind.
    for dx in (-13.5, 13.5):
        for dz in (-9.5, 9.5):
            m.add_tube(w / 2 + dx, WALL + 3.0, h / 2 + dz - 2.0,
                       h / 2 + dz + 2.0, 3.0, M2, 12)

    # Neck socket in the underside, for the servo horn.
    m.add_extrusion(circle(w / 2, d / 2, 9.0, SEG), 0, 5.0,
                    holes=[circle(w / 2, d / 2, 3.0, SEG_FINE)])
    return m


def eye_pod() -> Mesh:
    """One of Wall-E's binocular eye tubes. Print two.

    Prints standing up: a tube is the one shape FDM does perfectly.
    """
    m = Mesh("eye_pod")
    m.add_tube(0, 0, 0, 20.0, 12.5, 10.0, SEG)
    m.add_tube(0, 0, 0, 2.5, 15.5, 10.0, SEG)  # flange that sits on the roof
    return m


def arm() -> Mesh:
    """A simple articulated-looking arm. Print two (they are symmetric)."""
    m = Mesh("arm")
    length, width, plate = 62.0, 14.0, 3.2
    holes = [
        circle(7.0, width / 2, M3, SEG_FINE),          # shoulder pivot
        circle(length - 7.0, width / 2, M2, SEG_FINE),  # hand detail
    ]
    m.add_extrusion(rounded_rect(0, 0, length, width, width / 2), 0, plate, holes=holes)
    # Shoulder boss for a nut trap.
    m.add_tube(7.0, width / 2, plate, plate + 2.5, 5.0, M3, SEG_FINE)
    return m


def battery_tray() -> Mesh:
    """Drop-in tray for a 2S 18650 holder, keeps the pack from sliding."""
    m = Mesh("battery_tray")
    w, d = 76.0, 42.0
    m.add_extrusion(rounded_rect(0, 0, w, d, 3), 0, 2.0,
                    holes=[circle(x, y, M3, SEG_FINE)
                           for x in (6.0, w - 6.0) for y in (6.0, d - 6.0)]
                    + [rounded_rect(w / 2 - 20, d / 2 - 8, w / 2 + 20, d / 2 + 8, 4)])
    for x0, x1 in ((0.0, 2.0), (w - 2.0, w)):
        m.add_box(x0, 0, 2.0, x1, d, 12.0)
    m.add_box(0, 0, 2.0, w, 2.0, 12.0)
    m.add_box(0, d - 2.0, 2.0, w, d, 12.0)
    return m


PARTS: dict[str, tuple] = {
    "chassis_base": (chassis_base, 1, "PLA/PETG", "0.2 mm, 20% infill, no supports"),
    "track_frame": (track_frame, 2, "PLA/PETG", "0.2 mm, 30% infill"),
    "drive_wheel": (drive_wheel, 2, "PETG", "0.16 mm, 40% infill - it takes the torque"),
    "idler_wheel": (idler_wheel, 2, "PLA/PETG", "0.2 mm, 25% infill"),
    "track_belt": (track_belt, 2, "TPU 95A", "0.2 mm, 100% infill, 20 mm/s"),
    "speaker_grille": (speaker_grille, 1, "PLA", "0.16 mm, 20% infill"),
    "neck_bracket": (neck_bracket, 1, "PETG", "0.2 mm, 35% infill"),
    "head_shell": (head_shell, 1, "PLA/PETG", "0.2 mm, 20% infill, open face down"),
    "eye_pod": (eye_pod, 2, "PLA", "0.16 mm, 25% infill, print standing"),
    "arm": (arm, 2, "PLA/PETG", "0.2 mm, 25% infill"),
    "battery_tray": (battery_tray, 1, "PLA", "0.2 mm, 15% infill"),
}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"writing STLs to {OUT_DIR}\n")
    header = f"{'part':16} {'qty':>3}  {'size (mm)':>22} {'cm3':>7} {'tris':>6}  status"
    print(header)
    print("-" * len(header))

    failures = 0
    total_volume = 0.0
    for name, (builder, quantity, _material, _settings) in PARTS.items():
        mesh = builder()
        problems = mesh.check_manifold()
        size = mesh.size()
        volume = mesh.volume_cm3()
        total_volume += volume * quantity
        status = "ok" if not problems else "FAILED: " + "; ".join(problems)
        print(
            f"{name:16} {quantity:>3}  "
            f"{size[0]:6.1f} x{size[1]:6.1f} x{size[2]:5.1f} "
            f"{volume:7.2f} {len(mesh.triangles):6d}  {status}"
        )
        if problems:
            failures += 1
            continue
        mesh.write_stl(OUT_DIR / f"{name}.stl")

    print()
    # ~1.24 g/cm3 for PLA; filament is sold by weight, so that is the useful number.
    print(f"total printed volume: {total_volume:.1f} cm3 (~{total_volume * 1.24:.0f} g of PLA)")
    if failures:
        print(f"\n{failures} part(s) failed validation and were not written", file=sys.stderr)
        return 1
    print("all parts watertight")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
