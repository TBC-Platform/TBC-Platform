# SPDX-License-Identifier: MIT
"""Offline intent routing - the reason voice commands work with no internet."""

from __future__ import annotations

import pytest

from walle.intent import normalise, route
from walle.tools import DeviceRegistry


@pytest.fixture
def registry():
    return DeviceRegistry(
        {"desk lamp": "light.desk_lamp", "office light": "light.office_ceiling", "fan": "fan.office"}
    )


def test_normalise_strips_wake_word_and_filler():
    assert normalise("Wall-E, could you please go forward") == "go forward"
    assert normalise("Hey wally, um, stop!") == "stop"


def test_normalise_keeps_like_as_a_verb():
    """'like' is filler in 'um, like', but the whole point of 'I like coffee'."""
    assert "like" in normalise("remember that I like coffee")


@pytest.mark.parametrize("text,cmd", [
    ("go forward", "forward"),
    ("drive straight ahead", "forward"),
    ("back up", "back"),
    ("move backwards", "back"),
    ("turn left", "left"),
    ("spin right", "right"),
    ("do a little dance", "wiggle"),
])
def test_movement_commands(text, cmd):
    intent = route(text)
    assert intent.kind == "move"
    assert intent.params["cmd"] == cmd
    assert intent.speech


def test_stop_beats_everything():
    for text in ("stop", "stop moving forward", "freeze!", "don't move"):
        assert route(text).kind == "stop"


def test_duration_is_parsed_and_capped():
    assert route("go forward for 3 seconds").params["ms"] == 3000
    # A runaway number must not send the robot off the desk.
    assert route("go forward for 900 seconds").params["ms"] == 5000


def test_speed_words():
    assert route("go forward slowly").params["speed"] == 35
    assert route("go forward quickly").params["speed"] == 90
    assert route("go forward").params["speed"] == 60


def test_look_left_pans_the_head_rather_than_spinning():
    intent = route("look left")
    assert intent.kind == "head"
    assert intent.params["deg"] < 90
    assert route("look right").params["deg"] > 90
    assert route("look straight ahead").params["deg"] == 90


@pytest.mark.parametrize("text", [
    "what do you see",
    "what can you see",
    "what's in front of you",
    "take a picture",
    "describe what you see",
])
def test_vision_requests(text):
    assert route(text).kind == "look"


def test_smart_home_on_off(registry):
    on = route("turn on the desk lamp", registry)
    assert on.kind == "smarthome"
    assert on.params["entity_id"] == "light.desk_lamp"
    assert on.params["service"] == "turn_on"

    off = route("switch off the office light", registry)
    assert off.params["entity_id"] == "light.office_ceiling"
    assert off.params["service"] == "turn_off"


def test_longest_alias_wins(registry):
    """'office light' must beat a bare 'light' substring."""
    intent = route("turn on the office light", registry)
    assert intent.params["entity_id"] == "light.office_ceiling"


def test_fan_percentage(registry):
    intent = route("turn on the fan to 70 percent", registry)
    assert intent.params["service"] == "set_percentage"
    assert intent.params["data"]["percentage"] == 70


def test_unknown_device_is_answered_helpfully(registry):
    intent = route("turn on the flux capacitor", registry)
    assert intent.kind == "smarthome"
    assert intent.params["entity_id"] is None
    assert "desk lamp" in intent.speech


def test_no_registry_means_no_smart_home_intent():
    """With no devices configured, 'turn on the light' is just conversation."""
    assert route("turn on the desk lamp", None).kind == "chat"


def test_remember_a_name():
    intent = route("my name is sam")
    assert intent.kind == "remember"
    assert intent.params == {"key": "name", "value": "Sam"}


def test_remember_keeps_the_users_own_words():
    intent = route("remember that I like strong coffee.")
    assert intent.kind == "remember"
    assert intent.params["value"] == "I like strong coffee"


def test_forget():
    assert route("forget everything").kind == "forget"
    assert route("clear your memory").kind == "forget"


def test_housekeeping():
    assert route("goodnight").kind == "sleep"
    assert route("how much battery do you have").kind == "status"


@pytest.mark.parametrize("text", [
    "what is the capital of France",
    "tell me a joke about robots",
    "why is the sky blue",
    "",
])
def test_open_questions_fall_through_to_chat(text):
    assert route(text).kind == "chat"
