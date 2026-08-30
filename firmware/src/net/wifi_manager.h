// SPDX-License-Identifier: MIT
// Wi-Fi bring-up and self-healing reconnect.
#pragma once

#include <stdint.h>

namespace wifi_manager {

// Blocks up to WIFI_CONNECT_TIMEOUT_MS. Returns false on timeout - the caller
// should still continue to loop(), which keeps retrying in the background.
bool begin();

// Non-blocking reconnect supervision. Call every loop.
void tick();

bool isConnected();
int8_t rssi();
const char *ipAddress();

}  // namespace wifi_manager
