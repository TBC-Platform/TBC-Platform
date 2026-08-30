# SPDX-License-Identifier: MIT
"""System prompt construction.

The prompt is short on purpose. Every token here is prefill on every single
turn, and on a 3B model running locally that prefill is a measurable part of
the response latency. It also keeps small models on task: long persona
documents make them ramble, and rambling is directly proportional to how long
the user waits for the robot to stop talking.
"""

from __future__ import annotations

from ..protocol import FACES, MOVES

PERSONA = (
    "You are Wall-E, a small curious desk robot. You are warm, a bit naive, and "
    "endlessly interested in the person you are talking to."
)

STYLE_RULES = (
    "Answer in one or two short sentences - your reply is read aloud by a small "
    "speaker, so anything long is tiring to listen to. Never use markdown, "
    "bullet points, emoji, or stage directions. Say numbers as words. If you do "
    "not know something, say so plainly."
)


def build_system_prompt(
    *,
    user_facts: list[str] | None = None,
    vision_context: str | None = None,
    smart_home_hint: str | None = None,
) -> str:
    """Assembles the system message for one turn.

    ``user_facts`` are the durable things the robot has been told to remember
    (see memory.store). They are injected fresh each turn rather than being
    left in the conversation history, so they survive history truncation.
    """
    parts = [PERSONA, STYLE_RULES]

    parts.append(
        "You can show an expression and move by appending markers at the very end "
        f"of your reply. Faces: {', '.join(FACES)}. Moves: {', '.join(MOVES)}. "
        "Example: I found it! [[face:happy]] [[move:wiggle]] "
        "Use at most one of each, and only when it genuinely fits."
    )

    if user_facts:
        joined = "; ".join(user_facts[:12])
        parts.append(f"Things you remember about this person: {joined}.")

    if vision_context:
        parts.append(
            f"You just looked through your camera and saw: {vision_context}. "
            "Only mention this if the person asked about what you can see."
        )

    if smart_home_hint:
        parts.append(smart_home_hint)

    return "\n\n".join(parts)
