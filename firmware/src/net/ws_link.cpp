// SPDX-License-Identifier: MIT
#include "ws_link.h"

#include <Arduino.h>
#include <WebSocketsClient.h>

#include "config.h"
#include "core/protocol.h"
#include "secrets.h"

namespace {

WebSocketsClient gWs;
ws_link::ControlHandler gOnControl = nullptr;
ws_link::AudioHandler gOnAudio = nullptr;
bool gConnected = false;
uint16_t gTxSeq = 0;
uint32_t gLastRx = 0;

// One scratch buffer for outgoing binary frames: header + payload, built in
// place so we never allocate on the audio path.
uint8_t gTxBuf[WALLE_BIN_HEADER_LEN + WALLE_AUDIO_FRAME_BYTES];

// JPEGs are sent in chunks so a single WebSocket frame stays well inside the
// TCP window. 4 KB is large enough that a 40 KB frame is only ten writes.
constexpr size_t kJpegChunk = 4096;
uint8_t gJpegBuf[WALLE_BIN_HEADER_LEN + kJpegChunk];

void handleBinary(const uint8_t *payload, size_t length) {
  WalleBinType type;
  uint8_t flags;
  uint16_t seq, len;
  if (!walleParseBinHeader(payload, length, &type, &flags, &seq, &len)) return;
  if (type != WALLE_BIN_AUDIO_DOWN || !gOnAudio) return;

  // The payload is S16LE, and the ESP32 is little endian, so this cast is
  // free. (It would not be on a big-endian host - noted for portability.)
  const int16_t *samples = reinterpret_cast<const int16_t *>(payload + WALLE_BIN_HEADER_LEN);
  gOnAudio(samples, len / sizeof(int16_t), flags);
}

void handleText(const uint8_t *payload, size_t length) {
  // 1 KB covers every control message in the protocol with room to spare;
  // anything larger is malformed and gets dropped rather than fragmenting the
  // heap.
  JsonDocument doc;
  const DeserializationError err = deserializeJson(doc, payload, length);
  if (err) return;
  JsonObjectConst obj = doc.as<JsonObjectConst>();
  const char *type = obj["t"];
  if (!type || !gOnControl) return;
  gOnControl(type, obj);
}

void onEvent(WStype_t type, uint8_t *payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      gConnected = true;
      gLastRx = millis();
      ws_link::sendHello();
      break;
    case WStype_DISCONNECTED:
      gConnected = false;
      break;
    case WStype_TEXT:
      gLastRx = millis();
      handleText(payload, length);
      break;
    case WStype_BIN:
      gLastRx = millis();
      handleBinary(payload, length);
      break;
    case WStype_PONG:
      gLastRx = millis();
      break;
    default:
      break;
  }
}

}  // namespace

namespace ws_link {

bool begin(ControlHandler onControl, AudioHandler onAudio) {
  gOnControl = onControl;
  gOnAudio = onAudio;

  // The shared secret travels as a header, so it never appears in a URL that
  // might be logged by a proxy.
  static char headers[160];
  snprintf(headers, sizeof(headers), "X-Walle-Token: %s\r\nX-Walle-Device: %s",
           WALLE_AUTH_TOKEN, WALLE_DEVICE_NAME);
  gWs.setExtraHeaders(headers);

#if WALLE_SERVER_USE_TLS
  gWs.beginSSL(WALLE_SERVER_HOST, WALLE_SERVER_PORT, WALLE_SERVER_PATH);
#else
  gWs.begin(WALLE_SERVER_HOST, WALLE_SERVER_PORT, WALLE_SERVER_PATH);
#endif
  gWs.onEvent(onEvent);
  gWs.setReconnectInterval(WS_RECONNECT_MIN_MS);
  // Ping every 10 s, expect a pong within 3 s, give up after two misses. This
  // is what catches a router that dropped our NAT entry without telling us.
  gWs.enableHeartbeat(WS_PING_INTERVAL_MS, 3000, 2);
  return true;
}

void loop() { gWs.loop(); }

bool isConnected() { return gConnected; }

uint32_t msSinceLastRx() { return millis() - gLastRx; }

bool sendJson(JsonDocument &doc) {
  if (!gConnected) return false;
  char buf[512];
  const size_t n = serializeJson(doc, buf, sizeof(buf));
  if (n == 0 || n >= sizeof(buf)) return false;
  return gWs.sendTXT(buf, n);
}

bool sendAudio(const int16_t *samples, size_t count, uint8_t flags) {
  if (!gConnected) return false;
  // A zero-length frame is legal and meaningful: it is how the end of an
  // utterance is signalled (count == 0, flags == WALLE_FLAG_LAST).
  if (!samples) count = 0;
  if (count > WALLE_AUDIO_FRAME_SAMPLES) count = WALLE_AUDIO_FRAME_SAMPLES;
  const uint16_t bytes = (uint16_t)(count * sizeof(int16_t));
  walleWriteBinHeader(gTxBuf, WALLE_BIN_AUDIO_UP, flags, gTxSeq++, bytes);
  if (bytes) memcpy(gTxBuf + WALLE_BIN_HEADER_LEN, samples, bytes);
  return gWs.sendBIN(gTxBuf, WALLE_BIN_HEADER_LEN + bytes);
}

bool sendJpeg(const uint8_t *data, size_t len, uint16_t width, uint16_t height) {
  if (!gConnected || !data || len == 0) return false;

  // Announce the frame first so the server can size its buffer and knows the
  // geometry without parsing the JPEG header.
  JsonDocument meta;
  meta["t"] = MSG_CAM_META;
  meta["len"] = len;
  meta["w"] = width;
  meta["h"] = height;
  if (!sendJson(meta)) return false;

  size_t offset = 0;
  while (offset < len) {
    const size_t chunk = (len - offset) > kJpegChunk ? kJpegChunk : (len - offset);
    uint8_t flags = WALLE_FLAG_NONE;
    if (offset == 0) flags |= WALLE_FLAG_FIRST;
    if (offset + chunk >= len) flags |= WALLE_FLAG_LAST;
    walleWriteBinHeader(gJpegBuf, WALLE_BIN_JPEG_UP, flags, gTxSeq++, (uint16_t)chunk);
    memcpy(gJpegBuf + WALLE_BIN_HEADER_LEN, data + offset, chunk);
    if (!gWs.sendBIN(gJpegBuf, WALLE_BIN_HEADER_LEN + chunk)) return false;
    offset += chunk;
    // Let the TCP stack drain between chunks; without this the socket buffer
    // fills and sendBIN starts failing on larger frames.
    gWs.loop();
  }
  return true;
}

bool sendHello() {
  JsonDocument doc;
  doc["t"] = MSG_HELLO;
  doc["dev"] = WALLE_DEVICE_NAME;
  doc["fw"] = WALLE_FW_VERSION;
  doc["proto"] = WALLE_PROTO_VERSION;
  doc["sr"] = WALLE_AUDIO_SAMPLE_RATE;
  doc["token"] = WALLE_AUTH_TOKEN;  // belt and braces alongside the header
  JsonArray caps = doc["caps"].to<JsonArray>();
  caps.add("mic");
  caps.add("speaker");
#if WALLE_ENABLE_DISPLAY
  caps.add("display");
#endif
#if WALLE_ENABLE_MOTORS
  caps.add("tracks");
  caps.add("head");
#endif
#if WALLE_ENABLE_CAMERA
  caps.add("camera");
#endif
  return sendJson(doc);
}

bool sendWake(const char *word, uint8_t score) {
  JsonDocument doc;
  doc["t"] = MSG_WAKE;
  doc["word"] = word;
  doc["score"] = score;
  return sendJson(doc);
}

bool sendUttBegin() {
  JsonDocument doc;
  doc["t"] = MSG_UTT_BEGIN;
  doc["sr"] = WALLE_AUDIO_SAMPLE_RATE;
  return sendJson(doc);
}

bool sendUttEnd(uint32_t durationMs, bool speechDetected) {
  JsonDocument doc;
  doc["t"] = MSG_UTT_END;
  doc["ms"] = durationMs;
  doc["speech"] = speechDetected;
  return sendJson(doc);
}

bool sendState(const char *state, uint16_t battMv, uint8_t battPct, int8_t rssi) {
  JsonDocument doc;
  doc["t"] = MSG_STATE;
  doc["s"] = state;
  doc["batt_mv"] = battMv;
  doc["batt_pct"] = battPct;
  doc["rssi"] = rssi;
  doc["heap"] = ESP.getFreeHeap();
  doc["up"] = millis() / 1000;
  return sendJson(doc);
}

bool sendLog(const char *level, const char *message) {
  JsonDocument doc;
  doc["t"] = MSG_LOG;
  doc["lvl"] = level;
  doc["msg"] = message;
  return sendJson(doc);
}

}  // namespace ws_link
