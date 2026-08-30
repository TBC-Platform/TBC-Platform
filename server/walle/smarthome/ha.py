# SPDX-License-Identifier: MIT
"""Home Assistant REST backend.

Talks to a Home Assistant instance on the LAN with a long-lived access token.
The token should belong to a dedicated non-admin user - see
docs/06-smart-home-security.md.
"""

from __future__ import annotations

import logging

import httpx

from ..config import SmartHomeConfig
from .allowlist import check, domain_of

log = logging.getLogger(__name__)


class HomeAssistantClient:
    name = "homeassistant"

    def __init__(self, cfg: SmartHomeConfig) -> None:
        self.cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.ha_url.rstrip("/"),
            timeout=httpx.Timeout(cfg.timeout_s, connect=3.0),
            headers={
                "Authorization": f"Bearer {cfg.ha_token}",
                "Content-Type": "application/json",
            },
        )

    async def call(self, entity_id: str, service: str, **data) -> tuple[bool, str]:
        """Calls a service on one entity. Returns (ok, human-readable detail).

        The allowlist is checked here, at the last possible moment before the
        network call, so there is exactly one place to audit and no way to
        bypass it by calling a different code path.
        """
        decision = check(
            entity_id,
            service,
            allowed_entities=self.cfg.allowed_entities,
            allowed_domains=self.cfg.allowed_domains,
        )
        if not decision:
            log.warning("refused %s.%s on %s: %s", domain_of(entity_id), service,
                        entity_id, decision.reason)
            return False, decision.reason

        domain = domain_of(entity_id)
        payload = {"entity_id": entity_id, **data}
        try:
            resp = await self._client.post(f"/api/services/{domain}/{service}", json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            detail = {
                401: "Home Assistant rejected the token",
                403: "the token's user has no access to that entity",
                404: "Home Assistant does not know that service",
            }.get(code, f"Home Assistant returned {code}")
            log.error("HA %s: %s", code, exc.response.text[:200])
            return False, detail
        except httpx.HTTPError as exc:
            log.error("HA unreachable at %s: %s", self.cfg.ha_url, exc)
            return False, "Home Assistant is not reachable"

        log.info("ha: %s.%s -> %s %s", domain, service, entity_id, data or "")
        return True, f"{domain}.{service} on {entity_id}"

    async def state(self, entity_id: str) -> str | None:
        """Reads one entity's state. Reads are also allowlisted - knowing
        whether the bedroom light is on is information leakage too."""
        decision = check(
            entity_id,
            "turn_on",  # any permitted service proves the entity is in scope
            allowed_entities=self.cfg.allowed_entities,
            allowed_domains=self.cfg.allowed_domains,
        )
        if not decision:
            return None
        try:
            resp = await self._client.get(f"/api/states/{entity_id}")
            resp.raise_for_status()
            return resp.json().get("state")
        except (httpx.HTTPError, ValueError):
            return None

    async def close(self) -> None:
        await self._client.aclose()
