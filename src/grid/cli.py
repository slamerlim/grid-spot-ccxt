"""The gridctl entrypoint remains an offline doctor."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from grid.config import ConfigError, load_config
from grid.doctor import run_doctor


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse normally repeats arbitrary arguments, which may contain secrets.
        self.print_usage(sys.stderr)
        self.exit(2, "gridctl: error: invalid command or arguments; use --help.\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Return 0 for a report, 1 for blocked --check, or 2 for invalid config."""
    parser = _ArgumentParser(prog="gridctl", description="GRID I3 offline diagnostics")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="report offline live-readiness blockers")
    doctor.add_argument(
        "--config", type=Path, help="explicit YAML path; no automatic config search"
    )
    doctor.add_argument("--json", action="store_true", help="emit a structured report")
    doctor.add_argument("--check", action="store_true", help="exit nonzero while live is blocked")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        if args.json:
            print(
                json.dumps(
                    {"schema_version": 1, "live": "DISABLED", "ready": False, "error": str(exc)},
                    sort_keys=True,
                )
            )
        else:
            print(f"LIVE: DISABLED\nConfiguration error: {exc}", file=sys.stderr)
        return 2
    report = run_doctor(config)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True) if args.json else report.to_text())
    return 1 if args.check and not report.ready else 0
