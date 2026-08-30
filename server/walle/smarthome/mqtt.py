# SPDX-License-Identifier: MIT
"""MQTT backend for setups without Home Assistant.

Publishes to ``<prefix>/<domain>/<object_id>/set`` with a plain ``ON``/``OFF``
payload, which is the convention Tasmota, ESPHome and Zigbee2MQTT all
understand.

The same allowlist gate applies. MQTT has no per-topic permission model of its
own in most home setups, which makes the server-side allowlist the only thing
standing between a misheard sentence and every device on the broker - so it is
enforced identically here.

Optional dependency: ``pip install aiomqtt``.
"""

from __future__ import annotations

import contextlib
import logging

from ..config import SmartHomeConfig
from .allowlist import check, domain_of

log = logging.getLogger(__name__)


class MqttClient:
    name = "mqtt"

    def __init__(self, cfg: SmartHomeConfig) -> None:
        self.cfg = cfg
        self._client = None

    async def _connect(self):
        if self._client is not None:
            return self._client
        try:
            import aiomqtt  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "aiomqtt is not installed. Either `pip install aiomqtt` or set "
                "WALLE_SMARTHOME_BACKEND=homeassistant."
            ) from exc

        self._client = aiomqtt.Client(
            hostname=self.cfg.mqtt_host,
            port=self.cfg.mqtt_port,
            username=self.cfg.mqtt_username or None,
            password=self.cfg.mqtt_password or None,
            identifier="walle-server",
        )
        await self._client.__aenter__()
        log.info("mqtt connected to %s:%s", self.cfg.mqtt_host, self.cfg.mqtt_port)
        return self._client

    async def call(self, entity_id: str, service: str, **data) -> tuple[bool, str]:
        decision = check(
            entity_id,
            service,
            allowed_entities=self.cfg.allowed_entities,
            allowed_domains=self.cfg.allowed_domains,
        )
        if not decision:
            log.warning("refused %s on %s: %s", service, entity_id, decision.reason)
            return False, decision.reason

        domain = domain_of(entity_id)
        object_id = entity_id.split(".", 1)[1]
        topic = f"{self.cfg.mqtt_prefix}/{domain}/{object_id}/set"

        if service == "turn_on":
            payload = "ON"
        elif service == "turn_off":
            payload = "OFF"
        elif service == "toggle":
            payload = "TOGGLE"
        elif service == "set_percentage":
            payload = str(int(data.get("percentage", 50)))
        else:
            return False, f"service {service} has no MQTT mapping"

        try:
            client = await self._connect()
            await client.publish(topic, payload=payload.encode(), qos=1)
        except RuntimeError as exc:
            log.error("%s", exc)
            return False, "MQTT support is not installed"
        except Exception as exc:  # aiomqtt raises its own exception tree
            log.error("mqtt publish failed: %s", exc)
            self._client = None  # force a reconnect next time
            return False, "the MQTT broker is not reachable"

        log.info("mqtt: %s <- %s", topic, payload)
        return True, f"{topic} = {payload}"

    async def state(self, entity_id: str) -> str | None:
        # Reading state over MQTT means subscribing and waiting for a retained
        # message, which is more machinery than this is worth. Home Assistant
        # is the backend to use if you need state.
        return None

    async def close(self) -> None:
        if self._client is not None:
            # A broker that has already gone away raises on disconnect; that is
            # not something a shutdown path should care about.
            with contextlib.suppress(Exception):
                await self._client.__aexit__(None, None, None)
            self._client = None
