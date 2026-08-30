# Wall-E chassis — 3D printable parts

Eleven parts, about 175 cm³ of filament (~220 g), roughly 14 hours of printing
on a stock Ender 3 or Prusa MINI. Everything fits a 180 × 180 mm bed and
**nothing needs support material**.

| File | Qty | Material | Print time* | Notes |
|---|---|---|---|---|
| `chassis_base.stl` | 1 | PLA or PETG | ~5 h | Main tub. Open side up. |
| `track_frame.stl` | 2 | PLA or PETG | ~1 h each | Side plates. 30 % infill. |
| `drive_wheel.stl` | 2 | **PETG** | ~50 min each | Takes the servo torque — PETG or ABS, not PLA. 40 % infill. |
| `idler_wheel.stl` | 2 | PLA or PETG | ~45 min each | Front wheels. |
| `track_belt.stl` | 2 | **TPU 95A** | ~40 min each | 100 % infill, 20 mm/s. Prints flat, stretches on. |
| `speaker_grille.stl` | 1 | PLA | ~15 min | Chest plate. 0.16 mm layers look best. |
| `neck_bracket.stl` | 1 | PETG | ~25 min | Holds the head servo. |
| `head_shell.stl` | 1 | PLA or PETG | ~3 h | Open face **down**. |
| `eye_pod.stl` | 2 | PLA | ~30 min each | Print standing up. |
| `arm.stl` | 2 | PLA or PETG | ~20 min each | Symmetric — same file twice. |
| `battery_tray.stl` | 1 | PLA | ~40 min | Optional if you hot-glue the pack. |

\* At 0.2 mm layers, 60 mm/s, on a 0.4 mm nozzle. Your slicer's estimate is
better than this table.

## Slicer settings

The defaults of any modern slicer are fine. The three that matter:

- **Layer height 0.2 mm** (0.16 mm for `speaker_grille` and `eye_pod`, which
  are the parts people look at).
- **No supports.** Every part is oriented so overhangs stay under 45°. If your
  slicer wants to add supports, the part is rotated wrong — the flat face goes
  on the bed.
- **Holes print undersize on FDM.** Every hole here is already modelled 0.2 mm
  oversize, so drill only if a screw genuinely will not start.

TPU belts: slow down to 20 mm/s, turn retraction down or off, and print them
directly on the bed with no brim. They come off looking like a bicycle inner
tube and behave the same way.

## Non-printed hardware

| Item | Qty | Notes |
|---|---|---|
| M3 × 10 mm machine screw | 12 | Frames, neck, lid |
| M3 nut | 12 | |
| M2 × 6 mm self-tapping screw | 16 | Servos, OLED, grille |
| M2.5 × 6 mm self-tapping screw | 4 | ESP32 board |
| 6 mm × 60 mm steel rod or M6 bolt | 2 | Wheel axles (a smooth bolt shank works) |
| M6 nut | 4 | Axle retention |

## Assembly order

1. **Wheels onto the frames.** Push an axle through `track_frame`, slide on
   `idler_wheel`, nut on the outside. Leave it free to spin.
2. **Drive servos.** Screw each `drive_wheel` to a continuous-rotation servo
   horn (two M2 screws, 5 mm pitch), then bolt the servos inside the chassis so
   the wheels sit in the rear axle position of the frames.
3. **Frames onto the chassis.** Four M3 per side, into the holes near the
   chassis floor.
4. **Belts.** Stretch a `track_belt` over each pair of wheels. It should be
   tight enough that it does not sag but loose enough to turn by hand.
5. **Electronics.** ESP32 on the four M2.5 standoff holes, battery in the tray,
   speaker behind `speaker_grille` on the front opening.
6. **Neck and head.** `neck_bracket` onto the raised pad, pan servo into the
   bracket, `head_shell` onto the servo horn through its underside socket.
7. **Face.** OLED module screwed to the four bosses behind the window, camera
   through the roof hole, `eye_pod` × 2 dropped into the roof holes over it.
8. **Arms.** One M3 each, through the shoulder boss into the body sides. Leave
   them slightly loose so they swing.

Wiring is in [`docs/02-wiring.md`](../docs/02-wiring.md). Get the electronics
working on the bench *before* you close the body up — it is far easier to
re-seat a loose I2S wire when you can see it.

## Regenerating the models

The canonical models are the parametric OpenSCAD files in `src/`. Change a
dimension in `src/walle_params.scad` and every part follows.

```bash
# From OpenSCAD (the real models)
cd 3d-models && make openscad

# Without OpenSCAD installed (pure Python, standard library only)
cd 3d-models && make

# Verify every part is watertight and fits a 180 mm bed
cd 3d-models && make check
```

`tools/gen_stl.py` is a small solid modeller (`tools/meshlib.py`) that builds
each part as a union of watertight primitives, with real holes extruded into
the geometry rather than subtracted. It validates every part before writing it,
so a modelling mistake fails at generation time instead of four hours into a
print.

## Making it your own

`src/walle_params.scad` is where to start:

- `body_w`, `body_d`, `body_h` — resize the whole body.
- `wheel_r`, `wheel_gap` — bigger wheels and a longer wheelbase climb over
  cables better. The belt follows automatically.
- `oled_win_w`, `oled_win_h` — swap in a different display.
- `wall` — thicker walls if you are printing in PETG and want a heavier robot.

All parts are MIT licensed. Print them, sell them, remix them.
