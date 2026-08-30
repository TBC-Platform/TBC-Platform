# SPDX-License-Identifier: MIT
"""Text-to-speech backends."""

from __future__ import annotations

from ..config import TtsConfig
from .base import Speech, TtsEngine


def build_tts(cfg: TtsConfig) -> TtsEngine:
    backend = cfg.backend.strip().lower()
    if backend == "piper":
        from .piper import PiperTts

        return PiperTts(cfg)
    raise ValueError(f"unknown WALLE_TTS_BACKEND {cfg.backend!r}; expected 'piper'")


__all__ = ["Speech", "TtsEngine", "build_tts"]
