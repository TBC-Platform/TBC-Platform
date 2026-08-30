// SPDX-License-Identifier: MIT
#include "audio_out.h"

#include <Arduino.h>
#include <driver/i2s_std.h>
#include <esp_heap_caps.h>

#include "config.h"
#include "core/protocol.h"

namespace {

i2s_chan_handle_t gTx = nullptr;
bool gReady = false;

// 4 seconds of 16 kHz mono in PSRAM. Wi-Fi delivers TTS in bursts; this
// absorbs them so the speaker never stutters mid-sentence.
constexpr size_t kRingSamples = WALLE_AUDIO_SAMPLE_RATE * 4;
// Half a second, for boards without usable PSRAM.
constexpr size_t kRingSamplesFallback = WALLE_AUDIO_SAMPLE_RATE / 2;

int16_t *gRing = nullptr;
// The capacity actually allocated. Every ring operation must use this rather
// than kRingSamples: on the fallback path they differ by 8x, and wrapping at
// the wrong modulus writes straight past the end of the allocation.
size_t gRingCapacity = 0;
volatile size_t gRingHead = 0;  // write
volatile size_t gRingTail = 0;  // read
uint8_t gLevel = 0;

inline size_t ringUsed() {
  if (!gRingCapacity) return 0;
  return (gRingHead + gRingCapacity - gRingTail) % gRingCapacity;
}

}  // namespace

namespace audio_out {

bool begin() {
  gRing = (int16_t *)heap_caps_malloc(kRingSamples * sizeof(int16_t),
                                      MALLOC_CAP_SPIRAM);
  gRingCapacity = kRingSamples;
  if (!gRing) {
    // No PSRAM? Fall back to a much smaller internal-RAM buffer. Playback
    // still works, it is just less tolerant of Wi-Fi hiccups.
    gRing = (int16_t *)heap_caps_malloc(kRingSamplesFallback * sizeof(int16_t),
                                        MALLOC_CAP_INTERNAL);
    gRingCapacity = kRingSamplesFallback;
    if (!gRing) {
      gRingCapacity = 0;
      return false;
    }
    Serial.println("[audio] no PSRAM: playback buffer is 0.5 s, not 4 s");
  }

  i2s_chan_config_t chanCfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
  chanCfg.dma_desc_num = 6;
  chanCfg.dma_frame_num = WALLE_AUDIO_FRAME_SAMPLES;
  chanCfg.auto_clear = true;  // play silence on underrun instead of a buzz
  if (i2s_new_channel(&chanCfg, &gTx, nullptr) != ESP_OK) return false;

  i2s_std_config_t stdCfg = {
      .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(WALLE_AUDIO_SAMPLE_RATE),
      .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                      I2S_SLOT_MODE_MONO),
      .gpio_cfg = {
          .mclk = I2S_GPIO_UNUSED,
          .bclk = (gpio_num_t)PIN_SPK_BCLK,
          .ws = (gpio_num_t)PIN_SPK_LRC,
          .dout = (gpio_num_t)PIN_SPK_DIN,
          .din = I2S_GPIO_UNUSED,
          .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
      },
  };
  if (i2s_channel_init_std_mode(gTx, &stdCfg) != ESP_OK) return false;
  if (i2s_channel_enable(gTx) != ESP_OK) return false;

  gReady = true;
  return true;
}

size_t freeSpace() {
  if (!gReady) return 0;
  return gRingCapacity - ringUsed() - 1;  // -1 keeps head != tail when full
}

size_t write(const int16_t *samples, size_t count) {
  if (!gReady) return 0;
  const size_t room = freeSpace();
  if (count > room) count = room;
  for (size_t i = 0; i < count; i++) {
    gRing[gRingHead] = samples[i];
    gRingHead = (gRingHead + 1) % gRingCapacity;
  }
  return count;
}

bool busy() { return gReady && ringUsed() > 0; }

void flush() {
  gRingTail = gRingHead;
  gLevel = 0;
  if (gReady) {
    // Dropping the DMA queue too is what makes barge-in feel instant; without
    // it the last ~100 ms of the old sentence still plays.
    i2s_channel_disable(gTx);
    i2s_channel_enable(gTx);
  }
}

uint8_t level() { return gLevel; }

void tick() {
  if (!gReady) return;
  size_t used = ringUsed();
  if (used == 0) {
    gLevel = 0;
    return;
  }

  // Push at most one frame per tick so a long buffer cannot monopolise the
  // loop and starve the microphone.
  static int16_t frame[WALLE_AUDIO_FRAME_SAMPLES];
  const size_t n = used < WALLE_AUDIO_FRAME_SAMPLES ? used : WALLE_AUDIO_FRAME_SAMPLES;

  uint32_t peak = 0;
  for (size_t i = 0; i < n; i++) {
    const int16_t s = gRing[gRingTail];
    gRingTail = (gRingTail + 1) % gRingCapacity;
    frame[i] = s;
    const uint32_t a = (uint32_t)abs((int)s);
    if (a > peak) peak = a;
  }

  size_t written = 0;
  // Zero timeout: if the DMA queue is full we simply try again next tick
  // rather than blocking the loop.
  i2s_channel_write(gTx, frame, n * sizeof(int16_t), &written, 0);
  if (written < n * sizeof(int16_t)) {
    // Rewind the samples I2S refused so nothing is lost.
    const size_t unwritten = n - written / sizeof(int16_t);
    gRingTail = (gRingTail + gRingCapacity - unwritten) % gRingCapacity;
  }

  // Smooth the envelope a little; raw peak makes the mouth flicker.
  const uint8_t inst = (uint8_t)((peak * 100) / 32768);
  gLevel = (uint8_t)((gLevel * 2 + inst * 3) / 5);
}

void playWakeChirp() {
  if (!gReady) return;
  // Two ascending 60 ms tones (880 Hz, 1320 Hz) with a short fade so the amp
  // does not click. Generated inline - no sample files to flash.
  const int toneMs = 60;
  const int n = (WALLE_AUDIO_SAMPLE_RATE * toneMs) / 1000;
  static int16_t buf[(WALLE_AUDIO_SAMPLE_RATE * 60) / 1000];
  const float freqs[2] = {880.0f, 1320.0f};
  for (int t = 0; t < 2; t++) {
    for (int i = 0; i < n; i++) {
      const float env = (i < n / 8)          ? (float)i / (n / 8)
                        : (i > n - n / 8)    ? (float)(n - i) / (n / 8)
                                             : 1.0f;
      const float s = sinf(2.0f * PI * freqs[t] * i / WALLE_AUDIO_SAMPLE_RATE);
      buf[i] = (int16_t)(s * env * 6000.0f);  // ~ -15 dBFS, audible not harsh
    }
    write(buf, n);
  }
}

}  // namespace audio_out
