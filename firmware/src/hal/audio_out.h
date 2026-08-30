// SPDX-License-Identifier: MIT
// I2S speaker output (MAX98357A) with a PSRAM jitter buffer.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace audio_out {

bool begin();

// Queues PCM for playback. Returns the number of samples accepted - fewer than
// requested means the buffer is full and the caller should back off (the
// server also honours a flow-control message, see ws_link).
size_t write(const int16_t *samples, size_t count);

// Free space, in samples. The link layer uses this to decide whether to ask
// the server to pause the TTS stream.
size_t freeSpace();

// True while there is still audio queued or draining.
bool busy();

// Drops everything queued - used for barge-in when the user talks over Wall-E.
void flush();

// 0..100 envelope of what is playing right now, for the mouth animation.
uint8_t level();

// Pumps the buffer into I2S. Call every loop; never blocks for long.
void tick();

// A short two-tone chirp so the robot acknowledges the wake word instantly,
// long before the server has produced any speech. This is the single cheapest
// perceived-latency win in the whole project.
void playWakeChirp();

}  // namespace audio_out
