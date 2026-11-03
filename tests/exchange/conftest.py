from __future__ import annotations

import socket

import pytest
import pytest_socket


@pytest.fixture
def async_sockets() -> None:
    # asyncio's self-pipe is local AF_UNIX. TCP remains blocked in every mock.
    pytest_socket.enable_socket()
    pytest_socket.disable_socket(allow_unix_socket=True)
    with (
        pytest.warns(UserWarning, match="socket.socket"),
        pytest.raises(pytest_socket.SocketBlockedError),
    ):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


@pytest.fixture
def raw_market() -> dict[str, object]:
    return {
        "id": "BTC-USDT",
        "symbol": "BTC/USDT",
        "base": "BTC",
        "quote": "USDT",
        "baseId": "BTC",
        "quoteId": "USDT",
        "spot": True,
        "type": "spot",
        "contract": False,
        "active": True,
        "precision": {"price": "0.01", "amount": "0.000001"},
        "limits": {"price": {}, "amount": {"min": "0.00001"}, "cost": {"min": "5"}},
    }
