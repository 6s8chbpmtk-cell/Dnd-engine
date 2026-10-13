"""Local CLI for source-attributed OS-random dice, with optional 5e d20 outcome.

This CLI never requests fake dice or allows test injection. It prints a single
JSON response that can be copied into the engine's Airtable Dice Audit v2.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Run the script directly without installation, or run from a checked out repo.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine_core import RulesError, d20_result, roll


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Produce an auditable OS-random RPG dice result")
    parser.add_argument("expression", help="dice notation: d20+5, 2d6+3")
    parser.add_argument("--mode", choices=("normal", "advantage", "disadvantage"), default="normal")
    parser.add_argument("--kind", choices=("attack", "ability_check", "saving_throw"))
    parser.add_argument("--target", type=int, help="AC/DC; requires --kind and --edition")
    parser.add_argument("--edition", choices=("2014", "2024"))
    args = parser.parse_args(argv)
    trio = (args.kind, args.target, args.edition)
    if any(v is not None for v in trio) and not all(v is not None for v in trio):
        parser.error("--kind, --target, and --edition must be provided together")
    try:
        outcome = roll(args.expression, args.mode)
        result = {"roll": outcome.to_dict()}
        if args.kind:
            result["resolution"] = d20_result(outcome, args.target, args.kind, args.edition)
    except RulesError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
