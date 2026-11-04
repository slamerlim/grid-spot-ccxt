"""Supplementary I0 boundaries; these do not certify future trading invariants."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_imported_specifications_match_recorded_source_hashes() -> None:
    manifest = json.loads((ROOT / "docs/SPECIFICATION_SOURCES.json").read_text())
    assert manifest["schema_version"] == 1
    documents = manifest["documents"]
    assert len(documents) == 4
    assert len({entry["path"] for entry in documents}) == 4
    for entry in documents:
        relative_path = Path(entry["path"])
        assert not relative_path.is_absolute()
        assert len(relative_path.parts) == 1
        source = ROOT / "docs" / relative_path
        assert hashlib.sha256(source.read_bytes()).hexdigest() == entry["sha256"]


def test_application_limits_network_imports_and_has_no_exchange_writes() -> None:
    """Guard this increment's scope; static inspection is not full effect analysis."""
    forbidden_modules = {
        "aiohttp",
        "ccxt",
        "httpx",
        "requests",
        "socket",
        "subprocess",
        "urllib.request",
        "websockets",
    }
    forbidden_calls = {
        "__import__",
        "cancel_all_orders",
        "cancel_order",
        "cancel_orders",
        "connect",
        "create_limit_buy_order",
        "create_limit_sell_order",
        "create_market_buy_order",
        "create_market_sell_order",
        "create_order",
        "create_orders",
        "eval",
        "exec",
        "popen",
        "system",
    }
    files = sorted((ROOT / "src/grid").rglob("*.py"))
    assert files
    for source in files:
        tree = ast.parse(source.read_text(), filename=str(source))
        for node in ast.walk(tree):
            imports: list[str] = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
            for name in imports:
                if source == ROOT / "src/grid/research/git_identity.py" and name == "subprocess":
                    continue  # Bounded local plumbing; isolated environment and denied transports.
                if source == ROOT / "src/grid/operations/local.py" and name == "socket":
                    continue  # Same-user AF_UNIX only; runtime IPC tests reject IP sockets.
                if source == ROOT / "src/grid/exchange/ccxt_transport.py" and name in {
                    "aiohttp",
                    "ccxt.pro",
                }:
                    continue
                assert not any(
                    name == forbidden or name.startswith(f"{forbidden}.")
                    for forbidden in forbidden_modules
                ), (source.name, name)
            if isinstance(node, ast.Call):
                called = node.func
                name = (
                    called.id
                    if isinstance(called, ast.Name)
                    else called.attr
                    if isinstance(called, ast.Attribute)
                    else ""
                )
                approved_sqlite = (
                    source == ROOT / "src/grid/persistence/storage.py"
                    and isinstance(called, ast.Attribute)
                    and isinstance(called.value, ast.Name)
                    and called.value.id == "sqlite3"
                    and called.attr == "connect"
                )
                assert approved_sqlite or name not in forbidden_calls, (
                    source.name,
                    node.lineno,
                    name,
                )


def test_gitignore_excludes_private_state_and_keeps_reviewable_inputs() -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is unavailable; ignore behavior cannot be checked.")
    repository = subprocess.run(
        [git, "-C", str(ROOT), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if repository.returncode != 0 or Path(repository.stdout.strip()) != ROOT:
        pytest.skip("Source archive is not a repository; ignore behavior cannot be checked.")
    private_paths = {
        ".env",
        ".env.production",
        "secrets/account.txt",
        "credentials/account.json",
        "account_api_key.txt",
        "private.pem",
        "private.key",
        "config.yaml",
        "config.local.yaml",
        "ledger.db",
        "ledger.db-wal",
        "ledger.db-shm",
        "operator.sock",
        "ledger.sqlite3",
        "ledger.sqlite3-wal",
        "ledger.sqlite3-shm",
        "data/candles.bin",
        "datasets/candles.bin",
        "reports/results.json",
        "benchmarks/results.json",
        "candles.parquet",
        "candles.arrow",
        "candles.ipc",
    }
    public_paths = {
        "config.example.yaml",
        "docs/PRD_GRID_SPOT_CCXT_FINAL_v2.md",
        "docs/SPECIFICATION_SOURCES.json",
        "pyproject.toml",
        "uv.lock",
        "tests/test_i0_boundaries.py",
    }
    result = subprocess.run(
        [git, "-C", str(ROOT), "check-ignore", "--no-index", "--stdin"],
        input="\n".join(sorted(private_paths | public_paths)) + "\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.splitlines()) == private_paths


def test_local_control_constructs_only_unix_stream_sockets() -> None:
    tree = ast.parse((ROOT / "src/grid/operations/local.py").read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "socket"
    ]
    assert len(calls) == 2  # Client and listener; no extra socket factory.
    for node in calls:
        assert isinstance(node.func, ast.Attribute)
        assert node.func.attr == "socket"
        assert not node.keywords and len(node.args) == 2
        assert [ast.unparse(arg) for arg in node.args] == ["socket.AF_UNIX", "socket.SOCK_STREAM"]
