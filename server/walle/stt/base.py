# SPDX-License-Identifier: MIT
"""Speech-to-text interface shared by every backend."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

from ..audio import PcmArray


@dataclass(slots=True)
class Transcript:
    text: str
    language: str = "en"
    # Wall time the recogniser took, for the latency log.
    latency_ms: int = 0
    # Backend-specific extras (segments, confidence, ...) for debugging.
    meta: dict = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        """True when nothing usable was said.

        Whisper hallucinates stock phrases on silence - "Thank you.",
        "[BLANK_AUDIO]", a lone "you" - so those are filtered here rather than
        being sent to the LLM as if the user had spoken.
        """
        cleaned = self.text.strip().strip(".!?,").lower()
        if not cleaned:
            return True
        return cleaned in _HALLUCINATIONS


_HALLUCINATIONS = {
    "you",
    "thank you",
    "thanks for watching",
    "thank you for watching",
    "bye",
    "[blank_audio]",
    "(silence)",
    "[silence]",
    "subtitles by the amara.org community",
    "please subscribe",
}


class SttEngine(abc.ABC):
    """Base class for recognisers.

    Implementations must be safe to call from an asyncio event loop, which in
    practice means doing the blocking work in a thread or a subprocess.
    """

    name: str = "stt"

    @abc.abstractmethod
    async def transcribe(self, samples: PcmArray, sample_rate: int) -> Transcript:
        """Turns 16 bit mono PCM into text."""

    async def warmup(self) -> None:
        """Optional: load models and run a dummy inference.

        Called once at startup so the very first real utterance does not pay
        the model-loading cost - worth 1-3 seconds on the first question.
        """
        return None

    async def close(self) -> None:
        return None
