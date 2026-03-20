from __future__ import annotations

import base64

from bt_api_py.feeds.live_gateio.request_base import GateioRequestData
from bt_api_py.feeds.live_kraken.request_base import KrakenRequestData


def test_gateio_accepts_api_key_and_api_secret_aliases() -> None:
    request_data = GateioRequestData(api_key="public-key", api_secret="secret-key")
    headers = request_data._build_auth_headers("GET", "/api/v4/spot/accounts")

    assert request_data.public_key == "public-key"
    assert request_data.private_key == "secret-key"
    assert headers["KEY"] == "public-key"
    assert headers["SIGN"]


def test_kraken_accepts_api_key_and_api_secret_aliases() -> None:
    private_key = base64.b64encode(b"secret-key").decode("utf-8")
    request_data = KrakenRequestData(api_key="public-key", api_secret=private_key)
    headers = request_data._sign_request("/0/private/Balance", {})

    assert request_data.public_key == "public-key"
    assert request_data.private_key == private_key
    assert headers["API-Key"] == "public-key"
    assert headers["API-Sign"]
