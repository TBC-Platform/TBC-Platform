# SPDX-License-Identifier: MIT
"""Smart home control, behind an allowlist."""

from __future__ import annotations

from typing import Protocol

from ..config import SmartHomeConfig
from .allowlist import ALLOWED_SERVICES, HARD_DENIED_DOMAINS, Decision, check, domain_of


class SmartHomeBackend(Protocol):
    name: str

    async def call(self, entity_id: str, service: str, **data) -> tuple[bool, str]: ...
    async def state(self, entity_id: str) -> str | None: ...
    async def close(self) -> None: ...


def build_smarthome(cfg: SmartHomeConfig) -> SmartHomeBackend | None:
    """Returns None when smart home control is disabled, which is the default."""
    if not cfg.enabled:
        return None
    backend = cfg.backend.strip().lower()
    if backend in {"homeassistant", "home-assistant", "ha"}:
        from .ha import HomeAssistantClient

        return HomeAssistantClient(cfg)
    if backend == "mqtt":
        from .mqtt import MqttClient

        return MqttClient(cfg)
    raise ValueError(
        f"unknown WALLE_SMARTHOME_BACKEND {cfg.backend!r}; expected 'homeassistant' or 'mqtt'"
    )


__all__ = [
    "ALLOWED_SERVICES",
    "HARD_DENIED_DOMAINS",
    "Decision",
    "SmartHomeBackend",
    "build_smarthome",
    "check",
    "domain_of",
]
