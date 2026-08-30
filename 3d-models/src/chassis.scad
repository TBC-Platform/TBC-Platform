// SPDX-License-Identifier: MIT
// Body: the tub that holds the ESP32, the battery and the drive servos.
//
//   openscad -o ../stl/chassis_base.stl -D 'part="chassis_base"' chassis.scad
//
// Everything prints without support. The front opening is a rectangle rather
// than a grille so nothing has to bridge; the grille is a separate flat part.

include <walle_params.scad>

part = "chassis_base";   // chassis_base | speaker_grille | battery_tray

if (part == "chassis_base")      chassis_base();
else if (part == "speaker_grille") speaker_grille();
else if (part == "battery_tray")   battery_tray();

module chassis_base() {
    speaker_open = [36, 24];
    difference() {
        union() {
            // Floor slab.
            slab([body_w, body_d, floor_t], 4);
            // Perimeter walls, with a gap left for the speaker opening.
            difference() {
                translate([0, 0, floor_t])
                    difference() {
                        slab([body_w, body_d, body_h], 4);
                        translate([wall, wall, -1])
                            cube([body_w - 2 * wall, body_d - 2 * wall, body_h + 2]);
                    }
                // Speaker opening in the front wall.
                translate([(body_w - speaker_open[0]) / 2, -1, floor_t])
                    cube([speaker_open[0], wall + 2, speaker_open[1]]);
            }
            // Lid screw bosses.
            for (p = [[6, 6], [body_w - 6, 6], [6, body_d - 6], [body_w - 6, body_d - 6]])
                translate([p[0], p[1], floor_t]) cylinder(h = body_h, r = 4.2);
            // Raised pad the neck bracket bolts to.
            translate([body_w / 2 - 20, body_d - 30, floor_t])
                slab([40, 22, 3], 3);
        }

        // --- everything removed below ---
        // ESP32-S3 board mounts, 60 x 28 pattern.
        for (dx = [-30, 30], dy = [-14, 14])
            translate([body_w / 2 + dx, body_d / 2 + dy, -1])
                cylinder(h = floor_t + 2, r = m25);
        // Cable pass-throughs.
        for (dx = [-15, 15])
            translate([body_w / 2 + dx - 7, 8, -1])
                hull() for (x = [0, 14]) translate([x, 4, 0]) cylinder(h = floor_t + 2, r = 4);
        // Track frame bolts.
        for (x = [wall + 4, body_w - wall - 4], y = [18, body_d - 18])
            translate([x, y, -1]) cylinder(h = floor_t + 2, r = m3);
        // Lid screw holes through the bosses.
        for (p = [[6, 6], [body_w - 6, 6], [6, body_d - 6], [body_w - 6, body_d - 6]])
            translate([p[0], p[1], -1]) cylinder(h = body_h + floor_t + 2, r = m3);
        // Neck bracket bolts.
        for (dx = [-15, 15])
            translate([body_w / 2 + dx, body_d - 19, -1])
                cylinder(h = floor_t + 6, r = m3);
    }
}

module speaker_grille() {
    difference() {
        slab([36, 24, 2], 2);
        for (row = [0 : 2], col = [0 : 4])
            translate([6 + col * 6, 6 + row * 6, -1]) cylinder(h = 4, r = 2);
        for (x = [3, 33], y = [3, 21])
            translate([x, y, -1]) cylinder(h = 4, r = m2);
    }
}

module battery_tray() {
    difference() {
        union() {
            slab([76, 42, 2], 3);
            difference() {
                translate([0, 0, 2]) slab([76, 42, 10], 3);
                translate([2, 2, 1]) cube([72, 38, 12]);
            }
        }
        // Weight-saving window and mounting holes.
        translate([76 / 2 - 20, 42 / 2 - 8, -1]) slab([40, 16, 4], 4);
        for (x = [6, 70], y = [6, 36]) translate([x, y, -1]) cylinder(h = 4, r = m3);
    }
}
