# SPDX-License-Identifier: MIT
"""Audio helpers."""

from __future__ import annotations

import math
from array import array

import pytest

from walle.audio import (
    duration_ms,
    pcm_from_bytes,
    pcm_from_wav,
    pcm_to_bytes,
    peak_normalize,
    resample_linear,
    rms,
    robot_voice,
    wav_from_pcm,
)


def tone(freq: float, rate: int, seconds: float, amplitude: int = 12000) -> array:
    n = int(rate * seconds)
    return array("h", (int(amplitude * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)))


def test_bytes_round_trip():
    samples = tone(440, 16000, 0.05)
    assert pcm_from_bytes(pcm_to_bytes(samples)) == samples


def test_odd_trailing_byte_is_dropped():
    """One stray byte would shift every sample and turn speech into static."""
    assert len(pcm_from_bytes(b"\x01\x02\x03")) == 1


def test_wav_round_trip():
    samples = tone(440, 22050, 0.1)
    back, rate = pcm_from_wav(wav_from_pcm(samples, 22050))
    assert rate == 22050
    assert back == samples


def test_wav_stereo_is_mixed_to_mono():
    import io
    import wave

    left = tone(440, 16000, 0.05)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        interleaved = array("h")
        for sample in left:
            interleaved.append(sample)
            interleaved.append(sample)
        wav.writeframes(interleaved.tobytes())

    mono, rate = pcm_from_wav(buf.getvalue())
    assert rate == 16000
    assert len(mono) == len(left)


def test_resample_length_and_pitch():
    samples = tone(440, 22050, 1.0)
    out = resample_linear(samples, 22050, 16000)
    assert abs(len(out) - 16000) <= 1
    # A 440 Hz tone resampled correctly still has ~440 zero crossings per
    # second in each direction; a broken resampler changes the pitch.
    crossings = sum(1 for i in range(1, len(out)) if out[i - 1] < 0 <= out[i])
    assert 420 <= crossings <= 460


def test_resample_is_a_noop_at_the_same_rate():
    samples = tone(440, 16000, 0.05)
    assert resample_linear(samples, 16000, 16000) is samples


def test_resample_rejects_bad_rates():
    with pytest.raises(ValueError):
        resample_linear(tone(440, 16000, 0.01), 0, 16000)


def test_resample_handles_empty_input():
    assert len(resample_linear(array("h"), 22050, 16000)) == 0


def test_peak_normalize_hits_the_target():
    quiet = array("h", (s // 20 for s in tone(440, 16000, 0.05)))
    loud = peak_normalize(quiet, target_peak=0.8)
    peak = max(abs(min(loud)), abs(max(loud)))
    assert 0.75 * 32767 <= peak <= 0.85 * 32767


def test_peak_normalize_survives_silence():
    """Dividing by a zero peak must not raise."""
    silence = array("h", bytes(200))
    assert peak_normalize(silence) == silence


def test_robot_voice_never_clips():
    samples = tone(200, 16000, 0.2, amplitude=32000)
    out = robot_voice(samples, 16000, 1.0)
    assert len(out) == len(samples)
    assert max(out) <= 32767
    assert min(out) >= -32768


def test_robot_voice_zero_amount_is_a_noop():
    samples = tone(200, 16000, 0.05)
    assert robot_voice(samples, 16000, 0.0) is samples


def test_robot_voice_changes_the_signal():
    samples = tone(200, 16000, 0.1)
    out = robot_voice(samples, 16000, 0.5)
    assert out != samples
    # ...but it must still be recognisably the same speech, not noise.
    assert 0.5 < rms(out) / rms(samples) < 1.5


def test_duration_ms():
    assert duration_ms(array("h", bytes(16000 * 2)), 16000) == 1000
    assert duration_ms(array("h"), 16000) == 0
