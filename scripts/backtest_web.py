"""Local UI for the existing Module 1 engine; run from the repository with -m.

The HTTP process owns one worker. The separate, single-threaded worker reuses
the existing bounded child driver, including exact accounting validation.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import fcntl
import hashlib
import re
import secrets
import shutil
import signal
import struct
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from aiohttp import web

from scripts.module1_study import (
    ENTRIES,
    LADDERS,
    MAX_RESULT_BYTES,
    MODELS,
    export_tsv,
    financial_checks,
    read_json,
    validate,
    validate_result,
)
from scripts.module1_verify_sources import identity, run_child, sha256, utc_ms, write_json

ROOT = Path(__file__).resolve().parents[1]
ASSETS = Path(__file__).with_name("backtest_web_static")
CONFIG = ROOT / "research/runs/module1-20261110/experiment_config.json"
BINARY = Path("/opt/grid-builds/module1/release-public-guards-v1/grid_backtest")
STUDY = Path("/opt/grid-runs/module1-20261110/full-study-v4-notices")
OUTPUT = Path("/opt/grid-runs/backtest-ui")
DEFAULTS = {
    "name": "My backtest",
    "entry": ENTRIES[0],
    "ladder": LADDERS[0],
    "ohlc": MODELS[0],
    "reentry": "reentry_near_fresh_book",
    "capital": "1000",
    "reserve": "100",
    "spacing_pct": "1",
    "net_target": "0.01",
    "levels": "3",
    "max_lots": "100",
    "buy_fee_pct": "0.1",
    "sell_fee_pct": "0.1",
    "spread_pct": "0.1",
    "latency": "1",
}
BOUNDS = {
    "capital": ("10", "100000"),
    "reserve": ("0", "100000"),
    "spacing_pct": ("0.05", "20"),
    "net_target": ("0.000001", "100"),
    "levels": ("1", "16"),
    "max_lots": ("1", "1000"),
    "buy_fee_pct": ("0", "2"),
    "sell_fee_pct": ("0", "2"),
    "spread_pct": ("0", "5"),
    "latency": ("1", "60"),
}
ENUMS = {
    "entry": ENTRIES,
    "ladder": LADDERS,
    "ohlc": MODELS,
    "reentry": ("reentry_near_fresh_book", "reentry_one_step_below_sell"),
}


def stamp(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def market_id(source: dict[str, Any]) -> str:
    return f"{source['venue']}:{source['market']}"


def settings(payload: object, manifest: dict[str, Any]) -> dict[str, Any]:
    """Only declared fields, exact decimal text and minute-aligned UTC windows."""
    if not isinstance(payload, dict) or set(payload) != set(DEFAULTS) | {"markets", "start", "end"}:
        raise ValueError("Supply exactly the fields in the run form")
    value = dict(payload)
    name = value["name"]
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or not name.isprintable():
        raise ValueError("Name must contain 1–80 printable characters")
    value["name"] = name.strip()
    for key, choices in ENUMS.items():
        if value[key] not in choices:
            raise ValueError(f"Invalid {key}")
    for key, (low, high) in BOUNDS.items():
        raw = value[key]
        if not isinstance(raw, str) or not re.fullmatch(r"\d{1,6}(?:\.\d{1,6})?", raw):
            raise ValueError(f"{key}: use a decimal number with at most six decimal places")
        number = Decimal(raw)
        if not Decimal(low) <= number <= Decimal(high):
            raise ValueError(f"{key} must be between {low} and {high}")
        if key in ("levels", "max_lots", "latency") and number != int(number):
            raise ValueError(f"{key} must be an integer")
        value[key] = format(number.normalize(), "f")
    if Decimal(value["reserve"]) >= Decimal(value["capital"]):
        raise ValueError("Reserve must be smaller than capital per market")
    roster = value["markets"]
    available = {market_id(row): row for row in manifest["selected"]}
    if (
        not isinstance(roster, list)
        or not 1 <= len(roster) <= len(available)
        or any(not isinstance(key, str) or key not in available for key in roster)
        or len(set(roster)) != len(roster)
    ):
        raise ValueError("Choose one or more distinct markets from the supplied manifest")
    value["markets"] = sorted(roster)
    for key in ("start", "end"):
        if not isinstance(value[key], str) or len(value[key]) > 32:
            raise ValueError("A UTC date and time is required")
        moment = utc_ms(value[key])
        if moment % 60000:
            raise ValueError("Date boundaries must use whole minutes")
        value[key] = stamp(moment)
    start, end = utc_ms(value["start"]), utc_ms(value["end"])
    first = max(utc_ms(available[key]["first_utc"]) for key in roster)
    last = min(utc_ms(available[key]["last_utc"]) + 60000 for key in roster)
    if not first <= start < end <= last:
        raise ValueError("Dates must lie inside the selected markets' shared coverage")
    return value


def window(source: dict[str, Any], start: int, end: int) -> tuple[int, int, int]:
    """Count selected rows by read-only binary search; C++ rechecks full chronology/hash."""
    path = Path(source["source_path"])
    before = identity(path)
    if list(before) != source["file_identity_before_and_after"]:
        raise ValueError("Source changed since its validated manifest")
    with path.open("rb") as stream:

        def timestamp(index: int) -> int:
            stream.seek(index * 24)
            return int(struct.unpack("<q", stream.read(8))[0])

        def lower_bound(target: int) -> int:
            lo, hi = 0, int(source["rows"])
            while lo < hi:
                middle = (lo + hi) // 2
                if timestamp(middle) < target:
                    lo = middle + 1
                else:
                    hi = middle
            return lo

        left, right = lower_bound(start), lower_bound(end)
        if left == right:
            raise ValueError(f"No observations in this window: {market_id(source)}")
        result = right - left, timestamp(left), timestamp(right - 1) + 60000
    if identity(path) != before:
        raise ValueError("Source changed while counting the window")
    return result


def prepare(
    value: dict[str, Any], baseline: dict[str, Any], source_manifest: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    config, manifest = copy.deepcopy(baseline), copy.deepcopy(source_manifest)
    config.update(declaration_status="USER_EXPLORATORY_RUN", experiment_id=value["name"])
    selected = [s for s in manifest["selected"] if market_id(s) in value["markets"]]
    start, end = utc_ms(value["start"]), utc_ms(value["end"])
    spans = []
    for source in selected:
        count, first, last = window(source, start, end)
        for key in list(source):
            if key.startswith("aligned_"):
                del source[key]
        source["aligned_rows"] = count
        spans.append((first, last))
    manifest["selected"] = selected
    # Recompute only the exported coverage fields; do not retain old cohort statistics.
    manifest["coverage"] = {
        "common_start_utc": value["start"],
        "common_end_utc_exclusive": value["end"],
    }
    config["funding"].update(
        initial_quote_per_allocation=value["capital"],
        initial_total_quote=str(Decimal(value["capital"]) * len(selected)),
        quote_reserve_per_allocation=value["reserve"],
        maximum_committed_cost_per_allocation=str(
            Decimal(value["capital"]) - Decimal(value["reserve"])
        ),
    )
    config["grid"].update(
        spacing=str(Decimal(value["spacing_pct"]) / 100),
        minimum_net_target_quote=value["net_target"],
        multi_pending_buy_levels=int(Decimal(value["levels"])),
        maximum_accumulated_lots=int(Decimal(value["max_lots"])),
        maximum_open_orders=max(8, int(Decimal(value["levels"])) + 2),
        reentry_policy=value["reentry"],
    )
    for side in ("buy", "sell"):
        config["fees"][f"{side}_rate"] = str(Decimal(value[f"{side}_fee_pct"]) / 100)
    config["execution"].update(
        synthetic_spread=str(Decimal(value["spread_pct"]) / 100),
        order_eligibility_delay_bars=int(Decimal(value["latency"])),
        cancel_finality_delay_bars=int(Decimal(value["latency"])),
    )
    case = {key: value[key] for key in ("entry", "ladder", "ohlc")}
    case["id"] = "custom"
    config["primary_cases"] = [case]
    job = {
        "case": case,
        "selected": selected,
        "full_history": False,
        "initial_quote_per_allocation": value["capital"],
        "start_ms": min(a for a, _ in spans),
        "end_ms_exclusive": max(b for _, b in spans),
        "declared_start_ms": start,
        "declared_end_ms_exclusive": end,
    }
    return config, manifest, job


def review(folder: Path, job: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    if "settings" in job:
        for filename, key in (
            ("experiment_config.json", "config_sha256"),
            ("selection_manifest.json", "manifest_sha256"),
            ("grid_backtest", "executable_sha256"),
        ):
            if sha256(folder / filename) != job[key]:
                raise ValueError(f"Retained {filename} hash changed")
    for filename, key in (("input.tsv", "input_sha256"), ("stdout.json", "result_file_sha256")):
        if sha256(folder / filename) != receipt[key]:
            raise ValueError(f"Retained {filename} hash changed")
    result = read_json(folder / "stdout.json", max_bytes=MAX_RESULT_BYTES)
    errors = validate_result(result, job) + financial_checks(result)
    for key in ("declared_start_ms", "declared_end_ms_exclusive"):
        if key in job and result.get(key) != job[key]:
            errors.append(key)
    if errors:
        raise ValueError("Result validation failed: " + ", ".join(errors[:10]))
    return result


def worker(folder: Path, timeout: float) -> None:
    def stop(_signum: int, _frame: object) -> None:
        raise InterruptedError("Run cancelled; retained artifacts")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    receipt: dict[str, Any] = {"status": "FAIL", "validation_failures": []}
    try:
        job = read_json(folder / "job.json")
        before = {s["source_path"]: identity(Path(s["source_path"])) for s in job["selected"]}
        for filename, key in (
            ("grid_backtest", "executable_sha256"),
            ("input.tsv", "input_sha256"),
            ("experiment_config.json", "config_sha256"),
            ("selection_manifest.json", "manifest_sha256"),
        ):
            if sha256(folder / filename) != job[key]:
                raise ValueError(f"Prepared artifact changed: {filename}")
        receipt.update(
            run_child(
                [str(folder / "grid_backtest"), "replay", str(folder / "input.tsv")],
                folder,
                timeout,
                log_bytes=MAX_RESULT_BYTES,
            )
        )
        if receipt["exit_code"] != 0:
            raise ValueError(
                "Engine timed out" if receipt["timed_out"] else "Engine refused this run"
            )
        receipt.update(
            input_sha256=job["input_sha256"],
            result_file_sha256=sha256(folder / "stdout.json"),
        )
        review(folder, job, receipt)
        if any(identity(Path(path)) != old for path, old in before.items()):
            raise ValueError("Source identity changed during replay")
        if sha256(folder / "grid_backtest") != job["executable_sha256"]:
            raise ValueError("Engine identity changed during replay")
        receipt.update(status="PASS", source_identities_unchanged=True)
    except InterruptedError as exc:
        receipt.update(status="CANCELLED", error=str(exc))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        receipt.update(error=str(exc))
    write_json(folder / "receipt.json", receipt)


def saved_settings(config: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = dict(DEFAULTS)
    value.update(
        name=job["id"],
        markets=sorted(market_id(s) for s in job["selected"]),
        start=stamp(job["start_ms"]),
        end=stamp(job["end_ms_exclusive"]),
        **{key: job["case"][key] for key in ("entry", "ladder", "ohlc")},
        capital=config["funding"]["initial_quote_per_allocation"],
        reserve=config["funding"]["quote_reserve_per_allocation"],
        spacing_pct=str(Decimal(config["grid"]["spacing"]) * 100),
        net_target=config["grid"]["minimum_net_target_quote"],
        levels=str(config["grid"]["multi_pending_buy_levels"]),
        max_lots=str(config["grid"]["maximum_accumulated_lots"]),
        reentry=config["grid"]["reentry_policy"],
        buy_fee_pct=str(Decimal(config["fees"]["buy_rate"]) * 100),
        sell_fee_pct=str(Decimal(config["fees"]["sell_rate"]) * 100),
        spread_pct=str(Decimal(config["execution"]["synthetic_spread"]) * 100),
        latency=str(config["execution"]["order_eligibility_delay_bars"]),
    )
    for key in BOUNDS:
        value[key] = format(Decimal(value[key]).normalize(), "f")
    return value


class Store:
    def __init__(self, output: Path, binary: Path, config_path: Path, study: Path | None) -> None:
        self.config = read_json(config_path)
        self.manifest = read_json(config_path.parent / self.config["selection_manifest"])
        validate(self.config, self.manifest)
        self.binary = binary.resolve(strict=True)
        identity(self.binary)
        self.output = output.resolve()
        if (
            self.output.is_relative_to(Path("/opt/datasets"))
            or any(
                self.output.is_relative_to(Path(s["source_path"]).parent)
                for s in self.manifest["selected"]
            )
            or any((p / ".git").exists() for p in (self.output, *self.output.parents))
        ):
            raise ValueError("Run output must be outside datasets and Git repositories")
        self.output.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (self.output / ".owner.lock").open("a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise ValueError("This output directory already has a UI or worker owner") from None
        self.rows: dict[str, dict[str, Any]] = {}
        self.folders: dict[str, Path] = {}
        self.results: dict[str, dict[str, Any]] = {}
        self.notices: list[str] = []
        self.active: str | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task[None] | None = None
        self.cancel_requested = False
        self.token = secrets.token_urlsafe(32)
        for folder in sorted(self.output.glob("run-*")):
            if not folder.is_symlink() and (folder / "job.json").is_file():
                try:
                    job = read_json(folder / "job.json")
                    self.load(folder.name, folder, job, job["settings"], "Local")
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    self.notices.append(f"Retained unreadable run {folder.name}: {exc}")
        if study is not None and study.is_dir():
            try:
                plan = read_json(study / "plan.json", max_bytes=MAX_RESULT_BYTES)
                for filename, key in (
                    ("experiment_config.json", "config_sha256"),
                    ("selection_manifest.json", "manifest_sha256"),
                    ("grid_backtest", "executable_sha256"),
                ):
                    if sha256(study / filename) != plan[key]:
                        raise ValueError(f"Study artifact changed: {filename}")
                config = read_json(study / "experiment_config.json")
                for job in plan["jobs"]:
                    job.update(
                        {
                            key: plan[key]
                            for key in ("config_sha256", "manifest_sha256", "executable_sha256")
                        }
                    )
                    key = "study-" + job["id"]
                    self.load(key, study / job["id"], job, saved_settings(config, job), "Study")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.notices.append(f"Existing study could not be fully loaded: {exc}")

    def load(
        self, key: str, folder: Path, job: dict[str, Any], value: dict[str, Any], origin: str
    ) -> None:
        row: dict[str, Any] = {
            "id": key,
            "name": value["name"],
            "settings": value,
            "origin": origin,
            "status": "INTERRUPTED",
            "error": "No final receipt; never automatically resumed",
        }
        self.rows[key], self.folders[key] = row, folder
        try:
            if (folder / "receipt.json").exists():
                receipt = read_json(folder / "receipt.json")
                row.update(
                    status=receipt["status"],
                    error=receipt.get("error", ""),
                    seconds=receipt.get("wall_seconds_including_process_start_and_poll"),
                )
                if receipt["status"] == "PASS":
                    if origin == "Study" and any(
                        receipt[key] != job[key]
                        for key in ("config_sha256", "manifest_sha256", "executable_sha256")
                    ):
                        raise ValueError("Study receipt does not match its frozen plan")
                    result = review(folder, job, receipt)
                    # Keep only validation metadata resident; load full curves on demand.
                    self.results[key] = {"job": job, "receipt": receipt}
                    row.update(
                        portfolio=result["portfolio"],
                        processed_rows=result["processed_rows"],
                        ending_nav_timestamp_ms=result["ending_nav_timestamp_ms"],
                        executable_sha256=job.get(
                            "executable_sha256", receipt.get("executable_sha256", "unknown")
                        ),
                    )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            row.update(status="INVALID", error=str(exc))

    async def launch(self, payload: object) -> str:
        if self.active is not None:
            raise web.HTTPConflict(text="A backtest is already running")
        value = settings(payload, self.manifest)
        config, manifest, job = prepare(value, self.config, self.manifest)
        key = "run-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)
        folder = self.output / key
        folder.mkdir(mode=0o700)
        config.update(external_outputs=str(folder), declared_utc=datetime.now(UTC).isoformat())
        write_json(folder / "experiment_config.json", config)
        write_json(folder / "selection_manifest.json", manifest)
        shutil.copy2(self.binary, folder / "grid_backtest")
        data = export_tsv(config, manifest, job["case"], job["selected"], full_history=False)
        (folder / "input.tsv").write_bytes(data)
        job.update(
            id=key,
            settings=value,
            input_sha256=hashlib.sha256(data).hexdigest(),
            config_sha256=sha256(folder / "experiment_config.json"),
            manifest_sha256=sha256(folder / "selection_manifest.json"),
            executable_sha256=sha256(folder / "grid_backtest"),
            ui_sha256=sha256(Path(__file__)),
        )
        write_json(folder / "job.json", job)
        self.active = key
        self.cancel_requested = False
        self.rows[key] = {
            "id": key,
            "name": value["name"],
            "settings": value,
            "origin": "Local",
            "status": "RUNNING",
            "started": stamp(int(datetime.now(UTC).timestamp()) * 1000),
        }
        self.folders[key] = folder
        self.task = asyncio.create_task(self.execute(key, folder, job))
        return key

    async def execute(self, key: str, folder: Path, job: dict[str, Any]) -> None:
        try:
            with (folder / "worker.log").open("xb") as log:
                self.process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "scripts.backtest_web",
                    "--worker",
                    str(folder),
                    cwd=ROOT,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    pass_fds=(self.lock.fileno(),),
                )
                await self.process.wait()
        except OSError as exc:
            if not (folder / "receipt.json").exists():
                write_json(folder / "receipt.json", {"status": "FAIL", "error": str(exc)})
        finally:
            if self.cancel_requested and not (folder / "receipt.json").exists():
                write_json(
                    folder / "receipt.json",
                    {"status": "CANCELLED", "error": "Cancelled during worker startup"},
                )
            self.load(key, folder, job, job["settings"], "Local")
            self.active, self.process = None, None

    async def stop(self) -> None:
        self.cancel_requested = self.active is not None
        # Launch may be between create_task and subprocess completion. Let it settle first.
        while (
            self.active is not None and self.process is None and self.task and not self.task.done()
        ):
            await asyncio.sleep(0.01)
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
        if self.task is not None:
            await self.task


def application(store: Store) -> web.Application:
    @web.middleware
    async def boundary(
        request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
    ) -> web.StreamResponse:
        host = request.host.split(":", 1)[0]
        if host not in ("localhost", "127.0.0.1"):
            raise web.HTTPForbidden(text="Use the localhost URL or an SSH port forward")
        if request.method != "GET" and not secrets.compare_digest(
            request.headers.get("X-Run-Token", ""), store.token
        ):
            raise web.HTTPForbidden(text="Reload this local page before submitting a run")
        origin = request.headers.get("Origin")
        if origin and origin != f"http://{request.host}":
            raise web.HTTPForbidden(text="Same-origin requests only")
        try:
            response = await handler(request)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            response = web.json_response({"error": str(exc)}, status=400)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
                "style-src 'self'; connect-src 'self'; img-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'",
            }
        )
        return response

    async def catalog(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "defaults": DEFAULTS,
                "sources": store.manifest["selected"],
                "coverage": store.manifest["coverage"],
                "token": store.token,
                "notices": store.notices,
                "output": str(store.output),
            }
        )

    async def runs(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "runs": sorted(
                    store.rows.values(),
                    key=lambda row: (row["origin"] == "Local", row["id"]),
                    reverse=True,
                ),
                "active": store.active,
            }
        )

    async def launch(request: web.Request) -> web.Response:
        return web.json_response({"id": await store.launch(await request.json())}, status=201)

    async def cancel(_request: web.Request) -> web.Response:
        await store.stop()
        return web.json_response({"status": "stopped"})

    async def result(request: web.Request) -> web.Response:
        key = request.match_info["key"]
        if key not in store.results:
            raise web.HTTPNotFound(text="No validated result for this run")
        metadata = store.results[key]
        return web.json_response(review(store.folders[key], metadata["job"], metadata["receipt"]))

    async def artifact(request: web.Request) -> web.FileResponse:
        key, name = request.match_info["key"], request.match_info["name"]
        if key not in store.folders or name not in {
            "stdout.json",
            "receipt.json",
            "input.tsv",
            "stderr.txt",
            "job.json",
            "experiment_config.json",
            "selection_manifest.json",
        }:
            raise web.HTTPNotFound()
        path = store.folders[key] / name
        if not path.is_file() or path.is_symlink():
            raise web.HTTPNotFound()
        return web.FileResponse(
            path, headers={"Content-Disposition": f'attachment; filename="{name}"'}
        )

    async def asset(request: web.Request) -> web.FileResponse:
        name = request.match_info.get("name", "index.html")
        if name not in {"index.html", "app.js", "style.css"}:
            raise web.HTTPNotFound()
        return web.FileResponse(ASSETS / name)

    async def cleanup(_app: web.Application) -> None:
        await store.stop()
        store.lock.close()

    app = web.Application(middlewares=[boundary], client_max_size=16384)
    app.add_routes(
        [
            web.get("/", asset),
            web.get("/assets/{name}", asset),
            web.get("/api/catalog", catalog),
            web.get("/api/runs", runs),
            web.post("/api/runs", launch),
            web.post("/api/cancel", cancel),
            web.get("/api/runs/{key}/result", result),
            web.get("/api/runs/{key}/files/{name}", artifact),
        ]
    )
    app.on_cleanup.append(cleanup)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--binary", type=Path, default=BINARY)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--study", type=Path, default=STUDY)
    parser.add_argument("--no-study", action="store_true")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, 21600)
        return
    if not 1024 <= args.port <= 65535:
        parser.error("port must be 1024–65535")
    try:
        store = Store(args.output, args.binary, args.config, None if args.no_study else args.study)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"UI refused: {exc}\n")
    print(f"Backtests: http://127.0.0.1:{args.port} | results: {store.output}", flush=True)
    web.run_app(application(store), host="127.0.0.1", port=args.port, access_log=None)


if __name__ == "__main__":
    main()
