# SPDX-License-Identifier: MIT
"""The smart home gate. These are the most safety-relevant tests in the repo."""

from __future__ import annotations

import pytest

from walle.smarthome import HARD_DENIED_DOMAINS, check, domain_of

ALLOWED = ["light.desk_lamp", "light.office_ceiling", "fan.office", "media_player.speaker"]
DOMAINS = ["light", "switch", "fan", "scene", "media_player"]


def gate(entity_id: str, service: str, *, entities=None, domains=None):
    return check(
        entity_id,
        service,
        allowed_entities=ALLOWED if entities is None else entities,
        allowed_domains=DOMAINS if domains is None else domains,
    )


def test_allows_an_allowlisted_light():
    assert gate("light.desk_lamp", "turn_on")
    assert gate("light.desk_lamp", "turn_off")
    assert gate("light.desk_lamp", "toggle")


def test_refuses_entity_not_on_the_list():
    decision = gate("light.bedroom", "turn_on")
    assert not decision
    assert "not allowlisted" in decision.reason


def test_empty_allowlist_refuses_everything():
    """Fail closed: a misconfigured server controls nothing, not everything."""
    decision = gate("light.desk_lamp", "turn_on", entities=[])
    assert not decision
    assert "no entities are allowlisted" in decision.reason


@pytest.mark.parametrize("entity_id", [
    "lock.front_door",
    "cover.garage",
    "alarm_control_panel.house",
    "valve.main_water",
    "camera.nursery",
    "vacuum.robot",
])
def test_hard_denied_domains_are_refused_even_if_allowlisted(entity_id):
    """A misheard word must never be able to unlock a door.

    These domains are refused even when explicitly allowlisted AND explicitly
    added to the allowed domains - the deny list is not configurable.
    """
    decision = check(
        entity_id, "turn_on",
        allowed_entities=[entity_id],
        allowed_domains=[domain_of(entity_id)],
    )
    assert not decision
    assert "never" in decision.reason
    assert domain_of(entity_id) in HARD_DENIED_DOMAINS


def test_refuses_domain_not_in_allowed_domains():
    decision = gate("switch.heater", "turn_on", entities=["switch.heater"], domains=["light"])
    assert not decision
    assert "WALLE_ALLOWED_DOMAINS" in decision.reason


def test_refuses_service_outside_the_domain_whitelist():
    """Reaching light.turn_on must not open the door to homeassistant.restart."""
    decision = gate("light.desk_lamp", "restart")
    assert not decision
    assert "not permitted" in decision.reason


def test_refuses_service_valid_for_a_different_domain():
    decision = gate("light.desk_lamp", "volume_set")
    assert not decision


def test_allows_media_player_volume():
    assert gate("media_player.speaker", "volume_set")


@pytest.mark.parametrize("entity_id", [
    "",
    "light",
    "light.desk.lamp",
    "light.desk lamp",
    "light./etc/passwd",
    "light.{{ states }}",
    'light."; DROP TABLE',
])
def test_refuses_malformed_entity_ids(entity_id):
    assert not gate(entity_id, "turn_on", entities=["*"])


def test_refuses_empty_service():
    assert not gate("light.desk_lamp", "")


def test_glob_patterns_work():
    decision = check(
        "light.office_desk", "turn_on",
        allowed_entities=["light.office_*"],
        allowed_domains=["light"],
    )
    assert decision
    assert not check(
        "light.bedroom_lamp", "turn_on",
        allowed_entities=["light.office_*"],
        allowed_domains=["light"],
    )


def test_glob_cannot_escape_the_hard_deny_list():
    """`*` in the allowlist is a bad idea, but it still must not unlock doors."""
    assert not check(
        "lock.front_door", "turn_on",
        allowed_entities=["*"],
        allowed_domains=["lock"],
    )


def test_case_is_normalised():
    assert gate("LIGHT.DESK_LAMP", "TURN_ON")


def test_domain_of():
    assert domain_of("light.desk_lamp") == "light"
    assert domain_of("nonsense") == ""
