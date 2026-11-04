"""Bounded local Git reads only; no ambient config, credentials or allowed transport."""

from __future__ import annotations

import os
import re
import selectors
import signal

# Only allowlisted local plumbing, with no shell and an isolated environment.
import subprocess  # nosec B404
import time
from contextlib import suppress
from pathlib import Path

from grid.domain.numbers import DomainError

GIT = "/usr/bin/git"
ENV = {
    "PATH": "/usr/bin:/bin",
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_ALLOW_PROTOCOL": "",
}
MAX_OUTPUT = 262144
TIMEOUT = 2.0


def read(root: Path, arguments: tuple[str, ...]) -> bytes:
    """Fixed plumbing commands with a bounded pipe and one process group to reap."""
    if arguments != ("rev-parse", "--verify", "HEAD^{commit}") and not (
        len(arguments) == 8
        and arguments[:3] == ("ls-tree", "-r", "-z")
        and re.fullmatch(r"[0-9a-f]{40}", arguments[3]) is not None
        and arguments[4:] == ("--", "src/grid", "pyproject.toml", "uv.lock")
    ):
        raise DomainError("unsupported local Git command")
    command = [
        GIT,
        "--no-pager",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        *arguments,
    ]
    # The executable and argument grammar above are fixed; caller data cannot select a command.
    with subprocess.Popen(  # nosec B603
        command,
        cwd=root,
        env=ENV,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    ) as child:
        deadline = time.monotonic() + TIMEOUT
        output = bytearray()
        reaped = False
        try:
            if child.stdout is None:
                raise DomainError("local Git pipe unavailable")
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise DomainError("local Git read timed out")
                    chunk = os.read(child.stdout.fileno(), min(65536, MAX_OUTPUT + 1 - len(output)))
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > MAX_OUTPUT:
                        raise DomainError("local Git output bound")
            status = child.wait(timeout=max(0.001, deadline - time.monotonic()))
            reaped = True
            if status != 0:
                raise DomainError("local Git read unavailable")
            return bytes(output)
        finally:
            if not reaped:
                # The unreaped leader retains its PID even if a descendant holds the pipe.
                with suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=TIMEOUT)


def compare(
    root: Path | None, supplied: str, blobs: dict[str, tuple[str, str]]
) -> dict[str, object]:
    result: dict[str, object] = {
        "observed_head": None,
        "source_matches_head": None,
        "supplied_revision_matches_head": False,
        "revision_verified": False,
        "status": "unavailable",
        "scope": "Python source bytes/modes under src/grid plus pyproject.toml and uv.lock",
        "whole_repository_clean": "NOT_CHECKED; documentation/tests are outside this comparison",
    }
    if root is None:
        result["status"] = "no_source_checkout"
        return result
    try:
        head = read(root, ("rev-parse", "--verify", "HEAD^{commit}")).decode().strip()
        if re.fullmatch(r"[0-9a-f]{40}", head) is None:
            raise DomainError("unsupported Git object identity")
        result["observed_head"] = head
        result["supplied_revision_matches_head"] = supplied == head
        raw = read(
            root, ("ls-tree", "-r", "-z", head, "--", "src/grid", "pyproject.toml", "uv.lock")
        )
        expected: dict[str, tuple[str, str]] = {}
        for entry in raw.split(b"\0"):
            if not entry:
                continue
            metadata, path_bytes = entry.split(b"\t", 1)
            mode, kind, identity = metadata.decode("ascii").split()
            path = path_bytes.decode("utf-8")
            if path not in {"pyproject.toml", "uv.lock"} and not path.endswith(".py"):
                continue
            if (
                kind != "blob"
                or mode not in {"100644", "100755"}
                or re.fullmatch(r"[0-9a-f]{40}", identity) is None
                or path in expected
            ):
                raise DomainError("unsupported source tree entry")
            expected[path] = mode, identity
        matches = {"pyproject.toml", "uv.lock"} <= expected.keys() and expected == blobs
        result.update(
            source_matches_head=matches,
            revision_verified=matches and supplied == head,
            status="verified" if matches and supplied == head else "mismatch",
        )
    except (OSError, ValueError, DomainError, subprocess.SubprocessError):
        result["status"] = "unavailable"
    return result
