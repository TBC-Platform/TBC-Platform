// SPDX-License-Identifier: MIT
#include "motors.h"

#include "config.h"

#if WALLE_ENABLE_MOTORS

#include <Arduino.h>
#include <ESP32Servo.h>

namespace {

Servo gLeft, gRight, gHead;
bool gReady = false;
bool gInhibited = false;

MoveCmd gCmd = MOVE_STOP;
uint8_t gSpeed = 0;
uint32_t gStopAt = 0;   // millis deadline, 0 while stopped
int16_t gHeadDeg = (SERVO_HEAD_MIN_DEG + SERVO_HEAD_MAX_DEG) / 2;

uint32_t gWiggleNext = 0;
uint8_t gWigglePhase = 0;

// Converts a signed -100..100 throttle into a servo pulse width. Continuous
// rotation servos read pulse width as *speed*, not position: STOP_US means
// stationary and the sign of the offset picks the direction.
int leftPulse(int throttle) {
  if (SERVO_LEFT_INVERT) throttle = -throttle;
  return SERVO_LEFT_STOP_US + (throttle * SERVO_SPAN_US) / 100;
}
int rightPulse(int throttle) {
  if (SERVO_RIGHT_INVERT) throttle = -throttle;
  return SERVO_RIGHT_STOP_US + (throttle * SERVO_SPAN_US) / 100;
}

void drive(int leftThrottle, int rightThrottle) {
  if (!gReady) return;
  gLeft.writeMicroseconds(leftPulse(leftThrottle));
  gRight.writeMicroseconds(rightPulse(rightThrottle));
}

}  // namespace

namespace motors {

bool begin() {
  // ESP32Servo needs explicit LEDC timer allocation on the S3.
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  gLeft.setPeriodHertz(50);
  gRight.setPeriodHertz(50);
  gHead.setPeriodHertz(50);
  gLeft.attach(PIN_SERVO_LEFT, 500, 2500);
  gRight.attach(PIN_SERVO_RIGHT, 500, 2500);
  gHead.attach(PIN_SERVO_HEAD, 500, 2500);
  gReady = true;
  drive(0, 0);
  gHead.write(gHeadDeg);
  return true;
}

void move(MoveCmd cmd, uint8_t speed, uint32_t durationMs) {
  if (!gReady) return;
  if (gInhibited && cmd != MOVE_STOP) return;
  if (speed > 100) speed = 100;

  gCmd = cmd;
  gSpeed = speed;

  // Every motion is time-boxed. Explicit duration wins, otherwise the
  // dead-man watchdog applies.
  uint32_t limit = durationMs ? durationMs : MOTOR_WATCHDOG_MS;
  if (limit > MOTOR_WATCHDOG_MS && cmd != MOVE_WIGGLE) limit = MOTOR_WATCHDOG_MS;
  gStopAt = (cmd == MOVE_STOP) ? 0 : millis() + limit;

  switch (cmd) {
    case MOVE_FORWARD: drive(speed, speed); break;
    case MOVE_BACK:    drive(-(int)speed, -(int)speed); break;
    case MOVE_LEFT:    drive(-(int)speed, speed); break;
    case MOVE_RIGHT:   drive(speed, -(int)speed); break;
    case MOVE_WIGGLE:
      gWigglePhase = 0;
      gWiggleNext = millis();
      break;
    case MOVE_STOP:
    default:
      drive(0, 0);
      gCmd = MOVE_STOP;
      break;
  }
}

void stop() {
  gCmd = MOVE_STOP;
  gStopAt = 0;
  drive(0, 0);
}

void setHead(int16_t degrees) {
  if (!gReady) return;
  if (degrees < SERVO_HEAD_MIN_DEG) degrees = SERVO_HEAD_MIN_DEG;
  if (degrees > SERVO_HEAD_MAX_DEG) degrees = SERVO_HEAD_MAX_DEG;
  gHeadDeg = degrees;
  gHead.write(gHeadDeg);
}

int16_t headAngle() { return gHeadDeg; }

bool isMoving() { return gCmd != MOVE_STOP; }

void setInhibited(bool inhibited) {
  gInhibited = inhibited;
  if (inhibited) stop();
}

void tick() {
  if (!gReady || gCmd == MOVE_STOP) return;
  const uint32_t now = millis();

  if (gCmd == MOVE_WIGGLE && now >= gWiggleNext) {
    // Alternate spin directions six times, ~120 ms each.
    if (gWigglePhase >= 6) {
      stop();
      return;
    }
    const int t = (gWigglePhase % 2) ? gSpeed : -(int)gSpeed;
    drive(t, -t);
    gWigglePhase++;
    gWiggleNext = now + 120;
    return;
  }

  if (gStopAt && now >= gStopAt) stop();
}

MoveCmd cmdFromName(const char *name) {
  if (!name) return MOVE_STOP;
  if (!strcmp(name, "forward")) return MOVE_FORWARD;
  if (!strcmp(name, "back") || !strcmp(name, "backward")) return MOVE_BACK;
  if (!strcmp(name, "left")) return MOVE_LEFT;
  if (!strcmp(name, "right")) return MOVE_RIGHT;
  if (!strcmp(name, "wiggle") || !strcmp(name, "dance")) return MOVE_WIGGLE;
  return MOVE_STOP;
}

}  // namespace motors

#else  // !WALLE_ENABLE_MOTORS

namespace motors {
bool begin() { return true; }
void move(MoveCmd, uint8_t, uint32_t) {}
void stop() {}
void setHead(int16_t) {}
int16_t headAngle() { return 90; }
void tick() {}
bool isMoving() { return false; }
void setInhibited(bool) {}
MoveCmd cmdFromName(const char *) { return MOVE_STOP; }
}  // namespace motors

#endif
