# Firmware

ESP32-S3, Arduino framework, built with PlatformIO.

## Build and flash

```bash
pio run                      # build
pio run -t upload            # flash over USB
pio device monitor           # watch the log
```

Three build environments:

| Environment | What it is for |
|---|---|
| `walle` | The full robot. The default. |
| `walle-minimal` | Bring-up: no camera, no motors, push-to-talk instead of the wake word. Start here. |
| `walle-ota` | Flash over Wi-Fi. Set `upload_port` to the robot's IP and `--auth` to your OTA password. |

```bash
pio run -e walle-minimal -t upload      # bring-up build
pio run -e walle-ota -t upload          # update over Wi-Fi, no cable
```

### The wake-word models

Offline wake-word detection needs Espressif's ESP-SR models flashed into the
`model` partition. If they are absent, `wakeword::begin()` returns false, the
boot log says `wake word backend: button only`, and the robot falls back to
push-to-talk — it does not fail to build or refuse to run.

The pioarduino platform packages ESP-SR with the Arduino core, so a normal
`pio run -t upload` handles it on an ESP32-S3 with the partition table in
`partitions.csv`. If detection never fires, check the boot log first: it prints
which backend actually came up.

## Layout

```
include/
  config.h            every pin and tunable. Start here.
  secrets.example.h   copy to secrets.h (git-ignored)
src/
  main.cpp            state machine and the main loop
  core/protocol.h     wire format, mirrored in server/walle/protocol.py
  hal/                display, motors, audio_in, audio_out, camera, power
  voice/              wakeword (ESP-SR), vad
  net/                wifi_manager, ws_link, ota
partitions.csv        8 MB layout: two OTA app slots + the ESP-SR models
```

## How the loop works

`audio_in::readFrame()` blocks until the I2S DMA has exactly 20 ms of audio.
That makes the microphone the system clock: the main loop runs at a steady
50 Hz, every subsystem is serviced between audio frames, and there is not a
single `delay()` in the firmware.

```
loop:
  readFrame()          <- blocks ~20 ms, paces everything
  service wifi, websocket, ota, motors, battery, display, button, audio out
  feed the wake word detector
  run the state machine
```

The I2S DMA holds 160 ms of slack, so a display redraw or a Wi-Fi retry cannot
drop audio.

## Tuning

Everything is in `include/config.h`.

| Symptom | Change |
|---|---|
| Tracks creep when idle | `SERVO_LEFT_STOP_US` / `SERVO_RIGHT_STOP_US`, a few µs at a time |
| A track runs backwards | `SERVO_LEFT_INVERT` / `SERVO_RIGHT_INVERT` |
| Cuts you off mid-sentence | Raise `VOICE_SILENCE_TAIL_MS` |
| Slow to realise you stopped | Lower `VOICE_SILENCE_TAIL_MS` |
| Triggers on background noise | Raise `VOICE_VAD_RMS_THRESHOLD` |
| Misses quiet speech | Lower `VOICE_VAD_RMS_THRESHOLD`, or raise the mic gain (`kMicShiftBits` in `hal/audio_in.cpp`) |
| Wakes up to the TV | `DET_MODE_95` instead of `DET_MODE_90` in `voice/wakeword.cpp` |
| Clips the first word | Raise `VOICE_PREROLL_MS` |
| Robot too quiet | Tie the MAX98357A's GAIN pin to GND (12 dB) |

To disable a subsystem entirely, set its `WALLE_ENABLE_*` flag to 0 — the code
drops out of the build and stubs take its place, so everything else still
compiles and runs.

## Serial log

At 115200 baud:

```
Wall-E 1.0.0 booting
[init] wifi ok, ip=192.168.1.61
[init] wake word backend: esp-sr
[link] server ready
[state] -> idle
[voice] listening (wakeword), preroll 5120 samples
[state] -> thinking
[say] Paris is the capital of France.
[state] -> speaking
[state] -> idle
```

`monitor_filters = esp32_exception_decoder` is already set, so a crash prints a
decoded stack trace with file names and line numbers instead of raw addresses.

## Arduino IDE

PlatformIO is the supported route, because the ESP-SR models, the partition
table and the library versions are all pinned in `platformio.ini`. To use the
Arduino IDE anyway: install the ESP32 core 3.x, select an ESP32-S3 board with
8 MB flash and PSRAM enabled, install the libraries listed under `lib_deps`,
flatten `src/` into one sketch folder, and select a partition scheme with two
OTA slots. It works; it is just more steps to get wrong.

## Protocol

The wire format is defined once in `src/core/protocol.h` and mirrored in
`server/walle/protocol.py`. `server/tests/test_protocol.py` checks both halves:
it parses the C header so the constants cannot drift, and it compiles
`test/test_protocol_host.cpp` with `g++ -Werror` to run the *actual* C encoder
and decoder against the Python ones byte for byte.

That host build is worth more than it looks. `protocol.h` is plain C++ with no
Arduino dependency, so it needs no Xtensa toolchain — and because the harness
includes it first, with warnings as errors, a header that quietly leans on
`Arduino.h` for `size_t` fails in a two second test instead of two thirds of
the way through a firmware build.
