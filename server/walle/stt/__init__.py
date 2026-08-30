# SPDX-License-Identifier: MIT
"""Speech-to-text backends."""

from __future__ import annotations

from ..config import SttConfig
from .base import SttEngine, Transcript


def build_stt(cfg: SttConfig) -> SttEngine:
    """Factory. Unknown backend names fail loudly at startup rather than
    silently giving you a robot that never hears anything."""
    backend = cfg.backend.strip().lower()
    if backend in {"whispercpp", "whisper.cpp", "whisper_cpp"}:
        from .whispercpp import WhisperCppStt

        return WhisperCppStt(cfg)
    if backend in {"faster-whisper", "faster_whisper", "fasterwhisper"}:
        from .faster_whisper import FasterWhisperStt

        return FasterWhisperStt(cfg)
    raise ValueError(
        f"unknown WALLE_STT_BACKEND {cfg.backend!r}; expected 'whispercpp' or 'faster-whisper'"
    )


__all__ = ["SttEngine", "Transcript", "build_stt"]
