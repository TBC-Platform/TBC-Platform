// SPDX-License-Identifier: MIT
// Copy this file to `secrets.h` and fill it in. `secrets.h` is git-ignored so
// your Wi-Fi password never ends up in a commit.
#pragma once

#define WIFI_SSID "your-wifi-name"
#define WIFI_PASSWORD "your-wifi-password"

// Where the brain lives. Use the LAN IP of the PC/Mac running server/,
// not a hostname - mDNS lookups add latency and sometimes fail on ESP32.
// ws://  = plain (fine inside your own LAN)
// wss:// = TLS (required if the server is reachable from outside)
#define WALLE_SERVER_HOST "192.168.1.50"
#define WALLE_SERVER_PORT 8765
#define WALLE_SERVER_PATH "/ws"
#define WALLE_SERVER_USE_TLS 0

// Shared secret. Must match WALLE_AUTH_TOKEN in server/.env.
// The server rejects any socket that does not present it.
#define WALLE_AUTH_TOKEN "change-me-to-a-long-random-string"

// Password for the OTA / diagnostics web endpoint on the device itself.
#define WALLE_OTA_PASSWORD "change-me-too"
