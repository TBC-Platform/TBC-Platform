// SPDX-License-Identifier: MIT
// Head, eye pods, neck bracket and arms.
//
//   openscad -o ../stl/head_shell.stl -D 'part="head_shell"' head.scad
//
// The head prints open-face-down. The OLED window has no bridge over it
// because the front wall is split above and below the opening.

include <walle_params.scad>

part = "head_shell";  // head_shell | eye_pod | neck_bracket | arm

if (part == "head_shell")        head_shell();
else if (part == "eye_pod")      eye_pod();
else if (part == "neck_bracket") neck_bracket();
else if (part == "arm")          arm();

module head_shell() {
    difference() {
        union() {
            // Shell: outer slab hollowed from below.
            difference() {
                slab([head_w, head_d, head_h], 5);
                translate([wall, wall, -1])
                    cube([head_w - 2 * wall, head_d - 2 * wall, head_h - wall + 1]);
            }
            // Neck socket for the servo horn.
            translate([head_w / 2, head_d / 2, 0]) cylinder(h = 5, r = 9);
            // Bosses to screw the OLED module in from behind.
            for (dx = [-13.5, 13.5], dz = [-9.5, 9.5])
                translate([head_w / 2 + dx, wall + 3, head_h / 2 + dz])
                    rotate([90, 0, 0]) cylinder(h = 4, r = 3, center = true);
        }

        // Display window.
        translate([(head_w - oled_win_w) / 2, -1, (head_h - wall - oled_win_h) / 2])
            cube([oled_win_w, wall + 2, oled_win_h]);
        // Eye pod holes and the camera hole in the roof.
        for (dx = [-17, 17])
            translate([head_w / 2 + dx, head_d / 2 + 6, head_h - wall - 1])
                cylinder(h = wall + 2, r = 10.5);
        translate([head_w / 2, head_d / 2 - 12, head_h - wall - 1])
            cylinder(h = wall + 2, r = 4.5);
        // Servo horn screw.
        translate([head_w / 2, head_d / 2, -1]) cylinder(h = 8, r = 3);
        // OLED boss pilot holes.
        for (dx = [-13.5, 13.5], dz = [-9.5, 9.5])
            translate([head_w / 2 + dx, wall + 5, head_h / 2 + dz])
                rotate([90, 0, 0]) cylinder(h = 8, r = m2, center = true);
    }
}

module eye_pod() {
    difference() {
        union() {
            cylinder(h = 20, r = 12.5);
            cylinder(h = 2.5, r = 15.5);   // flange, sits on the roof
        }
        translate([0, 0, -1]) cylinder(h = 24, r = 10);
    }
}

module neck_bracket() {
    w = 40; d = 30; t = 3.2;
    difference() {
        union() {
            slab([w, d, t], 3);
            for (x = [0, w - 5]) translate([x, d - 4, t]) cube([5, 4, 14]);
        }
        // Servo body pocket and its flange screws.
        translate([w / 2 - servo_body[0] / 2, d / 2 - servo_body[1] / 2, -1])
            cube([servo_body[0], servo_body[1], t + 2]);
        for (dx = [-servo_screw_pitch / 2, servo_screw_pitch / 2])
            translate([w / 2 + dx, d / 2, -1]) cylinder(h = t + 2, r = m2);
        // Bolts down to the chassis pad.
        for (dx = [-15, 15]) translate([w / 2 + dx, 4, -1]) cylinder(h = t + 2, r = m3);
    }
}

module arm() {
    l = 62; w = 14; t = 3.2;
    difference() {
        union() {
            slab([l, w, t], w / 2);
            translate([7, w / 2, t]) cylinder(h = 2.5, r = 5);  // shoulder boss
        }
        translate([7, w / 2, -1]) cylinder(h = t + 8, r = m3);
        translate([l - 7, w / 2, -1]) cylinder(h = t + 2, r = m2);
    }
}
