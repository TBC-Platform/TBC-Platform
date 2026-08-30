// SPDX-License-Identifier: MIT
#include "wakeword.h"

#include <Arduino.h>

#include "config.h"
#include "core/protocol.h"

// ESP-SR ships with the Arduino ESP32 core 3.x for the S3. If the headers are
// missing (wrong core, or an S2/C3 target) we degrade to push-to-talk instead
// of failing the build - a half-working robot beats a red compiler.
#if WALLE_WAKEWORD_BACKEND == 1 && __has_include(<esp_wn_iface.h>)
#define WALLE_HAVE_ESP_SR 1
#include <esp_wn_iface.h>
#include <esp_wn_models.h>
#include <model_path.h>
#else
#define WALLE_HAVE_ESP_SR 0
#endif

namespace {

bool gMuted = false;
char gLastWord[24] = "push-to-talk";
uint8_t gLastScore = 0;
const char *gBackend = "button";

#if WALLE_HAVE_ESP_SR
const esp_wn_iface_t *gWakenet = nullptr;
model_iface_data_t *gWnData = nullptr;
srmodel_list_t *gModels = nullptr;
char *gModelName = nullptr;

// WakeNet wants fixed-size chunks (320 samples on every model shipped so far,
// i.e. exactly our 20 ms frame). We still bridge through a staging buffer so a
// model with a different chunk size keeps working.
int gChunkSamples = 0;
int16_t *gStage = nullptr;
int gStageFill = 0;
#endif

}  // namespace

namespace wakeword {

bool begin() {
#if WALLE_HAVE_ESP_SR
  // esp_srmodel_init reads the models from the "model" partition flashed
  // alongside the firmware (see partitions.csv / board_build.partitions).
  gModels = esp_srmodel_init("model");
  if (!gModels) return false;

  gModelName = esp_srmodel_filter(gModels, ESP_WN_PREFIX, nullptr);
  if (!gModelName) return false;

  gWakenet = esp_wn_handle_from_name(gModelName);
  if (!gWakenet) return false;

  // DET_MODE_90 = trigger at 90% confidence: the least trigger-happy of the
  // single-channel modes. Raise to DET_MODE_95 if the robot wakes to the TV.
  gWnData = gWakenet->create(gModelName, DET_MODE_90);
  if (!gWnData) return false;

  gChunkSamples = gWakenet->get_samp_chunksize(gWnData);
  gStage = (int16_t *)malloc(sizeof(int16_t) * gChunkSamples);
  if (!gStage) return false;
  gStageFill = 0;

  // Model names look like "wn9_hiesp"; strip the prefix for a friendly label.
  const char *word = strchr(gModelName, '_');
  snprintf(gLastWord, sizeof(gLastWord), "%s", word ? word + 1 : gModelName);
  gBackend = "esp-sr";
  return true;
#else
  gBackend = "button";
  return false;
#endif
}

bool feed(const int16_t *samples, size_t count) {
#if WALLE_HAVE_ESP_SR
  if (!gWnData || gMuted || !samples || count == 0) return false;

  bool detected = false;
  size_t offset = 0;
  while (offset < count) {
    const int room = gChunkSamples - gStageFill;
    const size_t take = ((count - offset) < (size_t)room) ? (count - offset) : (size_t)room;
    memcpy(gStage + gStageFill, samples + offset, take * sizeof(int16_t));
    gStageFill += (int)take;
    offset += take;

    if (gStageFill == gChunkSamples) {
      gStageFill = 0;
      if (gWakenet->detect(gWnData, gStage) == WAKENET_DETECTED) {
        detected = true;
        gLastScore = 90;
        // Drop whatever is left of this frame: we do not want a second
        // detection from the tail of the same utterance.
        break;
      }
    }
  }
  return detected;
#else
  (void)samples;
  (void)count;
  return false;
#endif
}

const char *lastWord() { return gLastWord; }
uint8_t lastScore() { return gLastScore; }

void setMuted(bool muted) {
  gMuted = muted;
#if WALLE_HAVE_ESP_SR
  // Clear the staging buffer so the first frame after unmuting is not glued to
  // a frame from before Wall-E started talking.
  if (muted) gStageFill = 0;
#endif
}

bool isMuted() { return gMuted; }

const char *backendName() { return gBackend; }

}  // namespace wakeword
