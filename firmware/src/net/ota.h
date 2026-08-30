// SPDX-License-Identifier: MIT
// Over-the-air firmware updates. Two paths, both password protected:
//   * ArduinoOTA  - push from PlatformIO / Arduino IDE over the LAN.
//   * pullUpdate  - the server tells the robot to fetch a .bin over HTTP(S).
#pragma once

#include <stdint.h>

namespace ota {

bool begin();
void tick();

// Downloads and flashes a firmware image, then reboots. Only ever called in
// response to an authenticated `ota` control message. Returns false if the
// download or the flash write failed - on success it does not return at all.
bool pullUpdate(const char *url);

// True while an update is in flight, so the main loop can stop the motors and
// stop capturing audio.
bool inProgress();

// 0..100 progress, for the OLED.
uint8_t progress();

}  // namespace ota
