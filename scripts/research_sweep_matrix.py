"""Bounded aggregate-TP matrix jobs and exact, indexed binary results.

The caller owns the existing research heavy-work lock. Only newly generated,
successfully committed batch scratch is removed; source histories and prior
jobs are never edited. Financial values stay exact strings inside MessagePack.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import struct
import time
import zlib
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import msgpack  # type: ignore[import-untyped]

from scripts.research_sweep_reference import (
    EXPANDED_CONFIGURATION_COUNT,
    VENUES,
    expanded_configuration,
)

MATRIX_KIND = "aggregate_tp_v1"
DEPTH_PLAN_SCHEMA = "grid-research-configuration-plan-v2"
GRID_DEPTHS = (30, 35, 40, 45, 50, 55, 60)
# Block zero preserves every original canonical ordinal and configuration ID.
_DEPTH_BLOCKS = (60, 30, 35, 40, 45, 50, 55)
DEPTH_CONFIGURATION_COUNT = 59_367
LEGACY_CATALOG_SCHEMA = "grid-research-matrix-catalog-v1"
CATALOG_SCHEMA = "grid-research-matrix-index-v2"
RESULT_MAGIC = b"GRIDRB2\n"
RESULT_HEADER = struct.Struct("<Q32s")
MAX_PAYLOAD_BYTES = 4 * 1024**2
MAX_BATCH_SIZE = 8192
DEFAULT_BATCH_SIZE = 1024
MATRIX_ACCEPTANCE_POLICY = {
    "id": "selected-matrix-600s-binary-v3",
    "market_coverage": "all_admitted_pair_ids_venues_and_rules",
    "load_prices": "same_deterministic_synthetic_history_for_every_configuration",
    "fixture_bars": 1024,
    "fixture_size_kind": "exact_records_per_source_with_gaps",
    "maximum_fixture_bars": 32000,
    "short_load_action": "double_bars_and_repeat_complete_selected_matrix",
    "minimum_replay_seconds": 600,
    "minimum_checkpoints": 2,
    "recovery_required": True,
    "execution": "bounded_configuration_batches",
}
STATISTICS_POLICY = {
    "mode": "monthly_v1",
    "calendar": "UTC",
    "calendar_scope": "venue_own_observed_span_v1",
    "profit_factor": "net_completed_pair_positions_in_sale_month_including_both_fees",
    "risk_observations": "complete_calendar_month_changes_in_net_marked_pnl",
    "monthly_risk": "cumulative_from_history_start",
    "risk_free_and_target": "0",
    "annualization_periods": 12,
    "sharpe_deviation": "sample",
    "sortino_deviation": "downside_rms_over_all_observations",
    "partial_months": "financial_totals_included_risk_observations_excluded",
    "risk_storage": "exact_integer_moments_and_squared_ratios_with_24_decimal_root_display",
}


def _statistics_policy(selected_sources: bool) -> dict[str, Any]:
    policy = dict(STATISTICS_POLICY)
    if not selected_sources:
        policy["calendar_scope"] = "shared_portfolio_span_v1"
    return policy


PERIOD_FIELDS = (
    "start_ms",
    "end_ms",
    "complete_month",
    "spent_open",
    "spent",
    "peak_spent",
    "peak_spent_ms",
    "total_pnl",
    "realized_gross",
    "fees",
    "buy_count",
    "sell_count",
    "net_profit_closed",
    "net_loss_closed",
    "winning_closures",
    "losing_closures",
    "breakeven_closures",
    "profit_factor",
    "profit_factor_status",
    "sample_months",
    "monthly_sum_units",
    "monthly_square_sum_units",
    "monthly_downside_square_sum_units",
    "sharpe",
    "sharpe_squared",
    "sharpe_status",
    "sortino",
    "sortino_squared",
    "sortino_status",
)
PERIOD_RATIO_FIELDS = frozenset(
    (
        "spent_open",
        "spent",
        "peak_spent",
        "total_pnl",
        "realized_gross",
        "fees",
        "net_profit_closed",
        "net_loss_closed",
        "profit_factor",
        "sharpe_squared",
        "sortino_squared",
    )
)
PERIOD_INTEGER_FIELDS = frozenset(
    (
        "monthly_sum_units",
        "monthly_square_sum_units",
        "monthly_downside_square_sum_units",
    )
)
RESULT_FIELDS = (
    "id",
    "step_pct",
    "tp_pct",
    "exchange_tp_pct",
    "global_tp_pct",
    "total",
    "venues",
    "trade_hash",
    "pair_fingerprint",
    "aggregate_cycles",
)
METRIC_FIELDS = (
    "buy_count",
    "fees",
    "longest_hold_ms",
    "max_drawdown",
    "max_drawdown_ms",
    "open_value",
    "peak_spent",
    "peak_spent_ms",
    "realized_gross",
    "return_pct",
    "sell_count",
    "spent",
    "total_pnl",
    "unrealized_gross",
)
MATRIX_DESCRIPTION = {
    "kind": MATRIX_KIND,
    "configuration_count": EXPANDED_CONFIGURATION_COUNT,
    "step_half_percent": [1, 20],
    "pair_tp_half_percent": [4, 30],
    "exchange_tp_half_percent": [None, 4, 30],
    "global_tp_half_percent": [None, 4, 30],
    "order": "step,pair_tp,exchange_tp,global_tp; disabled precedes enabled",
}


def _runner() -> Any:
    # Existing worker imports this module lazily; avoid a second job/lock API.
    from scripts import research_sweep

    return research_sweep


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _pack_metrics(value: dict[str, Any]) -> list[Any]:
    return [
        *[value[name] for name in METRIC_FIELDS],
        {name: item for name, item in value.items() if name not in METRIC_FIELDS},
    ]


def _unpack_metrics(value: list[Any]) -> dict[str, Any]:
    if not isinstance(value, list) or len(value) != len(METRIC_FIELDS) + 1:
        raise ValueError("invalid binary metrics")
    result = dict(zip(METRIC_FIELDS, value[:-1], strict=True))
    if not isinstance(value[-1], dict) or set(result) & value[-1].keys():
        raise ValueError("invalid binary metric extensions")
    result.update(value[-1])
    return result


@lru_cache(maxsize=16384)
def _integer_bytes(value: str) -> bytes:
    # Immutable number-text conversion only. No price/position facts or derived
    # trading permissions are cached; the full literal is the key.
    number = int(value)
    return number.to_bytes((number.bit_length() + 8) // 8, "little", signed=True)


def _pack_period(row: dict[str, Any]) -> list[Any]:
    result = []
    for name in PERIOD_FIELDS:
        value = row.get(name)
        if value is not None and name in PERIOD_RATIO_FIELDS:
            numerator, denominator = value.split("/")
            value = [_integer_bytes(numerator), _integer_bytes(denominator)]
        elif value is not None and name in PERIOD_INTEGER_FIELDS:
            value = _integer_bytes(value)
        result.append(value)
    if set(row) - set(PERIOD_FIELDS):
        raise ValueError("unknown monthly statistics field")
    return result


def _unpack_period(values: list[Any], *, overall: bool = False) -> dict[str, Any]:
    if not isinstance(values, list) or len(values) != len(PERIOD_FIELDS):
        raise ValueError("invalid monthly statistics layout")
    result = dict(zip(PERIOD_FIELDS, values, strict=True))
    for name, value in result.items():
        if value is not None and name in PERIOD_RATIO_FIELDS:
            if (
                not isinstance(value, list)
                or len(value) != 2
                or not all(isinstance(v, bytes) for v in value)
            ):
                raise ValueError("invalid binary statistics ratio")
            numerator, denominator = (int.from_bytes(v, "little", signed=True) for v in value)
            if denominator <= 0:
                raise ValueError("invalid binary statistics denominator")
            result[name] = f"{numerator}/{denominator}"
        elif value is not None and name in PERIOD_INTEGER_FIELDS:
            if not isinstance(value, bytes):
                raise ValueError("invalid binary statistics integer")
            result[name] = str(int.from_bytes(value, "little", signed=True))
    if overall:
        result.pop("complete_month")
    return result


def _pack_performance(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("schema") != "grid-monthly-performance-v1":
        raise ValueError("invalid monthly statistics schema")

    def scope(row: dict[str, Any]) -> list[Any]:
        return [
            row.get("venue"),
            _integer_bytes(row["money_scale_denominator"]),
            _pack_period(row["overall"]),
            [_pack_period(item) for item in row["monthly"]],
        ]

    return {"binary_monthly_v1": [scope(value["total"]), [scope(row) for row in value["venues"]]]}


def _unpack_performance(value: dict[str, Any]) -> dict[str, Any]:
    def scope(row: list[Any]) -> dict[str, Any]:
        if not isinstance(row, list) or len(row) != 4 or not isinstance(row[1], bytes):
            raise ValueError("invalid binary monthly scope")
        denominator = int.from_bytes(row[1], "little", signed=True)
        if denominator <= 0 or not isinstance(row[3], list) or len(row[3]) > 2401:
            raise ValueError("invalid binary monthly scope bound")
        result = {
            "money_scale_denominator": str(denominator),
            "overall": _unpack_period(row[2], overall=True),
            "monthly": [_unpack_period(item) for item in row[3]],
        }
        if row[0] is not None:
            result["venue"] = row[0]
        return result

    items = value["binary_monthly_v1"]
    if (
        not isinstance(items, list)
        or len(items) != 2
        or not isinstance(items[1], list)
        or len(items[1]) > 7
    ):
        raise ValueError("invalid binary monthly scopes")
    return {
        "schema": "grid-monthly-performance-v1",
        "total": scope(items[0]),
        "venues": [scope(row) for row in items[1]],
    }


def encode_result(result: dict[str, Any]) -> tuple[bytes, bytes]:
    """Fixed field positions remove repeated field names without numeric loss."""
    values = [result.get(name) for name in RESULT_FIELDS]
    values[5] = _pack_metrics(result["total"])
    values[6] = [_pack_metrics(row) for row in result["venues"]]
    if not isinstance(result.get("trade_hash"), str) or len(result["trade_hash"]) != 64:
        raise ValueError("invalid trade hash")
    values[7] = bytes.fromhex(result["trade_hash"])
    extension = {key: value for key, value in result.items() if key not in RESULT_FIELDS}
    if "performance" in extension:
        extension["performance"] = _pack_performance(extension["performance"])
    values.append(extension)
    raw = msgpack.packb(values, use_bin_type=True)
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ValueError("configuration result exceeds binary bound")
    return zlib.compress(raw, level=3), hashlib.sha256(raw).digest()


def _verified_payload_bytes(payload: bytes, digest: bytes) -> bytes:
    decoder = zlib.decompressobj()
    raw = decoder.decompress(payload, MAX_PAYLOAD_BYTES + 1)
    if (
        len(raw) > MAX_PAYLOAD_BYTES
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
    ):
        raise ValueError("invalid or oversized compressed result")
    if hashlib.sha256(raw).digest() != digest:
        raise ValueError("binary result checksum mismatch")
    return raw


def _verify_stored_payload(payload: bytes, digest: bytes, config_id: str) -> None:
    """Check the complete stored bytes/structure without expanding monthly rows."""
    raw = _verified_payload_bytes(payload, digest)
    unpacker = msgpack.Unpacker(raw=False, strict_map_key=True, max_buffer_size=MAX_PAYLOAD_BYTES)
    try:
        unpacker.feed(raw)
        if unpacker.read_array_header() != len(RESULT_FIELDS) + 1 or unpacker.unpack() != config_id:
            raise ValueError("binary result configuration identity mismatch")
        for _ in RESULT_FIELDS:
            unpacker.skip()
    except msgpack.UnpackException as error:
        raise ValueError("invalid binary result structure") from error
    if unpacker.tell() != len(raw):
        raise ValueError("binary result has trailing data")


def _decode_result(payload: bytes, digest: bytes) -> dict[str, Any]:
    raw = _verified_payload_bytes(payload, digest)
    values = msgpack.unpackb(raw, raw=False, strict_map_key=True)
    if not isinstance(values, list) or len(values) != len(RESULT_FIELDS) + 1:
        raise ValueError("invalid binary result layout")
    result = dict(zip(RESULT_FIELDS, values[:-1], strict=True))
    result["total"] = _unpack_metrics(result["total"])
    result["venues"] = [_unpack_metrics(row) for row in result["venues"]]
    if not isinstance(result["trade_hash"], bytes) or len(result["trade_hash"]) != 32:
        raise ValueError("invalid binary trade hash")
    result["trade_hash"] = result["trade_hash"].hex()
    if not isinstance(values[-1], dict) or set(result) & values[-1].keys():
        raise ValueError("invalid binary result extensions")
    result.update(values[-1])
    if "performance" in result:
        result["performance"] = _unpack_performance(result["performance"])
    return result


def decode_result(payload: bytes, digest: bytes) -> dict[str, Any]:
    try:
        return _decode_result(payload, digest)
    except (zlib.error, msgpack.UnpackException, TypeError, KeyError) as error:
        raise ValueError("invalid binary result payload") from error


def _readonly(path: Path) -> sqlite3.Connection:
    if path.is_symlink():
        raise ValueError("binary catalog symlinks are not permitted")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _indices(identity: dict[str, Any]) -> list[int] | range:
    return cast(
        list[int] | range,
        identity.get("matrix", {}).get("canonical_ordinals", range(EXPANDED_CONFIGURATION_COUNT)),
    )


def _configuration_count(study: dict[str, Any]) -> int:
    return len(_indices(study["identity"]))


def _depth_configuration(canonical: int) -> dict[str, Any]:
    block, base = divmod(canonical, EXPANDED_CONFIGURATION_COUNT)
    if canonical < 0 or block >= len(_DEPTH_BLOCKS):
        raise ValueError("configuration ordinal outside depth matrix")
    depth = _DEPTH_BLOCKS[block]
    row = dict(expanded_configuration(base), grid_depth_pct=str(depth))
    if depth != 60:
        row["id"] = str(row["id"]) + f"-d{depth}"
    return row


def configuration_at(identity: dict[str, Any], ordinal: int) -> dict[str, Any]:
    """Reconstruct one canonical input, retaining legacy IDs and depth defaults."""
    indices = _indices(identity)
    if type(ordinal) is not int or not 0 <= ordinal < len(indices):
        raise ValueError("configuration ordinal outside selected matrix")
    matrix = identity.get("matrix", {})
    if matrix.get("configuration_plan_schema") == DEPTH_PLAN_SCHEMA:
        row = _depth_configuration(indices[ordinal])
        if matrix.get("configurations", [])[ordinal] != row:
            raise ValueError("selected depth configuration identity changed")
        return row
    return dict(expanded_configuration(indices[ordinal]))


def _check_configuration_parameters(row: dict[str, Any], expected: dict[str, Any]) -> None:
    fields = ["step_pct", "tp_pct", "exchange_tp_pct", "global_tp_pct"]
    if "grid_depth_pct" in expected or "grid_depth_pct" in row:
        fields.append("grid_depth_pct")
    for field in fields:
        # Missing depth is the legacy 60% contract, never a variable-depth input.
        wanted = expected.get(field, "60" if field == "grid_depth_pct" else None)
        actual = row.get(field)
        if (actual is None) != (wanted is None) or (
            actual is not None and wanted is not None and Fraction(actual) != Fraction(wanted)
        ):
            raise ValueError(f"batch configuration differs: {field}")


class ResultCatalog:
    """One index writer; fsync binary frames before committing a batch boundary."""

    def __init__(self, path: Path, identity: dict[str, Any], *, create: bool = False) -> None:
        if create and (path.exists() or path.with_suffix(".bin").exists()):
            raise ValueError("refusing to replace binary catalog")
        if not create and not path.is_file():
            raise ValueError("missing binary catalog")
        if path.is_symlink() or path.with_suffix(".bin").is_symlink():
            raise ValueError("binary catalog symlinks are not permitted")
        self.path, self.identity = path, identity
        self.binary_path = path.with_suffix(".bin")
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=DELETE")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.binary = self.binary_path.open("x+b" if create else "r+b")
        self.prefix = hashlib.sha256()
        try:
            if create:
                self.binary.write(RESULT_MAGIC)
                self.binary.flush()
                os.fsync(self.binary.fileno())
                self.prefix.update(RESULT_MAGIC)
                self.connection.executescript("""
                    CREATE TABLE metadata(key TEXT PRIMARY KEY, value BLOB NOT NULL);
                    CREATE TABLE configurations(
                      ordinal INTEGER PRIMARY KEY, config_id TEXT NOT NULL UNIQUE,
                      canonical_ordinal INTEGER NOT NULL UNIQUE,
                      offset INTEGER NOT NULL UNIQUE, size INTEGER NOT NULL,
                      sha256 BLOB NOT NULL CHECK(length(sha256)=32));
                    CREATE TABLE batches(
                      first_ordinal INTEGER PRIMARY KEY, end_ordinal INTEGER NOT NULL,
                      receipt BLOB NOT NULL, sha256 BLOB NOT NULL CHECK(length(sha256)=32));
                """)
                with self.connection:
                    self.set_metadata("schema", CATALOG_SCHEMA)
                    self.set_metadata("identity", identity)
                    self.set_metadata("complete", False)
                    self.set_metadata(
                        "fields",
                        {
                            "result": list(RESULT_FIELDS),
                            "metrics": list(METRIC_FIELDS),
                            "codec": "framed positional MessagePack + zlib-3; exact ratios",
                        },
                    )
                    self.set_metadata(
                        "committed_boundary",
                        {
                            "bytes": len(RESULT_MAGIC),
                            "sha256": self.prefix.hexdigest(),
                        },
                    )
                descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            if self.metadata("schema") != CATALOG_SCHEMA or self.metadata("identity") != identity:
                raise ValueError("binary catalog identity differs from immutable study")
            if not create:
                boundary = self.metadata("committed_boundary")
                self.binary.seek(0)
                remaining = boundary["bytes"]
                while remaining:
                    block = self.binary.read(min(1024**2, remaining))
                    if not block:
                        raise ValueError("truncated committed binary prefix")
                    self.prefix.update(block)
                    remaining -= len(block)
                if self.prefix.hexdigest() != boundary["sha256"]:
                    raise ValueError("committed binary prefix checksum mismatch")
                # This writer owns its new output. Uncommitted bytes are never
                # treated as results; resume copies only the verified prefix.
                self.binary.truncate(boundary["bytes"])
                self.binary.seek(boundary["bytes"])
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self.binary.close()
        self.connection.close()

    def metadata(self, name: str) -> Any:
        row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"missing catalog metadata: {name}")
        return msgpack.unpackb(row[0], raw=False)

    def set_metadata(self, name: str, value: Any) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata VALUES(?,?)",
            (name, msgpack.packb(value, use_bin_type=True)),
        )

    def count(self) -> int:
        return int(self.connection.execute("SELECT count(*) FROM configurations").fetchone()[0])

    def append(
        self,
        first: int,
        results: Iterable[dict[str, Any]],
        receipt: dict[str, Any],
        pulse: Callable[[int], None] | None = None,
    ) -> None:
        if first != self.count() or self.metadata("complete"):
            raise ValueError("batch does not append the next bounded configuration interval")
        boundary = self.metadata("committed_boundary")
        previous_hash = self.prefix.copy()
        count = 0
        indices = _indices(self.identity)
        try:
            with self.connection:
                for ordinal, row in enumerate(results, first):
                    if count >= MAX_BATCH_SIZE or ordinal >= len(indices):
                        raise ValueError("batch exceeds bounded configuration interval")
                    canonical = indices[ordinal]
                    expected = configuration_at(self.identity, ordinal)
                    if row["id"] != expected["id"]:
                        raise ValueError("batch configuration identity/order mismatch")
                    _check_configuration_parameters(row, expected)
                    payload, digest = encode_result(row)
                    frame = RESULT_HEADER.pack(len(payload), digest) + payload
                    if pulse:
                        pulse(len(frame))
                    offset = self.binary.tell()
                    if self.binary.write(frame) != len(frame):
                        raise OSError("short binary result write")
                    self.prefix.update(frame)
                    self.connection.execute(
                        "INSERT INTO configurations VALUES(?,?,?,?,?,?)",
                        (
                            ordinal,
                            row["id"],
                            canonical,
                            offset,
                            len(payload),
                            digest,
                        ),
                    )
                    count += 1
                if count == 0:
                    raise ValueError("empty batch cannot append results")
                # Exhausting results also completes its whole-stream checksum and
                # accounting checks. Only now make bytes durable and publish them.
                self.binary.flush()
                os.fsync(self.binary.fileno())
                if pulse:
                    pulse(0)
                packed = msgpack.packb(receipt, use_bin_type=True)
                self.connection.execute(
                    "INSERT INTO batches VALUES(?,?,?,?)",
                    (
                        first,
                        first + count,
                        packed,
                        hashlib.sha256(packed).digest(),
                    ),
                )
                self.set_metadata(
                    "committed_boundary",
                    {
                        "bytes": self.binary.tell(),
                        "sha256": self.prefix.hexdigest(),
                    },
                )
        except BaseException:
            self.binary.seek(boundary["bytes"])
            self.binary.truncate()
            self.prefix = previous_hash
            raise

    def payloads(
        self, first: int = 0, end: int | None = None
    ) -> Iterator[tuple[int, str, bytes, bytes]]:
        end = self.count() if end is None else end
        self.binary.flush()
        with self.binary_path.open("rb") as handle:
            for ordinal, config_id, offset, size, checksum in self.connection.execute(
                "SELECT ordinal,config_id,offset,size,sha256 FROM configurations "
                "WHERE ordinal>=? AND ordinal<? ORDER BY ordinal",
                (first, end),
            ):
                yield ordinal, config_id, _read_payload(handle, offset, size, checksum), checksum

    def verify(self, expected_count: int, *, complete: bool = False) -> dict[str, Any]:
        if self.connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("binary catalog failed SQLite integrity check")
        self.binary.seek(0)
        if self.binary.read(len(RESULT_MAGIC)) != RESULT_MAGIC:
            raise ValueError("invalid binary results header")
        count = 0
        digest, prefix = hashlib.sha256(), hashlib.sha256(RESULT_MAGIC)
        offset = len(RESULT_MAGIC)
        indices = _indices(self.identity)
        for ordinal, config_id, canonical, actual_offset, size, checksum in self.connection.execute(
            "SELECT ordinal,config_id,canonical_ordinal,offset,size,sha256 "
            "FROM configurations ORDER BY ordinal"
        ):
            if (
                count >= len(indices)
                or ordinal != count
                or canonical != indices[count]
                or config_id != configuration_at(self.identity, count)["id"]
            ):
                raise ValueError("binary catalog coverage or configuration identity mismatch")
            if actual_offset != offset:
                raise ValueError("binary catalog frame coverage mismatch")
            self.binary.seek(offset)
            payload = _read_payload(self.binary, offset, size, checksum)
            _verify_stored_payload(payload, checksum, config_id)
            prefix.update(RESULT_HEADER.pack(size, checksum))
            prefix.update(payload)
            offset += RESULT_HEADER.size + size
            digest.update(ordinal.to_bytes(8, "little"))
            digest.update(checksum)
            count += 1
        boundary = self.metadata("committed_boundary")
        if boundary != {"bytes": offset, "sha256": prefix.hexdigest()}:
            raise ValueError("committed binary prefix checksum mismatch")
        self.binary.seek(offset)
        if count != expected_count:
            raise ValueError("binary catalog configuration count mismatch")
        end = 0
        for first, current_end, receipt, checksum in self.connection.execute(
            "SELECT first_ordinal,end_ordinal,receipt,sha256 FROM batches ORDER BY first_ordinal"
        ):
            if first != end or current_end <= first or hashlib.sha256(receipt).digest() != checksum:
                raise ValueError("binary catalog batch coverage/receipt mismatch")
            end = current_end
        if end != count:
            raise ValueError("binary catalog batch coverage incomplete")
        result = {
            "configuration_count": count,
            "results_sha256": digest.hexdigest(),
            "binary_bytes": offset,
            "binary_sha256": prefix.hexdigest(),
        }
        if complete:
            if count != len(indices):
                raise ValueError("only the complete selected matrix can be published")
            with self.connection:
                self.set_metadata("complete", True)
                self.set_metadata("verification", result)
        return result


def _read_payload(handle: Any, offset: int, size: int, checksum: bytes) -> bytes:
    if not 0 < size <= MAX_PAYLOAD_BYTES + 65536 or offset < len(RESULT_MAGIC):
        raise ValueError("invalid binary result index bounds")
    handle.seek(offset)
    header = handle.read(RESULT_HEADER.size)
    if len(header) != RESULT_HEADER.size or RESULT_HEADER.unpack(header) != (size, checksum):
        raise ValueError("binary result frame checksum/index mismatch")
    payload = handle.read(size)
    if len(payload) != size:
        raise ValueError("truncated binary result frame")
    return cast(bytes, payload)


def _page_results(
    path: Path, cursor: int = 0, limit: int = 50, config_id: str | None = None
) -> dict[str, Any]:
    if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("invalid bounded catalog page")
    path = path / "results.sqlite" if path.is_dir() else path
    with closing(_readonly(path)) as connection:
        metadata = {
            key: msgpack.unpackb(value, raw=False)
            for key, value in connection.execute("SELECT key,value FROM metadata")
        }
        schema = metadata.get("schema")
        if schema not in (CATALOG_SCHEMA, LEGACY_CATALOG_SCHEMA):
            raise ValueError("unsupported binary catalog")
        fields = (
            "ordinal,payload,sha256"
            if schema == LEGACY_CATALOG_SCHEMA
            else "ordinal,offset,size,sha256"
        )
        count = int(connection.execute("SELECT count(*) FROM configurations").fetchone()[0])
        if config_id is None:
            rows = connection.execute(
                f"SELECT {fields} FROM configurations WHERE ordinal>=? ORDER BY ordinal LIMIT ?",
                (cursor, limit + 1),
            ).fetchall()
        else:
            rows = connection.execute(
                f"SELECT {fields} FROM configurations WHERE config_id=?", (config_id,)
            ).fetchall()
        results = []
        if schema == LEGACY_CATALOG_SCHEMA:
            results = [decode_result(row[1], row[2]) for row in rows[:limit]]
        else:
            binary_path = path.with_suffix(".bin")
            if binary_path.is_symlink():
                raise ValueError("binary catalog symlinks are not permitted")
            with binary_path.open("rb") as handle:
                if handle.read(len(RESULT_MAGIC)) != RESULT_MAGIC:
                    raise ValueError("invalid binary results header")
                boundary = metadata["committed_boundary"]["bytes"]
                for _, offset, size, checksum in rows[:limit]:
                    if offset + RESULT_HEADER.size + size > boundary:
                        raise ValueError("binary index extends beyond committed prefix")
                    results.append(
                        decode_result(_read_payload(handle, offset, size, checksum), checksum)
                    )
        return {
            "configurations": results,
            "next_cursor": rows[limit][0] if len(rows) > limit else None,
            "complete": len(rows) <= limit,
            "study_complete": bool(metadata["complete"]),
            "configuration_count": count,
            "charts_available": False,
            "chart_mode": "on_demand",
            "binary_results": True,
            "matrix_kind": MATRIX_KIND,
        }


def page_results(
    path: Path, cursor: int = 0, limit: int = 50, config_id: str | None = None
) -> dict[str, Any]:
    try:
        return _page_results(path, cursor, limit, config_id)
    except sqlite3.Error as error:
        raise ValueError("cannot read binary result catalog") from error


catalog_page = page_results


def selected_configuration_plan() -> dict[str, Any]:
    """Owner-approved first study; stable original IDs and canonical ordinals."""
    indices = []
    for step in range(2, 21, 2):
        for tp in range(4, 17, 2):
            for exchange in (0, *range(4, 17, 2)):
                for global_tp in (0, *range(4, 17, 2)):
                    indices.append(
                        ((step - 1) * 27 + tp - 4) * 784
                        + (0 if exchange == 0 else exchange - 3) * 28
                        + (0 if global_tp == 0 else global_tp - 3)
                    )
    return {
        "schema": "grid-research-configuration-plan-v1",
        "grid_depth_pct": "60",
        "configurations": [expanded_configuration(index) for index in indices],
    }


def _base_ordinal(step: int, tp: int, exchange: int, global_tp: int) -> int:
    """Half-percent axis coordinates from the unchanged original matrix."""
    return (
        ((step - 1) * 27 + tp - 4) * 784
        + (0 if exchange == 0 else exchange - 3) * 28
        + (0 if global_tp == 0 else global_tp - 3)
    )


def depth_configuration_plan() -> dict[str, Any]:
    """Owner-approved 59,290 factorial choices plus 77 one-axis probes."""
    bases = {
        _base_ordinal(step, tp, exchange, global_tp)
        for step in range(2, 21, 2)
        for tp in range(4, 17, 2)
        for exchange in range(4, 25, 2)
        for global_tp in range(4, 25, 2)
    }
    # Nine probes around 6/5/4/6 and two around 1/4/5/4, all axes in half-%.
    probes = [
        *((12, 10, 8, global_tp) for global_tp in (11, 13, 15)),
        *((12, 10, exchange, 12) for exchange in (7, 9)),
        *((step, 10, 8, 12) for step in (11, 13)),
        *((12, tp, 8, 12) for tp in (9, 11)),
        *((step, 8, 10, 8) for step in (1, 3)),
    ]
    bases.update(_base_ordinal(*probe) for probe in probes)
    rows = [
        _depth_configuration(block * EXPANDED_CONFIGURATION_COUNT + base)
        for block in range(len(_DEPTH_BLOCKS))
        for base in sorted(bases)
    ]
    return {
        "schema": DEPTH_PLAN_SCHEMA,
        "grid_depth_pct_values": [str(value) for value in GRID_DEPTHS],
        "configurations": rows,
    }


def _selection(path: Path) -> dict[str, Any]:
    return _configuration_selection(_runner().read_json(path), _runner().sha256(path))


def _configuration_selection(plan: dict[str, Any], plan_sha256: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", plan_sha256):
        raise ValueError("invalid configuration plan identity")
    if plan.get("schema") == DEPTH_PLAN_SCHEMA:
        return _depth_configuration_selection(plan, plan_sha256)
    if (
        plan.get("schema") != "grid-research-configuration-plan-v1"
        or plan.get("grid_depth_pct") != "60"
    ):
        raise ValueError("configuration plan must retain declared 60 percent grid depth")
    configurations = plan.get("configurations")
    if (
        not isinstance(configurations, list)
        or not 1 <= len(configurations) <= EXPANDED_CONFIGURATION_COUNT
    ):
        raise ValueError("invalid selected configuration list")
    indices = []
    previous = -1
    # IDs and parameter strings must match the existing canonical matrix exactly.
    for row in configurations:
        if not isinstance(row, dict):
            raise ValueError("invalid selected configuration row")
        match = re.fullmatch(r"s(\d{2})-t(\d{2})(?:-e(\d{2})-g(\d{2}))?", str(row.get("id", "")))
        if not match:
            raise ValueError("invalid selected configuration ID")
        step, tp, exchange, global_tp = (int(value or 0) for value in match.groups())
        index = (
            ((step - 1) * 27 + tp - 4) * 784
            + (0 if exchange == 0 else exchange - 3) * 28
            + (0 if global_tp == 0 else global_tp - 3)
        )
        if (
            not 0 <= index < EXPANDED_CONFIGURATION_COUNT
            or index <= previous
            or row != expanded_configuration(index)
        ):
            raise ValueError("selected configurations must be unique, canonical and ordered")
        indices.append(index)
        previous = index
    return {
        "kind": MATRIX_KIND,
        "configuration_count": len(indices),
        "canonical_ordinals": indices,
        "configurations": configurations,
        "configuration_plan_sha256": plan_sha256,
        "grid_depth_pct": "60",
        "order": "selected canonical order; study ordinal differs from canonical ordinal",
    }


def _depth_configuration_selection(plan: dict[str, Any], plan_sha256: str) -> dict[str, Any]:
    if set(plan) != {"schema", "grid_depth_pct_values", "configurations"} or plan.get(
        "grid_depth_pct_values"
    ) != [str(value) for value in GRID_DEPTHS]:
        raise ValueError("depth plan must declare the approved seven BUY depths")
    rows = plan.get("configurations")
    if not isinstance(rows, list) or not 1 <= len(rows) <= DEPTH_CONFIGURATION_COUNT:
        raise ValueError("invalid selected depth configuration list")
    indices = []
    previous = -1
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid selected depth configuration row")
        match = re.fullmatch(
            r"s(\d{2})-t(\d{2})-e(\d{2})-g(\d{2})(?:-d(\d{2}))?", str(row.get("id", ""))
        )
        if not match:
            raise ValueError("invalid selected depth configuration ID")
        step, tp, exchange, global_tp = (int(value) for value in match.groups()[:4])
        depth = row.get("grid_depth_pct")
        if depth not in [str(value) for value in GRID_DEPTHS]:
            raise ValueError("invalid selected BUY depth")
        if not (1 <= step <= 20 and 4 <= tp <= 16 and 4 <= exchange <= 24 and 4 <= global_tp <= 24):
            raise ValueError("selected depth configuration outside approved axes")
        base = _base_ordinal(step, tp, exchange, global_tp)
        canonical = _DEPTH_BLOCKS.index(int(depth)) * EXPANDED_CONFIGURATION_COUNT + base
        if canonical <= previous or row != _depth_configuration(canonical):
            raise ValueError("selected depth configurations must be unique, canonical and ordered")
        indices.append(canonical)
        previous = canonical
    return {
        "kind": MATRIX_KIND,
        "configuration_plan_schema": DEPTH_PLAN_SCHEMA,
        "configuration_count": len(indices),
        "canonical_ordinals": indices,
        "configurations": rows,
        "configuration_plan_sha256": plan_sha256,
        "grid_depth_pct_values": [str(value) for value in GRID_DEPTHS],
        "order": "depth blocks 60,30,35,40,45,50,55; original canonical order within each block",
    }


def _policy(identity: dict[str, Any]) -> dict[str, Any]:
    result = {
        **MATRIX_ACCEPTANCE_POLICY,
        "configuration_count": len(_indices(identity)),
        "configuration_list_sha256": _digest(list(_indices(identity))),
    }
    if identity.get("matrix", {}).get("configuration_plan_schema") == DEPTH_PLAN_SCHEMA:
        result["configuration_parameters_sha256"] = _digest(identity["matrix"]["configurations"])
    return result


def _identity(
    preparation: Path,
    binary: Path,
    settings: dict[str, Any],
    batch_size: int,
    selection: dict[str, Any] | None = None,
    source_selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runner = _runner()
    root = Path(__file__).resolve().parents[1]
    names = (
        "scripts/research_sweep.py",
        "scripts/research_sweep_matrix.py",
        "scripts/research_sweep_binary.py",
        "scripts/research_sweep_reference.py",
        "scripts/research_sweep_sources.py",
        "scripts/research_sweep_verify.py",
        "scripts/research_sweep_recovery.py",
        "scripts/research_sweep_statistics.py",
        "cpp/backtest/sweep.cpp",
        "cpp/backtest/sweep.hpp",
        "cpp/backtest/numeric.cpp",
        "cpp/backtest/numeric.hpp",
        "cpp/backtest/sweep_units.cpp",
        "cpp/backtest/sweep_units.hpp",
        "cpp/backtest/CMakeLists.txt",
        "uv.lock",
    )
    hashes = {name: runner.sha256(root / name) for name in names}
    identity = {
        "preparation_sha256": runner.sha256(preparation),
        "binary_sha256": runner.sha256(binary),
        "code_sha256": _digest(hashes),
        "code_files": hashes,
        "matrix": selection if selection is not None else MATRIX_DESCRIPTION,
        "settings": settings,
        "batch_size": batch_size,
        "runtime_limits": dict(runner.LIMITS),
        "statistics_policy": _statistics_policy(source_selection is not None),
        "acceptance_policy": dict(MATRIX_ACCEPTANCE_POLICY),
    }
    if source_selection is not None:
        identity["source_selection"] = source_selection
    return identity


def _study(
    request: dict[str, Any], output: Path, binary: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    runner = _runner()
    parent = Path(request["source_output"]) if request.get("source_output") else None
    parent_study = (
        runner.read_json(parent / "matrix-study.json")
        if parent and (parent / "matrix-study.json").is_file()
        else None
    )
    if parent_study is not None and (
        parent_study.get("schema") != "grid-research-matrix-study-v1"
        or not isinstance(parent_study.get("identity"), dict)
        or parent_study.get("study_sha256") != _digest(parent_study["identity"])
    ):
        raise runner.Blocked("matrix parent study identity changed or is malformed")
    preparation = (
        Path(parent_study["preparation_path"]) if parent_study else runner.preparation_path(parent)
    )
    prepared = runner.read_json(preparation)
    sources = runner._engine_sources(prepared)
    from scripts.research_sweep_sources import (
        CURRENT_RULES_SCHEMA,
        FLAT_HOUR_SCHEMA,
        GAP_FREE_POLICY,
        GAP_FREE_SCHEMA,
        GAP_FREE_VENUES,
        REVIEWED_SCHEMA,
    )

    source_selection = None
    if prepared.get("schema") in (
        GAP_FREE_SCHEMA,
        REVIEWED_SCHEMA,
        CURRENT_RULES_SCHEMA,
        FLAT_HOUR_SCHEMA,
    ):
        expected_venues = list(GAP_FREE_VENUES)
        source_ids = sorted(row["id"] for row in sources)
        source_selection = {
            "history_policy": GAP_FREE_POLICY,
            "expected_venues": expected_venues,
            "manifest_sha256": runner.sha256(preparation),
            "source_ids": source_ids,
            "selected_source_ids": source_ids,
        }
        if {row["venue"] for row in sources} != set(expected_venues):
            raise runner.Blocked("gap-free study requires exactly its five declared venues")
    elif set(VENUES) != {row["venue"] for row in sources}:
        raise runner.Blocked("expanded full-history study requires all seven venues")
    settings = runner.execution_settings(request)
    settings["chart_mode"] = "on_demand"
    batch_size = request.get("batch_size", DEFAULT_BATCH_SIZE)
    if parent_study:
        for field, value in parent_study["identity"]["settings"].items():
            if field not in request:
                settings[field] = value
        if "batch_size" not in request:
            batch_size = parent_study["identity"]["batch_size"]
    if type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError("matrix batch_size must be between 1 and 8192")
    selection = parent_study["identity"]["matrix"] if parent_study else None
    if request.get("configuration_plan"):
        requested = _selection(Path(request["configuration_plan"]))
        if selection is not None and requested != selection:
            raise runner.Blocked("configuration plan differs from immutable selected list")
        selection = requested
    if source_selection is not None:
        approved = (
            depth_configuration_plan()
            if selection is not None
            and selection.get("configuration_plan_schema") == DEPTH_PLAN_SCHEMA
            else selected_configuration_plan()
        )
        if selection is None:
            path = output / "configuration-plan.json"
            runner.write_json(path, approved)
            selection = _selection(path)
        plan_sha256 = selection.get("configuration_plan_sha256")
        if not isinstance(plan_sha256, str) or selection != _configuration_selection(
            approved, plan_sha256
        ):
            raise runner.Blocked(
                "gap-free study must preserve the approved selected configurations"
            )
    identity = _identity(preparation, binary, settings, batch_size, selection, source_selection)
    if parent_study and identity != parent_study["identity"]:
        raise runner.Blocked("matrix study code, binary, rules, sources or settings changed")
    study = {
        "schema": "grid-research-matrix-study-v1",
        "identity": identity,
        "study_sha256": _digest(identity),
        "preparation_path": str(preparation.resolve()),
        "artifact_root": (
            parent_study.get("artifact_root")
            if parent_study
            else str(Path(request["artifact_root"]).resolve())
            if request.get("artifact_root")
            else None
        ),
        "sources": len(sources),
        "source_records": sum(row["records"] for row in sources),
        "matrix": identity["matrix"],
        "assumptions": {
            "fee_each_side": "0.002",
            "capital_limit": None,
            "common_tp_basis": "current_scope_cycle_peak_spent",
            "common_tp_net_of_paid_and_projected_exit_fees": True,
            "common_tp_condition": "greater_or_equal",
            "event_order": ["ordinary_trades", "global_tp", "exchange_tp"],
            "common_exit_price": "last_known_including_stale",
            "reentry": "minimum_buy_at_next_observed_point_including_previously_empty_pairs",
            "exchange_exit_resets": "that_exchange_cycle_only",
            "global_exit_resets": "all_cycles",
            "chart_mode": "on_demand",
            "financial_storage": "results.bin: framed zlib-3 MessagePack; SQLite index only",
            "statistics": identity["statistics_policy"],
        },
    }
    if identity["matrix"].get("configuration_plan_schema") == DEPTH_PLAN_SCHEMA:
        study["assumptions"].update(
            grid_depth_pct_values=identity["matrix"]["grid_depth_pct_values"],
            grid_depth_basis="per_configuration_current_grid_anchor",
            grid_depth_behavior="exclude_buy_levels_below_boundary_without_stop_loss",
        )
    if source_selection is not None:
        study["source_selection"] = source_selection
        study["assumptions"].update(
            history_policy=GAP_FREE_POLICY,
            expected_venues=list(GAP_FREE_VENUES),
            historical_rules_verified=False,
            research_admission="PENDING_PRICE_IDENTITY_AND_HISTORICAL_RULE_REVIEW",
            common_tp_requires_current_price_for_every_open_position=True,
            common_exit_price="current_observed_price_only",
        )
        if prepared.get("schema") in (REVIEWED_SCHEMA, CURRENT_RULES_SCHEMA, FLAT_HOUR_SCHEMA):
            study["assumptions"]["research_admission"] = prepared["research_readiness"]
        if prepared.get("schema") in (CURRENT_RULES_SCHEMA, FLAT_HOUR_SCHEMA):
            study["assumptions"]["rules_policy"] = (
                "current_public_ccxt_snapshot_applied_to_full_history"
            )
            study["assumptions"]["rules_collection"] = prepared["rules_collection"]
        if prepared.get("schema") == FLAT_HOUR_SCHEMA:
            study["assumptions"]["flat_price_screen"] = (
                "exclude_pair_with_60_consecutive_identical_flat_ohlc_minutes"
            )
    runner.write_json(output / "matrix-study.json", study)
    return study, sources


def _evidence(parent: Path | None, study: dict[str, Any]) -> dict[str, Any]:
    runner = _runner()
    if not parent:
        return {}
    path = parent / "receipt.json"
    if not path.is_file():
        path = parent / "admission.json"
    if not path.is_file():
        return {}
    old = runner.read_json(path)
    candidates = dict(old.get("evidence", {}))
    if old.get("status") in ("PASS", "COMPLETE") and old.get("matrix_kind") == MATRIX_KIND:
        candidates[old.get("completed_stage", old["action"])] = {
            "receipt": str((parent / "receipt.json").resolve()),
            "sha256": runner.sha256(parent / "receipt.json"),
        }
    accepted = {}
    for stage, binding in candidates.items():
        path = Path(binding["receipt"])
        if path.is_file() and runner.sha256(path) == binding["sha256"]:
            value = runner.read_json(path)
            if (
                value.get("status") in ("PASS", "COMPLETE")
                and value.get("study_sha256") == study["study_sha256"]
                and value.get("matrix_kind") == MATRIX_KIND
                and value.get("completed_stage", value.get("action")) == stage
            ):
                accepted[stage] = binding
    return accepted


def _verify_summary(
    summary: dict[str, Any],
    configurations: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    **options: Any,
) -> None:
    for _ in _verified_results(summary, configurations, sources, **options):
        pass


def _verified_results(
    summary: dict[str, Any],
    configurations: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    *,
    full_history: bool,
    expected_records: int | None = None,
    binary_sha256: str | None = None,
    statistics_required: bool = False,
    result_dir: Path | None = None,
    pulse: Callable[[], None] | None = None,
) -> Iterator[dict[str, Any]]:
    from scripts.research_sweep_verify import HASH, MONEY_FIELDS, SUM_FIELDS, _check_metrics

    if summary.get("status") not in ("COMPLETE", "COMPLETE_BATCH", "COMPLETE_DIAGNOSTIC"):
        raise ValueError("batch did not complete")
    if statistics_required and summary.get("statistics_mode") != "monthly_v1":
        raise ValueError("batch omitted required monthly statistics")
    from scripts.research_sweep_binary import engine_results

    rows = engine_results(result_dir or Path("."), summary, pulse)
    count = (
        summary["configuration_count"]
        if "configuration_count" in summary
        else len(summary.get("configurations", []))
    )
    if count != len(configurations) or summary.get("source_count") != len(sources):
        raise ValueError("batch source/configuration count mismatch")
    if full_history and summary.get("processed_records") != sum(row["records"] for row in sources):
        raise ValueError("batch did not process every source record")
    if expected_records is not None and summary.get("processed_records") != expected_records:
        raise ValueError("batch historical-window record count mismatch")
    if full_history and summary.get("duration_stopped", False):
        raise ValueError("full-history batch stopped at a duration boundary")
    if binary_sha256 is not None and summary.get("build_sha256") != binary_sha256:
        raise ValueError("batch executable identity mismatch")
    if summary.get("audit", {}).get("source_sha256_verified") is not True:
        raise ValueError("batch lacks full source identity verification")
    if summary["audit"].get("records") != sum(source["records"] for source in sources):
        raise ValueError("batch audited source record count mismatch")
    if all("bytes" in source for source in sources) and summary["audit"].get("bytes") != sum(
        source["bytes"] for source in sources
    ):
        raise ValueError("batch audited source byte count mismatch")
    venues = {row["venue"] for row in sources}
    bindings = []
    for expected, row in zip(configurations, rows, strict=True):
        if row["id"] != expected["id"] or not HASH.fullmatch(row.get("trade_hash", "")):
            raise ValueError("batch identity or trade hash mismatch")
        _check_configuration_parameters(row, expected)
        fingerprint = row.get("pair_fingerprint", {})
        if (
            fingerprint.get("rows") != len(sources)
            or not HASH.fullmatch(fingerprint.get("sha256", ""))
            or type(fingerprint.get("bytes")) is not int
            or fingerprint["bytes"] <= 0
        ):
            raise ValueError("batch lacks a complete per-configuration pair fingerprint")
        if len(row["venues"]) != len(venues) or {item["venue"] for item in row["venues"]} != venues:
            raise ValueError("batch venue coverage mismatch")
        totals = _check_metrics(row["total"])
        venue_metrics = [_check_metrics(item) for item in row["venues"]]
        for name in SUM_FIELDS:
            value = sum(
                (item[name] for item in venue_metrics), Fraction() if name in MONEY_FIELDS else 0
            )
            if totals[name] != value:
                raise ValueError(f"batch aggregate {name} mismatch")
        if statistics_required or summary.get("statistics_mode") == "monthly_v1":
            _check_performance_summary(row, sources, summary)
        bindings.append({**row["pair_fingerprint"], "config_id": row["id"]})
        yield row
    fingerprint = summary.get("fingerprint", {})
    if fingerprint.get("pairs_hash_scope") == "per_configuration" and (
        fingerprint.get("pairs_fingerprints_sha256") != _digest(bindings)
        or fingerprint.get("pairs_bytes") != sum(row["bytes"] for row in bindings)
        or fingerprint.get("pair_rows") != len(sources) * count
        or fingerprint.get("configuration_count") != count
    ):
        raise ValueError("compact pair fingerprint manifest mismatch")


def _next_month_ms(timestamp: int) -> int:
    value = datetime.fromtimestamp(timestamp // 1000, UTC)
    year, month = (value.year + 1, 1) if value.month == 12 else (value.year, value.month + 1)
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000)


def _month_start_ms(timestamp: int) -> int:
    value = datetime.fromtimestamp(timestamp // 1000, UTC)
    return int(datetime(value.year, value.month, 1, tzinfo=UTC).timestamp() * 1000)


def _check_performance_summary(
    row: dict[str, Any], sources: list[dict[str, Any]], summary: dict[str, Any]
) -> None:
    """Verify each new number once in integer scale, without Fraction reductions."""
    performance = row.get("performance", {})
    if performance.get("schema") != "grid-monthly-performance-v1":
        raise ValueError("batch lacks monthly financial results")
    scopes = [performance["total"], *performance["venues"]]
    originals = [row["total"], *row["venues"]]
    if len(scopes) != len(originals):
        raise ValueError("monthly venue coverage mismatch")
    own_calendar = summary.get("statistics_calendar_scope") == STATISTICS_POLICY["calendar_scope"]
    if summary.get("statistics_calendar_scope") not in (None, STATISTICS_POLICY["calendar_scope"]):
        raise ValueError("unknown monthly calendar scope")
    expected_calendar = None
    total_period = scopes[0]["overall"]
    origin, end = total_period["start_ms"], total_period["end_ms"]
    if type(origin) is not int or type(end) is not int or end < origin:
        raise ValueError("invalid portfolio calendar bounds")
    venue_spans: dict[str, tuple[int, int]] = {}
    venue_firsts: dict[str, int] = {}
    if own_calendar:
        for source in sources:
            venue = source["venue"]
            venue_firsts[venue] = min(
                venue_firsts.get(venue, source["first_ms"]), source["first_ms"]
            )
            if source["last_ms"] < origin or source["first_ms"] >= end:
                continue
            first, last = source["first_ms"], source["last_ms"] + 60_000
            if venue in venue_spans:
                old_first, old_last = venue_spans[venue]
                venue_spans[venue] = min(first, old_first), max(last, old_last)
            else:
                venue_spans[venue] = first, last
    for scope, original in zip(scopes, originals, strict=True):
        if scope.get("venue") != original.get("venue"):
            raise ValueError("monthly venue identity mismatch")
        scale = int(scope["money_scale_denominator"])
        if scale <= 0:
            raise ValueError("invalid monthly monetary scale")

        def units(value: str, scale: int = scale) -> int:
            numerator, denominator = value.split("/")
            divisor = int(denominator)
            if divisor <= 0:
                raise ValueError("invalid monthly financial denominator")
            result, remainder = divmod(int(numerator) * scale, divisor)
            if remainder:
                raise ValueError("monthly amount is outside its exact scale")
            return result

        fields = ("total_pnl", "realized_gross", "fees", "net_profit_closed", "net_loss_closed")
        counts = (
            "buy_count",
            "sell_count",
            "winning_closures",
            "losing_closures",
            "breakeven_closures",
        )
        sums = dict.fromkeys((*fields, *counts), 0)
        sample_count = sample_sum = sample_square = sample_downside = 0
        calendar_rows: list[tuple[int, int, bool]] = []
        peak = spent = previous_end = None
        for period in [*scope["monthly"], scope["overall"]]:
            overall = period is scope["overall"]
            amounts = {
                name: units(period[name]) for name in (*fields, "spent_open", "spent", "peak_spent")
            }
            if (
                amounts["fees"] < 0
                or amounts["net_profit_closed"] < 0
                or amounts["net_loss_closed"] < 0
                or min(amounts["spent_open"], amounts["spent"]) < 0
                or amounts["peak_spent"] < max(amounts["spent_open"], amounts["spent"])
            ):
                raise ValueError("invalid monthly financial bounds")
            if any(type(period[name]) is not int or period[name] < 0 for name in counts):
                raise ValueError("invalid monthly trade counts")
            if sum(period[name] for name in counts[2:]) != period["sell_count"]:
                raise ValueError("monthly closure count mismatch")
            if not overall:
                if (
                    period["end_ms"] <= period["start_ms"]
                    or previous_end is not None
                    and period["start_ms"] != previous_end
                    or spent is not None
                    and amounts["spent_open"] != spent
                ):
                    raise ValueError("monthly history or exposure continuity mismatch")
                previous_end, spent = period["end_ms"], amounts["spent"]
                peak = amounts["peak_spent"] if peak is None else max(peak, amounts["peak_spent"])
                calendar_rows.append(
                    (period["start_ms"], period["end_ms"], period["complete_month"])
                )
                for name in fields:
                    sums[name] += amounts[name]
                for name in counts:
                    sums[name] += period[name]
                if period["complete_month"]:
                    value = amounts["total_pnl"]
                    sample_count += 1
                    sample_sum += value
                    sample_square += value * value
                    if value < 0:
                        sample_downside += value * value
            else:
                if any(amounts[name] != sums[name] for name in fields) or any(
                    period[name] != sums[name] for name in counts
                ):
                    raise ValueError("monthly figures do not sum to overall results")
                if peak is not None and (
                    amounts["spent"] != spent or amounts["peak_spent"] != peak
                ):
                    raise ValueError("monthly exposure extrema mismatch")
                if any(
                    period[name] != original[name]
                    for name in (
                        "spent",
                        "peak_spent",
                        "peak_spent_ms",
                        "total_pnl",
                        "realized_gross",
                        "fees",
                        "buy_count",
                        "sell_count",
                    )
                ):
                    raise ValueError("monthly overall results differ from primary accounting")
                if (
                    500
                    * (
                        amounts["net_profit_closed"]
                        - amounts["net_loss_closed"]
                        - amounts["realized_gross"]
                        + amounts["fees"]
                    )
                    != amounts["spent"]
                ):
                    raise ValueError(
                        "closed-position results do not reconcile both fees and open buys"
                    )
            profit, loss = amounts["net_profit_closed"], amounts["net_loss_closed"]
            if loss:
                numerator, denominator = period["profit_factor"].split("/")
                if (
                    int(numerator) * loss != int(denominator) * profit
                    or int(denominator) <= 0
                    or period["profit_factor_status"] != "available"
                ):
                    raise ValueError("monthly profit factor mismatch")
            elif period["profit_factor"] is not None or period["profit_factor_status"] != (
                "no_closures" if period["sell_count"] == 0 else "no_losses"
            ):
                raise ValueError("invalid unavailable monthly profit factor")
            if (
                period["sample_months"],
                int(period["monthly_sum_units"]),
                int(period["monthly_square_sum_units"]),
                int(period["monthly_downside_square_sum_units"]),
            ) != (sample_count, sample_sum, sample_square, sample_downside):
                raise ValueError("monthly exact moment mismatch")
            for name in ("sharpe", "sortino"):
                denominator = (
                    sample_count * (sample_count * sample_square - sample_sum**2)
                    if name == "sharpe"
                    else sample_count * sample_downside
                )
                unavailable = (
                    "insufficient_months"
                    if sample_count < 2
                    else ("zero_variance" if name == "sharpe" else "no_downside")
                )
                if sample_count < 2 or denominator == 0:
                    if (
                        period[name] is not None
                        or period[name + "_squared"] is not None
                        or period[name + "_status"] != unavailable
                    ):
                        raise ValueError("invalid unavailable monthly risk ratio")
                else:
                    numerator = 12 * sample_sum**2 * (sample_count - 1 if name == "sharpe" else 1)
                    stored_n, stored_d = map(int, period[name + "_squared"].split("/"))
                    if (
                        stored_d <= 0
                        or stored_n * denominator != stored_d * numerator
                        or period[name + "_status"] != "available"
                        or re.fullmatch(r"-?\d+(?:\.\d{1,24})?", period[name]) is None
                    ):
                        raise ValueError("monthly risk ratio mismatch")
        if not own_calendar:
            if expected_calendar is None:
                expected_calendar = calendar_rows
            elif expected_calendar != calendar_rows:
                raise ValueError("monthly calendars differ between scopes")
            continue
        venue = scope.get("venue")
        if venue is None:
            expected_start, expected_end = origin, end
        else:
            if venue not in venue_firsts:
                raise ValueError("monthly venue absent from sources")
            if venue in venue_spans:
                first, last = venue_spans[venue]
                expected_start = min(max(first, origin), end)
                expected_end = max(expected_start, min(last, end))
            else:
                edge = origin if venue_firsts[venue] < origin else end
                expected_start = expected_end = edge
        if (
            scope["overall"]["start_ms"] != expected_start
            or scope["overall"]["end_ms"] != expected_end
        ):
            raise ValueError("monthly scope period differs from its observed history")
        cursor = expected_start
        for start, stop, complete in calendar_rows:
            boundary = min(_next_month_ms(cursor), expected_end)
            expected_complete = cursor == _month_start_ms(cursor) and stop == _next_month_ms(cursor)
            if start != cursor or stop != boundary or complete != expected_complete:
                raise ValueError("monthly scope calendar has a missing or padded month")
            cursor = stop
        if cursor != expected_end:
            raise ValueError("monthly scope calendar does not cover its observed history")


def _window_records(sources: list[dict[str, Any]], window: tuple[int, int] | None) -> int:
    if window is None:
        return sum(source["records"] for source in sources)
    result = 0
    for source in sources:
        with Path(source["path"]).open("rb") as handle:
            positions = []
            for boundary in window:
                low, high = 0, source["records"]
                while low < high:
                    middle = (low + high) // 2
                    handle.seek(middle * 24)
                    raw = handle.read(8)
                    if len(raw) != 8:
                        raise ValueError("source truncated during window accounting")
                    if struct.unpack("<q", raw)[0] < boundary:
                        low = middle + 1
                    else:
                        high = middle
                positions.append(low)
            result += positions[1] - positions[0]
    return result


def _batch_plan(
    study: dict[str, Any],
    sources: list[dict[str, Any]],
    configurations: list[dict[str, Any]],
    output: Path,
    *,
    window: tuple[int, int] | None = None,
    diagnostic: bool = False,
) -> dict[str, Any]:
    runner = _runner()
    plan = runner.make_plan(
        sources,
        output / "engine",
        "validation" if window or diagnostic else "matrix_batch",
        configurations=configurations,
        settings=study["identity"]["settings"],
    )
    plan.update(
        matrix_kind=MATRIX_KIND,
        runtime_limits=dict(study["identity"]["runtime_limits"]),
        keep_results=False,
        keep_charts=False,
        summary_stdout=False,
        matrix_study_sha256=study["study_sha256"],
        statistics_mode="monthly_v1",
        result_output="framed_msgpack_v1",
    )
    if window:
        plan.update(start_ms=window[0], end_ms=window[1])
    _bind_source_selection(study, sources, plan, diagnostic=bool(window or diagnostic))
    return cast(dict[str, Any], plan)


def _bind_source_selection(
    study: dict[str, Any],
    sources: list[dict[str, Any]],
    plan: dict[str, Any],
    *,
    diagnostic: bool = False,
) -> None:
    """Carry immutable roster and quote semantics into batch and detail replays."""
    source_selection = study["identity"].get("source_selection")
    if source_selection is not None:
        ids = sorted(row["id"] for row in sources)
        if (
            len(set(ids)) != len(ids)
            or {row["venue"] for row in sources} != set(source_selection["expected_venues"])
            or not diagnostic
            and ids != source_selection["selected_source_ids"]
        ):
            raise _runner().Blocked("matrix batch differs from declared source selection")
        plan["source_selection"] = {**source_selection, "source_ids": ids}


def _cleanup_committed_batch(path: Path, study_sha256: str) -> None:
    runner = _runner()
    marker = path / "matrix-scratch.json"
    if (
        not marker.is_file()
        or path.is_symlink()
        or runner.read_json(marker) != {"study_sha256": study_sha256}
    ):
        raise ValueError("refusing to remove unowned batch scratch")
    shutil.rmtree(path)


def _audit_financial_catalog(
    catalog: ResultCatalog,
    study: dict[str, Any],
    sources: list[dict[str, Any]],
    window: tuple[int, int] | None,
    pulse: Callable[[], None],
) -> dict[str, Any]:
    """Independent Python re-read of final binary finances and original sources."""
    count = 0
    expected_records = _window_records(sources, window)
    for first, end, packed in catalog.connection.execute(
        "SELECT first_ordinal,end_ordinal,receipt FROM batches ORDER BY first_ordinal"
    ):
        receipt = msgpack.unpackb(packed, raw=False)
        summary = dict(receipt["summary"])
        summary.setdefault("configuration_count", end - first)
        canonical = hashlib.sha256(b"[")

        def rows(
            first: int = first, end: int = end, canonical: Any = canonical
        ) -> Iterator[dict[str, Any]]:
            for ordinal, _, payload, checksum in catalog.payloads(first, end):
                pulse()
                row = decode_result(payload, checksum)
                if ordinal != first:
                    canonical.update(b",")
                canonical.update(_canonical(row))
                yield row
            canonical.update(b"]")

        summary.pop("binary_configurations", None)
        summary["configurations"] = rows()
        configurations = [configuration_at(study["identity"], index) for index in range(first, end)]
        _verify_summary(
            summary,
            configurations,
            sources,
            full_history=window is None,
            expected_records=expected_records,
            binary_sha256=study["identity"].get("binary_sha256"),
            statistics_required=True,
        )
        expected_hash = summary.get("fingerprint", {}).get("configurations_sha256")
        if expected_hash is not None and expected_hash != canonical.hexdigest():
            raise ValueError("final binary financial fingerprint differs from engine")
        count += end - first
    rehashed = False
    if "preparation_sha256" in study["identity"]:
        from scripts.research_sweep_verify import _verify_source_hash

        with ThreadPoolExecutor(max_workers=study["identity"]["settings"]["audit_threads"]) as pool:
            for _ in pool.map(_verify_source_hash, sources):
                pulse()
        rehashed = True
    return {
        "configurations_checked": count,
        "financial_and_monthly_checks": True,
        "sources_sha256_reverified": rehashed,
        "source_count": len(sources),
    }


def _execute_batches(
    binary: Path,
    study: dict[str, Any],
    sources: list[dict[str, Any]],
    output: Path,
    *,
    resume_from: Path | None = None,
    window: tuple[int, int] | None = None,
) -> dict[str, Any]:
    runner = _runner()
    catalog_path = output / "results.sqlite"
    if resume_from:
        with closing(_readonly(resume_from / "results.sqlite")) as source:
            metadata = {
                key: msgpack.unpackb(value, raw=False)
                for key, value in source.execute("SELECT key,value FROM metadata")
            }
            if (
                metadata.get("schema") != CATALOG_SCHEMA
                or metadata.get("identity") != study["identity"]
            ):
                raise runner.Blocked("resume binary catalog identity/format differs")
            boundary = metadata["committed_boundary"]
            projected = runner.resource_observation(output)
            size = (resume_from / "results.sqlite").stat().st_size + boundary["bytes"]
            projected["artifact_bytes"] += size
            projected["free_disk_bytes"] -= size
            runner.check_resources(projected, runner.LIMITS)
            with closing(sqlite3.connect(catalog_path)) as target:
                source.backup(target)
            prefix_hash = hashlib.sha256()
            source_binary = resume_from / "results.bin"
            if source_binary.is_symlink():
                raise ValueError("binary catalog symlinks are not permitted")
            with (
                source_binary.open("rb") as original,
                (output / "results.bin").open("xb") as target_binary,
            ):
                remaining = boundary["bytes"]
                while remaining:
                    block = original.read(min(1024**2, remaining))
                    if not block:
                        raise ValueError("truncated committed binary prefix")
                    target_binary.write(block)
                    prefix_hash.update(block)
                    remaining -= len(block)
                target_binary.flush()
                os.fsync(target_binary.fileno())
            if prefix_hash.hexdigest() != boundary["sha256"]:
                raise ValueError("resume binary prefix checksum mismatch")
    catalog = ResultCatalog(catalog_path, study["identity"], create=not bool(resume_from))
    started = time.monotonic()
    configuration_count = _configuration_count(study)
    last_pulse = 0.0
    pending_bytes = 0
    observed: dict[str, int] = {}

    def pulse(reserve: int = 0) -> None:
        nonlocal last_pulse, pending_bytes, observed
        if time.monotonic() - last_pulse >= 1:
            observed = runner.resource_observation(runner._ACTIVE_JOB_ROOT or output, os.getpid())
            pending_bytes = 0
            last_pulse = time.monotonic()
        pending_bytes += reserve
        runner.check_resources(
            {
                **observed,
                "artifact_bytes": observed["artifact_bytes"] + pending_bytes,
                "free_disk_bytes": observed["free_disk_bytes"] - pending_bytes,
            },
            study["identity"]["runtime_limits"],
        )

    try:
        expected_records = _window_records(sources, window)
        saved_window = list(window) if window else None
        if resume_from:
            if catalog.metadata("window") != saved_window:
                raise runner.Blocked("resume historical window differs from saved catalog")
            if catalog.metadata("source_manifest_sha256") != _digest(sources):
                raise runner.Blocked("resume source roster differs from saved catalog")
        else:
            with catalog.connection:
                catalog.set_metadata("window", saved_window)
                catalog.set_metadata("source_manifest_sha256", _digest(sources))
        first = catalog.count()
        catalog.verify(first)
        batch_size = study["identity"]["batch_size"]
        runner.write_json(
            output / "progress.json",
            {
                "status": "RUNNING",
                "matrix_kind": MATRIX_KIND,
                "completed_configurations": first,
                "configuration_count": configuration_count,
                "source_count": len(sources),
                "batch_size": batch_size,
                "wall_seconds": time.monotonic() - started,
            },
            durable=False,
        )
        for begin in range(first, configuration_count, batch_size):
            # Reread code/binary/preparation identities before starting another owned child.
            current = _identity(
                Path(study["preparation_path"]),
                binary,
                study["identity"]["settings"],
                batch_size,
                study["identity"].get("matrix"),
                study["identity"].get("source_selection"),
            )
            if current != study["identity"]:
                raise runner.Blocked("matrix identities changed between batches")
            runner.check_resources(runner.resource_observation(output), runner.LIMITS)
            end = min(begin + batch_size, configuration_count)
            configs = [configuration_at(study["identity"], index) for index in range(begin, end)]
            batch = output / "batches" / f"{begin:06d}"
            batch.mkdir(parents=True)
            runner.write_json(
                batch / "matrix-scratch.json", {"study_sha256": study["study_sha256"]}
            )
            plan = _batch_plan(study, sources, configs, batch, window=window)
            if resume_from and begin == first:
                old_batch = resume_from / "batches" / f"{begin:06d}"
                if any(
                    (old_batch / "engine" / filename).is_file()
                    for filename in ("checkpoint.latest.json", "checkpoint.previous.json")
                ):
                    plan = runner.copy_checkpoint(binary, old_batch, batch)
                    if (
                        plan.get("configurations") != configs
                        or plan.get("matrix_study_sha256") != study["study_sha256"]
                    ):
                        raise runner.Blocked("saved partial batch differs from matrix study")
            runner.write_json(
                output / "active-batch.json",
                {
                    "first_ordinal": begin,
                    "end_ordinal": end,
                    "path": str(batch),
                    "study_sha256": study["study_sha256"],
                    "window": window,
                },
            )
            outcome = runner.engine_run(binary, plan, batch, output / "batch-progress.json")
            summary = outcome["summary"]
            compact = {key: value for key, value in outcome.items() if key != "summary"}
            compact["summary"] = {
                key: value for key, value in summary.items() if key != "configurations"
            }
            compact["study_sha256"] = study["study_sha256"]
            rows = _verified_results(
                summary,
                configs,
                sources,
                full_history=window is None,
                expected_records=expected_records,
                binary_sha256=study["identity"].get("binary_sha256"),
                statistics_required=True,
                result_dir=Path(outcome.get("result_dir", plan["output_dir"])),
                pulse=pulse,
            )
            catalog.append(begin, rows, compact, pulse=pulse)
            for _, config_id, payload, digest in catalog.payloads(begin, end):
                pulse()
                _verify_stored_payload(payload, digest, config_id)
            _cleanup_committed_batch(batch, study["study_sha256"])
            runner.write_json(
                output / "progress.json",
                {
                    "status": "RUNNING",
                    "matrix_kind": MATRIX_KIND,
                    "completed_configurations": end,
                    "configuration_count": configuration_count,
                    "source_count": len(sources),
                    "batch_size": batch_size,
                    "wall_seconds": time.monotonic() - started,
                },
                durable=False,
            )
        verification = catalog.verify(configuration_count)
        audit = _audit_financial_catalog(catalog, study, sources, window, pulse)
        current = _identity(
            Path(study["preparation_path"]),
            binary,
            study["identity"]["settings"],
            batch_size,
            study["identity"].get("matrix"),
            study["identity"].get("source_selection"),
        )
        if current != study["identity"]:
            raise runner.Blocked("matrix identities changed before final publication")
        verification = catalog.verify(configuration_count, complete=True)
        completed = {
            "status": "COMPLETE",
            "result_dir": str(output),
            "catalog": str(catalog_path),
            "binary_file": str(catalog.binary_path),
            "verification": verification,
            "independent_audit": audit,
            "configuration_count": configuration_count,
            "wall_seconds": time.monotonic() - started,
            "charts_available": False,
            "chart_mode": "on_demand",
            "binary_results": True,
            "window": list(window) if window else None,
            "matrix_kind": MATRIX_KIND,
            "study_sha256": study["study_sha256"],
            "source_count": len(sources),
            "processed_records_per_configuration": expected_records,
            "monthly_results_available": True,
            "statistics_policy": study["identity"]["statistics_policy"],
        }
        runner.write_json(output / "summary.json", completed)
        return completed
    finally:
        catalog.close()


def _validation(binary: Path, output: Path, study: dict[str, Any]) -> dict[str, Any]:
    from scripts.research_sweep_reference import fixture_sources, verify_result
    from scripts.research_sweep_statistics import calendar_fixtures, verify_performance

    runner = _runner()
    output.mkdir(parents=True)
    source_selection = study["identity"].get("source_selection")
    sources = fixture_sources(
        output / "fixtures", count=8, minutes=48, gap_free=source_selection is not None
    )
    if source_selection is not None:
        sources = [
            source for source in sources if source["venue"] in source_selection["expected_venues"]
        ]
    configs = [expanded_configuration(index) for index in (0, 1, 27, 28, 29, 783, 784, 423359)]
    if study["identity"].get("matrix", {}).get("configuration_plan_schema") == DEPTH_PLAN_SCHEMA:
        # Explicitly exercise every new lower boundary alongside the unchanged
        # old-policy fixtures, including sequential/parallel and restore paths.
        configs.extend(
            _depth_configuration(
                block * EXPANDED_CONFIGURATION_COUNT + _base_ordinal(12, 10, 8, 12)
            )
            for block in range(len(_DEPTH_BLOCKS))
        )
    checks = []
    baseline = None
    first_plan = None
    for threads in (1, 32):
        directory = output / f"oracle-{threads}"
        plan = _batch_plan(study, sources, configs, directory, diagnostic=True)
        plan.update(
            mode="validation",
            result_output="json",
            keep_results=True,
            trace=True,
            threads=threads,
            max_timestamp_groups=1,
            block_bytes=4096,
            chart_mode="on_demand",
        )
        outcome = runner.engine_run(binary, plan, directory)
        verified = verify_result(plan, Path(plan["output_dir"]))
        verified.update(verify_performance(plan, Path(plan["output_dir"])))
        _verify_summary(outcome["summary"], configs, sources, full_history=True)
        current = outcome["summary"]["configurations"]
        if baseline is not None and current != baseline:
            raise ValueError("aggregate TP results differ between sequential and parallel runs")
        if baseline is None:
            baseline, first_plan = current, plan
        checks.append({"name": f"fraction_oracle_{threads}_threads", "passed": True, **verified})
    if first_plan is None:
        raise ValueError("missing matrix validation baseline")
    pause = output / "paused"
    pause_plan = dict(
        first_plan, output_dir=str(pause / "engine"), checkpoint_seconds=0, stop_after_blocks=2
    )
    runner.engine_run(binary, pause_plan, pause, expected_codes=(3,))
    resumed = output / "resumed"
    resumed.mkdir()
    plan = runner.copy_checkpoint(binary, pause, resumed)
    plan["threads"] = 32
    outcome = runner.engine_run(binary, plan, resumed)
    if outcome["summary"]["configurations"] != baseline:
        raise ValueError("aggregate TP checkpoint continuation changed exact results")
    verified = verify_result(plan, Path(plan["output_dir"]))
    checks.append({"name": "aggregate_cycle_checkpoint_resume", "passed": True, **verified})
    # Prove compact output retains the same canonical pair digest and all finances.
    compact = output / "compact"
    compact_plan = dict(
        first_plan,
        output_dir=str(compact / "engine"),
        trace=False,
        keep_results=False,
        threads=32,
        result_output="framed_msgpack_v1",
    )
    compact_outcome = runner.engine_run(binary, compact_plan, compact)
    from scripts.research_sweep_binary import engine_results

    compact_rows = list(
        engine_results(Path(compact_outcome["result_dir"]), compact_outcome["summary"])
    )
    if compact_rows != baseline:
        raise ValueError("binary-storage preparation changed exact results")
    for row in baseline:
        payload, checksum = encode_result(row)
        if decode_result(payload, checksum) != row:
            raise ValueError("binary result codec changed an exact value")
    checks.append({"name": "compact_pair_fingerprint_and_binary_roundtrip", "passed": True})
    uncached = output / "uncached-trade-text"
    uncached_plan = dict(
        compact_plan,
        output_dir=str(uncached / "engine"),
        trade_render_cache=False,
        grid_level_cache=False,
    )
    uncached_outcome = runner.engine_run(binary, uncached_plan, uncached)
    if (
        list(engine_results(Path(uncached_outcome["result_dir"]), uncached_outcome["summary"]))
        != baseline
    ):
        raise ValueError("trade/grid derived caches changed exact results or hashes")
    # A fine tick widens the common monetary scale beyond int128, even while
    # observed prices are ordinary. Both cache bypass and exact large-number
    # accounting must remain equivalent to the independent Fraction oracle.
    wide = output / "wide-scale-cache"
    wide_source = {**sources[0], "rules": {**sources[0]["rules"], "price_tick": "1e-40"}}
    wide_plan = runner.make_plan(
        [wide_source], wide / "engine", "validation", configurations=[configs[-1]], threads=1
    )
    wide_plan.update(matrix_kind=MATRIX_KIND, chart_mode="on_demand")
    wide_outcome = runner.engine_run(binary, wide_plan, wide)
    wide_checked = verify_result(wide_plan, Path(wide_plan["output_dir"]))
    cache = wide_outcome["summary"].get("trade_render_cache", {})
    if not cache.get("bypasses", 0):
        raise ValueError("wide-scale fixture did not exercise exact cache bypass")
    checks.append(
        {
            "name": "derived_caches_exact_parity_and_large_scale_bypass",
            "passed": True,
            **wide_checked,
        }
    )
    # Legacy off/off retains every pair result and trade digest, with identical IDs.
    legacy = output / "legacy-off"
    legacy_config = {key: configs[0][key] for key in ("id", "step_pct", "tp_pct")}
    legacy_plan = runner.make_plan(
        sources, legacy / "engine", "validation", configurations=[legacy_config], threads=1
    )
    legacy_plan["chart_mode"] = "on_demand"
    legacy_outcome = runner.engine_run(binary, legacy_plan, legacy)
    extended_off = baseline[0]
    legacy_row = legacy_outcome["summary"]["configurations"][0]
    for field in ("total", "venues", "trade_hash"):
        if extended_off[field] != legacy_row[field]:
            raise ValueError(f"disabled aggregate TP changed legacy {field}")
    checks.append({"name": "disabled_aggregate_tp_legacy_parity", "passed": True})
    calendar_sources = calendar_fixtures(output / "calendar-fixtures")
    # Retain sparse seven-venue legacy regressions. These intentionally gapped
    # calendar fixtures are not evidence that the new source policy admits gaps.
    calendar_study = {
        **study,
        "identity": {
            key: value for key, value in study["identity"].items() if key != "source_selection"
        },
    }
    calendar_results = None
    calendar_plan = None
    for threads in (1, 32):
        directory = output / f"calendar-{threads}"
        plan = _batch_plan(calendar_study, calendar_sources, configs, directory, diagnostic=True)
        plan.update(
            mode="validation",
            result_output="json",
            keep_results=True,
            trace=True,
            threads=threads,
            max_timestamp_groups=1,
            block_bytes=4096,
        )
        outcome = runner.engine_run(binary, plan, directory)
        verified = verify_result(plan, Path(plan["output_dir"]))
        verified.update(verify_performance(plan, Path(plan["output_dir"])))
        current = outcome["summary"]["configurations"]
        if calendar_results is not None and calendar_results != current:
            raise ValueError("monthly statistics differ between serial and parallel runs")
        calendar_results, calendar_plan = current, plan
        for row in current:
            payload, checksum = encode_result(row)
            if decode_result(payload, checksum) != row:
                raise ValueError("monthly binary codec changed exact financial statistics")
        checks.append(
            {"name": f"calendar_fraction_oracle_{threads}_threads", "passed": True, **verified}
        )
    if calendar_plan is None:
        raise ValueError("missing calendar validation")
    paused = output / "calendar-paused"
    pause_plan = dict(
        calendar_plan, output_dir=str(paused / "engine"), stop_after_blocks=3, checkpoint_seconds=0
    )
    runner.engine_run(binary, pause_plan, paused, expected_codes=(3,))
    resumed = output / "calendar-resumed"
    resumed.mkdir()
    resumed_plan = runner.copy_checkpoint(binary, paused, resumed)
    outcome = runner.engine_run(binary, resumed_plan, resumed)
    if outcome["summary"]["configurations"] != calendar_results:
        raise ValueError("calendar checkpoint changed financial statistics")
    checked = verify_performance(resumed_plan, Path(resumed_plan["output_dir"]))
    checks.append({"name": "calendar_checkpoint_exact_restore", "passed": True, **checked})
    binary_calendar = output / "calendar-binary"
    binary_plan = dict(
        calendar_plan, output_dir=str(binary_calendar / "engine"), result_output="framed_msgpack_v1"
    )
    binary_outcome = runner.engine_run(binary, binary_plan, binary_calendar)
    if (
        list(engine_results(Path(binary_outcome["result_dir"]), binary_outcome["summary"]))
        != calendar_results
    ):
        raise ValueError("binary engine calendar output changed exact statistics")
    checks.append({"name": "calendar_binary_exact_parity", "passed": True})
    return {
        "checks": checks,
        "recovery": {"passed": True, "checks": [checks[2]]},
        "configurations": len(configs),
        "sources": len(sources),
        "scope": "short synthetic exact oracle, parallelism, binary roundtrip and recovery",
    }


def _representatives() -> list[dict[str, Any]]:
    """All 540 local-grid choices; both additional axes cover every level."""
    return [
        expanded_configuration(base * 784 + (base % 28) * 28 + ((base * 5 + base // 28) % 28))
        for base in range(540)
    ]


def _calibration(
    binary: Path, study: dict[str, Any], sources: list[dict[str, Any]], output: Path
) -> dict[str, Any]:
    runner = _runner()
    selected = runner.choose_sources(sources, min(64, len(sources)))
    window = runner.best_window(selected, 7)
    indices = _indices(study["identity"])
    count = min(study["identity"]["batch_size"], len(indices))
    configs = [
        configuration_at(study["identity"], index * (len(indices) - 1) // max(1, count - 1))
        for index in range(count)
    ]
    observations = []
    baseline = None
    for repeat in range(3):
        directory = output / f"repeat-{repeat}"
        plan = _batch_plan(study, selected, configs, directory, window=window)
        plan["max_records"] = 65536
        outcome = runner.engine_run(binary, plan, directory, output / "progress.json")
        _verify_summary(
            outcome["summary"],
            configs,
            selected,
            full_history=False,
            result_dir=Path(outcome["result_dir"]),
        )
        fingerprint = runner.result_fingerprint(outcome)
        if baseline is not None and fingerprint != baseline:
            raise ValueError("calibration repeat changed exact results")
        baseline = fingerprint
        observations.append(
            {
                "repeat": repeat,
                "replay_seconds": outcome["summary"]["timings"]["replay_seconds"],
                "records": outcome["summary"]["processed_records"],
                "fingerprint": fingerprint,
                "wall_seconds": outcome["wall_seconds"],
            }
        )
    return {
        "status": "PASS",
        "measurements": observations,
        "settings": study["identity"]["settings"],
        "sources": len(selected),
        "configurations": len(configs),
        "window": list(window),
        "scope": "three short warm-cache repeats; full batch from selected list",
        "technical_selection": "requested settings retained; no unmeasured optimum claimed",
    }


def _load_recovery(
    binary: Path, study: dict[str, Any], sources: list[dict[str, Any]], output: Path
) -> dict[str, Any]:
    """Restore a full-size batch and compare every result with uninterrupted replay."""
    runner = _runner()
    output.mkdir()
    selected_indices = _indices(study["identity"])
    count = min(study["identity"]["batch_size"], len(selected_indices))
    ordinals = [index * (len(selected_indices) - 1) // max(1, count - 1) for index in range(count)]
    indices = [selected_indices[index] for index in ordinals]
    configs = [configuration_at(study["identity"], ordinal) for ordinal in ordinals]
    continuous = output / "continuous"
    plan = _batch_plan(study, sources, configs, continuous)
    plan["mode"] = "validation"
    expected = runner.engine_run(binary, plan, continuous)
    if expected["summary"]["processed_blocks"] < 3:
        raise ValueError("load recovery history must span at least three input blocks")
    _verify_summary(
        expected["summary"],
        configs,
        sources,
        full_history=True,
        statistics_required=True,
        result_dir=Path(expected["result_dir"]),
    )
    paused = output / "paused"
    pause_plan = dict(
        plan,
        output_dir=str(paused / "engine"),
        checkpoint_seconds=0,
        stop_after_blocks=2,
    )
    runner.engine_run(binary, pause_plan, paused, expected_codes=(3,))
    checkpoints: dict[str, dict[str, Any]] = {}
    for name in ("checkpoint.previous.json", "checkpoint.latest.json"):
        path = paused / "engine" / name
        with path.open("rb") as handle:
            first = handle.readline(1024 * 1024)
        suffix = b',"configurations":[\n'
        if not first.endswith(suffix):
            raise ValueError("invalid bounded load checkpoint header")
        header = json.loads(first[: -len(suffix)] + b"}")
        if header.get("processed_blocks") != len(checkpoints) + 1:
            raise ValueError("load did not publish two successive safe checkpoints")
        checkpoints[name] = {"sha256": runner.sha256(path), "bytes": path.stat().st_size}
    resumed = output / "resumed"
    resumed.mkdir()
    restored_plan = runner.copy_checkpoint(binary, paused, resumed)
    restored_plan["checkpoint_seconds"] = 900
    actual = runner.engine_run(binary, restored_plan, resumed)
    _verify_summary(
        actual["summary"],
        configs,
        sources,
        full_history=True,
        statistics_required=True,
        result_dir=Path(actual["result_dir"]),
    )
    if runner.result_fingerprint(actual) != runner.result_fingerprint(expected):
        raise ValueError("full-market load recovery changed exact configuration results")
    for field in ("processed_records", "last_ms"):
        if actual["summary"][field] != expected["summary"][field]:
            raise ValueError(f"full-market load recovery changed exact {field}")
    proof = {
        "passed": True,
        "source_count": len(sources),
        "configuration_count": count,
        "configuration_ordinals_sha256": _digest(indices),
        "processed_records": expected["summary"]["processed_records"],
        "checkpoint_count": len(checkpoints),
        "checkpoints": checkpoints,
        "results_sha256": runner.result_fingerprint(actual),
        "scope": "full-size batch; exact uninterrupted/resumed finances and monthly statistics",
    }
    runner.write_json(output / "verification.json", proof)
    return proof


def _check_complete_matrix_load(
    output: Path, sources: list[dict[str, Any]], policy: dict[str, Any]
) -> dict[str, Any]:
    """A sample, missing batch, truncated stream or short run cannot pass this gate."""
    expected_count = policy["configuration_count"]
    records = sum(source["records"] for source in sources)
    replay = 0.0
    peak_rss = 0
    end = batches = 0
    with closing(_readonly(output / "results.sqlite")) as catalog:
        metadata = {
            key: msgpack.unpackb(value, raw=False)
            for key, value in catalog.execute("SELECT key,value FROM metadata")
        }
        if (
            metadata.get("complete") is not True
            or metadata.get("source_manifest_sha256") != _digest(sources)
            or catalog.execute("SELECT count(*) FROM configurations").fetchone()[0]
            != expected_count
        ):
            raise ValueError("load catalog lacks the complete matrix or expected source roster")
        for first, following, packed, checksum in catalog.execute(
            "SELECT first_ordinal,end_ordinal,receipt,sha256 FROM batches ORDER BY first_ordinal"
        ):
            if first != end or following <= first or hashlib.sha256(packed).digest() != checksum:
                raise ValueError("load batch coverage or receipt checksum differs")
            receipt = msgpack.unpackb(packed, raw=False)
            summary = receipt["summary"]
            if (
                summary["configuration_count"] != following - first
                or summary["source_count"] != len(sources)
                or summary["processed_records"] != records
                or summary.get("duration_stopped", False)
            ):
                raise ValueError("load batch omitted configurations, sources or input records")
            seconds = summary["timings"]["replay_seconds"]
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError("load batch has an invalid observed replay duration")
            replay += seconds
            peak_rss = max(peak_rss, receipt.get("observed_peak_rss_bytes", 0))
            end = following
            batches += 1
    if end != expected_count:
        raise ValueError("load did not execute every expanded-matrix configuration")
    if replay < policy["minimum_replay_seconds"]:
        raise ValueError("complete-matrix load did not reach minimum observed replay duration")
    return {
        "configuration_count": end,
        "batch_count": batches,
        "processed_records_per_configuration": records,
        "replay_seconds": replay,
        "observed_peak_rss_bytes": peak_rss,
    }


def _endurance(
    binary: Path, study: dict[str, Any], sources: list[dict[str, Any]], output: Path
) -> dict[str, Any]:
    from scripts.research_sweep_reference import fixture_sources

    runner = _runner()
    policy = _policy(study["identity"])
    if study["identity"]["acceptance_policy"] != MATRIX_ACCEPTANCE_POLICY:
        raise runner.Blocked("load study does not bind selected-matrix policy")
    started = time.monotonic()
    bars = policy["fixture_bars"]
    attempts: list[dict[str, Any]] = []
    while True:
        directory = output if not attempts else output / f"attempt-{bars}"
        directory.mkdir(parents=True, exist_ok=True)
        projected = runner.resource_observation(runner._ACTIVE_JOB_ROOT or output)
        size = len(sources) * bars * 24
        projected["artifact_bytes"] += size
        projected["free_disk_bytes"] -= size
        runner.check_resources(projected, runner.LIMITS)
        fixtures = fixture_sources(
            directory / "synthetic",
            count=len(sources),
            minutes=bars,
            exact_records=True,
            gap_free="source_selection" in study["identity"],
        )
        if any(source["records"] != bars for source in fixtures):
            raise ValueError("load requires the exact declared record count on every source")
        synthetic = [
            {**fixture, **{key: source[key] for key in ("id", "venue", "symbol", "rules")}}
            for source, fixture in zip(sources, fixtures, strict=True)
        ]
        manifest = {
            "source_kind": "synthetic_load_prices_with_admitted_pair_roster_and_rules",
            "study_sha256": study["study_sha256"],
            "acceptance_policy": policy,
            "records_per_source": bars,
            "sources": synthetic,
        }
        runner.write_json(directory / "load-manifest.json", manifest)
        completed = _execute_batches(binary, study, synthetic, directory)
        measured = _check_complete_matrix_load(
            directory, synthetic, {**policy, "minimum_replay_seconds": 0}
        )
        enough = measured["replay_seconds"] >= policy["minimum_replay_seconds"]
        attempts.append(
            {
                "directory": str(directory),
                "fixture_bars": bars,
                **measured,
                "status": "DURATION_REACHED" if enough else "BELOW_MINIMUM_DURATION",
            }
        )
        runner.write_json(output / "load-attempts.json", {"attempts": attempts})
        if enough:
            break
        if bars == policy["maximum_fixture_bars"]:
            raise ValueError(
                "selected-matrix load did not reach 600 seconds at maximum input volume"
            )
        bars = min(bars * 2, policy["maximum_fixture_bars"])
    runner.write_json(
        output / "progress.json",
        {
            "status": "RUNNING",
            "phase": "full_batch_checkpoint_recovery",
            "configuration_count": _configuration_count(study),
            "completed_configurations": _configuration_count(study),
            "source_count": len(sources),
            "batch_size": study["identity"]["batch_size"],
        },
        durable=False,
    )
    recovery = _load_recovery(binary, study, synthetic, output / "recovery")
    if not recovery.get("passed") or recovery["checkpoint_count"] < policy["minimum_checkpoints"]:
        raise ValueError("selected-matrix load lacks verified recovery and two checkpoints")
    verified = _check_complete_matrix_load(directory, synthetic, policy)
    return {
        **completed,
        **verified,
        "sources": len(sources),
        "configurations": _configuration_count(study),
        "wall_seconds": time.monotonic() - started,
        "checkpoint_count": recovery["checkpoint_count"],
        "acceptance_policy": policy,
        "load_manifest_sha256": runner.sha256(directory / "load-manifest.json"),
        "source_kind": manifest["source_kind"],
        "attempts": attempts,
        "scope": "all selected configurations and admitted pairs/rules; synthetic prices",
        "duration_scope": "sum of accepted complete-batch replay times; recovery excluded",
        "recovery": recovery,
    }


def _cached_chart(output: Path, cache_key: str, identity: str) -> dict[str, Any] | None:
    runner = _runner()
    permitted = {"summary.json", "pairs.jsonl", "pairs.index.json", "series.jsonl"}
    for index, candidate in enumerate(output.parent.iterdir()):
        if index >= 1000:
            break
        if (
            candidate == output
            or candidate.is_symlink()
            or not (candidate / "receipt.json").is_file()
        ):
            continue
        try:
            receipt = runner.read_json(candidate / "receipt.json")
            if (
                receipt.get("status") != "COMPLETE"
                or receipt.get("chart_cache_key") != cache_key
                or receipt.get("parent_result_identity") != identity
            ):
                continue
            saved = Path(receipt["result_dir"])
            hashes = receipt["chart_artifact_sha256"]
            if (
                saved.resolve() != (candidate / "engine").resolve()
                or saved.is_symlink()
                or set(hashes) != permitted
            ):
                continue
            size = sum((saved / name).stat().st_size for name in hashes)
            if size > 128 * 1024**2:
                continue
            if any(
                (saved / name).is_symlink() or runner.sha256(saved / name) != digest
                for name, digest in hashes.items()
            ):
                continue
            observed = runner.resource_observation(output)
            observed["artifact_bytes"] += size
            observed["free_disk_bytes"] -= size
            runner.check_resources(observed, runner.LIMITS)
            target = output / "engine"
            target.mkdir()
            for name in hashes:
                shutil.copyfile(saved / name, target / name)
            return {
                "status": "COMPLETE",
                "result_dir": str(target),
                "config_id": receipt["config_id"],
                "charts_available": True,
                "chart_mode": "eager",
                "chart_source": str(target / "series.jsonl"),
                "parent_result_identity": identity,
                "chart_cache_key": cache_key,
                "chart_artifact_sha256": hashes,
                "cache_hit": True,
            }
        except (OSError, ValueError, KeyError):
            continue
    return None


def build_chart(
    binary: Path,
    request: dict[str, Any],
    output: Path,
    study: dict[str, Any],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    runner = _runner()
    parent = Path(request["source_output"])
    receipt = runner.read_json(parent / "receipt.json")
    if receipt.get("status") != "COMPLETE":
        raise runner.Blocked("details require a completed binary matrix")
    selected = page_results(parent, config_id=request["config_id"])["configurations"]
    if len(selected) != 1:
        raise ValueError("unknown matrix configuration")
    expected = selected[0]
    scope = request.get("scope", "all")
    scopes = {
        "all",
        *["venue:" + source["venue"] for source in sources],
        *["pair:" + source["id"] for source in sources],
    }
    if scope not in scopes:
        raise ValueError("unknown matrix detail scope")
    with closing(_readonly(parent / "results.sqlite")) as connection:
        ordinal = connection.execute(
            "SELECT ordinal FROM configurations WHERE config_id=?", (expected["id"],)
        ).fetchone()[0]
    # C++ plans accept canonical decimal inputs; summary fields are exact ratios.
    config = configuration_at(study["identity"], ordinal)
    window = tuple(receipt["window"]) if receipt.get("window") else None
    identity = _digest(
        {"study_sha256": study["study_sha256"], "result": expected, "window": window}
    )
    cache_key = _digest([identity, scope if scope.startswith("pair:") else "aggregate"])
    cached = _cached_chart(output, cache_key, identity)
    if cached:
        return {**cached, "scope": scope}
    plan = _batch_plan(study, sources, [config], output, window=window)
    plan["result_output"] = "json"
    plan.update(mode="validation", keep_results=True, chart_mode="eager", threads=1)
    if scope.startswith("pair:"):
        plan["chart_pair_source_id"] = scope.removeprefix("pair:")
    outcome = runner.engine_run(binary, plan, output, output / "progress.json")
    _verify_summary(
        outcome["summary"],
        [config],
        sources,
        full_history=window is None,
        expected_records=_window_records(sources, window),
        binary_sha256=study["identity"].get("binary_sha256"),
    )
    if outcome["summary"]["configurations"] != selected:
        raise ValueError("detail replay differs from original exact results or pair hashes")
    result = Path(outcome["result_dir"])
    # Scope filters only presentation. The replay always contains the original full roster.
    artifacts = {
        name: runner.sha256(result / name)
        for name in ("summary.json", "pairs.jsonl", "pairs.index.json", "series.jsonl")
    }
    return {
        "status": "COMPLETE",
        "result_dir": str(result),
        "config_id": config["id"],
        "scope": scope,
        "charts_available": True,
        "chart_mode": "eager",
        "chart_source": str(result / "series.jsonl"),
        "parent_result_identity": identity,
        "chart_cache_key": cache_key,
        "chart_artifact_sha256": artifacts,
        "cache_hit": False,
    }


def _run_matrix(request: dict[str, Any], output: Path) -> dict[str, Any]:
    """Dispatch under research_sweep.worker's existing lock/resource ownership."""
    runner = _runner()
    from scripts.research_sweep_sources import CURRENT_RULES_SCHEMA, FLAT_HOUR_SCHEMA

    binary = Path(request.get("binary", runner.DEFAULT_MATRIX_BINARY)).resolve()
    action = request["action"]
    skip_endurance = request.get("skip_endurance", False)
    if type(skip_endurance) is not bool or (
        skip_endurance and action not in ("matrix-full", "matrix-resume")
    ):
        raise ValueError("skip_endurance applies only to matrix-full or matrix-resume")
    if action == "matrix-detail":
        from scripts.results_viewer_jobs import matrix_detail

        return matrix_detail(binary, request, output)
    if not binary.is_file():
        raise runner.Blocked("matrix executable is missing")
    study, sources = _study(request, output, binary)
    if study.get("artifact_root"):
        runner._ACTIVE_JOB_ROOT = Path(study["artifact_root"])
        if not output.resolve().is_relative_to(runner._ACTIVE_JOB_ROOT):
            raise ValueError("stage output must be inside its declared artifact root")
    parent = Path(request["source_output"]) if request.get("source_output") else None
    prepared = (
        runner.read_json(Path(study["preparation_path"])) if study.get("source_selection") else {}
    )
    if action == "matrix-resume" and parent is not None:
        previous_path = parent / "receipt.json"
        if not previous_path.is_file():
            previous_path = parent / "admission.json"
        previous_waivers = runner.read_json(previous_path).get("waived_stages", {})
        if not isinstance(previous_waivers, dict):
            raise runner.Blocked("original matrix waiver is malformed")
        original_skip = previous_waivers.get("matrix-endurance") == "WAIVED_BY_OWNER"
        if "skip_endurance" in request and skip_endurance != original_skip:
            raise runner.Blocked("resume endurance waiver differs from original run")
        skip_endurance = original_skip
    if skip_endurance and prepared.get("schema") != FLAT_HOUR_SCHEMA:
        raise runner.Blocked("endurance waiver applies only to the hour-filter selection")
    evidence = _evidence(parent, study)
    result = {
        "matrix_kind": MATRIX_KIND,
        "study_sha256": study["study_sha256"],
        "matrix_study": str(output / "matrix-study.json"),
        "preparation_path": study["preparation_path"],
        "preparation_sha256": study["identity"]["preparation_sha256"],
        "binary_sha256": study["identity"]["binary_sha256"],
        "code_sha256": study["identity"]["code_sha256"],
        "evidence": evidence,
        "settings": study["identity"]["settings"],
        "matrix": study["identity"].get("matrix", MATRIX_DESCRIPTION),
        "statistics_policy": study["identity"]["statistics_policy"],
        "acceptance_policy": _policy(study["identity"]),
    }
    if skip_endurance:
        result["waived_stages"] = {"matrix-endurance": "WAIVED_BY_OWNER"}
    runner.write_json(output / "admission.json", {**result, "action": action})
    if action == "matrix-prepare":
        return {**result, "status": "READY", "configuration_count": _configuration_count(study)}
    if action == "matrix-chart":
        return {**result, **build_chart(binary, request, output, study, sources)}
    if action == "matrix-validate":
        checked = _validation(binary, output / "validation", study)
        return {**result, **checked, "status": "PASS"}
    prerequisites: tuple[str, ...] = (
        "matrix-validate",
        "matrix-calibrate",
        "matrix-endurance",
        "matrix-rehearsal",
    )
    if study.get("source_selection") and prepared.get("schema") in (
        CURRENT_RULES_SCHEMA,
        FLAT_HOUR_SCHEMA,
    ):
        # The owner explicitly waived a week-long rehearsal for this selected study.
        prerequisites = prerequisites[:-1]
    if skip_endurance:
        prerequisites = tuple(stage for stage in prerequisites if stage != "matrix-endurance")
    requirements = {
        "matrix-calibrate": prerequisites[:1],
        "matrix-endurance": prerequisites[:2],
        "matrix-rehearsal": prerequisites[:3],
        "matrix-full": prerequisites,
        "matrix-resume": prerequisites,
    }
    window = None
    resumed_stage = "matrix-full"
    if action == "matrix-resume":
        if parent is None:
            raise ValueError("matrix-resume requires original job")
        previous_receipt = parent / "receipt.json"
        if not previous_receipt.is_file():
            previous_receipt = parent / "admission.json"
        old = runner.read_json(previous_receipt)
        resumed_stage = old.get("completed_stage", old.get("action"))
        if resumed_stage == "matrix-resume":
            resumed_stage = old.get("resume_stage", "matrix-full")
        if resumed_stage not in ("matrix-full", "matrix-rehearsal"):
            raise ValueError("source job is not an interrupted matrix replay")
        if skip_endurance and resumed_stage != "matrix-full":
            raise runner.Blocked("endurance waiver cannot resume a rehearsal")
        requirements[action] = requirements[resumed_stage]
        window = tuple(old["window"]) if old.get("window") else None
        if resumed_stage == "matrix-rehearsal" and (
            window is None
            or len(window) != 2
            or any(type(point) is not int for point in window)
            or window[0] >= window[1]
        ):
            raise runner.Blocked("interrupted rehearsal has no valid bounded replay window")
    if (
        action == "matrix-full" or (action == "matrix-resume" and resumed_stage == "matrix-full")
    ) and study.get("source_selection"):
        admitted = prepared.get("research_readiness") == "APPROVED_FOR_FULL_REPLAY" or (
            prepared.get("schema") in (CURRENT_RULES_SCHEMA, FLAT_HOUR_SCHEMA)
            and prepared.get("research_readiness") == "CURRENT_PUBLIC_RULES_ASSUMPTION"
        )
        if not admitted:
            raise runner.Blocked(
                "full replay blocked by unresolved source/rule/statistics review: "
                + str(prepared.get("research_readiness", "UNKNOWN"))
            )
    if action in requirements:
        missing = [name for name in requirements[action] if name not in evidence]
        if missing:
            raise runner.Blocked("missing matching expanded-matrix stages: " + ", ".join(missing))
    if action == "matrix-calibrate":
        return {**result, **_calibration(binary, study, sources, output)}
    if action == "matrix-endurance":
        return {**result, **_endurance(binary, study, sources, output)}
    if action in ("matrix-full", "matrix-resume", "matrix-rehearsal"):
        if action == "matrix-rehearsal":
            window = runner.best_window(sources, 7)
        runner.write_json(
            output / "admission.json",
            {
                **result,
                "action": action,
                "window": list(window) if window else None,
                "resume_stage": resumed_stage if action == "matrix-resume" else action,
            },
        )
        resumed = parent if action == "matrix-resume" else None
        return {
            **result,
            **_execute_batches(binary, study, sources, output, resume_from=resumed, window=window),
            "completed_stage": resumed_stage if action == "matrix-resume" else action,
        }
    raise ValueError("unsupported matrix stage")


def run_matrix(request: dict[str, Any], output: Path) -> dict[str, Any]:
    try:
        return _run_matrix(request, output)
    except sqlite3.Error as error:
        raise ValueError("binary result catalog transaction or integrity failure") from error
