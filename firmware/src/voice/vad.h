// SPDX-License-Identifier: MIT
// Dead-simple energy voice-activity detector used only to decide when the
// user has stopped talking. It does not need to be clever: a false "still
// talking" costs a few hundred milliseconds, and the server-side recogniser
// is the thing that actually has to understand the audio.
#pragma once

#include <stdint.h>

namespace vad {

// Resets the state machine at the start of an utterance.
void reset();

// Feed the RMS of each frame. Returns true once the utterance looks finished:
// speech was heard, then VOICE_SILENCE_TAIL_MS of quiet followed.
bool feedRms(uint16_t rms);

// True once any speech at all has been detected in this utterance. If this is
// still false at the timeout, the wake word was probably a false trigger and
// the caller can drop the recording instead of paying for a transcription.
bool heardSpeech();

// Milliseconds of audio fed since reset().
uint32_t elapsedMs();

// True when the hard VOICE_MAX_UTTERANCE_MS ceiling is hit.
bool timedOut();

}  // namespace vad
