from __future__ import annotations

from unittest.mock import MagicMock

from bt_api_py.feeds.live_mercado_bitcoin.request_base import MercadoBitcoinRequestData
from bt_api_py.feeds.live_satoshitango.request_base import SatoshiTangoRequestData


def test_mercado_bitcoin_disconnect_closes_http_client() -> None:
    request_data = MercadoBitcoinRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_satoshitango_disconnect_closes_http_client() -> None:
    request_data = SatoshiTangoRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_satoshitango_accepts_public_private_key_aliases() -> None:
    request_data = SatoshiTangoRequestData(public_key="public-key", private_key="secret-key")
    headers = request_data._get_headers()

    assert request_data._params.api_key == "public-key"
    assert request_data._params.api_secret == "secret-key"
    assert headers["Authorization"] == "Bearer public-key"
