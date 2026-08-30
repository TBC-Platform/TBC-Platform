# SPDX-License-Identifier: MIT
"""The smart home safety gate.

This module is the answer to "what's the safest way to implement smart home
control without exposing my home network". The full reasoning is in
docs/06-smart-home-security.md; the short version is four rules, all enforced
here:

1. **Outbound only.** The robot connects to the server; the server connects to
   Home Assistant or the MQTT broker. Nothing listens on the internet and no
   port is ever forwarded. There is no inbound path to attack.
2. **Allowlist, not blocklist.** Only entities named in
   ``WALLE_ALLOWED_ENTITIES`` can be touched. An empty allowlist refuses
   everything - fail closed.
3. **Domain restrictions.** Even an allowlisted entity is refused if its domain
   is not in ``WALLE_ALLOWED_DOMAINS``. Locks, covers/garage doors, alarm
   panels and water valves are rejected unconditionally, no matter what the
   config says, because a misheard word must never be able to unlock a door.
4. **Least-privilege credentials.** The Home Assistant token belongs to a
   dedicated non-admin user that only has access to the allowlisted entities.
   If this server is compromised, the blast radius is the lamps.

Every decision is a pure function, which means it is testable - see
``tests/test_allowlist.py``.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Domains refused unconditionally. This list is not configurable on purpose: a
# voice assistant driven by a speech recogniser should not be one homophone
# away from opening your house. Someone who really wants this has to edit the
# source, at which point they have made a deliberate choice.
HARD_DENIED_DOMAINS: frozenset[str] = frozenset(
    {
        "lock",           # doors
        "cover",          # garage doors, blinds on the same domain
        "alarm_control_panel",
        "valve",          # water shutoffs
        "water_heater",
        "camera",         # no remote enabling of cameras
        "device_tracker",
        "person",
        "vacuum",         # a vacuum leaving its dock unattended can flood a room
    }
)

# Services allowed per domain. Anything not listed is refused, so the model
# cannot reach for `light.turn_on` and get `homeassistant.restart`.
ALLOWED_SERVICES: dict[str, frozenset[str]] = {
    "light": frozenset({"turn_on", "turn_off", "toggle"}),
    "switch": frozenset({"turn_on", "turn_off", "toggle"}),
    "fan": frozenset({"turn_on", "turn_off", "toggle", "set_percentage"}),
    "scene": frozenset({"turn_on"}),
    "script": frozenset({"turn_on"}),
    "input_boolean": frozenset({"turn_on", "turn_off", "toggle"}),
    "media_player": frozenset(
        {"turn_on", "turn_off", "media_play", "media_pause", "media_stop",
         "volume_set", "volume_up", "volume_down", "media_next_track",
         "media_previous_track"}
    ),
    "climate": frozenset({"set_temperature", "turn_on", "turn_off"}),
}


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


ALLOW = Decision(True)


def domain_of(entity_id: str) -> str:
    """``light.desk_lamp`` -> ``light``. Returns "" for a malformed id."""
    if "." not in entity_id:
        return ""
    return entity_id.split(".", 1)[0].strip().lower()


def check(
    entity_id: str,
    service: str,
    *,
    allowed_entities: list[str],
    allowed_domains: list[str],
) -> Decision:
    """Decides whether one action may proceed. Fails closed on anything odd.

    ``allowed_entities`` entries may be exact ids (``light.desk_lamp``) or
    glob patterns (``light.office_*``). Patterns are convenient but widen the
    blast radius, so the docs recommend exact ids.
    """
    entity_id = (entity_id or "").strip().lower()
    service = (service or "").strip().lower()

    if not entity_id or not service:
        return Decision(False, "missing entity or service")

    # Reject anything that is not a plain `domain.object_id`. This also blocks
    # attempts to smuggle a path or a template through.
    if entity_id.count(".") != 1 or any(c in entity_id for c in " /\\{}\"'"):
        return Decision(False, f"malformed entity id {entity_id!r}")

    domain = domain_of(entity_id)
    if not domain:
        return Decision(False, f"no domain in {entity_id!r}")

    if domain in HARD_DENIED_DOMAINS:
        return Decision(False, f"domain {domain!r} can never be controlled by voice")

    if not allowed_entities:
        return Decision(
            False,
            "no entities are allowlisted (set WALLE_ALLOWED_ENTITIES)",
        )

    if domain not in {d.strip().lower() for d in allowed_domains}:
        return Decision(False, f"domain {domain!r} is not in WALLE_ALLOWED_DOMAINS")

    if not any(fnmatch.fnmatchcase(entity_id, pattern.strip().lower())
               for pattern in allowed_entities):
        return Decision(False, f"{entity_id} is not allowlisted")

    permitted = ALLOWED_SERVICES.get(domain)
    if permitted is None:
        return Decision(False, f"no services are defined for domain {domain!r}")
    if service not in permitted:
        return Decision(False, f"service {domain}.{service} is not permitted")

    return ALLOW
