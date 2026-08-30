# SPDX-License-Identifier: MIT
"""Text-to-speech interface."""

from __future__ import annotations

import abc
from dataclasses import dataclass

from ..audio import PcmArray


@dataclass(slots=True)
class Speech:
    """Synthesised audio, already at the protocol sample rate."""

    samples: PcmArray
    sample_rate: int
    latency_ms: int = 0

    @property
    def duration_ms(self) -> int:
        return int(len(self.samples) * 1000 / self.sample_rate) if self.sample_rate else 0


class TtsEngine(abc.ABC):
    name: str = "tts"

    @abc.abstractmethod
    async def synthesize(self, text: str) -> Speech:
        """Renders text to 16 kHz mono PCM ready to stream to the robot."""

    async def warmup(self) -> None:
        return None

    async def close(self) -> None:
        return None
