// SPDX-License-Identifier: MIT
// Drive train: side frames, wheels and the flexible printed belt.
//
//   openscad -o ../stl/drive_wheel.stl -D 'part="drive_wheel"' tracks.scad
//
// The belt prints flat in TPU as a closed loop, then stretches over the two
// wheels. Individual printed links with pins look better in renders and shed
// themselves across the desk at this scale.

include <walle_params.scad>

part = "track_frame";  // track_frame | drive_wheel | idler_wheel | track_belt

if (part == "track_frame")      track_frame();
else if (part == "drive_wheel") drive_wheel();
else if (part == "idler_wheel") idler_wheel();
else if (part == "track_belt")  track_belt();

frame_l = wheel_gap + 2 * wheel_r + 8;
frame_h = 2 * wheel_r + 8;
frame_t = 3.2;

module track_frame() {
    axle_y = frame_h / 2;
    difference() {
        union() {
            slab([frame_l, frame_h, frame_t], 6);
            // Axle bosses give the shaft more bearing surface.
            for (cx = [wheel_r + 4, wheel_r + 4 + wheel_gap])
                translate([cx, axle_y, frame_t]) cylinder(h = 3, r = axle + 3);
        }
        for (cx = [wheel_r + 4, wheel_r + 4 + wheel_gap])
            translate([cx, axle_y, -1]) cylinder(h = frame_t + 8, r = axle);
        for (x = [frame_l / 2 - 14, frame_l / 2 + 14], y = [6, frame_h - 6])
            translate([x, y, -1]) cylinder(h = frame_t + 2, r = m3);
    }
}

module drive_wheel() {
    difference() {
        union() {
            cylinder(h = wheel_h, r = wheel_r - 2);
            // Rim nubs: these are what grip the TPU belt. A fine-toothed
            // sprocket strips at this scale.
            for (i = [0 : 9])
                rotate([0, 0, i * 36])
                    translate([wheel_r - 2, 0, 0]) cylinder(h = wheel_h, r = 2.2);
            // Flanges stop the belt walking off.
            translate([0, 0, -1.6]) cylinder(h = 1.6, r = wheel_r + 1.5);
            translate([0, 0, wheel_h]) cylinder(h = 1.6, r = wheel_r + 1.5);
        }
        translate([0, 0, -3]) cylinder(h = wheel_h + 6, r = axle);
        // Screws into the servo horn, standard 5 mm pitch.
        for (dx = [-5, 5]) translate([dx, 0, -3]) cylinder(h = wheel_h + 6, r = m2);
    }
}

module idler_wheel() {
    difference() {
        union() {
            cylinder(h = wheel_h, r = wheel_r - 2);
            translate([0, 0, -1.6]) cylinder(h = 1.6, r = wheel_r + 1.5);
            translate([0, 0, wheel_h]) cylinder(h = 1.6, r = wheel_r + 1.5);
        }
        translate([0, 0, -3]) cylinder(h = wheel_h + 6, r = axle);
    }
}

module track_belt() {
    r = wheel_r - 1;
    linear_extrude(height = belt_t)
        difference() {
            hull() for (dx = [-wheel_gap / 2, wheel_gap / 2])
                translate([dx, 0]) circle(r = r);
            hull() for (dx = [-wheel_gap / 2, wheel_gap / 2])
                translate([dx, 0]) circle(r = r - belt_w);
        }
}
