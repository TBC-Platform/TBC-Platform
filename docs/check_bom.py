#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Verifies bom.csv adds up, and that docs/01-parts-list.md agrees with it.

A bill of materials with arithmetic errors sends people to the shop twice, so
this runs in CI alongside the code tests.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
BOM = DOCS / "bom.csv"
PARTS_LIST = DOCS / "01-parts-list.md"

GROUPS = ("core", "motion", "power", "passives", "hardware", "filament")

# Build name -> the groups it includes, and the total the markdown claims.
BUILDS = {
    "Voice assistant only": ({"core", "passives"}, 41.50),
    "Full robot, USB powered": ({"core", "motion", "passives", "hardware", "filament"}, 63.30),
    "Full robot, battery powered": (set(GROUPS), 83.00),
}


def load_rows() -> list[dict[str, str]]:
    with BOM.open(encoding="utf-8") as handle:
        lines = [line for line in handle if not line.startswith("#")]
    return list(csv.DictReader(lines))


def main() -> int:
    rows = load_rows()
    errors: list[str] = []

    if not rows:
        print("bom.csv is empty", file=sys.stderr)
        return 1

    subtotals: dict[str, float] = dict.fromkeys(GROUPS, 0.0)
    for row in rows:
        qty = float(row["qty"])
        unit = float(row["unit_price_usd"])
        total = float(row["total_usd"])
        if abs(qty * unit - total) > 0.005:
            errors.append(
                f"line {row['item']} ({row['part']}): "
                f"{qty} x {unit} = {qty * unit:.2f}, but total says {total:.2f}"
            )
        if row["group"] not in subtotals:
            errors.append(f"line {row['item']}: unknown group {row['group']!r}")
        else:
            subtotals[row["group"]] += total
        if row["required"] not in {"yes", "no"}:
            errors.append(f"line {row['item']}: required must be yes/no")

    markdown = PARTS_LIST.read_text(encoding="utf-8")
    for name, (groups, claimed) in BUILDS.items():
        actual = sum(subtotals[g] for g in groups)
        if abs(actual - claimed) > 0.005:
            errors.append(f"build {name!r}: bom.csv totals ${actual:.2f}, docs claim ${claimed:.2f}")
        if not re.search(rf"\|\s*{re.escape(name)}\s*\|[^|]*\|\s*\*\*\$?{claimed:.2f}\*\*", markdown):
            errors.append(f"build {name!r}: 01-parts-list.md does not show ${claimed:.2f}")

    print(f"{len(rows)} line items")
    for group in GROUPS:
        print(f"  {group:<10} ${subtotals[group]:>7.2f}")
    print(f"  {'TOTAL':<10} ${sum(subtotals.values()):>7.2f}")

    if errors:
        print("\nproblems:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("\nbom.csv is internally consistent and matches 01-parts-list.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
