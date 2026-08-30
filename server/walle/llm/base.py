# SPDX-License-Identifier: MIT
"""LLM interface and reply parsing."""

from __future__ import annotations

import abc
import re
from dataclasses import dataclass, field

from ..protocol import FACES, MOVES


@dataclass(slots=True)
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class Reply:
    """What the robot should do in response to one user turn."""

    text: str                      # spoken out loud
    face: str | None = None        # one of protocol.FACES
    move: str | None = None        # one of protocol.MOVES
    latency_ms: int = 0
    meta: dict = field(default_factory=dict)


# Small local models cannot be relied on for clean tool-calling, but they are
# perfectly capable of appending a tag. We ask for markers like [[face:happy]]
# and strip them out of the spoken text. Unknown or malformed markers are
# simply removed - a stray tag never gets read aloud.
_MARKER_RE = re.compile(r"\[\[\s*(face|move)\s*:\s*([a-z_]+)\s*\]\]", re.IGNORECASE)


def parse_markers(raw: str) -> Reply:
    """Splits marker tags out of a model reply into a structured :class:`Reply`."""
    face: str | None = None
    move: str | None = None

    for kind, value in _MARKER_RE.findall(raw):
        kind = kind.lower()
        value = value.lower()
        if kind == "face" and value in FACES:
            face = value
        elif kind == "move" and value in MOVES:
            move = value

    text = _MARKER_RE.sub("", raw)
    # Models sometimes wrap replies in quotes or leak a stage direction in
    # asterisks; neither should be spoken.
    text = re.sub(r"\*[^*]{0,60}\*", "", text)
    text = " ".join(text.split()).strip().strip('"')
    return Reply(text=text, face=face, move=move)


class LlmEngine(abc.ABC):
    name: str = "llm"

    @abc.abstractmethod
    async def chat(self, messages: list[Message]) -> Reply:
        """Generates one reply. Implementations must not raise on network
        errors - return a Reply with an empty text and an error in meta, so the
        session layer can say something friendly instead of crashing."""

    async def warmup(self) -> None:
        return None

    async def close(self) -> None:
        return None
