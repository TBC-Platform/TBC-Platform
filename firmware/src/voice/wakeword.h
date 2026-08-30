// SPDX-License-Identifier: MIT
// On-device wake-word detection. Runs entirely offline - no cloud, ever.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace wakeword {

// Returns false if the selected backend could not start (e.g. the ESP-SR model
// partition is missing). The caller should fall back to push-to-talk rather
// than refusing to boot.
bool begin();

// Feed every captured frame here, wake or not - WakeNet needs a continuous
// stream to keep its internal state warm. Returns true exactly once per
// detection.
bool feed(const int16_t *samples, size_t count);

// Name and confidence of the most recent detection, for logging and for the
// `wake` control message.
const char *lastWord();
uint8_t lastScore();  // 0..100; ESP-SR does not expose a real score, so a
                      // detection reports a fixed high value

// Detection is suppressed while Wall-E is speaking, otherwise the robot wakes
// itself up on its own voice coming back through the mic.
void setMuted(bool muted);
bool isMuted();

// Which backend actually came up, for the boot banner: "esp-sr" or "button".
const char *backendName();

}  // namespace wakeword
