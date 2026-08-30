# Parts list and bill of materials

Prices are **indicative USD as of early 2026**, for single-unit quantities from
the usual hobby suppliers (Adafruit, Pimoroni, AliExpress, Amazon). They move
around; treat the totals as a budget, not a quote. The machine-readable version
is [`bom.csv`](bom.csv).

## Summary

| Build | Cost |
|---|---|
| **Minimum viable** — voice assistant, no motors, no battery | **≈ $42** |
| **Recommended** — everything in this repo | **≈ $83** |
| **Plus a dedicated server** — if you don't already own a PC/Mac | **+ $150–600** |

The server side is deliberately not counted in the build: the whole point of
the architecture is that it runs on a computer you already have.

## Core electronics — required

| # | Part | Qty | Unit | Total | Why this one |
|---|---|---|---|---|---|
| 1 | **Freenove ESP32-S3-WROOM CAM** board (8 MB PSRAM, OV2640 included) | 1 | $16.00 | $16.00 | The S3 is what makes offline wake-word detection possible, PSRAM is what makes the camera and audio buffers possible, and this board breaks out enough free GPIO for two I2S buses. The default pin map in `config.h` targets it. |
| 2 | **INMP441** I2S MEMS microphone module | 1 | $3.50 | $3.50 | I2S, not analog: a digital mic keeps servo noise out of the audio. ICS-43434 is a drop-in upgrade if you can find one. |
| 3 | **MAX98357A** I2S class-D amplifier | 1 | $4.50 | $4.50 | 3 W into 4 Ω from 5 V, no heatsink, and it takes the same I2S clocks as the mic. |
| 4 | 4 Ω 3 W speaker, 40 mm | 1 | $3.00 | $3.00 | Anything 4–8 Ω fits behind the grille. |
| 5 | **SSD1306** OLED, 0.96", 128×64, I2C | 1 | $4.00 | $4.00 | The face. The window in `head_shell.stl` is cut for this module. |
| 6 | MicroUSB / USB-C cable | 1 | $2.00 | $2.00 | Flashing and bench power. |
| | **Subtotal** | | | **$33.00** | |

## Motion — optional but it's what makes it a robot

| # | Part | Qty | Unit | Total | Notes |
|---|---|---|---|---|---|
| 7 | **FS90R** continuous-rotation micro servo | 2 | $5.00 | $10.00 | Continuous rotation, *not* standard servos — pulse width sets speed, not angle. |
| 8 | **SG90** standard micro servo (head pan) | 1 | $2.50 | $2.50 | 180°, for the neck. |
| 9 | 6 mm × 60 mm steel rod or M6 bolt | 2 | $1.00 | $2.00 | Wheel axles. |
| | **Subtotal** | | | **$14.50** | |

## Power

Two options. Pick one.

| # | Part | Qty | Unit | Total | Notes |
|---|---|---|---|---|---|
| 10a | USB power only (no battery) | — | $0.00 | $0.00 | Fine for a desk robot that never leaves the desk. Skip 10b–13. |
| 10b | 18650 Li-ion cell, protected | 2 | $6.00 | $12.00 | 2S = 7.4 V nominal. |
| 11 | 2S 18650 holder | 1 | $2.50 | $2.50 | |
| 12 | **MP1584EN** or **LM2596** buck converter, set to 5.0 V | 1 | $2.00 | $2.00 | The servos brown out on anything less. Set the voltage *before* connecting the ESP32. |
| 13 | 2S BMS / balance charge board | 1 | $3.00 | $3.00 | Do not skip this on lithium. |
| 14 | 100 kΩ resistor (battery divider) | 2 | $0.10 | $0.20 | 2:1 divider into `PIN_BATTERY_ADC`. |
| | **Subtotal (battery build)** | | | **$19.70** | |

## Passives, wiring, hardware

| # | Part | Qty | Unit | Total | Notes |
|---|---|---|---|---|---|
| 15 | 470 µF 10 V electrolytic capacitor | 1 | $0.30 | $0.30 | Across the servo 5 V rail. Cheapest fix for random reboots. |
| 16 | 100 nF ceramic capacitor | 2 | $0.10 | $0.20 | Decoupling at the mic and amp. |
| 17 | Dupont jumper wires, 20 cm F-F | 40 | $0.05 | $2.00 | For the bench. Solder before you close the body. |
| 18 | 22 AWG silicone hookup wire, 3 colours | 1 | $4.00 | $4.00 | Power and servo runs. |
| 19 | Heat-shrink assortment | 1 | $2.00 | $2.00 | |
| 20 | M3 × 10 screws + nuts | 12 | $0.05 | $0.60 | |
| 21 | M2 × 6 self-tapping screws | 16 | $0.03 | $0.48 | |
| 22 | M2.5 × 6 self-tapping screws | 4 | $0.03 | $0.12 | |
| 23 | Slide switch, SPST | 1 | $0.50 | $0.50 | Main power. |
| | **Subtotal** | | | **$10.20** | |

## Filament

| # | Part | Qty | Unit | Total | Notes |
|---|---|---|---|---|---|
| 24 | PLA filament | 180 g | $0.02/g | $3.60 | Body, head, most parts. |
| 25 | PETG filament | 40 g | $0.025/g | $1.00 | Drive wheels, neck bracket. |
| 26 | TPU 95A filament | 25 g | $0.04/g | $1.00 | The two track belts. |
| | **Subtotal** | | | **$5.60** | |

## Totals

| Build | Parts | Cost |
|---|---|---|
| Voice assistant only | 1–6, 10a, 15–19 | **$41.50** |
| Full robot, USB powered | 1–9, 10a, 15–26 | **$63.30** |
| Full robot, battery powered | everything | **$83.00** |

These add up from [`bom.csv`](bom.csv), which is checked by
`docs/check_bom.py` — every line's `qty × unit` must equal its `total`, and the
group subtotals must match this table.

## The server

The server needs a machine that is on when you want to talk to the robot. Rough
guide, measured on the pipeline in this repo (see
[`04-latency.md`](04-latency.md)):

| Machine | Wake→speech | Notes |
|---|---|---|
| Mac Mini M2 / M4 | **0.9–1.4 s** | The sweet spot. Whisper uses Metal, Ollama uses the ANE/GPU. Idles at ~7 W. |
| Any PC with an NVIDIA GPU (≥ 6 GB) | 0.7–1.2 s | Fastest option. Use `faster-whisper` here, not whisper.cpp. |
| Modern laptop, CPU only | 1.8–3.0 s | Perfectly usable. Use `base.en` and a 3B model. |
| Raspberry Pi 5, 8 GB | 4–8 s | Works for STT and TTS. Too slow for a local LLM — point `WALLE_LLM_BACKEND` at a cloud API or another machine. |
| Raspberry Pi 4 | — | Not recommended. Whisper alone eats the whole board. |

If you are buying a machine for this, a used Mac Mini M1/M2 with 16 GB is the
best value: it runs the whole stack locally, silently, on about as much power as
a lightbulb.

## Things you do *not* need

Worth stating, because comparable projects list them:

- **No I2S DAC breakout.** The MAX98357A is amplifier and DAC in one.
- **No motor driver board.** Continuous-rotation servos take PWM directly.
- **No level shifters.** Everything here is 3.3 V logic. (The MAX98357A takes
  5 V *power* and 3.3 V *logic* — that is fine and intended.)
- **No SD card.** Models live on the server; the firmware fits in flash.
- **No microphone array.** One INMP441 plus the pre-roll buffer and
  server-side recognition is enough at desk distance. An array only earns its
  place across a room.
