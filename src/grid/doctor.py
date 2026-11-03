"""Offline readiness evidence; no CCXT import, clients, sockets, or DB connections."""

from __future__ import annotations

import os
import platform
import sqlite3
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from importlib import metadata
from typing import Literal

from grid.config import Config

CheckStatus = Literal["PASS", "BLOCKED", "NOT_TESTED"]
SQLITE_VERSION_THRESHOLD = (3, 51, 3)
_FALSE_ENV = frozenset({"", "0", "false", "no", "off"})


@dataclass(frozen=True)
class Check:
    code: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    python_version: str
    ccxt_version: str | None
    sqlite_version: str
    checks: tuple[Check, ...]
    missing_prerequisites: tuple[str, ...]

    @property
    def ready(self) -> bool:
        """Live readiness cannot be granted by an I0 diagnostic."""
        return False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "iteration": "I0",
            "mode": "offline",
            "live": "DISABLED",
            "ready": self.ready,
            "runtime": {
                "python": self.python_version,
                "ccxt": self.ccxt_version,
                "sqlite": self.sqlite_version,
            },
            "checks": [asdict(check) for check in self.checks],
            "missing_prerequisites": list(self.missing_prerequisites),
        }

    def to_text(self) -> str:
        lines = [
            "GRID doctor — I0 offline diagnostics",
            "LIVE: DISABLED",
            "Live readiness: BLOCKED",
            f"Python: {self.python_version}",
            f"CCXT package metadata: {self.ccxt_version or 'NOT INSTALLED'}",
            f"SQLite linked runtime: {self.sqlite_version}",
        ]
        lines.extend(f"[{check.status}] {check.code}: {check.detail}" for check in self.checks)
        if self.missing_prerequisites:
            lines.append("Unconfigured prerequisites:")
            lines.extend(f"  - {name}" for name in self.missing_prerequisites)
        return "\n".join(lines)


def run_doctor(config: Config, *, environ: Mapping[str, str] | None = None) -> DoctorReport:
    """Inspect metadata and approved flags only; never load credential variables."""
    env = os.environ if environ is None else environ
    live_requested = config.live or any(
        env.get(name, "").strip().lower() not in _FALSE_ENV for name in ("LIVE", "GRID_LIVE")
    )
    try:
        ccxt_version = metadata.version("ccxt")
    except metadata.PackageNotFoundError:
        ccxt_version = None
    python_ok = sys.version_info[:2] in {(3, 12), (3, 13)}
    sqlite_threshold_met = sqlite3.sqlite_version_info >= SQLITE_VERSION_THRESHOLD
    missing = config.missing_prerequisites()
    checks = (
        Check("i0_boundary", "BLOCKED", "I0 has no trading runtime; live mode is unavailable."),
        Check(
            "live_request",
            "BLOCKED" if live_requested else "PASS",
            "A live flag was requested but cannot enable trading."
            if live_requested
            else "No live mode was requested.",
        ),
        Check(
            "python_runtime",
            "PASS" if python_ok else "BLOCKED",
            "Python is in the supported 3.12/3.13 range."
            if python_ok
            else "This interpreter is outside the supported 3.12/3.13 range.",
        ),
        Check(
            "ccxt_metadata",
            "PASS" if ccxt_version else "BLOCKED",
            "Installed distribution metadata found; exchange functionality is untested."
            if ccxt_version
            else "The pinned CCXT distribution is not installed.",
        ),
        Check(
            "ccxt_lock_verification",
            "NOT_TESTED",
            "Metadata presence does not verify the dependency lock; use uv sync --locked and CI.",
        ),
        Check(
            "sqlite_version_threshold",
            "PASS" if sqlite_threshold_met else "BLOCKED",
            "Linked SQLite meets the >=3.51.3 version threshold only."
            if sqlite_threshold_met
            else "Linked SQLite is below 3.51.3; fixed backports need separate verification.",
        ),
        Check(
            "sqlite_build_provenance",
            "NOT_TESTED",
            "Build approval and fixed-backport provenance are unverified; live remains blocked.",
        ),
        Check(
            "operator_configuration",
            "BLOCKED" if missing else "PASS",
            "Required configuration choices are missing."
            if missing
            else "Fields are populated; economic and venue validation are still required.",
        ),
        Check(
            "strategy_and_execution",
            "NOT_TESTED",
            "Strategy, ledger, recovery and limit-order executor are not implemented in I0.",
        ),
        Check(
            "venue_capabilities",
            "NOT_TESTED",
            "Seven target venues require version-specific spot account and order-event probes.",
        ),
        Check(
            "account_ownership",
            "NOT_TESTED",
            "Dedicated funds, trading-only permissions and account ownership remain unverified.",
        ),
        Check(
            "storage_and_recovery",
            "NOT_TESTED",
            "WAL/FULL, filesystem suitability, single writer and backup restore remain unverified.",
        ),
        Check(
            "fault_and_soak_tests",
            "NOT_TESTED",
            "Trading fault tests, hardware resource limits and paper/sandbox soak are outstanding.",
        ),
        Check(
            "operator_signoff",
            "BLOCKED",
            "I1 approval and separate live canary approval are required; flags are not approval.",
        ),
    )
    return DoctorReport(
        python_version=platform.python_version(),
        ccxt_version=ccxt_version,
        sqlite_version=sqlite3.sqlite_version,
        checks=checks,
        missing_prerequisites=missing,
    )
