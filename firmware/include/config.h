// SPDX-License-Identifier: MIT
// ---------------------------------------------------------------------------
// Wall-E build configuration: pin map, tunables, feature switches.
//
// Everything a builder might reasonably want to change lives in this one file.
// Nothing secret goes here - Wi-Fi and server credentials belong in
// secrets.h (copy secrets.example.h, it is git-ignored).
//
// The default pin map targets the *Freenove ESP32-S3-WROOM CAM* board, because
// it is cheap, has 8 MB PSRAM (needed for camera frames + audio buffers) and
// still breaks out enough free GPIOs for two I2S buses, an OLED and servos.
// Camera pins on that board are hard-wired and must not be changed.
// ---------------------------------------------------------------------------
#pragma once

// ------------------------------- identity ---------------------------------
#define WALLE_DEVICE_NAME "walle-01"
#define WALLE_FW_VERSION "1.0.0"

// ------------------------------- features ---------------------------------
// Turn a subsystem off and its code drops out of the build. Useful for
// bring-up: get Wi-Fi + audio working before you solder the camera on.
//
// Every one is #ifndef-guarded so a PlatformIO env can override it with a -D
// flag. Without the guard these unconditional defines win over the command
// line, and the `walle-minimal` bring-up build would still compile in the
// camera and motors it promises to leave out.
#ifndef WALLE_ENABLE_CAMERA
#define WALLE_ENABLE_CAMERA 1
#endif
#ifndef WALLE_ENABLE_DISPLAY
#define WALLE_ENABLE_DISPLAY 1
#endif
#ifndef WALLE_ENABLE_MOTORS
#define WALLE_ENABLE_MOTORS 1
#endif
#ifndef WALLE_ENABLE_OTA
#define WALLE_ENABLE_OTA 1
#endif
#ifndef WALLE_ENABLE_BATTERY
#define WALLE_ENABLE_BATTERY 1
#endif

// Wake-word backend:
//   0 = push-to-talk only (BOOT button). Always compiles, zero dependencies.
//   1 = ESP-SR WakeNet ("Hi ESP" / "Alexa" models shipped by Espressif).
// See docs/03-research-notes.md for why ESP-SR wins on an S3.
#ifndef WALLE_WAKEWORD_BACKEND
#define WALLE_WAKEWORD_BACKEND 1
#endif

// --------------------------- camera pins (fixed) ---------------------------
// Freenove ESP32-S3-WROOM CAM / OV2640. Do not reassign.
#define CAM_PIN_PWDN -1
#define CAM_PIN_RESET -1
#define CAM_PIN_XCLK 15
#define CAM_PIN_SIOD 4
#define CAM_PIN_SIOC 5
#define CAM_PIN_D7 16
#define CAM_PIN_D6 17
#define CAM_PIN_D5 18
#define CAM_PIN_D4 12
#define CAM_PIN_D3 10
#define CAM_PIN_D2 8
#define CAM_PIN_D1 9
#define CAM_PIN_D0 11
#define CAM_PIN_VSYNC 6
#define CAM_PIN_HREF 7
#define CAM_PIN_PCLK 13

// ------------------------------ OLED (I2C) --------------------------------
// SSD1306 128x64. Its own I2C bus, separate from the camera's SCCB lines.
#define PIN_OLED_SDA 47
#define PIN_OLED_SCL 48
#define OLED_I2C_ADDR 0x3C
#define OLED_WIDTH 128
#define OLED_HEIGHT 64

// ----------------------- microphone: I2S RX (INMP441) ----------------------
// I2S peripheral 0. INMP441 L/R pin tied to GND => data on the left channel.
#define PIN_MIC_WS 38   // LRCL / word select
#define PIN_MIC_SCK 39  // BCLK
#define PIN_MIC_SD 40   // DOUT of mic -> DIN of ESP32

// ------------------- speaker: I2S TX (MAX98357A class-D) -------------------
// I2S peripheral 1. Leave the amp's GAIN pin floating for 9 dB.
#define PIN_SPK_BCLK 41
#define PIN_SPK_LRC 42
#define PIN_SPK_DIN 21

// --------------------------- servos & buttons ------------------------------
// FS90R / MG90S continuous-rotation servos drive the two tracks. A third
// standard (180 deg) servo pans the head. All driven by LEDC hardware PWM.
#define PIN_SERVO_LEFT 2
#define PIN_SERVO_RIGHT 3
#define PIN_SERVO_HEAD 14
#define PIN_BUTTON 0  // on-board BOOT button: push-to-talk / factory reset

// Continuous-rotation servos are centred at ~1500 us. Every servo is slightly
// off; trim these until the tracks are still when the robot is idle.
#define SERVO_LEFT_STOP_US 1500
#define SERVO_RIGHT_STOP_US 1500
// Full speed offset from stop. 200-400 us is the usable range on FS90R.
#define SERVO_SPAN_US 320
// The two servos face opposite directions, so one must be inverted.
#define SERVO_LEFT_INVERT 0
#define SERVO_RIGHT_INVERT 1
// Head pan limits in degrees, measured from the mechanical centre.
#define SERVO_HEAD_MIN_DEG 45
#define SERVO_HEAD_MAX_DEG 135
// Motion never runs longer than this without a fresh command (dead-man switch).
#define MOTOR_WATCHDOG_MS 1500

// ------------------------------- battery ----------------------------------
// 2S Li-ion (7.4 V nominal) through a divider into ADC1. With 100k/100k the
// ratio is 2.0; adjust if you use different resistors.
#define PIN_BATTERY_ADC 1
#define BATTERY_DIVIDER_RATIO 2.0f
#define BATTERY_LOW_MV 6600   // warn: ~3.3 V/cell
#define BATTERY_CRIT_MV 6200  // stop moving: ~3.1 V/cell

// ------------------------------ networking --------------------------------
#define WIFI_CONNECT_TIMEOUT_MS 20000
#define WS_RECONNECT_MIN_MS 500
#define WS_RECONNECT_MAX_MS 15000
#define WS_PING_INTERVAL_MS 10000
#define STATE_REPORT_INTERVAL_MS 5000

// --------------------------- voice capture tuning --------------------------
// Rolling pre-roll kept before the wake word fires, so the first syllable of
// "Wall-E, what's the weather" is never clipped.
#define VOICE_PREROLL_MS 320
// Stop capturing after this much trailing silence...
#define VOICE_SILENCE_TAIL_MS 700
// ...but never listen longer than this in one turn.
#define VOICE_MAX_UTTERANCE_MS 12000
// Energy threshold for the on-device VAD, in RMS units of the 16 bit signal.
// Tune with `walle-mic-monitor` (see firmware/README.md).
#define VOICE_VAD_RMS_THRESHOLD 900

// ------------------------------ diagnostics -------------------------------
#define WALLE_SERIAL_BAUD 115200
#define WALLE_LOG_LEVEL 3  // 0=off 1=err 2=warn 3=info 4=debug
