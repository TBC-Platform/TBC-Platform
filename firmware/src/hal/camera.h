// SPDX-License-Identifier: MIT
// OV2640 stills for the server-side vision pipeline.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace camera {

bool begin();
bool isReady();

// Captures one JPEG. The returned pointer stays valid until the next call to
// capture() or release(); it points into the driver's PSRAM frame buffer, so
// nothing is copied. Returns nullptr on failure.
const uint8_t *capture(size_t *outLen, uint16_t *outW, uint16_t *outH);

// Hands the frame buffer back to the driver. Always pair with capture().
void release();

// 10 (best) .. 63 (worst). Higher numbers mean smaller files and faster
// uploads; 12 is a good balance for object detection at VGA.
void setQuality(uint8_t q);

}  // namespace camera
