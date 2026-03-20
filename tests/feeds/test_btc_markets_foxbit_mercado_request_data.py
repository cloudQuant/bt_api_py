from __future__ import annotations

import base64
from typing import Any

from bt_api_py.feeds.live_btc_markets.request_base import BtcMarketsRequestData
from bt_api_py.feeds.live_foxbit.request_base import FoxbitRequestData
from bt_api_py.feeds.live_mercado_bitcoin.request_base import MercadoBitcoinRequestData


def test_foxbit_accepts_public_private_key_aliases(monkeypatch: Any) -> None:
    request_data = FoxbitRequestData(public_key="public-key", private_key="secret-key")
    monkeypatch.setattr("bt_api_py.feeds.live_foxbit.request_base.time.time", lambda: 1700000000.0)

    headers = request_data._get_headers("GET", "/rest/v3/accounts", None, "")

    assert request_data._params.api_key == "public-key"
    assert request_data._params.api_secret == "secret-key"
    assert headers["X-FB-ACCESS-KEY"] == "public-key"
    assert headers["X-FB-ACCESS-SIGNATURE"]


def test_btc_markets_accepts_public_private_key_aliases(monkeypatch: Any) -> None:
    private_key = base64.b64encode(b"secret-key").decode("utf-8")
    request_data = BtcMarketsRequestData(public_key="public-key", private_key=private_key)
    monkeypatch.setattr("bt_api_py.feeds.live_btc_markets.request_base.time.time", lambda: 1700000000.0)

    headers = request_data._get_headers("GET", "/v3/accounts", None, None)

    assert request_data.api_key == "public-key"
    assert request_data.api_secret == private_key
    assert headers["BM-AUTH-APIKEY"] == "public-key"
    assert headers["BM-AUTH-SIGNATURE"]


def test_mercado_bitcoin_accepts_public_private_key_aliases(monkeypatch: Any) -> None:
    request_data = MercadoBitcoinRequestData(public_key="public-key", private_key="secret-key")
    monkeypatch.setattr(
        "bt_api_py.feeds.live_mercado_bitcoin.request_base.time.time", lambda: 1700000000.0
    )

    headers = request_data._get_headers("get_balance")

    assert request_data._params.api_key == "public-key"
    assert request_data._params.api_secret == "secret-key"
    assert headers["TAPI-ID"] == "public-key"
    assert headers["TAPI-MAC"]
