// SPDX-License-Identifier: MIT
// ---------------------------------------------------------------------------
// Wall-E link protocol (device side).
//
// The robot and the server talk over ONE WebSocket connection that carries two
// different kinds of frames:
//
//   * TEXT frames   -> small JSON control messages ("wake up", "say this",
//                      "drive forward", "show a happy face", ...).
//   * BINARY frames -> raw audio / JPEG payloads prefixed with the 8 byte
//                      header described below. Audio never goes through JSON
//                      because base64 would waste ~33% of our Wi-Fi budget and
//                      add copies we cannot afford on an MCU.
//
// Binary frame layout (little endian):
//
//   offset size field
//   0      1    magic   = WALLE_BIN_MAGIC (0xA7) - cheap sanity check
//   1      1    type    = WalleBinType
//   2      1    flags   = bitmask, see WALLE_FLAG_*
//   3      1    reserved (0)
//   4      2    seq     - wraps at 65535, used only for loss diagnostics
//   6      2    len     - payload bytes that follow the header
//   8      len  payload
//
// Audio is ALWAYS 16 kHz, mono, signed 16 bit little endian ("S16LE") in both
// directions. Keeping both directions identical means the ESP32 never has to
// resample - the server does all rate conversion, which is exactly the
// "keep the heavy work off-device" rule this project is built around.
// ---------------------------------------------------------------------------
#pragma once

#include <stdint.h>

// Bump only for breaking changes; the server refuses mismatched majors.
#define WALLE_PROTO_VERSION 1

#define WALLE_BIN_MAGIC 0xA7
#define WALLE_BIN_HEADER_LEN 8

// Audio contract - shared with server/walle/protocol.py. Change both or neither.
#define WALLE_AUDIO_SAMPLE_RATE 16000
#define WALLE_AUDIO_CHANNELS 1
#define WALLE_AUDIO_BITS 16

// 20 ms of audio at 16 kHz mono = 320 samples = 640 bytes. One frame per
// WebSocket message keeps latency low without drowning the TCP stack in
// tiny writes.
#define WALLE_AUDIO_FRAME_SAMPLES 320
#define WALLE_AUDIO_FRAME_BYTES (WALLE_AUDIO_FRAME_SAMPLES * 2)

enum WalleBinType : uint8_t {
  WALLE_BIN_AUDIO_UP = 1,    // device -> server: microphone PCM
  WALLE_BIN_AUDIO_DOWN = 2,  // server -> device: speech PCM to play
  WALLE_BIN_JPEG_UP = 3,     // device -> server: camera still
};

// flags
#define WALLE_FLAG_NONE 0x00
#define WALLE_FLAG_LAST 0x01   // final chunk of this stream
#define WALLE_FLAG_FIRST 0x02  // first chunk of this stream

// Control message types (the "t" field of every JSON frame).
// Device -> server
#define MSG_HELLO "hello"
#define MSG_STATE "state"
#define MSG_WAKE "wake"
#define MSG_UTT_BEGIN "utt_begin"
#define MSG_UTT_END "utt_end"
#define MSG_CAM_META "cam_meta"
#define MSG_LOG "log"
// Server -> device
#define MSG_HELLO_ACK "hello_ack"
#define MSG_FACE "face"
#define MSG_SAY_BEGIN "say_begin"
#define MSG_SAY_END "say_end"
// Sent at the end of EVERY turn, including turns that produce no speech
// (nothing intelligible was heard, an engine failed). Without it the device
// would sit in THINKING until its 20 s timeout after every misheard
// utterance, which is 20 s of being unable to wake the robot.
#define MSG_TURN_END "turn_end"
#define MSG_MOVE "move"
#define MSG_HEAD "head"
#define MSG_CAM "cam"
#define MSG_OTA "ota"
#define MSG_ERROR "error"

// Writes an 8 byte binary header into `out`. Caller guarantees 8 bytes of room.
inline void walleWriteBinHeader(uint8_t *out, WalleBinType type, uint8_t flags,
                                uint16_t seq, uint16_t len) {
  out[0] = WALLE_BIN_MAGIC;
  out[1] = static_cast<uint8_t>(type);
  out[2] = flags;
  out[3] = 0;
  out[4] = static_cast<uint8_t>(seq & 0xFF);
  out[5] = static_cast<uint8_t>((seq >> 8) & 0xFF);
  out[6] = static_cast<uint8_t>(len & 0xFF);
  out[7] = static_cast<uint8_t>((len >> 8) & 0xFF);
}

// Parses a binary header. Returns false if the frame is too short or the magic
// byte is wrong, which is how we ignore stray frames from other tooling.
inline bool walleParseBinHeader(const uint8_t *in, size_t inLen,
                                WalleBinType *type, uint8_t *flags,
                                uint16_t *seq, uint16_t *len) {
  if (inLen < WALLE_BIN_HEADER_LEN || in[0] != WALLE_BIN_MAGIC) return false;
  *type = static_cast<WalleBinType>(in[1]);
  *flags = in[2];
  *seq = static_cast<uint16_t>(in[4] | (in[5] << 8));
  *len = static_cast<uint16_t>(in[6] | (in[7] << 8));
  return (*len + WALLE_BIN_HEADER_LEN) <= inLen;
}
