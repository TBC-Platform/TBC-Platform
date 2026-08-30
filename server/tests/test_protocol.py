# SPDX-License-Identifier: MIT
"""Wire format tests, including a check that the C and Python halves agree.

The firmware and the server each define the protocol constants in their own
language. That duplication is unavoidable (no shared build), so this test reads
the C header and asserts the numbers match - the drift it catches would
otherwise show up as garbled audio on real hardware.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from walle import protocol as proto

FIRMWARE = Path(__file__).resolve().parents[2] / "firmware"
HEADER = FIRMWARE / "src" / "core" / "protocol.h"
HOST_HARNESS = FIRMWARE / "test" / "test_protocol_host.cpp"


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


# ---------------------------------------------------------------------------
# The C half of the protocol, compiled and run for real.
#
# Comparing #define values catches drift in the constants but not in the code
# around them. protocol.h is plain C++ with no Arduino dependency, so it builds
# on the host - which lets us check the actual encoder and decoder against the
# Python ones byte for byte, and (with -Werror) that the header is
# self-contained. A firmware build needs an Xtensa toolchain; this needs g++.
# ---------------------------------------------------------------------------

requires_cxx = pytest.mark.skipif(
    shutil.which("g++") is None or not HOST_HARNESS.is_file(),
    reason="g++ or the host harness is unavailable",
)


@pytest.fixture(scope="module")
def host_harness(tmp_path_factory):
    """Compiles firmware/test/test_protocol_host.cpp and returns its path.

    -Werror is the point, not politeness: `#include "core/protocol.h"` comes
    first in that file, so a header that leans on Arduino.h for size_t fails
    here instead of two thirds of the way through a firmware build.
    """
    if shutil.which("g++") is None or not HOST_HARNESS.is_file():
        pytest.skip("g++ or the host harness is unavailable")
    binary = tmp_path_factory.mktemp("proto") / "proto_host"
    result = subprocess.run(
        ["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
         "-I", str(FIRMWARE / "src"), "-o", str(binary), str(HOST_HARNESS)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"protocol.h does not compile cleanly:\n{result.stderr}")
    return binary


def run_harness(binary, *args: str) -> str:
    result = subprocess.run([str(binary), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@requires_cxx
def test_c_header_compiles_standalone_without_warnings(host_harness):
    """The fixture failing is the test; reaching here means it built clean."""
    assert host_harness.exists()


@requires_cxx
@pytest.mark.parametrize("btype,flags,seq,payload", [
    (proto.BinType.AUDIO_DOWN, proto.FLAG_NONE, 0, b""),
    (proto.BinType.AUDIO_DOWN, proto.FLAG_FIRST, 1, b"\xde\xad\xbe\xef"),
    (proto.BinType.AUDIO_UP, proto.FLAG_LAST, 4660, bytes(proto.AUDIO_FRAME_BYTES)),
    (proto.BinType.JPEG_UP, proto.FLAG_FIRST | proto.FLAG_LAST, 65535, bytes(4096)),
])
def test_c_and_python_encoders_agree_byte_for_byte(host_harness, btype, flags, seq, payload):
    """A mismatch here is garbled audio on real hardware."""
    c_header = run_harness(host_harness, "emit", str(int(btype)), str(flags),
                           str(seq), str(len(payload)))
    python_header = proto.encode_bin(btype, payload, flags=flags, seq=seq)[:proto.BIN_HEADER_LEN]
    assert c_header == python_header.hex()


@requires_cxx
def test_c_decoder_reads_a_python_encoded_frame(host_harness):
    frame = proto.encode_bin(proto.BinType.AUDIO_DOWN, b"\xde\xad\xbe\xef",
                             flags=proto.FLAG_LAST, seq=4660)
    parsed = run_harness(host_harness, "parse", frame.hex())
    assert parsed == f"ok {int(proto.BinType.AUDIO_DOWN)} {proto.FLAG_LAST} 4660 4 deadbeef"


@requires_cxx
def test_c_decoder_accepts_an_empty_end_of_stream_frame(host_harness):
    frame = proto.encode_bin(proto.BinType.AUDIO_DOWN, b"", flags=proto.FLAG_LAST)
    assert run_harness(host_harness, "parse", frame.hex()).startswith("ok")


@requires_cxx
def test_c_decoder_rejects_what_python_rejects(host_harness):
    bad_magic = bytearray(proto.encode_bin(proto.BinType.AUDIO_UP, b"abc"))
    bad_magic[0] = 0x00
    assert run_harness(host_harness, "parse", bytes(bad_magic).hex()) == "reject"

    truncated = proto.encode_bin(proto.BinType.AUDIO_UP, b"abcdefgh")[:-3]
    assert run_harness(host_harness, "parse", truncated.hex()) == "reject"

    assert run_harness(host_harness, "parse", "a701") == "reject"
