"""Transport limits exercised without opening Internet sockets."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from grid.exchange.adapter import ReadLimits
from grid.exchange.ccxt_transport import HTTPWire, PublicCCXT, WireReply
from grid.exchange.models import Failure, ReadError


def test_http_reads_bounded_chunks_disables_redirects_and_closes(
    monkeypatch: pytest.MonkeyPatch,
    async_sockets: None,
) -> None:
    class Content:
        read_chunks = 0

        async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
            assert size == 16 * 1024
            for _ in range(10):
                self.read_chunks += 1
                yield b"x" * 128

    class Response:
        status = 200
        content = Content()
        exited = False

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *args: object) -> None:
            self.exited = True

    class Session:
        closed = False
        response = Response()

        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["trust_env"] is False
            assert kwargs["cookie_jar"].__class__.__name__ == "DummyCookieJar"
            self.connector = kwargs["connector"]

        def get(self, url: str, **kwargs: Any) -> Response:
            assert kwargs["allow_redirects"] is False
            assert kwargs["timeout"].total == 15
            return self.response

        async def close(self) -> None:
            self.closed = True
            await self.connector.close()

    sessions: list[Session] = []

    def factory(**kwargs: Any) -> Session:
        session = Session(**kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr("grid.exchange.ccxt_transport.aiohttp.ClientSession", factory)

    async def scenario() -> None:
        wire = HTTPWire()
        try:
            with pytest.raises(ReadError, match=Failure.BUDGET):
                await wire.get("https://www.okx.com/", ReadLimits(response_bytes=256))
            assert len(sessions) == 1
            assert sessions[0].response.content.read_chunks == 3
            assert sessions[0].response.exited
        finally:
            await wire.close()
        assert sessions[0].closed

    asyncio.run(scenario())


def test_slow_public_wire_times_out_without_retry(async_sockets: None) -> None:
    class SlowWire:
        calls = 0
        canceled = False
        closed = False

        async def get(self, url: str, limits: ReadLimits) -> WireReply:
            self.calls += 1
            try:
                await asyncio.Event().wait()
            finally:
                self.canceled = True
            raise AssertionError("unreachable")

        async def close(self) -> None:
            self.closed = True

    async def scenario() -> None:
        wire = SlowWire()
        transport = PublicCCXT("okx", ReadLimits(timeout_ms=10), wire=wire)
        try:
            with pytest.raises(ReadError, match=Failure.TIMEOUT):
                await transport.load_markets(False)
            assert wire.calls == 1 and wire.canceled
            with pytest.raises(ReadError, match=Failure.CLOSED):
                await transport.load_markets(True)
        finally:
            await transport.close()
        assert wire.closed

    asyncio.run(scenario())
