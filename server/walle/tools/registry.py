# SPDX-License-Identifier: MIT
"""Spoken device names -> Home Assistant / MQTT entity ids.

Nobody says "light dot desk underscore lamp". This maps the words people
actually use onto entity ids, and it is loaded from a JSON file the owner
edits (``data/devices.json``) rather than being hard-coded, so adding a lamp
does not mean touching Python.

Format::

    {
      "desk lamp":     "light.desk_lamp",
      "the lamp":      "light.desk_lamp",
      "office light":  "light.office_ceiling",
      "fan":           "fan.office"
    }

Aliases are matched longest-first, so "office light" wins over "light".
Entity ids listed here still have to pass the allowlist - this file is
convenience, not permission.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

EXAMPLE = {
    "desk lamp": "light.desk_lamp",
    "the lamp": "light.desk_lamp",
    "office light": "light.office_ceiling",
    "office lights": "light.office_ceiling",
    "fan": "fan.office",
    "speaker": "media_player.office_speaker",
}


class DeviceRegistry:
    def __init__(self, aliases: dict[str, str] | None = None) -> None:
        # Normalised to lowercase, sorted longest-first for greedy matching.
        self._aliases: list[tuple[str, str]] = sorted(
            ((k.strip().lower(), v.strip()) for k, v in (aliases or {}).items()),
            key=lambda item: len(item[0]),
            reverse=True,
        )

    @classmethod
    def load(cls, path: Path) -> DeviceRegistry:
        if not path.is_file():
            log.info("no device alias file at %s (smart home names unavailable)", path)
            return cls({})
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.error("could not read %s: %s", path, exc)
            return cls({})
        if not isinstance(data, dict):
            log.error("%s must contain a JSON object of alias -> entity_id", path)
            return cls({})
        log.info("loaded %d device aliases from %s", len(data), path)
        return cls({str(k): str(v) for k, v in data.items()})

    @staticmethod
    def write_example(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(EXAMPLE, indent=2) + "\n", encoding="utf-8")

    def resolve(self, phrase: str) -> tuple[str | None, str | None]:
        """Finds a device mentioned anywhere in ``phrase``.

        Returns (entity_id, matched_alias), or (None, None). Matching is
        substring-based because speech comes in as a whole sentence:
        "turn on the desk lamp please".
        """
        text = " ".join((phrase or "").lower().split())
        for alias, entity_id in self._aliases:
            if alias in text:
                return entity_id, alias
        return None, None

    @property
    def known_names(self) -> list[str]:
        return [alias for alias, _ in self._aliases]

    def __len__(self) -> int:
        return len(self._aliases)
