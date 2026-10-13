"""Auditable command-line dice roller for the D&D engine."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine_core import RulesError, d20_result, roll


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Roll RPG dice")
    parser.add_argument("expression", help="e.g. d20+5 or 2d6+3")
    parser.add_argument(
        "--mode",
        choices=("normal", "advantage", "disadvantage"),
        default="normal",
    )
    parser.add_argument(
        "--kind",
        choices=("attack", "ability_check", "saving_throw"),
    )
    parser.add_argument("--target", type=int)
    parser.add_argument("--edition", choices=("2014", "2024"))
    args = parser.parse_args(argv)

    trio = (args.kind, args.target, args.edition)
    if any(v is not None for v in trio) and not all(
        v is not None for v in trio
    ):
        parser.error("--kind, --target, and --edition must be provided together")

    try:
        outcome = roll(args.expression, args.mode)
        result = {"roll": outcome.to_dict()}
        if args.kind:
            result["resolution"] = d20_result(
                outcome, args.target, args.kind, args.edition
            )
    except RulesError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
