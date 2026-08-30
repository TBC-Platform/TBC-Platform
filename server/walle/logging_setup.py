# SPDX-License-Identifier: MIT
"""Console logging that stays readable while a robot is talking to you."""

from __future__ import annotations

import logging
import sys


class _CompactFormatter(logging.Formatter):
    """``12:04:31 INFO  walle.session  [walle-01] heard: 'hello'``

    Short module names and no milliseconds: the latency numbers are in the
    messages themselves, and the log is meant to be watchable in real time.
    """

    def format(self, record: logging.LogRecord) -> str:
        record.short_name = record.name.replace("walle.", "")
        return super().format(record)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _CompactFormatter(
            fmt="%(asctime)s %(levelname)-5s %(short_name)-14s %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # These three are chatty enough to bury the interesting lines.
    for noisy in ("httpx", "httpcore", "websockets", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
