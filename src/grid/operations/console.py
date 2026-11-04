"""Explicit mock operator commands; client packets never write the ledger."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from grid.domain.numbers import DomainError, bounded_int, identifier
from grid.operations.local import (
    ACTIONS,
    CONFIG_FIELDS,
    KEY_EDITS,
    REQUEST_BYTES,
    SENSITIVE,
    STORAGE_FIELDS,
    STORAGE_VIEW,
    VIEWS,
    call,
    decode,
)
from grid.persistence.storage import private_path
from grid.portfolio.model import ExitApproval
from grid.research.artifacts import canonical, publish
from grid.universe.controls import Retirement
from grid.universe.model import SelectionMode


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, "gridctl operator: invalid arguments; use --help\n")


def load_request(path: Path) -> dict[str, Any]:
    target = private_path(path, new=False)
    with target.open("rb") as stream:
        raw = stream.read(REQUEST_BYTES + 1)
    if len(raw) > REQUEST_BYTES:
        raise DomainError("retained operator request exceeds bound")
    return decode(raw)


async def execute(args: argparse.Namespace) -> tuple[int, object]:
    if args.op == STORAGE_VIEW:
        identifier(args.request_id)
        response = await call(
            args.socket, {"version": 1, "op": args.op, "request_id": args.request_id}
        )
        return (0 if response["ok"] else 2), response
    if args.op in VIEWS:
        response = await call(args.socket, {"version": 1, "op": args.op})
        return (0 if response["ok"] else 2), response
    bounded_int(args.revision, 1, 2**63 - 2)
    identifier(args.reason)
    desired = {
        "version": 1,
        "op": args.op,
        "revision": args.revision,
        "targets": "all" if args.all else sorted(args.allocation),
        "reason": args.reason,
        "confirmed": args.op in SENSITIVE,
    }
    configuring = args.op in CONFIG_FIELDS
    if args.op in STORAGE_FIELDS:
        desired.update(
            source=str(args.source.absolute()), destination=str(args.destination.absolute())
        )
        if args.op == "restore-verify":
            desired.update(digest=args.digest, requests=args.requests)
    if configuring:
        bounded_int(args.universe_revision, 1, 2**63 - 2)
        desired["universe_revision"] = args.universe_revision
        for field in CONFIG_FIELDS[args.op]:
            desired[field] = sorted(args.venue) if field == "venues" else getattr(args, field)
    needs_exit = (
        args.op == "limit-liquidate"
        or configuring
        and desired.get("retirement") == Retirement.LIMIT
    )
    if needs_exit:
        approval = ExitApproval(args.cycle, args.policy_sha256)
        desired.update(cycle_id=approval.cycle_id, policy_sha256=approval.policy_sha256)
    elif configuring and (
        getattr(args, "cycle", None) is not None or getattr(args, "policy_sha256", None) is not None
    ):
        raise DomainError("exit approval only belongs to bounded-limit-exit")
    if args.request.exists() or args.request.is_symlink():
        packet = load_request(args.request)
        if any(packet.get(k) != v for k, v in desired.items()):
            raise DomainError("retained request differs from the requested action")
    else:
        if args.confirm:
            raise DomainError("prepare and review the request before confirming")
        private_path(args.request, new=True)
        state = await call(args.socket, {"version": 1, "op": "status"})
        if not state["ok"]:
            return 2, state
        packet = dict(
            desired,
            session_id=state["session_id"],
            request_id="operator-" + uuid.uuid4().hex,
            observed_ms=state["data"]["observed_ms"],
        )
        if len(canonical(packet)) > REQUEST_BYTES:
            raise DomainError("operator request exceeds bound")
        publish(args.request, packet)
        descriptor = os.open(args.request.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)  # The exact replay identity is durable before sending.
        finally:
            os.close(descriptor)
    if args.op in SENSITIVE and not args.confirm:
        return 3, {
            "mode": "mock",
            "result": "confirmation-required",
            "request": packet,
            "next": "review the saved request, then repeat with --confirm",
        }
    response = await call(args.socket, packet)
    return (0 if response["ok"] else 2), response


def main(argv: Sequence[str] | None = None) -> int:
    parser = Parser(prog="gridctl operator", description="Local mock owner; LIVE DISABLED")
    commands = parser.add_subparsers(dest="op", required=True)
    serve = commands.add_parser("serve-mock", help="run a finite invented-account session")
    serve.add_argument("--directory", type=Path, required=True)
    serve.add_argument("--sqlite-source-id", required=True)
    serve.add_argument("--seconds", type=int, required=True)
    serve.add_argument("--resume", action="store_true")
    for name in sorted(
        VIEWS | ACTIONS.keys() | CONFIG_FIELDS.keys() | STORAGE_FIELDS.keys() | {STORAGE_VIEW}
    ):
        command = commands.add_parser(name)
        command.add_argument("--socket", type=Path, required=True)
        if name == STORAGE_VIEW:
            command.add_argument("--request-id", required=True)
        elif name not in VIEWS:
            command.add_argument("--revision", type=int, required=True)
            command.add_argument("--reason", required=True)
            command.add_argument("--request", type=Path, required=True)
            command.add_argument("--confirm", action="store_true")
            if name in STORAGE_FIELDS:
                command.add_argument(
                    "--output" if name == "backup" else "--source",
                    dest="source",
                    type=Path,
                    required=True,
                    help="new backup file" if name == "backup" else "existing backup file",
                )
                command.add_argument(
                    "--verify-to" if name == "backup" else "--destination",
                    dest="destination",
                    type=Path,
                    required=True,
                    help="new separate restored copy; never replaces an existing ledger",
                )
                if name == "restore-verify":
                    command.add_argument("--digest", required=True)
                    command.add_argument("--requests", type=int, required=True)
            if name == "limit-liquidate" or name in ("deny", "disable-venue"):
                command.add_argument("--cycle", required=name == "limit-liquidate")
                command.add_argument("--policy-sha256", required=name == "limit-liquidate")
            if name in CONFIG_FIELDS:
                command.add_argument("--universe-revision", type=int, required=True)
                for field in CONFIG_FIELDS[name]:
                    if field == "venues":
                        command.add_argument("--venue", action="append", required=True)
                    elif field == "capacity":
                        command.add_argument("--capacity", type=int, required=True)
                    elif field == "mode":
                        command.add_argument("--mode", choices=list(SelectionMode), required=True)
                    else:
                        command.add_argument(
                            "--retirement", choices=list(Retirement), required=True
                        )
            if name not in CONFIG_FIELDS and name not in STORAGE_FIELDS or name in KEY_EDITS:
                targets = command.add_mutually_exclusive_group(required=True)
                targets.add_argument("--all", action="store_true")
                targets.add_argument("--allocation", action="append")
            else:
                command.set_defaults(all=True, allocation=None)
    args = parser.parse_args(argv)
    try:
        if args.op == "serve-mock":
            from grid.operations.session import MockSession

            bounded_int(args.seconds, 1, 300)

            async def serve_mock() -> None:
                session = await MockSession.open(
                    args.directory, frozenset({args.sqlite_source_id}), resume=args.resume
                )
                await session.run(args.seconds)

            asyncio.run(serve_mock())
            return 0
        code, result = asyncio.run(execute(args))
        print(canonical(result).decode())
        return code
    except KeyboardInterrupt:
        return 130
    except Exception:
        # No untrusted arguments, request contents or chained exception traces.
        print(
            "gridctl operator: refused or outcome unknown; "
            "retain any request file and inspect status.",
            file=sys.stderr,
        )
        return 2
