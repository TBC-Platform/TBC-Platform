// SPDX-License-Identifier: MIT
#include "display.h"

#include "config.h"

#if WALLE_ENABLE_DISPLAY

#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Arduino.h>
#include <Wire.h>
#include <esp_random.h>
#include <string.h>

namespace {

Adafruit_SSD1306 gfx(OLED_WIDTH, OLED_HEIGHT, &Wire, -1);
bool gReady = false;

Expression gExpr = EXPR_BOOT;
Expression gPrevExpr = EXPR_IDLE;
uint32_t gExprUntil = 0;   // 0 = hold forever
uint8_t gLevel = 0;        // 0..100 audio level
char gStatus[24] = {0};

uint32_t gLastDraw = 0;
uint32_t gFrame = 0;       // animation clock, ticks at the redraw rate
uint32_t gNextBlink = 0;
uint32_t gBlinkUntil = 0;

// Redraw at 20 fps. Faster looks no better on an SSD1306 over I2C at 400 kHz
// and starts stealing time from the audio pipeline.
constexpr uint32_t kFrameIntervalMs = 50;

// Wire names, index-aligned with the Expression enum.
const char *const FACE_NAMES[EXPR_COUNT] = {
    "boot", "idle",  "listening", "thinking", "speaking", "happy",
    "sad",  "angry", "confused",  "love",     "sleep",    "error"};

// Wall-E's eyes are two big pods. Everything below is drawn relative to these
// so the whole face scales if you move to a different panel.
constexpr int16_t kEyeR = 19;         // eye pod radius
constexpr int16_t kEyeCyBase = 30;    // eye centre Y
constexpr int16_t kEyeLcx = 34;       // left eye centre X
constexpr int16_t kEyeRcx = 94;       // right eye centre X

void drawEye(int16_t cx, int16_t cy, int16_t r, int16_t pupilDx, int16_t pupilDy,
             int16_t pupilR, bool closed) {
  if (closed) {
    // A closed eye is just the lid line - reads much better than a filled slit.
    gfx.drawFastHLine(cx - r, cy, 2 * r, SSD1306_WHITE);
    gfx.drawFastHLine(cx - r + 2, cy + 1, 2 * r - 4, SSD1306_WHITE);
    return;
  }
  gfx.drawCircle(cx, cy, r, SSD1306_WHITE);
  gfx.drawCircle(cx, cy, r - 1, SSD1306_WHITE);
  if (pupilR > 0) gfx.fillCircle(cx + pupilDx, cy + pupilDy, pupilR, SSD1306_WHITE);
}

// Eyebrow-ish arc above an eye. Angle is faked with a straight line because a
// real arc costs float math for no visual gain at this size.
void drawBrow(int16_t cx, int16_t cy, int16_t r, int8_t tilt) {
  int16_t y = cy - r - 4;
  gfx.drawLine(cx - r, y - tilt, cx + r, y + tilt, SSD1306_WHITE);
  gfx.drawLine(cx - r, y - tilt + 1, cx + r, y + tilt + 1, SSD1306_WHITE);
}

void drawMouthBar(uint8_t level) {
  // Mouth height follows the audio envelope so speech looks synced.
  int16_t h = 2 + (level * 10) / 100;
  int16_t w = 26 + (level * 20) / 100;
  gfx.fillRoundRect(OLED_WIDTH / 2 - w / 2, 52 - h / 2, w, h, 2, SSD1306_WHITE);
}

void drawLevelMeter(uint8_t level) {
  // Seven bars along the bottom; a simple, glanceable "I can hear you".
  const int16_t bars = 7;
  const int16_t lit = (level * bars + 50) / 100;
  for (int16_t i = 0; i < bars; i++) {
    int16_t h = 3 + i * 2;
    int16_t x = 20 + i * 13;
    int16_t y = 60 - h;
    if (i < lit) {
      gfx.fillRect(x, y, 8, h, SSD1306_WHITE);
    } else {
      gfx.drawRect(x, y, 8, h, SSD1306_WHITE);
    }
  }
}

void drawStatus() {
  if (!gStatus[0]) return;
  gfx.setTextSize(1);
  gfx.setTextColor(SSD1306_WHITE);
  int16_t w = strlen(gStatus) * 6;
  gfx.setCursor((OLED_WIDTH - w) / 2, 0);
  gfx.print(gStatus);
}

void drawFace(Expression e, uint32_t frame) {
  // A blink is a short global event so both eyes always close together.
  const bool blinking = millis() < gBlinkUntil;
  const int16_t breathe = (int16_t)((frame / 12) % 2);  // 1 px idle bob

  switch (e) {
    case EXPR_BOOT:
      drawEye(kEyeLcx, kEyeCyBase, kEyeR, 0, 0, 4, false);
      drawEye(kEyeRcx, kEyeCyBase, kEyeR, 0, 0, 4, false);
      break;

    case EXPR_IDLE: {
      // Eyes drift slowly left/right - the difference between "off" and "alive".
      const int16_t phase = (frame / 30) % 4;
      const int16_t dx = (phase == 1) ? 4 : (phase == 3) ? -4 : 0;
      drawEye(kEyeLcx, kEyeCyBase + breathe, kEyeR, dx, 0, 6, blinking);
      drawEye(kEyeRcx, kEyeCyBase + breathe, kEyeR, dx, 0, 6, blinking);
      break;
    }

    case EXPR_LISTENING:
      drawEye(kEyeLcx, kEyeCyBase - 3, kEyeR, 0, 0, 8, false);
      drawEye(kEyeRcx, kEyeCyBase - 3, kEyeR, 0, 0, 8, false);
      drawLevelMeter(gLevel);
      break;

    case EXPR_THINKING: {
      // Looking up and away, with a cycling ellipsis.
      drawEye(kEyeLcx, kEyeCyBase, kEyeR, 5, -6, 6, false);
      drawEye(kEyeRcx, kEyeCyBase, kEyeR, 5, -6, 6, false);
      const int16_t dots = (frame / 8) % 4;
      for (int16_t i = 0; i < dots; i++) {
        gfx.fillCircle(52 + i * 12, 58, 2, SSD1306_WHITE);
      }
      break;
    }

    case EXPR_SPEAKING:
      drawEye(kEyeLcx, kEyeCyBase - 4, kEyeR - 2, 0, 0, 6, blinking);
      drawEye(kEyeRcx, kEyeCyBase - 4, kEyeR - 2, 0, 0, 6, blinking);
      drawMouthBar(gLevel);
      break;

    case EXPR_HAPPY:
      // Upward arcs: the classic "^ ^" eyes, drawn as two chevrons.
      for (int16_t i = 0; i < 3; i++) {
        gfx.drawLine(kEyeLcx - kEyeR, kEyeCyBase + 8 + i, kEyeLcx, kEyeCyBase - 10 + i, SSD1306_WHITE);
        gfx.drawLine(kEyeLcx, kEyeCyBase - 10 + i, kEyeLcx + kEyeR, kEyeCyBase + 8 + i, SSD1306_WHITE);
        gfx.drawLine(kEyeRcx - kEyeR, kEyeCyBase + 8 + i, kEyeRcx, kEyeCyBase - 10 + i, SSD1306_WHITE);
        gfx.drawLine(kEyeRcx, kEyeCyBase - 10 + i, kEyeRcx + kEyeR, kEyeCyBase + 8 + i, SSD1306_WHITE);
      }
      break;

    case EXPR_SAD:
      drawEye(kEyeLcx, kEyeCyBase + 2, kEyeR - 2, -3, 4, 6, false);
      drawEye(kEyeRcx, kEyeCyBase + 2, kEyeR - 2, 3, 4, 6, false);
      drawBrow(kEyeLcx, kEyeCyBase + 2, kEyeR - 2, 5);
      drawBrow(kEyeRcx, kEyeCyBase + 2, kEyeR - 2, -5);
      break;

    case EXPR_ANGRY:
      drawEye(kEyeLcx, kEyeCyBase + 2, kEyeR - 3, 2, 0, 7, false);
      drawEye(kEyeRcx, kEyeCyBase + 2, kEyeR - 3, -2, 0, 7, false);
      drawBrow(kEyeLcx, kEyeCyBase + 2, kEyeR - 3, -7);
      drawBrow(kEyeRcx, kEyeCyBase + 2, kEyeR - 3, 7);
      break;

    case EXPR_CONFUSED: {
      // One eye squints, the other is wide - reads as "huh?" instantly.
      drawEye(kEyeLcx, kEyeCyBase, kEyeR, 0, 0, 5, false);
      gfx.drawFastHLine(kEyeRcx - kEyeR, kEyeCyBase, 2 * kEyeR, SSD1306_WHITE);
      gfx.drawCircle(kEyeRcx, kEyeCyBase, kEyeR - 6, SSD1306_WHITE);
      gfx.setTextSize(2);
      gfx.setTextColor(SSD1306_WHITE);
      gfx.setCursor(OLED_WIDTH - 14, 2);
      if ((frame / 10) % 2) gfx.print('?');
      break;
    }

    case EXPR_LOVE:
      // Two hearts: a triangle with two lobes on top.
      for (int16_t cx : {kEyeLcx, kEyeRcx}) {
        gfx.fillCircle(cx - 7, kEyeCyBase - 4, 8, SSD1306_WHITE);
        gfx.fillCircle(cx + 7, kEyeCyBase - 4, 8, SSD1306_WHITE);
        gfx.fillTriangle(cx - 15, kEyeCyBase - 1, cx + 15, kEyeCyBase - 1,
                         cx, kEyeCyBase + 15, SSD1306_WHITE);
      }
      break;

    case EXPR_SLEEP: {
      gfx.drawFastHLine(kEyeLcx - kEyeR, kEyeCyBase, 2 * kEyeR, SSD1306_WHITE);
      gfx.drawFastHLine(kEyeRcx - kEyeR, kEyeCyBase, 2 * kEyeR, SSD1306_WHITE);
      gfx.setTextSize(1);
      gfx.setTextColor(SSD1306_WHITE);
      const int16_t z = (frame / 20) % 3;
      for (int16_t i = 0; i <= z; i++) {
        gfx.setCursor(100 + i * 6, 20 - i * 6);
        gfx.print('z');
      }
      break;
    }

    case EXPR_ERROR:
      // X eyes.
      for (int16_t cx : {kEyeLcx, kEyeRcx}) {
        gfx.drawLine(cx - 12, kEyeCyBase - 12, cx + 12, kEyeCyBase + 12, SSD1306_WHITE);
        gfx.drawLine(cx + 12, kEyeCyBase - 12, cx - 12, kEyeCyBase + 12, SSD1306_WHITE);
      }
      break;

    default:
      break;
  }
}

}  // namespace

namespace display {

bool begin() {
  Wire.begin(PIN_OLED_SDA, PIN_OLED_SCL);
  Wire.setClock(400000);
  if (!gfx.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDR)) {
    return false;
  }
  gReady = true;
  gfx.clearDisplay();
  gfx.display();
  gNextBlink = millis() + 3000;
  return true;
}

void setExpression(Expression e, uint32_t holdMs) {
  if (e >= EXPR_COUNT) return;
  if (holdMs > 0) {
    // Remember where to fall back to, but never stack temporary faces.
    if (gExprUntil == 0) gPrevExpr = gExpr;
    gExprUntil = millis() + holdMs;
  } else {
    gExprUntil = 0;
    gPrevExpr = e;
  }
  gExpr = e;
}

Expression expressionFromName(const char *name) {
  if (!name) return EXPR_COUNT;
  for (uint8_t i = 0; i < EXPR_COUNT; i++) {
    if (strcmp(name, FACE_NAMES[i]) == 0) return static_cast<Expression>(i);
  }
  return EXPR_COUNT;
}

const char *expressionName(Expression e) {
  return (e < EXPR_COUNT) ? FACE_NAMES[e] : "?";
}

void setAudioLevel(uint8_t level) { gLevel = level > 100 ? 100 : level; }

void setStatusLine(const char *text) {
  if (!text) {
    gStatus[0] = '\0';
    return;
  }
  strncpy(gStatus, text, sizeof(gStatus) - 1);
  gStatus[sizeof(gStatus) - 1] = '\0';
}

void showBanner(const char *line1, const char *line2) {
  if (!gReady) return;
  gfx.clearDisplay();
  gfx.setTextSize(1);
  gfx.setTextColor(SSD1306_WHITE);
  gfx.setCursor(0, 24);
  gfx.println(line1);
  if (line2) gfx.println(line2);
  gfx.display();
  gLastDraw = millis();  // don't let tick() immediately overwrite it
}

void tick() {
  if (!gReady) return;
  const uint32_t now = millis();
  if (now - gLastDraw < kFrameIntervalMs) return;
  gLastDraw = now;
  gFrame++;

  // Auto-revert temporary expressions.
  if (gExprUntil && now > gExprUntil) {
    gExprUntil = 0;
    gExpr = gPrevExpr;
  }

  // Blink on a randomised schedule; regular blinking looks robotic in the
  // wrong way.
  if (now > gNextBlink) {
    gBlinkUntil = now + 120;
    gNextBlink = now + 2500 + (esp_random() % 3500);
  }

  gfx.clearDisplay();
  drawFace(gExpr, gFrame);
  drawStatus();
  gfx.display();
}

}  // namespace display

#else  // !WALLE_ENABLE_DISPLAY

// Stubs so the rest of the firmware compiles unchanged with the OLED disabled.
namespace display {
bool begin() { return true; }
void setExpression(Expression, uint32_t) {}
Expression expressionFromName(const char *) { return EXPR_COUNT; }
const char *expressionName(Expression) { return "?"; }
void setAudioLevel(uint8_t) {}
void setStatusLine(const char *) {}
void showBanner(const char *, const char *) {}
void tick() {}
}  // namespace display

#endif
