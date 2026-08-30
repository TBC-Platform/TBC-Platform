# SPDX-License-Identifier: MIT
"""``python -m walle`` - start the brain."""

from __future__ import annotations

import sys

import uvicorn

from .config import Config
from .logging_setup import setup_logging


def main() -> int:
    config = Config.load()
    setup_logging(config.server.log_level)

    problems = config.validate()
    fatal = [p for p in problems if "WALLE_AUTH_TOKEN is not set" in p]
    for problem in problems:
        print(("ERROR: " if problem in fatal else "warning: ") + problem, file=sys.stderr)
    if fatal:
        # Everything else is a warning you can work around while testing. An
        # open socket that drives motors is not.
        return 2

    from .app import create_app

    uvicorn.run(
        create_app(config),
        host=config.server.host,
        port=config.server.port,
        log_level=config.server.log_level.lower(),
        # The default 16 MiB frame limit is far larger than anything we send;
        # capping it keeps a malformed length field from allocating wildly.
        ws_max_size=1024 * 1024,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
