// SPDX-License-Identifier: MIT
// Track drive (2x continuous-rotation servos) + head pan servo.
#pragma once

#include <stdint.h>

enum MoveCmd : uint8_t {
  MOVE_STOP = 0,
  MOVE_FORWARD,
  MOVE_BACK,
  MOVE_LEFT,   // spin in place, counter-clockwise
  MOVE_RIGHT,  // spin in place, clockwise
  MOVE_WIGGLE, // short celebratory shimmy
};

namespace motors {

bool begin();

// Drives for `durationMs` at `speed` (0..100) then stops itself. A duration of
// 0 means "until the next command", but the watchdog still stops the tracks
// after MOTOR_WATCHDOG_MS - a robot that keeps driving because Wi-Fi dropped
// is a robot that drives off the desk.
void move(MoveCmd cmd, uint8_t speed, uint32_t durationMs);

// Cuts drive immediately and detaches the servos so they stop buzzing.
void stop();

// Absolute head angle in degrees, clamped to the limits in config.h.
void setHead(int16_t degrees);
int16_t headAngle();

// Blocks nothing; call every loop.
void tick();

bool isMoving();

// Motion is refused while this is set - used for the low-battery cutout.
void setInhibited(bool inhibited);

MoveCmd cmdFromName(const char *name);

}  // namespace motors
