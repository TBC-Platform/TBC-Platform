# SPDX-License-Identifier: MIT
"""Offline intent router.

This module is why the project can promise "voice commands work with no
internet at all". Everything a robot on a desk is actually asked to do most of
the time - drive forward, turn the lamp on, look at something, remember a name
- is matched here by pattern, executed locally, and answered from a template.
No LLM, no network, and a round trip that finishes in tens of milliseconds
instead of seconds.

Only genuinely open-ended questions fall through to :mod:`walle.llm`, which is
the one part of the system that may need the internet (and does not, if you run
Ollama locally).

Ordering matters: the most specific patterns come first. Each handler returns
an :class:`Intent`; ``Intent.kind == "chat"`` means "not my problem, ask the
model".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Filler the recogniser reliably includes and which never changes the meaning.
# Note the absence of "like": it is filler in "um, like, you know" but it is
# the entire meaning of "remember that I like strong coffee", and stripping it
# there stored nonsense.
_FILLER_RE = re.compile(
    r"\b(please|hey|okay|ok|um+|uh+|just|could you|can you|would you|"
    r"i want you to|i'd like you to)\b",
    re.IGNORECASE,
)
_WAKE_RE = re.compile(r"\b(wall[\s-]?e|wally|hi esp|alexa)\b", re.IGNORECASE)


@dataclass(slots=True)
class Intent:
    kind: str                       # see KINDS below
    params: dict[str, Any] = field(default_factory=dict)
    # A ready-made spoken reply for intents that do not need the LLM. Empty
    # means the caller should generate one (or the handler will fill it in).
    speech: str = ""
    face: str | None = None
    move: str | None = None
    confidence: float = 1.0


KINDS = (
    "move",        # drive the tracks
    "head",        # pan the head
    "stop",        # stop moving
    "look",        # capture a frame and describe it
    "smarthome",   # control an allowlisted entity
    "remember",    # store a durable fact
    "forget",      # drop stored facts
    "sleep",       # go quiet
    "status",      # battery / uptime / diagnostics
    "chat",        # fall through to the LLM
)


def normalise(text: str) -> str:
    """Lowercases, strips the wake word and filler, and squashes whitespace.

    Done before matching so "Wall-E, could you please turn on the desk lamp"
    and "turn on desk lamp" hit the same pattern.
    """
    text = (text or "").strip()
    text = _WAKE_RE.sub(" ", text)
    text = _FILLER_RE.sub(" ", text)
    text = text.replace("'", "'")
    text = re.sub(r"[^\w\s%'-]", " ", text)
    return " ".join(text.split()).lower()


# --------------------------------------------------------------------------
# Movement
# --------------------------------------------------------------------------

_MOVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(go|move|drive|come)\s+(forward|forwards|ahead|straight)\b"), "forward"),
    (re.compile(r"\b(go|move|drive)\s+(back|backward|backwards)\b"), "back"),
    (re.compile(r"\b(back\s+up|reverse)\b"), "back"),
    # "look left" deliberately does NOT appear here - it belongs to the head
    # servo, which is both more natural and a lot quieter than spinning.
    (re.compile(r"\b(turn|spin|rotate)\s+left\b"), "left"),
    (re.compile(r"\b(turn|spin|rotate)\s+right\b"), "right"),
    (re.compile(r"\b(turn|spin)\s+(a)?round\b"), "left"),
    (re.compile(r"\b(dance|wiggle|celebrate|shimmy)\b"), "wiggle"),
)

_STOP_RE = re.compile(r"\b(stop|halt|freeze|stay|hold still|don'?t move)\b")

_DURATION_RE = re.compile(r"\b(?:for\s+)?(\d+(?:\.\d+)?)\s*(second|seconds|sec|s)\b")
_SPEED_RE = re.compile(r"\b(slowly|slow|fast|quickly|full speed)\b")

_MOVE_REPLIES = {
    "forward": "On my way.",
    "back": "Backing up.",
    "left": "Turning left.",
    "right": "Turning right.",
    "wiggle": "Watch this!",
}


def _match_move(text: str) -> Intent | None:
    if _STOP_RE.search(text):
        return Intent(kind="stop", speech="Stopping.", face="idle")

    for pattern, cmd in _MOVE_PATTERNS:
        if pattern.search(text):
            # Duration: default 800 ms, which is about 15 cm on carpet.
            duration_ms = 800
            match = _DURATION_RE.search(text)
            if match:
                duration_ms = int(min(float(match.group(1)), 5.0) * 1000)

            speed = 60
            speed_match = _SPEED_RE.search(text)
            if speed_match:
                word = speed_match.group(1)
                speed = 35 if word.startswith("slow") else 90

            if cmd == "wiggle":
                duration_ms = 0  # the firmware runs its own wiggle timing

            return Intent(
                kind="move",
                params={"cmd": cmd, "speed": speed, "ms": duration_ms},
                speech=_MOVE_REPLIES[cmd],
                face="happy" if cmd == "wiggle" else None,
            )
    return None


# --------------------------------------------------------------------------
# Head
# --------------------------------------------------------------------------

_HEAD_RE = re.compile(r"\b(head|face)\b.*\b(left|right|centre|center|forward|middle)\b")
_HEAD_ONLY_RE = re.compile(r"\blook\s+(left|right|straight|ahead|forward|at me)\b")


def _match_head(text: str) -> Intent | None:
    match = _HEAD_RE.search(text) or _HEAD_ONLY_RE.search(text)
    if not match:
        return None
    direction = match.groups()[-1]
    # Servo degrees: 45 is hard left, 135 hard right, 90 centre.
    degrees = {
        "left": 50,
        "right": 130,
        "centre": 90, "center": 90, "middle": 90,
        "forward": 90, "straight": 90, "ahead": 90, "at me": 90,
    }.get(direction, 90)
    return Intent(kind="head", params={"deg": degrees}, speech="")


# --------------------------------------------------------------------------
# Vision
# --------------------------------------------------------------------------

_LOOK_RE = re.compile(
    r"\b(what (do|can) you see|what'?s (in front of you|there|that)|"
    r"look (around|at (that|this))|describe what you see|take a (photo|picture)|"
    r"can you see (anything|me|that))\b"
)


def _match_look(text: str, raw: str = "") -> Intent | None:
    """Checks the raw text as well as the normalised form.

    "can you" is filler in "can you turn on the lamp", so normalise() removes
    it - but it is load-bearing in "what can you see". Matching both forms is
    cheaper than teaching the filler pattern about context.
    """
    if _LOOK_RE.search(text) or (raw and _LOOK_RE.search(" ".join(raw.lower().split()))):
        return Intent(kind="look", face="thinking")
    return None


# --------------------------------------------------------------------------
# Smart home
# --------------------------------------------------------------------------

_ON_RE = re.compile(r"\b(turn on|switch on|put on|light up|enable)\b")
_OFF_RE = re.compile(r"\b(turn off|switch off|shut off|kill|disable|turn out)\b")
_TOGGLE_RE = re.compile(r"\btoggle\b")
_PERCENT_RE = re.compile(r"\b(\d{1,3})\s*(?:%|percent)\b")


def _match_smarthome(text: str, registry) -> Intent | None:
    """Needs the device registry to know whether a device was even named."""
    if registry is None or len(registry) == 0:
        return None

    if _OFF_RE.search(text):
        service = "turn_off"
    elif _ON_RE.search(text):
        service = "turn_on"
    elif _TOGGLE_RE.search(text):
        service = "toggle"
    else:
        return None

    entity_id, alias = registry.resolve(text)
    if not entity_id:
        # A control verb with no recognised device: answer helpfully rather
        # than sending "turn on the thingy" to the LLM.
        names = ", ".join(registry.known_names[:5])
        return Intent(
            kind="smarthome",
            params={"entity_id": None},
            speech=f"I am not sure which device you mean. I know about {names}.",
            face="confused",
        )

    data: dict[str, Any] = {}
    percent = _PERCENT_RE.search(text)
    if percent and entity_id.startswith("fan."):
        service = "set_percentage"
        data["percentage"] = max(0, min(100, int(percent.group(1))))

    return Intent(
        kind="smarthome",
        params={"entity_id": entity_id, "service": service, "data": data, "alias": alias},
    )


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------

_REMEMBER_RE = re.compile(r"\b(remember|note|keep in mind)\s+(that\s+)?(?P<fact>.+)")
_MY_NAME_RE = re.compile(r"\bmy name is\s+(?P<name>[\w' -]{2,40})")
_FORGET_RE = re.compile(r"\b(forget (everything|what i said|about me)|clear your memory)\b")


def _match_memory(text: str, raw: str = "") -> Intent | None:
    """``text`` is normalised (for matching); ``raw`` is what the user actually
    said, which is what gets stored - "I like strong coffee" reads better in a
    prompt than "i like strong coffee"."""
    if _FORGET_RE.search(text):
        return Intent(kind="forget", speech="Forgotten. A clean slate.", face="idle")

    name = _MY_NAME_RE.search(text)
    if name:
        value = name.group("name").strip().title()
        return Intent(
            kind="remember",
            params={"key": "name", "value": value},
            speech=f"Nice to meet you, {value}.",
            face="happy",
        )

    match = _REMEMBER_RE.search(raw) or _REMEMBER_RE.search(text)
    if match:
        fact = match.group("fact").strip().rstrip(".!?")
        if len(fact) < 3:
            return None
        # Key the fact on its first couple of words so a later "remember I
        # like tea" updates the same row instead of stacking duplicates.
        key = " ".join(fact.split()[:3])
        return Intent(
            kind="remember",
            params={"key": key, "value": fact},
            speech="Got it, I will remember that.",
            face="happy",
        )
    return None


# --------------------------------------------------------------------------
# Housekeeping
# --------------------------------------------------------------------------

_SLEEP_RE = re.compile(r"\b(go to sleep|sleep now|goodnight|good night|be quiet|shush)\b")
_STATUS_RE = re.compile(
    r"\b(battery|how are you feeling|status report|diagnostics|are you (ok|okay)|"
    r"how much (battery|charge))\b"
)


def _match_housekeeping(text: str) -> Intent | None:
    if _SLEEP_RE.search(text):
        return Intent(kind="sleep", speech="Goodnight.", face="sleep")
    if _STATUS_RE.search(text):
        return Intent(kind="status", face="idle")
    return None


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def route(text: str, registry=None) -> Intent:
    """Classifies one utterance.

    Order is deliberate: "stop" beats everything, then explicit device control,
    then memory, then the softer patterns. Anything unmatched becomes a chat
    turn for the LLM.
    """
    normalised = normalise(text)
    if not normalised:
        return Intent(kind="chat", confidence=0.0)

    memory_intent = _match_memory(normalised, text)
    for intent in (
        _match_move(normalised),
        _match_head(normalised),
        _match_look(normalised, text),
        memory_intent,
        _match_housekeeping(normalised),
    ):
        if intent is not None:
            return intent

    intent = _match_smarthome(normalised, registry)
    if intent is not None:
        return intent

    return Intent(kind="chat")
