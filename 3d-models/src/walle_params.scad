// SPDX-License-Identifier: MIT
// Shared dimensions for every Wall-E part.
//
// These names and values match 3d-models/tools/gen_stl.py exactly. If you
// change something here, change it there too (or just regenerate from these
// files with OpenSCAD and ignore the Python generator entirely - it exists
// only so the repo ships STLs for people without OpenSCAD installed).

$fn = 48;                   // facets on curves; 48 is smooth at this scale

// ---- general -------------------------------------------------------------
wall        = 2.4;          // 6 perimeters at a 0.4 mm nozzle
floor_t     = 2.4;
clearance   = 0.3;          // between mating printed parts

// ---- body ----------------------------------------------------------------
body_w      = 86;           // left-right, inside the track frames
body_d      = 76;           // front-back
body_h      = 38;           // floor to open top

// ---- head ----------------------------------------------------------------
head_w      = 70;
head_d      = 45;
head_h      = 40;
oled_win_w  = 27;           // visible area of a 0.96" SSD1306
oled_win_h  = 15;

// ---- fasteners -----------------------------------------------------------
// Radii, already oversized ~0.2 mm because FDM printers shrink holes.
m2          = 1.2;
m25         = 1.45;
m3          = 1.7;
axle        = 3.2;          // 6 mm axle / M6 shank

// ---- drive ---------------------------------------------------------------
wheel_r     = 16;
wheel_h     = 9;
wheel_gap   = 58;           // drive-to-idler centre distance
belt_w      = 12;
belt_t      = 1.6;

// ---- servos --------------------------------------------------------------
// SG90 / MG90S / FS90R all share this body.
servo_body  = [23, 12.5, 22.5];
servo_screw_pitch = 27.5;

// Convenience: a rounded slab, the shape most of these parts start from.
module slab(size, r = 3) {
    linear_extrude(height = size[2])
        offset(r = r) offset(r = -r)
            square([size[0], size[1]]);
}
