// SPDX-License-Identifier: MIT
#include "power.h"

#include "config.h"

#if WALLE_ENABLE_BATTERY

#include <Arduino.h>

namespace {
uint32_t gLastRead = 0;
float gFiltered = 0.0f;  // exponential average, millivolts
bool gPrimed = false;

// Discharge curve for one Li-ion cell, 4.20 V down to 3.00 V. Voltage is a
// poor fuel gauge under load, but it is honest enough for "am I about to die".
uint8_t cellPercent(float cellMv) {
  static const struct { float mv; uint8_t pct; } curve[] = {
      {4200, 100}, {4100, 92}, {4000, 82}, {3900, 70}, {3800, 58},
      {3700, 45},  {3600, 30}, {3500, 18}, {3400, 10}, {3300, 5},
      {3200, 2},   {3000, 0},
  };
  const size_t n = sizeof(curve) / sizeof(curve[0]);
  if (cellMv >= curve[0].mv) return 100;
  for (size_t i = 1; i < n; i++) {
    if (cellMv >= curve[i].mv) {
      const float span = curve[i - 1].mv - curve[i].mv;
      const float frac = (cellMv - curve[i].mv) / span;
      return (uint8_t)(curve[i].pct + frac * (curve[i - 1].pct - curve[i].pct));
    }
  }
  return 0;
}
}  // namespace

namespace power {

bool begin() {
  analogReadResolution(12);
  // 12 dB attenuation gives a usable range up to ~3.1 V at the pin, which with
  // a 2:1 divider covers a full 2S pack.
  analogSetPinAttenuation(PIN_BATTERY_ADC, ADC_11db);
  return true;
}

void tick() {
  const uint32_t now = millis();
  if (now - gLastRead < 500) return;
  gLastRead = now;

  // analogReadMilliVolts uses the factory eFuse calibration, which is far more
  // accurate than scaling the raw count ourselves.
  const uint32_t pinMv = analogReadMilliVolts(PIN_BATTERY_ADC);
  const float packMv = pinMv * BATTERY_DIVIDER_RATIO;

  if (!gPrimed) {
    gFiltered = packMv;
    gPrimed = true;
  } else {
    // Heavy smoothing: servo current spikes drag the pack voltage down
    // momentarily and we do not want that to trip the cutout.
    gFiltered = gFiltered * 0.9f + packMv * 0.1f;
  }
}

uint16_t batteryMillivolts() { return gPrimed ? (uint16_t)gFiltered : 0; }

uint8_t batteryPercent() {
  if (!gPrimed) return 0;
  return cellPercent(gFiltered / 2.0f);  // 2S pack
}

bool isLow() { return gPrimed && gFiltered < BATTERY_LOW_MV; }
bool isCritical() { return gPrimed && gFiltered < BATTERY_CRIT_MV; }

}  // namespace power

#else  // !WALLE_ENABLE_BATTERY

namespace power {
bool begin() { return true; }
uint16_t batteryMillivolts() { return 0; }
uint8_t batteryPercent() { return 0; }
bool isLow() { return false; }
bool isCritical() { return false; }
void tick() {}
}  // namespace power

#endif
