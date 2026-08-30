# SPDX-License-Identifier: MIT
"""Wall-E link protocol (server side).

This is the exact mirror of ``firmware/src/core/protocol.h``. If you change a
constant here, change it there too - there is a test
(``tests/test_protocol.py``) that reads the C header and fails the build if the
two drift apart.

Transport: one WebSocket carrying two kinds of frame.

* **Text frames** are JSON control messages, each with a ``t`` (type) field.
* **Binary frames** are audio or JPEG payloads behind an 8 byte header::

      offset size field
      0      1    magic = 0xA7
      1      1    type  (BinType)
      2      1    flags (FLAG_*)
      3      1    reserved
      4      2    seq   (uint16 LE, diagnostics only)
      6      2    len   (uint16 LE, payload bytes)
      8      len  payload

Audio is 16 kHz mono signed 16 bit little endian in *both* directions, so the
ESP32 never resamples. Piper's 22.05 kHz output is downsampled here on the
server, where CPU is cheap.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Final

PROTO_VERSION: Final[int] = 1

BIN_MAGIC: Final[int] = 0xA7
BIN_HEADER_LEN: Final[int] = 8
_HEADER_STRUCT: Final[struct.Struct] = struct.Struct("<BBBBHH")

AUDIO_SAMPLE_RATE: Final[int] = 16000
AUDIO_CHANNELS: Final[int] = 1
AUDIO_BITS: Final[int] = 16
AUDIO_FRAME_SAMPLES: Final[int] = 320  # 20 ms
AUDIO_FRAME_BYTES: Final[int] = AUDIO_FRAME_SAMPLES * 2


class BinType(IntEnum):
    AUDIO_UP = 1
    AUDIO_DOWN = 2
    JPEG_UP = 3


FLAG_NONE: Final[int] = 0x00
FLAG_LAST: Final[int] = 0x01
FLAG_FIRST: Final[int] = 0x02

# --- control message types -------------------------------------------------
# device -> server
MSG_HELLO: Final[str] = "hello"
MSG_STATE: Final[str] = "state"
MSG_WAKE: Final[str] = "wake"
MSG_UTT_BEGIN: Final[str] = "utt_begin"
MSG_UTT_END: Final[str] = "utt_end"
MSG_CAM_META: Final[str] = "cam_meta"
MSG_LOG: Final[str] = "log"
# server -> device
MSG_HELLO_ACK: Final[str] = "hello_ack"
MSG_FACE: Final[str] = "face"
MSG_SAY_BEGIN: Final[str] = "say_begin"
MSG_SAY_END: Final[str] = "say_end"
# Ends every turn, speech or not, so the device never waits on a timeout.
MSG_TURN_END: Final[str] = "turn_end"
MSG_MOVE: Final[str] = "move"
MSG_HEAD: Final[str] = "head"
MSG_CAM: Final[str] = "cam"
MSG_OTA: Final[str] = "ota"
MSG_ERROR: Final[str] = "error"

# Faces the firmware knows how to draw. The LLM is only allowed to pick from
# this list; anything else is dropped rather than shown as a wrong emotion.
FACES: Final[tuple[str, ...]] = (
    "boot", "idle", "listening", "thinking", "speaking", "happy",
    "sad", "angry", "confused", "love", "sleep", "error",
)

MOVES: Final[tuple[str, ...]] = ("stop", "forward", "back", "left", "right", "wiggle")


class ProtocolError(ValueError):
    """Raised for a frame that cannot be parsed. Callers should log and drop."""


@dataclass(frozen=True, slots=True)
class BinFrame:
    type: BinType
    flags: int
    seq: int
    payload: bytes

    @property
    def is_last(self) -> bool:
        return bool(self.flags & FLAG_LAST)

    @property
    def is_first(self) -> bool:
        return bool(self.flags & FLAG_FIRST)


def encode_bin(btype: BinType, payload: bytes, *, flags: int = FLAG_NONE, seq: int = 0) -> bytes:
    """Builds a binary frame. ``payload`` must fit in a uint16 length field."""
    if len(payload) > 0xFFFF:
        raise ProtocolError(f"payload too large: {len(payload)} bytes")
    header = _HEADER_STRUCT.pack(BIN_MAGIC, int(btype), flags & 0xFF, 0, seq & 0xFFFF, len(payload))
    return header + payload


def decode_bin(data: bytes) -> BinFrame:
    """Parses a binary frame, raising :class:`ProtocolError` on anything odd."""
    if len(data) < BIN_HEADER_LEN:
        raise ProtocolError(f"frame shorter than header: {len(data)} bytes")
    magic, btype, flags, _reserved, seq, length = _HEADER_STRUCT.unpack_from(data, 0)
    if magic != BIN_MAGIC:
        raise ProtocolError(f"bad magic 0x{magic:02X}")
    if BIN_HEADER_LEN + length > len(data):
        raise ProtocolError(f"truncated: header says {length}, have {len(data) - BIN_HEADER_LEN}")
    try:
        parsed_type = BinType(btype)
    except ValueError as exc:
        raise ProtocolError(f"unknown binary type {btype}") from exc
    return BinFrame(
        type=parsed_type,
        flags=flags,
        seq=seq,
        payload=data[BIN_HEADER_LEN : BIN_HEADER_LEN + length],
    )


def encode_json(msg_type: str, **fields: Any) -> str:
    """Builds a control message. ``separators`` keeps the frame small - the
    ESP32 parses into a fixed 1 KB buffer."""
    payload = {"t": msg_type, **fields}
    return json.dumps(payload, separators=(",", ":"))


def decode_json(raw: str | bytes) -> dict[str, Any]:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(msg, dict) or "t" not in msg:
        raise ProtocolError("control message must be an object with a 't' field")
    return msg


def chunk_audio(pcm: bytes, chunk_bytes: int = AUDIO_FRAME_BYTES) -> list[bytes]:
    """Splits PCM into protocol-sized frames.

    Chunks are aligned to whole samples; a trailing odd byte (which should
    never happen with well-formed S16LE) is dropped rather than shifting every
    following sample by one byte and turning speech into noise.
    """
    if chunk_bytes % 2:
        raise ValueError("chunk_bytes must be even for 16 bit audio")
    usable = len(pcm) - (len(pcm) % 2)
    # Clamp the end of each slice to `usable`, not just the start: without the
    # min() the final chunk reaches past the aligned length and smuggles the
    # odd byte back in.
    return [
        pcm[i : min(i + chunk_bytes, usable)]
        for i in range(0, usable, chunk_bytes)
    ]
