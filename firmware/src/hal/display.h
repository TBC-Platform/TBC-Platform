// SPDX-License-Identifier: MIT
// OLED face + status rendering. Everything the robot "feels" is expressed here.
#pragma once

#include <stdint.h>

// Wall-E's emotional vocabulary. The server picks one of these by name; see
// FACE_NAMES in display.cpp for the wire strings.
enum Expression : uint8_t {
  EXPR_BOOT = 0,
  EXPR_IDLE,       // slow blink, eyes drift - the resting face
  EXPR_LISTENING,  // wide eyes + level meter
  EXPR_THINKING,   // eyes look up, dots cycle
  EXPR_SPEAKING,   // mouth bar animates with audio level
  EXPR_HAPPY,
  EXPR_SAD,
  EXPR_ANGRY,
  EXPR_CONFUSED,
  EXPR_LOVE,
  EXPR_SLEEP,
  EXPR_ERROR,
  EXPR_COUNT
};

namespace display {

// Brings up I2C + the SSD1306. Returns false if the panel does not ACK, which
// almost always means SDA/SCL are swapped or the module needs 5 V.
bool begin();

// Switches face. `holdMs` > 0 reverts to the previous face automatically -
// handy for a quick "happy" flash without the server having to send a second
// message. Cheap to call every loop with the same value.
void setExpression(Expression e, uint32_t holdMs = 0);

// Maps a protocol string ("happy") to an Expression. Returns EXPR_COUNT if the
// name is unknown so the caller can log it instead of showing a wrong face.
Expression expressionFromName(const char *name);
const char *expressionName(Expression e);

// 0..100 audio level, drives the listening meter and the speaking mouth.
void setAudioLevel(uint8_t level);

// One line of small text under the face. Pass nullptr to clear.
void setStatusLine(const char *text);

// Full-screen message for boot / fatal errors, drawn immediately.
void showBanner(const char *line1, const char *line2 = nullptr);

// Call from the main loop as often as you like; it redraws at a fixed ~20 fps
// and is a no-op in between, so it costs almost nothing.
void tick();

}  // namespace display
