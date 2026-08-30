// SPDX-License-Identifier: MIT
#include "vad.h"

#include "config.h"
#include "core/protocol.h"

namespace {

constexpr uint32_t kFrameMs = (WALLE_AUDIO_FRAME_SAMPLES * 1000) / WALLE_AUDIO_SAMPLE_RATE;
constexpr uint32_t kSilenceFrames = VOICE_SILENCE_TAIL_MS / kFrameMs;
// Require this much continuous speech before we believe it. One loud frame is
// a door closing; three in a row is a person.
constexpr uint32_t kOnsetFrames = 3;

uint32_t gFrames = 0;
uint32_t gSilenceRun = 0;
uint32_t gSpeechRun = 0;
bool gHeard = false;

// Noise floor tracked between utterances so the detector adapts to a humming
// fridge or a noisy fan without anyone touching config.h.
float gNoiseFloor = VOICE_VAD_RMS_THRESHOLD / 3.0f;

}  // namespace

namespace vad {

void reset() {
  gFrames = 0;
  gSilenceRun = 0;
  gSpeechRun = 0;
  gHeard = false;
}

bool feedRms(uint16_t rms) {
  gFrames++;

  // Speech has to beat both the configured floor and a margin over the
  // measured room noise.
  const uint16_t dynamicThreshold =
      (uint16_t)(gNoiseFloor * 2.5f) > VOICE_VAD_RMS_THRESHOLD
          ? (uint16_t)(gNoiseFloor * 2.5f)
          : VOICE_VAD_RMS_THRESHOLD;

  if (rms > dynamicThreshold) {
    gSpeechRun++;
    gSilenceRun = 0;
    if (gSpeechRun >= kOnsetFrames) gHeard = true;
  } else {
    gSpeechRun = 0;
    gSilenceRun++;
    // Only adapt the floor while it is quiet, and only slowly.
    gNoiseFloor = gNoiseFloor * 0.98f + rms * 0.02f;
  }

  if (gHeard && gSilenceRun >= kSilenceFrames) return true;
  return false;
}

bool heardSpeech() { return gHeard; }

uint32_t elapsedMs() { return gFrames * kFrameMs; }

bool timedOut() { return elapsedMs() >= VOICE_MAX_UTTERANCE_MS; }

}  // namespace vad
