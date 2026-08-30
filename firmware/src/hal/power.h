// SPDX-License-Identifier: MIT
// Battery monitoring and the low-voltage motion cutout.
#pragma once

#include <stdint.h>

namespace power {

bool begin();

// Pack voltage in millivolts, averaged over the last few seconds. Returns 0
// when battery sensing is disabled (i.e. running on USB).
uint16_t batteryMillivolts();

// Rough state-of-charge for a 2S Li-ion pack, 0..100.
uint8_t batteryPercent();

bool isLow();       // warn the user
bool isCritical();  // motion is inhibited

void tick();

}  // namespace power
