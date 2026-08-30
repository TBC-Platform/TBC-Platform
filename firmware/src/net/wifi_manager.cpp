// SPDX-License-Identifier: MIT
#include "wifi_manager.h"

#include <Arduino.h>
#include <WiFi.h>

#include "config.h"
#include "secrets.h"

namespace {
char gIp[16] = "0.0.0.0";
uint32_t gNextRetry = 0;
uint32_t gBackoffMs = 1000;
}  // namespace

namespace wifi_manager {

bool begin() {
  WiFi.persistent(false);   // stop rewriting flash on every boot
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  // Sleep mode saves ~30 mA but adds tens of milliseconds of latency to every
  // packet, which is exactly what this project cannot afford. A robot on a
  // desk is usually near its charger; responsiveness wins.
  WiFi.setSleep(false);
  WiFi.setHostname(WALLE_DEVICE_NAME);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  const uint32_t deadline = millis() + WIFI_CONNECT_TIMEOUT_MS;
  while (WiFi.status() != WL_CONNECTED && millis() < deadline) {
    delay(100);
  }
  if (WiFi.status() == WL_CONNECTED) {
    strncpy(gIp, WiFi.localIP().toString().c_str(), sizeof(gIp) - 1);
    gIp[sizeof(gIp) - 1] = '\0';
    return true;
  }
  return false;
}

void tick() {
  if (WiFi.status() == WL_CONNECTED) {
    gBackoffMs = 1000;
    return;
  }
  const uint32_t now = millis();
  if (now < gNextRetry) return;

  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  gNextRetry = now + gBackoffMs;
  // Exponential backoff capped at 30 s: a router rebooting should not be met
  // with a connection attempt every 100 ms for ten minutes.
  gBackoffMs = gBackoffMs < 30000 ? gBackoffMs * 2 : 30000;
}

bool isConnected() { return WiFi.status() == WL_CONNECTED; }
int8_t rssi() { return (int8_t)WiFi.RSSI(); }
const char *ipAddress() {
  if (WiFi.status() == WL_CONNECTED) {
    strncpy(gIp, WiFi.localIP().toString().c_str(), sizeof(gIp) - 1);
    gIp[sizeof(gIp) - 1] = '\0';
  }
  return gIp;
}

}  // namespace wifi_manager
