"""Explicit bounded study plan; monetary parameters use decimal strings."""

import json
from pathlib import Path

from grid.domain.numbers import DomainError
from grid.persistence.codec import parse_amount
from grid.research.artifacts import canonical
from grid.research.config import _fields, _pairs
from grid.research.study import Candidate, Fold, Plan, Scenario


def bundle(plan: Plan) -> dict[str, object]:
    return {"schema": "grid-study-plan-v1", **json.loads(canonical(plan))}


def load(path: Path) -> Plan:
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise DomainError("study plan exceeds 1 MiB")
    try:
        data = json.loads(raw, object_pairs_hook=_pairs)
        _fields(data, "schema candidates scenarios folds universe_evidence regime_evidence")
        if data["schema"] != "grid-study-plan-v1":
            raise DomainError("unknown study plan schema")
        for name, maximum in (("candidates", 16), ("scenarios", 8), ("folds", 8)):
            if not isinstance(data[name], list) or not 1 <= len(data[name]) <= maximum:
                raise DomainError("bounded study members required")
        candidates = []
        for row in data["candidates"]:
            _fields(row, "name spacing")
            candidates.append(Candidate(row["name"], parse_amount(row["spacing"])))
        scenarios = []
        for row in data["scenarios"]:
            _fields(row, "name model latency_bars buy_fee_rate sell_fee_rate")
            scenarios.append(
                Scenario(
                    row["name"],
                    row["model"],
                    row["latency_bars"],
                    parse_amount(row["buy_fee_rate"]),
                    parse_amount(row["sell_fee_rate"]),
                )
            )
        folds = []
        for row in data["folds"]:
            _fields(
                row,
                "name train_start_ms train_end_ms test_start_ms test_end_ms "
                "train_regime test_regime",
            )
            folds.append(Fold(**row))
        return Plan(
            tuple(candidates),
            tuple(scenarios),
            tuple(folds),
            data["universe_evidence"],
            data["regime_evidence"],
        )
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        raise DomainError("invalid study configuration") from exc
