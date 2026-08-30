# SPDX-License-Identifier: MIT
"""Wire format tests, including a check that the C and Python halves agree.

The firmware and the server each define the protocol constants in their own
language. That duplication is unavoidable (no shared build), so this test reads
the C header and asserts the numbers match - the drift it catches would
otherwise show up as garbled audio on real hardware.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from walle import protocol as proto

HEADER = Path(__file__).resolve().parents[2] / "firmware" / "src" / "core" / "protocol.h"


def _c_defines() -> dict[str, str]:
    text = HEADER.read_text(encoding="utf-8")
    pattern = re.compile(r"^#define\s+(\w+)\s+(.+?)\s*$", re.MULTILINE)
    return {name: value for name, value in pattern.findall(text)}


@pytest.mark.skipif(not HEADER.is_file(), reason="firmware header not present")
def test_constants_match_firmware():
    defines = _c_defines()
    assert int(defines["WALLE_PROTO_VERSION"]) == proto.PROTO_VERSION
    assert int(defines["WALLE_BIN_MAGIC"], 16) == proto.BIN_MAGIC
    assert int(defines["WALLE_BIN_HEADER_LEN"]) == proto.BIN_HEADER_LEN
    assert int(defines["WALLE_AUDIO_SAMPLE_RATE"]) == proto.AUDIO_SAMPLE_RATE
    assert int(defines["WALLE_AUDIO_CHANNELS"]) == proto.AUDIO_CHANNELS
    assert int(defines["WALLE_AUDIO_BITS"]) == proto.AUDIO_BITS
    assert int(defines["WALLE_AUDIO_FRAME_SAMPLES"]) == proto.AUDIO_FRAME_SAMPLES


@pytest.mark.skipif(not HEADER.is_file(), reason="firmware header not present")
def test_message_names_match_firmware():
    defines = _c_defines()
    for name, value in [
        ("MSG_HELLO", proto.MSG_HELLO),
        ("MSG_WAKE", proto.MSG_WAKE),
        ("MSG_UTT_BEGIN", proto.MSG_UTT_BEGIN),
        ("MSG_UTT_END", proto.MSG_UTT_END),
        ("MSG_HELLO_ACK", proto.MSG_HELLO_ACK),
        ("MSG_FACE", proto.MSG_FACE),
        ("MSG_SAY_BEGIN", proto.MSG_SAY_BEGIN),
        ("MSG_SAY_END", proto.MSG_SAY_END),
        ("MSG_TURN_END", proto.MSG_TURN_END),
        ("MSG_MOVE", proto.MSG_MOVE),
        ("MSG_HEAD", proto.MSG_HEAD),
        ("MSG_CAM", proto.MSG_CAM),
        ("MSG_OTA", proto.MSG_OTA),
        ("MSG_ERROR", proto.MSG_ERROR),
    ]:
        assert defines[name].strip('"') == value, name


def test_bin_roundtrip():
    payload = bytes(range(64))
    frame = proto.decode_bin(
        proto.encode_bin(proto.BinType.AUDIO_DOWN, payload, flags=proto.FLAG_LAST, seq=1234)
    )
    assert frame.type is proto.BinType.AUDIO_DOWN
    assert frame.payload == payload
    assert frame.seq == 1234
    assert frame.is_last and not frame.is_first


def test_empty_payload_is_valid():
    """A zero-length frame is how end-of-stream is signalled."""
    frame = proto.decode_bin(proto.encode_bin(proto.BinType.AUDIO_UP, b"", flags=proto.FLAG_LAST))
    assert frame.payload == b""
    assert frame.is_last


def test_rejects_bad_magic():
    raw = bytearray(proto.encode_bin(proto.BinType.AUDIO_UP, b"abc"))
    raw[0] = 0x00
    with pytest.raises(proto.ProtocolError, match="magic"):
        proto.decode_bin(bytes(raw))


def test_rejects_truncated_frame():
    raw = proto.encode_bin(proto.BinType.AUDIO_UP, b"abcdefgh")
    with pytest.raises(proto.ProtocolError, match="truncated"):
        proto.decode_bin(raw[:-3])


def test_rejects_short_frame():
    with pytest.raises(proto.ProtocolError, match="shorter than header"):
        proto.decode_bin(b"\xa7\x01")


def test_rejects_unknown_type():
    raw = bytearray(proto.encode_bin(proto.BinType.AUDIO_UP, b"x"))
    raw[1] = 99
    with pytest.raises(proto.ProtocolError, match="unknown binary type"):
        proto.decode_bin(bytes(raw))


def test_rejects_oversized_payload():
    with pytest.raises(proto.ProtocolError, match="too large"):
        proto.encode_bin(proto.BinType.JPEG_UP, bytes(70000))


def test_json_roundtrip():
    msg = proto.decode_json(proto.encode_json(proto.MSG_FACE, e="happy", ms=1500))
    assert msg == {"t": "face", "e": "happy", "ms": 1500}


def test_json_requires_type_field():
    with pytest.raises(proto.ProtocolError, match="'t' field"):
        proto.decode_json('{"e":"happy"}')
    with pytest.raises(proto.ProtocolError, match="invalid JSON"):
        proto.decode_json("{not json")


def test_chunk_audio_is_sample_aligned():
    pcm = bytes(proto.AUDIO_FRAME_BYTES * 2 + 100)
    chunks = proto.chunk_audio(pcm)
    assert sum(len(c) for c in chunks) == len(pcm)
    assert all(len(c) % 2 == 0 for c in chunks)
    assert all(len(c) <= proto.AUDIO_FRAME_BYTES for c in chunks)


def test_chunk_audio_drops_odd_trailing_byte():
    """An odd byte would shift every following sample and produce static."""
    chunks = proto.chunk_audio(bytes(101))
    assert sum(len(c) for c in chunks) == 100
