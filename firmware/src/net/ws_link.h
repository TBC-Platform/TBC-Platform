// SPDX-License-Identifier: MIT
// The single WebSocket that carries everything between robot and server.
#pragma once

#include <ArduinoJson.h>
#include <stddef.h>
#include <stdint.h>

namespace ws_link {

// Called for every JSON control frame. `type` is the "t" field, already
// extracted; `msg` is the whole object.
typedef void (*ControlHandler)(const char *type, JsonObjectConst msg);

// Called for every chunk of downstream speech audio.
typedef void (*AudioHandler)(const int16_t *samples, size_t count, uint8_t flags);

bool begin(ControlHandler onControl, AudioHandler onAudio);

// Services the socket. Must be called every loop; it is where reconnects,
// pings and incoming frames are handled.
void loop();

bool isConnected();

// Sends a JSON control message. `build` fills the document; doing it through a
// callback keeps the (fairly large) JsonDocument on this module's stack
// instead of every caller's.
bool sendJson(JsonDocument &doc);

// One 20 ms audio frame upstream.
bool sendAudio(const int16_t *samples, size_t count, uint8_t flags);

// A whole JPEG, chunked into WebSocket-friendly pieces.
bool sendJpeg(const uint8_t *data, size_t len, uint16_t width, uint16_t height);

// Convenience senders for the messages the main loop uses constantly.
bool sendHello();
bool sendWake(const char *word, uint8_t score);
bool sendUttBegin();
bool sendUttEnd(uint32_t durationMs, bool speechDetected);
bool sendState(const char *state, uint16_t battMv, uint8_t battPct, int8_t rssi);
bool sendLog(const char *level, const char *message);

// Milliseconds since the last frame arrived from the server; used to notice a
// silently dead link.
uint32_t msSinceLastRx();

}  // namespace ws_link
