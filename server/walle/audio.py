# SPDX-License-Identifier: MIT
"""Audio helpers: WAV framing, resampling and the robot voice effect.

Pure standard library on purpose. ``audioop`` was removed in Python 3.13 and
pulling in NumPy just to resample a two second clip is not a trade worth
making - the loops below run in single-digit milliseconds for typical
utterances, which is noise next to Whisper and the LLM.
"""

from __future__ import annotations

import io
import math
import wave
from array import array
from collections.abc import Iterable

from .protocol import AUDIO_SAMPLE_RATE

PcmArray = array  # array('h'), signed 16 bit host order (little on all targets)


def pcm_from_bytes(data: bytes) -> PcmArray:
    """Reinterprets raw S16LE bytes as samples, dropping a stray odd byte."""
    if len(data) % 2:
        data = data[:-1]
    samples = array("h")
    samples.frombytes(data)
    # array('h') uses native byte order; every platform we support is little
    # endian, but be explicit rather than silently producing static on a
    # big-endian host.
    if _is_big_endian():
        samples.byteswap()
    return samples


def pcm_to_bytes(samples: PcmArray) -> bytes:
    if _is_big_endian():
        copy = array("h", samples)
        copy.byteswap()
        return copy.tobytes()
    return samples.tobytes()


def _is_big_endian() -> bool:
    import sys

    return sys.byteorder == "big"


def wav_from_pcm(samples: PcmArray, rate: int = AUDIO_SAMPLE_RATE) -> bytes:
    """Wraps PCM in a mono 16 bit WAV container (what whisper.cpp wants)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm_to_bytes(samples))
    return buf.getvalue()


def pcm_from_wav(data: bytes) -> tuple[PcmArray, int]:
    """Reads a WAV (what Piper produces) into samples + sample rate.

    Handles stereo by averaging channels and 8/32 bit by converting, because
    voices in the wild are not always the 22.05 kHz mono 16 bit we expect.
    """
    with wave.open(io.BytesIO(data), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        raw = wav.readframes(wav.getnframes())

    if width == 2:
        samples = pcm_from_bytes(raw)
    elif width == 1:
        # 8 bit WAV is unsigned, centred on 128.
        samples = array("h", ((b - 128) * 256 for b in raw))
    elif width == 4:
        wide = array("i")
        wide.frombytes(raw[: len(raw) - (len(raw) % 4)])
        if _is_big_endian():
            wide.byteswap()
        samples = array("h", (v >> 16 for v in wide))
    else:
        raise ValueError(f"unsupported WAV sample width: {width} bytes")

    if channels > 1:
        samples = array(
            "h",
            (
                sum(samples[i + c] for c in range(channels)) // channels
                for i in range(0, len(samples) - channels + 1, channels)
            ),
        )
    return samples, rate


def resample_linear(samples: PcmArray, src_rate: int, dst_rate: int) -> PcmArray:
    """Linear-interpolation resampler.

    Good enough for speech at these rates, and roughly 20x faster than any
    windowed-sinc implementation written in pure Python. The artefacts it does
    introduce sit above 7 kHz, which a 3 W desk speaker cannot reproduce anyway.
    """
    if src_rate == dst_rate or not samples:
        return samples
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("sample rates must be positive")

    ratio = src_rate / dst_rate
    out_len = int(len(samples) / ratio)
    out = array("h", bytes(out_len * 2))
    last = len(samples) - 1
    for i in range(out_len):
        pos = i * ratio
        idx = int(pos)
        frac = pos - idx
        a = samples[idx]
        b = samples[idx + 1] if idx < last else a
        out[i] = int(a + (b - a) * frac)
    return out


def rms(samples: Iterable[int]) -> float:
    total = 0
    count = 0
    for s in samples:
        total += s * s
        count += 1
    return math.sqrt(total / count) if count else 0.0


def peak_normalize(samples: PcmArray, target_peak: float = 0.85) -> PcmArray:
    """Scales so the loudest sample sits at ``target_peak`` of full scale.

    TTS output level varies a lot between voices; normalising here means the
    robot's volume does not change when you swap the Piper model.
    """
    if not samples:
        return samples
    peak = max(abs(min(samples)), abs(max(samples)))
    if peak == 0:
        return samples
    gain = (32767 * target_peak) / peak
    if 0.98 < gain < 1.02:
        return samples
    return array("h", (_clip(int(s * gain)) for s in samples))


def robot_voice(samples: PcmArray, rate: int, amount: float = 0.35) -> PcmArray:
    """Gives Piper's output a bit of Wall-E.

    Two cheap effects mixed in by ``amount`` (0.0 = untouched, 1.0 = fully
    processed):

    * **Ring modulation** at 62 Hz - the classic "mechanical voice" sound.
      Low enough to add a buzz without destroying intelligibility.
    * **Bit reduction** to ~10 bits, for a touch of vintage servo grit.

    Kept subtle by default: pushing this past ~0.5 measurably hurts how well
    people understand the robot, which defeats the point of it talking.
    """
    if amount <= 0.0 or not samples:
        return samples
    amount = min(amount, 1.0)
    mod_hz = 62.0
    step = 2.0 * math.pi * mod_hz / rate
    mask = ~0x3F  # zero the bottom 6 bits -> 10 bit resolution

    out = array("h", bytes(len(samples) * 2))
    for i, s in enumerate(samples):
        # Ring mod, offset so the carrier never fully nulls the signal.
        carrier = 0.6 + 0.4 * math.sin(step * i)
        wet = int(s * carrier) & mask
        out[i] = _clip(int(s * (1.0 - amount) + wet * amount))
    return out


def _clip(value: int) -> int:
    if value > 32767:
        return 32767
    if value < -32768:
        return -32768
    return value


def duration_ms(samples: PcmArray, rate: int = AUDIO_SAMPLE_RATE) -> int:
    return int(len(samples) * 1000 / rate) if rate else 0
