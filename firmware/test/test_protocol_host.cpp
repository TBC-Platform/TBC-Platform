// SPDX-License-Identifier: MIT
// ---------------------------------------------------------------------------
// Host-compiled harness for the shared protocol header.
//
// The firmware needs an Xtensa toolchain, but protocol.h is plain C++ with no
// Arduino dependency, so it compiles and runs on any host. That buys two
// things a full firmware build would not:
//
//  1. It proves the header is self-contained. This file includes it first,
//     before anything else, and is compiled with -Werror - which is exactly
//     the failure that slipped through as `'size_t' has not been declared`.
//  2. It lets the Python test suite check the C encoder and decoder against
//     the Python ones byte for byte, rather than only comparing #define
//     values. A mismatch here is garbled audio on real hardware.
//
// Driven by server/tests/test_protocol.py. Two subcommands:
//
//   emit  <type> <flags> <seq> <len>   -> hex of the 8 byte header
//   parse <hex frame>                  -> "ok <type> <flags> <seq> <len> <payload hex>"
//                                         or "reject"
// ---------------------------------------------------------------------------

#include "core/protocol.h"   // deliberately first: self-containment check

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

std::string toHex(const uint8_t *data, size_t len) {
  static const char *digits = "0123456789abcdef";
  std::string out;
  out.reserve(len * 2);
  for (size_t i = 0; i < len; i++) {
    out.push_back(digits[data[i] >> 4]);
    out.push_back(digits[data[i] & 0x0F]);
  }
  return out;
}

bool fromHex(const char *text, std::vector<uint8_t> *out) {
  const size_t len = strlen(text);
  if (len % 2) return false;
  out->clear();
  for (size_t i = 0; i < len; i += 2) {
    char byte[3] = {text[i], text[i + 1], '\0'};
    char *end = nullptr;
    const long value = strtol(byte, &end, 16);
    if (end != byte + 2) return false;
    out->push_back(static_cast<uint8_t>(value));
  }
  return true;
}

int emit(int argc, char **argv) {
  if (argc < 6) {
    fprintf(stderr, "usage: emit <type> <flags> <seq> <len>\n");
    return 2;
  }
  const auto type = static_cast<WalleBinType>(atoi(argv[2]));
  const auto flags = static_cast<uint8_t>(atoi(argv[3]));
  const auto seq = static_cast<uint16_t>(atoi(argv[4]));
  const auto len = static_cast<uint16_t>(atoi(argv[5]));

  uint8_t header[WALLE_BIN_HEADER_LEN];
  walleWriteBinHeader(header, type, flags, seq, len);
  printf("%s\n", toHex(header, sizeof(header)).c_str());
  return 0;
}

int parse(int argc, char **argv) {
  if (argc < 3) {
    fprintf(stderr, "usage: parse <hex frame>\n");
    return 2;
  }
  std::vector<uint8_t> frame;
  if (!fromHex(argv[2], &frame)) {
    fprintf(stderr, "bad hex\n");
    return 2;
  }

  WalleBinType type;
  uint8_t flags;
  uint16_t seq, len;
  if (!walleParseBinHeader(frame.data(), frame.size(), &type, &flags, &seq, &len)) {
    printf("reject\n");
    return 0;
  }
  const std::string payload =
      toHex(frame.data() + WALLE_BIN_HEADER_LEN, len);
  printf("ok %d %u %u %u %s\n", static_cast<int>(type), flags, seq, len,
         payload.c_str());
  return 0;
}

}  // namespace

int main(int argc, char **argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: %s emit|parse ...\n", argv[0]);
    return 2;
  }
  if (strcmp(argv[1], "emit") == 0) return emit(argc, argv);
  if (strcmp(argv[1], "parse") == 0) return parse(argc, argv);
  fprintf(stderr, "unknown command %s\n", argv[1]);
  return 2;
}
