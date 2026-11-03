"""Strict research bundle with the existing allowlisted Decimal/domain codec."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from grid.domain.model import Context, Scope
from grid.domain.numbers import DomainError
from grid.persistence import codec
from grid.research.history import Source
from grid.research.replay import Allocation, Capital, Replay


def source_object(source: Source) -> dict[str, object]:
    return {
        "path": str(source.path),
        "venue": source.venue,
        "symbol": source.symbol,
        "offset": source.offset,
        "count": source.count,
        "format_evidence": source.format_evidence,
        "identity_evidence": source.identity_evidence,
        "collection_evidence": source.collection_evidence,
        "unleveraged_spot": source.unleveraged_spot,
    }


def bundle(spec: Replay) -> dict[str, object]:
    return {
        "schema": "grid-replay-config-v1",
        "start_ms": spec.start_ms,
        "end_ms": spec.end_ms,
        "code_revision": spec.code_revision,
        "model": spec.model,
        "latency_bars": spec.latency_bars,
        "interval_bars": spec.interval_bars,
        "synthetic_spread": codec.amount(spec.synthetic_spread),
        "allocations": [
            {
                "scope": codec.dumps(a.scope),
                "context": codec.dumps(a.context),
                "source": source_object(a.source),
                "quote_cash": codec.amount(a.quote_cash),
                "fee_balances": codec.dumps(a.fee_balances),
            }
            for a in spec.allocations
        ],
        "capital": [
            {
                "venue": p.venue,
                "account": p.account,
                "currency": p.currency,
                "amount": codec.amount(p.amount),
            }
            for p in spec.capital
        ],
    }


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise DomainError("duplicate research configuration field")
    return result


def _fields(value: Any, expected: str) -> None:
    if not isinstance(value, dict) or set(value) != set(expected.split()):
        raise DomainError("research configuration fields differ from schema")


def load(path: Path) -> Replay:
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise DomainError("research config exceeds 1 MiB")
    try:
        data = json.loads(raw, object_pairs_hook=_pairs)
        _fields(
            data,
            "schema start_ms end_ms code_revision model latency_bars interval_bars "
            "synthetic_spread allocations capital",
        )
        if data["schema"] != "grid-replay-config-v1":
            raise DomainError("unknown research config schema")
        allocations: list[Allocation] = []
        if not isinstance(data["allocations"], list) or len(data["allocations"]) > 64:
            raise DomainError("bounded allocations required")
        if not isinstance(data["capital"], list) or len(data["capital"]) > 192:
            raise DomainError("bounded capital pools required")
        for a in data["allocations"]:
            _fields(a, "scope context source quote_cash fee_balances")
            s = a["source"]
            _fields(
                s,
                "path venue symbol offset count format_evidence identity_evidence "
                "collection_evidence unleveraged_spot",
            )
            source = Source(
                Path(s["path"]),
                s["venue"],
                s["symbol"],
                s["offset"],
                s["count"],
                s["format_evidence"],
                s["identity_evidence"],
                s["collection_evidence"],
                s["unleveraged_spot"],
            )
            context = codec.loads(a["context"], Context)
            allocations.append(
                Allocation(
                    codec.loads(a["scope"], Scope),
                    source,
                    context,
                    codec.parse_amount(a["quote_cash"]),
                    codec.loads(a["fee_balances"], tuple),
                )
            )
        capital: list[Capital] = []
        for p in data["capital"]:
            _fields(p, "venue account currency amount")
            capital.append(
                Capital(p["venue"], p["account"], p["currency"], codec.parse_amount(p["amount"]))
            )
        return Replay(
            tuple(allocations),
            tuple(capital),
            data["start_ms"],
            data["end_ms"],
            data["code_revision"],
            data["model"],
            data["latency_bars"],
            data["interval_bars"],
            codec.parse_amount(data["synthetic_spread"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        raise DomainError(f"invalid research configuration: {exc}") from exc
