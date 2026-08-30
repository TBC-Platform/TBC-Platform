// SPDX-License-Identifier: MIT
#include "ota.h"

#include "config.h"

#if WALLE_ENABLE_OTA

#include <ArduinoOTA.h>
#include <HTTPClient.h>
#include <Update.h>
#include <WiFiClientSecure.h>

#include "hal/display.h"
#include "hal/motors.h"
#include "secrets.h"

namespace {
bool gBusy = false;
uint8_t gProgress = 0;

void enterUpdateMode() {
  gBusy = true;
  gProgress = 0;
  // Anything that moves or makes noise stops first: a servo twitching while
  // flash is being rewritten is how boards end up bricked on a brownout.
  motors::stop();
  display::showBanner("Updating...", "do not unplug");
}
}  // namespace

namespace ota {

bool begin() {
  ArduinoOTA.setHostname(WALLE_DEVICE_NAME);
  ArduinoOTA.setPassword(WALLE_OTA_PASSWORD);
  ArduinoOTA.onStart([]() { enterUpdateMode(); });
  ArduinoOTA.onProgress([](unsigned int done, unsigned int total) {
    gProgress = total ? (uint8_t)((done * 100) / total) : 0;
    char line[24];
    snprintf(line, sizeof(line), "%u%%", gProgress);
    display::showBanner("Updating...", line);
  });
  ArduinoOTA.onEnd([]() { display::showBanner("Update done", "rebooting"); });
  ArduinoOTA.onError([](ota_error_t) {
    gBusy = false;
    display::showBanner("Update failed", "check password");
  });
  ArduinoOTA.begin();
  return true;
}

void tick() { ArduinoOTA.handle(); }

bool inProgress() { return gBusy; }
uint8_t progress() { return gProgress; }

bool pullUpdate(const char *url) {
  if (!url || !*url) return false;
  enterUpdateMode();

  HTTPClient http;
  // A pulled image can come from anywhere, so https is supported. We do not
  // pin a CA here because the usual deployment is a LAN box with a
  // self-signed cert; the image itself is what should be trusted, and the
  // ESP32 verifies its checksum before swapping partitions.
  WiFiClientSecure secure;
  secure.setInsecure();

  const bool isHttps = strncmp(url, "https://", 8) == 0;
  bool ok = isHttps ? http.begin(secure, url) : http.begin(url);
  if (!ok) {
    gBusy = false;
    return false;
  }

  const int code = http.GET();
  if (code != HTTP_CODE_OK) {
    http.end();
    gBusy = false;
    display::showBanner("Update failed", "bad URL");
    return false;
  }

  const int contentLength = http.getSize();
  if (contentLength <= 0 || !Update.begin(contentLength)) {
    http.end();
    gBusy = false;
    display::showBanner("Update failed", "no space");
    return false;
  }

  Update.onProgress([](size_t done, size_t total) {
    gProgress = total ? (uint8_t)((done * 100) / total) : 0;
  });

  const size_t written = Update.writeStream(*http.getStreamPtr());
  http.end();

  if (written != (size_t)contentLength || !Update.end(true)) {
    Update.abort();
    gBusy = false;
    display::showBanner("Update failed", "bad image");
    return false;
  }

  display::showBanner("Update done", "rebooting");
  delay(500);
  ESP.restart();
  return true;  // unreachable
}

}  // namespace ota

#else  // !WALLE_ENABLE_OTA

namespace ota {
bool begin() { return true; }
void tick() {}
bool pullUpdate(const char *) { return false; }
bool inProgress() { return false; }
uint8_t progress() { return 0; }
}  // namespace ota

#endif
