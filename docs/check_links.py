#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Checks that every relative link in the Markdown files points at a real file.

Broken links in a build guide send people looking for a page that does not
exist, which is a bad first hour with a project.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")


def main() -> int:
    broken: list[str] = []
    checked = 0

    for markdown in sorted(ROOT.rglob("*.md")):
        if any(part in {".git", ".pio", ".venv", "node_modules"} for part in markdown.parts):
            continue
        text = markdown.read_text(encoding="utf-8")
        for target in LINK_RE.findall(text):
            target = target.split(" ")[0].strip()
            if not target or target.startswith(SKIP_PREFIXES):
                continue
            path = (markdown.parent / target.split("#")[0]).resolve()
            checked += 1
            if not path.exists():
                broken.append(f"{markdown.relative_to(ROOT)} -> {target}")

    print(f"checked {checked} relative links across the Markdown files")
    if broken:
        print("\nbroken:", file=sys.stderr)
        for item in broken:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("all resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
