"""Local backtest UI: immutable inputs, bounded ownership and observable failures."""

from __future__ import annotations

import asyncio
import copy
import os
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from pytest_socket import socket_allow_hosts

from scripts import backtest_web as ui
from scripts.module1_study import read_json
from scripts.module1_verify_sources import identity, sha256, utc_ms, write_json


@pytest.fixture
def local_sockets(socket_enabled: None) -> None:
    """Asyncio needs a socketpair; HTTP checks may connect only to loopback."""
    socket_allow_hosts(["127.0.0.1"], allow_unix_socket=True)


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[ui.Store, dict[str, Any]]]:
    config = read_json(ui.CONFIG)
    manifest = read_json(ui.CONFIG.parent / "selection_manifest.json")
    sources = tmp_path / "sources"
    sources.mkdir()
    config["original_sources_root"] = str(sources)
    manifest["source_root"] = str(sources)
    first = utc_ms("2025-01-01T00:00:00Z")
    for row in manifest["selected"]:
        path = sources / row["file"]
        # One deliberate missing minute between these valid observations.
        path.write_bytes(
            b"".join(struct.pack("<qffff", first + i * 60000, 10, 11, 9, 10) for i in (0, 1, 3, 4))
        )
        row.update(
            source_path=str(path),
            rows=4,
            bytes=96,
            sha256=sha256(path),
            first_utc=ui.stamp(first),
            last_utc=ui.stamp(first + 240000),
            file_identity_before_and_after=list(identity(path)),
        )
    config_path = tmp_path / "experiment_config.json"
    write_json(config_path, config)
    write_json(tmp_path / "selection_manifest.json", manifest)
    store = ui.Store(tmp_path / "runs", Path("/bin/false"), config_path, None)
    value: dict[str, Any] = dict(ui.DEFAULTS)
    value.update(
        markets=[ui.market_id(manifest["selected"][0])],
        start=ui.stamp(first),
        end=ui.stamp(first + 300000),
    )
    yield store, value
    store.lock.close()


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("capital", "NaN"),
        ("capital", 1000),
        ("capital", "1e100"),
        ("spacing_pct", "0"),
        ("levels", "2.5"),
        ("buy_fee_pct", "-1"),
        ("ohlc", "guaranteed"),
        ("markets", ["/etc/passwd"]),
        ("start", "2025-01-01T00:00:01Z"),
        ("start", "2025-01-01T00:00:00+03:00"),
        ("end", "2025-01-02T00:00:00Z"),
        ("name", "bad\nname"),
        ("reserve", "1000"),
    ],
)
def test_rejects_invalid_settings(
    setup: tuple[ui.Store, dict[str, Any]], field: str, bad: object
) -> None:
    store, value = setup
    value[field] = bad
    with pytest.raises(ValueError):
        ui.settings(value, store.manifest)


def test_exact_export_and_gap_count_preserve_originals(
    setup: tuple[ui.Store, dict[str, Any]],
) -> None:
    store, value = setup
    before = copy.deepcopy(store.manifest)
    value.update(spacing_pct="1.25", capital="1200", reserve="200", buy_fee_pct="0.15")
    config, manifest, job = ui.prepare(
        ui.settings(value, store.manifest), store.config, store.manifest
    )
    assert manifest["selected"][0]["aligned_rows"] == 4  # never fill the missing minute
    assert job["end_ms_exclusive"] - job["start_ms"] == 300000
    assert config["grid"]["spacing"] == "0.0125"
    assert config["fees"]["buy_rate"] == "0.0015"
    assert config["funding"]["maximum_committed_cost_per_allocation"] == "1000"
    assert store.manifest == before
    for source in store.manifest["selected"]:
        assert sha256(Path(source["source_path"])) == source["sha256"]


def test_empty_gap_window_and_changed_source_refused(
    setup: tuple[ui.Store, dict[str, Any]],
) -> None:
    store, value = setup
    source = store.manifest["selected"][0]
    start = utc_ms(value["start"])
    with pytest.raises(ValueError, match="No observations"):
        ui.window(source, start + 120000, start + 180000)
    changed = dict(source, file_identity_before_and_after=[0] * 5)
    with pytest.raises(ValueError, match="changed"):
        ui.window(changed, start, start + 60000)


def test_canonical_money_and_declared_window_survive_boundary_gaps(
    setup: tuple[ui.Store, dict[str, Any]],
) -> None:
    store, value = setup
    first = utc_ms(value["start"])
    value.update(capital="1000.0000", spacing_pct="1.00", start=ui.stamp(first + 120000))
    validated = ui.settings(value, store.manifest)
    assert validated["capital"] == "1000"  # matches the engine's canonical output
    assert validated["spacing_pct"] == "1"
    _, manifest, job = ui.prepare(validated, store.config, store.manifest)
    assert job["declared_start_ms"] == first + 120000
    assert job["start_ms"] == first + 180000
    assert manifest["selected"][0]["aligned_rows"] == 2


def test_corrupt_retained_job_does_not_hide_other_results(
    setup: tuple[ui.Store, dict[str, Any]],
) -> None:
    store, _ = setup
    broken = store.output / "run-broken"
    broken.mkdir()
    (broken / "job.json").write_text("{incomplete")
    store.lock.close()
    restored = ui.Store(
        store.output, store.binary, store.output.parent / "experiment_config.json", None
    )
    assert len(restored.notices) == 1
    assert "run-broken" in restored.notices[0]
    assert (broken / "job.json").read_text() == "{incomplete"
    restored.lock.close()


def test_output_guard_and_single_owner(setup: tuple[ui.Store, dict[str, Any]]) -> None:
    store, _ = setup
    config_path = store.output.parent / "experiment_config.json"
    with pytest.raises(ValueError, match="owner"):
        ui.Store(store.output, store.binary, config_path, None)
    with pytest.raises(ValueError, match="outside datasets"):
        ui.Store(Path(store.manifest["source_root"]) / "runs", store.binary, config_path, None)


@pytest.mark.usefixtures("local_sockets")
def test_failure_retained_and_restart_does_not_repeat(
    setup: tuple[ui.Store, dict[str, Any]],
) -> None:
    store, value = setup

    async def exercise() -> str:
        key = await store.launch(value)
        with pytest.raises(web.HTTPConflict):
            await store.launch(value)
        assert store.task is not None
        await store.task
        assert store.rows[key]["status"] == "FAIL"
        assert "Engine refused" in store.rows[key]["error"]
        assert (store.folders[key] / "stderr.txt").is_file()
        return key

    key = asyncio.run(exercise())
    store.lock.close()
    restored = ui.Store(
        store.output, store.binary, store.output.parent / "experiment_config.json", None
    )
    assert restored.rows[key]["status"] == "FAIL"
    assert restored.active is None
    restored.lock.close()


@pytest.mark.usefixtures("local_sockets")
def test_cancel_reaps_engine_and_retains_attempt(
    setup: tuple[ui.Store, dict[str, Any]], tmp_path: Path
) -> None:
    store, value = setup
    sleeper = tmp_path / "slow-engine"
    sleeper.write_text("#!/bin/sh\necho $$\nexec /bin/sleep 60\n")
    sleeper.chmod(0o700)
    store.binary = sleeper

    async def exercise() -> None:
        key = await store.launch(value)
        stdout = store.folders[key] / "stdout.json"
        for _ in range(500):
            if stdout.exists() and stdout.stat().st_size:
                break
            await asyncio.sleep(0.01)
        else:
            await store.stop()
            pytest.fail("Child did not start within five seconds")
        pid = int(stdout.read_text())
        await store.stop()
        assert store.rows[key]["status"] == "CANCELLED"
        assert store.active is None
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    asyncio.run(exercise())


@pytest.mark.usefixtures("local_sockets")
def test_http_rejects_foreign_requests_and_unsafe_downloads(
    setup: tuple[ui.Store, dict[str, Any]],
) -> None:
    store, value = setup

    async def exercise() -> None:
        async with TestClient(TestServer(ui.application(store))) as client:
            response = await client.get("/")
            assert response.status == 200
            assert "Backtest workspace" in await response.text()
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
            response = await client.post("/api/runs", json=value)
            assert response.status == 403
            response = await client.get("/api/catalog", headers={"Host": "evil.example"})
            assert response.status == 403
            response = await client.post(
                "/api/runs",
                json=value,
                headers={"X-Run-Token": store.token, "Origin": "https://evil.example"},
            )
            assert response.status == 403
            response = await client.get("/api/runs/unknown/files/secret")
            assert response.status == 404
            response = await client.post(
                "/api/runs", json=value, headers={"X-Run-Token": store.token}
            )
            assert response.status == 201
            assert store.task is not None
            await store.task
            response = await client.get("/api/runs")
            assert (await response.json())["runs"][0]["status"] == "FAIL"

    asyncio.run(exercise())


@pytest.mark.usefixtures("local_sockets")
def test_public_proxy_origin_preserves_host_and_csrf_checks(
    setup: tuple[ui.Store, dict[str, Any]],
) -> None:
    store, value = setup
    origin = "http://103.1.225.114"

    async def exercise() -> None:
        async with TestClient(TestServer(ui.application(store, public_origin=origin))) as client:
            for headers in (
                {"Origin": origin},  # the public origin is not a CSRF bypass
                {"Origin": origin + ".example", "X-Run-Token": store.token},
                {"Origin": origin, "X-Run-Token": store.token, "Host": "evil.example"},
            ):
                response = await client.post("/api/runs", json=value, headers=headers)
                assert response.status == 403
            response = await client.post(
                "/api/runs",
                json=value,
                headers={"Origin": origin, "X-Run-Token": store.token},
            )
            assert response.status == 201
            assert store.task is not None
            await store.task

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "file:///tmp/ui",
        "http://user:password@example.com",  # pragma: allowlist secret - invalid-origin fixture
        "http://example.com/path",
        "http://example.com?query=1",
        "http://example.com#fragment",
    ],
)
def test_public_origin_requires_an_exact_origin(
    setup: tuple[ui.Store, dict[str, Any]], origin: str
) -> None:
    with pytest.raises(ValueError, match="exact http"):
        ui.application(setup[0], public_origin=origin)
