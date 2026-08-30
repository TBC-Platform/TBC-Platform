// SPDX-License-Identifier: MIT
#include "camera.h"

#include "config.h"

#if WALLE_ENABLE_CAMERA

#include <Arduino.h>
#include <esp_camera.h>

namespace {
bool gReady = false;
camera_fb_t *gFrame = nullptr;
}  // namespace

namespace camera {

bool begin() {
  camera_config_t cfg = {};
  cfg.ledc_channel = LEDC_CHANNEL_7;  // high channels: servos own 0-2
  cfg.ledc_timer = LEDC_TIMER_3;
  cfg.pin_d0 = CAM_PIN_D0;
  cfg.pin_d1 = CAM_PIN_D1;
  cfg.pin_d2 = CAM_PIN_D2;
  cfg.pin_d3 = CAM_PIN_D3;
  cfg.pin_d4 = CAM_PIN_D4;
  cfg.pin_d5 = CAM_PIN_D5;
  cfg.pin_d6 = CAM_PIN_D6;
  cfg.pin_d7 = CAM_PIN_D7;
  cfg.pin_xclk = CAM_PIN_XCLK;
  cfg.pin_pclk = CAM_PIN_PCLK;
  cfg.pin_vsync = CAM_PIN_VSYNC;
  cfg.pin_href = CAM_PIN_HREF;
  cfg.pin_sccb_sda = CAM_PIN_SIOD;
  cfg.pin_sccb_scl = CAM_PIN_SIOC;
  cfg.pin_pwdn = CAM_PIN_PWDN;
  cfg.pin_reset = CAM_PIN_RESET;
  cfg.xclk_freq_hz = 20000000;
  cfg.pixel_format = PIXFORMAT_JPEG;
  // VGA is the sweet spot: big enough for the server's detector, ~25-40 kB per
  // frame, which crosses Wi-Fi in well under 100 ms.
  cfg.frame_size = FRAMESIZE_VGA;
  cfg.jpeg_quality = 12;
  // Two buffers + CONTINUOUS lets the sensor keep exposure adapting so a
  // capture-on-demand is not the usual dark first frame.
  cfg.fb_count = 2;
  cfg.fb_location = CAMERA_FB_IN_PSRAM;
  cfg.grab_mode = CAMERA_GRAB_LATEST;

  const esp_err_t err = esp_camera_init(&cfg);
  if (err != ESP_OK) return false;

  sensor_t *s = esp_camera_sensor_get();
  if (s) {
    // Wall-E's camera sits in the head; nothing is mirrored mechanically, so
    // no flip. Slight saturation bump helps colour-based detection.
    s->set_vflip(s, 0);
    s->set_hmirror(s, 0);
    s->set_saturation(s, 1);
  }
  gReady = true;
  return true;
}

bool isReady() { return gReady; }

const uint8_t *capture(size_t *outLen, uint16_t *outW, uint16_t *outH) {
  if (!gReady) return nullptr;
  release();  // defensive: never leak a buffer if a caller forgot
  gFrame = esp_camera_fb_get();
  if (!gFrame) return nullptr;
  if (outLen) *outLen = gFrame->len;
  if (outW) *outW = gFrame->width;
  if (outH) *outH = gFrame->height;
  return gFrame->buf;
}

void release() {
  if (gFrame) {
    esp_camera_fb_return(gFrame);
    gFrame = nullptr;
  }
}

void setQuality(uint8_t q) {
  if (!gReady) return;
  if (q < 10) q = 10;
  if (q > 63) q = 63;
  sensor_t *s = esp_camera_sensor_get();
  if (s) s->set_quality(s, q);
}

}  // namespace camera

#else  // !WALLE_ENABLE_CAMERA

namespace camera {
bool begin() { return true; }
bool isReady() { return false; }
const uint8_t *capture(size_t *, uint16_t *, uint16_t *) { return nullptr; }
void release() {}
void setQuality(uint8_t) {}
}  // namespace camera

#endif
