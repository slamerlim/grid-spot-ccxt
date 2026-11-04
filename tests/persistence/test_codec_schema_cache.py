"""Bounded code-schema caching preserves canonical bytes and per-payload checks."""

from collections import Counter
from typing import Any, cast, get_type_hints

import pytest

from grid.domain.model import Context, State
from grid.domain.numbers import DomainError
from grid.persistence import codec


def test_fixed_schema_resolves_once_without_caching_payloads(
    state: State,
    context: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = get_type_hints
    calls: Counter[type[Any]] = Counter()

    def observed(record: type[Any]) -> dict[str, Any]:
        calls[record] += 1
        return original(record)

    codec._hints.cache_clear()
    monkeypatch.setattr(codec, "get_type_hints", observed)
    try:
        before = codec.dumps((state, context))
        for _ in range(3):
            assert codec.loads(before, tuple) == (state, context)
            assert codec.dumps((state, context)) == before
        assert calls and set(calls.values()) == {1}
        info = codec._hints.cache_info()
        assert info.currsize == len(calls) <= len(codec._CLASSES)
        assert info.maxsize == len(codec._CLASSES)
        with pytest.raises(TypeError):
            cast(dict[str, Any], codec._hints(State))["quote_cash"] = str
        with pytest.raises(DomainError, match="unsupported snapshot schema"):
            codec._hints(dict)
        assert codec._hints.cache_info().currsize == info.currsize
    finally:
        codec._hints.cache_clear()


def test_warm_schema_never_accepts_mutated_or_unknown_financial_payload(state: State) -> None:
    valid = codec.dumps(state)
    for corrupted in (
        valid.replace('"next_id":1', '"next_id":true'),
        valid.replace('"quote_cash":{"decimal":"1000"}', '"quote_cash":1000'),
        valid.replace('"type":"State"', '"type":"UntrustedFinancialType"'),
    ):
        assert corrupted != valid
        with pytest.raises(DomainError):
            codec.loads(corrupted, State)
    assert codec.loads(valid, State) == state
