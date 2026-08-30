// SPDX-License-Identifier: MIT
// I2S microphone capture (INMP441 / ICS-43434) with a rolling pre-roll buffer.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace audio_in {

bool begin();

// Reads exactly one 20 ms frame (WALLE_AUDIO_FRAME_SAMPLES). Blocks until the
// I2S DMA has the data, which makes it a free, jitter-proof loop clock: the
// main loop naturally runs at 50 Hz without a single delay() call.
// Returns the number of samples written, or 0 on timeout.
size_t readFrame(int16_t *out);

// RMS of the most recent frame, in raw 16 bit units. Used by the VAD and by
// the OLED level meter.
uint16_t lastRms();

// 0..100 level for the UI, log-scaled so quiet speech still moves the bars.
uint8_t lastLevelPercent();

// The pre-roll holds the last VOICE_PREROLL_MS of audio at all times. When the
// wake word fires we send this first, so the recogniser hears the beginning of
// the sentence instead of starting mid-word.
size_t prerollSamples();
size_t copyPreroll(int16_t *out, size_t maxSamples);
void clearPreroll();

}  // namespace audio_in
