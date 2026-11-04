"""Canonical, bounded, allowlisted data codec. Never pickle or import stored names."""

from __future__ import annotations

import hashlib
import json
import types
from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, TypeAliasType, Union, cast, get_args, get_origin, get_type_hints

from grid.domain import market, model
from grid.domain.numbers import DomainError, decimal
from grid.persistence.records import Outcome
from grid.universe.model import (
    BuyAdmission,
    MarketKey,
    Membership,
    SelectionMode,
    UniversePolicy,
    UniverseSnapshot,
)

_CLASSES = (
    market.Precision,
    market.Limits,
    market.Market,
    market.Money,
    market.FeeRule,
    model.Scope,
    model.Policy,
    model.Book,
    model.Context,
    model.ActualFee,
    model.Lot,
    model.Order,
    model.Start,
    model.Fill,
    model.OrderReport,
    model.SubmissionUnknown,
    model.CancelAcknowledged,
    model.Control,
    model.Reconciled,
    model.PlaceLimit,
    model.Cancel,
    model.Reconcile,
    model.State,
    model.Transition,
    Outcome,
    MarketKey,
    UniversePolicy,
    Membership,
    UniverseSnapshot,
    BuyAdmission,
)
_RECORDS = {kind.__name__: kind for kind in _CLASSES}
_ENUMS = {
    kind.__name__: kind
    for kind in (
        market.PrecisionMode,
        model.Side,
        model.Phase,
        model.Mode,
        SelectionMode,
    )
}
MAX_BYTES = 16 * 1024 * 1024


def amount(value: Decimal) -> str:
    decimal(value, signed=True)
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if value == 0 else text


def parse_amount(value: object) -> Decimal:
    if not isinstance(value, str) or not 1 <= len(value) <= 200:
        raise DomainError("invalid stored decimal")
    try:
        number = Decimal(value)
        if number.is_finite():
            sign, digits, exponent = number.as_tuple()
            if not isinstance(exponent, int):
                raise DomainError("invalid decimal exponent")
            while len(digits) > 1 and digits[-1] == 0:
                digits, exponent = digits[:-1], exponent + 1
            number = Decimal((sign, digits, exponent))
        if amount(number) != value:
            raise DomainError("noncanonical stored decimal")
        return number
    except ArithmeticError as exc:
        raise DomainError("invalid stored decimal") from exc


def _matches(value: object, hint: Any) -> bool:
    if isinstance(hint, TypeAliasType):
        return _matches(value, hint.__value__)
    origin, args = get_origin(hint), get_args(hint)
    if origin in (Union, types.UnionType):
        return any(_matches(value, part) for part in args)
    if origin is Literal:
        return any(type(value) is type(part) and value == part for part in args)
    if origin is tuple:
        return isinstance(value, tuple) and (
            all(_matches(item, args[0]) for item in value)
            if len(args) == 2 and args[1] is Ellipsis
            else len(value) == len(args)
            and all(_matches(item, part) for item, part in zip(value, args, strict=True))
        )
    return type(value) is hint


def _encode(value: object, depth: int = 0) -> object:
    if depth > 32:
        raise DomainError("snapshot nesting bound exceeded")
    if value is None or type(value) in (bool, int, str):
        if isinstance(value, str) and len(value) > 4096:
            raise DomainError("snapshot string bound exceeded")
        if type(value) is int and not -(2**63) <= value < 2**63:
            raise DomainError("snapshot integer bound exceeded")
        return value
    if isinstance(value, Decimal):
        return {"decimal": amount(value)}
    if isinstance(value, StrEnum) and type(value).__name__ in _ENUMS:
        return {"enum": type(value).__name__, "value": str(value)}
    if isinstance(value, tuple):
        if len(value) > 100000:
            raise DomainError("snapshot collection bound exceeded")
        return {"tuple": [_encode(item, depth + 1) for item in value]}
    if is_dataclass(value) and not isinstance(value, type) and type(value) in _CLASSES:
        hints = get_type_hints(type(value))
        values = {}
        for field in fields(value):
            item = getattr(value, field.name)
            if not _matches(item, hints[field.name]):
                raise DomainError("snapshot field type mismatch")
            values[field.name] = _encode(item, depth + 1)
        return {"type": type(value).__name__, "fields": values}
    raise DomainError("unsupported snapshot value")


def dumps(value: object, *, limit: int = MAX_BYTES) -> str:
    text = json.dumps(_encode(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(text) > limit:
        raise DomainError("snapshot byte bound exceeded")
    return text


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise DomainError("duplicate snapshot key")
    return result


def _decode(value: Any, depth: int = 0) -> object:
    if depth > 32:
        raise DomainError("snapshot nesting bound exceeded")
    if value is None or type(value) in (bool, int, str):
        _encode(value)
        return value
    if not isinstance(value, dict):
        raise DomainError("invalid snapshot node")
    if set(value) == {"decimal"}:
        return parse_amount(value["decimal"])
    if set(value) == {"tuple"} and isinstance(value["tuple"], list):
        if len(value["tuple"]) > 100000:
            raise DomainError("snapshot collection bound exceeded")
        return tuple(_decode(item, depth + 1) for item in value["tuple"])
    if set(value) == {"enum", "value"}:
        kind = _ENUMS.get(value["enum"])
        if kind is not None:
            return kind(value["value"])
    if set(value) == {"type", "fields"}:
        record = _RECORDS.get(value["type"])
        raw = value["fields"]
        if record is not None and isinstance(raw, dict):
            if set(raw) != {field.name for field in fields(record)}:
                raise DomainError("snapshot fields differ from schema")
            decoded = {key: _decode(item, depth + 1) for key, item in raw.items()}
            hints = get_type_hints(record)
            if not all(_matches(item, hints[key]) for key, item in decoded.items()):
                raise DomainError("snapshot field type mismatch")
            constructor = cast(Any, record)
            result = constructor(
                **{field.name: decoded[field.name] for field in fields(record) if field.init}
            )
            if _encode(result) != value:
                raise DomainError("snapshot normalization changed stored facts")
            return result
    raise DomainError("unknown snapshot type")


def loads[T](text: str, expected: type[T], *, limit: int = MAX_BYTES) -> T:
    if not isinstance(text, str) or len(text) > limit:
        raise DomainError("snapshot byte bound exceeded")
    try:
        value = _decode(json.loads(text, object_pairs_hook=_pairs))
        if type(value) is not expected:
            raise DomainError("unexpected snapshot type")
        if dumps(value, limit=limit) != text:
            raise DomainError("noncanonical snapshot")
        return value
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise DomainError("invalid stored snapshot") from exc


def digest(value: object) -> str:
    return hashlib.sha256(dumps(value).encode("ascii")).hexdigest()
