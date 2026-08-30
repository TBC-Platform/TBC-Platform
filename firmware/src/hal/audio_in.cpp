// SPDX-License-Identifier: MIT
#include "audio_in.h"

#include <Arduino.h>
#include <driver/i2s_std.h>

#include "config.h"
#include "core/protocol.h"

namespace {

i2s_chan_handle_t gRx = nullptr;
bool gReady = false;
uint16_t gRms = 0;

// The INMP441 is a 24 bit part living in 32 bit slots, and it is a quiet mic.
// Right-shifting by 11 keeps the top of the 24 bit word and applies ~8x of
// digital gain, which puts normal desk-distance speech at a healthy level
// without clipping. Lower this if loud speech clips, raise it if you have to
// shout. (Shifting by 16 would be the "mathematically pure" 24->16 conversion
// and is far too quiet in practice.)
constexpr int kMicShiftBits = 11;

// Raw 32 bit scratch for one frame.
int32_t gRaw[WALLE_AUDIO_FRAME_SAMPLES];

// Pre-roll ring buffer.
constexpr size_t kPrerollSamples =
    (WALLE_AUDIO_SAMPLE_RATE * VOICE_PREROLL_MS) / 1000;
int16_t gPreroll[kPrerollSamples];
size_t gPrerollHead = 0;   // next write index
size_t gPrerollFill = 0;   // valid samples, saturates at kPrerollSamples

void prerollPush(const int16_t *samples, size_t count) {
  for (size_t i = 0; i < count; i++) {
    gPreroll[gPrerollHead] = samples[i];
    gPrerollHead = (gPrerollHead + 1) % kPrerollSamples;
    if (gPrerollFill < kPrerollSamples) gPrerollFill++;
  }
}

}  // namespace

namespace audio_in {

bool begin() {
  i2s_chan_config_t chanCfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  // 8 descriptors x 320 frames = 160 ms of slack. Generous on purpose: it is
  // what lets the main loop stall for a display redraw or a Wi-Fi retry
  // without dropping a single audio sample.
  chanCfg.dma_desc_num = 8;
  chanCfg.dma_frame_num = WALLE_AUDIO_FRAME_SAMPLES;
  chanCfg.auto_clear = true;
  if (i2s_new_channel(&chanCfg, nullptr, &gRx) != ESP_OK) return false;

  i2s_std_config_t stdCfg = {
      .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(WALLE_AUDIO_SAMPLE_RATE),
      .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                      I2S_SLOT_MODE_MONO),
      .gpio_cfg = {
          .mclk = I2S_GPIO_UNUSED,
          .bclk = (gpio_num_t)PIN_MIC_SCK,
          .ws = (gpio_num_t)PIN_MIC_WS,
          .dout = I2S_GPIO_UNUSED,
          .din = (gpio_num_t)PIN_MIC_SD,
          .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
      },
  };
  // L/R pin of the INMP441 is tied to GND, so it speaks on the left slot only.
  stdCfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;

  if (i2s_channel_init_std_mode(gRx, &stdCfg) != ESP_OK) return false;
  if (i2s_channel_enable(gRx) != ESP_OK) return false;

  gReady = true;
  return true;
}

size_t readFrame(int16_t *out) {
  if (!gReady) return 0;
  size_t bytesRead = 0;
  const esp_err_t err = i2s_channel_read(gRx, gRaw, sizeof(gRaw), &bytesRead,
                                         pdMS_TO_TICKS(60));
  if (err != ESP_OK || bytesRead == 0) return 0;

  const size_t count = bytesRead / sizeof(int32_t);
  uint64_t sumSquares = 0;
  for (size_t i = 0; i < count; i++) {
    int32_t v = gRaw[i] >> kMicShiftBits;
    if (v > 32767) v = 32767;
    if (v < -32768) v = -32768;
    out[i] = (int16_t)v;
    sumSquares += (uint64_t)((int64_t)v * v);
  }
  gRms = (uint16_t)sqrt((double)(sumSquares / (count ? count : 1)));

  prerollPush(out, count);
  return count;
}

uint16_t lastRms() { return gRms; }

uint8_t lastLevelPercent() {
  // Map roughly 300..12000 RMS onto 0..100 with a log curve. Linear scaling
  // makes the meter look dead for anything but shouting.
  if (gRms < 300) return 0;
  const float db = 20.0f * log10f((float)gRms / 300.0f);  // 0 dB at the floor
  const float pct = (db / 32.0f) * 100.0f;                // 32 dB of range
  if (pct <= 0) return 0;
  if (pct >= 100) return 100;
  return (uint8_t)pct;
}

size_t prerollSamples() { return gPrerollFill; }

size_t copyPreroll(int16_t *out, size_t maxSamples) {
  const size_t n = gPrerollFill < maxSamples ? gPrerollFill : maxSamples;
  // Oldest sample first: walk back n slots from the write head.
  size_t idx = (gPrerollHead + kPrerollSamples - n) % kPrerollSamples;
  for (size_t i = 0; i < n; i++) {
    out[i] = gPreroll[idx];
    idx = (idx + 1) % kPrerollSamples;
  }
  return n;
}

void clearPreroll() {
  gPrerollFill = 0;
  gPrerollHead = 0;
}

}  // namespace audio_in
