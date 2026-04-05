from typing import Any

import pytest

pytest.importorskip("eth_account")

from bt_api_py.feeds.live_dydx.request_base import DydxRequestData
from bt_api_py.feeds.live_hyperliquid.request_base import HyperliquidRequestData
from bt_api_py.feeds.live_pancakeswap.request_base import PancakeSwapRequestData
from bt_api_py.feeds.live_ripio.request_base import RipioRequestData


def test_hyperliquid_accepts_public_private_key_aliases() -> None:
    request_data = HyperliquidRequestData(
        public_key="public-key",
        private_key="0x59c6995e998f97a5a0044966f0945382d6f7d28e17f72c0f0f6f7d7f9d1c1b11",
    )

    assert request_data.api_key == "public-key"
    assert request_data.private_key.startswith("0x59c699")
    assert request_data.address is not None


def test_dydx_accepts_public_private_key_aliases() -> None:
    request_data = DydxRequestData(
        None,
        public_key="public-key",
        private_key="secret-key",
    )

    assert request_data.api_key == "public-key"
    assert request_data.private_key == "secret-key"


def test_pancakeswap_accepts_public_private_key_aliases() -> None:
    request_data = PancakeSwapRequestData(
        public_key="public-key",
        private_key="secret-key",
    )

    assert request_data.api_key == "public-key"
    assert request_data.api_secret == "secret-key"


def test_ripio_accepts_public_private_key_aliases(monkeypatch: Any) -> None:
    request_data = RipioRequestData(public_key="public-key", private_key="secret-key")

    timestamp = "1710000000000"
    monkeypatch.setattr("time.time", lambda: 1710000000.0)
    headers = request_data._build_headers("GET", "/api/v1/balances", is_sign=True)

    assert request_data.api_key == "public-key"
    assert request_data.api_secret == "secret-key"
    assert headers["X-API-KEY"] == "public-key"
    assert headers["X-API-TIMESTAMP"] == timestamp
    assert headers["X-API-SIGNATURE"]
